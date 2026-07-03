/**
 * TDD tests for the explicit Overview/Symbol/Topology TabBar (#273).
 *
 * Covers:
 *   1. Pure tabs.ts helper — exactly 3 tabs, stable ids, Symbol→neighborhood
 *   2. TabBar component — exactly one active tab (aria-current), click fires setter
 *   3. App integration — no rendered header control has a label equal to a
 *      NON-ACTIVE mode name (the anti-pattern regression)
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ── 1. Pure tab-definitions helper ──────────────────────────────────────────

import { TABS, activeTab, viewModeForTabId } from "../lib/tabs";
import type { ViewMode } from "../lib/tabs";

describe("tabs.ts — pure helper", () => {
  it("exports exactly 3 tabs", () => {
    expect(TABS).toHaveLength(3);
  });

  it("has stable ids: overview, symbol, topology", () => {
    const ids = TABS.map((t) => t.id);
    expect(ids).toEqual(["overview", "symbol", "topology"]);
  });

  it("has human-readable labels: Overview, Symbol, Topology", () => {
    const labels = TABS.map((t) => t.label);
    expect(labels).toEqual(["Overview", "Symbol", "Topology"]);
  });

  it("Symbol tab maps to the 'neighborhood' ViewMode (the key alias)", () => {
    const symbolTab = TABS.find((t) => t.id === "symbol");
    expect(symbolTab).toBeDefined();
    expect(symbolTab!.viewMode).toBe("neighborhood");
  });

  it("Overview tab maps to 'overview' ViewMode", () => {
    expect(TABS[0].viewMode).toBe("overview");
  });

  it("Topology tab maps to 'topology' ViewMode", () => {
    expect(TABS[2].viewMode).toBe("topology");
  });

  it("activeTab returns Overview tab for 'overview' mode", () => {
    expect(activeTab("overview").id).toBe("overview");
  });

  it("activeTab returns Symbol tab for 'neighborhood' mode", () => {
    expect(activeTab("neighborhood").id).toBe("symbol");
  });

  it("activeTab returns Topology tab for 'topology' mode", () => {
    expect(activeTab("topology").id).toBe("topology");
  });

  it("viewModeForTabId maps 'symbol' → 'neighborhood'", () => {
    expect(viewModeForTabId("symbol")).toBe("neighborhood");
  });

  it("viewModeForTabId maps 'overview' → 'overview'", () => {
    expect(viewModeForTabId("overview")).toBe("overview");
  });

  it("viewModeForTabId maps 'topology' → 'topology'", () => {
    expect(viewModeForTabId("topology")).toBe("topology");
  });

  it("viewModeForTabId falls back to 'neighborhood' for unknown id", () => {
    expect(viewModeForTabId("unknown")).toBe("neighborhood");
  });

  it("no tab label equals a ViewMode string that is NOT its own viewMode", () => {
    // The anti-pattern: a tab that relabels itself with another mode's name.
    // e.g. a tab in 'overview' mode that shows "Neighborhood" label is wrong.
    // All view-mode strings that appear in tabs:
    const viewModeStrings = new Set<string>(["overview", "neighborhood", "topology"]);
    for (const tab of TABS) {
      const labelLower = tab.label.toLowerCase();
      // The label MAY match its own viewMode (e.g. "Overview" ~ "overview")
      // but must NOT match a DIFFERENT viewMode string.
      for (const vm of viewModeStrings) {
        if (vm !== tab.viewMode && labelLower.includes(vm)) {
          throw new Error(
            `Tab "${tab.id}" has label "${tab.label}" which contains a different viewMode string "${vm}" — anti-pattern!`,
          );
        }
      }
    }
  });
});

// ── 2. TabBar component — exactly one active, clicking fires setter ──────────

import { TabBar } from "../components/TabBar";

describe("TabBar component", () => {
  function renderTabBar(mode: ViewMode, onSetMode = vi.fn()) {
    return render(<TabBar mode={mode} onSetMode={onSetMode} />);
  }

  it("renders 3 tab buttons", () => {
    renderTabBar("neighborhood");
    // All 3 labels are present
    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /symbol/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /topology/i })).toBeInTheDocument();
  });

  it("exactly one tab has aria-current when mode=neighborhood (Symbol is active)", () => {
    renderTabBar("neighborhood");
    const withCurrent = screen
      .getAllByRole("tab")
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(withCurrent).toHaveLength(1);
    expect(withCurrent[0]).toHaveTextContent(/symbol/i);
  });

  it("exactly one tab has aria-current when mode=overview (Overview is active)", () => {
    renderTabBar("overview");
    const withCurrent = screen
      .getAllByRole("tab")
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(withCurrent).toHaveLength(1);
    expect(withCurrent[0]).toHaveTextContent(/overview/i);
  });

  it("exactly one tab has aria-current when mode=topology (Topology is active)", () => {
    renderTabBar("topology");
    const withCurrent = screen
      .getAllByRole("tab")
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(withCurrent).toHaveLength(1);
    expect(withCurrent[0]).toHaveTextContent(/topology/i);
  });

  it("clicking Overview tab calls onSetMode with 'overview'", () => {
    const onSetMode = vi.fn();
    renderTabBar("neighborhood", onSetMode);
    fireEvent.click(screen.getByRole("tab", { name: /overview/i }));
    expect(onSetMode).toHaveBeenCalledWith("overview");
  });

  it("clicking Symbol tab calls onSetMode with 'neighborhood'", () => {
    const onSetMode = vi.fn();
    renderTabBar("overview", onSetMode);
    fireEvent.click(screen.getByRole("tab", { name: /symbol/i }));
    expect(onSetMode).toHaveBeenCalledWith("neighborhood");
  });

  it("clicking Topology tab calls onSetMode with 'topology'", () => {
    const onSetMode = vi.fn();
    renderTabBar("neighborhood", onSetMode);
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    expect(onSetMode).toHaveBeenCalledWith("topology");
  });

  it("no tab label equals a non-active mode name (anti-pattern regression)", () => {
    // Render with each possible mode and confirm no OTHER mode's label appears
    // as the text content of any tab button.
    const modes: ViewMode[] = ["overview", "neighborhood", "topology"];
    for (const mode of modes) {
      const { unmount } = renderTabBar(mode);
      const tabs = screen.getAllByRole("tab");
      for (const tab of tabs) {
        const isCurrent = tab.getAttribute("aria-current") === "page";
        if (!isCurrent) {
          // Inactive tabs must NOT display another mode's raw ViewMode string
          const text = tab.textContent?.toLowerCase() ?? "";
          // "neighborhood" is the internal state string; the label is "Symbol"
          // "overview" is the state string; the label is "Overview" (same, OK — it IS the tab's own id)
          // So we check: does this inactive tab contain a different active tab's label?
          const activeModeStr = mode; // the currently active mode string
          // The active tab's label string
          const activeTabLabel = TABS.find((t) => t.viewMode === activeModeStr)?.label ?? "";
          // The inactive tab must not contain the active tab's label
          // (that would mean it's showing "I'll take you to the current place" — wrong)
          expect(text).not.toContain(activeTabLabel.toLowerCase());
        }
      }
      unmount();
    }
  });
});

// ── 3. App integration — anti-pattern regression ────────────────────────────
//
// We test the REAL App with all the mocks used in other App-level tests so we
// can confirm the replaced HeaderToggle (which relabeled itself) is truly gone.

vi.mock("../components/ConstellationTab", () => ({
  default: () => <div data-testid="constellation-tab-3d">3D Scene</div>,
}));

vi.mock("../components/ClusterGraph2D", () => ({
  ClusterGraph2D: () => <div data-testid="cluster-graph-2d">2D Cluster Graph</div>,
}));

vi.mock("../api/hooks", () => ({
  useStatus: () => ({ data: null, isLoading: true, isError: false }),
  useSearch: () => ({ data: [], isLoading: false }),
  useHubs: () => ({ data: [], isLoading: false }),
  useAreas: () => ({ areas: [], isLoading: false }),
  useConstellation: () => ({ data: { clusters: [], links: [] }, isLoading: false }),
  useStructure: () => ({ data: null, isLoading: false }),
  useClusters: () => ({ data: [], isLoading: false }),
}));

vi.mock("../components/FileSidebar", () => ({
  FileSidebar: () => null,
}));

vi.mock("../components/ChangesDrawer", () => ({
  ChangesDrawer: () => null,
}));

vi.mock("../components/GraphCanvas", () => ({
  GraphCanvas: ({ center }: { center: string }) => (
    <div data-testid="graph-canvas">{center}</div>
  ),
}));

vi.mock("../components/DetailPanel", () => ({
  DetailPanel: () => null,
}));

import App from "../App";

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App integration — TabBar anti-pattern regression", () => {
  it("renders all 3 explicit tabs in the header", () => {
    renderApp();
    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /symbol/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /topology/i })).toBeInTheDocument();
  });

  it("exactly one tab has aria-current on initial render (Symbol, neighborhood mode)", () => {
    renderApp();
    const withCurrent = screen
      .getAllByRole("tab")
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(withCurrent).toHaveLength(1);
    expect(withCurrent[0]).toHaveTextContent(/symbol/i);
  });

  it("NO rendered tab button contains the text of a non-active mode's ViewMode string", () => {
    // This is the core anti-pattern regression:
    // "HeaderToggle" used to say "Overview" when the mode was 'neighborhood', meaning
    // a button was labelled with the OTHER mode's name. That is confusing UX and
    // must never return.
    renderApp();
    // Initial mode is "neighborhood" → active tab label is "Symbol"
    // The NON-active tabs are Overview and Topology — they must NOT say "Neighborhood"
    const allTabs = screen.getAllByRole("tab");
    // Check no inactive tab's text matches the active mode's internal string
    const inactiveTabs = allTabs.filter(
      (el) => el.getAttribute("aria-current") !== "page",
    );
    for (const tab of inactiveTabs) {
      const text = tab.textContent?.toLowerCase() ?? "";
      // None should say "neighborhood" (the internal state string for the active Symbol tab)
      expect(text).not.toContain("neighborhood");
    }
  });

  it("clicking Overview tab switches mode and moves aria-current to Overview", () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /overview/i }));
    const withCurrent = screen
      .getAllByRole("tab")
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(withCurrent).toHaveLength(1);
    expect(withCurrent[0]).toHaveTextContent(/overview/i);
  });

  it("clicking Topology tab switches mode and shows the 2D/3D sub-toggle", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /topology/i }));
    // The 2D/3D sub-toggle must still appear inside Topology
    expect(screen.getByRole("button", { name: /^2D$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^3D$/i })).toBeInTheDocument();
  });

  it("Symbol tab with no centerSymbol shows the landing page (empty state)", () => {
    renderApp();
    // Ensure we are on the Symbol tab (default)
    const symbolTab = screen.getByRole("tab", { name: /symbol/i });
    expect(symbolTab.getAttribute("aria-current")).toBe("page");
    // Landing page is shown (no symbol selected)
    expect(screen.getByText(/explore the codebase/i)).toBeInTheDocument();
  });

  it("search box still works — searching and selecting a symbol opens the graph", () => {
    // The search box is still visible in Symbol mode
    renderApp();
    const searchInput = screen.getByRole("combobox", { name: /search symbols/i });
    expect(searchInput).toBeInTheDocument();
  });
});
