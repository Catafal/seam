/**
 * GraphCanvas — the hero React Flow canvas for the Seam Explorer.
 *
 * Responsibilities:
 * 1. Accepts a center symbol name and calls useNeighborhood to load depth-1 graph
 * 2. Maps GraphNode/GraphEdge from the API to React Flow nodes/edges
 * 3. Computes layout with dagre (client-side, TB direction, padded)
 * 4. Renders custom SymbolNode cards with confidence-styled edges
 * 5. Single-click → select node (drives DetailPanel in F5)
 * 6. Double-click → expand: merges that node's neighborhood into the canvas
 *    (lazy expand pattern — no recursion, stays depth-1 per expand)
 * 7. MiniMap + Controls for navigation
 *
 * WHY dagre (not elkjs): dagre is a proven, minimal JS graph layout library
 * with stable TS types. elkjs is more powerful but requires a Web Worker to
 * avoid blocking the main thread. For depth-1 neighborhoods (< 50 nodes),
 * dagre is fast enough synchronously.
 *
 * WHY merge-expand (not replace): double-clicking a neighbor adds its
 * neighborhood to the existing canvas rather than replacing it. This lets
 * users build up context incrementally without losing already-expanded nodes.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  MarkerType,
  type Node,
  type Edge,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import dagre from "dagre";
import "@xyflow/react/dist/style.css";

import { useNeighborhood } from "../api/hooks";
import type { GraphNode, GraphEdge, NeighborhoodResponse } from "../api/schema-types";
import { SymbolNode } from "./SymbolNode";
import type { SymbolNodeData } from "./SymbolNode";
import { getEdgeStyle } from "../lib/edgeStyle";

// ── Constants ─────────────────────────────────────────────────────────────────

/** Node card dimensions for dagre — must match the Tailwind max-w-[240px] card */
const NODE_WIDTH = 240;
const NODE_HEIGHT = 64;

/**
 * Custom node type registry — tells React Flow which component to use.
 * Defined outside the component to maintain a stable reference (avoids
 * React Flow warning about nodeTypes changing on every render).
 */
const NODE_TYPES: NodeTypes = { symbolNode: SymbolNode };

// ── Type alias: RF Node with SymbolNodeData ───────────────────────────────────

// @xyflow/react v12: Node<T> requires T extends Record<string, unknown>.
// SymbolNodeData satisfies this — see SymbolNode.tsx where it's declared
// with `& Record<string, unknown>`.
type SymbolRFNode = Node<SymbolNodeData>;

// ── Dagre layout ──────────────────────────────────────────────────────────────

/**
 * Compute position for each node using dagre (top-to-bottom layout).
 * Returns a map from node id → { x, y } top-left position.
 *
 * WHY separate from the RF node list: dagre mutates the graph in-place.
 * Building positions first, then constructing RF nodes, keeps the data
 * flow clear and avoids mutating React state objects.
 */
function computeLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", ranksep: 80, nodesep: 40, marginx: 20, marginy: 20 });

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  const positions = new Map<string, { x: number; y: number }>();
  for (const node of nodes) {
    const pos = g.node(node.id);
    if (pos) {
      // dagre returns center coordinates; RF expects top-left corner
      positions.set(node.id, {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      });
    }
  }
  return positions;
}

// ── API → RF conversion ───────────────────────────────────────────────────────

/**
 * Convert API NeighborhoodResponse nodes/edges to React Flow nodes/edges
 * with dagre positions applied.
 */
function buildRFGraph(
  response: NeighborhoodResponse,
): { nodes: SymbolRFNode[]; edges: Edge[] } {
  const positions = computeLayout(response.nodes, response.edges);

  const rfNodes: SymbolRFNode[] = response.nodes.map((n) => {
    const pos = positions.get(n.id) ?? { x: 0, y: 0 };
    return {
      id: n.id,
      type: "symbolNode",
      position: pos,
      data: {
        name: n.name,
        kind: n.kind,
        signature: n.signature,
        cluster_id: n.cluster_id,
        cluster_label: n.cluster_label,
        definition_count: n.definition_count,
        isCenter: n.id === response.center,
      },
    };
  });

  const rfEdges: Edge[] = response.edges.map((e) => {
    const style = getEdgeStyle(e.confidence);
    return {
      id: String(e.id),
      source: e.source,
      target: e.target,
      // Directional arrowhead on target end
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: style.stroke ?? "#a1a1aa",
      },
      style,
      label: e.kind === "import" ? "import" : undefined,
      labelStyle: { fontSize: 9, fill: "#71717a" },
      labelBgStyle: { fill: "transparent" },
    };
  });

  return { nodes: rfNodes, edges: rfEdges };
}

// ── Merge helper ──────────────────────────────────────────────────────────────

/**
 * Merge a new neighborhood response into existing RF nodes/edges arrays.
 *
 * WHY merge rather than replace: double-click expand adds context without
 * wiping the already-visible graph. Duplicate nodes/edges are deduplicated
 * by id so merges are idempotent.
 */
