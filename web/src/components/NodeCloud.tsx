/**
 * NodeCloud — InstancedMesh rendering all visible nodes in a single GPU draw call.
 *
 * Rendering approach (S2 #261 — additive glows, color by kind):
 * - One InstancedMesh with sphereGeometry + meshBasicMaterial
 * - Material uses THREE.AdditiveBlending + depthWrite=false + transparent so
 *   nodes GLOW (colors ADD) instead of occluding (no black holes in the field).
 *   This is the "additive emissive InstancedMesh" documented fallback — chosen
 *   over Points because InstancedMesh raycasting already works perfectly for
 *   hover/click, while Points raycasting requires fiddly threshold tuning.
 * - Node HUE comes from KIND_COLORS[node.label] (single source of truth shared
 *   with the filter legend). node.color (stellar/degree scale) is NO LONGER used.
 * - Degree drives SIZE (via node.size from the layout engine) and BRIGHTNESS
 *   (the highlight/dim boost applied to the kind color). Hue is kind-only.
 * - Color boost > 1.0 for highlighted nodes so the Bloom post-processing pass
 *   picks them up as coronas (reference §2: "values exceed 1.0, Bloom fires").
 *
 * Pure helpers exported at module level for unit testing:
 *   kindColor(label)                               → hex color string
 *   computeInstanceColor(node, isH, isD)           → [r, g, b]
 */

import { useRef, useMemo, useCallback } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { LayoutNode } from "../lib/layoutTypes";
import { KIND_COLORS, DEFAULT_KIND_COLOR } from "../lib/constellationColors";

// ── Pure helpers (unit-testable without WebGL) ────────────────────────────────

/**
 * Parse a 6-char hex color string (e.g. "#1DA27E") into [r, g, b] ∈ [0, 1].
 * Returns [0, 0, 0] on malformed input (safe degradation).
 */
function parseHex(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return [0, 0, 0];
  return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
}

/**
 * Return the kind color for a symbol label — the single source of truth shared
 * with the filter legend chips. Unknown kinds fall back to DEFAULT_KIND_COLOR.
 *
 * This is the ONLY place where symbol kind → color is resolved, ensuring the
 * node colors in the 3D view always match the legend dots.
 */
export function kindColor(label: string): string {
  return KIND_COLORS[label] ?? DEFAULT_KIND_COLOR;
}

/**
 * Compute the per-instance [r, g, b] for a node.
 *
 * Color source: KIND_COLORS[node.label] (hue = symbol kind, same as the legend).
 * node.color is intentionally ignored — it was the stellar/degree scale and is
 * no longer the color authority after S2 (#261).
 *
 * Highlighted: brightness-boosted above 1.0 (triggers Bloom post-processing).
 *   boost = 1.2 + brightness × 0.8  (1.65 for dark colors; up to 2.0 for white)
 * Dimmed:      multiplied by 0.15 (dark, recedes into background)
 * Normal:      raw kind-color components unchanged
 */
export function computeInstanceColor(
  node: LayoutNode,
  isHighlighted: boolean,
  isDimmed: boolean,
): [number, number, number] {
  // Hue from kind, not from node.color (degree/stellar scale).
  const [r, g, b] = parseHex(kindColor(node.label));

  if (isDimmed) {
    return [r * 0.15, g * 0.15, b * 0.15];
  }
  if (isHighlighted) {
    const brightness = (r + g + b) / 3;
    const boost = 1.2 + brightness * 0.8;
    return [r * boost, g * boost, b * boost];
  }
  return [r, g, b];
}

// ── NodeCloud component ───────────────────────────────────────────────────────

interface NodeCloudProps {
  nodes: LayoutNode[];
  highlightedIds: Set<number>;
  onHover: (node: LayoutNode | null) => void;
  onSelect: (node: LayoutNode) => void;
}

const _matrix = new THREE.Matrix4();

