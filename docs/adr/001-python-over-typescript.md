# ADR-001: Python over TypeScript

## Status
Accepted — 2026-06-01

## Context
Seam needs a primary implementation language. Two candidates were seriously evaluated:

- **TypeScript** — The npm `seam` package name is available; tree-sitter npm bindings are described as "more mature" in community discussions; several adjacent code-intelligence tools are TypeScript.
- **Python** — Aligns with the primary author's stack (Bach, Koda, Skillia are all Python); `uvx seam` distribution is simple; Python MCP SDK is first-class (maintained by Anthropic); `watchdog` is a battle-tested file watcher.

## Decision
**Python.**

Specific reasons:
1. The `seam` npm name is permanently blocked by seamapi (Seam smart home API, v1.199.0, 284 versions). A Python `seam` name on PyPI is available.
2. Personal stack alignment means the author can maintain this without context-switching.
3. Python's tree-sitter bindings (the official `tree-sitter` PyPI package) are the primary SDK — they are not secondary to the npm package; the npm package's "maturity" advantage is marginal.
4. Anthropic's Python MCP SDK is first-class and actively maintained.
5. `uvx seam` gives zero-install CLI distribution equivalent to `npx`.

## Alternatives Rejected
- **TypeScript:** npm name collision is fatal for distribution. TypeScript tree-sitter advantage is marginal. Would require context-switching from primary stack.

## Consequences
- Distribution: `pip install seam` or `uvx seam`
- Package manager: uv (consistent with modern Python tooling)
- Tree-sitter: `tree-sitter` + `tree-sitter-python` + `tree-sitter-typescript` from PyPI
- If an npm package is ever needed (CLI wrapper, IDE extension): use `@catafal/seam`
