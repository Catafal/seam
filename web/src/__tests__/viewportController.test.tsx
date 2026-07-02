/**
 * TDD tests for ViewportController (issue #193).
 *
 * Two concerns tested here:
 *   1. fitDecision — pure helper; no React needed.
 *   2. ViewportController component — light test with the React Flow instance hook
 *      mocked via vi.mock so we can assert fitView is called only on real
 *      overlay activation transitions, not on data refreshes.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act } from "@testing-library/react";
import React, { useState } from "react";
import { fitDecision } from "../lib/viewportFit";
import { ViewportController } from "../components/ViewportController";

// ── fitDecision unit tests ──────────────────────────────────────────────────────

describe("fitDecision", () => {
  it("returns mode=all with empty nodeIds when no overlay is active", () => {
    const result = fitDecision(false, false, new Map(), new Set());
    expect(result.mode).toBe("all");
    expect(result.nodeIds).toEqual([]);
  });

  it("returns mode=impact with tier map keys when impact is active", () => {
    const tierMap = new Map([
      ["A", "WILL_BREAK"],
      ["B", "MAY_NEED_TESTING"],
    ]);
    const result = fitDecision(true, false, tierMap, new Set());
    expect(result.mode).toBe("impact");
    expect(result.nodeIds).toContain("A");
    expect(result.nodeIds).toContain("B");
    expect(result.nodeIds).toHaveLength(2);
  });

  it("returns mode=trace with trace node names when trace is active", () => {
    const traceNames = new Set(["X", "Y", "Z"]);
    const result = fitDecision(false, true, new Map(), traceNames);
    expect(result.mode).toBe("trace");
    expect(result.nodeIds).toContain("X");
    expect(result.nodeIds).toContain("Y");
    expect(result.nodeIds).toContain("Z");
    expect(result.nodeIds).toHaveLength(3);
  });

  it("prefers trace over impact when both are active (should not happen, but defensive)", () => {
    const tierMap = new Map([["A", "WILL_BREAK"]]);
    const traceNames = new Set(["X"]);
    const result = fitDecision(true, true, tierMap, traceNames);
    // Trace takes precedence so the path is framed precisely
    expect(result.mode).toBe("trace");
    expect(result.nodeIds).toContain("X");
  });

  it("returns mode=all when impact is active but tier map is empty (impact not yet loaded)", () => {
    // Empty tierMap means the impact data hasn't arrived yet; fit all to avoid
    // jumping to an empty node set.
    const result = fitDecision(true, false, new Map(), new Set());
    expect(result.mode).toBe("all");
    expect(result.nodeIds).toEqual([]);
  });
});

// ── ViewportController component test ─────────────────────────────────────────

// Mock useReactFlow so the component can be tested outside a ReactFlow provider.
const mockFitView = vi.fn();
vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ fitView: mockFitView }),
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe("ViewportController", () => {
  beforeEach(() => {
    mockFitView.mockClear();
  });

  /**
   * Helper: render ViewportController with controllable overlay props.
   * Uses a wrapper component with useState so we can drive prop transitions.
   */
  function renderController(initial: {
    impactActive: boolean;
    traceActive: boolean;
    tierMap?: Map<string, string>;
    traceNodeNames?: Set<string>;
  }) {
    const tierMap = initial.tierMap ?? new Map([["A", "WILL_BREAK"]]);
    const traceNodeNames = initial.traceNodeNames ?? new Set(["X", "Y"]);

    function Wrapper() {
      const [impactActive, setImpactActive] = useState(initial.impactActive);
      const [traceActive, setTraceActive] = useState(initial.traceActive);
      return (
        <>
          <button
            data-testid="toggle-impact"
            onClick={() => setImpactActive((v) => !v)}
          />
          <button
            data-testid="toggle-trace"
            onClick={() => setTraceActive((v) => !v)}
          />
          <ViewportController
            impactActive={impactActive}
            traceActive={traceActive}
            tierMap={tierMap}
            traceNodeNames={traceNodeNames}
          />
        </>
      );
    }

    return render(<Wrapper />);
  }

  it("does NOT call fitView on initial mount (declarative fitView on ReactFlow handles that)", () => {
    renderController({ impactActive: false, traceActive: false });
    // No overlay transition on mount — fitView must not fire
    expect(mockFitView).not.toHaveBeenCalled();
  });

  it("calls fitView with impact node ids when impact activates (false → true)", async () => {
    const { getByTestId } = renderController({ impactActive: false, traceActive: false });

    act(() => { getByTestId("toggle-impact").click(); });

    // fitView should be called once with the impact node ids
    expect(mockFitView).toHaveBeenCalledTimes(1);
    const opts = mockFitView.mock.calls[0][0];
    // nodes option contains entries with id = tierMap key
    expect(opts.nodes).toEqual(expect.arrayContaining([expect.objectContaining({ id: "A" })]));
  });

  it("calls fitView for all nodes when impact deactivates (true → false)", async () => {
    const { getByTestId } = renderController({ impactActive: true, traceActive: false });
    // Impact was active on mount — no extra fitView for the initial active state
    mockFitView.mockClear();

    act(() => { getByTestId("toggle-impact").click(); });

    expect(mockFitView).toHaveBeenCalledTimes(1);
    const opts = mockFitView.mock.calls[0][0];
    // Clearing overlay → fit all: nodes should be absent or empty
    expect(!opts?.nodes || opts.nodes.length === 0).toBe(true);
  });

  it("calls fitView with trace node ids when trace activates (false → true)", async () => {
    const { getByTestId } = renderController({ impactActive: false, traceActive: false });

    act(() => { getByTestId("toggle-trace").click(); });

    expect(mockFitView).toHaveBeenCalledTimes(1);
    const opts = mockFitView.mock.calls[0][0];
    expect(opts.nodes).toEqual(expect.arrayContaining([expect.objectContaining({ id: "X" })]));
  });

  it("frames the blast radius when impact data arrives AFTER activation (async)", () => {
    // Real flow: the Impact toggle flips active=true BEFORE the impact query
    // resolves, so tierMap is empty on the activation render and only populates
    // one render later. The controller must reframe on that data-arrival render.
    const { rerender } = render(
      <ViewportController
        impactActive={true}
        traceActive={false}
        tierMap={new Map()}
        traceNodeNames={new Set()}
      />,
    );
    // Activation with empty data → no premature fit to an empty/stale set.
    expect(mockFitView).not.toHaveBeenCalled();

    // Data arrives: same impactActive=true, but tierMap now populated.
    rerender(
      <ViewportController
        impactActive={true}
        traceActive={false}
        tierMap={new Map([["A", "WILL_BREAK"], ["B", "MAY_NEED_TESTING"]])}
        traceNodeNames={new Set()}
      />,
    );

    // Now the blast radius must be framed.
    expect(mockFitView).toHaveBeenCalledTimes(1);
    const opts = mockFitView.mock.calls[0][0];
    expect(opts.nodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "A" }),
        expect.objectContaining({ id: "B" }),
      ]),
    );
  });

  it("does NOT call fitView again when the same overlay state stays active (data refresh)", () => {
    // Simulate a data refresh: same impactActive=true prop but new component render
    const tierMap = new Map([["A", "WILL_BREAK"]]);
    const { rerender } = render(
      <ViewportController
        impactActive={true}
        traceActive={false}
        tierMap={tierMap}
        traceNodeNames={new Set()}
      />,
    );
    mockFitView.mockClear(); // clear any mount-time calls (should be none)

    // Re-render with new tierMap (data refresh) but same impactActive boolean
    const newTierMap = new Map([["A", "WILL_BREAK"], ["B", "MAY_NEED_TESTING"]]);
    rerender(
      <ViewportController
        impactActive={true}
        traceActive={false}
        tierMap={newTierMap}
        traceNodeNames={new Set()}
      />,
    );

    // State didn't transition (still true) → no spurious jump
    expect(mockFitView).not.toHaveBeenCalled();
  });
});
