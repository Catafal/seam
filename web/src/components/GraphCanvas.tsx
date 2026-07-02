/**
 * GraphCanvas — the hero React Flow canvas for the Seam Explorer.
 *
 * Base behaviour (v1):
 *   - center symbol → useNeighborhood → dagre-laid-out card-canvas
 *   - single-click → select (drives DetailPanel); double-click → lazy-merge expand
 *   - MiniMap + Controls + dotted background
 *
 * Phase 2 overlays (all derived from the base graph via useMemo — base node/edge
 * STATE is never mutated by an overlay, so toggling an overlay off restores the
 * original view exactly):
 *   - Edge FILTER (F2): hide edges by kind/confidence (client-side `hidden` flag)
 *   - IMPACT overlay (F3): paint the center symbol's blast radius by risk tier,
 *     dim non-affected nodes, and append off-canvas dependents as faint cards
 *   - TRACE overlay (F4): bold the shortest path center→traceTarget, dim the rest
 *   - LEGEND (F2): always-on key for confidence/clusters/(risk tiers when impacting)
 *
 * WHY dagre (not elkjs): proven, synchronous, stable TS types — fine for depth-1
 * neighborhoods (< 50 nodes). WHY merge-expand (not replace): double-click adds
 * context incrementally without losing already-expanded nodes.
 *
 * Overlay-decoration logic (decorateNodes, buildOffCanvasNodes, decorateEdges,
 * visibleClusters, tierMap, traceHL) lives in useGraphOverlays to keep this file
 * under the 1000-line limit as HUD/filter/fly-to-fit slices are added.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  Panel,
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
import { Zap, X } from "lucide-react";

import { useNeighborhood, useImpact, useTrace } from "../api/hooks";
import type { GraphNode, GraphEdge, NeighborhoodResponse } from "../api/schema-types";
import { SymbolNode } from "./SymbolNode";
import type { SymbolNodeData } from "./SymbolNode";
import { getEdgeStyle } from "../lib/edgeStyle";
import { Legend } from "./Legend";
import { FilterBar } from "./FilterBar";
import { GraphHUD } from "./GraphHUD";
import {
  toggleFilterValue,
} from "../lib/edgeFilter";
import {
  loadGraphFilter,
  saveGraphFilter,
  toggleNodeKind,
  allNodeKinds,
  noneNodeKinds,
  type GraphFilterState,
} from "../lib/graphFilterState";
import { useGraphOverlays } from "../hooks/useGraphOverlays";
import { computeHudCounts } from "../lib/hudCounts";
import { ViewportController } from "./ViewportController";
import {
  countVisibleEdgesByKind,
  countVisibleEdgesByConfidence,
} from "../lib/filterBarCounts";
import { ALL_EDGE_KINDS, ALL_CONFIDENCES } from "../lib/edgeFilter";

// ── Constants ─────────────────────────────────────────────────────────────────

/** Node card dimensions for dagre — must match the Tailwind max-w-[240px] card */
const NODE_WIDTH = 240;
const NODE_HEIGHT = 64;

const NODE_TYPES: NodeTypes = { symbolNode: SymbolNode };

type SymbolRFNode = Node<SymbolNodeData>;

/** Edge payload carried for client-side filtering (kind + confidence). */
interface EdgeData extends Record<string, unknown> {
  kind: string;
  confidence: string;
}

// ── Dagre layout ──────────────────────────────────────────────────────────────

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
      positions.set(node.id, {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      });
    }
  }
  return positions;
}

// ── API → RF conversion ───────────────────────────────────────────────────────

/** Build a base RF node from an API GraphNode (no overlay state — that's derived). */
function toRFNode(n: GraphNode, center: string, pos: { x: number; y: number }): SymbolRFNode {
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
      isCenter: n.id === center,
      is_exported: n.is_exported,
      visibility: n.visibility,
    },
  };
}

/** Build a base RF edge — confidence style + kind/confidence in data for filtering. */
function toRFEdge(e: GraphEdge): Edge {
  const style = getEdgeStyle(e.confidence);
  return {
    id: String(e.id),
    source: e.source,
    target: e.target,
    markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke ?? "#a1a1aa" },
    style,
    data: { kind: e.kind, confidence: e.confidence } as EdgeData,
    label: e.kind === "import" ? "import" : undefined,
    labelStyle: { fontSize: 9, fill: "#71717a" },
    labelBgStyle: { fill: "transparent" },
  };
}

function buildRFGraph(
  response: NeighborhoodResponse,
): { nodes: SymbolRFNode[]; edges: Edge[] } {
  const positions = computeLayout(response.nodes, response.edges);
  const rfNodes = response.nodes.map((n) =>
    toRFNode(n, response.center, positions.get(n.id) ?? { x: 0, y: 0 }),
  );
  const rfEdges = response.edges.map(toRFEdge);
  return { nodes: rfNodes, edges: rfEdges };
}

// ── Merge helper ──────────────────────────────────────────────────────────────

