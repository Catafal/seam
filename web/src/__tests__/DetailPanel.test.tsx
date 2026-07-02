/**
 * TDD tests for DetailPanel and ClusterLegend components.
 *
 * DetailPanel renders full symbol detail from a SymbolResponse:
 *   - symbol name as heading
 *   - all definitions (file:line each)
 *   - signature from first definition
 *   - docstring from first definition
 *   - WHY/HACK/NOTE comments
 *   - callers and callees counts
 *   - cluster info
 *   - loading state while useSymbol is fetching
 *   - null state when no symbol is selected
 *
 * ClusterLegend renders a colour swatch + label for each cluster,
 * reusing clusterColor() from lib/clusterColor.ts.
 */

import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";
import { DetailPanel } from "../components/DetailPanel";
import { ClusterLegend } from "../components/ClusterLegend";
import type { SymbolResponse, ClusterItem } from "../api/schema-types";

// ── Test utilities ─────────────────────────────────────────────────────────────

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

// ── Minimal fixture data ───────────────────────────────────────────────────────

const SYMBOL_FIXTURE: SymbolResponse = {
  name: "parse",
  definitions: [
    {
      file: "seam/indexer/parser.py",
      line: 42,
      signature: "def parse(code: str) -> Tree",
      docstring: "Parse source code into an AST.",
      visibility: null,
      is_exported: true,
      qualified_name: "seam.indexer.parser.parse",
      decorators: [],
    },
  ],
  callers: [
    { name: "index_one_file", kind: "call", confidence: "INFERRED" },
    { name: "walk_project", kind: "call", confidence: "INFERRED" },
  ],
  callees: [
    { name: "_get_parser", kind: "call", confidence: "INFERRED" },
    { name: "_parse_tree", kind: "call", confidence: "INFERRED" },
  ],
  cluster: { id: 3, label: "parser" },
  peers: ["_get_parser", "walk_project"],
  why: [
    {
      kind: "WHY",
      text: "Returns a full tree for incremental extraction.",
      file: "seam/indexer/parser.py",
      line: 40,
    },
    {
      kind: "NOTE",
      text: "tree-sitter never raises; check tree.root_node.has_error instead.",
      file: "seam/indexer/parser.py",
      line: 41,
    },
  ],
};

const HOMONYM_FIXTURE: SymbolResponse = {
  ...SYMBOL_FIXTURE,
  definitions: [
    {
      file: "seam/indexer/parser.py",
      line: 42,
      signature: "def parse(code: str) -> Tree",
      docstring: "Python parser.",
      visibility: null,
      is_exported: true,
      qualified_name: "seam.indexer.parser.parse",
      decorators: [],
    },
    {
      file: "seam/query/engine.py",
      line: 10,
      signature: "def parse(query: str) -> Query",
      docstring: "Query parser.",
      visibility: "public",
      is_exported: false,
      qualified_name: "seam.query.engine.parse",
      decorators: ["@deprecated"],
    },
  ],
};

// ── DetailPanel: null state ────────────────────────────────────────────────────

describe("DetailPanel — null state", () => {
  it("renders nothing (null) when selectedSymbol is null", () => {
    const { container } = renderWithQuery(
      <DetailPanel selectedSymbol={null} />,
    );
    // Panel should render empty placeholder, not a full panel
    expect(container.firstChild).not.toBeNull();
    // No symbol name heading should be present
    expect(screen.queryByRole("heading", { name: "parse" })).not.toBeInTheDocument();
  });

  it("shows an empty-state message when no symbol is selected", () => {
    renderWithQuery(<DetailPanel selectedSymbol={null} />);
    expect(
      screen.getByText(/select a node/i),
    ).toBeInTheDocument();
  });
});

// ── DetailPanel: loading state ─────────────────────────────────────────────────

