/**
 * TDD tests for end-to-end breadcrumbs (#275).
 *
 * Covers the pure `state → Crumb[]` derivation in breadcrumbs.ts — no React,
 * no rendering, just the logic that maps App navigation state to a crumb list.
 *
 * Test matrix:
 *   1. landing  → [repo]
 *   2. overview without area → [repo]
 *   3. overview with area  → [repo, area]
 *   4. neighborhood + symbol (no area) → [repo, symbol]
 *   5. neighborhood + area + symbol → [repo, area, symbol]
 *   6. neighborhood + symbol + selected (distinct) → [repo, symbol, selected]
 *   7. full chain: area + symbol + selected → [repo, area, symbol, selected]
 *   8. selectedSymbol === centerSymbol → no duplicate (same as #4)
 *   9. topology mode → [repo] only, even with area/symbol set
 *  10. handler wiring — each crumb calls the correct handler
 *  11. isCurrent on last crumb; non-last crumbs are not current
 *  12. repo crumb always calls goHome
 */

import { describe, it, expect, vi } from "vitest";
import { deriveCrumbs } from "../lib/breadcrumbs";
import type { BreadcrumbState, BreadcrumbHandlers } from "../lib/breadcrumbs";
import type { Area } from "../lib/deriveAreas";

// ── Fixtures ─────────────────────────────────────────────────────────────────

const MOCK_AREA: Area = {
  key: "seam/indexer",
  name: "indexer",
  fileCount: 5,
  symbolCount: 20,
  keySymbols: ["Parser", "db"],
  paths: ["seam/indexer/db.py", "seam/indexer/parser.py"],
};

function makeHandlers(): { handlers: BreadcrumbHandlers; mocks: { goHome: ReturnType<typeof vi.fn>; openArea: ReturnType<typeof vi.fn>; openCenterSymbol: ReturnType<typeof vi.fn> } } {
  const goHome = vi.fn();
  const openArea = vi.fn();
  const openCenterSymbol = vi.fn();
  return { handlers: { goHome, openArea, openCenterSymbol }, mocks: { goHome, openArea, openCenterSymbol } };
}

function landingState(): BreadcrumbState {
  return { mode: "neighborhood", preselectedArea: null, centerSymbol: null, selectedSymbol: null };
}

// ── Pure derivation: labels + length ─────────────────────────────────────────

describe("deriveCrumbs — label structure", () => {
  it("landing → exactly [repo]", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs(landingState(), handlers);
    expect(crumbs).toHaveLength(1);
    expect(crumbs[0].label).toBe("repo");
  });

  it("overview without preselectedArea → [repo]", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "overview", preselectedArea: null, centerSymbol: null, selectedSymbol: null }, handlers);
    expect(crumbs).toHaveLength(1);
    expect(crumbs[0].label).toBe("repo");
  });

  it("overview with preselectedArea → [repo, area.name]", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "overview", preselectedArea: MOCK_AREA, centerSymbol: null, selectedSymbol: null }, handlers);
    expect(crumbs).toHaveLength(2);
    expect(crumbs[0].label).toBe("repo");
    expect(crumbs[1].label).toBe("indexer");
  });

  it("neighborhood + centerSymbol (no area, no selected) → [repo, symbol]", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: null, centerSymbol: "MyClass.parse", selectedSymbol: null }, handlers);
    expect(crumbs).toHaveLength(2);
    expect(crumbs[1].label).toBe("MyClass.parse");
  });

  it("neighborhood + area + symbol (no selected) → [repo, area, symbol]", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: MOCK_AREA, centerSymbol: "Parser", selectedSymbol: null }, handlers);
    expect(crumbs).toHaveLength(3);
    expect(crumbs[1].label).toBe("indexer");
    expect(crumbs[2].label).toBe("Parser");
  });

  it("neighborhood + symbol + selected (distinct) → [repo, symbol, selected]", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: null, centerSymbol: "MyClass", selectedSymbol: "MyClass.method" }, handlers);
    expect(crumbs).toHaveLength(3);
    expect(crumbs[1].label).toBe("MyClass");
    expect(crumbs[2].label).toBe("MyClass.method");
  });

  it("area + symbol + selected → full four-crumb chain", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: MOCK_AREA, centerSymbol: "Parser", selectedSymbol: "Parser.parse" }, handlers);
    expect(crumbs).toHaveLength(4);
    expect(crumbs[0].label).toBe("repo");
    expect(crumbs[1].label).toBe("indexer");
    expect(crumbs[2].label).toBe("Parser");
    expect(crumbs[3].label).toBe("Parser.parse");
  });

  it("selectedSymbol === centerSymbol → no duplicate (same as symbol-only trail)", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: null, centerSymbol: "MyClass", selectedSymbol: "MyClass" }, handlers);
    // No extra crumb — identical names are not both shown
    expect(crumbs).toHaveLength(2);
    expect(crumbs[1].label).toBe("MyClass");
  });

  it("topology mode → [repo] only, even when area + symbol + selected are all set", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "topology", preselectedArea: MOCK_AREA, centerSymbol: "Parser", selectedSymbol: "Parser.parse" }, handlers);
    expect(crumbs).toHaveLength(1);
    expect(crumbs[0].label).toBe("repo");
  });
});

