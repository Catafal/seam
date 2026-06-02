# Seam

Local code intelligence MCP server for AI agents. Index your codebase once; let agents query instead of grep.

## Status

Phase 8 complete — lean output (`verbose`/`--lean`) + `seam_impact` summary tier shipped; benchmark reduction recovered to 91.8%/88.7%. 1107 tests. Gate green.

## Quickstart

```bash
# Install
pip install seam  # or: uvx seam

# Index your project
cd /path/to/your/project
seam init

# Start the MCP server
seam start
```

Add to your Claude Code MCP config:
```json
{
  "mcpServers": {
    "seam": {
      "command": "seam",
      "args": ["start", "--stdio"]
    }
  }
}
```

## MCP Tools

### Phase 0 — Symbol Search

- `seam_query(concept, limit=10)` — find all code related to a concept (FTS5 + 1-hop graph expansion)
- `seam_context(symbol)` — get callers, callees, file location, docstring for a symbol
- `seam_search(text, limit=20)` — full-text search across all symbol names and docstrings

### Phase 1 — Code Reasoning

- `seam_impact(target, direction="upstream", max_depth=3)` — blast-radius analysis: what breaks if this symbol changes?
- `seam_trace(source, target, max_depth=10)` — shortest call/dependency path between two symbols
- `seam_changes(scope="working", base_ref="main")` — pre-commit risk check: map git diff to affected symbols and risk level

### Phase 2 — Graph Clustering

- `seam_clusters()` — list all functional areas (clusters) as `[{id, label, size}]`
- `seam_clusters(cluster_id=N)` — list member symbols of a specific cluster
- `seam_context(symbol)` — now also returns `cluster_id`, `cluster_label`, and `cluster_peers` so you can see a symbol's functional neighborhood without a second call

### Phase 3 — Agent-First Interface

- `seam_affected(changed_files, depth=5)` — given a list of changed file paths, return the impacted test files via reverse-dependency traversal. Result: `{changed_files, affected_tests, total_dependents_traversed, partial}`. Mirrors the CLI `seam affected` command.

**Search improvement (affects `seam_search` and `seam_query`):** multi-term queries are now OR-joined so one off-vocabulary word cannot zero the result. Results are re-ranked with name/path/test/cluster signals. A LIKE fallback and Damerau-Levenshtein fuzzy scan run when FTS returns no rows. A query like `"parse issues board"` now reliably returns results even when `"board"` is not a token in the index.

### Phase 4 — Node-Field Enrichment

Five new nullable fields are now extracted at parse time and returned by `seam_context`, `seam_search`, and `seam_query`:

| Field | Type | Description |
|-------|------|-------------|
| `signature` | `string \| null` | Declaration header normalized to one line (e.g. `def parse(path: Path) -> Node \| None`). Truncated to `SEAM_MAX_SIGNATURE_LEN` chars (default 300). |
| `decorators` | `string[]` | Verbatim decorator strings for Python (`@dataclass`) and TypeScript (`@Injectable`). Always `[]` for Go and Rust. |
| `is_exported` | `boolean \| null` | `true` when the symbol is part of the public API (TS `export` keyword, Go uppercase, Rust `pub`, Python no-underscore prefix). `null` when language is unsupported. |
| `visibility` | `string \| null` | `"public"`, `"private"`, `"protected"` (TS/Python), or `"crate"` (Rust). `null` when not applicable or unknown. |
| `qualified_name` | `string \| null` | `"ClassName.method"` or plain symbol name when scope-resolved; `null` for top-level names without a resolvable outer scope. |

**`signature` is FTS-searchable:** the FTS5 index now covers `(name, docstring, signature)`, so type-shaped queries like `"conn sqlite3 Connection"` match on parameter and return-type annotations — not just symbol names. The rescore pass applies a +15 signature-match signal (per matched term) that is intentionally smaller than the exact-name (+80) and prefix-name (+40) signals to avoid displacing name-match results.

**Schema v5:** the `symbols` table gained five nullable columns (see `docs/database/schema.sql`). The `connect()` function auto-runs the v4→v5 migration on first open so existing indexes don't break. Field values are `null` until the next full `seam init` re-index — migration adds the columns but cannot backfill parse-time data.

