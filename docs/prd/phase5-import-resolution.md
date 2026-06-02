# Phase 5 — Import Resolution → Confidence Promotion + Builtin Filtering

> Correctness tier, item 5 of the import roadmap (`.claude/research/codegraph-vs-seam.md` §8 / §3.5 A+B+C+D, build sequence B→C→A→D).
> Builds directly on the existing read-time confidence resolver (`seam/analysis/confidence.py`).

## Problem Statement

Seam resolves every edge's confidence tier (EXTRACTED / AMBIGUOUS / INFERRED) by a single
whole-index rule: a target name that appears **once** in the index is EXTRACTED, a name that
appears **more than once** is AMBIGUOUS, and a name that is **absent** is INFERRED. This rule
is global and name-only — it never looks at *how* a reference actually binds. Three consequences
hurt agents relying on Seam to reason about a real codebase:

1. **Homonym-collapse.** Two files each define `parse()`. Every call to `parse()` anywhere in the
   repo collapses to AMBIGUOUS, even when the calling file `from app.json import parse` makes the
   binding unambiguous. The import statement that disambiguates it is sitting right there in the
   file, indexed, and ignored. `seam impact`, `seam trace`, and `seam_context` all over-report
   ambiguity as a result.

2. **Stdlib/builtin noise.** A call to `len()`, `print()`, `console.log`, `make()`, or `Vec::new`
   resolves to INFERRED ("external/unindexed") — indistinguishable from a genuinely unresolved
   user symbol. Agents can't tell "this is the language builtin" from "this is a dependency we
   didn't index" from "this is a typo / dead reference."

3. **Tiers are unexplained.** An edge says `AMBIGUOUS` but never says *why* — was it a name
   collision, a fuzzy guess, an unresolved external? An agent (or a human debugging the index)
   has no provenance to trust or challenge the tier.

## Solution

Teach the read-time resolver to use the evidence already in the index — import statements and a
curated builtin vocabulary — and to **explain itself**:

- **(B) `resolved_by` provenance.** Every resolved edge reports *how* it reached its tier:
  `import` (promoted via a resolved import), `name-unique`, `name-collision`, `builtin`,
  or `unresolved`. Surfaced in MCP/CLI output so agents and humans can trust or audit the tier.

- **(C) Builtin/stdlib filtering.** A target whose name is a known language builtin/stdlib symbol
  is tagged `builtin` (resolved_by) instead of a misleading INFERRED "external" — **but only when
  nothing in the repo declares that name.** A user who writes their own `def get()` keeps a normal
  resolution; the builtin set never shadows a real repo symbol.

- **(A) Import resolution → tier promotion.** When a colliding reference is bound by a same-file
  import that resolves to exactly one declaring file, the edge is **promoted to EXTRACTED**
  (`resolved_by: import`) — even though the name collides globally. This is the core homonym fix.

- **(D) Proximity scoring for residual AMBIGUOUS.** For collisions that import resolution can't
  settle (no import, dynamic, star-import), rank the candidate declarations by file-path proximity
  to the referencing file. The edge stays AMBIGUOUS (we don't manufacture false certainty) but
  reports its best-proximity candidate so downstream tools and humans have a most-likely target.

All of this stays **read-time** — consistent with Seam's existing model where stored confidence is
a debugging hint and the authoritative tier is computed against the live index on every query. No
write-amplification, no staleness after an incremental watcher re-index.

## User Stories

1. As an agent calling `seam_trace`, I want a call bound by `from app.json import parse` to resolve
   to the specific `parse` in `app/json.py`, so that the trace path is correct even though another
   `parse` exists elsewhere.
2. As an agent calling `seam_impact`, I want import-resolved edges to read EXTRACTED instead of
   AMBIGUOUS, so that the blast radius isn't inflated by false ambiguity.
3. As an agent, I want each edge to tell me *how* its confidence was decided (`resolved_by`), so
   that I can weight EXTRACTED-via-import higher than EXTRACTED-via-name-uniqueness.
4. As an agent, I want calls to language builtins (`len`, `print`, `console.log`, `make`,
   `Vec::new`) tagged `builtin`, so that I don't chase them as unresolved dependencies.
5. As a developer who wrote my own `def get()`, I want my function to keep its real resolution and
   NOT be silently treated as a builtin, so that builtin filtering never hides my code.
6. As an agent debugging a homonym, I want a residual AMBIGUOUS edge to report its
   highest-proximity candidate declaration, so that I have a most-likely target to investigate.
7. As an agent, I want a reference with no matching import, no repo declaration, and no builtin
   match to read `resolved_by: unresolved`, so that genuinely dangling references are visible.
8. As a Python developer, I want `from pkg.mod import Thing` and `import pkg.mod` to both feed
   import resolution, so that both import styles disambiguate references.
9. As a TypeScript developer, I want `import { foo } from './bar'`, default imports, and namespace
   imports to feed resolution, so that ES module bindings disambiguate references.
10. As a Go developer, I want `import "module/pkg"` and `pkg.Func` references to resolve through the
    package import, so that cross-package calls aren't reported as ambiguous.
11. As a Rust developer, I want `use crate::mod::Thing` to feed resolution, so that path-qualified
    references disambiguate.
12. As an agent, I want import resolution to follow each language's file-extension order (Python
    `.py` / `/__init__.py`, Rust `.rs` / `/mod.rs`, TS `.ts`/`.tsx`/`.js`, Go package dir, JS
    `.js`/`.mjs`/`.cjs`), so that an import source maps to the right declaring file.
