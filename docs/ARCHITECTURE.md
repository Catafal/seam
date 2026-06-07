# Architecture — Seam

> Phase 0 + Phase 1 + Phase 2 (clustering) + Phase 3 (agent-first interface) + Phase 4 (node-field enrichment) + Phase 5 (import resolution & confidence promotion). See ADRs in `docs/adr/` for decision rationale.

---

## System Diagram

```
Source files (Python, TypeScript, JavaScript, Go, Rust, Java, C#, Ruby, C, C++, PHP, Swift)
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

Stdio transport (no HTTP, no ports). The Python MCP SDK handles protocol framing. Ten tools exposed (Phase 0 + Phase 1 + Phase 1b + Phase 2 + Phase 3 + Phase 6). Tool handlers in `tools.py` validate inputs and delegate to `query/engine.py`, `query/clusters.py`, or `analysis/`. Since Phase 4, `seam_context`, `seam_search`, and `seam_query` pass through the five enrichment fields from the engine layer unchanged. Since Phase 5, `seam_impact` and `seam_trace` additionally return `resolved_by` (provenance) and `best_candidate` (proximity pick on AMBIGUOUS entries) on each hop/entry.

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
      — synthesized edges (synthesized_by IS NOT NULL) are EXCLUDED here to avoid
        feedback pollution (synthesis runs AFTER clustering and its edges persist)
   c. cluster_naming.label_cluster(members, ...) → (label, naming_source) per community
   d. writes clusters table + symbols.cluster_id in one transaction
5. Edge-synthesis post-pass (whole-graph, runs AFTER clustering — gated):
   a. synthesis_index.index_synthesis(conn, ...) — reads all symbols + edges
   b. synthesis.py (A2 interface→impl override fan-out) + synthesis_channels.py
      (A1a closure-collection, A1b event-emitter) compute synthesized edges
   c. writes them with kind='call', confidence='INFERRED', synthesized_by=<channel>
      in ONE transaction under a synthetic ':synthesis:' files row (idempotent
      delete-then-insert). Never raises; returns -1 on failure (surfaced as "failed").
   — In `seam init` this always runs; in `seam sync` it is GATED on graph_changed
     (or --force-synthesis), exactly like the cluster recompute. NOT run by the watcher.
   — Master switch SEAM_EDGE_SYNTHESIS (default on); "off" skips this step entirely.
6. seam.db committed, watcher starts
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

Per-language rules (original 5 — `seam/indexer/signatures.py`; Phase 9 additions — `seam/indexer/signatures_ext.py`):
- **Python** — `def name(params) -> return_type` / `class Name(Bases)`; decorators from `decorated_definition` siblings; export = no leading `_`.
- **TypeScript/JavaScript** — `function name(params): return_type` / `class Name<T> extends B`; decorators from prev-sibling walk; export = `export_statement` parent; visibility from access-modifier children.
- **Go** — `func (recv) Name(params) result` / `type Name kind`; export = capitalized first letter; decorators always `[]`.
- **Rust** — `pub fn name(params) -> type` / `pub struct Name`; visibility from `visibility_modifier`; `pub(crate)` → `"crate"`; decorators always `[]`.
- **Java** (Phase 9) — full declaration header; decorators = Java annotations from `modifiers` node (`@Service`, `@Override`); export/visibility from `public`/`private`/`protected` modifier.
- **C#** (Phase 9) — full declaration header; decorators = C# attribute lists (`[Serializable]`); export/visibility from access modifier (`public`/`private`/`protected`/`internal`).
- **Ruby** (Phase 9) — `def name(params)` / `class Name`; decorators always `[]`; visibility = `null` (dynamic DSL); is_exported = `null`.
- **C** (Phase 9) — return type + declarator; decorators always `[]`; `static` storage class → `visibility="private"`, `is_exported=false`; otherwise `is_exported=true`.
- **C++** (Phase 9) — return type + declarator; decorators always `[]`; visibility = `null` (MVP — access specifiers not threaded to individual symbols); is_exported = `null`.
- **PHP** (Phase 9) — full declaration header; decorators = PHP attribute lists (`#[Route(...)]`); export/visibility from `public`/`private`/`protected` modifier.
- **Swift** (Phase 10) — declaration header; decorators = Swift `@attributes` (`@objc`, `@available`); visibility/is_exported from access modifiers (`public`/`open` → exported; `private`/`fileprivate`/`internal` → not exported).

