# PRD — Seam Phase 1 (Core): Trustworthy Code Reasoning

> Status: ready-for-agent · Phase 1 (Core slice) · Author: synthesized via /to-prd · 2026-06-01
> Supersedes the relevant Phase-1 non-requirements in `PRD.md §4`. Respects ADR-003 (heuristic flows, zero-LLM) and ADR-005 (language scope).
> Deferred to a separate **Phase 1b** PRD: semantic comment nodes, Go/Rust parsers.

---

## Problem Statement

Phase 0 made Seam a fast, local **map** of a codebase: an agent can ask "where is the code about X?" (`seam_query`), "what does this symbol look like?" (`seam_context`), and "find this text" (`seam_search`). That answers *where things are* — but not *what happens if I touch them*.

Today, when an agent (or the developer) is about to change a function, it has no trustworthy way to ask:

- "If I change `upsert_file`, what breaks?" — there is no blast-radius analysis.
- "My branch changed these files — what's the risk, and what should I re-test?" — there is no change-impact summary; the developer falls back to reading the whole diff and guessing.
- "How does control actually get from the CLI entry point to the database write?" — there is no way to trace a multi-hop call chain; the agent greps and reconstructs it by hand.

Worse, the graph the agent *would* reason over is known to be imprecise: edges store **string names, not symbol IDs** (ADR rationale: independent re-indexing). When two symbols share a name, their caller/callee counts are conflated and `seam_context` returns an arbitrary one of them. Any blast-radius answer built on top would silently over- or under-count — and the agent would have no signal that the answer is shaky. An unreliable impact tool is worse than none: it teaches the agent to trust a wrong answer.

This is the exact capability gap that keeps the project dependent on GitNexus for its own development (see `CLAUDE.md` — Seam is being built to supersede it).

## Solution

Phase 1 (Core) turns the static map into a **reasoning engine** the agent can trust, by building one shared graph-traversal capability and exposing it three ways, on a hardened, **confidence-aware** edge graph.

From the user's perspective:

1. **Trustworthy edges.** Every edge carries a confidence tag — `EXTRACTED` (resolved to exactly one symbol), `INFERRED` (resolved by heuristic), or `AMBIGUOUS` (the name matched more than one symbol). Edge extraction also gets richer (calls inside arrow functions, namespace and aliased imports). Every downstream answer reports the weakest confidence on the path, so the agent knows which conclusions to lean on and which to verify by reading.

2. **Impact (blast radius).** `seam_impact("upsert_file")` returns the symbols that depend on it, grouped into risk tiers by distance — direct dependents that **will break** (d=1), **likely-affected** indirect dependents (d=2), and **may-need-testing** transitive ones (d=3) — each annotated with the path's confidence. Direction is selectable: upstream (who depends on me), downstream (what I depend on), or both.

3. **detect_changes (pre-commit risk).** `seam_changes(base_ref="main")` diffs the working tree / staged set / branch against a git ref, maps each changed line range back to the symbols it touched, runs those through impact, and returns an overall **risk level** plus the affected symbols — the answer to "what did I actually change, and what's downstream of it" before committing.

4. **Flow tracing (on demand).** `seam_trace("init", "upsert_file")` returns the call path(s) connecting two symbols; `callers`/`callees` walk one hop out. Heuristic, computed live from the edge graph per ADR-003 — no precomputed processes, no new staleness surface.

All of it stays inside the Phase-0 envelope: SQLite only, zero external services, zero API keys, heuristic (no LLM).

---

## User Stories

### Trustworthy edges (foundation)

1. As an **AI agent**, I want each edge to carry a confidence tag, so that I can tell a definite caller from a guessed one before I rely on it.
2. As an **AI agent**, I want an edge whose target name resolves to exactly one symbol to be tagged `EXTRACTED`, so that I treat high-certainty dependencies as facts.
3. As an **AI agent**, I want an edge resolved by a heuristic (e.g. an aliased import binding) to be tagged `INFERRED`, so that I weight it as probable but not certain.
4. As an **AI agent**, I want an edge whose target name matches multiple symbols to be tagged `AMBIGUOUS`, so that I never silently over-count callers on a name collision.
5. As a **developer dogfooding Seam on a TypeScript project**, I want calls made inside arrow functions to produce edges, so that modern JS/TS call graphs aren't full of holes.
6. As a **developer**, I want namespace imports (`import * as ns`) and aliased imports (`import {a as b}`) to resolve to the right target name, so that import edges point where they should.
7. As an **AI agent**, I want `seam_context` on a name shared by two symbols to tell me the result is ambiguous, so that I know to disambiguate rather than trust one arbitrary definition.

