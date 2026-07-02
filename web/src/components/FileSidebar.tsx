/**
 * FileSidebar — collapsible, resizable file-tree sidebar for the Seam Explorer.
 *
 * Renders a VS-Code-style dir → file → class → symbol tree beside the graph.
 * Structure data is lazy-fetched (only on first open). A debounced search input
 * filters the flat symbol list before tree-building; when a filter is active all
 * dirs are force-expanded so matching nodes are immediately visible.
 *
 * Clicking a symbol opens its neighborhood using the QUALIFIED name when
 * available, resolving the correct homonym instead of an arbitrary match.
 *
 * Width and open/closed state persist to localStorage.
 *
 * localStorage keys:
 *   seam-sidebar-open — "true" / "false" (default: "false" = starts closed)
 *   seam-sidebar-w    — numeric pixel width (clamped [150, 500])
 */

import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import {
  ChevronRight,
  ChevronDown,
  FolderOpen,
  Folder,
  FileText,
  Code2,
  X,
} from "lucide-react";
import { buildTree, type TreeNode } from "../lib/buildTree";
import { useStructure } from "../api/hooks";
import { ResizeHandle, readPanelWidth, clampPanelWidth } from "./ResizeHandle";

// ── Constants ─────────────────────────────────────────────────────────────────

const LS_SIDEBAR_OPEN_KEY = "seam-sidebar-open";
const LS_SIDEBAR_W_KEY = "seam-sidebar-w";
const DEFAULT_SIDEBAR_W = 240;
const SEARCH_DEBOUNCE_MS = 250;

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Read the persisted sidebar open/closed state. Defaults to false (closed). */
function readSidebarOpen(): boolean {
  try {
    const val = localStorage.getItem(LS_SIDEBAR_OPEN_KEY);
    if (val === null) return false; // closed by default on first load
    return val !== "false";
  } catch {
    return false;
  }
}

// ── SymbolRow ──────────────────────────────────────────────────────────────────

interface SymbolRowProps {
  node: TreeNode;
  depth: number;
  onOpen: (name: string) => void;
}

/**
 * Clickable leaf row for function / method / field / type symbols.
 * Uses qualified name when present to open the correct homonym in the graph.
 */
function SymbolRow({ node, depth, onOpen }: SymbolRowProps) {
  // Prefer qualified name to resolve the right homonym; fall back to bare name.
  const openName = node.qualifiedName ?? node.name;
  return (
    <button
      onClick={() => onOpen(openName)}
      className="w-full flex items-center gap-1 py-0.5 text-left hover:bg-zinc-800 transition-colors"
      style={{ paddingLeft: `${depth * 12 + 20}px` }}
      title={openName}
      data-testid={`sidebar-symbol-${node.name}`}
    >
      <Code2 className="w-3 h-3 text-zinc-600 shrink-0" aria-hidden="true" />
      <span className="text-xs text-zinc-300 truncate">{node.name}</span>
    </button>
  );
}

// ── DirFileRow ────────────────────────────────────────────────────────────────

interface DirFileRowProps {
  node: TreeNode;
  depth: number;
  /** Full slash-joined path string — used as the expansion-state key. */
  nodePath: string;
  isExpanded: boolean;
  expandedDirs: Set<string>;
  forceExpand: boolean;
  onToggle: (nodePath: string) => void;
  onOpen: (name: string) => void;
}

/**
 * Expandable row for dir or file nodes.
 * Shows a chevron, folder/file icon, name, and a symbol-count badge.
 * Children are rendered recursively when expanded.
 */
