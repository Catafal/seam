# Phase 4 — Node-Field Enrichment (Correctness Tier)

> Source: `.claude/research/codegraph-vs-seam.md` §8 item 4 / §2.5. Modeled on
> CodeGraph v0.9.8's node data model (`cg:src/types.ts:107-167`,
> `cg:src/db/schema.sql:20-41`).

## Problem Statement

When an agent asks Seam about a symbol, it gets back a name, kind, line range, and
(if present) a docstring — but not the symbol's **signature**, its **decorators**,
or any signal of whether it's **public API or an internal detail**. To learn how to
call a function the agent still has to open the file and read the source, which is
exactly the token-wasting behavior Seam exists to eliminate. Worse, full-text search
is blind to parameter and return types: a query like "function that takes a `conn`
and returns an `AffectedResult`" cannot match, because the only FTS-indexed text is
the symbol name and docstring. Two symbols with the same short name (`helper`,
`run`) are also indistinguishable in results — there is no qualified name to tell
them apart.

## Solution

Enrich every extracted symbol with five additional fields captured at parse time —
**signature**, **decorators**, **is_exported**, **visibility**, and
**qualified_name** — across all five supported languages (Python, TypeScript,
JavaScript, Go, Rust). The signature is also fed into the FTS5 index alongside the
name and docstring, so concept and type-shaped queries match on parameter and return
types. `seam_context`, `seam_search`, and `seam_query` surface the new fields in
their output, so an agent can read a function's call shape, see that it is a
`@pytest.fixture` or `@app.route`, and tell public API from internals — all without
opening a single source file. The schema advances v4 → v5 via a guarded, additive
migration that follows the same pattern as the v3 → v4 cluster migration.

## User Stories

1. As an AI coding agent, I want a symbol's full signature returned by `seam_context`, so that I can call it correctly without opening the source file.
2. As an AI coding agent, I want the signature included in `seam_search` results, so that I can pick the right overload or candidate from the result list alone.
3. As an AI coding agent, I want to full-text search on parameter and return types, so that "function returning AffectedResult" surfaces the right symbol even when its name doesn't contain those words.
4. As an AI coding agent, I want to see a symbol's decorators, so that I can recognize framework semantics (`@app.route`, `@pytest.fixture`, `@cached`, `@staticmethod`) without reading the body.
5. As an AI coding agent, I want to know whether a symbol is exported/public, so that I can distinguish the public API surface from internal helpers when reasoning about blast radius.
6. As an AI coding agent, I want a symbol's visibility (public/private/protected), so that I can respect encapsulation when suggesting call sites.
7. As an AI coding agent, I want a qualified name (e.g. `ClassName.method`, `module.func`), so that I can disambiguate two symbols that share a short name.
8. As an AI coding agent working in a Python codebase, I want Python decorators captured verbatim, so that I can tell a route handler from an ordinary function.
9. As an AI coding agent working in TypeScript/JavaScript, I want `export`/`export default` reflected in `is_exported`, so that I know what the module's public surface is.
10. As an AI coding agent working in a Go codebase, I want capitalization-based export detection in `is_exported`, so that exported identifiers (capitalized) are flagged correctly per Go's visibility rule.
11. As an AI coding agent working in a Rust codebase, I want `pub`/`pub(crate)` reflected in visibility and `is_exported`, so that I understand the crate's public API.
12. As an AI coding agent, I want the signature truncated to a sane maximum length, so that a pathologically long signature can't bloat a single result.
13. As an AI coding agent, I want decorators returned as a structured list, so that I can match on individual decorators programmatically.
14. As a developer running `seam init` on a fresh repo, I want the new fields populated automatically, so that no extra step is required to get enrichment.
15. As a developer with an existing v4 index, I want `seam init`/`seam start` to migrate my index to v5 in place, so that I don't have to delete and rebuild it.
16. As a developer with an existing v4 index, I want the migration to be additive and non-destructive, so that my existing symbols, edges, clusters, and comments are preserved.
17. As a developer, I want the FTS5 index rebuilt to include signatures during migration, so that type-shaped search works on my pre-existing index after upgrade.
18. As a developer, I want symbols whose signature/decorators can't be extracted to still be indexed (with NULL fields), so that extraction gaps never drop a symbol.
19. As a developer, I want the parser to never raise on signature extraction, so that one malformed node can't abort the whole file's indexing.
20. As an AI coding agent, I want the new fields to be optional in tool output (omitted or null when absent), so that the response contract stays stable for symbols that lack them.
21. As an AI coding agent, I want `seam_context` to keep returning its existing cluster/peer/edge fields, so that enrichment adds to — never replaces — the current 360° view.
22. As a developer, I want `seam status` to keep working unchanged, so that the enrichment doesn't disturb existing commands.
23. As an AI coding agent, I want signature matches to optionally influence search ranking, so that a query whose terms appear in a signature ranks that symbol sensibly.
24. As a developer, I want the watcher's per-file re-index to populate the new fields too, so that edits after `seam init` keep enrichment current (unlike clusters, which only recompute on full init).
25. As a developer, I want a config knob for the maximum signature length, so that I can tune the truncation cap without code changes.
26. As a maintainer, I want the schema version metadata bumped to 5, so that future migrations can branch correctly on the stored version.
27. As a maintainer, I want the `Symbol` type and `schema.sql` to remain the single sources of truth for the symbol shape, so that the data model stays coherent.

