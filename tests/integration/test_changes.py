"""Integration tests for seam/analysis/changes.py and handle_seam_changes.

Uses a real temp git repository: init a repo, commit a file, index it,
then make edits; validates behavior through the public interface.

Coverage:
  C1   working-tree edit maps to the correct changed symbol(s)
  C2   staged edit maps to the correct changed symbol(s)
  C3   branch scope compares against a base ref
  C4   scope=working and scope=staged differ on unstaged vs staged changes
  C5   an added (untracked) file surfaces as new symbols (not invisible)
  C6   risk rollup correct (WILL_BREAK -> critical)
  C7   AMBIGUOUS attenuation: all-AMBIGUOUS edges cap risk at medium
  C8   non-git directory -> NotAGitRepoError (clear error, no traceback)
  C9   handler: invalid scope -> INVALID_INPUT
  C10  handler: blank base_ref -> INVALID_INPUT
  C11  handler: not-a-git-repo -> NOT_A_GIT_REPO error dict (no exception)
  C12  handler: file paths are relativized to root in the response
  C13  detect_changes on empty diff returns empty changed_symbols
  C14  module-level changes (lines outside any symbol) attributed to file
"""

import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

from seam.analysis.changes import (
    NotAGitRepoError,
    detect_changes,
)
from seam.analysis.traversal import CONFIDENCE_AMBIGUOUS, CONFIDENCE_EXTRACTED
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_changes

# ── Shared helpers ────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 5) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=start, end_line=end, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_EXTRACTED) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


