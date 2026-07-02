/**
 * TDD tests for FilterPanel pure helper: countByField.
 *
 * S5 (issue #173): FilterPanel exposes countByField as a named export so it
 * can be unit-tested without React/WebGL.
 */
import { describe, it, expect } from "vitest";
import { countByField } from "../components/FilterPanel";
import type { LayoutNode } from "../lib/layoutTypes";

const mk = (kind: string): LayoutNode => ({
  id: 0,
  x: 0,
  y: 0,
  z: 0,
  label: kind,
  name: "n",
  file_path: null,
  size: 4,
  color: "#fff",
});

describe("countByField", () => {
  it("counts nodes by label", () => {
    const counts = countByField([mk("function"), mk("function"), mk("class")], "label");
    expect(counts).toEqual({ function: 2, class: 1 });
  });

  it("returns empty object for empty array", () => {
    expect(countByField([], "label")).toEqual({});
  });

  it("handles a single node", () => {
    expect(countByField([mk("method")], "label")).toEqual({ method: 1 });
  });

  it("counts all 6 node kinds independently", () => {
    const nodes = ["function", "class", "method", "interface", "type", "field"].map(mk);
    const counts = countByField(nodes, "label");
    for (const k of ["function", "class", "method", "interface", "type", "field"]) {
      expect(counts[k]).toBe(1);
    }
  });
});