Never raises — any extraction error returns safe defaults (all `null`/`[]`).

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

## Phase 8 — Lean Output + `seam_impact` Summary Tier

> Phase 8 shipped on branch `feat/phase8-lean-output`. **No schema change, no migration, no new
> tools** — pure output-shaping at the serialization layer (`seam/server/tools.py`). Motivated by
> the benchmark re-run that showed enrichment had narrowed the token win and that `seam_impact` on
> a hub symbol cost more than reading the files.

### Lever 1 — Lean output (`verbose`)

A single shared helper, `_apply_verbosity(record, verbose)`, is applied at the return edge of every
enrichment-carrying handler (`seam_context`, `seam_trace`, `seam_impact`, `seam_context_pack`),
including the nested records (trace hops, impact tier entries, context_pack neighbors).

- `verbose=True` (default) returns the **same dict object** unchanged — a zero-copy fast path that
  keeps output byte-identical to pre-Phase-8 (the callers build records inline and never mutate
  them, so returning the original is safe).
- `verbose=False` returns a **new dict** without the 6 heavy keys (a single module-level
  `_HEAVY_FIELDS` frozenset is the sole canonical list): `decorators`, `is_exported`, `visibility`,
  `qualified_name`, `resolved_by`, `best_candidate`. `signature` + all core identity fields are kept.

`seam_query` and `seam_search` are **enrichment-free** (their results carry no heavy fields), so they
deliberately have **no** `verbose` flag — advertising a no-op flag would mislead callers. The win
concentrates where records repeat the heavy fields: `seam_trace` ≈ −40%; `seam_context` only ≈ −1–2%
(its heavy fields sit on the single target, not the bare-name caller/callee lists).

### Lever 2 — `seam_impact` summary tier + per-tier cap

`handle_seam_impact` computes `risk_summary` = `{direction: {tier: count}}` from the raw result
**before** capping (so the histogram is trustworthy regardless of `limit`), then slices each tier to
`limit` (default `SEAM_IMPACT_MAX_RESULTS=25`; `limit<=0` = unlimited) and records `truncated` =
`{direction: {tier: omitted}}`. The kept slice is the closest/highest-risk: WILL_BREAK (d=1) and
LIKELY_AFFECTED (d=2) are single-distance tiers, and MAY_NEED_TESTING (d=3..max_depth) arrives in
the analysis layer's BFS (ascending-distance) order, so `entries[:limit]` keeps the closest. The cap
applies **by default** — this is the one place a default-output change is justified, because the
prior default (~30k tokens for `init_db`, worse than grep) was actively harmful. `risk_summary`
keeps totals honest, `truncated` signals the omission, and `limit=0` restores the full set.
`risk_summary` counts the post-`include_tests`-filter, pre-cap set (production-only when
`include_tests=False`), matching the entries actually returned.

### CLI parity — all three impact modes through one handler

`seam impact`'s `--json`, `--quiet`, and default **Rich** modes all route through
`handle_seam_impact` (previously Rich called `impact()` directly and silently ignored `--limit` /
`--lean` — a confirmed parity bug). A shared `_IMPACT_META_KEYS` frozenset is skipped by every
result iterator (quiet output, the total-entry count, Rich rendering) so the new `risk_summary` /
`truncated` dicts are never mistaken for direction groups (the count path would otherwise call
`len()` on an `int`). Rich mode prints a per-direction truncation footer; quiet mode writes a
truncation signal to **stderr** so `stdout` stays a pure bare-name list for pipelines.

## Roadmap P2–P6 — Graph Quality, Resolution & Stable Handles

> Shipped on branch `feat/roadmap-p2-p6`. Schema migrated v7 → v9 (additive). **No new MCP tool
> — count stays 11.** Each feature is gated by a defaulted-on switch; turning the switch off
> restores byte-identical pre-P* behavior.

### P6a — Class inheritance as graph edges (`seam/indexer/graph*.py`)

The dependency graph previously captured only `call` and `import` edges, so a base-class or
interface change had **no upstream blast radius** — its subclasses were invisible to `seam_impact`.
P6a extracts a subclass→base `extends` edge and a class→interface `implements` edge for Python,
TypeScript, Java, and C#. Because the `edges` table is **name-keyed and homonym-collapsed**, every
base/interface reference is normalized to its bare rightmost type name (generic args and
namespace/package qualifiers stripped) by the shared `_base_type_name` helper in `graph_common.py`,
matching how call/import targets are stored. Edges carry `confidence='INFERRED'` (a base name is a
type reference, not a resolved-in-file symbol) and flow through the **existing** impact/trace
traversal unchanged — no new traversal code. Gated by `SEAM_INHERITANCE_EDGES` (`"on"` default).