## Implementation Decisions

- **New deep leaf module: signature/field extractor.** A new pure module
  (`seam/indexer/signatures.py`) exposes a single entry point —
  `extract_node_fields(node, language) -> NodeFields` — that, given a tree-sitter
  symbol node and its language, returns the five new fields. It imports only stdlib
  + `tree_sitter` (same leaf contract as `graph_common.py`) and never raises:
  on any extraction failure it returns the field as `None`/empty so the caller can
  still emit the symbol. This is the testable boundary for the whole feature.
- **`NodeFields` shape (from prototype):** the extractor returns a `TypedDict` with
  `signature: str | None`, `decorators: list[str]` (empty when none),
  `is_exported: bool | None`, `visibility: str | None` (`"public" | "private" |
  "protected"`), `qualified_name: str | None`.
- **`Symbol` TypedDict extended.** `graph_common.Symbol` gains the five fields.
  `_make_symbol` accepts and stores them. `graph.py` (Python/TS/JS) and
  `graph_go_rust.py` (Go/Rust) call `extract_node_fields` while building each symbol.
- **Per-language extraction rules:**
  - *signature* — the declaration header text (params + return type / type
    annotations), normalized to a single line and truncated to a max length.
  - *decorators* — Python `decorator` nodes and TS/JS decorators captured verbatim
    (e.g. `@app.route("/x")`); Go/Rust have no decorator construct → empty list.
  - *is_exported* — TS/JS: presence of `export`/`export default`; Go: identifier
    starts with an uppercase letter; Rust: `pub`/`pub(crate)` visibility modifier;
    Python: not single-underscore-prefixed (heuristic, since Python has no export
    keyword) — and respect `__all__` is **out of scope** (see below).
  - *visibility* — Rust: from `pub`/`pub(crate)`/none; TS/JS: from
    `public`/`private`/`protected` modifiers when present; Python: `private` when
    name starts with `_`, else `public`; Go: derived from capitalization.
  - *qualified_name* — reuse the existing enclosing-scope resolution
    (`ClassName.method`, receiver-qualified for Go, `Type.fn` for Rust); plain name
    for top-level symbols.
- **Schema v4 → v5 migration (additive + FTS rebuild).** `db.py`:
  - `ALTER TABLE symbols ADD COLUMN` for `signature`, `decorators` (TEXT, JSON-encoded
    list), `is_exported` (INTEGER, 0/1/NULL), `visibility` (TEXT), `qualified_name`
    (TEXT) — all nullable, all defaulting to NULL on existing rows.
  - Recreate the FTS5 virtual table and its three sync triggers to index
    `(name, docstring, signature)` instead of `(name, docstring)` — fts5 columns
    cannot be altered, so the table is dropped and rebuilt, then repopulated from the
    `symbols` content table.
  - Bump `metadata.schema_version` to `5`; fresh DBs are seeded at v5 directly so a
    brand-new `seam init` triggers no migration advisory (same convention as v4).
  - The migration is guarded: it runs only when the stored version is < 5, and each
    `ALTER` is wrapped so re-running is a no-op.
- **`upsert_file` writes the new columns.** The shared write path persists the five
  fields; `decorators` is JSON-encoded on write and decoded on read.
