/**
 * Regression tests for non-navigable node interaction (#286).
 *
 * Spec (from PRD §2):
 *   - Single-click on any node → calls onSelectSymbol (detail panel) — ALWAYS.
 *   - Double-click on a NAVIGABLE node → calls setExpandTarget (re-center/expand).
 *   - Double-click on a NON-NAVIGABLE node → does NOT call setExpandTarget.
 *
 * These tests mount GraphCanvas with stubbed hooks and verify that
 * the expand handler is (or is not) called based on node navigability.
 *
 * Strategy: same pattern as GraphCanvasEmptyState.test.tsx — mock hooks and
 * @xyflow/react so we can control the node data fed to click handlers.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";

// ── Mocks ─────────────────────────────────────────────────────────────────────

// Capture the click handlers that GraphCanvas passes to ReactFlow so we can invoke them.
let capturedNodeClick: ((evt: unknown, node: unknown) => void) | null = null;
let capturedNodeDoubleClick: ((evt: unknown, node: unknown) => void) | null = null;

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    children,
    onNodeClick,
    onNodeDoubleClick,
  }: {
    children?: ReactNode;
    onNodeClick?: (evt: unknown, node: unknown) => void;
    onNodeDoubleClick?: (evt: unknown, node: unknown) => void;
  }) => {
    // Capture the handlers on each render so tests can call them.
    capturedNodeClick = onNodeClick ?? null;
    capturedNodeDoubleClick = onNodeDoubleClick ?? null;
    return <div data-testid="react-flow-stub">{children}</div>;
  },
  MiniMap: () => null,
  Controls: () => null,
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  useNodesState: (init: unknown[]) => [init, vi.fn(), vi.fn()],
  useEdgesState: (init: unknown[]) => [init, vi.fn(), vi.fn()],
  useReactFlow: () => ({ fitView: vi.fn() }),
  MarkerType: { ArrowClosed: "arrowclosed" },
}));

vi.mock("../api/hooks", () => ({
  useNeighborhood: vi.fn(),
  useImpact: vi.fn(() => ({ data: undefined })),
  useTrace: vi.fn(() => ({ data: undefined })),
  useStatus: vi.fn(() => ({ data: undefined })),
}));

vi.mock("../hooks/useGraphOverlays", () => ({
  useGraphOverlays: vi.fn(() => ({
    displayNodes: [],
    displayEdges: [],
    clusters: [],
    tierMap: new Map(),
    traceActive: false,
    traceNodeNames: new Set(),
  })),
}));

import { useNeighborhood } from "../api/hooks";
import { GraphCanvas } from "../components/GraphCanvas";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/**
 * A minimal connected response so GraphCanvas renders the full ReactFlow cockpit
 * (not the empty-state panel), giving us access to the click handlers.
 */
