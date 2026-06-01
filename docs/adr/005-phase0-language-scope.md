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
