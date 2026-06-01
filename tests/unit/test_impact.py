"""Unit tests for seam/analysis/impact.py.

Tests build a hand-crafted fixture graph via the db write path (upsert_file),
then assert on the public interface: impact(conn, target, direction, max_depth).

Coverage:
  T1  upstream direction returns callers in correct tiers
  T2  downstream direction returns callees in correct tiers
  T3  direction=both returns upstream + downstream keys
  T4  d=1 maps to WILL_BREAK, d=2 to LIKELY_AFFECTED, d=3+ to MAY_NEED_TESTING
  T5  path confidence propagates (AMBIGUOUS hop -> tier entry is AMBIGUOUS)
  T6  max_depth cap respected (no results beyond cap)
  T7  cycle terminates
  T8  unknown symbol -> found=False, empty ImpactResult (not error)
  T9  clamp_depth clamps to [1, 10]
  T10 invalid direction raises ValueError
  T11 TierGroup always has all three tier keys even if empty
  T12 tier entry has correct tier field
  T13 found=True for known symbol (with dependents)
  T14 found=True for known symbol (without dependents / isolated)
  T15 TieredEntry includes file field — absolute path for indexed symbols
  T16 TieredEntry file=None for non-indexed symbol names
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.impact import (
    TIER_LIKELY_AFFECTED,
    TIER_MAY_NEED_TESTING,
    TIER_WILL_BREAK,
    TierGroup,
    clamp_depth,
    impact,
)
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


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_EXTRACTED) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


@pytest.fixture()
def db_conn() -> tuple[sqlite3.Connection, str]:
    """Create a temporary, initialized SQLite DB."""
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


def _all_entries(tier_group: TierGroup) -> list[dict]:  # type: ignore[type-arg]
    """Flatten all tier entries from a TierGroup into a single list."""
    return [entry for entries in tier_group.values() for entry in entries]


# ── T1: upstream direction ────────────────────────────────────────────────────


def test_upstream_returns_callers(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Upstream impact: symbols that depend on the target should appear."""
    conn, src = db_conn
    # A -> B (A calls B). Upstream of B = A.
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    result = impact(conn, "B", direction="upstream", max_depth=3)

    assert "upstream" in result
    entries = _all_entries(result["upstream"])
    names = [e["name"] for e in entries]
    assert "A" in names
    assert "B" not in names  # target never in its own impact


# ── T2: downstream direction ──────────────────────────────────────────────────


def test_downstream_returns_callees(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Downstream impact: symbols that the target depends on should appear."""
    conn, src = db_conn
    # A -> B. Downstream of A = B.
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    result = impact(conn, "A", direction="downstream", max_depth=3)

    assert "downstream" in result
    assert "upstream" not in result
    entries = _all_entries(result["downstream"])
    names = [e["name"] for e in entries]
    assert "B" in names
    assert "A" not in names


# ── T3: direction=both ────────────────────────────────────────────────────────


def test_both_direction_returns_two_keys(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """direction='both' must return both 'upstream' and 'downstream' keys."""
    conn, src = db_conn
    # A -> B -> C. Impact of B: upstream=A (caller), downstream=C (callee).
    _seed(conn, src, ["A", "B", "C"], [_edge("A", "B", src), _edge("B", "C", src)])

    result = impact(conn, "B", direction="both", max_depth=3)

    assert "upstream" in result
    assert "downstream" in result

    upstream_names = [e["name"] for e in _all_entries(result["upstream"])]
    downstream_names = [e["name"] for e in _all_entries(result["downstream"])]
    assert "A" in upstream_names
    assert "C" in downstream_names


# ── T4: tier bucketing by distance ───────────────────────────────────────────


def test_tier_bucketing(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """d=1 -> WILL_BREAK, d=2 -> LIKELY_AFFECTED, d=3 -> MAY_NEED_TESTING."""
    conn, src = db_conn
    # Chain: D -> C -> B -> A (upstream of A: B=d1, C=d2, D=d3)
    _seed(
        conn,
        src,
        ["A", "B", "C", "D"],
        [
            _edge("B", "A", src),
            _edge("C", "B", src),
            _edge("D", "C", src),
        ],
    )

    result = impact(conn, "A", direction="upstream", max_depth=3)
    tg = result["upstream"]

    will_break_names = [e["name"] for e in tg[TIER_WILL_BREAK]]
    likely_names = [e["name"] for e in tg[TIER_LIKELY_AFFECTED]]
    may_test_names = [e["name"] for e in tg[TIER_MAY_NEED_TESTING]]

    assert "B" in will_break_names
    assert "C" in likely_names
    assert "D" in may_test_names


# ── T5: path confidence in entries ────────────────────────────────────────────


def test_ambiguous_hop_in_tier_entry(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An edge to a name in multiple files must produce a tier entry with confidence=AMBIGUOUS.

    Phase 1b: confidence resolved from whole index based on edge target_name (B).
    To get AMBIGUOUS, B must appear in more than one file (count=2).
    """
    import tempfile

    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src)])

    # Insert a second 'B' in another file so count(B)=2 → AMBIGUOUS.
    with tempfile.TemporaryDirectory() as other_tmp:
        from pathlib import Path as _Path

        from seam.indexer.db import upsert_file as _upsert
        from seam.indexer.graph import Symbol as _Sym

        other_file = _Path(other_tmp) / "other.py"
        other_file.write_text("# other\n")
        _upsert(conn, other_file, "python", "h_other",
                [_Sym(name="B", kind="function", file=str(other_file), start_line=1, end_line=2, docstring=None)], [])

        result = impact(conn, "B", direction="upstream", max_depth=1)
        entries = _all_entries(result["upstream"])
        a_entry = next((e for e in entries if e["name"] == "A"), None)

        assert a_entry is not None
        assert a_entry["confidence"] == CONFIDENCE_AMBIGUOUS, (
            f"B in 2 files → AMBIGUOUS, got {a_entry['confidence']!r}"
        )


