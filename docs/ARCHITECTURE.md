# Architecture — Seam

> Phase 0 + Phase 1 + Phase 2 (clustering) + Phase 3 (agent-first interface) + Phase 4 (node-field enrichment) + Phase 5 (import resolution & confidence promotion). See ADRs in `docs/adr/` for decision rationale.

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
                         ├── seam_changes(scope)   → analysis.changes.detect_changes()
                         │         [Phase 1]
                         │
                         └── seam_clusters(id?)    → query.clusters.list_clusters()
                                                      / cluster_members()
                                   [Phase 2]
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

Main tables + FTS5 virtual table (schema v6):
- `files` — indexed files with hash + mtime
- `symbols` — functions, classes, methods; includes `cluster_id` FK (schema v4) and five Phase 4 enrichment columns: `signature`, `decorators`, `is_exported`, `visibility`, `qualified_name` (schema v5)
- `edges` — directed relationships (import, call) with `confidence` column (schema v2)
- `comments` — semantic comments: WHY/HACK/NOTE/TODO/FIXME markers (schema v3)
- `clusters` — community detection results: id, label, size, naming_source (schema v4)
- `symbols_fts` — FTS5 virtual table covering `symbols.name + docstring + signature` (signature added in schema v5)
- `import_mappings` — per-file import bindings (`local_name`, `exported_name`, `source_module`, `is_default`, `is_namespace`, `is_wildcard`, `line`); populated by pipeline + watcher per file; NOT backfilled by migration (schema v6)

See `docs/database/schema.sql` for full DDL.

### File Watcher
**File:** `seam/watcher/daemon.py`

Runs as a background thread/process alongside the MCP server. Uses watchdog's `Observer` + a custom `FileSystemEventHandler`. Debounces rapid saves to avoid thrashing (default 500ms). On trigger: re-parses the changed file, diffs symbols, updates DB.

### MCP Server
**Files:** `seam/server/mcp.py`, `seam/server/tools.py`

Stdio transport (no HTTP, no ports). The Python MCP SDK handles protocol framing. Nine tools exposed (Phase 0 + Phase 1 + Phase 1b + Phase 2 + Phase 3). Tool handlers in `tools.py` validate inputs and delegate to `query/engine.py`, `query/clusters.py`, or `analysis/`. Since Phase 4, `seam_context`, `seam_search`, and `seam_query` pass through the five enrichment fields from the engine layer unchanged. Since Phase 5, `seam_impact` and `seam_trace` additionally return `resolved_by` (provenance) and `best_candidate` (proximity pick on AMBIGUOUS entries) on each hop/entry.

### Query Engine
**File:** `seam/query/engine.py`

The read path. Three query types:
- **FTS5 search** — BM25-ranked full-text search across symbol names + docstrings + signature (signature added Phase 4)
- **Concept query** — FTS5 match + 1-hop graph expansion (connected symbols)
- **Context** — Direct lookup by symbol name + join to get callers/callees

Since Phase 4, all three functions include the five enrichment fields in their output TypedDicts (`ContextResult`, `SearchResult`, `QueryResult`). Pre-v5 rows carry `null` for those fields — callers treat `null` as "unknown".

### Clustering (Phase 2)
**Files:** `seam/analysis/clustering.py`, `seam/analysis/cluster_naming.py`, `seam/indexer/cluster_index.py`, `seam/query/clusters.py`

A post-pass that runs after the full `seam init` indexing loop. Never runs per-file or in the watcher.

