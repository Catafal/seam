# PRD - Agent Answerability Graph-Quality Coherence

> Status: ready-for-agent.
> Created: 2026-07-06.
> Roadmap source: `.claude/tasks/agent-answerability-roadmap.md` and
> `.claude/tasks/codememory-inspired-agent-answerability-roadmap.md`.
> Current trigger: the core answerability workstreams have shipped, but roadmap,
> benchmark, issue-tracker, schema, and architecture signals can still drift.

## Problem Statement

Seam's product goal is to help coding agents navigate a local codebase with less
token spend, fewer broad reads, and stronger local evidence than ad hoc grep. The
current tree has moved a long way toward that goal: schema introspection, search,
graph search recipes, snippets, architecture summaries, context packs, change
plans, impact, affected tests, cleanup suspects, routes, HTTP-call evidence,
config/resource evidence, Docker/Compose and Dockerfile infra evidence,
exception edges, test edges, docs/spec grounding, semantic discovery metadata,
trusted shared index bootstrap, MCP auto-init, and installer autodetect all have
implementation evidence.

The remaining next-step problem is not another graph domain by default. The
problem is coherence.

Agents choose their next action from Seam's signals: `seam schema`, architecture
sections, answerability reports, local PRDs, roadmap files, open GitHub issues,
tool registry guidance, docs, and capability caveats. If any of those signals
contradict the shipped code, agents waste time or rebuild stale work. This has
already happened in the current repository state:

- the local schema reports many shipped answerability capabilities;
- the answerability benchmark exists and runs, but its roadmap signal currently
  counts product-gap labels even when scenarios pass or intentionally test honest
  unsupported capability states;
- GitHub issue #371 is still open for docs/spec grounding even though docs
  grounding has implementation evidence in `main`;
- GitHub issue #316 for Kubernetes/Kustomize remains open, but the newer
  answerability roadmap explicitly says Kubernetes/Kustomize should stay
  demand-gated rather than become the automatic next phase;
- documentation files mention scenario counts and fixture hashes that can drift
  from the maintained JSON scenario catalog;
- capability flags such as "supported but empty", "unsupported", "populated",
  "stale", and "old index" must stay precise across schema, architecture, MCP,
  CLI, Web, docs, and benchmark output.

From the user's perspective, the risk is simple: future agents may look at a
stale issue, stale PRD, or misleading benchmark aggregate and conclude that Seam
should build the wrong thing next. That directly violates Seam's purpose: reduce
agent token spend and navigation friction.

This phase should make Seam's answerability signals internally consistent,
failure-weighted, and hard to misread. It should make the next roadmap decision
come from real failing agent questions, not from stale metadata or product-gap
labels attached to successful regression scenarios.

## Solution

Build a graph-quality and roadmap-signal coherence layer around the existing
answerability benchmark and shipped tool surfaces.

This is a product-quality phase, not a new extractor. It should add deterministic
checks and small reporting improvements that answer:

- Which answerability scenarios are failing, partial, passing, or only recording
  future roadmap pressure?
- Which product-gap labels are attached to actual failures versus passing
  regression scenarios?
- Which roadmap recommendations are justified by failing scenario evidence?
- Which local PRDs or GitHub issues appear stale relative to current schema,
  merged implementation evidence, or closed child issues?
- Do schema capabilities, architecture sections, graph-search recipes, MCP tool
  descriptions, docs, and benchmark reports use the same language for supported,
  empty, unsupported, stale, and populated evidence?
- Can an agent trust the benchmark's "recommended next PRD" without re-auditing
  every roadmap and issue manually?

The first implementation should produce a stricter answerability report and a
coherence audit. The report should separate three different concepts that are
currently easy to conflate:

1. **Failure gaps**: product-gap labels from scenarios whose scores prove missing
   facts, missing evidence, excessive output, poor caveats, stale-index behavior,
   or false confidence.
2. **Roadmap pressure**: labels from passing scenarios that represent unsupported
   but intentionally deferred capabilities.
3. **Regression coverage**: passing scenarios with no current product demand.