def _git(args: list[str], cwd: Path) -> None:
    """Run a git command, raise on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def git_repo_with_index() -> tuple[sqlite3.Connection, Path, Path]:
    """Create a temp git repo with one committed Python file, indexed in Seam.

    Layout:
        <tmp>/           — project root (git repo)
        <tmp>/src.py     — source file with functions A (lines 1-5), B (lines 7-11)
        <tmp>/.seam/seam.db

    Yields (conn, project_root, src_path).
    """
    with tempfile.TemporaryDirectory() as tmp:
        # FIX 3: Resolve tmp so that path comparisons match the indexer's contract.
        # The indexer (cli init) calls Path.resolve() before upsert_file, so DB paths
        # are resolved. Tests must use the same resolved root to exercise the real contract.
        # On macOS /tmp -> /private/tmp; without resolve() DB lookups silently miss.
        tmp_path = Path(tmp).resolve()
        src = tmp_path / "src.py"

        # Write initial Python source: two functions.
        src.write_text(
            "def A():\n"
            "    pass\n"
            "\n"
            "\n"
            "\n"
            "def B():\n"
            "    A()\n"
            "\n"
        )

        # Add a .gitignore so the .seam/ DB files don't appear as untracked.
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".seam/\n")

        # Init git repo and commit.
        _git(["init", "--initial-branch=main"], tmp_path)
        _git(["config", "user.email", "test@seam.local"], tmp_path)
        _git(["config", "user.name", "Test"], tmp_path)
        _git(["add", "src.py", ".gitignore"], tmp_path)
        _git(["commit", "-m", "initial commit"], tmp_path)

        # Build Seam index.
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # A is at lines 1-2, B is at lines 6-7 (0-based columns in file above).
        # Use exact line numbers matching the content written above.
        upsert_file(
            conn,
            src,
            "python",
            "hash1",
            [
                _sym("A", str(src), start=1, end=2),
                _sym("B", str(src), start=6, end=7),
            ],
            [
                _edge("B", "A", str(src), CONFIDENCE_EXTRACTED),
            ],
        )

        yield conn, tmp_path, src  # type: ignore[misc]
        conn.close()


# ── C1: working-tree edit maps to changed symbol(s) ──────────────────────────


def test_working_tree_edit_maps_to_symbol(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """Editing a line inside function A must produce A in changed_symbols."""
    conn, repo_root, src = git_repo_with_index

    # Modify line 2 (inside A, which spans lines 1-2).
    lines = src.read_text().splitlines()
    lines[1] = "    return 1  # modified"
    src.write_text("\n".join(lines) + "\n")

    report = detect_changes(conn, scope="working", repo_root=repo_root)

    changed_names = [s["name"] for s in report["changed_symbols"]]
    assert "A" in changed_names, f"Expected 'A' in changed_symbols, got: {changed_names}"


# ── C2: staged edit maps to changed symbol(s) ────────────────────────────────


def test_staged_edit_maps_to_symbol(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """Staged edit inside B must produce B in changed_symbols for scope=staged."""
    conn, repo_root, src = git_repo_with_index

    # Modify line 7 (inside B which spans lines 6-7).
    lines = src.read_text().splitlines()
    lines[6] = "    A()  # staged change"
    src.write_text("\n".join(lines) + "\n")

    # Stage the change.
    _git(["add", "src.py"], repo_root)

    report = detect_changes(conn, scope="staged", repo_root=repo_root)

    changed_names = [s["name"] for s in report["changed_symbols"]]
    assert "B" in changed_names, f"Expected 'B' in changed_symbols (staged), got: {changed_names}"


# ── C3: branch scope compares against a base ref ─────────────────────────────


def test_branch_scope_compares_against_base_ref(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """Branch scope must detect changes committed on a branch vs the base ref."""
    conn, repo_root, src = git_repo_with_index

    # Create a new branch, commit a change to A.
    _git(["checkout", "-b", "feature-branch"], repo_root)
    lines = src.read_text().splitlines()
    lines[1] = "    return 42  # branch change"
    src.write_text("\n".join(lines) + "\n")
    _git(["add", "src.py"], repo_root)
    _git(["commit", "-m", "change A on branch"], repo_root)

    report = detect_changes(conn, base_ref="main", scope="branch", repo_root=repo_root)

    changed_names = [s["name"] for s in report["changed_symbols"]]
    assert "A" in changed_names, (
        f"Expected 'A' in changed_symbols for branch scope, got: {changed_names}"
    )


# ── C4: scope working vs staged differ ────────────────────────────────────────


def test_working_and_staged_differ(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """scope=staged must not include unstaged changes; scope=working must include them."""
    conn, repo_root, src = git_repo_with_index

    # Only modify the working tree (do NOT stage).
    lines = src.read_text().splitlines()
    lines[1] = "    return 99  # unstaged only"
    src.write_text("\n".join(lines) + "\n")

    staged_report = detect_changes(conn, scope="staged", repo_root=repo_root)
    working_report = detect_changes(conn, scope="working", repo_root=repo_root)

    staged_names = [s["name"] for s in staged_report["changed_symbols"]]
    working_names = [s["name"] for s in working_report["changed_symbols"]]

    # Staged diff should be empty (nothing staged).
    assert "A" not in staged_names, f"A should not appear in staged diff; got: {staged_names}"
    # Working diff should contain A.
    assert "A" in working_names, f"A should appear in working diff; got: {working_names}"


# ── C5: added file surfaces as new symbols ────────────────────────────────────


def test_added_file_surfaces_as_new_symbols(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """An untracked added file must produce an entry in new_files (not invisible)."""
    conn, repo_root, _ = git_repo_with_index

    # Add a new untracked file (NOT committed, NOT staged).
    new_file = repo_root / "new_module.py"
    new_file.write_text("def new_func():\n    pass\n")

    report = detect_changes(conn, scope="working", repo_root=repo_root)

    # The new file's absolute path must appear in new_files.
    new_files_rel = report["new_files"]
    assert any("new_module.py" in f for f in new_files_rel), (
        f"Expected new_module.py in new_files; got: {new_files_rel}"
    )

    # The changed_symbols must include a synthetic entry for the new file
    # (since it's not indexed yet).
    changed_names = [s["name"] for s in report["changed_symbols"]]
    assert any("new_module.py" in n for n in changed_names), (
        f"Expected a synthetic entry for new_module.py in changed_symbols; got: {changed_names}"
    )


# ── C5b: staged new file is detected as is_new_file=True (FIX 1 regression test) ──────────────


def test_staged_new_file_appears_in_new_files(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """A staged (git add) new file must appear in report['new_files'] with scope='staged'.

    This test would FAIL before FIX 1 because git emits 'new file mode' before
    '+++ b/path', and the old parser reset is_new_file=False when it hit '+++ b/'.
    The pending_new_file flag in FIX 1 corrects the ordering issue.

    Also verifies that its absolute path appears in new_files (not just changed_symbols),
    confirming the is_new_file flag drives the correct branch in detect_changes.
    """
    conn, repo_root, _ = git_repo_with_index

    # Create a brand-new file and STAGE it (not committed yet).
    new_file = repo_root / "staged_new.py"
    new_file.write_text("def staged_func():\n    return 42\n")
    _git(["add", "staged_new.py"], repo_root)

    # Index the new file into Seam so symbols are visible (simulates watcher indexing).
    upsert_file(
        conn,
        new_file,
        "python",
        "hashN",
        [_sym("staged_func", str(new_file), start=1, end=2)],
        [],
    )

    report = detect_changes(conn, scope="staged", repo_root=repo_root)

    # The new file must appear in new_files (not just changed_symbols).
    new_file_str = str(new_file)
    assert new_file_str in report["new_files"], (
        f"staged_new.py must be in new_files; got: {report['new_files']}"
    )

    # Its symbols must be surfaced in changed_symbols.
    changed_names = [s["name"] for s in report["changed_symbols"]]
    assert "staged_func" in changed_names, (
        f"staged_func must be in changed_symbols; got: {changed_names}"
    )


# ── C6: risk rollup correct ────────────────────────────────────────────────────


def test_risk_rollup_will_break_is_critical(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """Editing A (which B depends on, d=1) must produce risk_level=critical."""
    conn, repo_root, src = git_repo_with_index

    # Edit line 2 (inside A, lines 1-2).
    lines = src.read_text().splitlines()
    lines[1] = "    return 'changed'"
    src.write_text("\n".join(lines) + "\n")

    report = detect_changes(conn, scope="working", repo_root=repo_root)

    # A has a direct dependent B at d=1 -> WILL_BREAK -> critical.
    assert report["risk_level"] == "critical", (
        f"Expected risk_level=critical, got: {report['risk_level']}; "
        f"affected={report['affected']}"
    )


# ── C7: AMBIGUOUS attenuation ─────────────────────────────────────────────────


def test_ambiguous_attenuation_caps_risk_at_medium() -> None:
    """When ALL affected symbols have AMBIGUOUS confidence, risk must be capped at medium."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()  # FIX 3: use resolved path to match DB storage contract
        src = tmp_path / "src.py"
        src.write_text("def A():\n    pass\n\ndef B():\n    A()\n")
        (tmp_path / ".gitignore").write_text(".seam/\n")

        _git(["init", "--initial-branch=main"], tmp_path)
        _git(["config", "user.email", "test@seam.local"], tmp_path)
        _git(["config", "user.name", "Test"], tmp_path)
        _git(["add", "src.py", ".gitignore"], tmp_path)
        _git(["commit", "-m", "initial"], tmp_path)

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # B -> A with AMBIGUOUS confidence (all edges AMBIGUOUS).
        upsert_file(
            conn,
            src,
            "python",
            "hashX",
            [_sym("A", str(src), start=1, end=2), _sym("B", str(src), start=4, end=5)],
            [_edge("B", "A", str(src), CONFIDENCE_AMBIGUOUS)],
        )

        # Edit A (line 2).
        lines = src.read_text().splitlines()
        lines[1] = "    return 0"
        src.write_text("\n".join(lines) + "\n")

        report = detect_changes(conn, scope="working", repo_root=tmp_path)
        conn.close()

        # With all-AMBIGUOUS edges, risk must be capped at medium even though
        # the raw tier is WILL_BREAK (d=1).
        assert report["ambiguous_warning"] is True, "ambiguous_warning must be True"
        assert report["risk_level"] == "medium", (
            f"Expected risk_level=medium (AMBIGUOUS attenuation), got: {report['risk_level']}"
        )