- **Read path surfaces the fields.** `engine.context()` and
  `engine.search()`/`query()` select and return the new columns; `decorators` is
  decoded back to a list. Fields that are NULL are returned as `null`/omitted so the
  output contract is stable.
- **MCP handlers pass through.** `server/tools.py` handlers for context/search/query
  include the new fields in their JSON output; no new tool is added.
- **Optional ranking signal.** `query/fts.rescore` may add a small boost when query
  terms appear in a candidate's signature; this is additive to the existing
  name/path/test/cluster signals and must not regress current rankings.
- **Config knob.** `seam/config.py` gains `SEAM_MAX_SIGNATURE_LEN` (default ~300
  chars) controlling signature truncation, read only via the config module.
- **Watcher parity.** Because per-file re-index goes through the same
  `indexer/pipeline.py` path, enrichment is populated on watcher edits automatically
  — no separate watcher change needed (contrast with clusters, which only recompute
  on full `seam init`).

## Testing Decisions

- **What makes a good test here:** assert on *external behavior* — the fields a
  symbol carries after extraction, the JSON a tool handler returns, and the rows in
  the DB after a migration — not on private helper internals. Drive extraction
  through real fixtures so the tree-sitter grammar is exercised, not mocked.
- **`signatures.py` (unit, deep-module boundary):** the primary target. For each of
  the five languages, parse a fixture and assert the extracted `signature`,
  `decorators`, `is_exported`, `visibility`, and `qualified_name` for representative
  symbols (a decorated function, an exported class, a private/underscore symbol, a
  method on a class, a Go capitalized vs lowercase func, a Rust `pub` vs private fn).
  Include a "malformed/edge node returns Nones, never raises" case.
- **Migration v4 → v5 (integration):** build a v4 index, run the migration, assert
  the new columns exist, existing rows are preserved with NULL new fields, the FTS5
  table now indexes signatures, and `schema_version` is `5`. Assert idempotency
  (running twice is a no-op). Mirror the existing v3→v4 migration test.
- **Engine read path (integration):** index a fixture end-to-end and assert
  `context()`/`search()`/`query()` return the new fields with correct values and
  stable shape when fields are NULL.
- **FTS signature search (integration):** assert a query matching only on a parameter
  or return type in the signature surfaces the symbol (the headline new capability).
- **MCP handler passthrough (integration):** assert `seam_context`/`seam_search`
  JSON includes the new fields, extending the existing `test_mcp_tools.py` suite.
- **Prior art:** `tests/unit/test_fts.py`, `tests/unit/test_affected.py` (deep-module
  unit style), `tests/unit/test_hardening.py` (migration/extraction guards),
  `tests/integration/test_mcp_tools.py` and `tests/integration/test_cli_json.py`
  (handler/contract style), and the fixtures in `tests/fixtures/`.

## Out of Scope

- **Column spans** (`start_column`/`end_column`) — listed in §2.5 but deferred; not
  required for the enrichment win and adds extraction surface.
- **The remaining §2.5 node kinds** (expanding from 5 kinds toward CodeGraph's 22)
  and the new edge kinds (`extends`/`implements`/`overrides`/`instantiates`) — those
  belong to roadmap item 5 (import resolution), a separate phase.
- **Import resolution → confidence promotion** (§8 item 5) — not touched here.
- **Python `__all__`-based export detection** — `is_exported` uses the
  underscore-prefix heuristic only; honoring `__all__` is a future refinement.
- **`is_async`/`is_static`/`is_abstract`/`type_parameters`** boolean/JSON flags from
  §2.2 — deferred; the five chosen fields are the highest-leverage subset.
- **Any new MCP tool or CLI command** — this phase enriches existing tool output
  only.
- **Re-clustering or re-scoring changes beyond the optional signature boost.**

## Further Notes

- This phase deliberately multiplies the Phase 3 search work: signatures in FTS5 turn
  the OR-join + rescore search into a type-aware retrieval, which is the compounding
  payoff for landing the search fix first.
- Seam keeps its differentiators intact: still zero external services, SQLite-only,
  parsers-never-raise, edges keyed on string names. Enrichment is purely additive.
- The FTS5 rebuild is the single riskiest migration step; it must repopulate from the
  `symbols` content table and be verified by the migration test before merge.
- Schema authority remains `docs/database/schema.sql` (to be bumped to v5) and the
  `Symbol` TypedDict in `graph_common.py`.
