# PRD - Agent Answerability Benchmark

> Status: ready for implementation.
> Created: 2026-07-05.
> Roadmap source: Agent Answerability And Product Additions.
> Purpose: define the first phase of the answerability roadmap before adding
> more graph domains.

## Problem Statement

Seam has accumulated many useful static code-intelligence primitives: schema
inspection, lexical and graph search, bounded snippets, architecture summaries,
symbol context, blast-radius impact, affected tests, flows, routes, config and
resource evidence, exception edges, test edges, and an editing-oriented context
pack. That is a strong foundation, but it does not yet prove the product can
answer the questions real coding agents ask every day.

Today the product roadmap can still be pulled toward impressive graph surfaces
before we know whether they improve day-to-day agent work. Kubernetes and
Kustomize indexing is a concrete example: it exists in the external
`codebase-memory-mcp` inspiration repo and it can be valuable, but it is not
obviously the next best investment for Seam unless agent questions are failing
because deployment topology is missing.

The user's problem is that Seam needs a disciplined way to decide what to build
next. The decision should be based on whether an agent such as Claude Code or
Codex can answer real codebase questions faster, with less context, fewer tool
round trips, stronger evidence, and less false confidence.

The current eval harness is useful but too narrow for this product decision. It
primarily protects recall/MRR over fixed fixture queries. It does not model
complete agent questions, evidence sufficiency, stale-index behavior,
token/byte budget, tool-call count, latency, or whether the answer contains the
right caveats when evidence is inferred, ambiguous, absent, or unsupported.

## Solution

Build an Agent Answerability Benchmark as the first implementation phase of the
new roadmap.

The benchmark will define a versioned catalog of natural-language coding
questions, run those questions through explicit Seam tool plans, compare the
result against ground-truth evidence, and produce a report that says which daily
agent questions Seam answers well, which it answers partially, and which product
gap should be prioritized next.

This is primarily an evaluation and product-decision surface, not a new graph
extraction feature. The first version should reuse existing Seam tools and the
existing eval style, then add a deeper scenario model around them:

- natural-language question text;
- repo or fixture identity;
- required answer facts;
- required evidence;
- acceptable caveats;
- intended Seam tool path;
- grep/read fallback path for comparison;
- scoring axes;
- token/byte estimate;
- round-trip count;
- latency;
- freshness and capability notes;
- final product-gap classification.

The output should be useful to both maintainers and future agents. A maintainer
should be able to run the benchmark and see a ranked list of answerability gaps.
A future agent should be able to read the report and know whether the next PRD
should be a context-pack upgrade, change-planning surface, dead-code suspects,
docs/spec grounding, infra graph, protocol graph, or graph-quality coherence
work.

## User Stories

1. As a Seam maintainer, I want a repeatable answerability benchmark, so that
   roadmap decisions are based on observed agent failures instead of feature
   speculation.
2. As a Seam maintainer, I want benchmark questions written in natural language,
   so that the benchmark measures real agent needs rather than tool-shaped
   prompts.
3. As a Seam maintainer, I want each question to declare the answer facts it
   expects, so that scoring is grounded in source truth rather than vibes.
4. As a Seam maintainer, I want each question to declare required evidence, so
   that a correct answer without file, symbol, line, or provenance support does
   not receive full credit.
5. As a Seam maintainer, I want each question to declare acceptable caveats, so
   that Seam is rewarded for honest uncertainty instead of punished for not
   guessing.
6. As a Seam maintainer, I want the benchmark to capture false-confidence
   failures, so that Seam does not imply unsupported certainty when evidence is
   ambiguous, inferred, absent, stale, or unavailable.
7. As a Seam maintainer, I want the benchmark to track token or byte cost, so
   that improvements reduce context pressure instead of only improving raw
   correctness.
8. As a Seam maintainer, I want the benchmark to track tool round trips, so that
   we can identify when agents need too many Seam calls to answer a basic
   editing question.
9. As a Seam maintainer, I want the benchmark to track latency, so that the
   answerability workflow stays usable inside normal coding loops.
10. As a Seam maintainer, I want the benchmark to record index freshness and
    schema capabilities, so that stale or unsupported evidence is visible in the
    score.
