/**
 * Unit tests for the pure overlay-decoration helpers extracted into useGraphOverlays.
 *
 * We test the exported pure functions directly — no React rendering needed.
 * The hook itself just wires these inside useMemo; correctness lives here.
 */

import { describe, it, expect } from "vitest";
import {
  decorateNodes,
  buildOffCanvasNodes,
  decorateEdges,
  visibleClusters,
  applyNodeKindFilter,
} from "../hooks/useGraphOverlays";
import { defaultEdgeFilter, toggleFilterValue } from "../lib/edgeFilter";
import type { SymbolNodeData } from "../components/SymbolNode";
import type { Node, Edge } from "@xyflow/react";

// ── Minimal test fixtures ───────────────────────────────────────────────────

/** Build a minimal SymbolRFNode for testing. */
function makeNode(id: string, overrides: Partial<SymbolNodeData> = {}): Node<SymbolNodeData> {
  return {
    id,
    type: "symbolNode",
    position: { x: 0, y: 0 },
    data: {
      name: id,
      kind: "function",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      isCenter: false,
      ...overrides,
    },
  };
}

/** Build a minimal RF edge for testing. */
function makeEdge(source: string, target: string, kind = "call", confidence = "EXTRACTED"): Edge {
  return {
    id: `${source}->${target}`,
    source,
    target,
    data: { kind, confidence },
  };
}

// ── decorateNodes ─────────────────────────────────────────────────────────────

describe("decorateNodes", () => {
  const nodes = [makeNode("center"), makeNode("a"), makeNode("b")];

  it("sets impactTier for nodes in the tierMap", () => {
    const tierMap = new Map([["a", "WILL_BREAK"]]);
    const result = decorateNodes(nodes, tierMap, true, "center", false, new Set());
    expect(result.find((n) => n.id === "a")!.data.impactTier).toBe("WILL_BREAK");
    expect(result.find((n) => n.id === "b")!.data.impactTier).toBeNull();
  });

  it("dims nodes not in the tierMap during impact mode (except the impact target itself)", () => {
    const tierMap = new Map([["a", "WILL_BREAK"]]);
    const result = decorateNodes(nodes, tierMap, true, "center", false, new Set());
    // "b" is not in tierMap and not the impact target → dimmed
    expect(result.find((n) => n.id === "b")!.data.dimmed).toBe(true);
    // "center" is the impact target → NOT dimmed even though not in tierMap
    expect(result.find((n) => n.id === "center")!.data.dimmed).toBe(false);
    // "a" is in tierMap → NOT dimmed
    expect(result.find((n) => n.id === "a")!.data.dimmed).toBe(false);
  });

  it("dims nodes not on the trace path in trace mode", () => {
    const tracePathNames = new Set(["center", "a"]);
    const result = decorateNodes(nodes, new Map(), false, "center", true, tracePathNames);
    expect(result.find((n) => n.id === "center")!.data.dimmed).toBe(false);
    expect(result.find((n) => n.id === "a")!.data.dimmed).toBe(false);
    expect(result.find((n) => n.id === "b")!.data.dimmed).toBe(true);
  });

  it("does not dim nodes when no overlay is active", () => {
    const result = decorateNodes(nodes, new Map(), false, "center", false, new Set());
    for (const n of result) {
      expect(n.data.dimmed).toBe(false);
    }
  });

  it("trace mode takes priority over impact mode", () => {
    // Both active: trace mode wins (it checks traceActive first).
    const tierMap = new Map([["a", "WILL_BREAK"]]);
    const tracePathNames = new Set(["center"]);
    const result = decorateNodes(nodes, tierMap, true, "center", true, tracePathNames);
    // "a" is in tierMap but NOT in tracePathNames → dimmed (trace wins)
    expect(result.find((n) => n.id === "a")!.data.dimmed).toBe(true);
  });
});

// ── buildOffCanvasNodes ───────────────────────────────────────────────────────

