/**
 * clusterGraphLayout — pure, deterministic layout for the 2D cluster graph (C2).
 *
 * Transforms the /api/constellation envelope (clusters + links) into React Flow
 * nodes and edges with a radial/circular arrangement ordered by cluster size.
 *
 * Design decisions:
 *   - DETERMINISTIC: clusters are sorted by size DESC (ties broken by cluster_id ASC)
 *     before positioning, so the layout is stable across rerenders and page reloads.
 *   - NO physics simulation: at the 20–50 cluster scale this API returns, a static radial
 *     layout is already legible — the developer can read the macro shape (hub-and-spoke,
 *     mesh, chain) at a glance. A force simulation would add jitter, non-determinism, and
 *     test complexity without adding legibility for this cluster count. Reproducibility
 *     (story-13) and unit-testability (story-16) are free bonuses of the static choice.
 *     (A 2,000-node 3D cloud IS illegible because perspective + occlusion hide the shape;
 *     a 40-node 2D circle is not, so the simpler layout is the better one here.)
 *   - Node size ∝ cluster symbol count (monotonic: larger cluster → bigger node).
 *   - Edge strokeWidth ∝ link weight (monotonic: heavier link → wider stroke).
 *   - Edge opacity ∝ link weight (monotonic: heavier link → more opaque).
 *   - Node color = clusterColor(cluster_id) (identity palette shared with treemap/detail).
 *   - Pure: no React, no DB, no side effects. All inputs → pure outputs.
 */

import type { Node, Edge } from "@xyflow/react";
import type { ConstellationCluster, ConstellationLink } from "../api/schema-types";
import { clusterColor } from "./clusterColor";

// ── Constants ──────────────────────────────────────────────────────────────────

/** Minimum node size (width and height are equal — circular nodes). */
const NODE_SIZE_MIN = 40;
/** Maximum node size cap. */
const NODE_SIZE_MAX = 120;
/** Radius of the layout circle in pixels. Scales with cluster count. */
const BASE_RADIUS = 260;
/** Minimum edge stroke width. */
const EDGE_WIDTH_MIN = 1;
/** Maximum edge stroke width. */
const EDGE_WIDTH_MAX = 8;
/** Minimum edge opacity (faintest link). */
const EDGE_OPACITY_MIN = 0.25;
/** Maximum edge opacity. */
const EDGE_OPACITY_MAX = 1.0;

// ── Types ──────────────────────────────────────────────────────────────────────

/** Data payload on each cluster node. */
export interface ClusterNodeData extends Record<string, unknown> {
  clusterId: number;
  label: string | null;
  size: number;
  representative: string | null;
  color: string | null;
  /** Derived dimension — used by the renderer and tests for monotonicity checks. */
  nodeSize: number;
}

/** Data payload on each cluster edge. */
export interface ClusterEdgeData extends Record<string, unknown> {
  weight: number;
  /** Derived strokeWidth — for tests and style. */
  strokeWidth: number;
  /** Derived opacity — for tests and style. */
  opacity: number;
}

/** Result type returned by clusterGraphLayout. */
export interface ClusterGraphResult {
  nodes: Node<ClusterNodeData>[];
  edges: Edge<ClusterEdgeData>[];
}

/** Optional layout options (currently reserved for future tuning). */
export interface ClusterGraphOpts {
  /** Override the layout circle radius (px). Default: BASE_RADIUS. */
  radius?: number;
}

// ── Pure helpers ───────────────────────────────────────────────────────────────

/**
 * Map a cluster size (symbol count) to a node dimension in pixels.
 * Uses a square-root scale so large clusters are visible but not overwhelming.
 *
 * @param size  Symbol count for the cluster
 * @param maxSize  Maximum size in the current cluster set (for normalisation)
 */
function computeNodeSize(size: number, maxSize: number): number {
  if (maxSize <= 0) return NODE_SIZE_MIN;
  // sqrt scale: medium clusters are not dwarfed by large ones
  const ratio = Math.sqrt(size) / Math.sqrt(maxSize);
  return Math.round(NODE_SIZE_MIN + ratio * (NODE_SIZE_MAX - NODE_SIZE_MIN));
}

/**
 * Map a link weight to an edge stroke width in pixels.
 *
 * @param weight     The link's cross-cluster edge count
 * @param maxWeight  Maximum weight in the current link set (for normalisation)
 */
function computeStrokeWidth(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return EDGE_WIDTH_MIN;
  const ratio = Math.log1p(weight) / Math.log1p(maxWeight);
  return parseFloat((EDGE_WIDTH_MIN + ratio * (EDGE_WIDTH_MAX - EDGE_WIDTH_MIN)).toFixed(2));
}

/**
 * Map a link weight to an opacity value.
 *
 * @param weight     The link's cross-cluster edge count
 * @param maxWeight  Maximum weight in the current link set (for normalisation)
 */
