/**
 * TabBar — the three-tab navigation bar for the Explorer shell.
 *
 * Replaces the contextual HeaderToggle (which relabelled itself with the OTHER
 * mode's name — the anti-pattern killed in #273) with a stable, explicit tab
 * bar where each tab always shows its own fixed label.
 *
 * Design principles (from the PRD):
 *   - Structure encodes truth: 3 tabs = 3 real questions the developer has
 *     ("What's in here?" / "What does this symbol do?" / "How is it laid out?")
 *   - Spend boldness in one place: the active tab is the single sky-accented element.
 *   - Restraint: inactive tabs are zinc/neutral — they do not compete for attention.
 *
 * The tab list is owned by lib/tabs.ts — the ONLY place a new tab is added.
 */

import { Network, Search, Orbit } from "lucide-react";
import { TABS, activeTab } from "../lib/tabs";
import type { ViewMode } from "../lib/tabs";

// ── Icon map — keeps the component stateless-friendly and tabs.ts Node-safe ──

/** Map iconName strings from TabDef to Lucide icon components. */
const ICON_MAP = {
  network: Network,
  search: Search,
  orbit: Orbit,
} as const;

// ── Props ─────────────────────────────────────────────────────────────────────

interface TabBarProps {
  /** The current App ViewMode — determines which tab is active. */
  mode: ViewMode;
  /** Called when the user clicks a tab, with the ViewMode the tab represents. */
  onSetMode: (mode: ViewMode) => void;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * Renders the three fixed tabs: Overview · Symbol · Topology.
 *
 * WHY aria-current="page" and not aria-selected: aria-selected belongs on
 * tabs in a `tablist` role with associated `tabpanel`.  Here we have a
 * simplified navigation bar (not a full ARIA tab widget with panels), so
 * aria-current="page" is semantically correct — it marks the currently
 * active navigation destination.
 *
 * Accessibility: each tab is a `<button role="tab">` inside a `<nav role="tablist">`.
 */
export function TabBar({ mode, onSetMode }: TabBarProps) {
  const current = activeTab(mode);

  return (
    <nav role="tablist" aria-label="Explorer views" className="flex items-center gap-1">
      {TABS.map((tab) => {
        const isActive = tab.id === current.id;
        const Icon = ICON_MAP[tab.iconName];

        return (
          <button
            key={tab.id}
            role="tab"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSetMode(tab.viewMode)}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs transition-colors border ${
              isActive
                ? "bg-sky-500/15 border-sky-500/50 text-sky-300 font-medium"
                : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100"
            }`}
          >
            <Icon className="w-3.5 h-3.5" aria-hidden="true" />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}