The output should rank next work from failure gaps first. Roadmap pressure should
be visible, but it should not outrank actual failing daily-agent questions.

The coherence audit should be local-first and deterministic. GitHub issue checks
can be an optional online enhancement because normal tests and local development
must not require network access. The local layer should inspect checked-in PRDs,
roadmaps, docs, answerability scenarios, schema capability output, and
architecture output. The optional tracker layer should compare open PRD issues
against local shipped evidence and produce comments or close recommendations for
humans or implementation agents.

## User Stories

1. As a Seam maintainer, I want answerability reports to rank product gaps only
   when scenarios actually fail or partially fail, so that the next PRD follows
   measured agent friction.
2. As a Seam maintainer, I want passing scenarios to be allowed to carry roadmap
   pressure separately from failure gaps, so that demand-gated ideas remain
   visible without pretending they are blocking today's agent workflow.
3. As a Seam maintainer, I want passing regression scenarios with no remaining
   product gap to show no product-gap pressure, so that reports do not keep
   recommending work that already shipped.
4. As a future coding agent, I want the answerability report to say "recommended
   next PRD" only when the recommendation is based on failing or partial
   scenarios, so that I do not rebuild stale features.
5. As a future coding agent, I want the report to distinguish "Seam lacks this
   evidence" from "Seam correctly reports this evidence is unsupported here", so
   that honest absence is not mistaken for a product failure.
6. As a future coding agent, I want unsupported capability scenarios to pass when
   Seam gives an explicit caveat, so that Seam is rewarded for honesty.
7. As a future coding agent, I want unsupported capability scenarios to create
   demand-gated roadmap pressure only when the roadmap says that domain is
   useful later, so that deferred work remains intentional.
8. As a Seam maintainer, I want the benchmark to expose per-axis low scores in
   the aggregate summary, so that a low evidence score is not hidden behind a
   high average.
9. As a Seam maintainer, I want product-gap counts grouped by failing score axis,
   so that output-cost failures point to byte-budget work and missing-evidence
   failures point to graph or presentation work.
10. As a Seam maintainer, I want the benchmark Markdown report to show top
    failure gaps, top roadmap-pressure labels, and top regression-only labels as
    separate sections, so that roadmap decisions are auditable.
11. As a Seam maintainer, I want machine-readable JSON to contain the same
    separation, so that future automation can consume the report safely.
12. As a Seam maintainer, I want tests proving a fully passing scenario does not
    become a top failure gap, so that stale product-gap tags cannot dominate
    roadmap recommendations.
13. As a Seam maintainer, I want tests proving a partial scenario with a
    product-gap tag does become a top failure gap, so that real failures still
    drive the roadmap.
14. As a Seam maintainer, I want tests proving an unsupported-but-honest
    scenario can pass while still appearing as roadmap pressure, so that
    Kubernetes/Kustomize and similar deferred ideas stay visible but not urgent.
15. As a Seam maintainer, I want scenario metadata to include an explicit
    demand state, so that `product_gap_tags` are not overloaded with several
    meanings.
16. As a Seam maintainer, I want scenario metadata validation to reject ambiguous
    gap metadata, so that future scenario authors must choose failure gap,
    roadmap pressure, or regression coverage deliberately.
17. As a Seam maintainer, I want benchmark docs to state how scenario labels
    affect roadmap recommendations, so that future agents do not infer their own
    rules.
18. As a Seam maintainer, I want docs and scenario headers to agree on scenario
    set version, scenario count, and fixture hash, so that reproduction
    instructions do not drift.
19. As a Seam maintainer, I want a local coherence check to fail when docs claim
    a different scenario count or fixture hash than the scenario catalog, so that
    report docs stay fresh.
20. As a Seam maintainer, I want a local coherence check to compare schema
    capability flags with architecture section statuses, so that architecture
    output does not warn that evidence is missing while schema says it exists.
21. As a Seam maintainer, I want coherence checks around `has_test_edges`,
    `has_http_calls`, `has_doc_grounding`, `has_infra_graph`, `has_embeddings`,
    and bootstrap provenance, so that high-value agent tool choices stay honest.
