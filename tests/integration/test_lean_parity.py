"""Integration tests for Phase 8 Feature 1 — MCP schema + CLI parity.

Tests verify:
    LP1 — MCP tool schemas expose 'verbose' boolean param; tool count stays 10
    LP2 — seam context --lean matches handle_seam_context(..., verbose=False)
    LP3 — seam impact --lean strips heavy fields from entries
    LP4 — seam trace --lean strips heavy fields from hops
    LP5 — seam pack --lean strips heavy fields from target + neighbors
    LP6 — seam query --lean produces same shape as handle_seam_query(..., verbose=False)
"""

from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.mcp import create_server
from seam.server.tools import (
    handle_seam_context,
    handle_seam_context_pack,
    handle_seam_impact,
    handle_seam_query,
    handle_seam_trace,
)

# ── Test fixtures ─────────────────────────────────────────────────────────────

HEAVY_FIELDS = {
    "decorators",
    "is_exported",
    "visibility",
    "qualified_name",
    "resolved_by",
    "best_candidate",
}


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring="docstring",
        signature=f"def {name}()",
        decorators=["@classmethod"],
        is_exported=True,
        visibility="public",
        qualified_name=f"module.{name}",
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence="INFERRED")


def _make_db(tmp_path: Path):
    """Build small indexed DB: foo->bar->baz."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    conn = init_db(db_path)

    src = tmp_path / "src.py"
    src.write_text("def foo(): bar()\ndef bar(): baz()\ndef baz(): pass\n")

    upsert_file(
        conn,
        src,
        "python",
        "h1",
        [
            _sym("foo", str(src), line=1),
            _sym("bar", str(src), line=2),
            _sym("baz", str(src), line=3),
        ],
        [_edge("foo", "bar", str(src)), _edge("bar", "baz", str(src))],
    )
    return conn, tmp_path, db_path, src


# ── LP1: MCP tool schemas expose 'verbose'; tool count == 10 ─────────────────


class TestMcpSchemaVerbose:
    """MCP tools affected by Phase 8 must expose a 'verbose' boolean parameter."""

    def test_tool_count_still_ten(self, tmp_path: Path) -> None:
        """Tool count is 11 (seam_flows added; verbose params add no tools)."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        # FastMCP stores tools in a dict; get_tool is the official API
        tool_names = list(server._tool_manager._tools.keys())
        assert len(tool_names) == 19, (
            f"Expected 19 tools, got {len(tool_names)}: {sorted(tool_names)}"
        )

    def test_seam_query_has_no_verbose_param(self, tmp_path: Path) -> None:
        """seam_query must NOT expose 'verbose' — it carries no enrichment, so lean
        mode would be a no-op (same rationale as seam_search's exclusion)."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_query"]
        params = tool.parameters
        assert "verbose" not in params.get("properties", {}), (
            "seam_query is enrichment-free and must not advertise a no-op 'verbose' flag"
        )

    def test_seam_context_has_verbose_param(self, tmp_path: Path) -> None:
        """seam_context must expose 'verbose' in its input schema."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_context"]
        params = tool.parameters
        assert "verbose" in params.get("properties", {}), (
            "seam_context schema must include 'verbose' property"
        )

    def test_seam_impact_has_verbose_param(self, tmp_path: Path) -> None:
        """seam_impact must expose 'verbose' in its input schema."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_impact"]
        params = tool.parameters
        assert "verbose" in params.get("properties", {}), (
            "seam_impact schema must include 'verbose' property"
        )

    def test_seam_trace_has_verbose_param(self, tmp_path: Path) -> None:
        """seam_trace must expose 'verbose' in its input schema."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_trace"]
        params = tool.parameters
        assert "verbose" in params.get("properties", {}), (
            "seam_trace schema must include 'verbose' property"
        )

    def test_seam_context_pack_has_verbose_param(self, tmp_path: Path) -> None:
        """seam_context_pack must expose 'verbose' in its input schema."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_context_pack"]
        params = tool.parameters
        assert "verbose" in params.get("properties", {}), (
            "seam_context_pack schema must include 'verbose' property"
        )

    def test_seam_search_has_no_verbose_param(self, tmp_path: Path) -> None:
        """seam_search must NOT have verbose (it returns no enrichment)."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_search"]
        params = tool.parameters
        assert "verbose" not in params.get("properties", {}), (
            "seam_search must NOT expose 'verbose' — it carries no enrichment fields"
        )
        assert "semantic" in params.get("properties", {}), (
            "seam_search must expose semantic so agents can force keyword-only retrieval"
        )

    def test_seam_query_has_semantic_param(self, tmp_path: Path) -> None:
        """seam_query exposes semantic so concept search can be made keyword-only."""
        conn, root, db_path, _ = _make_db(tmp_path)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_query"]
        params = tool.parameters
        assert "semantic" in params.get("properties", {}), (
            "seam_query must expose semantic so agents can force keyword-only retrieval"
        )


# ── LP2: seam context --lean parity ──────────────────────────────────────────


class TestContextLeanParity:
    """seam context --lean output must match handle_seam_context(..., verbose=False)."""

    def test_context_lean_handler_matches_verbose_false(self, tmp_path: Path) -> None:
        """Direct handler call with verbose=False must equal verbose=True minus heavy fields."""
        conn, root, db_path, _ = _make_db(tmp_path)
        verbose_result = handle_seam_context(conn, "foo", root, verbose=True)
        lean_result = handle_seam_context(conn, "foo", root, verbose=False)
        conn.close()

        assert verbose_result is not None
        assert lean_result is not None

        # Lean result must not have heavy fields
        for field in {"decorators", "is_exported", "visibility", "qualified_name"}:
            assert field not in lean_result

        # Lean result must have all core fields
        for field in {"symbol", "file", "line", "kind", "signature"}:
            assert field in lean_result

        # Core field values should match (only enrichment differs)
        assert lean_result["symbol"] == verbose_result["symbol"]
        assert lean_result["file"] == verbose_result["file"]
        assert lean_result["signature"] == verbose_result["signature"]


