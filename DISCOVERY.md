# Discovery: Seam

> Generated: 2026-06-01 | Status: Approved

---

## What You Actually Want

A personal productivity multiplier for AI-assisted coding. You want AI agents to stop wasting tokens re-discovering codebase structure every session. You want to dogfood this on your own projects (Bach, Koda, Skillia) and eventually open-source it — not as a side project, but as a portfolio centerpiece with measurable, publishable benchmarks.

This is not primarily an open-source product. It is a tool you build for yourself, that happens to be worth sharing.

---

## The Delta (what you said vs what you mean)

**You said:** "A local code intelligence MCP server"

**You mean:** An always-on, zero-config background layer that eliminates the exploration tax AI agents pay every session — without you having to think about it. Seam should feel like a compiler: you run it once, it stays fresh automatically, and it silently makes every coding session cheaper.

**Why it matters:** The delta is the "zero-config" + "automatic" part. If developers have to manually re-index, Seam fails its own premise. Auto-sync is not a feature — it is the core value proposition.

---

## Success in 90 Days

- Seam is running on Bach, Koda, and Skillia (3 real projects)
- At least one session where `seam_query` returns the answer that would have cost 3+ file reads
- A benchmark report showing ≥30% token reduction on at least one project
- `seam init` works cleanly on a Python project and a TypeScript project

---

## The Simplest Path

**Phase 0 MVP:**
1. `seam init` — index the current repo into `.seam/seam.db`
2. Three MCP tools: `seam_query` (find code by concept), `seam_context` (360° symbol view), `seam_search` (FTS5 text search)
3. File watcher auto-sync (debounced, background)
4. Python + TypeScript parsers only
5. Publish token-reduction benchmark on one real project

This is enough to start using Seam daily and prove the value proposition.

---

## Explicitly NOT Building

- **No web UI** — pure CLI + MCP server, no dashboard
- **No cloud sync** — `.seam/seam.db` is local, per-project, period
- **No team features** — no sharing, no multi-user, no auth
- **No LLM integration in Phase 0** — execution flow tracing is heuristic (static graph traversal), not LLM-assisted
- **No community detection in Phase 0** — Leiden/Louvain clustering is Phase 2
- **No semantic comment nodes in Phase 0** — `# WHY:` parsing is Phase 1
- **No Go/Rust parsers in Phase 0** — Python + TypeScript only; Go + Rust added in Phase 1
- **No npm package** — Python first (`uvx seam` or `pip install seam`); npm is blocked by seamapi namespace collision
- **No external code-intelligence dependency** — Seam is built to be the local source of truth for code search, graph traversal, impact analysis, and pre-commit risk.
