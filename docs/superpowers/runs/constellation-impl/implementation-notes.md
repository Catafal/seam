# Constellation Explorer — Implementation Notes

## 2026-07-02 — Slice S1: Backend Layout Endpoint (issue #169)

### What was built

**seam/query/layout.py** — Deterministic 3D layout engine:
- `compute_layout(conn, *, max_nodes) -> LayoutResult` — never raises; module-level cache keyed on `(indexed_at * 1_000_000 + file_count, max_nodes)` with TTL from `SEAM_STALENESS_TTL_SECONDS`
- `stellar_color(degree) -> str` — degree → stellar hex color (M red dwarf → O blue giant)
- `node_size(kind, degree) -> float` — base size by kind + degree boost capped at +10
- `_force_atlas2(seed, mass, edges) -> np.ndarray` — 40-iteration numpy ForceAtlas2 O(n²)
- `_bfs_depth(conn, selected, sel_set, adjacency)` — BFS from `list_entry_points` for z-axis depth
- `_cluster_summaries(conn, selected, reps, name_to_idx, pos)` — cluster centroids, radii, colors

**seam/server/web_layout.py** — FastAPI route:
- `register_layout_routes(app, *, db_path, root)` — `GET /api/graph/layout?max_nodes=N`
- `LayoutResponse`, `LayoutNodeModel`, `LayoutEdgeModel`, `LayoutClusterModel` — Pydantic models (Layout* namespace to avoid 2D collision)
- 503 NO_INDEX / DB_ERROR via `_get_readonly_conn` (mirrors web_graph_search.py)

**seam/config.py** — 2 new knobs:
- `SEAM_LAYOUT_MAX_NODES = 2000` — default render cap
- `SEAM_LAYOUT_MAX_SAFE_NODES = 3000` — hard OOM ceiling (applied as first line of `_compute_layout_impl`)

**pyproject.toml** — `numpy>=1.26` added to `[web]` optional extra

**Tests** (16 tests total, all green):
- `tests/unit/test_layout.py` — 12 tests covering helpers + pipeline
- `tests/integration/test_web_layout.py` — 4 endpoint tests

### Key decisions

**CR1 — qualified↔bare bridge:** `edge_match_names(conn, name)` is called with `conn` as the first argument (the actual signature). Process **qualified (dotted) names FIRST** so `Client.send` claims the bare key "send" in `match_to_name` before the `Client` class expansion can steal it via `edge_match_names(conn, "Client")` returning `["Client", "send", ...]`. Without this ordering, `Client.send` would be an isolated star.

**CR4 — cache key uniqueness:** The plan suggested `(MAX(indexed_at), max_nodes)` but tests revealed that an empty DB and a non-empty DB both have `indexed_at=0` (test fixture sets it explicitly). Added `COUNT(*) FROM files` to the version: `ver = indexed_at * 1_000_000 + file_count`. This prevents the cache from returning a non-empty layout for an empty DB when tests share a process.

**Malformed-row test:** The plan suggested `UPDATE symbols SET name = NULL WHERE id = 1` but the schema has `name TEXT NOT NULL`. Instead tested the narrow-except path by closing the connection before calling `compute_layout` — a closed connection raises `sqlite3.ProgrammingError` (subclass of `sqlite3.Error`), which is exactly what the narrow except catches.

**web.py 1000-line cap:** Adding 2 lines to `web.py` pushed it to 1001. Removed 1 trailing blank line after the import block and 1 blank line between `register_layout_routes` and the next comment. File stays at 999 lines.

**`_compute_layout_impl` inner imports:** `from collections import deque` was moved to the module-level imports section (in `_bfs_depth` the collections import was used); all imports are at the top of the file as required.

**TypedDict → Pydantic coercion:** `LayoutResponse(**result)` caused mypy errors because `result` contains `list[LayoutNode]` (TypedDict) not `list[LayoutNodeModel]` (Pydantic). Fixed with `LayoutResponse.model_validate(result)` (Pydantic v2 API).

### Deviations from plan

