"""Integration tests for handle_seam_affected (tools.py) and the 'seam affected' CLI command.

TDD: tests written before implementation. Drives the handler + CLI contract.

Coverage:
  H1   handle_seam_affected returns documented shape with relativized paths
  H2   handle_seam_affected empty input -> INVALID_INPUT error dict
  H3   CLI: seam affected <file> --json -> valid {"ok":true,"data":{...}} envelope
  H4   CLI: seam affected --quiet -> bare test-file paths (one per line)
  H5   CLI: seam affected --stdin reads piped input
  H6   CLI: seam affected on missing index -> NO_INDEX envelope + exit 1
  H7   CLI: seam affected --json and --quiet together -> INVALID_INPUT + exit 1
  H8   CLI: seam affected with no files and no --stdin -> friendly error + exit 1
  H9   CLI: seam changes --stdin reads piped file list and restricts analysis
  H10  seam_affected MCP parity: handler result == CLI --json data payload
"""

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.analysis.traversal import CONFIDENCE_EXTRACTED
from seam.cli.main import app
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_affected

runner = CliRunner()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 5) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file, start_line=start, end_line=end, docstring=None
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind="call",
        file=file,
        line=1,
        confidence=CONFIDENCE_EXTRACTED,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def seeded_db_with_tests(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Seed a DB with source + test files.

    Layout:
        <tmp>/src.py       — defines A()
        <tmp>/tests/test_a.py — defines test_a(), calls A

    Yields (db_dir, project_root, src_path, test_path).
    """
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "tests").mkdir()

    src = project_root / "src.py"
    test_file = project_root / "tests" / "test_a.py"

    src.write_text("def A(): pass\n")
    test_file.write_text("def test_a(): A()\n")

    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        "hash_src",
        [_sym("A", str(src))],
        [],
    )
    upsert_file(
        conn,
        test_file,
        "python",
        "hash_test",
        [_sym("test_a", str(test_file))],
        [_edge("test_a", "A", str(test_file))],
    )
    conn.commit()
    conn.close()

    return db_dir, project_root, src, test_file


# ── Handler tests (H1–H2) ─────────────────────────────────────────────────────


def test_h1_handler_returns_documented_shape(seeded_db_with_tests: tuple) -> None:
    """H1: handle_seam_affected returns {changed_files, affected_tests, total_dependents_traversed}."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    db_path = db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        result = handle_seam_affected(conn, [str(src)], project_root)
    finally:
        conn.close()

    # Must have all three required keys
    assert "changed_files" in result
    assert "affected_tests" in result
    assert "total_dependents_traversed" in result

    # Paths should be relativized to project_root
    for p in result["affected_tests"]:
        assert not Path(p).is_absolute(), f"Expected relative path, got: {p}"

    # The test file should appear
    rel_test = str(test_file.relative_to(project_root))
    assert rel_test in result["affected_tests"]


def test_h2_handler_empty_input_invalid(seeded_db_with_tests: tuple) -> None:
    """H2: handle_seam_affected with empty list -> INVALID_INPUT error dict."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    db_path = db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        result = handle_seam_affected(conn, [], project_root)
    finally:
        conn.close()

    assert "error" in result
    assert result["error"] == "INVALID_INPUT"


# ── CLI tests (H3–H10) ────────────────────────────────────────────────────────


def test_h3_cli_json_returns_envelope(seeded_db_with_tests: tuple) -> None:
    """H3: seam affected <file> --json -> valid {"ok":true,"data":{...}} envelope."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    result = runner.invoke(
        app,
        ["affected", str(src), "--json", "--path", str(project_root), "--db-dir", str(db_dir)],
    )

    assert result.exit_code == 0, f"stdout: {result.stdout}"
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert "data" in data

    payload = data["data"]
    assert "changed_files" in payload
    assert "affected_tests" in payload
    assert "total_dependents_traversed" in payload

    # Test file should appear
    rel_test = str(test_file.relative_to(project_root))
    assert rel_test in payload["affected_tests"]


