/**
 * GraphHUD — heads-up display overlay for the 2D GraphCanvas.
 *
 * Modelled on ConstellationHUD but scoped to the 2D canvas:
 *   - Visible node count (on-canvas only; off-canvas impact cards excluded)
 *   - Visible edge count (after client-side filter)
 *   - Filtered-out edge count badge (when > 0)
 *   - "+N impacted" readout (off-canvas impact dependents, when overlay is active)
 *   - Selected count (when a node is clicked)
 *   - Freshness dot (shared freshnessColor helper)
 *
 * Positioned as a React Flow Panel at bottom-left to avoid overlapping the
 * top-left Legend and top-right controls.
 *
 * WHY no max_nodes selector or cap notice: the 2D graph is a depth-1
 * neighborhood (dagre-laid-out), not a layout-capped point cloud.  The cap
 * concept does not apply here.
 */

import { useStatus } from "../api/hooks";
import { freshnessColor } from "../lib/freshnessColor";
import type { HudCounts } from "../lib/hudCounts";

interface GraphHUDProps {
  /** Pre-computed HUD counts derived from the display arrays. */
  counts: HudCounts;
  /** True when the impact overlay is active (controls the impacted badge). */
  impactActive: boolean;
}

/**
 * Compact HUD overlay for the 2D GraphCanvas.
 *
 * Renders inside a React Flow Panel (position="bottom-left") so React Flow
 * manages z-index and avoids overlap with native controls.
 * pointer-events: none on the wrapper — the HUD is read-only.
 */
export function GraphHUD({ counts, impactActive }: GraphHUDProps) {
  const { data: status } = useStatus();
  const dotColor = freshnessColor(status?.last_indexed);
  const {
    visibleNodes,
    visibleEdges,
    filteredOut,
    impactedOffCanvas,
    selectedCount,
  } = counts;

  return (
    <div className="pointer-events-none" aria-label="Graph HUD">
      <div className="flex flex-col gap-1 bg-black/50 backdrop-blur-sm rounded-lg px-3 py-2 text-xs text-zinc-300 min-w-[160px]">

        {/* Freshness dot + node / edge counts */}
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="inline-block w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: dotColor }}
            title={
              status
                ? `Last indexed: ${status.last_indexed ?? "unknown"}`
                : "Loading…"
            }
          />
          <span>
            <span className="font-semibold text-white">{visibleNodes}</span>
            <span className="text-zinc-500"> nodes</span>
          </span>
          <span className="text-zinc-600">/</span>
          <span>
            <span className="font-semibold text-white">{visibleEdges}</span>
            <span className="text-zinc-500"> edges</span>
          </span>
        </div>

        {/* Filtered-out edge count — only when something is hidden */}
        {filteredOut > 0 && (
          <div className="text-[10px] text-zinc-500">
            <span className="text-zinc-400 font-semibold">{filteredOut}</span> filtered out
          </div>
        )}

        {/* Off-canvas impact dependents — only when the overlay is active */}
        {impactActive && impactedOffCanvas > 0 && (
          <div className="text-[10px] text-amber-400/80">
            +{impactedOffCanvas} impacted
          </div>
        )}

        {/* Selected count — only when something is selected */}
        {selectedCount > 0 && (
          <div className="text-[10px] text-zinc-400">
            <span className="text-white font-semibold">{selectedCount}</span> selected
          </div>
        )}
      </div>
    </div>
  );
}
