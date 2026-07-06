/**
 * DetailPanel — right-side panel for selected symbol detail.
 *
 * Shown when the user single-clicks a node on the GraphCanvas.
 * Calls useSymbol(name) and renders:
 *   - Symbol name (heading)
 *   - All definitions (file:line), to handle homonyms
 *   - Signature from the first definition (wraps cleanly)
 *   - Docstring from the first definition (clamped with show-more for long text)
 *   - Source section (B4) — indexed source via useSnippet, collapsible
 *   - WHY/HACK/NOTE/TODO/FIXME comments (with kind badge)
 *   - Callers grouped by edge kind with clickable rows (S3)
 *   - Callees grouped by edge kind with clickable rows (S3)
 *   - Cluster info (id, label with colour swatch via clusterColor)
 *
 * S3 additions:
 *   - Full caller/callee rows instead of count summary
 *   - Rows grouped by edge kind (call, import, reads, writes, holds, uses, …)
 *   - Each row is clickable → calls onNavigate(name) → drives selectedSymbol only
 *     (the graph view / centerSymbol is NOT changed)
 *   - Each row shows a confidence badge (EXTRACTED / INFERRED / AMBIGUOUS)
 *   - Qualified names show last segment; full name is the title tooltip
 *   - Per-group cap (GROUP_CAP=5) with a "show N more" expander
 *   - Docstring clamped at DOCSTRING_CHAR_LIMIT with a show-more toggle
 *
 * B4 additions:
 *   - Source section fed by useSnippet(buildSnippetSelector(symbol, firstDef))
 *   - Collapsed by default; toggle to expand/collapse
 *   - Renders source in a bounded max-height <pre> (no syntax-highlight dep)
 *   - Truncation note when SnippetResponse.truncated is set
 *   - Amber freshness note when freshness.index_stale OR !file_hash_matches
 *   - Clear empty states: not-found, ambiguous, and reason/message cases
 *
 * States:
 *   - selectedSymbol=null → empty-state placeholder ("Select a node…")
 *   - isLoading          → loading indicator
 *   - data               → full detail rendering
 */

import { useState, useCallback } from "react";
import { clusterColor } from "../lib/clusterColor";
import { useSymbol, useClusters, useSnippet } from "../api/hooks";
import { buildSnippetSelector } from "../lib/buildSnippetSelector";
import { ClusterLegend } from "./ClusterLegend";
import type { SymbolDefinition, WhyComment, CallerRef, SnippetResponse } from "../api/schema-types";

// ── Layout constants ──────────────────────────────────────────────────────────

/** Default panel width — matches the former fixed Tailwind w-72 (18rem = 288px). */
const DEFAULT_PANEL_WIDTH = 288;

/**
 * Per-group caller/callee row cap before the "show N more" expander appears.
 * Prevents hub symbols from turning the panel into an endless scroll.
 */
const GROUP_CAP = 5;

/**
 * Character limit before the docstring is clamped with a show-more toggle.
 * 200 chars ≈ 2–3 comfortable lines in the narrow panel.
 */
const DOCSTRING_CHAR_LIMIT = 200;

// ── Sub-components ─────────────────────────────────────────────────────────────

/**
 * Renders one definition row: file path (shortened to last 3 segments)
 * and line number. Each row is a separate entry for homonym visibility.
 */
