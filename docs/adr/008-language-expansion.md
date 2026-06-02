# ADR-008: Phase 9 Language Expansion (5 → 11 Languages)

## Status
Accepted — 2026-06-02

## Context

ADR-005 established Seam's initial language scope (Python + TypeScript/JavaScript for Phase 0, Go + Rust in Phase 1) and stated a target of 20+ languages by v1.0. A competitive benchmark run after Phase 8 confirmed that language coverage was Seam's narrowest axis relative to alternatives: the 5-language set excluded the majority of enterprise and embedded/systems codebases.

The six languages selected for Phase 9 collectively cover the dominant enterprise, web backend, and systems domains that Python/Go/Rust do not:

- **Java** — largest enterprise/Android codebase; tree-sitter grammar mature and widely used
- **C#** — dominant .NET/Unity ecosystem; second-largest enterprise footprint after Java
- **Ruby** — Rails-heavy web backend; active in AI-assisted coding tooling
- **C** — foundational systems/embedded; no ORM or class hierarchy to model
- **C++** — games, finance, automotive; shares grammar ancestry with C (both via tree-sitter-c family)
- **PHP** — largest web-server footprint globally (WordPress, Laravel, Symfony)

## Decision

**Add Java, C#, Ruby, C, C++, and PHP to the indexer. Language count grows from 5 to 11.**

No schema change, no migration, no new MCP tools (tool count stays 10). All new languages are parsed at index time and surface through the existing 10 MCP tools with the same field contract (symbol, kind, signature, decorators, is_exported, visibility, qualified_name).

### Module Split Strategy

Each language family gets its own extractor module (a leaf that imports only from `graph_common`), following the precedent set by `graph_go_rust.py` when Go + Rust were split from `graph.py` to respect the 1000-line-per-file limit:

| Module | Languages | Why paired |
|--------|-----------|-----------|
| `seam/indexer/graph_java_csharp.py` | Java, C# | Both JVM-lineage OOP languages; share annotation/attribute enrichment patterns and method-qualification logic |
| `seam/indexer/graph_c_cpp.py` | C, C++ | C++ is a strict superset of C at the grammar level; both use `preproc_include` for imports and `comment` nodes for both `//` and `/* */` styles |
| `seam/indexer/graph_ruby.py` | Ruby | Standalone — Ruby's dynamic `def self.x` singleton-method pattern and constant-child class/module AST structure have no natural pair among the new six |
| `seam/indexer/graph_php.py` | PHP | Standalone — PHP reached 1000 lines in an earlier draft that combined it with Ruby; split enforces the per-file limit |

`graph.py` imports each new module's public extractors at the top level and routes language strings through the existing `extract_symbols` / `extract_edges` / `extract_comments` dispatchers with no change to the dispatch contract.

### New Leaf Modules (Enrichment + Import Resolution)

**`seam/indexer/signatures_ext.py`** — Phase 4 enrichment (`extract_node_fields` equivalent) for the 6 new languages. Deliberately a separate leaf rather than extending `signatures.py` because:

1. `signatures.py` imports only `tree_sitter`; adding 6 new language dispatchers would push it past 1000 lines.
2. Both `graph_ruby.py` and `graph_php.py` need enrichment but must not import from `graph.py` (import cycle). A separate leaf breaks the potential cycle cleanly.
3. The `NodeFields` TypedDict is re-declared in `signatures_ext.py` rather than imported from `signatures.py` to satisfy the all-imports-at-top + leaf-purity rules. A drift test (`tests/unit/test_signatures_ext_drift.py`) asserts that both TypedDicts have identical keys, so the re-declaration can never silently diverge.

**`seam/analysis/imports_ext.py`** — import-mapping extraction and best-effort resolution for the 6 new languages. Extends the Phase 5 import-resolution pipeline. Same leaf-purity rationale: `imports.py` would exceed 1000 lines with 6 more language branches, and the `_ImportMapping` TypedDict is re-declared with a companion drift test.

**`seam/analysis/builtins.py`** — extended in-place (did not require a new file) with 6 conservative per-language frozensets covering common builtins and standard-library names for Java, C#, Ruby, C, C++, and PHP.

### Kind Mapping (Closed Vocabulary)

All new languages map to the existing closed-vocabulary kind set: `function | class | method | interface | type`.

| Language | AST node → kind |
|----------|----------------|
| Java | `class_declaration` → class; `interface_declaration` → interface; `enum_declaration` → type; `record_declaration` → class; `method_declaration` / `constructor_declaration` (inside type) → method (`Class.method`) |
| C# | `class_declaration` / `struct_declaration` / `record_declaration` → class; `interface_declaration` → interface; `enum_declaration` / `delegate_declaration` → type; `method_declaration` / `constructor_declaration` (inside type) → method (`Class.method`); `namespace_declaration` traversed, not emitted |
| Ruby | `class` node → class; `module` → class; `def` inside class/module → method (`Class.method`); `singleton_method` (`def self.x`) → method (`Class.x`); top-level `def` → function |
| C | `function_definition` → function; `struct_specifier` / `union_specifier` → class; `enum_specifier` → type; `type_definition` → type; no methods (C has no class concept) |
| C++ | `class_specifier` / `struct_specifier` → class; `union_specifier` → class; `enum_specifier` → type; `function_definition` (free) → function; `function_definition` (in-class or out-of-line `Class::method`) → method (`Class.method`); `namespace_definition` traversed, not emitted; `template_declaration` unwrapped to inner declaration |
| PHP | `class_declaration` → class; `interface_declaration` → interface; `trait_declaration` → interface; `enum_declaration` → type; `function_definition` (top-level) → function; `method_declaration` (inside type) → method (`Class.method`) |

