"""Unit tests for Phase 8 Feature 1 — lean output / verbose flag.

Tests verify the observable shape of handler output under each flag combination.
No internal helper calls are tested — only key presence/absence.

Test groups:
    LO1 — _apply_verbosity helper strips heavy fields when verbose=False
    LO2 — verbose=True (default) leaves output byte-identical to pre-Phase-8
    LO3 — verbose=False strips the 6 heavy keys; keeps signature + core fields
    LO4 — stripping reaches nested structures (impact entries, trace hops,
           context_pack neighbors), not just top-level
    LO5 — handle_seam_context verbose flag
    LO6 — handle_seam_query verbose flag
    LO7 — handle_seam_trace verbose flag
    LO8 — handle_seam_impact verbose flag (entry-level stripping)
    LO9 — handle_seam_context_pack verbose flag (target + neighbor stripping)
"""

from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import (
    _apply_verbosity,
    handle_seam_context,
    handle_seam_context_pack,
    handle_seam_impact,
    handle_seam_query,
    handle_seam_trace,
)

# ── Constants ─────────────────────────────────────────────────────────────────

# The 6 heavy fields that must be ABSENT in lean mode (key not present, not null)
HEAVY_FIELDS = {
    "decorators",
    "is_exported",
    "visibility",
    "qualified_name",
    "resolved_by",
    "best_candidate",
}

# Core identity fields that must always be present when the record is a real symbol
# (not an error dict)
CORE_FIELDS_CONTEXT = {
    "symbol", "file", "line", "end_line", "kind", "docstring",
    "callers", "callees", "ambiguous", "cluster_id", "cluster_label",
    "cluster_peers", "signature",
}

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    """Build a Symbol with all optional fields populated (so lean stripping is testable)."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring="A docstring.",
        signature=f"def {name}() -> None",
        decorators=["@classmethod"],
        is_exported=True,
        visibility="public",
        qualified_name=f"module.{name}",
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call",
        file=file, line=1, confidence="INFERRED",
    )


def _make_db(tmp_path: Path):
    """Build a minimal indexed DB: foo calls bar; bar calls baz."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    src = tmp_path / "src.py"
    src.write_text("def foo(): bar()\ndef bar(): baz()\ndef baz(): pass\n")

    upsert_file(
        conn, src, "python", "h1",
        [
            _sym("foo", str(src), line=1),
            _sym("bar", str(src), line=2),
            _sym("baz", str(src), line=3),
        ],
        [
            _edge("foo", "bar", str(src)),
            _edge("bar", "baz", str(src)),
        ],
    )
    return conn, tmp_path, src


# ── LO1: _apply_verbosity helper ──────────────────────────────────────────────


class TestApplyVerbosity:
    """Direct tests for the _apply_verbosity helper function."""

    def test_verbose_true_returns_record_unchanged(self) -> None:
        """verbose=True must return the record with all fields intact."""
        record = {
            "name": "foo",
            "decorators": ["@classmethod"],
            "is_exported": True,
            "visibility": "public",
            "qualified_name": "module.foo",
            "resolved_by": "import",
            "best_candidate": "src/foo.py",
            "signature": "def foo() -> None",
        }
        result = _apply_verbosity(record, verbose=True)
        # All keys must still be present
        for key in record:
            assert key in result, f"Key {key!r} was unexpectedly removed (verbose=True)"

    def test_verbose_false_removes_heavy_fields(self) -> None:
        """verbose=False must remove all 6 heavy fields."""
        record = {
            "name": "foo",
            "decorators": ["@classmethod"],
            "is_exported": True,
            "visibility": "public",
            "qualified_name": "module.foo",
            "resolved_by": "import",
            "best_candidate": "src/foo.py",
            "signature": "def foo() -> None",
            "kind": "function",
        }
        result = _apply_verbosity(record, verbose=False)
        for field in HEAVY_FIELDS:
            assert field not in result, f"Heavy field {field!r} should be absent in lean mode"

    def test_verbose_false_keeps_signature(self) -> None:
        """verbose=False must NOT remove signature (highest value-to-byte field)."""
        record = {
            "name": "foo",
            "signature": "def foo(x: int) -> str",
            "decorators": ["@staticmethod"],
        }
        result = _apply_verbosity(record, verbose=False)
        assert "signature" in result
        assert result["signature"] == "def foo(x: int) -> str"

    def test_verbose_false_keeps_core_fields(self) -> None:
        """verbose=False must keep all core identity fields."""
        record = {
            "symbol": "foo",
            "file": "src/foo.py",
            "line": 1,
            "end_line": 10,
            "kind": "function",
            "docstring": "Does foo.",
            "callers": ["a"],
            "callees": ["b"],
            "ambiguous": False,
            "cluster_id": 1,
            "cluster_label": "core",
            "cluster_peers": ["bar"],
            "signature": "def foo() -> None",
            # heavy fields mixed in
            "decorators": [],
            "is_exported": True,
        }
        result = _apply_verbosity(record, verbose=False)
        for field in CORE_FIELDS_CONTEXT:
            assert field in result, f"Core field {field!r} was stripped (must always be present)"

    def test_verbose_false_absent_not_null(self) -> None:
        """Heavy fields must be ABSENT (key not present), not set to null/None."""
        record = {
            "name": "foo",
            "decorators": ["@x"],
            "is_exported": True,
            "visibility": "public",
            "qualified_name": "module.foo",
            "resolved_by": None,  # even if already None, key should be gone
            "best_candidate": None,
        }
        result = _apply_verbosity(record, verbose=False)
        for field in HEAVY_FIELDS:
            # Key must not exist at all (not just be None)
            assert field not in result, (
                f"Field {field!r} is present (value={result.get(field)!r}) — "
                "lean mode must omit the key entirely, not set it to null"
            )

    def test_returns_new_dict_not_mutate(self) -> None:
        """_apply_verbosity must not mutate the original record."""
        record = {
            "name": "foo",
            "decorators": ["@x"],
            "signature": "def foo()",
        }
        original_keys = set(record.keys())
        _apply_verbosity(record, verbose=False)
        assert set(record.keys()) == original_keys, "_apply_verbosity mutated the original dict"


