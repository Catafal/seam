# ADR-009: Swift language support (Phase 10) — and explicit Kotlin deferral

**Status:** Accepted  
**Date:** 2026-06-02  
**Phase:** 10

## Context

Phase 9 expanded Seam to 11 languages (Java, C#, Ruby, C, C++, PHP, plus the original 5).
Swift (iOS/macOS) and Kotlin (Android/JVM) were evaluated as the next candidates.

Swift is the dominant Apple-platform language and a significant gap. Kotlin was evaluated
alongside Swift as a potential co-ship.

## Decision — Swift: Added

`tree-sitter-swift 0.7.3` was evaluated against `tree-sitter 0.25.2` with a realistic
Swift fixture covering: class, struct, actor, extension, enum, protocol, top-level function,
import declarations, bare-identifier calls, member/navigation calls, doc-comments, and
semantic comment markers.

**Verification result:** `has_error = False`. All key constructs parsed cleanly:

| Construct | Grammar node | Seam kind |
|-----------|-------------|-----------|
| `class Foo {}` | `class_declaration` keyword=`class` | `class` |
| `struct Foo {}` | `class_declaration` keyword=`struct` | `class` |
| `actor Foo {}` | `class_declaration` keyword=`actor` | `class` |
| `extension Foo {}` | `class_declaration` keyword=`extension` | `class` (extended type name) |
| `enum Foo {}` | `class_declaration` keyword=`enum` (body=`enum_class_body`) | `type` |
| `protocol Foo {}` | `protocol_declaration` | `interface` |
| Top-level `func foo()` | `function_declaration` | `function` |
| Method `func foo()` inside class | `function_declaration` in `class_body` | `method` |
| Protocol method | `protocol_function_declaration` | `method` |
| `import Foundation` | `import_declaration → identifier → simple_identifier` | import edge |
| `import UIKit.UIView` | last `simple_identifier` segment → `UIView` | import edge |
| `foo()` bare call | `call_expression` callee=`simple_identifier` | call edge |
| `obj.method()` | `call_expression` callee=`navigation_expression` | SKIPPED |

**Implementation:** New extractor module `seam/indexer/graph_swift.py` (mirrors
`graph_go_rust.py` structure and contract). Dispatch wired in `graph.py`, `parser.py`,
`pipeline.py`. Phase 4 enrichment in `signatures_ext.py`. Phase 5 import mapping in
`imports_ext.py`. Builtins in `builtins.py`.

**No schema change, no migration, no new MCP tools. Tool count stays 10.**

## Decision — Kotlin: Explicitly Deferred

`tree-sitter-kotlin 1.1.0` was evaluated against `tree-sitter 0.25.2` with a realistic
Kotlin fixture covering: class, interface, object, fun, data class.

**Verification result:** `has_error = True` on all common constructs. Specifically:

- `class Foo { ... }` — `ERROR` node wrapping the class body
- `interface Bar { fun method() }` — `ERROR` node
- `object Singleton { }` — `ERROR` node
- `fun topLevel() {}` — parsed but with `ERROR` children on parameters
- Data class — `ERROR` on constructor parameters

**Empirical yield:** Approximately 1 out of 6 symbols recovered on a realistic file.
The remaining 5/6 produce `ERROR` nodes, meaning they would be silently dropped from
the index — a substantially worse outcome than indexing nothing at all (which at least
doesn't mislead impact/trace queries).

**Root cause:** `tree-sitter-kotlin 1.1.0` grammar targets Kotlin 1.x syntax but has
known incompatibilities with the `tree-sitter 0.25.x` ABI. The grammar generates ERROR
nodes for standard class/interface syntax that every production Kotlin file uses.

**Deferral condition:** Revisit when either:
1. `tree-sitter-kotlin` ships a grammar version that parses idiomatic Kotlin with
   `has_error = False` against `tree-sitter 0.25.x`, OR
2. An alternative grammar emerges (e.g. tree-sitter-kotlin from a different maintainer)
   that passes the same fixture test with ≥5/6 symbols recovered and `has_error = False`.

**Risk of shipping anyway:** Indexing ~1/6 of a Kotlin file would make `seam_impact` and
`seam_trace` silently incomplete, producing false negatives worse than no Swift support at
all. The existing "no result" from unindexed files is a known, documented limitation; a
partial index with ERROR-dropped symbols is an invisible, harder-to-debug regression.

## Consequences

- `.swift` files are now indexed across all ten MCP tools.
- Kotlin remains unindexed. `.kt` / `.kts` are not in `SEAM_LANGUAGE_MAP`.
- The decision record exists so future contributors know Kotlin was evaluated (not ignored)
  and what the acceptance bar is.
- `tree-sitter-swift` is added to `pyproject.toml` as a pinned dependency.
- `tree-sitter-kotlin` is intentionally NOT added.
