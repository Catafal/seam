/**
 * TDD tests for web/src/lib/edgeStyle.ts.
 *
 * Tests the confidence → edge style mapping for GraphCanvas.
 * This is a pure function so tests are deterministic and fast.
 *
 * Confidence values (from the API schema):
 *   EXTRACTED = solid line (highest confidence, direct import evidence)
 *   INFERRED  = dotted line (heuristic, name-count based)
 *   AMBIGUOUS = dashed line (multiple possible targets, unresolved)
 *
 * The spec is explicit: EXTRACTED=solid, AMBIGUOUS=dashed, INFERRED=dotted.
 */

import { getEdgeStyle, CONFIDENCE_STYLES } from "../lib/edgeStyle";

describe("getEdgeStyle", () => {
  it("returns solid style for EXTRACTED confidence", () => {
    const style = getEdgeStyle("EXTRACTED");
    // Solid = no strokeDasharray (or explicit 'none')
    expect(style.strokeDasharray).toBeUndefined();
    expect(style.stroke).toBeDefined();
  });

  it("returns dashed style for AMBIGUOUS confidence", () => {
    const style = getEdgeStyle("AMBIGUOUS");
    // Dashed: a dash-gap pattern like "8 4"
    expect(style.strokeDasharray).toBeDefined();
    expect(style.strokeDasharray).toMatch(/^\d/); // starts with a number
  });

  it("returns dotted style for INFERRED confidence", () => {
    const style = getEdgeStyle("INFERRED");
    // Dotted: a tight pattern like "2 4"
    expect(style.strokeDasharray).toBeDefined();
    expect(style.strokeDasharray).toMatch(/^\d/);
    // Dotted pattern must differ from dashed
    expect(style.strokeDasharray).not.toBe(getEdgeStyle("AMBIGUOUS").strokeDasharray);
  });

  it("falls back to INFERRED style for unknown confidence values", () => {
    // Future-proof: unknown confidence strings degrade gracefully to dotted
    const style = getEdgeStyle("UNKNOWN_VALUE");
    const inferredStyle = getEdgeStyle("INFERRED");
    expect(style.strokeDasharray).toBe(inferredStyle.strokeDasharray);
  });

  it("all styles include a stroke color", () => {
    for (const confidence of ["EXTRACTED", "AMBIGUOUS", "INFERRED"] as const) {
      const style = getEdgeStyle(confidence);
      expect(style.stroke).toBeTruthy();
    }
  });
});

describe("CONFIDENCE_STYLES constant map", () => {
  it("contains entries for EXTRACTED, AMBIGUOUS, INFERRED", () => {
    expect(CONFIDENCE_STYLES).toHaveProperty("EXTRACTED");
    expect(CONFIDENCE_STYLES).toHaveProperty("AMBIGUOUS");
    expect(CONFIDENCE_STYLES).toHaveProperty("INFERRED");
  });

  it("EXTRACTED has no strokeDasharray (solid line)", () => {
    expect(CONFIDENCE_STYLES.EXTRACTED.strokeDasharray).toBeUndefined();
  });

  it("AMBIGUOUS and INFERRED have different dash patterns", () => {
    expect(CONFIDENCE_STYLES.AMBIGUOUS.strokeDasharray).not.toBe(
      CONFIDENCE_STYLES.INFERRED.strokeDasharray,
    );
  });
});
