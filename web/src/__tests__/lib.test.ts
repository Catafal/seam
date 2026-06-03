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
import {
  buildConstellationGraph,
  clusterDiameter,
} from "../lib/constellationLayout";
import type {
  ImpactResponse,
  TraceResponse,
  ConstellationResponse,
} from "../api/schema-types";

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

// ── constellationLayout ─────────────────────────────────────────────────────────

describe("constellationLayout", () => {
  it("clusterDiameter scales within bounds", () => {
    expect(clusterDiameter(0, 100)).toBe(60); // min
    expect(clusterDiameter(100, 100)).toBe(160); // max
    const mid = clusterDiameter(25, 100);
    expect(mid).toBeGreaterThan(60);
    expect(mid).toBeLessThan(160);
  });

  it("builds one node per cluster and one edge per link with weight label", () => {
    const data: ConstellationResponse = {
      clusters: [
        { cluster_id: 1, label: "A", size: 10 },
        { cluster_id: 2, label: "B", size: 4 },
      ],
      links: [{ source: 1, target: 2, weight: 3 }],
    };
    const { nodes, edges } = buildConstellationGraph(data);
    expect(nodes).toHaveLength(2);
    expect(nodes[0].data.cluster_id).toBe(1);
    expect(edges).toHaveLength(1);
    expect(edges[0].source).toBe("1");
    expect(edges[0].target).toBe("2");
    expect(edges[0].label).toBe("3");
  });

  it("returns empty graph for empty clusters", () => {
    const { nodes, edges } = buildConstellationGraph({ clusters: [], links: [] });
    expect(nodes).toEqual([]);
    expect(edges).toEqual([]);
  });
});
