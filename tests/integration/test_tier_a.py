"""Integration tests for Tier A Slice 1: qualified<->bare name bridging in context().

These tests exercise the REAL extract->store->read path with a multi-class fixture
to verify that context("Class.method") now returns cross-class callers that were
previously empty, and that unique-name functions remain byte-stable.

Test groups:
    TA1 — context("Class.method") returns cross-class callers/callees
    TA2 — unique-name function with already-matching edges is byte-stable
    TA3 — bare-name lookup still works when only bare edges exist
    TA4 — context("Class.method") callers deduped (no duplicates)
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.query.engine import context

# ── DB seed helpers ───────────────────────────────────────────────────────────


def _sym(
    name: str,
    kind: str = "function",
    start: int = 1,
    end: int = 5,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file="/tmp/placeholder.py",
        start_line=start,
        end_line=end,
        docstring=None,
    )


def _edge(source: str, target: str, kind: str = "call") -> Edge:
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file="/tmp/placeholder.py",
        line=10,
        confidence="EXTRACTED",
    )


def _seed_db(symbols: list[Symbol], edges: list[Edge]) -> sqlite3.Connection:
    """Create an in-memory DB seeded with the provided symbols and edges.

    All symbols/edges are attributed to a single temp file so upsert_file works.
    """
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


# ── Multi-class fixture ───────────────────────────────────────────────────────
#
# Schema:
#   Symbol "Parser.parse"    — method on Parser class
#   Symbol "Renderer.render" — method on Renderer class
#   Symbol "orchestrate"     — plain function
#
# Edges:
#   orchestrate -> "parse"    (bare target — as graph.py stores it)
#   Renderer.render -> "parse" (bare target from another class)
#
# Before Slice 1: context("Parser.parse") callers = [] (no edge targets "Parser.parse")
# After Slice 1:  context("Parser.parse") callers = ["orchestrate", "Renderer.render"]


@pytest.fixture()
def multi_class_db() -> sqlite3.Connection:
    """Fixture: multi-class codebase with cross-class bare-target call edges."""
    symbols = [
        _sym("Parser.parse", kind="method", start=10, end=20),
        _sym("Renderer.render", kind="method", start=30, end=40),
        _sym("orchestrate", kind="function", start=50, end=70),
    ]
    edges = [
        # orchestrate calls parse (bare target — this is how graph.py stores it)
        _edge("orchestrate", "parse"),
        # Renderer.render also calls parse (bare target, cross-class)
        _edge("Renderer.render", "parse"),
        # Parser.parse calls render (bare target)
        _edge("Parser.parse", "render"),
    ]
    return _seed_db(symbols, edges)


# ── TA1: cross-class callers returned for qualified symbol ────────────────────


class TestContextQualifiedBridging:
    """TA1: context("Class.method") returns cross-class callers via bare bridging."""

    def test_qualified_method_has_callers_via_bare_edge(
        self, multi_class_db: sqlite3.Connection
    ) -> None:
        """context("Parser.parse") must return callers that target bare 'parse'."""
        result = context(multi_class_db, "Parser.parse")
        assert result is not None, "Parser.parse should be found in index"
        # The key assertion: callers must be non-empty.
        # Previously this returned [] because no edge had target_name='Parser.parse'.
        assert len(result["callers"]) > 0, (
            "Expected callers for Parser.parse via bare 'parse' edge, got empty list. "
            "This means the qualified<->bare bridging in context() is not working."
        )
        caller_set = set(result["callers"])
        assert "orchestrate" in caller_set, "orchestrate->parse edge should be bridged"
        assert "Renderer.render" in caller_set, "Renderer.render->parse edge should be bridged"

    def test_qualified_method_has_callees_via_bare_edge(
        self, multi_class_db: sqlite3.Connection
    ) -> None:
        """context("Renderer.render") must find callers that used bare 'render' target."""
        result = context(multi_class_db, "Renderer.render")
        assert result is not None
        # Parser.parse -> "render" (bare) should show up as a caller of Renderer.render
        assert "Parser.parse" in result["callers"], (
            "Parser.parse calls 'render' (bare), should resolve to Renderer.render caller"
        )

    def test_qualified_method_callees_via_bare_source(
        self, multi_class_db: sqlite3.Connection
    ) -> None:
        """context("Parser.parse") callees include symbols targeted from Parser.parse edges."""
        result = context(multi_class_db, "Parser.parse")
        assert result is not None
        # Parser.parse -> "render" (bare target) should appear as callee
        assert len(result["callees"]) > 0, "Parser.parse should have callees"


# ── TA2: unique-name function byte-stability (regression guard) ───────────────


class TestUniqueNameByteStable:
    """TA2: a unique-name function with matching edge keys is byte-stable after slice 1."""

    def test_unique_function_callers_unchanged(self) -> None:
        """orchestrate has no dot -> callers still found identically to before."""
        symbols = [
            _sym("orchestrate", kind="function", start=1, end=10),
            _sym("main", kind="function", start=15, end=25),
        ]
        edges = [
            _edge("main", "orchestrate"),  # target_name="orchestrate" matches exactly
        ]
        conn = _seed_db(symbols, edges)
        result = context(conn, "orchestrate")
        conn.close()
        assert result is not None
        # "main" calls "orchestrate" — exact match, must still work
        assert "main" in result["callers"]

    def test_unique_function_callees_unchanged(self) -> None:
        """orchestrate's callees are still found via exact source_name match."""
        symbols = [
            _sym("orchestrate", kind="function", start=1, end=10),
            _sym("helper", kind="function", start=15, end=25),
        ]
        edges = [
            _edge("orchestrate", "helper"),  # source_name="orchestrate" exact match
        ]
        conn = _seed_db(symbols, edges)
        result = context(conn, "orchestrate")
        conn.close()
        assert result is not None
        assert "helper" in result["callees"]

    def test_unique_function_result_fields_intact(self) -> None:
        """All required ContextResult fields are present and have correct types."""
        symbols = [
            _sym("process_data", kind="function", start=1, end=10),
        ]
        conn = _seed_db(symbols, [])
        result = context(conn, "process_data")
        conn.close()
        assert result is not None
        # Verify all required ContextResult fields exist
        assert result["symbol"] == "process_data"
        assert isinstance(result["callers"], list)
        assert isinstance(result["callees"], list)
        assert isinstance(result["ambiguous"], bool)


