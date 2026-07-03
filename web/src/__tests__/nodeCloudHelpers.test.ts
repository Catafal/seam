/**
 * TDD anchor for NodeCloud pure helpers.
 *
 * S2 (#261): tests updated so computeInstanceColor derives color from node.label
 * (via KIND_COLORS) rather than node.color (stellar/degree scale). node.color is
 * now irrelevant to hue; degree drives size (in the component) and brightness
 * (via the existing boost/dim logic applied to the kind color).
 *
 * Tests for computeCameraTarget + easeOutCubic (ConstellationScene) are also here.
 */
import { describe, it, expect } from "vitest";

// ── kindColor (new pure helper, exported from NodeCloud) ──────────────────────
import { kindColor, computeInstanceColor } from "../components/NodeCloud";
import { KIND_COLORS, DEFAULT_KIND_COLOR } from "../lib/constellationColors";
import type { LayoutNode } from "../lib/layoutTypes";

describe("kindColor", () => {
  it("returns KIND_COLORS.function for 'function'", () => {
    expect(kindColor("function")).toBe(KIND_COLORS["function"]);
  });

  it("returns KIND_COLORS.class for 'class'", () => {
    expect(kindColor("class")).toBe(KIND_COLORS["class"]);
  });

  it("returns KIND_COLORS.method for 'method'", () => {
    expect(kindColor("method")).toBe(KIND_COLORS["method"]);
  });

  it("returns KIND_COLORS.interface for 'interface'", () => {
    expect(kindColor("interface")).toBe(KIND_COLORS["interface"]);
  });

  it("returns KIND_COLORS.type for 'type'", () => {
    expect(kindColor("type")).toBe(KIND_COLORS["type"]);
  });

  it("returns KIND_COLORS.field for 'field'", () => {
    expect(kindColor("field")).toBe(KIND_COLORS["field"]);
  });

  it("returns DEFAULT_KIND_COLOR for unknown kind", () => {
    expect(kindColor("unknown_kind")).toBe(DEFAULT_KIND_COLOR);
  });

  it("returns DEFAULT_KIND_COLOR for empty string", () => {
    expect(kindColor("")).toBe(DEFAULT_KIND_COLOR);
  });

  // Single source of truth: kindColor must agree with KIND_COLORS for every
  // rendered kind. If the legend shows a different color than the node, that's a bug.
  it("is the same source of truth as the legend (KIND_COLORS)", () => {
    for (const k of ["function", "class", "method", "interface", "type", "field"]) {
      expect(kindColor(k)).toBe(KIND_COLORS[k]);
    }
  });
});

// ── computeInstanceColor (now uses kind, not node.color) ─────────────────────

/**
 * Build a minimal LayoutNode for testing. node.color is intentionally set to
 * a garbage value to prove computeInstanceColor no longer reads it — the kind
 * (label) is the sole source of hue.
 */
const makeNode = (kind: string, id = 0): LayoutNode => ({
  id,
  x: 0, y: 0, z: 0,
  label: kind,
  name: "n",
  file_path: null,
  size: 4,
  // deliberately NOT the kind color — if computeInstanceColor reads this instead
  // of KIND_COLORS[label], the color assertions below will fail.
  color: "#000000",
});

describe("computeInstanceColor — kind-based color", () => {
  it("uses kind color (not node.color) for a normal function node", () => {
    const node = makeNode("function");
    const [r, g, b] = computeInstanceColor(node, false, false);
    // KIND_COLORS.function = "#06b6d4" (cyan)
    const expected = KIND_COLORS["function"];
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(expected)!;
    expect(r).toBeCloseTo(parseInt(m[1], 16) / 255, 4);
    expect(g).toBeCloseTo(parseInt(m[2], 16) / 255, 4);
    expect(b).toBeCloseTo(parseInt(m[3], 16) / 255, 4);
  });

  it("uses kind color for a class node", () => {
    const node = makeNode("class");
    const [r, g, b] = computeInstanceColor(node, false, false);
    // KIND_COLORS.class = "#a855f7" (purple)
    const expected = KIND_COLORS["class"];
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(expected)!;
    expect(r).toBeCloseTo(parseInt(m[1], 16) / 255, 4);
    expect(g).toBeCloseTo(parseInt(m[2], 16) / 255, 4);
    expect(b).toBeCloseTo(parseInt(m[3], 16) / 255, 4);
  });

  it("uses DEFAULT_KIND_COLOR for an unknown kind", () => {
    const node = makeNode("widget");
    const [r, g, b] = computeInstanceColor(node, false, false);
    const expected = DEFAULT_KIND_COLOR;
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(expected)!;
    expect(r).toBeCloseTo(parseInt(m[1], 16) / 255, 4);
    expect(g).toBeCloseTo(parseInt(m[2], 16) / 255, 4);
    expect(b).toBeCloseTo(parseInt(m[3], 16) / 255, 4);
  });
});

