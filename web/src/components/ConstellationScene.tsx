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
 *   EffectComposer — Bloom (threshold .6, intensity .8, radius .65, mipmapBlur) [#262]
 *   CameraAnimator — ease-out cubic fly-to with 0.08 lerp factor
 *   EdgeLines      — additive-blended LineSegments (S3)
 *   NodeLabels     — canvas-sprite labels for top-80 nodes (S3)
 *   NodeTooltip    — drei Html glass-card on hover (S3)
 *
 * Interaction (# 263):
 *   onPointerMissed on Canvas → fires when a click hits empty space → calls onDeselect
 *   prefers-reduced-motion    → disables auto-rotate + snaps camera fly-to instantly
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
import { CANVAS_BG } from "../lib/constellationColors";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── Constants ─────────────────────────────────────────────────────────────────

/** Idle timeout in ms before auto-rotation kicks in (reference §2). */
const IDLE_TIMEOUT_MS = 60_000;

// ── Accessibility helper ──────────────────────────────────────────────────────

/**
 * Returns true if the user has opted into reduced motion via the OS preference
 * (prefers-reduced-motion: reduce).  Checked once per component mount — the
 * preference does not change mid-session in practice.
 *
 * Guards both auto-rotation (disable) and the camera fly-to (snap, no lerp).
 * Returns false in SSR / environments without matchMedia.
 */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

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
 * prefers-reduced-motion (#263): when the user has opted into reduced motion,
 * the camera snaps to the target position immediately (no lerp animation).
 * This keeps the isolate feature usable without triggering vestibular discomfort.
 *
 * Reference: §2 "Camera fly-to" code block.
 */
function CameraAnimator({ target }: CameraAnimatorProps) {
  const { camera } = useThree();
  const progress = useRef(0);
  const prevTarget = useRef<CameraTarget | null>(null);
  // Read once at mount — the preference does not change mid-session.
  const reducedMotion = useRef(prefersReducedMotion());

  // Reset progress whenever the target changes
  if (target !== prevTarget.current) {
    progress.current = 0;
    prevTarget.current = target;
  }

  const lookAtVec = useRef(new THREE.Vector3());
  const posVec = useRef(new THREE.Vector3());

  useFrame(() => {
    if (!target || progress.current >= 1.0) return;

    posVec.current.set(...target.position);
    lookAtVec.current.set(...target.lookAt);

    if (reducedMotion.current) {
      // Snap: jump directly to the target with no animation (one frame).
      camera.position.copy(posVec.current);
      camera.lookAt(lookAtVec.current);
      progress.current = 1.0; // mark done so we don't re-run
    } else {
      // Smooth ease-out cubic fly-to.
      progress.current = Math.min(progress.current + 0.02, 1.0);
      const t = easeOutCubic(progress.current) * 0.08;
      camera.position.lerp(posVec.current, t);
      camera.lookAt(lookAtVec.current);
    }
  });

  return null;
}

interface AutoRotateControllerProps {
  controlsRef: React.RefObject<{ autoRotate: boolean } | null>;
}

/**
 * Tracks the last user interaction and enables auto-rotation after IDLE_TIMEOUT_MS.
 * Implemented as a useFrame check (not setInterval) per the reference.
 *
 * prefers-reduced-motion (#263): when the user has opted into reduced motion,
 * auto-rotation is permanently disabled — the idle timer never fires.
 * This prevents continuous rotation that can cause vestibular discomfort.
 */
function AutoRotateController({ controlsRef }: AutoRotateControllerProps) {
  const lastInteraction = useRef(Date.now());
  // Read once at mount — the preference does not change mid-session.
  const reducedMotion = useRef(prefersReducedMotion());

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
    // Reduced motion: always keep auto-rotate off (never engage).
    if (reducedMotion.current) {
      controlsRef.current.autoRotate = false;
      return;
    }
    const idle = Date.now() - lastInteraction.current > IDLE_TIMEOUT_MS;
    controlsRef.current.autoRotate = idle;
  });

  return null;
}

// ── ConstellationScene ────────────────────────────────────────────────────────

interface ConstellationSceneProps {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  highlightedIds: Set<number>;
  cameraTarget: CameraTarget | null;
  /** Currently hovered node (passed from ConstellationTab) for the tooltip. */
  hoveredNode?: LayoutNode | null;
  onHover: (node: LayoutNode | null) => void;
  onSelect: (node: LayoutNode) => void;
  /**
   * Called when the user clicks empty canvas (no 3D object hit).
   * Used to deselect the current node and restore the full star field (#263).
   * Wired to Canvas.onPointerMissed which fires only on genuine miss-clicks,
   * not on orbit-drag releases.
   */
  onDeselect?: () => void;
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
 *
 * Note: ClusterHalos removed in Phase A (A5) — the 556 translucent spheres
 * composited into an opaque blob and ignored node/edge kind filters.
 */
export function ConstellationScene({
  nodes,
  edges,
  highlightedIds,
  cameraTarget,
  hoveredNode,
  onHover,
  onSelect,
  onDeselect,
}: ConstellationSceneProps) {
  const controlsRef = useRef<{ autoRotate: boolean } | null>(null);

  return (
    <Canvas
      dpr={[1, 1.5]}
      gl={{ antialias: false, alpha: false, powerPreference: "high-performance" }}
      camera={{ position: [0, 0, 800], fov: 50, near: 0.1, far: 100000 }}
      style={{ background: CANVAS_BG, width: "100%", height: "100%" }}
      onPointerMissed={onDeselect}
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

      {/* Post-processing: Bloom glow corona (#262 — bright cores only, no white-out).
          luminanceThreshold 0.3→0.6: only pixels above 0.6 linear luminance bloom.
            - Ambient edges peak at ~0.0635 (call same-cluster G) — never bloom.
            - Highlighted edges peak at ~0.488 (instantiates R × 0.5) — still below 0.6.
            - Node cores with highlight boost reach 1.4–2.0 on dominant channels → bloom.
          intensity 1.2→0.8: reduces spread on dense hub clusters (was clipping to white).
          luminanceSmoothing 0.7: kept — smooth transition around the threshold.
          radius 0.6→0.65: slightly wider corona to compensate for the lower intensity.
          mipmapBlur: kept — anti-flickering pass; no cost change. */}
      <EffectComposer multisampling={0}>
        <Bloom
          luminanceThreshold={0.6}
          luminanceSmoothing={0.7}
          intensity={0.8}
          mipmapBlur
          radius={0.65}
        />
      </EffectComposer>
    </Canvas>
  );
}
