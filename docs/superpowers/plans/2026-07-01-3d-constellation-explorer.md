# 3D Constellation Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lazy-loaded 3D "constellation" Explorer tab (React-Three-Fiber) that renders the indexed codebase as a glowing star field, fed by a new read-only `/api/graph/layout` endpoint whose positions are computed server-side with a numpy ForceAtlas2 layout.

**Architecture:** Purely additive. One new pure Python layout module (`seam/query/layout.py`) + one new FastAPI route module (`seam/server/web_layout.py`, following the existing `register_*_routes` pattern) + one new lazy-loaded frontend tab under `web/src/`. The MCP/CLI core, the 2D React Flow canvas, the existing 2D `/api/constellation` cluster-overview, and all existing routes are untouched. No schema migration, no watcher change, no re-index, no new MCP tool.

**Tech Stack:** Python 3.14 + numpy (new, `[web]` extra only) + FastAPI/Pydantic (existing `[web]`); React 19 + Vite + `@react-three/fiber ^9` + `@react-three/drei ^10` + `@react-three/postprocessing ^3` + `three ~0.183` (new) + existing Tailwind v3 + react-query.

## Global Constraints

- Max 200 lines per function; max 1000 lines per file. If `layout.py` nears 1000 lines, split the force kernel into `seam/query/layout_forces.py`.
- All imports at top of file. Config from `seam/config.py` only — never `os.getenv()` elsewhere.
- Type hints required; use `X | None` not `Optional[X]`.
- snake_case files+functions | PascalCase classes | UPPER_SNAKE constants.
- Zero external services at runtime; zero network on the read path. numpy is a compiled-C/SIMD local library — no network.
- Parsers/analysis modules never raise — return an empty-but-valid result on any internal error; let the caller degrade.
- Edges are name-keyed (`source_name`/`target_name`), not symbol ids. The graph collapses by symbol NAME (one node per unique name), matching `seam/server/graph_api.py` `build_neighborhood`.
- The layout must be **deterministic** — no `random`, no `Math.random`, no `Date.now()` in layout math. Seed from FNV-1a/LCG so tests assert stable positions.
- `make gate` (ruff + mypy + full pytest) must pass before every commit. Frontend: `npm run typecheck` + `npm test` (vitest) must pass.
- New MCP tool count stays as-is (this is web-only). No Playwright for v1 — canvas verified manually.
- Naming to avoid collision with the existing 2D `/api/constellation`: backend endpoint is `/api/graph/layout`; backend Pydantic models are `Layout*`; frontend components use the `Constellation*` prefix (there is no existing frontend `Constellation*` component).

---

## Applied Review Revisions (2026-07-01)

The auto plan-review (`docs/superpowers/plans/review-2026-07-01/`) found 6 critical issues. The
inline code in Tasks 1–2 has been corrected for the fixture (CR3) and connection handling (CR2/CR5).
The following cross-cutting corrections MUST also be applied while implementing — they supersede the
original Step-8 code where they overlap:

**CR6 — config knobs (non-negotiable: config only via `seam/config.py`).** Add to `seam/config.py`
(read via `seam.config` in `layout.py`/`web_layout.py`; algorithm constants stay module-local):
```python
SEAM_LAYOUT_MAX_NODES = int(os.getenv("SEAM_LAYOUT_MAX_NODES", "2000"))       # default render cap
SEAM_LAYOUT_MAX_SAFE_NODES = int(os.getenv("SEAM_LAYOUT_MAX_SAFE_NODES", "3000"))  # OOM ceiling
# cache TTL reuses existing SEAM_STALENESS_TTL_SECONDS
```

**CR5 — clamp inside the module** so it is safe regardless of caller. First line of
`_compute_layout_impl`: `max_nodes = min(max_nodes, config.SEAM_LAYOUT_MAX_SAFE_NODES)`.

**CR1 — bridge qualified↔bare names (correctness).** Edges store the BARE target (`send`), symbols
store QUALIFIED (`Client.send`). Keying degree/adjacency on `symbols.name` gives every method
degree 0 → isolated stars. Reuse the existing leaf `seam/query/names.py`. In `_compute_layout_impl`,
build a match index from each representative's `edge_match_names` (qualified + bare) and count/route
edges through it:
```python
from seam.query.names import edge_match_names   # returns {qualified, bare} for a symbol name
# match_name (bare or qualified as stored on the edge) -> owning selected symbol name
match_to_name: dict[str, str] = {}
for name in reps:
    for m in edge_match_names(name):
        match_to_name.setdefault(m, name)   # first (min-id rep) wins — homonym collapse
# degree: an edge endpoint counts for the symbol whose match-set contains it
for e in edge_rows:
    s = match_to_name.get(e["source_name"]); t = match_to_name.get(e["target_name"])
    if s in degree: degree[s] += 1
    if t in degree: degree[t] += 1
# adjacency + edge filtering use match_to_name[...] to resolve endpoints to selected node names
```
Cross-ref `seam/server/graph_api.py` (`build_constellation`/`top_hub_symbols`) which collapse by
name similarly (IM5) — reuse its degree SQL idea rather than re-deriving where clean.

**CR4 — caching (module-level, in `layout.py`).** `compute_layout` must not re-run the O(n²) kernel
every request:
```python
import time
_CACHE: dict[tuple[int, int], tuple[float, LayoutResult]] = {}   # (index_ver, max_nodes) -> (ts, result)

def compute_layout(conn, *, max_nodes=config.SEAM_LAYOUT_MAX_NODES) -> LayoutResult:
    try:
        ver = conn.execute("SELECT COALESCE(MAX(indexed_at), 0) FROM files").fetchone()[0] or 0
        key = (int(ver), int(max_nodes))
        hit = _CACHE.get(key)
        if hit and (time.monotonic() - hit[0]) < config.SEAM_STALENESS_TTL_SECONDS:
            return hit[1]
        result = _compute_layout_impl(conn, max_nodes=max_nodes)
        if len(_CACHE) > 8:            # bounded — evict oldest
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic(), result)
        return result
    except (sqlite3.Error, ValueError, KeyError):   # IM3: narrow, not blanket Exception
        logger.exception("compute_layout failed; returning empty layout")
        return {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}
```
(This replaces the Step-3 try/except-`Exception` skeleton. `time.monotonic` — not `Date`/`time.time`
in the layout math — keeps determinism of POSITIONS intact; the cache timestamp is not part of the
result.)

**IM2 — extract pure helpers from the R3F components and unit-test them** (Tasks 4–5): add
`buildEdgeGeometry(nodes, edges, highlightedIds) → {positions, colors}` (EdgeLines — highest value),
`computeInstanceColor(node, isHighlighted, isDimmed)` (NodeCloud boost math),
`easeOutCubic(p) = 1-(1-p)**3`, `selectLabelNodes(nodes, cap=80)` (NodeLabels). Each gets a vitest.

