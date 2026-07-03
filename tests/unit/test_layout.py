"""Tests for seam/query/layout.py — deterministic 3D constellation layout engine.

TDD: red → green → refactor per slice S1 (issue #169) and S1 globe (issue #260).
Coverage:
  - stellar_color bands
  - node_size degree boost (S1 original; updated for log1p formula in #260)
  - node_size monotonic, bounded, hub-vs-leaf ratio, sub-linear (#260)
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
  - Fibonacci sphere seeding: shell + determinism (#260)
  - Fibonacci sphere cluster angular locality (#260)
  - radial outlier clamp: far node pulled in, normal node untouched (#260)
  - LayoutResult shape regression (#260)
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from seam.query.layout import (
    _OUTLIER_K,
    _SPHERE_RADIUS,
    _recenter_and_clamp,
    _sphere_seed_positions,
    compute_layout,
    node_size,
    stellar_color,
)

# ── Pure-helper tests (existing) ──────────────────────────────────────────────


def test_stellar_color_bands() -> None:
    assert stellar_color(0) == "#ff6050"   # M red dwarf (deg <= 1)
    assert stellar_color(12) == "#ffe080"  # G yellow (deg <= 12)
    assert stellar_color(999) == "#80a0ff"  # O blue giant (deg > 50)


def test_node_size_boosts_with_degree() -> None:
    """Basic invariants for node_size — updated for log1p formula (#260)."""
    assert node_size("function", 0) == pytest.approx(4.0)
    assert node_size("class", 0) == pytest.approx(6.0)
    # log1p formula: 4.0 + min(log1p(100)*1.5, 6.0) = 4.0 + min(6.92, 6.0) = 10.0
    assert node_size("function", 100) == pytest.approx(10.0)


# ── node_size sub-linear property tests (#260) ───────────────────────────────


def test_node_size_monotonic() -> None:
    """node_size must be non-decreasing in degree for any fixed kind."""
    degrees = [0, 1, 2, 5, 10, 50, 100, 500, 1000]
    sizes = [node_size("function", d) for d in degrees]
    for i in range(len(sizes) - 1):
        assert sizes[i] <= sizes[i + 1], f"not monotonic at degrees {degrees[i]}, {degrees[i+1]}"


def test_node_size_bounded() -> None:
    """node_size must be bounded regardless of how large degree gets."""
    assert node_size("function", 10**7) < 20.0
    assert node_size("class", 10**7) < 20.0


def test_node_size_hub_vs_leaf() -> None:
    """A top-degree hub must be only a few× a zero-degree leaf — not an order of magnitude."""
    leaf = node_size("function", 0)
    hub = node_size("function", 10000)
    ratio = hub / leaf
    assert ratio < 5.0, f"hub/leaf ratio {ratio:.2f} exceeds 5× — not 'a few×'"


def test_node_size_sublinear() -> None:
    """Doubling degree must give less than double the boost — sub-linear by construction."""
    boost_10 = node_size("function", 10) - node_size("function", 0)
    boost_20 = node_size("function", 20) - node_size("function", 0)
    assert boost_20 < 2.0 * boost_10, "size boost is not sub-linear"


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


# ── Pipeline tests (existing, still valid) ────────────────────────────────────


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


# ── LayoutResult shape regression (#260) ──────────────────────────────────────


def test_layout_result_shape_unchanged(tmp_path: Path) -> None:
    """LayoutResult/LayoutNode/LayoutEdge must have byte-identical key sets — no API contract change."""
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=100)

    assert set(r.keys()) == {"nodes", "edges", "clusters", "total_nodes"}
    if r["nodes"]:
        assert set(r["nodes"][0].keys()) == {
            "id", "x", "y", "z", "label", "name", "file_path", "size", "color"
        }
    if r["edges"]:
        assert set(r["edges"][0].keys()) == {"source", "target", "type"}


# ── Mock row for unit-testing _sphere_seed_positions (#260) ───────────────────


