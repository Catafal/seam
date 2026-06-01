"""Unit tests for seam/analysis/traversal.py.

Tests build a hand-crafted fixture graph via the db write path (upsert_file),
then assert on the public interface: walk(conn, seeds, direction, max_depth).

Coverage:
  T1  upstream walk returns callers (edges where target_name == seed)
  T2  downstream walk returns callees (edges where source_name == seed)
  T3  empty seeds returns empty list
  T4  max_depth=1 caps results to direct neighbors only
  T5  max_depth=2 reaches two-hop neighbors
  T6  cycle (A->B->A) terminates and returns bounded results
  T7  unknown symbol (no edges) returns empty list
  T8  path confidence: AMBIGUOUS hop downgrades whole path
  T9  path confidence: INFERRED hop downgrades vs EXTRACTED
  T10 path confidence: all-EXTRACTED path stays EXTRACTED
  T11 multiple paths to same symbol — strongest confidence wins
  T12 self-edges are skipped (source == target)
  T13 seeds are not included in the output
  T14 direction=both not supported by walk directly (two separate walks)
  T15 >1000 upstream callers do not raise OperationalError (SQLite variable limit)
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.traversal import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
    walk,
)
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    """Helper: create a minimal Symbol dict."""
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_EXTRACTED) -> Edge:
    """Helper: create an Edge dict with a given confidence."""
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


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
    """Helper: insert symbols + edges into the DB via upsert_file."""
    syms = [_sym(name, src) for name in symbols]
    upsert_file(conn, Path(src), "python", "hash1", syms, edges)


# ── T1: upstream walk ─────────────────────────────────────────────────────────


def test_upstream_returns_callers(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """upstream walk: follow edges where target_name == seed -> return source_name."""
    conn, src = db_conn
    # Graph: A -> B (A calls B), so upstream of B is A.
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    results = walk(conn, ["B"], "upstream", max_depth=1)

    names = [r["name"] for r in results]
    assert "A" in names
    assert "B" not in names  # seeds excluded


# ── T2: downstream walk ───────────────────────────────────────────────────────


def test_downstream_returns_callees(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """downstream walk: follow edges where source_name == seed -> return target_name."""
    conn, src = db_conn
    # Graph: A -> B, so downstream of A is B.
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    results = walk(conn, ["A"], "downstream", max_depth=1)

    names = [r["name"] for r in results]
    assert "B" in names
    assert "A" not in names  # seeds excluded


# ── T3: empty seeds ───────────────────────────────────────────────────────────


def test_empty_seeds_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """walk with empty seeds must return an empty list."""
    conn, _ = db_conn
    results = walk(conn, [], "upstream", max_depth=3)
    assert results == []


# ── T4: max_depth=1 caps to direct neighbors ─────────────────────────────────


def test_max_depth_1_caps_results(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """max_depth=1 must not return two-hop neighbors."""
    conn, src = db_conn
    # Graph: C -> B -> A (so upstream of A at d=1 is B, at d=2 is C).
    _seed(conn, src, ["A", "B", "C"], [_edge("B", "A", src), _edge("C", "B", src)])

    results = walk(conn, ["A"], "upstream", max_depth=1)
    names = [r["name"] for r in results]

    assert "B" in names
    assert "C" not in names  # two hops away — must not appear at max_depth=1


# ── T5: max_depth=2 reaches two-hop neighbors ────────────────────────────────


def test_max_depth_2_reaches_two_hops(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """max_depth=2 must return both d=1 and d=2 neighbors."""
    conn, src = db_conn
    # Graph: C -> B -> A.
    _seed(conn, src, ["A", "B", "C"], [_edge("B", "A", src), _edge("C", "B", src)])

    results = walk(conn, ["A"], "upstream", max_depth=2)
    by_name = {r["name"]: r for r in results}

    assert "B" in by_name and by_name["B"]["distance"] == 1
    assert "C" in by_name and by_name["C"]["distance"] == 2


# ── T6: cycle terminates ──────────────────────────────────────────────────────


def test_cycle_terminates(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """A cycle (A->B->A) must terminate and return a bounded result."""
    conn, src = db_conn
    # Graph: A -> B -> A (cycle)
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src), _edge("B", "A", src)])

    # upstream of A: follow edges where target == A -> source B (d=1)
    # Then from B: edges where target == B -> source A, but A is already visited.
    results = walk(conn, ["A"], "upstream", max_depth=10)

    # Should terminate; B is reachable, A is not returned (seed)
    names = [r["name"] for r in results]
    assert "B" in names
    assert len(results) < 10  # definitely not infinite


# ── T7: unknown symbol ────────────────────────────────────────────────────────


def test_unknown_symbol_returns_empty(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """A symbol with no edges in the graph should return an empty list."""
    conn, _ = db_conn
    results = walk(conn, ["totally_unknown_xyz"], "upstream", max_depth=3)
    assert results == []


# ── T8: AMBIGUOUS hop downgrades path ────────────────────────────────────────


def test_ambiguous_hop_downgrades_path(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An AMBIGUOUS edge on the path must make the whole path AMBIGUOUS."""
    conn, src = db_conn
    # Graph: A -[AMBIGUOUS]-> B
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src, confidence=CONFIDENCE_AMBIGUOUS)])

    results = walk(conn, ["B"], "upstream", max_depth=1)
    by_name = {r["name"]: r for r in results}

    assert "A" in by_name
    assert by_name["A"]["confidence"] == CONFIDENCE_AMBIGUOUS


