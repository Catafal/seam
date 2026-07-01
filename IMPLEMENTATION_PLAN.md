# Implementation Plan — Seam Phase 0

> This is the build script. Execute in order. Do not skip steps.
> Update status as work completes: `[ ]` → `[x]`
> Append implementation notes after each completed step.

---

## Phase 0: Foundation

### 1.0 — Day 0 Bootstrap (this session)
- [x] 1.1 — Repository structure created
- [x] 1.2 — DISCOVERY.md written and approved
- [x] 1.3 — CLAUDE.md created (skeleton)
- [x] 1.4 — PRD.md, APP_FLOW.md, TECH_STACK.md, BACKEND_STRUCTURE.md written
- [x] 1.5 — docs/ architecture artifacts created (ARCHITECTURE.md, schema.sql, MCP tool specs, ADRs)
- [x] 1.6 — Python package scaffold created (empty modules)
- [x] 1.7 — Makefile + pyproject.toml + gate command verified passing
- [x] 1.8 — .gitignore, .env.example committed
- [x] 1.9 — Seam agent guidance finalized

---

### 2.0 — SQLite Schema + Database Layer

- [ ] 2.1 — Implement `seam/indexer/db.py`
  - `init_db(db_path)` — create schema from `docs/database/schema.sql`
  - `upsert_file(conn, filepath, symbols, edges)` — atomic transaction
  - `delete_file(conn, filepath)` — cascading delete
  - Verify FTS5 available at init time; raise clear error if not
  
- [ ] 2.2 — Unit tests for `db.py` using in-memory SQLite
  - `tests/unit/test_db.py`
  - Test: schema creates cleanly
  - Test: upsert is idempotent (run twice = same result)
  - Test: delete removes all data for a file

*Gate must pass after this step.*

---

### 3.0 — Tree-sitter Parser Layer

- [ ] 3.1 — Python parser (`seam/indexer/parser.py`)
  - `parse_python(path: Path) → Optional[Node]`
  - Handle: encoding detection, binary files, parse errors (return None)
  - Must return a valid tree-sitter Node or None (never raise)

- [ ] 3.2 — TypeScript/JavaScript parser
  - `parse_typescript(path: Path) → Optional[Node]`
  - `parse_javascript(path: Path) → Optional[Node]`
  - Detect .ts / .tsx / .js / .mjs / .cjs

- [ ] 3.3 — Parser tests with fixtures
  - `tests/unit/test_parser.py`
  - Use `tests/fixtures/sample.py` and `tests/fixtures/sample.ts`
  - Test: parse returns Node (not None) for valid files
  - Test: parse returns None for binary files
  - Test: parse returns None for malformed files (no raise)

*Gate must pass after this step.*

---

### 4.0 — Symbol + Edge Extraction

- [ ] 4.1 — Symbol extraction (`seam/indexer/graph.py`)
  - `extract_symbols(node: Node, language: str, filepath: Path) → list[Symbol]`
  - Python: functions, classes, methods (not nested lambdas)
  - TypeScript: functions, classes, interfaces, type aliases
  - Symbol fields: `name, kind, file, start_line, end_line, docstring`

- [ ] 4.2 — Edge extraction
  - `extract_edges(node: Node, language: str, filepath: Path) → list[Edge]`
  - Python: `import` statements (resolve to symbol names)
  - TypeScript: `import` statements, function calls within same file scope
  - Edge fields: `source, target, kind` (kind: import | call)

- [ ] 4.3 — Graph tests
  - `tests/unit/test_graph.py`
  - Use `tests/fixtures/sample.py` and `tests/fixtures/sample.ts`
  - Test: known function extracted with correct name, line, kind
  - Test: known import edge extracted with correct source/target
  - Test: docstring extracted for documented function

*Gate must pass after this step.*

---

### 5.0 — Query Engine

- [ ] 5.1 — FTS5 search (`seam/query/engine.py`)
  - `search(conn, text: str, limit: int = 20) → list[SearchResult]`
  - Uses FTS5 MATCH with BM25 ranking
  - Returns: `{symbol, file, line, snippet}`

