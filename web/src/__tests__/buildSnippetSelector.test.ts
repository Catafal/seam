/**
 * TDD tests for buildSnippetSelector — pure helper that turns a symbol name
 * and its first indexed definition into a SnippetSelector for the Source panel.
 *
 * Contract:
 *   - symbol + firstDef present  → file+line selector (most precise; matches the shown metadata)
 *   - symbol present, firstDef null → symbol-only selector (API resolves by name)
 *   - symbol falsy (null/empty/undefined) → undefined (hook should be disabled)
 */

import { buildSnippetSelector } from "../lib/buildSnippetSelector";
import type { SymbolDefinition } from "../api/schema-types";

// ── Fixture ────────────────────────────────────────────────────────────────────

const DEF: SymbolDefinition = {
  file: "seam/indexer/parser.py",
  line: 42,
  signature: "def parse(code: str) -> Tree",
  docstring: "Parse source code.",
  visibility: null,
  is_exported: true,
  qualified_name: "seam.indexer.parser.parse",
  decorators: [],
};

// ── symbol + firstDef ─────────────────────────────────────────────────────────

describe("buildSnippetSelector — symbol + firstDef", () => {
  it("returns a selector with symbol, file, and line", () => {
    const sel = buildSnippetSelector("parse", DEF);
    expect(sel).toBeDefined();
    expect(sel!.symbol).toBe("parse");
    expect(sel!.file).toBe("seam/indexer/parser.py");
    expect(sel!.line).toBe(42);
  });

  it("includes the symbol name so the API can tie-break on homonyms", () => {
    const sel = buildSnippetSelector("Lexer.tokenize", DEF);
    expect(sel!.symbol).toBe("Lexer.tokenize");
  });

  it("uses the file from the given definition, not from the symbol name", () => {
    const otherDef: SymbolDefinition = { ...DEF, file: "seam/query/engine.py", line: 10 };
    const sel = buildSnippetSelector("parse", otherDef);
    expect(sel!.file).toBe("seam/query/engine.py");
    expect(sel!.line).toBe(10);
  });

  it("does NOT set uid (the panel does not have a uid)", () => {
    const sel = buildSnippetSelector("parse", DEF);
    expect(sel!.uid).toBeUndefined();
  });
});

// ── symbol only (missing definition) ─────────────────────────────────────────

describe("buildSnippetSelector — missing definition (symbol-only selector)", () => {
  it("returns a selector with symbol but no file/line when firstDef is null", () => {
    const sel = buildSnippetSelector("parse", null);
    expect(sel).toBeDefined();
    expect(sel!.symbol).toBe("parse");
    expect(sel!.file).toBeUndefined();
    expect(sel!.line).toBeUndefined();
  });

  it("returns a selector with symbol but no file/line when firstDef is undefined", () => {
    const sel = buildSnippetSelector("parse", undefined);
    expect(sel).toBeDefined();
    expect(sel!.symbol).toBe("parse");
    expect(sel!.file).toBeUndefined();
  });
});

// ── falsy symbol → undefined ──────────────────────────────────────────────────

describe("buildSnippetSelector — falsy symbol", () => {
  it("returns undefined when symbol is null", () => {
    expect(buildSnippetSelector(null, DEF)).toBeUndefined();
  });

  it("returns undefined when symbol is empty string", () => {
    expect(buildSnippetSelector("", DEF)).toBeUndefined();
  });

  it("returns undefined when symbol is whitespace-only", () => {
    expect(buildSnippetSelector("  ", DEF)).toBeUndefined();
  });

  it("returns undefined when symbol is null and firstDef is also null", () => {
    expect(buildSnippetSelector(null, null)).toBeUndefined();
  });
});
