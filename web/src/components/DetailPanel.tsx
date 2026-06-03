/**
 * DetailPanel — right-side panel for selected symbol detail.
 *
 * Shown when the user single-clicks a node on the GraphCanvas.
 * Calls useSymbol(name) and renders:
 *   - Symbol name (heading)
 *   - All definitions (file:line), to handle homonyms — multiple
 *     files may define the same name (see CONTEXT.md on homonym-collapse)
 *   - Signature from the first definition
 *   - Docstring from the first definition
 *   - WHY/HACK/NOTE/TODO/FIXME comments (with kind badge)
 *   - Callers count + callees count (as summary, not full lists)
 *   - Cluster info (id, label with colour swatch via clusterColor)
 *
 * States:
 *   - selectedSymbol=null → empty-state placeholder ("Select a node…")
 *   - isLoading          → loading indicator
 *   - data               → full detail rendering
 *
 * WHY counts not full lists for callers/callees: showing the full list
 * of caller/callee names would make the panel very tall for hub symbols
 * (init_db has 30+ callers). The count gives useful signal; the user can
 * use seam_impact or the canvas expand for the full blast radius.
 */

import { clusterColor } from "../lib/clusterColor";
import { useSymbol, useClusters } from "../api/hooks";
import { ClusterLegend } from "./ClusterLegend";
import type { SymbolDefinition, WhyComment } from "../api/schema-types";

// ── Sub-components ─────────────────────────────────────────────────────────────

/**
 * Renders one definition row: file path (shortened to last 3 segments)
 * and line number. Each row is a separate entry for homonym visibility.
 */
function DefinitionRow({ def }: { def: SymbolDefinition }) {
  // Show a shortened file path: last 3 path segments are enough context
  const segments = def.file.split("/");
  const shortPath = segments.slice(-3).join("/");

  return (
    <li className="flex items-baseline gap-1 text-[11px] font-mono">
      <span
        className="text-zinc-400 truncate flex-1 min-w-0"
        title={def.file}
      >
        {shortPath}
      </span>
      <span className="text-zinc-600 shrink-0">:{def.line}</span>
    </li>
  );
}

/**
 * Renders a single WHY/HACK/NOTE comment with a kind badge.
 * Badge colours map to comment urgency:
 *   WHY/NOTE → informational (sky)
 *   HACK/TODO/FIXME → warning (amber)
 */
function CommentRow({ comment }: { comment: WhyComment }) {
  const isWarning =
    comment.kind === "HACK" ||
    comment.kind === "TODO" ||
    comment.kind === "FIXME";

  const badgeClass = isWarning
    ? "bg-amber-900/50 text-amber-300"
    : "bg-sky-900/50 text-sky-300";

  return (
    <li className="flex gap-2 items-start">
      <span
        className={`shrink-0 text-[9px] font-mono font-bold px-1 py-0.5 rounded ${badgeClass}`}
      >
        {comment.kind}
      </span>
      <span className="text-[11px] text-zinc-400 leading-snug">
        {comment.text}
      </span>
    </li>
  );
}

// ── Section header helper ──────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 mb-1">
      {children}
    </h3>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export interface DetailPanelProps {
  /** The symbol name to show detail for, or null when nothing is selected */
  selectedSymbol: string | null;
}

/**
 * Right-side detail panel driven by a selected symbol name.
 * Delegates data fetching to useSymbol() (TanStack Query hook).
 */
