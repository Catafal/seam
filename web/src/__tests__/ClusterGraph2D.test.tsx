/**
 * TDD tests for ClusterGraph2D component (C2).
 *
 * Covers:
 *   - Renders one React Flow node per cluster from the layout
 *   - Click fires onOpenCluster with the correct cluster data
 *   - Empty state renders when no clusters are provided
 *   - Loading state while data is fetching
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";
import { vi } from "vitest";
import type { ConstellationCluster, ConstellationLink } from "../api/schema-types";

// Mock @xyflow/react so tests don't need a DOM canvas
vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  type RFNode = { id: string; data: { clusterId: number; label: string | null; size: number; representative: string | null } };
  type RFEdge = { id: string; source: string; target: string };

  return {
    ReactFlow: ({ nodes, onNodeClick, children }: {
      nodes: RFNode[];
      edges?: RFEdge[];
      onNodeClick?: (event: React.MouseEvent, node: RFNode) => void;
      children?: ReactNode;
    }) =>
      React.createElement(
        "div",
        { "data-testid": "reactflow-root" },
        nodes.map((n) =>
          React.createElement(
            "div",
            {
              key: n.id,
              "data-testid": `cluster-node-${n.id}`,
              "data-cluster-id": n.data.clusterId,
              onClick: onNodeClick
                ? (e: React.MouseEvent) => onNodeClick(e, n)
                : undefined,
            },
            n.data.label ?? String(n.data.clusterId),
          ),
        ),
        children,
      ),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    Panel: ({ children }: { children: ReactNode }) =>
      React.createElement("div", { "data-testid": "rf-panel" }, children),
    // Use real useState so setNodes/setEdges updates propagate to ReactFlow mock
    useNodesState: (init: RFNode[]) => {
      const [state, setState] = React.useState<RFNode[]>(init);
      // onNodesChange no-op
      return [state, setState, vi.fn()];
    },
    useEdgesState: (init: RFEdge[]) => {
      const [state, setState] = React.useState<RFEdge[]>(init);
      return [state, setState, vi.fn()];
    },
    useReactFlow: () => ({ fitView: vi.fn() }),
    BackgroundVariant: { Dots: "dots" },
  };
});

// Mock the useConstellation hook
vi.mock("../api/hooks", () => ({
  useConstellation: vi.fn(),
}));

import { ClusterGraph2D } from "../components/ClusterGraph2D";
import { useConstellation } from "../api/hooks";

const mockUseConstellation = useConstellation as ReturnType<typeof vi.fn>;

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeWrapper(): FC<{ children: ReactNode }> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function renderWithQuery(ui: React.ReactElement) {
  return render(ui, { wrapper: makeWrapper() });
}

function makeCluster(
  cluster_id: number,
  size: number,
  label: string | null = null,
  representative: string | null = null,
): ConstellationCluster {
  return { cluster_id, size, label, representative };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ClusterGraph2D", () => {
  it("shows empty state when there are no clusters", () => {
    mockUseConstellation.mockReturnValue({
      data: { clusters: [], links: [] },
      isLoading: false,
    });

    renderWithQuery(<ClusterGraph2D onOpenCluster={vi.fn()} />);
    expect(screen.getByText(/No clusters yet/i)).toBeInTheDocument();
    expect(screen.getByText(/seam init/i)).toBeInTheDocument();
  });

  it("shows loading state while fetching", () => {
    mockUseConstellation.mockReturnValue({
      data: undefined,
      isLoading: true,
    });

    renderWithQuery(<ClusterGraph2D onOpenCluster={vi.fn()} />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders one React Flow node per cluster", () => {
    const clusters: ConstellationCluster[] = [
      makeCluster(1, 20, "Parsers"),
      makeCluster(2, 10, "DB"),
      makeCluster(3, 5, "CLI"),
    ];
    const links: ConstellationLink[] = [];

    mockUseConstellation.mockReturnValue({
      data: { clusters, links },
      isLoading: false,
    });

    renderWithQuery(<ClusterGraph2D onOpenCluster={vi.fn()} />);

    expect(screen.getByTestId("cluster-node-cluster-1")).toBeInTheDocument();
    expect(screen.getByTestId("cluster-node-cluster-2")).toBeInTheDocument();
    expect(screen.getByTestId("cluster-node-cluster-3")).toBeInTheDocument();
  });

  it("fires onOpenCluster with the correct cluster when a node is clicked", () => {
    const clusters: ConstellationCluster[] = [
      makeCluster(5, 30, "Indexer", "Indexer.run"),
    ];
    const links: ConstellationLink[] = [];
    const onOpenCluster = vi.fn();

    mockUseConstellation.mockReturnValue({
      data: { clusters, links },
      isLoading: false,
    });

    renderWithQuery(<ClusterGraph2D onOpenCluster={onOpenCluster} />);

    const node = screen.getByTestId("cluster-node-cluster-5");
    fireEvent.click(node);

    expect(onOpenCluster).toHaveBeenCalledOnce();
    // The callback receives the cluster data object
    const arg = onOpenCluster.mock.calls[0][0] as ConstellationCluster;
    expect(arg.cluster_id).toBe(5);
    expect(arg.representative).toBe("Indexer.run");
  });

  it("does not crash when data has no links", () => {
    mockUseConstellation.mockReturnValue({
      data: { clusters: [makeCluster(1, 10)], links: [] },
      isLoading: false,
    });

    expect(() =>
      renderWithQuery(<ClusterGraph2D onOpenCluster={vi.fn()} />),
    ).not.toThrow();
  });
});