function mergeNeighborhood(
  existingNodes: SymbolRFNode[],
  existingEdges: Edge[],
  newResponse: NeighborhoodResponse,
): { nodes: SymbolRFNode[]; edges: Edge[] } {
  const maxY = existingNodes.reduce(
    (m, n) => Math.max(m, (n.position.y ?? 0) + NODE_HEIGHT),
    0,
  );
  const positions = computeLayout(newResponse.nodes, newResponse.edges);
  const existingNodeIds = new Set(existingNodes.map((n) => n.id));
  const existingEdgeIds = new Set(existingEdges.map((e) => e.id));

  const addedNodes: SymbolRFNode[] = [];
  for (const n of newResponse.nodes) {
    if (existingNodeIds.has(n.id)) continue;
    const pos = positions.get(n.id) ?? { x: 0, y: 0 };
    const node = toRFNode(n, newResponse.center, { x: pos.x, y: pos.y + maxY + 40 });
    addedNodes.push(node);
  }

  const addedEdges: Edge[] = [];
  for (const e of newResponse.edges) {
    if (existingEdgeIds.has(String(e.id))) continue;
    addedEdges.push(toRFEdge(e));
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
  /** Trace target — when set, highlight the shortest path center→target (F4) */
  traceTarget?: string | null;
}

export function GraphCanvas({ center, onSelectSymbol, traceTarget }: GraphCanvasProps) {
  const [expandTarget, setExpandTarget] = useState<string | null>(null);
  // Filter state is initialized from localStorage so preferences persist across
  // page reloads. It is NOT reset on center change (session-global by design).
  const [filter, setFilter] = useState<GraphFilterState>(() => loadGraphFilter());
  const [impactActive, setImpactActive] = useState(false);
  // The node the user last clicked — impact analyses THIS (falls back to center),
  // so "click a node → Impact" shows that node's blast radius, not the center's.
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const impactTarget = selectedNode ?? center;

  const { data: centerData, isLoading } = useNeighborhood(center);
  const { data: expandData } = useNeighborhood(expandTarget ?? "");
  // Impact is opt-in (toggle); it analyses the selected node, else the center.
  const { data: impactData } = useImpact(impactTarget, "both", impactActive);
  // Trace runs whenever App supplies a target (second search box).
  const { data: traceData } = useTrace(center, traceTarget ?? null);

  const [nodes, setNodes, onNodesChange] = useNodesState<SymbolRFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Persist filter to localStorage whenever it changes.
  useEffect(() => { saveGraphFilter(filter); }, [filter]);

  // Rebuild canvas + reset overlays when the center changes.
  useEffect(() => {
    if (!centerData) return;
    setExpandTarget(null);
    setImpactActive(false);
    setSelectedNode(null);
    const { nodes: rfNodes, edges: rfEdges } = buildRFGraph(centerData);
    setNodes(rfNodes);
    setEdges(rfEdges);
  }, [centerData, setNodes, setEdges]);

  // Merge an expanded neighborhood in when its fetch resolves.
  useEffect(() => {
    if (!expandData || expandTarget === null) return;
    const merged = mergeNeighborhood(nodes, edges, expandData);
    setNodes(merged.nodes);
    setEdges(merged.edges);
    setExpandTarget(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandData]);

  // ── Derived overlay state (delegated to useGraphOverlays) ───────────────────
  const { displayNodes, displayEdges, clusters, tierMap, traceActive, traceNodeNames } = useGraphOverlays({
    nodes,
    edges,
    impactActive,
    impactData,
    impactTarget,
    traceTarget,
    traceData,
    filter,
    enabledNodeKinds: filter.nodeKinds,
  });

  // ── Filter counts from post-overlay edges (updates after impact/trace) ──────
  // useMemo so counts only recompute when displayEdges actually changes.
  const kindCounts = useMemo(
    () => countVisibleEdgesByKind(displayEdges),
    [displayEdges],
  );
  const confidenceCounts = useMemo(
    () => countVisibleEdgesByConfidence(displayEdges),
    [displayEdges],
  );

  // ── Node-kind counts (from base nodes before filtering) ───────────────────
  // Count each kind in the raw neighborhood (pre-filter) so chips show corpus size.
  const nodeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of nodes) {
      const k = n.data.kind ?? "";
      if (k) counts[k] = (counts[k] ?? 0) + 1;
    }
    return counts;
  }, [nodes]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleNodeClick: NodeMouseHandler<SymbolRFNode> = useCallback(
    (_evt, node) => {
      setSelectedNode(node.id);
      onSelectSymbol?.(node.id);
    },
    [onSelectSymbol],
  );
  const handleNodeDoubleClick: NodeMouseHandler<SymbolRFNode> = useCallback(
    (_evt, node) => setExpandTarget(node.id),
    [],
  );
  const handleToggleFilter = useCallback(
    (field: "kinds" | "confidences", value: string) =>
      // toggleFilterValue returns EdgeFilterState; cast is safe because the
      // spread preserves nodeKinds from the GraphFilterState input.
      setFilter((f) => toggleFilterValue(f, field, value) as GraphFilterState),
    [],
  );
  // Select-all: enable every kind / confidence tier.
  const handleAllKinds = useCallback(
    () => setFilter((f) => ({ ...f, kinds: new Set(ALL_EDGE_KINDS) })),
    [],
  );
  // Clear-all: disable every kind (no edges visible until re-enabled).
  const handleNoneKinds = useCallback(
    () => setFilter((f) => ({ ...f, kinds: new Set<string>() })),
    [],
  );
  const handleAllConfidences = useCallback(
    () => setFilter((f) => ({ ...f, confidences: new Set(ALL_CONFIDENCES) })),
    [],
  );
  const handleNoneConfidences = useCallback(
    () => setFilter((f) => ({ ...f, confidences: new Set<string>() })),
    [],
  );

  // ── Node-kind filter handlers ─────────────────────────────────────────────
  const handleToggleNodeKind = useCallback(
    (kind: string) => setFilter((f) => toggleNodeKind(f, kind)),
    [],
  );
  const handleAllNodeKinds = useCallback(
    () => setFilter((f) => allNodeKinds(f)),
    [],
  );
  const handleNoneNodeKinds = useCallback(
    () => setFilter((f) => noneNodeKinds(f)),
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
        nodes={displayNodes}
        edges={displayEdges}
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
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#3f3f46" />

        {/* Top-left: legend */}
        <Panel position="top-left">
          <Legend clusters={clusters} showRiskTiers={impactActive && tierMap.size > 0} />
        </Panel>

        {/* Top-right: impact toggle + edge filter */}
        <Panel position="top-right">
          <div className="flex flex-col items-end gap-1.5">
            <button
              onClick={() => setImpactActive((a) => !a)}
              aria-pressed={impactActive}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold border transition-colors ${
                impactActive
                  ? "bg-red-500/20 border-red-500/60 text-red-300"
                  : "bg-zinc-900/90 border-zinc-700 text-zinc-300 hover:border-zinc-500"
              }`}
              title={`Show blast radius for ${impactTarget}`}
            >
              {impactActive ? <X className="w-3.5 h-3.5" /> : <Zap className="w-3.5 h-3.5" />}
              {impactActive ? "Clear impact" : `Impact: ${impactTarget}`}
            </button>
            {impactActive && impactData && (
              <ImpactSummary summary={impactData.risk_summary} />
            )}
            <FilterBar
              filter={filter}
              onToggle={handleToggleFilter}
              onAllKinds={handleAllKinds}
              onNoneKinds={handleNoneKinds}
              onAllConfidences={handleAllConfidences}
              onNoneConfidences={handleNoneConfidences}
              kindCounts={kindCounts}
              confidenceCounts={confidenceCounts}
              nodeKindFilter={filter.nodeKinds}
              onToggleNodeKind={handleToggleNodeKind}
              onAllNodeKinds={handleAllNodeKinds}
              onNoneNodeKinds={handleNoneNodeKinds}
              nodeCounts={nodeCounts}
            />
          </div>
        </Panel>

        <MiniMap maskColor="rgba(24,24,27,0.8)" style={{ background: "#18181b" }} />
        <Controls style={{ background: "#27272a", border: "1px solid #3f3f46" }} />

        {/* Bottom-left: HUD overlay — below the legend to avoid overlap */}
        <Panel position="bottom-left">
          <GraphHUD
            counts={computeHudCounts(displayNodes, displayEdges, selectedNode)}
            impactActive={impactActive}
          />
        </Panel>

        {/* Viewport fly-to-fit controller: must live inside <ReactFlow> to access
            useReactFlow(). Renders the "fit all" escape-hatch button (bottom-right). */}
        <ViewportController
          impactActive={impactActive}
          traceActive={traceActive}
          tierMap={tierMap}
          traceNodeNames={traceNodeNames}
        />
      </ReactFlow>
    </div>
  );
}

// ── Impact summary chip ─────────────────────────────────────────────────────

/** Compact per-tier total across both directions, shown under the Impact toggle. */
function ImpactSummary({ summary }: { summary: Record<string, Record<string, number>> }) {
  const totals: Record<string, number> = {};
  for (const dir of Object.values(summary)) {
    for (const [tier, count] of Object.entries(dir)) {
      totals[tier] = (totals[tier] ?? 0) + count;
    }
  }
  const will = totals["WILL_BREAK"] ?? 0;
  const likely = totals["LIKELY_AFFECTED"] ?? 0;
  const maybe = totals["MAY_NEED_TESTING"] ?? 0;
  return (
    <div className="bg-zinc-900/90 border border-zinc-700 rounded-md px-2.5 py-1.5 text-[10px] font-mono backdrop-blur-sm">
      <span className="text-red-400">{will} break</span>
      <span className="text-zinc-600"> · </span>
      <span className="text-amber-400">{likely} likely</span>
      <span className="text-zinc-600"> · </span>
      <span className="text-slate-400">{maybe} maybe</span>
    </div>
  );
}
