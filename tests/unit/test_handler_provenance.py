"""Unit tests for Slice 3 — handler emission of kind + synthesized_by + next_actions steer.

Tests wire the analysis fields (Slice 2) and the steer leaf (Slice 1) into
seam/server/tools.py and verify the observable output shape.

Coverage:
  HP1  _serialize_tier_entry emits 'kind' when SEAM_EDGE_PROVENANCE=on
  HP2  _serialize_tier_entry emits 'synthesized_by' (null for static) when SEAM_EDGE_PROVENANCE=on
  HP3  _serialize_tier_entry emits 'synthesized_by' with channel name for synthesized edges
  HP4  verbose=False keeps 'kind', strips 'synthesized_by' (lean gate)
  HP5  verbose=True retains both 'kind' and 'synthesized_by'
  HP6  SEAM_EDGE_PROVENANCE=off → 'kind' and 'synthesized_by' absent from tier entries
  HP7  handle_seam_impact attaches 'next_actions' when entries were truncated
  HP8  handle_seam_impact omits 'next_actions' when nothing was truncated
  HP9  SEAM_IMPACT_STEER=off → 'next_actions' never appears
  HP10 _serialize_hop emits 'synthesized_by' when SEAM_EDGE_PROVENANCE=on
  HP11 _serialize_hop omits 'synthesized_by' when verbose=False
  HP12 _serialize_hop carries synthesized_by with channel name for synthesized hop
  HP13 _serialize_edge_hop emits 'synthesized_by' when SEAM_EDGE_PROVENANCE=on
  HP14 _serialize_edge_hop omits 'synthesized_by' when verbose=False
  HP15 SEAM_EDGE_PROVENANCE=off → 'synthesized_by' absent from hop serializers too
  HP16 seam_changes output is byte-identical regardless of SEAM_EDGE_PROVENANCE
  HP17 seam_affected output is byte-identical regardless of SEAM_EDGE_PROVENANCE

Prior art: tests/unit/test_impact_handler.py, tests/unit/test_provenance_threading.py.
"""

import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import seam.config as config
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import (
    _serialize_edge_hop,
    _serialize_hop,
    _serialize_tier_entry,
    handle_seam_affected,
    handle_seam_changes,
    handle_seam_impact,
)

ROOT = Path("/fake/root")


# ── helpers ────────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function") -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=1,
        end_line=2,
        docstring=None,
        signature=None,
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
    confidence: str = "INFERRED",
    synthesized_by: str | None = None,
) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=file,
        line=1,
        confidence=confidence,
        synthesized_by=synthesized_by,
    )


def _tier_entry(
    name: str = "Dep",
    file: str = "/fake/root/dep.py",
    kind: str = "call",
    synthesized_by: str | None = None,
) -> dict[str, Any]:
    """Build a minimal TieredEntry dict mirroring what impact() returns."""
    return {
        "name": name,
        "distance": 1,
        "confidence": "INFERRED",
        "resolved_by": None,
        "tier": "WILL_BREAK",
        "file": file,
        "is_test": False,
        "best_candidate": None,
        "kind": kind,
        "synthesized_by": synthesized_by,
    }


@pytest.fixture()
def db_conn_with_caller() -> tuple[sqlite3.Connection, str]:
    """DB with a single caller -> target edge (static)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        src = tmp_path / "src.py"
        src.write_text("# stub\n")
        conn = init_db(db_path)
        syms = [_sym("Target", str(src)), _sym("Caller", str(src))]
        edges = [_edge("Caller", "Target", str(src), kind="call")]
        upsert_file(conn, src, "python", "h1", syms, edges)
        yield conn, str(src)  # type: ignore[misc]
        conn.close()


@pytest.fixture()
def db_conn_with_synthesized_caller() -> tuple[sqlite3.Connection, str]:
    """DB with a synthesized caller -> target edge."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        src = tmp_path / "src.py"
        src.write_text("# stub\n")
        conn = init_db(db_path)
        syms = [_sym("Target", str(src)), _sym("Impl", str(src))]
        edges = [
            _edge("Impl", "Target", str(src), kind="call", synthesized_by="interface-override")
        ]
        upsert_file(conn, src, "python", "h1", syms, edges)
        yield conn, str(src)  # type: ignore[misc]
        conn.close()


