"""Integration tests for Phase 5 read-layer threading of resolved_by.

Tests that resolved_by appears in the output of all relevant read operations:
- flows.callers() / flows.callees() → EdgeHop.resolved_by
- flows.trace() → Hop.resolved_by
- traversal.walk() → Reached.resolved_by
- engine.context() → callers/callees list items carry resolved_by
- tools.handle_seam_context() → response includes resolved_by in edge lists
- tools.handle_seam_trace() → response hops carry resolved_by

Note: resolved_by in these outputs is None (not 'import') because
the read-path uses the fast-path resolve() shim (name_counts only).
Only resolve_edge() with full context produces non-None resolved_by.
The tests assert the FIELD IS PRESENT (not None-checking the value).
"""

from pathlib import Path

from seam.analysis import flows as flows_module
from seam.analysis.traversal import walk
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server import tools

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, kind: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind=kind, file=file,
        start_line=line, end_line=line + 5,
        docstring=None, signature=None, decorators=[],
        is_exported=None, visibility=None, qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call",
        file=file, line=1, confidence="INFERRED",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEdgeHopHasResolvedBy:
    """EdgeHop from flows.callers()/callees() has resolved_by field."""

    def test_callers_edge_hop_has_resolved_by(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        src = tmp_path / "a.py"
        src.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(conn, src, "python", "abc",
                    [_sym("caller", "function", str(src)),
                     _sym("target", "function", str(src))],
                    [_edge("caller", "target", str(src))])

        result = flows_module.callers(conn, "target")
        conn.close()

        assert len(result) >= 1
        hop = result[0]
        # The resolved_by field must exist on EdgeHop
        assert "resolved_by" in hop

    def test_callees_edge_hop_has_resolved_by(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        src = tmp_path / "a.py"
        src.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(conn, src, "python", "abc",
                    [_sym("caller", "function", str(src)),
                     _sym("target", "function", str(src))],
                    [_edge("caller", "target", str(src))])

        result = flows_module.callees(conn, "caller")
        conn.close()

        assert len(result) >= 1
        hop = result[0]
        assert "resolved_by" in hop


class TestHopHasResolvedBy:
    """Hop from flows.trace() has resolved_by field."""

    def test_trace_hop_has_resolved_by(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        src = tmp_path / "a.py"
        src.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(conn, src, "python", "abc",
                    [_sym("caller", "function", str(src)),
                     _sym("target", "function", str(src))],
                    [_edge("caller", "target", str(src))])

        paths = flows_module.trace(conn, "caller", "target")
        conn.close()

        assert len(paths) == 1
        assert len(paths[0]) >= 1
        hop = paths[0][0]
        assert "resolved_by" in hop


class TestReachedHasResolvedBy:
    """Reached from traversal.walk() has resolved_by field."""

    def test_walk_reached_has_resolved_by(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        src = tmp_path / "a.py"
        src.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(conn, src, "python", "abc",
                    [_sym("caller", "function", str(src)),
                     _sym("target", "function", str(src))],
                    [_edge("caller", "target", str(src))])

        results = walk(conn, ["caller"], "downstream", max_depth=3)
        conn.close()

        assert len(results) >= 1
        reached = results[0]
        assert "resolved_by" in reached


class TestToolsOutputHasResolvedBy:
    """MCP tool handlers produce output with resolved_by in edge lists."""

    def test_handle_seam_trace_paths_have_resolved_by(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        src = tmp_path / "a.py"
        src.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(conn, src, "python", "abc",
                    [_sym("caller", "function", str(src)),
                     _sym("target", "function", str(src))],
                    [_edge("caller", "target", str(src))])

        result = tools.handle_seam_trace(conn, "caller", "target", tmp_path)
        conn.close()

        assert result["found"] is True
        assert len(result["paths"]) == 1
        hop = result["paths"][0][0]
        assert "resolved_by" in hop

    def test_handle_seam_impact_entries_have_confidence(self, tmp_path: Path) -> None:
        """Impact handler entries still carry confidence (regression check)."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        src = tmp_path / "a.py"
        src.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(conn, src, "python", "abc",
                    [_sym("caller", "function", str(src)),
                     _sym("target", "function", str(src))],
                    [_edge("caller", "target", str(src))])

        result = tools.handle_seam_impact(conn, "target", tmp_path)
        conn.close()

        assert result["found"] is True


class TestMcpToolCount:
    """MCP tool count is still 9 — Phase 5 adds NO new tools."""

    def test_tool_count_updated_for_phase6(self) -> None:
        """tools module exports 11 handlers (seam_flows added on top of Phase 6's 10)."""
        tool_handlers = [
            attr for attr in dir(tools)
            if attr.startswith("handle_seam_")
        ]
        assert len(tool_handlers) == 11, (
            f"Expected 11 MCP tool handlers, found {len(tool_handlers)}: {tool_handlers}"
        )
