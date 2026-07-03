# Phase 11 Explorer · 3D Constellation Polish — make Topology/3D legible and phenomenal

> Prioritized BEFORE Phase D (the tab-frame). Frontend rendering + server-side
> layout math only. No schema change, no migration, no re-index, no Web API
> contract change, MCP tool count stays 16. Zero new deps (three / R3F /
> @react-three/postprocessing already installed).

## Problem Statement

The 3D "Topology" view is meant to be the orientation "wow" surface — one glance
tells you the codebase's macro shape. Today it does the opposite. Compared to a
known-good reference (codebase-memory-mcp's halo-free 3D view, cited as
dissent-on-record in the redesign spec), ours reads as a broken, overexposed blob:

1. **Nodes punch black holes.** Each node is an opaque lit-material sphere sized
   up to ~16 world units. The edges and Bloom fill the background with additive
   light, and the opaque spheres *occlude* that light — so the biggest, most
   important hubs render as **black orbs** silhouetted against the glow. The most
   load-bearing symbols look like voids. The reference renders nodes as small
   additive glows that *add* light, never subtract it.
2. **The center is blown out to pure white.** Bloom (threshold 0.3, intensity 1.2)
   plus additive edges plus HDR color boosts accumulate in dense regions and clip
   to a featureless white wash — the exact "green/white blob" the redesign set out
   to kill. The reference has bright hub cores but the structure stays readable.
3. **The layout is a lopsided ovoid with a spike, not a balanced globe.** Nodes are
   seeded on a flat XY ring and smeared along Z by call-depth, so the cloud is a
   disc-smear, not a sphere. A few high-`uses`-degree outliers get flung far out,
   producing an ugly orange spike. The reference is an evenly-populated spherical
   shell — instantly legible as "a ball of code."
4. **Color is redundant and the legend lies.** Node color encodes *degree* (a
   red→blue stellar scale) while size *also* encodes degree — the same quantity on
   two channels. Meanwhile the left filter legend shows per-**kind** colors that
   don't match any node. Two signals, one meaning, zero agreement.
5. **Clicking a node goes nowhere.** A node click currently tries to hand off to a
   center symbol but stays inside 3D, so nothing visible happens — a dead
   interaction that erodes trust in the whole view.

The individual pieces (layout, node cloud, edges, bloom) exist and are wired; they
are just **tuned wrong** and **compose badly**. This is a polish-and-retune pass,
not a rebuild.

## Solution

Make the constellation a **legible star-field globe**: an evenly-distributed
sphere of soft, kind-colored glows where importance reads as size and bloom, edges
form a calm web, hubs corona without blowing out, and clicking a star lights up
only its own constellation.

- **Nodes glow, never occlude.** Render nodes as additive, depth-write-off point
  glows (soft round sprites) instead of opaque lit spheres. Bright cores add light
  to the field; they never punch black holes. Size follows a sub-linear (√degree)
  scale so hubs are *noticeably* — not grotesquely — larger.
- **Color = kind, size/glow = degree.** Node hue encodes the symbol kind
  (Function/Class/Method/Interface/Type/Field), matching the filter legend exactly;
  size and brightness encode fan-in degree. One quantity per channel; the legend
  finally tells the truth (user-confirmed decision).
- **A balanced globe.** Seed nodes on a sphere surface (deterministic golden-spiral
  distribution, cluster-grouped by angular locality) and let the force pass refine
  it; recenter to the centroid and clamp radial outliers so no node spikes out. The
  result is a round, evenly-filled ball regardless of node count.
- **Controlled bloom.** Retune Bloom (higher threshold, lower intensity, smoothing)
  and dim the edge field so dense regions read as bright structure, not white paint.
  Additive-on-dark stays the anti-blob principle; we just stop over-driving it.
- **Click isolates, never navigates.** Remove the broken 3D→app hand-off entirely.
  A node click *isolates*: the node and its direct neighbors stay lit, everything
  else dims to the background, edges to the selection brighten, and the camera flies
  to frame the neighborhood. Clicking empty space (or Esc) restores the full field.
  No mode change, no page transition — the 3D view is a self-contained orientation
  spectacle with one real interaction: "show me this star's constellation."