**IM4 — react-query error/loading UI** (Task 7): `ConstellationTab` renders `isError`/`isLoading`
branches; add a vitest mocking `fetch → 500` asserting a visible error message (not a blank canvas).

**IM6 — staleness banner: intentionally omitted** for this cosmetic web-only endpoint; the HUD
freshness dot (`/api/status`) already surfaces staleness. Recorded here as an explicit decision.

**MI1 — rename the TS types** to `LayoutNode`/`LayoutEdge` (Task 3) to avoid colliding with the
existing 2D `graph_api` `GraphNode` (different shape). Keep `ClusterSummary`/`LayoutData`.

**MI2 — golden coordinate:** add one assertion on a specific node's rounded `x` so an accidental
algorithm change is caught, not just re-run determinism.

---

## Backend

### Task 1: Layout engine `seam/query/layout.py`

**Files:**
- Create: `seam/query/layout.py`
- Modify: `pyproject.toml` (add `numpy>=1.26` to the `[web]` optional extra)
- Test: `tests/query/test_layout.py`

**Interfaces:**
- Consumes: `sqlite3.Connection`; `seam.analysis.processes.list_entry_points(conn, ...)` for depth-0 seeds; symbols table (`id, file_id, name, kind, start_line, cluster_id, qualified_name`), `files.path`, `edges (source_name, target_name)`, `clusters (id, label)`.
- Produces:
  - `LayoutNode = TypedDict('LayoutNode', {'id': int, 'x': float, 'y': float, 'z': float, 'label': str, 'name': str, 'file_path': str | None, 'size': float, 'color': str})`
  - `LayoutEdge = TypedDict('LayoutEdge', {'source': int, 'target': int, 'type': str})`
  - `LayoutCluster = TypedDict('LayoutCluster', {'cluster_id': int, 'label': str | None, 'centroid': list[float], 'radius': float, 'color': str})`
  - `LayoutResult = TypedDict('LayoutResult', {'nodes': list[LayoutNode], 'edges': list[LayoutEdge], 'clusters': list[LayoutCluster], 'total_nodes': int})`
  - `compute_layout(conn: sqlite3.Connection, *, max_nodes: int = 2000) -> LayoutResult` — never raises; empty index → `{'nodes': [], 'edges': [], 'clusters': [], 'total_nodes': 0}`.
  - `stellar_color(degree: int) -> str`
  - `node_size(kind: str, degree: int) -> float`

**Reference:** exact constants + pipeline in `docs/prd/phase11-p2-1-3d-constellation-reference.md` §3 and §6.

- [ ] **Step 1: Write failing tests for the pure helpers**

```python
# tests/query/test_layout.py
import sqlite3
import pytest
from seam.query.layout import stellar_color, node_size, compute_layout


def test_stellar_color_bands():
    assert stellar_color(0) == "#ff6050"     # M red dwarf (deg <= 1)
    assert stellar_color(12) == "#ffe080"    # G yellow (deg <= 12)
    assert stellar_color(999) == "#80a0ff"   # O blue giant (deg > 50)


def test_node_size_boosts_with_degree():
    assert node_size("function", 0) == 4.0
    assert node_size("class", 0) == 6.0
    assert node_size("function", 100) == 4.0 + 10.0   # boost capped at 10
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/jordicatafal/Documents/Github/seam && uv run pytest tests/query/test_layout.py -q`
Expected: FAIL (ImportError / module not found).

- [ ] **Step 3: Implement the pure helpers + module skeleton**

```python
# seam/query/layout.py
"""Server-side 3D layout for the constellation Explorer view (Phase 11 P2.1).

Pure, deterministic, never raises. Reimplements the graph-ui ForceAtlas2 +
anchor-spring + ring-seed algorithm in numpy (compiled-C speed, no scipy, no C
extension). Nodes collapse by symbol NAME (Seam is name-keyed like graph_api).

See docs/prd/phase11-p2-1-3d-constellation-reference.md for the source algorithm.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TypedDict

import numpy as np

from seam.analysis.processes import list_entry_points

logger = logging.getLogger(__name__)

# ── Constants (from the reference §3/§6) ──────────────────────────────────────
_ITERATIONS = 40
_REPULSION = 8.0
_ATTRACTION = 1.0
_ANCHOR = 0.25
_DISPLACEMENT_CAP = 8.0
_DEPTH_Z = 50.0
_RING_MIN, _RING_SPAN = 500.0, 250.0
_JITTER = 40.0

_STELLAR = [
    (1, "#ff6050"), (3, "#ff8855"), (5, "#ffa060"), (8, "#ffc070"),
    (12, "#ffe080"), (18, "#fff0c0"), (25, "#fff8e8"), (35, "#e8e8ff"),
    (50, "#c0d0ff"),
]
_STELLAR_MAX = "#80a0ff"
_SIZE_FOR_KIND = {"class": 6.0, "interface": 6.0}


class LayoutNode(TypedDict):
    id: int
    x: float
    y: float
    z: float
    label: str
    name: str
    file_path: str | None
    size: float
    color: str


class LayoutEdge(TypedDict):
    source: int
    target: int
    type: str


class LayoutCluster(TypedDict):
    cluster_id: int
    label: str | None
    centroid: list[float]
    radius: float
    color: str


class LayoutResult(TypedDict):
    nodes: list[LayoutNode]
    edges: list[LayoutEdge]
    clusters: list[LayoutCluster]
    total_nodes: int


def stellar_color(degree: int) -> str:
    """Map a node's undirected degree to a stellar color (red dwarf → blue giant)."""
    for threshold, color in _STELLAR:
        if degree <= threshold:
            return color
    return _STELLAR_MAX


def node_size(kind: str, degree: int) -> float:
    """Base size by symbol kind + a degree boost capped at +10."""
    base = _SIZE_FOR_KIND.get(kind, 4.0)
    boost = min(degree * 0.3, 10.0) if degree > 5 else 0.0
    return base + boost


def _fnv1a(text: str) -> int:
    """32-bit FNV-1a hash (unsigned) — deterministic ring seeding."""
    h = 2166136261
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def compute_layout(conn: sqlite3.Connection, *, max_nodes: int = 2000) -> LayoutResult:
    """Compute a deterministic 3D layout of the indexed graph. Never raises."""
    empty: LayoutResult = {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}
    try:
        return _compute_layout_impl(conn, max_nodes=max_nodes)
    except Exception:  # never raise — degrade to empty
        logger.exception("compute_layout failed; returning empty layout")
        return empty
```

(Leave `_compute_layout_impl` for the next steps — the helper tests pass now.)

- [ ] **Step 4: Run helper tests to verify pass**

Run: `uv run pytest tests/query/test_layout.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add seam/query/layout.py tests/query/test_layout.py pyproject.toml
git commit -m "feat(layout): stellar-color + node-size helpers + layout module skeleton"
```

- [ ] **Step 6: Write failing tests for the full pipeline on a fixture index**