# ── LO2: verbose=True (default) — shape identical to pre-Phase-8 ─────────────


class TestVerboseTrueDefault:
    """verbose=True (default) must preserve all Phase 4/5 fields."""

    def test_context_verbose_true_has_heavy_fields(self, tmp_path: Path) -> None:
        """handle_seam_context with verbose=True must include all heavy fields."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context(conn, "foo", root, verbose=True)
        conn.close()

        assert result is not None
        assert "error" not in result
        # All heavy fields must be present (even if null) in verbose mode
        for field in HEAVY_FIELDS - {"resolved_by", "best_candidate"}:
            # resolved_by / best_candidate are not on context results — only on
            # impact/trace entries. Restrict to the context-specific heavy fields.
            assert field in result, f"Field {field!r} missing from verbose=True context result"

    def test_context_default_is_verbose_true(self, tmp_path: Path) -> None:
        """Default call (no verbose arg) must equal verbose=True call."""
        conn, root, _ = _make_db(tmp_path)
        default_result = handle_seam_context(conn, "foo", root)
        verbose_result = handle_seam_context(conn, "foo", root, verbose=True)
        conn.close()
        assert default_result == verbose_result


# ── LO3: verbose=False — heavy fields absent, signature + core present ────────


class TestVerboseFalseTopLevel:
    """verbose=False must strip exactly the 6 heavy keys from top-level records."""

    def test_context_lean_strips_heavy_fields(self, tmp_path: Path) -> None:
        """handle_seam_context(..., verbose=False) must omit heavy fields."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context(conn, "foo", root, verbose=False)
        conn.close()

        assert result is not None
        assert "error" not in result
        # heavy fields for context (no resolved_by/best_candidate on context)
        context_heavy = {"decorators", "is_exported", "visibility", "qualified_name"}
        for field in context_heavy:
            assert field not in result, (
                f"Heavy field {field!r} should be absent in lean mode"
            )

    def test_context_lean_keeps_signature(self, tmp_path: Path) -> None:
        """handle_seam_context(..., verbose=False) must keep signature."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context(conn, "foo", root, verbose=False)
        conn.close()

        assert result is not None
        assert "signature" in result

    def test_context_lean_keeps_core_fields(self, tmp_path: Path) -> None:
        """handle_seam_context(..., verbose=False) must keep all core identity fields."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context(conn, "foo", root, verbose=False)
        conn.close()

        assert result is not None
        for field in CORE_FIELDS_CONTEXT:
            assert field in result, f"Core field {field!r} missing in lean mode"

    def test_query_results_have_no_heavy_fields(self, tmp_path: Path) -> None:
        """handle_seam_query is enrichment-free (no verbose flag): results never carry
        the heavy Phase 4/5 fields, so they are always 'lean' by construction."""
        conn, root, _ = _make_db(tmp_path)
        results = handle_seam_query(conn, "foo", root)
        conn.close()

        assert isinstance(results, list)
        for rec in results:
            for field in HEAVY_FIELDS:
                assert field not in rec, (
                    f"query result unexpectedly carries heavy field {field!r}"
                )


# ── LO4: stripping reaches nested structures ──────────────────────────────────


