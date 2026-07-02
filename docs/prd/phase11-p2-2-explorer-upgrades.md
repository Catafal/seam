# Phase 11 P2.2 — 2D Explorer UX Upgrades

> Parent: Phase 11 roadmap §P2.2 "Upgrade the existing 2D Explorer"
> (`docs/prd/phase11-codebase-memory-roadmap.md`). Sibling of the shipped
> P2.1 3D Constellation (#162). `[web]` extra; read-path only.

## Problem Statement

Seam's 2D Explorer (the React Flow canvas) is the precise debugging surface for
neighborhood, impact, trace, and changes workflows — but its interaction model
lags the polish of the newly-shipped 3D constellation, and several affordances a
developer expects from a graph tool are missing:

- The detail panel is a **fixed 288px column** — a developer reading a long
  signature or docstring cannot widen it, and its width is forgotten on reload.
- The detail panel shows caller/callee **counts only, with zero clickable rows** —
  a developer can see that a symbol has 12 callers but cannot see *which* symbols,
  cannot group them by relationship, and cannot click through to explore them.
- There is **no HUD** — a developer has no at-a-glance readout of how many nodes
  and edges are on screen, how many are filtered out, or what is selected.
- The filter bar has **no All/None controls and no counts**, lists **six edge
  kinds that do not exist** in the schema (always zero), and has **no node-kind
  filter axis at all**. Filter preferences are silently **reset on every symbol
  navigation** and never persisted.
- The structure view is a **full-screen mode** with **no search** — a developer
  cannot keep a file tree open beside the graph, cannot search for a file, and
  cannot jump from a file to a symbol's neighborhood without leaving the graph.
- When an impact or trace overlay fires, the **viewport never moves** — off-canvas
  blast-radius cards and highlighted trace paths render outside the visible area
  and the developer must manually pan and zoom to find them.

## Solution

Six low-risk UX upgrades to the 2D Explorer, reusing patterns already proven in
the 3D constellation (`ResizeHandle`, `ConstellationHUD`, `FilterPanel`):

1. **Resizable, persisted side panels.** The detail panel (and the new file
   sidebar) can be drag-resized; widths persist across reloads via `localStorage`.
2. **Grouped, clickable caller/callee lists.** The detail panel renders full
   caller/callee rows grouped by edge kind, each row clickable to navigate to that
   symbol **without losing the current graph view**. Long signatures/docstrings no
   longer expand the panel unboundedly.
3. **Graph HUD.** A compact overlay on the canvas shows visible nodes, visible
   edges, filtered-out count, and selected count — updating live as filters and
   impact/trace overlays change.
4. **All/None filters with counts and a node-kind axis.** The filter surface gains
   select-all / clear-all controls, per-option counts (node kind, edge kind,
   confidence), a **new node-kind filter axis**, removal of the six phantom edge
   kinds, and **session-global + `localStorage`-persisted** filter state.
5. **Searchable file/path sidebar.** A new collapsible, resizable file-tree
   sidebar sits beside the graph (3-column layout: sidebar | canvas | detail),
   is **lazy-loaded on first open**, supports text search over files and symbols,
   and opens a symbol's neighborhood on click.
6. **Viewport fly-to-fit.** Activating an impact or trace overlay smoothly flies
   the viewport to frame the affected nodes/path; clearing the overlay restores
   the full-graph view.

Additive backend enrichment (allowed): caller/callee entries in the symbol API
gain edge `kind` + `confidence` for exact grouping; impact entries gain `kind`.
No schema change, no re-index, no new MCP tool. All read-path, `[web]`-only.

## User Stories

### Resizable & persisted panels
1. As a developer reading a wide function signature, I want to drag the detail
   panel wider, so that I can read the whole signature without wrapping.
2. As a developer, I want the detail panel to remember its width after I reload,
   so that I don't re-adjust it every session.
3. As a developer, I want a minimum canvas width preserved while dragging, so that
   I can never accidentally collapse the graph to nothing.
4. As a developer, I want the file sidebar to be resizable and remember its width,
   so that I can tune the sidebar/canvas/detail split to my screen.
5. As a developer, I want the detail panel width to stay stable while it loads
   data, so that the layout doesn't jump between states.

### Grouped caller/callee lists
6. As a developer inspecting a symbol, I want to see the actual list of callers
   and callees, so that I know which symbols depend on it.
7. As a developer, I want callers/callees grouped by edge kind (call, import,
   holds, reads, writes, uses, …), so that I can distinguish control-flow from
   data-flow coupling at a glance.
8. As a developer, I want each caller/callee row to be clickable, so that I can
   navigate to that symbol's detail.
9. As a developer clicking a caller/callee, I want the current graph view
   preserved, so that I don't lose my place while exploring detail.
10. As a developer, I want a confidence badge on each row, so that I can tell a
    heuristically-inferred link from a statically-extracted one.
11. As a developer viewing a hub symbol with 40 callers, I want the list capped
    with a "show N more" control, so that the panel stays scannable.
12. As a developer, I want long qualified names shown by their last segment with
    the full name on hover, so that rows stay compact but unambiguous.
13. As a developer, I want a very long docstring clamped with a "show more"
    toggle, so that it doesn't push the references section off screen.

### Graph HUD
14. As a developer, I want a HUD showing the visible node count, so that I know
    how large the current graph is.
15. As a developer, I want the HUD to show visible edge count and filtered-out
    count, so that I know how much the filters are hiding.
16. As a developer, I want the HUD to show the selected count, so that I can
    confirm my selection state.
17. As a developer, I want the HUD counts to update after I apply an impact or
    trace overlay, so that the readout always reflects what's on screen.
18. As a developer, I want the HUD placed so it never overlaps the legend, so that
    both are readable.
19. As a developer, I want a freshness indicator in the HUD consistent with the
    3D view, so that I have a staleness signal without leaving the canvas.

### Filters (All/None, counts, node-kind axis, persistence)
20. As a developer, I want an "All" and a "None" control per filter group, so that
    I can reset or clear a whole axis in one click.
21. As a developer, I want a count next to each filter option, so that I know how
    many edges/nodes match before toggling.
22. As a developer, I want to filter by node kind (function, class, method,
    interface, type, field), so that I can focus the graph on one kind of symbol.
23. As a developer, I want per-node-kind counts, so that I can see the composition
    of the current graph.
24. As a developer, I do NOT want to see edge-kind filters for relationships that
    don't exist in the index, so that the filter bar isn't padded with dead
    always-zero options.
25. As a developer, I want my filter preferences to persist as I navigate between
    symbols, so that I don't re-apply "hide INFERRED edges" on every new graph.
26. As a developer, I want my filter preferences to survive a page reload, so that
    my working configuration is durable.
27. As a developer, I want filter counts to recompute after impact/trace overlays,
    so that the counts reflect the currently-visible set.
28. As a developer, I want a colored dot beside each edge-kind and node-kind
    filter option matching the graph colors, so that the filter and canvas agree
    visually.

### Searchable file/path sidebar
29. As a developer, I want a file-tree sidebar beside the graph, so that I can
    browse the repository structure without leaving the canvas.
30. As a developer, I want to collapse and expand the sidebar, so that I can
    reclaim canvas space when I don't need it.
31. As a developer, I want to search the file tree by path or symbol name, so that
    I can jump to a file or symbol quickly.
32. As a developer, I want the sidebar's structure data fetched only when I first
    open it, so that opening the Explorer stays fast on large repos.
33. As a developer clicking a symbol in the sidebar, I want its neighborhood graph
    to open, so that I can go from structure to relationships in one click.
34. As a developer, I want the sidebar to show symbol-count badges per
    directory/file, so that I can gauge density before expanding.
35. As a developer, I want the sidebar width and open/closed state persisted, so
    that my layout is stable across sessions.
36. As a developer, I want the sidebar to open a symbol by its qualified name when
    available, so that I don't land on the wrong homonym.

### Viewport fly-to-fit
37. As a developer activating an impact overlay, I want the viewport to fly to
    frame the blast radius (including off-canvas cards), so that I immediately see
    the affected symbols.
38. As a developer activating a trace overlay, I want the viewport to fly to frame
    the path, so that the highlighted route is legible at a comfortable zoom.
39. As a developer clearing an overlay, I want the viewport to restore the
    full-graph view, so that I return to my baseline.
40. As a developer, I do NOT want the viewport to jump when I expand a node or the
    data merely refreshes, so that fly-to-fit only fires on real overlay changes.
41. As a developer, I want a "fit all" control on the canvas, so that I have an
    escape hatch when I've panned far away.

### Cross-cutting
42. As a developer, I want none of these features to make any network call beyond
    the existing local web API, so that the local-first guarantee holds.
43. As a maintainer, I want `GraphCanvas` to stay under the 1000-line file limit,
    so that the codebase conventions are respected.
44. As a developer on mobile/narrow screens, I want the layout to degrade
    gracefully (sidebar collapsible, panels clamp), so that the Explorer stays
    usable.

## Implementation Decisions

### Scope confirmed (all features, full scope)
All six features ship. Backend enrichment is **allowed and included**. Filter
state is **session-global and persisted**. The file sidebar is a **persistent
3-column companion** (not merely search added to the existing full-screen mode).
The filter work **adds a new node-kind axis** and **prunes the six phantom edge
kinds**.

### Reuse from the shipped 3D constellation (do not reinvent)
- **`ResizeHandle`** — the drag-resize component (pointer-capture, `clampPanelWidth`,
  `PANEL_MIN_W`/`PANEL_MAX_W`) is ready to drop into the 2D layout unchanged for
  both the detail panel and the file sidebar.
- **`ConstellationHUD`** — the glassmorphic stat-overlay structure (rows, freshness
  dot, `pointer-events-none`) is the template for the new 2D graph HUD; drop the
  `max_nodes` selector and cap notice (not applicable to the per-center 2D graph).
- **`FilterPanel`** (3D) — the All/None button pattern, per-chip count display,
  and colored-dot chips are the template for the upgraded 2D filter surface. Its
  pure counting helpers (`countByField`, `countEdgesByType`) should be **extracted
  to a shared module** and reused by both surfaces.
- **`ConstellationTab`** — its `localStorage` width pattern (clamped lazy init +
  write-on-change effect) is the template for 2D panel persistence.

### Shared helper extraction (deep modules, isolation-testable)
- **`readWidth` / width-persistence helper** — extract the `localStorage`
  read+clamp helper (currently inline in the 3D tab) into the resize module as a
  named export, so 2D and 3D share one implementation.
- **Filter counting module** — a pure module that, given the current node and edge
  arrays, returns per-node-kind, per-edge-kind, and per-confidence counts. No
  React, no DOM — a plain data-in/data-out function. Reused by the HUD and the
  filter surface.
- **HUD-counts helper** — a pure function `(displayNodes, displayEdges,
  selectedNode) → {visibleNodes, visibleEdges, filteredOut, selected}` so the HUD
  math is unit-testable without rendering React Flow.
- **Freshness-color helper** — extract the freshness→color mapping (currently
  inline in the 3D HUD) to a shared module used by both HUDs.
- **Filter-state module** — extend the existing edge-filter model with a
  `nodeKinds` set; add `localStorage` load/save and a merge-with-defaults on load
  (so a persisted set from an older vocabulary still resolves). All pure functions.

### GraphCanvas 1000-line guard (mandatory, do early)
`GraphCanvas` is ~470 lines and already holds heavy derived state. Adding the HUD,
the viewport controller, and count computation risks the 1000-line limit.
**Before adding features, extract the overlay-decoration logic** (node/edge
decoration, off-canvas card building, tier-map derivation) into a dedicated hook
(e.g. `useGraphOverlays`). This is a prerequisite task, not optional cleanup.

### Feature 1 — Resizable & persisted panels
- Add a detail-panel width state in the top-level app, lazily initialized from a
  dedicated `localStorage` key **distinct from the 3D keys** (avoid collision),
  written on change.
- Insert a `ResizeHandle` (right-side convention) between the canvas column and the
  detail panel; gate it on the same condition that renders the panel.
- The detail panel must accept a `width` prop and apply it via inline style on
  **all render branches** (null/loading/not-found/full), replacing the hardcoded
  fixed-width class everywhere so the width never snaps between states.
- Add a minimum-width constraint on the canvas column so dragging cannot collapse
  the graph.
- Clamp bounds may be tighter than the 3D bounds given the narrower 2D layout.

### Feature 2 — Grouped, clickable caller/callee lists
- **Backend (additive):** extend the symbol API response so caller/callee entries
  carry `{name, kind, confidence}` instead of bare names, sourced by joining the
  symbol's edges — **without changing** the underlying context handler. Keep the
  change localized to the web route/model. This gives exact grouping for **all**
  callers (not just depth-1), superseding the frontend-join fallback.
- Render caller/callee **rows grouped by edge kind**, each row a clickable control
  that navigates by updating the **selected** symbol only (drives the detail
  panel) and **not** the center symbol (preserves the graph view) — this is the
  "navigate without losing graph state" contract.
- Reuse the 3D panel's clickable-row pattern (last-segment label + full name in
  `title`), the section-label helper, and the definition-row layout.
