// TDD anchor for S2 Task 3: constellation color palette.
// Tests the EDGE_TYPE_COLORS and KIND_COLORS exports.
import { describe, it, expect } from "vitest";
import { EDGE_TYPE_COLORS, KIND_COLORS, CANVAS_BG } from "../lib/constellationColors";
import { ALL_EDGE_KINDS } from "../lib/edgeFilter";

describe("constellation colors", () => {
  it("maps every edge kind", () => {
    for (const k of ALL_EDGE_KINDS) {
      expect(EDGE_TYPE_COLORS[k], `missing kind: ${k}`).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("call edge is seafoam teal #1DA27E", () => {
    expect(EDGE_TYPE_COLORS.call).toBe("#1DA27E");
  });

  it("maps every node kind", () => {
    const kinds = ["function", "class", "method", "interface", "type", "field"];
    for (const k of kinds) {
      expect(KIND_COLORS[k], `missing kind: ${k}`).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("CANVAS_BG is the teal-void color", () => {
    expect(CANVAS_BG).toBe("#04100f");
  });
});