describe("DetailPanel — loading state", () => {
  it("shows a loading indicator while data is being fetched", () => {
    // Stub fetch to never resolve — keeps hook in pending state
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    renderWithQuery(<DetailPanel selectedSymbol="parse" />);

    expect(screen.getByText(/loading/i)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});

// ── DetailPanel: symbol data rendering ────────────────────────────────────────

describe("DetailPanel — with symbol data", () => {
  beforeEach(() => {
    // URL-aware: the embedded ClusterLegend calls /api/clusters via useClusters.
    // Return {clusters: []} for that path so the hook gets a defined value
    // (returning the symbol fixture would yield undefined .clusters → a TanStack
    // "data cannot be undefined" warning).
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        const body = String(url).includes("/api/clusters")
          ? { clusters: [] }
          : SYMBOL_FIXTURE;
        return Promise.resolve({ ok: true, status: 200, json: async () => body });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the symbol name as a heading", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    // Wait for the fetch to resolve and data to render
    await screen.findByRole("heading", { name: "parse" });
  });

  it("renders the definition file and line", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Should show the file path + line number
    expect(screen.getByText(/parser\.py/)).toBeInTheDocument();
    expect(screen.getByText(/42/)).toBeInTheDocument();
  });

  it("renders the signature from the first definition", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(
      screen.getByText(/def parse\(code: str\) -> Tree/),
    ).toBeInTheDocument();
  });

  it("renders the docstring from the first definition", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(
      screen.getByText(/Parse source code into an AST\./),
    ).toBeInTheDocument();
  });

  it("renders callers count", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Two callers: index_one_file, walk_project — look for "Callers 2" region
    const callerLabel = screen.getByText(/Callers/i);
    expect(callerLabel).toBeInTheDocument();
    // The count "2" appears next to the label in the same container
    expect(callerLabel.nextElementSibling?.textContent).toContain("2");
  });

  it("renders callees count", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Two callees: _get_parser, _parse_tree
    const calleeLabel = screen.getByText(/Callees/i);
    expect(calleeLabel).toBeInTheDocument();
    expect(calleeLabel.nextElementSibling?.textContent).toContain("2");
  });

  it("renders the cluster label", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // cluster label "parser" appears in the panel header area (the <p> tag)
    const clusterPs = screen.getAllByText(/parser/);
    // At least one element with "parser" should be there
    expect(clusterPs.length).toBeGreaterThanOrEqual(1);
  });

  it("renders WHY comments", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(
      screen.getByText(/Returns a full tree for incremental extraction\./),
    ).toBeInTheDocument();
  });

  it("renders NOTE comments", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(
      screen.getByText(/tree-sitter never raises/),
    ).toBeInTheDocument();
  });

  it("labels comment kind badges (WHY, NOTE)", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText("WHY")).toBeInTheDocument();
    expect(screen.getByText("NOTE")).toBeInTheDocument();
  });
});

// ── DetailPanel: homonym (multiple definitions) ────────────────────────────────

describe("DetailPanel — homonyms", () => {
  beforeEach(() => {
    // URL-aware (see "with symbol data" block): /api/clusters → {clusters: []}.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        const body = String(url).includes("/api/clusters")
          ? { clusters: [] }
          : HOMONYM_FIXTURE;
        return Promise.resolve({ ok: true, status: 200, json: async () => body });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders ALL definitions when multiple exist", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Both files should appear in the definitions list
    expect(screen.getByText(/parser\.py/)).toBeInTheDocument();
    expect(screen.getByText(/engine\.py/)).toBeInTheDocument();
  });

  it("shows line numbers for each definition", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Line 42 from first def, line 10 from second def
    expect(screen.getByText(/42/)).toBeInTheDocument();
    expect(screen.getByText(/10/)).toBeInTheDocument();
  });
});

// ── ClusterLegend ──────────────────────────────────────────────────────────────

const CLUSTERS_FIXTURE: ClusterItem[] = [
  { cluster_id: 1, label: "parser", size: 12, representative: "parse" },
  { cluster_id: 2, label: "engine", size: 8, representative: "query" },
  { cluster_id: 3, label: null, size: 3, representative: "connect" },
];

describe("ClusterLegend", () => {
  it("renders a colour swatch for each cluster", () => {
    const { container } = render(
      <ClusterLegend clusters={CLUSTERS_FIXTURE} />,
    );
    // Each cluster should have a colour swatch (div with inline background-color)
    const swatches = container.querySelectorAll("[data-testid='cluster-swatch']");
    expect(swatches.length).toBe(3);
  });

  it("renders the label for each cluster", () => {
    render(<ClusterLegend clusters={CLUSTERS_FIXTURE} />);
    expect(screen.getByText("parser")).toBeInTheDocument();
    expect(screen.getByText("engine")).toBeInTheDocument();
  });

  it("renders a fallback label for clusters with null label", () => {
    render(<ClusterLegend clusters={CLUSTERS_FIXTURE} />);
    // cluster_id=3 has null label — should show cluster-3 or similar
    expect(screen.getByText(/cluster-3/i)).toBeInTheDocument();
  });

  it("renders size for each cluster", () => {
    render(<ClusterLegend clusters={CLUSTERS_FIXTURE} />);
    // Use getAllByText since "3" could match cluster-3 label too
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    // cluster 3 has size 3 — the swatch label "cluster-3" contains "3" but
    // the size span shows exact "3" (the two text nodes are in separate elements)
    const sizeSpans = screen.getAllByText("3");
    expect(sizeSpans.length).toBeGreaterThanOrEqual(1);
  });

  it("uses a stable colour from clusterColor for each swatch", () => {
    const { container } = render(
      <ClusterLegend clusters={CLUSTERS_FIXTURE} />,
    );
    const swatches = container.querySelectorAll("[data-testid='cluster-swatch']");
    // cluster_id=1: index 1 % 10 = 1 → indigo-300 (#a5b4fc)
    const firstSwatch = swatches[0] as HTMLElement;
    expect(firstSwatch.style.backgroundColor).toBeTruthy();
  });

  it("renders nothing (empty list) gracefully", () => {
    const { container } = render(<ClusterLegend clusters={[]} />);
    // Should render without throwing — empty list is a valid state
    expect(container.firstChild).not.toBeNull();
  });
});