11. As a Seam maintainer, I want the benchmark to reuse the existing eval
    infrastructure where possible, so that it fits the repo's test and reporting
    conventions.
12. As a Seam maintainer, I want a machine-readable report, so that future tools
    can summarize regressions and product gaps automatically.
13. As a Seam maintainer, I want a human-readable report, so that roadmap
    discussions can use the benchmark without reading raw JSON.
14. As a Seam maintainer, I want benchmark results grouped by question family,
    so that discovery, navigation, change-safety, cleanup, risk, architecture,
    protocol, and infra gaps can be compared separately.
15. As a Seam maintainer, I want benchmark results grouped by underlying Seam
    capability, so that failures point to concrete workstreams.
16. As a Seam maintainer, I want benchmark scenarios to name the ideal Seam tool
    path, so that we can measure whether the current tool surface is sufficient.
17. As a Seam maintainer, I want benchmark scenarios to name a grep/read fallback
    path, so that we can compare Seam against an ordinary agent reading files
    directly.
18. As a coding agent, I want to ask "where is this behavior implemented?", so
    that I can start edits in the correct module.
19. As a coding agent, I want to ask "which files define this concept?", so that
    I can avoid broad repo scans.
20. As a coding agent, I want to ask "which module owns this responsibility?", so
    that I can preserve existing boundaries.
21. As a coding agent, I want to ask "what should I read first before changing
    this feature?", so that I can build enough context without reading too much.
22. As a coding agent, I want to ask "who calls this symbol?", so that I can
    understand direct blast radius.
23. As a coding agent, I want to ask "what does this symbol call?", so that I can
    understand dependencies and side effects.
24. As a coding agent, I want to ask "what breaks if I change this symbol?", so
    that I can plan a safer implementation.
25. As a coding agent, I want to ask "which tests are likely relevant?", so that
    I can run focused verification.
26. As a coding agent, I want to ask "which routes, config keys, resources, or
    exceptions are involved?", so that I can see operational and boundary impact.
27. As a coding agent, I want to ask "what changed in my current diff and what
    should I test?", so that I can verify edits before handoff.
28. As a coding agent, I want to ask "is this touching a hotspot or shared
    boundary?", so that I can be more conservative on risky edits.
29. As a coding agent, I want to ask "show me the shortest path from entry point
    to implementation", so that I can reason through a flow.
30. As a coding agent, I want to ask "give me local context without dumping whole
    files", so that my context window stays focused.
31. As a coding agent, I want to ask "give me a compact editing context pack", so
    that I can gather symbol, neighbor, test, and rationale evidence in one
    step.
32. As a coding agent, I want to ask "find the route handler for this endpoint",
    so that API changes start from the right entry point.
33. As a coding agent, I want to ask "find client code that calls this route", so
    that protocol-edge gaps become visible.
34. As a coding agent, I want to ask "find config keys and resources used by this
    code path", so that deployment and configuration impact is not hidden.
35. As a coding agent, I want to ask "is this symbol likely unused?", so that I
    can identify cleanup candidates without unsafe deletion claims.
36. As a coding agent, I want to ask "is this file orphaned or still imported?",
    so that I can assess cleanup risk.
37. As a coding agent, I want to ask "which public APIs have no known tests?", so
    that I can focus hardening work.
38. As a coding agent, I want to ask "which edges are inferred or ambiguous?", so
    that I know where static evidence needs human verification.
39. As a coding agent, I want to ask "what are the repo's main boundaries?", so
    that I can place changes in the right area.
40. As a coding agent, I want to ask "which modules are hubs?", so that I can
    identify high-risk shared code.
41. As a coding agent, I want to ask "which areas are highly coupled?", so that I
    can avoid accidental architectural drift.
42. As a product owner, I want answerability reports to identify the next best
    Seam PRD, so that the roadmap stays sequenced by impact.
43. As a product owner, I want the benchmark to say when Kubernetes/Kustomize or
    another large graph domain is not yet justified, so that scope stays tied to
    measured need.
44. As a product owner, I want the benchmark to distinguish missing capability
    from poor presentation, so that we do not build new extraction when a better
    output bundle would solve the problem.
