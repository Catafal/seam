/**
 * ChangesDrawer — git working-tree changes view (F5).
 *
 * A right-side drawer (toggled from the header) that lists the symbols touched by
 * the current `git diff`, the overall risk level, new files, and an ambiguity
 * notice. Clicking a changed symbol centers the canvas on it.
 *
 * Reuses useChanges (GET /api/changes). Non-git repos return 400 → the hook
 * surfaces an error and the drawer shows a "not a git repo" notice instead of
 * crashing. The hook is enabled only while the drawer is open (no git call on load).
 */

import { useChanges } from "../api/hooks";
import type { ChangedSymbol } from "../api/schema-types";
import { filterCodeFiles } from "../lib/codeFileFilter";
import { X, GitBranch } from "lucide-react";

/** Risk level → badge classes. Covers the engine's rollup vocabulary. */
function riskBadgeClass(level: string): string {
  switch (level) {
    case "critical":
      return "bg-red-900/60 text-red-300";
    case "high":
      return "bg-orange-900/60 text-orange-300";
    case "medium":
      return "bg-amber-900/60 text-amber-300";
    case "low":
      return "bg-sky-900/60 text-sky-300";
    default: // "none" / unknown
      return "bg-zinc-800 text-zinc-400";
  }
}

function ChangedRow({
  sym,
  onSelect,
}: {
  sym: ChangedSymbol;
  onSelect: (name: string) => void;
}) {
  const shortFile = sym.file ? sym.file.split("/").slice(-2).join("/") : "—";
  return (
    <li>
      <button
        onClick={() => onSelect(sym.name)}
        className="w-full text-left px-3 py-2 hover:bg-zinc-800 transition-colors flex flex-col gap-0.5"
      >
        <span className="text-xs font-semibold text-zinc-100 truncate">{sym.name}</span>
        <span className="text-[10px] text-zinc-500 font-mono truncate" title={sym.file ?? ""}>
          {shortFile}:{sym.start_line}
        </span>
      </button>
    </li>
  );
}

export interface ChangesDrawerProps {
  open: boolean;
  onClose: () => void;
  onSelectSymbol: (name: string) => void;
}

export function ChangesDrawer({ open, onClose, onSelectSymbol }: ChangesDrawerProps) {
  const { data, isLoading, isError, error } = useChanges("working", open);

  if (!open) return null;

  return (
    <aside
      className="w-80 shrink-0 border-l border-zinc-800 bg-zinc-950 overflow-y-auto flex flex-col"
      aria-label="Changes drawer"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800 sticky top-0 bg-zinc-950">
        <GitBranch className="w-4 h-4 text-zinc-400" />
        <h2 className="text-sm font-semibold text-zinc-100 flex-1">Working changes</h2>
        {data && (
          <span
            className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded uppercase ${riskBadgeClass(data.risk_level)}`}
          >
            {data.risk_level}
          </span>
        )}
        <button
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
          aria-label="Close changes drawer"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Loading */}
      {isLoading && (
        <p className="text-xs text-zinc-500 animate-pulse p-4">Computing diff…</p>
      )}

      {/* Error — typically NOT_A_GIT_REPO */}
      {isError && (
        <div className="p-4 text-xs text-zinc-500">
          <p className="text-zinc-400 mb-1">Couldn't compute changes.</p>
          <p className="text-zinc-600">{(error as Error)?.message ?? "Unknown error"}</p>
          <p className="text-zinc-600 mt-2">
            If this isn't a git repo, run{" "}
            <code className="text-zinc-400">git init</code> to use this view.
          </p>
        </div>
      )}

      {/* Data */}
      {data && (() => {
        // Filter to code files only — non-indexed files (docs, configs, logs) have no
        // symbols in the graph and produce misleading entries in the drawer.
        const codeSymbols = filterCodeFiles(data.changed_symbols);
        return (
        <div className="flex-1">
          {data.ambiguous_warning && (
            <p className="text-[10px] text-amber-400/80 px-4 py-2 border-b border-zinc-800/60">
              ⚠ Some impacted symbols are ambiguous (name collisions) — risk may be over/understated.
            </p>
          )}

          {codeSymbols.length === 0 && data.new_files.length === 0 ? (
            <p className="text-xs text-zinc-500 p-4">No changes in the working tree.</p>
          ) : (
            <>
              {codeSymbols.length > 0 && (
                <section>
                  <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 px-3 pt-3 pb-1">
                    Changed symbols ({codeSymbols.length})
                  </h3>
                  <ul className="divide-y divide-zinc-900">
                    {codeSymbols.map((s, i) => (
                      <ChangedRow key={`${s.name}:${s.file}:${i}`} sym={s} onSelect={onSelectSymbol} />
                    ))}
                  </ul>
                </section>
              )}

              {data.new_files.length > 0 && (
                <section className="border-t border-zinc-800/60 mt-1">
                  <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 px-3 pt-3 pb-1">
                    New files ({data.new_files.length})
                  </h3>
                  <ul className="px-3 pb-3 space-y-1">
                    {data.new_files.map((f) => (
                      <li key={f} className="text-[10px] text-zinc-500 font-mono truncate" title={f}>
                        {f}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {data.partial && (
                <p className="text-[10px] text-zinc-600 px-4 py-2">
                  Result truncated (too many changed symbols).
                </p>
              )}
            </>
          )}
        </div>
        );
      })()}
    </aside>
  );
}
