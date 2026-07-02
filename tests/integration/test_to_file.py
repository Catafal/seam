"""Integration tests for WS5 Slice 2 — --to-file escape hatch.

Issue #227: Wire --to-file into impact/context/trace/flows commands.

Tests follow the TDD vertical-slice approach (one concern per cell), driven
against a small fixture repo indexed via `seam init`. All assertions use the
public CLI surface (CliRunner.invoke), never implementation internals.

Fixture topology (hub_repo):
  hub.py defines function `hub` with 7 callers (a..g), giving 7 WILL_BREAK
  dependents so we can verify --to-file overrides --limit 5.

Coverage:
  TF1   impact --to-file writes file + prints summary+path (not payload)
  TF2   impact --to-file path is under .seam/out/ (auto location)
  TF3   file contains valid full JSON with risk_summary
  TF4   --to-file overrides --limit (file has >5 entries, fixture has 7)
  TF5   --to-file explicit file path honored
  TF6   --to-file explicit dir (trailing slash) places auto-file inside dir
  TF7   --json --to-file emits small pointer envelope (no payload inline)
  TF8   --quiet --to-file prints only bare file path
  TF9   context --to-file writes file + prints summary+path
  TF10  trace --to-file writes file + prints summary+path
  TF11  flows (list mode) --to-file writes file + prints summary+path
  TF12  flows (expand mode) --to-file writes file + prints summary+path
  TF13  not-found trace --to-file still writes file + coherent summary
  TF14  empty flows (no entry_points) --to-file still writes file
  TF15  default (no --to-file) unchanged for all four commands
  TF16  --json/--quiet still mutually exclusive when --to-file is set
  TF17  bare --to-file (no value) works — regression for the boolean-flag fix
  TF18  --to-file-path alone (without --to-file) implies file mode

Flag design note: `--to-file` is a BOOLEAN flag (bare, no value) — Typer 0.26
cannot express an optional-value option — and `--to-file-path <dest>` carries an
explicit destination. Either flag enables file mode; --to-file-path implies it.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


# ── Fixture ────────────────────────────────────────────────────────────────────


def _make_hub_repo(tmp_path: Path) -> Path:
    """Create a small Python fixture with 7 callers of `hub` and index it.

    The fixture gives `hub` 7 direct dependents (WILL_BREAK) so tests can
    verify that --to-file overrides --limit 5 and the file contains > 5 entries.
    """
    (tmp_path / "hub.py").write_text(
        "def hub():\n"
        '    """Central hub function."""\n'
        "    pass\n"
        "\n"
        "def a(): hub()\n"
        "def b(): hub()\n"
        "def c(): hub()\n"
        "def d(): hub()\n"
        "def e(): hub()\n"
        "def f(): hub()\n"
        "def g(): hub()\n"
    )
    res = runner.invoke(app, ["init", str(tmp_path)])
    assert res.exit_code == 0, f"seam init failed: {res.output}"
    return tmp_path


@pytest.fixture()
def hub_repo(tmp_path: Path) -> Path:
    """Indexed fixture repo with a hub symbol that has 7 WILL_BREAK dependents."""
    return _make_hub_repo(tmp_path)


# ── TF1: impact --to-file writes file + prints summary+path ──────────────────


def test_tf1_impact_to_file_writes_file(hub_repo: Path) -> None:
    """impact --to-file must write a file and print summary + path, not the payload."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, f"Unexpected exit: {res.output}"
    output = res.output
    # Must NOT contain the full JSON payload inline (risk_summary printed as JSON)
    assert '"risk_summary"' not in output
    # Must print a path to an existing file
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    assert len(lines) >= 1
    # At least one line should be a path ending in .json
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path found in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists(), f"File not written: {written_path}"


# ── TF2: auto location is .seam/out/ ─────────────────────────────────────────


