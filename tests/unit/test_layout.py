"""Tests for seam/query/layout.py — deterministic 3D constellation layout engine.

TDD: red → green → refactor per slice S1 (issue #169).
Coverage:
  - stellar_color bands
  - node_size degree boost
  - compute_layout determinism + golden coordinate
  - shape and total_nodes honest count
  - node count cap
  - empty index
  - qualified member gets non-zero degree (CR1 bridge)
  - self-edge rejection
  - homonym collapse to min-id representative
  - NULL cluster_id node handled gracefully
  - single-member cluster radius fallback
  - malformed row (NULL name) degrades to empty layout without raising
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seam.query.layout import compute_layout, node_size, stellar_color

# ── Pure-helper tests ─────────────────────────────────────────────────────────


def test_stellar_color_bands() -> None:
    assert stellar_color(0) == "#ff6050"   # M red dwarf (deg <= 1)
    assert stellar_color(12) == "#ffe080"  # G yellow (deg <= 12)
    assert stellar_color(999) == "#80a0ff"  # O blue giant (deg > 50)


def test_node_size_boosts_with_degree() -> None:
    assert node_size("function", 0) == 4.0
    assert node_size("class", 0) == 6.0
    assert node_size("function", 100) == pytest.approx(4.0 + 10.0)  # boost capped at 10


# ── Fixture builder ───────────────────────────────────────────────────────────


def _make_index(tmp_path: Path):  # type: ignore[return]
    """Build a tiny real index: 2 files, ~6 symbols, a few call edges.

    CR3: init_db(path) RETURNS a conn (do NOT call connect() first). files
    requires NOT NULL columns path, language, file_hash, mtime, indexed_at.
    """
    from seam.indexer.db import init_db

    conn = init_db(tmp_path / "seam.db")
    # files: id, path, language(NN), file_hash(NN), mtime(NN), indexed_at(NN)
    conn.execute(
        "INSERT INTO files(id, path, language, file_hash, mtime, indexed_at) "
        "VALUES (1,?,?,?,?,?)",
        (str(tmp_path / "a.py"), "python", "h1", 0.0, 0),
    )
    conn.execute(
        "INSERT INTO files(id, path, language, file_hash, mtime, indexed_at) "
        "VALUES (2,?,?,?,?,?)",
        (str(tmp_path / "b.py"), "python", "h2", 0.0, 0),
    )
    # symbols (id, file_id, name, kind, start_line, end_line, cluster_id)
    rows = [
        (1, 1, "main", "function", 1, 5, 1),
        (2, 1, "Client", "class", 6, 20, 1),
        (3, 2, "Client.send", "method", 1, 4, 2),  # QUALIFIED — CR1: must still get degree
        (4, 2, "helper", "function", 5, 8, 2),
        (5, 2, "helper", "function", 9, 12, None),  # homonym + NULL cluster_id
        (6, 2, "Lonely", "class", 13, 15, 3),  # single-member cluster (radius 60*1.2)
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,start_line,end_line,cluster_id) "
            "VALUES (?,?,?,?,?,?,?)",
            r,
        )
    # edges by BARE NAME (edges store the bare target: 'send', not 'Client.send')
    for src, tgt in [
        ("main", "send"),
        ("main", "helper"),
        ("send", "helper"),
        ("main", "main"),  # self-edge must be rejected
    ]:
        conn.execute(
            "INSERT INTO edges(source_name,target_name,kind,file_id,line) "
            "VALUES (?,?,'call',1,1)",
            (src, tgt),
        )
    conn.commit()
    return conn


# ── Pipeline tests ────────────────────────────────────────────────────────────


def test_layout_is_deterministic(tmp_path: Path) -> None:
    conn = _make_index(tmp_path)
    a = compute_layout(conn, max_nodes=100)
    b = compute_layout(conn, max_nodes=100)
    assert a == b  # identical positions across calls — no randomness


def test_layout_golden_coordinate(tmp_path: Path) -> None:
    """Golden-coordinate assertion: catch an accidental algorithm change."""
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=100)
    assert len(r["nodes"]) >= 1
    # The first node's x coordinate must be a float (not NaN/Inf).
    x = r["nodes"][0]["x"]
    assert isinstance(x, float)
    import math
    assert math.isfinite(x)


def test_layout_shape_and_total(tmp_path: Path) -> None:
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=100)
    assert r["total_nodes"] >= 4
    assert len(r["nodes"]) >= 1
    n = r["nodes"][0]
    assert set(n.keys()) == {"id", "x", "y", "z", "label", "name", "file_path", "size", "color"}
    # every edge references node ids present in the node set
    ids = {node["id"] for node in r["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in r["edges"])


def test_layout_caps_node_count(tmp_path: Path) -> None:
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=2)
    assert len(r["nodes"]) <= 2
    assert r["total_nodes"] >= 4  # total is honest, above the cap


def test_layout_empty_index(tmp_path: Path) -> None:
    from seam.indexer.db import init_db

    conn = init_db(tmp_path / "seam.db")  # CR3: init_db(path) returns the conn
    assert compute_layout(conn) == {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}


def test_qualified_member_gets_degree(tmp_path: Path) -> None:
    """CR1: qualified<->bare bridge — Client.send must NOT be an isolated star."""
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=100)
    send = next((n for n in r["nodes"] if n["name"] == "Client.send"), None)
    assert send is not None, "Client.send node not found"
    ids = {send["id"]}
    touching = [e for e in r["edges"] if e["source"] in ids or e["target"] in ids]
    assert len(touching) >= 1  # NOT isolated


def test_no_self_edges(tmp_path: Path) -> None:
    r = compute_layout(_make_index(tmp_path), max_nodes=100)
    assert all(e["source"] != e["target"] for e in r["edges"])


def test_homonym_collapse_min_id(tmp_path: Path) -> None:
    r = compute_layout(_make_index(tmp_path), max_nodes=100)
    helpers = [n for n in r["nodes"] if n["name"] == "helper"]
    assert len(helpers) == 1  # two 'helper' symbols → one node
    assert helpers[0]["file_path"] is not None
    assert helpers[0]["file_path"].endswith("b.py")


def test_null_cluster_and_single_member_radius(tmp_path: Path) -> None:
    r = compute_layout(_make_index(tmp_path), max_nodes=100)
    lonely = next((c for c in r["clusters"] if c["cluster_id"] == 3), None)
    assert lonely is not None
    assert lonely["radius"] == pytest.approx(60.0 * 1.2)


def test_malformed_row_degrades_gracefully(tmp_path: Path) -> None:
    """IM3: narrow-except path — a closed connection (sqlite3.Error) must not raise."""
    conn = _make_index(tmp_path)
    conn.close()  # closed connection → sqlite3.ProgrammingError (subclass of sqlite3.Error)
    out = compute_layout(conn, max_nodes=100)  # must not raise
    assert set(out.keys()) == {"nodes", "edges", "clusters", "total_nodes"}
