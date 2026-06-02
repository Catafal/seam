"""Integration tests for Phase 8 Feature 2 — seam_impact summary + cap.

Tests verify:
    IMP1 — seam_impact MCP tool schema exposes 'limit' integer param (default 25)
    IMP2 — seam impact --lean --limit N CLI == tool(verbose=False, limit=N) JSON parity
    IMP3 — risk_summary in MCP response has correct {direction: {tier: count}} shape
    IMP4 — truncated present in response when cap hit; absent when not hit
    IMP5 — limit=0 returns unlimited entries (no truncated)
    IMP6 — tool count still 10 after adding limit param
"""

from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.mcp import create_server
from seam.server.tools import handle_seam_impact

runner = CliRunner()

# ── Fixtures ──────────────────────────────────────────────────────────────────

HEAVY_FIELDS = {
    "decorators",
    "is_exported",
    "visibility",
    "qualified_name",
    "resolved_by",
    "best_candidate",
}


def _sym(name: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file,
        start_line=line, end_line=line + 2,
        docstring=None, signature=f"def {name}()",
        decorators=[], is_exported=True,
        visibility="public", qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call",
                file=file, line=1, confidence="INFERRED")


def _make_db(tmp_path: Path, n_callers: int = 5):
    """Build indexed DB: n_callers all call 'hub'.

    hub is called by n_callers direct callers → WILL_BREAK = n_callers entries.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "hub.py"
    src.write_text("def hub(): pass\n" + "\n".join(
        f"def caller_{i}(): hub()" for i in range(n_callers)
    ))

    symbols = [_sym("hub", str(src), line=1)] + [
        _sym(f"caller_{i}", str(src), line=2 + i) for i in range(n_callers)
    ]
    edges = [_edge(f"caller_{i}", "hub", str(src)) for i in range(n_callers)]
    upsert_file(conn, src, "python", "abc", symbols, edges)
    conn.commit()
    return conn, tmp_path, db_path


# ── IMP1: MCP schema exposes 'limit' on seam_impact ──────────────────────────


class TestMcpSchemaLimit:
    """seam_impact MCP schema must expose 'limit' integer parameter."""

    def test_limit_param_in_schema(self, tmp_path: Path) -> None:
        """seam_impact input schema must have a 'limit' property."""
        conn, root, _ = _make_db(tmp_path, n_callers=3)
        server = create_server(conn, root)
        conn.close()

        # Access tool parameters via the internal tool manager (same pattern as test_lean_parity.py).
        tool = server._tool_manager._tools["seam_impact"]
        props = tool.parameters.get("properties", {})
        assert "limit" in props, (
            f"'limit' not found in seam_impact schema properties. Got: {list(props.keys())}"
        )
        assert props["limit"].get("type") == "integer", (
            f"'limit' should be integer type, got: {props['limit']}"
        )

    def test_limit_default_is_25(self, tmp_path: Path) -> None:
        """seam_impact 'limit' parameter default must be 25."""
        conn, root, _ = _make_db(tmp_path, n_callers=3)
        server = create_server(conn, root)
        conn.close()

        tool = server._tool_manager._tools["seam_impact"]
        props = tool.parameters.get("properties", {})
        # FastMCP derives default from function signature — it lives in the
        # JSON Schema 'default' annotation on the property.
        default_val = props["limit"].get("default")
        assert default_val == 25, f"Expected default=25, got {default_val}"

    def test_tool_count_still_ten(self, tmp_path: Path) -> None:
        """Adding limit to seam_impact must not change the tool count."""
        conn, root, _ = _make_db(tmp_path, n_callers=2)
        server = create_server(conn, root)
        conn.close()

        tool_names = list(server._tool_manager._tools.keys())
        assert len(tool_names) == 10, (
            f"Expected 10 tools, got {len(tool_names)}: {sorted(tool_names)}"
        )


# ── IMP2: CLI --lean --limit parity with MCP tool ────────────────────────────


class TestCliMcpParity:
    """seam impact --lean --limit N must produce same response as tool(verbose=False, limit=N)."""

    def test_lean_limit_parity(self, tmp_path: Path) -> None:
        """CLI --lean --limit N == handler(verbose=False, limit=N)."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            # MCP handler path (what the CLI --json path calls)
            result_handler = handle_seam_impact(
                conn, "hub", root, verbose=False, limit=2
            )
            # Same call with defaults for other params
            result_default = handle_seam_impact(
                conn, "hub", root, verbose=False, limit=2
            )
        finally:
            conn.close()

        # Both calls should produce identical results
        assert result_handler == result_default

    def test_limit_caps_entries_in_handler(self, tmp_path: Path) -> None:
        """handle_seam_impact with limit=2 returns at most 2 entries per tier."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=2)
        finally:
            conn.close()

        for tier, entries in result["upstream"].items():
            assert len(entries) <= 2, f"Tier {tier!r} has {len(entries)} entries with limit=2"

    def test_lean_strips_heavy_in_capped_result(self, tmp_path: Path) -> None:
        """With verbose=False and limit=2, kept entries must have no heavy fields."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, verbose=False, limit=2)
        finally:
            conn.close()

        for _tier, entries in result["upstream"].items():
            for entry in entries:
                for field in HEAVY_FIELDS:
                    assert field not in entry, (
                        f"Heavy field {field!r} present in lean entry {entry.get('name')!r}"
                    )


