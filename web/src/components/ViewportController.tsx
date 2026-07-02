/**
 * ViewportController — flies the React Flow viewport to frame impact/trace
 * overlays when they activate, and restores the full-graph view when they clear.
 *
 * MUST be rendered as a child of <ReactFlow> so useReactFlow() has access to
 * the provider context. It cannot be called at the outer canvas level.
 *
 * WHY a separate component (not inline in GraphCanvas):
 *   useReactFlow() is only valid inside the ReactFlow tree. Extracting it here
 *   lets GraphCanvas stay outside the provider while keeping the viewport logic
 *   clean and unit-testable in isolation.
 *
 * Effect gating via prev-value refs:
 *   fitView fires on the boolean activation transition AND on the first render
 *   where the precise node set becomes available for the current activation
 *   (impact/trace data arrives ASYNCHRONOUSLY, after the toggle). Later refreshes
 *   for the same activation (node expansion, data re-fetch) are ignored via the
 *   `framed` ref so a stable active overlay never causes spurious jumps.
 *
 * "Fit all" escape hatch:
 *   A small button on the canvas lets the user restore the full-graph view at any
 *   time — useful after manual panning into a tight area.
 */

import { useEffect, useRef, useCallback } from "react";
import { useReactFlow, Panel } from "@xyflow/react";
import { Maximize2 } from "lucide-react";
import { fitDecision } from "../lib/viewportFit";

/** Duration of the fly-to-fit animation in milliseconds. */
const FIT_DURATION_MS = 600;

/** Padding around the framed nodes (fraction of viewport). */
const FIT_PADDING = 0.2;

export interface ViewportControllerProps {
  /** Whether the impact blast-radius overlay is currently active. */
  impactActive: boolean;
  /** Whether the trace path overlay is currently active. */
  traceActive: boolean;
  /** name → risk-tier for all impacted symbols (from seam_impact). */
  tierMap: Map<string, string>;
  /** Names of nodes on the highlighted trace path. */
  traceNodeNames: Set<string>;
}

/**
 * ViewportController renders nothing visible except the "fit all" button.
 * Its primary job is to trigger smooth fitView calls on overlay transitions.
 */
export function ViewportController({
  impactActive,
  traceActive,
  tierMap,
  traceNodeNames,
}: ViewportControllerProps) {
  const { fitView } = useReactFlow();

  // Track previous boolean values so we fire only on real transitions.
  const prevImpact = useRef<boolean>(impactActive);
  const prevTrace = useRef<boolean>(traceActive);
  // Whether the precise node set has already been framed for the CURRENT
  // activation. Reset when the overlay clears so the next activation reframes
  // once its (async) data lands.
  const framed = useRef<boolean>(false);

  useEffect(() => {
    const impactChanged = impactActive !== prevImpact.current;
    const traceChanged = traceActive !== prevTrace.current;
    prevImpact.current = impactActive;
    prevTrace.current = traceActive;

    const active = impactActive || traceActive;

    // Overlay cleared → restore the full-graph view, but only on the clearing
    // transition (not on later inactive re-renders).
    if (!active) {
      if (impactChanged || traceChanged) {
        framed.current = false;
        void fitView({ duration: FIT_DURATION_MS, padding: FIT_PADDING });
      }
      return;
    }

    const decision = fitDecision(impactActive, traceActive, tierMap, traceNodeNames);

    // Overlay active but its node set is not available yet (the toggle fired
    // before impact/trace data resolved). Do nothing now — the data-arrival
    // render below reframes once tierMap/traceNodeNames populate.
    if (decision.nodeIds.length === 0) return;

    // Fire on the activation transition OR on the first render where the node
    // set becomes available for this activation. Skip later refreshes so a
    // stable active overlay (node expansion, re-fetch) never jumps the viewport.
    if (impactChanged || traceChanged || !framed.current) {
      framed.current = true;
      void fitView({
        nodes: decision.nodeIds.map((id) => ({ id })),
        duration: FIT_DURATION_MS,
        padding: FIT_PADDING,
      });
    }
  }, [impactActive, traceActive, tierMap, traceNodeNames, fitView]);

  // "Fit all" escape hatch: always available on the canvas.
  const handleFitAll = useCallback(() => {
    void fitView({ duration: FIT_DURATION_MS, padding: FIT_PADDING });
  }, [fitView]);

  return (
    <Panel position="bottom-right">
      <button
        onClick={handleFitAll}
        aria-label="Fit all nodes in view"
        title="Fit all"
        className="
          flex items-center justify-center
          w-7 h-7 rounded
          bg-zinc-900/90 border border-zinc-700
          text-zinc-400 hover:text-zinc-100
          hover:border-zinc-500
          transition-colors
        "
      >
        <Maximize2 className="w-3.5 h-3.5" />
      </button>
    </Panel>
  );
}
