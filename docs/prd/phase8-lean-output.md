# Phase 8 — Lean Output (`verbose` flag) + `seam_impact` Summary Tier

> Motivated by the `docs/benchmark.md` re-run at commit `6690bb1`: the token-reduction win
> narrowed (88.7% → 83.4%) because Phase 4–6 enrichment fields are sent on *every* record, and
> `seam_impact` on a hub symbol (`init_db`) now returns ~30k tokens — *more* than reading every
> matched file whole (−1.3%). This phase adds the two levers the benchmark proves are needed.
> **No schema change.**

## Problem Statement

As an AI agent calling Seam's read tools, I get back a lot of bytes I often don't need:

1. **Every record carries the full Phase 4/5 enrichment** — `decorators`, `is_exported`,
   `visibility`, `qualified_name`, `resolved_by`, `best_candidate` — even when my question is just
   "who calls X?" or "trace A→B". That enrichment is valuable *sometimes*, but it is always-on, so
   `seam_context` / `seam_trace` / `seam_query` / `seam_context_pack` payloads are several times
   larger than the core answer I asked for. The benchmark shows this directly: Seam's output grew
   +170% while the repo grew +90%, and the windowed-baseline reduction on graph queries collapsed
   (row #1 63.9% → 24.1%; row #5 66.8% → 35.2%).
2. **`seam_impact` on a high-fanout symbol dumps the entire transitive blast radius** — every
   tier, every entry, each fully enriched. For `init_db` (imported almost everywhere) that is
   ~30k tokens, larger than just reading the files. I rarely need all 200 transitive entries in
   one shot; I need to know *how big* the blast radius is per risk tier and *which* high-risk
   callers to look at first.

## Solution

Two additive levers, both honoring the existing contract:

1. **`verbose` flag (lean output).** Add `verbose: bool = True` to the enrichment-returning read
   tools. The default (`True`) is **fully backward compatible** — output is byte-identical to
   today. When `verbose=False`, the *heavy* enrichment fields are **omitted** from every record:
   `decorators`, `is_exported`, `visibility`, `qualified_name`, `resolved_by`, `best_candidate`.
   The **core identity fields are always kept** — `name`/`symbol`, `file`, `line`, `kind`,
   `docstring`, `callers`, `callees`, `ambiguous`, `cluster_*`, **and `signature`** (the single
   highest value-to-byte enrichment: it tells the agent *how to call* the symbol). Lean mode is
   the agent's lever to reverse the +170% output growth on the queries where enrichment is noise.

2. **`seam_impact` summary tier + result cap.** `seam_impact` gains:
   - **`risk_summary`** — per-direction, per-tier **counts over the FULL result set** (computed
     before any cap). Always present. This is the compact, high-signal answer to "how big is the
     blast radius?" — e.g. `{"upstream": {"WILL_BREAK": 3, "LIKELY_AFFECTED": 40,
     "MAY_NEED_TESTING": 157}}`.
   - a **per-tier entry cap** (`SEAM_IMPACT_MAX_RESULTS`, default 25) — each tier's entry list is
     truncated to the cap (entries are already distance-ordered, so the kept ones are the
     closest/highest-risk), with a **`truncated`** count per direction/tier reporting how many
     were omitted.
   - a **`limit` parameter** (default = `SEAM_IMPACT_MAX_RESULTS`; `limit=0` = unlimited) so an
     agent can opt back into the full transitive list when it genuinely needs it.

   Combined with `verbose=False`, an `init_db` impact call returns a tiny `risk_summary` + ≤25 lean
   entries per tier instead of 200+ fully-enriched ones — turning the benchmark's one true loss
   into a win, while keeping the full answer one `limit=0` away.

Surfaced on both the **MCP tools** (params in the input schema) and the **CLI** (`--lean` flag on
the enrichment-returning read commands; `--limit` on `seam impact`).

## User Stories

1. As an AI agent, I want a `verbose=false` option on the read tools, so that I get a compact
   answer without the enrichment fields I'm not using on this query.
2. As an AI agent, I want `verbose` to default to `true`, so that existing behavior is unchanged
   and I never silently lose fields I was relying on.
3. As an AI agent, I want lean mode to **keep `signature`**, so that I still know how to call a
   symbol without a second lookup — the one enrichment field worth its bytes.
4. As an AI agent, I want lean mode to drop `decorators`, `is_exported`, `visibility`,
   `qualified_name`, `resolved_by`, and `best_candidate`, so that the heavy/situational fields stop
   inflating every record.
5. As an AI agent, I want lean mode applied consistently across `seam_context`, `seam_query`,
   `seam_trace`, `seam_impact`, and `seam_context_pack`, so that one flag means the same thing
   everywhere.
6. As an AI agent, I want `seam_search` left unchanged (it already returns only
   symbol/file/line/snippet/score), so that the flag surface stays minimal and meaningful.
7. As an AI agent, I want `seam_impact` to always include a `risk_summary` with per-tier counts over
   the full blast radius, so that I learn the *size* of the impact in a few bytes even when the
   entry lists are capped.
8. As an AI agent, I want `seam_impact` to cap each tier's entries to a sane default
   (`SEAM_IMPACT_MAX_RESULTS`), so that a hub symbol returns a usable answer instead of a 30k-token
   wall.
9. As an AI agent, I want a `truncated` count per tier when the cap drops entries, so that I know
   the list is partial and can re-query for the rest.
10. As an AI agent, I want a `limit` parameter on `seam_impact` (with `limit=0` meaning unlimited),
    so that I can fetch the complete transitive set when I actually need it.
11. As an AI agent, I want the capped entries to be the highest-risk ones (closest distance first),
    so that the truncation never hides the `WILL_BREAK`/direct-caller entries I most need.
12. As a developer, I want a `seam impact --limit N` CLI flag mirroring the tool parameter, so that
    the CLI and MCP behave identically.
13. As a developer, I want a `--lean` CLI flag on `seam query`/`context`/`trace`/`impact`/`pack`, so
    that I get the same compact output from the terminal that an agent gets with `verbose=false`.
14. As an AI agent, I want the new behavior to require no schema change and no re-index, so that it
    works immediately on any existing index.
15. As an AI agent, I want `risk_summary` counts to match the full (pre-cap) result set exactly, so
    that the histogram is trustworthy even when entries are truncated.
16. As a developer, I want the cap default documented as a config knob (`SEAM_IMPACT_MAX_RESULTS`),
    so that I can tune or disable it without reading source.
17. As an AI agent, I want lean mode and the impact cap to be independent (I can use either, both, or
    neither), so that I can dial token cost precisely per call.

## Implementation Decisions

- **`verbose: bool = True` parameter** added to the enrichment-returning MCP handlers in
  `seam/server/tools.py`: `handle_seam_query`, `handle_seam_context`, `handle_seam_trace`,
  `handle_seam_impact`, `handle_seam_context_pack`. `handle_seam_search` is **not** touched (it
  returns no enrichment). The flag is registered in each tool's input schema in `seam/server/mcp.py`.
- **A single shared stripping helper** in `seam/server/tools.py` — e.g.
  `_apply_verbosity(record: dict, verbose: bool) -> dict` — removes the heavy keys
  (`decorators`, `is_exported`, `visibility`, `qualified_name`, `resolved_by`, `best_candidate`)
  when `verbose=False`. Applied uniformly at serialization so the lean contract is DRY and
  identical across tools. **`signature` and all core fields are never stripped.** Stripping means
  the keys are **absent** (not null) in lean mode — lean mode's whole point is fewer bytes.
- **Backward compatibility:** `verbose=True` default preserves today's exact output (the documented
  "fields always present, null-means-unknown" contract holds in verbose mode). Lean mode is strictly
  opt-in for #1.
- **`seam_impact` summary + cap:** `handle_seam_impact` gains:
  - `risk_summary`: `{direction: {tier: count}}` computed from the **raw (full)** impact result
    before capping. Always included.
  - `limit: int = SEAM_IMPACT_MAX_RESULTS` parameter; `limit <= 0` means unlimited. Each tier's
    entry list is sliced to `limit` (entries arrive distance-ordered from the analysis layer, so the
    kept slice is the closest/highest-risk).
  - `truncated`: `{direction: {tier: omitted_count}}` included when any tier was capped.
  - The impact cap default **does change** the default impact output for high-fanout symbols (the
    point of the phase), but the change is *additive and signaled* — `risk_summary` preserves the
    true totals, `truncated` flags the omission, and `limit=0` restores the full list. This is the
    one place a default-output change is justified, because the current default (~30k tokens, worse
    than grep) is actively harmful.
- **New config knob** in `seam/config.py`: `SEAM_IMPACT_MAX_RESULTS` (default 25) — per-tier entry
  cap for `seam_impact`. No other new knobs (`verbose` is a per-call parameter, not a knob).
- **CLI** in `seam/cli/main.py`: a `--lean` flag (sets `verbose=False`) on `seam query`,
  `seam context`, `seam trace`, `seam impact`, `seam pack`; a `--limit N` flag on `seam impact`
  (0 = unlimited). All route through the same handlers so MCP and CLI produce identical output.
- **No schema change, no migration, no new deps.** Pure output-shaping at the serialization layer.
- **MCP tool count unchanged (10).** No new tools; existing tools gain optional parameters.

## Testing Decisions

A good test asserts the **observable shape of the output** under each flag combination — which keys
are present/absent, that counts are correct, that caps hold — not the internal helper calls.

1. **Lean stripping** (`tests/unit/test_lean_output.py` or extend existing handler tests):
   - `verbose=True` (default) → output byte-shape identical to today (heavy fields present).
   - `verbose=False` → `decorators`/`is_exported`/`visibility`/`qualified_name`/`resolved_by`/
     `best_candidate` keys are **absent**; `signature` + all core fields **present**.
   - lean stripping applies inside nested structures too (impact tier entries, trace hops,
     context_pack neighbors), not just the top-level record.
   - Prior art: `tests/integration/test_phase5_read_layer.py`, existing context/impact/trace tests.
2. **Impact summary + cap** (`tests/unit/test_impact_summary.py` + integration):
   - `risk_summary` present and its per-tier counts **equal the full pre-cap counts**, even when
     entries are capped.
   - with a fixture exceeding the cap: each tier's entry list length ≤ cap; `truncated` reports the
     exact omitted count; the kept entries are the lowest-distance (highest-risk) ones.
   - `limit=0` → unlimited (all entries returned, no `truncated` or all-zero).
   - `limit=N` → respected; `risk_summary` still reflects the full set.
   - Prior art: existing `seam_impact` handler tests, the Phase 5 read-layer tests.
3. **MCP ↔ CLI parity + schema** (`tests/integration/test_lean_parity.py`):
   - `seam impact --lean --limit N` and the tool with `verbose=False, limit=N` produce the same
     bundle; `seam context --lean` matches `handle_seam_context(..., verbose=False)`.
   - the new params appear in the MCP tool input schemas (tool count stays 10).
   - Prior art: `tests/integration/test_pack_parity.py`, the tool-count assertions in
     `test_phase5_read_layer.py`.

Run `make gate` (ruff + mypy + pytest) before every commit — must stay green.

## Out of Scope

- **Arbitrary `fields=[...]` projection.** Only a boolean `verbose` (all-or-core). Per-field
  selection is more surface than the benchmark justifies for MVP.
- **Changing the default of `verbose` to `false`.** Default stays `true` to preserve the documented
  "fields always present" contract; lean is opt-in. (The `seam_impact` *cap* is the one default-shape
  change, and it is signaled — see Implementation Decisions.)
- **Touching `seam_search`.** It carries no enrichment.
- **Normalized / de-duplicated output** (one symbol table + references by id — benchmark
  improvement #3). Deferred; revisit only if lean + cap don't bring impact/trace under control.
- **Pagination cursors.** A simple `limit` + `truncated` count is the MVP; no cursor/offset protocol.
- **`tiktoken`-based measurement or the live agent A/B** (benchmark improvement #4). The benchmark
  re-run in the doc step uses the existing chars÷4 proxy and reports a default-vs-lean comparison.
- **Schema / new tables / migration.** None.

## Further Notes

- The doc step should **re-run `benchmarks/run_benchmark.py`** after implementation and update
  `docs/benchmark.md` to show the post-Phase-8 numbers, ideally reporting the `init_db` impact row
  under the new default (summary + cap) and a lean-mode variant for the context/trace rows — making
  the recovered reduction visible and honest.
- The capped-entry ordering relies on the analysis layer already returning tier entries in
  distance order. The implementation must confirm that ordering (or sort by distance before
  capping) so the "keep highest-risk" guarantee in story 11 holds.
