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

Defined in `seam/indexer/graph.py`:

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

The `Confidence` type alias is defined in `graph.py` as
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

`docs/database/schema.sql` is authoritative. Schema version is `2` (Phase 1).

**Phase 1 change:** `edges` table has a new column:
```sql
confidence  TEXT NOT NULL DEFAULT 'EXTRACTED'
```

`init_db` runs a guarded migration: if an existing DB has no `confidence` column,
it issues `ALTER TABLE edges ADD COLUMN confidence ...` and logs a re-index warning.

## The mapping rules (db.py only)

In-memory `Edge` uses `source` / `target`. The DB columns are `source_name` /
`target_name`. `db.upsert_file` performs the translation:
- `Edge["source"] → edges.source_name`
- `Edge["target"] → edges.target_name`
- `Edge["confidence"] → edges.confidence`  ← Phase 1 addition

The names differ on purpose — do not "fix" either side.

## Ambiguity: two distinct scopes

There are **two orthogonal `ambiguous` signals** in Seam — callers must not conflate them:

| Signal | Location | What "ambiguous" means |
|--------|----------|------------------------|
| `Edge.confidence == 'AMBIGUOUS'` | `graph.py` / `edges` table | The edge target name matched **more than one symbol in the same file** at extraction time (extraction-time, per-file scope). |
| `ContextResult.ambiguous == True` | `engine.context()` | **More than one symbol shares this name across the whole index** (query-time, global scope). |

These are independent. An edge can be `AMBIGUOUS` (same-file name collision at index time) while `context()` returns `ambiguous=False` (only one DB row with that name). Conversely, `context()` can return `ambiguous=True` (two files define the same function name) while all edges to that target are `EXTRACTED` (unique within each file's own symbol list).

## Note on stale docs

`BACKEND_STRUCTURE.md` lists `Symbol.col`. That is stale prose — the
TypedDict has **no `col` field**. The code is correct; the doc is not.

## Analysis layer types (Phase 1 — Slice 5)

Defined in `seam/analysis/traversal.py` and `seam/analysis/impact.py`.
These are **read-only** types produced by the analysis layer; they are never written to the DB.

```python
class Reached(TypedDict):
    name:        str   # reachable symbol name (string, from edges table)
    distance:    int   # hops from any seed (1-based; seeds excluded from output)
    confidence:  str   # aggregated path confidence: EXTRACTED | INFERRED | AMBIGUOUS

# TieredEntry is a plain dict (not TypedDict) to accommodate the mixed-type `file` field.
# Shape:
#   name        (str)        — symbol name
#   distance    (int)        — hops from the target
#   confidence  (str)        — EXTRACTED | INFERRED | AMBIGUOUS
#   tier        (str)        — WILL_BREAK | LIKELY_AFFECTED | MAY_NEED_TESTING
#   file        (str | None) — ABSOLUTE path to source file for indexed symbols;
#                              None for names not in the symbols table (e.g. external deps)
#                              handle_seam_impact relativizes this to project root before return.

# TierGroup: tier-name -> list[TieredEntry] (always all 3 keys present, even if empty)
TierGroup = dict[str, list[dict[str, Any]]]

# ImpactResult: a plain dict[str, Any] with the following keys:
#   found   (bool) — True if the target is a known symbol or edge endpoint in the index;
#                    False if the target name was not found (unknown symbol / typo guard).
#   target  (str)  — the queried symbol name (echoed back for agent convenience).
#   <direction-key(s)>: TierGroup
#     direction="upstream"   -> {"found": bool, "target": str, "upstream": TierGroup}
#     direction="downstream" -> {"found": bool, "target": str, "downstream": TierGroup}
#     direction="both"       -> {"found": bool, "target": str,
#                                "upstream": TierGroup, "downstream": TierGroup}
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

## Drift rule

Do not edit `Symbol`/`Edge`/result TypedDicts or `schema.sql` without
updating all consumers and this file. Silent contract edits are the one
failure mode this approach exists to prevent.
