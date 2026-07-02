/**
 * EdgeLines — renders all graph edges as additive-blended LineSegments.
 *
 * The pure helper `buildEdgeGeometry` is exported for unit testing
 * (no WebGL dependency). The React component wraps it with useMemo.
 *
 * Intensity rules (reference §2 "Edge Lines"):
 *   Both highlighted:               0.50
 *   One highlighted:                0.04
 *   Same cluster, no highlight:     0.25
 *   Cross-cluster, no highlight:    0.06
 *   Neither highlighted when set:   0 (edge skipped — not emitted)
 *
 * Cluster key = first 2 slash components of file_path.
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §2
 */

import { useEffect, useMemo } from "react";
import * as THREE from "three";

import { EDGE_TYPE_COLORS, DEFAULT_EDGE_COLOR } from "../lib/constellationColors";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── Pure helper (exported for unit testing) ───────────────────────────────────

/**
 * Build Float32Array position and color buffers for a LineSegments geometry.
 *
 * `nodeMap`       — pre-built id→LayoutNode lookup (avoids O(n) per edge)
 * `edges`         — all edges from LayoutData
 * `highlightedIds`— set of currently-highlighted node ids
 *
 * Returns sliced arrays that contain only the emitted (non-dimmed) edges.
 * Each edge emits 2 vertices × 3 floats in both `positions` and `colors`.
 */
export function buildEdgeGeometry(
  nodeMap: Map<number, LayoutNode>,
  edges: LayoutEdge[],
  highlightedIds: Set<number>,
): { positions: Float32Array; colors: Float32Array } {
  const hasHighlight = highlightedIds.size > 0;

  // Cluster key: first 2 path components so nodes in the same directory share a cluster.
  const clusterKey = (n: LayoutNode): string =>
    (n.file_path ?? "").split("/").slice(0, 2).join("/");

  // Filter edges to those whose both endpoints exist in the node map.
  const validEdges = edges.filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target));

  // Allocate max-size buffers; we slice to actual length after writing.
  const positions = new Float32Array(validEdges.length * 6);
  const colors = new Float32Array(validEdges.length * 6);

  let writeIdx = 0;

  for (const e of validEdges) {
    const s = nodeMap.get(e.source)!;
    const t = nodeMap.get(e.target)!;
    const sH = highlightedIds.has(e.source);
    const tH = highlightedIds.has(e.target);

    // Intensity determines how bright this edge appears.
    let intensity: number;
    if (hasHighlight) {
      if (sH && tH) {
        intensity = 0.5; // both endpoints highlighted — full glow
      } else if (sH || tH) {
        intensity = 0.04; // one endpoint highlighted — faint trace
      } else {
        intensity = 0; // neither highlighted — skip (dimmed)
      }
    } else {
      // No highlight set: distinguish same-cluster vs cross-cluster.
      intensity = clusterKey(s) === clusterKey(t) ? 0.25 : 0.06;
    }

    if (intensity === 0) continue; // do not write this edge

    // Parse the edge-kind hex color into [r,g,b] ∈ [0,1].
    const hex = EDGE_TYPE_COLORS[e.type] ?? DEFAULT_EDGE_COLOR;
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    const [cr, cg, cb] = m
      ? [
          parseInt(m[1], 16) / 255,
          parseInt(m[2], 16) / 255,
          parseInt(m[3], 16) / 255,
        ]
      : [0.11, 0.63, 0.49]; // fallback: seam teal

    const r = cr * intensity;
    const g = cg * intensity;
    const b = cb * intensity;

    const base = writeIdx * 6;

    // Source vertex
    positions[base] = s.x;
    positions[base + 1] = s.y;
    positions[base + 2] = s.z;
    colors[base] = r;
    colors[base + 1] = g;
    colors[base + 2] = b;

    // Target vertex
    positions[base + 3] = t.x;
    positions[base + 4] = t.y;
    positions[base + 5] = t.z;
    colors[base + 3] = r;
    colors[base + 4] = g;
    colors[base + 5] = b;

    writeIdx++;
  }

  // Slice to the actually-written portion.
  return {
    positions: positions.slice(0, writeIdx * 6),
    colors: colors.slice(0, writeIdx * 6),
  };
}

// ── React component ───────────────────────────────────────────────────────────

interface EdgeLinesProps {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  highlightedIds: Set<number>;
}

/**
 * Renders all graph edges as additive-blended LineSegments.
 *
 * Uses THREE.AdditiveBlending + depthWrite=false so bright edges accumulate
 * light on the dark background (each edge adds its color, never subtracts)
 * without writing into the depth buffer (so nodes always render on top).
 * toneMapped=false tells Three.js to skip its built-in tone-mapping step for
 * this material, so the (very low) intensity values reach the linear render
 * target intact; the Bloom post-processing pass fires on luminance values
 * above 0.3 — the most intense "both-highlighted" edge at 0.5 × base-color
 * reaches those levels on the teal/amber palette, producing the Bloom corona.
 * At normal intensities (0.04–0.25) the same pipeline simply draws dim lines
 * without Bloom, keeping the background field legible.
 *
 * The geometry is rebuilt whenever `highlightedIds` changes (memoised).
 * Position updates due to camera movement are handled by Three.js — the
 * positions themselves are world-space coordinates baked into the geometry.
 */
export function EdgeLines({ nodes, edges, highlightedIds }: EdgeLinesProps) {
  // Build id→node lookup once per node-list change.
  const nodeMap = useMemo(
    () => new Map(nodes.map((n) => [n.id, n])),
    [nodes],
  );

  // Recompute geometry whenever highlight state changes.
  const { positions, colors } = useMemo(
    () => buildEdgeGeometry(nodeMap, edges, highlightedIds),
    [nodeMap, edges, highlightedIds],
  );

  // Rebuild the BufferGeometry whenever the typed arrays change.
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return g;
  }, [positions, colors]);

  // Dispose the GPU buffers when the geometry is replaced (highlight change) or on
  // unmount. Without this, every rebuild leaks a BufferGeometry's VBOs.
  useEffect(() => () => geometry.dispose(), [geometry]);

  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial
        vertexColors
        transparent
        blending={THREE.AdditiveBlending}
        depthWrite={false}
        toneMapped={false}
      />
    </lineSegments>
  );
}
