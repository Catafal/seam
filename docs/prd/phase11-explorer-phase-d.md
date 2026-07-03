# Phase 11 Explorer Redesign · Phase D — Coherent flow: tab model, status strip, breadcrumbs

> Closes the Explorer redesign (A→D). Frontend-only + one additive Web API field.
> No schema change, no migration, no re-index, MCP tool count stays 16. Zero new deps.

## Problem Statement

The Explorer has three genuine modes — browse the structure, study one symbol,
see the macro shape — but the header doesn't say so. Navigation is expressed as
**contextual-label toggles**: a single button reads "Overview" when you're in
Neighborhood and "Neighborhood" when you're in Overview, so the control is
labeled with the mode you are NOT in. A developer cannot tell, at a glance, where
they are or where a click will take them. The button labels the destination on
one press and the origin on the next — the most confusing possible affordance.

Two more coherence gaps compound it:

1. **Server admin pollutes code navigation.** Index counts and freshness live in
   the header beside the exploration controls, as if "how fresh is the index" were
   a peer of "what's in this codebase." It is not — it's operational status, and
   it belongs out of the navigation path. There is also no *watcher-aware* stale
   signal in the UI at all: `/api/status` returns a timestamp but never says
   "this index is stale," even though the staleness machinery already exists and
   `/api/schema` + `/api/architecture` already surface it.

2. **Breadcrumbs stop at the treemap.** The Overview treemap has a working
   breadcrumb, but the moment you hand off to a symbol's neighborhood, the trail
   is gone — there is no "you are here, here's the path back up" across the whole
   drill (landing → area → treemap → symbol). Each surface resets context.

The views themselves were fixed in Phases A–C. What's missing is the **frame**:
an honest, legible way to move between them and to always know where you are.

## Solution

Give the Explorer an explicit, three-tab frame and demote everything that isn't
code navigation out of it.

- **A real tab bar — Overview · Symbol · Topology.** Three tabs, one always
  visibly active. The active tab is the single bold element in the header; the
  rest is quiet. No button ever relabels itself with another mode's name. The tab
  names come from the developer's side of the screen — what you're looking at
  ("Symbol"), not the internal state name ("neighborhood").
- **A bottom status strip for server admin.** Index counts, freshness, and a
  watcher-aware stale indicator move to a thin monospace strip pinned to the
  bottom of the window — present, glanceable, out of the navigation path. When
  the index is stale it says so plainly, with the fix (`seam sync`).
- **End-to-end breadcrumbs.** A single breadcrumb trail spans the whole drill
  path — repo → area → file/class → symbol — so you can always step back up any
  number of levels, regardless of which surface you're on.
- **Polish.** Consistent active-state treatment, keyboard focus, one accent
  color used as signal (active tab / stale warning) and nowhere else.

The result: you always know which of the three questions you're asking, you can
always get back, and the machine's health is visible without competing with the
code.

## User Stories

1. As a developer, I want an explicit tab bar with one clearly-active tab, so that
   I always know which view I'm in without reading the button that would switch me
   away.
2. As a developer, I want the three tabs named for what I'm exploring (Overview,
   Symbol, Topology), so that the labels describe the content, not the app's
   internal mode names.
3. As a developer, I never want a navigation control labeled with the mode it is
   NOT currently showing, so that a single click's destination is unambiguous.
4. As a developer, I want the Symbol tab to be enabled only once a symbol is in
   play (searched, or handed off from Overview/Topology/Changes), so that the tab
   bar reflects what's actually reachable.
5. As a developer, I want clicking the Symbol tab with no symbol yet to drop me on
   the search-first landing, so that the tab is a promise ("pick a symbol"), not a
   dead end.
6. As a developer, I want the Topology tab to keep its 2D/3D sub-toggle, so that
   Phase C's legible-by-default 2D cluster graph still leads and 3D stays opt-in.
7. As a developer, I want index counts and freshness in a status strip at the
   bottom of the window, so that server admin does not sit among the exploration
   controls.
8. As a developer, I want a plain, watcher-aware "index is stale — run seam sync"
   indicator in the status strip, so that I trust what I'm reading and know the fix.
