/**
 * S3 TDD tests for pure helpers:
 *   - buildEdgeGeometry  (EdgeLines.tsx)
 *   - bareName           (NodeLabels.tsx)
 *   - selectLabelNodes   (NodeLabels.tsx)
 */

import { describe, it, expect } from "vitest";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── helpers ──────────────────────────────────────────────────────────────────

const mkNode = (
  id: number,
  opts: Partial<LayoutNode> = {},
): LayoutNode => ({
  id,
  x: id * 10,
  y: 0,
  z: 0,
  label: "function",
  name: `fn${id}`,
  file_path: `src/a.ts`,
  size: 4,
  color: "#1DA27E",
  ...opts,
});

const mkEdge = (source: number, target: number, type = "call"): LayoutEdge => ({
  source,
  target,
  type,
});

// ── buildEdgeGeometry ─────────────────────────────────────────────────────────

import { buildEdgeGeometry } from "../components/EdgeLines";

describe("buildEdgeGeometry", () => {
  it("returns empty arrays for no edges", () => {
    const nodeMap = new Map([[0, mkNode(0)], [1, mkNode(1)]]);
    const { positions, colors } = buildEdgeGeometry(nodeMap, [], new Set());
    expect(positions.length).toBe(0);
    expect(colors.length).toBe(0);
  });

  it("produces 6 position floats per edge (2 verts × 3 coords)", () => {
    const nodeMap = new Map([[0, mkNode(0)], [1, mkNode(1)]]);
    const { positions } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1)], new Set());
    expect(positions.length).toBe(6);
  });

  it("produces 6 color floats per edge (2 verts × RGB)", () => {
    const nodeMap = new Map([[0, mkNode(0)], [1, mkNode(1)]]);
    const { colors } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1)], new Set());
    expect(colors.length).toBe(6);
  });

  it("skips edges whose endpoints are not in the nodeMap", () => {
    const nodeMap = new Map([[0, mkNode(0)]]);
    const { positions } = buildEdgeGeometry(nodeMap, [mkEdge(0, 99)], new Set());
    expect(positions.length).toBe(0);
  });

  it("dims edges with zero intensity when one endpoint is not highlighted", () => {
    const nodeMap = new Map([[0, mkNode(0)], [1, mkNode(1)], [2, mkNode(2)]]);
    const edges = [mkEdge(0, 1), mkEdge(1, 2)];
    const highlightedIds = new Set([0, 1]); // edge 0→1 both highlighted; edge 1→2 one highlighted
    const { positions, colors } = buildEdgeGeometry(nodeMap, edges, highlightedIds);
    // edge 0→1 (both highlighted): intensity 0.5 → non-zero colors
    // edge 1→2 (one highlighted): intensity 0.04 → non-zero colors
    // total 2 edges × 6 floats each = 12 positions, 12 colors
    expect(positions.length).toBe(12);
    // first edge colors should be stronger than second
    const firstR = colors[0];
    const secondR = colors[6];
    expect(firstR).toBeGreaterThan(secondR);
  });

  it("skips dimmed edges (zero intensity) when a highlight set is active and edge has no highlighted endpoint", () => {
    // node 0,1 highlighted; edge 2→3 has neither → intensity 0 → skipped
    const nodeMap = new Map(
      [0, 1, 2, 3].map((id) => [id, mkNode(id)] as [number, LayoutNode]),
    );
    const edges = [mkEdge(2, 3)];
    const { positions } = buildEdgeGeometry(nodeMap, edges, new Set([0, 1]));
    expect(positions.length).toBe(0); // dimmed edge skipped
  });

  it("encodes source node position in first 3 floats", () => {
    const a = mkNode(0, { x: 10, y: 20, z: 30 });
    const b = mkNode(1, { x: 40, y: 50, z: 60 });
    const nodeMap = new Map([[0, a], [1, b]]);
    const { positions } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1)], new Set());
    expect(positions[0]).toBe(10);
    expect(positions[1]).toBe(20);
    expect(positions[2]).toBe(30);
    expect(positions[3]).toBe(40);
    expect(positions[4]).toBe(50);
    expect(positions[5]).toBe(60);
  });
});

// ── bareName ──────────────────────────────────────────────────────────────────

import { bareName, selectLabelNodes } from "../components/NodeLabels";

describe("bareName", () => {
  it("strips the container prefix for a qualified name", () => {
    expect(bareName("Client.send")).toBe("send");
  });

  it("returns the whole string when there is no dot", () => {
    expect(bareName("main")).toBe("main");
  });

  it("strips all container prefixes (multi-level)", () => {
    expect(bareName("A.B.method")).toBe("method");
  });

  it("handles trailing dot gracefully (empty string after last dot)", () => {
    expect(bareName("Foo.")).toBe("");
  });
});

// ── selectLabelNodes ──────────────────────────────────────────────────────────

describe("selectLabelNodes", () => {
  const bigNodes = (n: number): LayoutNode[] =>
    Array.from({ length: n }, (_, i) =>
      mkNode(i, { size: n - i }), // descending size
    );

  it("returns at most cap nodes (default 80)", () => {
    const nodes = bigNodes(200);
    const result = selectLabelNodes(nodes);
    expect(result.length).toBeLessThanOrEqual(80);
  });

  it("respects a custom cap", () => {
    const nodes = bigNodes(100);
    expect(selectLabelNodes(nodes, 10).length).toBe(10);
  });

  it("returns all nodes when count <= cap", () => {
    const nodes = bigNodes(5);
    expect(selectLabelNodes(nodes, 80).length).toBe(5);
  });

  it("selects the largest nodes first", () => {
    const nodes = [
      mkNode(0, { size: 1 }),
      mkNode(1, { size: 20 }),
      mkNode(2, { size: 5 }),
    ];
    const result = selectLabelNodes(nodes, 2);
    const ids = result.map((n) => n.id);
    expect(ids).toContain(1); // size=20 (largest)
    expect(ids).toContain(2); // size=5 (second)
    expect(ids).not.toContain(0); // size=1 (smallest — excluded)
  });
});
