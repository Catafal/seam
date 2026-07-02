/**
 * Build a structural tree (folder → file → class → method) from the flat
 * /api/structure symbol list. This is the data model behind the Overview treemap
 * — it organizes the codebase the way a human reads it (structure), not the way a
 * graph algorithm groups it (Louvain communities).
 *
 * A2 additions (issue #215):
 *   commonDirPrefix  — find the longest shared dir prefix so a scoped area can
 *                      strip its own parent path before building the tree.
 *   flattenSingleChild — collapse a/b/c/ chains where each dir has exactly one
 *                        dir child, so the treemap doesn't waste clicks on empty
 *                        intermediate dirs.
 *   buildTree now accepts an optional stripPrefix (3rd param) that applies both
 *   transformations in one call for callers like TreemapCanvas.
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
  /**
   * Fully-qualified symbol name (e.g. "Db.connect") when available.
   * Preferred over bare `name` when opening a neighborhood so the correct
   * homonym is resolved rather than an arbitrary match.
   */
  qualifiedName?: string | null;
  /** Total symbols beneath (sizes the treemap rectangle). */
  count: number;
  /**
   * Rolled-up fan-in degree for the treemap's one signal (B3).
   * Symbol/class: own degree; class also sums method degrees.
   * File/dir: sum of children's degree. Use max(degree, 1) at the sizing site
   * so zero-degree nodes still get a floor-size cell (never hidden).
   */
  degree: number;
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
        qualifiedName: s.qualified_name,
        count: 1,
        degree: s.degree ?? 0,
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
      qualifiedName: s.qualified_name,
      count: 1,
      degree: s.degree ?? 0,
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

/**
 * Compute the longest common directory prefix of the given file paths.
 *
 * Only the directory parts are compared — the filename is excluded. Returns ""
 * when there is no shared prefix (different top-level dirs) or when paths is empty.
 *
 * Examples:
 *   ["seam/server/tools.py", "seam/server/handler.py"] → "seam/server"
 *   ["seam/server/tools.py", "seam/analysis/clustering.py"] → "seam"
 *   ["web/app.ts", "seam/db.py"] → ""
 */
export function commonDirPrefix(paths: string[]): string {
  if (paths.length === 0) return "";

  // Strip filename from each path to get the directory part segments.
  const allDirParts = paths.map((p) => {
    const segments = p.split("/");
    return segments.slice(0, -1); // everything except the filename
  });

  const first = allDirParts[0];
  let commonLen = first.length;

  // Walk all other paths and shrink commonLen to the last matching segment index.
  for (let i = 1; i < allDirParts.length; i++) {
    const parts = allDirParts[i];
    let j = 0;
    while (j < commonLen && j < parts.length && first[j] === parts[j]) j++;
    commonLen = j;
    if (commonLen === 0) return ""; // early exit — no common prefix
  }

  return first.slice(0, commonLen).join("/");
}

/**
 * Collapse single-child directory chains into merged nodes.
 *
 * For each dir child that itself has exactly ONE dir child, those two levels are
 * merged into a single node whose name combines both dirs with "/" (e.g. "a" that
 * contains only "b" becomes "a/b"). The merge is applied recursively bottom-up so
 * chains of any depth are collapsed in one pass.
 *
 * The node passed in is returned as-is structurally (it is the entry-point node,
 * not a candidate for merging); only its children are collapsed.
 *
 * Non-dir nodes are returned as-is (identity, no mutation).
 *
 * Why: after stripping a common prefix there may still be a residual
 * intermediate dir (e.g. a single "sub/" node above a flat file list). Collapsing
 * it lets the user reach the files in one click instead of two.
 */
