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
 *   EdgeLines      — additive-blended LineSegments (S3)
 *   NodeLabels     — canvas-sprite labels for top-80 nodes (S3)
 *   NodeTooltip    — drei Html glass-card on hover (S3)
 *
 * Pure helpers exported for unit testing (no WebGL dependency):
 *   computeCameraTarget(nodes, ids) → CameraTarget | null
 *   easeOutCubic(p)                → number
 */

import { useRef, useCallback, useEffect } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";

import { NodeCloud } from "./NodeCloud";
import { EdgeLines } from "./EdgeLines";
import { NodeLabels } from "./NodeLabels";
import { NodeTooltip } from "./NodeTooltip";
import { ClusterHalos } from "./ClusterHalos";
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

  // Register pointer + wheel events on the canvas, cleaning up on unmount so the
  // listeners don't leak (and to avoid a side-effect in the render body).
  const { gl } = useThree();
  useEffect(() => {
    const el = gl.domElement;
    el.addEventListener("pointerdown", handleInteraction, { passive: true });
    el.addEventListener("wheel", handleInteraction, { passive: true });
    return () => {
      el.removeEventListener("pointerdown", handleInteraction);
      el.removeEventListener("wheel", handleInteraction);
    };
  }, [gl, handleInteraction]);

  useFrame(() => {
    if (!controlsRef.current) return;
    const idle = Date.now() - lastInteraction.current > IDLE_TIMEOUT_MS;
    controlsRef.current.autoRotate = idle;
  });

  return null;
}

// ── ConstellationScene ────────────────────────────────────────────────────────

interface ConstellationSceneProps {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  clusters: ClusterSummary[];
  highlightedIds: Set<number>;
  cameraTarget: CameraTarget | null;
  /** Currently hovered node (passed from ConstellationTab) for the tooltip. */
  hoveredNode?: LayoutNode | null;
  onHover: (node: LayoutNode | null) => void;
  onSelect: (node: LayoutNode) => void;
}

/**
 * Root R3F canvas for the constellation Explorer tab.
 *
 * Composes:
 *   NodeCloud     — instanced mesh for all nodes
 *   EdgeLines     — additive-blended line segments (S3)
 *   NodeLabels    — canvas-sprite labels for top-80 nodes (S3)
 *   NodeTooltip   — drei Html glass-card on hover (S3)
 *   OrbitControls — mouse/touch orbit with damping + idle auto-rotate
 *   EffectComposer + Bloom — post-processing glow corona
 *   CameraAnimator — smooth fly-to on node select
 */
export function ConstellationScene({
  nodes,
  edges,
  clusters,
  highlightedIds,
  cameraTarget,
  hoveredNode,
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

      {/* Cluster halos — faint translucent spheres marking functional areas (S6) */}
      <ClusterHalos clusters={clusters} />

      {/* Node cloud — all nodes in one draw call */}
      <NodeCloud
        nodes={nodes}
        highlightedIds={highlightedIds}
        onHover={onHover}
        onSelect={onSelect}
      />

      {/* Edges — additive-blended line segments (S3: from standalone EdgeLines.tsx) */}
      <EdgeLines nodes={nodes} edges={edges} highlightedIds={highlightedIds} />

      {/* Sprite labels for the top-80 nodes by size (S3) */}
      <NodeLabels nodes={nodes} highlightedIds={highlightedIds} />

      {/* Glass-card tooltip anchored to the hovered node (S3) */}
      {hoveredNode && <NodeTooltip node={hoveredNode} />}

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
