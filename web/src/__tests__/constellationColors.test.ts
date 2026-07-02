// TDD anchor for S2 Task 3: constellation color palette.
// Tests the EDGE_TYPE_COLORS (9 kinds) and KIND_COLORS (6 kinds) exports.
import { describe, it, expect } from "vitest";
import { EDGE_TYPE_COLORS, KIND_COLORS, CANVAS_BG } from "../lib/constellationColors";

describe("constellation colors", () => {
  it("maps every edge kind", () => {
    const kinds = [
      "call", "import", "extends", "implements", "instantiates",
      "holds", "reads", "writes", "uses",
    ];
    for (const k of kinds) {
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
