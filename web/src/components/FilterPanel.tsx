/**
 * FilterPanel — left-side panel for the 3D constellation Explorer.
 *
 * Shows toggle chips for:
 *   - 6 node kinds (function, class, method, interface, type, field)
 *   - the current Seam edge vocabulary from edgeFilter.ts
 *
 * Counts are derived from the RAW (unfiltered) data so the chip badge always
 * reflects the full corpus, not the current visible set.
 *
 * Pure helper (unit-tested):
 *   countByField(nodes, field) → Record<string, number>
 */

import type { LayoutNode, LayoutEdge, LayoutData } from "../lib/layoutTypes";
import { EDGE_TYPE_COLORS, KIND_COLORS, DEFAULT_KIND_COLOR, DEFAULT_EDGE_COLOR } from "../lib/constellationColors";
import { ALL_EDGE_KINDS } from "../lib/edgeFilter";

// ── Node kinds and edge kinds in stable display order ────────────────────────

const NODE_KINDS = ["function", "class", "method", "interface", "type", "field"] as const;

// ── Pure helper ───────────────────────────────────────────────────────────────

/**
 * Count nodes by a string field.
 *
 * Pure — no React state, no hooks. Exported for vitest.
 *
 * @param nodes - array of LayoutNode objects
 * @param field - the field to group by ("label" for symbol kind)
 * @returns a map of field-value → count
 */
export function countByField(nodes: LayoutNode[], field: "label"): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of nodes) {
    const key = n[field] as string;
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

/**
 * Count edges by their type field.
 *
 * Pure — used internally by FilterPanel to compute per-kind edge counts.
 */
function countEdgesByType(edges: LayoutEdge[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const e of edges) {
    out[e.type] = (out[e.type] ?? 0) + 1;
  }
  return out;
}

// ── FilterPanel ───────────────────────────────────────────────────────────────

interface FilterPanelProps {
  /** Raw (unfiltered) layout data — counts come from this. */
  data: LayoutData;
  /** Which node kinds are currently enabled. */
  enabledKinds: Set<string>;
  /** Which edge kinds are currently enabled. */
  enabledEdges: Set<string>;
  /** Called when a node kind chip is toggled. */
  onToggleKind: (kind: string) => void;
  /** Called when an edge kind chip is toggled. */
  onToggleEdge: (kind: string) => void;
  /** Called when "all" node kinds button is clicked. */
  onAllKinds: () => void;
  /** Called when "none" node kinds button is clicked. */
  onNoneKinds: () => void;
  /** Called when "all" edge kinds button is clicked. */
  onAllEdges: () => void;
  /** Called when "none" edge kinds button is clicked. */
  onNoneEdges: () => void;
}

/**
 * FilterPanel renders kind and edge-kind toggles in a scrollable left column.
 * Counts shown on each chip reflect the RAW data (not the filtered view).
 */
export function FilterPanel({
  data,
  enabledKinds,
  enabledEdges,
  onToggleKind,
  onToggleEdge,
  onAllKinds,
  onNoneKinds,
  onAllEdges,
  onNoneEdges,
}: FilterPanelProps) {
  const kindCounts = countByField(data.nodes as LayoutNode[], "label");
  const edgeCounts = countEdgesByType(data.edges as LayoutEdge[]);

  return (
    <div className="flex flex-col gap-4 p-3 text-xs overflow-y-auto select-none h-full">
      {/* Node kinds section */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-zinc-300 uppercase tracking-wider text-[10px]">
            Node kinds
          </span>
          <span className="flex gap-1">
            <button
              className="text-zinc-500 hover:text-zinc-300 transition-colors"
              onClick={onAllKinds}
              title="Show all node kinds"
            >
              all
            </button>
            <span className="text-zinc-700">/</span>
            <button
              className="text-zinc-500 hover:text-zinc-300 transition-colors"
              onClick={onNoneKinds}
              title="Hide all node kinds"
            >
              none
            </button>
          </span>
        </div>
        <div className="flex flex-col gap-1">
          {NODE_KINDS.map((kind) => {
            const count = kindCounts[kind] ?? 0;
            const active = enabledKinds.has(kind);
            const color = KIND_COLORS[kind] ?? DEFAULT_KIND_COLOR;
            return (
              <button
                key={kind}
                className={`flex items-center gap-2 px-2 py-1 rounded transition-colors text-left ${
                  active
                    ? "bg-zinc-800 text-zinc-200"
                    : "text-zinc-600 hover:text-zinc-400"
                }`}
                onClick={() => onToggleKind(kind)}
                title={`Toggle ${kind} nodes`}
              >
                {/* Color dot */}
                <span
                  className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: active ? color : "#374151" }}
                />
                <span className="flex-1 capitalize">{kind}</span>
                {count > 0 && (
                  <span className="text-zinc-500 text-[10px]">{count}</span>
                )}
              </button>
            );
          })}
        </div>
      </section>

      <div className="border-t border-zinc-800" />

      {/* Edge kinds section */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-zinc-300 uppercase tracking-wider text-[10px]">
            Edge kinds
          </span>
          <span className="flex gap-1">
            <button
              className="text-zinc-500 hover:text-zinc-300 transition-colors"
              onClick={onAllEdges}
              title="Show all edge kinds"
            >
              all
            </button>
            <span className="text-zinc-700">/</span>
            <button
              className="text-zinc-500 hover:text-zinc-300 transition-colors"
              onClick={onNoneEdges}
              title="Hide all edge kinds"
            >
              none
            </button>
          </span>
        </div>
        <div className="flex flex-col gap-1">
          {ALL_EDGE_KINDS.map((kind) => {
            const count = edgeCounts[kind] ?? 0;
            const active = enabledEdges.has(kind);
            const color = EDGE_TYPE_COLORS[kind] ?? DEFAULT_EDGE_COLOR;
            return (
              <button
                key={kind}
                className={`flex items-center gap-2 px-2 py-1 rounded transition-colors text-left ${
                  active
                    ? "bg-zinc-800 text-zinc-200"
                    : "text-zinc-600 hover:text-zinc-400"
                }`}
                onClick={() => onToggleEdge(kind)}
                title={`Toggle ${kind} edges`}
              >
                {/* Color bar — edge kinds use a horizontal line visual */}
                <span
                  className="inline-block w-4 h-0.5 flex-shrink-0"
                  style={{ backgroundColor: active ? color : "#374151" }}
                />
                <span className="flex-1">{kind}</span>
                {count > 0 && (
                  <span className="text-zinc-500 text-[10px]">{count}</span>
                )}
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