- **clustering.py** — pure-Python Louvain greedy modularity maximization. Graph in (nodes + edges) → `{symbol_name: cluster_id}` out. No SQLite, no I/O. Deterministic: nodes sorted, tie-breaking by community label.
- **cluster_naming.py** — produces a human-readable label per cluster. Default ("deterministic"): `dominant_dir/file — highest_degree_symbol`. Opt-in ("llm"): calls an OpenAI-compatible endpoint via stdlib `urllib` only when `SEAM_CLUSTER_NAMING=llm` AND `SEAM_LLM_API_KEY` is set. LLM call is isolated and fail-safe (any error falls back to deterministic).
- **cluster_index.py** — orchestration bridge (indexer layer). Reads symbols + edges from the DB, calls detection + naming, writes `clusters` rows and `symbols.cluster_id` in one transaction. Returns -1 on error (not 0) so the CLI can distinguish "clustering failed" from "zero connected edges."
- **query/clusters.py** — read-only query layer. Exposes `list_clusters`, `cluster_members`, `cluster_peers`. Guards pre-v4 indexes (missing table/column) by returning empty results + one-time warning.

Clusters are keyed on symbol name (not row id), which means cross-file symbols with the same name collapse into one graph node (see ADR-007 — known, accepted limitation).

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
1. CLI: walk directory tree, collect files by SEAM_LANGUAGE_MAP extension
2. For each file:
   a. parser.parse_*(path) → tree-sitter Node
   b. graph.extract_symbols(node, language, path) → [Symbol]
   c. graph.extract_edges(node, language, path, symbols) → [Edge] with confidence tags
   d. graph.extract_comments(node, language, path) → [Comment]
   e. db.upsert_file(conn, path, symbols, edges, comments)
3. FTS5 index updated automatically via SQLite triggers
4. Clustering post-pass (whole-graph, after all files indexed):
   a. cluster_index.index_clusters(conn, ...) — reads all symbols + edges
   b. clustering.detect_communities(nodes, edges) → {name: cluster_id}
   c. cluster_naming.label_cluster(members, ...) → (label, naming_source) per community
   d. writes clusters table + symbols.cluster_id in one transaction
5. seam.db committed, watcher starts
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

## Constraints

- **No external services at runtime** — zero network calls in the MCP server read path. The opt-in LLM cluster naming (`SEAM_CLUSTER_NAMING=llm`) runs only during `seam init` (a build step, not the server), and falls back to deterministic labels on any error — the server is always 100% local.
- **No new runtime dependencies** — Phase 2 clustering is pure-Python Louvain with stdlib only; zero new packages added.
- **No process per project** — MCP server launched with `cwd` = project root; single binary
- **SQLite file size** — target <50MB for a 100k LOC codebase
- **Startup time** — `seam start` must be ready in <500ms after first `seam init`

---

## Phase 3 — Agent-First Interface

> Phase 3 shipped on branch `feat/phase3-agent-interface`. No schema migration required (v4 stays).
> Three vertical slices: search fix, CLI machine-readability, and the `affected` command/tool.

### Search Fallback Cascade (`seam/query/fts.py` + `seam/query/engine.py`)

**Problem being solved:** before Phase 3, `search()` and `query()` passed raw user text directly into
the FTS5 `MATCH` clause. FTS5 implicitly AND-joins space-separated terms, so a query like
`"parse issues board"` returned zero hits if `"board"` was not an indexed token — even though
`"parse"` matched many symbols. To an agent, zero hits looks like "this code doesn't exist."

**Fix:** a new pure leaf module `seam/query/fts.py` centralises query construction and rescoring.

`build_match_query(text) -> str`
- Strips FTS5 special characters and operator keywords (AND, OR, NOT, NEAR).
- Wraps each surviving token as a quoted prefix: `"token"*`.
- Joins with ` OR ` — one non-matching word cannot zero the query.
- Why OR and not AND: precision is recovered by `rescore()` after the fact; OR maximises
  recall so the agent gets _something_ to work with rather than an empty list.

`rescore(rows, terms) -> list`
- Applies five signals on top of the raw FTS BM25 base score (unbounded heuristic, never rendered as %):
  1. Exact name match: +80
  2. Prefix name match: +40
  3. Path relevance: +10 per query term appearing in the file path
  4. Test-file dampening: -30 when the query has no test-signal words and the result is a test file
  5. Cluster peer boost: +20 when the row shares `cluster_id` with the highest-scoring seed row
     (this signal is unique to Seam — CodeGraph has no cluster concept in its rescore).

