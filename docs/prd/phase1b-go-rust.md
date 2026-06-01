# PRD — Phase 1b: Go + Rust parsers (reverses ADR-005 deferral)

> Slice of Phase 1b. Implements the "Phase 1 adds tree-sitter-go and tree-sitter-rust" consequence of ADR-005. Status: ready-for-agent.

## Problem Statement

As a developer working in a Go or Rust codebase, I cannot use Seam at all — `seam init`
silently skips `.go` and `.rs` files because they aren't in the language map. ADR-005
deliberately deferred Go and Rust to "Phase 1" (high demand, mature grammars); they remain
unimplemented. So a large slice of the target audience gets zero value from Seam.

## Solution

As a Go or Rust developer, I want `seam init` to index my `.go`/`.rs` files — extracting
functions, methods, types, imports, call edges, doc-comments, and semantic comments — so that
`seam_query`, `seam_context`, `seam_impact`, `seam_trace`, `seam_changes`, and `seam_why` all
work on my codebase exactly as they do for Python and TypeScript.

## User Stories

1. As a Go developer, I want `.go` files indexed by `seam init`, so that my Go code is searchable and analyzable.
2. As a Rust developer, I want `.rs` files indexed by `seam init`, so that my Rust code is searchable and analyzable.
3. As a Go developer, I want `func`, methods (`func (r T) M()`), structs, interfaces, and type aliases extracted as symbols, so that `seam_context`/`seam_query` find them.
4. As a Go developer, I want a method `func (r *Repo) Save()` indexed as `Repo.Save` (method kind), so that it reads like other languages' qualified methods.
5. As a Rust developer, I want `fn`, methods inside `impl` blocks (as `Type.method`), structs, enums, and traits extracted as symbols, so that my code is navigable.
6. As an agent, I want Go/Rust symbol kinds mapped to the existing vocabulary (function/class/method/interface/type) — Go struct→class, interface→interface, type→type; Rust struct→class, enum→type, trait→interface — so that impact/context behave uniformly and the kind enum stays closed.
7. As an agent, I want Go `import` specs and Rust `use` declarations extracted as import edges, so that cross-file dependency reasoning works.
8. As an agent, I want bare-identifier call expressions in Go/Rust extracted as call edges (same MVP heuristic as Python/TS — precision is not the goal), so that `seam_impact`/`seam_trace` have a call graph to traverse.
9. As an agent, I want Go/Rust call/import edges to flow through the same whole-index confidence resolution, so that EXTRACTED/AMBIGUOUS/INFERRED works for them automatically (no per-language confidence code).
10. As an agent, I want a Go declaration's leading `//` doc-comment block and a Rust item's leading `///` doc lines captured as the symbol's docstring, so that `seam_search`/`seam_context` surface the documented intent.
11. As an agent, I want semantic comments (WHY/HACK/NOTE/TODO/FIXME) extracted from Go (`//`, `/* */`) and Rust (`//`, `///`, `//!`, `/* */`), so that `seam_why` works for Go/Rust.
12. As a maintainer, I want Go/Rust parsing to never raise (binary/oversize/parse-error → None / []), consistent with the existing parser and extractor contracts.
13. As a maintainer, I want `.go`→go and `.rs`→rust added to the language map in `seam/config.py`, so that language detection stays config-driven (no hardcoding elsewhere).
14. As a maintainer, I want `tree-sitter-go` and `tree-sitter-rust` added as pinned dependencies, so that the build is reproducible.
15. As a maintainer, I want ADR-005 updated (or a short ADR-006) noting Go/Rust are now implemented, so that the decision record stays accurate.
16. As a Go/Rust developer, I want files with syntax errors to still index whatever parsed (partial tree), so that one bad file doesn't blank my index.

## Implementation Decisions

