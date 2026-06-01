"""Tests for whole-index confidence in flows.trace / callers / callees (Phase 1b).

These tests FAIL until flows.py is wired to use whole-index resolution.

Coverage:
  FL1  trace() — per-hop confidence reflects whole-index (unique target → EXTRACTED)
  FL2  callers() — one-hop callers confidence uses whole-index resolution
  FL3  callees() — one-hop callees confidence uses whole-index resolution
  FL4  callers() — target shared across files → AMBIGUOUS
  FL5  callers() — unindexed target stays INFERRED

Fixture style: temp DB, hand-built symbols + edges, assert on public API outputs.
"""

import tempfile
from pathlib import Path

from seam.analysis.flows import callees, callers, trace
from seam.analysis.traversal import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
)
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ──────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_INFERRED) -> Edge:
    """Default confidence is INFERRED — simulating what same-file resolver stores for cross-file edges."""
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


def _build_db(tmp_path: Path):
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir()
    return init_db(db_path)


# ── FL1: trace() per-hop confidence uses whole-index resolution ───────────────


def test_trace_hop_confidence_is_whole_index() -> None:
    """trace() must show EXTRACTED for a cross-file hop whose target is unique in the index.

    Setup:
      file_a: A → B  (stored INFERRED, cross-file)
      file_b: B defined (unique)

    trace(A, B) shortest path: one hop A→B with confidence EXTRACTED (unique target).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_a.write_text("# a\n")
            file_b.write_text("# b\n")

            upsert_file(conn, file_a, "python", "ha", [_sym("A", str(file_a))],
                        [_edge("A", "B", str(file_a), confidence=CONFIDENCE_INFERRED)])
            upsert_file(conn, file_b, "python", "hb", [_sym("B", str(file_b))], [])

            paths = trace(conn, "A", "B")
            assert len(paths) == 1, f"expected 1 path, got {len(paths)}"
            path = paths[0]
            assert len(path) == 1, f"expected 1-hop path, got {len(path)} hops"

            hop = path[0]
            assert hop["from_name"] == "A"
            assert hop["to_name"] == "B"
            assert hop["confidence"] == CONFIDENCE_EXTRACTED, (
                f"cross-file hop to unique target must be EXTRACTED, got {hop['confidence']!r}"
            )
        finally:
            conn.close()


# ── FL2: callees() uses whole-index resolution ────────────────────────────────


def test_callees_unique_target_is_extracted() -> None:
    """callees() must return EXTRACTED confidence for a callee that is unique in the index.

    A (in file_a) calls B (in file_b). The stored edge is INFERRED (cross-file at index time).
    After whole-index resolution: B has count=1 → EXTRACTED.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_a.write_text("# a\n")
            file_b.write_text("# b\n")

            upsert_file(conn, file_a, "python", "ha", [_sym("A", str(file_a))],
                        [_edge("A", "B", str(file_a), confidence=CONFIDENCE_INFERRED)])
            upsert_file(conn, file_b, "python", "hb", [_sym("B", str(file_b))], [])

            result = callees(conn, "A")
            b_hop = next((h for h in result if h["name"] == "B"), None)

            assert b_hop is not None, "B must appear as a callee of A"
            assert b_hop["confidence"] == CONFIDENCE_EXTRACTED, (
                f"callee B is unique in index → EXTRACTED, got {b_hop['confidence']!r}"
            )
        finally:
            conn.close()


# ── FL3: callers() uses whole-index resolution ────────────────────────────────


def test_callers_unique_target_is_extracted() -> None:
    """callers(B) must return EXTRACTED when B is uniquely defined in the index.

    A (file_a) calls B (file_b). callers(B) returns A with confidence EXTRACTED
    (because B — the edge's target_name — has count=1 in the whole index).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_a.write_text("# a\n")
            file_b.write_text("# b\n")

            upsert_file(conn, file_a, "python", "ha", [_sym("A", str(file_a))],
                        [_edge("A", "B", str(file_a), confidence=CONFIDENCE_INFERRED)])
            upsert_file(conn, file_b, "python", "hb", [_sym("B", str(file_b))], [])

            result = callers(conn, "B")
            a_hop = next((h for h in result if h["name"] == "A"), None)

            assert a_hop is not None, "A must appear as a caller of B"
            assert a_hop["confidence"] == CONFIDENCE_EXTRACTED, (
                f"B is unique in index → callers() edge confidence EXTRACTED, got {a_hop['confidence']!r}"
            )
        finally:
            conn.close()


# ── FL4: callers() — shared target across files → AMBIGUOUS ──────────────────


def test_callers_ambiguous_target() -> None:
    """callers(shared_fn) must return AMBIGUOUS when shared_fn is in two files.

    The edge's target_name is 'shared_fn'; count=2 in index → AMBIGUOUS.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_c = tmp_path / "c.py"
            for f in (file_a, file_b, file_c):
                f.write_text("# stub\n")

            # A calls shared_fn (cross-file, stored INFERRED)
            upsert_file(conn, file_a, "python", "ha", [_sym("A", str(file_a))],
                        [_edge("A", "shared_fn", str(file_a), confidence=CONFIDENCE_INFERRED)])
            # shared_fn defined in two files → count=2
            upsert_file(conn, file_b, "python", "hb", [_sym("shared_fn", str(file_b))], [])
            upsert_file(conn, file_c, "python", "hc", [_sym("shared_fn", str(file_c))], [])

            result = callers(conn, "shared_fn")
            a_hop = next((h for h in result if h["name"] == "A"), None)

            assert a_hop is not None
            assert a_hop["confidence"] == CONFIDENCE_AMBIGUOUS, (
                f"shared_fn (count=2) → AMBIGUOUS, got {a_hop['confidence']!r}"
            )
        finally:
            conn.close()


# ── FL5: callees() — unindexed target stays INFERRED ──────────────────────────


def test_callees_unindexed_target_stays_inferred() -> None:
    """callees() for an unindexed target (e.g. stdlib) must remain INFERRED."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_a.write_text("# stub\n")

            upsert_file(conn, file_a, "python", "ha", [_sym("A", str(file_a))],
                        [_edge("A", "os_path_join", str(file_a), confidence=CONFIDENCE_INFERRED)])

            result = callees(conn, "A")
            hop = next((h for h in result if h["name"] == "os_path_join"), None)

            assert hop is not None
            assert hop["confidence"] == CONFIDENCE_INFERRED, (
                f"unindexed callee must stay INFERRED, got {hop['confidence']!r}"
            )
        finally:
            conn.close()
