"""Unit tests for seam.cli.file_sink — WS5 Slice 1.

All tests use synthetic dicts and tmp_path — offline, no DB, no network.

Coverage targets (from issue #226 acceptance criteria):
  - summarize() per command shape: impact, context, trace (found+not-found),
    flows (list, expand, not-found).
  - summarize() graceful degradation on unexpected shape.
  - summarize() staleness note when index_status is present.
  - write_output_file(): exact bytes, path, byte count, dir auto-creation,
    atomicity (no partial file), overwrite-on-rerun, unsafe-label sanitization,
    explicit-dir vs explicit-file path_override.
  - A filesystem write failure is surfaced (not swallowed).
  - summarize() never raises.
"""

import json
import os
from pathlib import Path

import pytest

from seam.cli.file_sink import summarize, write_output_file

# ── Helpers ───────────────────────────────────────────────────────────────────

IMPACT_DATA = {
    "found": True,
    "target": "Client.send",
    "risk_summary": {
        "upstream": {
            "WILL_BREAK": 5,
            "LIKELY_AFFECTED": 3,
            "MAY_NEED_TESTING": 10,
        }
    },
    "upstream": {
        "WILL_BREAK": [{"name": "a"}] * 5,
        "LIKELY_AFFECTED": [{"name": "b"}] * 3,
        "MAY_NEED_TESTING": [{"name": "c"}] * 10,
    },
}

CONTEXT_DATA = {
    "found": True,
    "name": "Client.send",
    "callers": [{"name": "a"}, {"name": "b"}],
    "callees": [{"name": "c"}],
    "ambiguous": False,
}

CONTEXT_DATA_AMBIGUOUS = {
    "found": True,
    "name": "send",
    "callers": [{"name": "a"}],
    "callees": [],
    "ambiguous": True,
}

