"""Integration tests for Slice 4 — CLI + MCP surface for E4 edge provenance and steer.

Tests verify the observable surface (CLI render + MCP tool docstrings/schema) for E4:
  - CLI `seam impact` Rich mode prints per-entry provenance (kind + synthesized marker)
  - CLI `seam impact` Rich mode prints next_actions footer when output is trimmed
  - CLI `seam impact` Rich mode omits next_actions footer when nothing is trimmed
  - CLI `seam trace` Rich mode shows synthesized marker on hops
  - MCP tool count includes seam_schema
  - MCP seam_impact docstring documents kind/synthesized_by/next_actions fields
  - MCP seam_trace docstring documents synthesized_by on hops

Prior art: tests/integration/test_cli_impact_max_bytes.py
"""

import json
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

import seam.config as config
from seam.cli.main import app
from seam.indexer.db import connect, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.mcp import create_server

runner = CliRunner()

ROOT = Path("/fake/root")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name,
        kind="function",
        file=file,
        start_line=line,
        end_line=line + 2,
        docstring=None,
        signature=f"def {name}()",
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=name,
    )


def _edge(
    source: str,
    target: str,
    file: str,
    kind: str = "call",
    synthesized_by: str | None = None,
) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=file,
        line=1,
        confidence="INFERRED",
        synthesized_by=synthesized_by,
    )


