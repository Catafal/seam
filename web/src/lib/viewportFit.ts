/**
 * viewportFit — pure helper for deciding WHICH nodes to fit in the viewport.
 *
 * Kept pure (no React, no hooks) so it can be unit-tested in isolation and
 * imported safely by both the component and tests without side effects.
 *
 * WHY a separate module: GraphCanvas.tsx is approaching the 1000-line limit;
 * extracting the fit-decision logic keeps the component lean and lets other
 * callers (future tabs, tests) reuse the same policy.
 */

/** The outcome of a fit decision. */
export interface FitDecision {
  /** Which overlay (or none) drove the fit. */
  mode: "impact" | "trace" | "all";
  /**
   * Node ids to frame. Empty array means "fit all visible nodes"
   * (pass to fitView without a `nodes` filter).
   */
  nodeIds: string[];
}

/**
 * Decide which node ids (if any) to fit based on the current overlay state.
 *
 * Priority: trace > impact > all (trace gives the most precise path framing).
 * Guard: if impact is active but tierMap is empty, the data hasn't arrived yet —
 *   fall back to "all" so the viewport doesn't jump to an empty set.
 *
 * @param impactActive  Whether the impact overlay is enabled.
 * @param traceActive   Whether the trace overlay is enabled (path highlighted).
 * @param tierMap       name → risk-tier for all impacted symbols.
 * @param traceNodeNames Names of nodes on the highlighted trace path.
 */
export function fitDecision(
  impactActive: boolean,
  traceActive: boolean,
  tierMap: Map<string, string>,
  traceNodeNames: Set<string>,
): FitDecision {
  // Trace has the most precise scope — frame the path when active.
  if (traceActive && traceNodeNames.size > 0) {
    return { mode: "trace", nodeIds: [...traceNodeNames] };
  }

  // Impact: frame the blast radius, but only when data has arrived.
  if (impactActive && tierMap.size > 0) {
    return { mode: "impact", nodeIds: [...tierMap.keys()] };
  }

  // Clearing overlays, or overlay active but data not yet loaded → fit everything.
  return { mode: "all", nodeIds: [] };
}
