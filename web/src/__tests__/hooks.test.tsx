/**
 * TDD tests for web/src/api/hooks.ts.
 *
 * Each TanStack Query hook is tested by:
 * 1. Wrapping the component under test in QueryClientProvider
 * 2. Mocking globalThis.fetch to return controlled payloads
 * 3. Asserting the hook returns the expected data (or error state)
 *
 * Hooks under test:
 *   useStatus, useSearch, useNeighborhood, useSymbol, useClusters
 */

import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";
import {
  useStatus,
  useSearch,
  useNeighborhood,
  useSymbol,
  useClusters,
  useImpact,
  useTrace,
  useChanges,
  useSchema,
  useSnippet,
} from "../api/hooks";
import type {
  StatusResponse,
  SearchResponse,
  NeighborhoodResponse,
  SymbolResponse,
  ClustersResponse,
  ImpactResponse,
  TraceResponse,
  ChangesResponse,
  SchemaResponse,
  SnippetResponse,
} from "../api/schema-types";

// ── Test utilities ─────────────────────────────────────────────────────────────

/** Fresh QueryClient for each test — avoids cache contamination. */
function makeWrapper(): FC<{ children: ReactNode }> {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,   // Don't retry on error — makes error-path tests deterministic
        staleTime: 0,   // Always consider data stale — ensures fetches happen in tests
        gcTime: 0,      // Immediately garbage-collect — keeps test state clean
      },
    },
  });
  return ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Build a minimal 200 fetch mock response. */