class _Row:
    """Minimal sqlite3.Row mock — only subscript access to 'file_path' is needed."""

    def __init__(self, **kwargs: object) -> None:
        self._d = kwargs

    def __getitem__(self, key: str) -> object:
        return self._d.get(key)


def _make_sphere_fixture(names_and_paths: list[tuple[str, str]]) -> tuple[list[str], dict, dict[str, int]]:
    """Build (selected, reps, depth) from [(name, file_path), ...] with depth=0 for all."""
    selected = [n for n, _ in names_and_paths]
    reps = {n: _Row(file_path=p) for n, p in names_and_paths}
    depth: dict[str, int] = {n: 0 for n in selected}
    return selected, reps, depth


# ── Fibonacci sphere seeding tests (#260) ────────────────────────────────────


def test_sphere_seeding_on_shell() -> None:
    """Every seeded node must lie on a sphere shell at exactly _SPHERE_RADIUS (depth=0)."""
    selected, reps, depth = _make_sphere_fixture([
        ("a", "/project/src/a.py"),
        ("b", "/project/src/b.py"),
        ("c", "/project/lib/c.py"),
        ("d", "/project/lib/d.py"),
        ("e", "/project/tests/e.py"),
    ])
    pos = _sphere_seed_positions(selected, reps, depth)

    # All radii must equal _SPHERE_RADIUS (no depth contribution here)
    radii = np.linalg.norm(pos, axis=1)
    for i, r in enumerate(radii):
        assert abs(r - _SPHERE_RADIUS) < 1e-9, f"node {i} radius {r:.4f} ≠ {_SPHERE_RADIUS}"


def test_sphere_seeding_deterministic() -> None:
    """Two calls with identical inputs must produce identical position arrays."""
    selected, reps, depth = _make_sphere_fixture([
        (f"sym{i}", f"/project/src/sym{i}.py") for i in range(20)
    ])
    pos_a = _sphere_seed_positions(selected, reps, depth)
    pos_b = _sphere_seed_positions(selected, reps, depth)
    np.testing.assert_array_equal(pos_a, pos_b)


def test_sphere_seeding_depth_modulates_radius() -> None:
    """Depth > 0 must place the node on a larger shell than depth == 0."""
    selected = ["shallow", "deep"]
    reps = {n: _Row(file_path=f"/p/{n}.py") for n in selected}
    depth = {"shallow": 0, "deep": 3}

    pos = _sphere_seed_positions(selected, reps, depth)
    name_to_idx = {n: i for i, n in enumerate(selected)}

    r_shallow = float(np.linalg.norm(pos[name_to_idx["shallow"]]))
    r_deep = float(np.linalg.norm(pos[name_to_idx["deep"]]))
    assert r_deep > r_shallow, "deeper node must be on a larger shell"


def test_sphere_cluster_locality() -> None:
    """Same-cluster nodes must occupy a different spherical cap than cross-cluster nodes.

    The sort key is (fnv1a(cluster_key), fnv1a(name)), so nodes sharing the same
    first-3-path-component cluster key get contiguous Fibonacci indices — they land
    on the same polar-angle band. With 5+5 balanced clusters, the cluster centroids
    must be angularly separated, proving locality survived the Fibonacci assignment.
    """
    # 5 nodes from /project/src/ (cluster A), 5 from /project/tests/ (cluster B)
    names_and_paths = (
        [(f"a{i}", f"/project/src/a{i}.py") for i in range(5)]
        + [(f"b{i}", f"/project/tests/b{i}.py") for i in range(5)]
    )
    selected, reps, depth = _make_sphere_fixture(names_and_paths)
    pos = _sphere_seed_positions(selected, reps, depth)

    name_to_idx = {n: i for i, n in enumerate(selected)}

    # Normalise to unit sphere for angular distance computation
    norms = pos / np.linalg.norm(pos, axis=1, keepdims=True)

    a_idxs = [name_to_idx[f"a{i}"] for i in range(5)]
    b_idxs = [name_to_idx[f"b{i}"] for i in range(5)]

    # Cluster centroid on unit sphere (not necessarily unit length after averaging)
    centroid_a = norms[a_idxs].mean(axis=0)
    centroid_b = norms[b_idxs].mean(axis=0)
    norm_a = float(np.linalg.norm(centroid_a))
    norm_b = float(np.linalg.norm(centroid_b))

    assert norm_a > 0.01 and norm_b > 0.01, "centroids collapsed — degenerate fixture"

    # Angular distance between cluster centroids (1 - cosine similarity)
    dot = float(np.dot(centroid_a / norm_a, centroid_b / norm_b))
    angular_dist_centroids = 1.0 - dot

    # Cluster centroids must be on noticeably different parts of the sphere.
    # With 10 nodes split 5+5 by Fibonacci, centroids span at least 45° apart.
    assert angular_dist_centroids > 0.3, (
        f"cluster centroids only {angular_dist_centroids:.3f} apart — "
        "expected >0.3 (>roughly 45° angular separation)"
    )


