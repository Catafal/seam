"""Integration tests for handle_seam_trace (seam/server/tools.py).

Tests call the handler directly against a seeded SQLite DB, mirroring the
style of test_impact_handler.py. Fixtures build known graphs via the db write path.

Coverage:
  T1  blank source -> INVALID_INPUT
  T2  whitespace-only source -> INVALID_INPUT
  T3  blank target -> INVALID_INPUT
  T4  whitespace-only target -> INVALID_INPUT
  T5  depth clamping (0 -> 1, 999 -> 10)
  T6  happy path: existing multi-hop path found, hops ordered correctly
  T7  unconnected pair -> found=False, paths=[]
  T8  callers_source and callees_source present in response
  T9  callers_target and callees_target present in response
  T10 result is JSON-serializable (plain dict, no TypedDict wrappers)
  T11 per-hop confidence present in each hop dict (including AMBIGUOUS)
  T12 source == target -> found=True, paths=[[]]
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.traversal import CONFIDENCE_AMBIGUOUS, CONFIDENCE_EXTRACTED, CONFIDENCE_INFERRED
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_trace

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(
    source: str,
    target: str,
    file: str,
    confidence: str = CONFIDENCE_EXTRACTED,
    kind: str = "call",
) -> Edge:
    return Edge(source=source, target=target, kind=kind, file=file, line=1, confidence=confidence)


@pytest.fixture()
def seeded_trace_db() -> tuple[sqlite3.Connection, Path]:
    """Create a DB seeded with a 3-node chain:
        A -[EXTRACTED]-> B -[INFERRED]-> C

    Also seeds: D -[AMBIGUOUS]-> A (so A has a caller D with AMBIGUOUS confidence).

    Returns (conn, project_root).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        src = tmp_path / "src.py"
        src.write_text("# stub\n")

        conn = init_db(db_path)
        upsert_file(
            conn,
            src,
            "python",
            "hash1",
            [_sym("A", str(src)), _sym("B", str(src)), _sym("C", str(src)), _sym("D", str(src))],
            [
                _edge("A", "B", str(src), CONFIDENCE_EXTRACTED),
                _edge("B", "C", str(src), CONFIDENCE_INFERRED),
                _edge("D", "A", str(src), CONFIDENCE_AMBIGUOUS),
            ],
        )

        yield conn, tmp_path  # type: ignore[misc]
        conn.close()


# ── T1: blank source -> INVALID_INPUT ────────────────────────────────────────


