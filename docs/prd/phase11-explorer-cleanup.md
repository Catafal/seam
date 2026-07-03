# Phase 11 Explorer · Cleanup — Topology 3D-only · crash-proof non-navigable nodes · package-file exclusion

> Three post-redesign fixes. Frontend + read-path ranking only. No schema change,
> no migration, no re-index, MCP tool count stays 16, zero new deps.

## Problem Statement

The Explorer redesign (A→D) is shipped and coherent, but three rough edges remain
that a real user hit immediately:

1. **The Topology 2D view is now dead weight.** Phase C deliberately led Topology
   with a legible 2D cluster-graph and demoted the 3D constellation behind a
   toggle — because the old 3D was an illegible blob. Now that the 3D globe is
   polished and phenomenal (issue #259), the 2D cluster-graph is the weaker view
   and the 2D/3D toggle is friction. Topology should just *be* the 3D globe.

2. **Clicking a locked (private) node dumps you on a blank black screen.** In the
   Symbol neighborhood graph, private helper nodes render with a 🔒 lock chip
   (e.g. `_fetch_edges`, `_column_names`, `_warning`). Interacting with one
   navigates to nothing — a completely blank page (an unhandled React error that
   unmounts the entire app; there is **no error boundary anywhere**). A dead-end
   click that destroys the whole session is the worst possible failure.

3. **Package plumbing pollutes the rankings.** Test files are already excluded
   from rankings by default (with a "show tests" toggle) because the ~72%-test
   ratio drowns the signal. The same is true for package-init / barrel files —
   `__init__.py`, re-export index/barrel modules — which are structural plumbing,
   not the code you explore. They are first-party source (so, unlike vendored
   third-party packages, they are NOT skipped at index time) and they clutter the
   hub list, treemap, and constellation with low-signal nodes.

*(Investigated and out of scope: third-party/vendored packages — node_modules,
.venv, vendor/, dist, build, target, __pycache__, coverage, site — are ALREADY
excluded at index time via `SKIP_DIRS` in the indexer. Seam indexes your source,
never your dependencies. This PRD's "packages" item is specifically about
first-party package-plumbing files that ARE indexed.)*

## Solution

- **Topology = the 3D globe, full stop.** Delete the 2D cluster-graph surface and
  the 2D/3D sub-toggle. The Topology tab renders the polished 3D constellation
  directly. One view, no mode-within-a-mode.
- **No click can ever blank the app, and dead-end nodes don't pretend to be
  doors.** Add an app-level error boundary so any render error degrades to a
  graceful, recoverable fallback (never a blank screen). And gate node navigation
  so a non-navigable node (one that would resolve to an empty/no-neighborhood
  result) either does nothing destructive or degrades to the existing
  empty-neighborhood state — it never triggers a dead navigation.
- **Exclude package-plumbing from rankings by default, with a toggle** — exactly
  the mechanism tests already use. `__init__.py` and barrel/index re-export files
  are hidden from hubs / treemap / constellation by default; a "show packages"
  toggle reveals them, sitting right beside "show tests".

## User Stories

### Topology 3D-only
1. As a developer, I want the Topology tab to show the 3D globe directly, so that
   I'm not choosing between two topology views.
2. As a developer, I don't want a 2D/3D sub-toggle in the header, so that the tab
   bar stays simple now that 3D is the good view.
3. As a maintainer, I want the dead 2D cluster-graph code removed cleanly, so that
   there's no unused surface to maintain.
4. As a developer, I want everything else about Topology (filters, isolate-on-click,
   HUD, reduced-motion) to keep working exactly as it does today.

### Crash-proof, honest node interaction
5. As a developer, clicking any node in the neighborhood graph must never blank the
   whole app, so that one misclick doesn't destroy my session.
6. As a developer, I want an app-level error boundary with a "back to home" / reload
   action, so that if anything does throw, I recover in one click instead of
   reloading a black page.
7. As a developer, interacting with a locked/private helper node that has no
   navigable neighborhood should NOT navigate to nothing — it should do nothing
   destructive or show the graceful "no connections" state.
8. As a developer, I still want to hover and single-click any node to inspect it in
   the detail panel, so that locked nodes remain informative even if they're not
   navigable.
9. As a developer, I want a non-navigable node to look non-navigable (no
   "double-click to expand" affordance / default cursor), so that I'm not invited
   to take an action that leads nowhere.

### Package-plumbing exclusion (like tests)
10. As a developer, I want `__init__.py` and barrel/index re-export files excluded
    from the ranked hubs by default, so that package plumbing doesn't crowd out real
    entry points.
11. As a developer, I want the same exclusion applied everywhere tests are excluded
    — hub list, treemap sizing, constellation node selection — so the rule is
    consistent across every ranked surface.
12. As a developer, I want a "show packages" toggle beside "show tests", so that I
    can reveal package files when I actually want them.
13. As a developer, I want packages shown by default in the raw structure/file views
    (only RANKINGS exclude them), so that nothing is ever truly hidden — only
    de-prioritized, exactly like tests.
14. As a maintainer, I want package detection to be a pure, tested predicate mirroring
    `is_test_file`, so that it's a single source of truth and easy to extend.

## Implementation Decisions

### 1. Topology 3D-only (frontend removal)

- In `App.tsx`, delete the `TopologySubMode` state and the inline 2D/3D sub-toggle.
  The Topology tab renders the lazy `ConstellationTab` (3D) directly.
- Remove the now-dead 2D surface: `ClusterGraph2D.tsx`, its layout leaf
  `clusterGraphLayout.ts`, the `useConstellation` hook, and `resolveClusterHandoff.ts`
  (the 2D cluster→neighborhood hand-off) — plus their tests. Verify none are
  imported elsewhere before deleting (grep-gate).
- **Backend:** leave the `/api/constellation` endpoint and its `representative`
  field in place (removing them is a separate, riskier change and they're harmless
  dead-weight). Note them as "no longer consumed by the frontend" in a comment. The
  3D globe uses `/api/graph/layout`, which is untouched.
- Everything else in `ConstellationTab` (filters, isolate-on-click from #259, HUD,
  reduced-motion) is unchanged.

### 2. Error boundary + non-navigable node gating

- **App-level `ErrorBoundary`** (new class component — React error boundaries must be
  class components; this is the one sanctioned exception to the function-component
  norm): wraps the app (in `App.tsx` or `main.tsx`) with a graceful fallback —
  a short message + a "Back to home" / reload action, styled in the zinc theme. This
  is the durable guarantee that no interaction can ever leave the user on a blank
  page. Consider a second boundary around the graph surface so a graph crash doesn't
  take down the header/status strip.
- **Root-cause the specific throw:** the build must trace why interacting with a
  locked node (double-click → `setExpandTarget` → `useNeighborhood(target)`, or the
  single-click → detail-panel path) throws, and fix it at the source. The error
  boundary is the backstop, not the excuse to skip the root-cause fix.
- **Navigation gating via a pure `isNavigable(node)` predicate:** a node is
  navigable (offers double-click expand / re-center) only when it can resolve to a
  real neighborhood. A pure, unit-tested predicate decides this from the node data
  (e.g. treat pure edge-target references / locked private helpers with no
  independently-navigable definition as non-navigable). Non-navigable nodes:
  - keep hover + single-click (detail panel) — still informative;
  - do NOT trigger double-click expand / re-center;
  - render with a default (not pointer) cursor and no "expandable" affordance.
- **Defensive degrade:** any navigation that resolves to an empty/error neighborhood
  routes through the EXISTING `EmptyNeighborhoodState` (the `isEmptyNeighborhood`
  guard already exists) — never an unhandled throw, never a blank canvas.

### 3. Package-file exclusion (read-path ranking, mirrors tests)

- **New pure leaf `is_package_file(path)`** (mirroring `seam/analysis/testpaths.py`
  `is_test_file`): returns True for package-plumbing files by basename — Python
  `__init__.py` / `__init__.pyi`, Rust `mod.rs`, and JS/TS barrel `index.ts` /
  `index.tsx` / `index.js` / `index.jsx`. Conservative basename matching (not path
  segments) — a file is package plumbing by what it IS, not where it lives. Pure,
  never raises, single source of truth. (Live in `testpaths.py` alongside
  `is_test_file`, or a sibling `pathkinds.py` leaf — implementer's call.)
- **Thread `show_packages: bool = False` in parallel with `show_tests`** everywhere
  the test exclusion is already applied: `top_hub_symbols` (hubs), and any ranked
  surface that threads `show_tests` today (areas/`deriveAreas`, treemap sizing, and
  the constellation/layout node selection if it excludes tests). Grep for every
  `show_tests` / `is_test_file` call site and add the package parallel at each.
- **Web API:** the endpoints that accept `show_tests` (e.g. `/api/hubs`) gain an
  additive `show_packages` query param (default False). Additive only — existing
  behavior with the param absent is unchanged.
- **Frontend:** a "show packages" toggle beside the existing "show tests" toggle on
  the landing (and anywhere "show tests" appears). The `useHubs` / `useAreas` hooks
  gain an `includePackages` parameter parallel to `includeTests`.
- **Read-path only — no re-index.** Like tests, package files remain fully indexed;
  they are only post-filtered out of RANKINGS by default. Raw structure/file views
  still show them.

### Constraints (verbatim discipline)

- No SQLite schema change, no migration, no re-index. MCP tool count stays 16. Zero
  new deps. Every touched file < 1000 lines / function < 200. All imports at top.
  `X | None` not Optional. Frontend gate = vitest + tsc + build; backend gate = ruff
  + mypy + pytest. Rebuild `seam/_web` on merge.

## Testing Decisions

- **Good tests assert external behavior, not implementation detail.** Prior art:
  `tests/` for `is_test_file` + `top_hub_symbols` (backend); `web/src/__tests__/*`
  vitest for pure leaves and components.
- **Backend (pytest):**
  - `is_package_file`: True for `__init__.py`, `mod.rs`, barrel `index.ts/js`;
    False for a normal module; never raises on None/odd input (mirror the
    `is_test_file` test suite).
  - `top_hub_symbols`: `show_packages=False` excludes package files; `True` includes
    them; independent of `show_tests` (all four combinations behave correctly).
  - Web API: the `show_packages` param is additive; absent → prior behavior.
- **Frontend (vitest):**
  - `isNavigable` predicate: locked/non-navigable node → false; a normal symbol →
    true; each branch tested.
  - `ErrorBoundary`: a child that throws renders the fallback (with the recover
    action), not a blank tree.
  - Non-navigable node: double-click does NOT trigger the expand/re-center handler;
    single-click still opens the detail panel (regression against the blank-page bug).
  - Topology: the 2D surface is gone; the Topology tab renders the 3D view; no
    dangling imports (tsc enforces).
  - "show packages" toggle: flipping it changes the `includePackages` argument to the
    hook (mirror the existing "show tests" toggle test).
- **Gate:** ruff + mypy + full pytest; tsc + vitest + vite build green. Rebuild bundle.

## Out of Scope

- Removing `/api/constellation` / `build_constellation` / the `representative` field
  (kept as harmless dead-weight; a separate cleanup if ever wanted).
- Changing what is excluded at INDEX time (`SKIP_DIRS`) — third-party packages are
  already handled there; this PRD does not touch indexing.
- Any schema change, migration, re-index, or new MCP tool.
- A general "navigate to any symbol" resolver — this PRD only prevents dead-end
  navigation and crashes; it does not add new navigation targets.
- Adding package/module *grouping* nodes to the graph (a larger feature, explicitly
  not chosen).

## Further Notes

- The three items are independent vertical slices and can be built/reviewed in
  parallel-ish (sequential in one worktree to avoid races).
- Frontend-design rationale: **restraint** (Topology stops offering a redundant
  second view now that 3D is good), **honest affordances** (a node that leads nowhere
  must not look clickable — copy/cursor from the user's side), and **failure is
  direction, not a void** (the error boundary turns a blank crash into a recoverable,
  explained state). The package exclusion extends the existing "signal over volume"
  principle (tests) to package plumbing — same mechanism, same default, same toggle.
