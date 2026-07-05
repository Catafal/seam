# Agent Answerability Benchmark

> Reproduce with `make eval-answerability`.
> Scenario set: `2026-07-05`.
> Current fixture: `tests/eval/fixtures` with SHA `0f11955b3b5b277a`.

## The Claim Under Test

Seam should help a coding agent answer daily codebase questions with a small,
trustworthy evidence bundle. The benchmark asks:

> For common Claude Code / Codex-style questions, does current Seam output contain
> the expected facts, the required evidence, honest caveats, and bounded output
> cost?

This is intentionally different from a feature-parity checklist against another
codebase-memory tool. New graph domains should be prioritized only when the
benchmark shows real agent questions are failing because that evidence is absent.

## What This Measures

The benchmark is a deterministic static proxy. Each scenario declares:

- natural-language question text;
- expected facts, such as symbols or capabilities;
- required evidence, such as files, edge kinds, warnings, or provenance;
- acceptable caveats for unsupported or static-only evidence;
- the ideal Seam tool plan;
- a grep/read fallback plan for future comparison;
- capability and product-gap tags.

The runner executes the tool plan against the deterministic eval fixture, normalizes
the returned evidence, scores the scenario on a 0-2 rubric, and emits a Markdown
and machine-readable summary.

## What This Does Not Measure

- It is not a live agent-session A/B.
- It does not call an LLM judge.
- It does not add new extraction or graph edges.
- It does not execute runtime code, fetch external docs, inspect logs, or query
  production systems.
- It does not prove Seam can answer every possible agent question.

The benchmark is useful because it is cheap, local, and repeatable. Live agent A/Bs
can be added later, but the deterministic layer is the baseline that prevents
roadmap decisions from drifting into speculation.

## Scenario Coverage

The first maintained scenario set contains 20 natural-language questions across:

- discovery;
- navigation;
- change-safety;
- cleanup and risk;
- architecture;
- protocol capability honesty;
- infra capability honesty.

Unsupported protocol or infra capability scenarios can still pass when Seam is
honest. For example, the fixture has no HTTP-call or infra graph evidence, so a
correct answer is not "no risk"; it is an explicit unsupported/empty capability
with an acceptable caveat.

## Scoring

Each axis uses a 0-2 score:

- `0`: absent or misleading;
- `1`: partially useful but incomplete;
- `2`: correct, bounded, and evidence-backed.

The current score axes are:

- answer facts;
- evidence sufficiency;
- caveat honesty;
- output cost;
- round trips;
- latency;
- freshness;
- false confidence.

Reports also aggregate product-gap labels. These labels point to roadmap buckets
such as change-planning surface, protocol-edge quality, infra graph, graph-search
recipes, and graph-quality coherence. The earlier context-pack upgrade bucket is now
represented by `context_pack` scenarios with direct relationship evidence, caveats,
and follow-up calls; covered scenarios should keep an empty `product_gap_tags` list.

The graph-search recipe bucket is expected to shrink as scenarios are converted
from generic search/query plans to explicit `graph_search` recipe plans. A recipe-covered
scenario should keep an empty `product_gap_tags` list unless another product gap remains.

## Reproduce

```bash
make eval-answerability
```

For machine-readable output:

```bash
uv run python -m tests.eval.answerability_report --json
```

To save the Markdown report:

```bash
uv run python -m tests.eval.answerability_report \
  --markdown-out .claude/eval/agent-answerability-report.md
```

## Adding Scenarios

Edit `tests/eval/answerability_scenarios.json`.

Keep these rules:

- question text should be natural language, not a tool command;
- expected facts and required evidence must be authored from source truth;
- do not generate ground truth from live Seam output;
- add acceptable caveats when static evidence cannot prove runtime behavior;
- use optional/unsupported capability scenarios to test honesty, not to force
  false certainty;
- update the fixture hash only after intentionally changing the fixture and
  reviewing the scenario ground truth.

## Current Interpretation

The first run is deliberately fixture-sized. A perfect fixture score does not mean
Seam is done; it means the benchmark harness is now able to measure future gaps.
When scores are low, those failures should drive the next implementation PRD.
When scores are high, recurring product-gap labels are still useful roadmap
pressure because they show which unsupported or under-modeled questions keep
appearing across representative repos.
