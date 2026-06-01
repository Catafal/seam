"""Integration tests for MCP tool handlers (seam/server/tools.py).

Tests call each handler directly against a seeded SQLite DB.
Fixtures seed known symbols/edges so assertions are deterministic.

Coverage:
  T1  seam_query — happy path, returns QueryResult-shaped dicts
  T2  seam_query — INVALID_INPUT on blank concept
  T3  seam_query — limit clamping (>50 clamped to 50)
  T4  seam_query — file paths are relative to project root
  T5  seam_context — happy path, returns ContextResult-shaped dict
  T6  seam_context — returns null payload for unknown symbol
  T7  seam_context — INVALID_INPUT on blank symbol
  T8  seam_search — happy path, returns SearchResult-shaped dicts
  T9  seam_search — INVALID_INPUT on whitespace-only text
  T10 seam_search — INVALID_QUERY when FTS5 syntax is malformed
  T11 seam_search — limit clamping (>100 clamped to 100)
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_context, handle_seam_query, handle_seam_search

# ── DB fixture ────────────────────────────────────────────────────────────────


@pytest.fixture()
def seeded_db() -> tuple[sqlite3.Connection, Path]:
    """Create a temporary DB seeded with known symbols and edges.

    Returns (conn, project_root). The conn must NOT be closed by the test —
    the fixture teardown handles that.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)

        conn = init_db(db_path)

        # Seed a fake Python file at a known absolute path.
        # The file must exist on disk because upsert_file reads its mtime.
        src_path = tmp_path / "src" / "auth.py"
        src_path.parent.mkdir(parents=True)
        src_path.write_text("# stub\n")

        symbols: list[Symbol] = [
            Symbol(
                name="authenticate_user",
                kind="function",
                file=str(src_path),
                start_line=10,
                end_line=25,
                docstring="Verify credentials and return a session token.",
            ),
            Symbol(
                name="UserService",
                kind="class",
                file=str(src_path),
                start_line=30,
                end_line=80,
                docstring="Service layer for user operations.",
            ),
            Symbol(
                name="UserService.validate",
                kind="method",
                file=str(src_path),
                start_line=45,
                end_line=60,
                docstring="Validate user input fields.",
            ),
        ]
        edges: list[Edge] = [
            Edge(
                source="UserService.validate",
                target="authenticate_user",
                kind="call",
                file=str(src_path),
                line=50,
                confidence="EXTRACTED",
            ),
        ]
        upsert_file(conn, src_path, "python", "abc123", symbols, edges)

        yield conn, tmp_path  # type: ignore[misc]

        conn.close()


# ── T1: seam_query happy path ─────────────────────────────────────────────────


