/**
 * ConstellationHUD — heads-up display overlay for the 3D constellation Explorer.
 *
 * Shows:
 *   - Visible node / edge counts (after filtering)
 *   - "Showing N of M" notice when total_nodes > visible nodes (layout was capped)
 *   - Selected node count
 *   - A max_nodes selector (500 / 1000 / 2000 / 3000)
 *   - A freshness dot (green = fresh, amber = stale / unknown) via /api/status
 *
 * Positioned as an absolute overlay in the top-left corner of the canvas area.
 * pointer-events: none so it never blocks orbit dragging.
 * The maxNodes selector wraps its own interactive element with pointer-events: auto.
 */

import { useStatus } from "../api/hooks";
import { freshnessColor } from "../lib/freshnessColor";

/** Supported node-count options for the selector. */
const NODE_COUNT_OPTIONS = [500, 1000, 2000, 3000] as const;

interface ConstellationHUDProps {
  /** Number of nodes currently visible (after kind filtering). */
  visibleNodes: number;
  /** Number of edges currently visible (after edge-kind filtering). */
  visibleEdges: number;
  /** Total nodes in the layout index (honest total_nodes from backend). */
  totalNodes: number;
  /** Number of currently selected/highlighted nodes. */
  selectedCount: number;
  /** Current max_nodes cap value. */
  maxNodes: number;
  /** Called when the user changes the max_nodes cap. */
  onChangeMaxNodes: (n: number) => void;
}

/**
 * ConstellationHUD renders an absolute-positioned read-out in the top-left of
 * the canvas.  The parent must use `position: relative` on the canvas wrapper.
 */
export function ConstellationHUD({
  visibleNodes,
  visibleEdges,
  totalNodes,
  selectedCount,
  maxNodes,
  onChangeMaxNodes,
}: ConstellationHUDProps) {
  const { data: status } = useStatus();
  const dotColor = freshnessColor(status?.last_indexed);
  const isCapped = totalNodes > visibleNodes;

  return (
    <div
      className="absolute top-3 left-3 z-10 pointer-events-none"
      aria-label="Constellation HUD"
    >
      <div className="flex flex-col gap-1 bg-black/50 backdrop-blur-sm rounded-lg px-3 py-2 text-xs text-zinc-300 min-w-[160px]">

        {/* Freshness dot + node count */}
        <div className="flex items-center gap-2">
          <span
            className="inline-block w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: dotColor }}
            title={status ? `Last indexed: ${status.last_indexed ?? "unknown"}` : "Loading…"}
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

        {/* "Showing N of M" notice — only when the layout was capped */}
        {isCapped && (
          <div className="text-zinc-500 text-[10px]">
            showing {visibleNodes} of {totalNodes} indexed
          </div>
        )}

        {/* Selected count — only when something is highlighted */}
        {selectedCount > 0 && (
          <div className="text-[10px] text-zinc-400">
            <span className="text-white font-semibold">{selectedCount}</span> selected
          </div>
        )}

        {/* Max-nodes selector — pointer-events: auto so it receives clicks */}
        <div className="pointer-events-auto flex items-center gap-1 mt-1 pt-1 border-t border-zinc-700/50">
          <span className="text-zinc-500 text-[10px] mr-1">cap:</span>
          {NODE_COUNT_OPTIONS.map((n) => (
            <button
              key={n}
              className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                maxNodes === n
                  ? "bg-teal-700/60 text-teal-200"
                  : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700/40"
              }`}
              onClick={() => onChangeMaxNodes(n)}
              title={`Render up to ${n} nodes`}
            >
              {n >= 1000 ? `${n / 1000}k` : String(n)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
