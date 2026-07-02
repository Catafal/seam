/**
 * Shared pure edge-counting helpers for the FilterBar and GraphHUD.
 *
 * WHY a separate module: both the FilterBar (per-option count badges) and any
 * future HUD extension need the same "how many edges of each kind/confidence are
 * currently visible?" query.  Putting it here keeps the components thin and lets
 * us unit-test the arithmetic without React.
 *
 * All functions are pure (no React, no side effects).  Counts are derived from
 * the POST-overlay display edge array (the `displayEdges` returned by
 * useGraphOverlays), so they update automatically after impact/trace overlays
 * set the `hidden` flag.
 *
 * Issue #191 — S6a.
 */

import type { Edge } from "@xyflow/react";

/** Edge data payload carrying kind + confidence from the API. */
interface EdgeData {
  kind?: string;
  confidence?: string;
  [key: string]: unknown;
}

// ── Generic counting primitive ─────────────────────────────────────────────────

/**
 * Count edges by a string field on `edge.data`.
 *
 * @param edges       - array of React Flow edges (may have `hidden` flag)
 * @param field       - the data field to group by ("kind" | "confidence")
 * @param onlyVisible - when true, skip edges with `hidden === true`
 * @returns a map of field-value → count; absent keys have count 0 (not included)
 */
export function countEdgesByField(
  edges: Edge[],
  field: "kind" | "confidence",
  onlyVisible: boolean,
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const e of edges) {
    if (onlyVisible && e.hidden) continue;
    const data = (e.data ?? {}) as EdgeData;
    const value = data[field];
    if (typeof value !== "string" || value === "") continue;
    out[value] = (out[value] ?? 0) + 1;
  }
  return out;
}

// ── Convenience wrappers ───────────────────────────────────────────────────────

/**
 * Count visible (non-hidden) edges by their `kind` field.
 *
 * "Visible" here means edges where `hidden !== true` — the same predicate the
 * FilterBar uses when deciding whether to show an edge.  Hidden edges have been
 * removed from view by the kind/confidence filter but are still in the array so
 * toggling restores them.
 */
export function countVisibleEdgesByKind(edges: Edge[]): Record<string, number> {
  return countEdgesByField(edges, "kind", true);
}

/**
 * Count visible (non-hidden) edges by their `confidence` field.
 *
 * Uses the same visibility predicate as `countVisibleEdgesByKind`.
 */
export function countVisibleEdgesByConfidence(edges: Edge[]): Record<string, number> {
  return countEdgesByField(edges, "confidence", true);
}
