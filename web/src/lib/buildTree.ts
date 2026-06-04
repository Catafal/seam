/**
 * Build a structural tree (folder → file → class → method) from the flat
 * /api/structure symbol list. This is the data model behind the Overview treemap
 * — it organizes the codebase the way a human reads it (structure), not the way a
 * graph algorithm groups it (Louvain communities).
 *
 * Pure + framework-free → unit-tested in isolation.
 */

import type { StructureSymbol } from "../api/schema-types";

/** Kinds that can contain methods (a method nests under its class). */
const CONTAINER_KINDS = new Set([
  "class",
  "interface",
  "struct",
  "enum",
  "trait",
  "actor",
]);

export type TreeNodeKind = "dir" | "file" | "class" | "symbol";

export interface TreeNode {
  name: string;
  nodeKind: TreeNodeKind;
  /** File path (for file nodes + symbol/class leaves) — used to open the graph. */
  path?: string;
  /** Original symbol kind (function/class/method/…) for class/symbol nodes. */
  symbolKind?: string;
  line?: number;
  /** Total symbols beneath (sizes the treemap rectangle). */
  count: number;
  children: TreeNode[];
}

/** Owner class of a method, from its qualified_name ("Class.method" → "Class"). */
function methodOwner(qn: string | null | undefined): string | null {
  if (!qn) return null;
  const parts = qn.split(".");
  return parts.length >= 2 ? parts[parts.length - 2] : null;
}

/** Build the symbol subtree for a single file (classes with nested methods). */
function buildFileSymbols(symbols: StructureSymbol[]): TreeNode[] {
  // First pass: create class container nodes keyed by name.
  const classes = new Map<string, TreeNode>();
  const topLevel: TreeNode[] = [];

  for (const s of symbols) {
    if (CONTAINER_KINDS.has(s.kind)) {
      const node: TreeNode = {
        name: s.name,
        nodeKind: "class",
        path: s.path,
        symbolKind: s.kind,
        line: s.line,
        count: 1,
        children: [],
      };
      classes.set(s.name, node);
      topLevel.push(node);
    }
  }

  // Second pass: attach methods to their owning class, else make them top-level.
  for (const s of symbols) {
    if (CONTAINER_KINDS.has(s.kind)) continue;
    const leaf: TreeNode = {
      name: s.name,
      nodeKind: "symbol",
      path: s.path,
      symbolKind: s.kind,
      line: s.line,
      count: 1,
      children: [],
    };
    const owner = s.kind === "method" ? methodOwner(s.qualified_name) : null;
    const cls = owner ? classes.get(owner) : undefined;
    if (cls) {
      cls.children.push(leaf);
      cls.count += 1;
    } else {
      topLevel.push(leaf);
    }
  }

  return topLevel;
}

/** Recursively assign counts = symbols beneath, counting self when it's a symbol.
 *  dir/file are structural (self=0); class/symbol are real symbols (self=1), so a
 *  class with 3 methods counts 4 (itself + 3). */
function rollupCounts(node: TreeNode): number {
  const self = node.nodeKind === "class" || node.nodeKind === "symbol" ? 1 : 0;
  const childSum = node.children.reduce((s, c) => s + rollupCounts(c), 0);
  node.count = self + childSum;
  return node.count;
}

/**
 * Build the full structural tree from a flat symbol list.
 *
 * Returns a root "dir" node. Folder nesting comes from the file path; within a
 * file, classes contain their methods. Empty input → an empty root.
 */
export function buildTree(symbols: StructureSymbol[], rootName = "root"): TreeNode {
  const root: TreeNode = { name: rootName, nodeKind: "dir", count: 0, children: [] };
  if (symbols.length === 0) return root;

  // Group symbols by file path (preserves input order within a file).
  const byFile = new Map<string, StructureSymbol[]>();
  for (const s of symbols) {
    const arr = byFile.get(s.path);
    if (arr) arr.push(s);
    else byFile.set(s.path, [s]);
  }

  // Insert each file into the dir tree by its path components.
  for (const [path, fileSymbols] of byFile) {
    const parts = path.split("/").filter(Boolean);
    if (parts.length === 0) continue;
    const fileName = parts[parts.length - 1];
    const dirs = parts.slice(0, -1);

    // Walk/create the directory chain.
    let cursor = root;
    for (const dir of dirs) {
      let child = cursor.children.find(
        (c) => c.nodeKind === "dir" && c.name === dir,
      );
      if (!child) {
        child = { name: dir, nodeKind: "dir", count: 0, children: [] };
        cursor.children.push(child);
      }
      cursor = child;
    }

    // Create the file node with its symbol subtree.
    cursor.children.push({
      name: fileName,
      nodeKind: "file",
      path,
      count: 0,
      children: buildFileSymbols(fileSymbols),
    });
  }

  rollupCounts(root);
  return root;
}
