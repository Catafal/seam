# PRD — Phase 11 Explorer Redesign · Phase B (Landing · Areas · Snippet)

> Status: ready-for-agent — 2026-07-02.
> Parent: #213 (Explorer Redesign master). Prerequisite: **Phase A shipped** (PR #228, merged
> `690f36e`) — degree-ranked test-excluded hubs, `/api/hubs?show_tests`, treemap prefix-strip +
> single-child collapse, empty-symbol state, Changes code-file filter, `ClusterHalos` deleted.
> Basis: `docs/prd/phase11-explorer-redesign.md` §"Phase B" + frontend-design pass (color-as-signal,
> spend boldness in one place, structure encodes truth, copy from the user's side).

## Problem Statement

Phase A stopped the rankings from lying, but three things still make the Explorer feel like a pile
of features instead of a coherent tool:

1. **The landing still shows junk "areas."** The "Largest areas" section on the landing is fed by
   `useClusters()` — the Louvain community list, whose labels are test-derived strings like
   `unit — _sym`. Meanwhile the *Overview* tab already shows a clean, folder-based areas concept
   (`deriveAreas`). The developer sees **two contradictory definitions of "area"** depending on which
   screen they are on. One of them is the good one; the other should not exist.

2. **The treemap sizes and colors by the wrong thing.** Cell area is symbol *count* and cell color is
   a random per-name hash (`hashColor`). So the biggest, most eye-catching cell is the most *numerous*
   file, and its color means nothing. A developer scanning the map cannot tell what is architecturally
   load-bearing — the map has two visual channels (size, color) and neither carries signal.

3. **Opening a symbol never shows its source.** The Symbol detail panel lists callers, callees,
   docstring, and comments — but to actually read the code you must leave the Explorer and open the
   file in an editor. Every mature code-navigation tool (Sourcetrail, Sourcegraph) keeps the source
   one panel away from the graph. Seam already ships the `/api/snippet` route and a `useSnippet` hook;
   nothing consumes them.

## Solution

Make the drill path **repo → area → file → symbol → source** cohere around one idea and one signal.

- **One areas concept, everywhere.** The landing and the Overview both derive their areas from the
  same folder-based `deriveAreas`. The junk cluster cards are deleted from the landing. "Area" means
  the same thing on every screen.
- **Fan-in degree is the treemap's one signal.** Cell area encodes fan-in degree (coupling — how
  depended-upon the code is), and a single sequential color ramp encodes the same quantity, so size and
  color reinforce each other instead of competing. This is the map's one bold move; everything else on
  it stays quiet zinc. The most prominent, hottest cell is the most load-bearing code — the thing you
  should understand first — not merely the most numerous.
- **Source is one panel away.** The Symbol detail panel gains a collapsible **Source** section that
  shows the selected symbol's exact indexed source via `useSnippet`, with a freshness note when the
  index is behind the file on disk. Reading the implementation no longer requires leaving the Explorer.

No SQLite schema change, no migration, no re-index, no new MCP tool (count stays 16). One additive Web
API field (`degree` on the structure rows) is the only backend change.

## User Stories

1. As a developer landing on the Explorer, I want the "areas" I see on the landing to be the same
   folder-based areas I see in the Overview, so that "area" means one consistent thing.
2. As a developer, I want the landing to stop showing cluster cards labeled after test files
   (`unit — _sym`), so that my first impression is my architecture, not my test scaffolding.
3. As a developer, I want each landing area card to show its name, its size, and a few of its
   highest-degree key symbols, so that I can recognize what part of the codebase it is at a glance.
4. As a developer, I want to click a landing area and drop straight into its file treemap, so that the
   landing and the Overview share one drill path.