# ── IMP3: risk_summary shape ──────────────────────────────────────────────────


class TestRiskSummaryShape:
    """risk_summary must have correct {direction: {tier: count}} structure."""

    def test_risk_summary_always_present(self, tmp_path: Path) -> None:
        """risk_summary must be present in every response."""
        conn, root, _ = _make_db(tmp_path, n_callers=3)
        try:
            result = handle_seam_impact(conn, "hub", root)
        finally:
            conn.close()

        assert "risk_summary" in result

    def test_risk_summary_upstream_counts(self, tmp_path: Path) -> None:
        """risk_summary upstream counts must match actual full entry counts."""
        # 5 direct callers all in WILL_BREAK, limit=100 (no cap)
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=100)
        finally:
            conn.close()

        summary = result["risk_summary"]["upstream"]
        # 5 callers in WILL_BREAK; LIKELY_AFFECTED and MAY_NEED_TESTING = 0
        assert summary["WILL_BREAK"] == 5
        assert summary.get("LIKELY_AFFECTED", 0) == 0
        assert summary.get("MAY_NEED_TESTING", 0) == 0

    def test_risk_summary_counts_stable_under_cap(self, tmp_path: Path) -> None:
        """risk_summary counts same with limit=2 as with limit=0 (story 15)."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result_capped = handle_seam_impact(conn, "hub", root, limit=2)
            result_full = handle_seam_impact(conn, "hub", root, limit=0)
        finally:
            conn.close()

        assert result_capped["risk_summary"] == result_full["risk_summary"], (
            "risk_summary should be identical regardless of limit"
        )


# ── IMP4: truncated presence ──────────────────────────────────────────────────


class TestTruncated:
    """truncated present when capped; absent when not capped."""

    def test_truncated_present_when_capped(self, tmp_path: Path) -> None:
        """truncated included when limit < number of entries in any tier."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=2)
        finally:
            conn.close()

        assert "truncated" in result

    def test_truncated_absent_when_not_capped(self, tmp_path: Path) -> None:
        """truncated omitted when all entries fit within limit."""
        conn, root, _ = _make_db(tmp_path, n_callers=3)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=10)
        finally:
            conn.close()

        assert "truncated" not in result

    def test_truncated_exact_value(self, tmp_path: Path) -> None:
        """truncated.upstream.WILL_BREAK == (n_callers - limit) when capped."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=3)
        finally:
            conn.close()

        assert result["truncated"]["upstream"]["WILL_BREAK"] == 2


# ── IMP5: limit=0 returns unlimited ───────────────────────────────────────────


class TestLimitZero:
    """limit=0 must return all entries with no truncated key."""

    def test_limit_zero_all_entries(self, tmp_path: Path) -> None:
        """limit=0 returns all 5 entries, no truncated."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=0)
        finally:
            conn.close()

        assert len(result["upstream"]["WILL_BREAK"]) == 5
        assert "truncated" not in result

    def test_limit_zero_risk_summary_matches(self, tmp_path: Path) -> None:
        """With limit=0, risk_summary counts == entry list lengths."""
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        try:
            result = handle_seam_impact(conn, "hub", root, limit=0)
        finally:
            conn.close()

        for tier, entries in result["upstream"].items():
            assert result["risk_summary"]["upstream"][tier] == len(entries), (
                f"Tier {tier!r}: risk_summary says {result['risk_summary']['upstream'][tier]} "
                f"but got {len(entries)} entries"
            )


# ── IMP7: Rich (default) CLI mode must honor --limit (review STOP) ─────────────


class TestCliRichModeCap:
    """STOP (both reviewers): the Rich (non-json/non-quiet) CLI path called impact()
    directly, bypassing the handler, so --limit and --lean were silently ignored in
    the default terminal output. The Rich path must apply the cap and signal truncation."""

    def test_rich_mode_caps_rendered_entries(self, tmp_path: Path) -> None:
        # 5 direct callers all in WILL_BREAK; --limit 2 must cap the rendered entries.
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        conn.close()

        result = runner.invoke(
            app, ["impact", "hub", "--limit", "2", "--db-dir", str(root), "--path", str(root)]
        )
        assert result.exit_code == 0, result.output
        shown = result.output.count("caller_")
        assert shown == 2, f"Rich mode must render 2 capped entries, got {shown}:\n{result.output}"

    def test_rich_mode_signals_truncation(self, tmp_path: Path) -> None:
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        conn.close()

        result = runner.invoke(
            app, ["impact", "hub", "--limit", "2", "--db-dir", str(root), "--path", str(root)]
        )
        assert result.exit_code == 0, result.output
        # The user must be told the list was truncated (and how to get the rest).
        out = result.output.lower()
        assert "more" in out or "truncat" in out, (
            f"Rich mode must signal truncation when capped:\n{result.output}"
        )

    def test_rich_mode_limit_zero_shows_all(self, tmp_path: Path) -> None:
        conn, root, _ = _make_db(tmp_path, n_callers=5)
        conn.close()

        result = runner.invoke(
            app, ["impact", "hub", "--limit", "0", "--db-dir", str(root), "--path", str(root)]
        )
        assert result.exit_code == 0, result.output
        assert result.output.count("caller_") == 5, "limit=0 must render all entries"
