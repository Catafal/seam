/**
 * ConstellationCanvas — whole-repo overview (F6).
 *
 * Renders every cluster as a region node (size-scaled circle, cluster colour),
 * with inter-cluster links whose width ∝ coupling weight. Clicking a region
 * drills into that cluster's neighborhood, centered on a representative member
 * symbol (from /api/clusters, since a cluster is not itself a symbol).
 *
 * Reuses the pure buildConstellationGraph layout helper; the canvas only wires
 * data + interaction.
 */

import { useMemo } from "react";
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  Handle,
  Position,
  type NodeTypes,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useConstellation, useClusters } from "../api/hooks";
import { buildConstellationGraph, type ClusterNodeData } from "../lib/constellationLayout";
import { clusterColor } from "../lib/clusterColor";

// ── Custom cluster region node ──────────────────────────────────────────────

/**
 * A cluster region: a colour-filled circle sized by member count, label below.
 * Handles are hidden (links attach at the circle edge) but required by RF.
 */
function ClusterNode({ data }: NodeProps) {
  const { label, size, cluster_id, diameter } = data as ClusterNodeData;
  const colour = clusterColor(cluster_id) ?? "#52525b";
  return (
    <div
      className="flex flex-col items-center justify-center cursor-pointer select-none rounded-full transition-transform hover:scale-105"
      style={{
        width: diameter,
        height: diameter,
        backgroundColor: `${colour}22`, // ~13% alpha fill
        border: `2px solid ${colour}`,
      }}
      title={`${label ?? `cluster-${cluster_id}`} — ${size} symbols`}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <span className="text-[11px] font-semibold text-zinc-100 px-2 text-center truncate max-w-full">
        {label ?? `cluster-${cluster_id}`}
      </span>
      <span className="text-[9px] text-zinc-400 font-mono">{size}</span>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const NODE_TYPES: NodeTypes = { clusterNode: ClusterNode };

export interface ConstellationCanvasProps {
  /** Called with a representative symbol name when a cluster region is clicked. */
  onSelectCluster: (representative: string) => void;
}

export function ConstellationCanvas({ onSelectCluster }: ConstellationCanvasProps) {
  const { data, isLoading } = useConstellation(true);
  // Representative member per cluster (so a region click can open a real symbol).
  const { data: clusterList } = useClusters();

  const { nodes, edges } = useMemo(
    () => (data ? buildConstellationGraph(data) : { nodes: [], edges: [] }),
    [data],
  );

  const repByCluster = useMemo(() => {
    const m = new Map<number, string>();
    for (const c of clusterList ?? []) {
      if (c.representative) m.set(c.cluster_id, c.representative);
    }
    return m;
  }, [clusterList]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center w-full h-full text-zinc-500 text-sm">
        Loading overview…
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center w-full h-full gap-2 text-center p-8">
        <p className="text-zinc-500 text-sm">No clusters to map.</p>
        <p className="text-zinc-600 text-xs">
          Run <code className="text-zinc-400">seam init</code> to build clusters, or the
          repo may be too small for community detection.
        </p>
      </div>
    );
  }

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_evt, node) => {
          const cid = (node.data as ClusterNodeData).cluster_id;
          const rep = repByCluster.get(cid);
          if (rep) onSelectCluster(rep);
        }}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={2}
        nodesDraggable={false}
        attributionPosition="bottom-left"
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#3f3f46" />
        <MiniMap maskColor="rgba(24,24,27,0.8)" style={{ background: "#18181b" }} />
        <Controls
          showInteractive={false}
          style={{ background: "#27272a", border: "1px solid #3f3f46" }}
        />
      </ReactFlow>
    </div>
  );
}