TRACE_FOUND_DATA = {
    "found": True,
    "paths": [
        [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
    ],
}

TRACE_NOT_FOUND_DATA = {
    "found": False,
    "paths": [],
}

FLOWS_LIST_DATA = {
    "entry_points": [
        {"name": "main", "kind": "function", "file": "main.py", "reach": 50},
        {"name": "init", "kind": "function", "file": "app.py", "reach": 20},
        {"name": "run", "kind": "function", "file": "runner.py", "reach": 5},
    ]
}

FLOWS_EXPAND_DATA = {
    "entry": "main",
    "kind": "function",
    "file": "main.py",
    "steps": [
        {
            "name": "setup",
            "kind": "function",
            "file": "setup.py",
            "confidence": "EXTRACTED",
            "truncated": False,
            "children": [
                {
                    "name": "load_config",
                    "kind": "function",
                    "file": "config.py",
                    "confidence": "EXTRACTED",
                    "truncated": False,
                    "children": [],
                }
            ],
        },
        {
            "name": "process",
            "kind": "function",
            "file": "proc.py",
            "confidence": "INFERRED",
            "truncated": False,
            "children": [],
        },
    ],
    "total_steps": 3,  # setup + load_config + process
    "truncated": False,
}

FLOWS_NOT_FOUND_DATA = {"found": False}

STALE_BANNER = {"stale": True, "reason": "file mtime changed", "hint": "run seam sync"}


# ── summarize() — impact ──────────────────────────────────────────────────────


def test_summarize_impact_total_and_tiers() -> None:
    """summarize impact includes total dependents and per-tier counts."""
    result = summarize(IMPACT_DATA, "impact")
    # Total = 5 + 3 + 10 = 18
    assert "18" in result
    assert "WILL_BREAK" in result
    assert "5" in result
    assert "LIKELY_AFFECTED" in result
    assert "3" in result
    assert "MAY_NEED_TESTING" in result
    assert "10" in result


def test_summarize_impact_direction_upstream() -> None:
    """summarize impact includes direction label when only upstream."""
    result = summarize(IMPACT_DATA, "impact")
    assert "upstream" in result


def test_summarize_impact_direction_both() -> None:
    """summarize impact includes both directions when risk_summary has both."""
    data = {
        "found": True,
        "target": "X",
        "risk_summary": {
            "upstream": {"WILL_BREAK": 2, "LIKELY_AFFECTED": 0, "MAY_NEED_TESTING": 0},
            "downstream": {"WILL_BREAK": 0, "LIKELY_AFFECTED": 1, "MAY_NEED_TESTING": 0},
        },
    }
    result = summarize(data, "impact")
    assert "upstream" in result
    assert "downstream" in result


def test_summarize_impact_not_found() -> None:
    """summarize impact handles not-found gracefully."""
    result = summarize({"found": False, "target": "X", "risk_summary": {}}, "impact")
    # Should not raise; some meaningful content
    assert isinstance(result, str)
    assert len(result) > 0


# ── summarize() — context ─────────────────────────────────────────────────────


def test_summarize_context_caller_callee_counts() -> None:
    """summarize context includes caller and callee counts."""
    result = summarize(CONTEXT_DATA, "context")
    assert "2" in result  # 2 callers
    assert "1" in result  # 1 callee
    assert "caller" in result.lower()
    assert "callee" in result.lower()


def test_summarize_context_no_ambiguous_marker_when_false() -> None:
    """summarize context omits ambiguous marker when not set."""
    result = summarize(CONTEXT_DATA, "context")
    assert "ambiguous" not in result.lower()


def test_summarize_context_ambiguous_marker() -> None:
    """summarize context appends ambiguous marker when result is ambiguous."""
    result = summarize(CONTEXT_DATA_AMBIGUOUS, "context")
    assert "ambiguous" in result.lower()


# ── summarize() — trace ───────────────────────────────────────────────────────


def test_summarize_trace_found_path() -> None:
    """summarize trace shows hop count when path found."""
    result = summarize(TRACE_FOUND_DATA, "trace")
    assert "path found" in result.lower()
    # 4 hops in the first path
    assert "4" in result


def test_summarize_trace_not_found() -> None:
    """summarize trace returns no-path message when not found."""
    result = summarize(TRACE_NOT_FOUND_DATA, "trace")
    assert "no path" in result.lower()


# ── summarize() — flows list mode ─────────────────────────────────────────────


def test_summarize_flows_list_entry_point_count() -> None:
    """summarize flows list mode shows entry point count."""
    result = summarize(FLOWS_LIST_DATA, "flows")
    assert "3" in result
    assert "entry" in result.lower()


def test_summarize_flows_empty_list() -> None:
    """summarize flows list mode with zero entry points."""
    result = summarize({"entry_points": []}, "flows")
    assert "0" in result
    assert "entry" in result.lower()


# ── summarize() — flows expand mode ───────────────────────────────────────────


def test_summarize_flows_expand_step_count() -> None:
    """summarize flows expand mode includes total step count."""
    result = summarize(FLOWS_EXPAND_DATA, "flows")
    assert "3" in result  # total_steps = 3
    assert "step" in result.lower()


def test_summarize_flows_expand_depth() -> None:
    """summarize flows expand mode includes tree depth."""
    result = summarize(FLOWS_EXPAND_DATA, "flows")
    # Depth: entry(0) → setup(1) → load_config(2); process(1) → max=2
    assert "depth" in result.lower()
    assert "2" in result


def test_summarize_flows_expand_no_depth_when_empty_steps() -> None:
    """summarize flows expand mode with empty steps."""
    data = {
        "entry": "main",
        "kind": "function",
        "file": "main.py",
        "steps": [],
        "total_steps": 0,
        "truncated": False,
    }
    result = summarize(data, "flows")
    assert "0" in result or "step" in result.lower()


# ── summarize() — flows not found ─────────────────────────────────────────────


def test_summarize_flows_not_found() -> None:
    """summarize flows shows not-found message for {found: false}."""
    result = summarize(FLOWS_NOT_FOUND_DATA, "flows")
    assert "not found" in result.lower()


# ── summarize() — graceful degradation ───────────────────────────────────────


def test_summarize_unknown_command_returns_generic() -> None:
    """summarize on unknown command returns generic message, never raises."""
    result = summarize({"anything": 123}, "unknown_cmd")
    assert "wrote" in result.lower() or "unknown_cmd" in result


def test_summarize_unexpected_impact_shape_no_raise() -> None:
    """summarize impact on unexpected data shape returns generic, never raises."""
    result = summarize({}, "impact")
    assert isinstance(result, str)
    assert len(result) > 0


def test_summarize_unexpected_context_shape_no_raise() -> None:
    """summarize context on unexpected data shape returns generic, never raises."""
    result = summarize({"callers": "not-a-list"}, "context")
    assert isinstance(result, str)


def test_summarize_unexpected_trace_shape_no_raise() -> None:
    """summarize trace on unexpected data returns generic, never raises."""
    result = summarize({"paths": "bad"}, "trace")
    assert isinstance(result, str)


def test_summarize_none_data_no_raise() -> None:
    """summarize never raises even when data is None (guard against caller bugs)."""
    # pyright would complain but we still protect at runtime
    result = summarize(None, "impact")  # type: ignore[arg-type]
    assert isinstance(result, str)


# ── summarize() — staleness note ─────────────────────────────────────────────


def test_summarize_impact_stale_appends_note() -> None:
    """summarize appends stale note when index_status is present."""
    data = {**IMPACT_DATA, "index_status": STALE_BANNER}
    result = summarize(data, "impact")
    assert "stale" in result.lower()


def test_summarize_context_stale_appends_note() -> None:
    """summarize appends stale note on context result with index_status."""
    data = {**CONTEXT_DATA, "index_status": STALE_BANNER}
    result = summarize(data, "context")
    assert "stale" in result.lower()


def test_summarize_trace_found_stale_appends_note() -> None:
    """summarize appends stale note on trace result with index_status."""
    data = {**TRACE_FOUND_DATA, "index_status": STALE_BANNER}
    result = summarize(data, "trace")
    assert "stale" in result.lower()


def test_summarize_no_stale_when_index_status_absent() -> None:
    """summarize does not mention stale when index_status is absent."""
    result = summarize(IMPACT_DATA, "impact")
    assert "stale" not in result.lower()


def test_summarize_no_stale_when_index_status_not_stale() -> None:
    """summarize does not mention stale when index_status.stale is False."""
    data = {
        **IMPACT_DATA,
        "index_status": {"stale": False, "reason": "", "hint": ""},
    }
    result = summarize(data, "impact")
    assert "stale" not in result.lower()


# ── write_output_file() — basic contract ─────────────────────────────────────


def test_write_output_file_returns_result(tmp_path: Path) -> None:
    """write_output_file returns a ToFileResult with path, bytes, summary."""
    out_dir = tmp_path / "out"
    data = {"hello": "world"}
    result = write_output_file(data, command="impact", label="Foo", out_dir=out_dir)
    assert isinstance(result, dict)
    assert "path" in result
    assert "bytes" in result
    assert "summary" in result


def test_write_output_file_exact_json_bytes(tmp_path: Path) -> None:
    """write_output_file writes exact compact JSON bytes matching output.py serializer."""
    out_dir = tmp_path / "out"
    data = {"key": "value", "num": 42}
    result = write_output_file(data, command="context", label="MySymbol", out_dir=out_dir)

    written = Path(result["path"]).read_bytes()
    expected = (json.dumps(data, ensure_ascii=False) + "\n").encode()
    assert written == expected


def test_write_output_file_byte_count(tmp_path: Path) -> None:
    """write_output_file returns the correct byte size in the result."""
    out_dir = tmp_path / "out"
    data = {"key": "value"}
    result = write_output_file(data, command="trace", label="src_dst", out_dir=out_dir)

    on_disk = Path(result["path"]).stat().st_size
    assert result["bytes"] == on_disk


def test_write_output_file_default_path_naming(tmp_path: Path) -> None:
    """write_output_file auto-names the file <command>-<sanitized_label>.json."""
    out_dir = tmp_path / "out"
    data = {"x": 1}
    result = write_output_file(data, command="impact", label="MyClass", out_dir=out_dir)

    assert Path(result["path"]).name == "impact-MyClass.json"


def test_write_output_file_creates_out_dir(tmp_path: Path) -> None:
    """write_output_file creates the output directory when it does not exist."""
    out_dir = tmp_path / "nonexistent" / "deep"
    data = {"a": 1}
    write_output_file(data, command="flows", label="main", out_dir=out_dir)
    assert out_dir.is_dir()


def test_write_output_file_overwrites_on_rerun(tmp_path: Path) -> None:
    """write_output_file overwrites the prior file for the same command+label."""
    out_dir = tmp_path / "out"
    first_data = {"run": 1}
    second_data = {"run": 2}

    r1 = write_output_file(first_data, command="impact", label="X", out_dir=out_dir)
    r2 = write_output_file(second_data, command="impact", label="X", out_dir=out_dir)

    # Same path
    assert r1["path"] == r2["path"]
    # File has second data
    content = json.loads(Path(r2["path"]).read_text(encoding="utf-8"))
    assert content == second_data


def test_write_output_file_atomicity(tmp_path: Path) -> None:
    """write_output_file does not leave a partial temp file on success.

    Verify: the temp file used during writing does not persist after the call.
    We check by listing directory contents before and after.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    data = {"big": "data" * 1000}

    before = set(out_dir.iterdir())
    write_output_file(data, command="impact", label="Hub", out_dir=out_dir)
    after = set(out_dir.iterdir())

    # Only one new file added (the target .json); no orphan temp files
    new_files = after - before
    assert len(new_files) == 1
    assert list(new_files)[0].suffix == ".json"


# ── write_output_file() — label sanitization ─────────────────────────────────


def test_sanitize_dots_in_label(tmp_path: Path) -> None:
    """Dots in the label are replaced with _ for a safe filename."""
    out_dir = tmp_path / "out"
    r = write_output_file({}, command="context", label="Class.method", out_dir=out_dir)
    assert ".." not in Path(r["path"]).name
    assert "Class_method" in Path(r["path"]).name or "_" in Path(r["path"]).name


def test_sanitize_slash_in_label(tmp_path: Path) -> None:
    """Forward slashes in the label are replaced to prevent path traversal."""
    out_dir = tmp_path / "out"
    r = write_output_file({}, command="impact", label="pkg/symbol", out_dir=out_dir)
    # File must actually be inside out_dir
    assert Path(r["path"]).parent.resolve() == out_dir.resolve()
    # No slash in filename
    assert "/" not in Path(r["path"]).name


def test_sanitize_path_traversal_label(tmp_path: Path) -> None:
    """A path-escape label like ../evil stays inside out_dir."""
    out_dir = tmp_path / "out"
    r = write_output_file({}, command="impact", label="../evil", out_dir=out_dir)
    # Resolved path must start with out_dir
    resolved = Path(r["path"]).resolve()
    assert str(resolved).startswith(str(out_dir.resolve()))


def test_sanitize_backslash_in_label(tmp_path: Path) -> None:
    """Backslashes in the label are sanitized."""
    out_dir = tmp_path / "out"
    r = write_output_file({}, command="trace", label="a\\b", out_dir=out_dir)
    assert "\\" not in Path(r["path"]).name


def test_sanitize_label_length_cap(tmp_path: Path) -> None:
    """Very long labels are capped to prevent fs limit errors."""
    out_dir = tmp_path / "out"
    long_label = "x" * 300
    r = write_output_file({}, command="impact", label=long_label, out_dir=out_dir)
    # Filename should be well under 255 chars
    assert len(Path(r["path"]).name) <= 255


# ── write_output_file() — path_override ──────────────────────────────────────


def test_path_override_explicit_directory(tmp_path: Path) -> None:
    """path_override pointing to an existing directory writes auto-named file inside."""
    explicit_dir = tmp_path / "mydir"
    explicit_dir.mkdir()
    out_dir = tmp_path / "default"

    r = write_output_file(
        {"x": 1},
        command="impact",
        label="Sym",
        out_dir=out_dir,
        path_override=explicit_dir,
    )
    assert Path(r["path"]).parent.resolve() == explicit_dir.resolve()
    assert Path(r["path"]).name == "impact-Sym.json"


def test_path_override_explicit_file(tmp_path: Path) -> None:
    """path_override pointing to a specific file writes exactly that file."""
    target = tmp_path / "custom_output.json"
    out_dir = tmp_path / "default"

    r = write_output_file(
        {"y": 2},
        command="impact",
        label="Sym",
        out_dir=out_dir,
        path_override=target,
    )
    assert Path(r["path"]).resolve() == target.resolve()
    assert target.exists()


def test_path_override_directory_with_trailing_sep(tmp_path: Path) -> None:
    """path_override with trailing separator is treated as a directory."""
    # Create a path string with trailing separator (before the dir exists)
    target_dir = tmp_path / "trailing"
    target_dir.mkdir()
    out_dir = tmp_path / "default"

    # Use a string path with trailing separator
    r = write_output_file(
        {"z": 3},
        command="flows",
        label="entry",
        out_dir=out_dir,
        path_override=Path(str(target_dir) + os.sep),
    )
    assert Path(r["path"]).parent.resolve() == target_dir.resolve()


# ── write_output_file() — result summary field ───────────────────────────────


def test_write_output_file_summary_in_result(tmp_path: Path) -> None:
    """write_output_file result carries the summarize() string."""
    out_dir = tmp_path / "out"
    data = IMPACT_DATA
    r = write_output_file(data, command="impact", label="Sym", out_dir=out_dir)
    # Summary should be non-empty and mention "dependents" for impact
    assert isinstance(r["summary"], str)
    assert len(r["summary"]) > 0


# ── write_output_file() — filesystem failure surfaced ────────────────────────


def test_write_output_file_surfaces_fs_error(tmp_path: Path) -> None:
    """A genuine filesystem write failure raises an exception (not silently swallowed)."""
    # Point out_dir at a file path so mkdir fails
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("I am a file, not a dir")

    # out_dir is the blocking file itself — mkdirs will fail
    with pytest.raises((OSError, PermissionError)):
        write_output_file(
            {"data": 1},
            command="impact",
            label="X",
            out_dir=blocking_file,
        )


# ── summarize() — never raises (regression guard) ────────────────────────────


@pytest.mark.parametrize("command", ["impact", "context", "trace", "flows", "other"])
def test_summarize_never_raises_on_empty_dict(command: str) -> None:
    """summarize never raises regardless of command or data shape."""
    result = summarize({}, command)
    assert isinstance(result, str)


@pytest.mark.parametrize("bad_data", [None, 42, "string", [], True])
def test_summarize_never_raises_on_bad_types(bad_data: object) -> None:
    """summarize never raises even when data is completely wrong type."""
    result = summarize(bad_data, "impact")  # type: ignore[arg-type]
    assert isinstance(result, str)
