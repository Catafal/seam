/**
 * breadcrumbs.ts — pure App-level breadcrumb derivation.
 *
 * WHY this is a separate pure module (not inline in App.tsx):
 *   Keeps the derivation unit-testable without React — inject mock handlers,
 *   assert exact crumb labels and onClick wiring. The component (Breadcrumb.tsx)
 *   is just a thin renderer over the Crumb[] array this function produces.
 *
 * TWO-LEVEL BREADCRUMB SYSTEM:
 *   App-level (this module): repo → area → symbol → selected
 *     - Models cross-surface navigation (landing → overview → neighborhood drill)
 *     - Lives above <main>; present on ALL surfaces
 *   Treemap-internal (TreemapCanvas.tsx): scopeName → folder → file → class
 *     - Models the within-Overview drill (folder→file→class)
 *     - Lives inside the treemap; only present on the Overview surface
 *   Together they form one coherent trail: the App-level crumbs are the "outer"
 *   path that gets you to the overview/symbol; the treemap crumbs are the "inner"
 *   path once you're inside a specific area.
 */

import type { Area } from "./deriveAreas";
import type { ViewMode } from "./tabs";

// ── Public types ─────────────────────────────────────────────────────────────

/** A single breadcrumb entry: a label and the action to restore that level. */
export interface Crumb {
  /** Display text — repo root, area name, symbol name, or selected node name. */
  label: string;
  /**
   * Click handler that restores navigation to exactly this level.
   * Always a no-arg function; the specific action is baked in at derivation time.
   */
  onClick: () => void;
  /** True for the last (rightmost) crumb — marks the current position. */
  isCurrent: boolean;
}

/** Snapshot of the App navigation state relevant to breadcrumb derivation. */
export interface BreadcrumbState {
  /** Which tab/surface is active. */
  mode: ViewMode;
  /** Set when the user entered Overview from a landing area card. */
  preselectedArea: Area | null;
  /** The symbol centered in the neighborhood graph (null on landing). */
  centerSymbol: string | null;
  /** The node selected in the graph detail panel (may equal centerSymbol). */
  selectedSymbol: string | null;
}

/**
 * Handlers injected from App so the pure function doesn't import React or
 * reference App state directly — this keeps deriveCrumbs fully unit-testable.
 */
export interface BreadcrumbHandlers {
  /** Reset the entire App to the landing page (the home action). */
  goHome: () => void;
  /** Enter the Overview pre-drilled to a specific area. */
  openArea: (area: Area) => void;
  /**
   * Navigate back to the center symbol level (clears selectedSymbol, stays in
   * neighborhood mode). A no-op when there is no deeper selected node.
   */
  openCenterSymbol: () => void;
}

// ── Core derivation ───────────────────────────────────────────────────────────

/**
 * Derive the App-level breadcrumb trail from the current navigation state.
 *
 * Trail grammar (each step only added when its condition is met):
 *   repo (always)
 *   → area  (when preselectedArea is set AND mode is overview or neighborhood)
 *   → symbol (when centerSymbol is set AND mode is neighborhood)
 *   → selected (when selectedSymbol is set, distinct from centerSymbol, AND in neighborhood)
 *
 * Topology mode: only [repo] — the topology surface has no drill path.
 *
 * The last crumb always has isCurrent=true. All crumbs (including the current one)
 * carry an onClick — keyboard users can always tab through and activate any crumb.
 * For the current crumb the onClick is a harmless self-restoration (no visible effect).
 */
export function deriveCrumbs(state: BreadcrumbState, handlers: BreadcrumbHandlers): Crumb[] {
  const { mode, preselectedArea, centerSymbol, selectedSymbol } = state;

  // Internal accumulator — (label, onClick) pairs; isCurrent added at the end.
  const items: Array<{ label: string; onClick: () => void }> = [];

  // ── Repo root — always first ──────────────────────────────────────────────
  // onClick = goHome so clicking "repo" from anywhere returns to the landing.
  items.push({ label: "repo", onClick: handlers.goHome });

  // ── Topology: no further drill levels ─────────────────────────────────────
  if (mode === "topology") {
    return items.map((item, i) => ({ ...item, isCurrent: i === items.length - 1 }));
  }

  // ── Overview surface ──────────────────────────────────────────────────────
  if (mode === "overview") {
    if (preselectedArea) {
      // Re-entering this area: openArea switches to overview + sets the area.
      const area = preselectedArea;
      items.push({ label: area.name, onClick: () => handlers.openArea(area) });
    }
    return items.map((item, i) => ({ ...item, isCurrent: i === items.length - 1 }));
  }

  // ── Neighborhood surface ──────────────────────────────────────────────────
  // Area crumb: present when the user drilled from a landing area card into a
  // symbol's neighborhood (preselectedArea is still set even in neighborhood mode).
  if (preselectedArea) {
    const area = preselectedArea;
    items.push({ label: area.name, onClick: () => handlers.openArea(area) });
  }

  if (centerSymbol) {
    // Symbol crumb: clicking it restores the center-only view (clears selectedSymbol).
    // openCenterSymbol always sets neighborhood mode + clears selected, which is
    // correct whether or not a deeper selected node exists.
    items.push({ label: centerSymbol, onClick: handlers.openCenterSymbol });

    // Selected node crumb: only when selectedSymbol is distinct from centerSymbol.
    // Clicking it is effectively a no-op (you're already here), but the crumb is
    // still a focusable button for keyboard users (WAI-ARIA breadcrumb pattern).
    if (selectedSymbol && selectedSymbol !== centerSymbol) {
      const sel = selectedSymbol;
      items.push({ label: sel, onClick: () => {} });
    }
  }

  return items.map((item, i) => ({ ...item, isCurrent: i === items.length - 1 }));
}
