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
 */

import { useState, useMemo, useCallback } from "react";

import { ConstellationScene, computeCameraTarget } from "./ConstellationScene";
import { useLayoutData } from "../hooks/useLayoutData";
import type { CameraTarget } from "./ConstellationScene";
import type { LayoutNode, LayoutEdge } from "../lib/layoutTypes";

// ── ConstellationTab ──────────────────────────────────────────────────────────

interface ConstellationTabProps {
  focusSymbol?: string | null;
  onFocusSymbol?: (name: string) => void;
}

/**
 * Three-column shell:
 *   [future FilterPanel] | ConstellationScene (flex-1) | [future NodeDetailPanel]
 *
 * For S2 this is the minimal wiring: data hook + scene + error/loading states.
 * FilterPanel, NodeDetailPanel, and ResizeHandle are added in later slices.
 */
export default function ConstellationTab({
  focusSymbol: _focusSymbol,
  onFocusSymbol,
}: ConstellationTabProps) {
  const { data, isLoading, isError } = useLayoutData();

  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<LayoutNode | null>(null);
  const [cameraTarget, setCameraTarget] = useState<CameraTarget | null>(null);

  // Highlighted ids: selected node + its direct neighbors
  const highlightedIds = useMemo<Set<number>>(() => {
    if (!selectedNode || !data) return new Set();
    const ids = new Set<number>([selectedNode.id]);
    for (const e of data.edges as LayoutEdge[]) {
      if (e.source === selectedNode.id) ids.add(e.target);
      if (e.target === selectedNode.id) ids.add(e.source);
    }
    return ids;
  }, [selectedNode, data]);

  const handleSelect = useCallback(
    (node: LayoutNode) => {
      setSelectedNode(node);
      const newIds = new Set<number>([node.id]);
      if (data) {
        for (const e of data.edges as LayoutEdge[]) {
          if (e.source === node.id) newIds.add(e.target);
          if (e.target === node.id) newIds.add(e.source);
        }
      }
      const target = computeCameraTarget(data?.nodes ?? [], newIds);
      setCameraTarget(target);
      onFocusSymbol?.(node.name);
    },
    [data, onFocusSymbol],
  );

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
    <div className="relative w-full h-full flex">
      {/* Center: 3D WebGL canvas */}
      <div className="flex-1 relative">
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

      {/* Future: NodeDetailPanel slides in here when selectedNode is set */}
    </div>
  );
}