## User Stories

1. As an evaluator, I want the 3D view to read as a clean, evenly-distributed globe
   of code at first glance, so that my first impression builds trust instead of
   destroying it.
2. As a developer, I want the most-connected symbols to appear as the *brightest,
   largest* glows (not black holes), so that "what is load-bearing" is instantly
   visible.
3. As a developer, I want node color to tell me each symbol's KIND (function, class,
   method, …), so that I can read categories at a glance and the filter legend
   matches the stars.
4. As a developer, I want node size and glow to encode importance (degree), so that
   color and size carry *different* information, not the same thing twice.
5. As a developer, I never want a node to render as an opaque black sphere, so that
   the hubs I care about are the ones that shine, not the ones that vanish.
6. As an evaluator, I want the dense core to show bright *structure* rather than a
   featureless white wash, so that I can see hubs and clusters, not paint.
7. As a developer, I want the constellation shaped like a balanced sphere with no
   stray spike, so that the macro-topology (ball / clustered lobes) is honest.
8. As a developer, I want the edge field to be a calm, mostly-teal web, so that it
   supports the nodes rather than overwhelming them with neon.
9. As a developer, I want clicking a node to isolate that node and its direct
   connections (dimming everything else), so that I can read one symbol's
   neighborhood out of the whole field.
10. As a developer, I want the camera to smoothly frame the isolated neighborhood on
    click, so that the selection is easy to inspect.
11. As a developer, I want clicking empty space (or pressing Esc) to restore the full
    field, so that isolate is a reversible lens, not a trap.
12. As a developer, I do NOT want clicking a 3D node to silently change my center
    symbol or exit the view, so that the interaction never "goes nowhere."
13. As a developer, I want hovering a node to show its name · kind · connection
    count, so that I can identify a star without clicking.
