/**
 * TDD tests for the C3 Topology 2D/3D toggle and cluster hand-off wiring.
 *
 * Covers:
 *   - "Topology" button is present in the header
 *   - Clicking "Topology" shows a 2D/3D sub-toggle (default 2D selected)
 *   - 2D sub-mode renders ClusterGraph2D (mocked)
 *   - Clicking the "3D" toggle renders ConstellationTab (mocked)
 *   - Clicking a cluster in 2D mode calls setCenterSymbol and switches to
 *     neighborhood mode (hand-off via resolveClusterHandoff)
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ConstellationCluster } from "../api/schema-types";

// ── Mock: ConstellationTab (lazy — not loaded until 3D is toggled) ─────────────
vi.mock("../components/ConstellationTab", () => ({
  default: () => <div data-testid="constellation-tab-3d">3D Scene</div>,
}));

// ── Mock: ClusterGraph2D ───────────────────────────────────────────────────────
// We expose a fake onOpenCluster so we can fire cluster clicks from tests.
let capturedOnOpenCluster: ((c: ConstellationCluster) => void) | null = null;

vi.mock("../components/ClusterGraph2D", () => ({
  ClusterGraph2D: ({ onOpenCluster }: { onOpenCluster: (c: ConstellationCluster) => void }) => {
    capturedOnOpenCluster = onOpenCluster;
    return <div data-testid="cluster-graph-2d">2D Cluster Graph</div>;
  },
}));

// ── Mock: heavy hooks that App.tsx pulls in ────────────────────────────────────
vi.mock("../api/hooks", () => ({
  useStatus: () => ({ data: null, isLoading: true, isError: false }),
  useSearch: () => ({ data: [], isLoading: false }),
  useHubs: () => ({ data: [], isLoading: false }),
  useAreas: () => ({ areas: [], isLoading: false }),
  useConstellation: () => ({ data: { clusters: [], links: [] }, isLoading: false }),
  useStructure: () => ({ data: null, isLoading: false }),
  useClusters: () => ({ data: [], isLoading: false }),
}));

// ── Mock: FileSidebar so it doesn't pull in its own heavy deps ─────────────────
vi.mock("../components/FileSidebar", () => ({
  FileSidebar: () => null,
}));

// ── Mock: ChangesDrawer ───────────────────────────────────────────────────────
vi.mock("../components/ChangesDrawer", () => ({
  ChangesDrawer: () => null,
}));

// ── Mock: GraphCanvas — we only test App-level wiring, not canvas internals ───
// When a cluster hand-off fires, App switches to neighborhood mode which would
// render GraphCanvas. Mock it so it doesn't pull in useNeighborhood and other
// unmocked hooks that would throw uncaught exceptions.
vi.mock("../components/GraphCanvas", () => ({
  GraphCanvas: ({ center }: { center: string }) => (
    <div data-testid="graph-canvas">{center}</div>
  ),
}));

// ── Mock: DetailPanel ─────────────────────────────────────────────────────────
vi.mock("../components/DetailPanel", () => ({
  DetailPanel: () => null,
}));

import App from "../App";

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("Topology 2D/3D toggle (C3)", () => {
  beforeEach(() => {
    capturedOnOpenCluster = null;
  });

  it("renders a Topology button in the header", () => {
    renderApp();
    expect(screen.getByRole("tab", { name: /topology/i })).toBeInTheDocument();
  });

  it("does NOT show the 2D/3D sub-toggle before Topology is activated", () => {
    renderApp();
    // 2D/3D sub-toggle should be hidden until the user clicks Topology
    expect(screen.queryByRole("button", { name: /^2D$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^3D$/i })).not.toBeInTheDocument();
  });

  it("shows the 2D/3D sub-toggle and renders 2D by default when Topology is clicked", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));

    await waitFor(() => {
      // Sub-toggle buttons are visible
      expect(screen.getByRole("button", { name: /^2D$/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^3D$/i })).toBeInTheDocument();
    });

    // The 2D graph is rendered by default
    expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument();
    // The 3D tab is NOT yet rendered
    expect(screen.queryByTestId("constellation-tab-3d")).not.toBeInTheDocument();
  });

  it("2D sub-toggle button has aria-pressed=true by default", () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));

    const btn2d = screen.getByRole("button", { name: /^2D$/i });
    expect(btn2d).toHaveAttribute("aria-pressed", "true");
    const btn3d = screen.getByRole("button", { name: /^3D$/i });
    expect(btn3d).toHaveAttribute("aria-pressed", "false");
  });

  it("clicking 3D toggle shows ConstellationTab (lazy) and hides ClusterGraph2D", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));

    // Confirm 2D is showing
    await waitFor(() => expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument());

    // Switch to 3D
    fireEvent.click(screen.getByRole("button", { name: /^3D$/i }));

    // 3D constellation tab should now be rendered; 2D should be gone
    await waitFor(() =>
      expect(screen.getByTestId("constellation-tab-3d")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("cluster-graph-2d")).not.toBeInTheDocument();
  });

  it("3D button has aria-pressed=true after switching to 3D", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    await waitFor(() => screen.getByTestId("cluster-graph-2d"));

    fireEvent.click(screen.getByRole("button", { name: /^3D$/i }));

    await waitFor(() => {
      const btn3d = screen.getByRole("button", { name: /^3D$/i });
      expect(btn3d).toHaveAttribute("aria-pressed", "true");
      const btn2d = screen.getByRole("button", { name: /^2D$/i });
      expect(btn2d).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("clicking a cluster with a representative hands off to neighborhood mode", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    await waitFor(() => expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument());

    // Simulate a cluster click from ClusterGraph2D
    const cluster: ConstellationCluster = {
      cluster_id: 3,
      size: 15,
      label: "Indexer",
      representative: "Indexer.run",
    };
    capturedOnOpenCluster!(cluster);

    // After hand-off: the neighborhood landing (or graph) is shown, not topology
    await waitFor(() =>
      expect(screen.queryByTestId("cluster-graph-2d")).not.toBeInTheDocument(),
    );
    // Story-7: land in the 2D neighborhood centered on the cluster's most-connected
    // symbol (its representative). The mocked GraphCanvas renders `center` as text,
    // so the resolved target is observable — assert it is the representative.
    expect(screen.getByTestId("graph-canvas")).toHaveTextContent("Indexer.run");
  });

  it("cluster hand-off with null representative falls back to label", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    await waitFor(() => expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument());

    // Cluster with no representative — label is the fallback
    const cluster: ConstellationCluster = {
      cluster_id: 7,
      size: 5,
      label: "CLI",
      representative: null,
    };
    capturedOnOpenCluster!(cluster);

    // Hand-off still switches to neighborhood mode
    await waitFor(() =>
      expect(screen.queryByTestId("cluster-graph-2d")).not.toBeInTheDocument(),
    );
    // Null representative → resolveClusterHandoff falls back to the cluster label.
    // Assert the centered symbol is the label, not the representative or id — this
    // distinguishes the fallback path from the representative path above.
    expect(screen.getByTestId("graph-canvas")).toHaveTextContent("CLI");
  });

  it("cluster hand-off with both null does nothing (no crash, stays in topology)", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    await waitFor(() => expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument());

    // Cluster with no representative and no label → resolveClusterHandoff returns null
    const cluster: ConstellationCluster = {
      cluster_id: 9,
      size: 2,
      label: null,
      representative: null,
    };
    // Should not crash
    expect(() => capturedOnOpenCluster!(cluster)).not.toThrow();

    // Topology is still showing (hand-off was a no-op)
    expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument();
  });

  it("switching from Topology to Symbol tab returns to neighborhood (landing) view", async () => {
    // #273: With an explicit TabBar, clicking the already-active Topology tab is a no-op.
    // To return to the landing, the user clicks the Symbol tab.
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    await waitFor(() => expect(screen.getByTestId("cluster-graph-2d")).toBeInTheDocument());

    // Click the Symbol tab to leave Topology and go back to the neighborhood landing
    fireEvent.click(screen.getByRole("tab", { name: /symbol/i }));

    await waitFor(() =>
      expect(screen.queryByTestId("cluster-graph-2d")).not.toBeInTheDocument(),
    );
    // Landing page should be back (no symbol selected in Symbol mode = landing)
    expect(screen.getByText(/explore the codebase/i)).toBeInTheDocument();
  });
});