```python
def _make_index(tmp_path):
    """Build a tiny real index: 2 files, ~6 symbols, a few call edges.

    CR3: init_db(path) RETURNS a conn (do NOT call connect() first). files
    requires NOT NULL columns path, language, file_hash, mtime, indexed_at.
    """
    from seam.indexer.db import init_db
    conn = init_db(tmp_path / "seam.db")
    # files: id, path, language(NN), file_hash(NN), mtime(NN), indexed_at(NN)
    conn.execute("INSERT INTO files(id, path, language, file_hash, mtime, indexed_at) "
                 "VALUES (1,?,?,?,?,?)", (str(tmp_path / "a.py"), "python", "h1", 0.0, 0))
    conn.execute("INSERT INTO files(id, path, language, file_hash, mtime, indexed_at) "
                 "VALUES (2,?,?,?,?,?)", (str(tmp_path / "b.py"), "python", "h2", 0.0, 0))
    # symbols (id, file_id, name, kind, start_line, end_line, cluster_id)
    rows = [
        (1, 1, "main", "function", 1, 5, 1),
        (2, 1, "Client", "class", 6, 20, 1),
        (3, 2, "Client.send", "method", 1, 4, 2),   # QUALIFIED — CR1: must still get degree
        (4, 2, "helper", "function", 5, 8, 2),
        (5, 2, "helper", "function", 9, 12, None),   # IM1: homonym + NULL cluster_id
        (6, 2, "Lonely", "class", 13, 15, 3),        # IM1: single-member cluster (radius 60*1.2)
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,start_line,end_line,cluster_id) "
            "VALUES (?,?,?,?,?,?,?)", r)
    # edges by BARE NAME (edges store the bare target: 'send', not 'Client.send')
    for src, tgt in [("main", "send"), ("main", "helper"), ("send", "helper"),
                     ("main", "main")]:   # IM1: self-edge must be rejected
        conn.execute(
            "INSERT INTO edges(source_name,target_name,kind,file_id,line) VALUES (?,?, 'call',1,1)",
            (src, tgt))
    conn.commit()
    return conn


def test_layout_is_deterministic(tmp_path):
    conn = _make_index(tmp_path)
    a = compute_layout(conn, max_nodes=100)
    b = compute_layout(conn, max_nodes=100)
    assert a == b   # identical positions across runs — no randomness


def test_layout_shape_and_total(tmp_path):
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=100)
    assert r["total_nodes"] >= 4
    assert len(r["nodes"]) >= 1
    n = r["nodes"][0]
    assert set(n.keys()) == {"id", "x", "y", "z", "label", "name", "file_path", "size", "color"}
    # every edge references node ids present in the node set
    ids = {n["id"] for n in r["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in r["edges"])


def test_layout_caps_node_count(tmp_path):
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=2)
    assert len(r["nodes"]) <= 2
    assert r["total_nodes"] >= 4       # total is honest, above the cap


def test_layout_empty_index(tmp_path):
    from seam.indexer.db import init_db
    conn = init_db(tmp_path / "seam.db")   # CR3: init_db(path) returns the conn
    assert compute_layout(conn) == {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}


def test_qualified_member_gets_degree(tmp_path):   # CR1: bridge qualified<->bare
    conn = _make_index(tmp_path)
    r = compute_layout(conn, max_nodes=100)
    send = next(n for n in r["nodes"] if n["name"] == "Client.send")
    # 'Client.send' must connect via bare edge name 'send' (main->send, send->helper)
    ids = {send["id"]}
    touching = [e for e in r["edges"] if e["source"] in ids or e["target"] in ids]
    assert len(touching) >= 1   # NOT isolated


def test_no_self_edges(tmp_path):   # IM1
    r = compute_layout(_make_index(tmp_path), max_nodes=100)
    assert all(e["source"] != e["target"] for e in r["edges"])


def test_homonym_collapse_min_id(tmp_path):   # IM1
    r = compute_layout(_make_index(tmp_path), max_nodes=100)
    helpers = [n for n in r["nodes"] if n["name"] == "helper"]
    assert len(helpers) == 1                       # two 'helper' symbols → one node
    assert helpers[0]["file_path"].endswith("b.py")


def test_null_cluster_and_single_member_radius(tmp_path):   # IM1
    r = compute_layout(_make_index(tmp_path), max_nodes=100)
    lonely = next((c for c in r["clusters"] if c["cluster_id"] == 3), None)
    assert lonely is not None and lonely["radius"] == pytest.approx(60.0 * 1.2)


def test_malformed_row_degrades_gracefully(tmp_path):   # IM3: narrow-except path
    conn = _make_index(tmp_path)
    conn.execute("UPDATE symbols SET name = NULL WHERE id = 1")  # break a row
    conn.commit()
    out = compute_layout(conn, max_nodes=100)   # must not raise
    assert set(out.keys()) == {"nodes", "edges", "clusters", "total_nodes"}
```

- [ ] **Step 7: Run to verify failure**

Run: `uv run pytest tests/query/test_layout.py -q`
Expected: FAIL (`_compute_layout_impl` not defined / KeyError).

- [ ] **Step 8: Implement `_compute_layout_impl` + numpy force kernel**

Append to `seam/query/layout.py`. Pipeline: collapse symbols by name (representative = min id), compute name-degree from edges, select top-`max_nodes` by degree DESC (tie-break name ASC for determinism), filter edges to selected names, BFS depth from entry-point names, FNV-1a ring seed, run FA2, assign colors/sizes, compute cluster centroids.

