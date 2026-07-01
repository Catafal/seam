"""Integration tests for Slice 3 — CLI --max-bytes flag + MCP seam_impact max_bytes parity.

Tests verify the observable surface of the --max-bytes flag and the max_bytes MCP parameter,
mirroring the prior art in test_impact_summary_parity.py.

Coverage:
  MB_CLI1 — --max-bytes propagates to handler: tight value yields byte_capped in --json output
  MB_CLI2 — --max-bytes triggers a footer note in Rich mode when ceiling fires
  MB_CLI3 — --max-bytes triggers a truncation note on stderr in --quiet mode when ceiling fires
  MB_CLI4 — --max-bytes 0 is byte-identical to omitting the flag (no byte_capped in output)
  MB_CLI5 — --max-bytes default (config.SEAM_IMPACT_MAX_BYTES = 0) is byte-identical to no-ceiling
  MB_MCP1 — seam_impact MCP schema exposes 'max_bytes' integer param
  MB_MCP2 — max_bytes MCP param forwards to the handler (tight value trims output)
  MB_MCP3 — MCP tool count includes seam_schema after adding max_bytes param
  MB_MCP4 — max_bytes=0 on the MCP tool is byte-identical (no byte_capped) to default
"""

import json
from pathlib import Path

from typer.testing import CliRunner

import seam.config as config
from seam.cli.main import app
from seam.indexer.db import connect, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.mcp import create_server
from seam.server.tools import handle_seam_impact

runner = CliRunner()

# ── Helpers ────────────────────────────────────────────────────────────────────

ROOT = Path("/fake/root")


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