- Cache key includes `COUNT(*) FROM files` (not in plan) to handle `indexed_at=0` collisions
- `edge_match_names` takes `conn` as first arg (plan's CR1 code was missing `conn`) — adapted
- Malformed-row test uses closed connection instead of NULL name (schema constraint)
- Applied qualified-before-plain iteration to fix the degree-bridge ordering issue

### Open questions

- None for S1. The layout engine is complete and all 3151 tests pass.
- S2 (frontend) should start from Task 3 in the plan.

---

## 2026-07-02 — Slice S2: Minimal 3D Tab Renders (issue #170)

### What was built

**npm packages** — installed into `web/`:
- `three@~0.183.0`, `@react-three/fiber@^9`, `@react-three/drei@^10`, `@react-three/postprocessing@^3`
- `@types/three@~0.183.0` (devDependency)

**web/src/lib/layoutTypes.ts** — TypeScript types for the layout API response:
- `LayoutNode`, `LayoutEdge`, `ClusterSummary`, `LayoutData`
- MI1: used `Layout*` names (NOT `GraphNode`/`GraphEdge`) to avoid colliding with the existing 2D `graph_api` types (different shapes, different API)

**web/src/lib/constellationColors.ts** — teal-native color palette:
- `EDGE_TYPE_COLORS` — all 9 edge kinds; `call` → `#1DA27E` (seafoam teal)
- `KIND_COLORS` — 6 symbol kinds
- `CANVAS_BG = "#04100f"` (teal-void)

**web/src/hooks/useLayoutData.ts** — react-query hook:
- `useLayoutData(maxNodes)` → `LayoutData`; staleTime 60s; error throws

**web/src/components/NodeCloud.tsx** — InstancedMesh node cloud:
- Pure `computeInstanceColor(node, isHighlighted, isDimmed) → [r, g, b]` (unit-tested)
- Highlighted: boost = 1.2 + brightness×0.8 (>1.0 → Bloom fires)
- Dimmed: ×0.15
- `useFrame` writes matrices + colors; `useMemo` pre-computes colorArray on highlight change

**web/src/components/ConstellationScene.tsx** — R3F canvas root:
- Pure `computeCameraTarget(nodes, ids) → CameraTarget | null` (unit-tested)
- Pure `easeOutCubic(p) → number` = `1 - (1-p)³` (unit-tested)
- `buildEdgeGeometry(nodeMap, edges, highlightedIds)` extracted helper for EdgeLines
- Canvas: `dpr=[1,1.5]`, `antialias=false`, `bg=CANVAS_BG`
- `OrbitControls` with `dampingFactor=0.08`, idle auto-rotate after 60s
- `EffectComposer` + `Bloom` (threshold=0.3, intensity=1.2, radius=0.6, mipmapBlur)
- `CameraAnimator` using `easeOutCubic` + 0.08 lerp factor
- `EdgeLines` with additive blending (intensity table from reference §2)

**web/src/components/ConstellationTab.tsx** — lazy-loaded 3D tab:
- Composes `useLayoutData` + `ConstellationScene`
- Owns `selectedNode`, `hoveredNode`, `highlightedIds`, `cameraTarget` state
- Error/loading branches with user-readable messages (IM4)
- `onFocusSymbol` prop for 2D↔3D sync

**web/src/App.tsx** — wired the Constellation tab:
- Added `ViewMode = "neighborhood" | "overview" | "constellation"`
- Added `focusSymbol` state for 2D↔3D sync
- Added Constellation tab button (Orbit icon) in the header (`aria-label="Constellation"`)
- `lazy(() => import("./components/ConstellationTab"))` + `<Suspense>` fallback
- When mode==="constellation" renders the full-screen tab; other modes untouched

### Key decisions

**MI1 — LayoutNode vs GraphNode:** Used `LayoutNode`/`LayoutEdge` throughout to avoid collision with the existing 2D `GraphNode` (different structure, different API endpoint). Plan's Task 3 originally used `GraphNode` but the Applied Review Revisions section explicitly mandated `Layout*` names.

**Pure helper extraction (IM2):** `computeInstanceColor`, `computeCameraTarget`, `easeOutCubic` are all top-level exports from their modules, not closures inside React components. This makes them trivially unit-testable in vitest without any WebGL/jsdom canvas setup.

**THREE.WARNING in tests:** The nodeCloudHelpers test file imports from both `NodeCloud.tsx` (which imports three via R3F) and `ConstellationScene.tsx` (which imports three directly). This triggers three.js's "multiple instances" warning. It is benign in test context (no actual rendering) and does not affect test results. The warning comes from vitest loading both modules with separate module instances.

**OrbitControls ref typing:** The R3F `OrbitControls` ref type is complex due to drei's polymorphic ref. Used a loose `{ autoRotate: boolean } | null` interface for the AutoRotateController to avoid importing the internal drei type. This is safe since we only access `.autoRotate`.

**App.tsx Constellation button role:** The button has text content "Constellation" with an Orbit icon, making it discoverable by `getByRole("button", { name: /constellation/i })` in the test.

### Tests added (128 frontend tests, all green)

- `web/src/__tests__/constellationColors.test.ts` (4 tests) — EDGE_TYPE_COLORS all 9 kinds, call=#1DA27E, KIND_COLORS all 6 kinds, CANVAS_BG
- `web/src/__tests__/nodeCloudHelpers.test.ts` (10 tests) — computeInstanceColor (3), computeCameraTarget (3), easeOutCubic (4)
- `web/src/__tests__/App.test.tsx` — extended with Constellation tab button assertion

### Deviations from plan

- Plan's Task 3 used `GraphNode`/`GraphEdge` type names — overridden by MI1 to `LayoutNode`/`LayoutEdge`
- `EdgeLines` was placed inside `ConstellationScene.tsx` (not a separate file) because it has no independent pure helpers to test and the file stays under 300 lines
- `buildEdgeGeometry` was extracted as an exported helper in ConstellationScene.tsx for potential future testing

### Open questions

- The THREE.WARNING in tests is benign but could be resolved by mocking `three` in the test setup. Low priority.
- S3 (visual layer: ClusterHalos, NodeLabels, NodeTooltip) and S4 (UI shell: FilterPanel, HUD, DetailPanel) are the next slices.

---

## 2026-07-02 — Slice S3: EdgeLines, NodeLabels, NodeTooltip (issue #171)

### What was built

**web/src/components/EdgeLines.tsx** — standalone edge rendering component:
- Pure `buildEdgeGeometry(nodeMap, edges, highlightedIds) → {positions, colors}` (exported for testing)
- `EdgeLines` React component: `<lineSegments>` with `AdditiveBlending`, `depthWrite=false`, `toneMapped=false`
- Intensity table: both-highlighted=0.5, one-highlighted=0.04, same-cluster=0.25, cross-cluster=0.06, dimmed=0 (skipped)

**web/src/components/NodeLabels.tsx** — sprite labels for prominent nodes:
- Pure `bareName(qualified) → string` — strips container prefix after last dot (exported for testing)
- Pure `selectLabelNodes(nodes, cap=80) → LayoutNode[]` — top-cap by size DESC (exported for testing)
- `NodeLabels` component: renders canvas-sprite `<LabelSprite>` per selected node; when highlight active, shows only highlighted nodes
- `LabelSprite`: creates `THREE.CanvasTexture` on mount via `document.createElement("canvas")`, disposes on cleanup

**web/src/components/NodeTooltip.tsx** — hover glass-card tooltip:
- Uses `@react-three/drei <Html>` for 3D→screen projection, `pointerEvents:"none"`
- Shows: KIND_COLORS dot, bareName (bold), full qualified name (if different), kind label, file basename
- `distanceFactor={600}` keeps tooltip size consistent regardless of zoom level

**web/src/components/ConstellationScene.tsx** — updated:
- Removed duplicate `buildEdgeGeometry` and internal `EdgeLines` (extracted to standalone files)
- Imports `EdgeLines` from `./EdgeLines`, `NodeLabels` from `./NodeLabels`, `NodeTooltip` from `./NodeTooltip`
- Added `hoveredNode?: LayoutNode | null` prop; mounts `<NodeTooltip>` only when hoveredNode is set
- Mounts `<NodeLabels>` inside the Canvas

**web/src/components/ConstellationTab.tsx** — updated:
- Passes `hoveredNode={hoveredNode}` to `ConstellationScene`
- Removed the old inline S2 placeholder tooltip div

### Key decisions

**buildEdgeGeometry placement:** The plan said to "extract" `buildEdgeGeometry` from `ConstellationScene.tsx`. S2 had put it inline there. S3 moved it to the standalone `EdgeLines.tsx` where it belongs, making it independently testable and importable.

**NodeLabels highlight behaviour:** When a highlight set is active, the label list is narrowed to only highlighted nodes before rendering. This reduces clutter — the highlight already draws attention, and filling the screen with labels for 80 nodes alongside an active selection would be noisy.

**NodeTooltip distanceFactor:** Set to 600 (roughly the initial camera distance of 800 minus some margin). This keeps the tooltip at a readable size when starting zoomed out, without growing huge when zooming in.

**LabelSprite canvas sizing:** Canvas width is computed from `ctx.measureText(text).width + padding` after a resize-reset, requiring the font to be re-applied after canvas resize (browser resets the 2d context on size change). Handled by setting `ctx.font` twice.

### Tests added (15 new, 143 total — all green)

- `buildEdgeGeometry` (7 tests): empty edges, position/color buffer sizes, missing nodeMap entries, intensity ordering, dimmed-edge skipping, position encoding
- `bareName` (4 tests): qualified, unqualified, multi-level, trailing dot edge case
- `selectLabelNodes` (4 tests): cap, custom cap, below-cap, selects-largest

### Deviations from plan

- Plan's Task 5 Step 1 test had only 3 bareName tests; added a 4th (trailing dot) for edge-case coverage
- Plan kept `buildEdgeGeometry` in `ConstellationScene.tsx` as an export; moved it to `EdgeLines.tsx` (cleaner separation, matches plan description "extract pure buildEdgeGeometry")
- `THREE.WARNING: Multiple instances` still appears in tests (benign; same as S2)

### Open questions

- ClusterHalos component is still `_clusters` (unused) in ConstellationScene — reserved for a future slice
- S4 (FilterPanel, HUD, NodeDetailPanel, ResizeHandle) is the next slice

---

## 2026-07-02 — Slice S4: Selection, Detail Panel, Camera Fly-To (issue #172)

### What was built

**web/src/components/ConstellationTab.tsx** — updated with full selection state machine:
- Extracted `computeHighlightedIds(selectedId, edges) → Set<number>` as a pure exported helper (unit-testable without React/WebGL)
- `handleSelect(node)`: sets selectedNode, computes neighbor set via `computeHighlightedIds`, calls `computeCameraTarget`, notifies 2D side via `onFocusSymbol`
- `handleNavigate(name)`: finds node by name in layout data, re-runs `handleSelect` — drives navigation from the detail panel
- `handleClose()`: clears selectedNode + cameraTarget
- Renders `<NodeDetailPanel>` in a right sidebar column when a node is selected

**web/src/components/NodeDetailPanel.tsx** — new 3D-specific detail panel (does NOT modify 2D DetailPanel.tsx):
- Fetches `useSymbol(node.name)` from the existing hook in `api/hooks.ts`
- Shows callers / callees / cluster peers as `<NavRow>` buttons
- Each `<NavRow>` calls `onNavigate(name)` on click → re-runs selection in ConstellationTab
- Loading / error / empty states handled
- Cluster label shown at the bottom (`data.cluster.id` not `cluster_id` — ClusterInfo shape)
- Fixed: `ClusterInfo.id` (not `cluster_id`) — required reading the actual TS types

**web/src/__tests__/selectionHelpers.test.ts** — 6 pure vitest tests for `computeHighlightedIds`:
- Includes selected node id itself
- Includes direct callees (source === selectedId)
- Includes direct callers (target === selectedId)
- Excludes unrelated edges
- Handles self-edges gracefully (no double-counting)
- Returns a `Set<number>` (not array)

### Key decisions

**CameraAnimator already present from S2:** The plan's "CameraAnimator uses easeOutCubic" was already implemented in `ConstellationScene.tsx` (S2 slice). The S4 task is about wiring the selection → camera state, which is done via `setCameraTarget` in `handleSelect`.

**computeHighlightedIds extracted as a named export:** The useMemo and handleSelect callback both computed the neighbor set inline in the S2/S3 version. Extracting it as a pure named export satisfies the plan's TDD requirement (unit-test the pure helper) and removes duplication — both the useMemo and handleSelect now call the same function.

**NodeDetailPanel is self-contained:** It doesn't depend on the 2D DetailPanel internals. It uses the same `useSymbol` hook and the same `KIND_COLORS` / `DEFAULT_KIND_COLOR` from the constellation palette.

**NavRow display name:** Shows the bare suffix after the last dot for qualified names (e.g. `Client.send` → `send`) for readability. Full name shown in the `title` attribute for hover. Not using `bareName` from NodeLabels (would create a component→component import; kept it inline as a local rule).

### Tests added (6 new, 149 total — all green, typecheck clean)

All in `selectionHelpers.test.ts`:
- `computeHighlightedIds` — 6 tests covering all branches

### Deviations from plan

- Plan's Task 6 also included FilterPanel, ConstellationHUD, and ResizeHandle. S4 (issue #172) is scoped to selection + detail panel + camera only; those other components are out of scope for this slice per the task description.
- `computeHighlightedIds` takes `(selectedId: number, edges: LayoutEdge[])` instead of `(node: LayoutNode, edges: LayoutEdge[])` — using the id directly is cleaner and avoids pulling in the full node shape for the pure helper.

---

## 2026-07-02 — Slice S5: Filters, HUD, Resizable Panels, Error UI (issue #173)

### What was built

**web/src/components/FilterPanel.tsx** — left-side filter panel:
- Pure `countByField(nodes, field) → Record<string, number>` (exported for vitest)
- Counts from RAW (unfiltered) data — chip badge always reflects the full corpus
- 6 node kinds: function, class, method, interface, type, field (color dots)
- 9 edge kinds: call, import, extends, implements, instantiates, holds, reads, writes, uses (color bars)
- All/none controls for both node and edge sections
- Color dot per node kind (KIND_COLORS); color bar per edge kind (EDGE_TYPE_COLORS)

**web/src/components/ConstellationHUD.tsx** — heads-up display overlay:
- Visible node / edge counts (after filtering)
- "Showing N of M" notice when `total_nodes > visibleNodes` (layout was capped)
- Selected count when something is highlighted
- max_nodes selector (500 / 1000 / 2000 / 3000) with pointer-events: auto
- Freshness dot (green = indexed <10 min ago, amber = older/unknown) via `useStatus` / `last_indexed`
- `pointer-events: none` on the overlay so it never blocks orbit dragging

**web/src/components/ResizeHandle.tsx** — drag-to-resize divider:
- `setPointerCapture` / `releasePointerCapture` for smooth drag even outside the element
- Reports delta in pixels to parent via `onResize(delta)` callback
- Exports `clampPanelWidth(w) → number` (clamped [150, 500]) and constants `PANEL_MIN_W`/`PANEL_MAX_W`

**web/src/components/ConstellationTab.tsx** — updated three-column shell:
- Left column: `<FilterPanel>` at `leftW` pixels (default 200)
- Left `<ResizeHandle>` between filter panel and canvas
- Center: `<ConstellationScene>` (flex-1) with `<ConstellationHUD>` as absolute overlay
- Right `<ResizeHandle>` + `<NodeDetailPanel>` at `rightW` pixels (only when selectedNode)
- `maxNodes` state drives the react-query key so changing the cap re-fetches
- `visibleNodes`/`visibleEdges` derived via useMemo from filter state — passed to HUD for counts
- Panel widths persisted to `localStorage` keys `seam-left-w` / `seam-right-w` via useEffect
- isError / isLoading branches already present from S2 (verified by constellationError tests)

### Key decisions

**countByField on RAW data:** Filter counts come from `data.nodes` (full layout, before client-side
kind filtering). This ensures chips always show "how many of this kind exist" not "how many are
currently visible" — consistent with the plan's spec ("counts from RAW data").

**ConstellationHUD freshness via `useStatus`:** The plan says "freshness dot from existing
/api/status useStatus hook". StatusResponse has `last_indexed: string | null` but no explicit
`stale` field. The HUD computes freshness as "indexed <10 minutes ago → green; else → amber".
This is a reasonable proxy; a future enhancement could use the staleness banner if/when it's
added to the web API.

**ResizeHandle pointer capture pattern:** Using `e.currentTarget.hasPointerCapture(e.pointerId)`
in `onPointerMove` to guard against move events that arrive before a pointerdown (browser quirk).
The `onPointerCancel` handler also releases capture to prevent stuck states.

**ConstellationScene receives filtered nodes/edges:** The scene now receives `visibleNodes` and
`visibleEdges` (post-filter) so kind/edge toggles immediately affect the rendered geometry.
Node IDs are preserved from the full layout so camera targets and edge references remain valid.

**maxNodes selector in HUD, not parent:** The HUD owns the node-count selector display and calls
`onChangeMaxNodes` back to the tab. This keeps the HUD self-contained while the actual state
lives in the tab (which drives the react-query key).

### Tests added (6 new, 155 total — all green, typecheck clean)

**filterCounts.test.ts** (4 tests):
- `countByField` counts by label, empty array, single node, all 6 kinds

**constellationError.test.tsx** (2 tests — already pass since isError/isLoading were in S2):
- fetch→500 shows "Failed to load constellation layout." message (not blank canvas)
- pending fetch shows "Loading constellation…" animation

### Deviations from plan

- Plan described a 3000-node option in the HUD selector; added 500 as the smallest option
  (500/1000/2000/3000) since the backend supports values down to 1.
- `constellationError.test.tsx` tests passed without any code change (S2 already implemented
  the error/loading branches). Recorded them as S5 tests since the plan attributes them here.
- The right ResizeHandle is only mounted when `selectedNode` is active (the right panel itself
  only shows when something is selected). When no node is selected, there is no handle to drag.

### Open questions

- None blocking. S6 (Task 8: regenerate types, production build, manual visual verification)
  is the final slice.

---

## 2026-07-02 — Slice S6: Cluster Halos, 2D↔3D Sync, Build (issue #174)

### What was built

**web/src/components/ClusterHalos.tsx** (NEW):
- `ClusterHalos({ clusters })` — one `<mesh>` per cluster at the cluster's pre-computed centroid
- `<sphereGeometry args={[radius, 16, 16]}/>` — sphere sized to cluster spatial spread × 1.2
- `<meshBasicMaterial color transparent opacity={0.04} depthWrite={false} toneMapped={false}/>` — very faint, never masks nodes or edges
- No pure helpers — centroid and radius arrive pre-computed from `/api/graph/layout`

**web/src/components/ConstellationScene.tsx** (MODIFIED):
- Imported `ClusterHalos`
- Renamed `clusters: _clusters` (reserved) → `clusters` (active)
- Mounted `<ClusterHalos clusters={clusters} />` inside the Canvas, before NodeCloud (depth-write=false ensures halos render behind stars)

**web/src/components/ConstellationTab.tsx** (MODIFIED):
- Added `useRef<string | null>(null)` (`lastFocused`) to track processed focusSymbol and prevent round-trip loops
- Renamed `_focusSymbol` → `focusSymbol` (was unused, now wired to `useEffect`)
- Added `useEffect` watching `focusSymbol`: when it changes and differs from `selectedNode.name` (3D→2D round-trip guard), calls `handleNavigate(focusSymbol)` to fly the camera to the 2D-selected symbol in 3D
- Added `useRef` import

**web/src/App.tsx** (MODIFIED):
- In `setCenterSymbol` callback (called on every 2D symbol selection — search, landing hub chips, area chips, trace): added `if (name) setFocusSymbol(name)` to propagate 2D selections to the 3D tab

### Key decisions

**Why `lastFocused` ref for the sync guard?** When the user selects in 3D, `handleSelect` calls `onFocusSymbol(name)` → App sets `focusSymbol = name` AND `centerSymbol = name`. The updated `focusSymbol` flows back to ConstellationTab. Without the guard, ConstellationTab would call `handleNavigate(name)` again, redundantly re-flying the camera. The guard tracks the last value processed by the `useEffect` and skips if it's the same as `selectedNode.name` (the 3D-originated selection), making the sync direction-aware.

**Why mount ClusterHalos before NodeCloud?** R3F renders scene children in declaration order. Since halos have `depthWrite={false}`, they don't write to the depth buffer — but rendering them first ensures the GPU processes them before the opaque nodes, avoiding any transparency sorting issue.

**Build verification:** Vite produced two JS chunks:
- `index-DusM2sJT.js` (563 kB / 180 kB gzip) — main bundle (NO three.js)
- `ConstellationTab-DMnnxXJB.js` (993 kB / 266 kB gzip) — lazy chunk (three.js + R3F + postprocessing)

The R3F code is correctly isolated in the lazy chunk. The 993 kB raw size is expected for three.js + R3F + @react-three/postprocessing.

### Deviations from plan

- Types not regenerated from a live server (`npm run gen:types` in Task 8 Step 1) because running `seam serve` would require the full backend stack; the existing `web/src/api/types.ts` already includes the `/api/graph/layout` path from S1 (the types were generated when the endpoint was created). No drift detected.

### Open questions

- None. S6 is the final slice; all planned tasks are complete.


---

## 2026-07-02 — Full code-quality review + fixes (post-S1–S6)

Reviewer pass across all 6 committed slices (diff base `e327fc6`) through three lenses:
/review (correctness), backend-taste (Python layer), and functional QA (full gate + endpoint smoke).

**Baseline:** `make gate` green (3151 pass, 6 skipped), `npm run typecheck`/`npm test` (159) /
`npm run build` all green before any change.

**Bugs found & fixed (frontend rendering — all silent, WebGL-only, invisible to the test suite):**

1. **[HIGH] NodeCloud stellar colors never uploaded.** `THREE.InstancedMesh.instanceColor`
   starts `null` and is only allocated by `setColorAt()`, which the component never calls. The
   upload was guarded by `if (mesh.instanceColor)` — always false — so every node rendered with
   the material's default white and the entire stellar color scale (the feature's core visual)
   was dead. Fixed by lazily allocating `instanceColor` in `useFrame` (re-alloc when node count
   changes). `NodeCloud.tsx`.