### P6b — Framework entry-point scoring (`seam/analysis/processes.py`, schema v9)

Raw downstream-reach ranking buries a framework's true entry points: a Flask route or Django view
often delegates to one service call (shallow reach) yet *is* where execution begins. `compute_entry_score(file_path, decorators)` returns a small multiplier (≥1.0, neutral baseline 1.0)
from two cheap, language-agnostic signals — the file **path** pattern (`views.py`, `routes/`,
`controllers/`, `cmd/`, …) and the symbol's **decorator** text (`@app.route`, `@router.get`,
`@GetMapping`, …) — taking the MAX matching multiplier (not a sum). It is **pure and never raises**:
bad input → 1.0.

The score is computed **at index time** in `upsert_file` and persisted to the new
`symbols.entry_score` column (schema v9). At read time, `list_entry_points` ranks by
`entry_score * reach` while still reporting the **raw** `reach` (the multiplier is a ranking signal
only). `_load_entry_scores` mirrors `_load_meta`'s lowest-id-wins homonym-collapse rule, and a NULL
score (pre-v9 / un-reindexed row) is treated as the neutral 1.0. `SEAM_ENTRY_SCORE=off` forces the
baseline for every symbol → byte-identical to raw-reach ranking.

### P3 — tsconfig aliases + go.mod module prefix (`seam/analysis/imports_resolve.py`)

Import promotion (Phase 5) could not resolve two common cross-file forms, leaving them falsely
`AMBIGUOUS`. A new leaf module — split from `imports.py` purely to stay under the 1000-line cap —
adds, all **index/read-time, cached once per `repo_root`, never-raise**:
- **TS/JS path aliases** — `_load_tsconfig_aliases` reads `tsconfig.json` / `jsconfig.json`
  `compilerOptions.paths` + `baseUrl` into a longest-prefix-first map; `_resolve_ts_alias` expands a
  non-relative specifier (`@/foo`) to real files **before** the third-party fallback in
  `resolve_import_source`.
- **Go module prefix** — `_load_go_module` reads the `module <path>` line from `go.mod`; a
  module-qualified import starting with that prefix is stripped to a repo-relative directory and
  resolved normally. Imports outside the prefix (true third-party) still correctly return `[]`.

### P4 — Barrel re-export chasing (`seam/analysis/confidence.py`)

A named import through a barrel (`export { X } from './x'` in an `index.ts`) resolves to the barrel
file, which does **not** declare `X` — so Phase 5's "resolved file must declare the name" guard
correctly refused to promote, but the edge stayed `AMBIGUOUS`. `_resolve_with_import_promotion` now,
when the resolved candidate does not declare the exported name, calls `_chase_barrel`: it follows
that file's **own** `import_mappings` for the name, resolving each re-export source (with a TS/JS
directory→`index.*` fallback in `_resolve_barrel_source`) and recursing until a single declaring
file is found — up to `SEAM_BARREL_DEPTH` (default `3`) hops. **Bounded, cycle-safe** (a `visited`
set of `(file, name)` pairs guards termination and avoids repeat DB hits), and it stops on a
branch to multiple declarers (genuine ambiguity). `SEAM_BARREL_DEPTH=0` disables it entirely.

### P2 — Cluster quality (`seam/indexer/cluster_index.py`, `cluster_naming.py`, schema v8)

Three changes make Louvain communities read as real functional areas:
1. **Confidence-filtered edges (large graphs only).** `_should_filter_edges(symbol_count)` gates on
   `SEAM_CLUSTER_CONFIDENCE_FILTER` (default `1000`): when the graph is large enough that homonym
   `AMBIGUOUS` edges would wrongly merge unrelated modules, only high-trust edges (EXTRACTED, or
   import-kind INFERRED) are passed to `detect_communities`. Small/sparse repos pass the full set
   (those AMBIGUOUS edges are often the only connective tissue). `"off"` never filters; `"0"` always.
2. **Two-level labels.** `_module_dir_for_path` walks a member file's path from leaf upward and
   returns the first **non-generic** directory, skipping packaging scaffolding (`GENERIC_DIRS` =
   `src`/`lib`/`app`/`pkg`/`main`/`core`/`base`) — so `render/src/widget.py` labels as `render`.
