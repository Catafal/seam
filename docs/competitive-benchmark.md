# Competitive Benchmark — Seam vs. CodeGraph and graphify

> Head-to-head run on **2026-06-02** against a real external codebase:
> **Bach** (`~/Documents/Github/bach`, 81 Python files), which Seam had never seen.
> Tools compared: **Seam v0.2.0**, **CodeGraph 0.9.8**
> (`@colbymchenry/codegraph`), **graphify** (`graphifyy`, code-only mode).
> Understand-Anything was excluded — it is an LLM-driven visualization plugin with no
> headless, queryable graph to measure comparably.

## Honesty / methodology

Every claim below is tagged **[measured]** (I ran it on Bach) or **[documented]** (from the
tool's README — that feature was not driven here).

- **Token estimate:** chars ÷ 4 (no `tiktoken` dependency). Ratios are robust to the divisor; absolute counts are approximate.
- **Single repo:** one ~900-symbol Python project. A multi-repo run would firm up the ranking. Larger/messier repos likely widen every tool's lead over grep.
- **Operation mismatches (called out inline):** CodeGraph's `context` is a *task-prompt bundler*, not a symbol view — its symbol-360 is `callers`/`callees`; `callers` on a *class* returns `[]` (it tracks calls, not instantiations). graphify has **no transitive impact command** — its `explain` is 1-hop neighbors only.
- **Not a live agent-session A/B.** This is a static retrieval-context proxy: how many tokens each tool's answer costs vs. grep+read. The gold-standard live A/B remains future work.

## Index build (same repo, Bach) — [measured]

| Tool | Files | Code symbols | Graph nodes | Edges | Communities | Exec flows | Time | Index size | Mutates tracked files? |
|---|--:|--:|--:|--:|--:|--:|--:|--:|:--|
| **Seam** | 81 | **894** | 894 | 2,869 | 136 | — | **0.26s** | **1.1 MB** | **No** |
| CodeGraph | 84 | **894** | 1,533 | 3,676 | — | — | 0.60s | 3.4 MB | on `install` |
| graphify | 132 | — | 2,406 | 5,999 | **135** | — | 2.25s | 5.2 MB | on `install` |

Notes:
- CodeGraph extracts the **exact same 894 code symbols** as Seam (821 fn + 44 method + 29 class) — same parsing depth.
- graphify finds **135 communities** to Seam's **136** — both Louvain-family, near-identical partitions.
- graphify "nodes" include files/imports/modules, so node totals are not 1:1 comparable to Seam's symbol count.

## Answer size per question (est. tokens the agent ingests) — [measured]

| Question | grep whole-file | grep windowed | **Seam** | CodeGraph | graphify |
|---|--:|--:|--:|--:|--:|
| callers of `load_config` | 42,433 | 12,070 | 774 | 768 | **273** |
| `TaskArtifactStore` neighborhood | 68,263 | 20,884 | 617 | `callers`→13 | **218** |
| blast radius of `Project` | 71,961 | 20,731 | **12,835** ❌ | 5,898 | 99 † |
| "parse issues" concept query | 36,258 | 7,637 | **520** ✅ | 1,937 | 1,567 |

† graphify `explain Project` = 1-hop neighbors, **not** a transitive blast radius (capability gap).

**Reading it:** every tool crushes grep. Among the tools — graphify is leanest on symbol lookups,
Seam is leanest on concept search, and **Seam is the single most
expensive result anywhere** (impact on a hub symbol — the known over-return bug).

## Scorecard (1–5; ✓ measured / · documented) — author judgment

| Dimension | Seam | CodeGraph | graphify |
|---|:--:|:--:|:--:|
| Index speed ✓ | **5** | 4 | 3 |
| Index footprint ✓ | **5** | 4 | 3 |
| Token leanness — lookup ✓ | 4 | 4 | **5** |
| Token leanness — impact ✓ | 1 | 3 | — |
| Language coverage · | 2 | **5** | **5** |
| Feature depth · | 2 | 3 | 4 |
| Agent CLI (structured JSON) ✓ | 3 | **5** | 2 |
| User visuals · | 1 | 1 | **5** |
| Repo cleanliness ✓ | **5** | 2 | 2 |
| Distribution / practicality · | 2 | **5** | 4 |
| Confidence / trust signals ✓ | **5** | 1 | 4 |
| Local / no API key ✓ | **5** | **5** | 3 |
| **Total** | **40** | **42** | **41** |

The totals are close enough that the ranking is a **weighting choice, not a fact**. Reweight the
dimensions and a different tool wins.

## Verdict per use case

- **Best for an AI coding agent, drop-in:** **CodeGraph** — `--json` CLI + MCP, 20+ languages, zero deps, incremental sync, fast. (Has its own 7-repo benchmark: ~70% median token cut, 62% fewer tool calls.)
- **Best for breadth + visuals + docs/PR work:** **graphify** — 33 languages, Louvain clustering **+ naming**, HTML/Mermaid/exports, PR dashboard, leanest lookups. Already ships Seam's clustering **and** EXTRACTED/INFERRED confidence tags.
- **Best for footprint, speed, trust, repo-cleanliness:** **Seam** — 0.26s, 1.1 MB, confidence tiers, only tool that doesn't touch tracked files. But the **narrowest** (5 languages, fewest features).

## Honest standing for Seam

Seam is **not** demonstrably "better" than these. It is the *lightest, fastest, cleanest, and
most trust-annotated*, and the *narrowest in scope*. graphify already
ship its headline Phase-2 feature (graph clustering), and graphify independently also surfaces
edge-confidence tiers — so Seam is at parity or behind on the exact work it most recently added.

### Measured weaknesses to fix (highest leverage first)
1. **`seam_impact` over-returns on high-fanout symbols** — `Project` cost 12,835 tokens; the worst single result in the whole benchmark. Cap/summarize tier output.
2. **No `--json` on the CLI** — agents must go through the MCP server; CodeGraph exposes JSON directly from the CLI.
3. **`seam_search` AND-s multi-term queries** — a natural-language query with one non-matching word returns 0 hits (found during Bach dogfooding: `"parse issues board"` → 0, while `"parse issues"` → 10).
4. **Distribution** — Seam is MCP-only, not packaged as a skill; the others install in one line.

### Measured strengths to keep
- Sub-second indexing.
- 1.1 MB index (smallest by 3–40×).
- EXTRACTED / AMBIGUOUS / INFERRED confidence on every edge/path.
- Writes nothing outside `.seam/` — the only tool that leaves the repo's tracked files untouched.

## Reproduce

```bash
# Seam (this repo's harness, self-test):
seam init . && python benchmarks/run_benchmark.py
# Competitors were run on ~/Documents/Github/bach with their own CLIs
# (codegraph init -i / graphify update); see git history of this doc.
```