def test_blank_source_returns_invalid_input(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_trace must return INVALID_INPUT for a blank source."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "", "C", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T2: whitespace source -> INVALID_INPUT ───────────────────────────────────


def test_whitespace_source_returns_invalid_input(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Whitespace-only source must also return INVALID_INPUT."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "   ", "C", root)

    assert result.get("error") == "INVALID_INPUT"


# ── T3: blank target -> INVALID_INPUT ────────────────────────────────────────


def test_blank_target_returns_invalid_input(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_trace must return INVALID_INPUT for a blank target."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "", root)

    assert result.get("error") == "INVALID_INPUT"


# ── T4: whitespace target -> INVALID_INPUT ───────────────────────────────────


def test_whitespace_target_returns_invalid_input(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Whitespace-only target must also return INVALID_INPUT."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "   ", root)

    assert result.get("error") == "INVALID_INPUT"


# ── T5: depth clamping ────────────────────────────────────────────────────────


def test_depth_clamping_zero(seeded_trace_db: tuple[sqlite3.Connection, Path]) -> None:
    """max_depth=0 must be silently clamped to 1 (not raise)."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "B", root, max_depth=0)
    # Should not raise; returns a valid trace dict (even if no path within 1 hop).
    assert isinstance(result, dict)
    assert "error" not in result


def test_depth_clamping_high(seeded_trace_db: tuple[sqlite3.Connection, Path]) -> None:
    """max_depth=999 must be silently clamped to 10 (not raise)."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "C", root, max_depth=999)
    assert isinstance(result, dict)
    assert "error" not in result


# ── T6: happy path — multi-hop path found ────────────────────────────────────


def test_happy_path_multi_hop(seeded_trace_db: tuple[sqlite3.Connection, Path]) -> None:
    """trace A -> C must return a 2-hop path: A->B->C."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "C", root, max_depth=10)

    assert isinstance(result, dict)
    assert "error" not in result
    assert result["found"] is True
    assert result["source"] == "A"
    assert result["target"] == "C"

    paths = result["paths"]
    assert len(paths) == 1
    path = paths[0]
    assert len(path) == 2

    # First hop: A -> B
    assert path[0]["from_name"] == "A"
    assert path[0]["to_name"] == "B"
    # Second hop: B -> C
    assert path[1]["from_name"] == "B"
    assert path[1]["to_name"] == "C"


# ── T7: unconnected pair -> found=False, paths=[] ────────────────────────────


def test_unconnected_pair_returns_not_found(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """An unconnected pair must return found=False and empty paths."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "C", "A", root, max_depth=10)

    assert result["found"] is False
    assert result["paths"] == []
    assert result["source"] == "C"
    assert result["target"] == "A"


# ── T8: callers_source and callees_source present ────────────────────────────


def test_callers_and_callees_source_present(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Response must include callers_source and callees_source for the source symbol."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "C", root)

    assert "callers_source" in result
    assert "callees_source" in result

    # A is called by D -> callers_source contains D.
    callers_source_names = {h["name"] for h in result["callers_source"]}
    assert "D" in callers_source_names

    # A calls B -> callees_source contains B.
    callees_source_names = {h["name"] for h in result["callees_source"]}
    assert "B" in callees_source_names


# ── T9: callers_target and callees_target present ────────────────────────────


def test_callers_and_callees_target_present(
    seeded_trace_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Response must include callers_target and callees_target for the target symbol."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "C", root)

    assert "callers_target" in result
    assert "callees_target" in result

    # C is called by B -> callers_target contains B.
    callers_target_names = {h["name"] for h in result["callers_target"]}
    assert "B" in callers_target_names

    # C calls nothing -> callees_target is empty.
    assert result["callees_target"] == []


# ── T10: result is JSON-serializable ─────────────────────────────────────────


def test_result_is_json_serializable(seeded_trace_db: tuple[sqlite3.Connection, Path]) -> None:
    """handle_seam_trace must return a plain dict with JSON-serializable values."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "C", root)

    # Must not raise.
    json_str = json.dumps(result)
    assert isinstance(json_str, str)


# ── T11: per-hop confidence present and correct ──────────────────────────────


def test_per_hop_confidence_present(seeded_trace_db: tuple[sqlite3.Connection, Path]) -> None:
    """Each hop in a path must include a 'confidence' field."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "C", root)

    assert result["found"] is True
    paths = result["paths"]
    assert len(paths) == 1
    path = paths[0]

    for hop in path:
        assert "confidence" in hop, f"Hop missing 'confidence' key: {hop}"
        assert hop["confidence"] in {CONFIDENCE_EXTRACTED, CONFIDENCE_INFERRED, CONFIDENCE_AMBIGUOUS}


def test_ambiguous_hop_in_callers() -> None:
    """D→A must surface in callers_source with confidence=AMBIGUOUS when A is in 2 files.

    Phase 1b: callers() resolves confidence from whole-index based on edge target_name (A).
    To get AMBIGUOUS, A must appear in more than one file (count=2).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        src = tmp_path / "src.py"
        src2 = tmp_path / "src2.py"
        src.write_text("# stub\n")
        src2.write_text("# stub2\n")

        conn = init_db(db_path)
        try:
            # src.py: A, B, C, D are indexed; edge D→A (target A will be count=2)
            upsert_file(
                conn, src, "python", "hash1",
                [_sym("A", str(src)), _sym("B", str(src)), _sym("C", str(src)), _sym("D", str(src))],
                [
                    _edge("A", "B", str(src), CONFIDENCE_EXTRACTED),
                    _edge("B", "C", str(src), CONFIDENCE_EXTRACTED),
                    _edge("D", "A", str(src)),  # stored confidence doesn't matter
                ],
            )
            # src2.py: second definition of A → count(A)=2 → D→A edge resolves to AMBIGUOUS.
            upsert_file(
                conn, src2, "python", "hash2",
                [_sym("A", str(src2))],
                [],
            )

            result = handle_seam_trace(conn, "A", "C", tmp_path)

            callers_source = result["callers_source"]
            d_hop = next((h for h in callers_source if h["name"] == "D"), None)

            assert d_hop is not None, "Expected D in callers_source"
            assert d_hop["confidence"] == CONFIDENCE_AMBIGUOUS, (
                f"A defined in 2 files → D→A edge is AMBIGUOUS, got {d_hop['confidence']!r}"
            )
        finally:
            conn.close()


# ── T12: source == target -> found=True, paths=[[]] ──────────────────────────


def test_source_equals_target(seeded_trace_db: tuple[sqlite3.Connection, Path]) -> None:
    """trace(A, A) should return found=True, paths=[[]] (trivial self-path)."""
    conn, root = seeded_trace_db
    result = handle_seam_trace(conn, "A", "A", root)

    assert result["found"] is True
    assert result["paths"] == [[]]
