# PRD — Phase 10: Swift support (11 → 12 languages)

> Adds Swift (iOS/macOS) to Seam. Pairs with the Phase 9 language expansion. **Kotlin was
> evaluated alongside Swift and DEFERRED** — the only available grammar (`tree-sitter-kotlin 1.1.0`)
> produces `ERROR` nodes on common constructs (interfaces, objects, classes-with-constructor) and
> recovered only ~1 of 6 symbols on a realistic file; shipping it would silently drop most code.
> Swift's grammar (`tree-sitter-swift 0.7.3`) parses cleanly against the pinned tree-sitter 0.25.2.
> Status: ready-for-agent.

## Problem Statement

As a developer or AI agent working in a Swift (iOS / macOS / SwiftUI) codebase, I get zero value
from Seam — `seam init` silently skips `.swift` files because they aren't in `SEAM_LANGUAGE_MAP`.
Swift is the dominant Apple-platform language and a major gap now that Seam covers 11 languages.

## Solution

As a Swift developer, I want `seam init` to index my `.swift` files — extracting classes, structs,
enums, protocols, extensions, functions, methods, imports, call edges, doc-comments, and semantic
comments — so that **all ten MCP tools** work on my Swift codebase exactly as they do for the other
11 languages, with the same EXTRACTED/AMBIGUOUS/INFERRED confidence model and the same Phase 4
enrichment fields (signature, decorators, is_exported, visibility, qualified_name).

## User Stories

1. As a Swift developer, I want `.swift` files indexed by `seam init`, so my Swift code is searchable and analyzable.
2. As an agent, I want Swift symbols mapped onto the existing closed kind vocabulary (function/class/method/interface/type), so impact/context behave uniformly.
3. As an agent, I want `class`, `struct`, `actor`, and `extension` → kind=class; `enum` → kind=type; `protocol` → kind=interface — so the kind enum stays closed.
4. As a Swift developer, I want a method `func save()` inside `class Repo` indexed as `Repo.save` (kind=method), so it reads like other languages' qualified methods.
5. As an agent, I want top-level `func` → kind=function, so free functions are navigable.
6. As an agent, I want `import` declarations extracted as import edges (target = the imported module / last path segment), so cross-file dependency reasoning works.
7. As an agent, I want bare-identifier call expressions extracted as call edges (same MVP heuristic as the other languages — member/method calls `obj.m()`/`self.m()` are NOT tracked), so `seam_impact`/`seam_trace` have a call graph.
8. As an agent, I want Swift edges to flow through the existing whole-index confidence resolution, so EXTRACTED/AMBIGUOUS/INFERRED works with no per-language confidence code.
9. As an agent, I want a Swift symbol's leading `///` or `/** */` doc-comment captured as its docstring, so `seam_search`/`seam_context` surface documented intent.
10. As an agent, I want semantic comments (WHY/HACK/NOTE/TODO/FIXME) extracted from Swift `//` and `/* */` comments, so `seam_why` works for Swift.
11. As an agent, I want the Phase 4 enrichment fields populated: signature always; visibility/is_exported from access-level modifiers (public/open → exported; private/fileprivate/internal); decorators = Swift attributes (`@objc`, `@available`, `@MainActor`) verbatim where present, else `[]`; qualified_name passed through.
12. As an agent, I want import-mapping extraction so Phase 5 promotion can run; file-path resolution is best-effort and returns `[]` for system frameworks (Foundation, UIKit) — degrading cleanly to the name-count rule.
13. As an agent, I want a conservative Swift builtin vocabulary (print, String, Int, Array, Dictionary, Optional, Bool, etc.), so count==0 builtin names aren't mislabeled as unresolved repo symbols (guarded by the existing count==0 rule).
14. As a maintainer, I want `.swift` → swift added to `SEAM_LANGUAGE_MAP`, so detection stays config-driven.
15. As a maintainer, I want `tree-sitter-swift` added as a pinned dependency, so the build is reproducible.
16. As a maintainer, I want Swift parsing/extraction to NEVER raise (binary/oversize/parse-error/malformed → None / [] / safe defaults), consistent with the existing contracts.
17. As a maintainer, I want files with syntax errors to still index whatever parsed (partial tree).
18. As a maintainer, I want no new module > 1000 lines and no function > 200 lines.
19. As a maintainer, I want the ADR to record Swift as added AND Kotlin as explicitly deferred (with the grammar evidence), so the decision record stays accurate.

## Implementation Decisions

### Dependency
Add and pin `tree-sitter-swift` (resolves 0.7.3; verified to parse against tree-sitter 0.25.2 with
`has_error=False` on idiomatic Swift). Add via `uv add` so `pyproject.toml` + `uv.lock` update.
**Do NOT add tree-sitter-kotlin** (deferred — see ADR).

### config.py
Add `".swift": "swift"` to `SEAM_LANGUAGE_MAP`.

### parser.py
Build `_SWIFT_LANG = Language(tree_sitter_swift.language())` at module load; add `parse_swift(path)`
delegating to the existing `_parse(path, lang)` helper.

### pipeline.py
Add the `"swift"` branch to `_dispatch_parser`.

### New extractor module — `seam/indexer/graph_swift.py`
A single-language module mirroring `graph_go_rust.py`'s contract (imports only graph_common + signatures + config):
`_extract_symbols_swift`, `_extract_edges_swift`, `_extract_comments_swift`. Routed in graph.py's
public `extract_symbols`/`extract_edges`/`extract_comments`.

