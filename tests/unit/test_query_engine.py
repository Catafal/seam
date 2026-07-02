"""Tests for seam/query/engine.py — Query Engine (Track A).

All tests use an in-memory SQLite DB seeded via db.upsert_file.
Symbols and edges are hand-built — do NOT import parser or graph.

Test groups:
    A3 — search(): FTS5 BM25 keyword search
    A4 — query(): FTS5 seed + 1-hop graph expansion
    A5 — context(): symbol lookup with callers + callees
"""

import tempfile
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.query.engine import context, query, search

# ── Shared seed helper ────────────────────────────────────────────────────────


def seed_db(symbols: list[Symbol], edges: list[Edge]) -> object:
    """Create a :memory: DB and upsert a single synthetic file.

    Returns an open sqlite3.Connection seeded with the provided data.
    Caller is responsible for closing.
    """
    conn = init_db(Path(":memory:"))
    # We need a real file for stat(); create one temp file and reuse its path
    # for all symbols/edges (file field is ignored by db.upsert_file for stat).
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        filepath = Path(f.name)
        f.write(b"# seam test\n")
    try:
        # Override symbol file fields to use the real filepath
        adjusted_syms: list[Symbol] = [
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
        adjusted_edges: list[Edge] = [
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


def _sym(
    name: str,
    kind: str = "function",
    start: int = 1,
    end: int = 5,
    doc: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file="/tmp/placeholder.py",
        start_line=start,
        end_line=end,
        docstring=doc,
    )


def _edge(source: str, target: str, kind: str = "call", line: int = 10, confidence: str = "EXTRACTED") -> Edge:
    return Edge(source=source, target=target, kind=kind, file="/tmp/placeholder.py", line=line, confidence=confidence)


# ── A3 — search() ────────────────────────────────────────────────────────────


class TestSearch:
    """A3: FTS5 BM25 full-text search across symbol names and docstrings."""

    def test_search_by_name_returns_result(self) -> None:
        """search() returns a result when querying for a symbol name."""
        conn = seed_db([_sym("authenticate_user")], [])
        results = search(conn, "authenticate_user")
        conn.close()
        assert len(results) == 1
        assert results[0]["symbol"] == "authenticate_user"

    def test_search_returns_correct_fields(self) -> None:
        """search() result dict has symbol, file, line, snippet, score fields."""
        conn = seed_db([_sym("parse_token", start=3)], [])
        results = search(conn, "parse_token")
        conn.close()
        assert len(results) >= 1
        r = results[0]
        assert "symbol" in r
        assert "file" in r
        assert "line" in r
        assert "snippet" in r
        assert "score" in r

    def test_search_by_docstring_keyword(self) -> None:
        """search() finds a symbol by a keyword in its docstring."""
        sym = _sym("process_data", doc="Validates the authentication token payload")
        conn = seed_db([sym], [])
        results = search(conn, "authentication")
        conn.close()
        assert len(results) == 1
        assert results[0]["symbol"] == "process_data"

    def test_search_empty_for_no_match(self) -> None:
        """search() returns an empty list when no symbols match."""
        conn = seed_db([_sym("func_a")], [])
        results = search(conn, "xyznonexistent")
        conn.close()
        assert results == []

    def test_search_returns_list_of_search_result(self) -> None:
        """search() return type is list[SearchResult]."""
        conn = seed_db([_sym("my_function")], [])
        results = search(conn, "my_function")
        conn.close()
        assert isinstance(results, list)
        if results:
            r = results[0]
            # Validate it matches SearchResult shape
            assert isinstance(r["symbol"], str)
            assert isinstance(r["file"], str)
            assert isinstance(r["line"], int)
            assert isinstance(r["snippet"], str)
            assert isinstance(r["score"], float)

    def test_search_respects_limit(self) -> None:
        """search() returns at most `limit` results."""
        syms = [_sym(f"func_{i}") for i in range(10)]
        conn = seed_db(syms, [])
        results = search(conn, "func", limit=3)
        conn.close()
        assert len(results) <= 3

    def test_search_bm25_score_is_float(self) -> None:
        """BM25 score field is a float (may be negative — BM25 returns lower=better)."""
        conn = seed_db([_sym("score_test")], [])
        results = search(conn, "score_test")
        conn.close()
        assert len(results) == 1
        assert isinstance(results[0]["score"], float)


# ── A4 — query() ─────────────────────────────────────────────────────────────


class TestQuery:
    """A4: FTS5 seed match + 1-hop graph expansion."""

    def test_query_returns_seed_matches(self) -> None:
        """query() returns symbols that match the concept term."""
        conn = seed_db([_sym("validate_input")], [])
        results = query(conn, "validate_input")
        conn.close()
        names = [r["symbol"] for r in results]
        assert "validate_input" in names

    def test_query_returns_query_result_shape(self) -> None:
        """query() returns list[QueryResult] with all required fields."""
        conn = seed_db([_sym("func_x")], [])
        results = query(conn, "func_x")
        conn.close()
        assert isinstance(results, list)
        if results:
            r = results[0]
            assert "symbol" in r
            assert "file" in r
            assert "line" in r
            assert "score" in r
            assert "callers_count" in r
            assert "callees_count" in r

    def test_query_callers_count_correct(self) -> None:
        """callers_count = number of edges where target_name = symbol name."""
        # func_b is called by func_a and func_c (2 callers)
        syms = [_sym("func_a"), _sym("func_b"), _sym("func_c")]
        edges = [
            _edge("func_a", "func_b"),
            _edge("func_c", "func_b"),
        ]
        conn = seed_db(syms, edges)
        results = query(conn, "func_b")
        conn.close()
        r = next((x for x in results if x["symbol"] == "func_b"), None)
        assert r is not None
        assert r["callers_count"] == 2

    def test_query_callees_count_correct(self) -> None:
        """callees_count = number of edges where source_name = symbol name."""
        # func_a calls func_b and func_c (2 callees)
        syms = [_sym("func_a"), _sym("func_b"), _sym("func_c")]
        edges = [
            _edge("func_a", "func_b"),
            _edge("func_a", "func_c"),
        ]
        conn = seed_db(syms, edges)
        results = query(conn, "func_a")
        conn.close()
        r = next((x for x in results if x["symbol"] == "func_a"), None)
        assert r is not None
        assert r["callees_count"] == 2

    def test_query_includes_1hop_neighbor(self) -> None:
        """query() includes a 1-hop neighbor not directly matching the FTS term."""
        # Search for 'login' — login_handler matches FTS
        # login_handler calls session_create (1-hop neighbor)
        syms = [_sym("login_handler"), _sym("session_create")]
        edges = [_edge("login_handler", "session_create")]
        conn = seed_db(syms, edges)
        results = query(conn, "login_handler")
        conn.close()
        names = [r["symbol"] for r in results]
        assert "session_create" in names

    def test_query_deduplicates(self) -> None:
        """query() does not return duplicate symbols even if reached via FTS + hop."""
        # func_a matches FTS AND is a 1-hop neighbor of login_handler
        syms = [_sym("login_handler"), _sym("func_a")]
        edges = [_edge("login_handler", "func_a")]
        conn = seed_db(syms, edges)
        results = query(conn, "func_a")
        conn.close()
        names = [r["symbol"] for r in results]
        # func_a should appear only once
        assert names.count("func_a") == 1

    def test_query_empty_for_no_match(self) -> None:
        """query() returns [] when concept has no FTS match and no neighbors."""
        conn = seed_db([_sym("unrelated")], [])
        results = query(conn, "xyznonexistent99")
        conn.close()
        assert results == []

    def test_query_respects_limit(self) -> None:
        """query() returns at most `limit` results."""
        syms = [_sym(f"func_{i}") for i in range(20)]
        conn = seed_db(syms, [])
        results = query(conn, "func", limit=5)
        conn.close()
        assert len(results) <= 5


# ── A5 — context() ───────────────────────────────────────────────────────────


class TestContext:
    """A5: 360-degree symbol lookup — location, callers, callees, docstring."""

    def test_context_returns_none_for_unknown(self) -> None:
        """context() returns None when the symbol is not in the index."""
        conn = seed_db([], [])
        result = context(conn, "nonexistent_symbol")
        conn.close()
        assert result is None

    def test_context_returns_context_result_shape(self) -> None:
        """context() returns a ContextResult with all required fields."""
        conn = seed_db([_sym("my_function", start=10, end=20, doc="Does stuff")], [])
        result = context(conn, "my_function")
        conn.close()
        assert result is not None
        assert "symbol" in result
        assert "file" in result
        assert "line" in result
        assert "end_line" in result
        assert "kind" in result
        assert "docstring" in result
        assert "callers" in result
        assert "callees" in result

    def test_context_symbol_name_matches(self) -> None:
        """context() result contains the correct symbol name."""
        conn = seed_db([_sym("target_function")], [])
        result = context(conn, "target_function")
        conn.close()
        assert result is not None
        assert result["symbol"] == "target_function"

    def test_context_line_number(self) -> None:
        """context() returns correct start_line."""
        conn = seed_db([_sym("line_test", start=42, end=55)], [])
        result = context(conn, "line_test")
        conn.close()
        assert result is not None
        assert result["line"] == 42
        assert result["end_line"] == 55

    def test_context_docstring(self) -> None:
        """context() returns the docstring if present."""
        conn = seed_db([_sym("doc_func", doc="This function does X")], [])
        result = context(conn, "doc_func")
        conn.close()
        assert result is not None
        assert result["docstring"] == "This function does X"

    def test_context_docstring_none(self) -> None:
        """context() returns docstring=None when no docstring was indexed."""
        conn = seed_db([_sym("no_doc_func", doc=None)], [])
        result = context(conn, "no_doc_func")
        conn.close()
        assert result is not None
        assert result["docstring"] is None

    def test_context_callers_list(self) -> None:
        """context() callers contains all source_names from edges targeting this symbol."""
        syms = [_sym("func_a"), _sym("func_b"), _sym("func_c")]
        edges = [
            _edge("func_b", "func_a"),  # func_b calls func_a
            _edge("func_c", "func_a"),  # func_c calls func_a
        ]
        conn = seed_db(syms, edges)
        result = context(conn, "func_a")
        conn.close()
        assert result is not None
        assert set(result["callers"]) == {"func_b", "func_c"}

    def test_context_callees_list(self) -> None:
        """context() callees contains all target_names from edges sourced from this symbol."""
        syms = [_sym("func_a"), _sym("func_b"), _sym("func_c")]
        edges = [
            _edge("func_a", "func_b"),  # func_a calls func_b
            _edge("func_a", "func_c"),  # func_a calls func_c
        ]
        conn = seed_db(syms, edges)
        result = context(conn, "func_a")
        conn.close()
        assert result is not None
        assert set(result["callees"]) == {"func_b", "func_c"}

    def test_context_empty_callers_and_callees(self) -> None:
        """context() returns empty lists when symbol has no edges."""
        conn = seed_db([_sym("isolated_func")], [])
        result = context(conn, "isolated_func")
        conn.close()
        assert result is not None
        assert result["callers"] == []
        assert result["callees"] == []

    def test_context_surfaces_test_edges_separately(self) -> None:
        """P3.3 test evidence is available without replacing callers/callees."""
        conn = seed_db(
            [_sym("entry"), _sym("test_entry")],
            [_edge("test_entry", "entry", kind="tests")],
        )

        production = context(conn, "entry")
        test = context(conn, "test_entry")
        conn.close()

        assert production is not None
        assert production["test_callers"] == ["test_entry"]
        assert production["tested_symbols"] == []
        assert test is not None
        assert test["test_callers"] == []
        assert test["tested_symbols"] == ["entry"]


# ── decode_enrichment_fields: shared helper (Fix G) ────────────────────────────


class TestDecodeEnrichmentFields:
    """decode_enrichment_fields(row) returns (decorators, is_exported) correctly.

    This helper is extracted from context() so pack._enrich_neighbors can reuse
    the same decode logic without reimplementing it. Both callers must agree on
    null/0/1 → bool | None and JSON TEXT → list[str] semantics.

    Rows are passed as dicts (sqlite3.Row supports __getitem__ the same way).
    """

    def test_null_decorators_returns_empty_list(self) -> None:
        """None decorators (pre-v5 or absent) decode to empty list."""
        from seam.query.engine import decode_enrichment_fields

        decs, is_exp = decode_enrichment_fields({"decorators": None, "is_exported": None})  # type: ignore[arg-type]
        assert decs == []
        assert is_exp is None

    def test_json_decorators_decoded_to_list(self) -> None:
        """JSON TEXT decorators are decoded back to list."""
        import json

        from seam.query.engine import decode_enrichment_fields

        row = {"decorators": json.dumps(["@property", "@classmethod"]), "is_exported": 1}
        decs, is_exp = decode_enrichment_fields(row)  # type: ignore[arg-type]
        assert decs == ["@property", "@classmethod"]
        assert is_exp is True

    def test_zero_is_exported_returns_false(self) -> None:
        """is_exported=0 (SQLite integer) decodes to False."""
        from seam.query.engine import decode_enrichment_fields

        _, is_exp = decode_enrichment_fields({"decorators": None, "is_exported": 0})  # type: ignore[arg-type]
        assert is_exp is False

    def test_corrupt_json_decorators_degrades_to_empty(self) -> None:
        """Corrupted JSON in decorators column degrades to empty list, never raises."""
        from seam.query.engine import decode_enrichment_fields

        decs, is_exp = decode_enrichment_fields({"decorators": "NOT_VALID_JSON{{{", "is_exported": None})  # type: ignore[arg-type]
        assert decs == []
