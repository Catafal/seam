"""Byte-stability regression test for the tools.py module split (Slice 2 / P2 #103).

WHY this test exists: The split is a PURE MECHANICAL REFACTOR — no behavior change.
These tests assert:
  1. Every handler and helper that tests/mcp.py/web.py/main.py import from
     seam.server.tools still resolves to a callable (re-export surface intact).
  2. Newly-extracted names also live in their respective new modules AND in tools.py
     (the facade re-exports them).
  3. No spurious new top-level keys appear on handler output after the split.

Prior art: tests/unit/test_handler_provenance.py, tests/unit/test_symbol_uid.py
"""

import importlib
from pathlib import Path
from typing import Any

import pytest

# ── 1. Re-export surface: every name used in the codebase must still import ──


# All public names that external callers (mcp.py, web.py, main.py, tests) use.
_HANDLER_NAMES = [
    "handle_seam_query",
    "handle_seam_context",
    "handle_seam_search",
    "handle_seam_impact",
    "handle_seam_trace",
    "handle_seam_changes",
    "handle_seam_why",
    "handle_seam_clusters",
    "handle_seam_flows",
    "handle_seam_affected",
    "handle_seam_context_pack",
    "handle_seam_structure",
]

# Private helpers that test files import directly from seam.server.tools.
_PRIVATE_NAMES = [
    "_apply_verbosity",
    "_serialize_hop",
    "_serialize_edge_hop",
    "_serialize_tier_entry",
    "_prioritize_tier_entries",
    "_maybe_attach_staleness",
    "compute_uid",
]

# Helpers that live in handler_common specifically (the shared layer).
_COMMON_NAMES = [
    "_apply_verbosity",
    "_serialize_hop",
    "_serialize_edge_hop",
    "_maybe_attach_staleness",
    "compute_uid",
]

# Helpers that live in impact_handler specifically (used by tests directly).
_IMPACT_HANDLER_NAMES = [
    "_serialize_tier_entry",
    "_prioritize_tier_entries",
]


@pytest.mark.parametrize("name", _HANDLER_NAMES)
def test_handler_importable_from_tools(name: str) -> None:
    """Every handler must still be importable from seam.server.tools (facade surface)."""
    tools = importlib.import_module("seam.server.tools")
    obj = getattr(tools, name, None)
    assert obj is not None, f"{name!r} is not exported from seam.server.tools"
    assert callable(obj), f"{name!r} should be callable"


@pytest.mark.parametrize("name", _PRIVATE_NAMES)
def test_helper_importable_from_tools(name: str) -> None:
    """Every helper imported by existing tests must still be importable from tools."""
    tools = importlib.import_module("seam.server.tools")
    obj = getattr(tools, name, None)
    assert obj is not None, f"{name!r} is not exported from seam.server.tools"


def test_handler_common_module_exports_shared_helpers() -> None:
    """The new handler_common module must export the shared helpers it owns."""
    common = importlib.import_module("seam.server.handler_common")
    for name in _COMMON_NAMES:
        assert hasattr(common, name), f"handler_common is missing {name!r}"


def test_impact_handler_module_exports_impact_specific_helpers() -> None:
    """impact_handler must export the impact-specific helpers used by tests."""
    mod = importlib.import_module("seam.server.impact_handler")
    for name in _IMPACT_HANDLER_NAMES:
        assert hasattr(mod, name), f"impact_handler is missing {name!r}"


def test_impact_handler_module_exports_impact_handler() -> None:
    """The new impact_handler module must export handle_seam_impact."""
    mod = importlib.import_module("seam.server.impact_handler")
    assert hasattr(mod, "handle_seam_impact"), "impact_handler missing handle_seam_impact"
    assert hasattr(mod, "_prioritize_tier_entries"), (
        "impact_handler missing _prioritize_tier_entries"
    )
    assert hasattr(mod, "_serialize_tier_entry"), "impact_handler missing _serialize_tier_entry"


