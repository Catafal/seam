# PRD: Phase 11 P2.1 — 3D Constellation Explorer

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P2.1.
> Competitive source: `DeusData/codebase-memory-mcp` ships a full-bleed 3D graph UI (`graph-ui`).
> Status: ready-for-agent.
> Design spec: `docs/superpowers/specs/2026-07-01-3d-constellation-explorer-design.md`.
> Implementation plan: `docs/superpowers/plans/2026-07-01-3d-constellation-explorer.md`.
> Modeling reference (deep study of graph-ui): `docs/prd/phase11-p2-1-3d-constellation-reference.md`.
> Schema target: no migration. Read-only, additive web surface over the current index.

## Problem Statement

Seam is agentic-first: the daily product is the MCP + CLI surface. But adoption for a local
developer tool is driven substantially by first impression — a human evaluating Seam opens
`seam serve` and decides in seconds whether this tool is worth installing. Today the Explorer is a
capable but utilitarian 2D React Flow canvas. It answers precise questions well (neighborhood,
impact, trace) but it does not produce the "wow" that makes a developer share a screenshot, star the
repo, or run `seam install`. The competing `codebase-memory-mcp` project ships a striking full-bleed
3D graph — glowing stars, bloom, orbiting galaxies — and that visual is a material part of its
distribution story.

From the user's perspective:

- a developer evaluating Seam has no visually compelling whole-repo overview — the 2D canvas is
  precise but not memorable;
- there is no single "show me the shape of this codebase" view that reads as impressive at a glance;
- the existing 2D view is a debugging instrument, not an orientation or demo surface;
- Seam has all the indexed evidence (symbols, edges, clusters, degrees) to render a beautiful
  constellation, but exposes none of it as a 3D layout;
- the distribution/marketing surface (README screenshot, demo) has nothing that competes with
  `codebase-memory-mcp`'s 3D UI.

Seam needs a visually striking 3D constellation overview that complements — never replaces — the
precise 2D workflow, built lean because it is a distribution/looks surface rather than the daily
MCP/CLI product.

## Solution

Add a new lazy-loaded **Constellation** tab to the Seam Explorer that renders the indexed codebase
as a glowing 3D star field:

- each symbol is a star, colored by connectivity on a physically-motivated stellar scale
  (red-dwarf → blue-giant by degree), sized by kind and degree;
- edges are additive-blended filaments; overlapping edges accumulate into nebula-like brightness;
- a post-processing bloom pass turns bright stars into glowing coronas;
- functional-area clusters are surrounded by faint translucent "halo" spheres — a Seam-original
  touch that groups stars into galaxies;
- orbit controls with idle auto-rotation; hover shows a tooltip; clicking a star highlights its
  neighbors, flies the camera to it, and opens a detail panel populated from the existing
  `/api/symbol/{name}` route;
- a filter panel (6 node kinds + 9 edge kinds) and a HUD (visible/filtered/selected counts, a
  node-count selector, and an index-freshness dot) frame the scene;
- selecting a symbol in 3D focuses it in the 2D tab and vice versa.

The 3D positions are computed server-side by a new read-only `/api/graph/layout` endpoint backed by
a deterministic numpy ForceAtlas2 layout. The frontend is a pure renderer. The MCP/CLI core, the 2D
React Flow canvas, and the existing 2D `/api/constellation` cluster-overview are untouched. No schema
migration, no watcher change, no re-index, no new MCP tool.

The intended human workflow: open `seam serve` → land on or switch to the Constellation tab → orbit
the galaxy → click an interesting hub → inspect it → jump to the precise 2D canvas for real
debugging.

## User Stories

1. As a developer evaluating Seam, I want a visually striking 3D overview of my codebase, so that I
   immediately understand this tool is worth installing.
2. As a developer, I want the constellation to open in its own Explorer tab, so that the precise 2D
   canvas remains available for debugging.
3. As a developer, I want stars colored by connectivity, so that hubs and leaf code are visually
   distinguishable at a glance.
4. As a developer, I want highly-connected symbols to appear as brighter, larger stars, so that the
   architecture's centers of gravity are obvious.
5. As a developer, I want edges rendered as glowing filaments, so that dense coupling reads as
   nebula-like brightness.
6. As a developer, I want a bloom glow on bright stars, so that the view feels like a real star field
   rather than a flat graph.
