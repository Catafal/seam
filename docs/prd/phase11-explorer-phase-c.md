# PRD — Phase 11 Explorer Redesign · Phase C (Constellation, done BOTH ways)

> Status: ready-for-agent — 2026-07-03.
> Parent: #213 (Explorer Redesign master). Prerequisites: **Phase A** (PR #228) and **Phase B**
> (PR #244) shipped and merged. Basis: `docs/prd/phase11-explorer-redesign.md` §"Phase C" + the
> frontend-design pass (structure encodes truth, one signal per view, spend boldness in one place,
> copy from the user's side).

## Problem Statement

The 3D constellation is the Explorer's "wow" surface, but today it is a dead end for actually
*understanding* the repo:

1. **It answers no question a developer can act on.** Even now that Phase A deleted the green-blob
   `ClusterHalos`, the 3D view is a spatial cloud of individual symbols. A developer can spin it, but
   cannot read "how do my functional areas couple — is this a hub-and-spoke, a mesh, or a chain?"
   from a cloud of 2,000 nodes in perspective. It is "semantically opaque without the sidebar."
2. **The one honest whole-repo overview Seam already computes is dead on the frontend.**
   `build_constellation` (`/api/constellation`) returns exactly the macro-topology answer — ~20–50
   clusters plus weighted inter-cluster links — but nothing renders it. The backend does the work; the
   UI throws it away.
3. **The "wow" view does not connect to navigation.** Selecting a node in 3D can sync a 2D center, but
   there is no deliberate "take me from this region into the code" hand-off. The spectacle is walled off
   from the drill path Phases A/B built (repo → area → file → symbol → source).

## Solution

Keep the constellation **both** ways, and make each one legible at the job it is actually good at.

- **A 2D cluster-graph is the legible, default Topology view.** Render `build_constellation`'s clusters
  and weighted links as a small node-link graph (~20–50 nodes) in the **same React Flow** the
  neighborhood already uses. Legible by construction: a developer sees the macro shape — star, mesh, or
  chain — at a glance. This is the honest answer to "how does this codebase couple at the top level."
- **The 3D constellation is the demoted "wow" alternative.** It already satisfies the redesign spec
  after Phase A (halos deleted; nodes sized/colored by degree as stellar spectral classes; edges as a
  single additive-blended `LineSegments` with `depthWrite=false` on a dark ground; 60s idle
  auto-rotate; camera fly-to). Phase C does **not** rebuild it — it puts it behind a 2D/3D toggle where
  2D leads.
- **Clicking a cluster hands off INTO the 2D drill path.** Selecting a cluster in either view centers
  the 2D neighborhood on that cluster's representative (hub) symbol and switches to the neighborhood
  view — the spectacle finally connects to the code. No navigation happens *within* the 3D scene.

One additive Web API field (`representative` on each `/api/constellation` cluster, reusing the exact
query `/api/clusters` already uses) is the only backend change. Zero new dependencies — React Flow and
three/R3F are already installed. No SQLite schema change, no migration, no re-index, no new MCP tool
(count stays 16).

## User Stories

1. As a developer opening the Topology view, I want a legible 2D cluster map by default, so that I can
   read the repo's macro shape without fighting a 3D cloud.
2. As a developer, I want each cluster drawn as a node sized by how many symbols it contains, so that
   the biggest areas are visually the biggest.
3. As a developer, I want inter-cluster links drawn with width proportional to their coupling weight,
   so that I can see which areas are tightly bound and which are loosely connected.
4. As a developer, I want each cluster colored by its identity (the existing cluster palette), so that
   the same cluster reads consistently here, in the treemap legend, and in the detail panel stripe.
5. As a developer, I want to tell at a glance whether the architecture is a hub-and-spoke, a mesh, or a
   chain, so that I understand the coupling structure before diving in.
6. As a developer, I want to hover a cluster to see its label and size, so that I can identify it
   without clicking.
7. As a developer, I want to click a cluster and land in the 2D neighborhood centered on that cluster's
   most-connected symbol, so that the overview hands me into the actual code.
8. As a developer, I want the 3D constellation still available behind a toggle, so that I keep the
   spatial "wow" view when I want it.
9. As a developer, I want the 2D/3D choice to default to 2D, so that the legible view leads and the
   spectacle is opt-in (the redesign thesis: 3D demoted, not led-with).
10. As a developer, I want the 2D cluster graph to fit the viewport on load, so that I never open onto
    an empty or off-screen canvas.
11. As a developer on a repo with no clusters (pre-v4 or empty index), I want a clear empty state, so
    that an empty canvas never looks like a bug.
12. As a developer, I want the 2D cluster graph to reuse the same React Flow interactions (pan, zoom,
    fit) as the neighborhood, so that navigation feels consistent across the app.
13. As a developer, I want clusters laid out deterministically, so that the map does not jump around
    between visits to the same index.
14. As a developer, I want the 3D scene to remain exactly as legible as it is now (no green blob,
    degree-sized stellar nodes, additive edges, idle rotate), so that switching to it is still useful.
15. As a developer selecting a node in 3D, I want an explicit "open in 2D" hand-off, so that the 3D
    view connects to the drill path instead of dead-ending.
16. As a maintainer, I want the cluster-graph layout to be a pure, tested function, so that its node/
    edge shaping and sizing cannot silently regress.
17. As a maintainer, I want `/api/constellation` to carry a `representative` per cluster as an additive
    field computed by the same query `/api/clusters` already uses, so that the hand-off has a target
    without a second round-trip or a divergent source of truth.
18. As an evaluator, I want my first impression of the Topology view to build trust, so that the
    constellation earns its place instead of destroying credibility with an opaque blob.

## Implementation Decisions

### The one signal per view (structure encodes truth)

- **2D cluster graph — signal = inter-cluster coupling.** Cluster **node size** ∝ cluster symbol count;
  **edge width** ∝ link weight (cross-cluster edge count from `build_constellation`); **node color** =
  the existing `clusterColor(cluster_id)` palette (identity, shared with the treemap legend and detail
  stripe). Nothing else competes. The signature element is the legible macro-shape read (star/mesh/
  chain) — the question the 3D cloud can never answer at a glance.
- **3D constellation — unchanged.** Its signal (degree via stellar spectral class + bloom on hubs) is
  already correct post-Phase-A. Phase C treats it as done and only fronts it with the toggle.

### The 2D cluster-graph fallback (the net-new deliverable)

- A thin `useConstellation()` hook fetches `/api/constellation` (clusters + links). The frontend type
  for the response already exists; the hook mirrors the other read hooks.
- A **pure** `clusterGraphLayout(clusters, links, opts)` deep module transforms the API envelope into
  React Flow `nodes` + `edges`: assigns a **deterministic** position per cluster (a radial/circular
  arrangement ordered by size — largest toward the center or a stable ring; no physics simulation, so
  it never jitters and is trivially unit-testable), maps size → node dimension, weight → edge width/
  opacity, and `cluster_id` → color. Bounded to the ~20–50 clusters the API returns. Pure: no DB, no
  React, no WebGL — unit-tested in isolation. (A deterministic layout is deliberately chosen over a
  force simulation: it is legible enough for 20–50 nodes, reproducible story-13, and testable
  story-16, per "match complexity to the vision — minimal directions need precision, not elaboration.")
- A `ClusterGraph2D` component renders the layout in `@xyflow/react` (the same library `GraphCanvas`
  uses — zero new deps), with pan/zoom/fit-to-view on load, cluster labels, hover, and a cluster-click
  callback. It is a sibling of `GraphCanvas`, not a modification of it (different node semantics:
  clusters, not symbols).
- **Empty state (copy from the user's side):** no clusters → "No clusters yet — run `seam init` to
  build the index." Never a bare empty canvas.

### The 2D/3D toggle (2D leads)

- The existing "Constellation" surface gains a 2D/3D sub-toggle. **Default = 2D cluster graph** (the
  legible view leads; 3D is opt-in), honoring the redesign thesis that the constellation is demoted.
  The 3D path is exactly today's `ConstellationTab` (lazy-loaded so three/R3F stays out of the initial
  bundle until the user asks for 3D). The toggle is a quiet control; it does not relabel itself with the
  *other* mode's name (the anti-pattern Phase D will also stamp out).

### The cluster → 2D hand-off

- Clicking a cluster (in the 2D graph, and from the 3D node-detail path) calls a single hand-off:
  center the 2D **neighborhood** on the cluster's `representative` symbol and switch to neighborhood
  mode. A small pure resolver maps a clicked cluster to its hand-off target (representative name), so
  the wiring is testable without the canvas.
- **Backend enabler (C1, additive):** `build_constellation` adds a `representative` (a member symbol
  name — the cluster's hub) to each cluster, computed with the **same** representative query
  `/api/clusters` already runs (single source of truth — the two endpoints must not diverge).
  `ConstellationCluster` (Pydantic) + the frontend response type gain `representative: str | None`.
  No schema change, no migration, no re-index, no new route, no new MCP tool. Watch the `web.py`
  <1000-line gate (currently 989) — extract a helper rather than cross it.

### Deep modules (built or reused)

- **`build_constellation` representative (backend, deep):** reuses the existing `/api/clusters`
  rep-query so the hand-off target is consistent everywhere. Additive.
- **`clusterGraphLayout(clusters, links, opts) → {nodes, edges}` (frontend, pure, deep):** the whole
  transform + deterministic layout behind one simple signature. Unit-tested.
- **`useConstellation()` (frontend, thin hook):** one fetch of the already-typed response.
- **`ClusterGraph2D` (frontend, component):** React Flow render of the layout; cluster-click hand-off.
- **cluster→handoff resolver (frontend, pure):** clicked cluster → representative target. Unit-tested.
- **`ConstellationScene` / `NodeCloud` / `EdgeLines` (frontend, reused unchanged):** the 3D path is
  spec-complete; Phase C does not touch its internals.

## Testing Decisions

- A good test asserts **external behavior** (rendered nodes/edges, node sizes proportional to cluster
  size, edge widths proportional to weight, the click hand-off target, the empty state) — not private
  layout math or React internals. Prior art: `web/src/__tests__/*` vitest for pure libs
  (`deriveAreas`, `buildTree`, `degreeColor`, `computeHighlightedIds`, `buildEdgeGeometry`) and
  components (`DetailPanel.test.tsx`); backend `tests/` mirror for `graph_api` helpers +
  `tests/integration/test_web_api.py` for routes.
- **Pure leaves (unit):**
  - `clusterGraphLayout` — N clusters → N nodes with deterministic positions; node size monotonic in
    cluster size; edge width monotonic in link weight; color from `clusterColor`; empty input → empty
    graph; stable across calls (determinism).
  - cluster→handoff resolver — cluster with a representative → that target; missing representative →
    graceful fallback (e.g. label, or no-op) rather than a broken center.
- **Backend (integration):** `/api/constellation` returns a `representative` per cluster matching the
  value `/api/clusters` returns for the same cluster (single source of truth); `null` for a cluster
  with no resolvable member; envelope still `{clusters, links}` shaped.
- **Components:** `ClusterGraph2D` renders one node per cluster from the layout and fires the hand-off
  callback with the right cluster on click; the empty state renders when there are no clusters; the
  Topology toggle defaults to 2D and switches to the lazy 3D path on demand.
- **Gate:** frontend vitest + typecheck + build; backend `make gate` (ruff + mypy + pytest). Rebuild +
  force-add `seam/_web` on merge (standing bundle gotcha).

## Out of Scope

- **Rebuilding the 3D constellation.** It already satisfies the redesign spec after Phase A (no halos,
  degree-sized stellar nodes, additive `depthWrite=false` edges, bloom, 60s idle rotate). Phase C only
  fronts it with the toggle and the hand-off — it does not modify `ConstellationScene`/`NodeCloud`/
  `EdgeLines` internals.
- A force-directed physics simulation for the 2D layout (a deterministic radial layout is chosen
  deliberately — legible, reproducible, testable).
- Navigation *within* the 3D scene (drilling cluster→cluster in 3D). Hand-off always exits to 2D.
- Any SQLite schema change, migration, or re-index. `representative` is an additive read-path field.
- New MCP tools (count stays 16). New routes beyond the additive field on `/api/constellation`.
- The explicit Overview/Symbol/Topology tab bar, status strip, and end-to-end breadcrumbs (Phase D).

## C1 implementation notes

**Slice:** C1 — additive `representative` field on `/api/constellation` (issue #251).

**What changed:**

- `seam/server/graph_api.py` — extracted a new public helper `fetch_cluster_representatives(conn) -> dict[int, str]` that runs `SELECT cluster_id, name, MIN(id) FROM symbols WHERE cluster_id IS NOT NULL GROUP BY cluster_id`. This is the single source of truth for the representative query; it is now used by BOTH `build_constellation()` and `get_clusters()` in `web.py`.
  - `build_constellation()` calls `fetch_cluster_representatives()` after fetching the cluster rows and attaches `representative: representatives.get(r["id"])` to each cluster dict (`None` for orphan clusters with no members).
- `seam/server/web.py` — `ConstellationCluster` Pydantic model gains `representative: str | None = None`. `get_constellation()` route unchanged — `ConstellationCluster(**c)` already unpacks the dict and Pydantic picks up `representative`. `get_clusters()` now calls `fetch_cluster_representatives(conn)` instead of running the inline SQL query, eliminating the prior divergence risk. `web.py` stayed at 991 lines (under the 1000-line gate).
- `web/src/api/types.ts` — `ConstellationCluster` gains `representative: string | null` with a JSDoc comment explaining the C1 contract.

**New tests (TDD — written before implementation):**

- `tests/unit/test_graph_api.py`:
  - `test_constellation_cluster_includes_representative` — cluster dict has `representative` key, non-null when a member exists.
  - `test_constellation_representative_null_when_no_member` — orphan cluster row (no symbols assigned) → `representative` is `None`.
  - `test_constellation_representative_consistent_with_clusters_query` — cross-check: `build_constellation` representative for each cluster equals what the raw `/api/clusters` rep query returns.
- `tests/integration/test_web_api.py`:
  - `test_constellation_clusters_have_representative_field` — HTTP response has `representative` key on every cluster.
  - `test_constellation_representative_matches_clusters_endpoint` — seeds a cluster in the indexed repo DB and verifies `/api/constellation` and `/api/clusters` return identical representatives for the same cluster_id.

**Decision: export `fetch_cluster_representatives` (not private).** Initially considered a private `_fetch_cluster_representatives`, but since `web.py` needs to import and call it, it must be public. Making it public also makes it directly unit-testable and available for future slices (e.g. the 2D layout component may need the same lookup).

**Gate:** ruff clean, mypy clean (122 files), 82 tests passed (0 failed), frontend typecheck clean.

---

## Further Notes

- **Why the 2D cluster graph and not "just fix 3D":** per the first-principles judge panel (Ghoniem
  2004; every mature tool), a whole-repo node-link graph is illegible past ~200 nodes in ANY dimension.
  The fix is not a better 3D cloud — it is showing the *right* number of nodes (the ~20–50 clusters),
  which is exactly what `build_constellation` already produces. 3D stays for spectacle; 2D answers the
  question.
- **Why default to 2D:** the redesign's recorded decision keeps the constellation "both ways" but
  demoted. Leading with the legible view builds trust on first impression; the 3D "wow" is one click
  away for those who want it. codebase-memory-mcp's clean 3D proves 3D *can* be legible, but it is
  still opaque without a sidebar — which is why it is the alternative, not the default.
- **Hand-off target choice:** centering the neighborhood on the cluster's representative (hub) symbol
  reuses the exact affordance the pre-Phase-B landing already used (`ClusterItem.representative`) and
  the drill path Phases A/B built — so the "wow" view now feeds the same coherent flow.
- **Bundle gotcha (standing):** `seam/_web` is gitignored but force-committed; rebuild + `git add -f`
  before the PR or a merged `main` serves an index.html that 404s its assets.