### Impact (blast radius)

8. As an **AI agent**, I want to ask for the blast radius of a symbol, so that I can report what breaks before I edit it.
9. As an **AI agent**, I want upstream impact (who depends on the target), so that I find every call site I must update.
10. As an **AI agent**, I want downstream impact (what the target depends on), so that I understand what I might break by changing the target's behavior.
11. As an **AI agent**, I want bidirectional impact in one call, so that I get the full neighborhood without two round trips.
12. As a **developer**, I want impact results grouped into risk tiers by distance (will break / likely affected / may need testing), so that I triage the must-fix set from the nice-to-check set.
13. As an **AI agent**, I want each impacted symbol annotated with the weakest confidence on its path to the target, so that I distinguish "definitely breaks" from "might break — verify."
14. As an **AI agent**, I want to cap traversal depth, so that impact on a hub symbol returns a bounded, useful answer instead of half the codebase.
15. As an **AI agent**, I want impact traversal to terminate on cycles, so that mutually-recursive code doesn't hang the query.
16. As an **AI agent**, I want impact on an unknown symbol to return an empty result (not an error), so that I can probe safely.
17. As an **AI agent**, I want impact file paths returned relative to the project root, so that the answer is portable.

### detect_changes (pre-commit risk)

18. As a **developer**, I want to see which symbols my uncommitted changes touched, so that I know the surface of my own edit.
19. As a **developer**, I want to compare my branch against a base ref (e.g. `main`), so that I see everything the branch changed, not just unstaged work.
20. As a **developer**, I want to scope the change check to staged / working-tree / branch, so that I can check exactly the set I'm about to commit.
21. As a **developer**, I want each changed line range mapped back to the symbol(s) that own it, so that I think in symbols, not raw diff hunks.
22. As a **developer**, I want changed symbols run through impact automatically, so that I see what's downstream of my change without a second command.
23. As a **developer**, I want an overall risk level (low / medium / high / critical) for the change set, so that I get a one-glance go/no-go signal before committing.
24. As an **AI agent**, I want `detect_changes` to flag when a change touches a symbol with many `AMBIGUOUS` edges, so that I warn the developer the risk estimate is uncertain.
25. As a **developer**, I want a change that touches only untracked/added files to still report (as new symbols), so that brand-new code isn't invisible to the risk check.
26. As a **developer working in a non-git directory**, I want `detect_changes` to fail with a clear message, so that I understand why it can't run.

### Flow tracing (on demand)

27. As an **AI agent**, I want to trace the call path(s) between two symbols, so that I can explain how control reaches one from the other without grepping.
28. As an **AI agent**, I want the direct callers of a symbol, so that I can answer "who calls this?" in one hop.
29. As an **AI agent**, I want the direct callees of a symbol, so that I can answer "what does this call?" in one hop.
30. As an **AI agent**, I want trace paths annotated with per-edge confidence, so that I flag any hop that rests on an ambiguous edge.
31. As an **AI agent**, I want trace to return empty when no path exists, so that "not connected" is a real, distinguishable answer.
32. As an **AI agent**, I want trace to terminate on cycles within the depth cap, so that recursive call graphs don't loop forever.

### Cross-cutting (CLI + MCP surface)

33. As a **developer**, I want `seam impact <symbol>` on the CLI, so that I can check blast radius without an MCP client.
34. As a **developer**, I want `seam changes [--base <ref>]` on the CLI, so that I can run the pre-commit risk check in a hook or terminal.
35. As a **developer**, I want `seam trace <from> <to>` on the CLI, so that I can inspect a call path interactively.
36. As an **AI agent**, I want `seam_impact`, `seam_changes`, and `seam_trace` exposed as MCP tools alongside the Phase-0 three, so that I reason over the same server I already query.
37. As an **AI agent**, I want the new MCP tools to validate blank/invalid input and return the existing `INVALID_INPUT` error shape, so that error handling is consistent with Phase 0.

---

## Implementation Decisions

### Architecture: one traversal engine, three views

The central decision: impact, detect_changes, and flow tracing are **the same recursive walk over the `edges` table**, queried differently. Build the walk once as a **deep module** with a small interface, and layer thin feature modules on top.