3. **Cohesion (schema v8 `clusters.cohesion`).** `_compute_cohesion` = internal-edge / total-edge
   ratio over a deterministic sample (≤50 members per cluster, a perf bound on hub clusters),
   computed from the **full unfiltered** edge graph so the score reflects real connectivity. It
   feeds a deliberately tiny additive search-rank bonus (`seam/query/fts.py`) that only nudges
   ordering among otherwise-equal results.

### P5 — Swift inter-class call resolution (`seam/indexer/graph_swift.py`)

Swift call edges were bare-identifier only. P5 adds **function-scope-local** receiver-type inference
for two high-value member-call patterns, resolved to qualified `Type.method` edges at index time:
`self.method()` → `<EnclosingType>.method`, and `Foo().method()` or a same-scope
`let x = Foo(); x.method()` → `Foo.method` (tracked via a per-function `var→class` dict during the
AST walk — no cross-file inference). `SEAM_SWIFT_TYPE_INFERENCE=off` reverts to bare-identifier
edges. See ADR-009.

### P6c — Stable symbol UID handle (`seam/server/tools.py`, `seam/query/engine.py`)

A homonym follow-up (search → context) otherwise forces an agent to re-disambiguate by file path —
an extra round-trip. `compute_uid(file_path, start_line)` = `sha1(abs_path)[:8] + ':' + line` is a
**pure computed string** surfaced on every `seam_search` / `seam_query` result (computed from the
ABSOLUTE path *before* relativization, so it round-trips). `seam_context` / `seam_impact` /
`seam_trace` accept it as an alternative to the name argument (`uid`, plus `target_uid` on trace).
Resolution (`_resolve_uid`) narrows by `start_line` in SQL — cheap, no schema change, no O(files)
scan — then recomputes the UID over each candidate's absolute path until one matches.
`engine.context_at(file, line)` powers the exact-symbol context path (vs. `context()`'s first-by-
name); the impact/trace graphs are name-keyed, so a UID there is resolved to its symbol NAME. An
unknown/stale UID returns the standard not-found result, never an error.

---

## Tier D11 — `seam_structure`: Whole-Repository Structure View

> Tier D11 shipped on branch `feat/tier-d11-structure-view`. **No schema change, no migration, no
> new config schema version** — pure read over the existing `symbols` + `files` + `clusters` tables.
> MCP tool count goes from **11 → 12** (`seam_structure` is the 12th tool).

### Why a Physical Structure View Alongside Clusters

Seam already exposes `seam_clusters` (semantic community view), which groups symbols by call/import
coupling. `seam_structure` adds the complementary **physical container map**: the filesystem
hierarchy with symbol counts and cluster area labels per node. The two views answer different
questions:

| View | Primary question | Organisation |
|------|-----------------|--------------|
| `seam_clusters` | "What is logically coupled to X?" | Semantic communities (Louvain) |
| `seam_structure` | "Where does X live? What else is in that file/dir?" | Filesystem hierarchy |

An agent understanding a repo for the first time needs the physical map first; the cluster view
provides a second, semantic cut. Both views share the same cluster `area` label, so a node in the
structure tree annotated `area: "auth"` is the Louvain community a maintainer would recognise.

### New Module: `seam/query/structure.py`

Leaf read module. Single public function:

```python
build_structure(conn, root, *, path=None, max_depth=None, max_nodes=None) -> StructureResult
```

Algorithm:
1. Fetch all `(file_path, name, kind, start_line)` rows in one `JOIN files` query — O(symbols),
   no per-file queries.
2. Partition each file's symbols into containers (class/interface/type), members (method or
   qualified `Owner.member`), and top-level functions. Members roll up into their owning
   container's `members` count — they are NOT emitted as separate tree nodes, keeping the
   skeleton compact.
3. Build the dir → file → container/function tree by navigating path parts, creating dir nodes
   on demand via a `rel_path → StructureNode` dict (O(1) lookup per dir).
4. Annotate each file node with a functional `area` label drawn from the cluster the plurality
   of that file's symbols belong to. Dir nodes inherit the plurality of their direct children's
   areas (bottom-up propagation after tree assembly).
5. Apply Slice 3 bounds (depth cap → node cap), accumulate dropped counts into `truncated`.