```python
def _compute_layout_impl(conn: sqlite3.Connection, *, max_nodes: int) -> LayoutResult:
    conn.row_factory = sqlite3.Row
    # 1. Representative symbol per unique name (min id wins → deterministic)
    sym_rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.cluster_id, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY s.id ASC"
    ).fetchall()
    reps: dict[str, sqlite3.Row] = {}
    for r in sym_rows:
        reps.setdefault(r["name"], r)   # first (min id) is the representative
    total_nodes = len(reps)
    if total_nodes == 0:
        return {"nodes": [], "edges": [], "clusters": [], "total_nodes": 0}

    # 2. Undirected degree per name (only edges touching known names count)
    edge_rows = conn.execute("SELECT source_name, target_name, kind FROM edges").fetchall()
    degree: dict[str, int] = {name: 0 for name in reps}
    for e in edge_rows:
        if e["source_name"] in degree:
            degree[e["source_name"]] += 1
        if e["target_name"] in degree:
            degree[e["target_name"]] += 1

    # 3. Select top-N names by degree DESC, name ASC (deterministic)
    selected = sorted(reps, key=lambda n: (-degree[n], n))[:max_nodes]
    sel_set = set(selected)
    name_to_idx = {name: i for i, name in enumerate(selected)}   # node id = index

    # 4. Filter edges (both endpoints selected), dedup
    seen: set[tuple[int, int, str]] = set()
    out_edges: list[LayoutEdge] = []
    adjacency: dict[str, list[str]] = {n: [] for n in selected}
    for e in edge_rows:
        s, t = e["source_name"], e["target_name"]
        if s in sel_set and t in sel_set and s != t:
            key = (name_to_idx[s], name_to_idx[t], e["kind"])
            if key not in seen:
                seen.add(key)
                out_edges.append({"source": key[0], "target": key[1], "type": e["kind"]})
            adjacency[s].append(t)   # directed, for BFS

    # 5. BFS call-depth from entry-point names
    depth = _bfs_depth(conn, selected, sel_set, adjacency)

    # 6. Ring seed positions (deterministic)
    n = len(selected)
    seed = np.zeros((n, 3), dtype=np.float64)
    mass = np.ones(n, dtype=np.float64)
    for name in selected:
        i = name_to_idx[name]
        fp = reps[name]["file_path"] or ""
        cluster_key = "/".join(fp.split("/")[:3])
        h = _fnv1a(cluster_key)
        angle = (h & 0xFFFF) / 65535.0 * 2.0 * np.pi
        radius = _RING_MIN + ((h >> 16) & 0xFF) / 255.0 * _RING_SPAN
        js = _fnv1a(name)
        js = (js * 1103515245 + 12345) & 0xFFFFFFFF
        jx = ((js >> 16) & 0x7FFF) / 32768.0 - 0.5
        js = (js * 1103515245 + 12345) & 0xFFFFFFFF
        jy = ((js >> 16) & 0x7FFF) / 32768.0 - 0.5
        seed[i] = [np.cos(angle) * radius + jx * _JITTER,
                   np.sin(angle) * radius + jy * _JITTER,
                   -depth[name] * _DEPTH_Z]
        mass[i] = 1.0 + degree[name]

    # 7. ForceAtlas2 (numpy O(n^2)), then colors/sizes
    pos = _force_atlas2(seed, mass, out_edges)
    nodes: list[LayoutNode] = []
    for name in selected:
        i = name_to_idx[name]
        r = reps[name]
        deg = degree[name]
        nodes.append({
            "id": i, "x": float(pos[i, 0]), "y": float(pos[i, 1]), "z": float(pos[i, 2]),
            "label": r["kind"], "name": name, "file_path": r["file_path"],
            "size": node_size(r["kind"], deg), "color": stellar_color(deg),
        })

    clusters = _cluster_summaries(conn, selected, reps, name_to_idx, pos)
    return {"nodes": nodes, "edges": out_edges, "clusters": clusters, "total_nodes": total_nodes}


def _bfs_depth(conn, selected, sel_set, adjacency) -> dict[str, int]:
    from collections import deque
    depth = {name: 0 for name in selected}
    try:
        seeds = [ep["name"] for ep in list_entry_points(conn) if ep.get("name") in sel_set]
    except Exception:
        seeds = []
    if not seeds:
        return depth
    q = deque((s, 0) for s in seeds)
    visited = set(seeds)
    while q:
        name, d = q.popleft()
        depth[name] = d
        for nxt in adjacency.get(name, []):
            if nxt not in visited:
                visited.add(nxt)
                q.append((nxt, d + 1))
    return depth


def _force_atlas2(seed: np.ndarray, mass: np.ndarray, edges: list[LayoutEdge]) -> np.ndarray:
    """40-iteration ForceAtlas2 with anchor springs. numpy O(n^2) repulsion."""
    pos = seed.copy()
    n = len(pos)
    if n <= 1:
        return pos
    src = np.array([e["source"] for e in edges], dtype=np.int64) if edges else np.empty(0, np.int64)
    tgt = np.array([e["target"] for e in edges], dtype=np.int64) if edges else np.empty(0, np.int64)
    for _ in range(_ITERATIONS):
        force = np.zeros_like(pos)
        # Repulsion (all pairs): F = kr * m_i * m_j / d
        delta = pos[:, None, :] - pos[None, :, :]          # (n, n, 3)
        dist2 = np.sum(delta * delta, axis=2) + 1e-3
        inv = _REPULSION * (mass[:, None] * mass[None, :]) / dist2
        np.fill_diagonal(inv, 0.0)
        force += np.sum(delta * inv[:, :, None], axis=1)
        # Attraction along edges: F = ka * (p_tgt - p_src)
        if len(src):
            d = pos[tgt] - pos[src]
            np.add.at(force, src, _ATTRACTION * d)
            np.add.at(force, tgt, -_ATTRACTION * d)
        # Anchor spring to seed
        force += _ANCHOR * mass[:, None] * (seed - pos)
        # Displacement cap
        mag = np.linalg.norm(force, axis=1, keepdims=True) + 1e-9
        scale = np.minimum(1.0, _DISPLACEMENT_CAP / mag)
        pos = pos + force * scale
    return pos


def _cluster_summaries(conn, selected, reps, name_to_idx, pos) -> list[LayoutCluster]:
    from collections import defaultdict
    labels: dict[int, str | None] = {}
    try:
        for row in conn.execute("SELECT id, label FROM clusters").fetchall():
            labels[row["id"]] = row["label"]
    except sqlite3.Error:
        pass
    members: dict[int, list[int]] = defaultdict(list)
    for name in selected:
        cid = reps[name]["cluster_id"]
        if cid is not None:
            members[cid].append(name_to_idx[name])
    out: list[LayoutCluster] = []
    for cid, idxs in members.items():
        pts = pos[idxs]
        centroid = pts.mean(axis=0)
        radius = float(np.max(np.linalg.norm(pts - centroid, axis=1))) if len(idxs) > 1 else 60.0
        out.append({
            "cluster_id": cid, "label": labels.get(cid),
            "centroid": [float(centroid[0]), float(centroid[1]), float(centroid[2])],
            "radius": radius * 1.2, "color": "#1DA27E",
        })
    out.sort(key=lambda c: c["cluster_id"])
    return out
```

Verify `list_entry_points` return shape (dict with `name` key) against `seam/analysis/processes.py:307`; adapt the `.get("name")` access if it returns objects instead of dicts.

- [ ] **Step 9: Run the full pipeline tests to verify pass**

Run: `uv run pytest tests/query/test_layout.py -q`
Expected: PASS (6 passed). If `list_entry_points` shape differs, fix `_bfs_depth` and re-run.

- [ ] **Step 10: Run gate (lint + type + tests)**

Run: `cd /Users/jordicatafal/Documents/Github/seam && make gate`
Expected: PASS. Fix any ruff/mypy issues (numpy types may need `# type: ignore[...]` only if mypy lacks stubs — prefer `float(...)` casts already present).

- [ ] **Step 11: Commit**

```bash
git add seam/query/layout.py tests/query/test_layout.py
git commit -m "feat(layout): numpy ForceAtlas2 layout engine (deterministic, name-keyed)"
```

---

### Task 2: Route `seam/server/web_layout.py` + wire into app

