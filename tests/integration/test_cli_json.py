"""Integration tests for --json / --quiet CLI flags (Slice 2).

Tests use typer CliRunner against a seeded temp DB, mirroring tests/integration/
test_indexer.py and test_impact_handler.py style.

Coverage:
  J1   impact --json returns valid {"ok":true,"data":...} envelope
  J2   impact --json --direction bad_dir exits 1 with error envelope
  J3   impact --json on missing index exits 1 with NO_INDEX error envelope
  J4   impact --quiet prints bare names (one per tier entry line)
  J5   impact default (no flag) still contains Rich-rendered output
  J6   trace --json returns valid success envelope
  J7   trace --json on missing index exits 1 with error envelope
  J8   changes --json returns valid success envelope (or NOT_A_GIT_REPO)
  J9   changes --json with invalid scope exits 1 with error envelope
  J10  why --json returns valid success envelope (empty data is ok)
  J11  why --json no file/symbol exits 1 with error envelope
  J12  clusters --json returns valid success envelope
  J13  status --json returns valid success envelope with stats fields
  J14  status --json on missing index exits 1 with NO_INDEX error envelope
  J15  --json and --quiet together exits 1 with error envelope
  J16  impact --json payload matches handle_seam_impact output (parity check)
"""

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.analysis.traversal import CONFIDENCE_EXTRACTED
from seam.cli.main import app
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_context_pack, handle_seam_impact

# ── Helpers ────────────────────────────────────────────────────────────────────

runner = CliRunner()

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=CONFIDENCE_EXTRACTED)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def seeded_db(tmp_path: Path) -> tuple[Path, Path]:
    """Return (db_dir, project_root) with a seeded call graph: C -> B -> A."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    src = tmp_path / "src.py"
    src.write_text("# stub\n")

    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        "hash1",
        [_sym("A", str(src)), _sym("B", str(src)), _sym("C", str(src))],
        [
            _edge("B", "A", str(src)),
            _edge("C", "B", str(src)),
        ],
    )
    conn.commit()
    conn.close()

    # Return db_dir as project root (the --db-dir override target)
    return db_dir, tmp_path


# ── J1: impact --json success envelope ────────────────────────────────────────


def test_impact_json_success_envelope(seeded_db: tuple[Path, Path]) -> None:
    """impact --json must return {"ok": true, "data": ...} and exit 0."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["impact", "A", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected exit: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert "data" in envelope


# ── J2: impact --json with invalid direction exits 1 with error envelope ──────


def test_impact_json_invalid_direction_exits_1(seeded_db: tuple[Path, Path]) -> None:
    """impact --json with bad direction must exit 1 and return error envelope."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["impact", "A", "--json", "--direction", "sideways", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 1, f"Expected exit 1, got {result.exit_code}: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "INVALID_INPUT"


# ── J3: impact --json on missing index exits 1 with NO_INDEX ──────────────────


def test_impact_json_missing_index_exits_1(tmp_path: Path) -> None:
    """impact --json with no index must exit 1 and return NO_INDEX error envelope."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = runner.invoke(
        app, ["impact", "A", "--json", "--db-dir", str(empty_dir), "--path", str(empty_dir)]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "NO_INDEX"


# ── J4: impact --quiet prints bare names ──────────────────────────────────────


def test_impact_quiet_prints_lines(seeded_db: tuple[Path, Path]) -> None:
    """impact --quiet must print something (bare lines), not Rich markup."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["impact", "A", "--quiet", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected: {result.output}"
    # Output must not contain Rich markup brackets like [bold]
    assert "[bold" not in result.output
    assert "[red" not in result.output
    # Should have some lines
    lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
    assert len(lines) > 0


# ── J5: impact default still renders Rich output ──────────────────────────────


def test_impact_default_renders_rich(seeded_db: tuple[Path, Path]) -> None:
    """impact without flags must still print the Rich view (existing behavior preserved)."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["impact", "A", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0
    # Rich view contains the tier labels in the output
    output = result.output
    # At minimum the symbol name or impact header should appear
    assert "A" in output or "impact" in output.lower() or "Impact" in output


# ── J6: trace --json success envelope ─────────────────────────────────────────


def test_trace_json_success_envelope(seeded_db: tuple[Path, Path]) -> None:
    """trace --json must return {"ok": true, "data": ...} envelope."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["trace", "C", "A", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert "data" in envelope


# ── J7: trace --json missing index exits 1 with error envelope ────────────────


def test_trace_json_missing_index_exits_1(tmp_path: Path) -> None:
    """trace --json with no index must exit 1 and return NO_INDEX error."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(
        app, ["trace", "A", "B", "--json", "--db-dir", str(empty), "--path", str(empty)]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "NO_INDEX"


# ── J8: changes --json (works or NOT_A_GIT_REPO) ─────────────────────────────