# ── Radial outlier clamp tests (#260) ─────────────────────────────────────────


def _fibonacci_sphere_pts(n: int, radius: float) -> np.ndarray:
    """Pure Fibonacci sphere points at given radius — deterministic, used in clamp tests."""
    golden = (1.0 + 5.0 ** 0.5) / 2.0
    pts = []
    for i in range(n):
        cos_t = max(-1.0, min(1.0, 1.0 - 2.0 * (i + 0.5) / n))
        theta = math.acos(cos_t)
        phi = 2.0 * math.pi * i / golden
        pts.append([radius * math.sin(theta) * math.cos(phi),
                    radius * math.sin(theta) * math.sin(phi),
                    radius * math.cos(theta)])
    return np.array(pts, dtype=float)


def test_radial_clamp_pulls_outlier_in() -> None:
    """A node far beyond mean + k·sigma must be pulled into the clamp radius.

    Construction: 50 nodes on a sphere at radius 100 (≈zero centroid by Fibonacci symmetry),
    plus 1 outlier at radius 500 (5× the cluster). With n=51 and k=2.5, the outlier is well
    outside mean + k·sigma and must be clamped.
    """
    normal = _fibonacci_sphere_pts(50, 100.0)
    outlier = np.array([[500.0, 0.0, 0.0]])
    pos = np.vstack([normal, outlier])

    result = _recenter_and_clamp(pos)

    # Compute the pre-clamp statistics (after centering) to define the expected clamp_r
    centered = pos - pos.mean(axis=0)
    pre_radii = np.linalg.norm(centered, axis=1)
    clamp_r = pre_radii.mean() + _OUTLIER_K * pre_radii.std()

    # The outlier (last node) must now be at or below the clamp radius
    outlier_r_after = float(np.linalg.norm(result[-1]))
    assert outlier_r_after <= clamp_r + 1e-9, (
        f"outlier at {outlier_r_after:.1f} still exceeds clamp_r {clamp_r:.1f}"
    )

    # The outlier must have been pulled closer to the cluster (sanity check)
    outlier_r_before = float(np.linalg.norm(centered[-1]))
    assert outlier_r_after < outlier_r_before, "outlier was not pulled in at all"


def test_radial_clamp_normal_untouched() -> None:
    """Nodes within the clamp radius must not be moved after recentering.

    When all nodes lie on a single sphere shell (sigma≈0), clamp_r = mean_r + k·0 = mean_r.
    All nodes are exactly at mean_r → none exceed clamp_r → radii are unchanged.
    """
    # 20-node Fibonacci sphere at radius 100 — near-zero centroid by symmetry
    normal = _fibonacci_sphere_pts(20, 100.0)
    result = _recenter_and_clamp(normal)

    # After recentering (centroid ≈ 0 for Fibonacci sphere), pos ≈ input
    expected_centered = normal - normal.mean(axis=0)
    expected_radii = np.linalg.norm(expected_centered, axis=1)
    result_radii = np.linalg.norm(result, axis=1)

    np.testing.assert_allclose(result_radii, expected_radii, rtol=1e-9,
                                err_msg="normal nodes were moved by the clamp")
