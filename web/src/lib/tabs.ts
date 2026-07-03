/**
 * tabs.ts — pure, unit-testable tab-definitions helper for the Explorer shell.
 *
 * WHY a separate module: the TabBar must be the SINGLE place a new tab is added,
 * and the mapping from visible label ("Symbol") to ViewMode ("neighborhood") must
 * be explicit, documented, and independently testable.  No component state lives
 * here — only data.
 *
 * LABEL → ViewMode mapping:
 *   "Overview"  → "overview"
 *   "Symbol"    → "neighborhood"   (developer-facing alias; state string stays "neighborhood")
 *   "Topology"  → "topology"
 */

/** The three ViewMode strings used in App.tsx state. */
export type ViewMode = "overview" | "neighborhood" | "topology";

/** One tab definition.  icon is a render function to stay serializable in tests. */
export interface TabDef {
  /** Stable ID (kebab-case).  Also the value stored in ViewMode when mapped. */
  id: string;
  /** Developer-facing label shown in the TabBar button. */
  label: string;
  /** The ViewMode this tab represents. */
  viewMode: ViewMode;
  /** Icon factory — lazy so the module stays importable in Node test environments. */
  iconName: "network" | "search" | "orbit";
}

/**
 * THE canonical tab list.  Add a new tab here — and only here.
 *
 * Order is intentional: Overview (structure) · Symbol (graph) · Topology (cluster map).
 * The order encodes the usual discovery flow: orient → drill → browse.
 */
export const TABS: readonly TabDef[] = [
  {
    id: "overview",
    label: "Overview",
    viewMode: "overview",
    iconName: "network",
  },
  {
    id: "symbol",
    label: "Symbol",
    viewMode: "neighborhood",
    // "neighborhood" is the internal state string; "Symbol" is the user-facing label.
    // This is the ONLY mapping — never duplicate it in a component.
    iconName: "search",
  },
  {
    id: "topology",
    label: "Topology",
    viewMode: "topology",
    iconName: "orbit",
  },
] as const;

/**
 * Return the tab whose viewMode matches the current App mode.
 * Never returns undefined — falls back to the Symbol tab if mode is unrecognised.
 */
export function activeTab(mode: ViewMode): TabDef {
  return TABS.find((t) => t.viewMode === mode) ?? TABS[1];
}

/**
 * Return the ViewMode for a tab id.
 * Used when the user clicks a tab: map id → viewMode → App state.
 */
export function viewModeForTabId(id: string): ViewMode {
  return TABS.find((t) => t.id === id)?.viewMode ?? "neighborhood";
}