22. As a coding agent, I want capability states to distinguish "tool supported",
    "table exists", "evidence populated", "evidence empty", and "old index
    unsupported", so that I choose a fallback only when necessary.
23. As a coding agent, I want `seam architecture` optional sections to use the
    same state vocabulary as `seam schema`, so that I do not have to reconcile
    separate meanings.
24. As a coding agent, I want architecture warnings to be evidence-based, so that
    stale warnings do not contradict populated edges.
25. As a coding agent, I want graph-search recipe availability to match schema
    capabilities, so that recipes do not advertise impossible follow-up paths.
26. As a coding agent, I want docs and MCP tool descriptions to match current
    tool names and caveats, so that tool selection does not depend on stale
    README text.
27. As a Seam maintainer, I want stale PRDs to be flagged when their described
    feature has schema/tool/test implementation evidence, so that future agents
    do not use stale PRDs as new work.
28. As a Seam maintainer, I want open GitHub PRD issues to be optionally audited
    against merged PRs, closed child issues, schema capabilities, and local docs,
    so that tracker hygiene can be done without manual archaeology.
29. As a Seam maintainer, I want tracker audit output to recommend actions
    instead of mutating issues by default, so that automation does not close
    issues incorrectly.
30. As a Seam maintainer, I want an explicit "can auto-close" classification only
    when evidence is strong, such as matching merged PR, implemented schema
    capability, and closed implementation slices, so that stale issue cleanup
    stays safe.
31. As a Seam maintainer, I want Kubernetes/Kustomize to remain demand-gated
    unless benchmark failure evidence justifies it, so that Seam does not chase
    competitor parity over daily-agent usefulness.
32. As a future implementation agent, I want the coherence audit to list the
    exact files, docs, issues, and capabilities that conflict, so that I can fix
    the smallest source of drift.
33. As a future implementation agent, I want a clear test target for coherence
    checks, so that PRs touching schema, architecture, docs, roadmap, or
    answerability scenarios run the right verification.
34. As a future implementation agent, I want full `make gate` to remain bounded,
    so that expensive optional online tracker checks are not required for normal
    code changes.
35. As a future implementation agent, I want the coherence layer to be
    deterministic and LLM-free, so that roadmap signals are reproducible.
36. As a future implementation agent, I want the output to be small and
    machine-readable, so that coding agents can ingest it without wasting tokens.
37. As a Seam user, I want the roadmap docs to say what is next and why, so that
    I can understand why Kubernetes/Kustomize is not the automatic next build.
38. As a Seam user, I want the roadmap docs to preserve deferred competitor
    ideas, so that useful future work is not lost just because it is not next.
39. As a Seam user, I want graph-quality coherence to cover both false positives
    and false negatives in product signals, so that Seam remains conservative
    without hiding useful gaps.
40. As a Seam maintainer, I want this phase to end with a clean recommendation
    for the next implementation PRD, so that the roadmap can proceed from a
    trustworthy signal loop.

## Implementation Decisions

- Treat this as Workstream 7 of the agent-answerability roadmap: graph-quality
  coherence, plus the tracker-hygiene concern from Workstream 1 that remains
  visible in open issues.
- Do not implement Kubernetes/Kustomize in this phase. Keep it as roadmap
  pressure until failing answerability scenarios prove the need.
- Do not add a new graph extractor, schema table for code facts, runtime probe,
  network path, or MCP mutation tool in this phase.
- Add a small deep module for answerability report classification. Its public
  contract should accept `ScenarioScore` records plus scenario metadata and
  return failure gaps, roadmap pressure, regression coverage, axis summaries,
  and a recommended next PRD decision.
- Extend scenario metadata so product labels have explicit semantics. A concrete
  shape can be chosen during implementation, but it should distinguish:
  `failure_gap_tags`, `roadmap_pressure_tags`, and `regression_tags`, or an
  equivalent typed structure.
- Preserve backwards compatibility while migrating existing scenarios. If the
  existing `product_gap_tags` key remains for one transition, it should be
  interpreted conservatively and accompanied by validation warnings.
- Update `summarize_results` so top failure gaps count only non-passing
  scenarios or low-scoring axes, not every label on every scenario.
