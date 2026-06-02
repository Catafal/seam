# Seam Benchmark

> IMPLEMENTATION_PLAN steps 9.1–9.3. Reproduce with `python benchmarks/run_benchmark.py`
> after `seam init`. Last run: 2026-06-02, against this repo at commit `7415d32`
> (72 files · 927 symbols · 3262 edges · 89 clusters).

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
| 1 | Who calls `upsert_file`? | `seam_context` | 21,169 | 3,986 | 1,438 | 93.2% | 63.9% |
| 2 | Blast radius of changing `init_db`? | `seam_impact` | 15,266 | 5,138 | 10,932 | 28.4% | **−112.8%** |
| 3 | Where is FTS5 search implemented? | `seam_search` | 14,443 | 17,886 | 160 | 98.9% | 99.1% |
| 4 | What are the functional areas / modules? | `seam_clusters` | 78,454 | 90,407 | 2,132 | 97.3% | 97.6% |
| 5 | How does `init` reach `upsert_file`? | `seam_trace` | 23,632 | 7,998 | 2,652 | 88.8% | 66.8% |
| 6 | Understand `extract_edges` (callers/callees) | `seam_context` | 13,285 | 7,412 | 1,548 | 88.3% | 79.1% |
| | **TOTAL** | | **166,249** | **132,827** | **18,862** | **88.7%** | **85.8%** |

*Estimated tokens (chars ÷ 4). Window = ±25 lines. Source scope: `seam/`.*

## Verdict

**Target met by a wide margin: 88.7% reduction vs. the realistic whole-file baseline,
85.8% vs. the conservative windowed baseline** — both far above the 30% goal. Five of six
queries land between 64% and 99% reduction.

The headline wins are the queries grep is *worst* at: "what are the functional areas?"
(`seam_clusters`, 97%) and "where is X implemented?" (`seam_search`, 99%) — questions where
the grep-and-read approach forces an agent to ingest large swaths of the tree, while Seam
returns a compact ranked answer.

## The honest outlier (row #2)

`seam_impact` on `init_db` returns **more** tokens than the windowed grep (−112.8%). This is
real and worth stating plainly: `init_db` is a high-fanout symbol, so its full tiered
blast-radius JSON is large (~10.9k est. tokens). Two caveats keep this fair to *both* sides:

- It is **not apples-to-apples**: a ±25-line grep window around `init_db` matches does **not
  answer** "what breaks if I change this" — it only shows where the string appears. Seam's
  output is the transitive, risk-tiered dependency set, which grep cannot produce at any size.
- It still beats the realistic whole-file baseline (28.4%), and an agent can cap `max_depth`
  to trade completeness for size.

The takeaway: Seam's largest wins are in *discovery/search* queries; for deep *impact* queries
on hub symbols the win narrows because the honest answer is genuinely large.

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
