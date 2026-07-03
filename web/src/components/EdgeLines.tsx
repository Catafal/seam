/**
 * EdgeLines — renders all graph edges as additive-blended LineSegments.
 *
 * The pure helper `buildEdgeGeometry` is exported for unit testing
 * (no WebGL dependency). The React component wraps it with useMemo.
 *
 * Intensity rules (#262 — calmer edges, controlled bloom):
 *   Both highlighted:                  0.50  (unchanged — full glow)
 *   One highlighted:                   0.04  (unchanged — faint trace)
 *   Same cluster, no highlight:        0.10  (was 0.25 — calmer ambient field)
 *   Cross-cluster, no highlight:       0.02  (was 0.06 — even dimmer cross links)
 *   Loud-kind multiplier (no-hl only): 0.50  (instantiates/uses/writes are warm+saturated
 *                                             and spike visually at equal intensity; halved
 *                                             in the ambient field; full color on highlight)
 *   Neither highlighted when set:      0     (edge skipped — not emitted)
 *
 * Cluster key = first 2 slash components of file_path.
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §2
 * Issue: #262 — "Calmer edges + controlled bloom"
 */

import { useEffect, useMemo } from "react";
import * as THREE from "three";

import { EDGE_TYPE_COLORS, DEFAULT_EDGE_COLOR } from "../lib/constellationColors";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── Pure helper (exported for unit testing) ───────────────────────────────────

// ── Intensity constants (#262) ────────────────────────────────────────────────

/** Both endpoints highlighted — full glow (unchanged from before #262). */
const INTENSITY_HL_BOTH = 0.5;
/** One endpoint highlighted — faint trace (unchanged from before #262). */
const INTENSITY_HL_ONE = 0.04;
/** Same-cluster, no highlight. Lowered from 0.25 → calmer ambient field. */
const INTENSITY_SAME_CLUSTER = 0.10;
/** Cross-cluster, no highlight. Lowered from 0.06 → softer long-range links. */
const INTENSITY_CROSS_CLUSTER = 0.02;

/**
 * Multiplier applied to "loud" edge kinds in the NO-HIGHLIGHT path only.
 *
 * `instantiates` (orange), `uses` (amber), `writes` (red) have high-saturation
 * warm hues with large R-channel values (0.918–0.976).  At equal intensity their
 * max channel exceeds that of `call` (teal, max G=0.635), so they dominate the
 * ambient field visually even though they are less frequent.  The 0.5× multiplier
 * brings their max channel below `call`'s max channel at the same base intensity.
 * Full color is restored when either endpoint is highlighted.
 */
const LOUD_KIND_DIM = 0.5;

/** Edge kinds that receive LOUD_KIND_DIM in the no-highlight ambient path. */
const LOUD_KINDS = new Set(["instantiates", "uses", "writes"]);

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
        // Both endpoints highlighted — full glow; no loud-kind dim so warm edges
        // return to their natural saturation in the highlighted selection.
        intensity = INTENSITY_HL_BOTH;
      } else if (sH || tH) {
        intensity = INTENSITY_HL_ONE; // one endpoint highlighted — faint trace
      } else {
        intensity = 0; // neither highlighted — skip (dimmed)
      }
    } else {
      // No highlight set: distinguish same-cluster vs cross-cluster, then
      // further dim loud/warm edge kinds so they don't spike in the ambient field.
      const base = clusterKey(s) === clusterKey(t)
        ? INTENSITY_SAME_CLUSTER
        : INTENSITY_CROSS_CLUSTER;
      const kindMult = LOUD_KINDS.has(e.type) ? LOUD_KIND_DIM : 1.0;
      intensity = base * kindMult;
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
 * target intact.
 *
 * #262 bloom contract: with luminanceThreshold raised to 0.6, ambient edges
 * (max channel 0.01–0.0635) and even the highlighted-endpoint faint traces
 * (max ~0.04×0.635=0.025) sit well below the threshold.  The both-highlighted
 * path at intensity 0.5 reaches a max channel of ~0.488 (instantiates R) —
 * still below 0.6 — so edges do NOT bloom; only genuine node cores (boosted
 * well above 1.0 by NodeCloud) bloom.  This eliminates the dense-region
 * white-out while keeping bright node coronas.
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