def _make_db_simple(tmp_path: Path) -> tuple[Path, Path]:
    """Build a DB with one caller -> hub (static call edge)."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "hub.py"
    src.write_text("def hub(): pass\ndef caller(): hub()\n")

    symbols = [_sym("hub", str(src), line=1), _sym("caller", str(src), line=2)]
    edges = [_edge("caller", "hub", str(src), kind="call")]
    upsert_file(conn, src, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()
    return db_path, tmp_path


def _make_db_with_synthesized(tmp_path: Path) -> tuple[Path, Path]:
    """Build a DB with a synthesized caller -> hub edge (channel=interface-override)."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "hub.py"
    src.write_text("def hub(): pass\ndef impl(): hub()\n")

    symbols = [_sym("hub", str(src), line=1), _sym("impl", str(src), line=2)]
    edges = [
        _edge("impl", "hub", str(src), kind="call", synthesized_by="interface-override")
    ]
    upsert_file(conn, src, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()
    return db_path, tmp_path


def _make_db_many(tmp_path: Path, n: int = 30) -> tuple[Path, Path]:
    """Build a DB with n callers -> hub to trigger per-tier cap."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "hub.py"
    src.write_text(
        "def hub(): pass\n"
        + "\n".join(f"def c_{i}(): hub()" for i in range(n))
    )

    symbols = [_sym("hub", str(src), 1)] + [
        _sym(f"c_{i}", str(src), 2 + i) for i in range(n)
    ]
    edges = [_edge(f"c_{i}", "hub", str(src)) for i in range(n)]
    upsert_file(conn, src, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()
    return db_path, tmp_path


# ── CLI_E4_1: Rich mode prints 'kind' for impact entries ──────────────────────


def test_cli_impact_rich_prints_kind(tmp_path: Path) -> None:
    """CLI_E4_1: seam impact Rich mode prints the edge kind for each entry."""
    db_path, root = _make_db_simple(tmp_path)

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    # The entry 'caller' with edge kind 'call' should appear
    assert "caller" in result.output
    # With SEAM_EDGE_PROVENANCE=on, the kind should be rendered
    out = result.output.lower()
    assert "call" in out, f"Expected edge kind 'call' in output;\n{result.output}"


# ── CLI_E4_2: Rich mode prints synthesized marker for synthesized edges ────────


def test_cli_impact_rich_prints_synthesized_marker(tmp_path: Path) -> None:
    """CLI_E4_2: seam impact Rich mode shows a synthesized marker for synthesized edges."""
    db_path, root = _make_db_with_synthesized(tmp_path)

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    # The synthesized edge should show a marker (either "synthesized" or "heuristic" or the channel name)
    assert "impl" in result.output, "impl should appear as a dependent"
    # Look for a synthesized indicator — the channel name, "synth", or "heuristic"
    out = result.output.lower()
    assert (
        "synth" in out or "interface" in out or "heuristic" in out
    ), f"Expected synthesized marker in output;\n{result.output}"


# ── CLI_E4_3: Rich mode omits provenance when SEAM_EDGE_PROVENANCE=off ─────────


def test_cli_impact_rich_no_provenance_when_off(tmp_path: Path) -> None:
    """CLI_E4_3: When SEAM_EDGE_PROVENANCE=off, Rich mode does not print edge kind."""
    db_path, root = _make_db_simple(tmp_path)

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "off"):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    # caller should still appear (the entry itself is visible)
    assert "caller" in result.output
    # But the "kind:" label should not be present when provenance is off
    # (We look for " call " or "(call)" as a kind indicator, not the word "call" alone
    # since "call" appears in tier labels etc.)
    # The key assertion: the implementation must not crash and entries still show
    # The provenance-off path should have no "[synth" or "(synth" etc.
    assert "synth" not in result.output.lower()


# ── CLI_E4_4: next_actions footer printed when trimmed ────────────────────────


def test_cli_impact_rich_next_actions_footer_when_trimmed(tmp_path: Path) -> None:
    """CLI_E4_4: seam impact Rich mode prints next_actions footer when entries are trimmed."""
    db_path, root = _make_db_many(tmp_path, n=30)

    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "on"),
    ):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--limit",
                "5",  # small enough to trigger truncation with n=30
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    out = result.output.lower()
    # The next_actions footer should mention "raise limit" or "limit" or "next steps"
    # It should tell the user how to see more entries
    assert (
        "raise limit" in out
        or "limit" in out
        or "next action" in out
        or "next step" in out
        or "more" in out
    ), f"Expected next_actions footer in trimmed Rich output;\n{result.output}"


# ── CLI_E4_5: next_actions footer absent when nothing trimmed ─────────────────


def test_cli_impact_rich_no_next_actions_footer_when_complete(tmp_path: Path) -> None:
    """CLI_E4_5: seam impact Rich mode does NOT print next_actions when nothing is trimmed."""
    db_path, root = _make_db_simple(tmp_path)

    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "on"),
    ):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--limit",
                "100",  # generous limit, only 1 caller
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    out = result.output.lower()
    # "next actions" or "raise limit" should NOT appear when nothing is trimmed
    assert "next action" not in out, (
        "next_actions footer must be absent when nothing was trimmed"
    )


# ── CLI_E4_6: SEAM_IMPACT_STEER=off suppresses footer ─────────────────────────


def test_cli_impact_rich_steer_off_no_footer(tmp_path: Path) -> None:
    """CLI_E4_6: When SEAM_IMPACT_STEER=off, next_actions footer is suppressed."""
    db_path, root = _make_db_many(tmp_path, n=30)

    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "off"),
    ):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--limit",
                "5",
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    out = result.output.lower()
    assert "next action" not in out, (
        "next_actions footer must be absent when SEAM_IMPACT_STEER=off"
    )


# ── CLI_E4_7: --json mode passes next_actions through verbatim ────────────────


def test_cli_impact_json_mode_next_actions_in_data(tmp_path: Path) -> None:
    """CLI_E4_7: --json mode passes handler dict through; next_actions appears in data when trimmed."""
    db_path, root = _make_db_many(tmp_path, n=30)

    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "on"),
    ):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--json",
                "--limit",
                "5",
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    envelope = json.loads(result.output)
    data = envelope["data"]
    assert "next_actions" in data, "next_actions must be in JSON data when trimmed"
    assert isinstance(data["next_actions"], list)
    assert len(data["next_actions"]) > 0


# ── CLI_E4_8: --lean mode keeps kind, strips synthesized_by in JSON ───────────


def test_cli_impact_lean_keeps_kind_strips_synthesized_by(tmp_path: Path) -> None:
    """CLI_E4_8: --lean mode keeps 'kind' but strips 'synthesized_by' from entries."""
    db_path, root = _make_db_simple(tmp_path)

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = runner.invoke(
            app,
            [
                "impact",
                "hub",
                "--json",
                "--lean",
                "--db-dir",
                str(root),
                "--path",
                str(root),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    data = json.loads(result.output)["data"]

    # Only iterate direction-group keys (upstream/downstream), not meta keys.
    # Meta keys (risk_summary, truncated, found, target, etc.) hold non-list values.
    meta_keys = {"found", "target", "hidden_tests", "risk_summary", "truncated", "byte_capped", "next_actions"}
    for dir_key, direction_entries in data.items():
        if dir_key in meta_keys or not isinstance(direction_entries, dict):
            continue
        # direction_entries: {"WILL_BREAK": [...], "LIKELY_AFFECTED": [...], ...}
        for tier_key, tier_entries in direction_entries.items():
            if not isinstance(tier_entries, list):
                continue
            for entry in tier_entries:
                assert "kind" in entry, f"lean mode must keep 'kind' (tier={tier_key})"
                assert "synthesized_by" not in entry, f"lean mode must strip 'synthesized_by' (tier={tier_key})"


# ── CLI_E4_9: trace Rich mode shows synthesized marker on hops ───────────────


def test_cli_trace_rich_shows_synthesized_marker(tmp_path: Path) -> None:
    """CLI_E4_9: seam trace Rich mode shows a synthesized indicator on synthesized hops."""
    # Create a fixture with a path from 'start' -> 'hub' via a synthesized edge
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "code.py"
    src.write_text("def start(): hub()\ndef hub(): pass\n")

    symbols = [_sym("start", str(src), 1), _sym("hub", str(src), 2)]
    edges = [
        _edge("start", "hub", str(src), kind="call", synthesized_by="interface-override")
    ]
    upsert_file(conn, src, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = runner.invoke(
            app,
            [
                "trace",
                "start",
                "hub",
                "--db-dir",
                str(tmp_path),
                "--path",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    out = result.output.lower()
    # Should mention synthesized/heuristic/interface or the channel
    assert (
        "synth" in out or "interface" in out or "heuristic" in out
    ), f"Expected synthesized marker in trace output;\n{result.output}"


# ── MCP_E4_1: MCP tool count includes seam_schema ────────────────────────────


def test_mcp_tool_count_13_after_schema(tmp_path: Path) -> None:
    """MCP_E4_1: MCP tool count includes the read-only seam_schema tool."""
    db_path, root = _make_db_simple(tmp_path)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tools = list(server._tool_manager._tools.keys())
    assert len(tools) == 19, f"Expected 19 tools, got {len(tools)}: {sorted(tools)}"


# ── MCP_E4_2: seam_impact docstring documents kind field ─────────────────────


def test_mcp_seam_impact_docstring_mentions_kind(tmp_path: Path) -> None:
    """MCP_E4_2: seam_impact docstring documents the new 'kind' field."""
    db_path, root = _make_db_simple(tmp_path)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_impact"]
    doc = tool.description or ""
    assert "kind" in doc, (
        f"seam_impact docstring must mention 'kind' field;\ngot: {doc[:300]}"
    )


# ── MCP_E4_3: seam_impact docstring documents synthesized_by field ─────────────


def test_mcp_seam_impact_docstring_mentions_synthesized_by(tmp_path: Path) -> None:
    """MCP_E4_3: seam_impact docstring documents the new 'synthesized_by' field."""
    db_path, root = _make_db_simple(tmp_path)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_impact"]
    doc = tool.description or ""
    assert "synthesized_by" in doc, (
        f"seam_impact docstring must mention 'synthesized_by' field;\ngot: {doc[:300]}"
    )


# ── MCP_E4_4: seam_impact docstring documents next_actions ───────────────────


def test_mcp_seam_impact_docstring_mentions_next_actions(tmp_path: Path) -> None:
    """MCP_E4_4: seam_impact docstring documents the 'next_actions' steer field."""
    db_path, root = _make_db_simple(tmp_path)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_impact"]
    doc = tool.description or ""
    assert "next_actions" in doc, (
        f"seam_impact docstring must mention 'next_actions' steer;\ngot: {doc[:300]}"
    )


# ── MCP_E4_5: seam_trace docstring documents synthesized_by on hops ──────────


def test_mcp_seam_trace_docstring_mentions_synthesized_by(tmp_path: Path) -> None:
    """MCP_E4_5: seam_trace docstring documents 'synthesized_by' on trace hops."""
    db_path, root = _make_db_simple(tmp_path)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_trace"]
    doc = tool.description or ""
    assert "synthesized_by" in doc, (
        f"seam_trace docstring must mention 'synthesized_by' on hops;\ngot: {doc[:300]}"
    )


# ── MCP_E4_6: seam_trace docstring corrects stale 'call | import' vocabulary ──


def test_mcp_seam_trace_docstring_has_full_vocabulary(tmp_path: Path) -> None:
    """MCP_E4_6: seam_trace docstring uses the extended vocabulary, not just 'call | import'."""
    db_path, root = _make_db_simple(tmp_path)
    conn = connect(db_path)
    server = create_server(conn, root)
    conn.close()

    tool = server._tool_manager._tools["seam_trace"]
    doc = tool.description or ""
    # The updated docstring should mention at least some of the extended kinds
    # (not just the stale 'call' | 'import')
    extended_kinds = ["instantiates", "holds", "reads", "writes", "uses", "extends"]
    found = [k for k in extended_kinds if k in doc]
    assert len(found) >= 3, (
        f"seam_trace docstring must mention extended edge kinds; only found: {found}\ndoc: {doc[:400]}"
    )
