/**
 * Legend — an always-on (collapsible) key for the GraphCanvas.
 *
 * Explains the canvas's visual encoding so a first-time viewer can read it:
 *   - Edge confidence (solid / dashed / dotted) → EXTRACTED / AMBIGUOUS / INFERRED
 *   - Cluster colours present in the current view (stripe colour → cluster label)
 *   - Risk tiers (only while the impact overlay is active)
 *
 * Presentational: all data is passed in. Mounted as a React Flow <Panel>.
 */

import { useState } from "react";
import { CONFIDENCE_STYLES, type Confidence } from "../lib/edgeStyle";
import { clusterColor } from "../lib/clusterColor";
import { RISK_TIERS, tierColor, tierLabel } from "../lib/riskTier";
import { ChevronDown, ChevronUp } from "lucide-react";

/** One cluster present in the current canvas (for the colour key). */
export interface LegendCluster {
  cluster_id: number;
  cluster_label: string | null;
}

export interface LegendProps {
  /** Distinct clusters visible on the canvas. */
  clusters: LegendCluster[];
  /** Show the risk-tier key (true while the impact overlay is active). */
  showRiskTiers?: boolean;
}

/** A small SVG line preview of an edge confidence style. */
function EdgeSwatch({ confidence }: { confidence: Confidence }) {
  const s = CONFIDENCE_STYLES[confidence];
  return (
    <svg width="22" height="6" aria-hidden="true">
      <line
        x1="1"
        y1="3"
        x2="21"
        y2="3"
        stroke={s.stroke}
        strokeWidth={s.strokeWidth}
        strokeDasharray={s.strokeDasharray}
      />
    </svg>
  );
}

const CONFIDENCE_ORDER: Confidence[] = ["EXTRACTED", "AMBIGUOUS", "INFERRED"];

export function Legend({ clusters, showRiskTiers = false }: LegendProps) {
  const [open, setOpen] = useState(true);

  return (
    <div className="bg-zinc-900/90 border border-zinc-700 rounded-md text-[10px] text-zinc-400 backdrop-blur-sm max-w-[200px]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-2.5 py-1.5 hover:bg-zinc-800/60 transition-colors"
        aria-expanded={open}
        aria-label="Toggle legend"
      >
        <span className="font-semibold uppercase tracking-wider text-zinc-500">Legend</span>
        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>

      {open && (
        <div className="px-2.5 pb-2.5 space-y-2.5">
          {/* Confidence */}
          <div className="space-y-1">
            <p className="text-zinc-600 font-semibold">Edge confidence</p>
            {CONFIDENCE_ORDER.map((c) => (
              <div key={c} className="flex items-center gap-2">
                <EdgeSwatch confidence={c} />
                <span>{c.toLowerCase()}</span>
              </div>
            ))}
          </div>

          {/* Risk tiers (impact overlay only) */}
          {showRiskTiers && (
            <div className="space-y-1">
              <p className="text-zinc-600 font-semibold">Risk tier</p>
              {RISK_TIERS.map((t) => (
                <div key={t} className="flex items-center gap-2">
                  <span
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: tierColor(t) ?? undefined }}
                  />
                  <span>{tierLabel(t)}</span>
                </div>
              ))}
            </div>
          )}

          {/* Clusters present on the canvas */}
          {clusters.length > 0 && (
            <div className="space-y-1">
              <p className="text-zinc-600 font-semibold">Clusters</p>
              {clusters.map((c) => {
                const colour = clusterColor(c.cluster_id);
                return (
                  <div key={c.cluster_id} className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-2.5 h-2.5 rounded-sm shrink-0"
                      style={{ backgroundColor: colour ?? "#52525b" }}
                    />
                    <span className="truncate">
                      {c.cluster_label ?? `cluster-${c.cluster_id}`}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
