/**
 * Tests for codeFileFilter helpers.
 *
 * Covers: extension allowlist membership (code kept, non-code dropped),
 * edge cases (no extension, dot-files, null/empty, uppercase extensions),
 * and filterCodeFiles array helper.
 */

import { describe, it, expect } from "vitest";
import { isCodeFile, filterCodeFiles, CODE_EXTENSIONS } from "../lib/codeFileFilter";

describe("CODE_EXTENSIONS allowlist", () => {
  it("contains every Seam-indexed extension", () => {
    const expected = [
      ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs",
      ".go", ".rs", ".java", ".cs", ".rb",
      ".c", ".h", ".cpp", ".cc", ".cxx", ".c++",
      ".hpp", ".hh", ".hxx", ".php", ".swift",
    ];
    for (const ext of expected) {
      expect(CODE_EXTENSIONS.has(ext), `missing ${ext}`).toBe(true);
    }
  });

  it("has exactly 22 entries (the canonical SEAM_LANGUAGE_MAP count)", () => {
    expect(CODE_EXTENSIONS.size).toBe(22);
  });
});

describe("isCodeFile", () => {
  // --- code files (must return true) ---
  it.each([
    ["auth.py"],
    ["src/components/App.tsx"],
    ["lib/utils.ts"],
    ["main.js"],
    ["worker.mjs"],
    ["bundle.cjs"],
    ["server/main.go"],
    ["src/lib.rs"],
    ["Service.java"],
    ["Controller.cs"],
    ["app.rb"],
    ["hash.c"],
    ["defs.h"],
    ["engine.cpp"],
    ["fast.cc"],
    ["core.cxx"],
    ["ops.c++"],
    ["types.hpp"],
    ["utils.hh"],
    ["defs.hxx"],
    ["index.php"],
    ["AppDelegate.swift"],
    // Nested paths
    ["deep/nested/path/module.py"],
    // Uppercase extension — should still match (case-insensitive)
    ["Script.PY"],
    ["Module.TS"],
  ])("returns true for code file: %s", (path) => {
    expect(isCodeFile(path)).toBe(true);
  });

  // --- non-code files (must return false) ---
  it.each([
    ["README.md"],
    ["CHANGELOG.md"],
    ["notes.txt"],
    ["config.yaml"],
    ["config.yml"],
    ["package.json"],
    ["Makefile"],          // no extension
    [".gitignore"],        // dot-file, no real extension
    [".env"],              // dot-file
    ["schema.sql"],
    ["Dockerfile"],        // no extension
    ["data.csv"],
    ["image.png"],
    ["seam.log"],
    ["output.log"],
    ["docs/api.rst"],
    ["report.pdf"],
    ["archive.tar.gz"],    // double ext — .gz not indexed
    ["file."],             // trailing dot
  ])("returns false for non-code file: %s", (path) => {
    expect(isCodeFile(path)).toBe(false);
  });

  // --- edge cases ---
  it("returns false for empty string", () => {
    expect(isCodeFile("")).toBe(false);
  });

  it("returns false when the only dot is at position 0 (dot-file)", () => {
    expect(isCodeFile(".hidden")).toBe(false);
  });

  it("handles a bare filename with no path segments", () => {
    expect(isCodeFile("main.go")).toBe(true);
  });

  it("handles Windows-style backslash paths gracefully (treats whole string as basename)", () => {
    // split("/") on a Windows path gives one segment — still extracts extension correctly
    expect(isCodeFile("src\\main.py")).toBe(true); // .py is the extension
  });
});

describe("filterCodeFiles", () => {
  it("keeps only code file entries", () => {
    const items = [
      { name: "check", file: "auth.py" },
      { name: "readme_link", file: "README.md" },
      { name: "parse", file: "parser.ts" },
      { name: "config_val", file: "config.yaml" },
    ];
    const result = filterCodeFiles(items);
    expect(result.map((i) => i.name)).toEqual(["check", "parse"]);
  });

  it("returns empty array when all items are non-code", () => {
    const items = [
      { name: "x", file: "CHANGELOG.md" },
      { name: "y", file: "notes.txt" },
    ];
    expect(filterCodeFiles(items)).toHaveLength(0);
  });

  it("returns all items when all are code files", () => {
    const items = [
      { name: "a", file: "a.py" },
      { name: "b", file: "b.go" },
    ];
    expect(filterCodeFiles(items)).toHaveLength(2);
  });

  it("drops items where file is null", () => {
    const items = [
      { name: "a", file: null as string | null },
      { name: "b", file: "main.py" },
    ];
    const result = filterCodeFiles(items);
    expect(result.map((i) => i.name)).toEqual(["b"]);
  });

  it("drops items where file is undefined", () => {
    const items = [
      { name: "a", file: undefined as string | undefined },
      { name: "b", file: "main.rs" },
    ];
    const result = filterCodeFiles(items);
    expect(result.map((i) => i.name)).toEqual(["b"]);
  });

  it("preserves all fields on kept items (type passthrough)", () => {
    const items = [{ name: "fn", file: "lib.py", start_line: 10, extra: "data" }];
    const result = filterCodeFiles(items);
    expect(result[0]).toEqual({ name: "fn", file: "lib.py", start_line: 10, extra: "data" });
  });

  it("handles an empty input array", () => {
    expect(filterCodeFiles([])).toEqual([]);
  });
});
