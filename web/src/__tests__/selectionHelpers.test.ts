/**
 * S4 TDD tests — pure selection helpers extracted from ConstellationTab.
 *
 * computeHighlightedIds(node, edges) → Set<number>
 *   Given a selected node and the full edge list, returns a set containing
 *   the node's id plus every direct neighbor (undirected: both source and target).
 */

import { describe, it, expect } from "vitest";
import type { LayoutEdge } from "../lib/layoutTypes";

// Will be implemented in ConstellationTab.tsx and re-exported for testability.
import { computeHighlightedIds } from "../components/ConstellationTab";

// ── fixtures ──────────────────────────────────────────────────────────────────

const mkEdge = (source: number, target: number): LayoutEdge => ({
  source,
  target,
  type: "call",
});

// ── computeHighlightedIds ─────────────────────────────────────────────────────

describe("computeHighlightedIds", () => {
  it("includes the selected node id itself", () => {
    const ids = computeHighlightedIds(5, []);
    expect(ids.has(5)).toBe(true);
  });

  it("includes direct callees (source === selectedId)", () => {
    const edges = [mkEdge(5, 10), mkEdge(5, 20)];
    const ids = computeHighlightedIds(5, edges);
    expect(ids.has(10)).toBe(true);
    expect(ids.has(20)).toBe(true);
  });

  it("includes direct callers (target === selectedId)", () => {
    const edges = [mkEdge(1, 5), mkEdge(2, 5)];
    const ids = computeHighlightedIds(5, edges);
    expect(ids.has(1)).toBe(true);
    expect(ids.has(2)).toBe(true);
  });

  it("excludes unrelated edges", () => {
    const edges = [mkEdge(10, 20), mkEdge(30, 40)];
    const ids = computeHighlightedIds(5, edges);
    expect(ids.size).toBe(1); // only the selected node itself
    expect(ids.has(5)).toBe(true);
  });

  it("handles self-edges gracefully (source === target === selectedId)", () => {
    const edges = [mkEdge(5, 5)];
    const ids = computeHighlightedIds(5, edges);
    // self-edge: only the node itself — no extra ids
    expect(ids.size).toBe(1);
    expect(ids.has(5)).toBe(true);
  });

  it("returns a Set (not an array)", () => {
    const result = computeHighlightedIds(0, []);
    expect(result).toBeInstanceOf(Set);
  });
});
