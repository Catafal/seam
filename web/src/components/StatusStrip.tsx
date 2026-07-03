/**
 * StatusStrip — bottom server-admin status bar (#274).
 *
 * WHY at the bottom (demoted from the header):
 *   The PRD "Status strip (server admin, demoted)" section explicitly moves
 *   index stats out of the header. The header is for navigation only.
 *   Stats are operational metadata — useful but not part of every interaction.
 *   Demoting to the bottom preserves full header width for search + tabs.
 *
 * Layout (always one fixed-height row, no layout shift on stale toggle):
 *   [counts · indexed X ago]           [amber dot · index stale — run seam sync]
 *
 * PRD design principles honored:
 *   - "Spend boldness in one place": amber is the ONLY accent element here.
 *     It fires ONLY for a real stale warning (SEAM_STALENESS_CHECK=on verdict).
 *   - "Copy from the user side": "run seam sync" names the fix the user should take.
 *   - "Restraint": fresh state is quiet zinc, no color accents at all.
 *   - "No layout shift": the strip always occupies a fixed single-line row;
 *     the stale indicator slot is always reserved (hidden with visibility:hidden
 *     when fresh rather than display:none so the row never grows).
 */

import { useStatus } from "../api/hooks";

// ── Utility: relative time formatter ─────────────────────────────────────────

/**
 * Format a last_indexed timestamp as a human-friendly relative string.
 * Extracted here from App.tsx so the StatusBadge logic in the header can be
 * fully removed — this component is the single owner of the relative-time display.
 *
 * @param ts  ISO 8601 timestamp string, or null if never indexed.
 * @returns   "just now" / "Nm ago" / "Nh ago" / "Nd ago" / "never"
 */
function formatRelative(ts: string | null | undefined): string {
  if (!ts) return "never";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    const diff = Date.now() - d.getTime();
    const secs = Math.round(diff / 1000);
    if (secs < 60) return "just now";
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  } catch {
    return ts;
  }
}

// ── StatusStrip ───────────────────────────────────────────────────────────────

/**
 * A thin monospace bottom strip that shows:
 *   - Symbol / edge / cluster counts + relative last-indexed time (always).
 *   - An amber dot + "index stale — run seam sync" ONLY when stale=true.
 *
 * Loading: shows a pulsing skeleton placeholder row.
 * Error: shows "no index" in muted red (same wording as the old StatusBadge).
 *
 * The component is self-contained — it calls useStatus() directly rather than
 * accepting props, mirroring the former StatusBadge pattern. This keeps App.tsx
 * from needing to plumb status data down to the strip explicitly.
 */
export function StatusStrip() {
  const { data, isLoading, isError } = useStatus();

  // ── Loading ──────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div
        className="shrink-0 h-7 flex items-center px-5 border-t border-zinc-800 bg-zinc-950"
        role="status"
        aria-label="Loading index statistics"
        aria-live="polite"
      >
        <span
          className="text-xs text-zinc-600 font-mono animate-pulse"
          aria-hidden="true"
        >
          ···
        </span>
      </div>
    );
  }

  // ── Error ────────────────────────────────────────────────────────────────
  if (isError || !data) {
    return (
      <div
        className="shrink-0 h-7 flex items-center px-5 border-t border-zinc-800 bg-zinc-950"
        role="status"
        aria-label="Index status error"
      >
        <span
          className="text-xs text-red-500/70 font-mono"
          title="Could not reach seam serve"
        >
          no index
        </span>
      </div>
    );
  }

  // ── Loaded ───────────────────────────────────────────────────────────────
  const { symbol_count, edge_count, cluster_count, last_indexed, stale } = data;

  return (
    <div
      className="shrink-0 h-7 flex items-center justify-between px-5 border-t border-zinc-800 bg-zinc-950"
      role="status"
      aria-label="Index statistics"
      aria-live="polite"
    >
      {/* Left: index counts + last-indexed relative time */}
      <span
        className="text-xs text-zinc-600 font-mono tabular-nums select-none"
        aria-label="index statistics"
      >
        {symbol_count.toLocaleString()} symbols
        {" · "}
        {edge_count.toLocaleString()} edges
        {" · "}
        {cluster_count} clusters
        {" · "}
        <span className="text-zinc-700">
          indexed {formatRelative(last_indexed)}
        </span>
      </span>

      {/* Right: stale warning — always reserves the slot to prevent layout shift.
          PRD: "spend boldness in one place" — amber fires ONLY for this real signal.
          Copy: "run seam sync" names the fix from the user's perspective.
          data-testid="stale-indicator" is the stable test hook (class names can
          change with Tailwind refactors; data attributes are stable). */}
      {stale ? (
        <span
          className="flex items-center gap-1.5 text-xs text-amber-400 font-mono"
          data-testid="stale-indicator"
          aria-live="assertive"
          role="alert"
          title={data.stale_reason ?? "Index may be out of date"}
        >
          {/* Amber dot — the single accent element in this strip */}
          <span
            className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0"
            aria-hidden="true"
          />
          index stale — run seam sync
        </span>
      ) : (
        /* Reserve the exact same dimensions as the stale indicator so the strip
           height never shifts when the stale state toggles. No text is rendered
           (just a transparent box of the same ~20px width) — queryByText(/stale/)
           must return null in tests. aria-hidden so screen readers ignore it. */
        <span
          className="flex items-center h-4 w-48 shrink-0"
          aria-hidden="true"
        />
      )}
    </div>
  );
}