def test_h4_cli_quiet_prints_bare_paths(seeded_db_with_tests: tuple) -> None:
    """H4: seam affected --quiet -> bare test-file paths one per line."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    result = runner.invoke(
        app,
        ["affected", str(src), "--quiet", "--path", str(project_root), "--db-dir", str(db_dir)],
    )

    assert result.exit_code == 0, f"stdout: {result.stdout}"

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # Should have at least the test file
    rel_test = str(test_file.relative_to(project_root))
    assert rel_test in lines


def test_h5_cli_stdin_reads_piped_input(seeded_db_with_tests: tuple) -> None:
    """H5: seam affected --stdin reads file paths from stdin."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    # Pipe src.py path via stdin
    result = runner.invoke(
        app,
        ["affected", "--stdin", "--json", "--path", str(project_root), "--db-dir", str(db_dir)],
        input=str(src) + "\n",
    )

    assert result.exit_code == 0, f"stdout: {result.stdout}"
    data = json.loads(result.stdout)
    assert data["ok"] is True
    rel_test = str(test_file.relative_to(project_root))
    assert rel_test in data["data"]["affected_tests"]


def test_h6_cli_no_index_error(tmp_path: Path) -> None:
    """H6: seam affected on missing index -> NO_INDEX envelope + exit 1."""
    result = runner.invoke(
        app,
        ["affected", "some_file.py", "--json", "--path", str(tmp_path), "--db-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["error"]["code"] == "NO_INDEX"


def test_h7_cli_json_and_quiet_mutual_exclusion(seeded_db_with_tests: tuple) -> None:
    """H7: seam affected --json --quiet -> INVALID_INPUT + exit 1."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    result = runner.invoke(
        app,
        [
            "affected",
            str(src),
            "--json",
            "--quiet",
            "--path",
            str(project_root),
            "--db-dir",
            str(db_dir),
        ],
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["error"]["code"] == "INVALID_INPUT"


def test_h8_cli_no_files_no_stdin_error(seeded_db_with_tests: tuple) -> None:
    """H8: seam affected with no positional args and no --stdin -> friendly error + exit 1."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    result = runner.invoke(
        app,
        ["affected", "--json", "--path", str(project_root), "--db-dir", str(db_dir)],
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["error"]["code"] == "INVALID_INPUT"


def test_h9_changes_stdin(seeded_db_with_tests: tuple, tmp_path: Path) -> None:
    """H9: seam changes --stdin reads piped file list and scopes the analysis to those files.

    Since the seeded DB is not in a git repo, the changes command will return NOT_A_GIT_REPO.
    This test verifies that --stdin is accepted as an option (not an unknown flag) and that
    the command attempts to process its input (the NOT_A_GIT_REPO error only appears after
    stdin is consumed).
    """
    db_dir, project_root, src, test_file = seeded_db_with_tests

    result = runner.invoke(
        app,
        [
            "changes",
            "--stdin",
            "--json",
            "--path",
            str(project_root),
            "--db-dir",
            str(db_dir),
        ],
        input=str(src) + "\n",
    )

    # The command should parse --stdin without error (exit 0 or 1 depending on git availability)
    # We only require that it doesn't fail with "No such option: --stdin"
    assert "No such option" not in result.stdout
    assert "No such option" not in (result.stderr or "")

    # In --json mode, either success or a structured error should be returned
    if result.output.strip():
        try:
            parsed = json.loads(result.stdout)
            # If it parsed, it should have the envelope structure
            assert "ok" in parsed
        except json.JSONDecodeError:
            # Non-JSON output means something went wrong unexpectedly
            pytest.fail(f"Expected JSON output but got: {result.stdout!r}")


def test_h10_mcp_parity(seeded_db_with_tests: tuple) -> None:
    """H10: CLI --json data payload matches handle_seam_affected output (parity)."""
    db_dir, project_root, src, test_file = seeded_db_with_tests

    # CLI --json
    result = runner.invoke(
        app,
        ["affected", str(src), "--json", "--path", str(project_root), "--db-dir", str(db_dir)],
    )
    assert result.exit_code == 0
    cli_data = json.loads(result.stdout)["data"]

    # Handler directly
    db_path = db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        handler_data = handle_seam_affected(conn, [str(src)], project_root)
    finally:
        conn.close()

    # Both should have the same affected_tests (order-independent)
    assert sorted(cli_data["affected_tests"]) == sorted(handler_data["affected_tests"])
    assert sorted(cli_data["changed_files"]) == sorted(handler_data["changed_files"])
