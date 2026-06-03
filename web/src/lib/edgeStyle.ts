/**
 * Confidence → React Flow edge style mapping.
 *
 * The three confidence levels from the Seam engine (EXTRACTED, AMBIGUOUS, INFERRED)
 * map to visual dash patterns that match standard graph conventions:
 *   EXTRACTED = solid    → highest confidence (import evidence found in source)
 *   AMBIGUOUS = dashed   → unresolved (multiple candidate targets)
 *   INFERRED  = dotted   → heuristic (name-count only, no import evidence)
 *
 * WHY a separate module: the mapping is referenced by both GraphCanvas (edge styling)
 * and ClusterLegend (F5 legend), so keeping it here prevents duplication.
 */

/** React Flow inline style shape for SVG path elements */
export interface EdgeStyle {
  stroke: string;
  strokeWidth?: number;
  strokeDasharray?: string;
}

/** Known confidence values from the API schema */
export type Confidence = "EXTRACTED" | "AMBIGUOUS" | "INFERRED";

/**
 * Static map from confidence level to edge visual style.
 *
 * Colors are chosen for contrast on a dark zinc background:
 * - EXTRACTED: sky-400 (#38bdf8) — bright, high confidence
 * - AMBIGUOUS: amber-400 (#fbbf24) — warning-tone, uncertain
 * - INFERRED:  zinc-400 (#a1a1aa) — muted, weakest signal
 */
export const CONFIDENCE_STYLES: Record<Confidence, EdgeStyle> = {
  EXTRACTED: {
    stroke: "#38bdf8",  // sky-400 — solid, high confidence
    strokeWidth: 1.5,
    // No strokeDasharray = solid line
  },
  AMBIGUOUS: {
    stroke: "#fbbf24",  // amber-400 — dashed, uncertain target
    strokeWidth: 1.5,
    strokeDasharray: "8 4",  // long-short dash pattern
  },
  INFERRED: {
    stroke: "#a1a1aa",  // zinc-400 — dotted, weakest heuristic
    strokeWidth: 1.5,
    strokeDasharray: "2 4",  // tight dot pattern
  },
};

/**
 * Get the React Flow edge style for a confidence string.
 *
 * Unknown/undefined confidence values fall back to INFERRED (dotted/muted)
 * so future API additions degrade visually rather than crashing.
 */
export function getEdgeStyle(confidence: string): EdgeStyle {
  return CONFIDENCE_STYLES[confidence as Confidence] ?? CONFIDENCE_STYLES.INFERRED;
}