`extract_terms(text) -> list[str]`
- Public single source of tokenisation shared by `build_match_query()` and `rescore()`.
  Before Phase 3, `engine.py` had a private duplicate; centralising here prevents drift.

**Fallback cascade in `engine.search()` and `engine.query()`:**

```
FTS5 OR-join MATCH
  → if zero rows: LIKE fallback (case-insensitive substring on symbol name)
    → if zero rows: Damerau-Levenshtein fuzzy scan over distinct symbol names
      (capped at SEAM_FUZZY_MAX_CANDIDATES symbols, max distance SEAM_FUZZY_MAX_DIST=1)
        → if still zero rows: return empty list
```

All three tiers feed `rescore()` before returning, so ranking is consistent regardless of
which tier produced the candidates. A genuinely empty result from all three tiers is still
distinct from `INVALID_QUERY` (bad FTS5 syntax raises `sqlite3.OperationalError` and is
caught and re-raised as the `INVALID_QUERY` error code before the fallback logic runs).

### CLI Output Envelope (`seam/cli/output.py`)

**Problem being solved:** every CLI command emitted Rich/ANSI markup. No `--json`, no `--quiet`,
no stdin support. An agent had to scrape ANSI-decorated output (fragile) or run the full MCP
server (heavy).

**Fix:** `seam/cli/output.py` is a pure leaf module that is the single source of truth for all
structured CLI output.

Explicit: the envelope is defined here, not scattered across commands:
- Success: `{"ok": true, "data": <payload>}`
- Failure: `{"ok": false, "error": {"code": "<STABLE_CODE>", "message": "<human text>"}}`
  + non-zero exit code, written to **stdout** (not stderr).

