/**
 * resolveClusterHandoff — pure cluster-to-neighborhood hand-off resolver (C3).
 *
 * Maps a clicked ConstellationCluster to the symbol name that should be
 * centered in the 2D neighborhood view.
 *
 * Resolution order:
 *   1. cluster.representative (the hub symbol, computed server-side by C1)
 *   2. cluster.label (the human-readable area name — a valid symbol fallback)
 *   3. null — caller must not navigate (no crash, just a no-op)
 *
 * Treats empty strings as absent (same contract as null). This ensures the
 * neighborhood center is never set to a blank string.
 *
 * Pure — no React state, no hooks, no DB. Exported for vitest.
 */

import type { ConstellationCluster } from "../api/schema-types";

/**
 * Given a clicked cluster, return the symbol name to center the neighborhood on.
 *
 * Returns null when there is no actionable target (caller should skip navigation).
 */
export function resolveClusterHandoff(cluster: ConstellationCluster): string | null {
  if (cluster.representative && cluster.representative.trim().length > 0) {
    return cluster.representative;
  }
  if (cluster.label && cluster.label.trim().length > 0) {
    return cluster.label;
  }
  return null;
}
