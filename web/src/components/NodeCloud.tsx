/**
 * NodeCloud — InstancedMesh rendering all visible nodes in a single GPU draw call.
 *
 * Rendering approach:
 * - One InstancedMesh with sphereGeometry + meshBasicMaterial (unlit, toneMapped=false)
 * - Per-instance position/scale matrix written in useFrame (dirty-flag optimised)
 * - Per-instance color Float32Array uploaded via instanceColor attribute
 * - Color boost > 1.0 for highlighted nodes so the Bloom post-processing pass
 *   picks them up as coronas (reference §2: "values exceed 1.0, Bloom fires")
 *
 * Pure helpers exported at module level for unit testing:
 *   computeInstanceColor(node, isHighlighted, isDimmed) → [r, g, b]
 */

import { useRef, useMemo, useCallback } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { LayoutNode } from "../lib/layoutTypes";

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
 * Compute the per-instance [r, g, b] for a node.
 *
 * Highlighted: brightness-boosted above 1.0 (triggers Bloom post-processing).
 *   boost = 1.2 + brightness × 0.8  (1.65 for red-dwarf; 2.0 for white/blue)
 * Dimmed:      multiplied by 0.15 (dark, recedes into background)
 * Normal:      raw color components unchanged
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §2
 */
export function computeInstanceColor(
  node: LayoutNode,
  isHighlighted: boolean,
  isDimmed: boolean,
): [number, number, number] {
  const [r, g, b] = parseHex(node.color);
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
      const scaleFactor = isH ? 0.5 : hasHighlight ? 0.2 : 1.0;
      const s = node.size * scaleFactor;
      _matrix.makeScale(s, s, s);
      _matrix.setPosition(node.x, node.y, node.z);
      mesh.setMatrixAt(i, _matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;

    // Upload colors via instanceColor buffer
    if (mesh.instanceColor) {
      mesh.instanceColor.array.set(colorArray);
      mesh.instanceColor.needsUpdate = true;
    }
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
      <meshBasicMaterial vertexColors toneMapped={false} />
    </instancedMesh>
  );
}