Why stdout for errors in `--json` mode: agents read stdout. Writing ANSI errors to stderr
(CodeGraph's behaviour) forces agents to multiplex two streams. Seam writes a parseable
envelope to stdout so the agent's stdout parser handles both success and failure paths with
one code path, and the non-zero exit code signals failure to shell pipelines independently.

`--json` and `--quiet` are mutually exclusive. `--quiet` emits bare line-oriented values
(dependent names, test-file paths, the risk level word) for direct piping.

**CLI/MCP parity:** when `--json` or `--quiet` is set, CLI read commands route through the
same `handle_seam_*` handlers that the MCP server uses (`seam/server/tools.py`). This means
the JSON payload inside the `data` envelope is byte-identical to what an MCP tool call would
return — one code path for agents regardless of whether they invoke Seam via MCP or shell.

**Commands with `--json`/`--quiet`:** `impact`, `trace`, `changes`, `why`, `clusters`, `status`, `affected`.
**Commands with `--stdin`:** `affected` (file list), `changes` (file list to narrow changed_symbols/new_files).

Stable error codes (single source in `output.py` docstring):

| Code | Trigger |
|------|---------|
| `NO_INDEX` | Index not found; run `seam init` first |
| `INVALID_INPUT` | Blank/missing required argument, oversized file list |
| `INVALID_QUERY` | Bad FTS5 syntax (rare since Phase 3 sanitization) |
| `NOT_A_GIT_REPO` | `changes` command outside a git repository |
| `DB_ERROR` | Database exists but cannot be opened (corrupted/locked) |

### Affected-Tests Analysis (`seam/analysis/affected.py` + `seam/server/tools.py`)

**Problem being solved:** Seam could tell an agent _what_ symbols were at risk (via `seam_impact`
and `seam_changes`) but not _which tests to run_. That last-mile, action-oriented answer is the
single most useful thing an agent wants before committing.

**New module `seam/analysis/affected.py`:**

`affected(conn, changed_files, *, depth, repo_root) -> AffectedResult`

Algorithm:
1. Resolve each input path to absolute (matching DB storage contract — indexer stores resolved paths).
2. For each changed file:
   - If the file is itself a test file (`is_test_file()`), include it directly.
   - Look up all symbols defined in the file.
   - For each symbol, call `impact(direction="upstream", max_depth=depth)` to find reverse dependents.
   - Collect dependent entries where `is_test=True` → add their files to the affected set.
3. Dedup and stable-sort the test file set (deterministic ordering for agents).

Why reuse `impact(direction="upstream")`: upstream dependents are exactly "who would break if
this changed" — which is also "who calls or imports this." The impact layer already carries
`is_test` per entry, so no duplicate traversal logic is needed (PRD user story 21).

`AffectedResult` TypedDict:
```python
{
  "changed_files":              list[str],  # resolved absolute paths of inputs
  "affected_tests":             list[str],  # sorted unique absolute paths of affected test files
  "total_dependents_traversed": int,        # total entries traversed (informational)
  "partial":                    bool,       # True when SEAM_MAX_AFFECTED_SYMBOLS cap was hit
}
```

`partial=True` means the per-file symbol cap (`SEAM_MAX_AFFECTED_SYMBOLS`, default 50) was hit
for at least one file; the affected set may be incomplete. Raise the cap via env var for
very large files.

**`seam_affected` MCP tool (9th tool, registered in `seam/server/mcp.py`):**

Input: `changed_files: list[str]`, optional `depth: int` (default `SEAM_AFFECTED_DEPTH=5`).
`handle_seam_affected` in `tools.py` clamps the file list to `SEAM_MAX_AFFECTED_FILES` (default 200)
and returns:
```json
{
  "changed_files": ["src/foo.py"],
  "affected_tests": ["tests/unit/test_foo.py"],
  "total_dependents_traversed": 12,
  "partial": false
}
```
All paths are relativized to the project root before returning (consistent with all other handlers).
Empty `changed_files` → `INVALID_INPUT` error. Files not in the index are silently skipped.

**CLI `seam affected` command:**

```bash
seam affected src/foo.py src/bar.py --json
seam affected src/foo.py --quiet               # bare test paths
git diff --name-only | seam affected --stdin --quiet | xargs pytest
```

`--stdin` and positional arguments are mutually exclusive (explicit error if both are supplied).
`--quiet` emits one test-file path per line for direct piping into `pytest` or `xargs`.

### System Diagram Update (Phase 3)

The MCP server section of the system diagram now has a 9th tool:

```
                         ├── seam_affected(files) → analysis.affected()
                                   [Phase 3]
```

And the query engine read path now passes through the FTS fallback cascade before returning:

```
FTS5 OR-MATCH → LIKE fallback → fuzzy scan → rescore() → results
```

---

## Phase 4 — Node-Field Enrichment

> Phase 4 shipped on branch `feat/phase4-node-enrichment`. Schema migrated v4 → v5.
> No new CLI command or MCP tool — three existing tools enriched.

### Extraction Data Flow

```
seam init
  For each source file:
    a. parser.parse_*(path)                        → tree-sitter Node
    b. graph.extract_symbols(node, language, path) → [Symbol]  ← calls extract_node_fields()
       └─ signatures.extract_node_fields(node, language, qualified_name, max_sig_len)
              → NodeFields{signature, decorators, is_exported, visibility, qualified_name}
    c. db.upsert_file() writes the five enrichment columns alongside existing fields
  FTS5 triggers fire on INSERT/UPDATE — 'signature' column now included in symbols_fts
  Read path (MCP call):
    engine.context / search / query → SELECT includes all five columns
    tools.py passes them through to the MCP response unchanged
```

### New Leaf Module: `seam/indexer/signatures.py`

A pure leaf (imports only stdlib + `tree_sitter`; does NOT import any `seam.*` module).
Single public entry point:

```python
extract_node_fields(node, language, qualified_name=None, max_signature_len=300) -> NodeFields
```

Per-language rules:
- **Python** — `def name(params) -> return_type` / `class Name(Bases)`; decorators from `decorated_definition` siblings; export = no leading `_`.
- **TypeScript/JavaScript** — `function name(params): return_type` / `class Name<T> extends B`; decorators from prev-sibling walk; export = `export_statement` parent; visibility from access-modifier children.
- **Go** — `func (recv) Name(params) result` / `type Name kind`; export = capitalized first letter; decorators always `[]`.
- **Rust** — `pub fn name(params) -> type` / `pub struct Name`; visibility from `visibility_modifier`; `pub(crate)` → `"crate"`; decorators always `[]`.

Never raises — any extraction error returns `_safe_defaults()` (all `null`/`[]`).

### Schema v5 Migration

**`db.py:_run_migration_v4_to_v5()`** — guarded additive migration:
1. `ALTER TABLE symbols ADD COLUMN signature TEXT` (× 5 for all five columns)
2. `DROP TABLE symbols_fts` — existing FTS virtual table lacks the `signature` column
3. Recreate `symbols_fts` with `(name, docstring, signature)` columns
4. `INSERT INTO symbols_fts(symbols_fts) VALUES ('rebuild')` — repopulate from `symbols` table
5. Row-count parity check: aborts and rolls back if FTS row count ≠ symbols row count
6. Bumps `metadata.schema_version` to `'5'`

Wrapped in `BEGIN IMMEDIATE` so concurrent readers are not blocked mid-migration (WAL mode).

**Auto-migrate on connect:** `db.connect()` calls `_apply_pending_migrations()` on every open. Pre-v5 indexes are transparently upgraded on the first `seam start` or `seam_*` call after package upgrade. Callers do not need to re-run `seam init` just to avoid breakage — field values will be `null` until they choose to re-index.

### FTS5 Signature Search

The `symbols_fts` virtual table now indexes three columns: `name`, `docstring`, `signature`.
A type-shaped query like `"conn sqlite3 Connection"` now matches on parameter and return-type
annotations. `fts.rescore()` applies a **+15 signature-match signal** per matched term — smaller
than name-exact (+80) and name-prefix (+40) so signature-only matches rank below name matches.

### Config Knob

`SEAM_MAX_SIGNATURE_LEN` (env var, default `300`) — hard cap on stored signature length.
Signatures longer than this are truncated with `...`. Callers can detect truncation by checking
for a trailing `...`. The default captures the full header of all but pathological multi-annotated
functions; the cap prevents FTS index bloat and unwieldy MCP responses.

---

## Phase 5 — Import Resolution & Confidence Promotion

> Phase 5 shipped on branch `feat/phase5-import-resolution`. Schema migrated v5 → v6.
> No new MCP tool — `seam_impact` and `seam_trace` output gained `resolved_by` + `best_candidate`.

### Why Read-Time

Seam's existing confidence model stores a per-edge `confidence` column written at index time, but
treats that stored value as a non-authoritative hint. The authoritative tier is recomputed against
the live whole-index name-count map on every query (see `seam/analysis/confidence.py`). Phase 5
extends this same model: import mappings are stored at index time, but the promotion decision is
made at read time. This keeps the watcher's per-file re-index correct without back-fill or
write-amplification — after the watcher re-indexes an edited file's import mappings, the next
query resolves against fresh state automatically.

### Resolution Order (`resolve_edge`)

`seam/analysis/confidence.py:resolve_edge()` applies a four-step ordered rule:

```
1. Import promotion (step A):
   If SEAM_IMPORT_RESOLUTION="on" AND a same-file import maps target_name to
   exactly one indexed declaring file → EXTRACTED, resolved_by: import.

2. Name-count rule:
   count == 1 → EXTRACTED, resolved_by: name-unique.
   count >  1 → AMBIGUOUS, resolved_by: name-collision
                + proximity best_candidate (step D, bounded by SEAM_PROXIMITY_MAX_CANDIDATES).

3. Builtin check (fires ONLY at count == 0):
   If SEAM_BUILTIN_FILTERING="on" AND is_builtin(name, language)
   → INFERRED, resolved_by: builtin.
   (Structural guarantee: a user-declared name with count >= 1 can never reach step 3.)

4. Fallback:
   count == 0, not a builtin → INFERRED, resolved_by: unresolved.
```

Step 3's `count==0` guard is load-bearing — it enforces that a user-defined symbol is never
silently treated as a builtin, regardless of what the builtin vocabulary contains.

### New Leaf Modules

**`seam/analysis/imports.py`** — the import-resolution engine. Three public functions:

- `extract_import_mappings(root, filepath, language) -> list[ImportMapping]` — parse a file's
  AST and return all import bindings as typed records. Per-language dispatch covers Python (all
  `import`/`from ... import` forms, relative imports, aliases), TypeScript/JavaScript (named,
  default, namespace, aliased imports), Go (grouped and single `import` declarations), and Rust
  (`use` declarations including scoped lists, wildcards, and aliases). Never raises — returns
  `[]` on any failure.
- `resolve_import_source(source_module, referencing_file, repo_root, language) -> list[str]` —
  map an import source string to existing file paths using per-language extension resolution
  order (Python: `.py`/`/__init__.py`; Rust: `.rs`/`/mod.rs`; TS: `.ts`/`.tsx`/`.d.ts`/`.js`;
  JS: `.js`/`.mjs`/`.cjs`/`/index.js`; Go: package directory). Relative sources resolve from
  the referencing file's directory. Third-party and unresolvable sources return `[]`.
- `compute_path_proximity(referencing_file, candidate_file) -> int` — shared directory segment
  count between two files' parent directories. Pure, no I/O. Used for step D tie-break.

**`seam/analysis/builtins.py`** — curated builtin vocabulary. Single public function:

- `is_builtin(name, language) -> bool` — over static per-language `frozenset`s covering Python,
  TypeScript, JavaScript, Go, and Rust. Conservative scope: well-known builtins/prelude/globals
  only — not an exhaustive stdlib mirror. Language-scoped: a Python builtin name does not
  affect Go or Rust edges.

### `import_mappings` Table (Schema v6)

Populated by `pipeline.py` in the same file-processing pass that writes symbols and edges.
The watcher refreshes mappings per-file using the same delete-then-insert pattern as
`upsert_file` — incremental edits stay fresh without a full re-index.

The table is NOT backfilled by the v5→v6 migration. On an existing index, `connect()` adds the
table (additive, fresh-DB-safe) but rows are empty until `seam init` runs. Until then, every
`resolve_edge()` call silently falls back to the name-count rule (step 2 above), which is
identical to pre-Phase-5 behavior. This mirrors the Phase 4 backfill caveat.

### Schema v6 Migration

`db.py:_run_migration_v5_to_v6()` — additive, guarded:
1. `CREATE TABLE IF NOT EXISTS import_mappings (...)` with two indexes (`file_id`, `local_name`).
2. Bumps `metadata.schema_version` to `'6'`.

No column additions to existing tables — purely additive. Fresh DBs are seeded at v6 directly.

### Config Knobs (Phase 5)

| Knob | Default | Purpose |
|------|---------|---------|
| `SEAM_IMPORT_RESOLUTION` | `"on"` | Master switch for step A import promotion |
| `SEAM_BUILTIN_FILTERING` | `"on"` | Master switch for step C builtin tagging |
| `SEAM_MAX_IMPORT_CANDIDATES` | `25` | Cap on candidate declaring files per import lookup |
| `SEAM_PROXIMITY_MAX_CANDIDATES` | `25` | Cap on collision candidates for step D proximity ranking |

---

## Phase 6 — Context-Pack Primitive

> Phase 6 shipped on branch `feat/phase6-context-pack`. **No schema change** — pure read-time
> orchestration over existing primitives.

### Why a New Leaf Module Instead of Extending `engine.py`

`seam/query/pack.py` deliberately adds *no* extraction or schema. It composes three existing
read primitives into one bundle so an agent makes a single call instead of chaining
`seam_context` + `seam_why` + a `seam_context` per neighbor. Keeping it in its own module makes
that "orchestration only" boundary explicit and keeps the already-large `engine.py` focused on
the core search/query/context path.

### Bundle Assembly (`context_pack`)

```
context_pack(conn, symbol_name) -> ContextPack | None
  target        ← engine.context(conn, symbol_name)            (verbatim; None ⇒ not found)
  callers/callees ← target["callers"/"callees"] names, ENRICHED via _enrich_neighbors()
  why           ← comments.why(conn, symbol=symbol_name)        (capped)
  cluster_peers ← target["cluster_peers"]                       (no extra query)
  truncated     ← {callers, callees, comments} dropped BY CAPS
```

### Neighbor Enrichment (`_enrich_neighbors`)

1. **Deduplicate** the neighbor names (order-preserving).
2. **Batched lookup** in chunks of `_SQLITE_MAX_IN_PARAMS` (900) — one `WHERE name IN (...)`
   per chunk, merged. Chunking exists because SQLite's default host-parameter limit is 999; a
   hot symbol with thousands of distinct neighbors would otherwise raise `OperationalError` and
   silently return an empty list. Each name appears in exactly one chunk, so merging is a dict
   update. `GROUP BY name` + `MIN(s.id)` gives a deterministic first-match-per-name.
3. **Per-file cap** (`SEAM_PACK_PER_FILE_CAP`, default 3) applied first, in lowest-symbol-id
   order, so one file's homonyms cannot flood the bundle (the §4.5a diversity mitigation).
4. **Global cap** (`SEAM_PACK_NEIGHBOR_LIMIT`, default 10 per list) applied to the diverse list.
5. **`truncated`** counts ONLY cap drops — per-file + global. A neighbor name with no symbol row
   (external/unindexed) is skipped and logged at debug, but does NOT inflate `truncated`, because
   a larger cap would never retrieve it (distinct from "the bundle was clipped").

The `decorators` JSON + `is_exported` 0/1/NULL decode is shared with `engine.context()` via the
extracted `engine.decode_enrichment_fields(row)` helper — one decode contract, two callers.

### Contract Parity

`context_pack` returns `None` for an unknown symbol — the same contract as `engine.context()`.
Both the MCP tool (`handle_seam_context_pack`) and the CLI (`seam pack`) surface a missing symbol
as a **success** envelope (`{found: false, symbol}`), not an error — mirroring `seam_context`.
No new error code is introduced. Both entry points route through the same handler, so the bundle
is byte-identical between MCP and CLI.

### New Leaf Module + 10th Tool

- `seam/query/pack.py` — `context_pack()`, `ContextPack`/`NeighborRef`/`TruncatedCounts` TypedDicts.
- `seam_context_pack` registered in `seam/server/mcp.py` — the 10th MCP tool.
- `seam pack <symbol>` CLI command in `seam/cli/main.py` (`--json` / `--quiet`).

### Config Knobs (Phase 6)

| Knob | Default | Purpose |
|------|---------|---------|
| `SEAM_PACK_NEIGHBOR_LIMIT` | `10` | Max enriched callers and max enriched callees per bundle |
| `SEAM_PACK_PER_FILE_CAP` | `3` | Max neighbor entries from any single file (homonym diversity) |
| `SEAM_PACK_MAX_COMMENTS` | `10` | Max WHY comments included in the bundle |

## Phase 7 — One-Shot `seam sync` with Gated Cluster Recompute

> Phase 7 shipped on branch `feat/phase7-seam-sync`. **No schema change, no migration, no new
> config knobs** — pure orchestration over the existing indexing primitives and the `files` table's
> existing `mtime` + `file_hash` columns.

### Why a New Leaf Module (`seam/indexer/sync.py`)

`seam sync` is the *one-shot* complement to the always-on watcher daemon and the full `seam init`.
The reconcile logic lives in `indexer/sync.py` (not `cli/main.py`) so the import hierarchy stays
`cli → indexer.sync → {indexer.pipeline, indexer.db, indexer.cluster_index}` and the engine is
testable without Typer. It adds **no extraction logic** — it composes `walk_project`,
`index_one_file`, `sha1`, `delete_file`, and `index_clusters`.

### Reconcile Data Flow (`sync`)

```
files table ──┐                       walk_project(root) ──┐
  {path: (mtime, hash)}                  current on-disk set
              │                                            │
              └────────────► per file classify ◄───────────┘
                                   │
   not tracked ──────────────► index_one_file → added   (None → skipped)
   tracked, st_mtime == stored ─► UNCHANGED (no read — cheap pre-filter)
   tracked, mtime differs ─► read + sha1
        hash == stored ─────► UNCHANGED (touch without content change; no re-index)
        hash != stored ─────► index_one_file → modified (None → skipped)
   tracked, absent from walk set AND not exists() ─► delete_file → removed
                                   │
                          graph_changed = (added + modified + removed) > 0
                                   │
   recompute_clusters and (graph_changed or force_clusters) ─► index_clusters (FULL pass)
```

**Why mtime → hash (not git):** filesystem reconcile catches non-git repos *and* committed changes
from pull/checkout/merge/rebase (git bumps mtime, so the cheap pre-filter still fires). The accepted
blind spot — a content change that preserves mtime exactly — is escaped by a full `seam init`.

**Why the `exists()` guard on delete (roadmap §6.1):** trusting the walk set alone to decide
deletions means a transient FS/permission hiccup, a wrong-directory sync, or a `--db-dir` pointed at
another project's index would silently delete *every* tracked file. A tracked path is removed only
once it genuinely no longer exists on disk; a path the walk merely skipped (still present) is kept.

### Why a FULL, Gated Cluster Recompute

Seam's clusters are a **global** Louvain partition over the name-keyed edge graph — one new edge can
re-partition unrelated communities, so there is no correct *incremental* cluster update (this is the
key divergence from CodeGraph, which has no clustering). `seam sync` therefore runs the **same
whole-graph `index_clusters`** that `seam init` runs, but **gated** on `graph_changed or
force_clusters`. A no-op sync skips it entirely — no Louvain cost, no churned cluster IDs.
`--force-clusters` covers the case where the live watcher already indexed edits into `files` (so
sync sees no on-disk drift) but left clusters stale.

`index_clusters` returns its documented `-1` sentinel on failure (it never raises). `sync` preserves
that in `cluster_count` and sets `clusters_recomputed = cluster_count >= 0`, so a failed recompute is
**not** reported as success — the CLI renders it as `clusters: failed` + a warning, mirroring
`seam init`'s `clustering_failed` guard. `cluster_count` is therefore three-valued: `None` (skipped),
`-1` (ran but failed), `≥0` (succeeded).

### CLI-Only, Read-Only MCP Preserved

`seam sync` is a maintenance/write command and joins `init`/`start`/`status` as **CLI-only** — there
is no `seam_sync` MCP tool, so the MCP server read path stays 100% local and read-only (tool count
stays 10). It requires an existing index (`connect()`, not `init_db()`); on a directory with no
`.seam/seam.db` it returns `NO_INDEX`. The `--json` / `--quiet` output flows through the same
`seam/cli/output.py` envelope as the read commands; `--quiet` emits `key: value` lines (one per
field) rather than the read commands' bare single-value form, because the 8-field `SyncResult` would
be ambiguous as bare positional values.