13. As an agent, I want a relative import (`from .sibling import x`, `./bar`) resolved against the
    referencing file's directory, so that intra-package imports resolve correctly.
14. As an agent, I want an import whose source resolves to a file but whose name is **not** declared
    there to fall back to the name-count rule rather than falsely promote, so that promotion never
    invents a wrong EXTRACTED edge.
15. As an agent, I want an import whose source can't be resolved to any indexed file (third-party
    package) to leave the edge at its name-count tier with `resolved_by` reflecting that, so that
    unresolved imports don't crash or mislead.
16. As an operator, I want the new `import_mappings` data populated on `seam init` and refreshed
    per-file by the watcher, so that resolution reflects the current code without a full re-index.
17. As an operator running an old index, I want the v5→v6 migration to run automatically on
    `connect()` (additive table creation, fresh-DB-safe), so that reads never crash with
    `no such table` after upgrade.
18. As an operator, I want `resolved_by`/promotion to stay correct after the watcher re-indexes a
    single edited file, so that incremental edits don't degrade resolution (read-time guarantee).
19. As an agent calling `seam_context`, I want the symbol's incoming/outgoing edges to carry both
    the promoted confidence and `resolved_by`, so that the 360° view reflects real binding.
20. As an agent, I want builtin filtering to be language-scoped (a Python builtin name doesn't
    suppress a Go edge of the same name), so that cross-language homonyms aren't wrongly filtered.
21. As an agent, I want the builtin vocabulary to cover all five indexed languages (Python, TS, JS,
    Go, Rust), so that builtin tagging is consistent regardless of language.
22. As a developer, I want a config knob to cap import-resolution work per query
    (candidate/mapping limits), so that resolution can't blow up read latency on a huge index.
23. As a developer, I want proximity scoring bounded (only run when a collision has a small,
    capped candidate set), so that the tie-break never dominates query time.
24. As an agent, I want resolution to degrade gracefully — any parse/resolve failure leaves the
    edge at its name-count tier and never raises — so that one malformed import can't break a query.
25. As a maintainer, I want the import-mapping extractor and the path resolver to be pure, deeply
    testable modules with no DB or AST coupling at their interface, so that resolution logic is
    unit-tested in isolation.
26. As a maintainer, I want the resolver's output to remain backward compatible — callers that only
    read the confidence string keep working — so that `resolved_by` is purely additive.
27. As an agent, I want star/glob imports (`from x import *`, `use x::*`) to NOT promote (they don't
    bind a specific name) but to optionally inform proximity, so that wildcard imports don't
    manufacture false EXTRACTED edges.
28. As a developer, I want aliased imports (`import numpy as np`, `import { x as y }`) to map the
    *local* alias to the *exported* name, so that a call to the alias resolves to the real symbol.

## Implementation Decisions

### New modules (deep, leaf, isolated)