5. As a developer, I want the landing areas to exclude tests by default with a toggle to include them,
   so that the 72%-test ratio does not bury the source areas (consistent with Phase A's hub toggle).
6. As a developer scanning the treemap, I want the biggest cells to be the most depended-upon code
   (highest fan-in degree), so that visual weight matches architectural weight.
7. As a developer, I want treemap cell color to encode that same degree on a single sequential ramp, so
   that size and color agree and the hottest cell is unmistakably the most-coupled code.
8. As a developer, I want a legend/scale for the degree color ramp, so that I can read what "hot" means
   without guessing.
9. As a developer, I want files and folders with zero indexed edges to still be visible (just cool and
   small), so that the map never hides code — low coupling is a reading, not an omission.
10. As a developer who has opened a symbol, I want a Source section in the detail panel showing its
    exact indexed source, so that I can read the implementation without leaving the Explorer.
11. As a developer, I want the Source section collapsed-by-default or compact, so that it does not push
    callers/callees off screen on a hub symbol.
12. As a developer reading a snippet, I want a note when the index is stale relative to the file on
    disk, so that I do not trust source lines that may have moved.
13. As a developer, I want the Source section to show a clear, actionable empty state when no source is
    available (symbol not found, ambiguous, or file unreadable), so that an empty panel never looks
    like a bug.
14. As a developer opening a symbol with multiple definitions (homonyms), I want the Source section to
    show the source for the definition the panel is already displaying, so that source and metadata
    agree.
15. As a developer, I want the snippet fetched only once I have actually selected a symbol, so that the
    panel does no source IO while I am just browsing the graph.
16. As a maintainer, I want the areas derivation to live in exactly one place, so that landing and
    Overview cannot drift apart again.
17. As a maintainer, I want the degree-to-color mapping to be a pure, tested function, so that the one
    signal on the map is deterministic and cannot silently regress.
18. As a maintainer, I want `/api/structure` to carry a `degree` per symbol as an additive field, so
    that the treemap can size by degree without a second round-trip or a schema change.

## Implementation Decisions

### The one signal: fan-in degree (color-as-information)

- Per the frontend-design pass, the treemap spends its boldness in exactly one place: **degree**. Cell
  **area** = rolled-up fan-in degree; cell **color** = the same degree on a single sequential ramp
  (cool zinc → hot accent). Size and color are two encodings of one truth, so they reinforce rather
  than compete. The random per-name `hashColor` is retired for leaf sizing/coloring — a hash is
  decoration, and decoration on the primary signal is the thing to cut.
- "Fan-in degree" for the treemap = the symbol's incoming-edge count (how depended-upon it is), rolled
  up to files/dirs by summation. This matches Phase A's hub ranking so the map and the hub list tell
  the same story. Rolling up by summation (not max) is deliberate: a folder full of moderately-coupled
  files should read as heavier than a folder with one hot file, because changing it touches more.
- Zero-degree code stays visible: it renders at the floor size and the coolest color. Low coupling is a
  reading the developer should be able to see, never an omission. (Squarify already drops strict
  zero-value cells; the size metric uses `max(degree, 1)` so isolated-but-present symbols still get a
  minimum cell.)

### One areas concept (delete the landing cluster cards)

- The landing's "Largest areas" section stops calling `useClusters()` and instead derives areas from
  the **same** folder-based `deriveAreas` the Overview uses, fed by `useStructure` + `useHubs`. The
  cluster-card code path on the landing is deleted.
- To guarantee landing and Overview cannot drift, the fetch-and-derive is extracted into a single
  shared hook (a thin `useAreas({ includeTests })` that composes `useStructure` + `useHubs` +
  `deriveAreas`). Both screens consume it. `deriveAreas` itself is unchanged (already pure + tested).
- The landing shows the top N areas (degree/size-ranked, tests excluded by default, same toggle
  semantics as the Phase A hub toggle) with an "open Overview for the full map →" affordance. Clicking
  a landing area enters the Overview treemap scoped to that area (same drill the Overview already
  performs) — the landing becomes a curated entry into the one Overview flow, not a parallel universe.
- `useClusters` is NOT removed globally — the DetailPanel cluster legend and the cluster color stripe
  still use it. Only the landing's *areas* consumption of clusters is removed.

### Source panel in the Symbol view

- The DetailPanel gains a **Source** section rendered below the signature/docstring (or collapsed), fed
  by the existing `useSnippet` hook against `/api/snippet`. The selector is built from the definition
  the panel is already showing (`firstDef` — file + line), so on a homonym the source matches the
  displayed metadata. Fetch is gated on a selected symbol (story 15) and is naturally lazy (the panel
  only mounts when a symbol is selected).
- A small pure helper builds the snippet selector from the symbol + its first definition (prefer a
  file+line selector so the exact displayed definition is retrieved rather than an arbitrary homonym).
- Rendering: monospaced, wrapped/scrollable within a bounded max-height so a large function cannot blow
  out the panel; the section is quiet (no syntax-highlight dependency — plain mono on zinc, consistent
  with the existing signature `<pre>`). `truncated` metadata surfaces a "showing N of M lines" note.
- **Freshness:** when `SnippetResponse.freshness.index_stale` (or `file_hash_matches === false`) is
  true, show a compact amber note: the displayed source may not match the file on disk. This reuses the
  contract the snippet route already returns.
- **Copy (frontend-design "write from the user's side"):** empty/failure states speak in the
  interface's voice and are actionable, never system-shaped —
  - not found → "No indexed source for this symbol. Try re-running `seam init`."
  - ambiguous → "Several definitions match — pick one from Definitions above."
  - unreadable/`found:false` with a reason → surface the human-readable `message`/`reason`.
  Never render a bare empty box.

### Backend: additive `degree` on structure rows

- `list_structure` (graph_api) gains a per-symbol `degree` (incoming edge count, matching the hub
  metric) via an additive aggregate over the `edges` table. `StructureSymbol` (Pydantic) and the
  `/api/structure` response gain `degree: int`. The frontend `StructureSymbol` type + `buildTree`'s
  `TreeNode` gain a `degree` field, with a `rollupDegree` pass mirroring the existing `rollupCounts`.
- This is the ONLY backend change: additive field, no schema change, no migration, no new route, no new
  MCP tool. Pre-existing consumers that ignore `degree` are unaffected. Watch the `web.py` <1000-line
  gate (Phase A already trimmed it to 999) — if adding the field pushes it over, extract the affected
  route helper rather than inflating the file.

### Deep modules (built or reused)

- **`list_structure` degree aggregate (backend, deep):** encapsulates the edge-count join behind the
  existing simple row interface; the treemap gets degree with no extra round-trip.
- **`degreeColor(degree, maxDegree) → hex` (frontend, pure, deep-ish):** the single-signal sequential
  ramp. Pure, deterministic, tested in isolation. Replaces `hashColor` for the degree signal.
- **`rollupDegree` + `TreeNode.degree` (frontend, pure):** mirrors `rollupCounts`; sizes the treemap by
  degree. Pure, tested.
- **`useAreas({ includeTests })` (frontend, thin composition hook):** the single fetch-and-derive shared
  by landing + Overview so the areas concept cannot fork. Wraps the already-pure `deriveAreas`.
- **`buildSnippetSelector(symbol, firstDef) → SnippetSelector` (frontend, pure):** builds the exact
  file+line selector for the displayed definition. Pure, tested.
- **`deriveAreas` (frontend, reused unchanged):** already pure + unit-tested; now consumed on the
  landing too.

## Testing Decisions

- A good test asserts **external behavior**, not implementation detail: given inputs, assert the
  rendered/returned result and the observable branch (empty state, stale note, degree ordering) — not
  private call sequences. Prior art: `web/src/__tests__/*` vitest suites for pure libs (`deriveAreas`,
  `buildTree`, `filterBarCounts`, `graphFilterState`) and components (`DetailPanel.test.tsx`,
  `FileSidebar.test.tsx`); backend `tests/` mirror for `graph_api` helpers (`tests/unit/test_graph_api.py`
  added in Phase A).
- **Pure leaves (unit):**
  - `degreeColor` — monotonic ramp, floor/ceiling clamping, `maxDegree === 0` guard (no divide-by-zero,
    returns the cool floor), determinism.
  - `rollupDegree` / `buildTree` degree — a class with N high-degree methods rolls up correctly; dirs
    sum children; zero-degree symbol yields floor, not omission.
  - `buildSnippetSelector` — file+line selector from a definition; missing-definition guard.
  - `deriveAreas` — existing suite still green (unchanged behavior).
- **Backend (integration):** `/api/structure` returns `degree` per row and the value matches the
  incoming-edge count for a known fixture symbol; `degree` is present and 0 for an isolated symbol.
- **Components:**
  - Landing area cards render from `deriveAreas` output and NOT from clusters; the test-toggle flips
    included areas; clicking a card enters the scoped Overview.
  - DetailPanel Source section: renders source when `useSnippet` returns `found:true`; shows the
    actionable empty copy on `found:false`/ambiguous; shows the amber stale note when
    `freshness.index_stale` is true; is not fetched when no symbol is selected.
- **Gate:** frontend vitest + typecheck + build; backend `make gate` (ruff + mypy + pytest). Rebuild and
  force-add `seam/_web` on merge so `seam serve` renders the new UI (the standing bundle gotcha).

## Out of Scope

- Any SQLite schema change, migration, or re-index. `degree` is an additive read-path aggregate.
- New MCP tools (count stays 16). New routes beyond the additive `degree` field on `/api/structure`.
- Syntax highlighting in the Source panel (plain mono is deliberate — no new dependency; revisit later).
- Editing/opening the file in an external editor from the panel (a deep-link is a later polish).
- The constellation redesign (Phase C) and the tab-model/status-strip/breadcrumbs work (Phase D).
- Changing `deriveAreas`'s derivation algorithm — it is reused as-is; only its consumers change.
- Removing `useClusters` globally — the DetailPanel cluster legend/stripe still use it.

## Further Notes

- **Why fan-in (incoming) degree, not total degree:** the treemap answers "what is load-bearing —
  what should I understand and be careful changing." That is fan-in (how many things depend on this),
  which is also the Phase A hub metric, so the map and the hub list stay consistent. Total degree would
  let a high-fan-out orchestrator masquerade as load-bearing.
- **Why a sequential ramp, not categorical color:** degree is a continuous quantity; a sequential
  lightness/saturation ramp reads as "more/less" at a glance, whereas categorical hues read as
  "different kinds." Encoding one quantity with one ordered channel is the whole point of the single
  signal.
- **Landing vs Overview:** after Phase B the landing is a *curated slice* of the Overview (top areas +
  top hubs + search), not a separate view with its own data model. This is the through-line Phase A/B
  are collectively buying; Phase D formalizes it into an explicit tab model.
- **Bundle gotcha (standing):** `seam/_web` is gitignored but force-committed; after the frontend build
  the new hashed assets must be `git add -f`'d or a merged `main` serves an index.html that 404s its
  assets. Verify independently before opening the PR.

## B2 implementation notes

**What changed:**

- `seam/server/graph_api.py` — `list_structure()` gains a `degree` field per symbol row.
  Computed by a LEFT JOIN against a pre-aggregated `(target_name, COUNT(*))` subquery over
  the `edges` table — one scan of edges, no correlated sub-select per row. `COALESCE(d.degree, 0)`
  ensures isolated symbols (no incoming edges) return `degree=0`. The dict returned per row
  now includes `"degree": r["degree"]`. No schema change; no re-index required.

- `seam/server/web_schema.py` — `StructureSymbol` and `StructureResponse` Pydantic models moved
  here from `web.py` (to keep `web.py` under 1000 lines; it was at 999). `StructureSymbol` gains
  `degree: int` with a docstring explaining the B2 semantics.

- `seam/server/web.py` — removed the old `StructureSymbol`/`StructureResponse` class definitions
  (~15 lines freed). Updated `from seam.server.web_schema import ...` to include the two moved
  models. The `/api/structure` route mapping now passes `degree=r["degree"]` to `StructureSymbol`.
  Final line count: **984** (under 1000).

- `web/src/api/types.ts` — `StructureSymbol` schema extended with `degree: number` (plus JSDoc).
  This is the hand-maintained types file (codegen output); the `degree` field is additive and
  non-optional, so existing consumers that construct literal `StructureSymbol` objects needed
  `degree: 0` added to their fixtures.

- `web/src/__tests__/buildTree.test.ts`, `FileSidebar.test.tsx`, `lib.test.ts` — all
  `StructureSymbol` fixture objects updated with `degree: 0` so TypeScript typecheck passes.

**Decisions:**

- **Fan-in only (not total degree):** the PRD is explicit — degree = incoming edges (how many
  things depend on this symbol). Total degree was rejected because a high-fan-out orchestrator
  would masquerade as load-bearing.
- **LEFT JOIN aggregate, not correlated subquery:** a single pre-aggregated subquery over edges
  followed by a LEFT JOIN is O(edges + symbols) rather than O(edges × symbols). The existing
  `top_hub_symbols` query uses `UNION ALL / GROUP BY` for total degree; `list_structure` uses
  only the `target_name` side for fan-in, keeping semantics distinct.
- **`StructureSymbol`/`StructureResponse` moved to `web_schema.py`:** the 1000-line constraint
  on `web.py` is load-bearing. Moving those two classes (saves ~15 lines) absorbs the +2 lines
  added by the `degree` field + route mapping update, leaving 984 lines total.
- **TDD:** two new tests (`test_list_structure_degree_incoming`, `test_list_structure_degree_field_present`)
  written and confirmed failing before implementation. The existing key-set assertion in
  `test_list_structure_returns_path_and_nesting` was updated to include `"degree"` as part of the
  same red-green cycle.

**Gate status:** ruff clean, mypy clean (120 files), 2533 Python tests passed (4 skipped),
378 frontend vitest tests passed, frontend typecheck clean.

## B1 implementation notes

**What changed:**

- `web/src/api/hooks.ts` — Added `useMemo` import from `"react"`. Added `useAreas({ includeTests })` hook
  at the bottom of the file. It composes `useStructure(true)` + `useHubs(60)` + `deriveAreas` via `useMemo`
  and returns `{ areas: Area[], isLoading: boolean }`. This is the single derivation site for the "area"
  concept — landing and Overview both consume it from here. `deriveAreas` itself is unchanged.

- `web/src/components/StructureOverview.tsx` — Removed the inline `useStructure` + `useHubs` + `deriveAreas`
  wiring; replaced with `useAreas({ includeTests })`. Added optional `initialArea?: Area | null` prop: when
  set (from a landing card click), `useState(initialArea ?? null)` makes the component start in the drilled
  treemap view immediately on mount. Since `StructureOverview` unmounts/remounts on every mode change, the
  initial-value pattern is sufficient — no `useEffect` needed.

- `web/src/App.tsx` — Changes to `LandingPage`:
  - Removed `useClusters` call and `topAreas` derivation from clusters.
  - Removed `clusterColor` and `ClusterItem` imports (no longer used in LandingPage).
  - Added `useAreas({ includeTests: showTests })` — the same `showTests` toggle controls both hub chips
    and area cards (one concept, one toggle).
  - Area cards now render `Area` objects: name, `fileCount files · symbolCount symbols`, up to 3 `keySymbols`.
  - `LandingPageProps` extended with `onOpenScopedOverview: (area: Area) => void`.
  - Added `preselectedArea: Area | null` state to `App`.
  - Added `handleOpenScopedOverview` callback: sets `preselectedArea` + `mode = "overview"`.
  - Header Overview toggle resets `preselectedArea = null` when switching TO overview (so the area-cards
    list shows first on a header-triggered open). The goHome action also clears it.
  - `StructureOverview` now receives `initialArea={preselectedArea}`.
  - `LandingPage` `onOpenOverview` now also clears `preselectedArea` before setting the mode.

**Decisions:**

- **Single toggle for hubs + areas:** the PRD says "mirror the existing Phase A show-tests hub toggle
  semantics." Sharing the same `showTests` state for both sections avoids a second toggle and makes the
  behaviour obvious: one switch, one meaning ("include tests" across the whole landing).
- **`initialArea` as `useState` initial value (not `useEffect`):** `StructureOverview` unmounts when mode
  leaves "overview", so it remounts fresh every time. The `useState(initialArea ?? null)` pattern is
  sufficient and simpler than syncing via an effect — no ref, no extra render.
- **`preselectedArea` reset on header open:** without the reset, navigating away from overview and back via
  the header would re-drill the last landing-clicked area. Clearing on the header toggle ("entering overview
  fresh") vs. the landing handler ("entering scoped") is the minimal stateful distinction needed.
- **`useClusters` kept globally:** the DetailPanel cluster legend and SymbolNode color stripe still reference
  it. Only the landing's areas section was migrated. No other consumer changed.

**TDD cycle:**
- `web/src/__tests__/useAreas.test.tsx` (4 tests) — written failing, then `useAreas` added to pass.
- `web/src/__tests__/LandingAreas.test.tsx` (5 tests) — written failing, then `LandingPage` refactored to pass.
  Two fixups after initial run: `useMemo` import source (React, not TanStack), `StructureSymbol` fixture
  shape (no `file`/`is_test` fields), `globalThis` instead of `global` for jsdom compat, `getAllByText`
  for "query" which appears both as hub chip and area name.

**Gate status:** 387 frontend vitest tests passed, frontend typecheck clean, build green.
