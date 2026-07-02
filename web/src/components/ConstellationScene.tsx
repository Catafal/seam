/**
 * ConstellationScene — the R3F canvas root for the 3D constellation Explorer.
 *
 * Canvas settings (reference §2):
 *   dpr [1, 1.5]  — CRITICAL: avoids MSAA compositor failure on Apple Silicon
 *   antialias: false — dark bg + additive blending masks aliasing
 *   bg: CANVAS_BG (#04100f, teal-void)
 *
 * Features:
 *   OrbitControls  — dampingFactor 0.08, auto-rotate after 60s idle
 *   EffectComposer — Bloom (threshold .3, intensity 1.2, radius .6, mipmapBlur)
 *   CameraAnimator — ease-out cubic fly-to with 0.08 lerp factor
 *
 * Pure helpers exported for unit testing (no WebGL dependency):
 *   computeCameraTarget(nodes, ids) → CameraTarget | null
 *   easeOutCubic(p)                → number
 */

import { useRef, useCallback } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";

import { NodeCloud } from "./NodeCloud";
import { CANVAS_BG } from "../lib/constellationColors";
import type { LayoutNode, LayoutEdge, ClusterSummary } from "../lib/layoutTypes";

// ── Constants ─────────────────────────────────────────────────────────────────

/** Idle timeout in ms before auto-rotation kicks in (reference §2). */
const IDLE_TIMEOUT_MS = 60_000;

// ── Pure helpers (unit-testable without WebGL) ────────────────────────────────

/** Camera position + look-at pair produced by computeCameraTarget. */
export type CameraTarget = {
  position: [number, number, number];
  lookAt: [number, number, number];
};

/**
 * Compute the fly-to camera target for a set of highlighted node ids.
 *
 * Centroid = average position of highlighted nodes.
 * Spread   = max distance from centroid among highlighted nodes (min 60).
 * Camera   = centroid offset by [spread×0.2, spread×0.15, spread×3]
 *
 * Returns null if the node list or id set is empty (no fly needed).
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §2 §camera
 */
export function computeCameraTarget(
  nodes: LayoutNode[],
  ids: Set<number>,
): CameraTarget | null {
  const pts = nodes.filter((n) => ids.has(n.id));
  if (pts.length === 0) return null;

  const cx = pts.reduce((s, n) => s + n.x, 0) / pts.length;
  const cy = pts.reduce((s, n) => s + n.y, 0) / pts.length;
  const cz = pts.reduce((s, n) => s + n.z, 0) / pts.length;

  const spread = Math.max(
    60,
    ...pts.map((n) => Math.hypot(n.x - cx, n.y - cy, n.z - cz)),
  );

  return {
    position: [cx + spread * 0.2, cy + spread * 0.15, cz + spread * 3],
    lookAt: [cx, cy, cz],
  };
}

/**
 * Ease-out cubic: 1 - (1-p)³
 *
 * Decelerates towards the end of the animation — fast start, smooth stop.
 * Used by CameraAnimator for the fly-to interpolation.
 */
export function easeOutCubic(p: number): number {
  return 1 - Math.pow(1 - p, 3);
}

// ── Internal R3F components (not unit-tested — require WebGL) ─────────────────

interface CameraAnimatorProps {
  target: CameraTarget | null;
}

/**
 * Animates the camera to a target position using ease-out cubic + lerp.
 * Progress increments by 0.02/frame (~50 frames at 60fps).
 * The inner lerp factor (0.08) ensures smooth asymptotic arrival.
 *
 * Reference: §2 "Camera fly-to" code block.
 */
function CameraAnimator({ target }: CameraAnimatorProps) {
  const { camera } = useThree();
  const progress = useRef(0);
  const prevTarget = useRef<CameraTarget | null>(null);

  // Reset progress whenever the target changes
  if (target !== prevTarget.current) {
    progress.current = 0;
    prevTarget.current = target;
  }

  const lookAtVec = useRef(new THREE.Vector3());
  const posVec = useRef(new THREE.Vector3());

  useFrame(() => {
    if (!target || progress.current >= 1.0) return;
    progress.current = Math.min(progress.current + 0.02, 1.0);
    const t = easeOutCubic(progress.current) * 0.08;
    posVec.current.set(...target.position);
    lookAtVec.current.set(...target.lookAt);
    camera.position.lerp(posVec.current, t);
    camera.lookAt(lookAtVec.current);
  });

  return null;
}

