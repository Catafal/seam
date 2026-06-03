/**
 * Constellation layout: turn a ConstellationResponse into React Flow nodes/edges
 * for the whole-repo overview, laid out with dagre.
 *
 * Each cluster becomes one region node (size-scaled by member count, cluster
 * colour); each inter-cluster link becomes an edge whose width ∝ weight. Clicking
 * a region drills into that cluster's neighborhood (handled by the canvas).
 *
 * Pure builder (dagre is deterministic) → unit-tested without React.
 */

import dagre from "dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { ConstellationResponse } from "../api/schema-types";
import { clusterColor } from "./clusterColor";

/** Data carried on a constellation region node. */
export interface ClusterNodeData extends Record<string, unknown> {
  cluster_id: number;
  label: string | null;
  size: number;
  /** Rendered diameter in px (sqrt-scaled by member count) — set by the layout. */
  diameter: number;
}

export type ClusterRFNode = Node<ClusterNodeData>;

// Region node size bounds (px). Diameter scales with member count between these.
const MIN_DIAMETER = 60;
const MAX_DIAMETER = 160;

/**
 * Scale a cluster's member count to a node diameter. Uses a sqrt scale so a
 * 100-symbol cluster isn't 10× the width of a 10-symbol one (area reads better
 * than linear width). Clamped to [MIN_DIAMETER, MAX_DIAMETER].
 */
export function clusterDiameter(size: number, maxSize: number): number {
  if (maxSize <= 0) return MIN_DIAMETER;
  const ratio = Math.sqrt(Math.max(0, size)) / Math.sqrt(maxSize);
  return Math.round(MIN_DIAMETER + ratio * (MAX_DIAMETER - MIN_DIAMETER));
}

/**
 * Build RF nodes + edges for the constellation overview.
 * Returns empty arrays for an empty response (never throws).
 */
export function buildConstellationGraph(
  data: ConstellationResponse,
): { nodes: ClusterRFNode[]; edges: Edge[] } {
  if (data.clusters.length === 0) {
    return { nodes: [], edges: [] };
  }

  const maxSize = data.clusters.reduce((m, c) => Math.max(m, c.size), 0);

  // dagre layout over the cluster graph (left-to-right reads well for regions).
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", ranksep: 120, nodesep: 60, marginx: 30, marginy: 30 });

  const diameters = new Map<number, number>();
  for (const c of data.clusters) {
    const d = clusterDiameter(c.size, maxSize);
    diameters.set(c.cluster_id, d);
    g.setNode(String(c.cluster_id), { width: d, height: d });
  }
  for (const link of data.links) {
    g.setEdge(String(link.source), String(link.target));
  }
  dagre.layout(g);

  const nodes: ClusterRFNode[] = data.clusters.map((c) => {
    const d = diameters.get(c.cluster_id) ?? MIN_DIAMETER;
    const pos = g.node(String(c.cluster_id));
    return {
      id: String(c.cluster_id),
      type: "clusterNode",
      position: pos
        ? { x: pos.x - d / 2, y: pos.y - d / 2 }
        : { x: 0, y: 0 },
      data: { cluster_id: c.cluster_id, label: c.label, size: c.size, diameter: d },
    };
  });

  // Edge width scales with weight (1..6px) so heavy coupling reads as thicker.
  const maxWeight = data.links.reduce((m, l) => Math.max(m, l.weight), 1);
  const edges: Edge[] = data.links.map((link) => {
    const width = 1 + Math.round((link.weight / maxWeight) * 5);
    const colour = clusterColor(link.source) ?? "#52525b";
    return {
      id: `${link.source}->${link.target}`,
      source: String(link.source),
      target: String(link.target),
      style: { stroke: colour, strokeWidth: width, opacity: 0.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: colour },
      label: String(link.weight),
      labelStyle: { fontSize: 9, fill: "#71717a" },
      labelBgStyle: { fill: "transparent" },
    };
  });

  return { nodes, edges };
}
