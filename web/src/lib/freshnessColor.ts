/**
 * Shared freshness-color helper for HUD components.
 *
 * Extracted from ConstellationHUD so both the 3D HUD and the new 2D GraphHUD
 * apply the same visual convention without duplicating logic.
 *
 * WHY 10-minute threshold: this is short enough to flag a stale index during
 * an active editing session, but long enough to stay green during normal
 * exploration of a recently-indexed codebase.
 */

/**
 * Return the CSS colour to render a freshness dot.
 *
 * Green  (#22c55e) = indexed within the last 10 minutes (likely up to date).
 * Amber  (#f59e0b) = indexed earlier or timestamp unknown / unparseable.
 *
 * Never throws: any parse failure degrades to amber (safe default).
 */
export function freshnessColor(lastIndexed: string | null | undefined): string {
  if (!lastIndexed) return "#f59e0b"; // amber — unknown
  try {
    const d = new Date(lastIndexed);
    if (isNaN(d.getTime())) return "#f59e0b";
    const ageMs = Date.now() - d.getTime();
    return ageMs < 10 * 60 * 1000 ? "#22c55e" : "#f59e0b"; // green or amber
  } catch {
    return "#f59e0b";
  }
}