- Update `render_markdown_report` so "Roadmap Signal" shows:
  top failing answerability gaps, top demand-gated roadmap pressure, passing
  regression coverage, lowest scoring scenarios, and the recommended next PRD.
- The recommendation algorithm should be simple and explainable. It should prefer
  failure gaps over roadmap pressure; if there are no meaningful failures, it
  should say that no new graph domain is justified and recommend either
  coherence cleanup, benchmark expansion, or the highest demand-gated item.
- Add per-axis aggregate summaries. At minimum include answer, evidence, caveats,
  output cost, round trips, latency, freshness, and false confidence.
- Add scenario-level status categories beyond `passed` and `partial` only if they
  improve clarity. Candidate statuses are `passed`, `partial`, `failed`, and
  `unsupported-honest`.
- Keep all scoring deterministic. Do not introduce an LLM judge for this phase.
- Add a local coherence-check module for documentation and benchmark metadata.
  Its public contract should accept root paths and loaded benchmark metadata,
  then return a list of coherence findings with severity, evidence, and suggested
  fix.
- The first local coherence checks should cover scenario count, scenario set
  version, fixture hash, documented command names, and stale statement patterns
  in the answerability benchmark docs.
- Add schema/architecture coherence checks around optional evidence surfaces. The
  checks should exercise a controlled fixture or current index and verify that
  schema capability flags do not contradict architecture section statuses or
  warnings.
- Reuse the existing architecture optional-section language wherever possible.
  The state vocabulary should be normalized to `unsupported`, `supported-empty`,
  `populated`, `stale`, and `old-index` or an equivalent small set.
- Add targeted regression tests for the previously observed class of bug where
  an architecture output can show populated evidence while warning that the
  evidence does not exist.
- Add graph-search recipe coherence checks only where recipes depend on optional
  capabilities. A recipe can exist even when a repo has no evidence, but its
  caveat should say empty evidence rather than unsupported support if the schema
  supports the surface.
- Add docs/tool-registry coherence checks for major tool names and caveat terms.
  This should be narrow and avoid brittle prose assertions.
- Add an optional tracker-audit command or script that uses GitHub only when
  explicitly invoked. It should not run in `make gate`.
- The tracker-audit output should classify open issues as:
  `active`, `possibly-stale`, `implemented-needs-close`, `deferred`, or
  `needs-human-review`.
- The tracker audit should use strong local evidence first: merged PR references
  in git log, local PRD status, schema capability, tool availability, tests, and
  child issue closure.
- Issue mutation should stay out of scope for the default audit. A future
  implementation phase can add explicit `--apply` behavior if desired.
- Update `.claude/tasks/agent-answerability-roadmap.md` or a successor task file
  only if implementation needs to mark shipped/stale status. The PRD itself
  should define the work; implementation can decide the exact docs touched.
- Add a generated or checked-in example report only if it is stable. Otherwise,
  document the command to reproduce it.
- Keep byte budgets visible. The stricter report should not become a huge wall
  of data by default; detailed per-scenario diagnostics can be behind JSON or a
  verbose flag.
- Preserve current benchmark command compatibility: `make eval-answerability`
  and `uv run python -m tests.eval.answerability_report --json` should continue
  to work.
- Add new flags only if they keep the default behavior understandable. Candidate
  flags: `--strict`, `--coherence`, `--markdown-out`, and `--tracker-audit`.
- The full implementation should end by running the answerability benchmark and
  updating its docs so scenario count, version, and fixture hash match the
  maintained catalog.

## Testing Decisions

- Good tests should assert public report behavior and coherence findings, not
  private helper ordering.
- Add unit tests for report classification:
  - passing scenario with roadmap-pressure metadata does not become a failure
    gap;
  - partial scenario with a gap label becomes a failure gap;
  - unsupported-but-honest scenario can pass and appear only as roadmap pressure;
  - scenario with no labels appears as regression coverage only;
  - recommendation prefers failure gaps over roadmap pressure;
  - recommendation says no product feature is justified when all scenarios pass
    and no roadmap pressure exists.
