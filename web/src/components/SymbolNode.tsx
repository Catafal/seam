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
import { tierColor, tierLabel } from "../lib/riskTier";
import {
  Box,
  Circle,
  Code2,
  FileCode2,
  Layers,
  Lock,
  Shield,
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
  // ── Phase 2 enrichment (threaded from the API GraphNode) ──────────────────
  /** 1=exported/public, 0=private, null=unknown. Drives the export badge. */
  is_exported?: boolean | null;
  /** 'public'|'private'|'protected'|'crate'|null. Drives the visibility chip. */
  visibility?: string | null;
  // ── Phase 2 overlay state (set by impact/trace overlays; absent normally) ──
  /** Impact risk tier when the impact overlay is active (else null/undefined). */
  impactTier?: string | null;
  /** True when this node is NOT part of the active overlay (dimmed out). */
  dimmed?: boolean;
  /** True for impact dependents that lie beyond the visible neighborhood. */
  offCanvas?: boolean;
  /**
   * Whether this node supports double-click expand / re-center (User Story 9).
   * Set by GraphCanvas from isNavigable(). Absent means navigable (backward compat).
   * WHY in data (not derived inline): SymbolNode cannot import isNavigable without
   * a circular dep (isNavigable imports SymbolNodeData from this file).
   */
  navigable?: boolean;
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
/** Small chip for restricted visibility (private/protected/crate). */
function VisibilityChip({ visibility }: { visibility: string | null | undefined }) {
  if (!visibility || visibility === "public") return null;
  const Icon = visibility === "protected" ? Shield : Lock;
  return (
    <span
      className="shrink-0 inline-flex items-center"
      title={visibility}
      aria-label={`visibility: ${visibility}`}
    >
      <Icon className="w-3 h-3 text-zinc-500" />
    </span>
  );
}

export function SymbolNode({ data }: SymbolNodeProps) {
  const {
    name,
    kind,
    signature,
    cluster_id,
    definition_count,
    isCenter,
    is_exported,
    visibility,
    impactTier,
    dimmed,
    offCanvas,
    // navigable absent (pre-existing nodes) → true (backward-compatible default).
    navigable = true,
  } = data;

  const colour = clusterColor(cluster_id);
  const tierColour = tierColor(impactTier);

  // Emphasis precedence: impact tier ring (when overlay active) > center ring.
  // WHY tier wins: during an impact overlay the user is asking "what breaks?",
  // so the tier signal is the one that must read at a glance.
  let ringStyle: React.CSSProperties | undefined;
  let ringClass = "";
  if (tierColour) {
    ringStyle = { boxShadow: `0 0 0 2px ${tierColour}` };
  } else if (isCenter) {
    ringClass = "ring-2 ring-sky-500 ring-offset-1 ring-offset-zinc-900";
  }

  // Off-canvas impact dependents render fainter + dashed so they read as
  // "beyond the current view" rather than first-class neighborhood members.
  const borderClass = offCanvas ? "border-dashed border-zinc-600" : "border-zinc-700";
  const dimClass = dimmed ? "opacity-40" : "";
  // WHY conditional cursor: non-navigable nodes (bare edge-target references with no
  // indexed definition) use cursor-default so the user is NOT invited to double-click
  // a dead end. Single-click (detail panel) still works on all nodes (User Story 9).
  const cursorClass = navigable ? "cursor-pointer" : "cursor-default";

  return (
    <div
      style={ringStyle}
      className={`
        relative flex items-stretch
        bg-zinc-900 border ${borderClass} rounded-md
        min-w-[160px] max-w-[240px] overflow-hidden
        ${cursorClass} select-none
        hover:border-zinc-500 transition-all
        ${ringClass} ${dimClass}
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
      <div className="flex flex-col gap-0.5 px-2.5 py-2 min-w-0 flex-1">
        {/* Name row: kind icon + name + visibility/export + homonym badge */}
        <div className="flex items-center gap-1.5 min-w-0">
          <KindIcon kind={kind} />
          <span className="text-xs font-semibold text-zinc-100 truncate leading-none">
            {name}
          </span>
          <VisibilityChip visibility={visibility} />
          {/* Exported badge: green dot marks a public/exported symbol */}
          {is_exported === true && (
            <span
              data-testid="exported-badge"
              className="shrink-0 w-1.5 h-1.5 rounded-full bg-emerald-400"
              title="exported"
              aria-label="exported"
            />
          )}
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

        {/* Impact tier label — only during the impact overlay */}
        {impactTier && tierColour && (
          <span
            className="text-[9px] font-mono leading-none mt-0.5"
            style={{ color: tierColour }}
          >
            {tierLabel(impactTier)}
          </span>
        )}
      </div>
    </div>
  );
}
