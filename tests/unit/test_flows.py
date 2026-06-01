"""Unit tests for seam/analysis/flows.py.

Tests build a hand-crafted fixture graph via the db write path (upsert_file),
then assert on the public interface: trace(), callers(), callees().

Coverage:
  T1  trace finds an existing multi-hop path (correct hops, correct order)
  T2  trace returns [] for an unconnected pair
  T3  trace terminates on a cycle (A->B->A) within the cap
  T4  trace: per-hop confidence present (EXTRACTED on a clean edge)
  T5  trace: AMBIGUOUS hop on path is surfaced (confidence visible per-hop)
  T6  trace: max_depth cap — path of len > cap returns []
  T7  trace: source == target returns [[]] (trivial self-path)
  T8  callers returns correct one-hop set with confidence
  T9  callers returns [] for a symbol with no callers
  T10 callees returns correct one-hop set with confidence
  T11 callees returns [] for a symbol with no callees
  T12 callers/callees: empty symbol string returns []
  T13 trace: direct one-hop path (two connected symbols)
  T14 trace: INFERRED confidence on hop is visible
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.flows import callees, callers, trace
from seam.analysis.traversal import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
)
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

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
def db_conn() -> tuple[sqlite3.Connection, str]:
    """Create a temporary, initialized SQLite DB. Yields (conn, src_path_str)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        src = tmp_path / "src.py"
        src.write_text("# stub\n")
        src_str = str(src)

        conn = init_db(db_path)
        yield conn, src_str  # type: ignore[misc]
        conn.close()


def _seed(conn: sqlite3.Connection, src: str, symbols: list[str], edges: list[Edge]) -> None:
    syms = [_sym(name, src) for name in symbols]
    upsert_file(conn, Path(src), "python", "hash1", syms, edges)


# ── T1: trace finds existing multi-hop path ───────────────────────────────────


def test_trace_multi_hop_path(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace should find A -> B -> C as a 2-hop path."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B", "C"], [_edge("A", "B", src), _edge("B", "C", src)])

    paths = trace(conn, "A", "C", max_depth=5)

    assert len(paths) == 1
    path = paths[0]
    assert len(path) == 2

    assert path[0]["from_name"] == "A"
    assert path[0]["to_name"] == "B"
    assert path[1]["from_name"] == "B"
    assert path[1]["to_name"] == "C"


def test_trace_path_hop_order(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Hops must be ordered from source to target."""
    conn, src = db_conn
    # Chain: X -> Y -> Z -> W
    _seed(
        conn,
        src,
        ["X", "Y", "Z", "W"],
        [_edge("X", "Y", src), _edge("Y", "Z", src), _edge("Z", "W", src)],
    )

    paths = trace(conn, "X", "W", max_depth=10)
    assert len(paths) == 1
    path = paths[0]

    # Verify each hop links to the next.
    for i in range(len(path) - 1):
        assert path[i]["to_name"] == path[i + 1]["from_name"], (
            f"Hop {i} to_name {path[i]['to_name']!r} != hop {i+1} from_name {path[i+1]['from_name']!r}"
        )
    assert path[0]["from_name"] == "X"
    assert path[-1]["to_name"] == "W"


# ── T2: trace returns [] for unconnected pair ─────────────────────────────────


def test_trace_unconnected_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace should return [] when no path exists between source and target."""
    conn, src = db_conn
    # A -> B, C is isolated — no path from A to C.
    _seed(conn, src, ["A", "B", "C"], [_edge("A", "B", src)])

    paths = trace(conn, "A", "C", max_depth=5)

    assert paths == []


def test_trace_unknown_symbol_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace returns [] when either symbol is not in the graph."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    # Source does not exist.
    assert trace(conn, "UNKNOWN", "B", max_depth=5) == []
    # Target does not exist.
    assert trace(conn, "A", "UNKNOWN", max_depth=5) == []


# ── T3: trace terminates on cycle ────────────────────────────────────────────


def test_trace_terminates_on_cycle(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace(A, A_unreachable) must terminate even with a cycle A->B->A."""
    conn, src = db_conn
    # Cycle: A -> B -> A; C is a dead-end not connected back.
    _seed(
        conn,
        src,
        ["A", "B", "C"],
        [_edge("A", "B", src), _edge("B", "A", src)],
    )

    # Trying to reach C from A: should terminate and return [].
    paths = trace(conn, "A", "C", max_depth=10)
    assert paths == []


def test_trace_cycle_finds_direct_neighbor(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace in a cycle graph can still find 1-hop reachable targets."""
    conn, src = db_conn
    # Cycle A -> B -> A; trace A -> B should find the direct edge.
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src), _edge("B", "A", src)])

    paths = trace(conn, "A", "B", max_depth=5)
    assert len(paths) == 1
    assert paths[0][0]["from_name"] == "A"
    assert paths[0][0]["to_name"] == "B"


