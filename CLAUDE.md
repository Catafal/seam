# Project: Seam

## What This Is
Local code intelligence MCP server — indexes codebases with tree-sitter, stores in SQLite, exposes `seam_query`, `seam_context`, `seam_search` via MCP so AI agents query instead of grep.

## Tech Stack
- Python 3.14+ | uv 0.9.14
- tree-sitter 0.25.2 + tree-sitter-python 0.25.0 + tree-sitter-typescript 0.23.2 + tree-sitter-go 0.25.0 + tree-sitter-rust 0.24.2 + tree-sitter-java 0.23.5 + tree-sitter-c-sharp 0.23.5 + tree-sitter-ruby 0.23.1 + tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4 + tree-sitter-php 0.24.1
- mcp 1.27.2 (stdio transport — OPTIONAL `server` extra, not a core dep) | watchdog 6.0.0 | typer 0.26.4 | tomlkit 0.15.0 (Codex install config)
- SQLite + FTS5 (built-in, no ORM) | pytest 9.0.3 | ruff 0.15.15 | mypy 2.1.0

## Commands
- `make gate` — Full verification (lint + typecheck + tests) — **run before every commit**
- `make install-dev` — Install all deps including dev
- `make fmt` — Format + fix lint (not part of gate)
- `make bench-semantic` — Run semantic recall benchmark (requires `[semantic]` extra + one-time model download; NOT part of gate)
- `uv run seam init` — Index current directory
- `uv run seam init --semantic` — Index + build local embeddings for hybrid semantic search (requires `pip install 'seam-mcp[semantic]'`; downloads model ~67 MB on first run)
- `uv run seam sync` — Incrementally reconcile the index (changed/added/removed files) + gated cluster recompute
- `uv run seam sync --semantic` — Reconcile + rebuild all embeddings (full re-embed; safe/idempotent)
- `uv run seam start` — Start MCP server + watcher
- `uv run seam status` — Show index stats (includes `embeddings` row: count + model, or mismatch warning)
- `uv run seam search <text>` / `seam query <concept>` / `seam context <symbol>` — CLI-only read
  commands (no MCP server needed); `--json`/`--quiet`, `--lean` on context
- `uv run seam search <text> --no-semantic` — Force keyword-only FTS5, bypassing hybrid path
- `uv run seam query <concept> --no-semantic` — Force keyword-only FTS5, bypassing hybrid path
- `uv run seam flows [entry]` — execution flows: list entry points (call-graph roots ranked by
  downstream reach), or expand one entry's forward call-chain tree; `--json`/`--quiet`
- `uv run seam install` — Write the MCP config into an agent (`--target claude|cursor|codex|all`,
  `--location project|user`, `--print-config`); `uv run seam uninstall` reverses it
- `uv run seam serve` — Start the local Seam Explorer web server (FastAPI, 127.0.0.1:7420);
  requires `[web]` extra (`pip install 'seam-mcp[web]'`); `--host`, `--port`, `--no-open`
- `uv sync` installs the CLI only; `uv sync --extra server` adds the optional MCP server (`mcp` package); `uv sync --extra semantic` adds fastembed for semantic search

