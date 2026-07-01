# Design — Phase 11 P2.1: 3D Constellation Explorer

> Status: approved (design gate passed 2026-07-01).
> Roadmap: `docs/prd/phase11-codebase-memory-roadmap.md` §P2.1.
> Modeling reference (deep study of DeusData/codebase-memory-mcp graph-ui):
> `docs/prd/phase11-p2-1-3d-constellation-reference.md`.
> Blast radius: additive only — a new lazy-loaded web tab + one new read-only backend
> endpoint. The MCP/CLI core, the 2D React Flow canvas, and all existing routes are untouched.

## Goal & Framing

A visually striking 3D "constellation" view of the indexed codebase — glowing stars (symbols)
colored by connectivity, floating in clustered galaxies, with bloom, orbit, and hover/click
inspection. It is a **new complementary Explorer tab**, not a replacement for the precise 2D
React Flow canvas.

**Explicit priority calibration (from the user):** Seam's daily product is the MCP + CLI
surface for agents. This 3D view exists for **distribution and good looks** — a demo/README
"wow" that drives installs. Therefore it is built **lean, not gold-plated**: minimal deps,
minimal test ceremony, fully additive, zero risk to the core. Make it dope, ship it, don't
over-invest.

## Key Decisions (locked)

1. **Renderer:** React-Three-Fiber (`@react-three/fiber` + `drei` + `postprocessing` + `three`),
   modeling graph-ui's techniques faithfully. React-19 compatible versions.
2. **Layout compute:** **server-side, Python + numpy only.** Reimplements graph-ui's
   ForceAtlas2 + anchor-spring + ring-seed algorithm. **No scipy, no C.** Rationale: numpy's
   vectorized ops are compiled C/SIMD, so the O(n²) force math runs at C speed without shipping
   a C extension (keeps pure-`pip` install). The layout runs once per session and is cached, so
   even the residual cost is invisible. graph-ui's own layout is hand-written C (`src/ui/layout3d.c`,
   Barnes-Hut octree) — not a library — so we model the *algorithm*, not the language.
3. **Cluster visual:** flat starfield **+ translucent per-cluster "halo" spheres** (a Seam
   original graph-ui does not have).
4. **Aesthetic:** Seam teal-native — canvas void `#04100f`, seafoam accent `#1DA27E`, stellar
   node colors (red-dwarf→blue-giant by degree) kept.
5. **Scope:** full-fat first build (starfield + bloom + orbit + hover/click + detail panel +
   filters + cluster halos + camera fly-to + panel persistence + 2D↔3D selection sync).
6. **Testing ceremony:** unit-test pure logic only (pytest for layout determinism; vitest for
   color/filter/camera math). **No Playwright** — canvas verified manually for v1.

## Architecture

```
web/src (React 19 + R3F, lazy-loaded tab)        seam/server + seam/query (FastAPI, [web] extra)
  ConstellationTab ──GET /api/graph/layout──────►  layout endpoint ──► seam/query/layout.py
  NodeDetailPanel  ──GET /api/symbol/{name}─────►  (existing route, reused)
```

Purely additive. No schema migration, no watcher change, no MCP tool change, no re-index.

## Backend

### `seam/query/layout.py` (new leaf module — pure, deterministic, never raises)

`compute_layout(conn, *, max_nodes=2000) -> LayoutResult`. Pipeline (ported from the reference):

1. **Node selection:** top-`max_nodes` symbols by degree DESC (most-connected always visible
   when capped). Degree computed from the `edges` table (undirected count).
2. **Edge filtering:** keep edges with both endpoints in the selected set.
3. **BFS call-depth:** seed depth-0 from `seam/analysis/processes.list_entry_points()`; BFS along
   `call` edges → `z = -depth * 50`.
4. **Ring seed positions:** FNV-1a hash of the file-path cluster key (first 3 path components)
   → angle + radius (500–750); per-node LCG jitter from the qualified name. **Fully
   deterministic — no `random`** (so tests assert stable positions).
5. **ForceAtlas2, 40 iterations, numpy-vectorized:** repulsion `kr=8.0` (O(n²) pairwise matrix,
   compiled numpy), edge attraction `ka=1.0`, anchor spring to seed `k_anchor=0.25*mass`,
   displacement cap 8 units/iter. Constants match the reference (`BH_THETA` concept dropped —
   O(n²) needs no octree at n≤2000).
6. **Stellar color** by degree (10-band scale) + **size** by kind+degree.
7. **Cluster summary:** post-layout centroid + radius per `cluster_id` (from the `clusters`/
   symbol cluster data) for the halos.

Cache: module-level dict keyed by `(db_mtime, max_nodes)`, TTL = `SEAM_STALENESS_TTL_SECONDS`.
Attaches the standard `index_status` staleness banner when stale (serves cached layout — stale
positions beat no positions). If the file approaches the 1000-line limit, the force kernel splits
into `seam/query/layout_forces.py`.

**New dep:** `numpy` added to the `[web]` optional extra only. Base install unchanged.

### `GET /api/graph/layout?max_nodes=N` (new route in `seam/server/web.py`)

Returns (Pydantic models → OpenAPI → generated TS types via existing `gen:types`):

```json
{
  "nodes":  [{"id": 42, "x": 312.4, "y": -88.2, "z": -150.0, "label": "function",
              "name": "parse_file", "file_path": "seam/indexer/parser.py",
              "size": 4.0, "color": "#ffe080"}],
  "edges":  [{"source": 42, "target": 107, "type": "call"}],
  "clusters": [{"cluster_id": 3, "label": "indexer", "centroid": [x,y,z],
                "radius": 220.0, "color": "#..."}],
  "total_nodes": 5488
}
```

