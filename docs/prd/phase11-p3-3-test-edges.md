# PRD: Phase 11 P3.3 — Test Edges

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P3.3.
> Competitive source: `DeusData/codebase-memory-mcp` treats test coverage and graph exploration as
> first-class codebase memory signals.
> Status: ready-for-agent.
> GitHub issue: https://github.com/Catafal/seam/issues/163.
> Tracker label: `ready-for-agent`.
> Schema target: additive graph vocabulary. Test files continue to be classified by the existing
> test-path logic, while `tests` edges materialize high-confidence relationships from test symbols
> to production symbols.

## Problem Statement

Seam can already estimate affected tests by traversing reverse impact and filtering dependents whose
files look like tests. That is useful, but test relationships are still implicit. Agents cannot ask
the graph directly "which tests exercise this symbol?", Explorer cannot overlay test coverage, and
architecture summaries cannot identify high-risk production areas with weak or heavy test coverage.

From the user's perspective, this creates repeated friction:

- `seam_affected` works as a workflow command, but the underlying test relationship is not visible as
  reusable graph evidence;
- test-to-source links are inferred at query time through generic `call`/`import` traversal rather
  than stored as a typed relationship;
- agents cannot use `seam_graph_search --edge-kind tests` to inspect coverage-like evidence;
- `seam_context` can show callers and callees, but it does not distinguish production callers from
  test coverage evidence;
- `seam_architecture` cannot summarize which modules have many tests, no obvious tests, or unusually
  broad test fan-in;
- Explorer cannot filter or color test relationships independently from production dependency edges;
- impact traversal can overstate production blast radius if test edges are mixed into default risk
  paths without guardrails;
- changed test files are handled directly by `seam_affected`, but changed production files still do
  not have explicit stored test evidence;
- future pipeline/performance work needs real graph pressure from route, config, exception, and test
  edges before formal pass refactors are worth doing.

Seam needs conservative, typed `tests` edges so test relationships become normal local code graph
evidence while preserving the current `seam_affected` contract.

## Solution

Add first-class `tests` graph edges from test symbols to production symbols when static evidence is
visible.

The first version should materialize `tests` edges from:

- direct call evidence where a symbol defined in a test file calls a production symbol;
- direct import evidence where a test file imports a production symbol, class, module, or helper;
- test file-name proximity where a test file strongly maps to a production file, such as
  `tests/test_parser.py` to `parser.py`, only when the mapping is deterministic;
- class/function naming proximity such as `TestParser` or `test_parse_config` to `Parser` or
  `parse_config`, only when the target candidate is unique in the index;
- language-neutral relationships already available in the graph, while keeping Python and
  TypeScript/JavaScript as the first explicit validation targets.

The graph should expose:

- `tests` as a typed edge kind with confidence and provenance;
- source test symbol, target production symbol, source file, and line where available;
- schema capability flags and counts so agents know whether the current index has test edges;
- `seam_graph_search --edge-kind tests` support for structural discovery;
- `seam_context` support that can surface incoming test edges without confusing them with
  production callers;
- `seam_architecture` support that summarizes tested modules, untested-looking hotspots, and heavy
  test fan-in;
- Explorer API/UI support so test edges can be filtered and visually distinguished;
- `seam_affected` compatibility, with the existing output remaining byte-compatible unless an
  explicit enhancement flag or internal fallback uses the new edge data without changing shape.

The feature should be intentionally conservative. A missing `tests` edge is acceptable when static
evidence is weak. A false `tests` edge is harmful because agents may skip necessary tests or
misunderstand coverage. `tests` edges are evidence of test relationship, not proof of semantic
coverage or assertion quality.

The intended workflow becomes:

1. Call `seam_schema` and see whether the index supports `tests` edges.
2. Call `seam_graph_search --edge-kind tests --preview` to inspect test-to-source evidence.
3. Call `seam_context <symbol>` and see tests that exercise that symbol when edges exist.
4. Call `seam_architecture --section tests` to identify covered, uncovered-looking, and heavily
   tested areas.
5. Call `seam_affected <changed-file>` and get the same output shape as before, with future room to
   use materialized edges internally for speed and precision.
6. Use Explorer to toggle test edges independently from production dependency edges.

## User Stories

1. As an AI coding agent, I want `tests` edges in the Seam graph, so that I can inspect test
   relationships without deriving them manually from generic call edges.
2. As an AI coding agent, I want test edges to be typed separately from production calls, so that
   impact and architecture views can treat them differently.
3. As an AI coding agent, I want `seam_schema` to report test-edge support, so that I know whether
   the current index was built with P3.3 support.
4. As an AI coding agent, I want schema counts for `tests` edges, so that I can tell whether a repo
   has populated test evidence.
5. As an AI coding agent, I want `seam_graph_search --edge-kind tests` to work, so that I can list
   test relationships through the same structural surface as calls, imports, routes, config, and
   exception edges.
6. As an AI coding agent, I want graph search previews for test edges, so that I can inspect evidence
   before expanding into context.
