# PRD — Phase 9: Language Expansion (Java, C#, Ruby, C, C++, PHP)

> Closes the last *measured* competitive weakness from `docs/competitive-benchmark.md` that
> isn't distribution: language coverage. Seam jumps from 5 → 11 languages, putting it in the
> same breadth class as CodeGraph (20+) and graphify (33) for the mainstream enterprise/web/
> systems stack. Status: ready-for-agent.

## Problem Statement

As a developer or AI agent working in a Java, C#, Ruby, C, C++, or PHP codebase, I get **zero
value** from Seam — `seam init` silently skips those files because they aren't in
`SEAM_LANGUAGE_MAP`. Seam currently indexes only Python, TypeScript, JavaScript, Go, and Rust.
The competitive benchmark (`docs/competitive-benchmark.md`) identified language coverage as
Seam's narrowest dimension: an agent navigating a Spring service, a .NET API, a Rails app, a
C/C++ systems project, or a PHP/Laravel backend cannot use Seam at all, while CodeGraph and
graphify cover it by default.

## Solution

As a Java/C#/Ruby/C/C++/PHP developer, I want `seam init` to index my source files —
extracting symbols (functions, classes, methods, interfaces, types), import edges, call edges,
doc-comments, and semantic comments — so that **all ten MCP tools** (`seam_query`,
`seam_context`, `seam_search`, `seam_impact`, `seam_trace`, `seam_changes`, `seam_why`,
`seam_clusters`, `seam_affected`, `seam_context_pack`) work on my codebase exactly as they do
for Python and TypeScript, with the same EXTRACTED/AMBIGUOUS/INFERRED confidence model and the
same Phase 4 enrichment fields (signature, decorators, is_exported, visibility, qualified_name).

## User Stories