- **Cap** rendered rows per group (e.g. 15) with a "show N more" expander to guard
  hub symbols; show a confidence badge per row.
- Clamp long docstrings with a show-more toggle.
- Update the symbol API TypeScript types + generated schema types accordingly.

### Feature 3 — Graph HUD
- New HUD component modeled on `ConstellationHUD`, props `{visibleNodes,
  visibleEdges, filteredOut, selected}` (no `max_nodes`, no cap notice).
- Counts derived via the pure HUD-counts helper from the already-computed display
  arrays; **exclude off-canvas impact cards** from "visible nodes" (or show them
  as a separate "+N impacted" readout) so the count doesn't jump confusingly when
  the impact overlay fires — decision: **exclude, with a separate impacted count
  when an overlay is active**.
- Place at **bottom-left** React Flow `Panel` to avoid the top-left legend.
- Include a freshness dot via the shared freshness helper + status hook.

### Feature 4 — Filters (All/None, counts, node-kind axis, persistence)
- Extend the filter model with a `nodeKinds` set (defaulting to all real node
  kinds: function, class, method, interface, type, field). Node-kind filtering
  sets `hidden` on nodes in the canvas decoration path.
- **Prune** the six phantom edge kinds (`http_calls`, `reads_config`, `configures`,
  `raises`, `catches`, `tests`) from the canonical edge-kind list; the real
  vocabulary is the 9 kinds: call, import, extends, implements, instantiates,
  holds, reads, writes, uses. (If a future graph-model phase adds any of these,
  they return then.)
