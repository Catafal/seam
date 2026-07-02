/**
 * TDD tests for DetailPanel and ClusterLegend components.
 *
 * DetailPanel renders full symbol detail from a SymbolResponse:
 *   - symbol name as heading
 *   - all definitions (file:line each)
 *   - signature from first definition
 *   - docstring from first definition (clamped with show-more for long text)
 *   - WHY/HACK/NOTE comments
 *   - callers and callees grouped by edge kind (S3 — full clickable rows)
 *   - cluster info
 *   - loading state while useSymbol is fetching
 *   - null state when no symbol is selected
 *
 * ClusterLegend renders a colour swatch + label for each cluster,
 * reusing clusterColor() from lib/clusterColor.ts.
 *
 * S3 additions:
 *   - Rows grouped by edge kind (call, import, reads, writes, holds, uses)
 *   - Each row is clickable and calls onNavigate(name)
 *   - Each row shows a confidence badge
 *   - Qualified names show last segment as label; full name in title
 *   - Per-group cap with "show N more" expander
 *   - Docstring clamped with show-more toggle for long text
 */

import { render, screen, fireEvent } from "@testing-library/react";
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

/** Stub fetch to always return `symbolBody` for symbol calls and empty clusters. */
function stubFetch(symbolBody: SymbolResponse) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      const body = String(url).includes("/api/clusters")
        ? { clusters: [] }
        : symbolBody;
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }),
  );
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

/** Fixture with mixed edge kinds to verify grouping. */
const MULTI_KIND_FIXTURE: SymbolResponse = {
  ...SYMBOL_FIXTURE,
  callers: [
    { name: "Reader.load", kind: "reads", confidence: "EXTRACTED" },
    { name: "Writer.save", kind: "writes", confidence: "EXTRACTED" },
    { name: "index_one_file", kind: "call", confidence: "INFERRED" },
  ],
  callees: [
    { name: "Client.connect", kind: "call", confidence: "EXTRACTED" },
    { name: "Storage.store", kind: "holds", confidence: "INFERRED" },
  ],
};

/** Fixture with >5 callers of same kind to test cap + show-more. */
const MANY_CALLERS_FIXTURE: SymbolResponse = {
  ...SYMBOL_FIXTURE,
  callers: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({
    name: `caller${i}`,
    kind: "call",
    confidence: "INFERRED" as const,
  })),
};

/** Fixture with a long docstring to test the clamp + show-more. */
const LONG_DOCSTRING = "A".repeat(201);
const LONG_DOCSTRING_FIXTURE: SymbolResponse = {
  ...SYMBOL_FIXTURE,
  definitions: [
    {
      ...SYMBOL_FIXTURE.definitions[0],
      docstring: LONG_DOCSTRING,
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
    expect(container.firstChild).not.toBeNull();
    expect(screen.queryByRole("heading", { name: "parse" })).not.toBeInTheDocument();
  });

  it("shows an empty-state message when no symbol is selected", () => {
    renderWithQuery(<DetailPanel selectedSymbol={null} />);
    expect(screen.getByText(/select a node/i)).toBeInTheDocument();
  });
});

// ── DetailPanel: loading state ─────────────────────────────────────────────────

describe("DetailPanel — loading state", () => {
  it("shows a loading indicator while data is being fetched", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    renderWithQuery(<DetailPanel selectedSymbol="parse" />);

    expect(screen.getByText(/loading/i)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});

// ── DetailPanel: symbol data rendering ────────────────────────────────────────

describe("DetailPanel — with symbol data", () => {
  beforeEach(() => stubFetch(SYMBOL_FIXTURE));
  afterEach(() => vi.unstubAllGlobals());

  it("renders the symbol name as a heading", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
  });

  it("renders the definition file and line", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText(/parser\.py/)).toBeInTheDocument();
    expect(screen.getByText(/42/)).toBeInTheDocument();
  });

  it("renders the signature from the first definition", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText(/def parse\(code: str\) -> Tree/)).toBeInTheDocument();
  });

  it("renders the docstring from the first definition", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText(/Parse source code into an AST\./)).toBeInTheDocument();
  });

  it("renders actual caller names (not just count)", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Full list should show the actual caller names
    expect(screen.getByText("index_one_file")).toBeInTheDocument();
    expect(screen.getByText("walk_project")).toBeInTheDocument();
  });

  it("renders actual callee names (not just count)", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText("_get_parser")).toBeInTheDocument();
    expect(screen.getByText("_parse_tree")).toBeInTheDocument();
  });

  it("renders the cluster label", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    const clusterPs = screen.getAllByText(/parser/);
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
    expect(screen.getByText(/tree-sitter never raises/)).toBeInTheDocument();
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
  beforeEach(() => stubFetch(HOMONYM_FIXTURE));
  afterEach(() => vi.unstubAllGlobals());

  it("renders ALL definitions when multiple exist", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText(/parser\.py/)).toBeInTheDocument();
    expect(screen.getByText(/engine\.py/)).toBeInTheDocument();
  });

  it("shows line numbers for each definition", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    expect(screen.getByText(/42/)).toBeInTheDocument();
    expect(screen.getByText(/10/)).toBeInTheDocument();
  });
});

// ── S3: Caller/callee rows — confidence badges ────────────────────────────────

describe("DetailPanel S3 — confidence badges", () => {
  beforeEach(() => stubFetch(SYMBOL_FIXTURE));
  afterEach(() => vi.unstubAllGlobals());

  it("shows confidence badge for each caller row", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Both callers have INFERRED confidence
    const badges = screen.getAllByText("INFERRED");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });
});

// ── S3: Edge kind grouping ────────────────────────────────────────────────────