7. As a developer, I want functional clusters wrapped in faint halo spheres, so that I can see
   logical groupings as galaxies.
8. As a developer, I want to orbit the scene with my mouse, so that I can explore the graph in 3D.
9. As a developer, I want the scene to idly auto-rotate after inactivity, so that it looks alive in a
   demo or on a second monitor.
10. As a developer, I want hovering a star to show a tooltip with its name, file, and kind, so that I
    can identify code without clicking.
11. As a developer, I want clicking a star to highlight its direct neighbors, so that I can see what
    it connects to.
12. As a developer, I want clicking a star to fly the camera to it, so that I can focus on a region
    of interest.
13. As a developer, I want a detail panel for the selected symbol with its callers and callees, so
    that I can inspect it without leaving the 3D view.
14. As a developer, I want detail-panel caller/callee rows to be clickable, so that I can walk the
    graph star to star.
15. As a developer, I want to filter by symbol kind (function, class, method, interface, type,
    field), so that I can reduce visual noise.
16. As a developer, I want to filter by edge kind (call, import, extends, implements, instantiates,
    holds, reads, writes, uses), so that I can isolate a relationship type.
17. As a developer, I want all/none filter controls with per-kind counts, so that I can toggle the
    view quickly and see how much each kind contributes.
18. As a developer, I want a HUD showing visible node/edge/selected counts, so that I know how much
    of the graph I am seeing.
19. As a developer, I want a "showing N of M" notice when the graph is capped, so that I am not
    misled into thinking the repo is smaller than it is.
20. As a developer, I want a node-count selector (e.g. 500/1000/2000/3000), so that I can trade
    visual density for completeness.
21. As a developer, I want an index-freshness indicator in the HUD, so that I know whether the
    constellation reflects my latest code.
22. As a developer, I want resizable side panels whose widths persist, so that my layout is
    remembered between sessions.
23. As a developer, I want the star labels to show the bare symbol name, so that qualified method
    names do not clutter the field.
24. As a developer, I want the full qualified name available on hover, so that I can disambiguate
    homonyms when needed.
25. As a developer, I want selecting a symbol in the 3D tab to focus it in the 2D tab, so that I can
    switch to precise debugging on the same target.
26. As a developer, I want selecting a symbol in the 2D tab to fly the 3D camera to it, so that the
    two views stay in sync.
27. As a developer, I want the 3D view to load quickly on repeated visits, so that switching tabs is
    not slow.
28. As a developer on a large repo, I want the view to cap the rendered node count safely, so that
    the browser and server stay responsive.
29. As a developer, I want the most-connected symbols shown first when the graph is capped, so that
    the important structure is always visible.
30. As a developer, I want an honest error message if the layout fails to load, so that I am not left
    staring at a silent blank canvas.
31. As an AI coding agent, I want a read-only `/api/graph/layout` endpoint, so that any future
    tooling can retrieve a positioned whole-repo graph.
32. As an AI coding agent, I want the layout endpoint to report the true total node count, so that I
    know when the returned set is capped.
33. As a Seam maintainer, I want one deep, testable layout module, so that positioning logic lives in
    a single isolated place rather than smeared across the transport.
34. As a Seam maintainer, I want the layout to be deterministic, so that tests can assert stable
    positions and agents can compare snapshots.
35. As a Seam maintainer, I want the layout computed with numpy (not scipy, not a C extension), so
    that the base install stays pure-pip and the read path stays zero-network.
36. As a Seam maintainer, I want the layout cached, so that the O(n²) force computation is not re-run
    on every request.
37. As a Seam maintainer, I want operational knobs (node cap, safe ceiling, cache TTL) in
    `seam/config.py`, so that the codebase's config convention is respected.
38. As a Seam maintainer, I want the endpoint to reuse the existing readonly-connection helper and
    `NO_INDEX`/`DB_ERROR` 503 contract, so that error behavior matches the sibling route modules.
39. As a Seam maintainer, I want the layout to bridge Seam's qualified↔bare name asymmetry, so that
    methods are correctly connected rather than rendered as isolated stars.
40. As a Seam maintainer, I want the 3D tab lazy-loaded, so that the heavy Three.js bundle never
    bloats the main Explorer chunk.
41. As a Seam maintainer, I want the frontend's pure logic extracted from the WebGL components, so
    that the visual layer has real unit-test coverage despite jsdom lacking WebGL.
