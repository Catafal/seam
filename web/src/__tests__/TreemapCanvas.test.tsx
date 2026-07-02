/**
 * Component tests for TreemapCanvas — the B3 degree-signal rendered surface
 * (stories 7 & 8). Prior to this file there was NO TreemapCanvas component test,
 * so the DegreeLegend wiring (shown when maxDegree > 0, hidden when everything is
 * zero-degree) and the degree pipeline reaching the placed cells were verified only
 * indirectly. These tests assert external behavior directly:
 *
 *   1. Legend renders with the correct max when the level has non-zero degree.
 *   2. Legend is absent when every node is zero-degree (clean empty state).
 *   3. The degree pipeline reaches the cells (hottest cell's title carries its
 *      fan-in degree — the same node.degree fed to degreeColor for the color ramp).
 *
 * Strategy: mock ../api/hooks useStructure to return fixed StructureSymbol rows,
 * and provide a ResizeObserver stub that reports real dimensions so squarify places
 * cells (jsdom has no ResizeObserver).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { StructureSymbol } from "../api/schema-types";

// Mock the hooks module: TreemapCanvas only consumes useStructure from it.
const useStructureMock = vi.fn();
vi.mock("../api/hooks", () => ({
  useStructure: () => useStructureMock(),
}));

// Import AFTER the mock is registered.
import { TreemapCanvas } from "../components/TreemapCanvas";

function sym(
  path: string,
  name: string,
  degree: number,
  kind = "function",
  qualified_name: string | null = null,
): StructureSymbol {
  return { path, name, kind, line: 1, qualified_name: qualified_name ?? name, degree };
}

/**
 * ResizeObserver stub that fires its callback with a fixed contentRect on observe,
 * so TreemapCanvas's `size` becomes non-zero and squarify actually places cells.
 */
function installFiringResizeObserver(w = 600, h = 400) {
  globalThis.ResizeObserver = class {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe() {
      this.cb(
        [{ contentRect: { width: w, height: h } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

describe("TreemapCanvas — degree legend (stories 7 & 8)", () => {
  beforeEach(() => {
    installFiringResizeObserver();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the degree legend with the correct max when a level has fan-in", () => {
    // Two root-level files: one hot (rolled-up degree 12), one cold (0). The visible
    // level's maxDegree is 12, so the legend shows and reports "high fan-in (12)".
    useStructureMock.mockReturnValue({
      data: [sym("hot.py", "a", 12), sym("cold.py", "b", 0)],
      isLoading: false,
    });

    render(<TreemapCanvas onSelectSymbol={() => {}} />);

    const legend = screen.getByTestId("degree-legend");
    expect(legend).toBeInTheDocument();
    expect(legend).toHaveTextContent("high fan-in (12)");
  });

  it("hides the legend when every node is zero-degree", () => {
    useStructureMock.mockReturnValue({
      data: [sym("iso1.py", "x", 0), sym("iso2.py", "y", 0)],
      isLoading: false,
    });

    render(<TreemapCanvas onSelectSymbol={() => {}} />);

    expect(screen.queryByTestId("degree-legend")).toBeNull();
  });

  it("wires node.degree through to the placed cell (title carries the fan-in degree)", () => {
    useStructureMock.mockReturnValue({
      data: [sym("hot.py", "a", 12), sym("cold.py", "b", 0)],
      isLoading: false,
    });

    render(<TreemapCanvas onSelectSymbol={() => {}} />);

    // The hottest file cell carries its rolled-up degree in the tooltip — the same
    // node.degree value passed to degreeColor(node.degree, maxDegree) for the ramp.
    const hot = screen.getByTitle(/hot\.py — fan-in degree: 12/);
    expect(hot).toBeInTheDocument();
    // And the zero-degree file is still rendered (never hidden) — story 9 at render.
    expect(screen.getByTitle(/cold\.py — fan-in degree: 0/)).toBeInTheDocument();
  });
});
