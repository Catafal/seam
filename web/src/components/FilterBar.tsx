/**
 * FilterBar — toggle which edge kinds and confidence tiers are shown on the canvas.
 *
 * Purely visual: toggling drives a client-side `hidden` flag on edges (no refetch).
 * Lets the user declutter a dense neighborhood (e.g. hide low-confidence INFERRED
 * edges, or show only `call` edges). Mounted as a React Flow <Panel>.
 *
 * Controlled component: state + onToggle are owned by GraphCanvas.
 */

import {
  ALL_EDGE_KINDS,
  ALL_CONFIDENCES,
  type EdgeFilterState,
} from "../lib/edgeFilter";

export interface FilterBarProps {
  filter: EdgeFilterState;
  onToggle: (field: "kinds" | "confidences", value: string) => void;
}

/** A single toggle pill — on = highlighted, off = muted. */
function Pill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`px-1.5 py-0.5 rounded text-[10px] font-mono transition-colors ${
        active
          ? "bg-zinc-700 text-zinc-100"
          : "bg-zinc-900 text-zinc-600 line-through"
      }`}
    >
      {label}
    </button>
  );
}

export function FilterBar({ filter, onToggle }: FilterBarProps) {
  return (
    <div className="flex items-center gap-2 bg-zinc-900/90 border border-zinc-700 rounded-md px-2 py-1.5 backdrop-blur-sm">
      <div className="flex items-center gap-1">
        <span className="text-[9px] text-zinc-600 uppercase tracking-wider mr-0.5">kind</span>
        {ALL_EDGE_KINDS.map((k) => (
          <Pill
            key={k}
            label={k}
            active={filter.kinds.has(k)}
            onClick={() => onToggle("kinds", k)}
          />
        ))}
      </div>
      <div className="w-px h-3.5 bg-zinc-700" />
      <div className="flex items-center gap-1">
        <span className="text-[9px] text-zinc-600 uppercase tracking-wider mr-0.5">conf</span>
        {ALL_CONFIDENCES.map((c) => (
          <Pill
            key={c}
            label={c.slice(0, 3).toLowerCase()}
            active={filter.confidences.has(c)}
            onClick={() => onToggle("confidences", c)}
          />
        ))}
      </div>
    </div>
  );
}
