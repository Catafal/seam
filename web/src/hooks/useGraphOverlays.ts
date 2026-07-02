/**
 * useGraphOverlays — overlay-decoration logic for the GraphCanvas.
 *
 * Extracts the pure decoration functions (decorateNodes, buildOffCanvasNodes,
 * decorateEdges, visibleClusters) and wires them into useMemo calls. Consumers
 * receive ready-to-render displayNodes + displayEdges without knowing the
 * derivation details.
 *
 * WHY a hook instead of plain functions in GraphCanvas: GraphCanvas was
 * approaching the 1000-line limit. Extracting overlays keeps each file focused
 * and allows S5/S6a/S6b/S7 slices to add code without violating the limit.
 *
 * All pure functions are exported so unit tests can verify them in isolation
 * without needing React or a test renderer.
 */

import { useMemo } from "react";
import type { Node, Edge } from "@xyflow/react";

import type { SymbolNodeData } from "../components/SymbolNode";
import type { LegendCluster } from "../components/Legend";
import type { ImpactResponse, TraceResponse } from "../api/schema-types";
import { isEdgeVisible, type EdgeFilterState } from "../lib/edgeFilter";
import { impactTierMap } from "../lib/impactOverlay";
import { tracePathHighlight, edgeKey } from "../lib/tracePath";

// ── Internal types ─────────────────────────────────────────────────────────────

type SymbolRFNode = Node<SymbolNodeData>;

/** Edge payload carried for client-side filtering. */
interface EdgeData extends Record<string, unknown> {
  kind: string;
  confidence: string;
}

// ── Node card dimensions (must match the Tailwind max-w-[240px] card) ──────────

const NODE_WIDTH = 240;
const NODE_HEIGHT = 64;

// ── Pure decoration functions (exported for unit testing) ──────────────────────

/**
 * Apply impact tier + dim flags to base nodes.
 * Does NOT add off-canvas nodes — that is handled by buildOffCanvasNodes.
 */
export function decorateNodes(
  nodes: SymbolRFNode[],
  tierMap: Map<string, string>,
  impactActive: boolean,
  impactTarget: string,
  traceActive: boolean,
  tracePathNames: Set<string>,
): SymbolRFNode[] {
  return nodes.map((n) => {
    const tier = tierMap.get(n.id) ?? null;
    let dimmed = false;
    if (traceActive) {
      // Trace mode: dim everything not on the path.
      dimmed = !tracePathNames.has(n.id);
    } else if (impactActive && tierMap.size > 0) {
      // Impact mode: dim nodes not in blast radius, but never dim the subject itself.
      dimmed = !tierMap.has(n.id) && n.id !== impactTarget;
    }
    return { ...n, data: { ...n.data, impactTier: tier, dimmed } };
  });
}

/**
 * Build faint cards for impacted symbols that are NOT present on the canvas.
 * Places them in a column grid to the right of the existing graph's right edge.
 */
export function buildOffCanvasNodes(
  names: string[],
  tierMap: Map<string, string>,
  baseNodes: SymbolRFNode[],
): SymbolRFNode[] {
  if (names.length === 0) return [];
  const maxX = baseNodes.reduce((m, n) => Math.max(m, n.position.x + NODE_WIDTH), 0);
  const startX = maxX + 80;
  const ROWS = 6;
  return names.map((name, i) => ({
    id: name,
    type: "symbolNode",
    // Off-canvas cards are derived, not base state — dragging would desync them.
    draggable: false,
    position: {
      x: startX + Math.floor(i / ROWS) * (NODE_WIDTH + 24),
      y: (i % ROWS) * (NODE_HEIGHT + 16),
    },
    data: {
      name,
      kind: "",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      isCenter: false,
      impactTier: tierMap.get(name) ?? null,
      offCanvas: true,
    },
  }));
}

/**
 * Apply edge filter (hidden flag) and trace highlight (animated / dimmed) to base edges.
 * Base edge state is never mutated — returns a new array with derived display fields.
 */
