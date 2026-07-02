/**
 * TreemapCanvas — the structural Overview (replaces the Louvain constellation).
 *
 * Organizes the codebase the way a human reads it: folders → files → classes →
 * methods, as nested rectangles sized by symbol count. This answers "what is in
 * here and how is it organized?" — the question the community-graph couldn't.
 *
 * Interaction:
 *   - click a folder/file/class  → drill INTO it (breadcrumb tracks the path)
 *   - click a function/method    → open its neighborhood graph (onSelectSymbol)
 *   - breadcrumb               → jump back up any number of levels
 *
 * Layout is the pure squarify() helper; this component only measures the
 * container, renders rectangles, and manages drill/breadcrumb state.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useStructure } from "../api/hooks";
import { buildTree, commonDirPrefix, type TreeNode } from "../lib/buildTree";
import { squarify } from "../lib/treemapLayout";
import { getClusterPalette } from "../lib/clusterColor";
import { ChevronRight, Folder, FileCode2, Box, FunctionSquare } from "lucide-react";

const PALETTE = getClusterPalette();

/** Stable colour for a label (so a folder keeps its hue across drill levels). */
function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/** Icon for a tree node kind. */
function NodeGlyph({ node }: { node: TreeNode }) {
  const cls = "w-3 h-3 shrink-0 opacity-80";
  if (node.nodeKind === "dir") return <Folder className={cls} />;
  if (node.nodeKind === "file") return <FileCode2 className={cls} />;
  if (node.nodeKind === "class") return <Box className={cls} />;
  return <FunctionSquare className={cls} />;
}

export interface TreemapCanvasProps {
  /** Called with a symbol name when a leaf (function/method) rect is clicked. */
  onSelectSymbol: (name: string) => void;
  /** When set, only these file paths are mapped (scopes the treemap to one area). */
  scopePaths?: string[];
  /** Root label when scoped (the area name). Defaults to "repo". */
  scopeName?: string;
  /** When set, a "‹ Areas" crumb returns to the functional-area cards. */
  onBack?: () => void;
}

export function TreemapCanvas({
  onSelectSymbol,
  scopePaths,
  scopeName,
  onBack,
}: TreemapCanvasProps) {
  const { data: symbols, isLoading } = useStructure(true);
  // Scope to an area's exact file set when provided (membership, not prefix — so
  // the synthetic "core" bucket scopes correctly too).
  const scoped = useMemo(() => {
    if (!scopePaths) return symbols ?? [];
    const allow = new Set(scopePaths);
    return (symbols ?? []).filter((s) => allow.has(s.path));
  }, [symbols, scopePaths]);

  // When scoped to an area, strip the common dir prefix from all paths so the
  // treemap does not re-nest files under their own parent dirs. For example, if
  // all paths start with "seam/server/", the tree shows files directly instead
  // of requiring the user to click through two empty intermediate dir nodes.
  const stripPrefix = useMemo(
    () => (scopePaths ? commonDirPrefix(scoped.map((s) => s.path)) : ""),
    [scoped, scopePaths],
  );

  const root = useMemo(
    () => buildTree(scoped, scopeName ?? "repo", stripPrefix),
    [scoped, scopeName, stripPrefix],
  );

  // Drill trail: nodes from root to the current view. Empty = at root.
  const [trail, setTrail] = useState<TreeNode[]>([]);
  // New data → reset the drill to the root (stale trail nodes no longer exist).
  useEffect(() => setTrail([]), [root]);
  const current = trail.length ? trail[trail.length - 1] : root;

  // Measure the container so squarify gets real pixel dimensions.
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const placed = useMemo(
    () =>
      squarify(
        current.children.map((c) => ({ value: Math.max(c.count, 0), node: c })),
        { x: 0, y: 0, w: size.w, h: size.h },
      ),
    [current, size],
  );

  const handleClick = (node: TreeNode) => {
    if (node.children.length > 0) {
      setTrail((t) => [...t, node]); // folder/file/class → drill in
    } else if (node.nodeKind === "symbol" || node.nodeKind === "class") {
      onSelectSymbol(node.name); // function/method/leaf class → open its graph
    }
  };

  const breadcrumb = [root, ...trail];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center w-full h-full text-zinc-500 text-sm">
        Loading structure…
      </div>
    );
  }
  if (root.children.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center w-full h-full gap-2 text-center p-8">
        <p className="text-zinc-500 text-sm">Nothing to map.</p>
        <p className="text-zinc-600 text-xs">
          Run <code className="text-zinc-400">seam init</code> to build the index.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col w-full h-full">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-zinc-800 text-xs shrink-0 overflow-x-auto">
        {onBack && (
          <button
            onClick={onBack}
            className="flex items-center gap-0.5 text-zinc-500 hover:text-zinc-300 shrink-0 mr-1"
            data-testid="back-to-areas"
          >
            ‹ Areas
            <ChevronRight className="w-3 h-3 text-zinc-600" />
          </button>
        )}
        {breadcrumb.map((n, i) => (
          <span key={i} className="flex items-center gap-1 shrink-0">
            {i > 0 && <ChevronRight className="w-3 h-3 text-zinc-600" />}
            <button
              onClick={() => setTrail(breadcrumb.slice(1, i + 1))}
              className={
                i === breadcrumb.length - 1
                  ? "text-zinc-200 font-semibold"
                  : "text-zinc-500 hover:text-zinc-300"
              }
            >
              {i === 0 ? scopeName ?? "repo" : n.name}
            </button>
          </span>
        ))}
        <span className="ml-2 text-[10px] text-zinc-600">
          {current.count} symbols · click a folder/file to drill in, a function to open its graph
        </span>
      </div>

      {/* Treemap canvas */}
      <div ref={containerRef} className="relative flex-1 overflow-hidden">
        {placed.map(({ node, rect }) => {
          // Hide labels on rects too small to read.
          const showLabel = rect.w > 46 && rect.h > 20;
          const isLeaf = node.children.length === 0 && node.nodeKind === "symbol";
          const colour = hashColor(node.name);
          return (
            <button
              key={`${node.nodeKind}:${node.name}:${rect.x.toFixed(1)}`}
              onClick={() => handleClick(node)}
              title={`${node.name} (${node.count} symbol${node.count === 1 ? "" : "s"})`}
              className="absolute flex flex-col items-start gap-0.5 p-1.5 overflow-hidden text-left transition-[filter] hover:brightness-125"
              style={{
                left: rect.x,
                top: rect.y,
                width: Math.max(rect.w - 2, 0),
                height: Math.max(rect.h - 2, 0),
                backgroundColor: isLeaf ? "#27272a" : `${colour}26`, // leaves muted
                border: `1px solid ${isLeaf ? "#3f3f46" : colour}`,
                borderRadius: 4,
              }}
            >
              {showLabel && (
                <>
                  <span className="flex items-center gap-1 min-w-0 max-w-full">
                    <NodeGlyph node={node} />
                    <span className="text-[11px] font-semibold text-zinc-100 truncate">
                      {node.name}
                    </span>
                  </span>
                  {rect.h > 36 && (
                    <span className="text-[9px] text-zinc-400 font-mono">
                      {node.count}
                    </span>
                  )}
                </>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