9. As a developer, I want the status strip quiet when the index is fresh, so that
   the stale warning is a real signal when it appears, not chrome I tune out.
10. As a developer, I want one breadcrumb trail that spans the whole drill path
    (repo → area → file/class → symbol), so that I can navigate back up from any
    surface.
11. As a developer, I want clicking any breadcrumb crumb to jump straight to that
    level, so that stepping back up is one click, not many.
12. As a developer on the landing page, I want the breadcrumb to show just the
    repo root, so that "home" is always visible and clickable.
13. As a developer, I want the Changes drawer, search, and trace controls to keep
    working exactly as before across the new tab frame, so that Phase D changes the
    frame without regressing any Phase A–C behavior.
14. As a developer, I want keyboard focus and active states on the tabs and
    breadcrumb, so that the frame is navigable without a mouse.
15. As an evaluator, I want the Explorer to feel like one coherent tool rather than
    three bolted-together views, so that my first impression is trust.
16. As a maintainer, I want the status strip's stale check to reuse the existing
    staleness source of truth, so that the UI never disagrees with `seam status` /
    `/api/schema` about whether the index is stale.

## Implementation Decisions

### The tab model (the central change)

- Replace the header's contextual-label toggles (`HeaderToggle` for
  Overview/Neighborhood + the standalone Topology button) with **one explicit
  `TabBar`** rendering three fixed tabs: **Overview**, **Symbol**, **Topology**.
- The existing `ViewMode` type (`"neighborhood" | "overview" | "topology"`) is the
  backing state; the tab bar is a pure presentational mapping over it. **"Symbol"
  is the developer-facing name for the `"neighborhood"` mode** — the internal state
  string does not change (avoids touching every consumer), only the label the user
  sees. Document the mapping at the type so it doesn't drift.
- **Active-tab treatment is the one bold thing in the header** (the sky accent
  already used for `aria-pressed`): filled/underlined active tab, quiet inactive
  tabs. No tab ever shows another mode's name.
- **Symbol tab enablement:** the Symbol tab is always clickable; clicking it with
  no `centerSymbol` shows the search-first `LandingPage` (already the current
  neighborhood-with-null-center behavior). It is NOT disabled — a disabled tab
  would be a dead end; the landing IS the Symbol tab's empty state.
- **Topology sub-toggle unchanged (Phase C):** the 2D/3D pill still renders inline
  when Topology is active; 2D leads, 3D is lazy/opt-in. Phase D only re-frames the
  outer switch as a tab.
- **Kill the anti-pattern concretely:** after this change, grep for a header
  control whose label is derived from the *current* mode — there must be none.
- **Extract a small pure helper** for the tab definitions (id → label → icon) so
  the tab list is data, unit-testable, and the single place a future tab is added.

### Status strip (server admin, demoted)

- **Move `StatusBadge` out of the header** into a new **bottom `StatusStrip`**
  component: a thin (`h-6`-ish) monospace strip pinned below `<main>`, showing
  symbol/edge/cluster counts + relative last-indexed, plus a **stale indicator**.
- **Additive Web API change (the only backend change):** add a `stale: bool` field
  (and a short `stale_reason: str | None`) to `StatusResponse` / `GET /api/status`,
  computed by reusing the existing staleness source of truth (`analysis/staleness.py`
  `check_staleness`, same call `/api/schema` and `/api/architecture` already make).
  This makes the strip **watcher-aware** (a live watcher self-heals → not stale;
  synth edges present → stale) and guarantees the UI agrees with `seam status`.
- **Fresh = quiet, stale = signal:** when `stale` is false the strip is
  low-contrast zinc. When true, a single amber dot + "index stale — run `seam sync`"
  (the accent used only for real warnings). Copy is from the user's side and names
  the fix.
- The existing `formatRelative` helper moves with the badge (or is shared).

### End-to-end breadcrumbs

- **One `Breadcrumb` component** rendered in a consistent location (a thin row at
  the top of `<main>`, above whichever surface is active) so the trail is present
  on every surface, not just the treemap.
