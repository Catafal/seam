/**
 * B1 — useAreas hook.
 *
 * Tests that useAreas:
 *   A. fetches /api/structure and /api/hubs, then returns deriveAreas output.
 *   B. re-derives when includeTests changes (test paths filtered in/out).
 *   C. isLoading is true while either sub-hook is pending.
 *
 * Strategy: mock globalThis.fetch for the two API endpoints and verify the
 * hook result matches what deriveAreas(symbols, hubs, opts) would produce
 * from the same fixture data.  We do NOT mock deriveAreas itself — we want
 * to prove the COMPOSITION works end-to-end (hook wires up correctly).
 */

import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";
import { useAreas } from "../api/hooks";
import { deriveAreas } from "../lib/deriveAreas";
import type { StructureSymbol, HubSymbol } from "../api/schema-types";

// ── Fixtures ───────────────────────────────────────────────────────────────────

// StructureSymbol fields: path, name, kind, line, qualified_name, degree
const STRUCTURE_SYMBOLS: StructureSymbol[] = [
  { name: "init_db",     kind: "function", path: "seam/indexer/db.py",    line: 1,  qualified_name: null, degree: 5 },
  { name: "upsert_file", kind: "function", path: "seam/indexer/db.py",    line: 22, qualified_name: null, degree: 3 },
  { name: "query",       kind: "function", path: "seam/query/engine.py",  line: 1,  qualified_name: null, degree: 4 },
  { name: "test_init",   kind: "function", path: "tests/unit/test_db.py", line: 1,  qualified_name: null, degree: 0 },
];

const HUBS: HubSymbol[] = [
  { name: "init_db", kind: "function", degree: 5, path: "seam/indexer/db.py" },
  { name: "query",   kind: "function", degree: 4, path: "seam/query/engine.py" },
];

// ── Test wrapper ──────────────────────────────────────────────────────────────

function makeWrapper(): FC<{ children: ReactNode }> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
    },
  });
  return ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Stub fetch to return the fixture data for each endpoint. */
function mockFetch(opts: { structurePending?: boolean } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (opts.structurePending && url.includes("/api/structure")) {
        return new Promise(() => {}); // never resolves
      }
      if (url.includes("/api/structure")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ symbols: STRUCTURE_SYMBOLS }),
        });
      }
      if (url.includes("/api/hubs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ symbols: HUBS }),
        });
      }
      // Other endpoints → empty safe response
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ symbols: [], clusters: [] }),
      });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("useAreas", () => {
  it("A: returns deriveAreas output from fetched structure + hubs", async () => {
    mockFetch();
    const { result } = renderHook(() => useAreas({ includeTests: false, includePackages: false }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    // Expected: deriveAreas with the fixture data (tests excluded)
    const expected = deriveAreas(STRUCTURE_SYMBOLS, HUBS, { includeTests: false, includePackages: false });
    expect(result.current.areas).toEqual(expected);
    // The test path (tests/unit/test_db.py) must NOT appear in any area
    const allKeys = result.current.areas.map((a) => a.key);
    expect(allKeys.some((k) => k.includes("tests"))).toBe(false);
  });

  it("B: includeTests=true includes the test area", async () => {
    mockFetch();
    const { result } = renderHook(() => useAreas({ includeTests: true, includePackages: false }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const expected = deriveAreas(STRUCTURE_SYMBOLS, HUBS, { includeTests: true, includePackages: false });
    expect(result.current.areas).toEqual(expected);
    // At least one area should cover the tests directory
    const allPaths = result.current.areas.flatMap((a) => a.paths);
    expect(allPaths.some((p) => p.includes("tests"))).toBe(true);
  });

  it("C: isLoading is true while structure is pending", async () => {
    mockFetch({ structurePending: true });
    const { result } = renderHook(() => useAreas({ includeTests: false, includePackages: false }), {
      wrapper: makeWrapper(),
    });

    // Structure fetch never resolves → always loading
    expect(result.current.isLoading).toBe(true);
    // areas should be empty while loading
    expect(result.current.areas).toEqual([]);
  });

  it("keySymbols for an area come from hubs in that area", async () => {
    mockFetch();
    const { result } = renderHook(() => useAreas({ includeTests: false, includePackages: false }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    // init_db hub is in seam/indexer → should appear as keySymbol for indexer area
    const indexerArea = result.current.areas.find((a) => a.name === "indexer");
    expect(indexerArea).toBeDefined();
    expect(indexerArea!.keySymbols).toContain("init_db");
  });
});
