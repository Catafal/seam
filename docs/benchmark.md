# Seam Benchmark

> IMPLEMENTATION_PLAN steps 9.1–9.3. Reproduce with `python benchmarks/run_benchmark.py`
> after `seam init`. Last run: 2026-06-02, against this repo at commit `6690bb1` (Phase 7)
> (104 files · 1757 symbols · 5836 edges · 123 clusters).
> Prior run: commit `7415d32` (72 files · 927 symbols · 3262 edges · 89 clusters) — see
> *Change since the last run* below for why the reduction narrowed.

> **See also:** [`competitive-benchmark.md`](competitive-benchmark.md) — head-to-head vs.
> gitnexus, CodeGraph, and graphify on a real external codebase (Bach), with a scorecard
> and honest per-use-case verdicts.

## The claim under test

From `DISCOVERY.md`: AI agents waste tokens re-discovering codebase structure with
`grep` + file reads every session; Seam lets them **query** that structure instead.
Target (IMPLEMENTATION_PLAN 9.3): **≥30% token reduction.**

## What this benchmark measures — and what it does not

This is a **static retrieval-context proxy**, not a live agent-session A/B. It measures
the load-bearing quantity directly: for a fixed set of realistic codebase-comprehension
questions, *how much context (text the model must ingest) does each approach yield?*

