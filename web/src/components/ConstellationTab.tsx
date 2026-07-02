/**
 * ConstellationTab — the lazy-loaded 3D constellation Explorer tab.
 *
 * Three-column shell:
 *   [FilterPanel] | [ResizeHandle] | [ConstellationScene (flex-1)] | [ResizeHandle] | [NodeDetailPanel]
 *
 * Owns the state machine for node selection:
 *   click node → setSelectedNode → compute highlightedIds (node + direct neighbors)
 *               → compute cameraTarget → fly camera → open NodeDetailPanel
 *
 * Props:
 *   focusSymbol    — symbol name set from the 2D side (2D→3D sync)
 *   onFocusSymbol  — called when a node is selected (3D→2D sync)
 *
 * Pure helper (unit-tested):
 *   computeHighlightedIds(selectedId, edges) → Set<number>
 *     Returns the selected node id plus all direct neighbors (undirected).
 *
 * localStorage keys:
 *   seam-left-w   — left FilterPanel width (pixels, clamped [150, 500])
 *   seam-right-w  — right NodeDetailPanel width (pixels, clamped [150, 500])
 */

import { useState, useMemo, useCallback, useEffect } from "react";

import { ConstellationScene, computeCameraTarget } from "./ConstellationScene";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { FilterPanel } from "./FilterPanel";
import { ConstellationHUD } from "./ConstellationHUD";
import { ResizeHandle, clampPanelWidth } from "./ResizeHandle";
import { useLayoutData, GRAPH_RENDER_NODE_LIMIT } from "../hooks/useLayoutData";
import type { CameraTarget } from "./ConstellationScene";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── Constants ─────────────────────────────────────────────────────────────────

const NODE_KINDS = ["function", "class", "method", "interface", "type", "field"];
const EDGE_KINDS = ["call", "import", "extends", "implements", "instantiates", "holds", "reads", "writes", "uses"];

const DEFAULT_LEFT_W = 200;
const DEFAULT_RIGHT_W = 280;
const LS_LEFT_KEY = "seam-left-w";
const LS_RIGHT_KEY = "seam-right-w";

function readWidth(key: string, fallback: number): number {
  try {
    const v = localStorage.getItem(key);
    return v ? clampPanelWidth(Number(v)) : fallback;
  } catch {
    return fallback;
  }
}

// ── Pure helper ───────────────────────────────────────────────────────────────

/**
 * Given a selected node id and the full edge list, compute the set of
 * highlighted ids: the selected node plus all direct neighbors (undirected).
 *
 * Pure — no React state, no hooks. Exported for vitest.
 */
export function computeHighlightedIds(
  selectedId: number,
  edges: LayoutEdge[],
): Set<number> {
  const ids = new Set<number>([selectedId]);
  for (const e of edges) {
    if (e.source === selectedId) ids.add(e.target);
    if (e.target === selectedId) ids.add(e.source);
  }
  return ids;
}

// ── ConstellationTab ──────────────────────────────────────────────────────────

interface ConstellationTabProps {
  focusSymbol?: string | null;
  onFocusSymbol?: (name: string) => void;
}

/**
 * Main 3D tab component. Manages filter state, panel widths, and selection.
 */
