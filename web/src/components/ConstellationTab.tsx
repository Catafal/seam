/**
 * ConstellationTab — the lazy-loaded 3D constellation Explorer tab.
 *
 * Three-column shell:
 *   [FilterPanel] | [ResizeHandle] | [ConstellationScene (flex-1)] | [ResizeHandle] | [NodeDetailPanel]
 *
 * Owns the state machine for node selection:
 *   click node  → setSelectedNode → compute highlightedIds (node + direct neighbors)
 *               → compute cameraTarget → fly camera → open NodeDetailPanel
 *   click empty → deselect (handleClose) — full field restored
 *   Esc key     → deselect (handleClose) — keyboard deselect
 *
 * ISOLATION CONTRACT (#263):
 *   A 3D node click NEVER navigates the 2D neighborhood. The click is purely an
 *   isolate action: the selection dims everything else and flies the camera to
 *   frame the node's neighborhood. onFocusSymbol has been deliberately removed
 *   from this component's props so no outbound navigation callback can exist.
 *   The inbound 2D→3D sync (focusSymbol prop) is kept: when the user picks a
 *   symbol in the 2D side, the 3D camera flies to it automatically.
 *
 * Props:
 *   focusSymbol — symbol name set from the 2D side (inbound 2D→3D sync only)
 *
 * Pure helper (unit-tested):
 *   computeHighlightedIds(selectedId, edges) → Set<number>
 *     Returns the selected node id plus all direct neighbors (undirected).
 *
 * localStorage keys:
 *   seam-left-w   — left FilterPanel width (pixels, clamped [150, 500])
 *   seam-right-w  — right NodeDetailPanel width (pixels, clamped [150, 500])
 */

import { useState, useMemo, useCallback, useEffect, useRef } from "react";

import { ConstellationScene, computeCameraTarget } from "./ConstellationScene";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { FilterPanel } from "./FilterPanel";
import { ConstellationHUD } from "./ConstellationHUD";
import { ResizeHandle, clampPanelWidth, readPanelWidth } from "./ResizeHandle";
import { useLayoutData, GRAPH_RENDER_NODE_LIMIT } from "../hooks/useLayoutData";
import type { CameraTarget } from "./ConstellationScene";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";
import { ALL_EDGE_KINDS } from "../lib/edgeFilter";

// ── Constants ─────────────────────────────────────────────────────────────────

const NODE_KINDS = ["function", "class", "method", "interface", "type", "field"];

const DEFAULT_LEFT_W = 200;
const DEFAULT_RIGHT_W = 280;
const LS_LEFT_KEY = "seam-left-w";
const LS_RIGHT_KEY = "seam-right-w";

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
  /**
   * Inbound 2D→3D sync: when set, the 3D camera flies to this symbol.
   * NOTE: onFocusSymbol is intentionally absent — a 3D click MUST NOT
   * navigate the 2D neighborhood (#263 isolation contract).
   */
  focusSymbol?: string | null;
}

/**
 * Main 3D tab component. Manages filter state, panel widths, and selection.
 */
export default function ConstellationTab({
  focusSymbol,
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
  const [enabledEdges, setEnabledEdges] = useState<Set<string>>(new Set(ALL_EDGE_KINDS));

  // ── Panel widths (persisted to localStorage) ───────────────────────────────
  const [leftW, setLeftW] = useState(() => readPanelWidth(LS_LEFT_KEY, DEFAULT_LEFT_W));
  const [rightW, setRightW] = useState(() => readPanelWidth(LS_RIGHT_KEY, DEFAULT_RIGHT_W));

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
   *
   * ISOLATION (#263): this handler ONLY updates local state — no external
   * navigation callback is invoked. Clicking a 3D node isolates its
   * neighborhood visually and flies the camera; nothing else changes.
   */
  const handleSelect = useCallback(
    (node: LayoutNode) => {
      setSelectedNode(node);
      const newIds = computeHighlightedIds(node.id, (data?.edges ?? []) as LayoutEdge[]);
      const target = computeCameraTarget(data?.nodes ?? [], newIds);
      setCameraTarget(target);
      // Intentionally no onFocusSymbol call here — 3D click must NOT navigate (#263).
    },
    [data],
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

  // ── Esc key deselect (#263) ────────────────────────────────────────────────
  //
  // Pressing Escape clears the selection and restores the full star field —
  // same as clicking empty canvas (via onPointerMissed → handleClose).
  // The handler is attached to the document so it fires regardless of which
  // element has focus (the R3F canvas does not propagate key events by default).

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") handleClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [handleClose]);

  // ── 2D → 3D sync: fly to focusSymbol when set from the 2D side ────────────
  //
  // When the 2D neighborhood selects a symbol (via setCenterSymbol → setFocusSymbol
  // in App.tsx), this effect flies the 3D camera to the corresponding node.
  //
  // De-duplication: track the last processed focusSymbol so we don't re-fly
  // when the prop re-renders with the same value. Since 3D clicks no longer set
  // onFocusSymbol (#263), the round-trip guard is simplified: we only skip if
  // focusSymbol is unchanged since last processing.

  const lastFocused = useRef<string | null>(null);

  useEffect(() => {
    if (!focusSymbol || !data) return;
    if (focusSymbol === lastFocused.current) return; // already processed
    lastFocused.current = focusSymbol;
    const node = (data.nodes as LayoutNode[]).find((n) => n.name === focusSymbol);
    if (node) handleSelect(node);
  }, [focusSymbol, data, handleSelect]);

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
          onAllEdges={() => setEnabledEdges(new Set(ALL_EDGE_KINDS))}
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

        {/* Discoverability hint — quiet one-liner at the bottom when nothing is selected.
            Hidden once a node is selected so it doesn't compete with NodeDetailPanel.
            The hint surfaces BOTH interaction modes: click-to-isolate + empty/Esc reset. */}
        {!selectedNode && (
          <div
            className="absolute bottom-4 left-0 right-0 flex justify-center pointer-events-none z-10"
            aria-live="polite"
          >
            <p className="text-zinc-600 text-[11px] select-none">
              Click a node to isolate its connections · click empty space or press Esc to reset
            </p>
          </div>
        )}

        <ConstellationScene
          nodes={visibleNodes}
          edges={visibleEdges}
          highlightedIds={highlightedIds}
          cameraTarget={cameraTarget}
          hoveredNode={hoveredNode}
          onHover={handleHover}
          onSelect={handleSelect}
          onDeselect={handleClose}
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
