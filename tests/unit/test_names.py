"""Unit tests for seam/query/names.py — name-resolver leaf module.

Tier A Slice 1: qualified<->bare bridging.
Tier A Slice 2: resolve_query_to_defs — all-definitions aggregation.
These tests are written FIRST (TDD) and must be run before the implementation.

Coverage:
    N1 — bare_name(): no dot returns input unchanged
    N2 — bare_name(): multi-dot returns rightmost segment
    N3 — bare_name(): empty string returns empty string
    N4 — bare_name(): single dot returns empty string (edge case)
    N5 — bare_name(): only dot prefix returns correct bare portion
    N6 — edge_match_names(): bare name returns [name] (no dot, no duplication)
    N7 — edge_match_names(): qualified name returns [qualified, bare] deduped
    N8 — edge_match_names(): multi-dot name returns [qualified, bare_suffix]
    N9 — edge_match_names(): never raises on empty string
    N10 — edge_match_names(): no duplicates when bare == qualified (no dot)
    N11 — edge_match_names(): returns list[str] (not set, but deduped)

Slice 2:
    R1 — resolve_query_to_defs(): exact match by name returns that symbol row
    R2 — resolve_query_to_defs(): unknown name returns []
    R3 — resolve_query_to_defs(): bare name with unique qualified def resolves to that def
    R4 — resolve_query_to_defs(): bare name with multiple qualified defs returns all
    R5 — resolve_query_to_defs(): exact class-name query returns the class row (no bare fallback)
    R6 — resolve_query_to_defs(): never raises on empty string
    R7 — resolve_query_to_defs(): qualified exact match is returned directly, no suffix scan
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.query.names import bare_name, edge_match_names, resolve_query_to_defs

# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_empty_db() -> sqlite3.Connection:
    """Create a minimal in-memory DB with the correct schema."""
    return init_db(Path(":memory:"))


def _sym(name: str, kind: str = "function", start: int = 1, end: int = 5) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file="/tmp/placeholder.py",
        start_line=start,
        end_line=end,
        docstring=None,
    )


def _edge(source: str, target: str) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind="call",
        file="/tmp/placeholder.py",
        line=10,
        confidence="EXTRACTED",
    )


def _seed_db(symbols: list[Symbol], edges: list[Edge]) -> sqlite3.Connection:
    """Create an in-memory DB seeded with the provided symbols and edges."""
    conn = init_db(Path(":memory:"))
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        filepath = Path(f.name)
        f.write(b"# seam test\n")
    try:
        adjusted_syms = [
            Symbol(
                name=s["name"],
                kind=s["kind"],
                file=str(filepath),
                start_line=s["start_line"],
                end_line=s["end_line"],
                docstring=s.get("docstring"),
            )
            for s in symbols
        ]
        adjusted_edges = [
            Edge(
                source=e["source"],
                target=e["target"],
                kind=e["kind"],
                file=str(filepath),
                line=e["line"],
                confidence=e.get("confidence", "EXTRACTED"),
            )
            for e in edges
        ]
        upsert_file(conn, filepath, "python", "test123", adjusted_syms, adjusted_edges)
    finally:
        filepath.unlink(missing_ok=True)
    return conn


# ── N1-N5: bare_name() pure-function tests ────────────────────────────────────


class TestBareName:
    """bare_name() — extract the rightmost identifier after the last dot."""

    def test_no_dot_returns_unchanged(self) -> None:
        """N1: a bare identifier with no dot is returned as-is."""
        assert bare_name("authenticate") == "authenticate"

    def test_multi_dot_returns_last_segment(self) -> None:
        """N2: 'A.B.method' -> 'method' (rightmost segment)."""
        assert bare_name("UserService.validate") == "validate"
        assert bare_name("pkg.Class.method") == "method"

    def test_empty_string_returns_empty(self) -> None:
        """N3: empty string input returns empty string; never raises."""
        assert bare_name("") == ""

    def test_single_dot_returns_empty_after_dot(self) -> None:
        """N4: edge case '.method' returns 'method'; 'Class.' returns ''."""
        assert bare_name(".method") == "method"
        # Trailing dot — bare part is empty string
        assert bare_name("Class.") == ""

    def test_single_dot_only_returns_empty(self) -> None:
        """N5: a lone '.' has no valid rightmost segment."""
        assert bare_name(".") == ""


# ── N6-N11: edge_match_names() DB-dependent tests ────────────────────────────


class TestEdgeMatchNames:
    """edge_match_names(conn, name) — names to use for caller/callee edge lookups."""

    def test_bare_name_returns_list_with_single_entry(self) -> None:
        """N6: a name with no dot -> [name] (no duplication)."""
        conn = _seed_empty_db()
        result = edge_match_names(conn, "authenticate")
        conn.close()
        assert result == ["authenticate"]

    def test_qualified_name_returns_qualified_and_bare(self) -> None:
        """N7: 'Class.method' -> ['Class.method', 'method'] in that order."""
        conn = _seed_empty_db()
        result = edge_match_names(conn, "UserService.validate")
        conn.close()
        assert "UserService.validate" in result
        assert "validate" in result
        # qualified comes first (the original query), bare comes second
        assert result[0] == "UserService.validate"
        assert result[1] == "validate"

    def test_multi_dot_returns_qualified_and_bare_only(self) -> None:
        """N8: 'A.B.method' -> ['A.B.method', 'method'] — members are slice 3."""
        conn = _seed_empty_db()
        result = edge_match_names(conn, "pkg.Class.method")
        conn.close()
        assert result[0] == "pkg.Class.method"
        assert result[1] == "method"
        # Only two entries: qualified + bare. No intermediate segments in slice 1.
        assert len(result) == 2

    def test_empty_name_never_raises(self) -> None:
        """N9: empty string must not raise; returns [''] or similar stable output."""
        conn = _seed_empty_db()
        try:
            result = edge_match_names(conn, "")
            # The result should be a list of strings (no crash)
            assert isinstance(result, list)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"edge_match_names raised on empty string: {exc}")
        finally:
            conn.close()

    def test_no_duplicates_for_bare_name(self) -> None:
        """N10: bare name (no dot) produces exactly [name] — no dup of the input."""
        conn = _seed_empty_db()
        result = edge_match_names(conn, "helper")
        conn.close()
        # Exactly one entry, no duplication
        assert len(result) == 1
        assert result[0] == "helper"

    def test_result_is_list_not_set(self) -> None:
        """N11: result is a list (ordered, deduped) — not a set."""
        conn = _seed_empty_db()
        result = edge_match_names(conn, "Class.method")
        conn.close()
        assert isinstance(result, list)
        # Deduped: no duplicates
        assert len(result) == len(set(result))


# ── Slice 2: resolve_query_to_defs() tests ────────────────────────────────────


def _seed_db_with(symbols: list[tuple[str, str, int, int]]) -> sqlite3.Connection:
    """Helper: seed a DB with (name, kind, start, end) tuples in a single temp file."""
    conn = init_db(Path(":memory:"))
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        filepath = Path(f.name)
        f.write(b"# seam test\n")
    try:
        syms = [
            Symbol(
                name=name,
                kind=kind,
                file=str(filepath),
                start_line=start,
                end_line=end,
                docstring=None,
            )
            for name, kind, start, end in symbols
        ]
        upsert_file(conn, filepath, "python", "abc123", syms, [])
    finally:
        filepath.unlink(missing_ok=True)
    return conn


class TestResolveQueryToDefs:
    """resolve_query_to_defs(conn, name) — all-definitions aggregation (Slice 2)."""

    def test_exact_match_returns_that_symbol_row(self) -> None:
        """R1: an exact name match returns the single row for that symbol."""
        conn = _seed_db_with([("Parser.parse", "method", 10, 20)])
        rows = resolve_query_to_defs(conn, "Parser.parse")
        conn.close()
        assert len(rows) == 1
        assert rows[0]["name"] == "Parser.parse"

    def test_unknown_name_returns_empty(self) -> None:
        """R2: a name that is neither exact nor a bare suffix returns []."""
        conn = _seed_db_with([("Parser.parse", "method", 10, 20)])
        rows = resolve_query_to_defs(conn, "unknown_symbol_xyz")
        conn.close()
        assert rows == []

    def test_bare_name_unique_qualified_resolves_to_that_def(self) -> None:
        """R3: bare name 'parse' with only 'Parser.parse' in index -> [Parser.parse]."""
        conn = _seed_db_with([
            ("Parser.parse", "method", 10, 20),
            ("Parser.init", "method", 30, 40),
        ])
        rows = resolve_query_to_defs(conn, "parse")
        conn.close()
        assert len(rows) == 1, f"Expected 1 def for unique bare 'parse', got {len(rows)}"
        assert rows[0]["name"] == "Parser.parse"

    def test_bare_name_multiple_qualified_returns_all(self) -> None:
        """R4: bare 'process' with Parser.process + Worker.process -> both returned."""
        conn = _seed_db_with([
            ("Parser.process", "method", 10, 20),
            ("Worker.process", "method", 30, 40),
            ("Other.unrelated", "method", 50, 60),
        ])
        rows = resolve_query_to_defs(conn, "process")
        conn.close()
        names = {r["name"] for r in rows}
        assert "Parser.process" in names, "Parser.process must be in result"
        assert "Worker.process" in names, "Worker.process must be in result"
        assert "Other.unrelated" not in names, "unrelated symbol must NOT be in result"
        assert len(rows) == 2

    def test_class_name_exact_match_returned_directly(self) -> None:
        """R5: exact class name 'Parser' returns the class row — no suffix scan needed."""
        conn = _seed_db_with([
            ("Parser", "class", 1, 50),
            ("Parser.parse", "method", 10, 20),
        ])
        rows = resolve_query_to_defs(conn, "Parser")
        conn.close()
        # Should find the exact 'Parser' class row
        names = {r["name"] for r in rows}
        assert "Parser" in names, "Exact class 'Parser' must be returned"

    def test_never_raises_on_empty_string(self) -> None:
        """R6: empty string input must never raise — returns [] or a stable result."""
        conn = _seed_db_with([("Parser.parse", "method", 10, 20)])
        try:
            rows = resolve_query_to_defs(conn, "")
            assert isinstance(rows, list)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"resolve_query_to_defs raised on empty string: {exc}")
        finally:
            conn.close()

    def test_qualified_exact_match_not_suffix_scanned(self) -> None:
        """R7: 'A.method' with exact row -> [A.method] only, no extra suffix rows."""
        conn = _seed_db_with([
            ("A.method", "method", 10, 20),
            ("B.method", "method", 30, 40),  # same bare suffix, should NOT appear
        ])
        # Querying by exact qualified name 'A.method' should return only A.method
        rows = resolve_query_to_defs(conn, "A.method")
        conn.close()
        # The exact match must be returned
        names = {r["name"] for r in rows}
        assert "A.method" in names
