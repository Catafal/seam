/**
 * FilterBar — toggle which node kinds, edge kinds, and confidence tiers are shown.
 *
 * Issue #191 (S6a) upgrades:
 *   - All / None controls per group (select-all / clear-all in one click)
 *   - Per-option live counts sourced from the post-overlay displayEdges so
 *     counts update after impact/trace overlays are applied
 *   - Colored dot per edge-kind matching the graph edge colors (EDGE_TYPE_COLORS)
 *   - 6 phantom edge kinds removed from the kind list (the 9 real kinds only)
 *   - Scrollable-column layout when the horizontal strip overflows the viewport
 *
 * Issue #192 (S6b) adds:
 *   - Node-kind filter group with 6 symbol kinds, colored dots, per-kind counts,
 *     and All/None controls. Node-kind filtering sets the React Flow `hidden` flag
 *     via useGraphOverlays/applyNodeKindFilter (handled upstream in GraphCanvas).
 *
 * Purely visual: toggling drives a client-side `hidden` flag (no refetch).
 * Controlled component: state + handlers are owned by GraphCanvas.
 */

import {
  ALL_EDGE_KINDS,
  ALL_CONFIDENCES,
  type EdgeFilterState,
} from "../lib/edgeFilter";
import { ALL_NODE_KINDS } from "../lib/graphFilterState";
import { EDGE_TYPE_COLORS, DEFAULT_EDGE_COLOR, KIND_COLORS, DEFAULT_KIND_COLOR } from "../lib/constellationColors";

// ── Props ─────────────────────────────────────────────────────────────────────

export interface FilterBarProps {
  /** Current filter state (which kinds + confidences are enabled). */
  filter: EdgeFilterState;
  /** Toggle a single edge kind or confidence value on/off. */
  onToggle: (field: "kinds" | "confidences", value: string) => void;
  /** Select all edge kinds. */
  onAllKinds: () => void;
  /** Deselect all edge kinds. */
  onNoneKinds: () => void;
  /** Select all confidence tiers. */
  onAllConfidences: () => void;
  /** Deselect all confidence tiers. */
  onNoneConfidences: () => void;
  /**
   * Per-kind visible edge counts (from the post-overlay display edges).
   * Keys are edge kind strings; values are visible-edge counts (hidden excluded).
   * Missing keys mean 0 edges of that kind are currently visible.
   */
  kindCounts: Record<string, number>;
  /**
   * Per-confidence visible edge counts (from the post-overlay display edges).
   * Same semantics as kindCounts.
   */
  confidenceCounts: Record<string, number>;

  // ── S6b node-kind props ─────────────────────────────────────────────────────

  /** Which node kinds are currently enabled (drives canvas hidden flag). */
  nodeKindFilter: Set<string>;
  /** Toggle a single node kind on/off. */
  onToggleNodeKind: (kind: string) => void;
  /** Enable all node kinds. */
  onAllNodeKinds: () => void;
  /** Disable all node kinds. */
  onNoneNodeKinds: () => void;
  /**
   * Per-node-kind counts from the BASE neighborhood (unfiltered corpus count).
   * Chips show the raw count so users know what is hidden, not what is visible.
   */
  nodeCounts: Record<string, number>;
}

// ── All/None control ──────────────────────────────────────────────────────────

/** Tiny "all / none" control rendered to the right of a group label. */
function AllNone({ onAll, onNone }: { onAll: () => void; onNone: () => void }) {
  return (
    <span className="flex items-center gap-0.5 text-[9px]">
      <button
        onClick={onAll}
        className="text-zinc-500 hover:text-zinc-300 transition-colors px-0.5"
        title="Enable all"
      >
        all
      </button>
      <span className="text-zinc-700">/</span>
      <button
        onClick={onNone}
        className="text-zinc-500 hover:text-zinc-300 transition-colors px-0.5"
        title="Disable all"
      >
        none
      </button>
    </span>
  );
}

// ── Node-kind pill (colored dot + count) ─────────────────────────────────────

/**
 * A single node-kind toggle pill with a colored dot (matching the symbol color
 * from KIND_COLORS) and a corpus-count badge.
 */
