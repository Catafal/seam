/**
 * Stable cluster_id → colour mapping for visual cluster identity.
 *
 * WHY a separate module: cluster colours are used by SymbolNode (stripe),
 * ClusterLegend (F5), and the landing cluster list (F3) — all three need
 * the same colour for the same cluster_id, so the hash lives here once.
 *
 * Design decision — deterministic hash, not a sequential palette:
 * A hash-based approach means cluster colours are stable across page reloads
 * and across different symbol neighborhoods. A sequential "assign next color"
 * approach would produce different colours depending on which clusters are
 * visible, breaking cross-section visual identity.
 *
 * Palette: 10 perceptually distinct colours at medium saturation, dark-theme
 * friendly. Same hue palette as the original ADR-010 "OpenAI-style card" spec.
 */

/** Colour palette: 10 entries, chosen for visibility on zinc-900 backgrounds */
const CLUSTER_PALETTE: string[] = [
  "#7dd3fc", // sky-300
  "#a5b4fc", // indigo-300
  "#6ee7b7", // emerald-300
  "#fca5a5", // red-300
  "#fcd34d", // amber-300
  "#c4b5fd", // violet-300
  "#86efac", // green-300
  "#fdba74", // orange-300
  "#67e8f9", // cyan-300
  "#f9a8d4", // pink-300
];

/**
 * Map a cluster_id integer to a stable colour from the palette.
 *
 * The mapping is stable because modulo arithmetic on the cluster_id
 * always produces the same bucket for the same id.
 *
 * @param clusterId  The numeric cluster_id from the API, or null
 * @returns  A hex colour string, or null when clusterId is null
 */
export function clusterColor(clusterId: number | null): string | null {
  if (clusterId === null) return null;
  // Ensure positive index even for negative ids (shouldn't happen in practice)
  const idx = Math.abs(clusterId) % CLUSTER_PALETTE.length;
  return CLUSTER_PALETTE[idx];
}

/**
 * Return the colour palette for use in legends.
 * Returns a shallow copy so callers can't mutate the module constant.
 */
export function getClusterPalette(): string[] {
  return [...CLUSTER_PALETTE];
}