- **New module — graph traversal (deep module).** Single responsibility: given seed symbol name(s), a direction (upstream / downstream), and a depth cap, walk the `edges` table and return reachable symbols with their distance and the aggregated path confidence. Implemented as a recursive CTE on SQLite (per ADR-003). This module knows nothing about "risk," "git," or "MCP" — it only walks the graph. Impact, trace, and detect_changes all call it.
- **New module — impact.** Thin wrapper over traversal: maps `direction` to one or both walks, buckets results into risk tiers by distance (d=1 → WILL BREAK, d=2 → LIKELY AFFECTED, d=3 → MAY NEED TESTING — the tier vocabulary already documented in `CLAUDE.md`), and returns an `ImpactResult`.
- **New module — flow tracing.** Path-finding between two symbols (bounded bidirectional walk over edges) plus one-hop `callers`/`callees`. Shares the same edge-walk primitives as traversal.
- **New module — detect_changes (deep module).** Owns the git boundary: shells out to `git diff` for the requested scope, parses changed file + line ranges, maps line ranges back to owning symbols via the `symbols` table (`start_line`/`end_line`), feeds those symbols into impact, and rolls the result up into a `ChangeReport` with an overall risk level. Simple interface, isolates all git interaction in one place.
- **Modified — query engine.** Gains the confidence-aware read helpers needed by the above; `context()` reports ambiguity when a name resolves to multiple symbols.
- **Modified — graph extraction (indexer).** Edge extraction is hardened (arrow-function call sites, namespace/aliased import resolution) and now assigns a **confidence** to every edge at extraction time.
- **Modified — MCP server + CLI.** Three new thin adapters (`seam_impact`, `seam_changes`, `seam_trace`) and three new CLI commands (`seam impact`, `seam changes`, `seam trace`), following the existing thin-adapter pattern (handlers validate → call engine/analysis → relativize paths → return dict).

### Module placement (respecting the import hierarchy)

A new `analysis` layer sits between `query` and `server` in the import order: `cli → server → analysis → query → indexer/db`. The `analysis` modules are read-only over the database (they never write), so they depend on `query`/`db` read helpers, never the reverse. `detect_changes` additionally depends on git (subprocess) and the repo root from `config`.

### Confidence tagging (adopted from Graphify, solves the string-name-collision limitation)

`Confidence` is a closed set of three string values. Captured here because the enum encodes the decision precisely:

```
Confidence = "EXTRACTED" | "INFERRED" | "AMBIGUOUS"
#   EXTRACTED  — target_name resolves to exactly one symbol in the index
#   INFERRED   — target resolved via a heuristic (aliased-import binding, re-export, single best guess)
#   AMBIGUOUS  — target_name matches more than one symbol; count/identity is uncertain
```

- Confidence is assigned during edge extraction/resolution and **persisted on the edge** (new column). It is part of the data contract, not recomputed per query.
- **Path confidence rule (decision):** the confidence of a multi-hop path is its **weakest hop** — any `AMBIGUOUS` edge on the path makes the whole path `AMBIGUOUS`; otherwise any `INFERRED` makes it `INFERRED`; only an all-`EXTRACTED` path is `EXTRACTED`. Impact and trace report this aggregate per result.
- This directly closes the documented `lessons.md` limitation ("string-name edges cause name-collision counting"): collisions become a visible `AMBIGUOUS` signal instead of a silent miscount.

### Schema changes (contract evolution — this PRD is the sanctioned escalation per CONTRACT.md)

- **`edges` table:** add a `confidence TEXT NOT NULL DEFAULT 'EXTRACTED'` column. No other column changes; the string-name design (ADR-002 rationale) is preserved.
- **`metadata`:** bump `schema_version` to `2`. `init_db` remains idempotent (`CREATE TABLE IF NOT EXISTS`); a migration adds the column to a v1 database if absent. A schema-version mismatch on an existing index triggers a one-line "re-index required" message rather than a crash.
- No new tables (flows are computed on demand; comment nodes are deferred to Phase 1b).

### API contracts (interfaces, not file paths)

- `impact(conn, target, direction, max_depth) -> ImpactResult` — `direction ∈ {upstream, downstream, both}`, default upstream; `max_depth` default 3, clamped.
- `trace(conn, source, target, max_depth) -> list[Path]` — each `Path` is an ordered list of hops with per-hop confidence; empty list when unconnected.
- `callers(conn, symbol) -> list[...]` / `callees(conn, symbol) -> list[...]` — one-hop convenience over traversal.
- `detect_changes(conn, base_ref, scope, repo_root) -> ChangeReport` — `scope ∈ {working, staged, branch}`; `ChangeReport` carries changed symbols, impacted set, and an overall `risk_level`.
- MCP tools mirror these with the Phase-0 conventions: clamp limits/depth, validate non-blank input → `INVALID_INPUT`, relativize all returned paths to the project root. Tool specs are added to `docs/api-contracts/mcp-tools.yaml`.

### Decisions taken as defaults (not surfaced as questions)

