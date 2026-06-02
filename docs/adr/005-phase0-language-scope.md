# ADR-005: Phase 0 Language Support (Python + TypeScript)

## Status
Accepted — 2026-06-01

## Context
Seam targets 20+ languages by v1.0. Phase 0 must pick a minimal set.

Languages considered for Phase 0:
- Python — author's primary language; easy to dogfood immediately
- TypeScript/JavaScript — most common in the target user base; CodeGraph data shows highest demand
- Go — high demand from CodeGraph/GitNexus users; tree-sitter grammar is excellent
- Rust — growing demand; tree-sitter grammar is mature

## Decision
**Python + TypeScript/JavaScript for Phase 0. Go + Rust added in Phase 1.**

JavaScript is included with TypeScript at no extra cost (same tree-sitter grammar family, `.js` detection is trivial).

## Alternatives Rejected
- **Python only:** Too narrow; TypeScript is the #1 language for AI-assisted coding in the target audience.
- **All four in Phase 0:** Doubles the scope of the parser layer. Each language requires its own extract_symbols logic and test fixtures.

## Consequences
- Phase 0 dogfooding on Bach (Python) and a TypeScript project (e.g. skills repo).
- Phase 1 adds `tree-sitter-go` and `tree-sitter-rust` grammar packages.
- Language detection: by file extension (`.py` → Python; `.ts`/`.tsx` → TypeScript; `.js`/`.mjs`/`.cjs` → JavaScript).
- No detection by shebang or content in Phase 0.

## Update (Phase 1b) — 2026-06-01

Go and Rust are now implemented. The Phase 1 consequence above has been fulfilled.

**What was added:**
- `tree-sitter-go==0.25.0` and `tree-sitter-rust==0.24.2` added as pinned deps.
- `seam/indexer/parser.py`: `parse_go()` and `parse_rust()` functions (delegating to the existing `_parse` helper).
- `seam/config.py`: `.go` → `go` and `.rs` → `rust` added to `SEAM_LANGUAGE_MAP`.
- `seam/indexer/pipeline.py`: `_dispatch_parser` routes `go` and `rust` language strings.
- `seam/indexer/graph_go_rust.py` (new): Go and Rust symbol, edge, and comment extractors, split from `graph.py` to keep that file under 1000 lines.
- `seam/indexer/graph.py`: public dispatchers (`extract_symbols`, `extract_edges`, `extract_comments`) extended to route `go` and `rust` to the new module; `_find_enclosing_function` extended with Go/Rust branches.

**Kind mapping implemented:**
- Go: `function_declaration` → function; `method_declaration` → method (`Recv.Name`, `*T` normalized); struct → class; interface → interface; type def/alias → type.
- Rust: `function_item` → function; impl method → method (`Type.fn`); struct → class; enum → type; trait → interface; mod traversed but not emitted.

**All tests pass** (`make gate` green). See `tests/unit/test_go_rust.py` for the behavioral test suite.

## Update (Phase 9) — 2026-06-02

Six more languages added (Java, C#, Ruby, C, C++, PHP), bringing the total to 11. See **ADR-008** for the full decision rationale, per-family module split strategy, kind mapping, enrichment rules, and MVP limitations.
