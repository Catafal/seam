# Backend Structure — Seam

> This is the authoritative module map. Every new file must fit this structure.
> Seam has no frontend. All code is in the `seam/` Python package.

---

## Package Layout

```
seam/                           ← Python package root
├── __init__.py                 ← Version + public API surface
├── config.py                   ← All settings (from env via os.getenv)
│                                 SEAM_LANGUAGE_MAP: .py→python, .ts/.tsx→typescript,
│                                 .js/.mjs/.cjs→javascript, .go→go, .rs→rust
│
├── cli/                        ← CLI entry points (Typer)
│   ├── __init__.py
│   └── main.py                 ← app = typer.Typer(); init, start, status
│
├── indexer/                    ← Parse → extract → store pipeline
│   ├── __init__.py
│   ├── parser.py               ← tree-sitter parsing per language
│   │                             parse_python, parse_typescript, parse_javascript,
│   │                             parse_go, parse_rust — all delegate to _parse()
│   ├── graph_common.py         ← LEAF: shared TypedDicts, constants, helpers
│   │                             Symbol, Edge, Comment, Confidence; _text, _node_name,
│   │                             _make_symbol, _match_marker, _block_comment_lines,
│   │                             _find_enclosing_function, _go_recv_type_name,
│   │                             _rust_impl_type_name. Imports stdlib + tree_sitter only.
│   ├── graph_go_rust.py        ← Go + Rust extractors (imports graph_common only)
│   │                             _extract_symbols_go/rust, _extract_edges_go/rust,
│   │                             _extract_comments_go/rust, doc-comment helpers
│   ├── graph.py                ← Python + TypeScript dispatchers; re-exports public types
│   │                             from graph_common; imports Go/Rust extractors from
│   │                             graph_go_rust. Public API: extract_symbols, extract_edges,
│   │                             extract_comments.
│   ├── pipeline.py             ← Shared parse→extract→upsert path (CLI + watcher)
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
cli/ → server/ → analysis/ → query/ → indexer/db
                                      ↑
                            watcher/ → indexer/
```

Within `indexer/`, the import order is strictly:
```
graph_common  (leaf — stdlib + tree_sitter only)
     ↑              ↑
graph_go_rust    graph.py   (both import from graph_common; graph.py also imports graph_go_rust)
     ↑
graph.py (dispatchers + re-exports)
     ↑
pipeline.py
```

| Layer | Can import from | Cannot import from |
|---|---|---|
| `cli/` | server, analysis, indexer, watcher, config | — |
| `server/` | analysis, query, config | cli, watcher |
| `analysis/` | indexer.db, query, config | cli, server |
| `query/` | indexer.db, config | cli, server, analysis, watcher |
| `indexer/pipeline` | indexer.db, indexer.graph, indexer.parser, config | cli, server, query, analysis, watcher |
| `indexer/graph` | graph_common, graph_go_rust, config | cli, server, query, analysis, watcher |
| `indexer/graph_go_rust` | graph_common only | graph.py or any other seam module |
| `indexer/graph_common` | stdlib, tree_sitter only | any seam module (leaf) |
| `indexer/parser` | config, tree_sitter grammars | any seam indexer sub-module |
| `indexer/db` | config | cli, server, query, analysis, watcher |
| `watcher/` | indexer, config | cli, server, query, analysis |
| `config` | stdlib only | anything in seam/ |

---

## Module Responsibilities

### `seam/config.py`
- Reads all config from `os.getenv()` with defaults
- Exports: `SEAM_DB_PATH`, `SEAM_LOG_LEVEL`, `SEAM_DEBOUNCE_MS`, `SEAM_MAX_FILE_BYTES`
- Never import from other seam modules (avoids circular imports)

### `seam/indexer/parser.py`
- One function per language: `parse_python`, `parse_typescript`, `parse_javascript`,
  `parse_go`, `parse_rust` — all delegate to the internal `_parse(path, language)` helper.
- Returns raw tree-sitter root Node for graph.py to interpret.
- Handles encoding, binary files, and parse errors gracefully. Never raises.

### `seam/indexer/graph_common.py` (leaf — Phase 1b addition)
- Shared TypedDicts: `Symbol`, `Edge`, `Comment`; type alias: `Confidence`
- Shared constants: `SEMANTIC_MARKERS`, `_MARKER_RE`
- Shared helpers: `_text`, `_node_name`, `_make_symbol`, `_match_marker`,
  `_block_comment_lines`, `_arrow_function_name`, `_find_enclosing_function`
- Go/Rust receiver helpers: `_go_recv_type_name`, `_rust_impl_type_name`
  (kept here so `_find_enclosing_function` can call them without importing from
  graph_go_rust — this is what maintains the leaf property)
- MUST remain a leaf: imports stdlib and tree_sitter only.

### `seam/indexer/graph_go_rust.py` (Phase 1b addition)
- Go extractors: `_extract_symbols_go`, `_extract_edges_go`, `_extract_comments_go`
- Rust extractors: `_extract_symbols_rust`, `_extract_edges_rust`, `_extract_comments_rust`
- Doc-comment helpers: `_go_doc_comment`, `_rust_doc_comment`
- Imports from `graph_common` only — never from `graph.py`.

### `seam/indexer/graph.py`
- Public API: `extract_symbols(node, language, filepath) → list[Symbol]`
- Public API: `extract_edges(node, language, filepath, symbols) → list[Edge]`
- Public API: `extract_comments(node, language, filepath) → list[Comment]`
- Python + TypeScript/JavaScript extractors live here.
- Go + Rust extractors are delegated to graph_go_rust.py (imported at top level).
- Re-exports `Symbol`, `Edge`, `Comment`, `Confidence` from graph_common so callers
  using `from seam.indexer.graph import Symbol` continue to work unchanged.
- Symbol: `{name, kind, file, start_line, end_line, docstring}`
- Edge: `{source, target, kind, file, line, confidence}`

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
