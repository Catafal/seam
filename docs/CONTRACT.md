# Phase 0 Data Contract (FROZEN)

> Both parallel build tracks code against this. **Do not change these shapes
> without updating every consumer and re-syncing both tracks.** This file is the
> first thing each track agent reads.

## Why this exists

Seam's Phase 0 is built as two independent tracks in parallel git worktrees:

- **Track A — Storage + Query:** `seam/indexer/db.py`, `seam/query/engine.py`
- **Track B — Parse + Graph:** `seam/indexer/parser.py`, `seam/indexer/graph.py`

They share **zero source files**. They touch only through the data shapes below.
If those shapes drift, the convergence step (`seam init`) breaks. Freezing them
up front is the single thing that makes the parallel build safe.

## Frozen in-memory types (produced by Track B, consumed by Track A)

Defined in `seam/indexer/graph.py` — already present, **field-frozen**:

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
```

## Frozen query-result types (produced by Track A)

Defined in `seam/query/engine.py` — already present, **field-frozen**:
`QueryResult`, `ContextResult`, `SearchResult`. Match `docs/api-contracts/mcp-tools.yaml`.

## Frozen storage schema

`docs/database/schema.sql` is authoritative and complete. **Do not edit it in Phase 0.**

## The one mapping rule (Track A only)

In-memory `Edge` uses `source` / `target`. The DB columns are `source_name` /
`target_name`. `db.upsert_file` performs the translation
(`Edge["source"] → edges.source_name`, `Edge["target"] → edges.target_name`).
The names differ on purpose — do not "fix" either side.

## Note on stale docs

`BACKEND_STRUCTURE.md` lists `Symbol.col`. That is stale prose — the frozen
TypedDict has **no `col` field**. The code is correct; the doc is not.

## Drift rule

No track edits the `Symbol`/`Edge`/result TypedDicts or `schema.sql`. If a track
believes the contract must change, it **STOPS and escalates to the orchestrator**,
which updates both sides and re-syncs. Silent contract edits are the one failure
mode this whole approach exists to prevent.
