/**
 * AreaCards — the functional-area landing for the Overview.
 *
 * Renders one "blackbox" card per functional area (derived from the folder layout
 * by deriveAreas). Each card answers "what is this part of the codebase?" via its
 * name, size, and key symbols; clicking it drills into the treemap scoped to that
 * area. Tests are hidden by default with a toggle — they'd otherwise dominate.
 *
 * Presentational only: all derivation lives in deriveAreas; this component just
 * lays out the cards and surfaces the toggle + click callbacks.
 */

import { getClusterPalette } from "../lib/clusterColor";
import type { Area } from "../lib/deriveAreas";
import { Boxes } from "lucide-react";

const PALETTE = getClusterPalette();

/** Stable accent colour for an area (same hue across re-renders). */
function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

export interface AreaCardsProps {
  areas: Area[];
  isLoading: boolean;
  includeTests: boolean;
  onToggleTests: () => void;
  /** Drill into an area → caller renders the scoped treemap. */
  onEnterArea: (area: Area) => void;
}

export function AreaCards({
  areas,
  isLoading,
  includeTests,
  onToggleTests,
  onEnterArea,
}: AreaCardsProps) {
  return (
    <div className="flex flex-col w-full h-full">
      {/* Header + tests toggle */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-zinc-800 shrink-0">
        <div className="flex items-center gap-2">
          <Boxes className="w-4 h-4 text-zinc-400" />
          <span className="text-sm font-semibold text-zinc-200">Functional areas</span>
          <span className="text-[11px] text-zinc-500">
            click an area to explore what's inside
          </span>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeTests}
            onChange={onToggleTests}
            className="accent-zinc-500"
            data-testid="show-tests-toggle"
          />
          show tests
        </label>
      </div>

      {/* Cards */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <p className="text-zinc-500 text-sm">Loading areas…</p>
        ) : areas.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
            <p className="text-zinc-500 text-sm">No areas to show.</p>
            <p className="text-zinc-600 text-xs">
              Run <code className="text-zinc-400">seam init</code> to build the index.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
            {areas.map((area) => {
              const colour = hashColor(area.name);
              return (
                <button
                  key={area.key}
                  onClick={() => onEnterArea(area)}
                  title={`Explore ${area.name}`}
                  className="flex flex-col gap-2 p-4 text-left rounded-lg border bg-zinc-900/60 hover:bg-zinc-800/70 transition-colors"
                  style={{ borderColor: `${colour}66` }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-2.5 h-2.5 rounded-sm shrink-0"
                      style={{ backgroundColor: colour }}
                    />
                    <span className="text-sm font-semibold text-zinc-100 truncate">
                      {area.name}
                    </span>
                  </div>
                  <div className="text-[11px] text-zinc-500 font-mono">
                    {area.fileCount} file{area.fileCount === 1 ? "" : "s"} ·{" "}
                    {area.symbolCount} symbol{area.symbolCount === 1 ? "" : "s"}
                  </div>
                  {area.keySymbols.length > 0 && (
                    <div className="text-[11px] text-zinc-400 truncate">
                      <span className="text-zinc-600">key: </span>
                      {area.keySymbols.join(", ")}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
