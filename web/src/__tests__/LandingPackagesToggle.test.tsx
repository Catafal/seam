/**
 * #287 — LandingPage "show packages" toggle.
 *
 * Mirrors LandingHubsToggle.test.tsx but for the package-file exclusion axis:
 *   P1. "show packages" button is rendered by default (off state, aria-pressed=false).
 *   P2. Clicking toggles to "hide packages" (aria-pressed=true).
 *   P3. Clicking again reverts to "show packages" (aria-pressed=false).
 *   P4. Initial /api/hubs fetch includes show_packages=false in the URL.
 *   P5. After toggling on, next fetch includes show_packages=true.
 *   P6. show_packages and show_tests toggles are independent — both visible simultaneously.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

import App from "../App";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function renderApp() {
  return render(<App />, { wrapper: makeWrapper() });
}

// ── Test suite ────────────────────────────────────────────────────────────────

describe("LandingPage – show packages toggle (#287)", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    // Return one hub so the "Key symbols" section (and both toggles) render.
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

  it("P1: 'show packages' button is present with aria-pressed=false by default", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    const btn = screen.getByRole("button", { name: /show packages/i });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveAttribute("aria-pressed", "false");
  });

  it("P2: clicking 'show packages' switches to 'hide packages' (aria-pressed=true)", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /show packages/i }));
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /hide packages/i });
      expect(btn).toBeInTheDocument();
      expect(btn).toHaveAttribute("aria-pressed", "true");
    });
  });

  it("P3: clicking 'hide packages' reverts to 'show packages'", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /show packages/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /hide packages/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /hide packages/i }));
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /show packages/i });
      expect(btn).toBeInTheDocument();
      expect(btn).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("P4: initial /api/hubs fetch includes show_packages=false", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    const hubCalls = (mockFetch.mock.calls as unknown[][]).filter(
      (args) => typeof args[0] === "string" && (args[0] as string).includes("/api/hubs"),
    );
    expect(hubCalls.length).toBeGreaterThan(0);
    const firstUrl = hubCalls[0][0] as string;
    expect(firstUrl).toMatch(/show_packages=false/);
  });

  it("P5: after toggling on, fetch includes show_packages=true", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /show packages/i }));
    await waitFor(() => {
      const hubCalls = (mockFetch.mock.calls as unknown[][]).filter(
        (args) => typeof args[0] === "string" && (args[0] as string).includes("/api/hubs"),
      );
      const hasTrueCall = hubCalls.some(
        (args) => typeof args[0] === "string" && (args[0] as string).includes("show_packages=true"),
      );
      expect(hasTrueCall).toBe(true);
    });
  });

  it("P6: show_tests and show_packages toggles coexist independently", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("init_db")).toBeInTheDocument());
    // Both buttons are visible by default.
    expect(screen.getByRole("button", { name: /show tests/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /show packages/i })).toBeInTheDocument();
    // Toggle tests on; packages stays off.
    fireEvent.click(screen.getByRole("button", { name: /show tests/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /hide tests/i })).toBeInTheDocument(),
    );
    // Packages toggle is still in "show packages" (off) state.
    expect(screen.getByRole("button", { name: /show packages/i })).toBeInTheDocument();
  });
});
