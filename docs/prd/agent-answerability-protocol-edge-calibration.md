# PRD - Agent Answerability Protocol-Edge Calibration

> Status: implemented in `codex/protocol-edge-calibration`.
> Created: 2026-07-06.
> GitHub issue: https://github.com/Catafal/seam/issues/418.
> Tracker label: `implemented`.
> Roadmap source: CodeMemory-inspired agent answerability roadmap and the
> pre-calibration answerability report recommendation.

## Problem Statement

Before this calibration, Seam's answerability report recommended
`protocol-edge quality` as the next PRD because the protocol category was still
partial and the top failure gap was protocol-related. The important nuance was
that the original HTTP-call extraction quality PRD had already been implemented
and closed. The local index already had route nodes, HTTP-call edges,
route-resolution evidence, a graph-search recipe for HTTP callers, architecture
protocol sections, and a fixture route caller with provenance.

From the user's perspective, this creates a different problem than the old
"build HTTP extraction" gap. The product now has protocol evidence, but the
answerability benchmark and user-facing protocol signals have not been fully
calibrated to the shipped behavior. One protocol scenario still expects route
nodes to be unsupported or empty even though the fixture now contains route
nodes. Another route-caller scenario can return the correct symbol, route,
`http_calls` edge, route-resolution flag, and provenance, while still landing as
partial because the benchmark scoring and output-cost expectations have not been
updated around the new protocol surface.

That stale signal matters because Seam is being optimized for commercial coding
agents such as Claude Code, Codex, OpenCode, Cursor, Gemini, and similar tools.
Those agents should be able to ask "who calls this route?" and "can Seam
represent routes here?" with less token spend than broad grep. If the benchmark
continues to say protocol-edge quality is failing after the core evidence exists,
future agents will either rebuild completed extractor work or mistrust a useful
tool path. That wastes exactly the time and context Seam is meant to save.

This PRD should close the gap between shipped protocol evidence and measured
answerability. It should make the benchmark, schema capability checks,
graph-search recipe behavior, architecture wording, docs, and roadmap signal all
agree about the same facts: route nodes are supported and populated when present;
HTTP caller evidence is populated when supported static call sites match local
routes; unsupported or empty cases are honest states rather than product
failures; and remaining protocol work should be driven by concrete missing
patterns, not stale scenario expectations.

## Solution

Build a focused protocol-edge answerability calibration pass. The pass should not
add a new protocol graph domain, a new route model, or a new MCP tool. It should
use the existing route and HTTP-call graph evidence, then fix the benchmark and
presentation layers so agents receive accurate, compact, evidence-backed answers.

The work should do five things.

First, update the protocol answerability scenarios so they reflect the current
fixture truth. The route-node capability scenario should no longer expect
`has_route_nodes:false` when the fixture has indexed route nodes. It should test
the positive state instead: route nodes are available, the relevant route appears
with route identity, and the output includes enough evidence for an agent to use
the route-entrypoint or HTTP-caller recipe next.

Second, tighten the route-caller scenario so it proves daily agent usefulness.
When the tool path returns the client function, the route node, the `http_calls`
edge, `route_resolved:true`, and direct extractor provenance, the scenario should
pass unless there is a real token, caveat, freshness, latency, or round-trip
regression. If output size is what keeps it partial, the scoring rule should
identify that explicitly as an output-budget issue instead of labeling it a
protocol-edge failure.

Third, improve protocol capability language across the relevant read surfaces.
Schema, graph-search recipes, architecture, answerability reports, and docs
should use the same distinction between supported-empty and populated evidence.
Route inventory and HTTP caller evidence are related but separate facts. A repo
can have routes and no static caller edges; that is not the same as unsupported
protocol extraction.

Fourth, add a small protocol coherence check to prevent this class of drift from
returning. The check should compare route-related scenario expectations with the
fixture's actual schema capability state. If a scenario expects a capability to
be false while the fixture exposes it as true, the check should fail with an
actionable finding before the roadmap report can recommend stale work.

Fifth, rerun the answerability report and make the next roadmap signal honest.
If protocol-edge quality is no longer backed by non-passing protocol scenarios,
the report should stop recommending it as the top failure gap. If a real
protocol scenario still fails, the failure should name the precise missing
pattern or presentation issue: wrapper extraction, method/path normalization,
route-node evidence, graph-search filtering, output budget, or caveat language.

## User Stories

1. As an AI coding agent, I want the answerability benchmark to reflect the
   current route-node capability state, so that I do not rebuild already-shipped
   route support.
2. As an AI coding agent, I want a route capability scenario to pass when route
   nodes are populated, so that positive protocol evidence is recognized as
   useful.
3. As an AI coding agent, I want a route capability scenario to fail only when
   route support is absent, stale, contradictory, or misleading, so that failure
   labels point to real product work.
4. As an AI coding agent, I want route inventory and HTTP caller evidence to be
   scored separately, so that routes-without-callers is not mistaken for no route
   support.
5. As an AI coding agent, I want the route-caller scenario to pass when it
   returns the caller symbol, route node, `http_calls` edge, resolved route flag,
   and provenance, so that correct protocol answers are rewarded.