Never raises — returns a valid empty tree on any error.

**Node shape:**
```
StructureNode:
  kind:         'dir' | 'file' | 'container' | 'function'
  name:         display name (dir basename, file name, symbol name)
  path:         repo-root-relative string; null for container and function nodes
  symbol_count: total symbols in this subtree (file: row count; dir: sum of children)
  area:         cluster label (null when no cluster data exists)
  children:     child StructureNodes
  members:      method/member count rolled into this container (0 for non-containers)
```

### Depth and Node Caps — Non-Obvious Semantics

**`depth` counts ALL tree levels, not only directory nesting.** Root is depth 0; its immediate
children are depth 1; a file directly under root is depth 1; its containers are depth 2. In
practice a codebase with two directory levels, files, and containers occupies depths 0–4. The
default `SEAM_STRUCTURE_MAX_DEPTH=8` is deliberately generous so containers survive for typical
repo layouts (3–5 dirs + 1 file level + 1 container level = 5–7 total). Maintainers who set a
small `--depth` (e.g. `--depth 2`) should expect containers to be truncated even for top-level
files, because file→container occupies depth 2 from root.

**`SEAM_STRUCTURE_MAX_NODES <= 0` means UNLIMITED** — matching the `seam_impact limit=0`
convention used throughout Seam. Setting `--nodes 0` or `SEAM_STRUCTURE_MAX_NODES=0` disables
the node cap entirely (the full tree is returned). A negative value has the same effect; the
guard in `_apply_node_cap` is `if max_nodes <= 0: return 0`. This avoids the footgun where an
operator setting the knob to `-1` or `0` silently receives an empty tree instead of an
uncapped one.

**BFS drop order (node cap):** when the node cap is reached, the algorithm drops *all* children of
the current node together rather than including partial sibling groups. Including 3 of 5 containers
in a file would imply the file has fewer symbols than it does. The whole-group drop keeps
`symbol_count` and `members` values honest on surviving nodes.

### New 12th MCP Tool: `seam_structure`

Registered in `seam/server/mcp.py`. Parameters:

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `path` | `str \| null` | `null` | Scope to a subdirectory (relative resolves against repo root, not server cwd) |
| `depth` | `int \| null` | `SEAM_STRUCTURE_MAX_DEPTH` (8) | Max tree depth (counts dir+file+container levels — see note above) |
| `nodes` | `int \| null` | `SEAM_STRUCTURE_MAX_NODES` (2000) | Max non-root nodes; 0 = unlimited |

Returns `{found: false}` when the tree has no symbols (empty index, or scope matches nothing).
All file paths in the response are root-relative; container nodes carry `path: null`.

### New CLI Command: `seam structure`

```bash
seam structure                        # Rich tree with branch glyphs (default)
seam structure --json                 # JSON envelope {"ok":true,"data":{tree,truncated}}
seam structure --quiet                # plain indented text (one node per line)
seam structure /path/to/repo          # inspect a specific project
seam structure --scope src/           # scope to the src/ subdirectory
seam structure --depth 3              # max depth 3 (note: includes file+container levels)
```

Implemented in `seam/cli/main.py` with two render helpers: `_render_structure_quiet` (plain text
for piping) and `_render_structure_rich` (branch glyphs + colour, mirroring `seam flows`). Both
helpers show the `area` label on dir and file nodes when present.

### System Diagram Update (Tier D11)

The MCP server section now has a 12th tool:

```
                         ├── seam_structure(path?, depth?, nodes?)
                         │           → query.structure.build_structure()
                                   [Tier D11]
```

### Config Knobs (Tier D11)

| Knob | Default | Purpose |
|------|---------|---------|
| `SEAM_STRUCTURE_MAX_DEPTH` | `8` | Max tree depth (counts dir+file+container levels, not dirs only) |
| `SEAM_STRUCTURE_MAX_NODES` | `2000` | Max non-root nodes; `<= 0` = unlimited |

---

## Composition ("holds") Edges

> Shipped on branch `feat/composition-holds-edges`. **No schema migration** — `"holds"` is a new
> value in the existing `edges.kind TEXT` column, not a new column or table. No read-path or
> traversal code was changed. MCP tool count stays 12.

### What "holds" captures

A `holds` edge records **class composition**: a class or struct that stores a typed user-defined
value as a named field/property, or receives one as a typed constructor/init parameter, emits:

```
Edge(source=OwningClass, target=HeldType, kind="holds", confidence="INFERRED")
```