function mockFetch(body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── Minimal fixture data ───────────────────────────────────────────────────────

const STATUS_FIXTURE: StatusResponse = {
  root: "/tmp/myproject",
  symbol_count: 100,
  edge_count: 200,
  cluster_count: 5,
  last_indexed: "2026-06-03 12:00:00",
  languages: ["python", "typescript"],
};

const SEARCH_FIXTURE: SearchResponse = {
  results: [
    {
      name: "parse",
      kind: "",
      file: "seam/indexer/parser.py",
      line: 10,
      signature: null,
      cluster_id: null,
      cluster_label: null,
    },
  ],
};

const NEIGHBORHOOD_FIXTURE: NeighborhoodResponse = {
  center: "parse",
  nodes: [
    {
      id: "parse",
      name: "parse",
      kind: "function",
      signature: "def parse(code: str) -> Tree",
      visibility: null,
      is_exported: true,
      cluster_id: 1,
      cluster_label: "parser",
      definition_count: 1,
    },
  ],
  edges: [],
};

const SYMBOL_FIXTURE: SymbolResponse = {
  name: "parse",
  definitions: [
    {
      file: "seam/indexer/parser.py",
      line: 10,
      signature: "def parse(code: str) -> Tree",
      docstring: "Parse source code.",
      visibility: null,
      is_exported: true,
      qualified_name: "seam.indexer.parser.parse",
      decorators: [],
    },
  ],
  callers: ["index_one_file"],
  callees: ["_get_parser"],
  cluster: { id: 1, label: "parser" },
  peers: ["_get_parser", "walk_project"],
  why: [],
};

const CLUSTERS_FIXTURE: ClustersResponse = {
  clusters: [
    { cluster_id: 1, label: "parser", size: 12, representative: "parse" },
    { cluster_id: 2, label: "engine", size: 8, representative: "query" },
  ],
};

const SNIPPET_FIXTURE: SnippetResponse = {
  found: true,
  symbol: "parse",
  uid: "abc12345:10",
  kind: "function",
  file: "seam/indexer/parser.py",
  start_line: 10,
  end_line: 14,
  source_start_line: 10,
  source_end_line: 14,
  signature: "def parse(code: str) -> Tree",
  docstring: null,
  source: "def parse(code: str) -> Tree:\n  ...\n",
  truncated: {
    by_lines: false,
    by_bytes: false,
    original_line_count: 5,
    returned_line_count: 5,
  },
  freshness: {
    file_hash_matches: true,
    mtime_matches: true,
    index_stale: false,
  },
  neighbors: [],
  candidates: [],
  warnings: [],
};

// ── useStatus ─────────────────────────────────────────────────────────────────

describe("useStatus", () => {
  it("fetches /api/status and returns index statistics", async () => {
    mockFetch(STATUS_FIXTURE);
    const { result } = renderHook(() => useStatus(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(STATUS_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "/api/status",
      expect.any(Object),
    );
  });

  it("returns isLoading=true before the response arrives", () => {
    // Never resolves — keeps the hook in pending state
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    const { result } = renderHook(() => useStatus(), { wrapper: makeWrapper() });

    expect(result.current.isPending).toBe(true);
  });
});

// ── useSearch ─────────────────────────────────────────────────────────────────

describe("useSearch", () => {
  it("fetches /api/search with the query string", async () => {
    mockFetch(SEARCH_FIXTURE);
    const { result } = renderHook(() => useSearch("parse"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(SEARCH_FIXTURE.results);
    // URL must include the query
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("q=parse"),
      expect.any(Object),
    );
  });

  it("does NOT fetch when query is empty", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useSearch(""), { wrapper: makeWrapper() });

    // fetch must not have been called
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("returns undefined data when query is empty", () => {
    vi.stubGlobal("fetch", vi.fn());
    const { result } = renderHook(() => useSearch(""), {
      wrapper: makeWrapper(),
    });
    expect(result.current.data).toBeUndefined();
  });
});

// ── useNeighborhood ───────────────────────────────────────────────────────────

describe("useNeighborhood", () => {
  it("fetches /api/graph/neighborhood with symbol and direction", async () => {
    mockFetch(NEIGHBORHOOD_FIXTURE);
    const { result } = renderHook(
      () => useNeighborhood("parse", "both"),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(NEIGHBORHOOD_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("symbol=parse"),
      expect.any(Object),
    );
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("direction=both"),
      expect.any(Object),
    );
  });

  it("does NOT fetch when symbol is empty", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useNeighborhood("", "both"), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// ── useSymbol ─────────────────────────────────────────────────────────────────

describe("useSymbol", () => {
  it("fetches /api/symbol/{name} and returns full detail", async () => {
    mockFetch(SYMBOL_FIXTURE);
    const { result } = renderHook(() => useSymbol("parse"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(SYMBOL_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "/api/symbol/parse",
      expect.any(Object),
    );
  });

  it("does NOT fetch when name is null", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useSymbol(null), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// ── useClusters ───────────────────────────────────────────────────────────────

describe("useClusters", () => {
  it("fetches /api/clusters and returns the cluster list", async () => {
    mockFetch(CLUSTERS_FIXTURE);
    const { result } = renderHook(() => useClusters(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(CLUSTERS_FIXTURE.clusters);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "/api/clusters",
      expect.any(Object),
    );
  });
});

// ── Phase 2 fixtures ─────────────────────────────────────────────────────────

const IMPACT_FIXTURE: ImpactResponse = {
  found: true,
  target: "check",
  risk_summary: { upstream: { WILL_BREAK: 1, LIKELY_AFFECTED: 0, MAY_NEED_TESTING: 0 } },
  upstream: {
    WILL_BREAK: [
      { name: "authenticate_user", distance: 1, confidence: "EXTRACTED", tier: "WILL_BREAK", file: "auth.py", is_test: false },
    ],
  },
  downstream: null,
  truncated: null,
};

const TRACE_FIXTURE: TraceResponse = {
  found: true,
  source: "authenticate_user",
  target: "check",
  paths: [[{ from_name: "authenticate_user", to_name: "check", kind: "call", confidence: "EXTRACTED" }]],
};

const CHANGES_FIXTURE: ChangesResponse = {
  changed_symbols: [
    { name: "check", file: "auth.py", kind: "function", start_line: 5, end_line: 6, changed_lines: [6] },
  ],
  new_files: [],
  affected: [],
  risk_level: "low",
  ambiguous_warning: false,
  scope: "working",
  base_ref: "HEAD",
  partial: false,
};

const SCHEMA_FIXTURE: SchemaResponse = {
  schema_version: 12,
  seam_version: "0.3.0",
  index_seam_version: "0.3.0",
  freshness: { stale: false, reason: null, hint: null },
  counts: {
    files: 10,
    symbols: 100,
    edges: 120,
    clusters: 4,
    comments: 3,
    import_mappings: 2,
    embeddings: 0,
  },
  breakdowns: {
    languages: { python: 10 },
    symbol_kinds: { function: 80, class: 20 },
    edge_kinds: { call: 120 },
    edge_confidence: { EXTRACTED: 120 },
    synthesized_edges: {},
    comment_markers: { WHY: 3 },
    embedding_models: {},
  },
  capabilities: {
    has_clusters: true,
    has_comments: true,
    has_import_mappings: true,
    has_embeddings: false,
    embedding_model_matches: true,
    has_synthesized_edges: false,
    has_field_symbols: false,
    has_receiver_column: true,
    has_search_text: true,
    has_signature_column: true,
    has_synthesized_by_column: true,
  },
  tools: [
    {
      name: "seam_schema",
      transports: ["cli", "mcp", "web"],
      read_only: true,
      use_when: "Discover index capabilities.",
      depends_on: null,
    },
  ],
  recommended_next_calls: ["Use seam_context before editing a known symbol."],
  warnings: [],
  tables: null,
};

// ── useImpact ─────────────────────────────────────────────────────────────────

describe("useImpact", () => {
  it("fetches /api/impact with symbol and direction", async () => {
    mockFetch(IMPACT_FIXTURE);
    const { result } = renderHook(() => useImpact("check", "both"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(IMPACT_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("symbol=check"),
      expect.any(Object),
    );
  });

  it("does NOT fetch when symbol is null", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useImpact(null, "both"), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("does NOT fetch when enabled=false (overlay opt-in)", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useImpact("check", "both", false), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// ── useTrace ──────────────────────────────────────────────────────────────────

describe("useTrace", () => {
  it("fetches /api/trace with source and target", async () => {
    mockFetch(TRACE_FIXTURE);
    const { result } = renderHook(
      () => useTrace("authenticate_user", "check"),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(TRACE_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("source=authenticate_user"),
      expect.any(Object),
    );
  });

  it("does NOT fetch until both endpoints are set", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useTrace("authenticate_user", null), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// ── useChanges ────────────────────────────────────────────────────────────────

describe("useChanges", () => {
  it("fetches /api/changes with the scope", async () => {
    mockFetch(CHANGES_FIXTURE);
    const { result } = renderHook(() => useChanges("working"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(CHANGES_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("scope=working"),
      expect.any(Object),
    );
  });

  it("does NOT fetch when enabled=false (drawer closed)", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useChanges("working", false), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// ── useSchema ────────────────────────────────────────────────────────────────

describe("useSchema", () => {
  it("fetches /api/schema with the verbose flag", async () => {
    mockFetch(SCHEMA_FIXTURE);
    const { result } = renderHook(() => useSchema(true), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(SCHEMA_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("verbose=true"),
      expect.any(Object),
    );
  });

  it("does NOT fetch when enabled=false", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useSchema(false, false), { wrapper: makeWrapper() });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// ── useSnippet ──────────────────────────────────────────────────────────────

describe("useSnippet", () => {
  it("fetches /api/snippet with uid and caps", async () => {
    mockFetch(SNIPPET_FIXTURE);
    const { result } = renderHook(
      () =>
        useSnippet({
          uid: "abc12345:10",
          contextLines: 2,
          maxLines: 50,
          includeNeighbors: true,
        }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(SNIPPET_FIXTURE);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("uid=abc12345%3A10"),
      expect.any(Object),
    );
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("context_lines=2"),
      expect.any(Object),
    );
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("include_neighbors=true"),
      expect.any(Object),
    );
  });

  it("does NOT fetch without a complete selector", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderHook(() => useSnippet({ file: "src/app.py" }), {
      wrapper: makeWrapper(),
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