Error contract follows existing conventions: `NO_INDEX`, `DB_ERROR`; `[web]` extra absent →
existing lazy-import guard.

## Frontend (`web/src/`)

New deps (React-19-compatible, matching graph-ui's versions): `three ~0.183`,
`@react-three/fiber ^9`, `@react-three/drei ^10`, `@react-three/postprocessing ^3`.
Existing React Flow / react-query / Tailwind unchanged.

New **"Constellation"** tab in `App.tsx`, **lazy-loaded** (`React.lazy` — the R3F bundle must
not enter the main chunk).

| File | Role |
|---|---|
| `components/ConstellationTab.tsx` | State machine: `selectedNode`, `highlightedIds`, `cameraTarget`, `filters`, `maxNodes`; fetches `useLayoutData` |
| `components/ConstellationScene.tsx` | `<Canvas>` (`dpr [1,1.5]`, `antialias off`, bg `#04100f`), OrbitControls (idle auto-rotate after 60s), `<Bloom threshold=.3 intensity=1.2 radius=.6 mipmapBlur>`, lights, `CameraAnimator` fly-to (ease-out cubic) |
| `components/NodeCloud.tsx` | InstancedMesh — all nodes, one draw call, per-instance color/scale, color-boost>1.0→bloom, native `instanceId` raycast (hover/click) |
| `components/EdgeLines.tsx` | LineSegments, `AdditiveBlending + depthWrite=false + toneMapped=false`, intensity by highlight/cluster |
| `components/ClusterHalos.tsx` | **Seam original** — translucent spheres at cluster centroids, opacity 0.03–0.05, cluster color |
| `components/NodeLabels.tsx` | Canvas-sprite labels, **bare name** (strip `Class.` prefix, keep qualified in tooltip), cap 80 by degree |
| `components/NodeTooltip.tsx` | drei `Html`, `pointerEvents:none` |
| `components/NodeDetailPanel.tsx` | Populated from `/api/symbol/{name}`; clickable callers/callees → `onNavigate` re-runs click handler |
| `components/FilterPanel.tsx` | 6 node kinds + 9 edge kinds, counts from **raw** data, all/none |
| `components/ConstellationHUD.tsx` | visible/filtered/selected counts + `max_nodes` selector + freshness dot (green fresh / amber stale) |
| `components/ResizeHandle.tsx` | panel resize, `localStorage` persist (`seam-left-w`/`seam-right-w`, clamp [150,500]) |
| `hooks/useLayoutData.ts` | react-query fetch `/api/graph/layout` |
| `lib/constellationColors.ts` | stellar scale, `KIND_COLORS`, `EDGE_TYPE_COLORS` (teal-native) |
| `lib/layoutTypes.ts` | `GraphNode`, `GraphEdge`, `ClusterSummary`, `GraphData` |

### 2D↔3D sync

Lift a shared `focusSymbol` into `App.tsx`: selecting a star sets it (2D tab centers on it);
selecting in 2D flies the 3D camera to it. URL `?symbol=` deep-link is a cheap follow-on.

### Interaction/render values (copied verbatim from the reference)

Bloom `threshold 0.3 / smoothing 0.7 / intensity 1.2 / radius 0.6 / mipmapBlur`;
`multisampling 0`; OrbitControls `damping 0.08 / rotate 0.5 / zoom 1.5`; fly-to
`t = 1-(1-p)^3`, lerp `0.08`, `p += 0.02`; label cap 80; `frustumCulled=false` on mesh+sprites.

## Data Flow

1. Tab mounts → `useLayoutData` → `GET /api/graph/layout?max_nodes=2000`.
2. `compute_layout` cache hit (or ~1–3s compute, then cached) → `{nodes, edges, clusters,
   total_nodes}`.
3. `ConstellationScene` renders InstancedMesh + LineSegments + halos + labels; Bloom pass glows.
4. Hover → tooltip; click → highlight neighbors, fly camera, open `NodeDetailPanel` (fetches
   `/api/symbol/{name}`).
5. Filters toggle node/edge kinds client-side (counts from raw data); HUD reflects visible set.

## Error Handling

- Layout module never raises → on any internal failure returns an empty-but-valid result +
  warning; endpoint maps to existing error codes.
- Empty index → valid empty payload (no crash).
- Stale index → `index_status` banner + cached layout.
- Frontend: react-query error state → inline "could not load constellation" (mirrors existing
  `StatusBadge` error pattern); canvas never mounts on error.

## Testing

- **pytest (`tests/query/test_layout.py`):** determinism (same input → identical positions),
  degree-ordered selection, edge filtering, cluster centroids, empty index, `max_nodes` cap,
  `total_nodes` honesty. Fully offline.
- **pytest (web):** `/api/graph/layout` shape + error mapping (extends existing web API test
  family).
- **vitest:** pure helpers — stellar color mapping, filter counts, camera-target math, label
  bare-name strip. R3F canvas is smoke-mounted only (jsdom can't render WebGL).
- **No Playwright** for v1 — canvas verified manually.
- Update tool-count / contract expectations only if any doc test asserts them (no new MCP tool,
  so count stays 15).

## Out of Scope

- No new MCP tool (this is web-only; count stays 15).
- No schema migration, no watcher change, no re-index.
- No scipy, no C extension, no Playwright.
- No multi-project / satellite-galaxy UI (Seam is single-project per server).
- No replacement of the 2D React Flow canvas.
- No route/config/test edges (those are P3; the 9 existing edge kinds are what's rendered).

## Open Follow-ons (not blocking v1)

- Client-side re-layout on filter change (currently the server layout is fixed per `max_nodes`).
- Level-of-detail geometry if profiling shows >8ms frames above ~5k nodes.
- URL deep-linking (`?symbol=`).
- Playwright visual-regression job once the surface stabilizes.