function DefinitionRow({ def }: { def: SymbolDefinition }) {
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

/**
 * Confidence badge for a caller/callee row.
 * EXTRACTED → green (high-certainty statically resolved edge)
 * INFERRED  → sky  (heuristic / type-inferred edge)
 * AMBIGUOUS → amber (multiple candidates; proximity pick)
 */
function ConfidenceBadge({ confidence }: { confidence: string }) {
  const cls =
    confidence === "EXTRACTED"
      ? "bg-emerald-900/50 text-emerald-300"
      : confidence === "AMBIGUOUS"
        ? "bg-amber-900/50 text-amber-300"
        : "bg-sky-900/50 text-sky-300"; // INFERRED (default)

  return (
    <span
      className={`shrink-0 text-[8px] font-mono font-bold px-1 py-0.5 rounded ${cls}`}
      title={confidence}
    >
      {confidence}
    </span>
  );
}

interface CallerRowProps {
  entry: CallerRef;
  onNavigate: (name: string) => void;
}

/**
 * One clickable caller/callee row.
 *
 * Clicking calls onNavigate(entry.name) — updates SELECTED symbol only
 * (the graph view / centerSymbol stays unchanged).
 *
 * WHY last-segment display: qualified names like "Reader.load" are usually
 * clear from context (the group heading already names the kind), so showing
 * "load" keeps the panel compact. The full name is the title tooltip.
 *
 * Mirrors the compact clickable-row pattern used across the panels.
 */
function CallerRow({ entry, onNavigate }: CallerRowProps) {
  const handleClick = useCallback(
    () => onNavigate(entry.name),
    [entry.name, onNavigate],
  );

  // Trim qualified names: "Reader.load" → "load"; bare names stay unchanged
  const display = entry.name.includes(".")
    ? entry.name.split(".").pop()!
    : entry.name;
  const isQualified = display !== entry.name;

  return (
    <li>
      <button
        onClick={handleClick}
        title={entry.name}
        className="
          w-full text-left px-2 py-0.5 flex items-center gap-1.5 rounded
          hover:bg-zinc-800/80 transition-colors group
        "
      >
        {/* Qualified indicator dot — visual cue that this is a method of a class */}
        {isQualified && (
          <span className="font-mono text-[9px] text-zinc-600 shrink-0">·</span>
        )}
        <span className="flex-1 min-w-0 text-[11px] text-zinc-300 group-hover:text-zinc-100 font-mono truncate">
          {display}
        </span>
        <ConfidenceBadge confidence={entry.confidence} />
      </button>
    </li>
  );
}

interface EdgeKindGroupProps {
  kind: string;
  entries: CallerRef[];
  onNavigate: (name: string) => void;
}

/**
 * A group of caller/callee rows under one edge-kind label.
 *
 * Caps at GROUP_CAP rows; excess is hidden behind a "show N more" toggle.
 * WHY per-group cap: hub symbols can have 100+ callers of the same kind — showing
 * all of them inline would make the panel unusable. The cap surfaces the most
 * relevant rows first (order is determined by the API) with a cheap escape hatch.
 */
function EdgeKindGroup({ kind, entries, onNavigate }: EdgeKindGroupProps) {
  const [expanded, setExpanded] = useState(false);

  const visible = expanded ? entries : entries.slice(0, GROUP_CAP);
  const hiddenCount = entries.length - GROUP_CAP;

  return (
    <div className="mb-2">
      {/* Edge-kind sub-label, visually distinct from the section header */}
      <p className="text-[9px] font-semibold uppercase tracking-widest text-zinc-600 mb-0.5 px-2">
        {kind}
      </p>
      <ul className="space-y-0.5">
        {visible.map((e) => (
          <CallerRow key={e.name} entry={e} onNavigate={onNavigate} />
        ))}
      </ul>
      {/* Show-more expander — only renders when the group is capped */}
      {!expanded && hiddenCount > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="text-[10px] text-sky-500/80 hover:text-sky-300 px-2 py-0.5 transition-colors"
        >
          show {hiddenCount} more
        </button>
      )}
    </div>
  );
}

interface GroupedRefsProps {
  refs: CallerRef[];
  label: string;
  onNavigate: (name: string) => void;
}

/**
 * Renders a set of CallerRefs grouped by edge kind.
 *
 * WHY group by kind: an upstream dependent via "reads" and one via "call" have
 * completely different change-risk implications. Grouping surfaces this structure
 * so the developer immediately sees whether a change will break callers,
 * readers, or data-composition owners.
 */
function GroupedRefs({ refs, label, onNavigate }: GroupedRefsProps) {
  if (refs.length === 0) return null;

  // Group entries by kind, preserving encounter order within each group
  const groups = new Map<string, CallerRef[]>();
  for (const ref of refs) {
    const group = groups.get(ref.kind) ?? [];
    group.push(ref);
    groups.set(ref.kind, group);
  }

  return (
    <section>
      <SectionLabel>
        {label} ({refs.length})
      </SectionLabel>
      {[...groups.entries()].map(([kind, entries]) => (
        <EdgeKindGroup
          key={kind}
          kind={kind}
          entries={entries}
          onNavigate={onNavigate}
        />
      ))}
    </section>
  );
}

