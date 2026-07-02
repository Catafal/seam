/**
 * TDD tests for the GraphCanvas empty-state branch (A3 — issue #216).
 *
 * When a symbol has no indexed connections (1 node, 0 edges) the full ReactFlow
 * cockpit is replaced by a lightweight inline empty-state panel showing:
 *   - the symbol name and kind
 *   - the signature (when present)
 *   - a "no indexed connections" guidance message
 *
 * Tests here drive the component contract; implementation follows.
 *
 * Strategy: mock useNeighborhood to return a fixture with 1 node / 0 edges,
 * mock @xyflow/react to a stub (so React Flow is never actually instantiated),
 * and assert the empty-state renders the correct content.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";

// ── Mocks ─────────────────────────────────────────────────────────────────────

// Stub @xyflow/react entirely so React Flow renders nothing (no DOM/canvas deps).
// useReactFlow is needed by ViewportController (rendered inside the cockpit panel).
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children?: ReactNode }) => (
    <div data-testid="react-flow-stub">{children}</div>
  ),
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

// Stub the hooks module — let individual tests override the return value.
// Include useStatus because GraphHUD (rendered inside the ReactFlow cockpit)
// calls it; the mock prevents "no export defined" errors.
vi.mock("../api/hooks", () => ({
  useNeighborhood: vi.fn(),
  useImpact: vi.fn(() => ({ data: undefined })),
  useTrace: vi.fn(() => ({ data: undefined })),
  useStatus: vi.fn(() => ({ data: undefined })),
}));

// Stub useGraphOverlays so it always returns stable empty state.
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

/** A symbol with no neighbors: exactly 1 node and 0 edges. */
const ISOLATED_RESPONSE = {
  center: "MyClass.isolatedMethod",
  nodes: [
    {
      id: "MyClass.isolatedMethod",
      name: "MyClass.isolatedMethod",
      kind: "method",
      signature: "def isolatedMethod(self) -> None",
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      is_exported: false,
      visibility: null,
    },
  ],
  edges: [],
};

/** A symbol with neighbors — should NOT trigger the empty-state. */
const CONNECTED_RESPONSE = {
  center: "connected",
  nodes: [
    {
      id: "connected",
      name: "connected",
      kind: "function",
      signature: "def connected()",
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      is_exported: false,
      visibility: null,
    },
    {
      id: "caller",
      name: "caller",
      kind: "function",
      signature: null,
      cluster_id: null,
      cluster_label: null,
      definition_count: 1,
      is_exported: false,
      visibility: null,
    },
  ],
  edges: [
    { id: 1, source: "caller", target: "connected", kind: "call", confidence: "EXTRACTED" },
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

function renderCanvas(center = "MyClass.isolatedMethod") {
  return render(
    <GraphCanvas center={center} />,
    { wrapper: makeWrapper() },
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("GraphCanvas empty-state (A3)", () => {
  const mockUseNeighborhood = vi.mocked(useNeighborhood);

  beforeEach(() => {
    mockUseNeighborhood.mockReturnValue({
      data: ISOLATED_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useNeighborhood>);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty-state panel (not ReactFlow) when nodes=1, edges=0", () => {
    renderCanvas();
    // The ReactFlow stub must NOT be in the document
    expect(screen.queryByTestId("react-flow-stub")).not.toBeInTheDocument();
    // The empty-state panel must be present
    expect(screen.getByTestId("empty-neighborhood")).toBeInTheDocument();
  });

  it("shows the symbol name in the empty-state", () => {
    renderCanvas();
    expect(screen.getByText("MyClass.isolatedMethod")).toBeInTheDocument();
  });

  it("shows the symbol kind in the empty-state", () => {
    renderCanvas();
    // Scope to the empty-state container and match the kind text exactly.
    // Regex "/method/i" would also match the symbol name "MyClass.isolatedMethod",
    // so use exact match on the stand-alone kind label ("method").
    const panel = screen.getByTestId("empty-neighborhood");
    expect(within(panel).getByText("method")).toBeInTheDocument();
  });

  it("shows the signature in the empty-state when present", () => {
    renderCanvas();
    expect(screen.getByText(/def isolatedMethod\(self\) -> None/)).toBeInTheDocument();
  });

  it("shows a 'no indexed connections' guidance message", () => {
    renderCanvas();
    // The message must communicate that there are no connections in the index.
    expect(
      screen.getByText(/no indexed connections/i),
    ).toBeInTheDocument();
  });

  it("does NOT render the empty-state when the symbol has edges (renders ReactFlow)", () => {
    mockUseNeighborhood.mockReturnValue({
      data: CONNECTED_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useNeighborhood>);

    renderCanvas("connected");

    // ReactFlow stub should be present (the full cockpit path)
    expect(screen.getByTestId("react-flow-stub")).toBeInTheDocument();
    // Empty-state should NOT be present
    expect(screen.queryByTestId("empty-neighborhood")).not.toBeInTheDocument();
  });

  it("shows the loading spinner while data is fetching (no empty-state yet)", () => {
    mockUseNeighborhood.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useNeighborhood>);

    renderCanvas();

    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByTestId("empty-neighborhood")).not.toBeInTheDocument();
  });

  it("shows guidance even when signature is absent", () => {
    mockUseNeighborhood.mockReturnValue({
      data: {
        ...ISOLATED_RESPONSE,
        nodes: [{ ...ISOLATED_RESPONSE.nodes[0], signature: null }],
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useNeighborhood>);

    renderCanvas();
    expect(screen.getByTestId("empty-neighborhood")).toBeInTheDocument();
    expect(screen.getByText(/no indexed connections/i)).toBeInTheDocument();
    // Signature must not appear
    expect(screen.queryByText(/def isolatedMethod/)).not.toBeInTheDocument();
  });
});