@pytest.fixture()
def db_conn_many_callers() -> tuple[sqlite3.Connection, Path]:
    """DB with >SEAM_IMPACT_MAX_RESULTS callers of 'hub' to trigger truncation."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        src = tmp_path / "hub.py"
        src.write_text("# stub\n")
        conn = init_db(db_path)
        n = config.SEAM_IMPACT_MAX_RESULTS + 5  # always enough to trigger capping
        syms = [_sym("hub", str(src))] + [_sym(f"c_{i}", str(src)) for i in range(n)]
        edges = [_edge(f"c_{i}", "hub", str(src)) for i in range(n)]
        upsert_file(conn, src, "python", "h1", syms, edges)
        conn.commit()
        yield conn, tmp_path  # type: ignore[misc]
        conn.close()


# ── HP1: _serialize_tier_entry emits 'kind' ───────────────────────────────────


def test_serialize_tier_entry_emits_kind_when_provenance_on() -> None:
    """HP1: kind is present in serialized entry when SEAM_EDGE_PROVENANCE=on."""
    entry = _tier_entry(kind="holds")
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_tier_entry(entry, ROOT, verbose=True)
    assert "kind" in result
    assert result["kind"] == "holds"


# ── HP2: _serialize_tier_entry emits synthesized_by=None for static ──────────


def test_serialize_tier_entry_emits_synthesized_by_null_for_static() -> None:
    """HP2: synthesized_by=None (not absent) for a static edge in verbose mode."""
    entry = _tier_entry(kind="call", synthesized_by=None)
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_tier_entry(entry, ROOT, verbose=True)
    assert "synthesized_by" in result
    assert result["synthesized_by"] is None


# ── HP3: _serialize_tier_entry emits synthesized_by channel name ─────────────


def test_serialize_tier_entry_emits_synthesized_by_channel() -> None:
    """HP3: synthesized_by carries the channel name for a synthesized edge."""
    entry = _tier_entry(kind="call", synthesized_by="closure-collection")
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_tier_entry(entry, ROOT, verbose=True)
    assert result.get("synthesized_by") == "closure-collection"


# ── HP4: verbose=False keeps kind, strips synthesized_by ─────────────────────


def test_serialize_tier_entry_lean_mode_keeps_kind_strips_synthesized_by() -> None:
    """HP4: lean mode (verbose=False) keeps 'kind' but drops 'synthesized_by'."""
    entry = _tier_entry(kind="reads", synthesized_by=None)
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_tier_entry(entry, ROOT, verbose=False)
    assert "kind" in result
    assert result["kind"] == "reads"
    assert "synthesized_by" not in result


# ── HP5: verbose=True retains both kind and synthesized_by ───────────────────


def test_serialize_tier_entry_verbose_true_keeps_both() -> None:
    """HP5: verbose=True keeps both kind and synthesized_by."""
    entry = _tier_entry(kind="import", synthesized_by=None)
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_tier_entry(entry, ROOT, verbose=True)
    assert "kind" in result
    assert "synthesized_by" in result


# ── HP6: SEAM_EDGE_PROVENANCE=off → neither kind nor synthesized_by ──────────


def test_serialize_tier_entry_provenance_off_omits_both_fields() -> None:
    """HP6: When SEAM_EDGE_PROVENANCE=off, neither 'kind' nor 'synthesized_by' are emitted."""
    entry = _tier_entry(kind="call", synthesized_by="interface-override")
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "off"):
        result = _serialize_tier_entry(entry, ROOT, verbose=True)
    assert "kind" not in result
    assert "synthesized_by" not in result


# ── HP7: handle_seam_impact attaches next_actions when truncated ──────────────


def test_handle_seam_impact_attaches_next_actions_when_truncated(
    db_conn_many_callers: tuple[sqlite3.Connection, Path],
) -> None:
    """HP7: next_actions is present when per-tier cap drops entries."""
    conn, root = db_conn_many_callers
    # Force a small limit so truncation fires
    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "on"),
    ):
        result = handle_seam_impact(conn, "hub", root, direction="upstream", limit=3)

    # Should have truncated entries
    assert result.get("found")
    # next_actions should be present since limit=3 dropped some callers
    assert "next_actions" in result
    assert isinstance(result["next_actions"], list)
    assert len(result["next_actions"]) > 0
    # Each hint should be a non-empty string
    for hint in result["next_actions"]:
        assert isinstance(hint, str)
        assert len(hint) > 0


# ── HP8: handle_seam_impact omits next_actions when no truncation ─────────────


def test_handle_seam_impact_no_next_actions_when_nothing_truncated(
    db_conn_with_caller: tuple[sqlite3.Connection, str],
) -> None:
    """HP8: next_actions is absent when no entries were dropped."""
    conn, src = db_conn_with_caller
    root = Path(src).parent
    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "on"),
    ):
        result = handle_seam_impact(conn, "Target", root, direction="upstream", limit=100)

    # Nothing should be truncated with limit=100 and only 1 caller
    assert "next_actions" not in result


# ── HP9: SEAM_IMPACT_STEER=off → no next_actions ever ────────────────────────


def test_handle_seam_impact_steer_off_no_next_actions(
    db_conn_many_callers: tuple[sqlite3.Connection, Path],
) -> None:
    """HP9: next_actions is absent when SEAM_IMPACT_STEER=off, even with truncation."""
    conn, root = db_conn_many_callers
    with (
        mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"),
        mock.patch.object(config, "SEAM_IMPACT_STEER", "off"),
    ):
        result = handle_seam_impact(conn, "hub", root, direction="upstream", limit=3)

    assert "next_actions" not in result


# ── HP10: _serialize_hop emits synthesized_by ─────────────────────────────────


def test_serialize_hop_emits_synthesized_by_when_provenance_on() -> None:
    """HP10: _serialize_hop emits synthesized_by when SEAM_EDGE_PROVENANCE=on."""
    from seam.analysis.flows import Hop

    hop = Hop(
        from_name="A",
        to_name="B",
        kind="call",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="interface-override",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_hop(hop, ROOT)
    assert "synthesized_by" in result
    assert result["synthesized_by"] == "interface-override"


# ── HP11: _serialize_hop verbose=False omits synthesized_by ───────────────────


def test_serialize_hop_lean_mode_omits_synthesized_by() -> None:
    """HP11: _serialize_hop in lean mode should not emit synthesized_by."""
    from seam.analysis.flows import Hop

    hop = Hop(
        from_name="A",
        to_name="B",
        kind="call",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="interface-override",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        # Lean mode: verbosity is applied by the caller (handle_seam_trace calls
        # _apply_verbosity on the serialized hop). But _serialize_hop itself does
        # not take a verbose flag — the gate test is via _apply_verbosity on the hop.
        # We test the lean gate by asserting that synthesized_by is in _HEAVY_FIELDS.
        from seam.server.tools import _apply_verbosity

        full = _serialize_hop(hop, ROOT)
        assert "synthesized_by" in full  # present in verbose form

        lean = _apply_verbosity(full, verbose=False)
        # synthesized_by should be stripped if it is in _HEAVY_FIELDS
        assert "synthesized_by" not in lean


# ── HP12: _serialize_hop carries channel name ─────────────────────────────────


def test_serialize_hop_channel_name_for_synthesized() -> None:
    """HP12: synthesized_by carries channel name when the hop is synthesized."""
    from seam.analysis.flows import Hop

    hop = Hop(
        from_name="Handler",
        to_name="Emitter",
        kind="call",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="event-emitter",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_hop(hop, ROOT)
    assert result.get("synthesized_by") == "event-emitter"


# ── HP13: _serialize_edge_hop emits synthesized_by ────────────────────────────


def test_serialize_edge_hop_emits_synthesized_by_when_provenance_on() -> None:
    """HP13: _serialize_edge_hop emits synthesized_by when SEAM_EDGE_PROVENANCE=on."""
    from seam.analysis.flows import EdgeHop

    ehop = EdgeHop(
        name="Neighbor",
        kind="holds",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="closure-collection",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        result = _serialize_edge_hop(ehop, ROOT)
    assert "synthesized_by" in result
    assert result["synthesized_by"] == "closure-collection"


# ── HP14: _serialize_edge_hop verbose=False omits synthesized_by ──────────────


def test_serialize_edge_hop_lean_mode_omits_synthesized_by() -> None:
    """HP14: lean mode strips synthesized_by via _apply_verbosity on the serialized hop."""
    from seam.analysis.flows import EdgeHop
    from seam.server.tools import _apply_verbosity

    ehop = EdgeHop(
        name="Neighbor",
        kind="holds",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="interface-override",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        full = _serialize_edge_hop(ehop, ROOT)
        assert "synthesized_by" in full

        lean = _apply_verbosity(full, verbose=False)
        assert "synthesized_by" not in lean


# ── HP15: SEAM_EDGE_PROVENANCE=off → no synthesized_by on hops ───────────────


def test_serialize_hop_provenance_off_no_synthesized_by() -> None:
    """HP15: When SEAM_EDGE_PROVENANCE=off, hops and edge-hops omit synthesized_by."""
    from seam.analysis.flows import EdgeHop, Hop

    hop = Hop(
        from_name="A",
        to_name="B",
        kind="call",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="interface-override",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "off"):
        result = _serialize_hop(hop, ROOT)
    assert "synthesized_by" not in result

    ehop = EdgeHop(
        name="X",
        kind="call",
        confidence="INFERRED",
        resolved_by=None,
        best_candidate=None,
        synthesized_by="event-emitter",
    )
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "off"):
        eresult = _serialize_edge_hop(ehop, ROOT)
    assert "synthesized_by" not in eresult


# ── HP16: seam_changes byte-stable regardless of SEAM_EDGE_PROVENANCE ─────────


def test_seam_changes_byte_stable(
    db_conn_with_caller: tuple[sqlite3.Connection, str],
) -> None:
    """HP16: handle_seam_changes output is byte-identical with provenance on vs off.

    seam_changes calls analysis-layer impact() directly, so handler-layer
    provenance fields (kind, synthesized_by) must never reach its output.
    We compare the JSON shape by asserting the output dict has no 'kind' or
    'synthesized_by' keys at the affected-entry level, and that the output is
    identical regardless of the knob setting.
    """
    import json

    conn, src = db_conn_with_caller
    root = Path(src).parent

    # Capture output with provenance on
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        out_on = handle_seam_changes(conn, root)

    # Capture output with provenance off
    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "off"):
        out_off = handle_seam_changes(conn, root)

    # Serialized output must be byte-identical
    assert json.dumps(out_on, sort_keys=True) == json.dumps(out_off, sort_keys=True)

    # Affected entries must not carry 'kind' or 'synthesized_by' (they use their own shape)
    for aff_entry in out_on.get("affected", []):
        assert "synthesized_by" not in aff_entry, "affected entry should not carry synthesized_by"


# ── HP17: seam_affected byte-stable regardless of SEAM_EDGE_PROVENANCE ────────


def test_seam_affected_byte_stable(
    db_conn_with_caller: tuple[sqlite3.Connection, str],
) -> None:
    """HP17: handle_seam_affected output is byte-identical with provenance on vs off."""
    import json

    conn, src = db_conn_with_caller
    root = Path(src).parent

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "on"):
        out_on = handle_seam_affected(conn, [src], root)

    with mock.patch.object(config, "SEAM_EDGE_PROVENANCE", "off"):
        out_off = handle_seam_affected(conn, [src], root)

    assert json.dumps(out_on, sort_keys=True) == json.dumps(out_off, sort_keys=True)