**New config knob:** `SEAM_MAX_SIGNATURE_LEN` (default `300`) — hard cap on stored signature length. Signatures longer than this are truncated with `...`. Useful when pathological function headers would dominate FTS results or make MCP responses painful to read.

### Phase 8 — Lean Output + `seam_impact` Summary Tier

Two levers to control how many tokens a read tool returns — driven by the benchmark, which showed enrichment-rich output had narrowed the win and that `seam_impact` on a hub symbol cost *more* than reading the files.

**Lean output (`verbose=false` / `--lean`).** The enrichment-carrying tools (`seam_context`, `seam_trace`, `seam_impact`, `seam_context_pack`) accept `verbose` (default `true` = unchanged). With `verbose=false`, the six heavy fields (`decorators`, `is_exported`, `visibility`, `qualified_name`, `resolved_by`, `best_candidate`) are **omitted**; `signature` + core identity are always kept. The win is largest where records repeat — `seam_trace` drops ~40%. (`seam_query`/`seam_search` carry no enrichment, so they have no `verbose` flag.)

**`seam_impact` summary tier.** `seam_impact` now returns:

| Field | Meaning |
|-------|---------|
| `risk_summary` | `{direction: {tier: count}}` over the **full** blast radius — the size of the impact in a few bytes, always present. |
| (capped entries) | each tier holds the closest ≤ `SEAM_IMPACT_MAX_RESULTS` (default 25) entries. |
| `truncated` | `{direction: {tier: omitted}}` when the cap dropped entries. |
| `limit` param | per-tier cap; `limit=0` returns the full transitive set. |

The cap applies **by default**, turning a hub symbol's wall-of-entries into a histogram + the highest-risk few: in the benchmark, `init_db` impact went from ~30k tokens (a net loss vs. grep) to ~4.5k (an 85% win). `seam impact` honors `--limit` / `--lean` in **all** output modes (JSON, quiet, and the default Rich table, which shows a truncation footer). **No schema change; MCP tool count stays 10.**

### Phase 7 — One-Shot `seam sync`

`seam sync` incrementally refreshes the index instead of a full `seam init`. It is **CLI-only** (a maintenance command — no MCP tool; the server stays read-only):

```bash
seam sync                    # reconcile the current project against the index
seam sync /path/to/project   # reconcile a specific project
seam sync --force-clusters   # recompute clusters even if nothing changed
seam sync -q >/dev/null 2>&1 # quiet, for a git post-merge / post-checkout hook
seam sync --json             # structured envelope for CI / agents
```

**How it decides what changed** — filesystem reconcile, *not* git: each file's on-disk `st_mtime` is compared to the stored value (cheap pre-filter); on a mismatch the content is SHA-1 hashed and compared, so a `touch` that doesn't change content is *not* re-indexed. Added files are indexed, content-changed files re-indexed, and a tracked file is removed **only once it genuinely no longer exists on disk** (an `existsSync` guard — a transient walk hiccup, a wrong directory, or a `--db-dir` mismatch cannot silently wipe the index). Works in non-git repos and catches pulled/merged/checked-out changes.

**Gated cluster recompute** — Seam's clusters are a *global* Louvain partition (one new edge can re-partition unrelated communities), so there is no correct cheap incremental update. After reconcile, `seam sync` runs the **same full `index_clusters` pass `seam init` uses, but only when the graph changed** (`added + modified + removed > 0`). A no-op sync skips it entirely (no churned cluster IDs, near-free). This closes the long-standing "clusters go stale after edits" gotcha for the sync path; `--force-clusters` covers the case where the live watcher already indexed your edits (so sync sees no drift) but left clusters stale.

The `--json` / `--quiet` payload (`SyncResult`):

| Field | Description |
|-------|-------------|
| `added` / `modified` / `removed` / `unchanged` / `skipped` | File reconcile counts (`skipped` = unsupported / oversize / binary / parse error). |
| `graph_changed` | `(added + modified + removed) > 0` — the cluster-recompute gate. |
| `clusters_recomputed` | `true` only when the recompute ran **and succeeded**. |
| `cluster_count` | `null` = recompute skipped · `-1` = recompute **ran but failed** (surfaced as a "clusters: failed" warning, exit still 0) · `≥0` = cluster count. |

