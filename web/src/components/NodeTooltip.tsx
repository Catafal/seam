/**
 * NodeTooltip — a floating glass-card tooltip for the hovered node.
 *
 * Uses @react-three/drei <Html> so the tooltip tracks the 3D position of
 * the node while rendering as an HTML overlay.  `pointerEvents: "none"`
 * prevents the tooltip from intercepting click/hover events.
 *
 * Contents (reference §2 "Tooltip"):
 *   • KIND_COLORS dot   — quick visual kind indicator
 *   • bareName          — short bare identifier
 *   • full name         — qualified symbol name
 *   • file_path         — source file (basename only to save space)
 *   • kind (label)      — symbol kind label
 *
 * Reference: docs/prd/phase11-p2-1-3d-constellation-reference.md §2
 */

import { Html } from "@react-three/drei";

import { KIND_COLORS, DEFAULT_KIND_COLOR } from "../lib/constellationColors";
import { bareName } from "./NodeLabels";
import type { LayoutNode } from "../lib/layoutTypes";

// ── NodeTooltip ───────────────────────────────────────────────────────────────

interface NodeTooltipProps {
  node: LayoutNode;
}

/**
 * Renders a glass-card tooltip anchored to the 3D position of `node`.
 *
 * The tooltip is pointer-events-none so it never blocks mouse/touch events
 * from reaching the canvas (crucial for orbit + click behaviour).
 *
 * The <Html> component from drei handles world→screen projection and keeps
 * the tooltip visible even when the camera orbits.
 */
export function NodeTooltip({ node }: NodeTooltipProps) {
  const kindColor = KIND_COLORS[node.label] ?? DEFAULT_KIND_COLOR;
  const bare = bareName(node.name);
  const fileName = node.file_path
    ? node.file_path.split("/").pop() ?? node.file_path
    : null;

  return (
    <Html
      position={[node.x, node.y + node.size * 2.5, node.z]}
      style={{ pointerEvents: "none" }}
      center
      distanceFactor={600}
    >
      <div
        className={
          "bg-zinc-900/90 backdrop-blur-sm border border-zinc-700 " +
          "rounded-lg px-3 py-2 text-xs font-mono whitespace-nowrap " +
          "shadow-xl min-w-[120px]"
        }
        style={{ pointerEvents: "none" }}
      >
        {/* Kind indicator dot + bare name */}
        <div className="flex items-center gap-1.5 mb-1">
          <span
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: kindColor }}
            aria-hidden
          />
          <span className="text-white font-semibold truncate max-w-[180px]">
            {bare}
          </span>
        </div>

        {/* Full qualified name (if different from bareName) */}
        {node.name !== bare && (
          <div className="text-zinc-400 truncate max-w-[200px] text-[10px]">
            {node.name}
          </div>
        )}

        {/* Kind label */}
        <div className="text-zinc-500 mt-0.5">{node.label}</div>

        {/* File path (basename) */}
        {fileName && (
          <div className="text-zinc-600 text-[10px] mt-0.5 truncate max-w-[200px]">
            {fileName}
          </div>
        )}
      </div>
    </Html>
  );
}
