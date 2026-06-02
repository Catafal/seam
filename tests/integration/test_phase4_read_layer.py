"""Integration tests for Phase 4 read layer — engine + MCP handler passthrough.

Slice 3 TDD: Tests written before implementation (RED phase).

Test groups:
    R1 — engine.context() returns all 5 new fields with correct values.
    R2 — engine.search() / engine.query() return new fields.
    R3 — FTS signature search: query matching only on signature param/return type surfaces symbol.
    R4 — MCP handler passthrough: seam_context/search/query JSON includes new fields.
    R5 — Stable shape when Phase 4 fields are NULL (backward compat for pre-v5 rows).
    R6 — rescore() signature boost: terms in signature get a small boost (optional).
"""

from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Symbol
from seam.query import engine
from seam.server.tools import handle_seam_context, handle_seam_query, handle_seam_search

# ── DB fixture ────────────────────────────────────────────────────────────────


def _sym_v5(
    name: str,
    kind: str,
    file: str,
    signature: str | None = None,
    decorators: list[str] | None = None,
    is_exported: bool | None = None,
    visibility: str | None = None,
    qualified_name: str | None = None,
    docstring: str | None = None,
) -> Symbol:
    """Build a v5 Symbol with all Phase 4 fields."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=1,
        end_line=10,
        docstring=docstring,
        signature=signature,
        decorators=decorators if decorators is not None else [],
        is_exported=is_exported,
        visibility=visibility,
        qualified_name=qualified_name,
    )


@pytest.fixture()
def enriched_db(tmp_path: Path):
    """DB seeded with symbols that have full Phase 4 enrichment."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    src = tmp_path / "src.py"
    src.write_text("# stub\n")

    symbols = [
        _sym_v5(
            "run_affected",
            "function",
            str(src),
            signature="def run_affected(conn: Connection, changed_files: list[str]) -> AffectedResult",
            decorators=[],
            is_exported=True,
            visibility="public",
            qualified_name="run_affected",
            docstring="Run affected-tests analysis.",
        ),
        _sym_v5(
            "MyClass.secret_method",
            "method",
            str(src),
            signature="def secret_method(self) -> None",
            decorators=["@app.route('/secret')", "@login_required"],
            is_exported=False,
            visibility="private",
            qualified_name="MyClass.secret_method",
            docstring=None,
        ),
        _sym_v5(
            "PublicHelper",
            "class",
            str(src),
            signature="class PublicHelper(BaseHelper)",
            decorators=["@dataclass"],
            is_exported=True,
            visibility="public",
            qualified_name="PublicHelper",
            docstring="Public helper class.",
        ),
        # A symbol with NULL Phase 4 fields (backward compat row)
        Symbol(
            name="legacy_symbol",
            kind="function",
            file=str(src),
            start_line=100,
            end_line=110,
            docstring="Legacy without enrichment.",
            signature=None,
            decorators=[],
            is_exported=None,
            visibility=None,
            qualified_name=None,
        ),
    ]
    upsert_file(conn, src, "python", "abc", symbols, [])
    yield conn, tmp_path
    conn.close()


# ── R1: engine.context() returns Phase 4 fields ───────────────────────────────


class TestEngineContextPhase4Fields:
    """R1: engine.context() includes all 5 new fields in the result."""

    def test_context_returns_signature(self, enriched_db) -> None:
        """context() includes signature field."""
        conn, _ = enriched_db
        result = engine.context(conn, "run_affected")
        assert result is not None
        assert "signature" in result
        assert result["signature"] == (
            "def run_affected(conn: Connection, changed_files: list[str]) -> AffectedResult"
        )

    def test_context_returns_decorators_as_list(self, enriched_db) -> None:
        """context() returns decorators as a Python list (not JSON string)."""
        conn, _ = enriched_db
        result = engine.context(conn, "MyClass.secret_method")
        assert result is not None
        assert "decorators" in result
        assert isinstance(result["decorators"], list)
        assert "@app.route('/secret')" in result["decorators"]
        assert "@login_required" in result["decorators"]

    def test_context_returns_is_exported(self, enriched_db) -> None:
        """context() includes is_exported field."""
        conn, _ = enriched_db
        result = engine.context(conn, "run_affected")
        assert result is not None
        assert "is_exported" in result
        assert result["is_exported"] is True

    def test_context_returns_visibility(self, enriched_db) -> None:
        """context() includes visibility field."""
        conn, _ = enriched_db
        result = engine.context(conn, "MyClass.secret_method")
        assert result is not None
        assert "visibility" in result
        assert result["visibility"] == "private"

    def test_context_returns_qualified_name(self, enriched_db) -> None:
        """context() includes qualified_name field."""
        conn, _ = enriched_db
        result = engine.context(conn, "MyClass.secret_method")
        assert result is not None
        assert "qualified_name" in result
        assert result["qualified_name"] == "MyClass.secret_method"

    def test_context_null_fields_when_legacy(self, enriched_db) -> None:
        """context() returns None/null for Phase 4 fields on pre-v5 symbols."""
        conn, _ = enriched_db
        result = engine.context(conn, "legacy_symbol")
        assert result is not None
        # signature is NULL for legacy rows
        assert result["signature"] is None
        assert result["is_exported"] is None
        assert result["visibility"] is None
        assert result["qualified_name"] is None

    def test_context_empty_decorators_when_none_stored(self, enriched_db) -> None:
        """context() returns [] for decorators when stored as '[]' or NULL."""
        conn, _ = enriched_db
        # legacy_symbol has decorators stored as '[]' → should come back as []
        result = engine.context(conn, "legacy_symbol")
        assert result is not None
        assert isinstance(result["decorators"], list)

    def test_context_existing_fields_still_present(self, enriched_db) -> None:
        """context() still returns cluster_id, cluster_label, cluster_peers, callers, callees."""
        conn, _ = enriched_db
        result = engine.context(conn, "run_affected")
        assert result is not None
        # Phase 2 fields
        assert "cluster_id" in result
        assert "cluster_label" in result
        assert "cluster_peers" in result
        # Pre-Phase-4 fields
        assert "callers" in result
        assert "callees" in result
        assert "kind" in result
        assert "docstring" in result