# ── TA3: bare-name lookup still works ────────────────────────────────────────


class TestBareNameContextStillWorks:
    """TA3: plain (bare) name context still works after edge_match_names introduced."""

    def test_bare_name_context_returns_result(self) -> None:
        """context("helper") with edges using exact "helper" as target still works."""
        symbols = [
            _sym("helper", kind="function", start=1, end=10),
            _sym("caller_fn", kind="function", start=15, end=25),
        ]
        edges = [
            _edge("caller_fn", "helper"),
        ]
        conn = _seed_db(symbols, edges)
        result = context(conn, "helper")
        conn.close()
        assert result is not None
        assert "caller_fn" in result["callers"]


# ── TA4: deduplication — callers list must not contain duplicates ─────────────


class TestCallerDeduplication:
    """TA4: even when both qualified and bare edges match, callers are deduped."""

    def test_no_duplicate_callers_when_both_edges_exist(self) -> None:
        """If an edge exists with BOTH 'Class.method' AND bare 'method' as target,
        the caller should appear only once in context result.
        """
        symbols = [
            _sym("Worker.process", kind="method", start=1, end=10),
            _sym("main", kind="function", start=15, end=25),
        ]
        edges = [
            # main calls 'process' (bare) — what graph.py stores
            _edge("main", "process"),
            # Also a direct qualified edge (less common but possible)
            _edge("main", "Worker.process"),
        ]
        conn = _seed_db(symbols, edges)
        result = context(conn, "Worker.process")
        conn.close()
        assert result is not None
        # "main" should appear at most once even though two edges target the method
        callers = result["callers"]
        assert callers.count("main") <= 1, (
            f"'main' appears {callers.count('main')} times in callers — must be deduped"
        )


# ── Slice 2: bare-name resolution and multi-def aggregation in context() ──────
#
# The test fixture below has:
#   Symbols: "TTS.speakText" and "AudioPlayer.speakText" (same bare suffix, 2 classes)
#   Edges:
#     "main" -> "speakText" (bare — as graph.py would store it)
#
# Slice 2 target behaviors:
#   TA5 — context("speakText") resolves (was found:false before), returns callers
#   TA6 — context("speakText") with 2 qualified defs sets ambiguous=True
#   TA7 — context("speakText") with unique qualified def is unambiguous, callers merged
#   TA8 — unique qualified symbol remains byte-stable (single def, ambiguous unchanged)


