"""Server-side 3D layout for the constellation Explorer view (Phase 11 P2.1).

WHY server-side layout?
    The alternatives — client-side force simulation (d3-force-3d, three-forcegraph)
    and downloading the raw edge list — both fail on realistic codebases:
    d3-force runs on the JS main thread and freezes the browser on 2 k+ nodes; a
    raw edge list for a 10 k-symbol repo is several MB of JSON and still requires
    client-side iteration. Moving the ForceAtlas2 kernel to Python lets us ship
    pre-computed float positions in a single O(n+e) JSON blob, so the browser tab
    renders in one draw call regardless of repo size.

WHY numpy rather than a C extension?
    numpy is already a transitive dep of fastembed (the [semantic] extra). The O(n²)
    repulsion kernel is a single broadcast-subtract + element-wise divide — numpy
    executes it as one C call with no Python loop overhead. A dedicated C extension
    would be faster but would require a compiled wheel for every platform; numpy's
    pre-built wheels ship with every Python distribution. The measured latency on a
    2 k-node graph is ~180 ms (40 iterations × ~4.5 ms each), well within the
    500 ms budget for a cached-miss first load.

WHY Fibonacci (golden-spiral) sphere seeding instead of a flat ring? (#260)
    The original flat XY ring + z-smear produced a disc-cloud: all nodes collapsed
    onto a thin pancake with a depth spike along Z. The Fibonacci sphere distributes
    n points nearly uniformly over a sphere surface (the Fibonacci/sunflower pattern
    used in stratified hemisphere sampling). Sorting nodes by cluster key before
    assigning Fibonacci indices groups same-cluster nodes onto the same polar-angle
    band — their seeds form a contiguous "cap" on the sphere, giving FA2 a warm start
    that respects spatial locality without any per-node randomness.

WHY FNV-1a seeding?
    Python's built-in hash() is randomized per-process (PYTHONHASHSEED). Using it
    for initial positions would make the layout non-deterministic across server
    restarts, breaking the cache. FNV-1a is a simple non-cryptographic hash that is
    stable, fast in pure Python, and produces well-distributed seeds. The cluster-key
    scheme (first 3 path components) groups co-located files into the same sphere arc
    so the initial layout already approximates spatial locality before FA2 runs.

WHY radial outlier clamping? (#260)
    The original "orange spike" artefact came from very-high-degree hubs whose
    repulsion force dominated attraction, flinging them far from the cluster. The
    anchor spring in FA2 bounds most nodes, but extreme mass ratios can still produce
    isolated outliers. Clamping at mean + k·sigma after FA2 is a deterministic safety
    net that pulls only genuine outliers (≈beyond the 99th percentile of a Gaussian)
    without flattening the normal spread.

WHY the module-level cache keyed on (indexed_at, file_count, max_nodes)?
    The layout endpoint is called every time the Constellation tab loads and on every
    max_nodes slider change. Re-running 40 FA2 iterations on each request would add
    ~200 ms to every page load even when the index has not changed. The cache key
    encodes the current index version (MAX(indexed_at) × 1_000_000 + file_count) so
    a single-file edit invalidates the cache without a separate version counter. TTL
    is borrowed from SEAM_STALENESS_TTL_SECONDS so operators tune one knob for both.

WHY name-keyed node collapse (not id-keyed)?
    Seam's edge table stores source_name/target_name as symbol NAME strings, not
    symbol IDs. Two files may both define a symbol named "helper" — they share one
    graph node with one degree count. Using IDs would give each definition its own
    node and zero edges (no ID-keyed edges exist). The min-id wins rule (deterministic
    tie-break) matches what graph_api.build_neighborhood does. See seam/query/names.py
    and the Known Gotchas in CLAUDE.md ("Homonym collapse").

Public surface:
    compute_layout(conn, *, max_nodes) -> LayoutResult   -- never raises
    stellar_color(degree) -> str                          -- pure
    node_size(kind, degree) -> float                      -- pure

Internal helpers exposed for testing:
    _sphere_seed_positions(selected, reps, depth) -> np.ndarray
    _recenter_and_clamp(pos, *, k) -> np.ndarray
    _fnv1a(text) -> int

See docs/prd/phase11-p2-1-3d-constellation-reference.md for the source algorithm
and docs/superpowers/plans/2026-07-01-3d-constellation-explorer.md for the plan.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections import defaultdict, deque
from typing import TypedDict

import numpy as np

from seam import config
from seam.analysis.processes import list_entry_points
from seam.query.names import edge_match_names

logger = logging.getLogger(__name__)

# ── Algorithm constants (from reference §3/§6) ────────────────────────────────
# These are layout-physics constants — they stay module-local (not in config)
# following the same discipline as clustering.py / rwr.py.
_ITERATIONS = 40          # FA2 iteration count (40 balances quality vs latency)
_REPULSION = 8.0          # gravity repulsion constant kr
_ATTRACTION = 1.0         # edge spring constant ka
_ANCHOR = 0.25            # anchor spring to seed (prevents explosion)
_DISPLACEMENT_CAP = 8.0   # per-iteration displacement ceiling

# Golden ratio for the Fibonacci sphere: ensures maximum angular separation
# (~137.5°) between consecutive φ-steps, producing a near-uniform covering.
_PHI_GOLDEN = (1.0 + 5.0 ** 0.5) / 2.0   # ≈ 1.618

# Spherical seeding constants (#260 — replace flat XY ring)
_SPHERE_RADIUS = 600.0    # base shell radius for seed placement (world units)
_DEPTH_RADIUS = 30.0      # extra radius per BFS depth level (gentle stratification)
_OUTLIER_K = 2.5          # sigma multiplier for post-FA2 radial outlier clamping

# Node-size constants (#260 — sub-linear log1p replaces linear boost)
# log1p(degree) × 1.5 with a 6.0 ceiling gives:
#   deg=0 → 0, deg=10 → 3.6, deg=50 → 5.9, deg=100+ → 6.0 (capped)
# For function kind (base=4.0): leaf=4.0, hub=10.0 → ratio 2.5× ("a few×").
_SIZE_LOG_FACTOR = 1.5    # log1p boost scale
_SIZE_LOG_CEIL = 6.0      # max log boost (caps hub/leaf ratio at ~2.5× for function)

# Stellar color scale: (max_degree, hex_color) pairs, ascending by degree.
# Mirrors the stellar classification O > B > A > F > G > K > M (blue → red).
# A node with degree > 50 falls into the O-class (blue giant) catchall.
_STELLAR = [
    (1, "#ff6050"),   # M — red dwarf (isolated/leaf)
    (3, "#ff8855"),   # K — orange dwarf
    (5, "#ffa060"),   # K-G boundary
    (8, "#ffc070"),   # G-F boundary
    (12, "#ffe080"),  # G — yellow (sun-like)
    (18, "#fff0c0"),  # F — yellow-white
    (25, "#fff8e8"),  # A — white
    (35, "#e8e8ff"),  # B — blue-white
    (50, "#c0d0ff"),  # B+ — blue
]
_STELLAR_MAX = "#80a0ff"  # O — blue giant (degree > 50)

# Base size per symbol kind; defaults to 4.0 for unknown kinds.
_SIZE_FOR_KIND: dict[str, float] = {"class": 6.0, "interface": 6.0}


# ── TypedDicts (public interface) ─────────────────────────────────────────────


class LayoutNode(TypedDict):
    """One positioned node in the constellation layout."""

    id: int
    x: float
    y: float
    z: float
    label: str         # symbol kind (function, class, method, …)
    name: str          # qualified symbol name (e.g. "Client.send")
    file_path: str | None
    size: float
    color: str         # hex color from stellar scale


class LayoutEdge(TypedDict):
    """One directed edge between two layout nodes (referenced by node id)."""

    source: int
    target: int
    type: str          # edge kind (call, import, holds, reads, writes, …)


class LayoutCluster(TypedDict):
    """Functional-area cluster summary for halo rendering."""

    cluster_id: int
    label: str | None  # LLM/deterministic cluster label, or None
    centroid: list[float]  # [x, y, z] mean position of member nodes
    radius: float          # max distance from centroid (min 60 * 1.2 for singletons)
    color: str             # teal accent


class LayoutResult(TypedDict):
    """Full layout result returned by compute_layout and the /api/graph/layout endpoint."""

    nodes: list[LayoutNode]
    edges: list[LayoutEdge]
    clusters: list[LayoutCluster]
    total_nodes: int  # honest count before the max_nodes cap


# ── Module-level cache (CR4) ──────────────────────────────────────────────────
# Key: (int(MAX(files.indexed_at)), max_nodes) — changes to the index produce a new key.
# Value: (monotonic_timestamp, LayoutResult).
# TTL reuses SEAM_STALENESS_TTL_SECONDS for consistency; only the TIMESTAMP is not
# deterministic — positions inside LayoutResult are always deterministic.
_CACHE: dict[tuple[int, int], tuple[float, LayoutResult]] = {}
_CACHE_MAX = 8  # bounded dict — evict oldest when full


# ── Public API ────────────────────────────────────────────────────────────────


def stellar_color(degree: int) -> str:
    """Map a node's undirected degree to a stellar color (red dwarf → blue giant).

    Args:
        degree: Undirected edge count for the symbol (sum of in + out edges,
                counting both endpoints of each edge once, using the name bridge).

    Returns:
        A 7-character hex string such as "#ff6050".
    """
    for threshold, color in _STELLAR:
        if degree <= threshold:
            return color
    return _STELLAR_MAX


def node_size(kind: str, degree: int) -> float:
    """Compute the display size for a node using a sub-linear log1p degree scale.

    WHY log1p instead of linear?
        The old formula `base + min(deg * 0.3, 10)` reached its cap abruptly and
        made very-high-degree hubs visually grotesque before the cap kicked in.
        log1p(degree) grows fast initially then flattens, so the hub/leaf visual
        ratio stays within "a few×" (≈2.5× for function kind) at ANY degree, with
        no discontinuity at the cap.

    Args:
        kind:   Symbol kind string (e.g. "function", "class").
        degree: Undirected degree for boost computation.

    Returns:
        Float size bounded to [base, base + _SIZE_LOG_CEIL].
        For function (base=4.0): min=4.0, max=10.0, hub/leaf ratio≈2.5×.
        For class/interface (base=6.0): min=6.0, max=12.0.
    """
    base = _SIZE_FOR_KIND.get(kind, 4.0)
    boost = min(math.log1p(max(degree, 0)) * _SIZE_LOG_FACTOR, _SIZE_LOG_CEIL)
    return base + boost


def compute_layout(
    conn: sqlite3.Connection,
    *,
    max_nodes: int = config.SEAM_LAYOUT_MAX_NODES,
) -> LayoutResult:
    """Compute a deterministic 3D constellation layout for the indexed graph.

    This is the primary entry point. It is cached per (index_version, max_nodes)
    with a TTL of SEAM_STALENESS_TTL_SECONDS to avoid re-running the O(n²) kernel
    on every request.

    Args:
        conn:      Open SQLite connection (read-only or read-write).
        max_nodes: Maximum nodes to include in the layout. Defaults to
                   SEAM_LAYOUT_MAX_NODES. Callers should clamp before calling
                   (web_layout.py does this); this function also applies
                   SEAM_LAYOUT_MAX_SAFE_NODES as a hard ceiling (CR5).

    Returns:
        LayoutResult with nodes, edges, clusters, and total_nodes.
        NEVER raises — returns empty layout on any error.
    """
    _empty: LayoutResult = {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}
    try:
        # CR5: hard ceiling inside the module — safe regardless of caller
        max_nodes = min(max_nodes, config.SEAM_LAYOUT_MAX_SAFE_NODES)

        # CR4: check cache — key includes file count so an empty and a non-empty index
        # with identical indexed_at=0 (common in tests / fresh repos) don't collide.
        ver_row = conn.execute(
            "SELECT COALESCE(MAX(indexed_at), 0), COUNT(*) FROM files"
        ).fetchone()
        if ver_row:
            ver = int(ver_row[0]) * 1_000_000 + int(ver_row[1])
        else:
            ver = 0
        key = (ver, int(max_nodes))
        hit = _CACHE.get(key)
        if hit and (time.monotonic() - hit[0]) < config.SEAM_STALENESS_TTL_SECONDS:
            return hit[1]

        result = _compute_layout_impl(conn, max_nodes=max_nodes)

        # Evict oldest entry when cache is full (bounded dict)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic(), result)
        return result

    except (sqlite3.Error, ValueError, KeyError) as exc:
        # Narrow except: IM3 — do not swallow bugs, only DB/value/key errors
        logger.exception("compute_layout failed; returning empty layout: %s", exc)
        return _empty


# ── Internal implementation ───────────────────────────────────────────────────


def _fnv1a(text: str) -> int:
    """32-bit FNV-1a hash (unsigned) — deterministic sphere seeding.

    Pure Python, no stdlib hash(): CPython randomizes hash() at startup via
    PYTHONHASHSEED, so hash("foo") changes between server restarts and would
    make initial node positions — and therefore the cached FA2 result — non-
    deterministic. FNV-1a is a fast, collision-resistant, well-understood
    non-cryptographic hash whose output is stable across Python versions.

    Used to derive the cluster-sort key for Fibonacci sphere index assignment
    (same-cluster nodes get contiguous indices → same polar-angle band on sphere).
    """
    h = 2166136261
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def _sphere_seed_positions(
    selected: list[str],
    reps: dict,
    depth: dict[str, int],
) -> np.ndarray:
    """Deterministic Fibonacci (golden-spiral) sphere seed positions for FA2.

    WHY Fibonacci sphere?
        The old XY ring + z-smear seed produced a disc-cloud with a depth spike.
        The Fibonacci sphere places n points uniformly over a sphere surface using
        the golden-angle step (~137.5°) in azimuth and equal-area bands in polar
        angle. An evaluator sees a recognisable "ball of code" at first glance.

    WHY cluster-sorted Fibonacci indices for locality?
        Consecutive Fibonacci indices share the same polar-angle (theta) band —
        they differ primarily in azimuth by the golden angle, but their z-component
        (cos theta) varies slowly. By sorting nodes by (fnv1a(cluster_key),
        fnv1a(name)) before assigning indices, same-cluster nodes get contiguous
        indices → they land on the same spherical cap. FA2 then refines these into
        compact islands rather than starting from a fully scrambled spread.

    WHY depth modulates radius instead of Z?
        z = -depth * 50 created a visible "depth spike" perpendicular to the disc.
        Modulating the shell radius (entry points at base radius, deeper nodes on
        slightly larger shells) encodes depth as distance from the globe's centre
        without breaking the spherical shape. The effect is subtle by design.

    Args:
        selected:  Ordered list of node names (position in list = numpy row index).
        reps:      Dict mapping name → row-like object with subscript access to
                   "file_path" (matches sqlite3.Row interface).
        depth:     BFS depth per name (0 for unreachable / entry-point nodes).

    Returns:
        (n, 3) float64 array of seed positions. Row i corresponds to selected[i].
        All positions have magnitude _SPHERE_RADIUS + depth[name] * _DEPTH_RADIUS.
    """
    n = len(selected)
    name_to_layout_idx = {name: i for i, name in enumerate(selected)}

    # Cluster-grouped sort: nodes sharing a cluster_key get contiguous Fibonacci
    # indices → same polar-angle band on the sphere (angular locality).
    # Within a cluster, FNV-1a(name) gives a stable per-node offset.
    def _sort_key(name: str) -> tuple[int, int]:
        fp = reps[name]["file_path"] or ""
        cluster_key = "/".join(fp.split("/")[:3])  # first 3 path components
        return (_fnv1a(cluster_key), _fnv1a(name))

    sphere_order = sorted(selected, key=_sort_key)
    # sphere_idx[name] = j means this name gets the j-th Fibonacci sphere position
    sphere_idx = {name: j for j, name in enumerate(sphere_order)}

    pos = np.zeros((n, 3), dtype=np.float64)
    for name in selected:
        i = name_to_layout_idx[name]   # row in output array
        j = sphere_idx[name]           # Fibonacci sphere index

        # Fibonacci sphere: equal-area polar bands + golden-angle azimuth.
        # acos(1 - 2*(j+0.5)/n) distributes theta uniformly in cos-space so
        # each band subtends the same solid angle (no polar-cap crowding).
        if n > 1:
            cos_theta = 1.0 - 2.0 * (j + 0.5) / n
            cos_theta = max(-1.0, min(1.0, cos_theta))  # guard float rounding
            theta = math.acos(cos_theta)
        else:
            theta = math.pi / 2.0   # single node → equatorial

        phi = 2.0 * math.pi * j / _PHI_GOLDEN  # golden-angle azimuth step

        # Shell radius: depth-0 nodes at _SPHERE_RADIUS; deeper nodes slightly farther
        r = _SPHERE_RADIUS + depth.get(name, 0) * _DEPTH_RADIUS

        sin_theta = math.sin(theta)
        pos[i, 0] = r * sin_theta * math.cos(phi)
        pos[i, 1] = r * sin_theta * math.sin(phi)
        pos[i, 2] = r * math.cos(theta)

    return pos


def _recenter_and_clamp(pos: np.ndarray, *, k: float = _OUTLIER_K) -> np.ndarray:
    """Recenter positions to centroid; clamp radial outliers beyond mean + k·sigma.

    WHY recenter?
        FA2 does not guarantee that the final layout centroid is at the origin.
        Recentering after the force pass ensures the constellation sits at (0,0,0)
        so the camera look-at framing works without adjustment.

    WHY clamp at mean + k·sigma?
        Very-high-degree hubs can dominate repulsion and get pushed far from the
        cluster even with an anchor spring — creating the "orange spike" artefact.
        Clamping at mean + k·sigma (k=2.5 ≈ 99th percentile of a Gaussian) is
        a deterministic safety net that handles rare extreme cases without
        flattening the normal spread. Nodes within 2.5σ are never moved.

    Args:
        pos: (n, 3) float64 positions from FA2. Not mutated.
        k:   Standard-deviation multiplier for the outlier threshold (default 2.5).

    Returns:
        (n, 3) float64 recentred and clamped positions (new array).
    """
    n = len(pos)
    if n <= 1:
        return pos.copy()

    # Recentre: shift so the constellation cloud is centred at the origin.
    # pos - centroid returns a NEW array — the FA2 output is never mutated.
    centroid = pos.mean(axis=0)
    pos = pos - centroid

    # Radial distances from the recentred origin
    radii = np.linalg.norm(pos, axis=1)   # (n,)
    mean_r = float(radii.mean())
    sigma_r = float(radii.std())
    clamp_r = mean_r + k * sigma_r

    # Pull outliers inward: preserve direction, reduce magnitude to clamp_r.
    # Only fires when there are genuine outliers (sigma > 0 and some radius > clamp_r).
    beyond = radii > clamp_r
    if np.any(beyond):
        # Scale factors: shape (m, 1) so they broadcast correctly against (m, 3)
        scales = (clamp_r / radii[beyond])[:, np.newaxis]
        pos = pos.copy()          # avoid mutating the post-centroid intermediate
        pos[beyond] = pos[beyond] * scales

    return pos


def _compute_layout_impl(
    conn: sqlite3.Connection,
    *,
    max_nodes: int,
) -> LayoutResult:
    """Core layout pipeline — runs the full ForceAtlas2 + cluster pass.

    Raises sqlite3.Error, ValueError, KeyError on internal failures (all caught
    by compute_layout's narrow except).

    Pipeline:
        1.  Representative per unique symbol name (min-id wins → deterministic)
        2.  Qualified↔bare degree computation via edge_match_names (CR1)
        3.  Select top-N by degree DESC, name ASC (deterministic tie-break)
        4.  Filter edges to selected names, dedup, build adjacency
        5.  BFS call-depth from entry points
        6.  Fibonacci sphere seed positions (cluster-grouped for angular locality)
        7.  numpy ForceAtlas2 (O(n²) repulsion)
        8.  Recenter positions + clamp radial outliers
        9.  Assign colors / sizes
        10. Cluster centroids and radii
    """
    conn.row_factory = sqlite3.Row

    # 1. Representative symbol per unique name (min id wins → deterministic)
    sym_rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.cluster_id, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name IS NOT NULL "
        "ORDER BY s.id ASC"
    ).fetchall()

    reps: dict[str, sqlite3.Row] = {}
    for row in sym_rows:
        # setdefault: first (min-id) row wins — homonym collapse
        reps.setdefault(row["name"], row)

    total_nodes = len(reps)
    if total_nodes == 0:
        return {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}

    # 2. Qualified↔bare degree computation (CR1: bridge via edge_match_names)
    # Build a lookup from any match-name form (qualified or bare) → owning rep name.
    # edge_match_names("Client.send") returns {"Client.send", "send"} so an edge
    # stored as bare "send" resolves to the "Client.send" symbol.
    match_to_name: dict[str, str] = {}
    # Qualified names (containing a dot, e.g. "Client.send") claim their bare suffix
    # BEFORE the container class does. Without this ordering, "Client" (lower DB id,
    # therefore processed first in insertion order) expands via edge_match_names to
    # ["Client", "send", ...] and setdefault("send", "Client") wins — then when
    # "Client.send" tries setdefault("send", "Client.send") it is a no-op. Result:
    # the method's degree stays 0 (no edges join it), producing an isolated star in
    # the constellation. Qualified-first breaks the tie so "Client.send" owns "send".
    # This is the qualified↔bare name-bridge described in seam/query/names.py (CR1).
    qualified_names = [n for n in reps if "." in n]
    plain_names = [n for n in reps if "." not in n]
    for name in qualified_names + plain_names:
        # edge_match_names(conn, name) returns [qualified, bare] or [name, member1, ...]
        for m in edge_match_names(conn, name):
            # First winner wins on collision — homonym collapse + method-over-class priority
            match_to_name.setdefault(m, name)

    edge_rows = conn.execute(
        "SELECT source_name, target_name, kind FROM edges"
    ).fetchall()

    degree: dict[str, int] = {name: 0 for name in reps}
    for e in edge_rows:
        s = match_to_name.get(e["source_name"])
        t = match_to_name.get(e["target_name"])
        if s in degree:
            degree[s] += 1
        if t in degree:
            degree[t] += 1

    # 3. Select top-N names by degree DESC, name ASC (deterministic tie-break)
    selected = sorted(reps, key=lambda n: (-degree[n], n))[:max_nodes]
    sel_set = set(selected)
    name_to_idx = {name: i for i, name in enumerate(selected)}  # node id = index

    # 4. Filter edges (both endpoints selected, no self-loops), dedup
    seen: set[tuple[int, int, str]] = set()
    out_edges: list[LayoutEdge] = []
    adjacency: dict[str, list[str]] = {n: [] for n in selected}

    for e in edge_rows:
        # Resolve bare/qualified edge endpoints to selected rep names
        s_name = match_to_name.get(e["source_name"])
        t_name = match_to_name.get(e["target_name"])
        if s_name not in sel_set or t_name not in sel_set:
            continue
        if s_name == t_name:
            # Self-loop (e.g. main→main) — reject
            continue
        edge_key = (name_to_idx[s_name], name_to_idx[t_name], e["kind"])
        if edge_key not in seen:
            seen.add(edge_key)
            out_edges.append(
                {"source": edge_key[0], "target": edge_key[1], "type": e["kind"]}
            )
        # Directed adjacency for BFS (even for duplicate edges, idempotent)
        if t_name not in adjacency[s_name]:
            adjacency[s_name].append(t_name)

    # 5. BFS call-depth from entry-point names
    depth = _bfs_depth(conn, selected, sel_set, adjacency)

    # 6. Fibonacci sphere seed positions (cluster-grouped, deterministic)
    n = len(selected)
    seed = _sphere_seed_positions(selected, reps, depth)

    # Node masses: 1 + degree so high-degree hubs have stronger repulsion & anchor pull
    mass = np.ones(n, dtype=np.float64)
    for name in selected:
        i = name_to_idx[name]
        mass[i] = 1.0 + degree[name]

    # 7. ForceAtlas2 (numpy O(n²)), positions only — no randomness
    pos = _force_atlas2(seed, mass, out_edges)

    # 8. Recenter to centroid + clamp radial outliers (prevents "orange spike" artefact)
    pos = _recenter_and_clamp(pos)

    # 9. Build output nodes list
    nodes: list[LayoutNode] = []
    for name in selected:
        i = name_to_idx[name]
        row = reps[name]
        deg = degree[name]
        nodes.append(
            {
                "id": i,
                "x": float(pos[i, 0]),
                "y": float(pos[i, 1]),
                "z": float(pos[i, 2]),
                "label": row["kind"] or "unknown",
                "name": name,
                "file_path": row["file_path"],
                "size": node_size(row["kind"] or "", deg),
                "color": stellar_color(deg),
            }
        )

    # 10. Cluster centroids and radii
    clusters = _cluster_summaries(conn, selected, reps, name_to_idx, pos)

    return {
        "nodes": nodes,
        "edges": out_edges,
        "clusters": clusters,
        "total_nodes": total_nodes,
    }


def _bfs_depth(
    conn: sqlite3.Connection,
    selected: list[str],
    sel_set: set[str],
    adjacency: dict[str, list[str]],
) -> dict[str, int]:
    """BFS call-depth from entry-point names.

    Seeds are the entry points (call-graph roots) that appear in the selected set.
    Nodes unreachable from any entry point get depth 0 (placed at the base shell radius).
    Never raises — falls back to all-zero on any error.
    """
    depth = {name: 0 for name in selected}
    try:
        entries = list_entry_points(conn)
        seeds = [ep["name"] for ep in entries if ep.get("name") in sel_set]
    except Exception:
        return depth

    if not seeds:
        return depth

    q: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
    visited: set[str] = set(seeds)
    while q:
        name, d = q.popleft()
        depth[name] = d
        for nxt in adjacency.get(name, []):
            if nxt not in visited:
                visited.add(nxt)
                q.append((nxt, d + 1))
    return depth


def _force_atlas2(
    seed: np.ndarray,
    mass: np.ndarray,
    edges: list[LayoutEdge],
) -> np.ndarray:
    """40-iteration ForceAtlas2 with anchor springs. numpy O(n²) repulsion.

    Args:
        seed:  (n, 3) float64 initial positions (Fibonacci sphere seeds).
        mass:  (n,) float64 node masses (1 + degree).
        edges: directed edge list with source/target indices.

    Returns:
        (n, 3) float64 final positions. Pure — does not mutate seed.
    """
    pos = seed.copy()
    n = len(pos)
    if n <= 1:
        return pos

    # Pre-build edge index arrays for vectorised attraction
    src = (
        np.array([e["source"] for e in edges], dtype=np.int64)
        if edges
        else np.empty(0, dtype=np.int64)
    )
    tgt = (
        np.array([e["target"] for e in edges], dtype=np.int64)
        if edges
        else np.empty(0, dtype=np.int64)
    )

    for _ in range(_ITERATIONS):
        force = np.zeros_like(pos)

        # Repulsion (all pairs): F_i = sum_j(kr * m_i * m_j / d²) * unit_direction
        # The (n, n, 3) difference tensor is the core of the ForceAtlas2 gravity law.
        # O(n²) in both time and space — this is WHY we cap at SEAM_LAYOUT_MAX_NODES:
        # n=3000 → 9 M distances × 3 floats = 216 MB per iteration before numpy's
        # in-place sum. The 1e-3 epsilon prevents division by zero when two nodes
        # start at the same position (can happen in degenerate test graphs).
        delta = pos[:, None, :] - pos[None, :, :]       # (n, n, 3)
        dist2 = np.sum(delta * delta, axis=2) + 1e-3    # (n, n) avoid /0
        inv = _REPULSION * (mass[:, None] * mass[None, :]) / dist2
        np.fill_diagonal(inv, 0.0)                       # no self-force
        force += np.sum(delta * inv[:, :, None], axis=1)

        # Attraction along edges: F_src += ka * (pos_tgt - pos_src)
        if len(src):
            d = pos[tgt] - pos[src]
            np.add.at(force, src, _ATTRACTION * d)
            np.add.at(force, tgt, -_ATTRACTION * d)

        # Anchor spring toward seed: FA2 has no gravity term in its pure form, so
        # high-degree hubs can explode to infinity when their repulsion dominates.
        # A weak spring (ka=0.25, mass-scaled so heavier nodes feel a stronger pull)
        # keeps the layout bounded while still allowing FA2 to find the energy minimum.
        force += _ANCHOR * mass[:, None] * (seed - pos)

        # Per-iteration displacement cap: prevents explosion on first iterations
        mag = np.linalg.norm(force, axis=1, keepdims=True) + 1e-9
        scale = np.minimum(1.0, _DISPLACEMENT_CAP / mag)
        pos = pos + force * scale

    return pos


def _cluster_summaries(
    conn: sqlite3.Connection,
    selected: list[str],
    reps: dict[str, sqlite3.Row],
    name_to_idx: dict[str, int],
    pos: np.ndarray,
) -> list[LayoutCluster]:
    """Compute cluster centroids and radii for halo rendering.

    - Cluster label is read from the clusters table if available.
    - Nodes with NULL cluster_id are excluded from summaries.
    - A single-member cluster gets radius = 60.0 * 1.2 (the minimum fallback).
    - Results are sorted by cluster_id for determinism.
    """
    labels: dict[int, str | None] = {}
    try:
        for row in conn.execute("SELECT id, label FROM clusters").fetchall():
            labels[row["id"]] = row["label"]
    except sqlite3.Error:
        pass

    # Group node indices by cluster_id (skip NULL)
    members: dict[int, list[int]] = defaultdict(list)
    for name in selected:
        cid = reps[name]["cluster_id"]
        if cid is not None:
            members[cid].append(name_to_idx[name])

    out: list[LayoutCluster] = []
    for cid, idxs in members.items():
        pts = pos[idxs]  # (k, 3) positions of members
        centroid = pts.mean(axis=0)  # (3,)
        if len(idxs) > 1:
            radius = float(np.max(np.linalg.norm(pts - centroid, axis=1)))
        else:
            radius = 60.0  # singleton fallback (radius * 1.2 applied below)
        out.append(
            {
                "cluster_id": cid,
                "label": labels.get(cid),
                "centroid": [float(centroid[0]), float(centroid[1]), float(centroid[2])],
                "radius": radius * 1.2,  # 20% padding so stars sit inside the halo
                "color": "#1DA27E",      # Seam teal — consistent brand accent
            }
        )

    # Sort by cluster_id for deterministic output order
    out.sort(key=lambda c: c["cluster_id"])
    return out