def test_seam_query_returns_results(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_query must return a list of QueryResult-shaped dicts on match."""
    conn, root = seeded_db
    results = handle_seam_query(conn, "authenticate", root, limit=10)

    assert isinstance(results, list)
    assert len(results) > 0

    # Each result must have the contract-specified fields
    for item in results:
        assert "symbol" in item
        assert "file" in item
        assert "line" in item
        assert "score" in item
        assert "callers_count" in item
        assert "callees_count" in item
        assert isinstance(item["symbol"], str)
        assert isinstance(item["file"], str)
        assert isinstance(item["line"], int)
        assert isinstance(item["score"], float)
        assert isinstance(item["callers_count"], int)
        assert isinstance(item["callees_count"], int)


# ── T2: seam_query INVALID_INPUT ─────────────────────────────────────────────


def test_seam_query_empty_concept_returns_invalid_input(
    seeded_db: tuple[sqlite3.Connection, Path],
) -> None:
    """seam_query must return an error dict with code INVALID_INPUT for blank concept."""
    conn, root = seeded_db
    result = handle_seam_query(conn, "  ", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


def test_seam_query_empty_string_returns_invalid_input(
    seeded_db: tuple[sqlite3.Connection, Path],
) -> None:
    """seam_query: empty string must also produce INVALID_INPUT."""
    conn, root = seeded_db
    result = handle_seam_query(conn, "", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T3: seam_query limit clamping ─────────────────────────────────────────────


def test_seam_query_limit_clamp_high(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_query: limit > 50 must be silently clamped to 50."""
    conn, root = seeded_db
    # Should not raise; just clamp and return results
    results = handle_seam_query(conn, "user", root, limit=999)
    assert isinstance(results, list)


def test_seam_query_limit_clamp_low(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_query: limit < 1 must be silently clamped to 1."""
    conn, root = seeded_db
    results = handle_seam_query(conn, "user", root, limit=0)
    assert isinstance(results, list)


# ── T4: seam_query file relativization ────────────────────────────────────────


def test_seam_query_file_is_relative(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_query must return relative paths (relative to project root)."""
    conn, root = seeded_db
    results = handle_seam_query(conn, "authenticate", root, limit=10)

    assert len(results) > 0
    for item in results:
        # Must not be an absolute path
        assert not Path(item["file"]).is_absolute(), f"Expected relative path, got: {item['file']}"


# ── T5: seam_context happy path ───────────────────────────────────────────────


def test_seam_context_returns_result(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_context must return a ContextResult-shaped dict for a known symbol."""
    conn, root = seeded_db
    result = handle_seam_context(conn, "authenticate_user", root)

    assert result is not None
    assert isinstance(result, dict)

    assert result.get("symbol") == "authenticate_user"
    assert "file" in result
    assert "line" in result
    assert "end_line" in result
    assert "kind" in result
    assert "docstring" in result
    assert "callers" in result
    assert "callees" in result

    assert isinstance(result["callers"], list)
    assert isinstance(result["callees"], list)
    assert result["kind"] == "function"
    # authenticate_user is called by UserService.validate
    assert "UserService.validate" in result["callers"]


# ── T6: seam_context — unknown symbol ─────────────────────────────────────────


def test_seam_context_unknown_symbol_returns_none(
    seeded_db: tuple[sqlite3.Connection, Path],
) -> None:
    """seam_context must return None (or a null-result dict) for an unknown symbol."""
    conn, root = seeded_db
    result = handle_seam_context(conn, "nonexistent_function_xyz", root)

    # None is the contract; a dict with null indicator is also acceptable
    assert result is None or (isinstance(result, dict) and result.get("symbol") is None)


# ── T7: seam_context INVALID_INPUT ────────────────────────────────────────────


def test_seam_context_blank_symbol_returns_invalid_input(
    seeded_db: tuple[sqlite3.Connection, Path],
) -> None:
    """seam_context must return INVALID_INPUT for whitespace-only symbol."""
    conn, root = seeded_db
    result = handle_seam_context(conn, "   ", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T8: seam_search happy path ────────────────────────────────────────────────


def test_seam_search_returns_results(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_search must return a list of SearchResult-shaped dicts on match."""
    conn, root = seeded_db
    results = handle_seam_search(conn, "authenticate", root, limit=20)

    assert isinstance(results, list)
    assert len(results) > 0

    for item in results:
        assert "symbol" in item
        assert "file" in item
        assert "line" in item
        assert "snippet" in item
        assert "score" in item
        assert isinstance(item["symbol"], str)
        assert isinstance(item["file"], str)
        assert isinstance(item["line"], int)
        assert isinstance(item["snippet"], str)
        assert isinstance(item["score"], float)


# ── T9: seam_search INVALID_INPUT ─────────────────────────────────────────────


def test_seam_search_blank_text_returns_invalid_input(
    seeded_db: tuple[sqlite3.Connection, Path],
) -> None:
    """seam_search must return INVALID_INPUT for whitespace-only text."""
    conn, root = seeded_db
    result = handle_seam_search(conn, "   ", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T10: seam_search INVALID_QUERY ────────────────────────────────────────────


def test_seam_search_fts5_syntax_error_returns_invalid_query(
    seeded_db: tuple[sqlite3.Connection, Path],
) -> None:
    """seam_search must return INVALID_QUERY when the text triggers an FTS5 syntax error."""
    conn, root = seeded_db
    # FTS5 rejects a bare AND — this is a known malformed syntax trigger
    result = handle_seam_search(conn, "AND", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_QUERY"


# ── T11: seam_search limit clamping ───────────────────────────────────────────


def test_seam_search_limit_clamp_high(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_search: limit > 100 must be silently clamped to 100."""
    conn, root = seeded_db
    results = handle_seam_search(conn, "user", root, limit=9999)
    assert isinstance(results, list)


def test_seam_search_file_is_relative(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_search must return relative file paths."""
    conn, root = seeded_db
    results = handle_seam_search(conn, "authenticate", root, limit=20)

    assert len(results) > 0
    for item in results:
        assert not Path(item["file"]).is_absolute(), f"Expected relative path, got: {item['file']}"


def test_seam_context_file_is_relative(seeded_db: tuple[sqlite3.Connection, Path]) -> None:
    """seam_context must return a relative file path."""
    conn, root = seeded_db
    result = handle_seam_context(conn, "authenticate_user", root)

    assert result is not None
    assert not Path(result["file"]).is_absolute(), f"Expected relative path, got: {result['file']}"
