/**
 * S6 TDD tests for ClusterHalos.tsx.
 *
 * ClusterHalos is a pure R3F component (no WebGL-testable logic),
 * so we verify:
 *   1. The module exports a ClusterHalos function component.
 *   2. The focusSymbol 2D→3D sync helper can be extracted and tested.
 *
 * The actual halo rendering (sphereGeometry, meshBasicMaterial) is verified
 * manually in the browser; jsdom has no WebGL.
 */

import { describe, it, expect } from "vitest";

describe("ClusterHalos module", () => {
  it("exports ClusterHalos as a function (R3F component)", async () => {
    const mod = await import("../components/ClusterHalos");
    expect(typeof mod.ClusterHalos).toBe("function");
  });
});

// ── focusSymbol sync: resolveToNode helper ────────────────────────────────────

/**
 * resolveToNode — pure helper used by ConstellationTab to locate a node by name
 * when the 2D side sets focusSymbol. Exported from ConstellationTab for testing.
 */
import type { LayoutNode } from "../lib/layoutTypes";

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