- **detect_changes is git-ref-based**, not hash-based: "what did my branch change vs a base" is the use case; the watcher already handles "vs last index" in real time.
- **Risk-level rollup** for `detect_changes`: driven by the highest tier reached across all changed symbols, attenuated when the dominant edges are `AMBIGUOUS` (uncertain inputs cap the confidence of the verdict).
- **No named/precomputed processes** this slice (flows are on-demand only) — keeps the schema additive-minimal and avoids a new staleness surface.

---

## Testing Decisions

**What makes a good test here:** assert on *external behavior* through the module's public interface — given a small fixture graph (files → symbols → edges in a temp SQLite db), calling `impact`/`trace`/`detect_changes` returns the expected symbols, tiers, and confidence. Do **not** assert on the SQL text, the CTE shape, or internal row ordering. Tests own their fixtures (build the graph via the public `db` write path, as `test_hardening.py` already does) so they read as behavior specs.

**Modules under test (all four core deep modules, per scope decision):**

1. **Edge hardening + confidence** — extraction-level tests: an arrow-function call produces an edge; `import * as ns` / `import {a as b}` resolve to the correct target name; a target resolving to one symbol is `EXTRACTED`, to none-but-guessed is `INFERRED`, to many is `AMBIGUOUS`. Prior art: `tests/unit/test_hardening.py` (extraction assertions over `extract_symbols`/`extract_edges`) and the existing graph tests.
2. **Impact** — over a hand-built fixture graph: upstream vs downstream vs both; correct d=1/d=2/d=3 bucketing; path-confidence aggregation (an `AMBIGUOUS` hop downgrades the whole path); depth cap honored; cycle terminates; unknown symbol → empty. Prior art: the integration-style db fixtures in `test_hardening.py` and `tests/integration/`.
3. **detect_changes** — using a temp git repo fixture: a working-tree edit maps to the right changed symbols; `--base` compares against a ref; an added file surfaces as new symbols; risk level rolls up correctly; non-git dir → clear error. Prior art: `tests/integration/test_indexer.py` and `test_watcher.py` (temp-dir + filesystem fixtures).
4. **Flow tracing** — over a fixture graph: `trace` finds an existing multi-hop path, returns empty for unconnected pairs, terminates on cycles within the cap; `callers`/`callees` return the right one-hop sets with confidence. Prior art: query-engine tests.

MCP handler tests follow the existing `tests/integration/test_mcp_tools.py` pattern (validation, clamping, relativization, error shapes) for the three new tools.

`make gate` (ruff + mypy + pytest) must stay green; type hints required on all new interfaces (`X | None`, never `Optional`).

---

## Out of Scope

- **Semantic comment nodes** (`# WHY:` / `# NOTE:` / `# HACK:` extraction) — deferred to **Phase 1b**. (Validated via Graphify; not in this slice.)
- **Go and Rust parsers** — deferred to **Phase 1b** (ADR-005 plans them; cheap tree-sitter add, but separable).
- **Precomputed named processes / execution-flow resources** (GitNexus `process/{name}` style) — Phase 2; flows are on-demand only here.
- **Leiden/Louvain community detection / clustering** — Phase 2 (`DISCOVERY.md`).
- **Any LLM layer** (flow naming, semantic summaries, PR triage) — explicitly excluded by ADR-003; Phase 2 at the earliest, as an optional plugin.
- **Symbol-ID edges.** The string-name design is retained; confidence tagging mitigates its weakness rather than replacing the model.
- **`rename` / safe-refactor tooling, `cypher`-style arbitrary graph queries** — not in Phase 1 Core.
- **Cross-language / framework-route edge synthesis** (CodeGraph-style Swift↔ObjC, URL→handler) — not planned for this slice.

---

## Further Notes

- **Build order is dependency-forced, not preference:** edge hardening + confidence must land first because impact, trace, and detect_changes all read confidence and all degrade to noise on a weak edge graph. Then the traversal engine (shared), then impact, then detect_changes (impact + git), then flow tracing. Ship and dogfood incrementally — each step is independently useful.
- **Self-supersession milestone:** completing impact + detect_changes is the point at which Seam can replace GitNexus for this repo's own "impact before edit / detect_changes before commit" workflow (`CLAUDE.md`). That's the concrete success signal for the slice.
- **Confidence is the headline idea of the slice** — borrowed from Graphify, it converts a known accuracy *liability* (string-name collisions) into an *honesty feature* (the engine tells you when it's unsure). Keep it visible in every surfaced result.
- A follow-up `lessons.md` entry and updates to `progress.txt` / `IMPLEMENTATION_PLAN.md` should accompany implementation; ADR-003 and ADR-005 move from "revisit for Phase 1" toward "implemented (Core)" / "Phase 1b" respectively.
