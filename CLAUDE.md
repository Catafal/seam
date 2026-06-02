# Project: Seam

## What This Is
Local code intelligence MCP server — indexes codebases with tree-sitter, stores in SQLite, exposes `seam_query`, `seam_context`, `seam_search` via MCP so AI agents query instead of grep.

## Tech Stack
- Python 3.14+ | uv 0.9.14
- tree-sitter 0.25.2 + tree-sitter-python 0.25.0 + tree-sitter-typescript 0.23.2 + tree-sitter-go 0.25.0 + tree-sitter-rust 0.24.2
- mcp 1.27.2 (stdio transport) | watchdog 6.0.0 | typer 0.26.4
- SQLite + FTS5 (built-in, no ORM) | pytest 9.0.3 | ruff 0.15.15 | mypy 2.1.0

## Commands
- `make gate` — Full verification (lint + typecheck + tests) — **run before every commit**
- `make install-dev` — Install all deps including dev
- `make fmt` — Format + fix lint (not part of gate)
- `uv run seam init` — Index current directory
- `uv run seam start` — Start MCP server + watcher
- `uv run seam status` — Show index stats

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
                                SEAM_LANGUAGE_MAP: .py .ts .tsx .js .mjs .cjs .go .rs
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
seam/cli/main.py             ← Typer CLI (init, start, status, impact, trace, changes, why, clusters, affected)
                                --json / --quiet on read commands; --stdin on affected + changes
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
seam/indexer/parser.py       ← tree-sitter parsing (Python, TypeScript, JavaScript, Go, Rust)
seam/indexer/graph_common.py ← LEAF: shared TypedDicts (Symbol/Edge/Comment), helpers
                                Symbol now carries: signature, decorators, is_exported, visibility, qualified_name
seam/indexer/graph_go_rust.py← Go + Rust extractors (imports graph_common only)
seam/indexer/graph.py        ← Python/TS dispatchers; re-exports types from graph_common;
                                imports Go/Rust extractors from graph_go_rust
seam/indexer/signatures.py   ← LEAF: Phase 4 enrichment — extract_node_fields(node, language, ...) → NodeFields
                                per-language: signature, decorators, is_exported, visibility, qualified_name
                                for Python / TypeScript / JavaScript / Go / Rust; never raises
seam/analysis/imports.py     ← LEAF: extract_import_mappings + resolve_import_source + compute_path_proximity
                                per-language import extraction for Python/TS/JS/Go/Rust; never raises
                                maps import source strings to candidate declaring-file paths (5-lang extension order)
seam/analysis/builtins.py    ← LEAF: is_builtin(name, language) → bool over static per-language frozensets
                                covers Python/TS/JS/Go/Rust; conservative vocabulary (common builtins only)
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
seam/server/tools.py         ← MCP tool handlers (thin adapters → engine + clusters)
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
Phase 5 complete — import resolution and confidence promotion shipped.
- Import-promotion (step A): when a same-file import binds a target name to exactly one indexed
  declaring file, the edge is promoted to EXTRACTED with `resolved_by: import` — the core homonym fix.
- Provenance (`resolved_by`): every resolved edge now reports how its tier was decided.
  Stable vocabulary: `import` | `name-unique` | `name-collision` | `builtin` | `unresolved`.
- Builtin/stdlib filtering (step C): count==0 names that are known language builtins are tagged
  `resolved_by: builtin` (INFERRED) instead of `unresolved`. Guard: the builtin check fires ONLY at
  count==0, so a user `def get()` is never silently filtered as the builtin `get`.
- Proximity tie-break (step D): residual AMBIGUOUS edges (collision, no import to resolve) report a
  `best_candidate` — the most file-path-proximate declaring file — so agents have a most-likely target.
- Schema v6: new `import_mappings` table stores per-file import bindings; `connect()` auto-migrates
  v5→v6 on open (additive, fresh-DB-safe, no backfill — run `seam init` to populate).
- 939 tests passing; gate green.
See `progress.txt` for session history. Next: benchmarking / v0.1.0 release prep.

## MCP Tools
- `seam_query` — FTS5 + 1-hop graph expansion (Phase 0); OR-join + rescore since Phase 3
- `seam_context` — symbol 360-degree view, enriched with cluster_id/label/peers (Phase 2) + signature/decorators/is_exported/visibility/qualified_name (Phase 4)
- `seam_search` — full-text FTS5 search (Phase 0); OR-join + rescore + fuzzy fallback since Phase 3; signature is FTS-searchable (Phase 4)
- `seam_impact` — blast-radius analysis by risk tier (Phase 1); each entry now carries `resolved_by` (provenance) and `best_candidate` (proximity pick on AMBIGUOUS) since Phase 5
- `seam_trace` — shortest call/dependency path (Phase 1); each hop now carries `resolved_by` and `best_candidate` since Phase 5
- `seam_changes` — git diff → changed symbols → risk level (Phase 1); --stdin on CLI
- `seam_why` — semantic comments WHY/HACK/NOTE/TODO/FIXME (Phase 1b)
- `seam_clusters` — list functional areas or drill into one cluster (Phase 2)
- `seam_affected` — changed files → impacted test files via reverse-dependency traversal (Phase 3)

All nine tools (seam_context, seam_search, seam_query) return the five Phase 4 enrichment fields where available: `signature`, `decorators`, `is_exported`, `visibility`, `qualified_name`. Fields are `null` (not absent) for pre-v5 rows or unsupported scenarios — callers treat `null` as "unknown".

`seam_impact` and `seam_trace` additionally return `resolved_by` and `best_candidate` on each entry/hop since Phase 5. Both are `null` for pre-v6 rows or when resolution context is unavailable (same null-contract as Phase 4 fields).

## Known Gotchas
- **Clusters recomputed only on full `seam init`**: the file watcher does NOT recompute
  clusters after per-file edits. New symbols after `seam init` get `cluster_id=NULL` until
  the next full re-index. Run `seam init` again to refresh clusters.
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