# ── T9: INFERRED hop downgrades vs EXTRACTED ─────────────────────────────────


def test_inferred_hop_downgrades_from_extracted(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An INFERRED edge on the path must make the path INFERRED (not EXTRACTED)."""
    conn, src = db_conn
    # Graph: A -[INFERRED]-> B
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src, confidence=CONFIDENCE_INFERRED)])

    results = walk(conn, ["B"], "upstream", max_depth=1)
    by_name = {r["name"]: r for r in results}

    assert "A" in by_name
    assert by_name["A"]["confidence"] == CONFIDENCE_INFERRED


# ── T10: all-EXTRACTED path stays EXTRACTED ───────────────────────────────────


def test_all_extracted_path_stays_extracted(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """A path with only EXTRACTED edges must report EXTRACTED confidence."""
    conn, src = db_conn
    # Graph: C -[EXTRACTED]-> B -[EXTRACTED]-> A
    _seed(
        conn,
        src,
        ["A", "B", "C"],
        [
            _edge("B", "A", src, confidence=CONFIDENCE_EXTRACTED),
            _edge("C", "B", src, confidence=CONFIDENCE_EXTRACTED),
        ],
    )

    results = walk(conn, ["A"], "upstream", max_depth=2)
    by_name = {r["name"]: r for r in results}

    assert "B" in by_name and by_name["B"]["confidence"] == CONFIDENCE_EXTRACTED
    assert "C" in by_name and by_name["C"]["confidence"] == CONFIDENCE_EXTRACTED


# ── T11: multiple paths — strongest confidence wins ───────────────────────────


def test_multiple_paths_strongest_confidence_wins(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """When two paths reach the same symbol at the same distance, the strongest confidence wins."""
    conn, src = db_conn
    # Two seeds (D and E) both point at C at d=1.
    # D -> C: EXTRACTED, E -> C: AMBIGUOUS
    # C should be reported with EXTRACTED (strongest).
    _seed(
        conn,
        src,
        ["C", "D", "E"],
        [
            _edge("D", "C", src, confidence=CONFIDENCE_EXTRACTED),
            _edge("E", "C", src, confidence=CONFIDENCE_AMBIGUOUS),
        ],
    )

    # Walk upstream of C from seeds [C].
    # D and E both call C, so upstream of C returns D (EXTRACTED) and E (AMBIGUOUS).
    results_from_c = walk(conn, ["C"], "upstream", max_depth=1)
    by_name = {r["name"]: r for r in results_from_c}

    assert "D" in by_name and by_name["D"]["confidence"] == CONFIDENCE_EXTRACTED
    assert "E" in by_name and by_name["E"]["confidence"] == CONFIDENCE_AMBIGUOUS

    # Now test multi-seed: seeds [D, E], walk downstream to C.
    # From D: C at d=1 with EXTRACTED; from E: C at d=1 with AMBIGUOUS.
    # Both paths arrive at C at distance 1 — keep EXTRACTED (strongest).
    results_downstream = walk(conn, ["D", "E"], "downstream", max_depth=1)
    by_name2 = {r["name"]: r for r in results_downstream}

    assert "C" in by_name2
    assert by_name2["C"]["confidence"] == CONFIDENCE_EXTRACTED


# ── T12: self-edges are skipped ───────────────────────────────────────────────


def test_self_edges_are_skipped(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Edges where source == target must not cause a symbol to reach itself."""
    conn, src = db_conn
    # A -> A (self-edge)
    _seed(conn, src, ["A"], [_edge("A", "A", src)])

    results = walk(conn, ["A"], "upstream", max_depth=3)
    # A is both seed and the only reachable via self-edge; should be excluded.
    assert results == []


# ── T13: seeds not in output ─────────────────────────────────────────────────


def test_seeds_not_in_output(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Seeds must never appear in the walk output."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B", "C"], [_edge("A", "B", src), _edge("B", "C", src)])

    results = walk(conn, ["A", "B"], "downstream", max_depth=3)
    output_names = {r["name"] for r in results}

    assert "A" not in output_names
    assert "B" not in output_names
    # C is reachable from B -> should be in output
    assert "C" in output_names


