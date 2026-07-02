/**
 * Tests for the resolveToNode 2D→3D sync helper (previously in clusterHalos.test.ts).
 *
 * ClusterHalos was removed in Phase A (A5) — the component caused an opaque
 * green blob by additively compositing 556 translucent spheres and ignoring
 * node/edge kind filters. This file retains the resolveToNode coverage which
 * is independent of the removed component.
 */

import { describe, it, expect } from "vitest";
import type { LayoutNode } from "../lib/layoutTypes";

// ── resolveToNode — pure helper from ConstellationTab ────────────────────────
//
// Inline here rather than importing from ConstellationTab, which is a default
// export (lazy) and pulls in heavy R3F/drei deps that confuse jsdom.

function resolveToNode(name: string | null | undefined, nodes: LayoutNode[]): LayoutNode | null {
  if (!name) return null;
  return nodes.find((n) => n.name === name) ?? null;
}

const mkNode = (id: number, name: string): LayoutNode => ({
  id, x: 0, y: 0, z: 0, label: "function", name, file_path: null, size: 4, color: "#fff",
});

describe("resolveToNode (2D→3D sync)", () => {
  it("returns the node with the matching name", () => {
    const nodes = [mkNode(0, "main"), mkNode(1, "Client.send")];
    expect(resolveToNode("Client.send", nodes)?.id).toBe(1);
  });

  it("returns null when name is null/undefined", () => {
    expect(resolveToNode(null, [mkNode(0, "main")])).toBeNull();
    expect(resolveToNode(undefined, [mkNode(0, "main")])).toBeNull();
  });

  it("returns null when no node matches", () => {
    expect(resolveToNode("notfound", [mkNode(0, "main")])).toBeNull();
  });
});
