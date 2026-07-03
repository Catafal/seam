/**
 * Unit tests for the isNavigable predicate (#286).
 *
 * A node is "navigable" when double-clicking it should trigger an
 * expand/re-center — i.e. when it can resolve to a real neighborhood.
 *
 * Non-navigable nodes:
 *   - locked/private helpers with visibility 'private' or 'protected'
 *     AND no independently useful definition (definition_count === 0 or 1
 *     for a leaf-only edge target)
 *
 * WHY definition_count: the API returns definition_count from the graph_api
 * degree-enrichment query. A node that is only an edge-target (never declared
 * as a standalone symbol) has degree but no definition rows — we use
 * definition_count === 0 as the "bare target, no navigable body" signal.
 *
 * The predicate is intentionally conservative: when uncertain, return true
 * (let the existing EmptyNeighborhoodState handle the empty result gracefully).
 */

import { describe, it, expect } from "vitest";
import { isNavigable } from "../lib/isNavigable";
import type { SymbolNodeData } from "../components/SymbolNode";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const BASE: SymbolNodeData = {
  name: "parse",
  kind: "function",
  signature: "def parse(src: str) -> Tree",
  cluster_id: 1,
  cluster_label: "parser",
  definition_count: 1,
  isCenter: false,
  is_exported: true,
  visibility: "public",
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("isNavigable", () => {
  // ── Normal / public nodes are navigable ──────────────────────────────────
  it("returns true for a normal exported function", () => {
    expect(isNavigable(BASE)).toBe(true);
  });

  it("returns true when visibility is null (unknown — be conservative)", () => {
    expect(isNavigable({ ...BASE, visibility: null })).toBe(true);
  });

  it("returns true when visibility is 'public'", () => {
    expect(isNavigable({ ...BASE, visibility: "public" })).toBe(true);
  });

  it("returns true for a center node regardless of visibility", () => {
    // The center node is always navigable (it IS the current neighborhood).
    expect(isNavigable({ ...BASE, isCenter: true, visibility: "private" })).toBe(true);
  });

  // ── Pure edge-target helpers: definition_count === 0 ─────────────────────
  it("returns false for a private node with no definition (bare edge target)", () => {
    expect(
      isNavigable({ ...BASE, visibility: "private", definition_count: 0 }),
    ).toBe(false);
  });

  it("returns false for a protected node with no definition", () => {
    expect(
      isNavigable({ ...BASE, visibility: "protected", definition_count: 0 }),
    ).toBe(false);
  });

  it("returns false for a 'crate' (Rust private) node with no definition", () => {
    expect(
      isNavigable({ ...BASE, visibility: "crate", definition_count: 0 }),
    ).toBe(false);
  });

  // ── Private WITH a definition: navigate (EmptyNeighborhoodState handles empty) ─
  it("returns true for a private node that has 1+ indexed definitions", () => {
    // It may have an empty neighborhood, but the guard already handles that.
    // Non-navigable gating is only for bare edge-target nodes with no definition.
    expect(
      isNavigable({ ...BASE, visibility: "private", definition_count: 1 }),
    ).toBe(true);
  });

  // ── Unknown visibility but zero definitions ───────────────────────────────
  it("returns false when visibility is null AND definition_count is 0", () => {
    // A completely unknown node with no definition row is a bare reference —
    // navigating would always produce nothing useful.
    expect(isNavigable({ ...BASE, visibility: null, definition_count: 0 })).toBe(false);
  });

  // ── Public node, zero definitions: still let it through (might be stale index) ─
  it("returns true for a public node with definition_count 0 (index may be stale)", () => {
    // Exported/public nodes with zero definitions are probably a stale index, not
    // a non-navigable helper. Let EmptyNeighborhoodState communicate that gracefully.
    expect(isNavigable({ ...BASE, is_exported: true, visibility: "public", definition_count: 0 })).toBe(true);
  });
});