6. As an AI coding agent, I want output-cost failures to be labeled as output
   budget issues, so that protocol extraction is not blamed for verbose output.
7. As an AI coding agent, I want evidence failures to be labeled as evidence
   issues, so that missing file, line, route, confidence, or provenance data is
   visible.
8. As an AI coding agent, I want stale scenario metadata to be caught before it
   drives the roadmap, so that future PRDs follow current facts.
9. As an AI coding agent, I want schema capability checks to report populated
   route nodes accurately, so that I choose graph-search route recipes with
   confidence.
10. As an AI coding agent, I want schema capability checks to report populated
    HTTP-call edges accurately, so that I know when route-caller discovery is
    available.
11. As an AI coding agent, I want schema guidance to distinguish unsupported,
    supported-empty, populated, stale, and old-index states, so that I choose the
    right fallback path.
12. As an AI coding agent, I want the route-entrypoints recipe to be available
    when route nodes are populated, so that I can enumerate endpoint handlers
    without grep.
13. As an AI coding agent, I want the HTTP-callers recipe to be available when
    `http_calls` edges are populated, so that I can answer "who calls this
    route?" with one bounded graph query.
14. As an AI coding agent, I want graph-search previews for HTTP-call edges to
    include route resolution and provenance, so that I can trust the edge without
    opening whole files.
15. As an AI coding agent, I want architecture output to say when routes exist
    but HTTP callers are empty, so that the absence of caller evidence is honest
    and not overinterpreted.
16. As an AI coding agent, I want architecture output to say when HTTP caller
    evidence is populated, so that protocol boundaries appear in a repository
    briefing.
17. As an AI coding agent, I want answerability reports to stop recommending
    protocol-edge work when protocol scenarios pass, so that the next roadmap
    item is chosen from real failures.
18. As an AI coding agent, I want answerability reports to keep recommending
    protocol-edge work when a supported route-caller question truly fails, so
    that real gaps are not hidden by optimistic scoring.
19. As a Seam maintainer, I want protocol scenarios to encode positive and
    negative capability states explicitly, so that future fixture changes do not
    silently invert expectations.
20. As a Seam maintainer, I want benchmark scenario metadata to avoid ambiguous
    legacy product-gap tags, so that route capability checks classify as failure
    gaps only when they actually fail.
21. As a Seam maintainer, I want a coherence check that detects impossible
    scenario expectations, so that a populated capability cannot be tested as
    unsupported by accident.
22. As a Seam maintainer, I want the protocol category average to represent real
    protocol quality, so that it can be compared against architecture,
    change-safety, cleanup, discovery, docs, and infra categories.
23. As a Seam maintainer, I want the route-caller scenario to include an explicit
    byte budget, so that a partial score caused by verbosity becomes actionable.
24. As a Seam maintainer, I want route-caller scoring to require provenance, so
    that an answer without extractor evidence cannot receive a full evidence
    score.
25. As a Seam maintainer, I want route-caller scoring to require route resolution,
    so that generic URL string matches do not count as local route evidence.
26. As a Seam maintainer, I want route-caller scoring to require the edge kind,
    so that ordinary call edges are not mistaken for protocol coupling.
27. As a Seam maintainer, I want capability checks to be tested through public
    handler behavior, so that tests cover what agents actually consume.
28. As a Seam maintainer, I want graph-search recipe tests to prove the same
    public route-caller path used by the benchmark, so that benchmark and product
    behavior cannot drift.
29. As a Seam maintainer, I want documentation to explain why route nodes and
    HTTP-call edges are separate surfaces, so that future agents do not collapse
    them into one capability.
30. As a Seam maintainer, I want the roadmap to preserve future protocol ideas
    such as GraphQL, gRPC, tRPC, and pub/sub as deferrals, so that this
    calibration pass does not become a broad protocol rewrite.
31. As a human developer, I want "which client function calls this route?" to
    pass the benchmark with compact evidence, so that I know Seam is useful for
    endpoint edits.
32. As a human developer, I want "can Seam represent routes here?" to return a
    true positive in route-enabled fixtures, so that capability checks match what
    I see in graph-search.
33. As a human developer, I want stale-index warnings to remain visible in
    protocol answers, so that route evidence is not trusted after source changes
    without a sync.
34. As a human developer, I want unsupported route states to remain honest in
    repos with no supported framework evidence, so that the calibration does not
    force false positives.
35. As a future implementation agent, I want a small implementation surface, so
    that I can fix the protocol roadmap signal without touching unrelated graph
    extractors.
36. As a future implementation agent, I want the PRD to say when to escalate to a
    new extractor PRD, so that missing wrapper patterns are not mixed with
    benchmark metadata fixes.

## Implementation Decisions

- Treat this as a calibration phase after the closed HTTP-call extraction work,
  not as a new extractor phase by default.
- Keep the existing graph model for route nodes and `http_calls` edges.
- Do not add new MCP tools, new graph tables, new edge kinds, new runtime
  probes, or new network behavior.
- Update the protocol route-node capability scenario from stale negative
  expectation to current positive capability expectation when the fixture has
  populated route nodes.
