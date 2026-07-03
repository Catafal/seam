/**
 * Breadcrumb — App-level navigation trail.
 *
 * Renders the Crumb[] array produced by deriveCrumbs() (breadcrumbs.ts) as a
 * thin, keyboard-navigable row. Sits at the TOP of <main> so it spans every
 * surface (landing, overview, symbol, topology) consistently.
 *
 * Design notes:
 * - Every crumb is a <button> for keyboard focus (WAI-ARIA breadcrumb pattern).
 * - The last (current) crumb has aria-current="page" and a lighter text style —
 *   it marks "you are here" without suppressing tab stop (keyboard users can still
 *   read it by focusing).
 * - Separators are aria-hidden <ChevronRight> icons — not meaningful to screen
 *   readers; the crumb labels alone tell the story.
 * - The component is intentionally thin: all state logic lives in deriveCrumbs().
 *
 * Relation to TreemapCanvas breadcrumb:
 *   This component covers the OUTER drill (landing → area → symbol).
 *   TreemapCanvas owns the INNER drill (scopeName → folder → file → class).
 *   The two rows appear in sequence when the Overview is open and an area is
 *   selected, forming a coherent two-level trail.
 */

import { ChevronRight } from "lucide-react";
import type { Crumb } from "../lib/breadcrumbs";

export interface BreadcrumbProps {
  crumbs: Crumb[];
}

/**
 * Renders a horizontal breadcrumb trail.
 * Returns null when the crumbs list is empty (defensive; deriveCrumbs always
 * returns at least the repo root crumb, so null should never occur in practice).
 */
export function Breadcrumb({ crumbs }: BreadcrumbProps) {
  if (crumbs.length === 0) return null;

  return (
    <nav
      aria-label="Navigation breadcrumb"
      className="flex items-center gap-0.5 px-4 py-1.5 border-b border-zinc-800/60 text-xs shrink-0 overflow-x-auto"
    >
      {crumbs.map((crumb, i) => (
        <span key={i} className="flex items-center gap-0.5 shrink-0">
          {/* Separator before every crumb except the first */}
          {i > 0 && (
            <ChevronRight
              className="w-3 h-3 text-zinc-700 mx-0.5 shrink-0"
              aria-hidden="true"
            />
          )}

          {/*
           * Every crumb is a button so it's keyboard-focusable (tab-reachable).
           * The current crumb uses aria-current="page" and a non-interactive style,
           * but remains a button so keyboard users can focus and read it.
           */}
          <button
            type="button"
            onClick={crumb.onClick}
            aria-current={crumb.isCurrent ? "page" : undefined}
            className={
              crumb.isCurrent
                ? "text-zinc-300 font-medium cursor-default focus:outline-none focus:ring-1 focus:ring-sky-500/50 rounded px-0.5"
                : "text-zinc-500 hover:text-zinc-300 transition-colors focus:outline-none focus:ring-1 focus:ring-sky-500/50 rounded px-0.5"
            }
          >
            {crumb.label}
          </button>
        </span>
      ))}
    </nav>
  );
}