45. As a future implementation agent, I want scenarios, scoring, reports, and
    acceptance criteria to be explicit, so that I can implement the benchmark
    without re-litigating product scope.

## Implementation Decisions

- Build the benchmark around a deep scenario-catalog module. Its public contract
  should be stable and small: load scenario definitions, validate required
  fields, expose scenario records, and reject malformed scenarios with clear
  messages.
- Scenario definitions should be structured data, not prose-only Markdown. Each
  scenario should include an id, category, natural-language question, fixture or
  repo target, setup notes, expected facts, required evidence, acceptable caveats,
  ideal Seam tool plan, fallback grep/read plan, scoring axes, capability tags,
  and product-gap tags.
- Keep natural-language question text separate from the tool plan. The question
  should describe what an agent needs to know; the tool plan should describe how
  the benchmark asks Seam to gather evidence.
- Define a small answerability runner module that executes scenario tool plans
  deterministically. The first version should run explicit Seam CLI or handler
  calls, not a free-form LLM agent, so that benchmark output is reproducible.
- The runner should always inspect schema/freshness before scenario execution
  for a repo or fixture. Stale index behavior and missing capability behavior are
  part of the measured contract.
- Define a tool-step result envelope shared by all tool executions. It should
  capture tool name, arguments, ok/error state, elapsed time, raw byte count,
  estimated token count, parsed output, freshness warnings, and capability
  warnings.
- Define an evidence extractor that normalizes symbol, file, line, route,
  config, resource, edge kind, confidence, and provenance facts from Seam output.
  Scoring should use normalized evidence rather than brittle raw-output string
  matching.
- Define a scorer module as another deep module. Its public contract should take
  a scenario plus normalized run evidence and return scores, notes, missing
  facts, missing evidence, caveat quality, false-confidence notes, and product
  gap labels.
- Use a 0-2 scale per primary axis. `0` means absent or misleading, `1` means
  partially useful but incomplete, and `2` means correct, bounded, and
  evidence-backed.
- Score at least these primary axes: answer facts, evidence sufficiency, caveat
  honesty, output cost, round trips, latency, freshness handling, and false
  confidence.
- The aggregate report should not hide low-confidence failures behind a single
  average. It should show per-question rows, category summaries, capability
  summaries, and top product gaps.
- The benchmark should classify failures into product-action buckets:
  context-pack upgrade, change-planning surface, dead-code/orphan suspects,
  docs/spec grounding, protocol-edge quality, infra graph, graph-search recipe,
  output-budget tuning, stale-index/freshness UX, or graph-quality coherence.
- The first scenario set should contain 20-40 questions. It should be broad
  enough to cover daily agent work but small enough to maintain by hand.
- The first scenario set should include both Seam itself and small controlled
  fixtures. Seam gives realistic surface area; fixtures give deterministic
  ground truth and stable regression behavior.
- The first scenario set may include optional external local repos when present,
  but missing optional repos must skip cleanly. Required CI/gate behavior should
  not depend on private local checkouts.
- Ground truth should be authored as expected facts and required evidence, not
  generated from the live Seam output. The existing recall golden style can be
  reused where appropriate, but answerability scoring must not treat today's
  Seam output as truth.
- For the grep/read baseline, model realistic bounded file-reading behavior
  rather than an omniscient baseline. The baseline should count commands,
  bytes/tokens read, and whether it found the same answer facts.
- The benchmark should produce both machine-readable and Markdown reports. The
  Markdown report should be suitable for roadmap review and should include a
  "recommended next PRD" section.
- Reports should include schema version, Seam version, timestamp, repo
  fingerprint or fixture hash, scenario-set version, and command/runtime
  metadata so that results can be compared later.
- The first implementation should add a local command or test target for running
  the answerability benchmark. It should not be part of the default gate until
  runtime and determinism are proven.
- A small smoke subset should be eligible for normal tests once stable. The full
  benchmark can remain a manual or optional CI target.
- The benchmark must stay local-first. It must not require network calls, remote
  model calls, telemetry, or external SaaS services.
- Optional LLM judging can be designed as a later extension, but the first
  implementation should not require it. Deterministic scoring is the core.
