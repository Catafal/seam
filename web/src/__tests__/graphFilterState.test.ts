/**
 * TDD tests for the graph filter-state module (web/src/lib/graphFilterState.ts).
 *
 * Issue #192 (S6b): node-kind filter axis + session-global localStorage persistence.
 *
 * Pure-function tests — no React rendering, no network.
 * Tests cover: toggle, All/None, load/save, merge-with-defaults.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  defaultGraphFilter,
  toggleNodeKind,
  allNodeKinds,
  noneNodeKinds,
  saveGraphFilter,
  loadGraphFilter,
  mergeWithDefaults,
  ALL_NODE_KINDS,
} from "../lib/graphFilterState";
import { ALL_EDGE_KINDS, ALL_CONFIDENCES } from "../lib/edgeFilter";

// ── defaultGraphFilter ────────────────────────────────────────────────────────

describe("defaultGraphFilter", () => {
  it("enables all 6 node kinds", () => {
    const f = defaultGraphFilter();
    for (const k of ALL_NODE_KINDS) {
      expect(f.nodeKinds.has(k)).toBe(true);
    }
    expect(f.nodeKinds.size).toBe(ALL_NODE_KINDS.length);
  });

  it("enables all edge kinds", () => {
    const f = defaultGraphFilter();
    for (const k of ALL_EDGE_KINDS) {
      expect(f.kinds.has(k)).toBe(true);
    }
  });

  it("enables all 3 confidence tiers", () => {
    const f = defaultGraphFilter();
    for (const c of ALL_CONFIDENCES) {
      expect(f.confidences.has(c)).toBe(true);
    }
  });
});

// ── toggleNodeKind ────────────────────────────────────────────────────────────

describe("toggleNodeKind", () => {
  it("removes an enabled kind (toggle-off)", () => {
    const f = defaultGraphFilter();
    const next = toggleNodeKind(f, "function");
    expect(next.nodeKinds.has("function")).toBe(false);
  });

  it("adds a disabled kind (toggle-on)", () => {
    const f = noneNodeKinds(defaultGraphFilter());
    const next = toggleNodeKind(f, "function");
    expect(next.nodeKinds.has("function")).toBe(true);
  });

  it("does not mutate the original state (immutable)", () => {
    const f = defaultGraphFilter();
    toggleNodeKind(f, "function");
    // original must be unchanged
    expect(f.nodeKinds.has("function")).toBe(true);
  });

  it("leaves other node kinds unchanged", () => {
    const f = defaultGraphFilter();
    const next = toggleNodeKind(f, "class");
    expect(next.nodeKinds.has("function")).toBe(true);
    expect(next.nodeKinds.has("method")).toBe(true);
    expect(next.nodeKinds.has("interface")).toBe(true);
    expect(next.nodeKinds.has("class")).toBe(false);
  });

  it("double-toggle restores original state", () => {
    const f = defaultGraphFilter();
    const once = toggleNodeKind(f, "type");
    const twice = toggleNodeKind(once, "type");
    expect(twice.nodeKinds.has("type")).toBe(true);
  });

  it("does not affect edge kinds", () => {
    const f = defaultGraphFilter();
    const next = toggleNodeKind(f, "function");
    expect(next.kinds.size).toBe(f.kinds.size);
    expect(next.confidences.size).toBe(f.confidences.size);
  });
});

// ── allNodeKinds ──────────────────────────────────────────────────────────────

describe("allNodeKinds", () => {
  it("enables all node kinds after none", () => {
    const f = noneNodeKinds(defaultGraphFilter());
    const next = allNodeKinds(f);
    for (const k of ALL_NODE_KINDS) {
      expect(next.nodeKinds.has(k)).toBe(true);
    }
    expect(next.nodeKinds.size).toBe(ALL_NODE_KINDS.length);
  });

  it("is idempotent on already-all state", () => {
    const f = defaultGraphFilter();
    const next = allNodeKinds(f);
    expect(next.nodeKinds.size).toBe(ALL_NODE_KINDS.length);
  });

  it("does not affect edge kinds", () => {
    const f = noneNodeKinds(defaultGraphFilter());
    const next = allNodeKinds(f);
    expect(next.kinds.size).toBe(f.kinds.size);
  });
});

// ── noneNodeKinds ─────────────────────────────────────────────────────────────

describe("noneNodeKinds", () => {
  it("disables all node kinds", () => {
    const f = defaultGraphFilter();
    const next = noneNodeKinds(f);
    expect(next.nodeKinds.size).toBe(0);
  });

  it("is idempotent on already-empty state", () => {
    const f = noneNodeKinds(defaultGraphFilter());
    const next = noneNodeKinds(f);
    expect(next.nodeKinds.size).toBe(0);
  });

  it("does not affect edge kinds", () => {
    const f = defaultGraphFilter();
    const next = noneNodeKinds(f);
    expect(next.kinds.size).toBe(f.kinds.size);
    expect(next.confidences.size).toBe(f.confidences.size);
  });
});

// ── mergeWithDefaults ─────────────────────────────────────────────────────────

describe("mergeWithDefaults", () => {
  it("returns all enabled when no disabled sets given (empty persisted)", () => {
    const defaults = defaultGraphFilter();
    const result = mergeWithDefaults({}, defaults);
    expect(result.nodeKinds).toEqual(defaults.nodeKinds);
    expect(result.kinds).toEqual(defaults.kinds);
    expect(result.confidences).toEqual(defaults.confidences);
  });

  it("disables node kinds that were explicitly disabled", () => {
    const defaults = defaultGraphFilter();
    const result = mergeWithDefaults(
      { disabledNodeKinds: ["function", "class"] },
      defaults,
    );
    expect(result.nodeKinds.has("function")).toBe(false);
    expect(result.nodeKinds.has("class")).toBe(false);
    expect(result.nodeKinds.has("method")).toBe(true);
  });

  it("ignores stale disabled node kinds not in current vocabulary", () => {
    const defaults = defaultGraphFilter();
    // "widget" is not a real node kind — must be silently ignored
    const result = mergeWithDefaults({ disabledNodeKinds: ["widget"] }, defaults);
    for (const k of ALL_NODE_KINDS) {
      expect(result.nodeKinds.has(k)).toBe(true);
    }
  });

  it("disables edge kinds that were explicitly disabled", () => {
    const defaults = defaultGraphFilter();
    const result = mergeWithDefaults({ disabledEdgeKinds: ["call", "import"] }, defaults);
    expect(result.kinds.has("call")).toBe(false);
    expect(result.kinds.has("import")).toBe(false);
    expect(result.kinds.has("extends")).toBe(true);
  });

  it("disables confidence tiers that were explicitly disabled", () => {
    const defaults = defaultGraphFilter();
    const result = mergeWithDefaults({ disabledConfidences: ["INFERRED"] }, defaults);
    expect(result.confidences.has("INFERRED")).toBe(false);
    expect(result.confidences.has("EXTRACTED")).toBe(true);
  });

  it("new node kinds not in persisted disabled set default to ENABLED", () => {
    const defaults = defaultGraphFilter();
    // Only "function" was disabled; all others (including "new" ones) default on.
    const result = mergeWithDefaults({ disabledNodeKinds: ["function"] }, defaults);
    expect(result.nodeKinds.has("function")).toBe(false);
    expect(result.nodeKinds.has("class")).toBe(true);
    expect(result.nodeKinds.has("method")).toBe(true);
    expect(result.nodeKinds.has("interface")).toBe(true);
    expect(result.nodeKinds.has("type")).toBe(true);
    expect(result.nodeKinds.has("field")).toBe(true);
  });

  it("handles all fields disabled simultaneously", () => {
    const defaults = defaultGraphFilter();
    const result = mergeWithDefaults(
      {
        disabledNodeKinds: [...ALL_NODE_KINDS],
        disabledEdgeKinds: [...ALL_EDGE_KINDS],
        disabledConfidences: [...ALL_CONFIDENCES],
      },
      defaults,
    );
    expect(result.nodeKinds.size).toBe(0);
    expect(result.kinds.size).toBe(0);
    expect(result.confidences.size).toBe(0);
  });
});

// ── localStorage mock (same pattern as panelWidth.test.ts) ───────────────────

/** Build a fresh in-memory localStorage stand-in for each test. */
function makeLocalStorageMock() {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string): string | null => store[key] ?? null,
    setItem: (key: string, value: string): void => { store[key] = String(value); },
    removeItem: (key: string): void => { delete store[key]; },
    clear: (): void => { store = {}; },
  };
}

