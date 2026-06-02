# Seam

Local code intelligence MCP server for AI agents. Index your codebase once; let agents query instead of grep.

## Status

Phase 3 complete — agent-first interface shipped. 631 tests. Gate green.

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