function DirFileRow({
  node,
  depth,
  nodePath,
  isExpanded,
  expandedDirs,
  forceExpand,
  onToggle,
  onOpen,
}: DirFileRowProps) {
  const FolderIcon = node.nodeKind === "dir"
    ? isExpanded ? FolderOpen : Folder
    : FileText;
  const Chevron = isExpanded ? ChevronDown : ChevronRight;

  return (
    <div>
      <button
        onClick={() => {
          // When force-expanding (search active) individual toggles are disabled.
          if (!forceExpand) onToggle(nodePath);
        }}
        className="w-full flex items-center gap-1 py-0.5 hover:bg-zinc-800 transition-colors"
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
        aria-expanded={isExpanded}
        // Unique testid encodes both kind and name for easy test selection.
        data-testid={
          node.nodeKind === "dir"
            ? `sidebar-dir-${node.name}`
            : `sidebar-file-${node.name}`
        }
      >
        <Chevron className="w-3 h-3 text-zinc-600 shrink-0" aria-hidden="true" />
        <FolderIcon className="w-3 h-3 text-zinc-500 shrink-0" aria-hidden="true" />
        <span className="text-xs text-zinc-400 truncate flex-1 text-left">
          {node.name}
        </span>
        {/* Count badge: total symbol count beneath this node (rollup) */}
        <span
          className="text-[10px] text-zinc-600 font-mono tabular-nums ml-1 shrink-0 pr-1"
          data-testid="sidebar-count-badge"
          aria-label={`${node.count} symbols`}
        >
          {node.count}
        </span>
      </button>

      {isExpanded && (
        <TreeNodeList
          nodes={node.children}
          depth={depth + 1}
          parentPath={nodePath}
          expandedDirs={expandedDirs}
          forceExpand={forceExpand}
          onToggleDir={onToggle}
          onOpen={onOpen}
        />
      )}
    </div>
  );
}

// ── TreeNodeList ───────────────────────────────────────────────────────────────

interface TreeNodeListProps {
  nodes: TreeNode[];
  depth: number;
  parentPath: string;
  expandedDirs: Set<string>;
  forceExpand: boolean;
  onToggleDir: (nodePath: string) => void;
  onOpen: (name: string) => void;
}

/**
 * Renders a list of TreeNode children recursively.
 * Passes the accumulated path down so each node has a unique expansion key.
 */
function TreeNodeList({
  nodes,
  depth,
  parentPath,
  expandedDirs,
  forceExpand,
  onToggleDir,
  onOpen,
}: TreeNodeListProps) {
  return (
    <>
      {nodes.map((node) => {
        // Build the full path for this node (unique key for expansion tracking).
        const nodePath = parentPath
          ? `${parentPath}/${node.name}`
          : node.name;

        if (node.nodeKind === "dir" || node.nodeKind === "file") {
          const isExpanded = forceExpand || expandedDirs.has(nodePath);
          return (
            <DirFileRow
              key={nodePath}
              node={node}
              depth={depth}
              nodePath={nodePath}
              isExpanded={isExpanded}
              expandedDirs={expandedDirs}
              forceExpand={forceExpand}
              onToggle={onToggleDir}
              onOpen={onOpen}
            />
          );
        }

        // class or symbol leaf — both are clickable to open neighborhood
        return (
          <SymbolRow
            key={`${nodePath}:${node.line ?? 0}`}
            node={node}
            depth={depth}
            onOpen={onOpen}
          />
        );
      })}
    </>
  );
}

// ── FileSidebar ───────────────────────────────────────────────────────────────

export interface FileSidebarProps {
  /**
   * Called when a symbol row is clicked.
   * Receives the qualified name when available, otherwise the bare name.
   */
  onOpen: (name: string) => void;
}

/**
 * Self-contained sidebar: manages its own open/closed, width, search, and
 * expansion state. Renders a collapsed toggle strip when closed, or the full
 * panel (search + tree + resize handle) when open.
 */
