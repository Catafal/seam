/**
 * IM4 test: ConstellationTab renders a visible error message when the layout
 * endpoint returns a 500, NOT a blank canvas.
 *
 * S5 (issue #173): explicit isError branch in ConstellationTab.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ConstellationTab from "../components/ConstellationTab";

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        gcTime: 0,
      },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("ConstellationTab error state", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an error message when the layout endpoint returns 500", async () => {
    // Stub fetch to return a 500 error response
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: "Internal Server Error" }),
      }),
    );

    renderWithQuery(<ConstellationTab />);

    // Wait for the error state to appear — must show a visible message, NOT a blank screen
    await waitFor(() => {
      expect(
        screen.getByText(/failed to load constellation layout/i),
      ).toBeInTheDocument();
    });
  });

  it("shows a loading state while the layout is being fetched", () => {
    // Never resolves — keeps the hook in pending state
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    renderWithQuery(<ConstellationTab />);

    // Loading indicator must be visible immediately
    expect(screen.getByText(/loading constellation/i)).toBeInTheDocument();
  });
});
