# Architecture — Seam

> Phase 0 + Phase 1. See ADRs in `docs/adr/` for decision rationale.

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
                         ├── seam_search(text)     → query.engine.search()
                         │         [Phase 0]
                         │
                         ├── seam_impact(target)   → analysis.impact()
                         ├── seam_trace(src,tgt)   → analysis.flows.trace()
                         └── seam_changes(scope)   → analysis.changes.detect_changes()
                                   [Phase 1]
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
2. **graph.py** — Extracts structured data from AST Nodes. Produces `Symbol` and `Edge` dicts with confidence tags. Pure functions.
3. **db.py** — Writes to SQLite in an atomic transaction. Handles schema init, upsert, delete, and v1→v2 migration (adds `edges.confidence`).

### SQLite Database
**File:** `.seam/seam.db` (per project)

Three main tables + FTS5 virtual table:
- `files` — indexed files with hash + mtime
- `symbols` — functions, classes, methods
- `edges` — directed relationships (import, call) with `confidence` column (schema v2)
- `symbols_fts` — FTS5 virtual table mirroring `symbols.name + docstring`

See `docs/database/schema.sql` for full DDL.

### File Watcher
**File:** `seam/watcher/daemon.py`

Runs as a background thread/process alongside the MCP server. Uses watchdog's `Observer` + a custom `FileSystemEventHandler`. Debounces rapid saves to avoid thrashing (default 500ms). On trigger: re-parses the changed file, diffs symbols, updates DB.

### MCP Server
**Files:** `seam/server/mcp.py`, `seam/server/tools.py`

Stdio transport (no HTTP, no ports). The Python MCP SDK handles protocol framing. Six tools exposed (Phase 0 + Phase 1). Tool handlers in `tools.py` validate inputs and delegate to `query/engine.py` or `analysis/`.

### Query Engine
**File:** `seam/query/engine.py`

The read path. Three query types:
- **FTS5 search** — BM25-ranked full-text search across symbol names + docstrings
- **Concept query** — FTS5 match + 1-hop graph expansion (connected symbols)
- **Context** — Direct lookup by symbol name + join to get callers/callees

### Analysis Layer (Phase 1)
**Files:** `seam/analysis/traversal.py`, `seam/analysis/impact.py`, `seam/analysis/flows.py`, `seam/analysis/changes.py`

Read-only graph reasoning on top of the SQLite index. No writes. Import hierarchy:

```
cli / server → analysis → query → indexer / db
```

- **traversal.py** — BFS edge-walk from seed symbols. Aggregates path confidence
  using the weakest-hop rule (AMBIGUOUS < INFERRED < EXTRACTED). Returns `Reached`
  dicts (name, distance, confidence). Batches IN-clauses to stay below SQLite's
  `SQLITE_MAX_VARIABLE_NUMBER` limit.
- **impact.py** — Wraps `traversal.walk()` and buckets results into risk tiers:
  `WILL_BREAK` (d=1), `LIKELY_AFFECTED` (d=2), `MAY_NEED_TESTING` (d≥3).
  Returns `ImpactResult` with `found` flag and per-direction `TierGroup` dicts.
- **flows.py** — BFS path-finding (source → target shortest path) and one-hop
  `callers()` / `callees()` queries. Each hop carries per-edge confidence.
- **changes.py** — Shells out to git to get a unified diff, parses it into
  per-file changed line ranges, maps ranges to symbols, runs `impact()` on each
  changed symbol, and rolls up an overall risk level with AMBIGUOUS attenuation.

---

## Data Flow: Write Path (seam init)

```
1. CLI: walk directory tree, collect .py + .ts + .js files
2. For each file:
   a. parser.parse_python(path) → tree-sitter Node
   b. graph.extract_symbols(node, "python", path) → [Symbol]
   c. graph.extract_edges(node, "python", path, symbols) → [Edge] with confidence tags
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

## Data Flow: Impact Analysis (seam_impact)

```
1. Agent: seam_impact("upsert_file", direction="upstream", max_depth=3)
2. server/tools.py: validate + clamp depth
3. analysis.impact.impact(conn, "upsert_file", "upstream", 3)
   a. analysis.traversal.walk(conn, ["upsert_file"], "upstream", 3)
      — BFS: follow edges where target_name == "upsert_file"
      — propagate weakest-hop confidence along each path
      — return Reached list (name, distance, confidence)
   b. Bucket Reached by distance into risk tiers (d=1/2/3+)
   c. Batch-lookup file paths for all reached names
4. Return: {found, target, upstream: {WILL_BREAK: [...], LIKELY_AFFECTED: [...], ...}}
```

---

## Phase 2 Extensions (planned)

- **Semantic comment nodes** — `# WHY:`, `# HACK:`, `# NOTE:` as queryable entities
- **Go + Rust parsers** — additional tree-sitter grammars
- **Cross-file confidence resolution** — upgrade INFERRED edges to EXTRACTED when
  the target resolves to a unique symbol across the full index (not just same-file)

---

## Constraints

- **No external services** — zero network calls at runtime
- **No process per project** — MCP server launched with `cwd` = project root; single binary
- **SQLite file size** — target <50MB for a 100k LOC codebase
- **Startup time** — `seam start` must be ready in <500ms after first `seam init`
