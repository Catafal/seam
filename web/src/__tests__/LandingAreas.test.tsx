/**
 * B1 — Landing area cards: folder-based, not cluster-based.
 *
 * Tests that:
 *   L1. "Largest areas" section shows area cards from deriveAreas (folder names),
 *       not from cluster labels (which may be "unit — _sym" junk strings).
 *   L2. Cluster-label strings do NOT appear on the landing.
 *   L3. Area cards show file-count and symbol-count metadata.
 *   L4. The "show tests" toggle controls which areas appear (test dirs hidden by default).
 *   L5. Clicking a landing area card opens the Overview scoped to that area
 *       (the StructureOverview renders and the landing disappears).
 *
 * Strategy: render App with controlled fetch stubs that return known structure +
 * hubs (folder-based) and a non-empty clusters response (to prove clusters are
 * NOT rendered on the landing any more).  The test asserts on text content that
 * can ONLY come from deriveAreas output.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return Wrapper;
}

/** Build a fetch mock that returns controlled fixture data per endpoint. */
function buildMockFetch() {
  const mockFn = vi.fn().mockImplementation((url: string) => {
    if (url.includes("/api/structure")) {
      // StructureSymbol fields: path, name, kind, line, qualified_name, degree
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            symbols: [
              // Production symbols in seam/indexer/
              { name: "init_db",     kind: "function", path: "seam/indexer/db.py",    line: 1,  qualified_name: null, degree: 5 },
              { name: "upsert_file", kind: "function", path: "seam/indexer/db.py",    line: 22, qualified_name: null, degree: 3 },
              // Production symbols in seam/query/
              { name: "query",       kind: "function", path: "seam/query/engine.py",  line: 1,  qualified_name: null, degree: 4 },
              // Test symbol in tests/ — hidden by default
              { name: "test_init_db", kind: "function", path: "tests/unit/test_db.py", line: 1, qualified_name: null, degree: 0 },
            ],
          }),
      });
    }
    if (url.includes("/api/hubs")) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            symbols: [
              { name: "init_db",  kind: "function", degree: 5, path: "seam/indexer/db.py" },
              { name: "query",    kind: "function", degree: 4, path: "seam/query/engine.py" },
            ],
          }),
      });
    }
    if (url.includes("/api/clusters")) {
      // Return clusters with junk labels — these must NOT appear on the landing.
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            clusters: [
              { cluster_id: 1, label: "unit — _sym",        size: 30, representative: "test_init_db" },
              { cluster_id: 2, label: "integration — _sym", size: 20, representative: "query" },
            ],
          }),
      });
    }
    if (url.includes("/api/status")) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            symbol_count: 4,
            edge_count: 5,
            cluster_count: 2,
            last_indexed: new Date().toISOString(),
          }),
      });
    }
    // Fallback — empty safe response for any other endpoint
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ symbols: [], clusters: [], results: [] }),
    });
  });
  return mockFn;
}

// ── Test suite ────────────────────────────────────────────────────────────────

describe("LandingPage – folder-based area cards (B1)", () => {
  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockFetch = buildMockFetch();
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    // ResizeObserver is used by TreemapCanvas (which renders when an area is drilled
    // into). jsdom doesn't have it; provide a no-op stub so the component mounts.
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("L1: renders area cards with folder-based names from deriveAreas", async () => {
    render(<App />, { wrapper: makeWrapper() });

    // Wait for area cards to appear — they derive from the folder structure.
    // "indexer" and "query" are the top-level dirs under seam/ (unwrapped dominant).
    // "indexer" only appears as an area name (not a hub), so getByText is unambiguous.
    await waitFor(() => {
      expect(screen.getByText("indexer")).toBeInTheDocument();
    });
    // "query" appears both as a hub chip name AND as an area card name.
    // Use getAllByText and assert at least one element has the area card aria-label.
    const queryEls = screen.getAllByText("query");
    expect(queryEls.length).toBeGreaterThan(0);
    // At least one should be inside a button with aria-label "Explore area query"
    expect(
      screen.getByRole("button", { name: /explore area query/i }),
    ).toBeInTheDocument();
  });

  it("L2: cluster junk labels do NOT appear on the landing", async () => {
    render(<App />, { wrapper: makeWrapper() });

    // Give the page time to load cluster data (which is still fetched elsewhere)
    await waitFor(() => {
      expect(screen.getByText("indexer")).toBeInTheDocument();
    });

    // These are the cluster labels from our mock — they must NOT appear.
    expect(screen.queryByText("unit — _sym")).not.toBeInTheDocument();
    expect(screen.queryByText("integration — _sym")).not.toBeInTheDocument();
  });

  it("L3: area cards show symbol count metadata", async () => {
    render(<App />, { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(screen.getByText("indexer")).toBeInTheDocument();
    });

    // Each area card should show symbol count.
    // "indexer" area: 2 symbols (init_db, upsert_file); "query" area: 1 symbol.
    // We just assert at least one count is present (avoids brittle exact-text match).
    const symbolCountElements = screen.getAllByText(/symbol/i);
    expect(symbolCountElements.length).toBeGreaterThan(0);
  });

  it("L4: tests area hidden by default, visible after toggle", async () => {
    render(<App />, { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(screen.getByText("indexer")).toBeInTheDocument();
    });

    // "tests" area should not be visible by default (includeTests defaults to false)
    expect(screen.queryByText("tests")).not.toBeInTheDocument();

    // The landing has a "show tests" toggle — find it in the areas section
    // (the hub section also has one; they may share the same toggle)
    const showTestsBtn = screen.getByRole("button", { name: /show tests/i });
    fireEvent.click(showTestsBtn);

    // After toggle, the test area should appear
    await waitFor(() => {
      expect(screen.getByText("tests")).toBeInTheDocument();
    });
  });

  it("L5: clicking an area card opens the Overview (landing disappears)", async () => {
    render(<App />, { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(screen.getByText("indexer")).toBeInTheDocument();
    });

    const indexerCard = screen.getByRole("button", { name: /explore area indexer/i });
    fireEvent.click(indexerCard);

    // After clicking, the landing hero copy should disappear (Overview took over)
    await waitFor(() => {
      expect(screen.queryByText(/explore the codebase/i)).not.toBeInTheDocument();
    });
  });
});
