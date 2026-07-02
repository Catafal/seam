# PRD — Phase 11 Explorer Redesign

> Status: proposed — 2026-07-02.
> Basis: 9-agent research + first-principles judge-panel workflow (run `wf_dd9084d6-562`).
> Verdict: **unanimous 2D-only navigation** (4/4 lenses at 0.9 conf), constellation
> **demoted, not deleted**. User decisions: constellation kept **both** ways (fixed 3D +
> 2D cluster-graph fallback); rankings **exclude tests by default and rank by fan-in degree**.

## Problem Statement

The Seam Explorer is "meh." Walking every page surfaced two root causes, not a hundred:

1. **Every ranked view lies.** The repo is 72% tests (4,544 test vs 1,776 source symbols).
   Landing hubs, clusters, key-symbols, and the treemap all rank by **raw symbol count**,
   so test-helper fixtures (`_sym`, `_walk`, `_text`, `_make_db`) and test-derived cluster
   labels ("unit — _sym") dominate. You see your test scaffolding, not your architecture.

2. **Too many half-connected views, no through-line.** Three confusing tabs (Overview /
   Neighborhood / Constellation), **three different "area" concepts**, dead-end drills, and
   empty cockpits. It is a pile of features, not a flow.

Confirmed concrete defects (grounded in code):
- **Constellation "green blob":** `ConstellationScene` renders 556 translucent cluster-halo
  spheres (`ClusterHalos`, opacity 0.04) that additively composite into an opaque ball,
  burying the 2,000 nodes. Not filter-controlled → deactivating everything still shows the
  blob. This is additive-compositing physics, not a tunable bug.
- **Treemap drill nesting:** drilling an area shows a redundant empty parent rectangle
  ("Areas › server › seam › server") before the useful file treemap — no common-prefix
  strip, no single-child collapse.
- **Empty-symbol cockpit:** opening a symbol with 0 edges renders the full impact/trace
  cockpit around one lonely node.
- **Two contradictory "areas":** the good folder-based `deriveAreas` (Overview) vs the
  junk cluster cards (landing).
- **Changes drawer** lists non-code files (`.md` PRDs, `.log`s) as "changed symbols."

## Solution

One coherent, **test-aware, 2D-first** drill path — repo → area → file → symbol — that
never lies and never dead-ends. The 3D constellation is kept but **demoted** to a secondary,
legible "Topology" surface answering exactly one question: *how do the functional areas
couple — star, mesh, or chain?* It is never the answer to "where is the code that does X."

**The decisive first principle** (Ghoniem 2004; every mature tool — Sourcetrail,
Sourcegraph, IntelliJ, CodeSee, Gephi): a whole-repo *node-link* graph is the wrong data
structure past ~200 nodes in ANY dimension. Keep node-link **local**; use **treemaps** for
overview. Rank everything by **fan-in degree**, never raw symbol count — this single
principle de-noises landing, treemap sizing, and clusters simultaneously.

## User Stories

1. As a developer arriving at the Explorer, I want a search box first, so that I can go
   straight to a concept instead of scanning an inventory.
2. As a developer, I want the "key symbols" to be real hub symbols (high fan-in), so that I
   see my architecture, not test helpers.
3. As a developer, I want tests excluded from rankings by default with a "show tests"
   toggle, so that the 72%-test ratio does not drown the signal.
4. As a developer, I want one consistent "functional areas" concept everywhere, so that the
   landing and Overview agree.
5. As a developer drilling into an area, I want to land directly on its file treemap, so
   that I do not click through redundant empty parent rectangles.
6. As a developer, I want the treemap to collapse single-child directories, so that deep
   nesting is not busywork.
7. As a developer, I want treemap cell area to encode fan-in degree (coupling), so that the
   biggest cells are the most-depended-on code, not the most-numerous test files.
8. As a developer opening a symbol with no edges, I want a graceful "no connections — here's
   its source" state, so that I am not staring at an empty cockpit.
9. As a developer, I want a code-snippet panel beside the neighborhood graph, so that
   clicking a node shows its source without a page transition (Sourcetrail three-panel model).
10. As a developer, I want the neighborhood graph scoped to 1–2 hops, so that it stays
    legible and never becomes a whole-repo hairball.
11. As a developer, I want an explicit tab bar (not a button labeled with the OTHER mode),
    so that navigation is unambiguous.
12. As a developer, I want the Changes drawer to show code changes, not `.md`/`.log` files,
    so that it is meaningful.
13. As an evaluator, I want a constellation that is legible at a glance (no green blob), so
    that my first impression builds trust instead of destroying it.
14. As an evaluator, I want the constellation to answer the macro-topology question
    (star/mesh/chain), so that it earns its place as an orientation view.
15. As a developer, I want clicking a cluster in the constellation to hand off INTO the 2D
    neighborhood/treemap, so that the "wow" view connects to real navigation.
16. As a maintainer, I want the constellation off the critical path, so that its cost is
    justified by a narrow, honest job rather than pretending to be the primary map.
17. As a developer, I want index freshness/watcher status in a status strip, not a tab, so
    that server admin does not pollute code-exploration navigation.
18. As a developer, I want working breadcrumbs the whole drill path, so that I can always
    navigate back up.

## Implementation Decisions