def test_changes_json_returns_envelope(seeded_db: tuple[Path, Path]) -> None:
    """changes --json must return a valid envelope (success or NOT_A_GIT_REPO error)."""
    db_dir, project = seeded_db
    result = runner.invoke(
        app, ["changes", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    # Either exit 0 (success) or exit 1 (NOT_A_GIT_REPO) — both should be JSON envelopes
    envelope = json.loads(result.output)
    assert "ok" in envelope
    if envelope["ok"]:
        assert "data" in envelope
    else:
        assert envelope["error"]["code"] in ("NOT_A_GIT_REPO", "NO_INDEX")


# ── J9: changes --json invalid scope exits 1 with error envelope ──────────────


def test_changes_json_invalid_scope_exits_1(seeded_db: tuple[Path, Path]) -> None:
    """changes --json with invalid scope must exit 1 and return error envelope."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["changes", "--json", "--scope", "badscope", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "INVALID_INPUT"


# ── J10: why --json returns valid envelope ────────────────────────────────────


def test_why_json_success_envelope(seeded_db: tuple[Path, Path]) -> None:
    """why --json --symbol A must return a valid envelope (data may be empty list)."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["why", "--symbol", "A", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert isinstance(envelope["data"], list)


# ── J11: why --json no file/symbol exits 1 with error envelope ───────────────


def test_why_json_no_target_exits_1(seeded_db: tuple[Path, Path]) -> None:
    """why --json with no file or symbol must exit 1 with INVALID_INPUT envelope."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["why", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "INVALID_INPUT"


# ── J12: clusters --json returns valid envelope ────────────────────────────────


def test_clusters_json_success_envelope(seeded_db: tuple[Path, Path]) -> None:
    """clusters --json must return a valid success envelope (data may be empty list)."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["clusters", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert isinstance(envelope["data"], list)


# ── J13: status --json returns success envelope with stats fields ─────────────


def test_status_json_success_envelope(seeded_db: tuple[Path, Path]) -> None:
    """status --json must return envelope with stats fields."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    # Must contain core stat fields
    assert "files" in data
    assert "symbols" in data
    assert "edges" in data


# ── J13b: status excludes synthetic file row + reports synth_edges ────────────


def test_status_json_excludes_synthetic_row_and_reports_synth_edges(
    seeded_db: tuple[Path, Path],
) -> None:
    """status file count must exclude the ':synthesis:' row, and report synth_edges.

    Regression: the edge-synthesis post-pass stores synthesized edges under a
    synthetic ':synthesis:' file row. That row is bookkeeping, not a real file —
    it must not inflate the reported file count, and synthesized edges must be
    surfaced under their own `synth_edges` key.
    """
    db_dir, _ = seeded_db
    db_path = db_dir / ".seam" / "seam.db"

    # The fixture seeded exactly one real file (src.py). Add the synthetic row + a
    # synthesized edge as the synthesis post-pass would.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (':synthesis:', '', '', 0.0, 0.0)"
    )
    synth_file_id = conn.execute(
        "SELECT id FROM files WHERE path = ':synthesis:'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence,"
        " synthesized_by) VALUES ('IBase.run', 'Impl.run', 'call', ?, 0, 'INFERRED',"
        " 'interface-override')",
        (synth_file_id,),
    )
    conn.commit()
    conn.close()

    result = runner.invoke(
        app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
    )
    assert result.exit_code == 0, f"Unexpected: {result.output}"
    data = json.loads(result.output)["data"]
    # Only the one real file counts — the ':synthesis:' row is excluded.
    assert data["files"] == 1, f"synthetic row leaked into file count: {data['files']}"
    # Synthesized edges are surfaced under their own key.
    assert data["synth_edges"] == 1, f"expected 1 synth edge, got {data.get('synth_edges')}"


# ── J14: status --json on missing index exits 1 with NO_INDEX ─────────────────


def test_status_json_missing_index_exits_1(tmp_path: Path) -> None:
    """status --json with no index must exit 1 and return NO_INDEX error."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(
        app, ["status", "--json", "--db-dir", str(empty), str(empty)]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "NO_INDEX"


# ── J15: --json and --quiet together exits 1 with error envelope ──────────────


def test_impact_json_and_quiet_mutual_exclusion(seeded_db: tuple[Path, Path]) -> None:
    """--json and --quiet together must exit 1 with a JSON error envelope."""
    db_dir, _ = seeded_db
    result = runner.invoke(
        app, ["impact", "A", "--json", "--quiet", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 1
    # Output must be a JSON error envelope (since --json was requested)
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert "mutually exclusive" in envelope["error"]["message"].lower()


# ── J16: parity — CLI --json payload matches handle_seam_impact ───────────────


def test_impact_json_payload_matches_handler(seeded_db: tuple[Path, Path]) -> None:
    """CLI impact --json data payload must match handle_seam_impact output (parity).

    WHY: This is the Article #37 ideal — CLI --json and MCP tool return identical data.
    We reuse the server handler in the CLI for exactly this reason.
    """
    db_dir, project = seeded_db
    # Get CLI output
    result = runner.invoke(
        app, ["impact", "A", "--json", "--db-dir", str(db_dir), "--path", str(db_dir)]
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    cli_data = json.loads(result.output)["data"]

    # Get handler output directly
    db_path = db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    handler_data = handle_seam_impact(conn, "A", db_dir, direction="upstream", max_depth=3)
    conn.close()

    # The CLI uses the handler output directly, so these must be equal
    # Compare found, target, and the upstream tier names
    assert cli_data["found"] == handler_data["found"]
    assert cli_data["target"] == handler_data["target"]
    # Upstream tier structure must match
    assert set(cli_data["upstream"].keys()) == set(handler_data["upstream"].keys())


def test_pack_json_payload_includes_evidence_and_matches_handler(
    seeded_db: tuple[Path, Path]
) -> None:
    """CLI pack --json data payload must match handle_seam_context_pack output."""
    db_dir, project = seeded_db
    result = runner.invoke(
        app, ["pack", "B", "--json", "--db-dir", str(db_dir), "--path", str(project)]
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    cli_data = json.loads(result.output)["data"]

    db_path = db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    handler_data = handle_seam_context_pack(conn, "B", project)
    conn.close()

    assert handler_data is not None
    assert cli_data == handler_data
    assert cli_data["relationship_evidence"]["callers"]
    assert cli_data["relationship_evidence"]["callees"]
    assert cli_data["caveats"]
    assert cli_data["recommended_next_calls"]