export function FileSidebar({ onOpen }: FileSidebarProps) {
  // ── Persistent open/closed and width ──────────────────────────────────────
  const [open, setOpen] = useState<boolean>(readSidebarOpen);
  const [width, setWidth] = useState<number>(() =>
    readPanelWidth(LS_SIDEBAR_W_KEY, DEFAULT_SIDEBAR_W),
  );

  // fetchEnabled: becomes true when the sidebar is first opened and stays true.
  // Separate from `open` so closing the sidebar doesn't re-gate the fetch.
  const [fetchEnabled, setFetchEnabled] = useState<boolean>(open);

  // ── Search state ───────────────────────────────────────────────────────────
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState(""); // debounced version
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Expansion state ────────────────────────────────────────────────────────
  // Tracks which dir/file node paths are currently expanded.
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => new Set());

  // ── Data ───────────────────────────────────────────────────────────────────
  // Lazy-fetch: enabled only after the sidebar has been opened at least once.
  const { data: symbols, isLoading } = useStructure(fetchEnabled);

  // ── Persistence side-effects ───────────────────────────────────────────────
  useEffect(() => {
    try { localStorage.setItem(LS_SIDEBAR_OPEN_KEY, String(open)); } catch { /* ignore */ }
  }, [open]);

  useEffect(() => {
    try { localStorage.setItem(LS_SIDEBAR_W_KEY, String(width)); } catch { /* ignore */ }
  }, [width]);

  // ── Callbacks ──────────────────────────────────────────────────────────────
  /** Open the sidebar and enable the lazy fetch in one step. */
  const openSidebar = useCallback(() => {
    setOpen(true);
    setFetchEnabled(true);
  }, []);

  const closeSidebar = useCallback(() => setOpen(false), []);

  /** Debounced input handler — waits 250ms after typing stops. */
  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setSearchInput(val);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(
        () => setSearchQuery(val),
        SEARCH_DEBOUNCE_MS,
      );
    },
    [],
  );

  /** Toggle expansion of a dir/file node by its path string. */
  const handleToggleDir = useCallback((nodePath: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(nodePath)) next.delete(nodePath);
      else next.add(nodePath);
      return next;
    });
  }, []);

  const handleResize = useCallback((delta: number) => {
    setWidth((w) => clampPanelWidth(w + delta));
  }, []);

  // ── Filtered tree ──────────────────────────────────────────────────────────
  const tree = useMemo(() => {
    const syms = symbols ?? [];
    const query = searchQuery.trim().toLowerCase();
    const filtered = query
      ? syms.filter(
          (s) =>
            s.path.toLowerCase().includes(query) ||
            s.name.toLowerCase().includes(query) ||
            (s.qualified_name ?? "").toLowerCase().includes(query),
        )
      : syms;
    return buildTree(filtered);
  }, [symbols, searchQuery]);

  const isFiltering = searchQuery.trim().length > 0;

  // ── Collapsed strip ────────────────────────────────────────────────────────
  if (!open) {
    return (
      <div
        className="flex flex-col shrink-0 items-center bg-zinc-950 border-r border-zinc-800 py-2"
        style={{ width: 28 }}
        data-testid="sidebar-collapsed"
      >
        <button
          onClick={openSidebar}
          aria-label="Open file sidebar"
          title="Open file sidebar"
          className="p-1 text-zinc-600 hover:text-zinc-300 transition-colors rounded"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    );
  }

  // ── Expanded panel ─────────────────────────────────────────────────────────
  return (
    <>
      <div
        className="flex flex-col shrink-0 bg-zinc-950 border-r border-zinc-800 overflow-hidden"
        style={{ width }}
        data-testid="sidebar-open"
      >
        {/* Header row */}
        <div className="flex items-center gap-1.5 px-2 py-2 border-b border-zinc-800 shrink-0">
          <button
            onClick={closeSidebar}
            aria-label="Close file sidebar"
            title="Close file sidebar"
            className="p-0.5 text-zinc-600 hover:text-zinc-300 transition-colors rounded"
          >
            <X className="w-3.5 h-3.5" />
          </button>
          <span className="text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
            Files
          </span>
        </div>

        {/* Debounced search */}
        <div className="px-2 py-1.5 border-b border-zinc-800/60 shrink-0">
          <input
            type="search"
            value={searchInput}
            onChange={handleSearchChange}
            placeholder="Filter files & symbols…"
            aria-label="Filter files and symbols"
            data-testid="sidebar-search"
            className="
              w-full px-2 py-1 text-xs
              bg-zinc-900 border border-zinc-700 rounded
              text-zinc-100 placeholder-zinc-600
              focus:outline-none focus:ring-1 focus:ring-sky-500
            "
          />
        </div>

        {/* Tree content */}
        <div className="flex-1 overflow-y-auto py-1" data-testid="sidebar-tree">
          {isLoading && (
            <p className="px-3 py-2 text-xs text-zinc-600 animate-pulse">
              Loading…
            </p>
          )}
          {!isLoading && tree.children.length === 0 && (
            <p className="px-3 py-2 text-xs text-zinc-600">
              {isFiltering ? "No matches" : "No files indexed"}
            </p>
          )}
          {!isLoading && tree.children.length > 0 && (
            <TreeNodeList
              nodes={tree.children}
              depth={0}
              parentPath=""
              expandedDirs={expandedDirs}
              forceExpand={isFiltering}
              onToggleDir={handleToggleDir}
              onOpen={onOpen}
            />
          )}
        </div>
      </div>

      {/* Drag handle between the sidebar and the canvas */}
      <ResizeHandle side="left" onResize={handleResize} />
    </>
  );
}
