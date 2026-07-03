/**
 * ClusterGraph2D — 2D cluster-graph using @xyflow/react (C2).
 *
 * Renders the /api/constellation envelope (clusters + weighted links) as a
 * legible node-link diagram. One node per cluster, one edge per inter-cluster
 * link. Node size ∝ cluster symbol count; edge width ∝ link weight.
 *
 * Signal: inter-cluster coupling — hub-and-spoke, mesh, or chain visible at
 * a glance. This is the default Topology view (2D leads; 3D is opt-in).
 *
 * Props:
 *   onOpenCluster   — called when the user clicks a cluster node; the caller
 *                     should center the neighborhood on cluster.representative.
 *
 * Empty state: "No clusters yet — run seam init to build the index."
 * Never a bare empty canvas (story-11).
 *
 * Interaction:
 *   - pan / zoom / fitView on load (same controls as GraphCanvas)
 *   - hover: tooltip via node title attribute
 *   - click: fires onOpenCluster(cluster)
 */

import { useCallback, useEffect } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useConstellation } from "../api/hooks";
import type { ConstellationCluster } from "../api/schema-types";
import {
  clusterGraphLayout,
  type ClusterNodeData,
  type ClusterEdgeData,
} from "../lib/clusterGraphLayout";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface ClusterGraph2DProps {
  /**
   * Called when the user clicks a cluster node. The caller should navigate to
   * the cluster's representative symbol (open the neighborhood centered on it).
   */
  onOpenCluster: (cluster: ConstellationCluster) => void;
}

type ClusterRFNode = Node<ClusterNodeData>;
type ClusterRFEdge = Edge<ClusterEdgeData>;

// ── FitView controller ─────────────────────────────────────────────────────────

/**
 * Inner component that calls fitView() after the graph loads.
 * Must be a child of <ReactFlow> to access the React Flow context.
 */
function FitOnLoad({ ready }: { ready: boolean }) {
  const { fitView } = useReactFlow();

  useEffect(() => {
    if (ready) {
      // Small timeout lets React Flow finish rendering nodes before fitting.
      const id = setTimeout(() => fitView({ padding: 0.15 }), 50);
      return () => clearTimeout(id);
    }
    return undefined;
  }, [ready, fitView]);

  return null;
}

// ── ClusterNode renderer ───────────────────────────────────────────────────────

/**
 * Simple circle-style node for cluster rendering. Because this is a sibling of
 * GraphCanvas (cluster semantics, not symbol semantics), we use a plain inline
 * style rather than the SymbolNode card component.
 *
 * The node is rendered by React Flow's built-in "default" type (clusterGraphLayout
 * sets `type: "default"`). We register NO `nodeTypes` map — the custom look is
 * achieved entirely via the `style` prop set by clusterGraphLayout.
 *
 * NOTE: We render cluster nodes as the RF "default" type and inject appearance
 * via the `style` prop so we stay dependency-free on react-flow's custom node API.
 */

// ── ClusterGraph2D ─────────────────────────────────────────────────────────────

export function ClusterGraph2D({ onOpenCluster }: ClusterGraph2DProps) {
  const { data, isLoading } = useConstellation();

  const [nodes, setNodes, onNodesChange] = useNodesState<ClusterRFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<ClusterRFEdge>([]);

  // Rebuild the layout whenever constellation data changes.
  useEffect(() => {
    if (!data) return;
    const { nodes: rfNodes, edges: rfEdges } = clusterGraphLayout(
      data.clusters,
      data.links,
    );
    setNodes(rfNodes as ClusterRFNode[]);
    setEdges(rfEdges as ClusterRFEdge[]);
  }, [data, setNodes, setEdges]);

  // Click handler: extract the cluster from node data and forward to the caller.
  const handleNodeClick: NodeMouseHandler<ClusterRFNode> = useCallback(
    (_event, node) => {
      const d = node.data;
      const cluster: ConstellationCluster = {
        cluster_id: d.clusterId,
        label: d.label,
        size: d.size,
        representative: d.representative,
      };
      onOpenCluster(cluster);
    },
    [onOpenCluster],
  );

  // ── Loading state ────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-400 text-sm">
        Loading cluster topology…
      </div>
    );
  }

  // ── Empty state ──────────────────────────────────────────────────────────────
  const hasClusters = data && data.clusters.length > 0;
  if (!hasClusters) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-zinc-400">
        <p className="text-base font-medium text-zinc-300">No clusters yet</p>
        <p className="text-sm text-center max-w-xs">
          Run{" "}
          <code className="font-mono bg-zinc-800 px-1 py-0.5 rounded text-zinc-200">
            seam init
          </code>{" "}
          to build the index and generate cluster regions.
        </p>
      </div>
    );
  }

  // ── Graph ────────────────────────────────────────────────────────────────────
  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        // Custom node style: each cluster node is a circle whose dimensions and
        // color are pre-set in clusterGraphLayout. We do not override nodeTypes
        // here — the layout uses the RF default node type with style injection.
        // The label is shown as the node's inner text via the node `label` field.
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.1}
        maxZoom={4}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#27272a" />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={(n) => {
            const data = n.data as ClusterNodeData | undefined;
            return data?.color ?? "#52525b";
          }}
          maskColor="rgba(0,0,0,0.6)"
        />
        <Panel position="top-left">
          <span className="text-xs text-zinc-500 bg-zinc-900/80 px-2 py-1 rounded">
            {data.clusters.length} clusters · {data.links.length} links
          </span>
        </Panel>
        <FitOnLoad ready={nodes.length > 0} />
      </ReactFlow>
    </div>
  );
}
