# PRD — Phase 1b: DB-wide confidence resolution (issue #9)

> Slice of Phase 1b. Parent: issue #9. Status: ready-for-agent.

## Problem Statement

As an AI agent using `seam impact` / `seam_trace` to decide whether an edit is safe,
I cannot trust the `confidence` signal for the case I care about most — **cross-file
blast radius**. Today confidence is resolved at extraction time against *same-file*
symbols only. Because almost every real call/import edge crosses a file boundary, in
practice nearly every edge is tagged `INFERRED`. Live example: `seam impact connect`
returns essentially all `INFERRED`, with only the same-file `init_db→connect` edge as
`EXTRACTED`. The signal is honest but gives almost no discrimination where it counts,
so I end up re-verifying everything by hand — defeating the purpose of the tool.

## Solution

As an agent, I want edge confidence resolved against the **full index** at read time,
so that a cross-file edge whose callee name is unique in the codebase reads as
`EXTRACTED` (trust it), a name shared by several symbols reads as `AMBIGUOUS` (verify),
and a name that isn't indexed at all reads as `INFERRED` (external/unknown). The
confidence on `seam impact`, `seam_trace`, and `seam_changes` becomes meaningfully
discriminating across files, so I know which conclusions to lean on and which to check.

The resolution rule (unchanged in spirit, widened in scope from same-file to whole-index):

- target callee name resolves to **exactly one** indexed symbol → `EXTRACTED`
- resolves to **more than one** indexed symbol → `AMBIGUOUS`
- resolves to **none** (external / unindexed) → `INFERRED`

## User Stories

1. As an agent, I want a cross-file edge whose target name is unique in the index to be reported as `EXTRACTED`, so that I can trust that blast-radius conclusion without re-grepping.
2. As an agent, I want a cross-file edge whose target name is shared by more than one indexed symbol to be reported as `AMBIGUOUS`, so that I know to verify which symbol is actually meant.
3. As an agent, I want an edge whose target is not indexed at all (stdlib, third-party, dynamic) to remain `INFERRED`, so that external dependencies are clearly flagged as unverifiable.
4. As an agent running `seam impact <symbol>`, I want each tiered dependent's aggregated path confidence computed from whole-index resolution, so that the WILL_BREAK / LIKELY_AFFECTED tiers reflect real cross-file certainty.
5. As an agent running `seam_trace A B`, I want each hop's confidence resolved against the whole index, so that I can spot the exact hop in a path that rests on an ambiguous name.
6. As an agent, I want `callers`/`callees` neighborhoods in trace results to carry whole-index confidence, so that the immediate neighborhood is as trustworthy as the path.
7. As an agent running `seam_changes`, I want the risk rollup and `ambiguous_warning` to be driven by whole-index confidence, so that the pre-commit risk level reflects real cross-file certainty rather than a uniform "everything is INFERRED".
8. As an agent, I want the path-confidence aggregation (weakest hop wins; strongest among equal-distance paths) to keep working unchanged, so that existing tier semantics stay stable.
9. As an agent, I want a target name that is indexed but in a *different* file to still resolve as `EXTRACTED` when unique, so that the same-file restriction no longer suppresses the signal.
10. As an agent, I want confidence to stay correct after the watcher incrementally re-indexes a single file, so that adding or removing a symbol that makes a name newly unique/ambiguous is reflected on the very next query without a full rebuild.
11. As a maintainer, I want the whole-index resolution rule to live in one small, testable module, so that the EXTRACTED/AMBIGUOUS/INFERRED definition has a single source of truth.
12. As a maintainer, I want the stored `edges.confidence` column to keep its existing same-file value as a cheap debugging hint, so that no schema migration or extraction-test churn is required.
13. As a maintainer, I want it documented that read-time global resolution is authoritative and overrides the stored column, so that nobody is confused by the two values.
14. As a maintainer, I want zero new runtime dependencies and no new network/LLM calls, so that the project's zero-external-services rule holds.
15. As a maintainer, I want the global name-count map loaded once per query (not per edge), so that the read path stays fast on large indexes.
16. As an agent, I want resolution to key on the edge's `target_name` (the callee/importee) regardless of traversal direction, so that upstream and downstream walks agree on a given edge's confidence.

## Implementation Decisions

- **Resolution moves to read time (query-time), in the analysis layer.** Confirmed via design decision: query-time over index-time post-pass. Rationale: confidence is a property of *global* state; the project's non-negotiable "edges store string names, not IDs — required for independent re-indexing" means an index-time global pass would have to re-resolve edges in *other* files on every incremental watcher update (write amplification + staleness). Read-time resolution is always correct under incremental re-index and keeps edge extraction file-local and pure.