const CONNECTED_RESPONSE = {
  center: "MyService",
  nodes: [
    {
      id: "MyService",
      name: "MyService",
      kind: "class",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      is_exported: true,
      visibility: "public",
    },
    {
      id: "_helper",
      name: "_helper",
      kind: "function",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: 0,   // bare edge-target: non-navigable
      is_exported: false,
      visibility: "private",
    },
    {
      id: "publicFn",
      name: "publicFn",
      kind: "function",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      is_exported: true,
      visibility: "public",
    },
  ],
  edges: [
    { id: 1, source: "MyService", target: "_helper", kind: "call", confidence: "EXTRACTED" },
    { id: 2, source: "MyService", target: "publicFn", kind: "call", confidence: "EXTRACTED" },
  ],
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeWrapper(): FC<{ children: ReactNode }> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Build a minimal RF-node-like object matching the shape GraphCanvas produces. */
function makeRFNode(override: Partial<{
  id: string;
  name: string;
  kind: string;
  visibility: string | null;
  definition_count: number;
  is_exported: boolean;
  isCenter: boolean;
}> = {}) {
  return {
    id: override.id ?? "sym",
    data: {
      name: override.name ?? "sym",
      kind: override.kind ?? "function",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: override.definition_count ?? 1,
      isCenter: override.isCenter ?? false,
      is_exported: override.is_exported ?? true,
      visibility: override.visibility ?? "public",
    },
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("non-navigable node gating (#286)", () => {
  const mockUseNeighborhood = vi.mocked(useNeighborhood);

  beforeEach(() => {
    capturedNodeClick = null;
    capturedNodeDoubleClick = null;
    mockUseNeighborhood.mockReturnValue({
      data: CONNECTED_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useNeighborhood>);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // ── Single-click: always works ─────────────────────────────────────────────

  it("single-click on a navigable node calls onSelectSymbol", () => {
    const onSelectSymbol = vi.fn();
    render(
      <GraphCanvas center="MyService" onSelectSymbol={onSelectSymbol} />,
      { wrapper: makeWrapper() },
    );

    const navigableNode = makeRFNode({ id: "publicFn", visibility: "public", definition_count: 1 });
    act(() => {
      capturedNodeClick?.({}, navigableNode);
    });

    expect(onSelectSymbol).toHaveBeenCalledWith("publicFn");
  });

  it("single-click on a NON-navigable (private, definition_count=0) node still calls onSelectSymbol", () => {
    // The detail panel must still work for non-navigable nodes (informative hover + click).
    const onSelectSymbol = vi.fn();
    render(
      <GraphCanvas center="MyService" onSelectSymbol={onSelectSymbol} />,
      { wrapper: makeWrapper() },
    );

    const nonNavigableNode = makeRFNode({ id: "_helper", visibility: "private", definition_count: 0 });
    act(() => {
      capturedNodeClick?.({}, nonNavigableNode);
    });

    expect(onSelectSymbol).toHaveBeenCalledWith("_helper");
  });

  // ── Double-click: gated by isNavigable ────────────────────────────────────

  it("double-click on a navigable node triggers expand (handler is wired)", () => {
    /**
     * Verify that setExpandTarget fires by observing useNeighborhood being called
     * with the expand target after double-click. The state update sets expandTarget
     * to "publicFn", which triggers a re-render that calls useNeighborhood("publicFn").
     *
     * WHY this assertion was previously unfalsifiable:
     *   The old form `calledWithPublicFn || allCalls.length >= initialCallCount`
     *   always evaluated to true because a mock call count can never decrease —
     *   `allCalls.length >= initialCallCount` is unconditionally true. Removing
     *   the disjunct makes the test fail if setExpandTarget never fires.
     */
    render(
      <GraphCanvas center="MyService" />,
      { wrapper: makeWrapper() },
    );

    const navigableNode = makeRFNode({ id: "publicFn", visibility: "public", definition_count: 1 });
    act(() => {
      capturedNodeDoubleClick?.({}, navigableNode);
    });

    const allCalls = mockUseNeighborhood.mock.calls;
    const calledWithPublicFn = allCalls.some(([arg]) => arg === "publicFn");
    // The only way this passes is if setExpandTarget("publicFn") actually fired,
    // causing a re-render that called useNeighborhood("publicFn").
    expect(calledWithPublicFn).toBe(true);
  });

  it("double-click on a NON-navigable node does NOT fire the expand/re-center handler", () => {
    const onSelectSymbol = vi.fn();
    render(
      <GraphCanvas center="MyService" onSelectSymbol={onSelectSymbol} />,
      { wrapper: makeWrapper() },
    );

    // Record useNeighborhood calls before the double-click.
    const callsBefore = mockUseNeighborhood.mock.calls.map((c) => c[0]);

    const nonNavigableNode = makeRFNode({
      id: "_helper",
      visibility: "private",
      definition_count: 0,
    });
    act(() => {
      capturedNodeDoubleClick?.({}, nonNavigableNode);
    });

    // After double-clicking a non-navigable node, useNeighborhood must NOT be
    // called with "_helper" (the expand target must not be set to a non-navigable id).
    const callsAfter = mockUseNeighborhood.mock.calls.map((c) => c[0]);
    const newCalls = callsAfter.slice(callsBefore.length);
    expect(newCalls).not.toContain("_helper");
  });

  it("double-click on a non-navigable node does not throw or crash the component", () => {
    // The boundary smoke test: the double-click handler must be a no-op, not an exception.
    render(
      <GraphCanvas center="MyService" />,
      { wrapper: makeWrapper() },
    );

    const nonNavigableNode = makeRFNode({
      id: "_helper",
      visibility: "private",
      definition_count: 0,
    });

    // Must not throw.
    expect(() => {
      act(() => {
        capturedNodeDoubleClick?.({}, nonNavigableNode);
      });
    }).not.toThrow();
  });
});