- **`seam/analysis/imports.py`** — the import-resolution engine. Two responsibilities behind a small
  interface:
  - `extract_import_mappings(root, filepath, language) -> list[ImportMapping]` — parse the
    referencing file's imports into `{local_name, exported_name, source_module, is_default,
    is_namespace, is_wildcard}` records (one source of truth for all five languages). Mirrors the
    existing tree-sitter extraction style in `graph.py`; never raises (returns `[]` on failure).
  - `resolve_import_source(source_module, referencing_file, repo_root, language) -> list[str]` —
    map an import source to candidate declaring-file paths using a per-language extension-resolution
    order (Python `['.py', '/__init__.py']`, Rust `['.rs', '/mod.rs']`, TS
    `['.ts','.tsx','.d.ts','.js']`, JS `['.js','.mjs','.cjs','/index.js']`, Go = package directory).
    Relative sources resolve against the referencing file's directory; non-relative/third-party
    sources that don't map to an indexed file return `[]`.
  - `compute_path_proximity(referencing_file, candidate_file) -> int` — pure path-distance score
    (shared-prefix-segment based; same-dir highest). Used only by D.
  - Imports ONLY stdlib + `seam.indexer` types as needed; no DB, no `seam.query`. Leaf in the import
    hierarchy alongside `confidence.py`.

- **`seam/analysis/builtins.py`** — curated builtin/stdlib vocabulary. `is_builtin(name, language)
  -> bool` over static, language-keyed frozensets for Python, TS, JS, Go, Rust. Pure, no I/O.
  Deliberately conservative: common builtins/globals/prelude + high-traffic stdlib names — NOT an
  exhaustive stdlib mirror (an over-broad set risks shadowing real repo symbols; the
  repo-declares-it guard is the safety net, but a tight list is the first line of defense).

### Modified modules

- **`seam/analysis/confidence.py`** — the orchestration point. The pure `resolve()` rule is
  extended (or wrapped by a new `resolve_edge()` that returns a small `Resolution` record carrying
  `confidence` + `resolved_by` + optional `best_candidate`). Resolution order:
  1. If a same-file import binds the target to exactly one indexed declaring file → EXTRACTED,
     `resolved_by: import`.
  2. Else apply the existing name-count rule. count==1 → EXTRACTED `name-unique`; count>1 →
     AMBIGUOUS `name-collision` (run D to attach `best_candidate`); count==0 → check builtins.
  3. count==0 and `is_builtin(name, lang)` → INFERRED `builtin`. Else INFERRED `unresolved`.
  - The builtin check fires **only** when count==0 (nothing in the repo declares the name) — this is
    the "user `def get()` keeps its edge" guarantee, enforced structurally by ordering.
  - Existing `resolve(name, name_counts) -> str` is kept as a thin compatibility shim so current
    callers that want only the string don't break (story 26).
  - A new `load_import_mappings(conn, file_path) -> list[ImportMapping]` loads a referencing file's
    mappings (one query, mirroring `load_name_counts`). Resolution is per (referencing-file, target).

- **`seam/indexer/`** (db.py, pipeline.py, graph extractors) — populate `import_mappings` at index
  time. The pipeline already parses each file's AST; the new extractor runs in the same pass and
  upserts mappings keyed by `file_id`. Per-file delete-then-insert mirrors `upsert_file`/`delete_file`
  so the watcher refreshes mappings incrementally.

- **Read path** (`seam/query/engine.py`, `seam/analysis/traversal.py`, `seam/analysis/flows.py`,
  `seam/server/tools.py`, `seam/cli/main.py`) — thread `resolved_by` (and `best_candidate` where
  meaningful) through edge/hop results into MCP + CLI output. Purely additive fields.

### Schema change — v5 → v6 (additive)

- New table `import_mappings(id, file_id, local_name, exported_name, source_module, is_default,
  is_namespace, is_wildcard, line)` with an index on `file_id` and on `local_name`. FK semantics
  match `symbols`/`edges` (string-name model unaffected — this is mapping metadata, not graph data).
- `connect()` runs the guarded v5→v6 migration inline (create table if absent, fresh-DB-safe, bump
  `schema_version` to '6') so reads never crash post-upgrade. **Mappings are NOT backfilled** — only
  a `seam init` (or per-file watcher re-index) populates them; until then resolution silently falls
  back to the name-count rule (documented gotcha, mirrors the Phase 4 backfill caveat).

### Config (all via `seam/config.py`)

- `SEAM_BUILTIN_FILTERING` — `"on" | "off"` (default `"on"`).
- `SEAM_IMPORT_RESOLUTION` — `"on" | "off"` (default `"on"`).
- `SEAM_MAX_IMPORT_CANDIDATES` — cap on candidate declaring files evaluated per reference (default 25).
- `SEAM_PROXIMITY_MAX_CANDIDATES` — cap on collision candidates ranked by D (default 25).

### Provenance vocabulary (`resolved_by`)

`import` | `name-unique` | `name-collision` | `builtin` | `unresolved`. Stable string enum,
surfaced verbatim in MCP/CLI output. `null` for pre-v6 / unresolved-context rows (treated as
"unknown", same null-contract as Phase 4 enrichment fields).

## Testing Decisions

Good tests assert **external behavior**, not internals: given an index state and a referencing
file, the resolver returns a specific (confidence, resolved_by) — not how it walked the AST.

Modules tested (the deep, isolated ones get the heaviest unit coverage):

- **`imports.py` (unit)** — per-language: extract mappings for every import form (plain, from-import,
  aliased, default, namespace, wildcard, relative); `resolve_import_source` extension-order +
  relative resolution + third-party-returns-empty; `compute_path_proximity` ordering. Prior art:
  `tests/unit/test_signatures.py` (per-language pure-function table tests, never-raises contract).
- **`builtins.py` (unit)** — `is_builtin` true/false per language; cross-language isolation
  (Python builtin name is not a Go builtin). Tiny, exhaustive.
- **`confidence.py` (unit)** — the resolution-order matrix: import-promotion beats collision;
  builtin fires only at count==0; user-declared name beats builtin; proximity attaches on residual
  collision; compatibility shim still returns a bare string. Prior art: existing
  `tests/unit` confidence tests.
- **Migration (integration)** — v5→v6 auto-migrate on `connect()` creates the table, is idempotent,
  fresh-DB-safe, bumps version; pre-v6 index reads without crashing. Prior art:
  `tests/integration/test_migration_v5.py`.
- **End-to-end (integration)** — index a fixture with a deliberate homonym + an import that
  disambiguates it; assert `seam_trace`/`seam_impact`/`seam_context` report EXTRACTED
  `resolved_by: import` for the imported binding and AMBIGUOUS for the un-imported one; assert a
  builtin call reports `builtin`; assert a watcher re-index of the referencing file preserves
  resolution. Prior art: `tests/integration/test_phase4_read_layer.py`.

`make gate` (ruff + mypy + pytest) must stay green. Format only edited files individually — never a
tree-wide `make fmt`/`ruff format`.

## Out of Scope

- **tsconfig/jsconfig path aliases, Go-module prefix stripping, `-I` include dirs** — the §3.1
  long tail of resolution. This phase ports relative-path + extension-order resolution only;
  alias/module-map resolution is a follow-up.
- **Barrel / re-export chasing** (`export * from`, recursive re-exports) — §3.1 step 4. Deferred.
- **The 14 framework resolvers** — explicitly rejected in §8 (import the mechanism, not the zoo).
- **Callback / interface-dispatch synthesis** (§3.5 E) and **edge-kind promotion** (§3.5 F) — later
  tiers, depend on this phase landing first.
- **Persisting `resolved_by` as an edge column** — resolution stays read-time; `resolved_by` is a
  computed output field, not stored graph state (consistent with how stored confidence is treated
  as a non-authoritative hint).
- **Exhaustive stdlib mirroring** — the builtin set is curated/common, not a full stdlib index.
- **New MCP tool** — count stays at 9; existing tools' output gains `resolved_by`.

## Further Notes

- The unifying lesson from the research doc holds: *Seam already has the data model; it's missing
  the pass.* Import statements are already extracted as `import` edges — but they drop the source
  module. The new `import_mappings` table captures the `{local→source}` binding that the edge throws
  away, which is the single missing input to resolution.
- Keeping resolution read-time is the load-bearing architectural choice: the watcher re-indexes a
  changed file's mappings in place, and the next query resolves against fresh state with zero
  back-fill or write-amplification — the same invariant `analysis/confidence.py` already guarantees
  for name-count resolution.
- The `count==0` ordering of the builtin check is not an optimization — it is the correctness
  guarantee for story 5 (a user `def get()` must never be filtered as the builtin `get`). It must be
  enforced by control flow, not by a hopeful set-membership check.