- **New deep module: whole-index confidence resolver.** A small module in the analysis layer encapsulating the entire EXTRACTED/AMBIGUOUS/INFERRED rule behind a tiny interface:
  - a function that loads a `name -> count` map from the symbols table in one `GROUP BY` query, given a connection;
  - a pure function that maps `(target_name, name_counts) -> confidence` per the rule above.
  This is the single source of truth for the rule. It depends only on stdlib + the connection. It sits in the read-only analysis layer (import rule: `cli → server → analysis → query → indexer/db`; analysis may read the DB, must not import server/cli).

- **`traversal.walk` resolves hop confidence from the global map.** `walk` builds the name-count map once at the start of the walk and resolves each hop's confidence from the **edge's `target_name`** via the resolver, overriding the stored `edges.confidence`. The neighbor-fetch helper is extended to return the edge's `target_name` so the resolver can key on it consistently for both directions. The existing weakest-hop / strongest-among-equal-distance aggregation is unchanged.

- **`flows.trace` / `callers` / `callees` resolve from the global map.** Same approach: load the map once per call, resolve per-hop confidence from each edge's `target_name`, keep the existing dedup-keep-strongest logic.

- **`engine.context()` is unchanged.** Its `ambiguous` flag is already computed from a whole-index count, consistent with the new rule.

- **Stored `edges.confidence` column: kept, unchanged.** `graph.extract_edges` continues to write same-file confidence (a valid lower-bound hint, useful for debugging). No schema migration. Read-time global resolution is authoritative and overrides it.

- **No API-contract shape change.** The `confidence` field on `seam_impact` / `seam_trace` / `seam_changes` keeps the same type and the same three values; only the *resolution scope* widens from same-file to whole-index. Docs (`CONTRACT.md`, `mcp-tools.yaml`) and the `graph.py` resolution-scope note are updated to describe read-time resolution.

## Testing Decisions

- **What makes a good test here:** assert external behavior — given a set of indexed symbols and edges, the confidence values surfaced by `walk` / `trace` / the resolver are correct — not internal call sequences. Tests build a tiny in-memory/temp index (symbols across multiple files + edges) and assert the resolved confidence.
- **Modules tested:**
  - The new resolver module — unit tests for the three-way rule: unique name → EXTRACTED, duplicated name (across files) → AMBIGUOUS, absent name → INFERRED; empty map; map loaded from a real connection.
  - `traversal.walk` — a cross-file edge to a uniquely-named target now reports `EXTRACTED` (the regression this fixes); a target name duplicated across two files reports `AMBIGUOUS`; an unindexed target stays `INFERRED`; weakest-hop aggregation across a multi-hop cross-file path still holds.
  - `flows.trace` — per-hop confidence reflects whole-index resolution; `callers`/`callees` reflect it too.
- **Prior art:** `tests/unit/test_confidence.py`, `tests/unit/test_traversal.py`, `tests/unit/test_impact.py`, and the integration handler tests under `tests/integration/`. Follow their fixture style (temp DB via the indexer, or hand-built rows) and assertion style.
- **TDD:** write the failing test that asserts a cross-file unique target is `EXTRACTED` first; watch it fail against current same-file behavior; implement the resolver + wire it in; watch it pass.

## Out of Scope

- Semantic comment nodes (`seam_why`) — separate Phase 1b slice.
- Go / Rust parsers (ADR-005) — separate Phase 1b slice.
- Issue #10 (test-caller filtering) and issue #11 (configurable detect_changes cap) — separate slices.
- Resolving confidence by *kind* (call vs import) or by symbol kind — out of scope; resolution keys on name only, as today.
- Any change to how edges are extracted or stored (no schema migration, no new columns).
- Disambiguating *which* of several same-named symbols an ambiguous edge points to — AMBIGUOUS deliberately signals "verify", it does not pick a winner.

## Further Notes

- Performance: the name-count map is a single `SELECT name, COUNT(*) FROM symbols GROUP BY name` loaded once per query and held in memory for that query — negligible next to the existing batched neighbor fetches. No per-edge DB round-trips.
- Honesty preserved: external/unindexed targets stay `INFERRED`, so the tool never over-claims certainty about code it cannot see.
- Acceptance criteria (from issue #9): cross-file unique → EXTRACTED; shared name → AMBIGUOUS; external → INFERRED; `seam impact` / `seam_trace` confidence becomes discriminating cross-file; contract docs + tests updated; `make gate` green.
