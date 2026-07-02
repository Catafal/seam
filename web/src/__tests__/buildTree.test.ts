/**
 * Unit tests for buildTree.ts slice A2 additions:
 *   - commonDirPrefix  — strip the longest shared dir prefix from scoped paths
 *   - flattenSingleChild — collapse single-child dir chains so the treemap
 *                          doesn't waste clicks on empty intermediate dirs
 *   - buildTree stripPrefix — integration: prefix strip + flatten in one call
 *
 * TDD: these tests were written BEFORE the implementation so they initially fail.
 */

import { describe, it, expect } from "vitest";
import { buildTree, commonDirPrefix, flattenSingleChild, type TreeNode } from "../lib/buildTree";
import type { StructureSymbol } from "../api/schema-types";

// ── helpers ───────────────────────────────────────────────────────────────────

function sym(path: string, name: string, kind = "function"): StructureSymbol {
  return { path, name, kind, line: 1, qualified_name: name, degree: 0 };
}

/** Collect direct child names of a node (for compact assertions). */
function childNames(node: TreeNode): string[] {
  return node.children.map((c) => c.name);
}

// ── commonDirPrefix ───────────────────────────────────────────────────────────

describe("commonDirPrefix", () => {
  it("returns the shared dir prefix for paths in the same folder", () => {
    expect(commonDirPrefix(["seam/server/tools.py", "seam/server/handler.py"])).toBe(
      "seam/server",
    );
  });

  it("returns only the shared prefix when paths diverge at a subdirectory", () => {
    expect(
      commonDirPrefix(["seam/server/tools.py", "seam/analysis/clustering.py"]),
    ).toBe("seam");
  });

  it("returns empty string when there is no common prefix", () => {
    expect(commonDirPrefix(["web/app.ts", "seam/db.py"])).toBe("");
  });

  it("returns empty string for a single file in the root", () => {
    expect(commonDirPrefix(["db.py"])).toBe("");
  });

  it("returns the parent dir for a single deep file", () => {
    expect(commonDirPrefix(["seam/server/tools.py"])).toBe("seam/server");
  });

  it("returns empty string for an empty array", () => {
    expect(commonDirPrefix([])).toBe("");
  });

  it("does NOT include the filename component in the prefix", () => {
    // Even though both files have the same name, the prefix is the dir only
    expect(commonDirPrefix(["a/config.py", "b/config.py"])).toBe("");
  });

  it("handles deeper shared nesting", () => {
    expect(
      commonDirPrefix([
        "pkg/a/sub/x.py",
        "pkg/a/sub/y.py",
        "pkg/a/sub/z.py",
      ]),
    ).toBe("pkg/a/sub");
  });
});

// ── flattenSingleChild ────────────────────────────────────────────────────────

describe("flattenSingleChild", () => {
  /** Quick TreeNode factory for dir/file nodes. */
  function dir(name: string, ...kids: TreeNode[]): TreeNode {
    return { name, nodeKind: "dir", count: kids.length, degree: 0, children: kids };
  }
  function file(name: string): TreeNode {
    return { name, nodeKind: "file", path: name, count: 0, degree: 0, children: [] };
  }

  it("collapses a single-child dir chain into one merged node", () => {
    // a/ → b/ → [x.py, y.py]   should become  a/b/ → [x.py, y.py]
    const root = dir("root", dir("a", dir("b", file("x.py"), file("y.py"))));
    const flat = flattenSingleChild(root);
    // root had one dir child "a" which itself had one dir child "b" → root still
    // has one child but it is now named "a/b" and has the two files directly
    expect(flat.children).toHaveLength(1);
    expect(flat.children[0].name).toBe("a/b");
    expect(flat.children[0].children.map((c) => c.name)).toEqual(["x.py", "y.py"]);
  });

  it("does NOT collapse when a dir has multiple children", () => {
    const root = dir("root", dir("a", file("x.py"), file("y.py")));
    const flat = flattenSingleChild(root);
    // "a" has two file children → no collapse
    expect(flat.children).toHaveLength(1);
    expect(flat.children[0].name).toBe("a");
    expect(flat.children[0].children).toHaveLength(2);
  });

  it("does NOT collapse a single-child dir that leads to a file (not a dir)", () => {
    const root = dir("root", dir("a", file("x.py")));
    const flat = flattenSingleChild(root);
    // "a" has one CHILD but it is a file, not a dir → no merge
    expect(flat.children[0].name).toBe("a");
    expect(flat.children[0].children[0].name).toBe("x.py");
  });

  it("handles deep chains (a/b/c all single-dir)", () => {
    const root = dir(
      "root",
      dir("a", dir("b", dir("c", file("z.py"), file("w.py")))),
    );
    const flat = flattenSingleChild(root);
    expect(flat.children[0].name).toBe("a/b/c");
    expect(flat.children[0].children.map((c) => c.name)).toEqual(["z.py", "w.py"]);
  });

  it("preserves non-dir leaf nodes unchanged", () => {
    const sym: TreeNode = {
      name: "myFunc",
      nodeKind: "symbol",
      count: 1,
      degree: 0,
      children: [],
    };
    expect(flattenSingleChild(sym)).toBe(sym); // identity — no mutation
  });

  it("preserves sibling dirs that each have single child chains", () => {
    // root → [a/b/…, c/d/…] — each chain collapsed independently
    const root = dir(
      "root",
      dir("a", dir("b", file("1.py"), file("2.py"))),
      dir("c", dir("d", file("3.py"))),
    );
    const flat = flattenSingleChild(root);
    expect(flat.children.map((c) => c.name)).toEqual(["a/b", "c/d"]);
  });
});