function mergeNeighborhood(
  existingNodes: SymbolRFNode[],
  existingEdges: Edge[],
  newResponse: NeighborhoodResponse,
): { nodes: SymbolRFNode[]; edges: Edge[] } {
  // Offset new nodes below the current graph's bottom edge
  const maxY = existingNodes.reduce(
    (m, n) => Math.max(m, (n.position.y ?? 0) + NODE_HEIGHT),
    0,
  );

  const positions = computeLayout(newResponse.nodes, newResponse.edges);
  const existingNodeIds = new Set(existingNodes.map((n) => n.id));
  const existingEdgeIds = new Set(existingEdges.map((e) => e.id));

  const addedNodes: SymbolRFNode[] = [];
  for (const n of newResponse.nodes) {
    if (existingNodeIds.has(n.id)) continue; // deduplicate by name
    const pos = positions.get(n.id) ?? { x: 0, y: 0 };
    addedNodes.push({
      id: n.id,
      type: "symbolNode",
      position: { x: pos.x, y: pos.y + maxY + 40 },
      data: {
        name: n.name,
        kind: n.kind,
        signature: n.signature,
        cluster_id: n.cluster_id,
        cluster_label: n.cluster_label,
        definition_count: n.definition_count,
        isCenter: n.id === newResponse.center,
      },
    });
  }

  const addedEdges: Edge[] = [];
  for (const e of newResponse.edges) {
    const eid = String(e.id);
    if (existingEdgeIds.has(eid)) continue; // deduplicate by id
    const style = getEdgeStyle(e.confidence);
    addedEdges.push({
      id: eid,
      source: e.source,
      target: e.target,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: style.stroke ?? "#a1a1aa",
      },
      style,
    });
  }

  return {
    nodes: [...existingNodes, ...addedNodes],
    edges: [...existingEdges, ...addedEdges],
  };
}

// ── Component ─────────────────────────────────────────────────────────────────

export interface GraphCanvasProps {
  /** Center symbol name — drives the initial neighborhood fetch */
  center: string;
  /** Called when a node is single-clicked (drives the detail panel) */
  onSelectSymbol?: (name: string) => void;
}

/**
 * GraphCanvas renders the neighborhood graph for a given center symbol.
 *
 * On center change: fresh fetch → rebuild layout → replace canvas.
 * On double-click: lazy-expand by merging the clicked node's neighborhood.
 */
export function GraphCanvas({ center, onSelectSymbol }: GraphCanvasProps) {
  // expandTarget: symbol name to expand on double-click; triggers secondary fetch
  const [expandTarget, setExpandTarget] = useState<string | null>(null);

  const { data: centerData, isLoading } = useNeighborhood(center);
  // Secondary hook for lazy expand — enabled only when expandTarget is set
  const { data: expandData } = useNeighborhood(expandTarget ?? "");

  const [nodes, setNodes, onNodesChange] = useNodesState<SymbolRFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // When the center changes: rebuild the canvas from scratch
  useEffect(() => {
    if (!centerData) return;
    setExpandTarget(null);
    const { nodes: rfNodes, edges: rfEdges } = buildRFGraph(centerData);
    setNodes(rfNodes);
    setEdges(rfEdges);
  }, [centerData, setNodes, setEdges]);

  // When an expand target resolves: merge the new neighborhood in
  useEffect(() => {
    if (!expandData || expandTarget === null) return;
    const merged = mergeNeighborhood(nodes, edges, expandData);
    setNodes(merged.nodes);
    setEdges(merged.edges);
    setExpandTarget(null); // clear so this effect doesn't re-fire
  // Intentionally not listing nodes/edges in deps — mergeNeighborhood reads
  // a snapshot; we only want to fire when NEW expandData arrives.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandData]);

  // Single-click: select → drive detail panel
  const handleNodeClick: NodeMouseHandler<SymbolRFNode> = useCallback(
    (_evt, node) => {
      onSelectSymbol?.(node.id);
    },
    [onSelectSymbol],
  );

  // Double-click: expand that node's neighborhood
  const handleNodeDoubleClick: NodeMouseHandler<SymbolRFNode> = useCallback(
    (_evt, node) => {
      setExpandTarget(node.id);
    },
    [],
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center w-full h-full text-zinc-500 text-sm">
        Loading neighborhood…
      </div>
    );
  }

  return (
    <div className="w-full h-full">
      <ReactFlow<SymbolRFNode, Edge>
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onNodeDoubleClick={handleNodeDoubleClick}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={3}
        attributionPosition="bottom-left"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="#3f3f46"
        />
        <MiniMap
          maskColor="rgba(24,24,27,0.8)"
          style={{ background: "#18181b" }}
        />
        <Controls
          style={{ background: "#27272a", border: "1px solid #3f3f46" }}
        />
      </ReactFlow>
    </div>
  );
}
