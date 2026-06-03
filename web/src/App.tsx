/**
 * App — the main Seam Explorer shell.
 *
 * Layout:
 *   ┌─────────────────────────────────────────┐
 *   │  Header (status + search box)           │
 *   ├─────────────────────────────────────────┤
 *   │  Landing (cluster list) OR              │
 *   │  GraphCanvas (when a symbol is set)     │
 *   └─────────────────────────────────────────┘
 *
 * State: `centerSymbol` drives everything.
 * - null → show landing page (cluster list as entry points)
 * - non-null → show GraphCanvas
 *
 * The detail panel (F5) will be added in the next task; for now, selected
 * symbol name is stored but not yet rendered in a side panel.
 */

import { useState, useRef, useCallback, useEffect } from "react";
import { GraphCanvas } from "./components/GraphCanvas";
import { DetailPanel } from "./components/DetailPanel";
import { ChangesDrawer } from "./components/ChangesDrawer";
import { ConstellationCanvas } from "./components/ConstellationCanvas";
import { useStatus, useSearch, useClusters } from "./api/hooks";
import type { ClusterItem, SearchResultItem } from "./api/schema-types";
import { clusterColor } from "./lib/clusterColor";
import { GitBranch, Orbit, Network, Route } from "lucide-react";

// ── Utility: relative time formatter ─────────────────────────────────────────

/**
 * Format a last_indexed timestamp as a human-friendly relative string.
 * Falls back to the raw string if parsing fails.
 */
function formatRelative(ts: string | null | undefined): string {
  if (!ts) return "never";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    const diff = Date.now() - d.getTime();
    const secs = Math.round(diff / 1000);
    if (secs < 60) return "just now";
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  } catch {
    return ts;
  }
}

// ── StatusBadge ───────────────────────────────────────────────────────────────

/**
 * Shows index counts and freshness in the header.
 * Renders loading skeleton / error states inline.
 */
function StatusBadge() {
  const { data, isLoading, isError } = useStatus();

  if (isLoading) {
    return (
      <span className="text-xs text-zinc-600 animate-pulse">loading…</span>
    );
  }
  if (isError || !data) {
    return (
      <span className="text-xs text-red-500" title="Could not reach seam serve">
        no index
      </span>
    );
  }

  return (
    <span className="text-xs text-zinc-400 font-mono tabular-nums" aria-label="index statistics">
      {data.symbol_count.toLocaleString()} symbols ·{" "}
      {data.edge_count.toLocaleString()} edges ·{" "}
      {data.cluster_count} clusters ·{" "}
      <span className="text-zinc-500">
        indexed {formatRelative(data.last_indexed)}
      </span>
    </span>
  );
}

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

interface LandingPageProps {
  onSelectCluster: (name: string) => void;
}

/**
 * Landing empty-state shown when no center symbol is set.
 * Lists clusters as clickable entry points into the graph.
 */
function LandingPage({ onSelectCluster }: LandingPageProps) {
  const { data: clusters, isLoading, isError } = useClusters();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center w-full h-full">
        <span className="text-zinc-600 text-sm animate-pulse">Loading clusters…</span>
      </div>
    );
  }

  if (isError || !clusters || clusters.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center w-full h-full gap-3">
        <p className="text-zinc-500 text-sm">Search for a symbol above to explore the graph.</p>
        <p className="text-zinc-600 text-xs">
          No clusters indexed yet — run{" "}
          <code className="text-zinc-400">seam init</code> to build the index.
        </p>
      </div>
    );
  }

  return (
    // Own scroll container: the cluster grid can be hundreds of cards tall, and
    // the parent main area is overflow-hidden — without overflow-y-auto here the
    // grid spills off-screen with no way to reach the lower cards.
    <div className="w-full h-full overflow-y-auto">
      <div className="flex flex-col items-center gap-6 p-8 min-h-full">
        <p className="text-zinc-400 text-sm">
          Search a symbol above, or explore a functional area:
        </p>
        <div
          className="grid gap-3 w-full max-w-3xl"
          style={{
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
          }}
        >
        {clusters.map((c: ClusterItem) => {
          const colour = clusterColor(c.cluster_id);
          return (
            <button
              key={c.cluster_id}
              // Center on a representative MEMBER symbol — a cluster is not a symbol,
              // so centering on its label/id would open an empty graph. Fall back to
              // label/id only if the backend gave no representative (degenerate cluster).
              onClick={() =>
                onSelectCluster(
                  c.representative ?? c.label ?? String(c.cluster_id),
                )
              }
              className="
                flex items-center gap-2.5 px-3 py-2.5
                bg-zinc-900 border border-zinc-700 rounded-md
                hover:border-zinc-500 hover:bg-zinc-800
                transition-colors cursor-pointer text-left
              "
              aria-label={`Explore cluster ${c.label ?? c.cluster_id}`}
            >
              {/* Colour dot — same colour as SymbolNode stripe for visual consistency */}
              {colour && (
                <div
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: colour }}
                  aria-hidden="true"
                />
              )}
              <div className="flex flex-col min-w-0">
                <span className="text-xs font-semibold text-zinc-200 truncate">
                  {c.label ?? `cluster-${c.cluster_id}`}
                </span>
                <span className="text-[10px] text-zinc-500">
                  {c.size} symbols
                </span>
              </div>
            </button>
          );
        })}
        </div>
      </div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

