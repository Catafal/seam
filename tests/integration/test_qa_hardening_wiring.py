"""Integration tests for QA hardening wiring (issues #10 + #11).

Tests verify EXTERNAL behavior through handle_seam_impact and handle_seam_changes:
  - include_tests parameter threads through the handler
  - is_test field appears in handler response entries
  - partial field appears in handle_seam_changes response

These tests follow the style of test_impact_handler.py and test_changes.py.

Coverage:
  W1  handle_seam_impact: is_test field present in every tier entry
  W2  handle_seam_impact: include_tests=False removes test entries from response
  W3  handle_seam_impact: include_tests=True (default) keeps test entries
  W4  handle_seam_changes: partial=True when cap is 1 and 2 symbols changed
  W5  handle_seam_changes: partial=False in normal conditions
"""

import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

import seam.config as config
from seam.analysis.traversal import CONFIDENCE_EXTRACTED
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_changes, handle_seam_impact

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 2) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=start, end_line=end, docstring=None)


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=CONFIDENCE_EXTRACTED)


def _git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def mixed_impact_db() -> tuple[sqlite3.Connection, Path]:
    """DB with prod caller and test caller both pointing at target A.

    Graph: prod_caller (prod.py) -> A, test_caller (tests/t.py) -> A.
    Returns (conn, project_root).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        prod_file = tmp_path / "prod.py"
        prod_file.write_text("# prod\n")

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_thing.py"
        test_file.write_text("# test\n")

        conn = init_db(db_path)

        # A and prod_caller in prod.py.
        upsert_file(
            conn, prod_file, "python", "h_prod",
            [_sym("A", str(prod_file)), _sym("prod_caller", str(prod_file))],
            [_edge("prod_caller", "A", str(prod_file))],
        )
        # test_caller in tests/test_thing.py.
        upsert_file(
            conn, test_file, "python", "h_test",
            [_sym("test_caller", str(test_file))],
            [_edge("test_caller", "A", str(test_file))],
        )

        yield conn, tmp_path  # type: ignore[misc]
        conn.close()


@pytest.fixture()
def git_repo_two_symbols() -> tuple[sqlite3.Connection, Path, Path]:
    """Minimal git repo with two functions, indexed. For partial tests."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()
        src = tmp_path / "src.py"
        src.write_text("def A():\n    pass\n\ndef B():\n    pass\n")
        (tmp_path / ".gitignore").write_text(".seam/\n")

        _git(["init", "--initial-branch=main"], tmp_path)
        _git(["config", "user.email", "test@seam.local"], tmp_path)
        _git(["config", "user.name", "Test"], tmp_path)
        _git(["add", "src.py", ".gitignore"], tmp_path)
        _git(["commit", "-m", "init"], tmp_path)

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        upsert_file(
            conn, src, "python", "h1",
            [_sym("A", str(src), 1, 2), _sym("B", str(src), 4, 5)],
            [],
        )

        yield conn, tmp_path, src  # type: ignore[misc]
        conn.close()


# ── W1: is_test field present in handler response entries ────────────────────


def test_handler_entries_have_is_test_field(
    mixed_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_impact must include 'is_test' in every tier entry."""
    conn, root = mixed_impact_db

    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=3)

    assert "error" not in result, f"Expected success, got: {result}"

    all_entries = [
        e
        for tier_list in result["upstream"].values()
        for e in tier_list
    ]
    assert len(all_entries) > 0, "Must have at least one entry in the fixture"

    for entry in all_entries:
        assert "is_test" in entry, f"Entry missing 'is_test': {entry}"
        assert isinstance(entry["is_test"], bool), (
            f"is_test must be bool, got {type(entry['is_test'])!r}"
        )


# ── W2: include_tests=False removes test entries from handler response ─────────


def test_handler_include_tests_false_removes_test_entries(
    mixed_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_impact with include_tests=False must exclude test-file entries."""
    conn, root = mixed_impact_db

    result = handle_seam_impact(
        conn, "A", root, direction="upstream", max_depth=3, include_tests=False
    )

    assert "error" not in result, f"Expected success, got: {result}"

    all_names = [
        e["name"]
        for tier_list in result["upstream"].values()
        for e in tier_list
    ]

    assert "test_caller" not in all_names, (
        f"test_caller must be filtered with include_tests=False; got: {all_names}"
    )
    assert "prod_caller" in all_names, (
        f"prod_caller must remain with include_tests=False; got: {all_names}"
    )


# ── W3: explicit include_tests=True keeps test entries (default now excludes) ──


def test_handler_include_tests_true_keeps_test_entries(
    mixed_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Explicit include_tests=True keeps test entries; the default now EXCLUDES them.

    P1 flipped the seam_impact default to production-only (include_tests=False). This
    verifies both halves of the flipped contract through the handler:
      - default (no arg) → test_caller filtered out (production blast radius)
      - explicit include_tests=True → test_caller restored (opt-in)
    """
    conn, root = mixed_impact_db

    # Default: production-only — test_caller is filtered out.
    default_result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=3)
    default_names = [
        e["name"] for tier_list in default_result["upstream"].values() for e in tier_list
    ]
    assert "test_caller" not in default_names, (
        f"default is now production-only; test_caller must be filtered; got: {default_names}"
    )
    assert "prod_caller" in default_names, (
        f"prod_caller must remain by default; got: {default_names}"
    )

    # Opt-in: include_tests=True restores test dependents.
    included_result = handle_seam_impact(
        conn, "A", root, direction="upstream", max_depth=3, include_tests=True
    )
    included_names = [
        e["name"] for tier_list in included_result["upstream"].values() for e in tier_list
    ]
    assert "test_caller" in included_names, (
        f"explicit include_tests=True must keep test_caller; got: {included_names}"
    )
    assert "prod_caller" in included_names


# ── W4: handle_seam_changes partial=True when cap hit ────────────────────────


def test_handler_partial_true_when_cap_hit(
    git_repo_two_symbols: tuple[sqlite3.Connection, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handle_seam_changes must return partial=True when cap is exceeded."""
    conn, repo_root, src = git_repo_two_symbols

    # Cap=1 so 2 real changed symbols triggers partial.
    monkeypatch.setattr(config, "SEAM_MAX_IMPACT_SYMBOLS", 1)

    lines = src.read_text().splitlines()
    lines[1] = "    return 1"
    lines[4] = "    return 2"
    src.write_text("\n".join(lines) + "\n")

    result = handle_seam_changes(conn, repo_root, scope="working")

    assert "error" not in result, f"Expected success, got: {result}"
    assert result.get("partial") is True, (
        f"Expected partial=True, got: {result.get('partial')!r}"
    )


# ── W5: handle_seam_changes partial=False in normal conditions ────────────────


def test_handler_partial_false_normal(
    git_repo_two_symbols: tuple[sqlite3.Connection, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handle_seam_changes must return partial=False when cap is not hit."""
    conn, repo_root, src = git_repo_two_symbols

    monkeypatch.setattr(config, "SEAM_MAX_IMPACT_SYMBOLS", 100)

    lines = src.read_text().splitlines()
    lines[1] = "    return 1"
    src.write_text("\n".join(lines) + "\n")

    result = handle_seam_changes(conn, repo_root, scope="working")

    assert "error" not in result, f"Expected success, got: {result}"
    assert result.get("partial") is False, (
        f"Expected partial=False, got: {result.get('partial')!r}"
    )