42. As a Seam maintainer, I want the layout to never raise, so that a malformed row degrades to an
    empty view instead of a 500.
43. As a Seam maintainer, I want the new endpoint's Pydantic models to feed the existing
    OpenAPI→TypeScript type generation, so that the frontend stays typed.
44. As a Seam maintainer, I want the backend layout models named `Layout*` and the frontend types
    named `Layout*`, so that they do not collide with the existing 2D `graph_api` `GraphNode`.
45. As a Seam maintainer, I want the 3D view to depend only on data already in SQLite, so that the
    zero-external-services guarantee holds.
46. As a future 3D-constellation contributor, I want cluster centroids/radii exposed by the layout
    endpoint, so that the halo rendering has a stable data source.
47. As a future contributor, I want the render constants (bloom, orbit, camera) documented from the
    modeling reference, so that the look can be tuned without re-deriving values.
48. As a demoing developer, I want a distinctly Seam-branded aesthetic (teal-void, seafoam accent),
    so that the view is recognizable as Seam and not a clone of the competitor.

## Implementation Decisions

- **New deep module `seam/query/layout.py`** with a small, stable interface:
  `compute_layout(conn, *, max_nodes) -> LayoutResult`. It encapsulates the full pipeline (node
  selection by degree, qualified↔bare name bridging, edge filtering, BFS call-depth, deterministic
  ring seeding, numpy ForceAtlas2, stellar coloring, size, cluster centroids) behind one function.
  Pure, deterministic, never raises. This is the module under test.
- **Layout is computed server-side in Python + numpy only** (no scipy, no C extension). numpy's
  vectorized ops run the O(n²) force math at compiled-C speed while keeping the base install
  pure-pip. The competitor's layout is hand-written C (Barnes-Hut octree); Seam models the algorithm
  (ForceAtlas2 + anchor springs + ring seeding), not the language.
- **Determinism** is a hard requirement — FNV-1a/LCG seeding, no randomness — so positions are
  stable across runs and testable.
- **Qualified↔bare name bridging** reuses the existing `seam/query/names.py` leaf
  (`edge_match_names`). Edges store bare names (`send`); symbols store qualified names
  (`Client.send`). Without bridging, methods would render as isolated stars — a correctness bug, not
  a cosmetic one.
- **Caching** is a module-level bounded dict keyed on `(MAX(indexed_at), max_nodes)` with TTL from
  the existing `SEAM_STALENESS_TTL_SECONDS`, so the expensive kernel is not re-run per request.
- **Config knobs** (new): `SEAM_LAYOUT_MAX_NODES` (default render cap, 2000) and
  `SEAM_LAYOUT_MAX_SAFE_NODES` (memory ceiling, 3000). Algorithm constants (repulsion, iterations,
  etc.) stay module-local, matching the `clustering.py`/`rwr.py` leaf discipline. A hard ceiling
  prevents the numpy `(n,n,3)` array from OOMing the local server at large node counts.
- **New route module `seam/server/web_layout.py`** exposing `GET /api/graph/layout?max_nodes=N`,
  following the existing `register_*_routes(app, *, db_path, root)` pattern used by
  `web_graph_search.py` / `web_architecture.py`. It reuses the readonly-connection helper and the
  `NO_INDEX`/`DB_ERROR` 503 contract; it does not open connections ad hoc.
- **Response contract** (Pydantic `Layout*` models → OpenAPI → generated TS):
  `{ nodes: [{id,x,y,z,label,name,file_path,size,color}], edges: [{source,target,type}],
  clusters: [{cluster_id,label,centroid,radius,color}], total_nodes }`. Endpoints and models use the
  `Layout*` name space to avoid collision with the existing 2D `/api/constellation`.
- **Naming:** the backend endpoint is `/api/graph/layout` and the 2D cluster-overview
  `/api/constellation` is untouched; frontend uses the `Constellation*` component prefix.
- **Frontend** is a new **lazy-loaded** Constellation tab (React-Three-Fiber + drei + postprocessing
  + three, React-19-compatible versions). Render components (scene, node cloud via `InstancedMesh`,
  additive edges, cluster halos, sprite labels, tooltip) plus a UI shell (filter panel, HUD, detail
  panel, resize handle) plus a state-machine tab component. The detail panel reuses the existing
  `/api/symbol/{name}` route — no query duplication.