export function DetailPanel({ selectedSymbol }: DetailPanelProps) {
  const { data, isLoading } = useSymbol(selectedSymbol);
  // useClusters is always-enabled (TanStack Query caches it from the landing page call)
  // so this does not cause a duplicate network request when the user navigates to a symbol
  const { data: clusters } = useClusters();

  // ── Null state ─────────────────────────────────────────────────────────────
  if (selectedSymbol === null) {
    return (
      <aside
        className="w-72 shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col items-center justify-center text-center p-6"
        aria-label="Symbol detail panel"
      >
        <p className="text-xs text-zinc-600">
          Select a node to see details
        </p>
      </aside>
    );
  }

  // ── Loading state ──────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <aside
        className="w-72 shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col items-center justify-center"
        aria-label="Symbol detail panel"
      >
        <p className="text-xs text-zinc-500 animate-pulse">Loading…</p>
      </aside>
    );
  }

  // ── Not found (fetch resolved but no data) ─────────────────────────────────
  if (!data) {
    return (
      <aside
        className="w-72 shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col items-center justify-center p-6"
        aria-label="Symbol detail panel"
      >
        <p className="text-xs text-zinc-500">
          Symbol{" "}
          <code className="text-zinc-300 font-mono">{selectedSymbol}</code>{" "}
          not found in index.
        </p>
      </aside>
    );
  }

  // ── Full detail ────────────────────────────────────────────────────────────

  const firstDef = data.definitions[0] ?? null;
  const clusterColour = data.cluster ? clusterColor(data.cluster.id) : null;

  return (
    <aside
      className="w-72 shrink-0 border-l border-zinc-800 bg-zinc-950 overflow-y-auto flex flex-col"
      aria-label="Symbol detail panel"
    >
      {/* ── Name + cluster stripe ───────────────────────────────────────── */}
      <div className="flex items-stretch border-b border-zinc-800">
        {/* Left cluster colour stripe — visual identity marker */}
        {clusterColour && (
          <div
            className="w-1 shrink-0"
            style={{ backgroundColor: clusterColour }}
            aria-hidden="true"
          />
        )}
        <div className="px-4 py-3 flex-1 min-w-0">
          <h2
            className="text-sm font-semibold text-zinc-100 truncate"
            aria-label={data.name}
          >
            {data.name}
          </h2>
          {data.cluster && (
            <p className="text-[10px] text-zinc-500 truncate mt-0.5">
              {data.cluster.label ?? `cluster-${data.cluster.id}`}
            </p>
          )}
        </div>
      </div>

      <div className="px-4 py-3 space-y-4 flex-1">

        {/* ── Signature ─────────────────────────────────────────────────── */}
        {firstDef?.signature && (
          <section>
            <SectionLabel>Signature</SectionLabel>
            <pre className="text-[10px] text-zinc-300 font-mono whitespace-pre-wrap break-all leading-snug bg-zinc-900 border border-zinc-800 rounded px-2 py-1.5">
              {firstDef.signature}
            </pre>
          </section>
        )}

        {/* ── Docstring ─────────────────────────────────────────────────── */}
        {firstDef?.docstring && (
          <section>
            <SectionLabel>Docstring</SectionLabel>
            <p className="text-[11px] text-zinc-400 leading-relaxed">
              {firstDef.docstring}
            </p>
          </section>
        )}

        {/* ── Definitions (all of them — homonym support) ───────────────── */}
        <section>
          <SectionLabel>
            {data.definitions.length === 1
              ? "Definition"
              : `Definitions (${data.definitions.length})`}
          </SectionLabel>
          <ul className="space-y-0.5">
            {data.definitions.map((def, i) => (
              <DefinitionRow
                key={`${def.file}:${def.line}:${i}`}
                def={def}
              />
            ))}
          </ul>
        </section>

        {/* ── Callers / Callees summary ─────────────────────────────────── */}
        <section>
          <SectionLabel>References</SectionLabel>
          <div className="flex gap-4 text-[11px]">
            <div>
              <span className="text-zinc-500">Callers </span>
              <span className="text-zinc-300 font-mono font-semibold">
                {data.callers.length}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Callees </span>
              <span className="text-zinc-300 font-mono font-semibold">
                {data.callees.length}
              </span>
            </div>
          </div>
        </section>

        {/* ── WHY / HACK / NOTE comments ────────────────────────────────── */}
        {data.why.length > 0 && (
          <section>
            <SectionLabel>Comments</SectionLabel>
            <ul className="space-y-2">
              {data.why.map((c, i) => (
                <CommentRow
                  key={`${c.file}:${c.line}:${i}`}
                  comment={c}
                />
              ))}
            </ul>
          </section>
        )}

        {/* ── Cluster legend — all clusters as colour reference ─────────── */}
        {clusters && clusters.length > 0 && (
          <section className="border-t border-zinc-800 pt-3 mt-1">
            <SectionLabel>All Clusters</SectionLabel>
            <ClusterLegend clusters={clusters} />
          </section>
        )}

      </div>
    </aside>
  );
}