**Files:**
- Create: `seam/server/web_layout.py`
- Modify: `seam/server/web.py` (import + call `register_layout_routes(app, db_path=..., root=...)` inside `create_web_app`, next to `register_graph_search_routes`)
- Test: `tests/server/test_web_layout.py`

**Interfaces:**
- Consumes: `seam.query.layout.compute_layout`; `seam.indexer.readonly.open_readonly_connection`; the `register_*_routes(app, *, db_path, root)` pattern from `seam/server/web_graph_search.py:42`.
- Produces: `register_layout_routes(app: FastAPI, *, db_path: Path, root: Path) -> None`; route `GET /api/graph/layout?max_nodes=N` → `LayoutResponse` Pydantic model `{nodes, edges, clusters, total_nodes}`.

- [ ] **Step 1: Write failing endpoint test**

```python
# tests/server/test_web_layout.py
import pytest
from fastapi.testclient import TestClient


def _client(tmp_path):
    from tests.query.test_layout import _make_index   # reuse the fixture builder
    from seam.server.web import create_web_app
    conn = _make_index(tmp_path); conn.close()
    app = create_web_app(db_path=tmp_path / "seam.db", root=tmp_path)
    return TestClient(app)


def test_layout_endpoint_returns_shape(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/graph/layout?max_nodes=100")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"nodes", "edges", "clusters", "total_nodes"}
    assert body["total_nodes"] >= 4


def test_layout_endpoint_clamps_max_nodes(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/graph/layout?max_nodes=1").json()["total_nodes"] >= 4
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/server/test_web_layout.py -q`
Expected: FAIL (404 — route not registered).

- [ ] **Step 3: Implement `web_layout.py`**

```python
# seam/server/web_layout.py
"""FastAPI route for the 3D constellation layout (Phase 11 P2.1).

Read-only. Mirrors the register_*_routes pattern used by web_graph_search.py /
web_architecture.py. Delegates all computation to seam.query.layout.
"""

from __future__ import annotations

from pathlib import Path

import sqlite3

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from seam import config
from seam.indexer.readonly import open_readonly_connection
from seam.query.layout import compute_layout


def _get_readonly_conn(db_path: Path) -> sqlite3.Connection:
    """CR2: mirror web_graph_search.py — Path (not str), NO_INDEX/DB_ERROR 503,
    plain connection the caller MUST close in a finally block."""
    if not db_path.exists():
        raise HTTPException(status_code=503, detail={"code": "NO_INDEX", "message": "run seam init"})
    try:
        return open_readonly_connection(db_path)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail={"code": "DB_ERROR", "message": str(exc)})


class LayoutNodeModel(BaseModel):
    id: int
    x: float
    y: float
    z: float
    label: str
    name: str
    file_path: str | None
    size: float
    color: str


class LayoutEdgeModel(BaseModel):
    source: int
    target: int
    type: str


class LayoutClusterModel(BaseModel):
    cluster_id: int
    label: str | None
    centroid: list[float]
    radius: float
    color: str


class LayoutResponse(BaseModel):
    """Response for GET /api/graph/layout."""

    nodes: list[LayoutNodeModel]
    edges: list[LayoutEdgeModel]
    clusters: list[LayoutClusterModel]
    total_nodes: int


def register_layout_routes(app: FastAPI, *, db_path: Path, root: Path) -> None:
    """Register GET /api/graph/layout on the given app."""

    @app.get("/api/graph/layout", response_model=LayoutResponse, tags=["graph"])
    def get_layout(
        max_nodes: int = Query(config.SEAM_LAYOUT_MAX_NODES, ge=1,
                               le=config.SEAM_LAYOUT_MAX_SAFE_NODES),  # CR5: safe ceiling
    ) -> LayoutResponse:
        """Whole-repo 3D constellation layout (server-computed positions)."""
        conn = _get_readonly_conn(db_path)   # CR2: Path, 503 on missing/broken index
        try:
            result = compute_layout(conn, max_nodes=max_nodes)
        finally:
            conn.close()                     # CR2: plain conn — must close explicitly
        return LayoutResponse(**result)
```

CR2: `open_readonly_connection(db_path: Path)` is a plain factory (takes a `Path`, calls
`.resolve()`, returns a `sqlite3.Connection` — NOT a context manager). The `_get_readonly_conn`
helper above matches `seam/server/web_graph_search.py:16-28` and restores the `NO_INDEX`/`DB_ERROR`
503 contract. **Add a test:** `GET /api/graph/layout` against a tmp dir with no db → assert 503.

- [ ] **Step 4: Wire into `create_web_app`**

In `seam/server/web.py`, near the existing `register_graph_search_routes(...)` call, add:

```python
from seam.server.web_layout import register_layout_routes
# ... inside create_web_app, after other register_*_routes calls:
register_layout_routes(app, db_path=db_path, root=root)
```

Match the exact `db_path`/`root` variable names used at that call site.

- [ ] **Step 5: Run endpoint tests to verify pass**

Run: `uv run pytest tests/server/test_web_layout.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run gate**

Run: `make gate`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add seam/server/web_layout.py seam/server/web.py tests/server/test_web_layout.py
git commit -m "feat(web): GET /api/graph/layout constellation endpoint"
```

---

## Frontend

> All frontend work is under `web/`. Build output goes to `seam/_web/` via `npm run build`.
> R3F canvas cannot render in jsdom (no WebGL) — component tasks unit-test the pure helpers
> and smoke-mount the shell; the 3D visuals are verified manually with `npm run dev` +
> `uv run seam serve` against this repo's own index.

### Task 3: Frontend deps + types + colors + data hook

**Files:**
- Modify: `web/package.json` (add deps)
- Create: `web/src/lib/layoutTypes.ts`, `web/src/lib/constellationColors.ts`, `web/src/hooks/useLayoutData.ts`
- Test: `web/src/__tests__/constellationColors.test.ts`

**Interfaces:**
- Produces: `GraphNode`, `GraphEdge`, `ClusterSummary`, `LayoutData` types; `EDGE_TYPE_COLORS`, `KIND_COLORS`, `stellarLegend`; `useLayoutData(maxNodes: number)` react-query hook returning `LayoutData`.

- [ ] **Step 1: Add deps**

Edit `web/package.json` dependencies (versions React-19-compatible, matching graph-ui):
```json
"@react-three/drei": "^10.7.0",
"@react-three/fiber": "^9.5.0",
"@react-three/postprocessing": "^3.0.4",
"three": "~0.183.0"
```
And devDependencies: `"@types/three": "~0.183.0"`.

Run: `cd /Users/jordicatafal/Documents/Github/seam/web && npm install`
Expected: installs without peer-dep errors (React 19 satisfied).

- [ ] **Step 2: Write failing color test**