### Kind mapping (verified against the real grammar — closed vocabulary)
The tree-sitter-swift grammar represents class / struct / actor / extension / enum all as
`class_declaration`, distinguished by the keyword child:
- keyword `class` / `struct` / `actor` → kind=class
- keyword `extension` → kind=class (the extended type's name; methods inside qualified as `Type.method`)
- keyword `enum` (body = `enum_class_body`) → kind=type
- `protocol_declaration` → kind=interface; `protocol_function_declaration` inside → kind=method (`Proto.method`)
- `function_declaration`: top-level → kind=function; inside a `class_body` → kind=method (`Type.method`)
- name fields: `type_identifier` for types, `simple_identifier` for functions/methods.
- `property_declaration` and `enum_entry` are NOT emitted as symbols in this MVP (no matching kind).

### Edges (same bare-identifier MVP as the other languages)
- **Imports:** `import_declaration` → target = the module name (for `import Foundation`) or the last
  `simple_identifier` segment (for `import UIKit.UIView` → `UIView`). `source = file stem`, `kind="import"`.
- **Calls:** `call_expression` with a bare `simple_identifier` callee → `kind="call"`, `source` =
  nearest enclosing named function/method (via the extended `_find_enclosing_function`). Member /
  navigation calls (`obj.m()`, `self.m()`) are NOT tracked.

### `graph_common._find_enclosing_function`
Add Swift: function node types `{function_declaration}`; class-context types `{class_declaration, protocol_declaration}`
(the enclosing type name for `Type.method` qualification). Keep the leaf property.

### Phase 4 enrichment — `signatures_ext.py`
Add a `_extract_swift` branch (and a dispatch branch in `signatures.extract_node_fields`). Per Swift:
signature (one-line header, truncated at `SEAM_MAX_SIGNATURE_LEN`); visibility/is_exported from
access-level modifiers (`public`/`open` → exported & public; `private`/`fileprivate` → private;
`internal` default → internal/not-exported is the convention — treat internal as public visibility,
is_exported True only for public/open); decorators = Swift `@attribute` nodes verbatim, else `[]`.

### Phase 5 import resolution — `imports_ext.py`
Add `_extract_swift` (import-mapping extraction — required) and `_resolve_swift` (best-effort:
`import ModuleName` → `[]` since Swift modules are not file-path-resolvable in-repo without a build
graph; degrade to name-count). Add dispatch branches in both `extract_import_mappings` and `resolve_import_source`.

### Phase 5 builtins — `builtins.py`
Add a conservative `_SWIFT_BUILTINS` frozenset (common stdlib: print, String, Int, Double, Bool,
Array, Dictionary, Set, Optional, Character, nil, true, false, Result, Error, etc.) and a `"swift"`
entry in `_LANGUAGE_BUILTINS`.

### No schema change, no migration, no new MCP tool
Swift reuses all existing tables, FTS, clustering, sync, and watcher paths. Tool count stays 10.

### ADR
Add `docs/adr/009-swift-support.md` recording Swift added + the per-grammar evidence and the
**explicit Kotlin deferral** (tree-sitter-kotlin 1.1.0 grammar too flaky — ERROR nodes on common
constructs, ~1/6 symbols recovered; revisit when a robust grammar ships).

## Testing Decisions

- **What makes a good test:** assert external behavior through the public API
  (`extract_symbols`/`extract_edges`/`extract_comments`, `extract_node_fields`,
  `extract_import_mappings`, `is_builtin`) — given a small Swift fixture, the extractors return the
  expected symbols (name, kind, line range), edges (≥1 import + ≥1 call), docstrings, semantic
  comments, and enrichment. Never assert against internals.
- **Fixture:** add `tests/fixtures/sample.swift` — a small, real, parseable file with: a documented
  function, a class with a method (+ a bare-identifier internal call), a struct, an enum, a protocol,
  an extension, an `import`, an `@attribute`, and WHY/HACK/NOTE markers.
- **Modules tested (mirror tests/unit/test_go_rust.py):** Swift symbols (class/struct/actor/extension→class,
  enum→type, protocol→interface, method→Type.method, top-level func→function; doc-comment→docstring);
  Swift edges (import + bare call); Swift comments (each marker; plain ignored); parser (`parse_swift`
  returns a node for valid source, None for binary); pipeline (`_dispatch_parser` routes `.swift`;
  `index_one_file` indexes the fixture); signatures (signature + visibility + @attribute decorators);
  imports (binding extracted); builtins (`is_builtin('print','swift')` True, repo name False).
- **TDD:** write the failing Swift-symbols test (struct→class or class→class) first, then build out.
- **Regression:** the full existing suite (1395 tests) must stay green — Swift is purely additive.

## Out of Scope

- Kotlin (deferred — grammar maturity; documented in the ADR).
- Member/navigation call precision (`obj.m()`, `self.m()`) — bare-identifier MVP only.
- Swift module/framework import resolution to files (no in-repo build graph) — extraction records the binding, resolution returns `[]`.
- `property_declaration`, `enum_entry`/cases, computed properties, subscripts, operators, generics-parameter modeling beyond literal signature text.
- SwiftUI result-builder / property-wrapper semantics beyond plain node surfacing.
- New MCP tools, config knobs (reuses `SEAM_MAX_SIGNATURE_LEN`, `SEAM_IMPORT_RESOLUTION`, `SEAM_BUILTIN_FILTERING`, `SEAM_LANGUAGE_MAP`), schema changes, or migrations.

## Further Notes

- **Confidence / clustering / FTS / sync / watcher come for free** — all language-agnostic.
- **Honesty:** the bare-identifier call heuristic is intentionally simple; cross-file confidence flags the uncertainty.
- **Acceptance:** `.swift` indexed; symbols/edges/docstrings/semantic-comments/enrichment extracted with the documented kind mapping; all ten tools work on Swift; ADR added (Swift + Kotlin-deferral); no module > 1000 lines; `make gate` green; full suite green.