1. As a Java developer, I want `.java` files indexed, so my Java code is searchable and analyzable.
2. As a C# developer, I want `.cs` files indexed, so my .NET code is searchable.
3. As a Ruby developer, I want `.rb` files indexed, so my Rails/Ruby code is searchable.
4. As a C developer, I want `.c`/`.h` files indexed, so my C code is searchable.
5. As a C++ developer, I want `.cpp`/`.cc`/`.cxx`/`.hpp`/`.hh`/`.hxx` files indexed, so my C++ code is searchable.
6. As a PHP developer, I want `.php` files indexed, so my PHP/Laravel code is searchable.
7. As an agent, I want each language's symbols mapped onto the **existing closed kind vocabulary** (function/class/method/interface/type) so impact/context behave uniformly and the kind enum stays closed.
8. As a Java/C#/PHP developer, I want a method `Class.method()` indexed as `Class.method` (kind=method) so it reads like Python/TS/Go/Rust qualified methods.
9. As an agent, I want `import`/`using`/`require`/`#include`/`use` statements extracted as import edges, so cross-file dependency reasoning works.
10. As an agent, I want bare-identifier call expressions extracted as call edges (same MVP heuristic as the existing 5 languages — precision is not the goal), so `seam_impact`/`seam_trace` have a call graph.
11. As an agent, I want all six languages' edges to flow through the **existing** whole-index confidence resolution, so EXTRACTED/AMBIGUOUS/INFERRED works automatically with no per-language confidence code.
12. As an agent, I want doc-comments captured as docstrings: Java/PHP `/** */`, C# `///` XML doc, C/C++ leading `/** */` or `//` block, Ruby leading `#` block — so `seam_search`/`seam_context` surface documented intent.
13. As an agent, I want semantic comments (WHY/HACK/NOTE/TODO/FIXME) extracted from every language's comment syntax, so `seam_why` works for all six.
14. As an agent, I want the Phase 4 enrichment fields populated per language (signature always; visibility/is_exported where the language has the concept; decorators only where the language has decorator/annotation syntax — Java annotations, C# attributes, PHP attributes; `[]` otherwise), so `seam_context`/`seam_context_pack` are as rich as for Python.
15. As an agent, I want import-mapping extraction per language so Phase 5 confidence promotion *can* run; file-path resolution is best-effort (returns `[]` when it can't resolve, degrading cleanly to the name-count rule).
16. As an agent, I want a conservative builtin/stdlib vocabulary per language (Java `System`/`String`/…, C# `Console`/…, Ruby `puts`/`require`/…, C `printf`/`malloc`/…, C++ `std`/`cout`/…, PHP `echo`/`count`/…) so count==0 builtin names aren't mislabeled as unresolved repo symbols — guarded by the existing count==0 rule in confidence.py.
17. As a maintainer, I want all six extensions added to `SEAM_LANGUAGE_MAP` in `seam/config.py`, so language detection stays config-driven (no hardcoding elsewhere).
18. As a maintainer, I want the six `tree-sitter-*` grammar packages added as pinned dependencies, so the build is reproducible.
19. As a maintainer, I want every new parser/extractor to **never raise** (binary/oversize/parse-error/malformed → None / [] / safe defaults), consistent with the existing parser and extractor contracts.
20. As a maintainer, I want files with syntax errors to still index whatever parsed (partial tree), so one bad file doesn't blank the index.
21. As a maintainer, I want no new module to exceed 1000 lines and no function to exceed 200 lines, consistent with the project's hard limits.
22. As a maintainer, I want a new ADR recording the language expansion and the per-language MVP scope decisions, so the decision record stays accurate.
23. As a maintainer, I want the cluster/FTS/sync/watcher paths to work unchanged for the new languages (they are language-agnostic), so no Phase 2/3/7 code needs touching.

## Implementation Decisions

### Dependencies
Add and pin (verified to resolve and parse against the pinned tree-sitter 0.25.2 core):
`tree-sitter-java` (0.23.5), `tree-sitter-c-sharp` (0.23.5), `tree-sitter-ruby` (0.23.1),
`tree-sitter-c` (0.24.2), `tree-sitter-cpp` (0.23.4), `tree-sitter-php` (0.24.1). Pin with `>=`
consistent with the existing grammar deps. Add via `uv add` so `pyproject.toml` + `uv.lock` update.

### config.py — language map (single source of truth)
Add: `.java`→java; `.cs`→csharp; `.rb`→ruby; `.c`→c, `.h`→c (the common case; `.h` ambiguity
between C and C++ is resolved to C — a deliberate MVP call, noted in the ADR); `.cpp`/`.cc`/
`.cxx`/`.c++`→cpp, `.hpp`/`.hh`/`.hxx`→cpp; `.php`→php.

### parser.py
Build six `Language` singletons at module load (mirroring `_GO_LANG`); add `parse_java`,
`parse_csharp`, `parse_ruby`, `parse_c`, `parse_cpp`, `parse_php`, each delegating to the
existing `_parse(path, language)` helper (all guards reused unchanged). **PHP grammar entry is
`tree_sitter_php.language_php()`** (the variant that handles the `<?php` open tag), not
`language()`.

### pipeline.py — `_dispatch_parser`
Add the six branches routing each language string to its `parse_*`.

### graph.py — extraction dispatch
Route the new languages in the public `extract_symbols` / `extract_edges` / `extract_comments`
dispatchers to per-family extractor modules (below). Reuse `_text`, `_make_symbol`, the
`Symbol`/`Edge`/`Comment` TypedDicts, `_match_marker`/`_MARKER_RE`, `_block_comment_lines`, and
the INFERRED-default edge confidence (whole-index resolution at read time handles the rest).

### New per-family extractor modules (mirrors `graph_go_rust.py`; respects 1000-line limit)
Group two languages per file (same as Go+Rust), each importing **only** `graph_common`
(leaf) + `signatures` (leaf) + `seam.config`:
- `seam/indexer/graph_java_csharp.py` — Java + C# (`_extract_symbols_java`, `_extract_edges_java`, `_extract_comments_java`, and the C# trio).
- `seam/indexer/graph_c_cpp.py` — C + C++.
- `seam/indexer/graph_ruby_php.py` — Ruby + PHP.

If any module approaches 1000 lines, split it (e.g. one language per file) — the 1000-line
limit is non-negotiable; the family grouping is a convenience, not a requirement.

### Per-language kind mapping (existing 5 kinds only)
- **Java:** `class_declaration`→class; `interface_declaration`→interface; `enum_declaration`→type; `record_declaration`→class; `method_declaration`/`constructor_declaration` inside a class → method (`Class.method`). No top-level functions.
- **C#:** (`namespace_declaration` is traversed, not emitted) `class_declaration`/`struct_declaration`/`record_declaration`→class; `interface_declaration`→interface; `enum_declaration`/`delegate_declaration`→type; `method_declaration`/`constructor_declaration`→method (`Class.method`).
- **Ruby:** `class`→class; `module`→class (a named container — closest fit in the closed vocabulary; noted in ADR); `method` (`def`)→method when inside a class/module else function; `singleton_method` (`def self.x`)→method (`Class.x`).
- **C:** `function_definition`→function; `struct_specifier`(named)/`union_specifier`(named)→class; `enum_specifier`(named)→type; `type_definition` (typedef)→type. No methods.
- **C++:** (`namespace_definition` traversed, not emitted) `class_specifier`/`struct_specifier`/`union_specifier`→class; `enum_specifier`→type; `function_definition`→function, or method (`Class.method`) when the declarator is a `qualified_identifier` (`Class::m`) or the definition is inside a class body. (C++ `::` qualified names are normalized to `Class.method` for cross-language uniformity.)
- **PHP:** `class_declaration`→class; `interface_declaration`→interface; `trait_declaration`→interface; `enum_declaration`→type; `function_definition`→function; `method_declaration`→method (`Class.method`).

### Edges (same bare-identifier MVP as the existing 5 languages)
- **Imports:** Java `import_declaration` (last segment of the qualified name); C# `using_directive` (last namespace segment); Ruby `require`/`require_relative` (the string-literal arg's basename); C/C++ `preproc_include` (the header filename stem); PHP `namespace_use_declaration` (last segment). `source = file stem`, `kind="import"`.
- **Calls:** the language's call node (`method_invocation` Java; `invocation_expression` C#; `call` Ruby; `call_expression` C/C++; `function_call_expression`/`member_call_expression` PHP) **with a bare identifier callee** → `kind="call"`, `source` = nearest enclosing named function/method (via the extended `_find_enclosing_function`). Selector/member calls (`obj.m()`, `pkg::f()`, `$this->m()`) are **not** tracked in this MVP — same bare-identifier-only rule as Python/TS/Go/Rust.

### `graph_common._find_enclosing_function`
Extend with the new languages' function/method node types and class-context types for
`Class.method` qualification. Keep the leaf property (no imports from the family modules).

### Phase 4 enrichment — `signatures.py` dispatch + `signatures_ext.py`
`extract_node_fields(node, language, …)` gains six dispatch branches routing to a new leaf
module `seam/indexer/signatures_ext.py` (imports only stdlib + tree_sitter; mirrors
`signatures.py`'s contract — **never raises**, returns `_safe_defaults` on failure). Per
language: `signature` (one-line header, truncated at `SEAM_MAX_SIGNATURE_LEN`) always;
`visibility`/`is_exported` from access modifiers (Java/C#/PHP public/private/protected[/internal];
C static→private/file-local; C++ class access specifiers; Ruby → public default / None where
undecidable); `decorators` = Java annotations (`@Override`…), C# attributes (`[Serializable]`),
PHP attributes (`#[...]`) verbatim, `[]` for C/C++/Ruby; `qualified_name` passed through from the
caller's scope-walker. If `signatures_ext.py` approaches 1000 lines, split it by family.

### Phase 5 import resolution — `imports.py` dispatch + `imports_ext.py`
`extract_import_mappings` and `resolve_import_source` gain six branches routing to a new leaf
module `seam/analysis/imports_ext.py` (leaf contract — never raises). Import-mapping
**extraction** is required for all six (so promotion *can* run). File-path **resolution** is
best-effort and may return `[]` (third-party / module-system paths out of scope), degrading
cleanly to the name-count rule:
- Java/C#: package/namespace→directory resolution is **out of scope** (returns `[]`); extraction still records the binding.
- Ruby: `require_relative './x'`→relative `.rb` resolution; `require 'x'`→`[]`.
- C/C++: `#include "x.h"`→relative-path resolution; `#include <x.h>`→`[]` (system).
- PHP: `use App\Foo`→`[]` (PSR-4 autoload mapping out of scope); extraction records the binding.

If `imports_ext.py` exceeds 1000 lines, split by family.

### Phase 5 builtins — `builtins.py` registry
Add six conservative frozensets (common builtins/globals/stdlib only — an over-broad set risks
shadowing real repo symbols; the count==0 guard in confidence.py is the safety net) and six
registry entries in `_LANGUAGE_BUILTINS`. Kept in `builtins.py` (stays under 1000 lines).

### No schema change, no migration
New languages reuse the existing tables, FTS, clustering, sync, and watcher paths verbatim
(all language-agnostic). No DB/MCP changes; tool count stays 10.

### ADR
Add `docs/adr/ADR-00X-language-expansion.md` recording the six languages, the MVP scope (bare-
identifier calls, `.h`→C, Ruby module→class, C++ `::`→`.`, resolution-out-of-scope cases), and
the per-family module split rationale (1000-line limit).

## Testing Decisions

- **What makes a good test:** assert **external behavior** through the public API
  (`extract_symbols`/`extract_edges`/`extract_comments`, `extract_node_fields`,
  `extract_import_mappings`, `is_builtin`) — given a small per-language fixture, the extractors
  return the expected symbols (name, kind, line range), edges (≥1 import + ≥1 call), docstrings,
  semantic comments, and enrichment fields. Never assert against internals.
- **Fixtures:** add `tests/fixtures/sample.java`, `sample.cs`, `sample.rb`, `sample.c`,
  `sample.cpp`, `sample.php` — each a small, **real, parseable** file containing: a documented
  function/method, a class (or struct) with a method, an interface/trait (where the language has
  one), an import/include/use/require, an internal bare-identifier call, and WHY/HACK/NOTE markers.
- **Modules tested (one behavioral slice per group, mirroring `test_go_rust.py`):** per language —
  symbols (each kind + docstring), edges (import + call), comments (each marker; plain ignored),
  parser (`parse_*` returns a node for valid source, None for binary), pipeline
  (`_dispatch_parser` routes the extension; `index_one_file` actually indexes the fixture),
  signatures (signature present; visibility/decorators where applicable), imports (binding
  extracted), builtins (`is_builtin` true for a known builtin, false for a repo name).
- **Prior art:** `tests/unit/test_go_rust.py`, `test_parser.py`, `test_signatures.py`,
  `test_imports.py`, `test_builtins.py`, `test_richer_edges.py`, `test_seam_why.py`, and the
  existing `sample.go`/`sample.rs` fixtures.
- **TDD:** for each language, write the failing symbols test (first kind) before implementing.
- **Regression:** the full existing suite (1107 tests) must stay green — the new languages are
  purely additive; existing-language behavior is byte-stable.

## Out of Scope

- Selector/member-call edge precision (`obj.m()`, `pkg::f()`, `$this->m()`) — bare-identifier MVP only.
- Cross-package/namespace/module resolution beyond same-repo-relative file probing (Java packages, C# namespaces, PHP PSR-4 autoload, Go-style module prefixes, system `#include <>`).
- C/C++ preprocessor macro expansion, conditional compilation (`#ifdef`), and macro-defined symbols beyond what tree-sitter surfaces as plain nodes.
- C++ templates / template specializations beyond plain node surfacing; operator overloads.
- Ruby metaprogramming (`define_method`, `method_missing`), dynamic `private`/`public` visibility tracking.
- Java/C# generics type-parameter modeling beyond the literal signature text.
- New MCP tools, new config knobs (reuses `SEAM_MAX_SIGNATURE_LEN`, `SEAM_IMPORT_RESOLUTION`, `SEAM_BUILTIN_FILTERING`, `SEAM_LANGUAGE_MAP`), schema changes, or migrations.
- `.h`→C++ disambiguation (heuristic content sniffing) — `.h` maps to C in this MVP.

## Further Notes

- **Confidence comes for free:** edges store string names and confidence resolves whole-index at
  read time, so all six languages get EXTRACTED/AMBIGUOUS/INFERRED with no new confidence code.
- **Clustering/FTS/sync/watcher come for free:** all are language-agnostic and operate on the
  symbols/edges tables, so Louvain clusters, FTS search, `seam sync`, and the live watcher work
  for the new languages immediately after `seam init`.
- **Honesty:** the bare-identifier call heuristic is intentionally simple (precision is not the
  goal); cross-file confidence flags the uncertainty. C++ is the hardest grammar (out-of-line
  methods, namespaces, templates) — the MVP captures the common cases and surfaces the rest as
  best-effort.
- **Acceptance:** all six extensions indexed; symbols/edges/docstrings/semantic-comments/
  enrichment extracted with the documented kind mapping; all ten tools work on all six languages;
  no module > 1000 lines, no function > 200 lines; ADR added; `make gate` green; full suite green.