export function flattenSingleChild(node: TreeNode): TreeNode {
  // Only dirs can have children to collapse; leave files/classes/symbols unchanged.
  if (node.nodeKind !== "dir") return node;

  // Recursively flatten each child's internal chains first (depth-first so the
  // bottom of a chain is collapsed before we look at the top).
  const kids = node.children.map(flattenSingleChild);

  // For each dir child that has exactly one dir grandchild, merge those two levels:
  // child becomes "child/grandchild" and inherits the grandchild's children.
  // Recursing on the merged node handles any residual chain (e.g. after "a/b" is
  // formed, "a/b" itself might still have a single dir child to collapse).
  const flatKids = kids.map((kid) => {
    if (kid.nodeKind !== "dir") return kid;
    if (kid.children.length === 1 && kid.children[0].nodeKind === "dir") {
      const grandkid = kid.children[0];
      return flattenSingleChild({
        ...grandkid,
        name: `${kid.name}/${grandkid.name}`,
      });
    }
    return kid;
  });

  return { ...node, children: flatKids };
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
 * Recursively assign degree = fan-in degree beneath (B3).
 *
 * - symbol leaf: own degree (set from StructureSymbol at build time).
 * - class node: own degree + sum of child method degrees.
 * - file/dir node: sum of children's degree (structural nodes contribute 0 self).
 *
 * WHY summation, not max: a folder of ten moderately-coupled files is more
 * architecturally significant than a folder with one hot file and nine isolates,
 * because changing it touches more callers across the codebase. Summation lets
 * folder weight reflect total coupling load; max would let a single hub symbol
 * dominate its whole directory even if everything else there is uncoupled.
 * This matches Phase A's hub ranking logic (total fan-in, not peak fan-in).
 *
 * Mirrors rollupCounts; called immediately after rollupCounts in buildTree().
 * The treemap sizing site uses max(node.degree, 1) so zero-degree nodes still
 * get a floor-size cell and are never hidden.
 */
function rollupDegree(node: TreeNode): number {
  // class and symbol nodes carry their own degree from the StructureSymbol.
  // For dir/file nodes the self-degree is 0 (they are structural, not symbols).
  const self =
    node.nodeKind === "class" || node.nodeKind === "symbol" ? node.degree : 0;
  const childSum = node.children.reduce((s, c) => s + rollupDegree(c), 0);
  node.degree = self + childSum;
  return node.degree;
}

/**
 * Build the full structural tree from a flat symbol list.
 *
 * Returns a root "dir" node. Folder nesting comes from the file path; within a
 * file, classes contain their methods. Empty input → an empty root.
 *
 * @param stripPrefix — when provided, this directory prefix is stripped from
 *   every file path before constructing the folder hierarchy. The original path
 *   is still stored on each file node (for navigation) — only the tree structure
 *   changes. After building, flattenSingleChild() is applied to collapse any
 *   residual single-child dir chains. Use commonDirPrefix(paths) to compute the
 *   right value before calling.
 */
export function buildTree(
  symbols: StructureSymbol[],
  rootName = "root",
  stripPrefix = "",
): TreeNode {
  const root: TreeNode = { name: rootName, nodeKind: "dir", count: 0, degree: 0, children: [] };
  if (symbols.length === 0) return root;

  // Normalize the prefix to end with "/" for consistent string slicing.
  const prefixWithSlash =
    stripPrefix && !stripPrefix.endsWith("/") ? `${stripPrefix}/` : stripPrefix;

  // Group symbols by file path (preserves input order within a file).
  const byFile = new Map<string, StructureSymbol[]>();
  for (const s of symbols) {
    const arr = byFile.get(s.path);
    if (arr) arr.push(s);
    else byFile.set(s.path, [s]);
  }

  // Insert each file into the dir tree by its path components.
  for (const [path, fileSymbols] of byFile) {
    // Strip the prefix from the path to avoid redundant intermediate dirs.
    // The original `path` is still stored on the node so navigation works.
    const displayPath =
      prefixWithSlash && path.startsWith(prefixWithSlash)
        ? path.slice(prefixWithSlash.length)
        : path;

    const parts = displayPath.split("/").filter(Boolean);
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
        child = { name: dir, nodeKind: "dir", count: 0, degree: 0, children: [] };
        cursor.children.push(child);
      }
      cursor = child;
    }

    // Create the file node with its symbol subtree.
    // Keep original `path` (not the stripped displayPath) for graph navigation.
    cursor.children.push({
      name: fileName,
      nodeKind: "file",
      path,
      count: 0,
      degree: 0,
      children: buildFileSymbols(fileSymbols),
    });
  }

  rollupCounts(root);
  rollupDegree(root);

  // When a prefix was stripped, also collapse any residual single-child dir chains.
  // flattenSingleChild(root) processes root's children (not the root itself), so
  // the root name — used as the area name in the breadcrumb — is always preserved.
  if (stripPrefix) {
    return flattenSingleChild(root);
  }

  return root;
}
