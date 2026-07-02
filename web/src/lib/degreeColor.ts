/**
 * degreeColor — sequential ramp for the treemap's degree signal (B3).
 *
 * Maps a fan-in degree value to a hex color on a single sequential ramp:
 *   cool zinc floor  (#3f3f46)  → hot amber ceiling (#f59e0b)
 *
 * Size AND color encode the same quantity (fan-in degree) so they reinforce
 * rather than compete. The random-hash per-name color is retired for leaves.
 *
 * Contract:
 *   degreeColor(degree, maxDegree) → "#rrggbb"
 *   - maxDegree === 0 → returns the cool floor (no divide-by-zero)
 *   - degree clamped to [0, maxDegree] before mapping
 *   - monotonic: higher degree → warmer/brighter color
 *   - deterministic: same inputs always produce the same output
 *   - never throws
 */

/** Cool floor: zinc-700 (#3f3f46) — muted, low-coupling cold color. */
const COOL: [number, number, number] = [63, 63, 70];

/** Hot ceiling: amber-500 (#f59e0b) — bright, high-coupling warm color. */
const HOT: [number, number, number] = [245, 158, 11];

/** Convert an [r,g,b] 0-255 triple to a "#rrggbb" hex string. */
function toHex(r: number, g: number, b: number): string {
  const hex = (n: number) => Math.round(n).toString(16).padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

/**
 * Map a fan-in degree value to a color on the cool-zinc → hot-amber ramp.
 *
 * @param degree    - fan-in degree of the symbol/node (≥ 0)
 * @param maxDegree - maximum degree among all currently-placed nodes (≥ 0)
 * @returns a "#rrggbb" hex color string
 */
export function degreeColor(degree: number, maxDegree: number): string {
  // Guard: maxDegree === 0 means all nodes are isolated; return the cool floor.
  if (maxDegree <= 0) return toHex(...COOL);

  // Clamp degree to [0, maxDegree] so out-of-range values snap to the endpoints.
  const t = Math.max(0, Math.min(degree, maxDegree)) / maxDegree;

  // Linear interpolation in RGB space between the cool floor and the hot ceiling.
  const r = COOL[0] + t * (HOT[0] - COOL[0]);
  const g = COOL[1] + t * (HOT[1] - COOL[1]);
  const b = COOL[2] + t * (HOT[2] - COOL[2]);

  return toHex(r, g, b);
}
