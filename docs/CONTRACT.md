# Data Contract (Phase 0 base + Phase 1 Core evolution)

> Phase 0 tracks are now converged. The parallel-worktree constraint is lifted.
> Phase 1 Core (issue #3 — confidence-tagged edges) is the **sanctioned escalation**
> that evolves both `Edge` and `ContextResult`. Both sides have been updated together.

## Why this exists

Seam's Phase 0 was built as two independent tracks in parallel git worktrees:

- **Track A — Storage + Query:** `seam/indexer/db.py`, `seam/query/engine.py`
- **Track B — Parse + Graph:** `seam/indexer/parser.py`, `seam/indexer/graph.py`

They share **zero source files**. They touch only through the data shapes below.
This file remains the authoritative contract reference across all phases.

## In-memory types (produced by graph.py, consumed by db.py)

Defined in `seam/indexer/graph_common.py` (the leaf module); re-exported from
`seam/indexer/graph.py` so existing callers (`from seam.indexer.graph import Symbol`)
continue to work without changes.

```python
class Symbol(TypedDict):
    name: str          # function name; "Class.method" for methods
    kind: str          # 'function' | 'class' | 'method' | 'interface' | 'type'
    file: str          # str(resolved absolute path)
    start_line: int    # 1-based
    end_line: int
    docstring: str | None

class Edge(TypedDict):
    source: str        # caller / importer name
    target: str        # callee / importee name
    kind: str          # 'import' | 'call'
    file: str
    line: int
    confidence: str    # PHASE 1 ADDITION: 'EXTRACTED' | 'INFERRED' | 'AMBIGUOUS'
                       # EXTRACTED = target resolves to exactly 1 same-file symbol
                       # INFERRED  = heuristic / not in same-file symbol set
                       # AMBIGUOUS = target name matches >1 same-file symbol
```

The `Confidence` type alias is defined in `graph_common.py` as
`Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]`.

## Query-result types (produced by engine.py)

Defined in `seam/query/engine.py`:

```python
class ContextResult(TypedDict):
    symbol: str
    file: str
    line: int
    end_line: int
    kind: str
    docstring: str | None
    callers: list[str]
    callees: list[str]
    ambiguous: bool    # PHASE 1 ADDITION: True when >1 symbol shares this name in DB
                       # Signals cross-file name collision; result is the first match.
```

`QueryResult` and `SearchResult` are unchanged from Phase 0.

## Storage schema

`docs/database/schema.sql` is authoritative. Schema version is `3` (Phase 1b — adds `comments` table).

**Phase 1 change:** `edges` table has a new column:
```sql
confidence  TEXT NOT NULL DEFAULT 'INFERRED'
```

> Note: the DEFAULT is `'INFERRED'` (conservative), not `'EXTRACTED'`. New rows
> created by `upsert_file` always carry an explicit confidence value; the DEFAULT
> only fires for v1 DBs upgraded via `_run_migration_v1_to_v2` (ALTER TABLE).
> `'INFERRED'` is the safe fallback — old edges whose confidence was never computed
> are treated as heuristic rather than trusted.

`init_db` runs a guarded migration: if an existing DB has no `confidence` column,
it issues `ALTER TABLE edges ADD COLUMN confidence ...` and logs a re-index warning.

## The mapping rules (db.py only)

In-memory `Edge` uses `source` / `target`. The DB columns are `source_name` /
`target_name`. `db.upsert_file` performs the translation:
- `Edge["source"] → edges.source_name`
- `Edge["target"] → edges.target_name`
- `Edge["confidence"] → edges.confidence`  ← Phase 1 addition

The names differ on purpose — do not "fix" either side.

## Confidence resolution — two layers

**Phase 1b change (issue #9):** Edge confidence is now resolved at **read time** against
the whole index, not at index time against the same-file symbol set.

### Layer 1 — Stored column (same-file lower-bound hint, index time)

`graph.extract_edges` writes a confidence value to `edges.confidence` based on the
symbol list extracted from the **same file** at the same call.  This is a cheap debugging
hint — it tells you whether the target was found locally — but it is **not authoritative**
for cross-file edges.

### Layer 2 — Read-time whole-index resolution (authoritative)

`seam/analysis/confidence.py` is the single source of truth for the three-value rule.
At read time, `traversal.walk`, `flows.trace`, `flows.callers`, and `flows.callees` load
a `name → count` map with one `SELECT name, COUNT(*) FROM symbols GROUP BY name` query
(once per call, not per edge), then resolve each edge's confidence from the edge's
`target_name` against that map:

| count in whole index | Resolved confidence |
|----------------------|---------------------|
| exactly 1            | `EXTRACTED`          |
| > 1                  | `AMBIGUOUS`          |
| 0 (not indexed)      | `INFERRED`           |

This overrides the stored `edges.confidence` value.  The stored column is kept unchanged
(no schema migration) as a lower-bound hint.

**Consequence:** a cross-file edge whose target name is unique in the whole index now
correctly reports `EXTRACTED` instead of `INFERRED`.  The signal is now discriminating
across files, not just within a single file.

### Ambiguity signals — two orthogonal concepts

There are **two orthogonal `ambiguous` signals** in Seam — callers must not conflate them:

| Signal | Location | What "ambiguous" means |
|--------|----------|------------------------|
| `Edge.confidence == 'AMBIGUOUS'` (read-time) | `seam/analysis/confidence.py` | The edge target name appears in **more than one indexed symbol** across the whole index (read-time, whole-index scope). |
| `ContextResult.ambiguous == True` | `engine.context()` | **More than one symbol shares this name across the whole index** (query-time, global scope — unchanged). |

These are now consistent in scope (both whole-index).  The stored `edges.confidence`
column retains its original same-file semantics as a debugging hint only.

## Note on resolved stale docs

`BACKEND_STRUCTURE.md` previously listed `Symbol.col` — that was stale prose; the
TypedDict has no `col` field. BACKEND_STRUCTURE.md has been updated (Phase 1b) to
reflect the correct fields and the new graph_common/graph_go_rust modules.

## Analysis layer types (Phase 1 — Slice 5)

Defined in `seam/analysis/traversal.py` and `seam/analysis/impact.py`.
These are **read-only** types produced by the analysis layer; they are never written to the DB.

```python
class Reached(TypedDict):
    name:        str   # reachable symbol name (string, from edges table)
    distance:    int   # hops from any seed (1-based; seeds excluded from output)
    confidence:  str   # aggregated path confidence: EXTRACTED | INFERRED | AMBIGUOUS

# TieredEntry is a plain dict (not TypedDict) to accommodate the mixed-type `file` field.
# Shape (Phase 1b QA hardening additions: is_test):
#   name        (str)        — symbol name
#   distance    (int)        — hops from the target
#   confidence  (str)        — EXTRACTED | INFERRED | AMBIGUOUS
#   tier        (str)        — WILL_BREAK | LIKELY_AFFECTED | MAY_NEED_TESTING
#   file        (str | None) — ABSOLUTE path to source file for indexed symbols;
#                              None for names not in the symbols table (e.g. external deps)
#                              handle_seam_impact relativizes this to project root before return.
#   is_test     (bool)       — True when `file` belongs to a test file (see is_test_file()
#                              heuristic below). False for production files AND for entries
#                              with file=None (unresolved names are never labelled as test,
#                              to avoid false positives — their provenance is unknown).

# is_test_file() heuristic (seam/analysis/testpaths.py — single authoritative source):
#   Returns True when ANY of the following hold:
#     1. A directory SEGMENT (Path.parts, not substring) is exactly 'tests' or 'test'.
#        'testdata/', 'contest/', 'attest/' do NOT match.
#     2. The basename (case-insensitive) matches:
#          test_*.py      — Python test_* prefix
#          *_test.py      — Python *_test suffix
#          conftest.py    — pytest config / fixtures
#          *.spec.{js,jsx,ts,tsx}
#          *.test.{js,jsx,ts,tsx}
#   Returns False for None or empty string (safe default for unresolved names).
#
#   Documented limitation (by design, conservative):
#     Test trees NOT named 'tests' or 'test' (e.g. e2e/, qa/, integration/) are
#     classified as production (is_test=False). This is a false-negative — such files
#     are under-flagged rather than over-flagged. The rule intentionally avoids
#     false positives like a production `integration/` module that happens to share a
#     directory name with a test runner convention. Callers should not rely on
#     is_test=False as a guarantee that a file is production code — it means "the
#     heuristic did not recognise it as a test file".

# TierGroup: tier-name -> list[TieredEntry] (always all 3 keys present, even if empty)
TierGroup = dict[str, list[dict[str, Any]]]

# ImpactResult: a plain dict[str, Any] with the following keys:
#   found        (bool) — True if the target is a known symbol or edge endpoint in the index;
#                         False if the target name was not found (unknown symbol / typo guard).
#   target       (str)  — the queried symbol name (echoed back for agent convenience).
#   <direction-key(s)>: TierGroup
#     direction="upstream"   -> {"found": bool, "target": str, "upstream": TierGroup}
#     direction="downstream" -> {"found": bool, "target": str, "downstream": TierGroup}
#     direction="both"       -> {"found": bool, "target": str,
#                                "upstream": TierGroup, "downstream": TierGroup}
#
#   Present only when impact() is called with include_tests=False:
#     hidden_tests (int) — count of test-file dependents filtered out across all tiers.
#                          Purpose: anti-false-safe. Lets callers distinguish "no dependents
#                          at all" (hidden_tests==0) from "all dependents were test files,
#                          all filtered" (hidden_tests>0). The latter is NOT safe to treat
#                          as dead code — tests would break. Without this field, a caller
#                          seeing empty tiers after filtering could wrongly conclude the
#                          symbol is unused. Absent when include_tests=True (the default)
#                          so its presence alone signals that filtering was applied.
#
# impact() parameters (Phase 1b addition):
#   include_tests (bool, default True) — when True, returns all dependents (default behavior,
#                                        backward-compatible). When False, test-file entries
#                                        are removed from all tiers and hidden_tests is added.
ImpactResult = dict[str, Any]
```

### Risk tier names (exact strings)

| Tier constant            | String value          | Distance |
|--------------------------|-----------------------|----------|
| `TIER_WILL_BREAK`        | `"WILL_BREAK"`        | d == 1   |
| `TIER_LIKELY_AFFECTED`   | `"LIKELY_AFFECTED"`   | d == 2   |
| `TIER_MAY_NEED_TESTING`  | `"MAY_NEED_TESTING"`  | d >= 3   |

These match CLAUDE.md d=1/2/3 and the GitNexus impact risk levels.

### Path-confidence rule (traversal.py)

The confidence of a multi-hop path is its **weakest hop**:

- Any `AMBIGUOUS` hop on the path → path is `AMBIGUOUS` (weakest).
- Any `INFERRED` hop (no AMBIGUOUS) → path is `INFERRED`.
- All `EXTRACTED` hops → path is `EXTRACTED` (strongest).

When the same symbol is reachable via **multiple paths at the same distance**,
the **strongest** path confidence is reported (best available path wins).

Confidence rank: `EXTRACTED` (2) > `INFERRED` (1) > `AMBIGUOUS` (0).

### Traversal direction semantics

| Direction    | Edge traversal                              | Meaning                 |
|--------------|---------------------------------------------|-------------------------|
| `upstream`   | Follow edges where `target_name == seed`    | Callers / importers     |
| `downstream` | Follow edges where `source_name == seed`    | Callees / importees     |

### Cycle safety

`traversal.walk()` uses a Python-side BFS with an explicit `visited` set.
A symbol is visited at its first (minimum-distance) encounter. Cycles terminate
because visited symbols are never re-added to the frontier.

## Flow tracing types (Phase 1 — Slice 6)

Defined in `seam/analysis/flows.py`.
These are **read-only** types produced by the analysis layer; they are never written to the DB.

```python
class Hop(TypedDict):
    """One step on a call/dependency path."""
    from_name:  str   # source symbol of this edge
    to_name:    str   # target symbol of this edge
    kind:       str   # 'call' | 'import'
    confidence: str   # per-edge confidence: EXTRACTED | INFERRED | AMBIGUOUS

# Path = list[Hop] (ordered, source-to-target)
# Invariants:
#   len == 0 → trivial self-path (source == target)
#   len >= 1 → path[0].from_name == source, path[-1].to_name == target
#   path[i].to_name == path[i+1].from_name (consecutive hops are linked)
Path = list[Hop]

class EdgeHop(TypedDict):
    """One-hop result for callers() / callees()."""
    name:       str   # neighboring symbol name
    kind:       str   # 'call' | 'import'
    confidence: str   # EXTRACTED | INFERRED | AMBIGUOUS
```

### trace() contract

```python
trace(conn, source, target, max_depth=10) -> list[Path]
```

- Returns **shortest path only** (BFS terminates at first match).
- Returns `[[]]` when `source == target` (zero-hop trivial path).
- Returns `[]` when no path exists within `max_depth` hops — distinguishable "not connected".
- CYCLE-SAFE: BFS `visited` set prevents infinite loops.
- Bounded by `max_depth` (clamped to [1, 10] by the handler layer).

### callers() and callees() contract

```python
callers(conn, symbol) -> list[EdgeHop]   # one-hop upstream (who calls/imports symbol)
callees(conn, symbol) -> list[EdgeHop]   # one-hop downstream (what symbol calls/imports)
```

- One-hop only — use `walk()` from `traversal.py` for multi-hop.
- Results deduplicated by (name, kind); strongest confidence kept for duplicates.
- Sorted by name alphabetically for determinism.
- Returns `[]` for unknown symbols or empty string.

### Per-hop confidence

Each `Hop` carries the confidence of that **specific edge** (not an aggregated path confidence).
The overall path confidence is `min(hop.confidence for hop in path)` (weakest-hop rule from traversal.py).
The caller is responsible for aggregating if needed.

An `AMBIGUOUS` hop means the edge target name appears in more than one indexed symbol at read
time (whole-index resolution) — the caller should flag this hop as "verify by reading the code".

### handle_seam_trace() response shape

```python
{
    "found":          bool,        # True if paths is non-empty
    "source":         str,         # echoed source
    "target":         str,         # echoed target
    "paths":          list[list[Hop]],  # shortest path (0 or 1 entries)
    "callers_source": list[EdgeHop],
    "callees_source": list[EdgeHop],
    "callers_target": list[EdgeHop],
    "callees_target": list[EdgeHop],
}
```

Error shape on blank source or target: `{"error": "INVALID_INPUT", "message": "..."}`.

## detect_changes types (Phase 1 — Slice 7)

Defined in `seam/analysis/changes.py`.

```python
class ChangedSymbol(TypedDict):
    name: str            # symbol name; "<module:file.py>" for module-level changes
    file: str            # absolute path to source file
    kind: str            # function | class | method | module (synthetic)
    start_line: int      # 0 for synthetic module-level entries
    end_line: int        # 0 for synthetic module-level entries
    changed_lines: list[int]  # new-file line numbers overlapping this symbol's range

class AffectedSymbol(TypedDict):
    name: str
    file: str | None     # absolute path if indexed; None for unindexed names
    tier: str            # WILL_BREAK | LIKELY_AFFECTED | MAY_NEED_TESTING
    confidence: str      # EXTRACTED | INFERRED | AMBIGUOUS
    distance: int        # hops from the nearest changed symbol

class ChangeReport(TypedDict):
    changed_symbols:   list[ChangedSymbol]
    new_files:         list[str]        # absolute paths of added/untracked files
    affected:          list[AffectedSymbol]
    risk_level:        str              # low | medium | high | critical
    ambiguous_warning: bool
    scope:             str              # working | staged | branch
    base_ref:          str
    partial:           bool             # True when changed real-symbol count exceeded
                                        # SEAM_MAX_IMPACT_SYMBOLS (env, default 50);
                                        # only the first N real symbols were analyzed.
                                        # "Real" means names that do NOT start with '<'
                                        # (excludes synthetic <module:...>/<new:...> entries
                                        # which _collect_impact already skips).
                                        # When partial=True treat risk_level as a lower bound.
```

### Risk rollup rule (exact)

1. Collect every `AffectedSymbol` from `impact(upstream)` across all changed symbols.
2. Find the **highest tier** reached:
   `WILL_BREAK > LIKELY_AFFECTED > MAY_NEED_TESTING > (none)`
3. Map to `risk_level`:
   | Highest tier        | risk_level |
   |---------------------|------------|
   | WILL_BREAK          | critical   |
   | LIKELY_AFFECTED     | high       |
   | MAY_NEED_TESTING    | medium     |
   | (no dependents)     | low        |
4. **AMBIGUOUS attenuation:**
   - If **ALL** affected symbols have `AMBIGUOUS` confidence:
     cap `risk_level` at `"medium"` (uncertain inputs limit the verdict confidence).
     Set `ambiguous_warning = True`.
   - If **SOME** (but not all) have `AMBIGUOUS` confidence:
     keep the raw `risk_level`, set `ambiguous_warning = True`.
   - If **NONE** have `AMBIGUOUS` confidence:
     `ambiguous_warning = False` (no attenuation).

### NotAGitRepoError

`seam/analysis/changes.py` defines `NotAGitRepoError(ValueError)`.
This is raised (not returned) when the repo_root is not a git repository
or git is unavailable. The handler converts it to
`{"error": "NOT_A_GIT_REPO", "message": "..."}`.
The CLI converts it to a `console.print("[red]Not a git repository:[/red] ...")`.

## Semantic comment nodes (Phase 1b — seam_why)

### Schema v3 — comments table

Added in Phase 1b. Defined in `docs/database/schema.sql`.

```sql
CREATE TABLE IF NOT EXISTS comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line    INTEGER NOT NULL,   -- 1-based line number in the source file
    marker  TEXT NOT NULL,      -- Normalized UPPERCASE: WHY|HACK|NOTE|TODO|FIXME
    text    TEXT NOT NULL       -- Body after the marker (and optional colon), stripped
);
CREATE INDEX IF NOT EXISTS idx_comments_file_id ON comments(file_id);
```

Key points:
- Cascade-deleted when the parent `files` row is removed (`ON DELETE CASCADE`).
- `upsert_file` deletes the file's existing comments then re-inserts atomically
  (same pattern as symbols and edges — re-index replaces, never accumulates).
- No FTS: lookup is by `file_id + line BETWEEN low AND high` (proximity), not full-text.

**Migration:** `_run_migration_v2_to_v3` in `db.py` bumps `schema_version` from `'2'`
to `'3'` on existing DBs exactly once, then logs an advisory to run `seam init` to
populate the now-empty `comments` table. Fresh DBs are seeded at `schema_version='3'`
directly (via `INSERT OR IGNORE`) so a brand-new `seam init` is born current and does
NOT emit the migration advisory. The migration is additive (no data loss).

### Comment extraction contract

`extract_comments(node, language, filepath) -> list[Comment]` in `seam/indexer/graph.py`.

```python
class Comment(TypedDict):
    marker: str   # Normalized UPPERCASE: WHY | HACK | NOTE | TODO | FIXME
    text:   str   # Body after the marker (and optional colon), stripped
    line:   int   # 1-based line number in the source file
```

**Marker set (fixed):** `WHY`, `HACK`, `NOTE`, `TODO`, `FIXME`.

**Matching rule:** Marker is matched case-insensitively at the START of the comment
body (after stripping the delimiter and leading whitespace), followed by `':'`,
whitespace, or end-of-string. This means:
- `# WHY: reason` → matched (WHY followed by `:`)
- `# hack workaround` → matched (hack followed by space)
- `# NOTE` → matched (NOTE at end-of-string)
- `# whyever` → NOT matched (`r` after `why` is a word char)
- `# notes on algo` → NOT matched (`s` after `note` is a word char)

Stored marker is normalized to UPPERCASE. Only marked comments are stored; plain
comments are silently ignored.

**Language support:**
- Python: `#` line comments (one body, one potential match).
- TypeScript/JavaScript: `//` line comments (single body) and `/* */` block comments.
  Block comments are scanned **line-by-line** — every non-empty body line is checked.
  A marker on line 2+ of a JSDoc-style block is detected, and its stored `line` points
  at the marker's real line (not at the `/*` opener). Each matched line in a block
  becomes a separate `Comment` entry.
- Go: `//` line comments and `/* */` block comments. Block comments are scanned
  line-by-line (same rule as TypeScript). Both `//' and `//` node variants use the
  single `comment` tree-sitter node type — the prefix is stripped before matching.
- Rust: `line_comment` nodes (`//`, `///`, `//!`) and `block_comment` nodes (`/* */`).
  All `line_comment` variants are scanned; the `//`, `///`, or `//!` prefix is stripped
  before matching. `///` lines are also used as docstrings (outer doc-comment), but
  both roles are independent — a `/// WHY: reason` line is stored as BOTH a docstring
  line AND a semantic comment. Block comments use the same line-by-line scan.

No per-language confidence code: Go and Rust semantic comment extraction and
edge confidence resolution use the same shared helpers as Python/TypeScript.

**Never raises:** returns `[]` on any parse trouble (consistent with `extract_symbols`
and `extract_edges`).

### why() contract

`why(conn, *, file=None, line=None, symbol=None) -> list[CommentHit]`
Defined in `seam/query/comments.py`.

```python
class CommentHit(TypedDict):
    file:   str   # Absolute path (DB-stored); handler/CLI relativizes before output.
    line:   int   # 1-based line number.
    marker: str   # Normalized UPPERCASE: WHY | HACK | NOTE | TODO | FIXME.
    text:   str   # Comment body after the marker (and optional colon), stripped.
```

**Lookup modes** (at least one of `file`/`symbol` required; omitting both raises `ValueError`):

| Mode | Condition | Query |
|------|-----------|-------|
| file only | `file` given, `line` is None | All comments for that file |
| file + line | `file` and `line` given | Comments within `[line - RADIUS, line + RADIUS]` |
| symbol | `symbol` given | Comments within `[start_line - LEAD, end_line]` of the symbol |

**Constants** (module-level in `seam/query/comments.py`, not env-driven):

| Constant | Value | Meaning |
|----------|-------|---------|
| `RADIUS` | `15` | ±lines from the queried line in file+line mode |
| `LEAD`   | `5`  | Lines above `start_line` included in symbol mode to capture pre-symbol rationale |

**Path matching:** `why()` does exact string matching on `files.path` (DB-stored absolute
paths). Callers (handler, CLI) must resolve user-supplied paths to absolute before calling.

**Missing-table guard:** `connect()` (used by `seam start`, `seam status`, `seam why`)
opens a bare connection and does NOT run the schema script — only `init_db()` does. A
pre-1b index (schema v2) opened via `connect()` has no `comments` table; querying it
would raise `OperationalError`. `why()` detects the missing table via `sqlite_master`
and returns `[]` with a warning log instead of raising — the MCP/CLI contract is an
empty list ("no recorded rationale"), not an error. Run `seam init` to migrate and
populate the table.

**Ambiguity on symbol lookup:** When multiple symbols share the same name, the first by
`(files.path, symbols.id)` is used — deterministic ordering, consistent with
`engine.context()`.

**Symbol mode with `line`:** When `symbol` is given, `line` is ignored — symbol mode uses
the symbol's own line range `[start_line - LEAD, end_line]`.

## Language kind mapping (Phase 1b — Go + Rust)

Supported languages as of Phase 1b: Python, TypeScript, JavaScript, Go, Rust.

Go kind mapping (tree-sitter node → Symbol.kind):

| tree-sitter node              | Symbol.kind | Name format           |
|-------------------------------|-------------|-----------------------|
| `function_declaration`        | `function`  | plain name            |
| `method_declaration`          | `method`    | `Recv.Name` (`*T` → `T`, `Repo[T]` → `Repo`) |
| `type_spec` with `struct_type`    | `class`     | plain name            |
| `type_spec` with `interface_type` | `interface` | plain name            |
| `type_spec` (other)            | `type`      | plain name            |
| `type_alias`                   | `type`      | plain name            |

Rust kind mapping:

| tree-sitter node                    | Symbol.kind | Name format       |
|-------------------------------------|-------------|-------------------|
| `function_item` (top-level / in mod) | `function`  | plain name        |
| `function_item` inside `impl_item`   | `method`    | `Type.fn`         |
| `function_item` inside `trait_item`  | `method`    | `Trait.fn`        |
| `function_signature_item` in trait   | `method`    | `Trait.fn` (signature-only, no body) |
| `struct_item`                        | `class`     | plain name        |
| `enum_item`                          | `type`      | plain name        |
| `trait_item`                         | `interface` | plain name        |
| `mod_item`                           | (not emitted — traversed for nested symbols) |

Docstrings:
- Go: contiguous `//` lines immediately above the declaration (no blank-line gap). `_go_doc_comment` in `seam/indexer/graph_go_rust.py`.
- Rust: contiguous `///` (outer doc) lines immediately above the item. `_rust_doc_comment` in `seam/indexer/graph_go_rust.py`. `//'  and `//!` lines are excluded from docstrings.

## Drift rule

Do not edit `Symbol`/`Edge`/result TypedDicts or `schema.sql` without
updating all consumers and this file. Silent contract edits are the one
failure mode this approach exists to prevent.
