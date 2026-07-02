/**
 * ResizeHandle — a draggable divider for resizing the constellation Explorer
 * side panels.
 *
 * Uses setPointerCapture so the drag is smooth even if the pointer moves outside
 * the element (prevents "sticky drag" on fast moves).
 *
 * The parent ConstellationTab persists widths to localStorage:
 *   seam-left-w   — left FilterPanel width (clamped [150, 500])
 *   seam-right-w  — right NodeDetailPanel width (clamped [150, 500])
 *
 * Usage:
 *   <ResizeHandle side="left" onResize={(delta) => setLeftWidth(w => clamp(w + delta))} />
 *   <ResizeHandle side="right" onResize={(delta) => setRightWidth(w => clamp(w - delta))} />
 *
 * Note: "left" handle sits to the RIGHT of the left panel (user drags it to
 * widen/narrow the left panel). "right" handle sits to the LEFT of the right
 * panel.
 */

import { useRef, useCallback } from "react";

/** Clamp a width to the safe panel range. */
export const PANEL_MIN_W = 150;
export const PANEL_MAX_W = 500;

export function clampPanelWidth(w: number): number {
  return Math.max(PANEL_MIN_W, Math.min(PANEL_MAX_W, w));
}

/**
 * Read a persisted panel width from localStorage, clamp it, and return it.
 * Falls back to `fallback` when the key is absent, unparseable, or storage throws.
 *
 * WHY shared here (not inlined per tab): 2D (App.tsx) and 3D (ConstellationTab.tsx)
 * both persist panel widths; one implementation prevents the two from drifting
 * in their clamp bounds or NaN handling.
 *
 * NaN guard: Number("bad-string") = NaN; Math.max/min propagate NaN so we
 * must catch it explicitly and fall back rather than store NaN in state.
 */
export function readPanelWidth(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    const n = Number(raw);
    return isNaN(n) ? fallback : clampPanelWidth(n);
  } catch {
    return fallback;
  }
}

interface ResizeHandleProps {
  /** Which panel this handle controls. */
  side: "left" | "right";
  /**
   * Called during drag with the raw horizontal delta in pixels.
   * Positive = pointer moved right; negative = pointer moved left.
   * The parent applies the delta to the correct panel width and clamps.
   */
  onResize: (delta: number) => void;
}

/**
 * ResizeHandle renders a thin vertical bar that the user can drag to resize
 * the adjacent panel.
 */
export function ResizeHandle({ side, onResize }: ResizeHandleProps) {
  const lastX = useRef<number | null>(null);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Capture pointer so drag continues even if pointer leaves the element
      e.currentTarget.setPointerCapture(e.pointerId);
      lastX.current = e.clientX;
    },
    [],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Only fire during active capture (pointer button held)
      if (lastX.current === null) return;
      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
      const delta = e.clientX - lastX.current;
      lastX.current = e.clientX;
      onResize(delta);
    },
    [onResize],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.currentTarget.releasePointerCapture(e.pointerId);
      lastX.current = null;
    },
    [],
  );

  return (
    <div
      role="separator"
      aria-label={`Resize ${side} panel`}
      aria-orientation="vertical"
      className={`
        flex-shrink-0 w-1 cursor-col-resize select-none
        bg-zinc-800 hover:bg-teal-700/60 active:bg-teal-600
        transition-colors
        ${side === "left" ? "border-l border-zinc-700/30" : "border-r border-zinc-700/30"}
      `}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      // Also release on pointer cancel (e.g. lost focus)
      onPointerCancel={handlePointerUp}
    />
  );
}