- **Dependencies.** Add `tree-sitter-go` (resolves 0.25.0) and `tree-sitter-rust` (resolves 0.24.2), compatible with the pinned tree-sitter 0.25 core. Pin with `>=` consistent with the existing grammar deps.
- **parser.py.** Build `_GO_LANG` / `_RUST_LANG` Language singletons at module load (like `_PY_LANG`); add `parse_go(path)` and `parse_rust(path)` delegating to the existing `_parse(path, language)` helper (all guards — size/binary/read/backstop — reused unchanged).
- **config.py language map.** Add `".go": "go"` and `".rs": "rust"`.
- **pipeline `_dispatch_parser`.** Add the `"go"` and `"rust"` branches.
- **graph.py extraction (the bulk).** Add `_extract_symbols_go` / `_extract_symbols_rust`, `_extract_edges_go` / `_extract_edges_rust`, `_extract_comments_go` / `_extract_comments_rust`, and route them in the public `extract_symbols` / `extract_edges` / `extract_comments` dispatchers for `language in ("go",)` and `("rust",)`. Reuse `_text`, `_make_symbol`, the `Edge`/`Symbol`/`Comment` TypedDicts, the marker regex (`_MARKER_RE` / `_match_marker`), and the same INFERRED-default edge confidence (whole-index resolution at read time handles the rest — no per-language confidence logic).
  - **Kind mapping (decision: existing 5 kinds).** Go: `function_declaration`→function; `method_declaration`→method (qualified `Recv.Name` from the receiver type, pointer `*T` normalized to `T`); `type_declaration`/`type_spec` with `struct_type`→class, `interface_type`→interface, else→type. Rust: `function_item`→function (or method when inside an `impl_item` → `Type.fn`); `struct_item`→class; `enum_item`→type; `trait_item`→interface. (`mod_item` is traversed for nested items but not emitted as its own symbol in this MVP.)
  - **Docstrings (decision: full parity).** Go: the contiguous block of `//` line comments immediately preceding a declaration (no blank line gap) → joined docstring. Rust: the contiguous leading `///` doc-comment lines → joined docstring. Captured into `Symbol.docstring`; `None` when absent. (Block `/** */`-style is out of scope for Go/Rust docs.)
  - **Edges.** Imports: Go `import_spec` (the imported path's last segment / package name) ; Rust `use_declaration` (the final path segment) — `source = file stem`, `kind="import"`, matching the Python/TS convention. Calls: `call_expression` with a bare identifier callee → `kind="call"`, `source` = nearest enclosing named function/method (reuse the existing enclosing-scope walk pattern). Selector/method calls (`pkg.Fn()`, `x.method()`) are NOT tracked in this MVP (same bare-identifier-only rule as Python/TS).
  - **Comments.** Go: `comment` nodes (`//` and `/* */`). Rust: `line_comment` (covers `//`, `///`, `//!`) and `block_comment`. Strip delimiters, run `_match_marker`, emit `Comment` with the correct line (block comments scanned line-by-line, like the TS path).
- **Never-raises contract.** All new `_extract_*_go/_rust` run under the existing try/except in the public dispatchers (return [] on error). `parse_go`/`parse_rust` inherit the `_parse` backstop.
- **No schema change.** Go/Rust symbols/edges/comments use the existing tables. No migration.
- **ADR.** Append a short status note to ADR-005 (or add ADR-006) recording Go/Rust as implemented in Phase 1b.

## Testing Decisions

- **What makes a good test:** assert external behavior — given a small Go/Rust source fixture, the extractors return the expected symbols (name, kind, line range), edges (import + call), docstrings, and semantic comments. Test through `extract_symbols`/`extract_edges`/`extract_comments` (the public API), not internals.
- **Fixtures:** add `tests/fixtures/sample.go` and `tests/fixtures/sample.rs` mirroring the existing `sample.py`/`sample.ts` — each containing a documented function, a struct/type with a method, an interface/trait, an import, an internal call, and WHY/HACK/NOTE markers (incl. a Rust `///` doc + a multi-line block).
- **Modules tested:**
  - Go symbols: func→function, method→`Recv.Name` method, struct→class, interface→interface, type→type; doc-comment → docstring.
  - Rust symbols: fn→function, impl method→`Type.fn`, struct→class, enum→type, trait→interface; `///` → docstring.
  - Go/Rust edges: an import edge and a call edge are produced; bare-identifier only.
  - Go/Rust comments: each marker extracted (incl. Rust `///`/`//!` and a block); plain comments ignored.
  - Pipeline/parser: `parse_go`/`parse_rust` return a node for valid source, None for binary; `_dispatch_parser` routes `.go`/`.rs`; a `.go`/`.rs` file is actually indexed by `index_one_file`.
- **Prior art:** `tests/unit/test_parser.py`, `test_confidence.py`, `test_richer_edges.py`, `test_seam_why.py`, and the `sample.py`/`sample.ts` fixtures.
- **TDD:** write the failing Go-symbols test (func→function) first, then build out.

## Out of Scope

- Selector/method-call edge precision (`pkg.Fn()`, `recv.method()`) — same bare-identifier MVP as Python/TS.
- Go generics / Rust macro-generated items beyond what tree-sitter surfaces as plain nodes.
- Rust `mod` as its own symbol — nested items are traversed, the module itself is not emitted.
- Go build tags / Rust `cfg` conditional compilation awareness.
- `/** */` JSDoc-style doc blocks for Go/Rust (Go uses `//`, Rust uses `///`).
- Cross-package/crate resolution beyond the existing whole-index name matching.

## Further Notes

- Confidence comes for free: edges store string names and confidence resolves whole-index at read time, so Go/Rust edges get EXTRACTED/AMBIGUOUS/INFERRED with no new code.
- Honesty: the bare-identifier call heuristic is intentionally simple (precision is not the Phase-1 goal) — cross-file confidence flags the uncertainty.
- After merge, Go/Rust users run `seam init` and immediately get all seven tools.
- Acceptance: `.go`/`.rs` indexed; symbols/edges/docstrings/semantic-comments extracted with the documented kind mapping; all tools work on Go/Rust; ADR updated; `make gate` green.