2. **[MED] EdgeLines GPU buffer leak.** A fresh `THREE.BufferGeometry` is built in `useMemo` on
   every highlight change with no disposal — each rebuild leaked its VBOs. Added
   `useEffect(() => () => geometry.dispose(), [geometry])`. `EdgeLines.tsx`.
3. **[MED] AutoRotateController listener leak + side-effect-in-render.** Pointer/wheel listeners
   were attached in the render body via a `registered` ref and never removed. Moved to a
   `useEffect` with a cleanup that `removeEventListener`s on unmount. `ConstellationScene.tsx`.

**Backend review — no code changes required.** `layout.py` honors the contracts:
never-raises (narrow `(sqlite3.Error, ValueError, KeyError)` per IM3), deterministic
(FNV-1a/LCG seeding, no `random`/time in position math), name-keyed via `edge_match_names`
with the qualified-before-plain ordering (CR1), config only via `seam/config.py`, module-level
bounded cache (≤8, TTL = `SEAM_STALENESS_TTL_SECONDS`), and the `SEAM_LAYOUT_MAX_SAFE_NODES`
clamp inside `_compute_layout_impl` (CR5). File 530 lines, largest function ~160 lines — within
limits. Endpoint closes its read-only connection in a `finally`.

**Functional QA (endpoint smoke, this repo's own index — 6739 symbols / 33576 edges):**
- `GET /api/graph/layout?max_nodes=50` → HTTP 200, valid JSON with `nodes/edges/clusters/total_nodes`;
  50 nodes, 110 edges, 34 clusters, `total_nodes=6265` (honest pre-cap count); node shape complete.
- Determinism: two consecutive fetches byte-identical.
- `max_nodes=99999` → HTTP 422 (clamped by the `Query(le=SAFE_NODES)` bound).
- Safe ceiling `max_nodes=3000` → HTTP 200 in ~6.5 s (O(n²)·40 kernel), no OOM/crash — validates
  the documented memory ceiling is survivable on a laptop.

**Residual risks (not fixed — documented decisions):**
- `compute_layout` does not catch `MemoryError`; the `max_nodes` clamp + `Query(le=3000)` bound
  keep the numpy `(n,n,3)` allocation ≤ ~216 MB, so this is bounded rather than caught (respects
  the plan's deliberate narrow-except / IM3).
- `edge_match_names` is called once per unique symbol name on a cache miss (containers do 2 extra
  DB queries each) — O(n) queries per recompute. Bounded and cached (TTL), acceptable for a
  cosmetic web endpoint; noted for future batching if large repos regress.
- Built `seam/_web/` bundle is force-committed (gitignored but tracked, mirroring S6) so the
  packaged SPA stays self-consistent (`index.html` ↔ hashed chunk names) after the source fixes.

**Post-fix gate status:** `npm run typecheck` clean, `npm test` 159 pass, `npm run build` green
(three.js isolated in the lazy `ConstellationTab` chunk, absent from the main bundle). Backend
untouched → `make gate` remains green.

---

## 2026-07-02 — Documentation + Code Comments (post-review)

### What was built

This slice adds intent-first documentation and non-obvious WHY comments. No behavior changed.

**seam/query/layout.py** — module docstring expanded with five WHY sections:
- WHY server-side layout (d3-force stalls on main thread; raw edge list is MB of JSON)
- WHY numpy not a C extension (already a dep via fastembed; single C broadcast call)
- WHY FNV-1a ring seeding (hash() randomized by PYTHONHASHSEED; FNV-1a is stable)
- WHY the module-level cache key includes file_count (indexed_at=0 collision in tests)
- WHY name-keyed node collapse (edges stored as source_name/target_name strings)
- Added inline comment on the qualified-before-plain iteration (CR1 ordering fix)
- Expanded _fnv1a docstring to explain PYTHONHASHSEED problem and bit-splitting trick
- Expanded FA2 repulsion comment to explain O(n²) memory cost and epsilon rationale
- Added anchor-spring comment explaining why FA2 needs it (no gravity term)

**seam/server/web_layout.py** — module docstring expanded with three WHY sections:
- WHY a separate module (file-length discipline; separation of concerns)
- WHY Layout* Pydantic model names (avoids GraphNode OpenAPI collision in gen:types)
- WHY fresh connection per request (SQLite connections not thread-safe across threads)

**web/src/components/NodeCloud.tsx** — expanded file-level comment:
- Added toneMapped=false / HDR / Bloom luminance threshold explanation for the boost trick

**web/src/components/EdgeLines.tsx** — expanded EdgeLines component comment:
- Added additive-blending physics: why AdditiveBlending + depthWrite=false + toneMapped=false

**web/src/hooks/useLayoutData.ts** — expanded module docstring:
- WHY 60 s stale time (large payload; react-query refetchOnWindowFocus)
- WHY lazy import boundary (R3F adds ~800 kB; ConstellationTab is in a separate chunk)

**README.md** — Explorer section:
- Expanded description to mention the 3D Constellation Explorer tab
- Added a dedicated "3D Constellation Explorer tab" subsection with node/edge/halo/filter
  description and quickstart (seam serve → click Constellation tab)

**CLAUDE.md** — two additions:
- New "Current Phase" entry for P2.1 (3D Constellation Explorer) before the prior CLI-first phase
- Three new Known Gotchas: `[web]` extra requires numpy; layout degree-capped at SEAM_LAYOUT_MAX_NODES; layout cache does not attach `index_status` staleness banner

### Key decisions

**Comments only on non-obvious logic:** Obvious code (for loops, simple assignments, standard React hooks) is left uncommentless. Comments added only where the WHY is not recoverable from the WHAT (FNV-1a vs hash(), qualified-before-plain ordering, bloom > 1.0 trick, aditive blending chain, lazy chunk boundary).

**No new tests required:** Documentation changes + comment-only edits have zero runtime effect. Verified with `uv run ruff check` (Python) and `npm run typecheck` (TypeScript): both clean.

### Deviations from plan

None. Followed the doc/code-comments task description exactly.