7. As an AI coding agent, I want test edges to preserve confidence, so that exact call/import evidence
   is distinguishable from file-name proximity.
8. As an AI coding agent, I want test edges to preserve provenance, so that I can tell why Seam thinks
   a test relates to a production symbol.
9. As an AI coding agent, I want tests that call a production function to link to that function, so
   that direct unit-test evidence is visible.
10. As an AI coding agent, I want tests that instantiate a production class to link to that class, so
    that class-level test relationships are visible.
11. As an AI coding agent, I want tests that import a production symbol to link when the imported
    symbol is unique and resolved, so that tests that assert through fixtures or indirect calls are
    not missed.
12. As an AI coding agent, I want ambiguous imports to be skipped or marked lower confidence, so that
    test evidence does not overclaim precision.
13. As an AI coding agent, I want file-name proximity to be used only when deterministic, so that
    `test_parser.py` can link to `parser.py` without inventing weak relationships.
14. As an AI coding agent, I want proximity edges to be clearly tagged as heuristic, so that they are
    not mistaken for observed execution evidence.
15. As an AI coding agent, I want generated, fixture-only, and helper-only test files to avoid noisy
    edges when no production target is visible, so that graph quality stays high.
16. As an AI coding agent, I want `seam_context <production-symbol>` to show tests that target the
    symbol, so that I can pick relevant tests before editing.
17. As an AI coding agent, I want `seam_context <test-symbol>` to show production symbols it tests,
    so that I can understand what a test covers.
18. As an AI coding agent, I want context output to label test edges separately from normal callers
    and callees, so that production dependency reasoning remains clear.
19. As an AI coding agent, I want `seam_architecture` to summarize tested and untested-looking
    hotspots, so that I can spot risky areas before changing them.
20. As an AI coding agent, I want architecture summaries to explain when test edges are unsupported or
    unpopulated, so that absence is not confused with "there are no tests."
21. As an AI coding agent, I want Explorer to filter test edges independently, so that I can overlay
    test coverage without cluttering production dependency views.
22. As an AI coding agent, I want Explorer to visually distinguish test edges from call/import/config
    edges, so that the graph remains scannable.
23. As an AI coding agent, I want `seam_affected` output to remain compatible, so that existing
    scripts and agent workflows do not break.
24. As an AI coding agent, I want `seam_affected` to be allowed to use test edges internally later, so
    that affected-test precision can improve without changing the public result shape.
25. As an AI coding agent, I want production impact traversal to exclude test edges by default, so
    that risk scores are not inflated by test-only dependencies.
26. As an AI coding agent, I want explicit options or separate surfaces for test impact, so that test
    relationships are available when I ask for them.
27. As a maintainer, I want test-edge extraction to reuse the existing test-path classifier, so that
    there is one definition of "test file."
28. As a maintainer, I want test-edge extraction to reuse resolved graph evidence where possible, so
    that the implementation does not duplicate import/call resolution logic.
29. As a maintainer, I want a small deep module for test-edge materialization, so that it can be
    tested in isolation with tiny synthetic symbol graphs.
30. As a maintainer, I want parser failures in test-edge extraction to degrade per file, so that one
    unusual test file does not abort indexing.
31. As a maintainer, I want old indexes to degrade gracefully, so that read paths do not crash when
    test edges are absent.
32. As a maintainer, I want no secret or runtime value extraction in this feature, so that test-edge
    work stays separate from config/resource indexing.
33. As a maintainer, I want no mandatory semantic embeddings for test edges, so that the feature works
    in the default local index.
34. As a maintainer, I want deterministic ordering of test-edge results, so that tests and agent
    outputs are stable.
35. As a maintainer, I want duplicate evidence to collapse into one best edge or clearly reported
    evidence count, so that repeated imports/calls do not flood the graph.
36. As a maintainer, I want confidence thresholds documented, so that future languages can extend the
    extractor consistently.
37. As a maintainer, I want P3.3 to avoid changing route, config, or exception semantics, so that each
    Phase 11 graph family remains independently reviewable.
38. As a maintainer, I want P3.3 to establish enough graph pressure for P4 pipeline/performance work,
    so that the later pass-model refactor is grounded in real indexed relationships.
39. As a CLI user, I want `seam graph-search --edge-kind tests --json` to return test relationships,
    so that I can script relevant-test discovery.
40. As a CLI user, I want `seam schema --json` to include `has_test_edges`, so that scripts can branch
    on index support.
41. As an MCP client, I want the MCP tool descriptions to mention test-edge discovery, so that agents
    choose the right Seam tool.
42. As a Web API client, I want schema and graph endpoints to include test-edge vocabulary, so that UI
    clients stay type-aligned.
43. As a docs reader, I want examples for test-edge discovery, so that I can understand how P3.3
    differs from `seam_affected`.
44. As a docs reader, I want an explicit warning that test edges are not coverage proof, so that I do
    not confuse static evidence with runtime coverage measurement.

## Implementation Decisions