interface AutoRotateControllerProps {
  controlsRef: React.RefObject<{ autoRotate: boolean } | null>;
}

/**
 * Tracks the last user interaction and enables auto-rotation after IDLE_TIMEOUT_MS.
 * Implemented as a useFrame check (not setInterval) per the reference.
 */
function AutoRotateController({ controlsRef }: AutoRotateControllerProps) {
  const lastInteraction = useRef(Date.now());

  const handleInteraction = useCallback(() => {
    lastInteraction.current = Date.now();
    if (controlsRef.current) controlsRef.current.autoRotate = false;
  }, [controlsRef]);

  // Register pointer + wheel events on the canvas
  const { gl } = useThree();
  const registered = useRef(false);
  if (!registered.current) {
    registered.current = true;
    gl.domElement.addEventListener("pointerdown", handleInteraction, { passive: true });
    gl.domElement.addEventListener("wheel", handleInteraction, { passive: true });
  }

  useFrame(() => {
    if (!controlsRef.current) return;
    const idle = Date.now() - lastInteraction.current > IDLE_TIMEOUT_MS;
    controlsRef.current.autoRotate = idle;
  });

  return null;
}

// ── EdgeLines ─────────────────────────────────────────────────────────────────

import { EDGE_TYPE_COLORS, DEFAULT_EDGE_COLOR } from "../lib/constellationColors";
import { useMemo } from "react";

/**
 * Compute Float32Array positions and colors for all visible edges.
 *
 * Intensity rules (reference §2 "Edge Lines" intensity table):
 *   Both highlighted:               0.50
 *   One highlighted:                0.04
 *   Same cluster, no highlight:     0.25
 *   Cross-cluster, no highlight:    0.06
 *   Both dimmed (no highlight set): skipped (zero intensity)
 *
 * Cluster key = first 2 slash components of file_path.
 */
export function buildEdgeGeometry(
  nodeMap: Map<number, LayoutNode>,
  edges: LayoutEdge[],
  highlightedIds: Set<number>,
): { positions: Float32Array; colors: Float32Array } {
  const hasHighlight = highlightedIds.size > 0;
  const clusterKey = (n: LayoutNode) =>
    (n.file_path ?? "").split("/").slice(0, 2).join("/");

  const filteredEdges = edges.filter((e) => {
    const s = nodeMap.get(e.source);
    const t = nodeMap.get(e.target);
    return s && t;
  });

  const positions = new Float32Array(filteredEdges.length * 6);
  const colors = new Float32Array(filteredEdges.length * 6);

  let idx = 0;
  for (const e of filteredEdges) {
    const s = nodeMap.get(e.source)!;
    const t = nodeMap.get(e.target)!;
    const sH = highlightedIds.has(e.source);
    const tH = highlightedIds.has(e.target);

    let intensity: number;
    if (hasHighlight) {
      if (sH && tH) intensity = 0.5;
      else if (sH || tH) intensity = 0.04;
      else intensity = 0; // dimmed — skip
    } else {
      const sameCluster = clusterKey(s) === clusterKey(t);
      intensity = sameCluster ? 0.25 : 0.06;
    }

    // Parse edge kind color
    const hex = EDGE_TYPE_COLORS[e.type] ?? DEFAULT_EDGE_COLOR;
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    const [cr, cg, cb] = m
      ? [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255]
      : [0.11, 0.63, 0.49];

    const r = cr * intensity;
    const g = cg * intensity;
    const b = cb * intensity;

    positions[idx * 6] = s.x;
    positions[idx * 6 + 1] = s.y;
    positions[idx * 6 + 2] = s.z;
    positions[idx * 6 + 3] = t.x;
    positions[idx * 6 + 4] = t.y;
    positions[idx * 6 + 5] = t.z;
    colors[idx * 6] = r; colors[idx * 6 + 1] = g; colors[idx * 6 + 2] = b;
    colors[idx * 6 + 3] = r; colors[idx * 6 + 4] = g; colors[idx * 6 + 5] = b;
    idx++;
  }

  // Slice to the actually-written portion (some edges may be skipped when dimmed)
  return {
    positions: positions.slice(0, idx * 6),
    colors: colors.slice(0, idx * 6),
  };
}