# ── C8: non-git directory -> NotAGitRepoError ─────────────────────────────────


def test_non_git_directory_raises_not_a_git_repo_error() -> None:
    """detect_changes on a non-git directory must raise NotAGitRepoError (not a traceback)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()  # FIX 3: consistent resolved path
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        try:
            with pytest.raises(NotAGitRepoError):
                detect_changes(conn, scope="working", repo_root=tmp_path)
        finally:
            conn.close()


# ── C9: handler invalid scope -> INVALID_INPUT ───────────────────────────────


def test_handler_invalid_scope_returns_invalid_input(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """handle_seam_changes with invalid scope must return INVALID_INPUT error."""
    conn, repo_root, _ = git_repo_with_index
    result = handle_seam_changes(conn, repo_root, scope="nonsense")

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT", f"Expected INVALID_INPUT, got: {result}"


# ── C10: handler blank base_ref -> INVALID_INPUT ─────────────────────────────


def test_handler_blank_base_ref_returns_invalid_input(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """handle_seam_changes with blank base_ref must return INVALID_INPUT."""
    conn, repo_root, _ = git_repo_with_index
    result = handle_seam_changes(conn, repo_root, base_ref="   ", scope="branch")

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT", f"Expected INVALID_INPUT, got: {result}"


# ── C11: handler not-a-git-repo -> NOT_A_GIT_REPO dict ───────────────────────


def test_handler_non_git_returns_not_a_git_repo_dict() -> None:
    """handle_seam_changes in non-git dir must return NOT_A_GIT_REPO error dict (no exception)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()  # FIX 3: consistent resolved path
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        try:
            result = handle_seam_changes(conn, tmp_path, scope="working")
        finally:
            conn.close()

    assert isinstance(result, dict)
    assert result.get("error") == "NOT_A_GIT_REPO", f"Expected NOT_A_GIT_REPO, got: {result}"
    assert "message" in result