def test_inferred_hop_in_tier_entry(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An edge to an unindexed target must produce a tier entry with confidence=INFERRED.

    Phase 1b: confidence resolved from whole index. To get INFERRED, B must not
    be in the symbols table at all (count=0). Insert A and edge A→B directly.
    """
    conn, src = db_conn
    _seed(conn, src, ["A"], [])  # only A in symbols; no B symbol
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
        " SELECT 'A', 'B', 'call', id, 1, 'INFERRED' FROM files WHERE path = ? LIMIT 1",
        (src,),
    )
    conn.commit()

    result = impact(conn, "B", direction="upstream", max_depth=1)
    entries = _all_entries(result["upstream"])
    a_entry = next((e for e in entries if e["name"] == "A"), None)

    assert a_entry is not None
    assert a_entry["confidence"] == CONFIDENCE_INFERRED, (
        f"B not in symbols (count=0) → INFERRED, got {a_entry['confidence']!r}"
    )


# ── T6: max_depth cap ─────────────────────────────────────────────────────────


def test_max_depth_cap_respected(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Symbols beyond max_depth must not appear in results."""
    conn, src = db_conn
    # D -> C -> B -> A (D is at d=3 from A).
    _seed(
        conn,
        src,
        ["A", "B", "C", "D"],
        [_edge("B", "A", src), _edge("C", "B", src), _edge("D", "C", src)],
    )

    # max_depth=2: only B (d=1) and C (d=2) should appear, not D (d=3).
    result = impact(conn, "A", direction="upstream", max_depth=2)
    names = [e["name"] for e in _all_entries(result["upstream"])]

    assert "B" in names
    assert "C" in names
    assert "D" not in names


# ── T7: cycle terminates ──────────────────────────────────────────────────────


def test_cycle_terminates(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """A cyclic graph must not cause infinite traversal."""
    conn, src = db_conn
    # A -> B -> A (cycle)
    _seed(conn, src, ["A", "B"], [_edge("A", "B", src), _edge("B", "A", src)])

    result = impact(conn, "A", direction="upstream", max_depth=10)
    # Should return finite results
    entries = _all_entries(result["upstream"])
    assert isinstance(entries, list)
    assert len(entries) < 20  # definitely not infinite


# ── T8: unknown symbol -> found=False, empty result ──────────────────────────


def test_unknown_symbol_returns_empty_result(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """An unknown symbol must return found=False and empty tiers (not an error)."""
    conn, _ = db_conn
    result = impact(conn, "totally_unknown_xyz", direction="upstream", max_depth=3)

    assert isinstance(result, dict)
    # found=False distinguishes "not in index" from "found but isolated"
    assert result["found"] is False
    assert result["target"] == "totally_unknown_xyz"
    assert "upstream" in result
    # All tiers empty
    total = sum(len(v) for v in result["upstream"].values())
    assert total == 0


# ── T9: clamp_depth ──────────────────────────────────────────────────────────


def test_clamp_depth_min() -> None:
    """clamp_depth(0) must return 1."""
    assert clamp_depth(0) == 1


def test_clamp_depth_max() -> None:
    """clamp_depth(100) must return 10."""
    assert clamp_depth(100) == 10


def test_clamp_depth_in_range() -> None:
    """clamp_depth(5) must return 5 unchanged."""
    assert clamp_depth(5) == 5


# ── T10: invalid direction raises ValueError ──────────────────────────────────


def test_invalid_direction_raises() -> None:
    """impact() must raise ValueError for an unrecognized direction."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    with pytest.raises(ValueError, match="direction"):
        impact(conn, "some_symbol", direction="sideways", max_depth=3)

    conn.close()


# ── T11: TierGroup always has all three keys ──────────────────────────────────


def test_tier_group_always_has_all_keys(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """TierGroup must always contain all three tier keys, even if some tiers are empty."""
    conn, src = db_conn
    # Only one edge: B -> A (only WILL_BREAK, others empty).
    _seed(conn, src, ["A", "B"], [_edge("B", "A", src)])

    result = impact(conn, "A", direction="upstream", max_depth=3)
    tg = result["upstream"]

    # All three keys must be present.
    assert TIER_WILL_BREAK in tg
    assert TIER_LIKELY_AFFECTED in tg
    assert TIER_MAY_NEED_TESTING in tg

    # WILL_BREAK has B.
    assert any(e["name"] == "B" for e in tg[TIER_WILL_BREAK])
    # The others are empty lists (not missing, not None).
    assert tg[TIER_LIKELY_AFFECTED] == []
    assert tg[TIER_MAY_NEED_TESTING] == []


# ── T12: tier entry has correct tier field ────────────────────────────────────


def test_tier_entry_has_tier_field(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """Each TieredEntry must include the 'tier' field matching the bucket it's in."""
    conn, src = db_conn
    # B -> A (d=1 => WILL_BREAK)
    _seed(conn, src, ["A", "B"], [_edge("B", "A", src)])

    result = impact(conn, "A", direction="upstream", max_depth=3)
    for entry in result["upstream"][TIER_WILL_BREAK]:
        assert entry["tier"] == TIER_WILL_BREAK
        assert "name" in entry
        assert "distance" in entry
        assert "confidence" in entry


# ── T13: found=True for known symbol with dependents ─────────────────────────


def test_found_true_for_known_symbol_with_dependents(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """found=True must be set when the target is an indexed symbol with dependents."""
    conn, src = db_conn
    _seed(conn, src, ["A", "B"], [_edge("B", "A", src)])

    result = impact(conn, "A", direction="upstream", max_depth=1)

    assert result["found"] is True
    assert result["target"] == "A"


# ── T14: found=True for known symbol with no dependents ──────────────────────


def test_found_true_for_isolated_symbol(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """found=True even when target is indexed but has zero dependents (isolated node)."""
    conn, src = db_conn
    # Insert A with no edges — isolated symbol.
    _seed(conn, src, ["A"], [])

    result = impact(conn, "A", direction="upstream", max_depth=3)

    assert result["found"] is True
    assert result["target"] == "A"
    total = sum(len(v) for v in result["upstream"].values())
    assert total == 0


# ── T15: TieredEntry includes file for indexed symbols ────────────────────────


def test_tier_entry_has_file_for_indexed_symbol(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """TieredEntry must include the absolute file path for indexed symbols."""
    conn, src = db_conn
    # B -> A; B is an indexed symbol so its file should be the src path.
    _seed(conn, src, ["A", "B"], [_edge("B", "A", src)])

    result = impact(conn, "A", direction="upstream", max_depth=1)
    entries = result["upstream"][TIER_WILL_BREAK]
    b_entry = next((e for e in entries if e["name"] == "B"), None)

    assert b_entry is not None
    assert "file" in b_entry
    # B is an indexed symbol; its file must be the absolute src path.
    assert b_entry["file"] == src


# ── T16: TieredEntry file=None for non-indexed symbol names ──────────────────


def test_tier_entry_file_none_for_non_indexed_name(db_conn: tuple[sqlite3.Connection, str]) -> None:
    """TieredEntry file must be None when the name is not an indexed symbol."""
    conn, src = db_conn
    # Insert an edge where the source "external_lib_fn" is not in the symbols table.
    # We insert the edge directly (bypassing upsert_file) so target "A" is indexed
    # but source "external_lib_fn" is not a symbol row.
    _seed(conn, src, ["A"], [])  # Only A is an indexed symbol.
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
        " SELECT 'external_lib_fn', 'A', 'call', id, 1, 'INFERRED' FROM files WHERE path = ? LIMIT 1",
        (src,),
    )
    conn.commit()

    result = impact(conn, "A", direction="upstream", max_depth=1)
    entries = result["upstream"][TIER_WILL_BREAK]
    ext_entry = next((e for e in entries if e["name"] == "external_lib_fn"), None)

    assert ext_entry is not None
    # external_lib_fn is not an indexed symbol, so file must be None.
    assert ext_entry["file"] is None