function NodeKindPill({
  kind,
  active,
  count,
  onClick,
}: {
  kind: string;
  active: boolean;
  count: number;
  onClick: () => void;
}) {
  const color = KIND_COLORS[kind] ?? DEFAULT_KIND_COLOR;
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors ${
        active
          ? "bg-zinc-700 text-zinc-100"
          : "bg-zinc-900 text-zinc-600 line-through"
      }`}
      title={`${kind} — ${count} in graph`}
    >
      {/* Colored dot matching the symbol kind color */}
      <span
        className="inline-block w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ backgroundColor: active ? color : "#52525b" }}
        aria-hidden="true"
      />
      {kind}
      {count > 0 && (
        <span
          className={`text-[9px] ${active ? "text-zinc-400" : "text-zinc-700"}`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

// ── Edge-kind pill (with colored dot + count) ─────────────────────────────────

/** A single edge-kind toggle pill with a colored dot indicator and count badge. */
function KindPill({
  kind,
  active,
  count,
  onClick,
}: {
  kind: string;
  active: boolean;
  count: number;
  onClick: () => void;
}) {
  const color = EDGE_TYPE_COLORS[kind] ?? DEFAULT_EDGE_COLOR;
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors ${
        active
          ? "bg-zinc-700 text-zinc-100"
          : "bg-zinc-900 text-zinc-600 line-through"
      }`}
      title={`${kind} — ${count} visible`}
    >
      {/* Colored dot matching the graph edge color */}
      <span
        className="inline-block w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ backgroundColor: active ? color : "#52525b" }}
        aria-hidden="true"
      />
      {kind}
      {count > 0 && (
        <span
          className={`text-[9px] ${active ? "text-zinc-400" : "text-zinc-700"}`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

// ── Confidence pill (count badge only) ───────────────────────────────────────

/** A single confidence-tier toggle pill with count badge. */
function ConfPill({
  label,
  value,
  active,
  count,
  onClick,
}: {
  label: string;
  value: string;
  active: boolean;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors ${
        active
          ? "bg-zinc-700 text-zinc-100"
          : "bg-zinc-900 text-zinc-600 line-through"
      }`}
      title={`${value} — ${count} visible`}
    >
      {label}
      {count > 0 && (
        <span
          className={`text-[9px] ${active ? "text-zinc-400" : "text-zinc-700"}`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

// ── FilterBar ─────────────────────────────────────────────────────────────────

/**
 * Renders node-kind, edge-kind, and confidence filter controls in a panel.
 *
 * Node-kind section (S6b): 6 symbol kinds with colored dots, corpus-count badges,
 * and All/None controls. Toggling sets the React Flow `hidden` flag via upstream
 * applyNodeKindFilter — no canvas refetch.
 *
 * Edge-kind and confidence sections are unchanged from S6a.
 *
 * When the panel would overflow the viewport it shifts to a scrollable layout
 * matching the 3D FilterPanel pattern.
 */
export function FilterBar({
  filter,
  onToggle,
  onAllKinds,
  onNoneKinds,
  onAllConfidences,
  onNoneConfidences,
  kindCounts,
  confidenceCounts,
  nodeKindFilter,
  onToggleNodeKind,
  onAllNodeKinds,
  onNoneNodeKinds,
  nodeCounts,
}: FilterBarProps) {
  return (
    <div
      className="
        flex flex-col gap-2
        bg-zinc-900/90 border border-zinc-700 rounded-md
        px-2 py-2 backdrop-blur-sm
        max-h-[60vh] overflow-y-auto
      "
      aria-label="Graph filter"
    >
      {/* ── Node-kind group (S6b) ─────────────────────────────────────────── */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <span className="text-[9px] text-zinc-500 uppercase tracking-wider">
            nodes
          </span>
          <AllNone onAll={onAllNodeKinds} onNone={onNoneNodeKinds} />
        </div>
        <div className="flex flex-wrap gap-1">
          {ALL_NODE_KINDS.map((k) => (
            <NodeKindPill
              key={k}
              kind={k}
              active={nodeKindFilter.has(k)}
              count={nodeCounts[k] ?? 0}
              onClick={() => onToggleNodeKind(k)}
            />
          ))}
        </div>
      </div>

      <div className="w-full h-px bg-zinc-800" />

      {/* ── Edge-kind group ───────────────────────────────────────────────── */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <span className="text-[9px] text-zinc-500 uppercase tracking-wider">
            edges
          </span>
          <AllNone onAll={onAllKinds} onNone={onNoneKinds} />
        </div>
        {/* Wrapping row — pills reflow to new lines rather than overflow */}
        <div className="flex flex-wrap gap-1">
          {ALL_EDGE_KINDS.map((k) => (
            <KindPill
              key={k}
              kind={k}
              active={filter.kinds.has(k)}
              count={kindCounts[k] ?? 0}
              onClick={() => onToggle("kinds", k)}
            />
          ))}
        </div>
      </div>

      <div className="w-full h-px bg-zinc-800" />

      {/* ── Confidence group ───────────────────────────────────────────────── */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <span className="text-[9px] text-zinc-500 uppercase tracking-wider">
            conf
          </span>
          <AllNone onAll={onAllConfidences} onNone={onNoneConfidences} />
        </div>
        <div className="flex flex-wrap gap-1">
          {ALL_CONFIDENCES.map((c) => (
            <ConfPill
              key={c}
              label={c.slice(0, 3).toLowerCase()}
              value={c}
              active={filter.confidences.has(c)}
              count={confidenceCounts[c] ?? 0}
              onClick={() => onToggle("confidences", c)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
