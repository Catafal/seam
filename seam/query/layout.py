"""Server-side 3D layout for the constellation Explorer view (Phase 11 P2.1).

Pure, deterministic, never raises. Implements the graph-ui ForceAtlas2 +
anchor-spring + ring-seed algorithm in numpy (compiled-C speed, no scipy, no C
extension). Nodes collapse by symbol NAME (Seam is name-keyed like graph_api).

The public surface:
    compute_layout(conn, *, max_nodes) -> LayoutResult   -- never raises
    stellar_color(degree) -> str                          -- pure
    node_size(kind, degree) -> float                      -- pure

See docs/prd/phase11-p2-1-3d-constellation-reference.md for the source algorithm
and docs/superpowers/plans/2026-07-01-3d-constellation-explorer.md for the plan.
"""

from __future__ import annotations

import logging
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
_DEPTH_Z = 50.0           # z-axis spread per BFS depth level
_RING_MIN = 500.0         # minimum ring radius for seed placement
_RING_SPAN = 250.0        # ring radius variation span
_JITTER = 40.0            # per-node positional jitter magnitude

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
    """Compute the display size for a node from its kind and degree.

    Base size is 6.0 for class/interface, 4.0 for everything else.
    Nodes with degree > 5 gain a boost of min(degree * 0.3, 10.0).

    Args:
        kind:   Symbol kind string (e.g. "function", "class").
        degree: Undirected degree for boost computation.

    Returns:
        Float size value (minimum 4.0 for most kinds, maximum base + 10.0).
    """
    base = _SIZE_FOR_KIND.get(kind, 4.0)
    boost = min(degree * 0.3, 10.0) if degree > 5 else 0.0
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
    """32-bit FNV-1a hash (unsigned) — deterministic ring seeding.

    Pure Python, no stdlib hash (which is randomized by default in CPython).
    """
    h = 2166136261
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def _compute_layout_impl(
    conn: sqlite3.Connection,
    *,
    max_nodes: int,
) -> LayoutResult:
    """Core layout pipeline — runs the full ForceAtlas2 + cluster pass.

    Raises sqlite3.Error, ValueError, KeyError on internal failures (all caught
    by compute_layout's narrow except).

    Pipeline:
        1. Representative per unique symbol name (min-id wins → deterministic)
        2. Qualified↔bare degree computation via edge_match_names (CR1)
        3. Select top-N by degree DESC, name ASC (deterministic tie-break)
        4. Filter edges to selected names, dedup, build adjacency
        5. BFS call-depth from entry points
        6. FNV-1a ring seed positions
        7. numpy ForceAtlas2 (O(n²) repulsion)
        8. Assign colors / sizes
        9. Cluster centroids and radii
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
    # Process qualified names (with dots, e.g. "Client.send") FIRST so methods claim
    # their bare suffix before a class expansion can steal it. Within each tier, min-id
    # (insertion) order is preserved, giving the homonym-collapse guarantee.
    #
    # Without this ordering, "Client" (lower id) would expand to ["Client","send",...]
    # via edge_match_names and claim "send" → "Client", then "Client.send" can't get
    # "send" via setdefault, leaving the method node degree 0 (isolated star). CR1.
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

    # 6. FNV-1a ring seed positions (deterministic — no random/time in math)
    n = len(selected)
    seed = np.zeros((n, 3), dtype=np.float64)
    mass = np.ones(n, dtype=np.float64)

    for name in selected:
        i = name_to_idx[name]
        fp = reps[name]["file_path"] or ""
        # Cluster ring: first 3 path components group co-located files together
        cluster_key = "/".join(fp.split("/")[:3])
        h = _fnv1a(cluster_key)
        angle = (h & 0xFFFF) / 65535.0 * 2.0 * np.pi
        radius = _RING_MIN + ((h >> 16) & 0xFF) / 255.0 * _RING_SPAN
        # Per-node jitter via LCG(FNV-1a(name)) — deterministic
        js = _fnv1a(name)
        js = (js * 1103515245 + 12345) & 0xFFFFFFFF
        jx = ((js >> 16) & 0x7FFF) / 32768.0 - 0.5
        js = (js * 1103515245 + 12345) & 0xFFFFFFFF
        jy = ((js >> 16) & 0x7FFF) / 32768.0 - 0.5
        seed[i, 0] = np.cos(angle) * radius + jx * _JITTER
        seed[i, 1] = np.sin(angle) * radius + jy * _JITTER
        seed[i, 2] = -depth[name] * _DEPTH_Z
        mass[i] = 1.0 + degree[name]

    # 7. ForceAtlas2 (numpy O(n²)), positions only — no randomness
    pos = _force_atlas2(seed, mass, out_edges)

    # 8. Build output nodes list
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

    # 9. Cluster centroids and radii
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
    Nodes unreachable from any entry point get depth 0 (placed at z=0).
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
        seed:  (n, 3) float64 initial positions.
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

        # Repulsion (all pairs): F_i = sum_j(kr * m_i * m_j / d²) * direction
        # (n, n, 3) difference tensor — O(n²) in both time and space.
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

        # Anchor spring toward seed: prevents nodes from drifting to infinity
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
