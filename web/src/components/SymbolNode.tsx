/**
 * SymbolNode — custom React Flow node card for a code symbol.
 *
 * Renders a compact card showing:
 *   - A kind icon (lucide-react) on the left
 *   - Symbol name (bold)
 *   - Truncated signature below the name
 *   - A cluster colour stripe on the left edge (stable hash of cluster_id)
 *   - A homonym badge (×N) when definition_count > 1
 *   - A ring highlight when the node is the center of the neighborhood
 *
 * WHY a named export (not default): React Flow's nodeTypes map expects a
 * component reference. Named exports make the import unambiguous and stable.
 *
 * WHY SymbolNodeData is exported: GraphCanvas needs to construct the data
 * object when building RF nodes from the API response.
 */

import { clusterColor } from "../lib/clusterColor";
import {
  Box,
  Circle,
  Code2,
  FileCode2,
  Layers,
  Type,
  Zap,
} from "lucide-react";

/**
 * Data shape passed via React Flow's `node.data` for SymbolNode.
 *
 * WHY `& Record<string, unknown>`: @xyflow/react v12 constrains NodeData to
 * `Record<string, unknown>`. The index signature is required for compatibility
 * without losing the specific field types.
 */
export interface SymbolNodeData extends Record<string, unknown> {
  name: string;
  kind: string;
  signature: string | null;
  cluster_id: number | null;
  cluster_label: string | null;
  definition_count: number;
  /** True when this node is the center of the current neighborhood view */
  isCenter: boolean;
}

/** Map Seam kind strings to lucide icons (closed vocabulary from engine) */
function KindIcon({ kind }: { kind: string }) {
  const cls = "w-3.5 h-3.5 shrink-0 text-zinc-400";
  switch (kind) {
    case "function":
      return <Zap className={cls} aria-label="function" />;
    case "class":
      return <Layers className={cls} aria-label="class" />;
    case "method":
      return <Code2 className={cls} aria-label="method" />;
    case "type":
      return <Type className={cls} aria-label="type" />;
    case "interface":
      return <Circle className={cls} aria-label="interface" />;
    case "variable":
      return <Box className={cls} aria-label="variable" />;
    default:
      // Unknown kind — fallback to generic file icon
      return <FileCode2 className={cls} aria-label={kind || "symbol"} />;
  }
}

/** Props accepted by SymbolNode — mirrors React Flow NodeProps<SymbolNodeData> */
export interface SymbolNodeProps {
  data: SymbolNodeData;
}

/**
 * Custom React Flow node card for a code symbol.
 *
 * Kept intentionally small: all layout decisions live in Tailwind classes,
 * the cluster stripe is the only inline style (dynamic colour from hash).
 */
export function SymbolNode({ data }: SymbolNodeProps) {
  const {
    name,
    kind,
    signature,
    cluster_id,
    definition_count,
    isCenter,
  } = data;

  const colour = clusterColor(cluster_id);

  // Center node: sky-500 ring to emphasize it as the focal point
  const ringClass = isCenter
    ? "ring-2 ring-sky-500 ring-offset-1 ring-offset-zinc-900"
    : "";

  return (
    <div
      className={`
        relative flex items-stretch
        bg-zinc-900 border border-zinc-700 rounded-md
        min-w-[160px] max-w-[240px] overflow-hidden
        cursor-pointer select-none
        hover:border-zinc-500 transition-colors
        ${ringClass}
      `}
    >
      {/* Cluster colour stripe — left edge visual identity marker */}
      {colour !== null && (
        <div
          data-testid="cluster-stripe"
          className="w-1 shrink-0"
          style={{ backgroundColor: colour }}
          aria-hidden="true"
        />
      )}

      {/* Card content */}
      <div className="flex flex-col gap-0.5 px-2.5 py-2 min-w-0">
        {/* Name row: kind icon + name + homonym badge */}
        <div className="flex items-center gap-1.5 min-w-0">
          <KindIcon kind={kind} />
          <span className="text-xs font-semibold text-zinc-100 truncate leading-none">
            {name}
          </span>
          {/* Homonym badge: shows when multiple files define the same name */}
          {definition_count > 1 && (
            <span className="ml-auto shrink-0 text-[10px] text-amber-400 font-mono leading-none">
              ×{definition_count}
            </span>
          )}
        </div>

        {/* Truncated signature — visual hint of what the symbol looks like */}
        {signature !== null && (
          <span className="text-[10px] text-zinc-500 truncate leading-snug font-mono">
            {signature}
          </span>
        )}
      </div>
    </div>
  );
}