- Add validation tests for new scenario metadata. Ambiguous or mutually
  contradictory labels should be rejected with actionable messages.
- Add migration tests for the current scenario catalog so the maintained
  `answerability_scenarios.json` remains valid after metadata changes.
- Add report-shape tests for JSON summary fields and Markdown sections:
  top failure gaps, top roadmap pressure, regression coverage, low-score axes,
  and recommended next PRD.
- Add docs coherence tests for scenario count, scenario set version, fixture hash,
  and reproduction commands in the benchmark docs.
- Add schema/architecture coherence tests using controlled fixtures:
  - no test edges should be reported as missing when test edges are populated;
  - route nodes and HTTP-call evidence should distinguish supported-empty from
    populated;
  - document grounding should distinguish anchors from resolved references;
  - infra graph should distinguish "supported extractor but no infra evidence in
    this repo" from "unsupported old index";
  - semantic discovery should remain opt-in and should not imply dependency
    evidence.
- Add recipe coherence tests where optional capability caveats are surfaced by
  graph-search recipes.
- Add optional tracker-audit tests with fake issue data and fake git evidence.
  These tests should not call GitHub or the network.
- Add CLI tests only if a new command or flag is added. If the work stays inside
  the existing answerability report command, test that command's JSON and
  Markdown output.
- Run the maintained answerability benchmark after implementation. The target is
  not necessarily a perfect score; the target is a trustworthy separation of
  failure gaps from roadmap pressure.
- Run focused tests for answerability, schema, architecture, graph-search recipe
  coherence, and docs metadata. Run `make gate` before merging implementation.
- Run `uv run seam sync` before relying on local schema/grounding output, and run
  `uv run seam changes --json` before commit as required by the project agent
  instructions.

## Out of Scope

- Kubernetes/Kustomize extraction.
- Helm, Terraform, cloud, runtime, cluster, log, telemetry, or network
  discovery.
- New dependency edges or new code graph semantics.
- Changing default impact traversal.
- Semantic similarity as dependency evidence.
- LLM-judged benchmark scoring.
- Auto-closing GitHub issues by default.
- Mutating GitHub issues, PRDs, roadmap files, or task files without an explicit
  implementation step and reviewable diff.
- Rewriting old PRDs wholesale.
- Making the answerability benchmark part of the default gate if runtime or
  determinism becomes unsuitable.
- Building a full project-management system inside Seam.
- Treating docs as source of truth over code. Docs can ground intent; shipped
  implementation evidence still needs schema, tests, and tool output.

## Further Notes

The current local research points to this as the next phase:

- `make eval-answerability` exists and runs through
  `tests/eval/answerability_report.py`.
- The maintained scenario catalog has 26 scenarios and reports average score
  around 1.913 in the current tree.
- The current summary reports top product gaps as graph-quality coherence, infra
  graph, and protocol-edge quality, but the implementation counts labels across
  all scenarios rather than only low-scoring scenarios.
- GitHub issue #371 remains open for docs/spec grounding even though docs
  grounding has landed in `main` and schema reports `has_doc_grounding`.
- GitHub issue #316 remains open for Kubernetes/Kustomize, but current roadmap
  text says Kubernetes/Kustomize is deferred until answerability evidence or user
  demand proves it is needed.
- Recent merged PRs show HTTP extraction quality, exact type resolution,
  semantic discovery metadata, trusted shared index bootstrap, MCP auto-init, and
  installer autodetect have all landed after the original roadmap was drafted.

This PRD intentionally keeps Seam focused on answerability, not competitor
parity. The product question is not "what does another memory repo index?" The
product question is "what makes Claude Code, Codex, OpenCode, Cursor, and similar
agents reach the right code with fewer tokens and less false certainty?"

After this PRD is implemented, the next roadmap decision should be much cleaner:
if failure gaps point to graph-quality output, fix that; if they point to infra
questions, reconsider Kubernetes/Kustomize; if they point to protocol questions,
improve protocol edges; if they point to no serious daily-agent gap, prioritize
release trust, docs, or benchmark expansion instead of adding a new graph domain.