```ts
// web/src/__tests__/constellationColors.test.ts
import { describe, it, expect } from "vitest";
import { EDGE_TYPE_COLORS, KIND_COLORS } from "../lib/constellationColors";

describe("constellation colors", () => {
  it("maps every edge kind", () => {
    for (const k of ["call","import","extends","implements","instantiates","holds","reads","writes","uses"])
      expect(EDGE_TYPE_COLORS[k]).toMatch(/^#[0-9a-f]{6}$/i);
  });
  it("call edge is seafoam teal", () => {
    expect(EDGE_TYPE_COLORS.call).toBe("#1DA27E");
  });
  it("maps every node kind", () => {
    for (const k of ["function","class","method","interface","type","field"])
      expect(KIND_COLORS[k]).toMatch(/^#[0-9a-f]{6}$/i);
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd /Users/jordicatafal/Documents/Github/seam/web && npm test -- constellationColors`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement types, colors, hook**

```ts
// web/src/lib/layoutTypes.ts
export type GraphNode = {
  id: number; x: number; y: number; z: number;
  label: string; name: string; file_path: string | null;
  size: number; color: string;
};
export type GraphEdge = { source: number; target: number; type: string };
export type ClusterSummary = {
  cluster_id: number; label: string | null;
  centroid: [number, number, number]; radius: number; color: string;
};
export type LayoutData = {
  nodes: GraphNode[]; edges: GraphEdge[];
  clusters: ClusterSummary[]; total_nodes: number;
};
```

```ts
// web/src/lib/constellationColors.ts  (Seam teal-native palette — reference §6/§7)
export const EDGE_TYPE_COLORS: Record<string, string> = {
  call: "#1DA27E", import: "#3b82f6", extends: "#a855f7", implements: "#8b5cf6",
  instantiates: "#f97316", holds: "#06b6d4", reads: "#22c55e", writes: "#ef4444",
  uses: "#eab308",
};
export const DEFAULT_EDGE_COLOR = "#1C8585";
export const KIND_COLORS: Record<string, string> = {
  class: "#a855f7", interface: "#8b5cf6", function: "#06b6d4",
  method: "#1DA27E", type: "#f97316", field: "#64748b",
};
export const DEFAULT_KIND_COLOR = "#94a3b8";
export const CANVAS_BG = "#04100f";     // teal-void
```

```ts
// web/src/hooks/useLayoutData.ts
import { useQuery } from "@tanstack/react-query";
import type { LayoutData } from "../lib/layoutTypes";

export const GRAPH_RENDER_NODE_LIMIT = 2000;

async function fetchLayout(maxNodes: number): Promise<LayoutData> {
  const res = await fetch(`/api/graph/layout?max_nodes=${maxNodes}`);
  if (!res.ok) throw new Error(`layout ${res.status}`);
  return res.json();
}

export function useLayoutData(maxNodes: number = GRAPH_RENDER_NODE_LIMIT) {
  return useQuery({
    queryKey: ["layout", maxNodes],
    queryFn: () => fetchLayout(maxNodes),
    staleTime: 60_000,
  });
}
```

- [ ] **Step 5: Run color test to verify pass**

Run: `npm test -- constellationColors`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/package.json web/package-lock.json web/src/lib/layoutTypes.ts web/src/lib/constellationColors.ts web/src/hooks/useLayoutData.ts web/src/__tests__/constellationColors.test.ts
git commit -m "feat(web): constellation deps, types, colors, layout data hook"
```

---

### Task 4: 3D scene core — `NodeCloud`, `EdgeLines`, `ConstellationScene`

**Files:**
- Create: `web/src/components/NodeCloud.tsx`, `web/src/components/EdgeLines.tsx`, `web/src/components/ConstellationScene.tsx`
- Test: `web/src/__tests__/cameraTarget.test.ts`

**Interfaces:**
- Consumes: `GraphNode`, `GraphEdge`, `EDGE_TYPE_COLORS`.
- Produces: `<ConstellationScene nodes edges clusters highlightedIds cameraTarget onHover onSelect />`; helper `computeCameraTarget(nodes, ids)` (pure, unit-tested).

- [ ] **Step 1: Write failing test for the pure camera helper**

```ts
// web/src/__tests__/cameraTarget.test.ts
import { describe, it, expect } from "vitest";
import { computeCameraTarget } from "../components/ConstellationScene";
import type { GraphNode } from "../lib/layoutTypes";

const n = (id: number, x: number, y: number, z: number): GraphNode => ({
  id, x, y, z, label: "function", name: `n${id}`, file_path: null, size: 4, color: "#fff",
});