- Add `tests` to the graph edge vocabulary as an additive edge kind. Use lower-case `tests` in APIs
  to match existing edge-kind naming.
- Do not add a dedicated test-node kind. Tests are already normal symbols in test files; the new
  information is the relationship from test symbol to production symbol.
- Reuse the existing test-file classifier as the only source of truth for identifying test files.
- Add a deep, isolated test-edge materialization module that accepts indexed symbols, files, and
  existing edges/import evidence and returns normalized graph edges.
- Treat direct test-symbol call/instantiation/import evidence as the highest-value first pass.
- Treat deterministic file-name or symbol-name proximity as optional heuristic evidence. It must be
  confidence-tagged and skipped when multiple candidates match.
- Store test edges in the existing graph edge table unless implementation discovers that richer
  evidence requires a separate metadata table. The default target is no schema migration beyond edge
  vocabulary/read-path capability updates.
- If a metadata table is added, keep it additive and optional. Old indexes must still open and report
  missing test-edge capability rather than failing.
- Deduplicate repeated evidence for the same source/target/kind pair. Prefer the strongest confidence
  and most concrete provenance.
- Do not infer semantic coverage, assertions, branches, fixtures, mocks, or runtime execution. This
  PRD models static relationship evidence only.
- Exclude `tests` edges from default production impact traversal. They may appear in explicit graph
  search, context, Explorer, architecture test sections, and affected-test workflows.
- Keep `seam_affected` byte-compatible by default. If it uses test edges internally, output keys,
  path format, sorting, and error behavior must remain unchanged.
- Update schema introspection with `has_test_edges`, counts, edge vocabulary, and recommended next
  calls.
- Update structural graph search to accept and return `tests` edges.
- Update context surfaces to expose test relationships without merging them into production callers
  and callees in a misleading way.
- Add or update an architecture `tests` section that reports support status, edge counts, top tested
  production symbols, test-heavy modules, untested-looking hotspots, truncation, and recommended next
  calls.
- Update Web API schemas and Explorer filters so UI consumers can toggle and style `tests` edges.
- Keep all extraction local and deterministic. No network access, no coverage tool invocation, and no
  test execution are part of indexing.
- Preserve the existing Phase 11 pattern: conservative extraction, explicit confidence/provenance,
  old-index compatibility, CLI/MCP/Web parity, and documentation updates.

## Testing Decisions

- Tests should verify external behavior and public contracts, not implementation internals. A good
  test asks "does the indexed graph expose the right typed relationship?" rather than "did this helper
  call another helper?"
- Unit tests should cover the isolated test-edge materializer with tiny synthetic graphs:
  direct call evidence, instantiation evidence, import evidence, deterministic file-name proximity,
  ambiguous proximity skipped, duplicate evidence collapsed, and non-test source files ignored.
- Integration tests should build small fixture indexes and assert that `seam_schema` reports
  test-edge capability and counts.
- Graph-search tests should assert `edge_kind=tests` returns deterministic preview rows and rejects
  nothing that the documented vocabulary allows.
- Context tests should assert production symbols can surface incoming test relationships and test
  symbols can surface outgoing production relationships without disrupting existing callers/callees.
- Architecture tests should assert the tests section reports unsupported/empty/populated states
  correctly and includes useful next calls.
- Affected-tests tests should assert existing `seam_affected` handler and CLI output remains
  compatible for seeded source/test graphs.
- Impact tests should assert default production impact excludes `tests` edges, while explicit graph
  search can still discover them.
- Web API and TypeScript contract tests should assert schema/graph responses include the new
  vocabulary and Explorer filter state.
- Regression tests should cover stale or old-index behavior where no test edges exist.
- Prior art includes existing affected-handler tests, graph-search tests, schema-tool tests,
  architecture-tool tests, web API integration tests, and the Phase 11 route/config/exception edge
  extractor tests.

## Out of Scope

- Runtime coverage collection.
- Running pytest, Vitest, Jest, Playwright, or any other test runner during indexing.
- Parsing coverage XML/LCOV reports.
- Claiming assertion quality or branch/path coverage.
- Dynamic fixture dependency analysis.
- Mock/patch target extraction beyond simple static import/call evidence.
- Cross-repository test mapping.
- Full semantic similarity between test names and source names.
- Changing the default `seam_affected` output shape.
- Including test edges in production impact traversal by default.
- Route, config/resource, or exception-edge changes except for keeping shared vocabulary/docs
  consistent.
- P4 pipeline/performance refactors. P3.3 should create the graph evidence that makes P4 worth doing,
  not perform the P4 refactor itself.

## Further Notes

- This is the missing P3 graph-model expansion after route nodes/HTTP calls, config/resource links,
  and exception edges.
- The feature should be built as conservative graph evidence, not a coverage system.
- The strongest first implementation is likely: reuse existing indexed `call`, `instantiates`, and
  resolved import evidence where source file is a test file and target file is not a test file; add
  deterministic proximity only after exact evidence is stable.
- Test edges should help agents decide what to run and inspect, but they must not be treated as proof
  that a symbol is fully tested.
