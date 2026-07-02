// TDD anchor for S2 Task 4: pure helpers extracted from NodeCloud and ConstellationScene.
// These are tested without WebGL/jsdom canvas — they are pure functions.
import { describe, it, expect } from "vitest";

// -- computeInstanceColor (from NodeCloud) ------------------------------------
import { computeInstanceColor } from "../components/NodeCloud";
import type { LayoutNode } from "../lib/layoutTypes";

const makeNode = (color: string, id = 0): LayoutNode => ({
  id,
  x: 0, y: 0, z: 0,
  label: "function",
  name: "n",
  file_path: null,
  size: 4,
  color,
});

describe("computeInstanceColor", () => {
  it("highlighted node exceeds 1.0 (bloom fires)", () => {
    const [r, g, b] = computeInstanceColor(makeNode("#ffffff"), true, false);
    // white node: brightness=1, boost=2.0 → values all above 1.0
    expect(r).toBeGreaterThan(1.0);
    expect(g).toBeGreaterThan(1.0);
    expect(b).toBeGreaterThan(1.0);
  });

  it("dimmed node is darkened to 0.15×", () => {
    const [r, g] = computeInstanceColor(makeNode("#ff6050"), false, true);
    // original r=1.0 → 0.15; g≈0.376 → 0.0564
    expect(r).toBeCloseTo(1.0 * 0.15, 5);
    expect(g).toBeCloseTo((0x60 / 255) * 0.15, 4);
  });

  it("normal node is returned unchanged", () => {
    const [r, g, b] = computeInstanceColor(makeNode("#1da27e"), false, false);
    // #1d = 29/255, #a2 = 162/255, #7e = 126/255
    expect(r).toBeCloseTo(29 / 255, 4);
    expect(g).toBeCloseTo(162 / 255, 4);
    expect(b).toBeCloseTo(126 / 255, 4);
  });
});

// -- computeCameraTarget + easeOutCubic (from ConstellationScene) --------------
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
