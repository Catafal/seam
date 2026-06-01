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

## Drift rule

Do not edit `Symbol`/`Edge`/result TypedDicts or `schema.sql` without
updating all consumers and this file. Silent contract edits are the one
failure mode this approach exists to prevent.
