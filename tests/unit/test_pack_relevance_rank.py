"""Integration tests for E3 — RWR relevance ranking of context_pack neighbors.

context_pack ranks neighbors by personalized-PageRank relevance to the seed BEFORE the per-file
+ global caps (SEAM_PACK_RELEVANCE_RANK="on"), so the kept N are the most relevant rather than
the lowest-symbol-id ones. These tests exercise the BEHAVIOR through context_pack():
  - the harm case: a relevant caller with a HIGH symbol id (would be dropped by the min_id cap)
    survives the cap once relevance ranking is on;
  - equal-relevance ties favor production over test;
  - knob "off" restores the prior min_id order (byte-identical revert);
  - the ranking path never raises (degrades to min_id order on failure).
"""

from pathlib import Path

import pytest

import seam.config as config
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.query.pack import context_pack


def _sym(name: str, file: str, kind: str = "function") -> Symbol:
    return Symbol(
        name=name, kind=kind, file=file,
        start_line=1, end_line=6,
        docstring=None, signature=None, decorators=[],
        is_exported=None, visibility=None, qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence="INFERRED")


@pytest.fixture(autouse=True)
def _wide_per_file_cap(monkeypatch: pytest.MonkeyPatch):
    """Neutralize the per-file cap so tests isolate the GLOBAL-limit ranking behavior."""
    monkeypatch.setattr(config, "SEAM_PACK_PER_FILE_CAP", 100)
    # Ensure ranking is on by default for these tests (some toggle it off explicitly).
    monkeypatch.setattr(config, "SEAM_PACK_RELEVANCE_RANK", "on")


def _central_db(tmp_path: Path):
    """hub called by p1,p2,p3 (peripheral leaves) and zzz_central (woven into hub's neighborhood
    via shared callees x,y). zzz_central is inserted LAST → highest symbol id → the min_id cap
    drops it; RWR must rescue it because it is the most relevant caller."""
    conn = init_db(tmp_path / "central.db")
    f = tmp_path / "a.py"
    f.write_text("# fixture\n")
    symbols = [
        _sym("hub", str(f)),
        _sym("p1", str(f)), _sym("p2", str(f)), _sym("p3", str(f)),
        _sym("x", str(f)), _sym("y", str(f)),
        _sym("zzz_central", str(f)),   # last → highest id, last alphabetically
    ]
    edges = [
        _edge("p1", "hub", str(f)), _edge("p2", "hub", str(f)), _edge("p3", "hub", str(f)),
        _edge("zzz_central", "hub", str(f)),     # caller of hub
        _edge("hub", "x", str(f)), _edge("hub", "y", str(f)),     # hub's callees
        _edge("zzz_central", "x", str(f)), _edge("zzz_central", "y", str(f)),  # shared → central
    ]
    upsert_file(conn, f, "python", "h1", symbols, edges)
    return conn


def test_relevant_high_id_caller_survives_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """RWR keeps the most-relevant caller (zzz_central) even though its high id / late name would
    drop it under the min_id cap."""
    monkeypatch.setattr(config, "SEAM_PACK_NEIGHBOR_LIMIT", 2)
    conn = _central_db(tmp_path)
    try:
        pack = context_pack(conn, "hub")
    finally:
        conn.close()
    assert pack is not None
    caller_names = [c["name"] for c in pack["callers"]]
    assert "zzz_central" in caller_names, f"RWR should keep the central caller; got {caller_names}"


def test_knob_off_drops_high_id_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """With ranking off, the cap keeps the min_id-first callers → the high-id central caller is
    dropped (the pre-E3 behavior; byte-identical revert)."""
    monkeypatch.setattr(config, "SEAM_PACK_NEIGHBOR_LIMIT", 2)
    monkeypatch.setattr(config, "SEAM_PACK_RELEVANCE_RANK", "off")
    conn = _central_db(tmp_path)
    try:
        pack = context_pack(conn, "hub")
    finally:
        conn.close()
    assert pack is not None
    caller_names = [c["name"] for c in pack["callers"]]
    # min_id order among callers = [p1, p2, p3, zzz_central]; cap 2 keeps [p1, p2].
    assert caller_names == ["p1", "p2"], caller_names
    assert "zzz_central" not in caller_names


def test_production_before_test_on_equal_relevance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two equally-relevant leaf callers (only call the hub) → production ranks before test."""
    monkeypatch.setattr(config, "SEAM_PACK_NEIGHBOR_LIMIT", 1)
    conn = init_db(tmp_path / "tie.db")
    prod = tmp_path / "svc.py"
    prod.write_text("# prod\n")
    test = tmp_path / "tests" / "test_svc.py"
    test.parent.mkdir()
    test.write_text("# test\n")
    upsert_file(conn, prod, "python", "h1",
                [_sym("hub2", str(prod)), _sym("prod_c", str(prod))],
                [_edge("prod_c", "hub2", str(prod))])
    upsert_file(conn, test, "python", "h2",
                [_sym("test_c", str(test))],
                [_edge("test_c", "hub2", str(test))])
    try:
        pack = context_pack(conn, "hub2")
    finally:
        conn.close()
    assert pack is not None
    caller_names = [c["name"] for c in pack["callers"]]
    assert caller_names == ["prod_c"], caller_names  # production survives the cap of 1


def test_ranking_never_raises_on_subgraph_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If the subgraph fetch blows up, ranking degrades silently — context_pack still returns."""
    import seam.query.pack as pack_mod

    def _boom(*a, **k):
        raise RuntimeError("simulated DB failure in subgraph fetch")

    monkeypatch.setattr(pack_mod, "_fetch_local_subgraph", _boom)
    conn = _central_db(tmp_path)
    try:
        pack = context_pack(conn, "hub")
    finally:
        conn.close()
    assert pack is not None  # did not raise
    # Falls back to min_id order (no scores) — callers still present.
    assert {c["name"] for c in pack["callers"]} <= {"p1", "p2", "p3", "zzz_central"}