interface EdgeLinesProps {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  highlightedIds: Set<number>;
}

/** Renders all edges as additive-blended LineSegments. */
function EdgeLines({ nodes, edges, highlightedIds }: EdgeLinesProps) {
  const nodeMap = useMemo(
    () => new Map(nodes.map((n) => [n.id, n])),
    [nodes],
  );

  const { positions, colors } = useMemo(
    () => buildEdgeGeometry(nodeMap, edges, highlightedIds),
    [nodeMap, edges, highlightedIds],
  );

  const geo = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return g;
  }, [positions, colors]);

  return (
    <lineSegments geometry={geo}>
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

// ── ConstellationScene ────────────────────────────────────────────────────────

interface ConstellationSceneProps {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  clusters: ClusterSummary[];
  highlightedIds: Set<number>;
  cameraTarget: CameraTarget | null;
  onHover: (node: LayoutNode | null) => void;
  onSelect: (node: LayoutNode) => void;
}

/**
 * Root R3F canvas for the constellation Explorer tab.
 *
 * Composes:
 *   NodeCloud     — instanced mesh for all nodes
 *   EdgeLines     — additive-blended line segments
 *   OrbitControls — mouse/touch orbit with damping + idle auto-rotate
 *   EffectComposer + Bloom — post-processing glow corona
 *   CameraAnimator — smooth fly-to on node select
 */
export function ConstellationScene({
  nodes,
  edges,
  clusters: _clusters, // reserved for ClusterHalos (future slice)
  highlightedIds,
  cameraTarget,
  onHover,
  onSelect,
}: ConstellationSceneProps) {
  const controlsRef = useRef<{ autoRotate: boolean } | null>(null);

  return (
    <Canvas
      dpr={[1, 1.5]}
      gl={{ antialias: false, alpha: false, powerPreference: "high-performance" }}
      camera={{ position: [0, 0, 800], fov: 50, near: 0.1, far: 100000 }}
      style={{ background: CANVAS_BG, width: "100%", height: "100%" }}
    >
      {/* Lighting (cosmetic depth-cuing; nodes use meshBasicMaterial → unlit) */}
      <ambientLight intensity={0.5} />
      <pointLight position={[500, 500, 500]} intensity={0.6} />
      <pointLight position={[-300, -200, -300]} intensity={0.4} color="#6040ff" />

      {/* Node cloud — all nodes in one draw call */}
      <NodeCloud
        nodes={nodes}
        highlightedIds={highlightedIds}
        onHover={onHover}
        onSelect={onSelect}
      />

      {/* Edges — additive blended line segments */}
      <EdgeLines nodes={nodes} edges={edges} highlightedIds={highlightedIds} />

      {/* Orbit controls with damping + idle auto-rotate */}
      <OrbitControls
        ref={controlsRef as React.RefObject<Parameters<typeof OrbitControls>[0]["ref"] extends React.Ref<infer T> ? T : never>}
        enableDamping
        dampingFactor={0.08}
        rotateSpeed={0.5}
        zoomSpeed={1.5}
        minDistance={10}
        maxDistance={50000}
        autoRotateSpeed={0.4}
      />

      {/* Idle auto-rotate: fires after IDLE_TIMEOUT_MS of no interaction */}
      <AutoRotateController controlsRef={controlsRef as React.RefObject<{ autoRotate: boolean } | null>} />

      {/* Camera fly-to animator */}
      <CameraAnimator target={cameraTarget} />

      {/* Post-processing: Bloom glow corona (reference §2) */}
      <EffectComposer multisampling={0}>
        <Bloom
          luminanceThreshold={0.3}
          luminanceSmoothing={0.7}
          intensity={1.2}
          mipmapBlur
          radius={0.6}
        />
      </EffectComposer>
    </Canvas>
  );
}