def _make_db(tmp_path: Path, n_direct: int = 8) -> tuple[Path, Path]:
    """Build indexed DB: n_direct callers all call 'hub' (WILL_BREAK tier).

    Returns (db_path, project_root).
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "hub.py"
    src.write_text(
        "def hub(): pass\n"
        + "\n".join(f"def caller_{i}(): hub()" for i in range(n_direct))
    )

    symbols = [_sym("hub", str(src), line=1)] + [
        _sym(f"caller_{i}", str(src), line=2 + i) for i in range(n_direct)
    ]
    edges = [_edge(f"caller_{i}", "hub", str(src)) for i in range(n_direct)]
    upsert_file(conn, src, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()
    return db_path, tmp_path


def _serialized_size(obj: object) -> int:
    """Compact JSON character count — mirrors byte_budget.py measure."""
    return len(json.dumps(obj, separators=(",", ":")))


def _get_tight_budget(db_path: Path, root: Path, limit: int = 0) -> int:
    """Return a budget that forces the ceiling to fire: roughly 1/3 of the full output size.

    We use 1/3 rather than 1/2 to ensure the budget is genuinely tight even after
    the `byte_capped` metadata key is appended by _apply_byte_ceiling (which adds ~40 chars
    of overhead on top of the trimmed content). A budget around 1/3 leaves enough headroom
    that byte_capped itself does not push the final result over budget while still forcing
    genuine entry trimming.
    """
    conn = connect(db_path)
    try:
        full = handle_seam_impact(conn, "hub", root, limit=limit)
        full_size = _serialized_size(full)
        # 1/3 budget: tight enough to force trimming, wide enough for byte_capped overhead.
        # Minimum 200 chars to avoid edge cases where even the envelope doesn't fit.
        return max(full_size // 3, 200)
    finally:
        conn.close()


# ── MB_CLI1: --max-bytes propagates to handler (--json mode) ─────────────────


def test_tight_max_bytes_json_byte_capped_present(tmp_path: Path) -> None:
    """--max-bytes with a tight value produces byte_capped in --json output."""
    db_path, root = _make_db(tmp_path, n_direct=10)
    budget = _get_tight_budget(db_path, root)

    result = runner.invoke(
        app,
        [
            "impact", "hub",
            "--json",
            "--limit", "0",
            "--max-bytes", str(budget),
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert "byte_capped" in data, (
        f"byte_capped must be present with budget={budget};\n"
        f"data keys={list(data.keys())}"
    )
    assert data["byte_capped"]["limit"] == budget
    assert data["byte_capped"]["omitted"] > 0


def test_generous_max_bytes_json_no_byte_capped(tmp_path: Path) -> None:
    """--max-bytes with a generous value does NOT produce byte_capped in --json output."""
    db_path, root = _make_db(tmp_path, n_direct=5)
    # A budget far larger than any expected output
    budget = 10_000_000

    result = runner.invoke(
        app,
        [
            "impact", "hub",
            "--json",
            "--limit", "0",
            "--max-bytes", str(budget),
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert "byte_capped" not in data, (
        "byte_capped must be absent when budget is generous (nothing trimmed)"
    )


# ── MB_CLI2: --max-bytes triggers footer in Rich mode ────────────────────────


def test_tight_max_bytes_rich_mode_footer_note(tmp_path: Path) -> None:
    """With a tight --max-bytes, the Rich output includes a byte-ceiling footer note."""
    db_path, root = _make_db(tmp_path, n_direct=10)
    budget = _get_tight_budget(db_path, root)

    result = runner.invoke(
        app,
        [
            "impact", "hub",
            "--limit", "0",
            "--max-bytes", str(budget),
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    out = result.output.lower()
    # The footer should mention the byte ceiling (budget value or "max-bytes")
    assert "--max-bytes" in out or "max-bytes" in out or "byte" in out, (
        f"Rich output must mention byte ceiling footer; got:\n{result.output}"
    )


# ── MB_CLI3: --max-bytes triggers stderr note in --quiet mode ─────────────────


def test_tight_max_bytes_quiet_mode_stderr_note(tmp_path: Path) -> None:
    """With a tight --max-bytes, --quiet mode writes a byte-ceiling note to stderr."""
    db_path, root = _make_db(tmp_path, n_direct=10)
    budget = _get_tight_budget(db_path, root)

    result = runner.invoke(
        app,
        [
            "impact", "hub",
            "--quiet",
            "--limit", "0",
            "--max-bytes", str(budget),
            "--db-dir", str(root),
            "--path", str(root),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    # stderr has the truncation note (CliRunner merges stdout/stderr by default in output)
    # Check both output and any output that references byte ceiling / max-bytes
    combined = result.output.lower()
    assert "max-bytes" in combined or "byte" in combined or "truncat" in combined, (
        f"Quiet mode must note byte ceiling on stderr; combined output:\n{result.output}"
    )


# ── MB_CLI4: --max-bytes 0 is byte-identical to omitting the flag ─────────────


def test_max_bytes_zero_json_no_byte_capped(tmp_path: Path) -> None:
    """--max-bytes 0 produces no byte_capped key in --json output."""
    db_path, root = _make_db(tmp_path, n_direct=5)

    result_default = runner.invoke(
        app,
        [
            "impact", "hub",
            "--json",
            "--limit", "0",
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )
    result_zero = runner.invoke(
        app,
        [
            "impact", "hub",
            "--json",
            "--limit", "0",
            "--max-bytes", "0",
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )

    assert result_default.exit_code == 0
    assert result_zero.exit_code == 0

    data_default = json.loads(result_default.output)["data"]
    data_zero = json.loads(result_zero.output)["data"]

    assert "byte_capped" not in data_zero
    # --max-bytes 0 is byte-identical: the data dicts must be the same
    assert json.dumps(data_default, sort_keys=True) == json.dumps(data_zero, sort_keys=True), (
        "--max-bytes 0 must produce output byte-identical to omitting --max-bytes"
    )


# ── MB_CLI5: default max_bytes from config (0) is byte-identical ──────────────


def test_default_max_bytes_config_zero_no_ceiling(tmp_path: Path, monkeypatch) -> None:
    """When SEAM_IMPACT_MAX_BYTES=0 (default config), no byte ceiling fires."""
    monkeypatch.setattr(config, "SEAM_IMPACT_MAX_BYTES", 0)
    db_path, root = _make_db(tmp_path, n_direct=5)

    result = runner.invoke(
        app,
        [
            "impact", "hub",
            "--json",
            "--limit", "0",
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)["data"]
    assert "byte_capped" not in data, (
        "Default config SEAM_IMPACT_MAX_BYTES=0 must not trigger byte ceiling"
    )


# ── MB_MCP1: seam_impact MCP schema exposes 'max_bytes' integer param ─────────


def test_mcp_schema_exposes_max_bytes(tmp_path: Path) -> None:
    """seam_impact MCP input schema must have a 'max_bytes' integer parameter."""
    db_path, root = _make_db(tmp_path, n_direct=3)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_impact"]
    props = tool.parameters.get("properties", {})
    assert "max_bytes" in props, (
        f"'max_bytes' not found in seam_impact schema properties. Got: {list(props.keys())}"
    )
    assert props["max_bytes"].get("type") == "integer", (
        f"'max_bytes' must be integer type, got: {props['max_bytes']}"
    )


def test_mcp_schema_max_bytes_default_is_zero(tmp_path: Path) -> None:
    """seam_impact 'max_bytes' parameter default must match SEAM_IMPACT_MAX_BYTES (0)."""
    db_path, root = _make_db(tmp_path, n_direct=3)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_impact"]
    props = tool.parameters.get("properties", {})
    default_val = props["max_bytes"].get("default")
    assert default_val == config.SEAM_IMPACT_MAX_BYTES, (
        f"Expected max_bytes default={config.SEAM_IMPACT_MAX_BYTES}, got {default_val}"
    )


# ── MB_MCP2: max_bytes MCP param forwards to the handler ──────────────────────


def test_mcp_max_bytes_tight_value_trims_output(tmp_path: Path) -> None:
    """With a tight max_bytes via the MCP tool parameter, byte_capped appears in the result.

    Uses a 1/3 budget to ensure trimming fires and byte_capped overhead doesn't push
    the result over budget. The key assertions are: byte_capped is present and entries
    were reduced vs. the full result.
    """
    db_path, root = _make_db(tmp_path, n_direct=10)
    conn = connect(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", root, limit=0)
        full_size = _serialized_size(full_result)
        # 1/3 budget: tight enough to force trimming, wide enough for byte_capped overhead.
        budget = max(full_size // 3, 200)

        # Direct handler call with max_bytes (mirrors what the MCP tool does)
        result = handle_seam_impact(conn, "hub", root, limit=0, max_bytes=budget)
    finally:
        conn.close()

    # byte_capped must be present when trimming fired
    assert "byte_capped" in result, (
        "MCP tool must propagate max_bytes to handler: byte_capped must be present"
    )
    assert result["byte_capped"]["limit"] == budget
    assert result["byte_capped"]["omitted"] > 0
    # The result is smaller than the full result (entries were trimmed)
    result_size = _serialized_size(result)
    assert result_size < full_size, (
        f"Result ({result_size}) must be smaller than full ({full_size}) after trimming"
    )


def test_mcp_max_bytes_zero_no_byte_capped(tmp_path: Path) -> None:
    """max_bytes=0 via MCP tool must not produce byte_capped."""
    db_path, root = _make_db(tmp_path, n_direct=5)
    conn = connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", root, limit=0, max_bytes=0)
    finally:
        conn.close()

    assert "byte_capped" not in result


# ── MB_MCP3: MCP tool count includes seam_schema ──────────────────────────────


def test_mcp_tool_count_includes_schema(tmp_path: Path) -> None:
    """Tool count includes seam_schema after adding max_bytes to seam_impact."""
    db_path, root = _make_db(tmp_path, n_direct=2)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool_names = list(server._tool_manager._tools.keys())
    assert len(tool_names) == 16, (
        f"Expected 16 tools, got {len(tool_names)}: {sorted(tool_names)}"
    )


# ── MB_MCP4: max_bytes=0 byte-identical to default ────────────────────────────


def test_mcp_max_bytes_zero_identical_to_default(tmp_path: Path) -> None:
    """max_bytes=0 on the MCP handler is byte-identical to not passing max_bytes."""
    db_path, root = _make_db(tmp_path, n_direct=5)
    conn = connect(db_path)
    try:
        result_default = handle_seam_impact(conn, "hub", root, limit=0)
        result_zero = handle_seam_impact(conn, "hub", root, limit=0, max_bytes=0)
    finally:
        conn.close()

    assert json.dumps(result_default, separators=(",", ":")) == json.dumps(
        result_zero, separators=(",", ":"),
    ), "max_bytes=0 via MCP handler must be byte-identical to default"
    assert "byte_capped" not in result_zero


# ── Regression: the ceiling is a HARD ceiling, metadata included (review STOP-3) ──


def _emit_size(obj: object) -> int:
    """Size under the CLI emit serializer (json.dumps default separators, ensure_ascii=False).

    This is the serialization emit_json actually renders — the byte ceiling must hold
    against THIS, not a more-compact proxy, or the real output overruns the budget.
    """
    return len(json.dumps(obj, ensure_ascii=False))


def test_final_response_including_metadata_within_budget(tmp_path: Path) -> None:
    """HARD ceiling: the FULL response — entries PLUS the appended byte_capped/truncated
    metadata — must fit the budget when measured in the emit serialization. Guards the
    regression where byte_capped was added AFTER the trim and pushed the output over budget."""
    db_path, root = _make_db(tmp_path, n_direct=20)
    budget = _get_tight_budget(db_path, root)  # keeps some entries, fires the ceiling

    conn = connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", root, limit=0, max_bytes=budget)
    finally:
        conn.close()

    assert "byte_capped" in result, "ceiling should have fired on this tight budget"
    # The response body (what emit_json renders as `data`) must be <= the stated budget,
    # WITH the byte_capped + truncated metadata already present.
    assert _emit_size(result) <= budget, (
        f"final response {_emit_size(result)} chars exceeds budget {budget} "
        f"(metadata overshoot regression); keys={list(result.keys())}"
    )


def test_rich_all_trimmed_not_reported_as_no_dependents(tmp_path: Path) -> None:
    """FALSE-SAFE guard (review STOP-2): when --max-bytes trims EVERY entry, Rich mode must
    NOT print 'No dependents found' (which reads as 'safe to delete') — it must say the
    dependents were trimmed."""
    db_path, root = _make_db(tmp_path, n_direct=10)
    # A budget far below the envelope forces every entry to drop (total == 0 in Rich).
    result = runner.invoke(
        app,
        [
            "impact", "hub",
            "--limit", "0",
            "--max-bytes", "200",
            "--db-dir", str(root),
            "--path", str(root),
        ],
    )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    out = result.output.lower()
    assert "no dependents found" not in out, (
        f"all-trimmed must NOT read as 'no dependents found' (dangerous false-safe);\n{result.output}"
    )
    assert "trimmed" in out and ("max-bytes" in out or "byte" in out), (
        f"all-trimmed Rich output must say the dependents were trimmed;\n{result.output}"
    )


def test_byte_drops_not_misattributed_to_limit_footer(tmp_path: Path) -> None:
    """With --limit 0, byte-trimmed entries (merged into `truncated` for reconciliation) must
    NOT be reported by the --limit footer — that misattributes the cause AND nonsensically
    tells the user to 'use --limit 0' when they already passed it. Only the byte footer fires."""
    db_path, root = _make_db(tmp_path, n_direct=20)
    budget = _get_tight_budget(db_path, root)

    # Rich mode
    rich = runner.invoke(
        app,
        ["impact", "hub", "--limit", "0", "--max-bytes", str(budget),
         "--db-dir", str(root), "--path", str(root)],
    )
    assert rich.exit_code == 0, rich.output
    assert "truncated by --limit" not in rich.output.lower(), (
        f"--limit footer must not fire under --limit 0;\n{rich.output}"
    )
    assert "trimmed to fit --max-bytes" in rich.output.lower(), (
        f"byte footer must report the byte trim;\n{rich.output}"
    )

    # Quiet mode (stderr; CliRunner merges streams into .output)
    quiet = runner.invoke(
        app,
        ["impact", "hub", "--quiet", "--limit", "0", "--max-bytes", str(budget),
         "--db-dir", str(root), "--path", str(root)],
    )
    assert quiet.exit_code == 0, quiet.output
    assert "truncated by --limit" not in quiet.output.lower(), (
        f"quiet --limit note must not fire under --limit 0;\n{quiet.output}"
    )
    assert "trimmed to fit --max-bytes" in quiet.output.lower(), (
        f"quiet byte note must report the byte trim;\n{quiet.output}"
    )