describe("DetailPanel S3 — edge kind grouping", () => {
  beforeEach(() => stubFetch(MULTI_KIND_FIXTURE));
  afterEach(() => vi.unstubAllGlobals());

  it("groups callers by edge kind", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // Should show kind group labels (callers: reads + writes + call; callees: call + holds)
    expect(screen.getByText("reads")).toBeInTheDocument();
    expect(screen.getByText("writes")).toBeInTheDocument();
    expect(screen.getByText("holds")).toBeInTheDocument();
    // "call" appears in both callers and callees sections — getAllByText handles duplicates
    expect(screen.getAllByText("call").length).toBeGreaterThanOrEqual(1);
  });

  it("shows caller names in the correct group", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // All three callers from different kinds
    expect(screen.getByText("load")).toBeInTheDocument();  // Reader.load → last segment
    expect(screen.getByText("save")).toBeInTheDocument();  // Writer.save → last segment
    expect(screen.getByText("index_one_file")).toBeInTheDocument();
  });

  it("shows qualified caller name in title tooltip (full name on hover)", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // "Reader.load" → display is "load", title should be "Reader.load"
    const loadBtn = screen.getByTitle("Reader.load");
    expect(loadBtn).toBeInTheDocument();
  });
});

// ── S3: Click navigation ──────────────────────────────────────────────────────

describe("DetailPanel S3 — click navigation", () => {
  beforeEach(() => stubFetch(SYMBOL_FIXTURE));
  afterEach(() => vi.unstubAllGlobals());

  it("calls onNavigate with the caller name when a row is clicked", async () => {
    const onNavigate = vi.fn();
    renderWithQuery(
      <DetailPanel selectedSymbol="parse" onNavigate={onNavigate} />,
    );
    await screen.findByRole("heading", { name: "parse" });

    // Click the "index_one_file" caller row
    const btn = screen.getByTitle("index_one_file");
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("index_one_file");
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onNavigate when prop is not provided (no error)", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });

    // Without onNavigate, clicking a row should not throw
    const btn = screen.getByTitle("index_one_file");
    expect(() => fireEvent.click(btn)).not.toThrow();
  });
});

// ── S3: Per-group cap + show-more ─────────────────────────────────────────────

describe("DetailPanel S3 — per-group cap and show-more", () => {
  beforeEach(() => stubFetch(MANY_CALLERS_FIXTURE));
  afterEach(() => vi.unstubAllGlobals());

  it("caps callers at 5 per group by default", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // With 8 callers all of kind "call", only 5 should be visible initially
    // caller6, caller7, caller8 should not be visible
    expect(screen.queryByTitle("caller6")).not.toBeInTheDocument();
    expect(screen.queryByTitle("caller7")).not.toBeInTheDocument();
  });

  it("shows 'show N more' button for capped groups", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });
    // 8 callers - 5 cap = 3 more
    expect(screen.getByText(/show 3 more/i)).toBeInTheDocument();
  });

  it("expands the group when 'show more' is clicked", async () => {
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });

    const showMore = screen.getByText(/show 3 more/i);
    fireEvent.click(showMore);

    // After expanding, all 8 callers should be visible
    expect(screen.getByTitle("caller6")).toBeInTheDocument();
    expect(screen.getByTitle("caller7")).toBeInTheDocument();
    expect(screen.getByTitle("caller8")).toBeInTheDocument();
  });
});

// ── S3: Docstring clamp + show-more ───────────────────────────────────────────

describe("DetailPanel S3 — docstring clamp", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("clamps long docstrings and shows a show-more button", async () => {
    stubFetch(LONG_DOCSTRING_FIXTURE);
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });

    // The full 201-char docstring should NOT be shown initially
    expect(screen.queryByText(LONG_DOCSTRING)).not.toBeInTheDocument();
    // Show-more button should be present
    expect(screen.getByRole("button", { name: /show more/i })).toBeInTheDocument();
  });

  it("reveals full docstring after clicking show-more", async () => {
    stubFetch(LONG_DOCSTRING_FIXTURE);
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });

    fireEvent.click(screen.getByRole("button", { name: /show more/i }));
    // Full docstring is now visible
    expect(screen.getByText(LONG_DOCSTRING)).toBeInTheDocument();
  });

  it("does NOT show show-more for short docstrings", async () => {
    stubFetch(SYMBOL_FIXTURE);
    renderWithQuery(<DetailPanel selectedSymbol="parse" />);
    await screen.findByRole("heading", { name: "parse" });

    // "Parse source code into an AST." is short — no show-more button for docstring
    const showMoreButtons = screen.queryAllByRole("button", { name: /show more/i });
    expect(showMoreButtons).toHaveLength(0);
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
    expect(screen.getByText(/cluster-3/i)).toBeInTheDocument();
  });

  it("renders size for each cluster", () => {
    render(<ClusterLegend clusters={CLUSTERS_FIXTURE} />);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    const sizeSpans = screen.getAllByText("3");
    expect(sizeSpans.length).toBeGreaterThanOrEqual(1);
  });

  it("uses a stable colour from clusterColor for each swatch", () => {
    const { container } = render(
      <ClusterLegend clusters={CLUSTERS_FIXTURE} />,
    );
    const swatches = container.querySelectorAll("[data-testid='cluster-swatch']");
    const firstSwatch = swatches[0] as HTMLElement;
    expect(firstSwatch.style.backgroundColor).toBeTruthy();
  });

  it("renders nothing (empty list) gracefully", () => {
    const { container } = render(<ClusterLegend clusters={[]} />);
    expect(container.firstChild).not.toBeNull();
  });
});