# ── T14: distance is 1-based ─────────────────────────────────────────────────


def test_distance_is_one_based(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Direct neighbors of seeds must have distance=1 (not 0)."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    results = walk(conn, ["B"], "upstream", max_depth=1)
    assert len(results) == 1
    assert results[0]["name"] == "A"
    assert results[0]["distance"] == 1


# ── T15: >1000 callers don't crash SQLite ────────────────────────────────────


def test_large_frontier_does_not_raise(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """walk() with >1000 upstream callers must not raise OperationalError.

    SQLite's SQLITE_MAX_VARIABLE_NUMBER is 999 on Linux/CI. This test inserts
    1100 callers for a single target and verifies that all are returned without
    raising an OperationalError (the bug that occurs when the entire frontier is
    passed to a single IN-clause with >999 bound parameters).
    """
    conn, src = db_conn
    caller_count = 1100
    target = "hub_function"

    # Insert the target symbol so it exists in the symbols table.
    _seed(conn, src, [target], [])

    # Insert 1100 caller symbols + edges directly via the raw connection to avoid
    # duplicating db.py test infrastructure; we insert into symbols + edges tables.
    # Each caller_N calls hub_function (upstream edges).
    for i in range(caller_count):
        caller_name = f"caller_{i}"
        conn.execute(
            "INSERT OR IGNORE INTO symbols (name, kind, file_id, start_line, end_line, docstring)"
            " SELECT ?, 'function', id, 1, 2, NULL FROM files WHERE path = ? LIMIT 1",
            (caller_name, src),
        )
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " SELECT ?, ?, 'call', id, 1, 'EXTRACTED' FROM files WHERE path = ? LIMIT 1",
            (caller_name, target, src),
        )
    conn.commit()

    # This must NOT raise sqlite3.OperationalError: "too many SQL variables"
    results = walk(conn, [target], "upstream", max_depth=1)

    caller_names = {r["name"] for r in results}
    # All 1100 callers must be present.
    assert len(results) == caller_count, f"Expected {caller_count} results, got {len(results)}"
    for i in range(caller_count):
        assert f"caller_{i}" in caller_names