- **Aesthetic:** Seam teal-native (canvas void `#04100f`, seafoam `#1DA27E` accent, stellar node
  colors kept), distinct from the competitor's teal-black theme. Techniques copied verbatim from the
  modeling reference: color-boost-above-1.0 → bloom, additive-blended edges, `dpr [1,1.5]`,
  `antialias off`, bloom `threshold 0.3 / intensity 1.2 / radius 0.6 / mipmapBlur`, OrbitControls
  `damping 0.08`, ease-out-cubic camera fly-to.
- **2D↔3D selection sync** via a shared `focusSymbol` state lifted into the Explorer shell.
- **Staleness banner** (`index_status`) is intentionally omitted from the layout endpoint — the HUD
  freshness dot (backed by `/api/status`) already covers the user-facing need for a cosmetic surface.
  Recorded as an explicit decision.
- **Out of the box: no new MCP tool, no schema migration, no watcher change, no re-index.** numpy is
  added to the `[web]` optional extra only; the base install is unchanged.

## Testing Decisions

- Good tests assert external behavior — response shape, deterministic ordering, correct
  connectivity, warnings/caps, error mapping — not internal SQL strings or numpy call sequences.
- **`seam/query/layout.py`** is the primary test target (deep module). Cover: determinism (same
  input → identical positions, plus a golden rounded-coordinate assertion); degree-ordered node
  selection; the honest `total_nodes` above the cap; the `max_nodes` cap; the empty index; and the
  edge cases the fixture is deliberately shaped to exercise — a qualified member (`Client.send`)
  getting non-zero degree via the bare edge name (the correctness fix), self-edge rejection, homonym
  collapse to the min-id representative, a NULL `cluster_id` node, a single-member cluster's radius
  fallback, and a malformed row (NULL name) degrading to an empty result rather than raising.
- **`seam/server/web_layout.py`** endpoint tests: response shape parity with the module, `max_nodes`
  clamping, and a no-index directory returning 503 `NO_INDEX`.
- **Frontend** tests target the pure helpers extracted from the WebGL components (which jsdom cannot
  render): stellar/edge color mapping, `computeCameraTarget`, `bareName`, `countByField`,
  `buildEdgeGeometry`, `computeInstanceColor`, `easeOutCubic`, `selectLabelNodes`, and the
  react-query error branch (mock `fetch → 500` → visible error, not a blank canvas). R3F components
  are smoke-mounted only.
- **Visual acceptance** is manual for v1 (no Playwright) — verified with `seam serve` against this
  repo's own index — consistent with the "lean, cosmetic surface" scope.
- Prior art: the web API test family (`tests/server/test_web_*`), the analysis-leaf test families
  (`tests/query/`, `tests/analysis/`), and the existing vitest suites under `web/src/__tests__/`.

## Out of Scope

- No new database schema migration.
- No new MCP tool (MCP tool count is unchanged; this is a web-only surface).
- No replacement of the 2D React Flow canvas or the existing 2D `/api/constellation`.
- No scipy, no C extension, no Playwright in the base gate.
- No client-side re-layout on filter change (server layout is fixed per `max_nodes`) — a follow-on.
- No level-of-detail geometry beyond the node cap — a follow-on if profiling shows frame pressure.
- No route/config/test edges (those are P3); the 9 existing edge kinds are what is rendered.
- No multi-project / satellite-galaxy UI (Seam is single-project per server).
- No staleness `index_status` banner on the layout endpoint (HUD freshness dot covers it).

## Further Notes

This feature is explicitly a distribution/looks investment, not a change to Seam's daily product,
which remains the MCP + CLI surface for agents. It is therefore built lean: minimal dependencies
(numpy + four frontend packages), minimal test ceremony (pure-logic unit tests + manual visual
verification), fully additive, and zero risk to the core.

The layout algorithm, exact render constants (bloom/orbit/camera), stellar color scale, and
component structure were reverse-engineered from a deep multi-agent study of the competitor's
`graph-ui` and captured in `docs/prd/phase11-p2-1-3d-constellation-reference.md`. The implementation
plan (`docs/superpowers/plans/2026-07-01-3d-constellation-explorer.md`) is an 8-task TDD breakdown
that has already passed an automated plan review; six critical corrections (name bridging,
connection handling, test-fixture schema, caching, memory ceiling, config knobs) are folded into the
plan.