function computeOpacity(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return EDGE_OPACITY_MIN;
  const ratio = Math.log1p(weight) / Math.log1p(maxWeight);
  return parseFloat((EDGE_OPACITY_MIN + ratio * (EDGE_OPACITY_MAX - EDGE_OPACITY_MIN)).toFixed(3));
}

// ── clusterGraphLayout ─────────────────────────────────────────────────────────

/**
 * Transform constellation clusters + links into a React Flow node/edge graph.
 *
 * Layout algorithm:
 *   1. Sort clusters by size DESC, cluster_id ASC (deterministic tiebreak).
 *   2. Place clusters evenly on a circle of radius `opts.radius` (default BASE_RADIUS).
 *      The first (largest) cluster starts at angle −π/2 (top of the circle).
 *   3. Node size ∝ sqrt(size) (monotonic).
 *   4. Edge strokeWidth and opacity ∝ log1p(weight) (monotonic).
 *   5. Color = clusterColor(cluster_id).
 *
 * @param clusters  Cluster records from /api/constellation
 * @param links     Weighted inter-cluster links from /api/constellation
 * @param opts      Optional layout overrides
 * @returns         { nodes, edges } for @xyflow/react
 */
export function clusterGraphLayout(
  clusters: ConstellationCluster[],
  links: ConstellationLink[],
  opts: ClusterGraphOpts = {},
): ClusterGraphResult {
  // Empty-input fast path.
  if (clusters.length === 0) {
    return { nodes: [], edges: [] };
  }

  const radius = opts.radius ?? Math.max(BASE_RADIUS, clusters.length * 15);

  // Sort: largest first, cluster_id as stable tiebreak.
  const sorted = [...clusters].sort(
    (a, b) => b.size - a.size || a.cluster_id - b.cluster_id,
  );

  const maxSize = sorted[0].size; // safe: sorted non-empty

  // Pre-compute max weight for normalisation.
  const maxWeight = links.reduce((m, l) => Math.max(m, l.weight), 0);

  // Radial positions: evenly distributed, starting at top (−π/2).
  const n = sorted.length;
  const angleStep = (2 * Math.PI) / n;
  const startAngle = -Math.PI / 2;

  // Build a cluster_id → position map for deterministic lookup.
  const posMap = new Map<number, { x: number; y: number }>();
  const sizeMap = new Map<number, number>();

  const nodes: Node<ClusterNodeData>[] = sorted.map((cluster, i) => {
    const angle = startAngle + i * angleStep;
    const nodeSize = computeNodeSize(cluster.size, maxSize);
    // Center the node: React Flow positions are top-left corner.
    const cx = Math.round(radius * Math.cos(angle));
    const cy = Math.round(radius * Math.sin(angle));
    const x = cx - Math.round(nodeSize / 2);
    const y = cy - Math.round(nodeSize / 2);

    posMap.set(cluster.cluster_id, { x: cx, y: cy });
    sizeMap.set(cluster.cluster_id, nodeSize);

    return {
      id: `cluster-${cluster.cluster_id}`,
      // Use React Flow's built-in "default" node type. ClusterGraph2D does NOT
      // register a `nodeTypes` map, so any custom type name (e.g. "clusterNode")
      // would trigger RF error 002 and fall back to default anyway. Appearance is
      // injected via the `style` prop below, keeping us off RF's custom-node API.
      type: "default",
      position: { x, y },
      data: {
        clusterId: cluster.cluster_id,
        // The React Flow default node renders `data.label` directly, so it must
        // never be null — an unlabeled cluster would show as a blank circle,
        // defeating the whole point of a legible map. Fall back to the hub
        // symbol, then a stable "cluster-<id>" so every node is identifiable.
        label: cluster.label ?? cluster.representative ?? `cluster-${cluster.cluster_id}`,
        size: cluster.size,
        representative: cluster.representative,
        color: clusterColor(cluster.cluster_id),
        nodeSize,
      },
      style: {
        width: nodeSize,
        height: nodeSize,
      },
    };
  });

  // Build edges — skip links referencing unknown cluster ids.
  const knownIds = new Set(clusters.map((c) => c.cluster_id));
  const edges: Edge<ClusterEdgeData>[] = links
    .filter((link) => knownIds.has(link.source) && knownIds.has(link.target))
    .map((link) => {
      const strokeWidth = computeStrokeWidth(link.weight, maxWeight);
      const opacity = computeOpacity(link.weight, maxWeight);
      return {
        id: `link-${link.source}-${link.target}`,
        source: `cluster-${link.source}`,
        target: `cluster-${link.target}`,
        style: {
          strokeWidth,
          stroke: "#a1a1aa",
          opacity,
        },
        data: {
          weight: link.weight,
          strokeWidth,
          opacity,
        },
      };
    });

  return { nodes, edges };
}