# ── C12: file paths relativized in handler response ──────────────────────────


def test_handler_relativizes_file_paths(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """handle_seam_changes must return file paths relative to root (not absolute)."""
    conn, repo_root, src = git_repo_with_index

    # Make a working-tree change to get changed_symbols.
    lines = src.read_text().splitlines()
    lines[1] = "    return 7  # relativize test"
    src.write_text("\n".join(lines) + "\n")

    result = handle_seam_changes(conn, repo_root, scope="working")

    assert "error" not in result, f"Expected success, got error: {result}"

    # Check each changed_symbol's file (if present) is relative, not absolute.
    for sym in result.get("changed_symbols", []):
        f = sym.get("file")
        if f is not None:
            assert not f.startswith("/"), (
                f"Expected relative path in changed_symbols, got absolute: {f!r}"
            )

    # Check each affected symbol's file (if present) is relative.
    for a in result.get("affected", []):
        f = a.get("file")
        if f is not None:
            assert not f.startswith("/"), (
                f"Expected relative path in affected, got absolute: {f!r}"
            )


# ── C13: empty diff returns empty changed_symbols ────────────────────────────


def test_empty_diff_returns_empty_report(
    git_repo_with_index: tuple[sqlite3.Connection, Path, Path],
) -> None:
    """When there are no changes, detect_changes must return empty changed_symbols."""
    conn, repo_root, _ = git_repo_with_index

    # No modifications — clean working tree.
    report = detect_changes(conn, scope="staged", repo_root=repo_root)

    assert report["changed_symbols"] == [], (
        f"Expected empty changed_symbols on clean tree, got: {report['changed_symbols']}"
    )
    assert report["risk_level"] == "low", (
        f"Expected risk_level=low on empty diff, got: {report['risk_level']}"
    )


# ── C14: module-level changes attributed to file ─────────────────────────────


def test_module_level_changes_attributed_to_file() -> None:
    """Lines outside any symbol must produce a synthetic <module:file> entry."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()  # FIX 3: consistent resolved path to match DB storage
        src = tmp_path / "mod.py"
        # Line 1 is module-level (not inside any function).
        src.write_text("X = 1\ndef foo():\n    pass\n")
        (tmp_path / ".gitignore").write_text(".seam/\n")

        _git(["init", "--initial-branch=main"], tmp_path)
        _git(["config", "user.email", "test@seam.local"], tmp_path)
        _git(["config", "user.name", "Test"], tmp_path)
        _git(["add", "mod.py", ".gitignore"], tmp_path)
        _git(["commit", "-m", "initial"], tmp_path)

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # Only index `foo` (lines 2-3), so line 1 (module-level) is uncovered.
        upsert_file(
            conn,
            src,
            "python",
            "hashM",
            [_sym("foo", str(src), start=2, end=3)],
            [],
        )

        # Modify line 1 (module-level, outside `foo`).
        src.write_text("X = 99\ndef foo():\n    pass\n")

        report = detect_changes(conn, scope="working", repo_root=tmp_path)
        conn.close()

    changed_names = [s["name"] for s in report["changed_symbols"]]
    # Should have a synthetic <module:mod.py> entry for the uncovered line.
    assert any("mod.py" in n for n in changed_names), (
        f"Expected module-level attribution for mod.py, got: {changed_names}"
    )