**No schema change, no migration, no new config knobs** (reuses `SEAM_CLUSTER_NAMING` / `SEAM_CLUSTER_MIN_SIZE` / `SEAM_LLM_*`). `seam sync` requires an existing index — on a directory with no `.seam/seam.db` it returns `NO_INDEX` (run `seam init` first).

### Phase 6 — Context-Pack Primitive

- `seam_context_pack(symbol)` — one call returns a ready-to-paste bundle for a symbol instead of chaining `seam_context` + `seam_why` + per-neighbor lookups. The bundle is:

| Field | Description |
|-------|-------------|
| `target` | The full `seam_context` payload (360° view + Phase 4/5 enrichment). |
| `callers` / `callees` | Direct 1-hop neighbors **enriched** to `{name, file, line, kind, signature, …}` — not bare names. Deterministically ordered by lowest symbol id within each file. |
| `why` | The `WHY/HACK/NOTE/TODO/FIXME` comments attached to the symbol. |
| `cluster_peers` | The symbol's functional-area peers. |
| `truncated` | `{callers, callees, comments}` — counts dropped **by the caps**, so you know the bundle is partial and can fall back to `seam_impact`. |

Pure read-time orchestration over existing primitives — **no schema change, no network, 1-hop only.** Homonym mitigation: neighbors are capped per source file (`SEAM_PACK_PER_FILE_CAP`, default 3) so one file's same-named symbols can't flood the bundle, then globally capped (`SEAM_PACK_NEIGHBOR_LIMIT`, default 10 per list); comments cap at `SEAM_PACK_MAX_COMMENTS` (default 10). Neighbor names with no symbol row in the index (external/unindexed) are silently skipped and do **not** count toward `truncated`.

**New config knobs:** `SEAM_PACK_NEIGHBOR_LIMIT` (10), `SEAM_PACK_PER_FILE_CAP` (3), `SEAM_PACK_MAX_COMMENTS` (10).

#### When to use each Phase 1 tool

| Tool | Use when |
|------|----------|
| `seam_impact` | Before editing any symbol — understand what downstream code depends on it |
| `seam_trace` | When you need to understand how control flows from one symbol to another |
| `seam_changes` | Before committing — verify your changes don't silently break callers |

#### Edge confidence

Every edge in the index carries one of three confidence levels:

| Level | Meaning |
|-------|---------|
| `EXTRACTED` | Target resolves to exactly one symbol in the same file — high certainty |
| `INFERRED` | Heuristic edge (target not in same-file symbol set, or import to external module) |
| `AMBIGUOUS` | Target name matches more than one symbol in the same file — verify by reading |

For multi-hop paths, confidence is aggregated using the **weakest-hop rule**: a path is as
strong as its weakest edge. When multiple paths reach the same symbol at the same distance,
the strongest path is reported.

#### Risk tiers

`seam_impact` and `seam_changes` group affected symbols into tiers by distance from the changed symbol:

| Tier | Distance | Action |
|------|----------|--------|
| `WILL_BREAK` | d=1 | Direct dependents — definitely affected, **must update** |
| `LIKELY_AFFECTED` | d=2 | Indirect dependents — probably affected, should test |
| `MAY_NEED_TESTING` | d≥3 | Transitive dependents — test if on a critical path |

`seam_changes` maps the highest tier to an overall risk level:
`low` → `medium` → `high` → `critical`

## CLI Commands

### Phase 0

```bash
# Index the current directory
seam init [path] [--db-dir DIR]

# Show index stats (file/symbol/edge counts, freshness, watcher PID)
seam status [path] [--db-dir DIR]
seam status --json     # {"ok":true,"data":{"files":…,"symbols":…,"freshness":"fresh"}}
seam status --quiet    # prints freshness only ("fresh" or "stale"), useful for CI gating

# Start the MCP server (stdio) and file watcher
seam start [path] [--db-dir DIR]
```

