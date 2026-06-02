# Architecture — Seam

> Phase 0 + Phase 1 + Phase 2 (clustering). See ADRs in `docs/adr/` for decision rationale.

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

Main tables + FTS5 virtual table (schema v4):
- `files` — indexed files with hash + mtime
- `symbols` — functions, classes, methods; includes `cluster_id` FK (schema v4)
- `edges` — directed relationships (import, call) with `confidence` column (schema v2)
- `comments` — semantic comments: WHY/HACK/NOTE/TODO/FIXME markers (schema v3)
- `clusters` — community detection results: id, label, size, naming_source (schema v4)
- `symbols_fts` — FTS5 virtual table mirroring `symbols.name + docstring`

See `docs/database/schema.sql` for full DDL.

### File Watcher
**File:** `seam/watcher/daemon.py`

Runs as a background thread/process alongside the MCP server. Uses watchdog's `Observer` + a custom `FileSystemEventHandler`. Debounces rapid saves to avoid thrashing (default 500ms). On trigger: re-parses the changed file, diffs symbols, updates DB.

### MCP Server
**Files:** `seam/server/mcp.py`, `seam/server/tools.py`

Stdio transport (no HTTP, no ports). The Python MCP SDK handles protocol framing. Eight tools exposed (Phase 0 + Phase 1 + Phase 1b + Phase 2). Tool handlers in `tools.py` validate inputs and delegate to `query/engine.py`, `query/clusters.py`, or `analysis/`.

### Query Engine
**File:** `seam/query/engine.py`

The read path. Three query types:
- **FTS5 search** — BM25-ranked full-text search across symbol names + docstrings
- **Concept query** — FTS5 match + 1-hop graph expansion (connected symbols)
- **Context** — Direct lookup by symbol name + join to get callers/callees

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