/**
 * All nodes rendered as one InstancedMesh.
 * Matrix and color arrays are rewritten on every relevant state change.
 *
 * AdditiveBlending + depthWrite=false: each node's glow ADDS color to whatever
 * is behind it (edges, other nodes, halos) — the sphere never punches a black
 * hole into the depth buffer. On the dark CANVAS_BG this creates the "star field"
 * look where bright hubs appear as glowing coronas under Bloom.
 */
export function NodeCloud({ nodes, highlightedIds, onHover, onSelect }: NodeCloudProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null!);
  const hasHighlight = highlightedIds.size > 0;

  // Pre-compute per-instance colors on highlight change (not every frame)
  const colorArray = useMemo(() => {
    const arr = new Float32Array(nodes.length * 3);
    nodes.forEach((node, i) => {
      const isH = highlightedIds.has(node.id);
      const isD = hasHighlight && !isH;
      const [r, g, b] = computeInstanceColor(node, isH, isD);
      arr[i * 3] = r;
      arr[i * 3 + 1] = g;
      arr[i * 3 + 2] = b;
    });
    return arr;
  }, [nodes, highlightedIds, hasHighlight]);

  // Upload matrices + colors every frame (only if mesh is mounted)
  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh) return;

    nodes.forEach((node, i) => {
      const isH = highlightedIds.has(node.id);
      // Highlighted nodes render at 0.5× (half of the no-selection 1.0 baseline) while
      // dimmed nodes shrink further to 0.2×.  The highlighted node still appears
      // visually prominent because the camera flies to it; the 0.5× vs 0.2× ratio
      // (2.5× larger than dimmed) provides the focal contrast without over-sizing the mesh.
      const scaleFactor = isH ? 0.5 : hasHighlight ? 0.2 : 1.0;
      const s = node.size * scaleFactor;
      _matrix.makeScale(s, s, s);
      _matrix.setPosition(node.x, node.y, node.z);
      mesh.setMatrixAt(i, _matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;

    // Upload colors via the instanceColor buffer. THREE.InstancedMesh starts with
    // instanceColor === null and only allocates it when setColorAt() is called — we
    // never call that, so without this lazy init the buffer stays null and every
    // instance renders with the material's default white (kind colors lost).
    // Re-allocate when the node count changes (a new InstancedMesh is mounted).
    if (!mesh.instanceColor || mesh.instanceColor.count !== nodes.length) {
      mesh.instanceColor = new THREE.InstancedBufferAttribute(
        new Float32Array(nodes.length * 3),
        3,
      );
    }
    mesh.instanceColor.array.set(colorArray);
    mesh.instanceColor.needsUpdate = true;
  });

  const handlePointerOver = useCallback(
    (e: THREE.Event) => {
      const evt = e as unknown as { instanceId?: number; stopPropagation: () => void };
      evt.stopPropagation();
      if (evt.instanceId !== undefined && nodes[evt.instanceId]) {
        onHover(nodes[evt.instanceId]);
      }
    },
    [nodes, onHover],
  );

  const handlePointerOut = useCallback(() => {
    onHover(null);
  }, [onHover]);

  const handleClick = useCallback(
    (e: THREE.Event) => {
      const evt = e as unknown as { instanceId?: number; stopPropagation: () => void };
      evt.stopPropagation();
      if (evt.instanceId !== undefined && nodes[evt.instanceId]) {
        onSelect(nodes[evt.instanceId]);
      }
    },
    [nodes, onSelect],
  );

  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, nodes.length]}
      frustumCulled={false}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
      onClick={handleClick}
    >
      <sphereGeometry args={[1, 32, 24]} />
      {/*
        AdditiveBlending + depthWrite=false + transparent:
        - Colors accumulate (ADD) on the dark background → no black holes.
        - depthWrite=false: the sphere sphere leaves the depth buffer clean so
          other primitives (edges, labels) always render on top correctly.
        - transparent=true: required by Three.js to actually apply blending.
        - toneMapped=false: HDR values > 1.0 on highlighted nodes reach the
          Bloom pass without being clamped by the tone-mapper.
      */}
      <meshBasicMaterial
        vertexColors
        toneMapped={false}
        transparent
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </instancedMesh>
  );
}