### Phase 2 — Clustering

```bash
# List all clusters (functional areas)
seam clusters
seam clusters --json   # structured envelope

# List members of cluster 3
seam clusters --id 3
```

### Phase 1 — Code Reasoning

```bash
# Blast-radius analysis: what breaks if 'upsert_file' changes?
seam impact upsert_file
seam impact upsert_file --direction upstream   # callers (default)
seam impact upsert_file --direction downstream # callees
seam impact upsert_file --direction both       # full neighborhood
seam impact upsert_file --depth 5              # up to 5 hops (default: 3)
seam impact upsert_file --path /some/project   # explicit project root
seam impact upsert_file --json                 # structured JSON envelope
seam impact upsert_file --quiet                # bare dependent names, one per line
```

Sample output:
```
Impact (upstream) of upsert_file:

  WILL BREAK         (d=1)
    index_one_file  EXTRACTED  d=1

  LIKELY AFFECTED   (d=2)
    init  INFERRED  d=2
```

```bash
# Trace the shortest path from 'init' to 'upsert_file'
seam trace init upsert_file
seam trace init upsert_file --depth 5   # max hops (default: 10)
seam trace init upsert_file --path .    # explicit project root
seam trace init upsert_file --json      # structured JSON envelope
seam trace init upsert_file --quiet     # hop names only, one per line
```

Sample output:
```
Path from init to upsert_file (2 hop(s)):
  init  →  index_one_file  call  EXTRACTED
  index_one_file  →  upsert_file  call  EXTRACTED

  callers(init): none
  callees(init):
    index_one_file  call  EXTRACTED
```

```bash
# Pre-commit risk check: map working-tree diff to affected symbols
seam changes
seam changes --scope staged               # staged changes only
seam changes --scope branch --base main   # entire branch vs main
seam changes --scope working --path .     # explicit project root
seam changes --json                       # structured JSON envelope
seam changes --quiet                      # risk level only ("low"/"medium"/"high"/"critical")
# --stdin: narrow changed_symbols/new_files to a precomputed file list
# NOTE: risk_level and affected intentionally reflect the FULL git diff even with --stdin
# (conservative: never under-reports risk)
git diff --name-only | seam changes --stdin --json
```

Sample output:
```
seam changes  scope=working

Risk: HIGH

Changed symbols (1):
  query  seam/query/engine.py  lines [42, 43]

Affected symbols (3):

  WILL BREAK         (d=1)
    handle_seam_query  EXTRACTED  d=1

  LIKELY AFFECTED   (d=2)
    seam_query  INFERRED  d=2
```

### Phase 3 — Agent-First Interface

```bash
# Find which test files are impacted by changed source files
seam affected src/foo.py src/bar.py
seam affected src/foo.py --json     # {"ok":true,"data":{"changed_files":[…],"affected_tests":[…],…}}
seam affected src/foo.py --quiet    # bare test-file paths, one per line

# Pipe pattern: run only the tests impacted by the current diff
git diff --name-only | seam affected --stdin --quiet | xargs pytest

# A changed file that is itself a test file is always included in the output.
# Files not in the index are silently skipped (no error).
# --stdin and positional arguments are mutually exclusive.
```

### Phase 6 — Context-Pack

```bash
# One-shot context bundle: target + enriched callers/callees + WHY comments + peers
seam pack context
seam pack context --json     # {"ok":true,"data":{"target":{…},"callers":[…],"truncated":{…}}}
seam pack context --quiet    # terse: target line, caller/callee names, WHY comments

# A missing symbol is NOT an error — it returns a success envelope with found:false,
# mirroring `seam context`. Neighbors are capped per file and globally; the `truncated`
# counts tell you when the bundle was clipped.
```

**JSON envelope (all read commands when `--json` is set):**

```json
// success
{"ok": true, "data": { ... command-specific payload ... }}

// failure (non-zero exit)
{"ok": false, "error": {"code": "NO_INDEX", "message": "No index found. Run 'seam init' first."}}
```

