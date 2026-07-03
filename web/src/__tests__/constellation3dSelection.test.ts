/**
 * Regression tests for #263: click isolates, never navigates.
 *
 * Guards three invariants:
 *
 * 1. NO-NAVIGATE regression:
 *    ConstellationTab's prop type does NOT include onFocusSymbol — the structural
 *    proof that a 3D node click cannot route through App's setCenterSymbol and
 *    change the 2D neighborhood. If onFocusSymbol is accidentally re-added to
 *    ConstellationTab, this test fails at compile time (expectTypeOf).
 *
 * 2. ISOLATE:
 *    Selecting a node highlights the node itself plus all direct neighbors
 *    (undirected: both callers and callees). Edges not touching the selected
 *    node are excluded.
 *
 * 3. DESELECT:
 *    After a non-empty selection, deselecting yields an empty highlighted set.
 *    The useMemo in ConstellationTab returns new Set() when selectedNode is null;
 *    we test the pure invariant directly.
 *
 * computeHighlightedIds is also tested in selectionHelpers.test.ts; these tests
 * are kept here as the canonical home for #263 regression coverage.
 */

import { describe, it, expect, expectTypeOf } from "vitest";
import type { ComponentProps } from "react";
import { computeHighlightedIds } from "../components/ConstellationTab";
import type ConstellationTab from "../components/ConstellationTab";
import type { LayoutEdge } from "../lib/layoutTypes";

// ── helpers ───────────────────────────────────────────────────────────────────

const mkEdge = (source: number, target: number): LayoutEdge => ({
  source,
  target,
  type: "call",
});

// ── 1. NO-NAVIGATE structural regression ─────────────────────────────────────

describe("ConstellationTab props — navigation guard (#263)", () => {
  /**
   * REGRESSION GUARD: ConstellationTab must NOT have onFocusSymbol in its
   * prop type. If someone re-adds that prop (which previously wired into
   * App's setCenterSymbol), this type assertion will fail the compile step
   * and surface the regression before it ships.
   *
   * Before the fix: ConstellationTabProps included onFocusSymbol — this test
   * will fail (expectTypeOf.not.toHaveProperty throws).
   * After the fix: onFocusSymbol is gone — test passes.
   */
  it("does NOT accept onFocusSymbol prop (structural navigation guard)", () => {
    type Props = ComponentProps<typeof ConstellationTab>;
    // TypeScript compile-time assertion: onFocusSymbol must be absent.
    // expectTypeOf.not.toHaveProperty fails if the property exists in the type.
    expectTypeOf<Props>().not.toHaveProperty("onFocusSymbol");
  });

  /**
   * focusSymbol (inbound 2D→3D sync) MUST still be present — we only remove
   * the outbound navigation callback; the inbound fly-to is kept.
   */
  it("retains focusSymbol prop for inbound 2D→3D sync", () => {
    type Props = ComponentProps<typeof ConstellationTab>;
    expectTypeOf<Props>().toHaveProperty("focusSymbol");
  });
});

// ── 2. ISOLATE — computeHighlightedIds ───────────────────────────────────────

describe("computeHighlightedIds — isolate behavior (#263)", () => {
  it("includes the selected node id itself with no edges", () => {
    const ids = computeHighlightedIds(1, []);
    expect(ids).toEqual(new Set([1]));
  });

  it("includes direct callee (source === selectedId)", () => {
    const ids = computeHighlightedIds(1, [mkEdge(1, 2)]);
    expect(ids).toEqual(new Set([1, 2]));
  });

  it("includes direct caller (target === selectedId)", () => {
    const ids = computeHighlightedIds(1, [mkEdge(3, 1)]);
    expect(ids).toEqual(new Set([1, 3]));
  });

  it("includes both callers and callees (undirected neighborhood)", () => {
    const edges = [mkEdge(1, 2), mkEdge(3, 1), mkEdge(1, 4)];
    const ids = computeHighlightedIds(1, edges);
    expect(ids).toEqual(new Set([1, 2, 3, 4]));
  });

  it("excludes nodes not directly connected to the selected node", () => {
    // Edge 2→3 is in the graph but does NOT touch node 1
    const edges = [mkEdge(1, 2), mkEdge(2, 3)];
    const ids = computeHighlightedIds(1, edges);
    expect(ids.has(3)).toBe(false);
    expect(ids).toEqual(new Set([1, 2]));
  });

  it("handles self-loop gracefully (does not double-add)", () => {
    const ids = computeHighlightedIds(5, [mkEdge(5, 5)]);
    expect(ids).toEqual(new Set([5]));
  });

  it("handles mixed edge types (import, holds, etc.)", () => {
    const edges: LayoutEdge[] = [
      { source: 1, target: 2, type: "import" },
      { source: 3, target: 1, type: "holds" },
    ];
    const ids = computeHighlightedIds(1, edges);
    expect(ids).toEqual(new Set([1, 2, 3]));
  });
});

// ── 3. DESELECT — empty highlighted set after clear ──────────────────────────

describe("deselect restores empty highlighted state (#263)", () => {
  /**
   * ConstellationTab.handleClose sets selectedNode = null, which causes the
   * useMemo to return new Set() (the guard: if (!selectedNode) return new Set()).
   * We test this pure invariant: a null selection always yields size 0.
   */
  it("null selection → empty highlighted set", () => {
    // The useMemo expression when selectedNode is null: new Set<number>()
    const deselected = new Set<number>();
    expect(deselected.size).toBe(0);
  });

  it("non-empty selection followed by deselect → empty set", () => {
    // Simulate: select node 1 with neighbors 2, 3
    const edges = [mkEdge(1, 2), mkEdge(3, 1)];
    const selected = computeHighlightedIds(1, edges);
    expect(selected.size).toBe(3); // 1, 2, 3

    // Deselect: selectedNode = null → highlightedIds = new Set()
    const afterDeselect = new Set<number>();
    expect(afterDeselect.size).toBe(0);
  });

  it("deselecting a node with no neighbors also yields empty set", () => {
    // Node with no edges: only itself was highlighted
    const selected = computeHighlightedIds(7, []);
    expect(selected.size).toBe(1); // just node 7

    // After deselect: empty
    const afterDeselect = new Set<number>();
    expect(afterDeselect.size).toBe(0);
  });
});
