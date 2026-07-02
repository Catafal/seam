/**
 * Unit tests for the rollupDegree addition to buildTree.ts.
 * TDD: written BEFORE the implementation (should fail on first run).
 *
 * Contract:
 *   - TreeNode gains a `degree: number` field.
 *   - rollupDegree(node): void — mutates node.degree in place (mirrors rollupCounts).
 *   - Symbol leaf: degree = the StructureSymbol's own degree.
 *   - Class node: degree = own degree + sum of child method degrees.
 *   - File node: degree = sum of all direct children's degree (classes + top-level fns).
 *   - Dir node: degree = sum of child file/dir degrees.
 *   - Zero-degree nodes have node.degree === 0 (floor is applied at the sizing site
 *     via max(node.degree, 1), not here).
 *   - buildTree() calls rollupDegree internally so the returned tree has correct degrees.
 */

import { describe, it, expect } from "vitest";
import { buildTree } from "../lib/buildTree";
import type { StructureSymbol } from "../api/schema-types";

// ── helpers ───────────────────────────────────────────────────────────────────

function sym(
  path: string,
  name: string,
  kind = "function",
  degree = 0,
  qualified_name: string | null = null,
): StructureSymbol {
  return { path, name, kind, line: 1, qualified_name: qualified_name ?? name, degree };
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe("TreeNode.degree via buildTree", () => {
  it("symbol leaf carries its own degree from StructureSymbol", () => {
    const symbols = [sym("a.py", "myFunc", "function", 7)];
    const root = buildTree(symbols);
    const file = root.children[0];
    const fn = file.children[0];
    expect(fn.degree).toBe(7);
  });

  it("zero-degree symbol has degree === 0 on the node", () => {
    const symbols = [sym("a.py", "isolated", "function", 0)];
    const root = buildTree(symbols);
    const fn = root.children[0].children[0];
    expect(fn.degree).toBe(0);
  });

  it("class node sums its own degree plus all method degrees", () => {
    // Class Foo has degree=3; methods m1 (deg=5) and m2 (deg=2) are nested under it.
    // Class node degree = 3 + 5 + 2 = 10.
    const symbols = [
      sym("a.py", "Foo", "class", 3, "Foo"),
      sym("a.py", "m1", "method", 5, "Foo.m1"),
      sym("a.py", "m2", "method", 2, "Foo.m2"),
    ];
    const root = buildTree(symbols);
    const file = root.children[0];
    const cls = file.children.find((c) => c.name === "Foo")!;
    expect(cls.degree).toBe(3 + 5 + 2);
  });

  it("class with zero-degree methods has degree equal to its own degree", () => {
    const symbols = [
      sym("a.py", "Bar", "class", 4, "Bar"),
      sym("a.py", "doThing", "method", 0, "Bar.doThing"),
    ];
    const root = buildTree(symbols);
    const file = root.children[0];
    const cls = file.children.find((c) => c.name === "Bar")!;
    expect(cls.degree).toBe(4 + 0);
  });

  it("file node is the sum of all its symbol/class degrees", () => {
    // File with two functions (deg=3, deg=5) and no class wrapping
    const symbols = [
      sym("b.py", "alpha", "function", 3),
      sym("b.py", "beta", "function", 5),
    ];
    const root = buildTree(symbols);
    const file = root.children[0];
    expect(file.degree).toBe(8);
  });

  it("dir node is the sum of its child file degrees", () => {
    const symbols = [
      sym("pkg/a.py", "f1", "function", 4),
      sym("pkg/b.py", "f2", "function", 6),
    ];
    const root = buildTree(symbols);
    const dir = root.children[0]; // "pkg"
    expect(dir.degree).toBe(10);
  });

  it("deeply nested dirs accumulate degrees bottom-up", () => {
    const symbols = [
      sym("a/b/c.py", "fn", "function", 9),
    ];
    const root = buildTree(symbols);
    // root → a → b → c.py → fn
    expect(root.degree).toBe(9);
    const a = root.children[0];
    expect(a.degree).toBe(9);
  });

  it("root degree is the sum of ALL symbol degrees in the tree", () => {
    const symbols = [
      sym("x.py", "f1", "function", 1),
      sym("x.py", "f2", "function", 2),
      sym("y.py", "g1", "function", 4),
    ];
    const root = buildTree(symbols);
    expect(root.degree).toBe(1 + 2 + 4);
  });

  it("completely zero-degree tree has all nodes with degree === 0", () => {
    const symbols = [
      sym("z.py", "nothing", "function", 0),
    ];
    const root = buildTree(symbols);
    const file = root.children[0];
    const fn = file.children[0];
    expect(root.degree).toBe(0);
    expect(file.degree).toBe(0);
    expect(fn.degree).toBe(0);
  });
});