### Ranking backbone (the single highest-leverage change)
- **Rank by fan-in degree / entry-point reach, exclude tests by default**, everywhere:
  landing hubs, cluster ordering, treemap cell sizing, constellation node sizing.
- Authoritative source-of-truth fix: `top_hub_symbols()` in `seam/server/graph_api.py`
  — add a test-path exclusion (reuse the segment list from `seam/analysis/testpaths.py`
  `is_test_file()`), rank by degree. A `show_tests` param threads to the Web API + a UI
  toggle. Additive; no schema change.

### View model (2D navigation)
- **Landing:** search-first → curated **Entry Points / Hotspots** (degree-ranked, tests
  excluded) → optional one-line `seam_architecture` briefing. Delete the junk cluster cards.
- **Overview:** squarified treemap (folder→file→symbol), **cell area = fan-in degree**,
  single color signal. `TreemapCanvas` already exists. Fix: strip longest common directory
  prefix from `scopePaths` before `buildTree` (+ `flattenSingleChild` single-child collapse).
- **Symbol (Neighborhood):** scoped 2D node-link, **1–2 hops only**, rendered AFTER a symbol
  is picked. Add a **code-snippet panel** (data from `seam_snippet`). Empty-graph branch in
  `GraphCanvas` (nodes.length===1 && edges.length===0) → inline empty-state, not full chrome.
- **Areas:** ONE folder-based concept (`deriveAreas`), used on landing AND Overview.
- **Tabs:** explicit tab bar — **Overview / Symbol / Topology**. Kill the contextual-label
  anti-pattern in `App.tsx`. Server admin (freshness/watcher) → bottom status strip.

### Constellation ("Topology") — demoted, done BOTH ways (user decision)
- **Fixed 3D:** DELETE `ClusterHalos.tsx` (root cause); nodes sized/colored by **edge
  degree** (stellar spectral class, hubs bloom); edges as a single `LineSegments` with
  `THREE.AdditiveBlending` + `depthWrite=false` on a dark background (dense regions add
  light, not opacity); drive at cluster/capped granularity; idle-timeout auto-rotate (60s).
- **2D cluster-graph fallback:** ~20–50 cluster nodes + weighted inter-cluster edges from the
  existing `/api/constellation` (`build_constellation`), rendered in the SAME React Flow as
  `GraphCanvas`. Legible by construction. Zero new deps.
- Clicking a cluster hands off INTO the 2D neighborhood/treemap (no navigation within 3D).

### Edge-rendering principle (any retained canvas)
- Additive blending on a dark background; intra-cluster edges brighter than inter-cluster.
  This is the single rendering decision that prevents the blob (proven by codebase-memory-mcp).

## Phased Delivery (linear; each phase = its own PR via the audit→PRD→issues→build→review→docs
pipeline, in a worktree off main)

- **Phase A — Truth & de-noise** (low risk; dimension-independent):
  degree-based + test-excluded ranking (hubs/clusters/treemap sizing) with `show_tests`
  toggle; treemap prefix-strip + single-child collapse; empty-symbol graceful state; Changes
  hides non-code files; stopgap constellation blob-stop (delete `ClusterHalos`).
- **Phase B — Landing, areas, snippet:** search-first landing with degree-ranked entry
  points (drop cluster cards); one folder-based areas concept on landing + Overview; treemap
  cell area = degree + single color signal; code-snippet panel in Symbol view.
- **Phase C — Constellation redesign (both):** fixed legible 3D (degree-size, additive edges,
  cluster granularity, idle rotate) + 2D cluster-graph fallback from `/api/constellation`;
  cluster→2D hand-off.
- **Phase D — Coherent flow + tab model:** explicit Overview/Symbol/Topology tab bar; kill
  contextual-label anti-pattern; server admin → status strip; end-to-end breadcrumbs; polish.

**Phase A is a hard prerequisite** — no point redesigning views that still rank test helpers
first.

## Testing Decisions

- Good tests assert external behavior, not implementation detail. Prior art: existing
  `web/src/__tests__/*` (vitest) for pure lib helpers (`deriveAreas`, `filterBarCounts`,
  `graphFilterState`) and component tests (`DetailPanel.test.tsx`, `FileSidebar.test.tsx`);
  backend `tests/` mirror for `graph_api` helpers.
- Unit-test the pure leaves: degree/test-exclusion ranking helper, treemap prefix-strip +
  single-child collapse, empty-graph predicate, cluster-graph transform.
- Integration-test the Web API changes (`/api/hubs`, `/api/clusters`, `/api/constellation`,
  snippet route) for the `show_tests` param and degree ordering.
- Frontend: gate on vitest + typecheck + build. Rebuild `seam/_web` on merge so `seam serve`
  renders the new UI.

## Out of Scope

- Any SQLite schema change, migration, or re-index (all changes are read-path / frontend /
  additive Web API).
- New MCP tools (count stays 16). Multi-project management (roadmap defers it).
- Full 2000-node 3D navigation, "one-map-two-renders" (panel rejected), semantic edges.

## Further Notes

- Dissent on record: codebase-memory-mcp runs a *working* halo-free 3D view (clean, reads
  degree via bloom) — proving 3D *can* be legible; it is still "semantically opaque without
  the sidebar," which is why 3D is demoted, not led-with.
- The panel called "both 3D + 2D fallback" mild over-engineering for a secondary surface;
  the user chose it deliberately to keep all doors open. Phase C honors that.