- Model the trail as a small **`crumbs` array** in `App` derived from the live
  state — repo root (always, = home/`goHome`) → area (when `preselectedArea` set)
  → symbol (when `centerSymbol` set) → selected node (when `selectedSymbol` set and
  distinct). Each crumb carries a click handler that restores exactly that level
  (reusing existing setters: `goHome`, `handleOpenScopedOverview`,
  `setCenterSymbol`, `setSelectedSymbol`).
- **The treemap's internal drill breadcrumb stays** (it tracks folder→file→class
  *within* Overview, which App-level state does not model); the App-level
  breadcrumb sits above it and covers cross-surface navigation. Document the
  two-level relationship so they read as one system, not two competing trails.
- **Extract the crumb-derivation as a pure function** (`state → Crumb[]`) so it is
  unit-testable without React.

### Polish

- Consistent focus rings on tabs + crumbs (keyboard reachable), `aria-current` on
  the active tab, reduced-motion respected on any transition.
- One accent color as signal (active tab, stale warning); everything else zinc.
- No layout shift when the status strip's stale state toggles.

### Constraints (verbatim from the redesign spec)

- No SQLite schema change, migration, or re-index. `web.py` must stay < 1000 lines
  (extract to `web_schema.py` if needed — the established pattern). MCP tool count
  stays 16. Zero new npm/py dependencies. `seam/_web` bundle rebuilt on merge.

## Testing Decisions

- **Good tests assert external behavior, not implementation detail.** Prior art:
  `web/src/__tests__/*` (vitest) for pure helpers (`deriveAreas`, `graphFilterState`,
  `resolveClusterHandoff`) and component tests (`DetailPanel.test.tsx`,
  `FileSidebar.test.tsx`); backend `tests/integration/test_web_api.py` for API shape.
- **Pure leaves (unit):**
  - tab-definitions helper: exactly three tabs, stable ids, "Symbol" label maps to
    `neighborhood`.
  - breadcrumb derivation `state → Crumb[]`: landing → `[repo]`; scoped area →
    `[repo, area]`; centered symbol → `[repo, symbol]`; area + symbol + selected →
    full chain; each crumb's target level.
- **Components (vitest + Testing Library):**
  - `TabBar`: renders three tabs, exactly one has `aria-current`, clicking a tab
    calls the mode setter, no rendered control's label equals a non-active mode name
    (the anti-pattern regression test).
  - `StatusStrip`: renders counts; fresh → no stale text; `stale:true` → amber dot +
    "run seam sync" copy; loading/error states.
  - `Breadcrumb`: clicking a crumb invokes the right handler; home crumb always
    present.
- **Web API (integration):** `GET /api/status` includes `stale` (bool) and
  `stale_reason`; a freshly-indexed fixture → `stale:false`. Assert the new keys are
  additive (existing keys unchanged).
- **Gate:** ruff + mypy clean, full pytest suite, vitest + `tsc --noEmit` + `vite
  build` green. Rebuild `seam/_web` so `seam serve` renders the new frame.

## Out of Scope

- Any SQLite schema change, migration, or re-index (Phase D is frontend + one
  additive read-path API field).
- New MCP tools (count stays 16). Multi-project management.
- Re-theming, new fonts, or a visual redesign of the individual views (A–C shipped
  them). Phase D is the *frame*, not the contents.
- URL-based routing / deep links (the breadcrumb is in-memory state; persisting to
  the URL is a separate future item).
- Any change to the 3D constellation internals (Phase C settled it).

## Further Notes

- This is the last phase of the linear A→D Explorer redesign. On merge, the
  redesign is complete: honest rankings (A), coherent landing/areas/snippet (B),
  legible topology both ways (C), and a coherent navigational frame (D).
- The `stale` field on `/api/status` is deliberately the *only* backend change and
  reuses the existing staleness source of truth — the UI must never disagree with
  `seam status` about freshness (story 16). This mirrors the Phase B/C discipline of
  a single additive field per phase.
- Frontend-design rationale baked in: **structure encodes truth** (three tabs = the
  three real questions the tool answers), **spend boldness in one place** (the active
  tab / stale warning are the only accented elements), **copy from the user's side**
  (tab + status copy name what the developer does, not how the app is built), and
  **restraint** (server admin demoted physically, quiet-until-it-matters status).
