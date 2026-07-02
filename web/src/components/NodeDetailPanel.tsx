/**
 * NodeDetailPanel — right-side detail panel for the 3D constellation Explorer.
 *
 * Separate from the 2D DetailPanel.tsx — do NOT modify that file.
 *
 * Shows the selected 3D constellation node's symbol detail:
 *   - Symbol name + kind badge
 *   - File path
 *   - Callers  (clickable → re-run selection via onNavigate)
 *   - Callees  (clickable → re-run selection via onNavigate)
 *   - Cluster peers (clickable → re-run selection via onNavigate)
 *
 * Fetches via the existing useSymbol hook (GET /api/symbol/{name}).
 *
 * Props:
 *   node       — selected LayoutNode (provides name + cosmetic fields)
 *   onNavigate — called with a symbol name when the user clicks a caller/callee row
 *   onClose    — called when the user clicks the close button (× in the header)
 */

import { useCallback } from "react";
import { useSymbol } from "../api/hooks";
import { KIND_COLORS, DEFAULT_KIND_COLOR } from "../lib/constellationColors";
import type { LayoutNode } from "../lib/layoutTypes";

// ── sub-components ────────────────────────────────────────────────────────────

interface NavRowProps {
  name: string;
  onNavigate: (name: string) => void;
}

/**
 * A single clickable symbol row in the callers / callees / peers lists.
 * Clicking re-runs the selection state machine in ConstellationTab.
 */
function NavRow({ name, onNavigate }: NavRowProps) {
  const handleClick = useCallback(() => onNavigate(name), [name, onNavigate]);
  // Trim long qualified names for display: show the last segment
  const display = name.includes(".") ? name.split(".").pop()! : name;

  return (
    <button
      onClick={handleClick}
      title={name}
      className="
        w-full text-left px-2 py-1 rounded
        text-xs text-zinc-300 hover:text-zinc-100
        hover:bg-zinc-700/60 transition-colors truncate
      "
    >
      <span className="font-mono text-[10px] text-zinc-500 mr-1">{display !== name ? "·" : ""}</span>
      {display}
    </button>
  );
}

// ── NodeDetailPanel ───────────────────────────────────────────────────────────

interface NodeDetailPanelProps {
  node: LayoutNode;
  onNavigate: (name: string) => void;
  onClose: () => void;
}

/**
 * Right-side panel for the selected 3D node.
 *
 * Renders loading / error / data states.
 * Uses useSymbol(node.name) → callers, callees, cluster.peers.
 */
export function NodeDetailPanel({ node, onNavigate, onClose }: NodeDetailPanelProps) {
  const { data, isLoading, isError } = useSymbol(node.name);

  // Color dot from the Seam constellation palette
  const kindColor = KIND_COLORS[node.label] ?? DEFAULT_KIND_COLOR;

  return (
    <aside
      className="
        w-64 shrink-0 flex flex-col
        bg-zinc-900/95 border-l border-zinc-800
        overflow-hidden
      "
      aria-label="Symbol detail panel"
    >
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-start gap-2 px-3 py-2.5 border-b border-zinc-800">
        {/* Kind color dot */}
        <div
          className="mt-0.5 w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: kindColor }}
          aria-hidden="true"
        />

        {/* Name + kind */}
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-zinc-100 truncate" title={node.name}>
            {node.name}
          </p>
          <p className="text-[10px] text-zinc-500">{node.label}</p>
          {node.file_path && (
            <p
              className="text-[10px] text-zinc-600 truncate"
              title={node.file_path}
            >
              {node.file_path.split("/").pop()}
            </p>
          )}
        </div>

        {/* Close button */}
        <button
          onClick={onClose}
          aria-label="Close detail panel"
          className="text-zinc-500 hover:text-zinc-300 transition-colors shrink-0 px-1"
        >
          ✕
        </button>
      </div>

      {/* ── Body ───────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-4 text-xs">
        {isLoading && (
          <p className="text-zinc-500 animate-pulse px-1 py-2">Loading…</p>
        )}

        {isError && (
          <p className="text-red-400 px-1 py-2">
            Could not load symbol detail.
          </p>
        )}

        {data && (
          <>
            {/* Callers */}
            {data.callers.length > 0 && (
              <section>
                <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 mb-1 px-1">
                  Callers ({data.callers.length})
                </h3>
                <ul className="space-y-0.5">
                  {data.callers.map((ref) => (
                    <li key={ref.name}>
                      <NavRow name={ref.name} onNavigate={onNavigate} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Callees */}
            {data.callees.length > 0 && (
              <section>
                <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 mb-1 px-1">
                  Callees ({data.callees.length})
                </h3>
                <ul className="space-y-0.5">
                  {data.callees.map((ref) => (
                    <li key={ref.name}>
                      <NavRow name={ref.name} onNavigate={onNavigate} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Cluster peers */}
            {data.peers.length > 0 && (
              <section>
                <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 mb-1 px-1">
                  Cluster peers ({data.peers.length})
                </h3>
                <ul className="space-y-0.5">
                  {data.peers.map((name) => (
                    <li key={name}>
                      <NavRow name={name} onNavigate={onNavigate} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Empty state: no callers / callees / peers */}
            {data.callers.length === 0 &&
              data.callees.length === 0 &&
              data.peers.length === 0 && (
                <p className="text-zinc-600 px-1 py-2 text-[11px]">
                  No connections found in the index.
                </p>
              )}

            {/* Cluster label */}
            {data.cluster && (
              <section>
                <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 mb-1 px-1">
                  Area
                </h3>
                <p className="px-2 py-1 text-[11px] text-zinc-400">
                  {data.cluster.label ?? `cluster-${data.cluster.id}`}
                </p>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
