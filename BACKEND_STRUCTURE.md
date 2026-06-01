# Backend Structure — Seam

> This is the authoritative module map. Every new file must fit this structure.
> Seam has no frontend. All code is in the `seam/` Python package.

---

## Package Layout

```
seam/                           ← Python package root
├── __init__.py                 ← Version + public API surface
├── config.py                   ← All settings (from env via os.getenv)
│
├── cli/                        ← CLI entry points (Typer)
│   ├── __init__.py
│   └── main.py                 ← app = typer.Typer(); init, start, status
│
├── indexer/                    ← Parse → extract → store pipeline
│   ├── __init__.py
│   ├── parser.py               ← tree-sitter parsing per language
│   ├── graph.py                ← Symbol + edge extraction from AST
│   └── db.py                   ← SQLite write operations (schema + upsert)
│
├── watcher/                    ← OS file watcher
│   ├── __init__.py
│   └── daemon.py               ← watchdog EventHandler; debounced re-index
│
├── server/                     ← MCP server
│   ├── __init__.py
│   ├── mcp.py                  ← MCP server setup (stdio transport)
│   └── tools.py                ← seam_query, seam_context, seam_search implementations
│
└── query/                      ← Query engine (read path)
    ├── __init__.py
    └── engine.py               ← FTS5 + graph traversal queries
```

---

## Layer Rules (Import Hierarchy)

```
cli/ → server/ → query/ → indexer/db
                         ↑
               watcher/ → indexer/
```

| Layer | Can import from | Cannot import from |
|---|---|---|
| `cli/` | server, indexer, watcher, config | — |
| `server/` | query, config | cli, watcher |
| `query/` | indexer.db, config | cli, server, watcher |
| `indexer/` | config | cli, server, query, watcher |
| `watcher/` | indexer, config | cli, server, query |
| `config` | stdlib only | anything in seam/ |

---

## Module Responsibilities

### `seam/config.py`
- Reads all config from `os.getenv()` with defaults
- Exports: `SEAM_DB_PATH`, `SEAM_LOG_LEVEL`, `SEAM_DEBOUNCE_MS`, `SEAM_MAX_FILE_BYTES`
- Never import from other seam modules (avoids circular imports)

### `seam/indexer/parser.py`
- One function per language: `parse_python(path) → Node`, `parse_typescript(path) → Node`
- Returns raw tree-sitter Node for graph.py to interpret
- Handles encoding, binary files, and parse errors gracefully

### `seam/indexer/graph.py`
- `extract_symbols(node, language, filepath) → list[Symbol]`
- `extract_edges(node, language, filepath) → list[Edge]`
- Pure functions: take AST node, return structured data
- Symbol: `{name, kind, file, line, col, docstring}`
- Edge: `{source_name, target_name, kind, file, line}`

### `seam/indexer/db.py`
- `init_db(db_path) → Connection` — create schema if not exists
- `upsert_file(conn, filepath, symbols, edges)` — atomic file update
- `delete_file(conn, filepath)` — remove all data for a file
- Never holds long-lived connections; pass conn as parameter

### `seam/query/engine.py`
- `query(conn, concept, limit) → list[QueryResult]` — FTS5 + graph hybrid
- `context(conn, symbol_name) → ContextResult` — callers + callees + metadata
- `search(conn, text, limit) → list[SearchResult]` — FTS5 full-text

### `seam/server/tools.py`
- Thin adapter: MCP tool handlers that call query.engine functions
- Input validation only — no business logic
- Returns MCP-compatible response format

### `seam/watcher/daemon.py`
- `SeamWatcher(db_path, root_path)` — watchdog EventHandler subclass
- `on_modified(event)` — debounced re-index of changed file
- `start()` / `stop()` — manage Observer lifecycle

---

## File Naming Conventions

| Type | Convention | Example |
|---|---|---|
| Python files | snake_case | `user_service.py` |
| Python classes | PascalCase | `SeamWatcher` |
| Python functions | snake_case | `extract_symbols()` |
| Python constants | UPPER_SNAKE_CASE | `SEAM_DB_PATH` |
| Test files | `test_<module>.py` | `test_parser.py` |
| Test classes | `Test<Feature>` | `TestPythonParser` |

---

## Tests Layout

```
tests/
├── unit/                       ← Pure function tests; no I/O
│   ├── test_parser.py          ← parse_python, parse_typescript
│   ├── test_graph.py           ← extract_symbols, extract_edges
│   └── test_query_engine.py    ← query, context, search (in-memory SQLite)
│
├── integration/                ← Tests that hit real SQLite + file system
│   ├── test_indexer.py         ← Full index pipeline
│   └── test_watcher.py         ← Watcher → re-index cycle
│
└── fixtures/                   ← Sample source files for parser tests
    ├── sample.py               ← Python with functions, classes, imports
    └── sample.ts               ← TypeScript with functions, interfaces
```

---

## Max Size Rules

- Max 200 lines per function (from global CLAUDE.md)
- Max 1000 lines per file (from global CLAUDE.md)
- If a module exceeds 1000 lines, split into `_module_part_a.py` + `_module_part_b.py` and re-export from `module.py`