class TestVerboseFalseNestedStructures:
    """verbose=False must strip heavy fields in nested impact entries, trace hops,
    and context_pack target + neighbors."""

    def test_impact_entries_stripped(self, tmp_path: Path) -> None:
        """handle_seam_impact(..., verbose=False) must strip heavy fields in tier entries."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_impact(conn, "baz", root, verbose=False)
        conn.close()

        assert "error" not in result
        for dir_key in ("upstream", "downstream"):
            tier_group = result.get(dir_key, {})
            for entries in tier_group.values():
                for entry in entries:
                    for field in HEAVY_FIELDS:
                        assert field not in entry, (
                            f"Heavy field {field!r} found in impact entry in lean mode"
                        )

    def test_impact_verbose_true_entries_have_resolved_by(self, tmp_path: Path) -> None:
        """handle_seam_impact(..., verbose=True) must keep resolved_by on entries."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_impact(conn, "baz", root, verbose=True)
        conn.close()

        assert "error" not in result
        # At least check structure is present and resolved_by key exists in entries
        for dir_key in ("upstream", "downstream"):
            tier_group = result.get(dir_key, {})
            for entries in tier_group.values():
                for entry in entries:
                    assert "resolved_by" in entry, (
                        "resolved_by must be present in verbose=True impact entries"
                    )

    def test_trace_hops_stripped(self, tmp_path: Path) -> None:
        """handle_seam_trace(..., verbose=False) must strip heavy fields from path hops."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root, verbose=False)
        conn.close()

        assert "error" not in result
        for path in result.get("paths", []):
            for hop in path:
                for field in HEAVY_FIELDS:
                    assert field not in hop, (
                        f"Heavy field {field!r} found in trace hop in lean mode"
                    )

    def test_trace_edge_hops_stripped(self, tmp_path: Path) -> None:
        """handle_seam_trace(..., verbose=False) must strip heavy fields from edge hops."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root, verbose=False)
        conn.close()

        assert "error" not in result
        for key in ("callers_source", "callees_source", "callers_target", "callees_target"):
            for hop in result.get(key, []):
                for field in HEAVY_FIELDS:
                    assert field not in hop, (
                        f"Heavy field {field!r} found in edge hop '{key}' in lean mode"
                    )

    def test_trace_verbose_true_hops_have_resolved_by(self, tmp_path: Path) -> None:
        """handle_seam_trace(..., verbose=True) must keep resolved_by in hops."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root, verbose=True)
        conn.close()

        assert "error" not in result
        for path in result.get("paths", []):
            for hop in path:
                assert "resolved_by" in hop, "resolved_by must be present in verbose=True trace hops"

    def test_pack_target_stripped(self, tmp_path: Path) -> None:
        """handle_seam_context_pack(..., verbose=False) must strip heavy fields in target."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root, verbose=False)
        conn.close()

        assert result is not None
        assert "error" not in result
        target = result["target"]
        for field in {"decorators", "is_exported", "visibility", "qualified_name"}:
            assert field not in target, (
                f"Heavy field {field!r} found in pack target in lean mode"
            )

    def test_pack_neighbors_stripped(self, tmp_path: Path) -> None:
        """handle_seam_context_pack(..., verbose=False) must strip heavy fields in neighbors."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root, verbose=False)
        conn.close()

        assert result is not None
        for nb in result.get("callers", []) + result.get("callees", []):
            for field in {"decorators", "is_exported", "visibility", "qualified_name"}:
                assert field not in nb, (
                    f"Heavy field {field!r} found in pack neighbor in lean mode"
                )

    def test_pack_verbose_true_has_heavy_fields(self, tmp_path: Path) -> None:
        """handle_seam_context_pack(..., verbose=True) must keep heavy fields."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root, verbose=True)
        conn.close()

        assert result is not None
        target = result["target"]
        # All 4 context-level heavy fields must be present in verbose mode
        for field in {"decorators", "is_exported", "visibility", "qualified_name"}:
            assert field in target, f"Field {field!r} missing from verbose=True pack target"


# ── LO5/LO6/LO7/LO8/LO9 — error and None passthrough ────────────────────────


class TestVerbosityPassthrough:
    """Errors and None values must pass through unchanged regardless of verbose."""

    def test_context_blank_symbol_still_errors(self, tmp_path: Path) -> None:
        """Blank symbol must return INVALID_INPUT even with verbose=False."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context(conn, "", root, verbose=False)
        conn.close()
        assert isinstance(result, dict)
        assert result.get("error") == "INVALID_INPUT"

    def test_context_missing_symbol_returns_none(self, tmp_path: Path) -> None:
        """Missing symbol must return None even with verbose=False."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context(conn, "no_such_sym", root, verbose=False)
        conn.close()
        assert result is None

    def test_pack_missing_symbol_returns_none(self, tmp_path: Path) -> None:
        """handle_seam_context_pack missing symbol returns None with verbose=False."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_context_pack(conn, "no_such_sym", root, verbose=False)
        conn.close()
        assert result is None

    def test_impact_invalid_direction_still_errors(self, tmp_path: Path) -> None:
        """Invalid direction must still return INVALID_INPUT with verbose=False."""
        conn, root, _ = _make_db(tmp_path)
        result = handle_seam_impact(conn, "foo", root, direction="bad", verbose=False)
        conn.close()
        assert isinstance(result, dict)
        assert result.get("error") == "INVALID_INPUT"
