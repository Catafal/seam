/**
 * NodeIdentityCard — lightweight identity panel for a selected 3D constellation node.
 *
 * WHY this replaces the old NodeDetailPanel (#361):
 *   The 3D constellation is an ORIENTATION view (macro-topology: hub / mesh /
 *   chain), NOT an inspection view — inspection is the 2D neighborhood's job,
 *   where clicking actually navigates and the real DetailPanel lives. The old
 *   NodeDetailPanel re-fetched /api/symbol and rendered clickable caller/callee/
 *   peer lists: it duplicated the 2D inspector AND crashed on an unguarded field
 *   for some symbols ("Cannot read properties of undefined").
 *
 *   This card answers only the question the globe actually raises — "what is this
 *   bright star?" — from fields already present on the LayoutNode (name, kind,
 *   file). It performs NO network fetch, so there is nothing to crash. For real
 *   inspection it offers exactly ONE explicit door: "Open in neighborhood →",
 *   which hands off to the 2D view centered on this symbol.
 *
 *   This preserves the #263 contract that a 3D *click* never navigates: the click
 *   isolates + flies the camera; navigation happens only via this explicit button.
 *
 * Props:
 *   node             — the selected LayoutNode (cosmetic + identity fields)
 *   connectionCount  — direct-neighbor count, computed locally by ConstellationTab
 *                      from the highlight set (NO fetch)
 *   onOpenInNeighborhood — hands off to the 2D neighborhood centered on node.name
 *   onClose          — clears the selection (× button)
 */

import { useCallback } from "react";
import { KIND_COLORS, DEFAULT_KIND_COLOR } from "../lib/constellationColors";
import type { LayoutNode } from "../lib/layoutTypes";

interface NodeIdentityCardProps {
  node: LayoutNode;
  connectionCount: number;
  onOpenInNeighborhood: (name: string) => void;
  onClose: () => void;
}

/** Basename of a path, guarding null/empty (never throws). */
function fileBasename(path: string | null | undefined): string | null {
  if (!path) return null;
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

export function NodeIdentityCard({
  node,
  connectionCount,
  onOpenInNeighborhood,
  onClose,
}: NodeIdentityCardProps) {
  // Defensive reads: a LayoutNode always carries these, but guard anyway so a
  // malformed node can never crash the orientation view (the whole point of #361).
  const name = node?.name ?? "(unknown)";
  const kind = node?.label ?? "symbol";
  const kindColor = KIND_COLORS[kind] ?? DEFAULT_KIND_COLOR;
  const basename = fileBasename(node?.file_path);

  const handleOpen = useCallback(
    () => onOpenInNeighborhood(name),
    [onOpenInNeighborhood, name],
  );

  return (
    <aside
      className="w-64 shrink-0 flex flex-col bg-zinc-900/95 border-l border-zinc-800 overflow-hidden"
      aria-label="Node identity"
    >
      {/* ── Header: kind dot + name + kind + file + close ─────────────────── */}
      <div className="flex items-start gap-2 px-3 py-2.5 border-b border-zinc-800">
        <div
          className="mt-0.5 w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: kindColor }}
          aria-hidden="true"
        />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-zinc-100 truncate" title={name}>
            {name}
          </p>
          <p className="text-[10px] text-zinc-500">{kind}</p>
          {basename && (
            <p
              className="text-[10px] text-zinc-600 truncate"
              title={node.file_path ?? undefined}
            >
              {basename}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Clear selection"
          className="text-zinc-500 hover:text-zinc-300 transition-colors shrink-0 px-1"
        >
          ✕
        </button>
      </div>

      {/* ── Body: connection count + the single explicit inspection door ───── */}
      <div className="flex-1 flex flex-col gap-3 px-3 py-3 text-xs">
        <p className="text-zinc-400">
          <span className="font-mono tabular-nums text-zinc-200">{connectionCount}</span>{" "}
          {connectionCount === 1 ? "connection" : "connections"}
        </p>

        {/* The ONE door to real inspection. The 3D view orients; the 2D
            neighborhood inspects. This explicit hand-off is not a click-navigate
            (the click only isolates) — it is a deliberate user action. */}
        <button
          onClick={handleOpen}
          className="
            w-full text-left px-3 py-2 rounded-md
            bg-sky-500/15 border border-sky-500/40 text-sky-300
            hover:bg-sky-500/25 hover:border-sky-500/60 transition-colors
          "
        >
          Open in neighborhood →
        </button>

        <p className="text-[10px] text-zinc-600 leading-snug">
          The globe shows how the codebase is shaped. Open the neighborhood to see
          this symbol&apos;s callers, callees and source.
        </p>
      </div>
    </aside>
  );
}