- Add All/None controls per group and per-option counts, using the shared counting
  module. Counts source from the **post-overlay display arrays** so they satisfy
  "counts update after impact/trace overlays"; count **visible (non-hidden)**
  entries.
- **Persistence:** filter state is lifted so it is **session-global** (survives
  center-symbol navigation — remove the reset-on-center-change effect) and
  **persisted to `localStorage`**, merged with current defaults on load so a stale
  persisted vocabulary still resolves.
- Colored dots per edge-kind and node-kind option, reusing the constellation color
  constants.
- The filter surface likely needs a layout change (collapsible panel/popover)
  since a horizontal strip with counts + All/None + node-kind axis will overflow
  narrow viewports; adopt the scrollable-column form of the 3D `FilterPanel`.

### Feature 5 — Searchable file/path sidebar (3-column layout)
- **New `FileSidebar` component** (do not overload the existing full-screen
  structure view). Renders a collapsible, indented directory→file→symbol tree
  (VS-Code style) built by the existing tree-builder from the flat structure list.
- **App layout restructure:** the neighborhood view becomes 3-column: sidebar |
  canvas | detail. Manage flex/overflow carefully alongside the existing changes
  drawer (which adds a rightmost panel) to avoid a broken 4-column overflow.
- **Lazy fetch:** the structure fetch is gated on the sidebar being open (use the
  existing hook's `enabled` param), so opening the Explorer does not fetch the
  whole-project structure until the sidebar is first opened.
- **Search:** a debounced text input filters the flat structure list client-side
  (by path and/or symbol name) before tree-building. Reuse the existing search
  input's debounce pattern.
- **Open-on-click:** clicking a symbol calls the existing open-from-structure
  callback (sets center symbol + switches to neighborhood), passing the
  **qualified name** when available (add a qualified-name field to the tree node
  so the correct homonym opens).
- Sidebar is resizable (`ResizeHandle`, left-side) and its width + open/closed
  state persist to `localStorage`.
- Symbol-count badges per directory/file from the tree-builder's roll-up counts.
- Client-side filtering over very large symbol lists is acceptable with debounce +
  memoization for typical repos; a backend `file_pattern` filter on the structure
  route is **out of scope** for this PRD (noted as a future optimization).

### Feature 6 — Viewport fly-to-fit
- Add a **`ViewportController` child component rendered inside `<ReactFlow>`** (the
  provider), calling the React Flow instance hook — it **cannot** be called at the
  outer canvas level. It receives the overlay flags + affected-node sets as props.
- On impact activation (flag transitions true, tier map non-empty), fly to fit the
  full set of affected node IDs (including off-canvas cards, whose IDs match the
  tier-map keys), with a short duration and padding.
- On trace activation, fly to fit only the path node set.
- On overlay clear, fly to fit all nodes (restore baseline).
- **Gate the effects on boolean activation transitions** (via a previous-value
  ref), not on the memoized set objects, so data refreshes and node expansions do
  not cause spurious viewport jumps. Keep the declarative initial-mount `fitView`
  prop for first layout.
- Optional "fit all" control on the canvas toolbar.

### Additive backend changes (allowed, included)
- **Symbol API:** caller/callee entries become `{name, kind, confidence}` objects.
- **Impact API:** add an optional `kind` field to impact entries (the E4 field the
  MCP handler already produces; the web route currently strips it via lean mode).
- Both are additive, localized to the web layer, no schema change, no re-index.
- Update the generated OpenAPI/TS types to match.

### Non-negotiables honored
- Zero network beyond the existing local web API; no external services.
- No SQLite schema change, no migration, no re-index, no new MCP tool
  (count stays 16).
- Config (if any new knob) via `seam/config.py` only. No new knob is anticipated;
  panel/filter persistence lives in browser `localStorage`, not server config.
- `[web]` extra only; backend Python stays within file/function size limits.

## Testing Decisions

**What makes a good test here:** assert externally-observable behavior, not
implementation details. For pure helpers, that means data-in → data-out. For React
components, that means rendered output and interaction outcomes (via Testing
Library queries + `fireEvent`), not internal state. For backend, that means the
API response shape/contents, not the query internals.

**Pure helper modules (highest-value unit tests):**
- Filter counting module — counts per node kind / edge kind / confidence over
  representative node/edge arrays, including empty and all-hidden cases.
- HUD-counts helper — visible/filtered/selected math, including off-canvas
  exclusion and overlay-active cases.
- Filter-state module — node-kind toggle, All/None, `localStorage` load/save,
  merge-with-defaults on a stale persisted vocabulary, phantom-kind pruning.
- Width-persistence helper — clamp bounds, missing/corrupt `localStorage` value.
- Freshness-color helper — boundary mappings.
- Prior art: the existing `filterCounts.test.ts` (tests the 3D counting helpers)
  and the constellation pure-helper vitest suite are the direct model.

**Component tests (Testing Library):**
- Detail panel — renders grouped caller/callee rows, rows are clickable and call
  the navigate callback with the right symbol, cap + "show more" behavior,
  docstring clamp toggle, width prop applied across render branches.
- Graph HUD — renders the four counts from props; updates when props change.
- Filter surface — All/None buttons toggle whole groups, counts render, node-kind
  axis present, phantom kinds absent, colored dots present.
- File sidebar — search filters the tree, click opens a symbol (callback fired
  with qualified name), collapse/expand, lazy-fetch gating (hook `enabled`),
  count badges render.
- App layout — 3-column layout renders with sidebar open/closed; brand/home and
  existing flows unaffected (extend the existing `App.test.tsx`).
- Prior art: existing `DetailPanel.test.tsx`, `App.test.tsx`, and the constellation
  component tests under `web/src/__tests__/`.

**Viewport fly-to-fit:** the React Flow instance is hard to drive in jsdom; test
the **pure decision logic** (which node-id set to fit given overlay flags + the
transition guard) as an extracted helper, and cover the wiring with a light
component test that mocks the instance hook and asserts the fit call is made only
on activation transitions. Do not attempt full WebGL/canvas assertions.

**Backend tests (pytest, TestClient):**
- Symbol route returns caller/callee entries with `name`, `kind`, `confidence`.
- Impact route includes `kind` on entries.
- Both remain byte-stable for consumers that ignore the new fields (additive).
- Prior art: existing `tests/integration/test_web_api.py`.

**Gate:** `make gate` (ruff + mypy + pytest) for backend; `npm run test` +
typecheck + build for frontend. Both must pass before every commit.

**Manual QA (explicit, non-automatable):** the viewport animation, drag-resize
feel, and overall 2D layout must be eyeballed in a running `seam serve` — jsdom
cannot verify React Flow viewport motion or pointer-drag ergonomics.

## Out of Scope

- The 3D constellation (P2.1, shipped) — touched only as a reuse reference.
- Project-management / multi-project controls (P2.3, deliberately deferred).
- New graph-model edge kinds (routes/`http_calls`, `configures`, `raises`,
  `tests`) — those are P3; this PRD **removes** their phantom filter entries until
  P3 actually populates them.
- A backend `file_pattern` filter / pagination on the structure route — noted as a
  future large-repo optimization; the sidebar uses client-side filtering for now.
- Multi-select on the 2D canvas — selection stays single (HUD selected count is
  0 or 1).
- Any SQLite schema change, migration, re-index, or new MCP tool.
- Semantic/embedding changes.

## Further Notes

- **Sequencing:** do the `GraphCanvas` overlay-decoration hook extraction first
  (unblocks HUD + fly-to-fit without breaching the line limit), then the shared
  helper extractions (counting, width, freshness) since multiple features consume
  them, then the features. The file-sidebar layout restructure is the largest
  single change and interacts with the changes drawer — schedule it where it can
  be reviewed on its own.
- **localStorage key hygiene:** choose 2D-specific keys that do not collide with
  the 3D keys; document the full key set in one place.
- **Filter persistence semantics change** (per decision): filters become
  session-global and durable. This is a deliberate behavior change from
  reset-on-navigate — call it out in docs so it isn't read as a regression.
- **Homonym safety:** the file sidebar must pass qualified names on open to avoid
  landing on the wrong same-named symbol.
- All acceptance targets from the roadmap §P2.2 are covered: detail panel no longer
  truncates awkwardly (13), caller/callee rows clickable without losing graph state
  (8–9), filter counts update after overlays (27), file sidebar opens contained
  symbols without a whole-project layout blob (32–33).
