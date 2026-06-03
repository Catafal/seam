/**
 * Tests for ChangesDrawer (F5).
 *
 * Verifies: renders changed symbols + risk badge from a fixture; renders the
 * not-a-git-repo notice when the API errors; renders nothing when closed.
 * fetch is mocked URL-awarely (the hook only hits /api/changes).
 */

import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FC, ReactNode } from "react";
import { ChangesDrawer } from "../components/ChangesDrawer";
import type { ChangesResponse } from "../api/schema-types";

function makeWrapper(): FC<{ children: ReactNode }> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const CHANGES_FIXTURE: ChangesResponse = {
  changed_symbols: [
    { name: "check", file: "auth.py", kind: "function", start_line: 5, end_line: 6, changed_lines: [6] },
  ],
  new_files: [],
  affected: [],
  risk_level: "medium",
  ambiguous_warning: false,
  scope: "working",
  base_ref: "HEAD",
  partial: false,
};

afterEach(() => vi.unstubAllGlobals());

function mockOk(body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body }),
  );
}

function mockError(status: number, detail: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: false, status, json: async () => ({ detail }) }),
  );
}

const noop = () => {};

describe("ChangesDrawer", () => {
  it("renders nothing when closed", () => {
    mockOk(CHANGES_FIXTURE);
    const { container } = render(
      <ChangesDrawer open={false} onClose={noop} onSelectSymbol={noop} />,
      { wrapper: makeWrapper() },
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders changed symbols and the risk badge", async () => {
    mockOk(CHANGES_FIXTURE);
    render(<ChangesDrawer open onClose={noop} onSelectSymbol={noop} />, {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(screen.getByText("check")).toBeInTheDocument());
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("shows a not-a-git-repo notice on error", async () => {
    mockError(400, { code: "NOT_A_GIT_REPO", message: "Not a git repository" });
    render(<ChangesDrawer open onClose={noop} onSelectSymbol={noop} />, {
      wrapper: makeWrapper(),
    });
    await waitFor(() =>
      expect(screen.getByText(/Couldn't compute changes/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/git init/)).toBeInTheDocument();
  });
});