### Enrichment Rules

| Language | signature | decorators | is_exported | visibility |
|----------|-----------|-----------|------------|-----------|
| Java | Full declaration header | Java annotations (`@Service`, `@Override`, …) verbatim | `true` when `public` modifier present | From access modifier (`public`/`private`/`protected`) |
| C# | Full declaration header | C# attribute lists (`[Serializable]`, `[HttpGet]`, …) verbatim | `true` when `public` modifier present | From access modifier (`public`/`private`/`protected`/`internal`) |
| Ruby | `def name(params)` / `class Name` | `[]` (Ruby decorators are runtime patterns, not AST nodes) | `null` (dynamic — `module_function` etc. are not statically resolvable) | `null` (dynamic visibility via `private`/`protected` method calls) |
| C | Return type + declarator | `[]` | `false` when `static` storage-class (file-local); `true` otherwise | `"private"` when static; `null` otherwise (no access modifiers in C) |
| C++ | Return type + declarator | `[]` | `null` (MVP — no standard export mechanism) | `null` (MVP — in-class access specifiers not yet threaded through) |
| PHP | Full declaration header | PHP attribute lists (`#[Route(...)]`, `#[Pure]`, …) verbatim | `true` when `public` modifier present | From access modifier (`public`/`private`/`protected`) |

### Edge Strategy

All new languages extract two edge kinds:
- **import** — from the file-level import/use/include statement to the declared symbol name (last segment of qualified path).
- **call** — bare-identifier calls only (`function_call_expression` / `method_invocation` without an `object` field / `call_expression` where `function` is an `identifier`). Member/selector calls (`obj.method()`, `Class::static()`) are skipped in this MVP — they require type inference to resolve safely.

Edges carry `confidence='INFERRED'` at extraction time; whole-index resolution (Phase 5) promotes to EXTRACTED / AMBIGUOUS at read time, unchanged from prior languages.

## Consequences

### Positive

- Seam now indexes 11 languages, covering the majority of enterprise, web-backend, and systems codebases.
- The existing 10 MCP tools, the SQLite schema (v6), and all CLI commands work without modification.
- Phase 4/5 enrichment (signature, decorators, is_exported, visibility, qualified_name) surfaces for all 6 new languages where statically determinable.
- The 4-module file-split strategy keeps every file within the 1000-line limit and preserves the all-imports-at-top + leaf-purity invariants.
- 1395 tests (1107 at Phase 8 baseline), gate green.

### MVP Limitations (known, accepted)

**(a) `.h` always maps to C.** The `SEAM_LANGUAGE_MAP` routes `.h` to `"c"`, not `"cpp"`. A C++-only project that stores declarations in `.h` headers parses those headers with the C grammar. This works for pure-C header patterns (structs, typedefs, function prototypes) but misses C++-only constructs (templates, namespaces, class members in headers). **Workaround:** use `.hpp`, `.hh`, or `.hxx` for C++ headers — those extensions map to `"cpp"` correctly.

**(b) Nested classes get flat qualified names.** An inner class `Inner` inside `Outer` is indexed as `Inner` (not `Outer.Inner`), matching the existing behavior for Go/Rust and the homonym-collapse gotcha documented in ADR-007. The edge graph is keyed on symbol name, so `Outer.Inner` as a qualified_name would not match any edge target. This is a deliberate consistency choice, not an oversight.

**(c) C++ pure-virtual method declarations are not extracted.** A declaration `virtual void f() = 0;` parses as `field_declaration` in the tree-sitter C++ grammar, not as `function_definition`. Only `function_definition` nodes are extracted as method symbols. Abstract interface contracts defined via pure-virtual declarations are therefore absent from the index. Concrete overriding implementations (which parse as `function_definition`) are indexed normally.

**(d) C function-pointer typedefs are not extracted.** A typedef of the form `typedef int (*Cb)(int);` is not extracted as a `type` symbol. The current C typedef handler looks for the last `type_identifier` child of a `type_definition` node; a function-pointer declarator uses `abstract_function_declarator`, not `type_identifier`. These typedefs are silently skipped.

**(e) Java/C# package+namespace and PHP PSR-4 import resolution returns `[]`.** The `imports_ext.py` module extracts Java `import` declarations and C# `using` directives, recording the last segment of the qualified name (e.g. `List` from `import java.util.List`). However, `resolve_import_source()` for Java/C#/PHP returns `[]` because mapping `java.util` → a file path on disk requires knowledge of the classpath / NuGet package layout / Composer autoload map — information not available without external tooling. As a result, import-promotion (Phase 5 step A) does not fire for cross-package Java/C#/PHP calls; those edges fall back to the name-count rule (step 2). Import extraction (the mapping record) still occurs and is correct — only resolution is out of scope.

**(f) C/C++ system `#include <...>` resolution returns `[]`.** `#include <stdio.h>` produces an import edge with target `stdio` but `resolve_import_source()` returns `[]` for system headers (no file found in the repo). The edge degrades to the name-count rule at read time. Same for Go-style system includes documented in ADR-005's Phase 1b update.

## Update (Phase 9) — 2026-06-02

This ADR supersedes the language-scope section of ADR-005 for the 6 new languages. ADR-005 remains authoritative for the original 5 languages and the extension rationale it documents.