Examples that produce a `holds` edge:
- Python `self.client: Client = client` (typed field assignment)
- TypeScript `private db: Database` (typed property declaration)
- Go `type Server struct { store Store }` (typed struct field)
- Java `private final UserRepo repo;` (typed class field)
- Swift `var engine: Engine` (stored property declaration)

Examples that do NOT produce a `holds` edge:
- Method parameter types (transient; not stored composition)
- Local variable type annotations (not stored on the class)
- Return types
- Optional/container/generic wrappers (`Client | None`, `list[Client]`, `Array<Client>`)
- Primitive types and builtins (`int`, `string`, `bool`, filtered via `is_builtin()`)
- Dotted-qualified type names (conservative: cross-package names not resolved)

### Why this is useful

Before composition edges, `seam_impact` on a data-model class showed only call-graph reach:
"who calls methods of this class?" Composition adds the structural dimension: "who *is* this
class, structurally?" — i.e., which owning classes embed it as a stored field. Changing the
constructor signature or field layout of `Client` now surfaces `Server` (which holds a `Client`)
in the blast radius at d=1, not just callers of `Client.send`.

### Per-family collector design

Composition scanning reuses the existing class-level pre-scans in the inference leaf modules —
the same AST pass that builds the `var_types` dict for receiver-type inference also collects
stored field/property types. No new AST traversal is added.

| Leaf module | Languages | AST nodes scanned |
|-------------|-----------|-------------------|
| `graph_scope_infer.py` | Python, TypeScript/JS | class body attribute defs, property declarations |
| `graph_scope_infer_ext.py` | Go, Rust | struct field declarations |
| `graph_scope_infer_ext2.py` | Java, C#, C++, Ruby, PHP | field/member declarations |
| `graph_swift_infer.py` | Swift | stored property declarations |

Each collector returns `list[tuple[str, str]]` — `(field_name, type_name)` pairs. The extractor
in the parent `graph_*.py` module converts each pair into a `holds` edge, applying the
conservatism contract before emitting.

### Conservatism contract

`holds` edges apply the **same conservatism rules** as receiver-type inference (Tier B):

- **Plain user type only**: `resolve_plain_type(type_text) → str | None` strips whitespace and
  returns `None` for optionals (`X | None`, `X?`, `Optional[X]`), containers (`list[X]`,
  `dict[K,V]`, `[X]`), generics (`Array<X>`, `Set<X>`), and dotted qualified names (`pkg.Type`).
- **Builtin filter**: `is_builtin(type_name, language)` gates emission — no `Class holds int`
  or `Class holds string` noise enters the graph.
- **INFERRED confidence**: composition is a structural reference, not a resolved-import link.
  `holds` edges always carry `confidence="INFERRED"`. This mirrors `extends`/`implements` edges
  (P6a) which also express structural relationships at INFERRED confidence.
- **Never raises**: any extraction failure silently returns an empty list; the extractor skips
  gracefully (same contract as all parsers in the pipeline).

### Traversal — automatic, no new code

The traversal layer (`seam/analysis/traversal.py`) is **kind-agnostic**: it walks all edges
regardless of `kind`. Adding `"holds"` to `edges.kind` therefore flows through `seam_impact`,
`seam_context`, and `seam_trace` automatically — if you change `HeldType`, `OwningClass`
appears upstream at d=1 with no traversal code changes.

### Config knob

`SEAM_COMPOSITION_EDGES: "on" | "off"` (default `"on"`) — master switch for composition-edge
emission at extraction time. Set to `"off"` to suppress all `holds` edges; byte-identical to
pre-composition behaviour. Like all other extraction-time knobs (`SEAM_TYPE_INFERENCE`,
`SEAM_INHERITANCE_EDGES`), toggling takes effect only on the next `seam init` re-index —
the existing stored edges are not retroactively removed.

---

## A3 — Field-Access Edges (`reads` / `writes`) + Field Symbols

> Shipped on branch `feat/field-access-edges`. **No schema migration** — `"reads"`, `"writes"`,
> and `"field"` are new values in the existing `edges.kind TEXT` and `symbols.kind TEXT` columns.
> Extraction-time, per-file, watcher-compatible. MCP tool count stays 12.
> `seam_context` gains `field_readers` and `field_writers` in its output.

### The visibility gap A3 closes