// ── buildTree with stripPrefix ────────────────────────────────────────────────

describe("buildTree with stripPrefix", () => {
  it("strips the common prefix so scoped files appear at the root level", () => {
    const symbols = [
      sym("seam/server/tools.py", "handle"),
      sym("seam/server/tools.py", "dispatch"),
      sym("seam/server/handler.py", "create"),
    ];
    // With stripPrefix "seam/server", files should be direct children of root
    const root = buildTree(symbols, "server", "seam/server");
    // After prefix strip: paths become tools.py and handler.py
    expect(root.name).toBe("server");
    const names = childNames(root);
    expect(names).toContain("tools.py");
    expect(names).toContain("handler.py");
    // No intermediate dir nodes
    expect(root.children.every((c) => c.nodeKind !== "dir")).toBe(true);
  });

  it("preserves original path on file nodes for navigation (not the stripped path)", () => {
    const symbols = [sym("seam/server/tools.py", "handle")];
    const root = buildTree(symbols, "server", "seam/server");
    const file = root.children.find((c) => c.name === "tools.py")!;
    // Original path must be kept so graph navigation works
    expect(file.path).toBe("seam/server/tools.py");
  });

  it("collapses single-child dirs produced after stripping", () => {
    // Only one file in a subdirectory after stripping — verify flatten is applied
    const symbols = [
      sym("seam/server/sub/tools.py", "handle"),
      sym("seam/server/sub/tools.py", "dispatch"),
    ];
    // Stripping "seam/server" leaves "sub/tools.py" → sub/ dir with 1 file child
    // flattenSingleChild won't collapse file-only chains, but it should handle
    // residual single-child dir nodes that are THEMSELVES dirs
    const root = buildTree(symbols, "server", "seam/server");
    // root → sub/ → tools.py  (sub has 1 file child, no dir collapse needed)
    expect(root.children[0].name).toBe("sub");
    expect(root.children[0].children[0].name).toBe("tools.py");
  });

  it("collapses multi-level single-dir chains after stripping", () => {
    // After stripping "pkg", the remaining paths all go through a/b/
    const symbols = [
      sym("pkg/a/b/x.py", "f1"),
      sym("pkg/a/b/y.py", "f2"),
    ];
    const root = buildTree(symbols, "pkg", "pkg");
    // After strip: a/b/x.py and a/b/y.py → a/ has 1 child b/ → collapsed to a/b
    expect(root.children[0].name).toBe("a/b");
    expect(root.children[0].children.map((c) => c.name).sort()).toEqual(["x.py", "y.py"]);
  });

  it("with empty stripPrefix behaves identically to the original buildTree", () => {
    const symbols = [
      sym("seam/indexer/db.py", "init_db"),
      sym("seam/analysis/clustering.py", "Louvain", "class"),
    ];
    const bare = buildTree(symbols, "root");
    const withEmpty = buildTree(symbols, "root", "");
    // Both must have the same top-level child structure
    expect(childNames(withEmpty)).toEqual(childNames(bare));
  });

  it("does not affect symbol nodes' qualifiedName or line metadata", () => {
    const symbols = [
      {
        path: "seam/server/tools.py",
        name: "handle",
        kind: "function",
        line: 42,
        qualified_name: "Handler.handle",
        degree: 0,
      },
    ];
    const root = buildTree(symbols, "server", "seam/server");
    const file = root.children.find((c) => c.nodeKind === "file")!;
    const fn = file.children[0];
    expect(fn.line).toBe(42);
    expect(fn.qualifiedName).toBe("Handler.handle");
  });
});
