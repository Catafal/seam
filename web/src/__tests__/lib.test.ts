/**
 * Unit tests for the pure lib helpers behind the Phase 2 overlays.
 * Grouped by module; each helper is tested in isolation (no React, no fetch).
 */

import { describe, it, expect } from "vitest";
import {
  tierColor,
  tierLabel,
  tierRank,
  moreSevereTier,
  RISK_TIERS,
} from "../lib/riskTier";
import {
  isEdgeVisible,
  toggleFilterValue,
  defaultEdgeFilter,
} from "../lib/edgeFilter";
import { impactTierMap } from "../lib/impactOverlay";
import { tracePathHighlight, edgeKey } from "../lib/tracePath";
import type { ImpactResponse, TraceResponse } from "../api/schema-types";

// ── riskTier ──────────────────────────────────────────────────────────────────

describe("riskTier", () => {
  it("maps each known tier to a colour", () => {
    for (const t of RISK_TIERS) {
      expect(tierColor(t)).toMatch(/^#/);
    }
  });

  it("returns null for unknown/empty tier", () => {
    expect(tierColor(null)).toBeNull();
    expect(tierColor("NOPE")).toBeNull();
  });

  it("labels fall back to the raw string for unknown tiers", () => {
    expect(tierLabel("WILL_BREAK")).toContain("Will break");
    expect(tierLabel("CUSTOM")).toBe("CUSTOM");
  });

  it("ranks WILL_BREAK as most severe and unknown as least", () => {
    expect(tierRank("WILL_BREAK")).toBe(0);
    expect(tierRank("MAY_NEED_TESTING")).toBe(2);
    expect(tierRank("UNKNOWN")).toBe(RISK_TIERS.length);
  });

  it("moreSevereTier keeps the most severe of two tiers", () => {
    expect(moreSevereTier("LIKELY_AFFECTED", "WILL_BREAK")).toBe("WILL_BREAK");
    expect(moreSevereTier("MAY_NEED_TESTING", "LIKELY_AFFECTED")).toBe("LIKELY_AFFECTED");
  });
});

// ── edgeFilter ──────────────────────────────────────────────────────────────────

describe("edgeFilter", () => {
  it("default filter shows all kinds and confidences", () => {
    const f = defaultEdgeFilter();
    expect(isEdgeVisible({ kind: "call", confidence: "EXTRACTED" }, f)).toBe(true);
    expect(isEdgeVisible({ kind: "import", confidence: "INFERRED" }, f)).toBe(true);
  });

  it("hides an edge when its kind is toggled off", () => {
    const f = toggleFilterValue(defaultEdgeFilter(), "kinds", "import");
    expect(isEdgeVisible({ kind: "import", confidence: "EXTRACTED" }, f)).toBe(false);
    expect(isEdgeVisible({ kind: "call", confidence: "EXTRACTED" }, f)).toBe(true);
  });

  it("hides an edge when its confidence is toggled off", () => {
    const f = toggleFilterValue(defaultEdgeFilter(), "confidences", "INFERRED");
    expect(isEdgeVisible({ kind: "call", confidence: "INFERRED" }, f)).toBe(false);
  });

  it("toggle is immutable — original state unchanged", () => {
    const f = defaultEdgeFilter();
    toggleFilterValue(f, "kinds", "call");
    expect(f.kinds.has("call")).toBe(true);
  });

  it("toggling a value twice restores it", () => {
    let f = defaultEdgeFilter();
    f = toggleFilterValue(f, "kinds", "call");
    expect(f.kinds.has("call")).toBe(false);
    f = toggleFilterValue(f, "kinds", "call");
    expect(f.kinds.has("call")).toBe(true);
  });
});

// ── impactOverlay ───────────────────────────────────────────────────────────────

describe("impactTierMap", () => {
  const impact: ImpactResponse = {
    found: true,
    target: "x",
    risk_summary: {},
    upstream: {
      WILL_BREAK: [{ name: "a", distance: 1, confidence: "EXTRACTED", tier: "WILL_BREAK", file: null, is_test: false }],
    },
    downstream: {
      MAY_NEED_TESTING: [
        { name: "a", distance: 3, confidence: "INFERRED", tier: "MAY_NEED_TESTING", file: null, is_test: false },
        { name: "b", distance: 3, confidence: "INFERRED", tier: "MAY_NEED_TESTING", file: null, is_test: false },
      ],
    },
    truncated: null,
  };

  it("keeps the most severe tier when a name appears in multiple directions", () => {
    const map = impactTierMap(impact);
    expect(map.get("a")).toBe("WILL_BREAK"); // upstream WILL_BREAK beats downstream MAY_NEED_TESTING
    expect(map.get("b")).toBe("MAY_NEED_TESTING");
  });

  it("returns empty map for undefined", () => {
    expect(impactTierMap(undefined).size).toBe(0);
  });
});

// ── tracePath ─────────────────────────────────────────────────────────────────

describe("tracePathHighlight", () => {
  it("collects node names + edge keys from the shortest path", () => {
    const trace: TraceResponse = {
      found: true,
      source: "a",
      target: "c",
      paths: [
        [
          { from_name: "a", to_name: "b", kind: "call", confidence: "EXTRACTED" },
          { from_name: "b", to_name: "c", kind: "call", confidence: "EXTRACTED" },
        ],
        // a second, longer path that must be ignored
        [{ from_name: "a", to_name: "c", kind: "call", confidence: "INFERRED" }],
      ],
    };
    const h = tracePathHighlight(trace);
    expect(h.active).toBe(true);
    expect([...h.nodeNames].sort()).toEqual(["a", "b", "c"]);
    expect(h.edgeKeys.has(edgeKey("a", "b"))).toBe(true);
    expect(h.edgeKeys.has(edgeKey("b", "c"))).toBe(true);
    // the longer path's a->c edge must NOT be highlighted
    expect(h.edgeKeys.has(edgeKey("a", "c"))).toBe(false);
  });

  it("is inactive when not found", () => {
    const trace: TraceResponse = { found: false, source: "a", target: "c", paths: [] };
    expect(tracePathHighlight(trace).active).toBe(false);
  });
});

// ── buildTree ─────────────────────────────────────────────────────────────────

import { buildTree } from "../lib/buildTree";
import { squarify, type Rect } from "../lib/treemapLayout";
import type { StructureSymbol } from "../api/schema-types";

describe("buildTree", () => {
  const symbols: StructureSymbol[] = [
    { path: "seam/indexer/db.py", name: "init_db", kind: "function", line: 1, qualified_name: "init_db" },
    { path: "seam/indexer/db.py", name: "upsert_file", kind: "function", line: 9, qualified_name: "upsert_file" },
    { path: "seam/analysis/clustering.py", name: "Louvain", kind: "class", line: 1, qualified_name: "Louvain" },
    { path: "seam/analysis/clustering.py", name: "detect", kind: "method", line: 5, qualified_name: "Louvain.detect" },
  ];

  it("nests folders → files → symbols", () => {
    const root = buildTree(symbols);
    const seam = root.children.find((c) => c.name === "seam")!;
    expect(seam.nodeKind).toBe("dir");
    const indexer = seam.children.find((c) => c.name === "indexer")!;
    const db = indexer.children.find((c) => c.name === "db.py")!;
    expect(db.nodeKind).toBe("file");
    expect(db.children.map((c) => c.name).sort()).toEqual(["init_db", "upsert_file"]);
  });

  it("nests methods under their class", () => {
    const root = buildTree(symbols);
    const clustering = root.children[0].children
      .find((c) => c.name === "analysis")!
      .children.find((c) => c.name === "clustering.py")!;
    const louvain = clustering.children.find((c) => c.name === "Louvain")!;
    expect(louvain.nodeKind).toBe("class");
    expect(louvain.children.map((c) => c.name)).toEqual(["detect"]);
  });

  it("rolls up counts (total symbols beneath)", () => {
    const root = buildTree(symbols);
    expect(root.count).toBe(4);
  });

  it("returns an empty root for no symbols", () => {
    const root = buildTree([]);
    expect(root.children).toEqual([]);
    expect(root.count).toBe(0);
  });
});

// ── squarify (treemap layout) ───────────────────────────────────────────────────

describe("squarify", () => {
  const rect: Rect = { x: 0, y: 0, w: 100, h: 100 };

  it("places every positive item within bounds", () => {
    const placed = squarify(
      [{ value: 6, node: "a" }, { value: 3, node: "b" }, { value: 1, node: "c" }],
      rect,
    );
    expect(placed).toHaveLength(3);
    for (const p of placed) {
      expect(p.rect.x).toBeGreaterThanOrEqual(-0.01);
      expect(p.rect.y).toBeGreaterThanOrEqual(-0.01);
      expect(p.rect.x + p.rect.w).toBeLessThanOrEqual(100.01);
      expect(p.rect.y + p.rect.h).toBeLessThanOrEqual(100.01);
    }
  });

  it("total placed area approximates the container area", () => {
    const placed = squarify([{ value: 5, node: "a" }, { value: 5, node: "b" }], rect);
    const area = placed.reduce((s, p) => s + p.rect.w * p.rect.h, 0);
    expect(area).toBeGreaterThan(9900);
  });

  it("larger value → larger area", () => {
    const placed = squarify([{ value: 9, node: "big" }, { value: 1, node: "small" }], rect);
    const big = placed.find((p) => p.node === "big")!;
    const small = placed.find((p) => p.node === "small")!;
    expect(big.rect.w * big.rect.h).toBeGreaterThan(small.rect.w * small.rect.h);
  });

  it("drops zero/negative values and handles empty", () => {
    expect(squarify([{ value: 0, node: "z" }], rect)).toEqual([]);
    expect(squarify([], rect)).toEqual([]);
  });
});
