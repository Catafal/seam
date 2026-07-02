/**
 * TDD tests for GraphHUD pure helpers:
 *   - computeHudCounts (web/src/lib/hudCounts.ts)
 *   - freshnessColor (web/src/lib/freshnessColor.ts)
 *
 * Both are framework-free pure functions so we test them directly without
 * React rendering.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { computeHudCounts } from "../lib/hudCounts";
import { freshnessColor } from "../lib/freshnessColor";
import type { Node, Edge } from "@xyflow/react";
import type { SymbolNodeData } from "../components/SymbolNode";

// ── Fixtures ──────────────────────────────────────────────────────────────────

type SymbolRFNode = Node<SymbolNodeData>;

function makeNode(
  id: string,
  overrides: Partial<SymbolNodeData> = {},
): SymbolRFNode {
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
      offCanvas: false,
      ...overrides,
    },
  };
}

function makeEdge(
  source: string,
  target: string,
  hidden = false,
): Edge {
  return {
    id: `${source}->${target}`,
    source,
    target,
    hidden,
  };
}

// ── computeHudCounts ──────────────────────────────────────────────────────────

describe("computeHudCounts", () => {
  it("counts visible on-canvas nodes (excluding offCanvas cards)", () => {
    const nodes: SymbolRFNode[] = [
      makeNode("a"),
      makeNode("b"),
      makeNode("c", { offCanvas: true }),
    ];
    const result = computeHudCounts(nodes, [], null);
    expect(result.visibleNodes).toBe(2);
  });

  it("counts off-canvas impact nodes separately", () => {
    const nodes: SymbolRFNode[] = [
      makeNode("a"),
      makeNode("ext1", { offCanvas: true }),
      makeNode("ext2", { offCanvas: true }),
    ];
    const result = computeHudCounts(nodes, [], null);
    expect(result.impactedOffCanvas).toBe(2);
  });

  it("always returns impactedOffCanvas count (caller gates the UI badge via impactActive)", () => {
    const nodes: SymbolRFNode[] = [
      makeNode("a"),
      makeNode("ext1", { offCanvas: true }),
    ];
    // The function is pure — it doesn't know whether the overlay is active.
    // The HUD component's impactActive prop controls whether to show the badge.
    const result = computeHudCounts(nodes, [], null);
    expect(result.impactedOffCanvas).toBe(1);
  });

  it("counts visible edges (not hidden)", () => {
    const edges: Edge[] = [
      makeEdge("a", "b", false),
      makeEdge("b", "c", false),
      makeEdge("c", "d", true), // hidden by filter
    ];
    const result = computeHudCounts([], edges, null);
    expect(result.visibleEdges).toBe(2);
  });

  it("counts filtered-out edges", () => {
    const edges: Edge[] = [
      makeEdge("a", "b", false),
      makeEdge("b", "c", true),
      makeEdge("c", "d", true),
    ];
    const result = computeHudCounts([], edges, null);
    expect(result.filteredOut).toBe(2);
  });

  it("returns selectedCount=1 when a node is selected", () => {
    const nodes: SymbolRFNode[] = [makeNode("a"), makeNode("b")];
    const result = computeHudCounts(nodes, [], "a");
    expect(result.selectedCount).toBe(1);
  });

  it("returns selectedCount=0 when no node is selected", () => {
    const nodes: SymbolRFNode[] = [makeNode("a")];
    const result = computeHudCounts(nodes, [], null);
    expect(result.selectedCount).toBe(0);
  });

  it("handles empty arrays gracefully", () => {
    const result = computeHudCounts([], [], null);
    expect(result).toEqual({
      visibleNodes: 0,
      visibleEdges: 0,
      filteredOut: 0,
      impactedOffCanvas: 0,
      selectedCount: 0,
    });
  });

  it("correctly separates on-canvas from off-canvas nodes", () => {
    const nodes: SymbolRFNode[] = [
      makeNode("center", { isCenter: true }),
      makeNode("neighbor"),
      makeNode("offA", { offCanvas: true }),
      makeNode("offB", { offCanvas: true }),
      makeNode("offC", { offCanvas: true }),
    ];
    const edges = [makeEdge("center", "neighbor", false)];
    const result = computeHudCounts(nodes, edges, "center");
    expect(result.visibleNodes).toBe(2);
    expect(result.impactedOffCanvas).toBe(3);
    expect(result.visibleEdges).toBe(1);
    expect(result.filteredOut).toBe(0);
    expect(result.selectedCount).toBe(1);
  });
});

// ── freshnessColor ─────────────────────────────────────────────────────────────

describe("freshnessColor", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns amber for null last_indexed", () => {
    expect(freshnessColor(null)).toBe("#f59e0b");
  });

  it("returns amber for undefined last_indexed", () => {
    expect(freshnessColor(undefined)).toBe("#f59e0b");
  });

  it("returns amber for invalid date string", () => {
    expect(freshnessColor("not-a-date")).toBe("#f59e0b");
  });

  it("returns green when indexed within the last 10 minutes", () => {
    const now = new Date("2026-01-01T12:00:00Z");
    vi.setSystemTime(now);
    // 5 minutes ago — should be green
    const recent = new Date(now.getTime() - 5 * 60 * 1000).toISOString();
    expect(freshnessColor(recent)).toBe("#22c55e");
  });

  it("returns amber when indexed more than 10 minutes ago", () => {
    const now = new Date("2026-01-01T12:00:00Z");
    vi.setSystemTime(now);
    // 15 minutes ago — should be amber
    const old = new Date(now.getTime() - 15 * 60 * 1000).toISOString();
    expect(freshnessColor(old)).toBe("#f59e0b");
  });

  it("returns amber exactly at the 10-minute boundary (not strictly less)", () => {
    const now = new Date("2026-01-01T12:00:00Z");
    vi.setSystemTime(now);
    // Exactly 10 minutes ago — ageMs < 10*60*1000 is false at the boundary
    const boundary = new Date(now.getTime() - 10 * 60 * 1000).toISOString();
    expect(freshnessColor(boundary)).toBe("#f59e0b");
  });
});
