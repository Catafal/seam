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

  // ── #262: calmer edges + controlled loud-kind dimming ────────────────────────
  //
  // Nodes in "src/foo/*" share clusterKey "src/foo" (first 2 path components).
  // Nodes in "src/foo/*" vs "src/bar/*" are cross-cluster.
  //
  // Color channel references:
  //   call        = #1DA27E → R=0.114 G=0.635 B=0.494  (max: G)
  //   instantiates= #f97316 → R=0.976 G=0.451 B=0.086  (max: R)
  //   uses        = #eab308 → R=0.918 G=0.702 B=0.031  (max: R)
  //   writes      = #ef4444 → R=0.937 G=0.267 B=0.267  (max: R)

  it("#262 same-cluster call edge has strictly lower base intensity than old 0.25 baseline", () => {
    // Old code: same-cluster intensity = 0.25. New target: ~0.10.
    // call G-channel = 0xA2/255 ≈ 0.635. At intensity 0.25 → G = 0.159.
    // After #262 the G-channel must be strictly below 0.159.
    const a = mkNode(0, { file_path: "src/foo/a.ts" });
    const b = mkNode(1, { file_path: "src/foo/b.ts" });
    const nodeMap = new Map([[0, a], [1, b]]);
    const { colors } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "call")], new Set());
    const g = colors[1]; // G channel of source vertex
    expect(g).toBeLessThan(0.635 * 0.25); // strictly less than the old 0.25 baseline
  });

  it("#262 cross-cluster is always dimmer than same-cluster (no highlight)", () => {
    // Same cluster: both nodes under "src/foo"; cross: one under "src/bar".
    const sA = mkNode(0, { file_path: "src/foo/a.ts" });
    const sB = mkNode(1, { file_path: "src/foo/b.ts" });
    const xA = mkNode(2, { file_path: "src/foo/a.ts" });
    const xB = mkNode(3, { file_path: "src/bar/c.ts" }); // different cluster
    const nodeMap = new Map([[0, sA], [1, sB], [2, xA], [3, xB]]);
    const { colors } = buildEdgeGeometry(
      nodeMap,
      [mkEdge(0, 1, "call"), mkEdge(2, 3, "call")],
      new Set(),
    );
    const sameG = colors[1];  // G of same-cluster edge source vertex
    const crossG = colors[7]; // G of cross-cluster edge source vertex (second edge)
    expect(sameG).toBeGreaterThan(crossG);
  });

  it("#262 both-highlighted pair is brighter than same-cluster no-highlight", () => {
    // Highlighted intensity (0.5) must exceed same-cluster no-highlight (~0.10).
    const a = mkNode(0, { file_path: "src/foo/a.ts" });
    const b = mkNode(1, { file_path: "src/foo/b.ts" });
    const nodeMap = new Map([[0, a], [1, b]]);
    const { colors: noH } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "call")], new Set());
    const { colors: hl } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "call")], new Set([0, 1]));
    expect(hl[1]).toBeGreaterThan(noH[1]); // G channel, highlighted > no-highlight
  });

  it("#262 loud kinds (instantiates, uses, writes) are dimmer than call in same-cluster no-highlight", () => {
    // Without the loud-kind dim, instantiates R = 0.976×0.25 = 0.244 > call G = 0.635×0.25 = 0.159.
    // After #262: call at ~0.10 → callMax ≈ 0.0635; instantiates at 0.10×0.5 → instMax ≈ 0.0488.
    // All three loud kinds must have a lower max-channel value than call in the same context.
    const a = mkNode(0, { file_path: "src/foo/a.ts" });
    const b = mkNode(1, { file_path: "src/foo/b.ts" });
    const nodeMap = new Map([[0, a], [1, b]]);
    const noHl = new Set<number>();

    const { colors: callC }   = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "call")],        noHl);
    const { colors: instC }   = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "instantiates")], noHl);
    const { colors: usesC }   = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "uses")],         noHl);
    const { colors: writesC } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "writes")],       noHl);

    // Max channel (= visual prominence proxy) for each kind
    const callMax   = Math.max(callC[0],   callC[1],   callC[2]);
    const instMax   = Math.max(instC[0],   instC[1],   instC[2]);
    const usesMax   = Math.max(usesC[0],   usesC[1],   usesC[2]);
    const writesMax = Math.max(writesC[0], writesC[1], writesC[2]);

    expect(instMax).toBeLessThan(callMax);
    expect(usesMax).toBeLessThan(callMax);
    expect(writesMax).toBeLessThan(callMax);
  });

  it("#262 loud kinds get full (undimmed) intensity when both endpoints are highlighted", () => {
    // The loud-kind dim must NOT apply in the highlight path — full color returns.
    // instantiates = #f97316, R ≈ 0.976. At intensity 0.5 (no dim): R ≈ 0.488.
    // If dim were wrongly applied: R ≈ 0.244. Threshold 0.4 distinguishes them.
    const a = mkNode(0, { file_path: "src/foo/a.ts" });
    const b = mkNode(1, { file_path: "src/foo/b.ts" });
    const nodeMap = new Map([[0, a], [1, b]]);
    const hlSet = new Set([0, 1]);
    const { colors: instHL }   = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "instantiates")], hlSet);
    const { colors: usesHL }   = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "uses")],         hlSet);
    const { colors: writesHL } = buildEdgeGeometry(nodeMap, [mkEdge(0, 1, "writes")],       hlSet);
    // R channel for all three warm kinds should be near 0.5 × base-R (above threshold 0.4)
    expect(instHL[0]).toBeGreaterThan(0.4);   // 0.976 × 0.5 ≈ 0.488
    expect(usesHL[0]).toBeGreaterThan(0.4);   // 0.918 × 0.5 ≈ 0.459
    expect(writesHL[0]).toBeGreaterThan(0.4); // 0.937 × 0.5 ≈ 0.469
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