- The benchmark should not add new dependency edges or schema tables to answer
  questions. It should measure the current tool surface first.
- Existing MCP/CLI/Web tool contracts should not be changed by this PRD except
  for small reporting or documentation improvements needed to make evaluation
  clear.
- The PRD does not require implementing the next product feature. It must end
  with evidence that chooses the next feature.

## Testing Decisions

- Good tests should assert external behavior: scenario loading, validation,
  deterministic runner output, scoring decisions, skip behavior, report shape,
  and failure classification. They should not assert private helper structure or
  raw command formatting when a structured result is available.
- The scenario-catalog module should have unit tests for required fields,
  unknown category rejection, duplicate id rejection, malformed scoring config,
  missing required evidence, and stable scenario ordering.
- The runner should have tests using small fake tool adapters so elapsed time,
  byte count, ok/error state, and parsed output behavior can be checked without
  invoking expensive indexing.
- The Seam tool adapter should have integration coverage against a controlled
  fixture to prove schema, search/query/context/impact/trace/affected or graph
  search steps can be executed and normalized.
- The scorer should have unit tests for full-credit, partial-credit, no-credit,
  misleading-answer, missing-evidence, honest-caveat, stale-index, unsupported
  capability, and false-confidence cases.
- The report writer should have tests for stable machine-readable output,
  stable Markdown headings, category summaries, top-gap ordering, and recommended
  next-PRD selection.
- Fixture hashing should be reused or mirrored so scenario ground truth cannot
  silently drift when fixture files change.
- The benchmark should include a regression test that proves a deliberately
  missing evidence item lowers the evidence score.
- The benchmark should include a regression test that proves an unsupported
  capability with an honest caveat is scored differently from a confident but
  unsupported answer.
- The benchmark should include a regression test that proves byte/token
  accounting changes when a tool emits a larger response.
- The benchmark should include a regression test that proves optional local repos
  skip without failing the whole run.
- Prior art includes the existing recall/MRR eval harness, fixture hash checks,
  golden generation, architecture tool tests, graph-search tests, affected tests,
  changes tests, context-pack tests, and schema tool tests.
- Before landing implementation work, run the focused eval tests plus the normal
  Python gate. If the full answerability benchmark is not yet gate-wired, run it
  manually and save the report.

## Out of Scope

- Adding Kubernetes, Kustomize, Helm, Terraform, cloud resources, or other new
  infra graph extraction.
- Adding new protocol families such as gRPC, GraphQL, tRPC, message queues, or
  pub/sub.
- Implementing the context-pack upgrade, change-planning surface, dead-code
  suspects, or docs/spec grounding. This benchmark should decide whether those
  are next.
- Building a general-purpose graph query language.
- Making MCP mutation-capable.
- Runtime probing, server introspection, network discovery, telemetry, or
  production log ingestion.
- Secret value indexing.
- Requiring a remote LLM judge, API key, or external service.
- Making the full benchmark part of the default gate before runtime and
  determinism are known.
- Treating semantic similarity as dependency evidence.
- Claiming Seam can answer every possible agent question. The benchmark should
  explicitly surface questions that require runtime state, external docs,
  product intent, human judgment, or data outside the local repo.

## Further Notes

The current local Seam index is fresh and reports schema version 15 with routes,
config/resource nodes, exception edges, synthesized test edges, architecture,
graph search, affected tests, changes, and context-pack support. That means this
benchmark can start by measuring real existing product behavior instead of
waiting for new extraction work.

The first benchmark should answer one product question:

> For daily coding-agent questions, does Seam provide the smallest trustworthy
> local evidence bundle?

The implementation should produce a report that makes the next roadmap decision
obvious. If failures cluster around too many tool calls and scattered evidence,
the next PRD should be a context-pack or change-planning upgrade. If failures
cluster around route/client relationships, protocol edges should move up. If
failures cluster around deployment questions, infra graph work should move up.
If failures cluster around "why does this exist?", docs/spec grounding should
move up. If failures cluster around stale or contradictory capability language,
graph-quality coherence should move up.

Success is not a perfect score. Success is a trustworthy measurement loop that
keeps Seam from adding impressive but low-leverage graph features before proving
they help real coding agents.
