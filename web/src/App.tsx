/**
 * App — the main Seam Explorer shell.
 *
 * Layout (Phase D final shell, #285 3D-only):
 *   ┌─────────────────────────────────────────────────────┐
 *   │  Header (brand + TabBar + search box(es) +          │
 *   │          Changes button)                            │
 *   ├─────────────────────────────────────────────────────┤
 *   │  Breadcrumb (repo → area → symbol → selected)      │  ← D4: cross-surface trail
 *   ├─────────────────────────────────────────────────────┤
 *   │  [FileSidebar]  Surface content (topology /         │
 *   │                  overview / neighborhood / landing) │
 *   │                  [DetailPanel] right of canvas      │
 *   ├─────────────────────────────────────────────────────┤
 *   │  StatusStrip (index counts + stale warn)            │  ← D3: demoted from header
 *   └─────────────────────────────────────────────────────┘
 *
 * State: `mode` (ViewMode from lib/tabs.ts) determines which surface is shown.
 * Within neighborhood mode, `centerSymbol` drives the graph canvas:
 *   - null → LandingPage (hub chips + area cards)
 *   - non-null → GraphCanvas + DetailPanel
 *
 * WHY tabs.ts owns ViewMode (not a local type here):
 *   The "Symbol" tab label maps to the "neighborhood" ViewMode string — a mapping
 *   that must be expressed exactly once. lib/tabs.ts is that one place; TabBar
 *   reads it, App imports the type from it. No other file may define ViewMode.
 *
 * #285: Topology is 3D-only. The 2D ClusterGraph2D surface (C2) and its inline
 * 2D/3D sub-toggle were removed. The backend /api/constellation endpoint and its
 * `representative` field remain in place but are no longer consumed by the frontend.
 */

import { useState, useRef, useCallback, useEffect, lazy, Suspense, useMemo } from "react";
import { GraphCanvas } from "./components/GraphCanvas";
import { DetailPanel } from "./components/DetailPanel";
import { ChangesDrawer } from "./components/ChangesDrawer";
import { StructureOverview } from "./components/StructureOverview";
import { FileSidebar } from "./components/FileSidebar";
import { TabBar } from "./components/TabBar";
import { StatusStrip } from "./components/StatusStrip";
import { Breadcrumb } from "./components/Breadcrumb";
import type { ViewMode } from "./lib/tabs";
import { ResizeHandle, clampPanelWidth, readPanelWidth } from "./components/ResizeHandle";
import { useSearch, useHubs, useAreas } from "./api/hooks";
import { deriveCrumbs } from "./lib/breadcrumbs";
import type { SearchResultItem, HubSymbol } from "./api/schema-types";
import type { Area } from "./lib/deriveAreas";
import { GitBranch, Route } from "lucide-react";

// ── 2D detail-panel resize constants ─────────────────────────────────────────

/**
 * localStorage key for the 2D detail-panel width.
 * Uses a 2D-specific key so it does not collide with the 3D panel keys
 * ("seam-left-w", "seam-right-w") stored by ConstellationTab.
 */
const LS_2D_DETAIL_KEY = "seam-2d-detail-w";

/** Default detail-panel width — matches the former fixed Tailwind w-72 (288px). */
const DEFAULT_2D_DETAIL_W = 288;

/**
 * Minimum canvas width so dragging the handle all the way cannot collapse the graph.
 * Keeps at least this many pixels available for the GraphCanvas at all times.
 */
const CANVAS_MIN_W = 300;

// Lazy-load the 3D constellation tab to keep the main bundle small.
// three.js + R3F are not loaded until the user clicks "Constellation".
const ConstellationTab = lazy(() => import("./components/ConstellationTab"));

// ── SearchBox ─────────────────────────────────────────────────────────────────

interface SearchBoxProps {
  onSelect: (name: string) => void;
  /** Placeholder text (default "Search symbols…"). Used to label the trace box. */
  placeholder?: string;
}

