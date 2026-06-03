/**
 * ClusterLegend — colour swatch + label list for all clusters.
 *
 * Reuses clusterColor() from lib/clusterColor.ts so the colours
 * shown here are ALWAYS byte-identical to the stripes on SymbolNode
 * cards and the dots on the LandingPage cluster grid.
 *
 * WHY a separate component (not inline in App): ClusterLegend is
 * displayed in the DetailPanel sidebar (F5) as a reference while
 * the user is exploring a symbol. Keeping it separate means it can
 * also be dropped into any other layout position without refactoring.
 *
 * Design: compact rows — swatch + label + size. No interaction;
 * purely informational. The landing cluster grid (App.tsx) is the
 * interactive entry point; this legend is the reference overlay.
 */

import { clusterColor } from "../lib/clusterColor";
import type { ClusterItem } from "../api/schema-types";

export interface ClusterLegendProps {
  /** The full cluster list from useClusters() */
  clusters: ClusterItem[];
}

/**
 * Colour legend mapping cluster_id → label + colour swatch.
 * Renders a compact vertical list; empty list renders a subtle empty state.
 */
export function ClusterLegend({ clusters }: ClusterLegendProps) {
  if (clusters.length === 0) {
    return (
      <div className="text-xs text-zinc-600 italic py-1">
        No clusters indexed
      </div>
    );
  }

  return (
    <ul className="space-y-1" aria-label="Cluster legend">
      {clusters.map((c) => {
        const colour = clusterColor(c.cluster_id);
        const label = c.label ?? `cluster-${c.cluster_id}`;

        return (
          <li
            key={c.cluster_id}
            className="flex items-center gap-2"
          >
            {/* Colour swatch — same colour as SymbolNode stripe for the same cluster */}
            <div
              data-testid="cluster-swatch"
              className="w-3 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: colour ?? "#52525b" }}
              aria-hidden="true"
            />
            <span className="text-xs text-zinc-300 truncate flex-1 min-w-0">
              {label}
            </span>
            <span className="text-[10px] text-zinc-600 font-mono tabular-nums shrink-0">
              {c.size}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