# ── R2: engine.search() and engine.query() return Phase 4 fields ─────────────


class TestEngineSearchQueryPhase4Fields:
    """R2: engine.search() and engine.query() include Phase 4 fields in results."""

    def test_search_results_contain_phase4_fields_via_context(self, enriched_db) -> None:
        """Searching for a symbol and fetching its context returns Phase 4 fields."""
        conn, _ = enriched_db
        results = engine.search(conn, "affected", limit=5)
        # Find the run_affected result
        hit = next((r for r in results if "run_affected" in r["symbol"]), None)
        assert hit is not None, "Expected run_affected in search results"
        # Verify the symbol can be looked up with Phase 4 fields
        ctx = engine.context(conn, hit["symbol"])
        assert ctx is not None
        assert ctx["signature"] is not None

    def test_search_result_shape_unchanged(self, enriched_db) -> None:
        """search() result shape is unchanged — existing keys still present."""
        conn, _ = enriched_db
        results = engine.search(conn, "affected", limit=5)
        assert len(results) > 0
        for r in results:
            assert "symbol" in r
            assert "file" in r
            assert "line" in r
            assert "snippet" in r
            assert "score" in r


# ── R3: FTS signature search (headline new capability) ────────────────────────


class TestFTSSignatureSearchCapability:
    """R3: Queries on signature param/return types surface the right symbol."""

    def test_query_on_signature_type_finds_symbol(self, enriched_db) -> None:
        """A query matching ONLY a type in the signature (not name/docstring) finds the symbol."""
        conn, _ = enriched_db
        # 'AffectedResult' appears ONLY in the signature, not the name or docstring
        results = engine.search(conn, "AffectedResult", limit=10)
        names = [r["symbol"] for r in results]
        assert "run_affected" in names, (
            f"Expected run_affected in results for AffectedResult search, got: {names}"
        )

    def test_query_on_return_type_finds_symbol(self, enriched_db) -> None:
        """Query on a return type in signature surfaces the function."""
        conn, _ = enriched_db
        # 'AffectedResult' is the return type in run_affected's signature
        results = engine.search(conn, "AffectedResult", limit=10)
        assert len(results) > 0, "Expected at least one result for signature type search"

    def test_query_on_param_type_finds_symbol(self, enriched_db) -> None:
        """Query on a parameter type in signature surfaces the function."""
        conn, _ = enriched_db
        # 'Connection' is a param type in run_affected's signature
        results = engine.search(conn, "Connection", limit=10)
        names = [r["symbol"] for r in results]
        assert "run_affected" in names, (
            f"Expected run_affected in results for Connection param type search, got: {names}"
        )

    def test_name_search_still_works(self, enriched_db) -> None:
        """Name-based search still returns the right symbol after FTS rebuild."""
        conn, _ = enriched_db
        results = engine.search(conn, "run_affected", limit=10)
        names = [r["symbol"] for r in results]
        assert "run_affected" in names, f"Name search broken: {names}"


# ── R4: MCP handler passthrough ───────────────────────────────────────────────


