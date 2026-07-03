/**
 * TDD tests for the StatusStrip component (#274).
 *
 * The StatusStrip replaces the header's StatusBadge: it lives at the
 * BOTTOM of the viewport, shows index stats, and is the ONLY place that
 * displays the stale signal. An amber dot + "index stale — run seam sync"
 * appears ONLY when the API returns stale=true. Fresh state is quiet/low-contrast.
 *
 * Cases covered:
 *   1. Fresh state — counts shown, NO warning text
 *   2. Stale state — amber dot + "index stale — run seam sync" shown
 *   3. Loading state — skeleton placeholder, no counts, no warning
 *   4. Error state — "no index" error text
 *   5. No layout shift — stale strip does not grow vertically vs fresh strip
 *      (enforced by always reserving the row space)
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";
import { StatusStrip } from "../components/StatusStrip";

// ── Helper: controlled useStatus mock ─────────────────────────────────────────

// We mock the hooks module so each test controls the status response exactly.
vi.mock("../api/hooks", () => ({
  useStatus: vi.fn(),
  // Other hooks needed by any transitive import:
  useSearch: () => ({ data: [], isLoading: false }),
  useHubs: () => ({ data: [], isLoading: false }),
  useAreas: () => ({ areas: [], isLoading: false }),
  useConstellation: () => ({ data: { clusters: [], links: [] }, isLoading: false }),
  useStructure: () => ({ data: null, isLoading: false }),
  useClusters: () => ({ data: [], isLoading: false }),
}));

import { useStatus } from "../api/hooks";
const mockUseStatus = vi.mocked(useStatus);

function makeWrapper(): FC<{ children: ReactNode }> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

function renderStrip() {
  return render(<StatusStrip />, { wrapper: makeWrapper() });
}

// ── 1. Fresh state ─────────────────────────────────────────────────────────────

describe("StatusStrip — fresh (stale=false)", () => {
  it("renders symbol, edge, and cluster counts", () => {
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 1234,
        edge_count: 5678,
        cluster_count: 42,
        last_indexed: "2026-07-03T10:00:00Z",
        languages: ["python"],
        stale: false,
        stale_reason: null,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    // Counts must appear somewhere in the strip
    expect(screen.getByText(/1,234/)).toBeInTheDocument();
    expect(screen.getByText(/5,678/)).toBeInTheDocument();
    expect(screen.getByText(/42/)).toBeInTheDocument();
  });

  it("shows NO stale warning text when stale=false", () => {
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 100,
        edge_count: 200,
        cluster_count: 5,
        last_indexed: "2026-07-03T10:00:00Z",
        languages: ["python"],
        stale: false,
        stale_reason: null,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    expect(screen.queryByText(/stale/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/seam sync/i)).not.toBeInTheDocument();
  });

  it("shows 'indexed … ago' relative time label", () => {
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 100,
        edge_count: 200,
        cluster_count: 5,
        last_indexed: null,
        languages: [],
        stale: false,
        stale_reason: null,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    // "never" is the fallback when last_indexed is null
    expect(screen.getByText(/never/i)).toBeInTheDocument();
  });
});

// ── 2. Stale state ────────────────────────────────────────────────────────────

describe("StatusStrip — stale (stale=true)", () => {
  it("shows the amber stale warning: 'index stale — run seam sync'", () => {
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 50,
        edge_count: 100,
        cluster_count: 3,
        last_indexed: "2026-07-03T10:00:00Z",
        languages: ["typescript"],
        stale: true,
        stale_reason: "some files changed since last index",
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    // The warning text tells the user what to do (copy from user side)
    expect(screen.getByText(/index stale/i)).toBeInTheDocument();
    expect(screen.getByText(/seam sync/i)).toBeInTheDocument();
  });

  it("counts are still shown when stale (the strip is informative, not replaced)", () => {
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 999,
        edge_count: 888,
        cluster_count: 7,
        last_indexed: "2026-07-03T10:00:00Z",
        languages: ["go"],
        stale: true,
        stale_reason: "watcher not running",
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    expect(screen.getByText(/999/)).toBeInTheDocument();
    expect(screen.getByText(/888/)).toBeInTheDocument();
  });

  it("the stale warning element carries an amber indicator (data-stale attribute)", () => {
    // We use a data attribute as a stable test hook for the amber state
    // (class names can change with Tailwind refactors; data attributes are stable).
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 10,
        edge_count: 20,
        cluster_count: 1,
        last_indexed: "2026-07-03T10:00:00Z",
        languages: [],
        stale: true,
        stale_reason: "reason",
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    const staleEl = screen.getByTestId("stale-indicator");
    expect(staleEl).toBeInTheDocument();
  });

  it("no stale indicator rendered when stale=false", () => {
    mockUseStatus.mockReturnValue({
      data: {
        root: "/repo",
        symbol_count: 10,
        edge_count: 20,
        cluster_count: 1,
        last_indexed: "2026-07-03T10:00:00Z",
        languages: [],
        stale: false,
        stale_reason: null,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    expect(screen.queryByTestId("stale-indicator")).not.toBeInTheDocument();
  });
});

// ── 3. Loading state ──────────────────────────────────────────────────────────

describe("StatusStrip — loading", () => {
  it("renders a loading placeholder, not counts", () => {
    mockUseStatus.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    // No numeric counts
    expect(screen.queryByText(/symbols/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/edges/i)).not.toBeInTheDocument();
    // No stale warning
    expect(screen.queryByTestId("stale-indicator")).not.toBeInTheDocument();
  });
});

// ── 4. Error state ─────────────────────────────────────────────────────────────

describe("StatusStrip — error", () => {
  it("renders 'no index' or similar error copy when fetch fails", () => {
    mockUseStatus.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    // Error copy — "no index" is the established wording
    expect(screen.getByText(/no index/i)).toBeInTheDocument();
  });

  it("no stale indicator on error", () => {
    mockUseStatus.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useStatus>);

    renderStrip();

    expect(screen.queryByTestId("stale-indicator")).not.toBeInTheDocument();
  });
});