- Keep unsupported-honest capability scenarios for domains that are truly absent,
  but do not reuse that shape for route nodes in a route-enabled fixture.
- Split scenario semantics clearly:
  failure gaps come from non-passing scenarios with real missing evidence or poor
  answerability; roadmap pressure comes from intentionally deferred capabilities;
  regression coverage comes from passing scenarios that guard shipped behavior.
- Make output-cost failures explicit. If a scenario returns correct protocol
  facts and evidence but exceeds the token budget, classify the gap as output
  budget or presentation, not as missing protocol extraction.
- Add a protocol coherence check that compares route-related scenario
  expectations with the schema capability state from the maintained fixture.
- The coherence check should be deterministic and local-only. It should not call
  GitHub, fetch remote docs, execute app servers, or use an LLM judge.
- The coherence check should report actionable findings with scenario id,
  expected capability, actual capability, and suggested metadata or fixture fix.
- Keep route inventory and HTTP caller evidence separate in all wording. Route
  nodes answer "what endpoints exist"; HTTP-call edges answer "what local code
  calls those endpoints."
- Preserve supported-empty as a valid state. A repo with route nodes but no
  matched HTTP callers should not be considered broken.
- Preserve unsupported as a valid state. A repo with no supported route
  framework evidence should receive caveats and fallback guidance instead of
  invented route nodes.
- Preserve populated as the strongest state. When route nodes and HTTP-call
  edges are present, recipes and reports should surface them as usable evidence.
- Update answerability report recommendation logic only if necessary. The
  existing failure-gap separation should remain; the main change should be
  correcting stale scenario inputs and adding coherence protection.
- Update documentation so agents understand the intended route protocol workflow:
  check schema, use route-entrypoints for route inventory, use HTTP-callers for
  static caller evidence, use snippet/context for bounded source inspection, and
  sync when stale.
- Keep broader protocol domains out of scope. GraphQL, gRPC, tRPC, WebSocket,
  pub/sub, and queue/topic edges need separate RFCs after HTTP semantics remain
  stable.
- Add a clear escalation rule: if calibration reveals a genuinely missing
  supported static pattern, create a new extractor-quality issue with a minimal
  fixture and failing test rather than broadening this PRD.

## Testing Decisions

- Good tests should assert public behavior consumed by agents: benchmark result
  status, schema capability payloads, graph-search recipe output, architecture
  caveats, report recommendation, and coherence findings.
- Do not test private implementation details of route extraction unless a real
  extractor bug is discovered during this phase.
- Add or update answerability tests so the route-node capability scenario passes
  when route nodes are populated.
- Add or update answerability tests so a stale negative capability expectation is
  caught by the protocol coherence check.
- Add or update answerability tests so route-caller discovery passes when the
  output includes the caller symbol, route symbol, `http_calls` edge,
  `route_resolved:true`, and provenance.
- Add a test that distinguishes an output-budget partial from a protocol-edge
  failure gap, so correct but verbose route answers do not keep recommending
  extractor work.
- Add schema-capability tests for `has_route_nodes:true` and
  `has_http_calls:true` over a fixture with populated protocol evidence.
- Add graph-search recipe tests for route-entrypoints and HTTP-callers using the
  same fixture facts expected by the benchmark.
- Add architecture/report tests only if wording or summary behavior changes.
- Add docs-coherence tests if documentation records scenario counts, fixture
  hashes, route capability claims, or protocol command examples that can drift.
- Run the answerability report with coherence enabled as the primary acceptance
  check. The report should no longer recommend protocol-edge quality unless a
  real non-passing protocol scenario remains.
- Run the focused eval test suite and the full project gate before merging the
  implementation.

## Out of Scope

- Rebuilding HTTP-call extraction from scratch.
- Adding new route extractors for new frameworks.
- Adding GraphQL, gRPC, tRPC, WebSocket, pub/sub, queue, or topic protocol
  edges.
- Adding cross-repo protocol matching.
- Adding OpenAPI fetching, parsing, or remote schema discovery.
- Running local servers, probing localhost, or executing application code.
- Adding runtime traces.
- Changing default impact traversal policy for `http_calls`.
- Adding new graph tables or migrations.
- Adding new MCP tools.
- Adding new Explorer visuals.
- Adding installer, release, diagnostics, or doctor functionality.
- Mutating GitHub issues automatically.
- Reclassifying Kubernetes/Kustomize as next work.

## Further Notes

The pre-calibration next-step decision was:

- protocol-edge quality was the reported top failure gap;
- the closed HTTP extraction PRD had already delivered the core route and
  HTTP-call evidence needed for the fixture;
- the live route-entrypoint query found route nodes;
- the live HTTP-callers query found the account-status caller, route edge,
  `route_resolved:true`, and direct Python requests provenance;
- the remaining work was to correct benchmark expectations, report labels, and
  protocol presentation before building more extraction.

After this calibration, protocol scenarios are regression coverage for shipped
route and HTTP-call behavior rather than the next implementation mandate. If
protocol appears as a future failure gap, the next PRD should name the exact
missing pattern or presentation issue instead of using the broad
`protocol-edge quality` label.