## File References
- `DISCOVERY.md` — real goal (what we're building and why)
- `PRD.md` — requirements and acceptance criteria
- `APP_FLOW.md` — agent interaction flows
- `TECH_STACK.md` — exact package versions
- `BACKEND_STRUCTURE.md` — module map and import rules
- `IMPLEMENTATION_PLAN.md` — current task breakdown (build script)
- `progress.txt` — session state (READ THIS FIRST)
- `lessons.md` — gotchas and AI mistake log
- `docs/ARCHITECTURE.md` — system diagram and data flows
- `docs/database/schema.sql` — SQLite schema (authoritative)
- `docs/api-contracts/mcp-tools.yaml` — MCP tool specs
- `docs/adr/` — architecture decision records

## Package Layout
```
seam/config.py               ← all settings (env vars with defaults)
                                SEAM_LANGUAGE_MAP: .py .ts .tsx .js .mjs .cjs .go .rs .java .cs .rb
                                  .c .h .cpp .cc .cxx .c++ .hpp .hh .hxx .php .swift (12 languages)
                                                   .java .cs .rb .c .h .cpp .cc .cxx .c++ .hpp .hh .hxx .php
                                SEAM_CLUSTER_NAMING: "deterministic" | "llm" (default: deterministic)
                                SEAM_LLM_API_KEY: optional, required for llm naming
                                SEAM_LLM_MODEL: default "gpt-4o-mini"
                                SEAM_CLUSTER_MIN_SIZE: min community size (default: 2)
                                SEAM_AFFECTED_DEPTH: max upstream hops for affected traversal (default: 5)
                                SEAM_MAX_AFFECTED_FILES: max changed files per seam_affected call (default: 200)
                                SEAM_MAX_AFFECTED_SYMBOLS: max symbols analyzed per file in affected() (default: 50)
                                SEAM_FUZZY_MAX_DIST: max Damerau-Levenshtein distance for fuzzy fallback (default: 1)
                                SEAM_FUZZY_MAX_CANDIDATES: max symbol names evaluated in fuzzy scan (default: 500)
                                SEAM_MAX_SIGNATURE_LEN: max signature length in chars before truncation (default: 300)
                                SEAM_BUILTIN_FILTERING: "on" | "off" — tag count==0 names as builtin (default: on)
                                SEAM_IMPORT_RESOLUTION: "on" | "off" — import-promotion step A (default: on)
                                SEAM_MAX_IMPORT_CANDIDATES: cap on declaring files per import lookup (default: 25)
                                SEAM_PROXIMITY_MAX_CANDIDATES: cap on collision candidates for proximity ranking (default: 25)
                                SEAM_PACK_NEIGHBOR_LIMIT: max enriched callers and max enriched callees in context_pack (default: 10)
                                SEAM_PACK_PER_FILE_CAP: max neighbor entries from any single file — diversity cap (default: 3)
                                SEAM_PACK_MAX_COMMENTS: max WHY/HACK/NOTE comments in context_pack bundle (default: 10)
                                SEAM_IMPACT_MAX_RESULTS: per-tier entry cap for seam_impact (default: 25; 0 = unlimited) [Phase 8]
                                SEAM_FLOW_ENTRY_LIMIT: max entry points listed by seam_flows (default: 20)
                                SEAM_FLOW_MAX_DEPTH: max depth when expanding a flow tree (default: 6)
                                SEAM_FLOW_MAX_BREADTH: max callees per node in a flow tree (default: 8)
                                SEAM_FLOW_REACH_DEPTH: BFS depth used to score entry-point reach (default: 5)
                                SEAM_SEMANTIC: "off" | "on" — master switch for hybrid semantic search (default: off)
                                SEAM_EMBED_MODEL: fastembed model name (default: "BAAI/bge-small-en-v1.5")
                                SEAM_SEMANTIC_LIMIT: top-k semantic candidates fetched before RRF merge (default: 20)
                                SEAM_SEMANTIC_SCAN_CAP: max embedding rows loaded per scan (default: 20000)
                                SEAM_RRF_K: RRF smoothing constant k, Cormack et al. SIGIR 2009 (default: 60)
                                SEAM_NAME_EXPANSION_CAP: max member bare names included when a class/interface/struct
                                  is used as a context/impact/query seed (Tier A name-resolution; default: 50)
                                SEAM_BARE_RESOLVE_CAP: max rows returned by the suffix scan inside
                                  resolve_query_to_defs() for bare-name → qualified-def lookup (Tier A; default: 25)
                                SEAM_TYPE_INFERENCE: "on" | "off" — master switch for extraction-time receiver-type
                                  inference in Python and TypeScript/JS extractors (Tier B; default: on).
                                  When "on", the extractor resolves receiver expressions (class fields, function
                                  params, local variables with type annotations) to qualified 'Type.method' call
                                  targets — e.g. `client: Client` turns obj.send() → `Client.send` as the edge
                                  target. Conservatism contract: only plain user types bind; optionals/generics/
                                  unknown identifiers return None → bare target kept (never emit a wrong edge).
                                  When "off", inference is skipped entirely — byte-identical to pre-Tier-B.
                                  See also SEAM_SWIFT_TYPE_INFERENCE (Swift-specific knob, independent).
                                SEAM_SWIFT_TYPE_INFERENCE: "on" | "off" — Swift-specific receiver-type inference
                                  (Phase 10 / Tier B Swift extension; default: on). Independent of SEAM_TYPE_INFERENCE.
seam/analysis/embeddings.py  ← LEAF: fastembed wrapper for semantic search (Semantic phase)
                                is_available() → bool (lazy, cached; never raises)
                                symbol_text(name, signature, docstring) → str (canonical embed input)
                                embed_texts(texts, model) → list[bytes] (float32 blobs; [] on failure)
                                embed_query(text, model) → bytes (b'' on failure); both degrade gracefully
                                fastembed + numpy are LAZY imports (only inside function bodies, never at module scope)
seam/indexer/embedding_index.py ← index orchestration bridge for embeddings (Semantic phase, mirrors cluster_index)
                                index_embeddings(conn, *, model, batch) → int: -1=error, 0=skipped, ≥1=count
                                single-transaction batch upsert (INSERT OR REPLACE) for clean-retry on failure
                                called by `seam init --semantic` after clustering; NOT called by the watcher
seam/query/semantic.py       ← LEAF: semantic search read path (Semantic phase)
                                rrf_merge(fts_ranked, semantic_ranked, k=60) → list[int] (pure RRF, no model)
                                cosine_sim(a_bytes, b_bytes) → float (pure-Python struct.unpack; no numpy dep)
                                semantic_candidates(conn, query, *, model, limit) → list[tuple[int, float]]
                                  model-mismatch guard → [] (never silently mixes embedding spaces)
                                  numpy fast path inside _semantic_candidates_impl (matmul, ~1–5ms/10k)
                                  pure-Python cosine_sim fallback when numpy absent (defensive)
seam/analysis/processes.py   ← LEAF: execution flows (Flows) — list_entry_points (call-graph roots
                                ranked by downstream reach, tests excluded) + build_flow (forward
                                call-chain tree, depth/breadth-capped, cycle-safe). Reuses confidence
                                + testpaths; name-count confidence (no import promotion). Never raises.
seam/installer/              ← `seam install`/`uninstall` engine (CLI-only; NO MCP tool)
                                __init__.py: TARGETS registry {claude,cursor,codex} + resolve_seam_command()
                                core.py: AgentTarget ABC + InstallResult + shared idempotent JSON merge
                                jsonfile.py (LEAF, stdlib json) — Claude/Cursor; tomlfile.py (LEAF, tomlkit) — Codex
                                claude.py/.mcp.json+type:stdio · cursor.py/.cursor/mcp.json · codex.py/~/.codex/config.toml
seam/cli/install.py          ← `seam install`/`uninstall` Typer commands (registered onto app in main.py)
seam/cli/read.py             ← `seam query`/`search`/`context` — CLI-only read commands over the
                                transport-agnostic tools.py handlers (query SQLite directly; NO MCP)
seam/cli/serve.py            ← `seam serve` — lazy-import FastAPI/uvicorn ([web] extra) + run the
                                Seam Explorer web server on 127.0.0.1:7420; NO_INDEX guard; opens browser
seam/cli/main.py             ← Typer CLI (init, sync, start, status, impact, trace, changes, why, clusters,
                                affected, pack, install, uninstall, query, search, context, serve)
                                NOTE: `from seam.server.mcp import create_server` is LAZY (inside start())
                                — `mcp` is an optional extra; only `seam start` needs it
seam/indexer/db.py (schema)  ← schema loaded packaged-first: seam/_data/schema.sql (force-included in wheel)
                                with fallback to docs/database/schema.sql (dev). Fixes installed `seam init`.
                                --json / --quiet on read commands; --stdin on affected + changes
                                sync: --json / --quiet / --force-clusters (Phase 7)
                                --lean on impact/trace/pack + --limit on impact (Phase 8); all 3 modes route through handlers
seam/indexer/sync.py         ← LEAF: Phase 7 reconcile engine — sync(conn, root, *, recompute_clusters,
                                force_clusters, naming_mode, llm_api_key, llm_model, min_size) → SyncResult
                                mtime pre-filter → SHA-1 confirm; existsSync-guarded delete; FULL cluster
                                recompute gated on graph_changed (added+modified+removed>0) or force_clusters
                                reuses walk_project + index_one_file + sha1 + delete_file + index_clusters
                                SyncResult: added, modified, removed, unchanged, skipped, graph_changed,
                                clusters_recomputed, cluster_count (None=skipped, -1=recompute failed, ≥0=ok)
seam/cli/output.py           ← LEAF: agent-output contract — success/error JSON envelope, quiet renderer
                                {"ok":true,"data":...} / {"ok":false,"error":{"code","message"}}
                                error codes: NO_INDEX INVALID_INPUT INVALID_QUERY NOT_A_GIT_REPO DB_ERROR
seam/query/fts.py            ← LEAF: FTS5 query construction + multi-signal rescoring (Phase 3)
                                build_match_query(text) → OR-joined prefix MATCH expression
                                rescore(rows, terms) → reranked rows (name/path/test/cluster signals)
                                extract_terms(text) → plain token list (single source of tokenisation)
seam/analysis/affected.py    ← affected(conn, changed_files, *, depth, repo_root) → AffectedResult
                                changed files → owning symbols → upstream impact → impacted test files
                                reuses analysis.impact + analysis.testpaths.is_test_file
seam/indexer/parser.py       ← tree-sitter parsing (Python, TypeScript, JavaScript, Go, Rust,
                                Java, C#, Ruby, C, C++, PHP)
seam/indexer/graph_common.py ← LEAF: shared TypedDicts (Symbol/Edge/Comment), helpers
                                Symbol now carries: signature, decorators, is_exported, visibility, qualified_name
                                Edge now carries: receiver (raw receiver text; None for bare/import/pre-v10 edges)
                                Edge kind vocabulary: 'call' | 'import' | 'extends' | 'implements' | 'instantiates'
                                  — 'instantiates' added by Tier B B6 (new/struct-literal/composite-literal nodes)
seam/indexer/graph_go_rust.py← Go + Rust extractors (imports graph_common only)
seam/indexer/graph_java_csharp.py ← Java + C# symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; split from graph.py to stay under 1000 lines
seam/indexer/graph_c_cpp.py  ← C + C++ symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; _dedup_cpp_symbols handles in-class/out-of-line duplicates
seam/indexer/graph_ruby.py   ← Ruby symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; handles def self.x singleton methods
seam/indexer/graph_php.py    ← PHP symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; handles grouped-use and enum methods
seam/indexer/graph_swift.py  ← Swift symbol/edge/comment extractors (Phase 10)
                                imports graph_common only; class/struct/actor/extension→class,
                                enum→type, protocol→interface; /// and /** */ docstrings
seam/indexer/graph_swift_infer.py ← LEAF: Swift receiver-type inference (Phase 10 / Tier B extension)
                                Two-layer scope model: class-level property pre-scan + per-function
                                param/local bindings. Controlled by SEAM_SWIFT_TYPE_INFERENCE config knob.
                                Conservatism contract: only plain user types bind; None on optionals/generics/
                                chained/unknown. _resolve_navigation_target is the core lookup function.
seam/indexer/graph_scope_infer.py ← LEAF: Python + TypeScript/JS receiver-type inference (Tier B B4)
                                Mirrors graph_swift_infer two-layer scope model. Used by graph.py extractors.
                                resolve_receiver_type(receiver_text, class_name, var_types, self_names) → str|None
                                self/cls/this normalize to enclosing class; optionals/containers/generics → None.
                                Controlled by SEAM_TYPE_INFERENCE config knob.
seam/indexer/graph_scope_infer_ext.py ← LEAF: Java + C# + Ruby receiver-type inference (Tier B B5)
                                Extends the two-layer scope model to Java/C#/Ruby families.
seam/indexer/graph_scope_infer_ext2.py ← LEAF: Go + Rust + C/C++ + PHP receiver-type inference (Tier B B5)
                                Extends the two-layer scope model to Go/Rust/C/C++/PHP families.
seam/indexer/graph_typescript.py ← TypeScript/JS extractors (split from graph.py for Tier B B3)
                                Tier B B3: member_expression call_expression nodes now emit call edges
                                (previously only bare identifier calls were indexed — this fixes the
                                major TS/JS recall hole where obj.method() calls were silently dropped).
                                Tier B B6: new_expression nodes emit 'instantiates' edges.
seam/indexer/graph.py        ← Python/TS dispatchers; re-exports types from graph_common;
                                imports Go/Rust/Java/C#/C/C++/Ruby/PHP/Swift extractors at top level
seam/indexer/signatures.py   ← LEAF: Phase 4 enrichment — extract_node_fields(node, language, ...) → NodeFields
                                per-language: signature, decorators, is_exported, visibility, qualified_name
                                for Python / TypeScript / JavaScript / Go / Rust; never raises
seam/indexer/signatures_ext.py ← LEAF: Phase 9 enrichment for Java/C#/Ruby/C/C++/PHP (Phase 9)
                                NodeFields re-declared (not imported) to avoid circular import; drift-tested
seam/analysis/imports.py     ← LEAF: extract_import_mappings + resolve_import_source + compute_path_proximity
                                per-language import extraction for Python/TS/JS/Go/Rust; never raises
                                maps import source strings to candidate declaring-file paths (5-lang extension order)
seam/analysis/imports_ext.py ← LEAF: Phase 9 import-mapping extraction for Java/C#/Ruby/C/C++/PHP (Phase 9)
                                _ImportMapping re-declared (not imported) to avoid circular import; drift-tested
                                resolution returns [] for Java/C#/PHP package paths (classpath out of scope)
seam/analysis/builtins.py    ← LEAF: is_builtin(name, language) → bool over static per-language frozensets
                                covers Python/TS/JS/Go/Rust/Java/C#/Ruby/C/C++/PHP; conservative vocabulary
seam/analysis/confidence.py  ← whole-index confidence resolver (Phase 5 extended)
                                resolve_edge() → Resolution{confidence, resolved_by, best_candidate}
                                load_import_mappings(conn, file_path) → list[ImportMapping]
                                resolve() kept as backward-compat thin shim
seam/indexer/pipeline.py     ← shared parse→extract→upsert path (CLI + watcher)
seam/indexer/cluster_index.py← clustering orchestration bridge (Phase 2)
                                index_clusters(conn, ...) → int; called by seam init only
seam/indexer/db.py           ← SQLite write (init_db, upsert_file, delete_file)
seam/analysis/clustering.py  ← LEAF: pure-Python Louvain community detection (Phase 2)
                                detect_communities(nodes, edges) → {name: cluster_id}
seam/analysis/cluster_naming.py ← LEAF: deterministic + opt-in LLM cluster labeling (Phase 2)
seam/query/engine.py         ← query(), context(), search() — read path
                                context() enriched with cluster_id/label/peers (Phase 2)
                                all three return signature/decorators/is_exported/visibility/qualified_name (Phase 4)
seam/query/names.py          ← LEAF: Tier A name-resolution helpers — bare_name, is_container_symbol,
                                get_member_names, edge_match_names, resolve_query_to_defs,
                                expand_impact_seeds. Imports only stdlib + seam/config (leaf, like clusters.py).
                                Bridges the qualified-symbol / bare-edge asymmetry: symbols stored as
                                "Class.method", edges stored as bare "method". Pure read-time; no schema change.
seam/query/clusters.py       ← cluster read queries (Phase 2): list_clusters, cluster_members,
                                cluster_peers; guards pre-v4 indexes
seam/query/pack.py           ← LEAF: context_pack(conn, symbol_name) → ContextPack | None
                                orchestrates context()+why() into one enriched bundle; applies caps from config
                                ContextPack: target, callers, callees (NeighborRef), why, cluster_peers, truncated
seam/server/tools.py         ← MCP tool handlers (thin adapters → engine + clusters + pack)
seam/server/graph_api.py     ← LEAF: build_neighborhood(conn, name, direction) → dict (Phase B1)
                                depth-1 neighbors from edges table; homonym-collapse (name-keyed nodes);
                                node enrichment: kind, signature, visibility, is_exported, cluster, definition_count
                                build_constellation(conn) → {clusters, links} (Explorer Phase 2): cluster
                                  list + weighted inter-cluster links; homonym-safe name→cluster map; never raises
seam/server/web.py           ← FastAPI app factory: create_web_app(db_path, root) → FastAPI (Phase B2)
                                v1: /api/status · /api/search · /api/graph/neighborhood · /api/symbol/{name} · /api/clusters
                                Explorer Phase 2 (all reuse handle_seam_* verbatim — zero query dup):
                                  /api/impact (handle_seam_impact, verbose=False) · /api/trace (handle_seam_trace,
                                  paths only) · /api/changes (handle_seam_changes; NOT_A_GIT_REPO→400) ·
                                  /api/constellation (graph_api.build_constellation)
                                Pydantic models = TS codegen source; static SPA at seam/_web/ (build hint if absent)
                                127.0.0.1-only enforced by CLI; requires [web] extra (lazy import pattern)
seam/watcher/daemon.py       ← watchdog daemon (debounced re-index)
tests/fixtures/              ← sample.py, sample.ts, sample.go, sample.rs
```

## Coding Conventions
- Max 200 lines per function | Max 1000 lines per file
- All imports at top of file
- Config from `seam/config.py` only — never `os.getenv()` in other modules
- Tests in `tests/` mirroring package structure
- snake_case files + functions | PascalCase classes | UPPER_SNAKE constants
- Type hints required; use `X | None` not `Optional[X]`

## Non-Negotiables
- **Gate must pass before every commit** — no exceptions, no `--no-verify`
- **Zero external services at runtime** — no API keys, no network calls
- **SQLite only** — no Neo4j, no graph DB, no ORM
- **Config from seam/config.py** — never hardcode paths or env var names
- **Parsers never raise** — return None on error; let the indexer skip gracefully
- **Edges use string names** (not symbol IDs) — required for independent re-indexing

## Current Phase
Tier B receiver capture + extraction-time receiver-type inference (schema v9 → v10).
- **Root cause (the real fix):** Tier A bridged the qualified/bare asymmetry at read time. Tier B fixes it at the source: the extractor now captures the raw receiver expression in `edges.receiver` (v9→v10 migration) AND infers its type to emit a qualified `Type.method` target on the edge itself. Once a call is stored as `Client.send`, it joins the symbol row `Client.send` exactly — no read-time bridging needed for that edge.
- **Schema v9 → v10:** single additive migration (`_run_migration_v9_to_v10`) adds `edges.receiver TEXT NULL`. Auto-runs on `connect()`; idempotent; never raises. Pre-v10 rows keep `receiver=NULL` (same null-contract as Phase 4/5 fields) — a full `seam init` re-index is needed to backfill receiver + qualified targets.
- **B1 — receiver column + Python receiver capture:** `edges.receiver` added to schema and `Edge` TypedDict. Python call-edge extractor captures raw receiver text (e.g. `self`, `client`) into `Edge.receiver`. Import and bare-identifier edges remain `receiver=None`.
- **B2 — receiver capture across remaining 11 languages:** all language extractors (TS/JS, Go, Rust, Java, C#, Ruby, C, C++, PHP, Swift) capture receiver text into `Edge.receiver` on attribute/member calls.
- **B3 — TS/JS member-expression call edges (recall hole fix):** previously only bare identifier calls were indexed for TypeScript/JavaScript; `obj.method()` patterns were silently dropped. B3 adds `member_expression call_expression` handling to the TS/JS extractor — a major recall improvement. Controlled by `SEAM_TYPE_INFERENCE` (on by default).
- **B4 — scope-inference module + Python/TS/JS receiver-type inference:** new leaf `seam/indexer/graph_scope_infer.py` provides `resolve_receiver_type()` — the core two-layer scope model (class-level field/property pre-scan + per-function param/local bindings) for Python and TS/JS. When a receiver type is confidently inferred, the extractor emits `target_name = "Type.method"` instead of the bare method name. Conservatism contract: NEVER emit a wrong edge — refuse on optionals, containers, generics, chained/unknown receivers.
- **B5 — receiver-type inference for remaining families + Swift static calls:** two more inference leaf modules (`graph_scope_infer_ext.py` for Java/C#/Ruby; `graph_scope_infer_ext2.py` for Go/Rust/C/C++/PHP) plus Swift static class call patterns (extension of `graph_swift_infer.py`).
- **B6 — instantiates edges across all 12 languages:** `new_expression` / struct-literal / composite-literal nodes now emit `kind="instantiates"` edges (e.g. `new Foo()`, `Foo{}`, `Foo { ... }`, PascalCase bare call in Swift). The `instantiates` kind is now part of the closed edge-kind vocabulary alongside `call`, `import`, `extends`, `implements`.
- **1 new config knob:** `SEAM_TYPE_INFERENCE: "on" | "off"` (default `"on"`) — master switch for extraction-time receiver-type inference in Python and TS/JS. Set to `"off"` to revert to bare-identifier-only targets (byte-identical to pre-Tier-B). Swift uses its own independent `SEAM_SWIFT_TYPE_INFERENCE` knob.
- **MCP tool count stays 11.** No new tools. Read path (Tier A names.py) consumes qualified targets automatically — no per-tool changes. Gate: all tests pass.
See `progress.txt`.

### Prior phase (Tier A name-resolution)
Tier A name-resolution (read-path-only bridge between qualified symbol names and bare call-edge targets).
- **Root cause fixed (read-path only, no schema change):** Seam stores method symbols as `Class.method` but call-edge `target_name` as the bare identifier `method`. This asymmetry caused every method context/impact to show empty upstream. Tier A patches this entirely at read time.
- **New leaf module `seam/query/names.py`:** five pure functions — `bare_name`, `is_container_symbol`, `get_member_names`, `edge_match_names`, `resolve_query_to_defs`, `expand_impact_seeds`. Imports only stdlib + `seam/config`. Pattern mirrors `seam/query/clusters.py`.
- **Slice 1 — qualified↔bare bridging in `engine.py` context():** edge lookups now search `[name, bare_name]` so a call stored as bare `method` joins against the qualified symbol `Class.method`.
- **Slice 2 — all-definitions aggregation:** `context()` resolves to ALL matching symbol defs (bare-name suffix scan via `resolve_query_to_defs`), not just the first homonym. A bare query `speakText` finds `TTS.speakText`, `AudioPlayer.speakText` etc. and merges their callers/callees. `ambiguous` flag is set when >1 definition is found.
- **Slice 3 — class→member expansion in `context()` + `query()`:** when the seed is a class/interface/struct, `edge_match_names` fans out to all member bare names so callers of any method of the class are included. Bounded by `SEAM_NAME_EXPANSION_CAP` (default 50).
- **Slice 4 — seed-expansion in `seam_impact` + `seam_trace`:** `expand_impact_seeds` provides the same qualified+bare (or class+members) seed list to the BFS `walk()`, so impact analysis now shows upstream callers for qualified method names and containers.
- **2 new config knobs:** `SEAM_NAME_EXPANSION_CAP` (default 50), `SEAM_BARE_RESOLVE_CAP` (default 25). No schema change, no migration, no re-index needed; MCP tool count stays 11. Gate: all tests pass.
See `progress.txt`.

### Prior phase (Semantic search)
- **New `[semantic]` extra** (`pip install 'seam-mcp[semantic]'`) — pulls `fastembed>=0.4` (ONNX/CPU, no torch). Base install unchanged; gate stays offline.
- **3 new modules:** `seam/analysis/embeddings.py` (fastembed wrapper), `seam/indexer/embedding_index.py` (index orchestration), `seam/query/semantic.py` (read path: RRF + cosine + model-mismatch guard).
- **Schema v6→v7:** new `embeddings(symbol_id PK, model, dim, vector BLOB)` table. Auto-migrated on `connect()`; no backfill — populated only by `seam init --semantic`.
- **5 new config knobs:** `SEAM_SEMANTIC` (off/on, default off), `SEAM_EMBED_MODEL` (default `BAAI/bge-small-en-v1.5`), `SEAM_SEMANTIC_LIMIT` (default 20), `SEAM_SEMANTIC_SCAN_CAP` (default 20000), `SEAM_RRF_K` (default 60).
- **Hybrid path in `engine.py`:** `search()` uses RRF-merged result set (FTS snippets preserved); `query()` injects semantic symbols as seeds (score=0.5) before 1-hop expansion. `_is_hybrid_enabled` check is per-query (one COUNT — negligible); warns once per process if `SEAM_SEMANTIC=on` but no embeddings.
- **CLI surfaces:** `seam init --semantic`, `seam sync --semantic`, `seam status` (embeddings row + model mismatch indicator), `seam search/query --no-semantic` (passes `semantic=False` param — no config mutation).
- **MCP transparent:** `seam_search`/`seam_query` auto-hybrid via engine.py. No new tool, count stays 11. Optional `semantic` param (default `true`) lets callers force keyword-only.
- **Benchmark:** `benchmarks/semantic_recall.py` (15 concept queries, 8 keyword-friendly + 7 vocabulary-gap), `make bench-semantic`. NOT part of gate — requires fastembed + model.
- **Gate:** 1747 tests, 5 skipped (real-model behind `pytest.importorskip("fastembed")`), 0 failed. Fully offline.
See `progress.txt`. Next: v0.1.0 — publish to PyPI as `seam-mcp`.

### Prior phase (CLI-only completion + optional-MCP install profile)
CLI-only completion + optional-MCP install profile.
- **3 new CLI commands** — `seam query` / `search` / `context` (seam/cli/read.py) over the existing
  transport-agnostic handlers; query SQLite directly → the FULL feature set is usable with NO MCP server.
- **`mcp` is now an OPTIONAL extra** (`[project.optional-dependencies] server`), not a core dep. `mcp` is
  imported lazily inside `start()`; `seam start` without it exits with an install hint. `pip install seam-mcp`
  = CLI only; `pip install 'seam-mcp[server]'` adds the server. (`mcp` kept in the dev group for tests.)
- **Distribution bug fixed (found via a real wheel install):** `seam init` read `docs/database/schema.sql`
  (outside the package) → crashed on any `pip install`. Schema now force-included at `seam/_data/schema.sql`,
  loaded packaged-first with a dev fallback. Guard test added.
- 1504 tests passing; gate green. Plan: `.claude/tasks/cli-query-context-search.md`.
See `progress.txt`. Next: v0.1.0 — publish to PyPI as `seam-mcp`.

### Prior phase
`seam install` (roadmap item 8) — one-command MCP wiring for Claude Code / Cursor / Codex.
- **New `seam/installer/` package** + `seam/cli/install.py`: `seam install` / `seam uninstall`.
  AgentTarget ABC; one target per agent. Claude → `.mcp.json` (project) / `~/.claude.json` `projects.<root>`
  (user), entry has `type:"stdio"`. Cursor → `.cursor/mcp.json` (no `type`). Codex → `~/.codex/config.toml`
  `[mcp_servers.seam]` (TOML via new dep `tomlkit`; user scope only).
- **Idempotent + safe:** deep-equal → `unchanged` (no write); atomic temp+rename; `.backup` on corrupt config;
  preserves other servers. `--target claude|cursor|codex|all`, `--location project|user`, `--print-config`, `--json`.
- Command written = absolute resolved `seam` path (via `sys.argv[0]`) + `["start", <root>]`. CLI-only — **no new
  MCP tool** (server stays read-only); tool count stays 10. No schema change, no migration.
- 1492 tests passing; gate green. Plan: `.claude/tasks/seam-install.md`.
See `progress.txt`. Next: v0.1.0 release prep — actually publish to PyPI as `seam-mcp`; add more agent targets
(one file each) as needed. Kotlin still parked behind a robust grammar.

### Prior phase
Agentic-readiness hardening (post-Phase-10) — 3 critical audit fixes.
- **Distribution renamed `seam` → `seam-mcp`** in pyproject (PyPI `seam` is taken by Seam Labs' SDK).
  Import package + console command stay `seam`. Not yet published; README install is from-source.
- **MCP error/not-found contract unified** via `_finalize` (seam/server/mcp.py): app errors now
  `isError=True` (`"CODE: message"`), not-found → `{"found": false}`. See the Known Gotchas entry.
- **`seam init` writes `.seam/.gitignore` (`*`)** so `seam_changes` stops reporting its own DB files.
- Source: an end-to-end agentic-readiness audit (real MCP stdio client on a fresh repo).

### Prior phase
Phase 10 complete — Swift support (11 → 12 languages). **Kotlin evaluated and deferred.**
- **New grammar:** tree-sitter-swift 0.7.3 (parses cleanly against tree-sitter 0.25.2, has_error=False).
  Entry point is `tree_sitter_swift.language()`.
- **Kotlin deferred:** the only available grammar (tree-sitter-kotlin 1.1.0) emits ERROR nodes on common
  constructs (interfaces, objects, classes-with-constructor) and recovered ~1 of 6 symbols on a realistic
  file — would silently drop most code. Revisit when a robust grammar ships. See ADR-009.
- **New extractor module:** graph_swift.py (mirrors graph_go_rust.py). class/struct/actor/extension→class,
  enum→type, protocol→interface, methods→Type.method; bare-identifier calls only; /// and /** */ docstrings.
- Swift wired into signatures_ext.py (visibility from access modifiers, @attributes as decorators) and
  imports_ext.py (import-mapping extraction; resolution returns [] — modules not file-resolvable in-repo).
- No schema change, no migration, MCP tool count stays 10.
- 1454 tests passing; gate green.
See `progress.txt` for session history. Next: roadmap item 8 (`seam install`) / v0.1.0 release prep.

### Prior phase
Phase 9 complete — language expansion (5 → 11 languages): Java, C#, Ruby, C, C++, PHP added.
- New grammars: tree-sitter-{java,c-sharp,ruby,c,cpp,php}; per-family extractor modules
  (graph_java_csharp.py, graph_c_cpp.py, graph_ruby.py, graph_php.py) mirroring graph_go_rust.py.
- New leaf modules signatures_ext.py + imports_ext.py (Phase 4 enrichment + Phase 5 import mappings
  for the new langs; TypedDicts re-declared to avoid circular imports, guarded by drift tests).
- Kind mapping uses the closed vocabulary; import + bare-identifier call edges only. See ADR-008.

### Prior phase
Phase 8 complete — lean output (`verbose`) + `seam_impact` summary tier shipped.
- **Lean output (#1):** `verbose: bool = True` on the enrichment-carrying handlers
  (seam_context, seam_trace, seam_impact, seam_context_pack). `verbose=False` strips the 6 heavy
  fields (decorators, is_exported, visibility, qualified_name, resolved_by, best_candidate) via
  the shared `_apply_verbosity` helper in tools.py — keeps signature + core fields. seam_search
  AND seam_query are enrichment-free → NO verbose flag (would be a no-op). CLI: `--lean` on
  impact/trace/pack (query/context have no CLI command — MCP-only).
- **Impact summary (#2):** seam_impact returns `risk_summary` {direction: {tier: count}} over the
  FULL pre-cap (post-include_tests) set, caps each tier at `SEAM_IMPACT_MAX_RESULTS` (default 25),
  reports `truncated` {direction: {tier: omitted}}, and accepts `limit` (0 = unlimited). The cap
  applies BY DEFAULT — this fixes the hub-symbol 30k-token blast (init_db: 30k → 4.5k tokens).
- All 3 CLI impact modes (--json/--quiet/Rich) route through `handle_seam_impact` so --lean/--limit
  apply uniformly; Rich shows a truncation footer, quiet signals truncation on stderr.
- No schema change, no migration, MCP tool count stays 10. Benchmark: 83.4%/77.6% → 91.8%/88.7%.
- 1107 tests passing; gate green.
See `progress.txt` for session history.

### Prior phase (Phase 7)
Phase 7 complete — one-shot `seam sync` with gated cluster recompute shipped.
- New leaf module `seam/indexer/sync.py`: `sync(conn, root, *, …) → SyncResult`.
- Filesystem reconcile (NOT git): mtime pre-filter → SHA-1 confirm; re-index only changed/added
  files, delete removed ones. Reuses walk_project + index_one_file + sha1 + delete_file.
- Delete is existsSync-guarded (roadmap §6.1): a tracked file is removed ONLY once it genuinely no
  longer exists on disk — a transient walk hiccup / wrong-dir / --db-dir mismatch can't wipe the index.
- FULL cluster recompute (clusters are global Louvain — no correct incremental update), GATED on
  `graph_changed = (added+modified+removed) > 0`; skipped when nothing changed. `--force-clusters`
  recomputes anyway (covers the live-watcher-already-indexed case → kills the stale-clusters gotcha).
- `cluster_count`: None = recompute skipped, -1 = recompute RAN but FAILED (index_clusters sentinel,
  surfaced as "failed" + warning, mirroring `seam init`), ≥0 = success. `clusters_recomputed` is
  True only on success.
- New CLI command `seam sync [path]` with --json / --quiet / --force-clusters. CLI-only —
  NO new MCP tool (MCP server stays read-only; tool count stays 10).
- No schema change, no new deps, no migration, no new config knobs (reuses SEAM_CLUSTER_*).
- 1031 tests passing; gate green.
See `progress.txt` for session history. Next: roadmap item 8 (`seam install`) / v0.1.0 release prep.

## MCP Tools
- `seam_query` — FTS5 + 1-hop graph expansion (Phase 0); OR-join + rescore since Phase 3; **hybrid semantic+FTS5 via RRF when `SEAM_SEMANTIC=on` and embeddings exist** (Semantic phase); optional `semantic: bool = True` param to force keyword-only
- `seam_context` — symbol 360-degree view, enriched with cluster_id/label/peers (Phase 2) + signature/decorators/is_exported/visibility/qualified_name (Phase 4); **Tier A: resolves bare/qualified/class names and aggregates all matching defs** (callers/callees merged across homonyms; `ambiguous=true` when >1 def found; class name fans out to all member callers)
- `seam_search` — full-text FTS5 search (Phase 0); OR-join + rescore + fuzzy fallback since Phase 3; signature is FTS-searchable (Phase 4); **hybrid semantic+FTS5 via RRF when `SEAM_SEMANTIC=on` and embeddings exist** (Semantic phase); optional `semantic: bool = True` param; FTS snippets preserved for FTS hits, "" for semantic-only hits
- `seam_impact` — blast-radius analysis by risk tier (Phase 1); each entry now carries `resolved_by` (provenance) and `best_candidate` (proximity pick on AMBIGUOUS) since Phase 5; Phase 8 adds `risk_summary` (full per-tier counts), a per-tier `limit` cap (default 25, 0=unlimited), and `truncated`; **Tier A: `expand_impact_seeds` bridges qualified↔bare and fans out class seeds to member names before BFS walk**; **Tier B: traverses `instantiates` edges alongside call/import/extends/implements; qualified Type.method targets resolve exactly**
- `seam_trace` — shortest call/dependency path (Phase 1); each hop now carries `resolved_by` and `best_candidate` since Phase 5; **Tier A: source/target seeds use the same qualified↔bare expansion as seam_impact**; **Tier B: hop `kind` may now be `instantiates`**
- `seam_changes` — git diff → changed symbols → risk level (Phase 1); --stdin on CLI
- `seam_why` — semantic comments WHY/HACK/NOTE/TODO/FIXME (Phase 1b)
- `seam_clusters` — list functional areas or drill into one cluster (Phase 2)
- `seam_affected` — changed files → impacted test files via reverse-dependency traversal (Phase 3)
- `seam_context_pack` — enriched context bundle: target + NeighborRef callers/callees + WHY + cluster peers + truncated counts (Phase 6)
- `seam_flows` — execution flows: list entry points (call-graph roots ranked by downstream reach), or expand one entry's depth/breadth-capped, cycle-safe forward call-chain tree (Flows). No arg → `{entry_points:[{name,kind,file,reach}]}`; with `entry` → a Flow tree (or `{found:false}`). Pure-structural, no LLM.

There are **eleven MCP tools** (`seam_flows` is the newest — see Flows below). The ten enrichment-carrying tools return the five Phase 4 enrichment fields where available: `signature`, `decorators`, `is_exported`, `visibility`, `qualified_name`. Fields are `null` (not absent) for pre-v5 rows or unsupported scenarios — callers treat `null` as "unknown". (`seam_flows` is the exception: its step shape is `name/kind/file/line/confidence` and it does NOT carry the Phase 4 fields.)

**Tier B edge enrichment:** The edge kind vocabulary now includes `instantiates` (added in Tier B B6) alongside `call`, `import`, `extends`, `implements`. `seam_impact` and `seam_trace` traverse `instantiates` edges. `seam_trace` hop `kind` may be `instantiates`. Edges with a confidently inferred receiver type now carry a qualified `Type.method` target directly in the DB — `seam_context` and `seam_impact` resolve these with higher confidence (EXTRACTED when unique, no read-time bridging needed for those hops). The raw receiver text is stored in `edges.receiver` (v10 column, NULL for pre-v10 rows and for bare/import edges).

**Semantic hybrid (Semantic phase):** `seam_search` and `seam_query` auto-merge FTS5 candidates with semantic (cosine) candidates via Reciprocal Rank Fusion (RRF, k=60) when BOTH conditions hold: `SEAM_SEMANTIC=on` AND embeddings exist for the configured model. No new MCP tool is added — tool count stays **11**. A keyword-only index behaves byte-identically to pre-Semantic. The `semantic` param (default `true`) can be passed to force keyword-only from a tool call.

**Phase 8 lean output:** `seam_context`, `seam_trace`, `seam_impact`, `seam_context_pack` accept `verbose: bool = True`. With `verbose=False` the 6 heavy fields (decorators, is_exported, visibility, qualified_name, resolved_by, best_candidate) are **absent** (not null) — `signature` + core fields are always kept. `verbose=True` is byte-identical to pre-Phase-8 (EXCEPT `seam_impact`, which always adds `risk_summary`/`truncated` and caps by default). `seam_query` and `seam_search` carry no enrichment → no `verbose` flag.

`seam_impact` and `seam_trace` additionally return `resolved_by` and `best_candidate` on each entry/hop since Phase 5. Both are `null` for pre-v6 rows or when resolution context is unavailable (same null-contract as Phase 4 fields).

`seam_context_pack` returns `truncated: {callers, callees, comments}` counts of entries dropped by caps. When a neighbor name has no indexed declaration it is silently skipped (not an error). Use `seam_impact` for the full blast radius when the pack is truncated.

## Known Gotchas
- **`edges.receiver` is NULL until `seam init` re-index after upgrading to v10**: the v9→v10 migration (auto-run on `connect()`) adds the `edges.receiver` column with `NULL` as the default. Existing edge rows keep `receiver=NULL` — same null-contract as the Phase 4/5 enrichment fields. Only a full `seam init` re-index populates `receiver` and upgrades bare call targets to qualified `Type.method` targets. Until then, qualified-target edges are absent and Tier A read-time bridging remains the only disambiguation.
- **Tier B inference is extraction-time only — changing `SEAM_TYPE_INFERENCE` requires re-index**: `SEAM_TYPE_INFERENCE=off` skips inference during extraction; switching it later has no retroactive effect. Run `seam init` to rebuild the index with the new setting. Toggling the knob at read time has no effect (the edges are already stored).
- **Conservatism contract — Tier B NEVER emits a wrong edge**: `resolve_receiver_type()` returns `None` (→ bare target kept) for optionals (`Foo | None`, `Foo?`, `Optional[Foo]`), containers (`list[T]`, `dict[K,V]`, `[Foo]`), generics (`Array<T>`, `Set<T>`), chained receivers (`a.b.c()`), and any identifier not found in the current scope. Only a plain user-type name that appears exactly in the class-field/param/local scope gets a qualified edge. The cost of a false negative (missed edge) is always lower than a false positive (wrong target).
- **TS/JS member-expression call edges (Tier B B3) require `seam init` re-index**: pre-B3 indexes have no `obj.method()` call edges for TypeScript/JavaScript (they were silently dropped). After upgrading to Tier B, run `seam init` to capture these edges. Until then, `seam_impact` / `seam_context` on TS/JS methods will under-report upstream callers.
- **`instantiates` edges require `seam init` re-index**: pre-B6 indexes have no `instantiates` edges. `new Foo()` / `Foo{}` / composite-literal calls appear as absent in the graph until re-indexed. The `instantiates` kind is traversed by `seam_impact` / `seam_trace` alongside `call` / `import` / `extends` / `implements`.
- **Tier A name-resolution is read-time-only**: the qualified↔bare bridging in `seam_context`, `seam_impact`, `seam_trace`, and `seam_query` is a pure read-path shim — it does NOT change how symbols or edges are stored. The extractor still writes method symbol names as `Class.method` and call-edge `target_name` as bare `method`. The bridge reconciles this at query time via `seam/query/names.py`. Once Tier B edges are indexed, these edges are already qualified — Tier A handles the remainder.
- **`ambiguous` flag semantics in `seam_context` (Tier A)**: before Tier A, `ambiguous=True` meant the name appeared in more than one file (cross-file collision). After Tier A, `ambiguous=True` also means a bare query resolved to multiple qualified definitions (e.g. querying `parse` found `Parser.parse` + `Lexer.parse`). In BOTH cases callers/callees are merged across ALL matching definitions. `ambiguous` signals "merged view — consider disambiguating with a qualified name or uid".
- **`SEAM_NAME_EXPANSION_CAP` (default 50) caps class→member fan-out**: when `seam_context`, `seam_impact`, or `seam_query` receives a class/interface/struct name, up to 50 member bare names are added to the edge lookup. Classes with >50 methods will silently have some members excluded from the fan-out; raise the cap via env var if precision matters more than query cost.
- **`SEAM_BARE_RESOLVE_CAP` (default 25) caps the bare-name suffix scan**: `resolve_query_to_defs` uses `LIKE '%.name'` which cannot use the B-tree index (full-table scan). The cap bounds the scan before the Python exact-suffix filter. Common identifiers like `run`, `get`, `parse` can match thousands of qualified symbols — without the cap this would be O(N) unbounded. Set to 0 for unlimited (not recommended on large codebases).
- **Clusters recomputed only on full `seam init` OR `seam sync` (Phase 7)**: the file *watcher*
  still does NOT recompute clusters after per-file edits — new symbols indexed by the live watcher
  get `cluster_id=NULL` until a recompute runs. `seam sync` now closes this: it recomputes clusters
  (gated on graph change) after reconciling. If the watcher already indexed your edits (so `seam sync`
  sees no on-disk drift → graph unchanged → recompute skipped), run `seam sync --force-clusters`
  (cheap — recomputes clusters without re-indexing files) or `seam init`.
- **`seam sync` is filesystem-reconcile, not git**: it detects changes by mtime + SHA-1 against the
  `files` table, so it works in non-git repos and catches pulled/merged/checked-out changes. Blind
  spot (same as CodeGraph): a content change that preserves mtime EXACTLY is missed — `seam init`
  (full re-index) is the escape hatch. A tracked file is deleted from the index only once it
  genuinely no longer exists on disk (existsSync guard) — a file the walk skipped but that still
  exists is kept, not removed.
- **`seam sync` requires an existing index**: it reconciles, it does not bootstrap. On a directory
  with no `.seam/seam.db` it errors `NO_INDEX` (run `seam init` first). It is CLI-only — there is no
  `seam_sync` MCP tool (the MCP server is read-only). A failed cluster recompute during sync surfaces
  as `cluster_count=-1` / `clusters_recomputed=false` / a "clusters: failed" warning (exit still 0 —
  the file reconcile succeeded); run `seam init` to rebuild clusters.
- **Homonym collapse**: the community detection graph is keyed on symbol NAME (not file+name),
  matching the `edges` table. Two files both defining a symbol named `helper` share one graph
  node — both get the same `cluster_id`. Visible in `clusters.size` (counts DB rows, not names).
- **SEAM_CLUSTER_MIN_SIZE default is 2**: pure singletons (symbols with no edges) are NOT
  persisted as clusters by default. Set to 1 to retain every symbol in its own cluster.
- **LLM naming is index-time only**: the MCP server read path is always 100% local.
  `SEAM_CLUSTER_NAMING=llm` only affects the `seam init` post-pass.
- **Search uses OR-join since Phase 3**: multi-term queries like `"parse issues board"` are
  built as `"parse"* OR "issues"* OR "board"*` so one non-matching word cannot zero the result.
  Results are re-ranked with name/path/test/cluster signals. If FTS returns zero rows a LIKE
  fallback runs, then a Damerau-Levenshtein fuzzy scan (up to SEAM_FUZZY_MAX_DIST=1 edit
  distance, capped at SEAM_FUZZY_MAX_CANDIDATES=500 symbols). A genuinely empty result from
  all three tiers still surfaces as an empty list — distinct from INVALID_QUERY.
- **`seam affected` uses the same edge graph as `seam impact`**: symbols not yet in the index
  (e.g. brand-new files before the next `seam init`) contribute zero dependents silently.
  Run `seam init` to refresh the index before running `seam affected` on new files.
- **`seam affected` depth cap**: traversal stops at SEAM_AFFECTED_DEPTH (default 5) hops.
  Raise via env var for deeper graphs. When a file has more symbols than SEAM_MAX_AFFECTED_SYMBOLS
  (default 50) the result carries `partial=true` — the affected set may be incomplete.
- **`--json` errors go to stdout, not stderr**: unlike CodeGraph (which emits ANSI errors on
  stderr even in JSON mode), Seam's `--json` mode always writes a structured envelope to stdout
  and exits non-zero. Shell pipelines and CI steps can branch on the `ok` key reliably.
- **MCP error contract ≠ CLI envelope — same code+message, different transport signal**:
  the CLI returns `{"ok":false,"error":{"code","message"}}`. The MCP tools (via `_finalize`
  in `seam/server/mcp.py`) instead **raise** on the handler's `{"error","message"}` sentinel so
  FastMCP sets `isError=True` with content `"<CODE>: <message>"` — because FastMCP only flips
  `isError` on a raise (returning a dict leaves `isError=False`, which an agent reads as success).
  A handler `None` ("nothing found") is normalized to `{"found": false}` (NOT empty content, NOT
  an error). Handlers/CLI/output.py are unchanged — only the MCP boundary normalizes.
- **`seam init` writes `.seam/.gitignore` (`*`)**: keeps the index (db/-shm/-wal) out of git so
  `seam_changes` never reports Seam's own artifacts as changed files. Written INSIDE `.seam/` —
  Seam still touches nothing outside `.seam/`. Idempotent (only written if absent).
- **Phase 4 enrichment fields are NULL until the next full `seam init` after upgrade**: the
  v4→v5 migration (run automatically on `connect()`) adds the five columns to the schema but
  does NOT backfill existing rows. Only a full re-index (`seam init`) populates signature,
  decorators, is_exported, visibility, and qualified_name for existing symbols.
- **`connect()` auto-migrates schema on open**: reads never break after a schema upgrade — the
  migration runs inline on the first `connect()` call. However, field values stay `null` for
  all rows that predate the re-index (see gotcha above).
- **Signature is FTS-searchable**: since Phase 4 the `symbols_fts` virtual table indexes
  `(name, docstring, signature)`. Type-shaped queries like `"conn sqlite3 Connection"` now
  match on parameter types and return annotations, not just symbol names.
- **`import_mappings` NOT backfilled by v5→v6 migration**: the v5→v6 migration (auto-run on
  `connect()`) creates the `import_mappings` table but does NOT populate it. `resolved_by`
  and import-promotion stay name-count-only until the next full `seam init`. Run `seam init`
  to enable Phase 5 resolution on an existing index.
- **Import promotion is read-time and requires `repo_root`**: `seam_changes` and `seam_affected`
  DELIBERATELY do not use import promotion — `changes.py` keeps name-count risk verdicts
  byte-stable across schema upgrades; `affected.py` does not read confidence at all.
  Import promotion applies only to `seam_impact`, `seam_trace`, and `seam_context`.
- **Go module-qualified imports are out of scope**: paths like `github.com/org/repo/pkg` are
  not resolved to indexed files. Go cross-package calls that use module-qualified import paths
  remain AMBIGUOUS if the target name has multiple declarations. Same-repo-relative Go paths
  resolve normally.
- **`.h` files always map to C, not C++** (Phase 9): SEAM_LANGUAGE_MAP routes `.h` → `"c"`.
  A C++-only project that puts declarations in `.h` headers parses those files with the C grammar,
  which handles most patterns (structs, typedefs, function prototypes) but misses C++-only constructs
  (templates, namespaces, in-class members). Use `.hpp`, `.hh`, or `.hxx` for C++ headers.
- **Nested classes have flat qualified names** (Phase 9): an inner class `Inner` inside `Outer`
  is indexed as `Inner` (not `Outer.Inner`), matching the existing Go/Rust precedent and the
  homonym-collapse gotcha. The edge graph is keyed on symbol name, so `Outer.Inner` would not match
  any edge target.
- **C++ pure-virtual method declarations are not extracted** (Phase 9): `virtual void f() = 0;`
  parses as `field_declaration` in the tree-sitter C++ grammar, not as `function_definition`. Only
  `function_definition` nodes are extracted. Concrete overriding implementations are indexed normally.
- **C function-pointer typedefs are not extracted** (Phase 9): `typedef int (*Cb)(int);` is silently
  skipped because the declarator is `abstract_function_declarator`, not `type_identifier`. Named-struct
  and enum typedefs (`typedef struct Foo Foo;`) are extracted correctly.
- **Java/C#/PHP import resolution returns `[]`** (Phase 9): import edges are extracted (e.g. `List`
  from `import java.util.List`) and stored in `import_mappings`, but `resolve_import_source()` returns
  `[]` for qualified package/namespace paths — classpath/NuGet/Composer layout is unavailable at index
  time. Cross-package Java/C#/PHP calls fall back to the name-count rule. Same-repo symbols whose name
  is unique in the index still resolve to EXTRACTED normally.
- **C/C++ system `#include <...>` resolution returns `[]`** (Phase 9): system headers like `<stdio.h>`
  produce an import edge with target `stdio`, but `resolve_import_source()` returns `[]` (no file found
  in the repo). These edges degrade to INFERRED/name-count at read time.
- **C++ visibility is null** (Phase 9): in-class access specifiers (`public:`, `private:`) are
  not yet threaded through to individual method symbols. All C++ symbols report `visibility=null`.
  Java, C#, and PHP visibility is extracted from access modifiers and is correct.
- **Ruby visibility is null** (Phase 9): Ruby's `private`/`protected` are method-call DSL constructs
  at runtime, not static AST nodes attached to `def`. Visibility cannot be determined statically without
  tracking which names appear after a `private` call — out of scope for this MVP.
- **On a multi-hop path, `resolved_by` reflects the FINAL hop**: path-level confidence uses
  the weakest-hop rule (AMBIGUOUS < INFERRED < EXTRACTED). `resolved_by` on the path entry
  reflects the provenance of the edge that produced the weakest-hop confidence, not of every
  hop individually.
- **Embeddings table is empty until `seam init --semantic`**: the v6→v7 migration (auto-run on
  `connect()`) creates the `embeddings` table but does NOT backfill it. Rows are populated
  only by `seam init --semantic` (or `seam sync --semantic`). Until then, `_is_hybrid_enabled`
  returns False and `seam_search`/`seam_query` behave byte-identically to pre-Semantic.
- **One-time model download on first `seam init --semantic`, then 100% local**: fastembed
  downloads the model (~67 MB for `BAAI/bge-small-en-v1.5`) on the FIRST `seam init --semantic`
  run; subsequent runs use the local fastembed cache at `~/.cache/huggingface/` (or the
  platform equivalent). The MCP read path (query embedding) never touches the network.
- **Changing `SEAM_EMBED_MODEL` requires a full `seam init --semantic` re-index**: vectors from
  different embedding models live in different metric spaces — mixing them silently corrupts
  cosine scores. When the stored model ≠ configured model, `semantic_candidates` detects the
  mismatch (COUNT WHERE model=? == 0), logs a WARNING, and returns `[]`. The engine falls
  through to pure-FTS5. Re-run `seam init --semantic` with the new model to rebuild.
- **`[semantic]` extra required**: `seam-mcp` base install does NOT include fastembed.
  Install with: `pip install 'seam-mcp[semantic]'` (or `uv sync --extra semantic`). When
  fastembed is absent, `is_available()` returns False, `index_embeddings` returns 0 (skipped),
  and the hybrid path degrades silently to FTS-only. An install hint is printed if `--semantic`
  is requested but fastembed is absent.
- **Gate skips real-model tests via `pytest.importorskip("fastembed")`**: all 5 skipped tests
  in the gate require the `[semantic]` extra (and would trigger a model download). They are
  skipped automatically when fastembed is not installed — the gate stays offline and fast.
  Synthetic vectors (`struct.pack` float32 blobs) are used for all other semantic tests.

## GitNexus: Code Intelligence (MCP)
This project is indexed. Use GitNexus MCP tools before coding on existing code.

**Decision rules:**
- SESSION START → read `gitnexus://repo/seam/context` first
- Understand a function/class → `context({name: "SymbolName"})`
- Find relevant code → `query({query: "keywords"})` before grep
- Before touching existing modules → query + context the affected area
- Before any refactor → `impact({target: "X", direction: "both"})` — do not skip
- Before committing → `detect_changes({scope: "all"})` to check risk level

**Re-index:** run `npx gitnexus analyze` when `gitnexus status` shows stale.
**Index location:** `.gitnexus/` (gitignored)

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **seam** (273 symbols, 293 relationships, 0 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/seam/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/seam/context` | Codebase overview, check index freshness |
| `gitnexus://repo/seam/clusters` | All functional areas |
| `gitnexus://repo/seam/processes` | All execution flows |
| `gitnexus://repo/seam/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
