# Product Requirements Document — Seam (Phase 0)

> Derived from DISCOVERY.md | Version: 0.1 | Date: 2026-06-01

---

## 1. Problem Statement

AI coding agents (Claude Code, Cursor, Codex) explore codebases by grepping and reading files on every session. Published benchmarks (CodeGraph, 2024) show this costs **57% more tokens** and **62% more tool calls** than necessary.

Seam eliminates this by indexing the codebase once and exposing a query API via MCP.

---

## 2. Target User

**Primary:** Jordi (personal daily use on Bach, Koda, Skillia)
**Secondary:** Developers using AI coding agents who discover the open-source release

**User profile:**
- Runs Claude Code, Cursor, or similar AI coding agent daily
- Works on Python or TypeScript codebases
- Comfortable running CLI tools, editing MCP config
- Not willing to pay for cloud services or manage external infrastructure

---

## 3. Phase 0 Scope

### 3.1 CLI Commands

| Command | Description | Required |
|---|---|---|
| `seam init` | Index the current directory into `.seam/seam.db` | Phase 0 |
| `seam start` | Start MCP server + file watcher | Phase 0 |
| `seam status` | Show index stats (files, symbols, last update) | Phase 0 |

### 3.2 MCP Tools (Phase 0)

| Tool | Input | Output | Description |
|---|---|---|---|
| `seam_query` | `concept: str` | List of symbols + file locations | Find all code related to a concept using hybrid search |
| `seam_context` | `symbol: str` | Callers, callees, file, line, docstring | 360° view of a symbol's connections |
| `seam_search` | `text: str, limit?: int` | FTS5 results with snippets | Full-text search across all indexed symbols |

### 3.3 File Watcher

- Watches all indexed files for changes
- Debounced re-index (500ms default, configurable)
- Re-indexes only changed files (incremental where possible)
- Runs as background process alongside MCP server

### 3.4 Parsers (Phase 0)

- **Python** — functions, classes, methods, imports, docstrings
- **TypeScript/JavaScript** — functions, classes, interfaces, imports, JSDoc

### 3.5 Storage

- Single SQLite file at `.seam/seam.db` per project
- FTS5 extension for full-text search
- No external database, no migration framework (raw SQL)

---

## 4. Non-Requirements (Phase 0)

- No execution flow tracing (Phase 1)
- No semantic comment nodes (Phase 1)
- No blast radius / impact analysis (Phase 1)
- No pre-commit detect_changes (Phase 1)
- No Go / Rust parsers (Phase 1)
- No LLM integration (Phase 2, optional)
- No web UI
- No cloud sync

---

## 5. Acceptance Criteria (Phase 0 Complete)

- [ ] `seam init` indexes a Python codebase with no errors
- [ ] `seam init` indexes a TypeScript codebase with no errors
- [ ] `seam_query("authentication")` returns relevant symbols from an indexed codebase
- [ ] `seam_context("UserService")` returns callers and callees
- [ ] `seam_search("validate")` returns FTS5 snippet results
- [ ] File watcher updates index within 1s of a file save
- [ ] Benchmark: ≥30% token reduction on at least one real project vs baseline (no Seam)
- [ ] Gate command passes: `make gate`
- [ ] `make gate` passes in CI (macOS + Linux)

---

## 6. Success Metrics

| Metric | Target |
|---|---|
| Token reduction (benchmark) | ≥30% on first real project |
| Index time (10k file repo) | <60 seconds |
| Query latency (`seam_query`) | <200ms p99 |
| Watcher lag | <1s after file save |
| Index freshness after `seam init` | 0 stale files |
