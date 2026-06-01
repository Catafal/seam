# ADR-002: SQLite over Graph Database

## Status
Accepted — 2026-06-01

## Context
Seam stores a code graph: symbols (nodes) and edges (relationships). Options considered:

- **Neo4j** — Native graph DB; powerful Cypher query language; used by GitNexus.
- **NetworkX** — In-memory Python graph library; no persistence.
- **SQLite with adjacency list** — Relational tables for nodes + edges; FTS5 built-in; zero external dependencies.

## Decision
**SQLite with adjacency list + FTS5.**

Specific reasons:
1. **Zero dependencies** — SQLite is built into Python's stdlib. No external process, no Docker, no install.
2. **FTS5 built-in** — Full-text search is a core requirement; FTS5 ships with modern SQLite. No additional index.
3. **WAL mode** — Write-ahead logging allows concurrent reads (file watcher writing, MCP server reading) without blocking.
4. **Graph traversal is 1-2 hops for Phase 0** — Simple JOIN queries on the `edges` table handle callers/callees. Cypher's expressiveness is not needed for Phase 0's query patterns.
5. **Single file** — `.seam/seam.db` is one file. Easy to delete, backup, or inspect with any SQLite browser.

## Alternatives Rejected
- **Neo4j:** Requires a running process; external dependency; overkill for 1-2 hop traversal.
- **NetworkX:** In-memory only; not suitable for large codebases; no FTS5.
- **DuckDB:** Columnar, better for analytics; less suited for the adjacency-list + FTS5 pattern.

## Consequences
- Graph traversal queries use SQL JOINs on `edges` table (acceptable for Phase 0 depth).
- Phase 1 execution flows (multi-hop paths) may require recursive CTEs in SQLite — these are supported in SQLite 3.8.3+.
- If Phase 2 introduces complex graph analytics (Leiden clustering), may add NetworkX as an optional in-memory layer on top of SQLite (not a replacement).
- All graph queries are in `seam/query/engine.py` — isolated, easy to swap later.