# ── LP3: seam impact --lean parity ───────────────────────────────────────────


class TestImpactLeanParity:
    """seam impact --lean strips heavy fields from tier entries."""

    def test_impact_lean_entries_have_no_heavy_fields(self, tmp_path: Path) -> None:
        """Impact entries with verbose=False must not contain any heavy fields."""
        conn, root, db_path, _ = _make_db(tmp_path)
        result = handle_seam_impact(conn, "baz", root, direction="upstream", verbose=False)
        conn.close()

        assert "error" not in result
        for dir_key in ("upstream", "downstream"):
            tier_group = result.get(dir_key, {})
            for entries in tier_group.values():
                for entry in entries:
                    for field in HEAVY_FIELDS:
                        assert field not in entry

    def test_impact_lean_entries_keep_core_fields(self, tmp_path: Path) -> None:
        """Impact entries with verbose=False must keep name, distance, confidence, tier, file."""
        conn, root, db_path, _ = _make_db(tmp_path)
        result = handle_seam_impact(conn, "baz", root, direction="upstream", verbose=False)
        conn.close()

        assert "error" not in result
        upstream = result.get("upstream", {})
        all_entries = [e for tier_list in upstream.values() for e in tier_list]
        for entry in all_entries:
            for field in ("name", "distance", "confidence", "tier", "file", "is_test"):
                assert field in entry, f"Core field {field!r} missing from lean impact entry"


# ── LP4: seam trace --lean parity ────────────────────────────────────────────


class TestTraceLeanParity:
    """seam trace --lean strips heavy fields from hops and edge hops."""

    def test_trace_lean_hops_have_no_heavy_fields(self, tmp_path: Path) -> None:
        """Path hops with verbose=False must not contain resolved_by or best_candidate."""
        conn, root, db_path, _ = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root, verbose=False)
        conn.close()

        assert "error" not in result
        for path in result.get("paths", []):
            for hop in path:
                for field in HEAVY_FIELDS:
                    assert field not in hop

    def test_trace_verbose_true_hops_keep_resolved_by(self, tmp_path: Path) -> None:
        """Path hops with verbose=True must have resolved_by."""
        conn, root, db_path, _ = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root, verbose=True)
        conn.close()

        for path in result.get("paths", []):
            for hop in path:
                assert "resolved_by" in hop

    def test_trace_lean_edge_hops_stripped(self, tmp_path: Path) -> None:
        """Edge hops (callers_source etc.) with verbose=False must not have heavy fields."""
        conn, root, db_path, _ = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root, verbose=False)
        conn.close()

        for key in ("callers_source", "callees_source", "callers_target", "callees_target"):
            for hop in result.get(key, []):
                for field in HEAVY_FIELDS:
                    assert field not in hop


# ── LP5: seam pack --lean parity ─────────────────────────────────────────────


class TestPackLeanParity:
    """seam pack --lean strips heavy fields from target and neighbors."""

    def test_pack_lean_verbose_false_vs_true_shape(self, tmp_path: Path) -> None:
        """verbose=False pack target must equal verbose=True minus heavy fields."""
        conn, root, db_path, _ = _make_db(tmp_path)
        verbose_pack = handle_seam_context_pack(conn, "foo", root, verbose=True)
        lean_pack = handle_seam_context_pack(conn, "foo", root, verbose=False)
        conn.close()

        assert verbose_pack is not None
        assert lean_pack is not None

        # Core target fields must be same value in both
        target_v = verbose_pack["target"]
        target_l = lean_pack["target"]
        for field in ("symbol", "file", "line", "kind", "signature"):
            assert target_v[field] == target_l[field]

        # Heavy fields absent in lean
        for field in {"decorators", "is_exported", "visibility", "qualified_name"}:
            assert field not in target_l

    def test_pack_lean_neighbor_heavy_fields_absent(self, tmp_path: Path) -> None:
        """Neighbors in lean pack must not have heavy fields."""
        conn, root, db_path, _ = _make_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root, verbose=False)
        conn.close()

        assert result is not None
        for nb in result.get("callers", []) + result.get("callees", []):
            for field in {"decorators", "is_exported", "visibility", "qualified_name"}:
                assert field not in nb

    def test_pack_lean_keeps_relationship_evidence(self, tmp_path: Path) -> None:
        """Lean pack keeps compact provenance for relationship claims."""
        conn, root, db_path, _ = _make_db(tmp_path)
        verbose_pack = handle_seam_context_pack(conn, "foo", root, verbose=True)
        lean_pack = handle_seam_context_pack(conn, "foo", root, verbose=False)
        conn.close()

        assert verbose_pack is not None
        assert lean_pack is not None
        assert lean_pack["relationship_evidence"] == verbose_pack["relationship_evidence"]


# ── LP6: seam query is enrichment-free (no verbose flag) ─────────────────────


class TestQueryEnrichmentFree:
    """seam_query carries no Phase 4/5 heavy fields, so it has no verbose flag at all —
    its results are always 'lean' by construction."""

    def test_query_results_have_no_heavy_fields(self, tmp_path: Path) -> None:
        conn, root, db_path, _ = _make_db(tmp_path)
        results = handle_seam_query(conn, "foo", root)
        conn.close()

        assert isinstance(results, list)
        for rec in results:
            for field in HEAVY_FIELDS:
                assert field not in rec, f"query result unexpectedly carries heavy field {field!r}"
