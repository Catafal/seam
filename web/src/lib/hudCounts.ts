/**
 * Pure HUD-counts helper for the 2D GraphCanvas HUD.
 *
 * Derives all displayed counts from the already-computed display arrays returned
 * by useGraphOverlays.  Pure + framework-free so it can be unit-tested without
 * React or React Flow.
 *
 * WHY separate from the component: the counts are computed once per render cycle
 * and can be verified independently.  Keeping the logic pure also allows the HUD
 * component to stay thin (just rendering the returned numbers).
 */

import type { Node, Edge } from "@xyflow/react";
import type { SymbolNodeData } from "../components/SymbolNode";

type SymbolRFNode = Node<SymbolNodeData>;

/** All values the GraphHUD needs to display. */
export interface HudCounts {
  /** On-canvas symbol nodes (excludes off-canvas impact-card appends). */
  visibleNodes: number;
  /** Edges not hidden by the client-side edge filter. */
  visibleEdges: number;
  /** Edges currently hidden by the edge filter (the "filtered out" badge). */
  filteredOut: number;
  /**
   * Off-canvas impact-card nodes appended by the impact overlay.
   * These are dependents beyond the depth-1 neighborhood; shown as a separate
   * "+N impacted" readout rather than inflating visibleNodes.
   * Always computed; caller controls visibility (e.g. only show when impactActive).
   */
  impactedOffCanvas: number;
  /** 1 when a node is selected, 0 otherwise (single-selection model). */
  selectedCount: number;
}

/**
 * Compute HUD display counts from the current canvas state.
 *
 * @param displayNodes  Decorated nodes from useGraphOverlays (may include off-canvas cards).
 * @param displayEdges  Decorated edges from useGraphOverlays (may have `hidden` set by filter).
 * @param selectedNode  The name of the currently selected node, or null.
 */
export function computeHudCounts(
  displayNodes: SymbolRFNode[],
  displayEdges: Edge[],
  selectedNode: string | null,
): HudCounts {
  // Off-canvas cards are appended by buildOffCanvasNodes for impact dependents
  // beyond the depth-1 neighborhood — they should NOT inflate "visible nodes".
  let visibleNodes = 0;
  let impactedOffCanvas = 0;
  for (const n of displayNodes) {
    if (n.data.offCanvas) {
      impactedOffCanvas++;
    } else {
      visibleNodes++;
    }
  }

  // Edge counts: the filter applies a `hidden` flag (never removes from the array).
  let visibleEdges = 0;
  let filteredOut = 0;
  for (const e of displayEdges) {
    if (e.hidden) {
      filteredOut++;
    } else {
      visibleEdges++;
    }
  }

  // Single-selection model: 1 selected when a node has been clicked, else 0.
  const selectedCount = selectedNode !== null ? 1 : 0;

  return { visibleNodes, visibleEdges, filteredOut, impactedOffCanvas, selectedCount };
}