// ── saveGraphFilter / loadGraphFilter ─────────────────────────────────────────

describe("saveGraphFilter / loadGraphFilter", () => {
  let storageMock: ReturnType<typeof makeLocalStorageMock>;

  beforeEach(() => {
    storageMock = makeLocalStorageMock();
    vi.stubGlobal("localStorage", storageMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("load returns defaults when localStorage is empty", () => {
    const loaded = loadGraphFilter();
    const defaults = defaultGraphFilter();
    expect(loaded.nodeKinds.size).toBe(defaults.nodeKinds.size);
    expect(loaded.kinds.size).toBe(defaults.kinds.size);
    expect(loaded.confidences.size).toBe(defaults.confidences.size);
  });

  it("save then load round-trips a disabled-function state", () => {
    const f = toggleNodeKind(defaultGraphFilter(), "function");
    saveGraphFilter(f);
    const loaded = loadGraphFilter();
    expect(loaded.nodeKinds.has("function")).toBe(false);
    expect(loaded.nodeKinds.has("class")).toBe(true);
  });

  it("save then load round-trips a none-node-kinds state", () => {
    const f = noneNodeKinds(defaultGraphFilter());
    saveGraphFilter(f);
    const loaded = loadGraphFilter();
    expect(loaded.nodeKinds.size).toBe(0);
  });

  it("save then load round-trips disabled edge kinds", () => {
    const f = { ...defaultGraphFilter(), kinds: new Set(["call"]) }; // only call enabled
    saveGraphFilter(f);
    const loaded = loadGraphFilter();
    expect(loaded.kinds.has("call")).toBe(true);
    expect(loaded.kinds.has("import")).toBe(false);
    expect(loaded.kinds.has("extends")).toBe(false);
  });

  it("load returns defaults when localStorage has invalid JSON", () => {
    storageMock.setItem("seam-graph-filter", "not-valid-json{{");
    const loaded = loadGraphFilter();
    const defaults = defaultGraphFilter();
    expect(loaded.nodeKinds.size).toBe(defaults.nodeKinds.size);
  });

  it("save overwrites previous saved state", () => {
    const first = toggleNodeKind(defaultGraphFilter(), "class");
    saveGraphFilter(first);

    const second = toggleNodeKind(defaultGraphFilter(), "method"); // different change
    saveGraphFilter(second);

    const loaded = loadGraphFilter();
    // Second save should win — class should be enabled, method disabled
    expect(loaded.nodeKinds.has("class")).toBe(true);
    expect(loaded.nodeKinds.has("method")).toBe(false);
  });
});
