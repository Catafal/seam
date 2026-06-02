# Project: Seam

## What This Is
Local code intelligence MCP server — indexes codebases with tree-sitter, stores in SQLite, exposes `seam_query`, `seam_context`, `seam_search` via MCP so AI agents query instead of grep.

## Tech Stack
- Python 3.14+ | uv 0.9.14
- tree-sitter 0.25.2 + tree-sitter-python 0.25.0 + tree-sitter-typescript 0.23.2 + tree-sitter-go 0.25.0 + tree-sitter-rust 0.24.2 + tree-sitter-java 0.23.5 + tree-sitter-c-sharp 0.23.5 + tree-sitter-ruby 0.23.1 + tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4 + tree-sitter-php 0.24.1
- mcp 1.27.2 (stdio transport) | watchdog 6.0.0 | typer 0.26.4 | tomlkit 0.15.0 (Codex install config)
- SQLite + FTS5 (built-in, no ORM) | pytest 9.0.3 | ruff 0.15.15 | mypy 2.1.0

## Commands
- `make gate` — Full verification (lint + typecheck + tests) — **run before every commit**
- `make install-dev` — Install all deps including dev
- `make fmt` — Format + fix lint (not part of gate)
- `uv run seam init` — Index current directory
- `uv run seam sync` — Incrementally reconcile the index (changed/added/removed files) + gated cluster recompute
- `uv run seam start` — Start MCP server + watcher
- `uv run seam status` — Show index stats
- `uv run seam install` — Write the MCP config into an agent (`--target claude|cursor|codex|all`,
  `--location project|user`, `--print-config`); `uv run seam uninstall` reverses it

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
seam/installer/              ← `seam install`/`uninstall` engine (CLI-only; NO MCP tool)
                                __init__.py: TARGETS registry {claude,cursor,codex} + resolve_seam_command()
                                core.py: AgentTarget ABC + InstallResult + shared idempotent JSON merge
                                jsonfile.py (LEAF, stdlib json) — Claude/Cursor; tomlfile.py (LEAF, tomlkit) — Codex
                                claude.py/.mcp.json+type:stdio · cursor.py/.cursor/mcp.json · codex.py/~/.codex/config.toml
seam/cli/install.py          ← `seam install`/`uninstall` Typer commands (registered onto app in main.py)
seam/cli/main.py             ← Typer CLI (init, sync, start, status, impact, trace, changes, why, clusters,
                                affected, pack, install, uninstall)
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
seam/query/clusters.py       ← cluster read queries (Phase 2): list_clusters, cluster_members,
                                cluster_peers; guards pre-v4 indexes
seam/query/pack.py           ← LEAF: context_pack(conn, symbol_name) → ContextPack | None
                                orchestrates context()+why() into one enriched bundle; applies caps from config
                                ContextPack: target, callers, callees (NeighborRef), why, cluster_peers, truncated
seam/server/tools.py         ← MCP tool handlers (thin adapters → engine + clusters + pack)
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
`seam install` complete (roadmap item 8) — one-command MCP wiring for Claude Code / Cursor / Codex.
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
- `seam_query` — FTS5 + 1-hop graph expansion (Phase 0); OR-join + rescore since Phase 3
- `seam_context` — symbol 360-degree view, enriched with cluster_id/label/peers (Phase 2) + signature/decorators/is_exported/visibility/qualified_name (Phase 4)
- `seam_search` — full-text FTS5 search (Phase 0); OR-join + rescore + fuzzy fallback since Phase 3; signature is FTS-searchable (Phase 4)
- `seam_impact` — blast-radius analysis by risk tier (Phase 1); each entry now carries `resolved_by` (provenance) and `best_candidate` (proximity pick on AMBIGUOUS) since Phase 5; Phase 8 adds `risk_summary` (full per-tier counts), a per-tier `limit` cap (default 25, 0=unlimited), and `truncated`
- `seam_trace` — shortest call/dependency path (Phase 1); each hop now carries `resolved_by` and `best_candidate` since Phase 5
- `seam_changes` — git diff → changed symbols → risk level (Phase 1); --stdin on CLI
- `seam_why` — semantic comments WHY/HACK/NOTE/TODO/FIXME (Phase 1b)
- `seam_clusters` — list functional areas or drill into one cluster (Phase 2)
- `seam_affected` — changed files → impacted test files via reverse-dependency traversal (Phase 3)
- `seam_context_pack` — enriched context bundle: target + NeighborRef callers/callees + WHY + cluster peers + truncated counts (Phase 6)

All ten tools return the five Phase 4 enrichment fields where available: `signature`, `decorators`, `is_exported`, `visibility`, `qualified_name`. Fields are `null` (not absent) for pre-v5 rows or unsupported scenarios — callers treat `null` as "unknown".

**Phase 8 lean output:** `seam_context`, `seam_trace`, `seam_impact`, `seam_context_pack` accept `verbose: bool = True`. With `verbose=False` the 6 heavy fields (decorators, is_exported, visibility, qualified_name, resolved_by, best_candidate) are **absent** (not null) — `signature` + core fields are always kept. `verbose=True` is byte-identical to pre-Phase-8 (EXCEPT `seam_impact`, which always adds `risk_summary`/`truncated` and caps by default). `seam_query` and `seam_search` carry no enrichment → no `verbose` flag.

`seam_impact` and `seam_trace` additionally return `resolved_by` and `best_candidate` on each entry/hop since Phase 5. Both are `null` for pre-v6 rows or when resolution context is unavailable (same null-contract as Phase 4 fields).

`seam_context_pack` returns `truncated: {callers, callees, comments}` counts of entries dropped by caps. When a neighbor name has no indexed declaration it is silently skipped (not an error). Use `seam_impact` for the full blast radius when the pack is truncated.

## Known Gotchas
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
