# Seam Benchmark

> IMPLEMENTATION_PLAN steps 9.1–9.3. Reproduce with `python benchmarks/run_benchmark.py`
> after `seam init`. Last run: 2026-06-02, against this repo at the **Phase 8** branch
> (104 files · 1757 symbols · 5836 edges · 123 clusters) — Phase 8 (lean output + the
> `seam_impact` summary tier) recovered the reduction to **91.8% / 88.7%**, see
> *Change since the last run* below.
> Prior runs: `6690bb1` (Phase 7) 83.4%/77.6%; `7415d32` (72 files) 88.7%/85.8%.

> **See also:** [`competitive-benchmark.md`](competitive-benchmark.md) — head-to-head vs.
> CodeGraph and graphify on a real external codebase (Bach), with a scorecard
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
| 1 | Who calls `upsert_file`? | `seam_context` | 39,371 | 6,115 | 4,765 | 87.9% | 22.1% |
| 2 | Blast radius of changing `init_db`? | `seam_impact` | 30,568 | 8,905 | 4,575 | **85.0%** | **48.6%** |
| 3 | Where is FTS5 search implemented? | `seam_search` | 34,681 | 46,968 | 1,055 | 97.0% | 97.8% |
| 4 | What are the functional areas / modules? | `seam_clusters` | 148,017 | 144,048 | 3,072 | 97.9% | 97.9% |
| 5 | How does `init` reach `upsert_file`? | `seam_trace` | 44,585 | 16,247 | 10,847 | 75.7% | 33.2% |
| 6 | Understand `extract_edges` (callers/callees) | `seam_context` | 17,202 | 7,369 | 1,618 | 90.6% | 78.0% |
| | **TOTAL** | | **314,424** | **229,652** | **25,932** | **91.8%** | **88.7%** |

*Estimated tokens (chars ÷ 4). Window = ±25 lines. Source scope: `seam/`. Run at the Phase 8
default settings (`seam_impact` summary + per-tier cap of 25; verbose output otherwise).*

## Verdict

**Target met by a wide margin: 91.8% reduction vs. the realistic whole-file baseline,
88.7% vs. the conservative windowed baseline** — both far above the 30% goal, and now *above*
the original pre-enrichment numbers (88.7% / 85.8% at `7415d32`). Every one of the six queries
is a win, none below 22%.

The headline wins remain the queries grep is *worst* at — `seam_clusters` (97.9%) and
`seam_search` (97.0–97.8%) — but the story of this run is **row #2**: the former loss is now an
85% win (see below).

## Change since the last run (`6690bb1` → Phase 8)

The reduction **recovered** from 83.4%/77.6% to **91.8%/88.7%** — Seam's total output more than
halved (51,004 → 25,932 est. tokens) with no loss of capability. Phase 8 shipped the two levers
the previous run's analysis identified:

- **`seam_impact` summary tier + per-tier cap (the dominant win).** `seam_impact` now returns a
  `risk_summary` histogram (per-tier counts over the *full* blast radius) plus the closest ≤25
  entries per tier, with a `truncated` count and a `limit=0` escape hatch for the full set. This
  alone took row #2 (`init_db`) from ~30k tokens to **4,575** — a −1.3% loss flipped to a **+85.0%
  win** — because the agent learns the blast-radius *size* (230 WILL_BREAK, 133 LIKELY_AFFECTED …)
  in a few bytes instead of ingesting every transitive entry.
- **Lean output (`verbose=false` / `--lean`).** Omits the heavy Phase 4/5 enrichment fields
  (`decorators`, `is_exported`, `visibility`, `qualified_name`, `resolved_by`, `best_candidate`),
  keeping `signature` + core identity. Its win is concentrated where records *repeat* those
  fields: `seam_trace` drops **−40%** (8,689 → 5,182 tokens for the `init`→`upsert_file` path),
  and `seam_impact`/`seam_context_pack` entries shrink similarly. For `seam_context` the effect is
  small (−1–2%) because the heavy fields sit only on the single target record — the
  callers/callees are bare names. The default benchmark above runs *verbose*; an agent that opts
  into `--lean` trims the trace/impact rows further.

## Where to improve (updated after Phase 8)

Phase 8 shipped levers **#1 (lean output)** and **#2 (impact summary tier)** — the two the
previous run flagged. What remains:

1. ~~Lean output / field projection~~ — **done (Phase 8).** `verbose=false` / `--lean` on the
   enrichment-carrying tools. Biggest effect on `seam_trace` (−40%).
2. ~~`seam_impact` summary tier~~ — **done (Phase 8).** `risk_summary` + per-tier cap +
   `truncated` + `limit`. Row #2 went from −1.3% to +85.0%.
3. **De-duplicate repeated enrichment in large results.** `seam_trace` is now the weakest row
   (75.7% / 33.2%): each hop still repeats the target symbol's `signature`/`qualified_name`. A
   normalized shape (one symbol table + references by id) would shrink it further — at the cost
   of a little client-side assembly. The next lever if trace size matters.
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
