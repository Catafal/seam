# Architecture — Seam

> System overview for Phase 0. See ADRs in `docs/adr/` for decision rationale.

---

## System Diagram

```
Source files (Python, TypeScript)
        │
        ▼ tree-sitter (structural parsing)
        │
   ┌────▼──────────────────────────────────┐
   │           Indexer Pipeline            │
   │  parser.py → graph.py → db.py        │
   │  (language-specific AST → symbols    │
   │   + edges → SQLite upsert)           │
   └────────────────┬──────────────────────┘
                    │
                    ▼
          .seam/seam.db (SQLite + FTS5)
                    │
          ┌─────────┴──────────┐
          │                    │
          ▼                    ▼
   OS File Watcher       MCP Server (stdio)
   (watchdog daemon)     │
   Debounce + re-index   ├── seam_query(concept)   → query.engine.query()
                         ├── seam_context(symbol)  → query.engine.context()
                         └── seam_search(text)     → query.engine.search()
                                  │
                                  ▼
                         AI Agent (Claude Code, Cursor, Codex)
```

---

## Component Responsibilities

### Indexer Pipeline
**Files:** `seam/indexer/parser.py`, `seam/indexer/graph.py`, `seam/indexer/db.py`

The write path. Triggered by `seam init` (full) or the file watcher (incremental).

1. **parser.py** — Parses source files with tree-sitter. Returns tree-sitter Nodes. Language-specific but implementation-agnostic.
2. **graph.py** — Extracts structured data from AST Nodes. Produces `Symbol` and `Edge` dicts. Pure functions.
3. **db.py** — Writes to SQLite in an atomic transaction. Handles schema init, upsert, and delete.

### SQLite Database
**File:** `.seam/seam.db` (per project)

Three main tables + FTS5 virtual table:
- `files` — indexed files with hash + mtime
- `symbols` — functions, classes, methods
- `edges` — directed relationships (import, call)
- `symbols_fts` — FTS5 virtual table mirroring `symbols.name + docstring`

See `docs/database/schema.sql` for full DDL.

### File Watcher
**File:** `seam/watcher/daemon.py`

Runs as a background thread/process alongside the MCP server. Uses watchdog's `Observer` + a custom `FileSystemEventHandler`. Debounces rapid saves to avoid thrashing (default 500ms). On trigger: re-parses the changed file, diffs symbols, updates DB.

### MCP Server
**Files:** `seam/server/mcp.py`, `seam/server/tools.py`

Stdio transport (no HTTP, no ports). The Python MCP SDK handles protocol framing. Three tools exposed (Phase 0). Tool handlers in `tools.py` validate inputs and delegate to `query/engine.py`.

### Query Engine
**File:** `seam/query/engine.py`

The read path. Three query types:
- **FTS5 search** — BM25-ranked full-text search across symbol names + docstrings
- **Concept query** — FTS5 match + 1-hop graph expansion (connected symbols)
- **Context** — Direct lookup by symbol name + join to get callers/callees

---

## Data Flow: Write Path (seam init)

```
1. CLI: walk directory tree, collect .py + .ts + .js files
2. For each file:
   a. parser.parse_python(path) → tree-sitter Node
   b. graph.extract_symbols(node, "python", path) → [Symbol]
   c. graph.extract_edges(node, "python", path) → [Edge]
   d. db.upsert_file(conn, path, symbols, edges)
3. FTS5 index updated automatically via SQLite triggers
4. seam.db committed, watcher starts
```

## Data Flow: Read Path (MCP tool call)

```
1. Agent: seam_query("rate limiting")
2. server/tools.py: validate input
3. query/engine.query(conn, "rate limiting", limit=10)
   a. FTS5 MATCH against symbols_fts
   b. Collect matching symbol IDs
   c. Expand: also return symbols that import/call any matched symbol
   d. Rank by BM25 score + graph proximity
4. Return: [{symbol, file, line, score, callers_count, callees_count}]
```

---

## Phase 1 Extensions (not in Phase 0)

- **Execution flows** — multi-hop path tracing from entry points (heuristic, not LLM)
- **Semantic comment nodes** — `# WHY:`, `# HACK:`, `# NOTE:` as queryable entities
- **Impact analysis** — `seam_impact(symbol)` returns blast radius
- **Go + Rust parsers** — additional tree-sitter grammars

---

## Constraints

- **No external services** — zero network calls at runtime
- **No process per project** — MCP server launched with `cwd` = project root; single binary
- **SQLite file size** — target <50MB for a 100k LOC codebase
- **Startup time** — `seam start` must be ready in <500ms after first `seam init`
