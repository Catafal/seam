/**
 * Unit tests for the isEmptyNeighborhood predicate (A3 — empty-state slice).
 *
 * Pure helper: returns true only when a neighborhood has exactly one node and
 * zero edges — the canonical "symbol exists but has no indexed connections" state.
 */

import { describe, it, expect } from "vitest";
import { isEmptyNeighborhood } from "../lib/emptyNeighborhood";

describe("isEmptyNeighborhood", () => {
  it("returns true when nodes.length===1 and edges.length===0", () => {
    expect(isEmptyNeighborhood(1, 0)).toBe(true);
  });

  it("returns false when there is more than one node (edges may be absent)", () => {
    expect(isEmptyNeighborhood(2, 0)).toBe(false);
  });

  it("returns false when there is one node but also edges", () => {
    expect(isEmptyNeighborhood(1, 1)).toBe(false);
  });

  it("returns false when there are multiple nodes and edges", () => {
    expect(isEmptyNeighborhood(3, 4)).toBe(false);
  });

  it("returns false when there are zero nodes (loading/error sentinel)", () => {
    // Zero nodes means the graph hasn't loaded yet — not an empty-state.
    expect(isEmptyNeighborhood(0, 0)).toBe(false);
  });
});
