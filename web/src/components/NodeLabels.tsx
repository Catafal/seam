/**
 * NodeLabels — canvas-sprite labels for the top-N most prominent nodes.
 *
 * Canvas sprites are used instead of drei <Text> to avoid a heavy font atlas
 * download; each label is a tiny CanvasTexture disposed on unmount/update.
 *
 * Pure helpers exported for unit testing (no WebGL dependency):
 *   bareName(qualified)         → bare identifier after the last dot
 *   selectLabelNodes(nodes, cap)→ top-cap nodes sorted by size DESC
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §2 "Node Labels"
 */

import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

import type { LayoutNode } from "../lib/layoutTypes";

// ── Pure helpers (exported for unit testing) ──────────────────────────────────

/**
 * Strip the container prefix from a qualified symbol name.
 *
 * "Client.send"   → "send"
 * "A.B.method"    → "method"
 * "main"          → "main"
 *
 * The display name is kept short to avoid label clutter.
 */
export function bareName(qualified: string): string {
  const i = qualified.lastIndexOf(".");
  return i === -1 ? qualified : qualified.slice(i + 1);
}

/**
 * Select the top `cap` nodes by size (largest first).
 *
 * Nodes with larger size (= higher degree, hub symbols) are the most
 * important to label. When the total count ≤ cap all nodes are returned.
 *
 * @param nodes  All visible nodes
 * @param cap    Maximum number of labels to show (default 80)
 */
export function selectLabelNodes(nodes: LayoutNode[], cap = 80): LayoutNode[] {
  if (nodes.length <= cap) return nodes.slice();
  return nodes
    .slice()
    .sort((a, b) => b.size - a.size)
    .slice(0, cap);
}

// ── Canvas texture factory ────────────────────────────────────────────────────

const LABEL_FONT = "bold 28px monospace";
const LABEL_PADDING = 8;
const LABEL_HEIGHT = 48; // canvas height in px
const LABEL_SPRITE_SCALE = 0.12; // world-units-per-pixel (tunable)

/**
 * Build a THREE.CanvasTexture with the given text centered on a transparent bg.
 * Caller is responsible for calling `.dispose()` when the texture is no longer used.
 */
function makeLabel(text: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;

  ctx.font = LABEL_FONT;
  const textWidth = ctx.measureText(text).width;
  canvas.width = Math.ceil(textWidth + LABEL_PADDING * 2);
  canvas.height = LABEL_HEIGHT;

  // Re-apply font after resize (canvas reset clears it).
  ctx.font = LABEL_FONT;
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  ctx.textBaseline = "middle";
  ctx.fillText(text, LABEL_PADDING, LABEL_HEIGHT / 2);

  return new THREE.CanvasTexture(canvas);
}

// ── LabelSprite — one sprite per label ───────────────────────────────────────

interface LabelSpriteProps {
  node: LayoutNode;
}

/** Single canvas-sprite label floating above a node. */
function LabelSprite({ node }: LabelSpriteProps) {
  const spriteRef = useRef<THREE.Sprite>(null!);
  const matRef = useRef<THREE.SpriteMaterial | null>(null);
  const texRef = useRef<THREE.CanvasTexture | null>(null);

  const text = bareName(node.name);

  useEffect(() => {
    const tex = makeLabel(text);
    const mat = new THREE.SpriteMaterial({
      map: tex,
      transparent: true,
      depthWrite: false,
      toneMapped: false,
    });
    texRef.current = tex;
    matRef.current = mat;
    if (spriteRef.current) {
      spriteRef.current.material = mat;
      const aspect = tex.image.width / LABEL_HEIGHT;
      spriteRef.current.scale.set(
        LABEL_HEIGHT * aspect * LABEL_SPRITE_SCALE,
        LABEL_HEIGHT * LABEL_SPRITE_SCALE,
        1,
      );
    }

    return () => {
      tex.dispose();
      mat.dispose();
    };
  }, [text]);

  // Position the label slightly above the node each frame.
  useFrame(() => {
    if (spriteRef.current) {
      spriteRef.current.position.set(node.x, node.y + node.size * 1.6, node.z);
    }
  });

  return <sprite ref={spriteRef} />;
}

// ── NodeLabels ─────────────────────────────────────────────────────────────────

interface NodeLabelsProps {
  nodes: LayoutNode[];
  highlightedIds: Set<number>;
}

/**
 * Renders canvas-sprite labels for the top-80 nodes by size.
 *
 * When a highlight set is active, only labels for highlighted nodes are shown
 * to reduce visual clutter (the highlight already draws attention).
 */
export function NodeLabels({ nodes, highlightedIds }: NodeLabelsProps) {
  const labeled = useMemo(() => {
    const base = selectLabelNodes(nodes);
    if (highlightedIds.size === 0) return base;
    // When highlighted, show labels only for highlighted nodes.
    return base.filter((n) => highlightedIds.has(n.id));
  }, [nodes, highlightedIds]);

  return (
    <>
      {labeled.map((node) => (
        <LabelSprite key={node.id} node={node} />
      ))}
    </>
  );
}