export default function ConstellationTab({
  focusSymbol: _focusSymbol,
  onFocusSymbol,
}: ConstellationTabProps) {
  // ── Node cap (drives the react-query key) ─────────────────────────────────
  const [maxNodes, setMaxNodes] = useState<number>(GRAPH_RENDER_NODE_LIMIT);
  const { data, isLoading, isError } = useLayoutData(maxNodes);

  // ── Selection state ────────────────────────────────────────────────────────
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<LayoutNode | null>(null);
  const [cameraTarget, setCameraTarget] = useState<CameraTarget | null>(null);

  // ── Filter state ───────────────────────────────────────────────────────────
  const [enabledKinds, setEnabledKinds] = useState<Set<string>>(new Set(NODE_KINDS));
  const [enabledEdges, setEnabledEdges] = useState<Set<string>>(new Set(EDGE_KINDS));

  // ── Panel widths (persisted to localStorage) ───────────────────────────────
  const [leftW, setLeftW] = useState(() => readWidth(LS_LEFT_KEY, DEFAULT_LEFT_W));
  const [rightW, setRightW] = useState(() => readWidth(LS_RIGHT_KEY, DEFAULT_RIGHT_W));

  // Persist widths on change
  useEffect(() => {
    try { localStorage.setItem(LS_LEFT_KEY, String(leftW)); } catch { /* ignore */ }
  }, [leftW]);
  useEffect(() => {
    try { localStorage.setItem(LS_RIGHT_KEY, String(rightW)); } catch { /* ignore */ }
  }, [rightW]);

  // ── Highlighted ids: selected node + direct neighbors ─────────────────────
  const highlightedIds = useMemo<Set<number>>(() => {
    if (!selectedNode || !data) return new Set();
    return computeHighlightedIds(selectedNode.id, data.edges as LayoutEdge[]);
  }, [selectedNode, data]);

  // ── Filtered nodes + edges for visible counts ─────────────────────────────
  const visibleNodes = useMemo(
    () => data ? (data.nodes as LayoutNode[]).filter((n) => enabledKinds.has(n.label)) : [],
    [data, enabledKinds],
  );
  const visibleEdges = useMemo(
    () => data ? (data.edges as LayoutEdge[]).filter((e) => enabledEdges.has(e.type)) : [],
    [data, enabledEdges],
  );

  // ── Selection handlers ─────────────────────────────────────────────────────

  /**
   * Core selection handler: runs the full click state machine.
   */
  const handleSelect = useCallback(
    (node: LayoutNode) => {
      setSelectedNode(node);
      const newIds = computeHighlightedIds(node.id, (data?.edges ?? []) as LayoutEdge[]);
      const target = computeCameraTarget(data?.nodes ?? [], newIds);
      setCameraTarget(target);
      onFocusSymbol?.(node.name);
    },
    [data, onFocusSymbol],
  );

  /**
   * Navigate from the detail panel: find the named node and re-run selection.
   */
  const handleNavigate = useCallback(
    (name: string) => {
      if (!data) return;
      const target = (data.nodes as LayoutNode[]).find((n) => n.name === name);
      if (target) handleSelect(target);
    },
    [data, handleSelect],
  );

  /** Deselect: clear node, highlights, and camera target. */
  const handleClose = useCallback(() => {
    setSelectedNode(null);
    setCameraTarget(null);
  }, []);

  const handleHover = useCallback((node: LayoutNode | null) => {
    setHoveredNode(node);
  }, []);

  // ── Filter handlers ────────────────────────────────────────────────────────

  const toggleKind = useCallback((kind: string) => {
    setEnabledKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else next.add(kind);
      return next;
    });
  }, []);

  const toggleEdge = useCallback((kind: string) => {
    setEnabledEdges((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else next.add(kind);
      return next;
    });
  }, []);

  // ── Resize handlers ────────────────────────────────────────────────────────

  const handleLeftResize = useCallback((delta: number) => {
    setLeftW((w) => clampPanelWidth(w + delta));
  }, []);

  const handleRightResize = useCallback((delta: number) => {
    // Right handle: dragging right makes panel smaller, left makes it wider
    setRightW((w) => clampPanelWidth(w - delta));
  }, []);

  // ── Loading / error states ─────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex items-center justify-center w-full h-full text-zinc-500 text-sm">
        <span className="animate-pulse">Loading constellation…</span>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex items-center justify-center w-full h-full text-red-400 text-sm">
        <div className="text-center space-y-1">
          <p>Failed to load constellation layout.</p>
          <p className="text-zinc-500 text-xs">
            Run <code className="text-zinc-400">seam init</code> and retry.
          </p>
        </div>
      </div>
    );
  }

  // ── Scene ──────────────────────────────────────────────────────────────────

  return (
    <div className="relative w-full h-full flex overflow-hidden">
      {/* Left: FilterPanel */}
      <div
        className="flex-shrink-0 overflow-hidden bg-zinc-950 border-r border-zinc-800/60"
        style={{ width: leftW }}
      >
        <FilterPanel
          data={data}
          enabledKinds={enabledKinds}
          enabledEdges={enabledEdges}
          onToggleKind={toggleKind}
          onToggleEdge={toggleEdge}
          onAllKinds={() => setEnabledKinds(new Set(NODE_KINDS))}
          onNoneKinds={() => setEnabledKinds(new Set())}
          onAllEdges={() => setEnabledEdges(new Set(EDGE_KINDS))}
          onNoneEdges={() => setEnabledEdges(new Set())}
        />
      </div>

      {/* Left resize handle */}
      <ResizeHandle side="left" onResize={handleLeftResize} />

      {/* Center: 3D WebGL canvas + HUD overlay */}
      <div className="flex-1 relative min-w-0">
        {/* HUD overlay — absolute over the canvas */}
        <ConstellationHUD
          visibleNodes={visibleNodes.length}
          visibleEdges={visibleEdges.length}
          totalNodes={data.total_nodes}
          selectedCount={highlightedIds.size}
          maxNodes={maxNodes}
          onChangeMaxNodes={setMaxNodes}
        />

        <ConstellationScene
          nodes={visibleNodes}
          edges={visibleEdges}
          clusters={data.clusters}
          highlightedIds={highlightedIds}
          cameraTarget={cameraTarget}
          hoveredNode={hoveredNode}
          onHover={handleHover}
          onSelect={handleSelect}
        />
      </div>

      {/* Right resize handle — only when detail panel is open */}
      {selectedNode && (
        <>
          <ResizeHandle side="right" onResize={handleRightResize} />
          <div
            className="flex-shrink-0 overflow-hidden bg-zinc-950 border-l border-zinc-800/60"
            style={{ width: rightW }}
          >
            <NodeDetailPanel
              node={selectedNode}
              onNavigate={handleNavigate}
              onClose={handleClose}
            />
          </div>
        </>
      )}
    </div>
  );
}