# ── T4: per-hop confidence on a clean (EXTRACTED) path ───────────────────────


def test_trace_confidence_extracted(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Hops on an all-EXTRACTED path must carry EXTRACTED confidence."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src, confidence=CONFIDENCE_EXTRACTED)])

    paths = trace(conn, "A", "B", max_depth=3)
    assert len(paths) == 1
    assert paths[0][0]["confidence"] == CONFIDENCE_EXTRACTED


# ── T5: AMBIGUOUS hop surfaced on path ────────────────────────────────────────


def test_trace_ambiguous_hop_visible(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An AMBIGUOUS edge must be visible as confidence=AMBIGUOUS on the hop.

    Phase 1b: confidence resolved from whole index.
    To get AMBIGUOUS on the B->C hop, 'C' must appear more than once in the index.
    We insert a second 'C' symbol in a different file so count(C)=2 → AMBIGUOUS.
    A and B are unique → A->B hop is EXTRACTED.
    """
    import tempfile

    conn, src = db_conn
    _seed(
        conn,
        src,
        ["A", "B", "C"],
        [
            _edge("A", "B", src),
            _edge("B", "C", src),
        ],
    )

    # Insert a second 'C' in another file so count(C)=2 → AMBIGUOUS for B->C hop.
    with tempfile.TemporaryDirectory() as other_tmp:
        from pathlib import Path as _Path

        from seam.indexer.db import upsert_file as _upsert
        from seam.indexer.graph import Symbol as _Sym

        other_file = _Path(other_tmp) / "other.py"
        other_file.write_text("# other\n")
        other_sym = _Sym(name="C", kind="function", file=str(other_file), start_line=1, end_line=2, docstring=None)
        _upsert(conn, other_file, "python", "h_other", [other_sym], [])

        paths = trace(conn, "A", "C", max_depth=5)
        assert len(paths) == 1
        path = paths[0]

        # B->C hop: C count=2 → AMBIGUOUS.
        bc_hop = next((h for h in path if h["from_name"] == "B" and h["to_name"] == "C"), None)
        assert bc_hop is not None, "Expected a B->C hop"
        assert bc_hop["confidence"] == CONFIDENCE_AMBIGUOUS

        # A->B hop: B count=1 → EXTRACTED.
        ab_hop = next((h for h in path if h["from_name"] == "A" and h["to_name"] == "B"), None)
        assert ab_hop is not None
        assert ab_hop["confidence"] == CONFIDENCE_EXTRACTED


# ── T6: max_depth cap ─────────────────────────────────────────────────────────


def test_trace_depth_cap_blocks_long_path(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace should return [] when the path length exceeds max_depth."""
    conn, src = db_conn
    # 3-hop chain: A -> B -> C -> D
    _seed(
        conn,
        src,
        ["A", "B", "C", "D"],
        [_edge("A", "B", src), _edge("B", "C", src), _edge("C", "D", src)],
    )

    # max_depth=2: only 2 hops allowed; A->D is 3 hops -> []
    paths = trace(conn, "A", "D", max_depth=2)
    assert paths == []

    # max_depth=3: path of 3 hops fits exactly.
    paths = trace(conn, "A", "D", max_depth=3)
    assert len(paths) == 1


# ── T7: source == target (self-path) ─────────────────────────────────────────


def test_trace_self_returns_trivial_path(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace(source, source) should return [[]] — trivially connected, zero hops."""
    conn, src = db_conn
    _seed(conn, src, ["A"], [])

    paths = trace(conn, "A", "A", max_depth=5)
    # Returns a list with one entry: the empty path.
    assert paths == [[]]


# ── T8: callers returns correct one-hop set ───────────────────────────────────


def test_callers_one_hop(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callers(B) returns A when A->B is the only upstream edge."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    result = callers(conn, "B")

    names = [h["name"] for h in result]
    assert "A" in names
    # B is not its own caller.
    assert "B" not in names


def test_callers_confidence_present(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Each EdgeHop from callers() must include a confidence field.

    Phase 1b: confidence resolved from whole index based on target_name (B).
    To produce INFERRED, 'B' must not exist in the symbols table at all.
    We insert only A (the caller) and the edge A→B directly (no B symbol).
    """
    conn, src = db_conn
    _seed(conn, src, ["A"], [])  # only A in symbols; no B
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
        " SELECT 'A', 'B', 'call', id, 1, 'INFERRED' FROM files WHERE path = ? LIMIT 1",
        (src,),
    )
    conn.commit()

    result = callers(conn, "B")

    assert len(result) == 1
    # B is not in symbols (count=0) → INFERRED.
    assert result[0]["confidence"] == CONFIDENCE_INFERRED
    assert result[0]["name"] == "A"
    assert result[0]["kind"] == "call"


def test_callers_multiple_callers(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callers() returns all symbols that call the target."""
    conn, src = db_conn
    _seed(
        conn,
        src,
        ["A", "B", "C", "T"],
        [_edge("A", "T", src), _edge("B", "T", src), _edge("C", "T", src)],
    )

    result = callers(conn, "T")
    names = {h["name"] for h in result}
    assert names == {"A", "B", "C"}


# ── T9: callers returns [] for symbol with no callers ────────────────────────


def test_callers_no_callers_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callers returns [] for an isolated symbol."""
    conn, src = db_conn
    _seed(conn, src, ["A"], [])

    assert callers(conn, "A") == []


# ── T10: callees returns correct one-hop set ─────────────────────────────────


def test_callees_one_hop(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callees(A) returns B when A->B is the only downstream edge."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    result = callees(conn, "A")

    names = [h["name"] for h in result]
    assert "B" in names
    assert "A" not in names


def test_callees_confidence_present(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Each EdgeHop from callees() must include a confidence field.

    Phase 1b: confidence resolved from whole index based on target_name (B).
    To produce AMBIGUOUS, 'B' must appear in more than one file.
    We insert A (caller in src file), B in src file, and a second B in another file.
    """
    import tempfile

    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    with tempfile.TemporaryDirectory() as other_tmp:
        from pathlib import Path as _Path

        from seam.indexer.db import upsert_file as _upsert
        from seam.indexer.graph import Symbol as _Sym

        other_file = _Path(other_tmp) / "other.py"
        other_file.write_text("# other\n")
        _upsert(conn, other_file, "python", "h_other",
                [_Sym(name="B", kind="function", file=str(other_file), start_line=1, end_line=2, docstring=None)], [])

        result = callees(conn, "A")

        assert len(result) == 1
        # B count=2 → AMBIGUOUS.
        assert result[0]["confidence"] == CONFIDENCE_AMBIGUOUS
        assert result[0]["name"] == "B"


# ── T11: callees returns [] for symbol with no callees ───────────────────────


def test_callees_no_callees_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callees returns [] for a symbol that calls nothing."""
    conn, src = db_conn
    _seed(conn, src, ["A"], [])

    assert callees(conn, "A") == []


# ── T12: empty symbol string returns [] ─────────────────────────────────────


def test_callers_empty_symbol_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callers('') must return [] without querying the DB."""
    conn, _ = db_conn
    assert callers(conn, "") == []


def test_callees_empty_symbol_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """callees('') must return [] without querying the DB."""
    conn, _ = db_conn
    assert callees(conn, "") == []


# ── T13: direct one-hop path ─────────────────────────────────────────────────


def test_trace_direct_one_hop(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """trace(A, B) with a direct A->B edge returns a single-hop path."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    paths = trace(conn, "A", "B", max_depth=3)

    assert len(paths) == 1
    assert len(paths[0]) == 1
    hop = paths[0][0]
    assert hop["from_name"] == "A"
    assert hop["to_name"] == "B"
    assert hop["kind"] == "call"


# ── T14: INFERRED confidence on hop is visible ────────────────────────────────


def test_trace_inferred_hop_visible(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An INFERRED hop (unindexed target) must be visible as confidence=INFERRED on the hop.

    Phase 1b: confidence resolved from whole index based on target_name.
    To produce INFERRED, 'B' must not exist in the symbols table at all.
    We seed only A and insert the edge A→B directly so B is an unindexed target.
    """
    conn, src = db_conn
    _seed(conn, src, ["A"], [])  # only A in symbols
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
        " SELECT 'A', 'B', 'call', id, 1, 'INFERRED' FROM files WHERE path = ? LIMIT 1",
        (src,),
    )
    conn.commit()

    paths = trace(conn, "A", "B", max_depth=3)
    assert len(paths) == 1
    # B count=0 (not in symbols) → INFERRED.
    assert paths[0][0]["confidence"] == CONFIDENCE_INFERRED