- **Baseline** = what an agent consumes using only `grep -rnE` + reading source under `seam/`.
  Two variants are reported so the result is honest rather than tuned:
  - **whole-file** — full content of every file with ≥1 grep match. This is the dominant
    real behavior (Claude Code's `Read` reads whole files by default) and an **upper bound**.
  - **windowed** — only ±25 lines around each match. A deliberately conservative **lower bound**.
- **Seam** = the exact JSON the corresponding MCP tool handler returns — the same bytes an
  agent receives over stdio.

Tokens are **estimated as chars ÷ 4** (the common GPT heuristic; `tiktoken` is not a project
dependency, per the zero-dep ethos). Raw character counts drive the table; the reduction
*ratio* is near-invariant to the divisor.

**Honest limitation.** Steps 9.1/9.2 were originally framed as two full agent coding
sessions on the Bach project (with/without Seam) read off provider token meters. That
gold-standard measurement cannot be produced autonomously, and inventing the numbers would
be dishonest. This proxy is fully reproducible and measures the same underlying cost; the
live-session study remains the recommended follow-up (see *Threats to validity*).

## Results

| # | Question | Tool | Baseline (whole-file) | Baseline (windowed) | Seam | Reduction vs whole | vs windowed |
|---|----------|------|----------------------:|--------------------:|-----:|-------------------:|------------:|
| 1 | Who calls `upsert_file`? | `seam_context` | 38,475 | 6,220 | 4,720 | 87.7% | 24.1% |
| 2 | Blast radius of changing `init_db`? | `seam_impact` | 29,671 | 8,914 | 30,043 | **−1.3%** | **−237.0%** |
| 3 | Where is FTS5 search implemented? | `seam_search` | 32,564 | 46,604 | 1,050 | 96.8% | 97.7% |
| 4 | What are the functional areas / modules? | `seam_clusters` | 144,817 | 142,581 | 2,995 | 97.9% | 97.9% |
| 5 | How does `init` reach `upsert_file`? | `seam_trace` | 43,688 | 16,326 | 10,578 | 75.8% | 35.2% |
| 6 | Understand `extract_edges` (callers/callees) | `seam_context` | 17,202 | 7,369 | 1,618 | 90.6% | 78.0% |
| | **TOTAL** | | **306,417** | **228,014** | **51,004** | **83.4%** | **77.6%** |

*Estimated tokens (chars ÷ 4). Window = ±25 lines. Source scope: `seam/`.*

## Verdict

**Target met by a wide margin: 83.4% reduction vs. the realistic whole-file baseline,
77.6% vs. the conservative windowed baseline** — both far above the 30% goal. Five of six
queries land between 24% and 98% reduction.

The headline wins are the queries grep is *worst* at: "what are the functional areas?"
(`seam_clusters`, 97.9%) and "where is X implemented?" (`seam_search`, 96.8–97.7%) — questions
where the grep-and-read approach forces an agent to ingest large swaths of the tree, while
Seam returns a compact ranked answer.

## Change since the last run (`7415d32` → `6690bb1`)

The reduction **narrowed** from 88.7%/85.8% to 83.4%/77.6%. This is not a regression in the
index — it is the direct, measurable cost of the Phase 4–6 enrichment work, and it is worth
understanding precisely:

- The repo nearly **doubled** (927 → 1757 symbols). Baselines grew with it (grep noise scales
  with codebase size), which *helps* Seam's ratio.
- But Seam's output grew **faster than the repo** (+170% vs. +90%). Phases 4–6 added enrichment
  fields to *every returned record* — `signature`, `decorators`, `is_exported`, `visibility`,
  `qualified_name` (Phase 4) and `resolved_by`, `best_candidate` (Phase 5). So each
  `seam_context` neighbor, `seam_trace` hop, and `seam_impact` entry now carries several times
  the bytes it did at `7415d32`.
- The graph-heavy queries felt this most: rows #1 and #5 dropped sharply on the *windowed*
  baseline (93.2%→87.7% and 88.8%→75.8% on whole-file; far more on windowed) because the tool
  payload grew while a ±25-line grep window stayed tiny.

The richer output is a feature (the agent gets signatures, visibility, and resolution
provenance without a second call) — but it has a real token cost, and that cost is now the
primary lever for improving these numbers (see **Where to improve**).

## The honest outlier (row #2)

`seam_impact` on `init_db` now returns **more** tokens than *both* baselines — −1.3% vs.
whole-file, −237.0% vs. windowed (it was +28.4% / −112.8% at `7415d32`). Stated plainly:
`init_db` is the DB bootstrap, imported almost everywhere, so its full tiered blast-radius
JSON — now ~30k est. tokens with Phase 5 `resolved_by`/`best_candidate` on every entry — is
genuinely larger than reading every matched file whole. Two caveats keep this fair to *both* sides:

- It is **not apples-to-apples**: a ±25-line grep window around `init_db` matches does **not
  answer** "what breaks if I change this" — it only shows where the string appears. Seam's
  output is the transitive, risk-tiered dependency set, which grep cannot produce at any size.
- An agent can cap `max_depth` to trade completeness for size.

The takeaway: Seam's largest wins are in *discovery/search* queries; for deep *impact* queries
on hub symbols the win has now inverted because the honest answer is genuinely large — which is
exactly the case the **Where to improve** section targets.

## Where to improve

Ranked by token impact, smallest-change-first:

1. **Lean output / field projection (biggest, broadest lever).** The Phase 4–5 enrichment
   fields are always-on. A `verbose=false` (or `fields=[…]`) parameter that omits
   `decorators`/`visibility`/`qualified_name`/`resolved_by`/`best_candidate` unless asked would
   directly reverse the +170% output growth — and recover most of the lost reduction on rows
   #1, #5, #6 without removing the richness when an agent wants it.
2. **`seam_impact` summary tier (fixes the one true loss, row #2).** Default to a risk-tier
   *histogram* (counts per tier) + the top-N highest-risk direct dependents, with the full
   transitive list behind an opt-in flag or pagination. A hub like `init_db` would return
   hundreds of bytes instead of ~30k, and the summary is *more* actionable than the wall of
   entries. Lower the default `max_depth`, and reuse the `truncated`-count pattern already used
   by `seam_affected`/`seam_context_pack`.
3. **De-duplicate repeated enrichment in large results.** In impact/trace results the same
   symbol's `signature`/`qualified_name` repeats per occurrence. A normalized shape (one symbol
   table + references by id) shrinks large payloads — at the cost of a little client-side
   assembly. Worth it only if #1 and #2 don't bring impact/trace under control.
4. **Measure with real tokens, and run the live A/B.** chars÷4 is fine for ratios but not
   absolute counts; an optional `tiktoken` path (dev-only, keeps the zero-dep runtime) would
   sharpen the numbers. The gold standard — two real agent sessions on an external repo read off
   provider token meters — is still the recommended follow-up and would *favor* Seam (it
   captures the repeated whole-file re-reads this static proxy omits).

## Threats to validity

- **Proxy, not live sessions.** Real agent runs include reasoning tokens, repeated reads, and
  tool-call overhead this proxy omits. Direction of bias is unclear: agents often read the
  *same* file multiple times (favoring Seam) but also sometimes answer from a single grep line
  (favoring baseline). A live A/B on Bach/Koda remains the gold standard.
- **Token estimate.** chars÷4 is approximate; ratios are robust to it, absolute counts are not.
- **Single repo, self-measurement.** Run against Seam itself (~900 symbols). Larger, less-tidy
  codebases would likely widen Seam's lead (grep noise scales with size; a tool answer does not),
  but that is untested here.
- **Query selection.** Six hand-picked structural questions. They reflect common agent tasks but
  are not a random sample.

## Reproduce

```bash
seam init .                          # build/refresh the index
python benchmarks/run_benchmark.py   # prints the markdown table above
```
