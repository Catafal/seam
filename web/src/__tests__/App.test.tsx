// Task F1 scaffold test: verify the App renders the 'Seam Explorer' header.
// This is the TDD anchor for the scaffold — failing before App.tsx exists,
// passing after. Subsequent tasks add their own test files.
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";

// Isolate each test with a fresh QueryClient to avoid cross-test cache contamination.
function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("App scaffold", () => {
  it("renders the Seam Explorer heading", () => {
    renderWithQuery(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Seam Explorer",
    );
  });

  it("renders the search box", () => {
    renderWithQuery(<App />);
    // F3: the old placeholder is replaced by a real search input
    expect(screen.getByRole("combobox", { name: /search symbols/i })).toBeInTheDocument();
  });

  it("renders the landing hero by default (no symbol selected)", () => {
    renderWithQuery(<App />);
    // Landing page is shown while no center symbol is set. The search-first hero
    // renders immediately (curated sections fill in as data arrives). This confirms
    // App renders the landing path (not GraphCanvas) on initial load.
    expect(screen.getByText(/explore the codebase/i)).toBeInTheDocument();
  });

  // C3: "Constellation" was renamed to "Topology" (2D leads, 3D is opt-in toggle).
  it("shows the Topology tab button (renamed from Constellation in C3)", () => {
    renderWithQuery(<App />);
    expect(screen.getByRole("button", { name: /topology/i })).toBeInTheDocument();
  });

  // The "Seam Explorer" brand is a home button: it is a clickable control and
  // clicking it lands on (or returns to) the landing hero without crashing.
  it("brand acts as a home button to the landing page", () => {
    renderWithQuery(<App />);
    const home = screen.getByRole("button", { name: /back to home/i });
    expect(home).toBeInTheDocument();
    fireEvent.click(home);
    expect(screen.getByText(/explore the codebase/i)).toBeInTheDocument();
  });
});
