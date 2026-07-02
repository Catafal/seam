/**
 * TDD tests for the shared edge-counting module (web/src/lib/filterBarCounts.ts).
 *
 * Issue #191 — S6a: FilterBar All/None per group + per-option counts.
 *
 * The counting module is pure (no React), so tests run without rendering.
 * Counts come from the post-overlay display edge array (after hidden flags are set by
 * useGraphOverlays), matching the "counts update after impact/trace overlays" requirement.
 */

import { describe, it, expect } from "vitest";
import {
  countVisibleEdgesByKind,
  countVisibleEdgesByConfidence,
  countEdgesByField,
} from "../lib/filterBarCounts";
import type { Edge } from "@xyflow/react";

// ── Fixture helpers ────────────────────────────────────────────────────────────

/** Build a minimal RF edge with kind/confidence data and an optional hidden flag. */
function mkEdge(
  id: string,
  kind: string,
  confidence: string,
  hidden = false,
): Edge {
  return {
    id,
    source: "a",
    target: "b",
    data: { kind, confidence },
    hidden,
  };
}

// ── countVisibleEdgesByKind ────────────────────────────────────────────────────

describe("countVisibleEdgesByKind", () => {
  it("counts visible (non-hidden) edges by kind", () => {
    const edges: Edge[] = [
      mkEdge("e1", "call", "EXTRACTED", false),
      mkEdge("e2", "call", "EXTRACTED", false),
      mkEdge("e3", "import", "INFERRED", false),
      mkEdge("e4", "call", "EXTRACTED", true), // hidden — must NOT be counted
    ];
    const counts = countVisibleEdgesByKind(edges);
    expect(counts["call"]).toBe(2);
    expect(counts["import"]).toBe(1);
    expect(counts["call"] + (counts["import"] ?? 0)).toBe(3);
  });

  it("returns zero counts for kinds with only hidden edges", () => {
    const edges: Edge[] = [
      mkEdge("e1", "extends", "EXTRACTED", true),
      mkEdge("e2", "extends", "EXTRACTED", true),
    ];
    const counts = countVisibleEdgesByKind(edges);
    // The key should be absent or 0; visible count is 0
    expect(counts["extends"] ?? 0).toBe(0);
  });

  it("returns empty object for empty edge array", () => {
    expect(countVisibleEdgesByKind([])).toEqual({});
  });

  it("handles all 9 real edge kinds", () => {
    const kinds = ["call", "import", "extends", "implements", "instantiates", "holds", "reads", "writes", "uses"];
    const edges: Edge[] = kinds.map((k, i) => mkEdge(`e${i}`, k, "EXTRACTED", false));
    const counts = countVisibleEdgesByKind(edges);
    for (const k of kinds) {
      expect(counts[k]).toBe(1);
    }
  });

  it("ignores edges whose data is missing kind", () => {
    // Edges without data.kind should be skipped (defensive against bad API data).
    const edges: Edge[] = [
      { id: "e1", source: "a", target: "b", data: { confidence: "EXTRACTED" } },
    ];
    // Should not throw; result may have an empty-string key or none
    expect(() => countVisibleEdgesByKind(edges)).not.toThrow();
  });

  it("only counts visible edges — hidden edges are excluded", () => {
    const edges: Edge[] = [
      mkEdge("e1", "holds", "INFERRED", false),
      mkEdge("e2", "holds", "INFERRED", true),
      mkEdge("e3", "holds", "INFERRED", false),
    ];
    expect(countVisibleEdgesByKind(edges)["holds"]).toBe(2);
  });
});

// ── countVisibleEdgesByConfidence ──────────────────────────────────────────────

describe("countVisibleEdgesByConfidence", () => {
  it("counts visible edges by confidence tier", () => {
    const edges: Edge[] = [
      mkEdge("e1", "call", "EXTRACTED", false),
      mkEdge("e2", "call", "EXTRACTED", false),
      mkEdge("e3", "import", "INFERRED", false),
      mkEdge("e4", "call", "AMBIGUOUS", false),
      mkEdge("e5", "call", "EXTRACTED", true), // hidden — excluded
    ];
    const counts = countVisibleEdgesByConfidence(edges);
    expect(counts["EXTRACTED"]).toBe(2);
    expect(counts["INFERRED"]).toBe(1);
    expect(counts["AMBIGUOUS"]).toBe(1);
  });

  it("returns empty object for empty array", () => {
    expect(countVisibleEdgesByConfidence([])).toEqual({});
  });

  it("excludes hidden edges from confidence counts", () => {
    const edges: Edge[] = [
      mkEdge("e1", "call", "INFERRED", true),
      mkEdge("e2", "call", "INFERRED", true),
    ];
    expect(countVisibleEdgesByConfidence(edges)["INFERRED"] ?? 0).toBe(0);
  });
});

// ── countEdgesByField (generic) ────────────────────────────────────────────────

describe("countEdgesByField", () => {
  it("counts any string field on edge data", () => {
    const edges: Edge[] = [
      mkEdge("e1", "call", "EXTRACTED", false),
      mkEdge("e2", "call", "INFERRED", false),
      mkEdge("e3", "import", "EXTRACTED", false),
    ];
    const byKind = countEdgesByField(edges, "kind", false);
    expect(byKind["call"]).toBe(2);
    expect(byKind["import"]).toBe(1);

    const byConf = countEdgesByField(edges, "confidence", false);
    expect(byConf["EXTRACTED"]).toBe(2);
    expect(byConf["INFERRED"]).toBe(1);
  });

  it("respects onlyVisible=true and skips hidden edges", () => {
    const edges: Edge[] = [
      mkEdge("e1", "call", "EXTRACTED", false),
      mkEdge("e2", "call", "EXTRACTED", true),
    ];
    const visible = countEdgesByField(edges, "kind", true);
    expect(visible["call"]).toBe(1);

    const all = countEdgesByField(edges, "kind", false);
    expect(all["call"]).toBe(2);
  });

  it("returns empty object for empty array", () => {
    expect(countEdgesByField([], "kind", true)).toEqual({});
  });
});