interface DocstringSectionProps {
  text: string;
}

/**
 * Docstring with a clamp + show-more toggle for long text.
 *
 * WHY clamp: some docstrings (especially multi-paragraph ones) can consume
 * the entire panel height, pushing callers/callees off screen. Clamping at
 * DOCSTRING_CHAR_LIMIT keeps the first-glance view compact while making the
 * full text accessible via the toggle.
 */
function DocstringSection({ text }: DocstringSectionProps) {
  const [expanded, setExpanded] = useState(false);

  const isLong = text.length > DOCSTRING_CHAR_LIMIT;
  const shown =
    !expanded && isLong ? text.slice(0, DOCSTRING_CHAR_LIMIT) + "…" : text;

  return (
    <section>
      <SectionLabel>Docstring</SectionLabel>
      <p className="text-[11px] text-zinc-400 leading-relaxed">{shown}</p>
      {isLong && (
        <button
          onClick={() => setExpanded((e) => !e)}
          aria-label={expanded ? "show less" : "show more"}
          className="text-[10px] text-sky-500/80 hover:text-sky-300 transition-colors mt-0.5"
        >
          {expanded ? "show less" : "show more"}
        </button>
      )}
    </section>
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

// ── B4: Source section ─────────────────────────────────────────────────────────

interface SourceSectionProps {
  /** Resolved snippet payload from useSnippet (undefined while loading). */
  snippet: SnippetResponse | undefined;
  /** True while the snippet query is still in-flight. */
  isLoading: boolean;
}

/**
 * Collapsible Source section that shows the indexed source for the selected
 * symbol. Collapsed by default so it does not push callers/callees off screen
 * on hub symbols with long implementations.
 *
 * WHY a dedicated sub-component: isolates all snippet-rendering logic and
 * empty-state copy so the main DetailPanel stays readable.
 */
function SourceSection({ snippet, isLoading }: SourceSectionProps) {
  const [expanded, setExpanded] = useState(false);

  const isStale =
    snippet?.freshness != null &&
    (snippet.freshness.index_stale || !snippet.freshness.file_hash_matches);

  const truncation = snippet?.truncated ?? null;

  return (
    <section>
      {/* ── Collapsible header toggle ──────────────────────────────────── */}
      <button
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="flex items-center gap-1 w-full text-left mb-1 group"
      >
        <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 group-hover:text-zinc-400 transition-colors">
          Source
        </h3>
        {/* Chevron indicator — down when collapsed, up when expanded */}
        <span className="text-[9px] text-zinc-700 group-hover:text-zinc-500">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {expanded && (
        <div className="space-y-1">
          {/* Amber freshness note — shown when index or file is stale */}
          {isStale && (
            <p className="text-[10px] text-amber-400 leading-snug">
              Source may not match disk — re-run <code className="font-mono">seam sync</code> to refresh.
            </p>
          )}

          {/* Loading state */}
          {isLoading && (
            <p className="text-[11px] text-zinc-500 animate-pulse">Loading source…</p>
          )}

          {/* Resolved: found */}
          {!isLoading && snippet?.found && snippet.source != null && (
            <>
              {truncation && (
                <p className="text-[10px] text-zinc-500 leading-snug">
                  showing {truncation.returned_line_count} of {truncation.original_line_count} lines
                </p>
              )}
              {/* Plain monospaced source — no syntax-highlight dep (mirrors signature <pre>) */}
              <pre className="text-[10px] text-zinc-300 font-mono whitespace-pre overflow-x-auto leading-snug bg-zinc-900 border border-zinc-800 rounded px-2 py-1.5 max-h-64 overflow-y-auto">
                {snippet.source}
              </pre>
            </>
          )}

          {/* Resolved: not found — ambiguous case */}
          {!isLoading && snippet && !snippet.found && snippet.ambiguous && (
            <p className="text-[11px] text-zinc-500 leading-snug">
              Several definitions match — pick one from Definitions above.
            </p>
          )}

          {/* Resolved: not found — with human-readable message/reason */}
          {!isLoading && snippet && !snippet.found && !snippet.ambiguous && (snippet.message || snippet.reason) && (
            <p className="text-[11px] text-zinc-500 leading-snug">
              {snippet.message ?? snippet.reason}
            </p>
          )}

          {/* Resolved: not found — generic fallback */}
          {!isLoading && snippet && !snippet.found && !snippet.ambiguous && !snippet.message && !snippet.reason && (
            <p className="text-[11px] text-zinc-500 leading-snug">
              No indexed source for this symbol. Try re-running{" "}
              <code className="font-mono text-zinc-400">seam init</code>.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export interface DetailPanelProps {
  /** The symbol name to show detail for, or null when nothing is selected. */
  selectedSymbol: string | null;
  /**
   * Explicit panel width in pixels (set by the ResizeHandle in App.tsx).
   * Applies to ALL render branches so width never snaps when loading/error states change.
   * Defaults to DEFAULT_PANEL_WIDTH (288px = w-72) when omitted.
   */
  width?: number;
  /**
   * Called when the user clicks a caller/callee row.
   * Updates the SELECTED symbol only — does NOT change the graph's center symbol
   * or lose the current graph view. This matches the 3D NavRow pattern in
   * the 3D identity card (NodeIdentityCard).
   */
  onNavigate?: (name: string) => void;
}

/**
 * Right-side detail panel driven by a selected symbol name.
 * Delegates data fetching to useSymbol() (TanStack Query hook).
 */
export function DetailPanel({ selectedSymbol, width, onNavigate }: DetailPanelProps) {
  const { data, isLoading } = useSymbol(selectedSymbol);
  // useClusters is always-enabled (TanStack Query caches it from the landing page call)
  const { data: clusters } = useClusters();

  // B4: Snippet for the Source section — built from the first definition
  // to match exactly what the panel is displaying (homonym-safe).
  // enabled only when a symbol is selected (the panel only mounts then).
  const firstDefForSnippet = data?.definitions[0] ?? null;
  const snippetSelector = buildSnippetSelector(selectedSymbol, firstDefForSnippet);
  const { data: snippet, isLoading: snippetLoading } = useSnippet(
    snippetSelector ?? {},
    snippetSelector !== undefined,
  );

  // Stable no-op when onNavigate is not provided — avoids null checks in CallerRow
  const handleNavigate = useCallback(
    (name: string) => { onNavigate?.(name); },
    [onNavigate],
  );

  const panelStyle: React.CSSProperties = { width: width ?? DEFAULT_PANEL_WIDTH };

  // ── Null state ─────────────────────────────────────────────────────────────
  if (selectedSymbol === null) {
    return (
      <aside
        className="shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col items-center justify-center text-center p-6"
        style={panelStyle}
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
        className="shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col items-center justify-center"
        style={panelStyle}
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
        className="shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col items-center justify-center p-6"
        style={panelStyle}
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
      className="shrink-0 border-l border-zinc-800 bg-zinc-950 overflow-y-auto flex flex-col"
      style={panelStyle}
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

        {/* ── Signature — wraps cleanly, no truncation ───────────────────── */}
        {firstDef?.signature && (
          <section>
            <SectionLabel>Signature</SectionLabel>
            <pre className="text-[10px] text-zinc-300 font-mono whitespace-pre-wrap break-all leading-snug bg-zinc-900 border border-zinc-800 rounded px-2 py-1.5">
              {firstDef.signature}
            </pre>
          </section>
        )}

        {/* ── Docstring — clamped with show-more for long text ──────────── */}
        {firstDef?.docstring && (
          <DocstringSection text={firstDef.docstring} />
        )}

        {/* ── Source section (B4) — indexed source, collapsible ─────────
            Placed below signature/docstring so the most-readable metadata
            (signature + docstring) is always visible above the fold. */}
        <SourceSection snippet={snippet} isLoading={snippetLoading} />

        {/* ── Definitions (all — homonym support) ───────────────────────── */}
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

        {/* ── Callers — grouped by edge kind, clickable rows (S3) ─────────
            Clicking a row updates SELECTED (not center) so the graph is kept. */}
        <GroupedRefs
          refs={data.callers}
          label="Callers"
          onNavigate={handleNavigate}
        />

        {/* ── Callees — same structure as callers ───────────────────────── */}
        <GroupedRefs
          refs={data.callees}
          label="Callees"
          onNavigate={handleNavigate}
        />

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