@pytest.fixture()
def multi_class_speaktext_db() -> sqlite3.Connection:
    """Two classes both with a 'speakText' method; caller uses bare 'speakText'."""
    symbols = [
        _sym("TTS.speakText", kind="method", start=10, end=20),
        _sym("AudioPlayer.speakText", kind="method", start=30, end=40),
        _sym("main", kind="function", start=50, end=70),
    ]
    edges = [
        # main calls speakText bare — as graph.py stores it
        _edge("main", "speakText"),
    ]
    return _seed_db(symbols, edges)


@pytest.fixture()
def unique_speaktext_db() -> sqlite3.Connection:
    """Single class with 'speakText' method; bare query should resolve to it uniquely."""
    symbols = [
        _sym("ElevenLabsTTSClient.speakText", kind="method", start=10, end=20),
        _sym("main", kind="function", start=50, end=70),
    ]
    edges = [
        _edge("main", "speakText"),
    ]
    return _seed_db(symbols, edges)


class TestBareNameResolutionInContext:
    """TA5-TA8: context() bare-name resolution resolving to qualified defs."""

    def test_bare_name_previously_not_found_now_found(
        self, unique_speaktext_db: sqlite3.Connection
    ) -> None:
        """TA5: context('speakText') no longer returns None (was found:false before)."""
        result = context(unique_speaktext_db, "speakText")
        assert result is not None, (
            "context('speakText') should resolve to ElevenLabsTTSClient.speakText, "
            "not return None. Bare-name resolution not working."
        )

    def test_unique_bare_resolution_returns_callers(
        self, unique_speaktext_db: sqlite3.Connection
    ) -> None:
        """TA7: unique bare 'speakText' -> resolves to unique def, caller 'main' returned."""
        result = context(unique_speaktext_db, "speakText")
        assert result is not None
        assert "main" in result["callers"], (
            "main->speakText edge should appear in callers after bare-name resolution"
        )

    def test_unique_bare_resolution_not_ambiguous(
        self, unique_speaktext_db: sqlite3.Connection
    ) -> None:
        """TA7: a bare name resolving to exactly ONE qualified def is NOT ambiguous."""
        result = context(unique_speaktext_db, "speakText")
        assert result is not None
        assert result["ambiguous"] is False, (
            "A bare name resolving to a single unique qualified def should NOT be ambiguous"
        )

    def test_homonym_bare_resolution_is_ambiguous(
        self, multi_class_speaktext_db: sqlite3.Connection
    ) -> None:
        """TA6: bare 'speakText' resolving to TTS.speakText AND AudioPlayer.speakText
        must set ambiguous=True."""
        result = context(multi_class_speaktext_db, "speakText")
        assert result is not None, "context('speakText') must find at least one def"
        assert result["ambiguous"] is True, (
            "Multiple qualified defs for bare 'speakText' must be marked ambiguous"
        )

    def test_homonym_bare_resolution_merges_callers(
        self, multi_class_speaktext_db: sqlite3.Connection
    ) -> None:
        """TA6: callers are merged across all resolved defs (main calls speakText bare)."""
        result = context(multi_class_speaktext_db, "speakText")
        assert result is not None
        assert "main" in result["callers"], (
            "Caller 'main' must appear in merged callers even with multiple defs"
        )

    def test_unique_exact_match_unchanged(self) -> None:
        """TA8: a unique exact-name match is byte-stable — behavior unchanged."""
        symbols = [
            _sym("unique_fn", kind="function", start=1, end=10),
            _sym("caller", kind="function", start=15, end=25),
        ]
        edges = [_edge("caller", "unique_fn")]
        conn = _seed_db(symbols, edges)
        result = context(conn, "unique_fn")
        conn.close()
        assert result is not None
        assert result["symbol"] == "unique_fn"
        assert result["ambiguous"] is False
        assert "caller" in result["callers"]

    def test_callers_deduped_across_multi_def_resolution(
        self, multi_class_speaktext_db: sqlite3.Connection
    ) -> None:
        """TA6: callers are deduped when the same caller appears for multiple defs."""
        result = context(multi_class_speaktext_db, "speakText")
        assert result is not None
        callers = result["callers"]
        # "main" may appear for both TTS.speakText and AudioPlayer.speakText via bare edge;
        # it must appear at most once after dedup
        assert callers.count("main") <= 1, (
            f"'main' appears {callers.count('main')} times — must be deduped across defs"
        )