export function decorateEdges(
  edges: Edge[],
  filter: EdgeFilterState,
  traceActive: boolean,
  tracePathEdges: Set<string>,
): Edge[] {
  return edges.map((e) => {
    const data = (e.data ?? { kind: "", confidence: "" }) as EdgeData;
    const hidden = !isEdgeVisible(data, filter);
    if (traceActive) {
      const onPath = tracePathEdges.has(edgeKey(e.source, e.target));
      return {
        ...e,
        hidden,
        animated: onPath,
        style: onPath
          ? { stroke: "#38bdf8", strokeWidth: 3 }
          : { ...e.style, opacity: 0.15 },
      };
    }
    return { ...e, hidden };
  });
}

/**
 * Collect the distinct clusters present on the canvas for the Legend colour key.
 * Nodes without a cluster_id (null / undefined) are skipped.
 */
export function visibleClusters(nodes: SymbolRFNode[]): LegendCluster[] {
  const seen = new Map<number, LegendCluster>();
  for (const n of nodes) {
    const id = n.data.cluster_id;
    if (id !== null && id !== undefined && !seen.has(id)) {
      seen.set(id, { cluster_id: id, cluster_label: n.data.cluster_label ?? null });
    }
  }
  return [...seen.values()];
}

// ── Hook ───────────────────────────────────────────────────────────────────────

export interface GraphOverlayInputs {
  nodes: SymbolRFNode[];
  edges: Edge[];
  impactActive: boolean;
  impactData: ImpactResponse | undefined;
  impactTarget: string;
  traceTarget: string | null | undefined;
  traceData: TraceResponse | undefined;
  filter: EdgeFilterState;
}

export interface GraphOverlays {
  /** Decorated nodes ready to pass to ReactFlow (includes off-canvas impact cards). */
  displayNodes: SymbolRFNode[];
  /** Decorated edges ready to pass to ReactFlow (hidden + trace highlight applied). */
  displayEdges: Edge[];
  /** Distinct clusters on canvas — drives the Legend colour key. */
  clusters: LegendCluster[];
  /**
   * The name → risk-tier map for the current impact target.
   * Exposed so the caller can check `tierMap.size > 0` to show the risk-tier Legend.
   */
  tierMap: Map<string, string>;
}

/**
 * Compute all overlay-derived display arrays from base graph state + overlay toggles.
 *
 * Everything inside is derived via useMemo so GraphCanvas only re-decorates
 * when the relevant slice of state actually changes.
 */
export function useGraphOverlays({
  nodes,
  edges,
  impactActive,
  impactData,
  impactTarget,
  traceTarget,
  traceData,
  filter,
}: GraphOverlayInputs): GraphOverlays {
  // Collapse the full impact result into a name→tier map (most-severe tier wins).
  const tierMap = useMemo(
    () => impactTierMap(impactActive ? impactData : undefined),
    [impactActive, impactData],
  );

  // Extract the trace path highlight (node names + edge keys to bold/animate).
  const traceHL = useMemo(
    () => tracePathHighlight(traceTarget ? traceData : undefined),
    [traceTarget, traceData],
  );

  const displayNodes = useMemo(() => {
    const decorated = decorateNodes(
      nodes,
      tierMap,
      impactActive,
      impactTarget,
      traceHL.active,
      traceHL.nodeNames,
    );
    // Impacted symbols that are NOT depth-1 neighbors → faint appended cards.
    const baseIds = new Set(nodes.map((n) => n.id));
    const offCanvasNames = [...tierMap.keys()].filter((name) => !baseIds.has(name));
    return [...decorated, ...buildOffCanvasNodes(offCanvasNames, tierMap, nodes)];
  }, [nodes, tierMap, impactActive, impactTarget, traceHL]);

  const displayEdges = useMemo(
    () => decorateEdges(edges, filter, traceHL.active, traceHL.edgeKeys),
    [edges, filter, traceHL],
  );

  const clusters = useMemo(() => visibleClusters(nodes), [nodes]);

  return { displayNodes, displayEdges, clusters, tierMap };
}