def test_tf2_impact_auto_location_under_seam_out(hub_repo: Path) -> None:
    """Auto location must be .seam/out/ under the project root."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    lines = [ln.strip() for ln in res.output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines
    written_path = Path(json_lines[-1])
    # The path must be inside .seam/out/ relative to hub_repo
    seam_out = hub_repo / ".seam" / "out"
    assert str(written_path).startswith(str(seam_out)), (
        f"Expected path under {seam_out}, got {written_path}"
    )


# ── TF3: file contains valid full JSON with risk_summary ─────────────────────


def test_tf3_impact_file_contains_full_json(hub_repo: Path) -> None:
    """The written file must be valid JSON containing risk_summary."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    lines = [ln.strip() for ln in res.output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    written_path = Path(json_lines[-1])
    content = written_path.read_text()
    data = json.loads(content)
    assert "risk_summary" in data, "Written file must contain risk_summary"
    # Check at least one tier has entries
    upstream = data.get("upstream", {})
    will_break = upstream.get("WILL_BREAK", [])
    assert len(will_break) == 7, f"Expected 7 WILL_BREAK entries, got {len(will_break)}"


# ── TF4: --to-file overrides --limit ─────────────────────────────────────────


def test_tf4_to_file_overrides_limit(hub_repo: Path) -> None:
    """--to-file must write the full result even when --limit 5 is passed.

    The fixture has 7 dependents; --limit 5 would normally cap at 5.
    The file must contain all 7.
    """
    res = runner.invoke(
        app, ["impact", "hub", "--path", str(hub_repo), "--limit", "5", "--to-file"]
    )
    assert res.exit_code == 0, res.output
    lines = [ln.strip() for ln in res.output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    written_path = Path(json_lines[-1])
    data = json.loads(written_path.read_text())
    # The file must have > 5 entries (all 7, not capped by --limit)
    upstream = data.get("upstream", {})
    will_break = upstream.get("WILL_BREAK", [])
    assert len(will_break) > 5, (
        f"Expected >5 WILL_BREAK entries in file (limit override), got {len(will_break)}"
    )


# ── TF5: explicit file path honored ──────────────────────────────────────────


def test_tf5_explicit_file_path(hub_repo: Path, tmp_path: Path) -> None:
    """--to-file <file> must write to the exact given path."""
    out_file = tmp_path / "myout" / "result.json"
    res = runner.invoke(
        app, ["impact", "hub", "--path", str(hub_repo), "--to-file-path", str(out_file)]
    )
    assert res.exit_code == 0, res.output
    assert out_file.exists(), f"File not written at explicit path: {out_file}"
    data = json.loads(out_file.read_text())
    assert "risk_summary" in data


# ── TF6: explicit dir (trailing slash) places auto-file inside it ────────────


def test_tf6_explicit_dir_with_trailing_slash(hub_repo: Path, tmp_path: Path) -> None:
    """--to-file /dir/ must create the dir and place the auto-named file inside."""
    out_dir = tmp_path / "out_dir"
    out_dir.mkdir()
    res = runner.invoke(
        app,
        ["impact", "hub", "--path", str(hub_repo), "--to-file-path", str(out_dir) + "/"],
    )
    assert res.exit_code == 0, res.output
    # The auto-named file must be inside out_dir
    files = list(out_dir.glob("*.json"))
    assert files, f"No JSON file written inside {out_dir}"
    data = json.loads(files[0].read_text())
    assert "risk_summary" in data


# ── TF7: --json --to-file emits small pointer envelope ───────────────────────


def test_tf7_json_mode_pointer_envelope(hub_repo: Path) -> None:
    """--json --to-file must emit the small pointer envelope, not the full payload."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo), "--json", "--to-file"])
    assert res.exit_code == 0, res.output
    envelope = json.loads(res.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    # Small pointer envelope: must have these keys
    assert data["command"] == "impact"
    assert "to_file" in data
    assert "bytes" in data
    assert "summary" in data
    # Must NOT inline the full payload
    assert "risk_summary" not in data
    assert "upstream" not in data
    # The to_file path must point to an existing file
    written_path = Path(data["to_file"])
    assert written_path.exists()
    # And the file must contain the actual payload
    actual = json.loads(written_path.read_text())
    assert "risk_summary" in actual


# ── TF8: --quiet --to-file prints only bare file path ────────────────────────


def test_tf8_quiet_mode_bare_path(hub_repo: Path) -> None:
    """--quiet --to-file must print only the bare file path (one line)."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo), "--quiet", "--to-file"])
    assert res.exit_code == 0, res.output
    lines = [ln.strip() for ln in res.output.splitlines() if ln.strip()]
    # Must be exactly one line (the file path)
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {res.output!r}"
    written_path = Path(lines[0])
    assert written_path.exists(), f"Path not found: {written_path}"
    assert written_path.suffix == ".json"


# ── TF9: context --to-file writes file + prints summary+path ─────────────────


def test_tf9_context_to_file(hub_repo: Path) -> None:
    """context --to-file must write a file and print summary + path."""
    res = runner.invoke(app, ["context", "hub", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    output = res.output
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists()
    data = json.loads(written_path.read_text())
    assert "symbol" in data or "callers" in data  # seam_context result shape


# ── TF10: trace --to-file writes file + prints summary+path ──────────────────


def test_tf10_trace_to_file(hub_repo: Path) -> None:
    """trace --to-file must write a file and print summary + path."""
    res = runner.invoke(app, ["trace", "a", "hub", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    output = res.output
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists()
    data = json.loads(written_path.read_text())
    assert "found" in data


# ── TF11: flows (list mode) --to-file writes file ────────────────────────────


def test_tf11_flows_list_mode_to_file(hub_repo: Path) -> None:
    """flows (no entry) --to-file must write a file with entry_points."""
    res = runner.invoke(app, ["flows", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    output = res.output
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists()
    data = json.loads(written_path.read_text())
    assert "entry_points" in data


# ── TF12: flows (expand mode) --to-file writes file ──────────────────────────


def test_tf12_flows_expand_mode_to_file(hub_repo: Path) -> None:
    """flows <entry> --to-file must write a file with the flow tree."""
    # Use a caller that itself calls hub
    res = runner.invoke(app, ["flows", "a", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    output = res.output
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists()
    data = json.loads(written_path.read_text())
    # Flow expand mode: has entry, steps, total_steps OR found=False
    assert "entry" in data or "found" in data


# ── TF13: not-found trace --to-file still writes a file ──────────────────────


def test_tf13_trace_not_found_still_writes_file(hub_repo: Path) -> None:
    """A not-found trace with --to-file must still write a file + coherent summary."""
    res = runner.invoke(
        app,
        ["trace", "hub", "nonexistent_xyz", "--path", str(hub_repo), "--to-file"],
    )
    assert res.exit_code == 0, res.output
    output = res.output
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists()
    data = json.loads(written_path.read_text())
    # Must be a valid result (not-found or found=False)
    assert "found" in data
    assert data["found"] is False


# ── TF14: empty flows --to-file still writes a file ──────────────────────────


def test_tf14_flows_not_found_still_writes_file(hub_repo: Path) -> None:
    """flows for unknown entry with --to-file must still write a file + summary."""
    res = runner.invoke(
        app,
        ["flows", "nonexistent_xyz", "--path", str(hub_repo), "--to-file"],
    )
    assert res.exit_code == 0, res.output
    output = res.output
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.endswith(".json")]
    assert json_lines, f"No .json path in output:\n{output}"
    written_path = Path(json_lines[-1])
    assert written_path.exists()


# ── TF15: default (no --to-file) unchanged ───────────────────────────────────


def test_tf15_default_unchanged_impact(hub_repo: Path) -> None:
    """Without --to-file, impact output must be unchanged (Rich mode)."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo)])
    assert res.exit_code == 0, res.output
    # Should contain Rich-rendered output, not a file path
    assert (
        "WILL BREAK" in res.output or "Impact" in res.output or "dependents" in res.output.lower()
    )
    # No .seam/out/ path should appear
    seam_out = hub_repo / ".seam" / "out"
    assert str(seam_out) not in res.output


def test_tf15_default_unchanged_context(hub_repo: Path) -> None:
    """Without --to-file, context output must be unchanged."""
    res = runner.invoke(app, ["context", "hub", "--path", str(hub_repo)])
    assert res.exit_code == 0, res.output
    # Should contain Rich rendering (caller/callee info)
    assert "hub" in res.output


def test_tf15_default_unchanged_trace(hub_repo: Path) -> None:
    """Without --to-file, trace output must be unchanged."""
    res = runner.invoke(app, ["trace", "a", "hub", "--path", str(hub_repo)])
    assert res.exit_code == 0, res.output


def test_tf15_default_unchanged_flows(hub_repo: Path) -> None:
    """Without --to-file, flows output must be unchanged."""
    res = runner.invoke(app, ["flows", "--path", str(hub_repo)])
    assert res.exit_code == 0, res.output


# ── TF16: --json + --quiet still mutually exclusive with --to-file ────────────


def test_tf16_json_quiet_still_mutually_exclusive(hub_repo: Path) -> None:
    """--json and --quiet must remain mutually exclusive even when --to-file is set."""
    res = runner.invoke(
        app,
        ["impact", "hub", "--path", str(hub_repo), "--json", "--quiet", "--to-file"],
    )
    assert res.exit_code == 1
    envelope = json.loads(res.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "INVALID_INPUT"


# ── Additional: context --json --to-file ──────────────────────────────────────


def test_tf_context_json_to_file(hub_repo: Path) -> None:
    """context --json --to-file must emit the small pointer envelope."""
    res = runner.invoke(app, ["context", "hub", "--path", str(hub_repo), "--json", "--to-file"])
    assert res.exit_code == 0, res.output
    envelope = json.loads(res.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["command"] == "context"
    assert "to_file" in data
    assert "bytes" in data
    assert "summary" in data


def test_tf_trace_json_to_file(hub_repo: Path) -> None:
    """trace --json --to-file must emit the small pointer envelope."""
    res = runner.invoke(app, ["trace", "a", "hub", "--path", str(hub_repo), "--json", "--to-file"])
    assert res.exit_code == 0, res.output
    envelope = json.loads(res.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["command"] == "trace"
    assert "to_file" in data


def test_tf_flows_json_to_file(hub_repo: Path) -> None:
    """flows --json --to-file must emit the small pointer envelope."""
    res = runner.invoke(app, ["flows", "--path", str(hub_repo), "--json", "--to-file"])
    assert res.exit_code == 0, res.output
    envelope = json.loads(res.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["command"] == "flows"
    assert "to_file" in data


def test_tf_context_quiet_to_file(hub_repo: Path) -> None:
    """context --quiet --to-file must print only the bare file path."""
    res = runner.invoke(app, ["context", "hub", "--path", str(hub_repo), "--quiet", "--to-file"])
    assert res.exit_code == 0, res.output
    lines = [ln.strip() for ln in res.output.splitlines() if ln.strip()]
    assert len(lines) == 1, f"Expected 1 line, got {lines!r}"
    assert Path(lines[0]).exists()


# ── TF17: bare --to-file (no value) regression ───────────────────────────────
# WHY: `--to-file` is a boolean flag (Typer 0.26 cannot express an optional-value
# option), so a BARE `--to-file` with no following argument MUST work and select
# the default .seam/out/ location. A prior single-string-option design failed here
# with "Option '--to-file' requires an argument." — this locks the fix.


def test_tf17_bare_to_file_flag_no_argument(hub_repo: Path) -> None:
    """A bare `--to-file` (no value) must succeed and write to the auto location."""
    res = runner.invoke(app, ["impact", "hub", "--path", str(hub_repo), "--to-file"])
    assert res.exit_code == 0, res.output
    assert "requires an argument" not in res.output
    # A file must have been auto-written under .seam/out/ inside the repo.
    out_files = list((hub_repo / ".seam" / "out").glob("impact-*.json"))
    assert out_files, "bare --to-file did not write an auto-named file"


# ── TF18: --to-file-path alone implies file mode ─────────────────────────────


def test_tf18_to_file_path_alone_implies_file_mode(hub_repo: Path, tmp_path: Path) -> None:
    """Passing only --to-file-path (without --to-file) must still enable file mode."""
    out_file = tmp_path / "only_path.json"
    res = runner.invoke(
        app, ["impact", "hub", "--path", str(hub_repo), "--to-file-path", str(out_file)]
    )
    assert res.exit_code == 0, res.output
    assert out_file.exists(), "--to-file-path alone did not enable file output"
    # stdout must be the summary + path, NOT the full inline payload.
    assert "risk_summary" not in res.output
