/**
 * Seam teal-native color palette for the 3D constellation Explorer.
 *
 * EDGE_TYPE_COLORS: maps all 15 edge kinds to distinct hues.
 * KIND_COLORS: maps 6 symbol kinds for node labeling and filter chips.
 * CANVAS_BG: teal-void background for the WebGL canvas.
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §6/§7
 */

/** Per-edge-kind colors for the 15-kind Seam vocabulary. */
export const EDGE_TYPE_COLORS: Record<string, string> = {
  call: "#1DA27E",          // seafoam teal — the primary call edge
  import: "#3b82f6",        // blue
  extends: "#a855f7",       // purple
  implements: "#8b5cf6",    // violet
  instantiates: "#f97316",  // orange
  holds: "#06b6d4",         // cyan (composition — stored fields)
  reads: "#22c55e",         // green (field read)
  writes: "#ef4444",        // red (field write)
  uses: "#eab308",          // amber (method param coupling)
  http_calls: "#38bdf8",     // sky (protocol boundary)
  reads_config: "#84cc16",   // lime (config read)
  configures: "#14b8a6",     // teal (operational resource wiring)
  raises: "#fb7185",         // rose (exception raise)
  catches: "#f59e0b",        // amber-orange (exception handler)
  tests: "#94a3b8",          // slate (static test evidence)
};

/** Fallback edge color for unknown kinds. */
export const DEFAULT_EDGE_COLOR = "#1C8585";

/** Per-symbol-kind colors for node labels, tooltip dots, and filter chips. */
export const KIND_COLORS: Record<string, string> = {
  class: "#a855f7",       // purple
  interface: "#8b5cf6",   // violet
  function: "#06b6d4",    // cyan
  method: "#1DA27E",      // seafoam teal
  type: "#f97316",        // orange
  field: "#64748b",       // slate (data field — less prominent)
};

/** Fallback node kind color. */
export const DEFAULT_KIND_COLOR = "#94a3b8";

/** WebGL canvas background — teal-void (matches the Seam visual language). */
export const CANVAS_BG = "#04100f";