describe("computeCameraTarget", () => {
  it("centers on the highlighted subset", () => {
    const nodes = [n(0, 0, 0, 0), n(1, 100, 0, 0), n(2, -100, 0, 0)];
    const t = computeCameraTarget(nodes, new Set([1, 2]));
    expect(t.lookAt).toEqual([0, 0, 0]);          // centroid of nodes 1,2
    expect(t.position[2]).toBeGreaterThan(0);      // camera pulled back on +z
  });
  it("returns null-ish default for empty set", () => {
    expect(computeCameraTarget([], new Set())).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- cameraTarget`
Expected: FAIL (module/function not found).

- [ ] **Step 3: Implement the three components**

Use the exact R3F/three values from `docs/prd/phase11-p2-1-3d-constellation-reference.md` §2. `NodeCloud` = `<instancedMesh>` with `sphereGeometry args={[1,32,24]}` + `meshBasicMaterial vertexColors toneMapped={false}`; write per-instance matrices + a color `Float32Array` in `useFrame`; color-boost `1.2 + brightness*0.8` for highlighted, `*0.15` for dimmed; register `onPointerOver/onPointerOut/onClick` reading `e.instanceId`. `EdgeLines` = `<lineSegments>` with `lineBasicMaterial vertexColors transparent blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false}`; build positions/colors `Float32Array` in `useMemo(..., [highlightedIds])`. `ConstellationScene` = `<Canvas dpr={[1,1.5]} gl={{antialias:false}} camera={{position:[0,0,800], fov:50, far:100000}} style={{background: CANVAS_BG}}>` with `<OrbitControls enableDamping dampingFactor={0.08}/>`, lights, `<EffectComposer multisampling={0}><Bloom luminanceThreshold={0.3} luminanceSmoothing={0.7} intensity={1.2} radius={0.6} mipmapBlur/></EffectComposer>`, a `CameraAnimator` (ease-out cubic `1-(1-p)^3`, `p+=0.02`, lerp `0.08`), and idle auto-rotate after 60s via a `lastInteraction` ref checked in `useFrame`.

Export the pure helper at top of `ConstellationScene.tsx`:

```ts
import type { GraphNode } from "../lib/layoutTypes";
export type CameraTarget = { position: [number, number, number]; lookAt: [number, number, number] };

export function computeCameraTarget(nodes: GraphNode[], ids: Set<number>): CameraTarget | null {
  const pts = nodes.filter((n) => ids.has(n.id));
  if (pts.length === 0) return null;
  const c: [number, number, number] = [
    pts.reduce((s, n) => s + n.x, 0) / pts.length,
    pts.reduce((s, n) => s + n.y, 0) / pts.length,
    pts.reduce((s, n) => s + n.z, 0) / pts.length,
  ];
  const spread = Math.max(60, ...pts.map((n) =>
    Math.hypot(n.x - c[0], n.y - c[1], n.z - c[2])));
  return { position: [c[0] + spread * 0.2, c[1] + spread * 0.15, c[2] + spread * 3], lookAt: c };
}
```

(The R3F component bodies are not exercised by jsdom tests; keep each component file < 300 lines. Split a component if it grows past that.)

- [ ] **Step 4: Run helper test to verify pass**

Run: `npm test -- cameraTarget`
Expected: PASS.

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: PASS (0 errors).

- [ ] **Step 6: Commit**

```bash
git add web/src/components/NodeCloud.tsx web/src/components/EdgeLines.tsx web/src/components/ConstellationScene.tsx web/src/__tests__/cameraTarget.test.ts
git commit -m "feat(web): 3D scene core — node cloud, edges, bloom, camera"
```

---

### Task 5: Visual layer — `ClusterHalos`, `NodeLabels`, `NodeTooltip`

**Files:**
- Create: `web/src/components/ClusterHalos.tsx`, `web/src/components/NodeLabels.tsx`, `web/src/components/NodeTooltip.tsx`
- Test: `web/src/__tests__/labelName.test.ts`

**Interfaces:**
- Consumes: `GraphNode`, `ClusterSummary`, `KIND_COLORS`.
- Produces: `<ClusterHalos clusters />`, `<NodeLabels nodes highlightedIds />`, `<NodeTooltip node />`; pure helper `bareName(qualified: string) -> string`.

- [ ] **Step 1: Write failing test for `bareName`**

```ts
// web/src/__tests__/labelName.test.ts
import { describe, it, expect } from "vitest";
import { bareName } from "../components/NodeLabels";
describe("bareName", () => {
  it("strips the container prefix", () => {
    expect(bareName("Client.send")).toBe("send");
    expect(bareName("main")).toBe("main");
    expect(bareName("A.B.method")).toBe("method");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- labelName`
Expected: FAIL.

- [ ] **Step 3: Implement the three components + helper**

`ClusterHalos` (Seam original) = one `<mesh>` per cluster at `centroid` with `<sphereGeometry args={[radius,16,16]}/>` + `<meshBasicMaterial color={cluster.color} transparent opacity={0.04} depthWrite={false} toneMapped={false}/>`. `NodeLabels` = canvas-sprite labels (reference §2 "Node Labels"), cap 80 sorted by size, `bareName` for display text, dispose `CanvasTexture` on cleanup. `NodeTooltip` = drei `<Html pointerEvents:none>` glass card showing `bareName`, full `name`, `file_path`, a `KIND_COLORS` dot.

```ts
// top of NodeLabels.tsx
export function bareName(qualified: string): string {
  const i = qualified.lastIndexOf(".");
  return i === -1 ? qualified : qualified.slice(i + 1);
}
```

- [ ] **Step 4: Run test to verify pass**

Run: `npm test -- labelName`
Expected: PASS.

- [ ] **Step 5: Typecheck + commit**

```bash
npm run typecheck
git add web/src/components/ClusterHalos.tsx web/src/components/NodeLabels.tsx web/src/components/NodeTooltip.tsx web/src/__tests__/labelName.test.ts
git commit -m "feat(web): cluster halos, sprite labels, hover tooltip"
```

---

### Task 6: UI shell — `FilterPanel`, `ConstellationHUD`, `NodeDetailPanel`, `ResizeHandle`

**Files:**
- Create: `web/src/components/FilterPanel.tsx`, `web/src/components/ConstellationHUD.tsx`, `web/src/components/NodeDetailPanel.tsx`, `web/src/components/ResizeHandle.tsx`
- Test: `web/src/__tests__/filterCounts.test.ts`

> Note: a `DetailPanel.tsx` already exists for the 2D view — do NOT modify it; the 3D
> `NodeDetailPanel.tsx` is separate and fetches `/api/symbol/{name}` via the existing
> `useSymbol` hook (`web/src/api/hooks.ts`).

**Interfaces:**
- Consumes: `GraphNode`, `GraphEdge`, `EDGE_TYPE_COLORS`, `KIND_COLORS`, existing `useSymbol` hook.
- Produces: `<FilterPanel data enabledKinds enabledEdges onToggle... />`, `<ConstellationHUD .../>`, `<NodeDetailPanel node onNavigate onClose />`, `<ResizeHandle side onResize />`; pure helper `countByField(nodes, field)`.

- [ ] **Step 1: Write failing test for `countByField`**

```ts
// web/src/__tests__/filterCounts.test.ts
import { describe, it, expect } from "vitest";
import { countByField } from "../components/FilterPanel";
import type { GraphNode } from "../lib/layoutTypes";
const mk = (kind: string): GraphNode =>
  ({ id: 0, x: 0, y: 0, z: 0, label: kind, name: "n", file_path: null, size: 4, color: "#fff" });
describe("countByField", () => {
  it("counts nodes by label", () => {
    const counts = countByField([mk("function"), mk("function"), mk("class")], "label");
    expect(counts).toEqual({ function: 2, class: 1 });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- filterCounts`
Expected: FAIL.

- [ ] **Step 3: Implement the four components + helper**

`FilterPanel` derives counts from the **raw** `data` (never the filtered set), all/none controls, 6 node-kind + 9 edge-kind chips (reference §5). `ConstellationHUD` = absolute `pointer-events-none` overlay: `{visible} nodes / {visible} edges`, filtered-from notice when `total_nodes > nodes.length`, selected count, a `max_nodes` selector (500/1000/2000/5000), and a freshness dot (green fresh / amber stale from `/api/status`). `NodeDetailPanel` fetches `useSymbol(node.name)` → callers/callees/cluster peers, each a `<button onClick={() => onNavigate(name)}>`. `ResizeHandle` = `setPointerCapture` drag reporting delta to the parent (parent persists to `localStorage` keys `seam-left-w`/`seam-right-w`, clamp [150,500]).

```ts
// top of FilterPanel.tsx
import type { GraphNode } from "../lib/layoutTypes";
export function countByField(nodes: GraphNode[], field: "label"): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of nodes) out[n[field]] = (out[n[field]] ?? 0) + 1;
  return out;
}
```

- [ ] **Step 4: Run test to verify pass**

Run: `npm test -- filterCounts`
Expected: PASS.

- [ ] **Step 5: Typecheck + commit**

```bash
npm run typecheck
git add web/src/components/FilterPanel.tsx web/src/components/ConstellationHUD.tsx web/src/components/NodeDetailPanel.tsx web/src/components/ResizeHandle.tsx web/src/__tests__/filterCounts.test.ts
git commit -m "feat(web): filter panel, HUD, detail panel, resize handle"
```

---

### Task 7: `ConstellationTab` + wire into `App.tsx` (lazy) + 2D↔3D sync

**Files:**
- Create: `web/src/components/ConstellationTab.tsx`
- Modify: `web/src/App.tsx` (add a lazy "Constellation" tab + shared `focusSymbol` state)
- Test: `web/src/__tests__/App.test.tsx` (extend — assert the new tab button renders)

**Interfaces:**
- Consumes: `useLayoutData`, `ConstellationScene`, `FilterPanel`, `ConstellationHUD`, `NodeDetailPanel`, `ResizeHandle`, `computeCameraTarget`.
- Produces: `<ConstellationTab focusSymbol onFocusSymbol />` — owns `selectedNode`, `highlightedIds`, `cameraTarget`, `filters`, `maxNodes`; the click state machine (select → highlight neighbors → fly camera → open panel).

- [ ] **Step 1: Write failing test — tab button present**

```tsx
// extend web/src/__tests__/App.test.tsx
it("shows the Constellation tab", async () => {
  render(<App />, { wrapper });   // reuse the file's existing wrapper
  expect(await screen.findByRole("button", { name: /constellation/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -- App`
Expected: FAIL (no such button).

- [ ] **Step 3: Implement `ConstellationTab` + wire the tab**

`ConstellationTab` composes the three-column shell: `<FilterPanel>` (top), left `<ResizeHandle>` + `<ConstellationScene>` (center, `flex-1`), right `<NodeDetailPanel>` (mounts only when `selectedNode`). Click handler: `setSelectedNode`; `highlightedIds = {id} ∪ direct neighbor ids from edges`; `setCameraTarget(computeCameraTarget(nodes, highlightedIds))`; calls `onFocusSymbol(node.name)` for 2D↔3D sync. In `App.tsx`: add a `focusSymbol` state lifted to the shell, add a lazy import `const ConstellationTab = lazy(() => import("./components/ConstellationTab"))` wrapped in `<Suspense fallback={...}>`, and a tab button (lucide `Orbit` icon) that switches `mode` to `"constellation"`. When `focusSymbol` is set from the 2D side, `ConstellationTab` flies to it; when set from 3D, the 2D `centerSymbol` follows.

- [ ] **Step 4: Run test to verify pass**

Run: `npm test -- App`
Expected: PASS.

- [ ] **Step 5: Full frontend check**

Run: `cd /Users/jordicatafal/Documents/Github/seam/web && npm run typecheck && npm test`
Expected: typecheck 0 errors; all vitest suites pass.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ConstellationTab.tsx web/src/App.tsx web/src/__tests__/App.test.tsx
git commit -m "feat(web): constellation tab + lazy wiring + 2D<->3D focus sync"
```

---

### Task 8: Regenerate types, build, manual visual verification, final gate

**Files:**
- Modify: `web/src/api/types.ts` (regenerated), `seam/_web/**` (built assets)

- [ ] **Step 1: Regenerate OpenAPI TS types**

Terminal A: `cd /Users/jordicatafal/Documents/Github/seam && uv run seam serve --no-open`
Terminal B: `cd web && npm run gen:types`
Expected: `src/api/types.ts` gains the `/api/graph/layout` path + `LayoutResponse` schema. Stop the server.

- [ ] **Step 2: Production build**

Run: `cd /Users/jordicatafal/Documents/Github/seam/web && npm run build`
Expected: `tsc --noEmit` clean + Vite build succeeds; assets emitted to `seam/_web/`. Confirm the R3F chunk is a **separate lazy chunk** (not in the main entry) in the Vite output summary.

- [ ] **Step 3: Manual visual verification (no Playwright for v1)**

Run: `uv run seam init` (ensure fresh index), then `uv run seam serve` (opens browser). Click the **Constellation** tab. Verify: a glowing star field renders with bloom; orbit drag works; hovering a star shows a tooltip; clicking highlights neighbors, flies the camera, and opens the detail panel; filter toggles change the visible set; cluster halos are faintly visible; the HUD shows counts. Note any issue and fix before proceeding.

- [ ] **Step 4: Final full gate**

Run: `cd /Users/jordicatafal/Documents/Github/seam && make gate && cd web && npm run typecheck && npm test`
Expected: all green.

- [ ] **Step 5: Commit built assets + types**

```bash
cd /Users/jordicatafal/Documents/Github/seam
git add web/src/api/types.ts seam/_web
git commit -m "chore(web): regenerate layout API types + build constellation assets"
```

- [ ] **Step 6: Update docs**

Add a one-paragraph note to `README.md` (Explorer section) and to `CLAUDE.md` (a P2.1 phase entry + a Known Gotcha: numpy required for `[web]`, layout is degree-capped at `max_nodes`, positions are deterministic and cached per `(db_mtime, max_nodes)`). Commit:

```bash
git add README.md CLAUDE.md
git commit -m "docs: constellation Explorer tab (Phase 11 P2.1)"
```

---

## Self-Review

**Spec coverage:** server-side numpy layout → Task 1; `/api/graph/layout` endpoint → Task 2; R3F scene/bloom/orbit → Task 4; cluster halos → Task 5; filters/HUD/detail/resize → Task 6; lazy tab + 2D↔3D sync → Task 7; teal-native palette → Task 3; determinism → Task 1 tests; no scipy/C/Playwright → Global Constraints + Task 8 Step 3; gen:types → Task 8; freshness dot → Task 6. All spec sections map to a task.

**Placeholder scan:** no TBD/TODO; each code step shows real code or exact commands. R3F component bodies reference the committed in-repo reference doc for verbatim constants (an in-repo artifact, not a placeholder) but each still specifies exact props/values inline.

**Type consistency:** `LayoutNode/Edge/Cluster/Result` (Python TypedDicts) mirror `Layout*Model` (Pydantic) mirror `GraphNode/GraphEdge/ClusterSummary/LayoutData` (TS). `compute_layout(conn, *, max_nodes)`, `register_layout_routes(app, *, db_path, root)`, `useLayoutData(maxNodes)`, `computeCameraTarget(nodes, ids)`, `bareName`, `countByField` are referenced consistently across tasks.

## Risks / Verify-at-execution

- `list_entry_points` return shape (dict vs object) — verify at Task 1 Step 8; `_bfs_depth` degrades to all-zero depth if it can't read names (still valid).
- `open_readonly_connection` usage (context manager vs factory) — match existing `web.py` usage at Task 2 Step 3.
- numpy O(n²) memory at `max_nodes=10000` → an `(n,n,3)` float64 array ≈ 2.4 GB. Guard: the endpoint's default is 2000; consider capping the pairwise path at ~4000 and documenting that 5000+ is a follow-on (chunked/knn) if manual testing shows memory pressure.
- `@react-three/*` peer-deps vs React 19 — verify at Task 3 Step 1; these are the same versions graph-ui ships on React 19.