/**
 * Command-palette-style search box.
 *
 * WHY debounce in the component (not in the hook): the hook is responsible for
 * fetching given a query string; the debounce lives here so it can be reset on
 * clear/selection without touching the hook internals.
 */
function SearchBox({ onSelect, placeholder = "Search symbols…" }: SearchBoxProps) {
  const [inputValue, setInputValue] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [open, setOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Debounce the query: 250ms after the user stops typing
  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setInputValue(val);
    setOpen(true);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(val), 250);
  }, []);

  const { data: results, isLoading } = useSearch(debouncedQuery, 20);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const handleSelect = useCallback(
    (item: SearchResultItem) => {
      setInputValue(item.name);
      setDebouncedQuery("");
      setOpen(false);
      onSelect(item.name);
    },
    [onSelect],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        setOpen(false);
        setInputValue("");
        setDebouncedQuery("");
      }
      if (e.key === "Enter" && results && results.length > 0) {
        handleSelect(results[0]);
      }
    },
    [results, handleSelect],
  );

  const hasResults =
    open && debouncedQuery.trim().length > 0 && results && results.length > 0;

  return (
    <div ref={wrapperRef} className="relative w-72">
      <input
        type="search"
        value={inputValue}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="
          w-full px-3 py-1.5 text-xs rounded-md
          bg-zinc-800 border border-zinc-700
          text-zinc-100 placeholder-zinc-500
          focus:outline-none focus:ring-1 focus:ring-sky-500 focus:border-sky-500
          transition-colors
        "
        aria-label="Search symbols"
        aria-expanded={hasResults ? "true" : "false"}
        aria-autocomplete="list"
        role="combobox"
      />

      {/* Loading indicator */}
      {open && debouncedQuery.trim().length > 0 && isLoading && (
        <div className="absolute top-full left-0 right-0 mt-1 z-50
            bg-zinc-900 border border-zinc-700 rounded-md px-3 py-2
            text-xs text-zinc-500 animate-pulse">
          Searching…
        </div>
      )}

      {/* Results dropdown */}
      {hasResults && (
        <ul
          className="
            absolute top-full left-0 right-0 mt-1 z-50
            bg-zinc-900 border border-zinc-700 rounded-md
            max-h-80 overflow-y-auto divide-y divide-zinc-800
          "
          role="listbox"
          aria-label="Search results"
        >
          {results!.map((item) => (
            <li key={`${item.name}:${item.file}:${item.line}`}>
              <button
                className="
                  w-full text-left px-3 py-2
                  hover:bg-zinc-800 transition-colors
                  flex items-center gap-2
                "
                onClick={() => handleSelect(item)}
                role="option"
              >
                <span className="text-xs font-semibold text-zinc-100 truncate">
                  {item.name}
                </span>
                <span className="text-[10px] text-zinc-500 truncate ml-auto shrink-0 max-w-[120px]">
                  {item.file.split("/").pop()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* No results state */}
      {open && debouncedQuery.trim().length > 0 && !isLoading &&
        results !== undefined && results.length === 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 z-50
            bg-zinc-900 border border-zinc-700 rounded-md px-3 py-2
            text-xs text-zinc-500">
          No symbols found
        </div>
      )}
    </div>
  );
}

// ── LandingPage ───────────────────────────────────────────────────────────────

/** How many curated entry points to show on the landing (the rest live in Overview). */
const LANDING_HUBS = 10;
const LANDING_AREAS = 12;

interface LandingPageProps {
  /** Center the graph on a symbol name (used by hub chips). */
  onSelect: (name: string) => void;
  /** Switch to the whole-repo Overview (structure treemap) view, no area pre-selected. */
  onOpenOverview: () => void;
  /**
   * B1: open the Overview pre-drilled into a specific folder-based area.
   * Called when the user clicks a landing area card.
   */
  onOpenScopedOverview: (area: Area) => void;
}

/**
 * Landing empty-state shown when no center symbol is set.
 *
 * B1: "Largest areas" now comes from folder-based deriveAreas (via useAreas),
 * replacing the old cluster-based list.  "Area" means the same thing on every
 * screen — one concept, one hook, no drift. useClusters is NOT called here any
 * more; the DetailPanel cluster legend still uses it from its own component.
 */
function LandingPage({ onSelect, onOpenOverview, onOpenScopedOverview }: LandingPageProps) {
  // WHY local state for showTests/showPackages: toggling triggers a fresh derive
  // without any global state change — toggles are scoped to the landing section.
  const [showTests, setShowTests] = useState(false);
  const [showPackages, setShowPackages] = useState(false);

  const { data: hubs, isLoading: hubsLoading } = useHubs(LANDING_HUBS, showTests, showPackages);
  // B1: areas from the same hook the Overview uses — one derivation site.
  const { areas, isLoading: areasLoading } = useAreas({
    includeTests: showTests,
    includePackages: showPackages,
  });

  const topAreas = areas.slice(0, LANDING_AREAS);

  const nothingIndexed =
    !hubsLoading &&
    !areasLoading &&
    (hubs?.length ?? 0) === 0 &&
    areas.length === 0;

  return (
    <div className="w-full h-full overflow-y-auto">
      <div className="flex flex-col items-center justify-center gap-8 p-8 min-h-full max-w-3xl mx-auto">
        {/* Hero tagline — the header search box sits directly above this */}
        <div className="text-center space-y-1">
          <h2 className="text-lg font-semibold text-zinc-100">Explore the codebase</h2>
          <p className="text-zinc-500 text-sm">
            Search a symbol above, jump to a key symbol, or browse a functional area.
          </p>
        </div>

        {nothingIndexed && (
          <p className="text-zinc-600 text-xs text-center">
            Nothing indexed yet — run <code className="text-zinc-400">seam init</code> to build the index.
          </p>
        )}

        {/* Key symbols — highest-degree hubs (the things everything touches).
            Single toggle controls both hubs AND areas (same showTests state). */}
        {(hubs?.length ?? 0) > 0 && (
          <section className="w-full">
            <div className="flex items-baseline justify-between mb-2">
              <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600">
                Key symbols
              </h3>
              {/* A1: toggle to re-include test-path symbols. Default is off because
                  test helpers pollute the hub list with non-production entry points.
                  B1: also controls the areas section — one toggle, one concept. */}
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setShowTests((prev) => !prev)}
                  aria-pressed={showTests}
                  className={`text-[10px] transition-colors ${
                    showTests
                      ? "text-sky-400 hover:text-sky-300"
                      : "text-zinc-600 hover:text-zinc-400"
                  }`}
                  title={showTests ? "Hiding test symbols — click to show" : "Showing production symbols — click to include tests"}
                >
                  {showTests ? "hide tests" : "show tests"}
                </button>
                {/* #287: toggle to re-include package-plumbing symbols (__init__.py,
                    mod.rs, index.ts …). Default off: these files accumulate degree
                    from every importer but carry no independent logic — they pollute
                    the hub list with plumbing, not meaningful entry points. */}
                <button
                  onClick={() => setShowPackages((prev) => !prev)}
                  aria-pressed={showPackages}
                  className={`text-[10px] transition-colors ${
                    showPackages
                      ? "text-sky-400 hover:text-sky-300"
                      : "text-zinc-600 hover:text-zinc-400"
                  }`}
                  title={showPackages ? "Hiding package files — click to show" : "Showing implementation symbols — click to include packages"}
                >
                  {showPackages ? "hide packages" : "show packages"}
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {hubs!.map((h: HubSymbol) => (
                <button
                  key={h.name}
                  onClick={() => onSelect(h.name)}
                  className="group flex items-center gap-2 px-3 py-1.5 bg-zinc-900 border border-zinc-700 rounded-full hover:border-sky-500/60 hover:bg-zinc-800 transition-colors"
                  title={`${h.kind ?? "symbol"} · ${h.degree} connections`}
                >
                  <span className="text-xs font-semibold text-zinc-100">{h.name}</span>
                  <span className="text-[10px] text-zinc-500 font-mono group-hover:text-sky-400">
                    {h.degree}
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        {/* Largest areas — folder-based (B1); full treemap in Overview.
            Clicking a card enters the Overview pre-drilled to that area. */}
        {topAreas.length > 0 && (
          <section className="w-full">
            <div className="flex items-baseline justify-between mb-2">
              <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600">
                Largest areas
              </h3>
              <button
                onClick={onOpenOverview}
                className="text-[10px] text-sky-400/80 hover:text-sky-300 transition-colors"
              >
                open Overview for the full map →
              </button>
            </div>
            <div
              className="grid gap-2"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}
            >
              {topAreas.map((a: Area) => (
                <button
                  key={a.key}
                  onClick={() => onOpenScopedOverview(a)}
                  className="flex flex-col gap-1 px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-md hover:border-zinc-500 hover:bg-zinc-800 transition-colors text-left"
                  aria-label={`Explore area ${a.name}`}
                >
                  <span className="text-xs font-semibold text-zinc-200 truncate">
                    {a.name}
                  </span>
                  <span className="text-[10px] text-zinc-500">
                    {a.fileCount} files · {a.symbolCount} symbols
                  </span>
                  {a.keySymbols.length > 0 && (
                    <span className="text-[10px] text-zinc-600 truncate">
                      {a.keySymbols.join(", ")}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

/** App view mode — defined in lib/tabs.ts and imported above; documented here for readers. */

/**
 * App root: assembles the header, search, mode toggle, and the main area.
 * The main area is the StructureOverview (functional areas → structure treemap),
 * the GraphCanvas (neighborhood with a center), or the LandingPage (no center yet).
 */
function App() {
  // centerSymbol: drives the graph canvas; null = show landing page
  const [centerSymbol, setCenterSymbolRaw] = useState<string | null>(null);
  // selectedSymbol: driven by node selection in canvas (feeds detail panel)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  // traceTarget: second search box endpoint — highlights the path center→target
  const [traceTarget, setTraceTarget] = useState<string | null>(null);
  // changesOpen: toggles the git working-changes drawer
  const [changesOpen, setChangesOpen] = useState(false);
  // mode: per-symbol neighborhood, whole-repo overview, or topology (3D constellation)
  const [mode, setMode] = useState<ViewMode>("neighborhood");
  // focusSymbol: propagated from the 2D neighborhood to the 3D topology tab so
  // switching to Topology after selecting a symbol flies the camera there.
  const [focusSymbol, setFocusSymbol] = useState<string | null>(null);
  // B1: preselectedArea — set when a landing area card is clicked; cleared when
  // opening overview from the header so the area-cards list shows first.
  const [preselectedArea, setPreselectedArea] = useState<Area | null>(null);

  // detailW: 2D detail-panel width in px, persisted to localStorage.
  // Initialized from localStorage so the user's last drag position survives reloads.
  const [detailW, setDetailW] = useState(() =>
    readPanelWidth(LS_2D_DETAIL_KEY, DEFAULT_2D_DETAIL_W),
  );

  // Persist detail panel width whenever it changes
  useEffect(() => {
    try { localStorage.setItem(LS_2D_DETAIL_KEY, String(detailW)); } catch { /* ignore */ }
  }, [detailW]);

  // Resize handler for the 2D detail panel.
  // The handle sits to the LEFT of the detail panel (side="right"):
  //   dragging right → positive delta → panel shrinks (w - delta)
  //   dragging left  → negative delta → panel grows (w - delta)
  const handleDetailResize = useCallback((delta: number) => {
    setDetailW((w) => clampPanelWidth(w - delta));
  }, []);

  // Centering on a new symbol invalidates any in-flight trace (the old target
  // is meaningless relative to the new center) — reset it alongside the center.
  // Also propagate to focusSymbol so the 3D tab flies to the same symbol when
  // the user switches to Constellation after a 2D selection (2D→3D sync).
  const setCenterSymbol = useCallback((name: string | null) => {
    setCenterSymbolRaw(name);
    setTraceTarget(null);
    if (name) setFocusSymbol(name);   // 2D→3D: propagate to constellation tab
  }, []);

  const handleSelectSymbol = useCallback((name: string) => {
    setSelectedSymbol(name);
  }, []);

  // Opening a symbol from the Overview treemap centers the graph on it.
  const handleOpenFromOverview = useCallback(
    (name: string) => {
      setCenterSymbol(name);
      setMode("neighborhood");
    },
    [setCenterSymbol],
  );

  // Opening a symbol from the file sidebar centers the neighborhood graph.
  // Mode is already "neighborhood" when the sidebar is visible, but set it
  // explicitly so the sidebar also works as a navigation shortcut from landing.
  const handleOpenFromSidebar = useCallback(
    (name: string) => {
      setCenterSymbol(name);
      setMode("neighborhood");
    },
    [setCenterSymbol],
  );

  // B1: open the Overview pre-drilled into the given area (landing card click).
  const handleOpenScopedOverview = useCallback((area: Area) => {
    setPreselectedArea(area);
    setMode("overview");
  }, []);

  // Clicking the "Seam Explorer" brand returns to the landing page: reset the
  // view to the default neighborhood mode with no centered/selected symbol, and
  // close any open drawer. This is the app's "home" action.
  const goHome = useCallback(() => {
    setMode("neighborhood");
    setCenterSymbol(null); // also clears the trace target
    setSelectedSymbol(null);
    setChangesOpen(false);
    setPreselectedArea(null);
  }, [setCenterSymbol]);

  /**
   * Restore the center-symbol level from a deeper selectedSymbol position.
   * Called by the symbol crumb's onClick — clears selectedSymbol and ensures
   * neighborhood mode. Does NOT change centerSymbol (it's already the target).
   */
  const openCenterSymbol = useCallback(() => {
    setSelectedSymbol(null);
    setMode("neighborhood");
  }, []);

  /**
   * App-level breadcrumb trail derived from navigation state.
   *
   * WHY useMemo: deriveCrumbs is pure and cheap but creates new closure objects
   * on every call; memoising avoids re-creating button onClick closures when
   * unrelated state (e.g. changesOpen) changes.
   *
   * See breadcrumbs.ts for the two-level breadcrumb system documentation.
   */
  const crumbs = useMemo(
    () =>
      deriveCrumbs(
        { mode, preselectedArea, centerSymbol, selectedSymbol },
        { goHome, openArea: handleOpenScopedOverview, openCenterSymbol },
      ),
    [mode, preselectedArea, centerSymbol, selectedSymbol, goHome, handleOpenScopedOverview, openCenterSymbol],
  );

  const showGraph = mode === "neighborhood" && centerSymbol;

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="flex items-center gap-3 px-5 py-3 bg-zinc-900 border-b border-zinc-800 shrink-0">
        {/* Brand — click to return to the landing page (home action) */}
        <button
          type="button"
          onClick={goHome}
          aria-label="Seam Explorer — back to home"
          title="Back to home"
          className="flex items-center gap-3 rounded-md -mx-1 px-1 py-0.5 hover:opacity-80 transition-opacity cursor-pointer"
        >
          {/* Graph icon */}
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="10" cy="10" r="2.5" fill="#7dd3fc" />
            <circle cx="3" cy="4" r="2" fill="#a5b4fc" />
            <circle cx="17" cy="4" r="2" fill="#a5b4fc" />
            <circle cx="3" cy="16" r="2" fill="#6ee7b7" />
            <circle cx="17" cy="16" r="2" fill="#6ee7b7" />
            <line x1="10" y1="10" x2="3" y2="4" stroke="#52525b" strokeWidth="1.5" />
            <line x1="10" y1="10" x2="17" y2="4" stroke="#52525b" strokeWidth="1.5" />
            <line x1="10" y1="10" x2="3" y2="16" stroke="#52525b" strokeWidth="1.5" />
            <line x1="10" y1="10" x2="17" y2="16" stroke="#52525b" strokeWidth="1.5" />
          </svg>

          <h1 className="text-sm font-semibold tracking-tight text-zinc-100">Seam Explorer</h1>
        </button>

        {/* ── Explicit tab bar (#273) ────────────────────────────────────────
            Overview · Symbol · Topology — the three questions a developer has.
            "Symbol" is the user-facing label for the "neighborhood" ViewMode.
            The tab bar replaces the old contextual HeaderToggle (which relabelled
            itself with the OTHER mode's name — the anti-pattern killed here).
            B1 note: switching to Overview from the TabBar always resets preselectedArea
            so the full area-cards list shows first; landing-card clicks set it again. */}
        <TabBar
          mode={mode}
          onSetMode={(next) => {
            // Entering Overview fresh (not from a landing card) → clear preselectedArea
            if (next === "overview" && mode !== "overview") setPreselectedArea(null);
            setMode(next);
          }}
        />

        {/* Center: search box(es) — hidden in overview mode */}
        <div className="flex-1 flex justify-center items-center gap-2">
          {mode === "neighborhood" && (
            <>
              <SearchBox onSelect={setCenterSymbol} />
              {/* Trace target search — only meaningful once a center is set (F4) */}
              {centerSymbol && (
                <div className="flex items-center gap-1.5">
                  <Route className="w-3.5 h-3.5 text-zinc-500" aria-hidden="true" />
                  <SearchBox onSelect={setTraceTarget} placeholder="Trace to…" />
                  {traceTarget && (
                    <button
                      onClick={() => setTraceTarget(null)}
                      className="text-[10px] text-zinc-500 hover:text-zinc-300 px-1"
                      aria-label="Clear trace target"
                    >
                      ✕
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Changes drawer toggle — a simple pill button, not a tab (it does not switch the
            main content area, it slides in a drawer overlay). Kept as a standalone control
            so it doesn't compete visually with the TabBar's sky-accent active-tab highlight.
            aria-pressed because it's a toggle, not a navigation tab. */}
        <button
          onClick={() => setChangesOpen((o) => !o)}
          aria-pressed={changesOpen}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs transition-colors border ${
            changesOpen
              ? "bg-sky-500/15 border-sky-500/50 text-sky-300"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:border-zinc-500"
          }`}
        >
          <GitBranch className="w-3.5 h-3.5" />
          Changes
        </button>

        {/* Clear center — neighborhood mode with an active center */}
        {showGraph && (
          <button
            onClick={() => {
              setCenterSymbol(null);
              setSelectedSymbol(null);
            }}
            className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors px-2 py-1 rounded hover:bg-zinc-800"
            aria-label="Clear current symbol and return to landing"
          >
            ✕ clear
          </button>
        )}
      </header>

      {/* ── Main content ───────────────────────────────────────────────── */}
      {/*
       * WHY flex-col here:
       *   Row 1 — Breadcrumb: a thin full-width trail above every surface.
       *   Row 2 — Surface content: topology / overview / neighborhood / landing.
       *
       * The Breadcrumb covers cross-surface navigation (landing → area → symbol).
       * The TreemapCanvas's internal breadcrumb (scopeName → folder → file) sits
       * INSIDE row 2 when Overview is active — the two rows form one coherent trail.
       */}
      <main className="flex flex-col flex-1 overflow-hidden">
        {/* App-level breadcrumb — present on every surface, always at the top */}
        <Breadcrumb crumbs={crumbs} />

        {/* Surface content — takes the remaining height */}
        <div className="flex flex-1 overflow-hidden">
        {/* Topology surface: 3D constellation (lazy-loaded to keep three/R3F out of
            the initial bundle). focusSymbol propagates the last neighborhood selection
            so the camera flies to it when the user switches to Topology (#285 3D-only).
            #263 ISOLATION CONTRACT: selecting a node in 3D ONLY isolates its
            neighborhood in the canvas — it does NOT navigate the 2D neighborhood.
            The 3D scene is a spatial relationship viewer; navigation happens in the
            2D neighborhood graph. */}
        {mode === "topology" ? (
          <Suspense
            fallback={
              <div className="flex items-center justify-center w-full h-full text-zinc-500 text-sm animate-pulse">
                Loading constellation…
              </div>
            }
          >
            {/* #361: the identity card's explicit "Open in neighborhood →"
                hands off to the 2D neighborhood centered on the symbol. This is
                the ONE deliberate navigation door out of the orientation view —
                the node click itself still only isolates (#263). Reuses the same
                center+switch handler the Overview treemap uses. */}
            <ConstellationTab
              focusSymbol={focusSymbol}
              onOpenInNeighborhood={handleOpenFromOverview}
            />
          </Suspense>
        ) : (
          <>
            {/* File sidebar: visible in neighborhood mode only (not overview).
                Manages its own open/closed and width state via localStorage.
                Renders as a collapsed strip when closed so the canvas always
                has at least CANVAS_MIN_W pixels available. */}
            {mode === "neighborhood" && (
              <FileSidebar onOpen={handleOpenFromSidebar} />
            )}

            {/* Center: overview / graph canvas / landing page.
                min-width guards the canvas so dragging the detail handle
                cannot collapse the graph to zero width. */}
            <div
              className="flex-1 overflow-hidden relative"
              style={{ minWidth: CANVAS_MIN_W }}
            >
              {mode === "overview" ? (
                // B1: pass initialArea so a landing card click pre-drills the treemap.
                // StructureOverview remounts on mode change, so initialArea is the
                // useState initial value — correctly consumed once on mount.
                <StructureOverview
                  onSelectSymbol={handleOpenFromOverview}
                  initialArea={preselectedArea}
                />
              ) : showGraph ? (
                <GraphCanvas
                  center={centerSymbol!}
                  onSelectSymbol={handleSelectSymbol}
                  traceTarget={traceTarget}
                />
              ) : (
                <LandingPage
                  onSelect={setCenterSymbol}
                  onOpenOverview={() => {
                    setPreselectedArea(null);
                    setMode("overview");
                  }}
                  onOpenScopedOverview={handleOpenScopedOverview}
                />
              )}
            </div>

            {/* Right: resize handle + detail panel.
                Mounted only when the graph is active so the width prop is
                consistent across all DetailPanel render branches. */}
            {showGraph && (
              <>
                <ResizeHandle side="right" onResize={handleDetailResize} />
                {/* onNavigate updates SELECTED only — centerSymbol is preserved */}
                <DetailPanel
                  selectedSymbol={selectedSymbol}
                  width={detailW}
                  onNavigate={setSelectedSymbol}
                />
              </>
            )}
          </>
        )}

        {/* Rightmost: changes drawer — toggled from the header */}
        <ChangesDrawer
          open={changesOpen}
          onClose={() => setChangesOpen(false)}
          onSelectSymbol={(name) => {
            setMode("neighborhood");
            setCenterSymbol(name);
            setSelectedSymbol(name);
          }}
        />
        </div>{/* end surface-content flex row */}
      </main>

      {/* ── Status strip ───────────────────────────────────────────────── */}
      {/* WHY below main (not in header): operational metadata (index stats + stale
          signal) is demoted from the header per the PRD. The header is for navigation
          only. The strip is always a single fixed-height row so no layout shift occurs
          when the stale flag toggles. StatusStrip owns its own useStatus() call. */}
      <StatusStrip />
    </div>
  );
}

export default App;
