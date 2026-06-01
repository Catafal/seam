"""Unit tests for the 'partial' flag in ChangeReport (issue #11).

Tests verify EXTERNAL behavior through detect_changes() and handle_seam_changes().

The cap is exercised by passing it directly to _collect_impact (internal) via
the path: monkeypatch seam.analysis.changes._MAX_IMPACT_SYMBOLS → but since
we redesigned _collect_impact to accept the cap as a parameter read from
seam.config.SEAM_MAX_IMPACT_SYMBOLS, we monkeypatch the config attribute
that the module reads at call time.

Coverage:
  P1  partial=False when changed symbols < cap (normal path)
  P2  partial=True  when changed symbols > cap (cap is hit)
  P3  handle_seam_changes passes 'partial' through in response dict
  P4  partial=False when diff is empty
"""

import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

import seam.config as config
from seam.analysis.changes import detect_changes
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_changes

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 2) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=start, end_line=end, docstring=None)


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence="EXTRACTED")


def _git(args: list[str], cwd: Path) -> None:
    """Run a git command; raise on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


@pytest.fixture()
def git_repo() -> tuple[sqlite3.Connection, Path, Path]:
    """Minimal git repo with two Python functions A (lines 1-2) and B (lines 4-5).

    Returns (conn, repo_root, src_path).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()
        src = tmp_path / "src.py"
        src.write_text("def A():\n    pass\n\ndef B():\n    pass\n")

        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".seam/\n")

        _git(["init", "--initial-branch=main"], tmp_path)
        _git(["config", "user.email", "test@seam.local"], tmp_path)
        _git(["config", "user.name", "Test"], tmp_path)
        _git(["add", "src.py", ".gitignore"], tmp_path)
        _git(["commit", "-m", "initial"], tmp_path)

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        upsert_file(
            conn,
            src,
            "python",
            "hash1",
            [
                _sym("A", str(src), start=1, end=2),
                _sym("B", str(src), start=4, end=5),
            ],
            [],
        )

        yield conn, tmp_path, src  # type: ignore[misc]
        conn.close()


# ── P1: partial=False when changed symbols < cap ──────────────────────────────


def test_partial_false_below_cap(
    git_repo: tuple[sqlite3.Connection, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When changed symbol count is below the cap, partial must be False."""
    conn, repo_root, src = git_repo

    # Set a cap large enough that 2 real symbols never hit it.
    monkeypatch.setattr(config, "SEAM_MAX_IMPACT_SYMBOLS", 100)

    # Modify A (line 2).
    lines = src.read_text().splitlines()
    lines[1] = "    return 1  # changed"
    src.write_text("\n".join(lines) + "\n")

    report = detect_changes(conn, scope="working", repo_root=repo_root)

    assert "partial" in report, "ChangeReport must include 'partial' key"
    assert report["partial"] is False, (
        f"Expected partial=False when cap not hit, got: {report['partial']!r}"
    )


# ── P2: partial=True when changed symbols > cap ───────────────────────────────


def test_partial_true_above_cap(
    git_repo: tuple[sqlite3.Connection, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When real changed symbol count exceeds the cap, partial must be True.

    We set the cap to 1 so that even 2 real changed symbols triggers it.
    Both A and B are modified so both appear in changed_symbols.
    """
    conn, repo_root, src = git_repo

    # Cap = 1: any diff with 2+ real symbols will exceed it.
    monkeypatch.setattr(config, "SEAM_MAX_IMPACT_SYMBOLS", 1)

    # Modify both A (line 2) and B (line 5) so two real symbols are changed.
    lines = src.read_text().splitlines()
    lines[1] = "    return 1  # changed A"
    lines[4] = "    return 2  # changed B"
    src.write_text("\n".join(lines) + "\n")

    report = detect_changes(conn, scope="working", repo_root=repo_root)

    assert "partial" in report, "ChangeReport must include 'partial' key"
    assert report["partial"] is True, (
        f"Expected partial=True when cap=1 and 2 real symbols changed, got: {report['partial']!r}"
    )


# ── P3: handle_seam_changes passes partial through ───────────────────────────


def test_handler_passes_partial_through(
    git_repo: tuple[sqlite3.Connection, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handle_seam_changes must include 'partial' in its response dict."""
    conn, repo_root, src = git_repo

    monkeypatch.setattr(config, "SEAM_MAX_IMPACT_SYMBOLS", 100)

    # Any working-tree change to get a non-trivial response.
    lines = src.read_text().splitlines()
    lines[1] = "    return 99"
    src.write_text("\n".join(lines) + "\n")

    result = handle_seam_changes(conn, repo_root, scope="working")

    assert "error" not in result, f"Expected success, got error: {result}"
    assert "partial" in result, (
        f"handle_seam_changes response must include 'partial'; got keys: {list(result.keys())}"
    )
    assert isinstance(result["partial"], bool), (
        f"'partial' must be a bool, got {type(result['partial'])!r}"
    )


# ── P4: partial=False when diff is empty ──────────────────────────────────────


def test_partial_false_on_empty_diff(
    git_repo: tuple[sqlite3.Connection, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No changes means partial must be False (zero symbols, cap not hit)."""
    conn, repo_root, _src = git_repo

    monkeypatch.setattr(config, "SEAM_MAX_IMPACT_SYMBOLS", 1)

    # Clean working tree — no modifications.
    report = detect_changes(conn, scope="staged", repo_root=repo_root)

    assert report["partial"] is False, (
        f"Empty diff must produce partial=False, got: {report['partial']!r}"
    )