describe("buildOffCanvasNodes", () => {
  const baseNodes = [makeNode("center", {}), makeNode("a", {})];

  it("returns empty array when names list is empty", () => {
    expect(buildOffCanvasNodes([], new Map(), baseNodes)).toHaveLength(0);
  });

  it("creates one node per name, placed to the right of the existing graph", () => {
    // baseNodes all at x=0, so maxX = NODE_WIDTH (240). startX = 240 + 80 = 320.
    const tierMap = new Map([["ext1", "WILL_BREAK"], ["ext2", "MAY_NEED_TESTING"]]);
    const result = buildOffCanvasNodes(["ext1", "ext2"], tierMap, baseNodes);
    expect(result).toHaveLength(2);
    // All off-canvas nodes have x >= startX
    for (const n of result) {
      expect(n.position.x).toBeGreaterThanOrEqual(0);
    }
    expect(result[0].data.offCanvas).toBe(true);
    expect(result[0].data.impactTier).toBe("WILL_BREAK");
    expect(result[1].data.impactTier).toBe("MAY_NEED_TESTING");
  });

  it("marks off-canvas nodes as non-draggable", () => {
    const result = buildOffCanvasNodes(["ext1"], new Map([["ext1", "WILL_BREAK"]]), baseNodes);
    expect(result[0].draggable).toBe(false);
  });
});

// ── decorateEdges ─────────────────────────────────────────────────────────────

describe("decorateEdges", () => {
  const edges = [
    makeEdge("a", "b", "call", "EXTRACTED"),
    makeEdge("b", "c", "import", "INFERRED"),
  ];

  it("hides edges that are filtered out", () => {
    const filter = toggleFilterValue(defaultEdgeFilter(), "kinds", "import");
    const result = decorateEdges(edges, filter, false, new Set());
    expect(result.find((e) => e.id === "b->c")!.hidden).toBe(true);
    expect(result.find((e) => e.id === "a->b")!.hidden).toBe(false);
  });

  it("shows all edges with default filter", () => {
    const result = decorateEdges(edges, defaultEdgeFilter(), false, new Set());
    for (const e of result) {
      expect(e.hidden).toBe(false);
    }
  });

  it("animates path edges and dims others in trace mode", () => {
    const tracePathEdges = new Set(["a->b"]);
    const result = decorateEdges(edges, defaultEdgeFilter(), true, tracePathEdges);
    const onPath = result.find((e) => e.id === "a->b")!;
    const offPath = result.find((e) => e.id === "b->c")!;
    expect(onPath.animated).toBe(true);
    expect((offPath.style as Record<string, unknown>)?.opacity).toBe(0.15);
  });
});

// ── applyNodeKindFilter ───────────────────────────────────────────────────────

describe("applyNodeKindFilter", () => {
  it("hides nodes whose kind is not in the enabled set", () => {
    const nodes = [
      makeNode("a", { kind: "function" }),
      makeNode("b", { kind: "class" }),
      makeNode("c", { kind: "method" }),
    ];
    const result = applyNodeKindFilter(nodes, new Set(["function"]));
    expect(result.find((n) => n.id === "a")!.hidden).toBe(false);
    expect(result.find((n) => n.id === "b")!.hidden).toBe(true);
    expect(result.find((n) => n.id === "c")!.hidden).toBe(true);
  });

  it("shows all nodes when all kinds are enabled", () => {
    const nodes = [
      makeNode("a", { kind: "function" }),
      makeNode("b", { kind: "class" }),
    ];
    const result = applyNodeKindFilter(nodes, new Set(["function", "class", "method"]));
    for (const n of result) {
      expect(n.hidden).toBe(false);
    }
  });

  it("hides all nodes when enabled set is empty", () => {
    const nodes = [makeNode("a", { kind: "function" }), makeNode("b", { kind: "class" })];
    const result = applyNodeKindFilter(nodes, new Set());
    for (const n of result) {
      expect(n.hidden).toBe(true);
    }
  });

  it("does not mutate the original nodes", () => {
    const node = makeNode("x", { kind: "function" });
    applyNodeKindFilter([node], new Set());
    // original should not have hidden set
    expect(node.hidden).toBeUndefined();
  });

  it("returns an empty array unchanged", () => {
    expect(applyNodeKindFilter([], new Set(["function"]))).toHaveLength(0);
  });
});

// ── visibleClusters ───────────────────────────────────────────────────────────

describe("visibleClusters", () => {
  it("returns distinct clusters from nodes", () => {
    const nodes = [
      makeNode("a", { cluster_id: 1, cluster_label: "Alpha" }),
      makeNode("b", { cluster_id: 1, cluster_label: "Alpha" }),
      makeNode("c", { cluster_id: 2, cluster_label: "Beta" }),
      makeNode("d", { cluster_id: null, cluster_label: null }),
    ];
    const clusters = visibleClusters(nodes);
    expect(clusters).toHaveLength(2);
    const ids = clusters.map((c) => c.cluster_id).sort();
    expect(ids).toEqual([1, 2]);
  });

  it("returns empty array when no nodes have clusters", () => {
    const nodes = [makeNode("a"), makeNode("b")];
    expect(visibleClusters(nodes)).toHaveLength(0);
  });
});