- [ ] 5.2 — Concept query (hybrid search)
  - `query(conn, concept: str, limit: int = 10) → list[QueryResult]`
  - FTS5 match + graph expansion (symbols connected to matching symbols)
  - Returns: `{symbol, file, line, score, callers_count, callees_count}`

- [ ] 5.3 — Context lookup
  - `context(conn, symbol_name: str) → Optional[ContextResult]`
  - Returns: `{symbol, file, line, docstring, callers: [str], callees: [str]}`
  - Returns None if symbol not found

- [ ] 5.4 — Query engine tests (in-memory SQLite)
  - `tests/unit/test_query_engine.py`
  - Seed a minimal in-memory DB; test all three query functions
  - Test: empty results for unknown symbol (not an error)

*Gate must pass after this step.*

---

### 6.0 — CLI (`seam init`, `seam status`)

- [ ] 6.1 — `seam init` command (`seam/cli/main.py`)
  - Walk directory tree, skip gitignored files (read `.gitignore`)
  - Dispatch to parser by file extension
  - Call `extract_symbols` + `extract_edges` + `upsert_file`
  - Print progress bar (Typer's rich progress)
  - Print summary on completion

- [ ] 6.2 — `seam status` command
  - Read from `seam.db`: file count, symbol count, edge count, last modified
  - Print watcher PID if running (check pidfile)
  - Print index freshness (compare DB mtime to most recent file mtime)

- [ ] 6.3 — CLI smoke test
  - `tests/integration/test_cli.py`
  - Test: `seam init` on the `tests/fixtures/` directory succeeds
  - Test: `seam status` after `seam init` returns non-zero symbol count

*Gate must pass after this step.*

---

### 7.0 — File Watcher

- [ ] 7.1 — Watcher daemon (`seam/watcher/daemon.py`)
  - `SeamWatcher` extends watchdog's `FileSystemEventHandler`
  - `on_modified(event)` — debounced trigger (500ms default)
  - Calls `parser → graph → db.upsert_file` for changed file
  - PID file at `.seam/watcher.pid`

- [ ] 7.2 — Watcher integration test
  - `tests/integration/test_watcher.py`
  - Start watcher on tmp dir, modify a file, assert DB updated within 2s

*Gate must pass after this step.*

---

### 8.0 — MCP Server

- [ ] 8.1 — MCP server setup (`seam/server/mcp.py`)
  - `create_server() → Server` — configure MCP server with stdio transport
  - Register three tools: `seam_query`, `seam_context`, `seam_search`

- [ ] 8.2 — Tool handlers (`seam/server/tools.py`)
  - `handle_seam_query(concept, limit?)` → calls `query.engine.query()`
  - `handle_seam_context(symbol)` → calls `query.engine.context()`
  - `handle_seam_search(text, limit?)` → calls `query.engine.search()`
  - Input validation: non-empty strings, positive limits

- [ ] 8.3 — `seam start` command
  - Starts watcher daemon (background)
  - Starts MCP server (foreground, stdio)
  - Writes watcher PID file

- [ ] 8.4 — MCP tool integration test (optional Phase 0)
  - Direct call to tool handlers with test DB
  - Verify output format is MCP-compatible

*Gate must pass after this step.*

---

### 9.0 — Benchmarking

- [ ] 9.1 — Baseline measurement on Bach (no Seam)
  - Record: tokens used in one full coding session
  - Record: tool calls (grep, file reads) count

- [ ] 9.2 — With-Seam measurement (same task on Bach)
  - Record: tokens used with Seam MCP active
  - Record: seam_query/seam_context calls

- [ ] 9.3 — Benchmark report
  - Write `docs/benchmark.md` with methodology + results
  - Target: ≥30% token reduction

---

### 10.0 — Release Prep (Phase 0 Done)

- [ ] 10.1 — README.md with install + quickstart
- [ ] 10.2 — `uv sync` → commit `uv.lock`
- [ ] 10.3 — Verify `make gate` passes in clean environment
- [ ] 10.4 — Tag v0.1.0

---

## Build Loop Rules

- Run `make gate` before every commit (no exceptions)
- One logical unit per commit (parser, schema, one CLI command)
- Update this file after each completed step
- Any gotcha discovered → add to `lessons.md`
- Any architectural decision → add ADR in `docs/adr/`