// ── isCurrent flag ────────────────────────────────────────────────────────────

describe("deriveCrumbs — isCurrent flag", () => {
  it("single crumb (landing) has isCurrent=true", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs(landingState(), handlers);
    expect(crumbs[0].isCurrent).toBe(true);
  });

  it("last crumb in a multi-crumb trail is isCurrent", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: MOCK_AREA, centerSymbol: "Parser", selectedSymbol: "Parser.parse" }, handlers);
    expect(crumbs[crumbs.length - 1].isCurrent).toBe(true);
  });

  it("all non-last crumbs have isCurrent=false", () => {
    const { handlers } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: MOCK_AREA, centerSymbol: "Parser", selectedSymbol: "Parser.parse" }, handlers);
    const nonLast = crumbs.slice(0, -1);
    for (const c of nonLast) {
      expect(c.isCurrent).toBe(false);
    }
  });
});

// ── Handler wiring ────────────────────────────────────────────────────────────

describe("deriveCrumbs — handler wiring", () => {
  it("repo crumb always calls goHome (landing)", () => {
    const { handlers, mocks } = makeHandlers();
    const crumbs = deriveCrumbs(landingState(), handlers);
    crumbs[0].onClick();
    expect(mocks.goHome).toHaveBeenCalledOnce();
  });

  it("repo crumb calls goHome when other crumbs exist too", () => {
    const { handlers, mocks } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: null, centerSymbol: "X", selectedSymbol: null }, handlers);
    crumbs[0].onClick();
    expect(mocks.goHome).toHaveBeenCalledOnce();
  });

  it("area crumb calls openArea with the exact area object", () => {
    const { handlers, mocks } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "overview", preselectedArea: MOCK_AREA, centerSymbol: null, selectedSymbol: null }, handlers);
    crumbs[1].onClick();
    expect(mocks.openArea).toHaveBeenCalledOnce();
    expect(mocks.openArea).toHaveBeenCalledWith(MOCK_AREA);
  });

  it("area crumb in neighborhood mode also calls openArea", () => {
    const { handlers, mocks } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: MOCK_AREA, centerSymbol: "Parser", selectedSymbol: null }, handlers);
    // crumbs: [repo, area, symbol]  — area is index 1
    crumbs[1].onClick();
    expect(mocks.openArea).toHaveBeenCalledWith(MOCK_AREA);
  });

  it("symbol crumb calls openCenterSymbol when a selected node is deeper", () => {
    const { handlers, mocks } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: null, centerSymbol: "MyClass", selectedSymbol: "MyClass.method" }, handlers);
    // crumbs: [repo, symbol, selected] — symbol is index 1
    crumbs[1].onClick();
    expect(mocks.openCenterSymbol).toHaveBeenCalledOnce();
  });

  it("symbol crumb on the two-crumb trail also calls openCenterSymbol", () => {
    // Even when symbol is the current/last crumb, its onClick is openCenterSymbol
    // (navigating to self is a no-op for the user, but the handler is consistent)
    const { handlers, mocks } = makeHandlers();
    const crumbs = deriveCrumbs({ mode: "neighborhood", preselectedArea: null, centerSymbol: "MyClass", selectedSymbol: null }, handlers);
    // crumbs: [repo, symbol] — symbol is index 1 and is current
    crumbs[1].onClick();
    expect(mocks.openCenterSymbol).toHaveBeenCalledOnce();
  });
});