describe("computeInstanceColor — highlight/dim on kind colors", () => {
  it("highlighted node exceeds 1.0 on at least one channel (bloom fires)", () => {
    // Use "class" (purple = #a855f7): high r and b channels so boosted value > 1.
    const node = makeNode("class");
    const [r, , b] = computeInstanceColor(node, true, false);
    expect(r + b).toBeGreaterThan(2.0); // at least one of r/b well above 1
  });

  it("dimmed node is darkened to 0.15× of kind color", () => {
    const node = makeNode("function"); // KIND_COLORS.function = "#06b6d4"
    const [r, g, b] = computeInstanceColor(node, false, true);
    const base = KIND_COLORS["function"];
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(base)!;
    const br = parseInt(m[1], 16) / 255;
    const bg = parseInt(m[2], 16) / 255;
    const bb = parseInt(m[3], 16) / 255;
    expect(r).toBeCloseTo(br * 0.15, 4);
    expect(g).toBeCloseTo(bg * 0.15, 4);
    expect(b).toBeCloseTo(bb * 0.15, 4);
  });

  it("normal node returns kind color unchanged", () => {
    const node = makeNode("method"); // KIND_COLORS.method = "#1DA27E"
    const [r, g, b] = computeInstanceColor(node, false, false);
    const base = KIND_COLORS["method"];
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(base)!;
    expect(r).toBeCloseTo(parseInt(m[1], 16) / 255, 4);
    expect(g).toBeCloseTo(parseInt(m[2], 16) / 255, 4);
    expect(b).toBeCloseTo(parseInt(m[3], 16) / 255, 4);
  });
});

// ── computeCameraTarget + easeOutCubic (from ConstellationScene) ──────────────
import { computeCameraTarget, easeOutCubic } from "../components/ConstellationScene";

const n = (id: number, x: number, y: number, z: number): LayoutNode => ({
  id, x, y, z, label: "function", name: `n${id}`, file_path: null, size: 4, color: "#fff",
});

describe("computeCameraTarget", () => {
  it("centers on the highlighted subset", () => {
    const nodes = [n(0, 0, 0, 0), n(1, 100, 0, 0), n(2, -100, 0, 0)];
    const t = computeCameraTarget(nodes, new Set([1, 2]));
    // centroid of node 1 (100,0,0) and node 2 (-100,0,0) → (0,0,0)
    expect(t).not.toBeNull();
    expect(t!.lookAt).toEqual([0, 0, 0]);
    // camera should be behind (positive z from centroid)
    expect(t!.position[2]).toBeGreaterThan(0);
  });

  it("returns null for empty set", () => {
    expect(computeCameraTarget([], new Set())).toBeNull();
  });

  it("returns null for empty nodes with non-empty set", () => {
    expect(computeCameraTarget([], new Set([1, 2]))).toBeNull();
  });
});

describe("easeOutCubic", () => {
  it("0 → 0, 1 → 1", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
  });

  it("is monotonically increasing on [0,1]", () => {
    const vals = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1].map(easeOutCubic);
    for (let i = 1; i < vals.length; i++) {
      expect(vals[i]).toBeGreaterThan(vals[i - 1]);
    }
  });

  it("accelerates fast at the start (concave down)", () => {
    // At p=0.5, ease-out should be above the linear midpoint 0.5
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5);
  });

  it("matches the formula 1 - (1-p)^3", () => {
    const p = 0.3;
    expect(easeOutCubic(p)).toBeCloseTo(1 - Math.pow(1 - p, 3), 10);
  });
});