Stable error codes: `NO_INDEX`, `INVALID_INPUT`, `INVALID_QUERY`, `NOT_A_GIT_REPO`, `DB_ERROR`.
Errors are written to **stdout** (not stderr) so agents parsing stdout always get a parseable envelope.
The human Rich output is unchanged when no flag is passed; `--json` and `--quiet` are mutually exclusive.

### Phase 5 — Import Resolution & Confidence Promotion

The confidence tier on every edge now explains itself, and import statements are used to fix false ambiguity.

**The homonym problem (and its fix).** When two files each define `parse()`, every call to `parse()` anywhere in the repo was previously reported as AMBIGUOUS — even when the calling file contained `from app.json import parse`, making the binding unambiguous. Phase 5 reads those import statements and, when a same-file import resolves to exactly one indexed file that declares the target, promotes the edge to EXTRACTED. The call stops being a false alarm.

**`resolved_by` provenance.** Every resolved edge now reports *how* its tier was decided:

| `resolved_by` | Meaning |
|---------------|---------|
| `import` | Promoted via a resolved same-file import (the homonym fix) |
| `name-unique` | Target name appears exactly once in the full index |
| `name-collision` | Target name shared by >1 symbol (homonym, no import to resolve) |
| `builtin` | Target name is a known language builtin/stdlib (count==0 guard — user-defined names are never suppressed) |
| `unresolved` | Target not in index, not a known builtin |

`null` means the edge was resolved against a pre-v6 index or without language/import context.

**Builtin filtering.** Calls to `len()`, `print()`, `console.log`, `make()`, or `Vec::new` are now tagged `resolved_by: builtin` (INFERRED) rather than appearing as mysteriously unresolved external dependencies. The builtin check fires **only when nothing in the repo declares that name** (count==0), so a user who writes their own `def get()` keeps a normal resolution — the builtin set never shadows real repo symbols.

**Proximity tie-break for residual AMBIGUOUS.** When a collision can't be resolved by import (no import, star import, third-party source), the edge stays AMBIGUOUS but reports a `best_candidate` — the file path of the declaring symbol that shares the most directory ancestry with the referencing file. This gives agents and developers a most-likely target without manufacturing false certainty.

**Schema v6.** A new `import_mappings` table stores per-file import bindings extracted at index time. `connect()` auto-migrates v5→v6 on first open (additive, fresh-DB-safe). Mappings are NOT backfilled by the migration — run `seam init` to populate them and enable full Phase 5 resolution on an existing index. Until then, resolution falls back to the name-count rule silently.

**New config knobs:**
- `SEAM_IMPORT_RESOLUTION` (`"on"` default) — master switch for import-promotion step A.
- `SEAM_BUILTIN_FILTERING` (`"on"` default) — master switch for builtin tagging step C.
- `SEAM_MAX_IMPORT_CANDIDATES` (default `25`) — cap on candidate declaring files evaluated per import lookup.
- `SEAM_PROXIMITY_MAX_CANDIDATES` (default `25`) — cap on collision candidates ranked by proximity.

`seam_impact` and `seam_trace` output now include `resolved_by` and `best_candidate` on each entry/hop. Both fields are `null` for pre-v6 indexes or when resolution context is unavailable — same null-contract as the Phase 4 enrichment fields.

## Known Limitations (Phase 1b candidates)

- **Cross-file confidence resolution:** Edge confidence is resolved against same-file symbols only, so edges to symbols defined in other files are mostly `INFERRED`. Full-index resolution (upgrading `INFERRED` to `EXTRACTED` or `AMBIGUOUS` after indexing) is a Phase-1b enhancement.
- **Impact includes test callers:** `seam_impact` and `seam_changes` include test functions in `WILL_BREAK` / `LIKELY_AFFECTED` tiers, which can be noisy. Test-file filtering is a future enhancement.
- **Large-diff cap:** `seam_changes` caps impact analysis at 50 changed symbols on very large diffs (deterministic — first 50 in list order). A warning is logged at `DEBUG` level when the cap is hit.

## Development

```bash
uv sync --dev   # install deps
make gate       # run lint + typecheck + tests (must be green before every commit)
make fmt        # format + fix lint (not part of gate)
```

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for build status and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system design.