def test_trace_handler_module_exports_trace_handler() -> None:
    """The new trace_handler module must export handle_seam_trace."""
    mod = importlib.import_module("seam.server.trace_handler")
    assert hasattr(mod, "handle_seam_trace"), "trace_handler missing handle_seam_trace"


def test_tools_facade_handler_is_same_callable_as_impact_handler() -> None:
    """tools.handle_seam_impact must be the SAME object as impact_handler.handle_seam_impact.

    WHY: if the facade re-exports by reference (not by value copy), then patching
    one is identical to patching the other — no test-isolation surprise.
    """
    tools = importlib.import_module("seam.server.tools")
    impact = importlib.import_module("seam.server.impact_handler")
    assert tools.handle_seam_impact is impact.handle_seam_impact


def test_tools_facade_handler_is_same_callable_as_trace_handler() -> None:
    """tools.handle_seam_trace must be the SAME object as trace_handler.handle_seam_trace."""
    tools = importlib.import_module("seam.server.tools")
    trace = importlib.import_module("seam.server.trace_handler")
    assert tools.handle_seam_trace is trace.handle_seam_trace


# ── 2. No new top-level keys injected by the split ──────────────────────────


def _make_temp_db(tmp_path: Path) -> Any:
    """Create a minimal on-disk Seam DB (empty) under tmp_path for handler smoke tests.

    Returns the open connection. The DB lives under pytest's tmp_path fixture, which
    is auto-cleaned — no leaked temp directory and no risk of writing artifacts into
    the repo root.
    """
    from seam.indexer.db import init_db

    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return init_db(db_path)


def test_search_handler_output_has_no_split_artifacts(tmp_path: Path) -> None:
    """handle_seam_search returns a list (or error dict); no stray keys from the split."""
    from unittest.mock import patch

    import seam.config as config
    from seam.server.tools import handle_seam_search

    conn = _make_temp_db(tmp_path)
    root = tmp_path

    with patch.object(config, "SEAM_STALENESS_CHECK", "off"):
        result = handle_seam_search(conn, "foo", root)

    # Empty FTS5 index → empty list (no FTS5 error on an initialized-but-empty index)
    assert isinstance(result, list), f"Expected list, got {type(result)}"


def test_impact_handler_output_has_no_split_artifacts(tmp_path: Path) -> None:
    """handle_seam_impact returns a dict with expected keys; no stray keys from the split."""
    from unittest.mock import patch

    import seam.config as config
    from seam.server.tools import handle_seam_impact

    conn = _make_temp_db(tmp_path)
    root = tmp_path

    with patch.object(config, "SEAM_STALENESS_CHECK", "off"):
        result = handle_seam_impact(conn, "nonexistent_symbol", root)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    # Core keys always present
    assert "found" in result
    assert "target" in result
    assert "risk_summary" in result
    # found=False for unknown symbol, no stray keys
    assert result["found"] is False
    # Ensure no extra top-level key was accidentally injected by the split
    expected_keys = {"found", "target", "risk_summary"}
    extra_keys = set(result.keys()) - expected_keys
    # Allow legitimate optional keys (truncated, byte_capped, etc.) but NOT
    # something like "handler_module" or other split artifacts.
    split_artifact_keys = {k for k in extra_keys if k.startswith("_") or "module" in k}
    assert not split_artifact_keys, f"Stray keys from split: {split_artifact_keys}"


def test_all_files_under_1000_lines() -> None:
    """Every seam/server/*.py file must be under the 1000-line cap after the split.

    This test reads each file and counts its lines. It is the gate-wired enforcement
    of the project's non-negotiable file-size rule.
    """
    server_dir = Path(__file__).parent.parent.parent / "seam" / "server"
    over_cap = []
    for py_file in sorted(server_dir.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
        if line_count >= 1000:
            over_cap.append((py_file.name, line_count))

    assert not over_cap, (
        "Files over 1000-line cap: "
        + ", ".join(f"{name}={count}" for name, count in over_cap)
    )
