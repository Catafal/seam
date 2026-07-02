/**
 * Unit tests for degreeColor.ts — the sequential degree-to-color ramp.
 * TDD: written BEFORE the implementation (should fail on first run).
 *
 * Contract:
 *   degreeColor(degree, maxDegree) → hex string
 *   - maxDegree === 0 → return the cool floor (no divide-by-zero)
 *   - degree === 0    → returns the coolest color
 *   - degree === maxDegree → returns the hottest color (ceiling)
 *   - monotonic: degreeColor(a, max) is "at least as hot as" degreeColor(b, max)
 *     when a >= b (measured by a simple brightness proxy)
 *   - clamping: degree > maxDegree returns same as degree === maxDegree
 *   - always returns a valid hex string "#rrggbb"
 */

import { describe, it, expect } from "vitest";
import { degreeColor } from "../lib/degreeColor";

// ── helpers ───────────────────────────────────────────────────────────────────

/** Parse "#rrggbb" → [r, g, b] (0-255). */
function parseHex(hex: string): [number, number, number] {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/**
 * Simple brightness proxy: weighted luminance (same as CSS color-mix perception).
 * Monotonic test: a "hotter" color should have higher or equal luminance.
 */
function brightness([r, g, b]: [number, number, number]): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe("degreeColor", () => {
  it("returns a valid hex color string", () => {
    expect(degreeColor(0, 10)).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(degreeColor(5, 10)).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(degreeColor(10, 10)).toMatch(/^#[0-9a-fA-F]{6}$/);
  });

  it("guard: maxDegree === 0 returns the cool floor without throwing", () => {
    // Must not throw (no divide-by-zero). Returns a valid hex string.
    expect(() => degreeColor(0, 0)).not.toThrow();
    const result = degreeColor(0, 0);
    expect(result).toMatch(/^#[0-9a-fA-F]{6}$/);
  });

  it("guard: maxDegree === 0 returns the same value as degree === 0 for normal max", () => {
    // Both should be the floor color.
    const floor = degreeColor(0, 100);
    const guard = degreeColor(0, 0);
    expect(guard).toBe(floor);
  });

  it("degree === 0 returns the coolest color", () => {
    const cool = degreeColor(0, 10);
    const warm = degreeColor(1, 10);
    // The cool floor should not be brighter than a degree-1 cell on any ramp
    // (asserting it is the MINIMUM, not necessarily strictly less)
    expect(brightness(parseHex(cool))).toBeLessThanOrEqual(brightness(parseHex(warm)));
  });

  it("degree === maxDegree returns the hottest color", () => {
    const hot = degreeColor(10, 10);
    const cool = degreeColor(0, 10);
    // Hottest should be brighter (or at least as bright) as the floor
    expect(brightness(parseHex(hot))).toBeGreaterThanOrEqual(brightness(parseHex(cool)));
  });

  it("is monotonic: higher degree → warmer/brighter color (or same)", () => {
    const max = 20;
    let prev = brightness(parseHex(degreeColor(0, max)));
    for (let d = 1; d <= max; d++) {
      const cur = brightness(parseHex(degreeColor(d, max)));
      expect(cur).toBeGreaterThanOrEqual(prev - 0.5); // allow tiny fp rounding
      prev = cur;
    }
  });

  it("clamps: degree > maxDegree returns same as degree === maxDegree", () => {
    const ceiling = degreeColor(10, 10);
    const clamped = degreeColor(15, 10);
    expect(clamped).toBe(ceiling);
  });

  it("clamps: negative degree returns same as degree === 0", () => {
    const floor = degreeColor(0, 10);
    const neg = degreeColor(-1, 10);
    expect(neg).toBe(floor);
  });

  it("is deterministic — same inputs always produce the same output", () => {
    expect(degreeColor(5, 10)).toBe(degreeColor(5, 10));
    expect(degreeColor(0, 0)).toBe(degreeColor(0, 0));
  });
});