Before A3, the call graph captured invocations but not data-flow through stored fields. A field
read (`obj.url`) or write (`obj.url = x`) had no edge — so renaming `Config.url` or changing
its type produced zero upstream results from `seam_impact`. A3 adds per-access-site edges that
make field data-flow as visible as method control-flow.

### New edge kinds: `reads` and `writes`

Edge kind vocabulary grows from 6 to **8**:

```
call | import | extends | implements | instantiates | holds | reads | writes
```

| Kind | When emitted | Mode detection |
|------|-------------|---------------|
| `reads` | `obj.field` appears as an expression rvalue | Default — any non-write access |
| `writes` | `obj.field = x`, `obj.field += x`, `del obj.field` | LHS of assignment, augmented-assign, or `del` |

All field-access edges carry `confidence='INFERRED'`. The edge `source` is the enclosing symbol
(function/method); the `target` is the field name (bare or `Type.field` when the receiver type is
inferred).

### Fields/properties as first-class symbols

`symbols.kind` gains `'field'`. A class field/property is now indexed as:

```
Symbol(name='Client.url', kind='field', qualified_name='Client.url', ...)
```

This is additive — no migration needed, no column added. Field symbols participate in FTS5 search,
`seam_context`, and `seam_impact` exactly like method symbols. Existing tooling that treats `kind`
as a closed enum must be updated to handle `'field'`.

### Conservatism contract (same as Tier B)

Receiver type resolution follows the same two-layer scope model as `resolve_receiver_type`:

1. `self`/`this`/`cls` → resolved to the enclosing class → qualified `Type.field` edge emitted
2. Typed local/param receiver (via `resolve_receiver_type`) → `Type.field` when confidently inferred
3. Unresolvable receiver → bare `field` name kept (never emit a wrong edge)

Optionals, containers, generics, chained receivers, and unknown identifiers return `None` →
edge silently omitted (false negative always preferred over false positive).

### New read-path view: `field_readers` / `field_writers`

`seam/query/context.py` adds two lists to the context result:

- `field_readers` — symbols that have a `reads` edge pointing to this symbol
- `field_writers` — symbols that have a `writes` edge pointing to this symbol

These are the **typed** complement to `callers`, which remains the inclusive BFS view (all edge
kinds). Use `field_readers`/`field_writers` to distinguish data-flow from control-flow precisely;
use `callers` for the full inclusive blast radius.

### Write-path data flow (seam init, per file)

```
For each source file (same pass as call-edge extraction):
  a. field_access.extract_field_access_edges(node, language, path, symbols)
     → dispatches to per-family leaf module
     → per access site: detect mode (reads / writes) from AST context
     → resolve receiver type (self→class / typed receiver / bare)
     → emit Edge(source=enclosing_fn, target=field_name, kind='reads'|'writes',
                 confidence='INFERRED', receiver=raw_text)
  b. db.upsert_file writes edges alongside call/holds edges
  c. field symbols (kind='field') written to symbols table in the same upsert
```

Because this runs per-file in the existing pipeline, the **watcher picks up field-access edges
automatically** — no post-pass needed (unlike synthesis or clustering).

### Module split (1000-line cap)

| Leaf module | Languages |
|-------------|-----------|
| `seam/indexer/field_access.py` | Python extractor + facade re-exports |
| `seam/indexer/field_access_ts.py` | TypeScript / JavaScript |
| `seam/indexer/field_access_go_rust.py` | Go / Rust |
| `seam/indexer/field_access_ext.py` | Java / C# |
| `seam/indexer/field_access_c_cpp.py` | C / C++ |
| `seam/indexer/field_access_ext2.py` | Ruby / PHP |
| `seam/indexer/field_access_php_swift.py` | PHP emission helpers + Swift |

### Traversal — automatic, no new code

The traversal layer (`seam/analysis/traversal.py`) is **kind-agnostic**: it walks all edges
regardless of `kind`. Adding `reads`/`writes` flows through `seam_impact`, `seam_context`,
and `seam_trace` automatically — exactly the same mechanism as `holds` and synthesized edges.

### Config knob

`SEAM_FIELD_ACCESS_EDGES: "on" | "off"` (default `"on"`) — master switch for field-access-edge
emission and field-symbol extraction at extraction time. Set to `"off"` for byte-identical
pre-A3 behavior (no `reads`/`writes` edges, no `kind='field'` symbols). Like all extraction-time
knobs, toggling takes effect only on the next `seam init` re-index.