/** App view mode: per-symbol neighborhood, or whole-repo cluster overview. */
type ViewMode = "neighborhood" | "overview";

/** A small header toggle pill (mode switch, drawer toggle). */
function HeaderToggle({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs transition-colors border ${
        active
          ? "bg-sky-500/15 border-sky-500/50 text-sky-300"
          : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:border-zinc-500"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

/**
 * App root: assembles the header, search, mode toggle, and the main area.
 * The main area is the ConstellationCanvas (overview), the GraphCanvas
 * (neighborhood with a center), or the LandingPage (no center yet).
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
  // mode: per-symbol neighborhood or whole-repo overview
  const [mode, setMode] = useState<ViewMode>("neighborhood");

  // Centering on a new symbol invalidates any in-flight trace (the old target
  // is meaningless relative to the new center) — reset it alongside the center.
  const setCenterSymbol = useCallback((name: string | null) => {
    setCenterSymbolRaw(name);
    setTraceTarget(null);
  }, []);

  const handleSelectSymbol = useCallback((name: string) => {
    setSelectedSymbol(name);
  }, []);

  // Clicking a cluster region in overview drills into its representative symbol.
  const handleSelectCluster = useCallback(
    (representative: string) => {
      setCenterSymbol(representative);
      setMode("neighborhood");
    },
    [setCenterSymbol],
  );

  const showGraph = mode === "neighborhood" && centerSymbol;

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="flex items-center gap-3 px-5 py-3 bg-zinc-900 border-b border-zinc-800 shrink-0">
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

        {/* Mode toggle: overview ⇄ neighborhood */}
        <HeaderToggle
          active={mode === "overview"}
          onClick={() => setMode((m) => (m === "overview" ? "neighborhood" : "overview"))}
          icon={mode === "overview" ? <Network className="w-3.5 h-3.5" /> : <Orbit className="w-3.5 h-3.5" />}
          label={mode === "overview" ? "Neighborhood" : "Overview"}
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

        {/* Changes drawer toggle */}
        <HeaderToggle
          active={changesOpen}
          onClick={() => setChangesOpen((o) => !o)}
          icon={<GitBranch className="w-3.5 h-3.5" />}
          label="Changes"
        />

        {/* Index status */}
        <StatusBadge />

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
      <main className="flex flex-1 overflow-hidden">
        {/* Left: overview / graph canvas / landing page */}
        <div className="flex-1 overflow-hidden relative">
          {mode === "overview" ? (
            <ConstellationCanvas onSelectCluster={handleSelectCluster} />
          ) : showGraph ? (
            <GraphCanvas
              center={centerSymbol!}
              onSelectSymbol={handleSelectSymbol}
              traceTarget={traceTarget}
            />
          ) : (
            <LandingPage onSelectCluster={setCenterSymbol} />
          )}
        </div>

        {/* Right: detail panel — shown in neighborhood mode with an active center */}
        {showGraph && <DetailPanel selectedSymbol={selectedSymbol} />}

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
      </main>
    </div>
  );
}

export default App;