class TestMCPHandlerPassthrough:
    """R4: seam_context/search/query JSON includes Phase 4 fields."""

    def test_seam_context_includes_signature(self, enriched_db) -> None:
        """handle_seam_context output includes signature field."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "run_affected", root)
        assert result is not None
        assert isinstance(result, dict)
        assert "signature" in result
        assert result["signature"] is not None

    def test_seam_context_includes_decorators(self, enriched_db) -> None:
        """handle_seam_context output includes decorators as list."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "MyClass.secret_method", root)
        assert result is not None
        assert "decorators" in result
        assert isinstance(result["decorators"], list)
        assert len(result["decorators"]) == 2

    def test_seam_context_includes_is_exported(self, enriched_db) -> None:
        """handle_seam_context output includes is_exported field."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "run_affected", root)
        assert result is not None
        assert "is_exported" in result
        assert result["is_exported"] is True

    def test_seam_context_includes_visibility(self, enriched_db) -> None:
        """handle_seam_context output includes visibility field."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "run_affected", root)
        assert result is not None
        assert "visibility" in result
        assert result["visibility"] == "public"

    def test_seam_context_includes_qualified_name(self, enriched_db) -> None:
        """handle_seam_context output includes qualified_name field."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "run_affected", root)
        assert result is not None
        assert "qualified_name" in result
        assert result["qualified_name"] == "run_affected"

    def test_seam_context_null_fields_stable(self, enriched_db) -> None:
        """handle_seam_context with NULL Phase 4 fields returns null, not KeyError."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "legacy_symbol", root)
        assert result is not None
        assert result["signature"] is None
        assert result["is_exported"] is None
        assert result["visibility"] is None

    def test_seam_context_existing_fields_intact(self, enriched_db) -> None:
        """handle_seam_context still returns all pre-Phase-4 fields."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "run_affected", root)
        assert result is not None
        for key in ("symbol", "file", "line", "end_line", "kind", "docstring",
                    "callers", "callees", "ambiguous", "cluster_id", "cluster_label",
                    "cluster_peers"):
            assert key in result, f"Missing expected key: {key}"

    def test_seam_search_result_shape_unchanged(self, enriched_db) -> None:
        """handle_seam_search result shape is unchanged — existing keys still present."""
        conn, root = enriched_db
        results = handle_seam_search(conn, "run_affected", root, limit=5)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "symbol" in r
            assert "file" in r
            assert "line" in r
            assert "snippet" in r
            assert "score" in r

    def test_seam_query_result_shape_unchanged(self, enriched_db) -> None:
        """handle_seam_query result shape is unchanged — existing keys still present."""
        conn, root = enriched_db
        results = handle_seam_query(conn, "run_affected", root, limit=5)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "symbol" in r
            assert "file" in r
            assert "line" in r
            assert "score" in r
            assert "callers_count" in r
            assert "callees_count" in r


# ── R5: Stable shape with NULL fields ─────────────────────────────────────────


class TestNullFieldsStableShape:
    """R5: All read paths handle NULL Phase 4 fields without errors."""

    def test_context_with_null_fields_no_exception(self, enriched_db) -> None:
        """context() on a symbol with NULL Phase 4 fields does not raise."""
        conn, _ = enriched_db
        # Should not raise
        result = engine.context(conn, "legacy_symbol")
        assert result is not None

    def test_handler_with_null_fields_no_exception(self, enriched_db) -> None:
        """handle_seam_context on a symbol with NULL Phase 4 fields does not raise."""
        conn, root = enriched_db
        result = handle_seam_context(conn, "legacy_symbol", root)
        assert result is not None
        assert isinstance(result, dict)


# ── R6: rescore signature boost ───────────────────────────────────────────────


class TestRescoreSignatureBoost:
    """R6: Signature boost in rescore() — terms in signature get additive boost."""

    def test_signature_boost_does_not_break_rescore(self, enriched_db) -> None:
        """rescore() with signature-bearing rows does not raise."""
        from seam.query.fts import rescore

        rows = [
            {
                "symbol": "run_affected",
                "file": "/src.py",
                "line": 1,
                "score": 5.0,
                "cluster_id": None,
                "signature": "def run_affected(conn: Connection) -> AffectedResult",
            },
            {
                "symbol": "other_func",
                "file": "/src.py",
                "line": 2,
                "score": 3.0,
                "cluster_id": None,
                "signature": None,
            },
        ]
        terms = ["connection", "affected"]
        result = rescore(rows, terms)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_signature_boost_does_not_demote_exact_name_match(self, enriched_db) -> None:
        """A symbol matching by name is never outranked by a signature-only match."""
        from seam.query.fts import rescore

        rows = [
            {
                # Exact name match — should always rank highly
                "symbol": "affected",
                "file": "/src.py",
                "line": 1,
                "score": 5.0,
                "cluster_id": None,
                "signature": None,
            },
            {
                # Has 'affected' in signature but not in name
                "symbol": "some_other_func",
                "file": "/src.py",
                "line": 2,
                "score": 1.0,
                "cluster_id": None,
                "signature": "def some_other_func() -> AffectedResult",
            },
        ]
        terms = ["affected"]
        result = rescore(rows, terms)
        # The exact name match ('affected') should be first
        assert result[0]["symbol"] == "affected", (
            f"Exact name match should be first; got {result[0]['symbol']}"
        )

    def test_no_signature_rows_still_work(self) -> None:
        """rescore() on rows without signature key works correctly (backward compat)."""
        from seam.query.fts import rescore

        rows = [
            {"symbol": "foo", "file": "/a.py", "line": 1, "score": 3.0, "cluster_id": None},
            {"symbol": "bar", "file": "/b.py", "line": 2, "score": 1.0, "cluster_id": None},
        ]
        result = rescore(rows, ["foo"])
        assert len(result) == 2
        assert result[0]["symbol"] == "foo"  # exact name match first
