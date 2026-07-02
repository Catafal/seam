/**
 * A1 (issue #214) — LandingPage "show tests" toggle.
 *
 * Tests that:
 *  T1. The toggle button is rendered with label "show tests" by default (off state).
 *  T2. Clicking the toggle switches aria-pressed to true and label to "hide tests".
 *  T3. Clicking again reverts to the off state.
 *  T4. useHubs is called with showTests=false by default (queries with show_tests=false).
 *  T5. After toggle, useHubs is called with showTests=true (queries with show_tests=true).
 *
 * Strategy: render LandingPage in isolation by mocking the hooks that fire network
 * requests and vitest-controlling the hubs response. We do NOT test /api/hubs
 * network traffic here — that belongs to the backend route tests.
 * Only the toggle-state transitions and the aria contract are tested here.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

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

// Render the full App (which contains LandingPage when no center symbol is set).
// We import App so we don't need to export LandingPage itself.
import App from "../App";

function renderApp() {
  const Wrapper = makeWrapper();
  return render(<App />, { wrapper: Wrapper });
}

// ── Test suite ────────────────────────────────────────────────────────────────

describe("LandingPage – show tests toggle (A1)", () => {
  // Mock fetch so hub requests do not error — return empty hubs so the section
  // does not render (we test toggle state without needing actual hub chips).
  // For toggle visibility tests the section only renders when hubs are non-empty,
  // so we seed one hub to make the section visible.
  const mockFetch = vi.fn();

  beforeEach(() => {
    // Return one hub symbol so the "Key symbols" section renders.
    // Each endpoint returns the minimal correct shape for its consumer.
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/api/hubs")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              symbols: [
                { name: "init_db", kind: "function", degree: 42, path: "seam/indexer/db.py" },
              ],
            }),
        });
      }
      if (url.includes("/api/status")) {
        // StatusBadge reads symbol_count / edge_count / cluster_count / last_indexed.
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              symbol_count: 100,
              edge_count: 200,
              cluster_count: 5,
              last_indexed: new Date().toISOString(),
              db_path: "/.seam/seam.db",
              schema_version: 12,
              freshness: "fresh",
            }),
        });
      }
      // All other endpoints (clusters, search, etc.) → empty safe response.
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ symbols: [], clusters: [] }),
      });
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("T1: toggle button is present with label 'show tests' by default", async () => {
    renderApp();
    // Wait for the hub section to appear (async fetch resolves).
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    const toggle = screen.getByRole("button", { name: /show tests/i });
    expect(toggle).toBeInTheDocument();
    // aria-pressed=false signals the "off" state to screen readers.
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("T2: clicking toggle switches to 'hide tests' and aria-pressed=true", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    const toggle = screen.getByRole("button", { name: /show tests/i });
    fireEvent.click(toggle);
    // After click: label changes and aria-pressed flips.
    // Use waitFor because the re-fetch may briefly unmount the section while loading.
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /hide tests/i });
      expect(btn).toBeInTheDocument();
      expect(btn).toHaveAttribute("aria-pressed", "true");
    });
  });

  it("T3: clicking toggle twice reverts to 'show tests' (off state)", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    const toggle = screen.getByRole("button", { name: /show tests/i });
    fireEvent.click(toggle);
    // Wait for "hide tests" to appear, then click again.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /hide tests/i })).toBeInTheDocument(),
    );
    const toggle2 = screen.getByRole("button", { name: /hide tests/i });
    fireEvent.click(toggle2);
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /show tests/i });
      expect(btn).toBeInTheDocument();
      expect(btn).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("T4: initial fetch includes show_tests=false in the URL query string", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    // At least one /api/hubs call should have show_tests=false.
    const hubCalls = (mockFetch.mock.calls as unknown[][]).filter(
      (args) => typeof args[0] === "string" && (args[0] as string).includes("/api/hubs"),
    );
    expect(hubCalls.length).toBeGreaterThan(0);
    // Default: show_tests=false should be present in the URL.
    const firstCall = hubCalls[0][0] as string;
    expect(firstCall).toMatch(/show_tests=false/);
  });

  it("T5: after toggling on, next fetch includes show_tests=true", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    const toggle = screen.getByRole("button", { name: /show tests/i });
    fireEvent.click(toggle);
    // A new fetch with show_tests=true should be made.
    await waitFor(() => {
      const hubCalls = (mockFetch.mock.calls as unknown[][]).filter(
        (args) => typeof args[0] === "string" && (args[0] as string).includes("/api/hubs"),
      );
      const hasTrueCall = hubCalls.some(
        (args) => typeof args[0] === "string" && (args[0] as string).includes("show_tests=true"),
      );
      expect(hasTrueCall).toBe(true);
    });
  });
});
