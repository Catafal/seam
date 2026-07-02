/**
 * ConstellationTab — the lazy-loaded 3D constellation Explorer tab.
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
 */

import { useState, useMemo, useCallback } from "react";

import { ConstellationScene, computeCameraTarget } from "./ConstellationScene";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { useLayoutData } from "../hooks/useLayoutData";
import type { CameraTarget } from "./ConstellationScene";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── Pure helper (extracted for unit-testability) ──────────────────────────────

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
 * Three-column shell:
 *   ConstellationScene (flex-1, center) | NodeDetailPanel (right, when selected)
 *
 * Selection state machine:
 *   handleSelect(node) → setSelectedNode + computeHighlightedIds + computeCameraTarget
 *   handleNavigate(name) → find node by name in data, re-run handleSelect
 *   handleClose() → clear selectedNode + highlightedIds + cameraTarget
 */
export default function ConstellationTab({
  focusSymbol: _focusSymbol,
  onFocusSymbol,
}: ConstellationTabProps) {
  const { data, isLoading, isError } = useLayoutData();

  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<LayoutNode | null>(null);
  const [cameraTarget, setCameraTarget] = useState<CameraTarget | null>(null);

  // Highlighted ids: selected node + its direct neighbors (derived from selectedNode)
  const highlightedIds = useMemo<Set<number>>(() => {
    if (!selectedNode || !data) return new Set();
    return computeHighlightedIds(selectedNode.id, data.edges as LayoutEdge[]);
  }, [selectedNode, data]);

  /**
   * Core selection handler: runs the full click state machine.
   *   1. Update selectedNode state
   *   2. Compute neighbor set
   *   3. Fly camera to the selection centroid
   *   4. Notify the 2D side via onFocusSymbol
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
   * Navigate from the detail panel: find the named node in the layout data
   * and re-run the full selection state machine.
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
      {/* Center: 3D WebGL canvas */}
      <div className="flex-1 relative min-w-0">
        <ConstellationScene
          nodes={data.nodes}
          edges={data.edges}
          clusters={data.clusters}
          highlightedIds={highlightedIds}
          cameraTarget={cameraTarget}
          hoveredNode={hoveredNode}
          onHover={handleHover}
          onSelect={handleSelect}
        />
      </div>

      {/* Right: NodeDetailPanel — slides in when a node is selected */}
      {selectedNode && (
        <NodeDetailPanel
          node={selectedNode}
          onNavigate={handleNavigate}
          onClose={handleClose}
        />
      )}
    </div>
  );
}