14. As a developer, I want a one-line hint ("click a node to isolate · click empty
    space to reset"), so that the single interaction is discoverable.
15. As a developer, I want the layout to stay deterministic and cached, so that the
    globe looks identical across reloads and the tab stays fast.
16. As a maintainer, I want the visual changes localized to the layout math and the
    R3F rendering leaves, so that no schema, API contract, or MCP surface changes.
17. As a developer with reduced-motion preferences, I want auto-rotate and fly-to to
    respect that setting, so that the view is comfortable.

## Implementation Decisions

### Layout: from disc-smear to balanced globe (server-side, `seam/query/layout.py`)

The layout stays server-side, deterministic, cached, and never-raising (unchanged
contract). What changes is the *seeding and shaping*:

- **Spherical seeding.** Replace the flat XY ring seed (`cos/sin × radius`, `z =
  -depth × 50`) with a **deterministic golden-spiral (Fibonacci) sphere**
  distribution, so nodes start spread over a spherical shell rather than a disc.
  Preserve spatial locality by grouping co-located files (the existing first-3-path-
  components cluster key) into contiguous arcs/caps on the sphere, and keep the
  FNV-1a determinism (no `random`, no `time` in the math). Call-depth may modulate
  radius (shell layering) instead of a flat Z smear.
- **Centering + outlier clamp.** After the force pass, recenter positions to their
  centroid and **clamp radial outliers** (e.g. any node beyond mean + k·σ of the
  radial distribution is pulled back to the clamp radius). This kills the spike and
  keeps the ball round. Deterministic and pure.
- **Sub-linear node size.** Change `node_size(kind, degree)` from `base + min(deg ×
  0.3, 10)` (up to ~16) to a **√degree (or log1p) scale with a much smaller base and
  ceiling**, so a top hub is a few× a leaf, not an order of magnitude. Keep it pure
  and unit-tested. Exact constants are a tuning detail for the build, chosen against
  the reference screenshot.
- **No API contract change.** `LayoutNode`/`LayoutEdge`/`LayoutResult` shapes are
  unchanged. The server keeps emitting `color` (stellar) for backward-compat, but
  **the client no longer uses it for fill** (see below) — it is now advisory. This
  keeps the change additive and reversible.

### Node rendering: additive glows, color by kind (`web/src/components/NodeCloud.tsx`)

- **Additive, non-occluding node material.** Render nodes as **additive-blended,
  depth-write-off soft point glows** (round sprite/point rendering with size
  attenuation) instead of opaque `meshBasicMaterial` spheres. Bright nodes ADD light
  to the field and never punch black holes. `depthWrite=false` + `AdditiveBlending`
  mirrors the edge material already in `EdgeLines.tsx`. (Point-sprite rendering is
  the target; an additive emissive InstancedMesh with a soft falloff is the
  documented fallback if raycasting on points proves fiddly — the build agents
  should ground the exact three.js / @react-three/postprocessing API surface in
  current docs, since Points raycasting + Bloom props are version-sensitive.)
- **Color by KIND (user-confirmed).** Fill color comes from the symbol kind
  (`node.label`) via the existing `KIND_COLORS` map in `constellationColors.ts` —
  the same colors the filter legend already shows — with a shared fallback. This
  makes the legend truthful and matches the reference. `KIND_COLORS` becomes the
  single source of truth for node hue (extend/confirm it covers all rendered kinds).
- **Degree drives size + glow, not hue.** Bigger + brighter for higher degree; the
  brightness lift is what makes hubs corona under Bloom (controlled, not clipped).
  Keep the highlight/dim brightness rules but re-derive them from the kind color.
- **Preserve hover + click raycasting.** Whatever the node primitive, hover and
  click must still resolve to a node (raycast threshold for points if needed).

### Edges: a calm web (`web/src/components/EdgeLines.tsx`)

- **Dim the field.** Lower the base non-highlight intensities (currently 0.25 same-
  cluster / 0.06 cross-cluster) and keep cross-cluster much dimmer than intra-cluster
  so the globe reads as structure, not spaghetti. Additive-on-dark stays.
- **Tame loud kinds.** Reduce the visual weight of the most saturated non-`call`
  kinds (`instantiates` orange, `uses` amber, `writes` red) in the no-highlight
  field so they don't create harsh spikes; they regain full color when their
  endpoints are part of an isolated selection. `EDGE_TYPE_COLORS` semantics stay; the
  *intensity* mapping is what's retuned.

### Bloom: bright cores, no white-out (`web/src/components/ConstellationScene.tsx`)

- **Retune the Bloom pass**: raise `luminanceThreshold` (~0.3 → ~0.55–0.7), lower
  `intensity` (~1.2 → ~0.7–0.9), keep/raise `luminanceSmoothing`, tune `radius`, so
  only genuine hub cores bloom and dense regions no longer clip to white. Exact
  values tuned against the reference. Keep the dark teal-void background and
  `dpr=[1,1.5]` (Apple-Silicon MSAA guard) unchanged.

### Interaction: isolate, never navigate (`ConstellationTab.tsx` + `App.tsx`)

- **Remove the broken hand-off.** Delete the 3D node-click → `onFocusSymbol` →
  `setCenterSymbol` path in `App.tsx` so a 3D click NEVER changes the app's center
  symbol or mode. This is the "goes to nowhere" fix. (The App→3D *focus sync*
  direction — flying to a symbol picked in 2D — may stay, but the 3D→App navigation
  is gone. If keeping the inbound sync adds complexity, it may be dropped too; the
  hard requirement is that a 3D node click does not navigate.)
- **Click = isolate.** A node click keeps `computeHighlightedIds` (node + direct
  neighbors), dims all other nodes to background, brightens edges to the selection
  (the existing `both-highlighted` rule), and flies the camera to frame the
  neighborhood (existing `computeCameraTarget`). This is the "see only the connected
  ones" behavior the user asked for — most of it already exists; this wires it as the
  *sole* click action.
- **Deselect.** Clicking empty canvas or pressing **Esc** clears the selection and
  restores the full field (reuse `handleClose`).
- **Hint + tooltip.** Keep the hover tooltip (name · kind · degree) and add a quiet
  one-line hint ("click a node to isolate its connections · click empty space to
  reset"). Copy from the user's side.
- **Reduced motion.** Auto-rotate (60s idle) and the camera fly-to respect
  `prefers-reduced-motion`.

### Constraints (verbatim discipline from the redesign spec)

- No SQLite schema change, migration, or re-index. No Web API contract change (layout
  response shape unchanged). MCP tool count stays 16. Zero new npm/py deps. Each
  touched file stays < 1000 lines. `seam/_web` bundle rebuilt on merge so `seam
  serve` shows the new view. `layout.py` stays pure/deterministic/never-raises.

## Testing Decisions

- **Good tests assert external behavior, not implementation detail.** Prior art:
  `web/src/__tests__/constellation/*` (pure helpers: `computeHighlightedIds`,
  `computeCameraTarget`, `easeOutCubic`, `buildEdgeGeometry`, `computeInstanceColor`,
  `selectLabelNodes`) and `tests/query/test_layout.py` (layout pipeline, `node_size`,
  FNV-1a, BFS depth).
- **Backend (pytest), pure + deterministic:**
  - spherical seeding: seeded points lie on/near a sphere shell; distribution is
    deterministic (same input → identical positions across calls); grouping keeps
    same-cluster nodes angularly close.
  - outlier clamp: an injected far node is pulled within the clamp radius; a normal
    node is untouched.
  - `node_size`: √/log scale is monotonic in degree, bounded, and a hub is only a
    few× a leaf (no order-of-magnitude blowup).
  - unchanged `LayoutResult` shape (regression: keys/types identical).
- **Frontend (vitest), pure helpers:**
  - color-by-kind mapping: each rendered kind → its `KIND_COLORS` hue; unknown kind →
    fallback; the legend map and the node-fill map are the SAME source.
  - retuned edge intensities: `buildEdgeGeometry` still emits only non-dimmed edges;
    same-cluster brighter than cross-cluster; highlighted-pair brightest.
  - **no-navigation regression:** simulating a 3D node select does NOT invoke the
    center-symbol / mode setter (guards the "goes nowhere" fix from regressing).
  - isolate: selecting a node yields highlighted = node + neighbors; deselect clears.
- **Not unit-tested (documented as manual/screenshot):** Bloom values, additive
  material appearance, and the "no black holes / no white-out" look are visual — the
  build agent verifies against the reference screenshot and notes it in the PR.
- **Gate:** ruff + mypy clean, full pytest suite, vitest + `tsc --noEmit` + `vite
  build` green. Rebuild `seam/_web`.

## Out of Scope

- The 2D cluster graph (Phase C shipped it; untouched).
- Phase D (the Overview/Symbol/Topology tab frame + status strip + breadcrumbs) —
  **deferred until after this**; this polish pass is the immediate priority.
- Any schema change, migration, re-index, or Web API contract change.
- New MCP tools (count stays 16). Full 2000-node navigable 3D graph; semantic edges.
- Re-introducing `ClusterHalos` (deleted in Phase A as the blob root cause — stays
  deleted).

## Further Notes

- Reference target: codebase-memory-mcp's 3D view (Image #11 in the request) — cited
  in the redesign spec's "Further Notes" as proof that 3D *can* be legible (halo-free,
  reads degree via bloom). This PRD operationalizes that proof for Seam.
- Frontend-design rationale baked in: **structure encodes truth** (a balanced globe =
  the honest macro-shape; kind = hue, degree = size/glow = one signal per channel);
  **spend boldness in one place** (hub bloom coronas + the isolate-on-click reveal are
  the only loud moments; edges and background stay calm); **restraint** (controlled
  bloom, dim edge field, dark teal-void); **copy from the user's side** (tooltip +
  hint name what the developer does). The single rendering decision that prevents the
  blob — additive-on-dark, nodes that add light instead of occluding it — is applied
  consistently to both nodes and edges.
- Deliberate scope guard: this is a **retune of existing, working machinery**
  (layout seed/shape, node material, edge intensities, bloom values, one interaction
  rewrite), not a new rendering system. If a change starts to require a new dependency
  or an API contract change, it is out of scope for this pass.
