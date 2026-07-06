# PRD — Phase 11 RFC: Protocol Edges, HTTP Calls First

> Status: ready for review.
> Created: 2026-07-03.
> GitHub issue: https://github.com/Catafal/seam/issues/276.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/phase11-rfc-roadmap.md`.
> Parent status matrix: `phase11-codebase-memory-roadmap.md`.
> Superseded for implementation by `phase11-rfc-http-call-extraction-quality.md`
> and GitHub issue https://github.com/Catafal/seam/issues/395.

## Problem Statement

Seam can already index first-class route nodes and route metadata. It can show an
architecture route inventory, list route symbols in graph search, and expose route
capability through schema introspection. That is useful, but it does not yet solve the
highest-value protocol-boundary question: which code actually calls those routes?

From the user's perspective, this creates a misleading gap. The graph vocabulary includes
`http_calls`, the tool guidance points agents toward HTTP boundary discovery, and tests
prove a small TypeScript literal path. But the current Seam index has route nodes and no
`http_calls` edges. Agents can discover exposed HTTP surface area, but they cannot reliably
trace client-to-route coupling in the same repo.

The gap matters because HTTP boundaries are product boundaries. When an API route changes,
the caller may not be a normal function caller. It may be frontend code, a local client
module, an integration helper, or a test fixture using a URL literal. Without explicit
HTTP-call evidence, agents fall back to grep and manual inference. That is slower, less
structured, and easier to get wrong.

The problem is not that Seam needs every protocol. The first problem is narrower: make
HTTP call extraction trustworthy enough that `http_calls` becomes an honest, visible,
conservative graph surface.

## Solution

Add a scoped, conservative HTTP-call extraction and reporting layer on top of the shipped
route-node foundation.

The first implementation should focus on HTTP calls in Python, TypeScript, and JavaScript,
because those are already within Seam's route extraction path and match the most common
client/server shapes in the current product. The feature should extract HTTP-call evidence
from literal or near-literal call sites, normalize method/path information, and connect
callers to known route nodes when the match is reliable.

The solution should preserve Seam's current graph philosophy:

- no network calls;
- no runtime probing;
- no OpenAPI fetching;
- no execution of user code;
- no secret capture;
- no hidden writes outside normal indexing;
- false negatives over false positives;
- confidence and provenance visible wherever possible.

The intended user workflow is:

1. Run schema introspection and see whether route nodes and HTTP-call edges are populated.
2. Ask graph search for route nodes, outgoing HTTP callers, or incoming callers of routes.
3. Ask architecture for a route/protocol boundary briefing that distinguishes route inventory
   from actual caller evidence.
4. Chain any returned UID into context or snippet retrieval.
5. Use the results as conservative evidence, not as a claimed complete runtime trace.

This PRD covers the first RFC scope only: HTTP calls. It does not include gRPC, GraphQL,
tRPC, pub/sub, cross-repo protocol matching, runtime trace ingestion, or infra graph work.

## User Stories

1. As an AI coding agent, I want Seam to distinguish route inventory from route caller
   evidence, so that I do not assume a listed route has known clients.
2. As an AI coding agent, I want `http_calls` edges when a local symbol calls a literal
   HTTP route, so that I can trace client-to-server coupling through the graph.
3. As an AI coding agent, I want `http_calls` edges to include confidence, so that I know
   whether a match is direct, inferred, or ambiguous.
4. As an AI coding agent, I want `http_calls` edges to include provenance, so that I know
   which extractor family produced the evidence.
5. As an AI coding agent, I want schema introspection to report whether `http_calls` is
   populated, so that I can choose the right follow-up tool.
6. As an AI coding agent, I want schema guidance to say that missing `http_calls` means
   no static evidence was found, so that I do not confuse absence with complete safety.
7. As an AI coding agent, I want graph search to return symbols with outgoing HTTP calls,
   so that I can find local client code.
8. As an AI coding agent, I want graph search to return routes with incoming HTTP calls,
   so that I can find who calls an endpoint.
9. As an AI coding agent, I want graph-search previews to show HTTP-call edges, so that
   I can move from a caller to a route without another broad query.
10. As an AI coding agent, I want route architecture output to show whether caller evidence
    exists, so that an architecture briefing identifies real protocol links.
11. As an AI coding agent, I want route architecture output to avoid stale warnings, so that
    warnings do not contradict populated evidence.
12. As an AI coding agent, I want direct `fetch("/path")` calls indexed, so that common
    frontend code links to known routes.
13. As an AI coding agent, I want `fetch("/path", { method: "POST" })` calls indexed, so
    that method-specific route matches are correct.
14. As an AI coding agent, I want `axios.get("/path")` and `axios.post("/path")` calls
    indexed, so that common client-library usage is visible.
15. As an AI coding agent, I want `axios({ url: "/path", method: "POST" })` indexed, so
    that config-object style calls are visible.
16. As an AI coding agent, I want Python `requests.get("/path")` and equivalent literal
    calls indexed, so that backend integration clients are visible.
17. As an AI coding agent, I want Python HTTPX literal calls indexed, so that async and
    sync Python clients are visible.
18. As an AI coding agent, I want local client instances with clear HTTP methods indexed
    when method and path are literal, so that instance-based HTTP clients are not missed.
19. As an AI coding agent, I want query strings stripped or normalized safely, so that
    `/users?id=1` can match the `/users` route without storing request parameters.
20. As an AI coding agent, I want URL fragments ignored, so that they do not create fake
    route identities.
21. As an AI coding agent, I want same-origin absolute URLs normalized when safe, so that
    `http://localhost:8000/api/users` can match `/api/users` when the path is literal.
22. As an AI coding agent, I want external absolute URLs represented as unresolved external
    calls or skipped, so that Seam does not invent local route nodes for third-party APIs.
23. As an AI coding agent, I want path parameters normalized consistently, so that
    `/users/123` does not incorrectly become a route template unless the matching rule is
    explicitly designed and conservative.
24. As an AI coding agent, I want dynamic URL construction skipped by default, so that
    speculative concatenation does not pollute the graph.
25. As an AI coding agent, I want simple constant aliases considered only when they are
    local, literal, and safe, so that useful wrappers can be captured without arbitrary
    data-flow solving.
26. As an AI coding agent, I want base URL constants treated carefully, so that service
    boundaries are not guessed from environment-dependent config.
27. As an AI coding agent, I want unresolved HTTP calls counted separately from matched
    route calls if they are exposed, so that external dependencies can be audited without
    implying internal route coupling.
28. As an AI coding agent, I want route caller evidence to be scoped by path filters, so
    that I can inspect one subsystem without reading the whole graph.
29. As an AI coding agent, I want route caller evidence to be bounded by limit and preview
    limits, so that MCP responses stay compact.
30. As an AI coding agent, I want HTTP-call extraction to work without embeddings, so that
    protocol edges remain deterministic and local.
31. As an AI coding agent, I want HTTP-call extraction to be independent of semantic search,
    so that semantic availability does not change graph facts.
32. As an AI coding agent, I want HTTP-call edges to avoid default production-impact noise
    until Seam explicitly decides otherwise, so that risk reports remain stable.
33. As a human developer, I want a terminal command to list callers of local routes, so that
    I can assess API changes quickly.
34. As a human developer, I want route architecture output to summarize top called routes,
    so that I can see important API surfaces.
35. As a human developer, I want route architecture output to summarize top HTTP callers,
    so that I can see client modules with broad API coupling.
36. As a human developer, I want `http_calls` results to show file and line, so that I can
    jump directly to the call site.
37. As a human developer, I want `http_calls` results to preserve method and normalized path,
    so that I can verify a route match quickly.
38. As a Seam Explorer user, I want route nodes with incoming HTTP-call edges visible, so
    that product boundaries appear in the graph UI.
39. As a Seam Explorer user, I want HTTP-call edges filterable separately from normal calls,
    so that protocol boundaries are visually distinct.
40. As a Seam Explorer user, I want route details to show caller count, so that I can
    prioritize routes by observed local use.
41. As a Seam maintainer, I want HTTP-call extraction isolated behind a deep module interface,
    so that behavior can be tested without transport code.
42. As a Seam maintainer, I want path and method normalization centralized, so that every
    extractor uses the same matching contract.
43. As a Seam maintainer, I want all new extraction to be additive, so that existing route
    nodes, route metadata, and graph search behavior do not regress.
44. As a Seam maintainer, I want no schema migration unless the design proves edge rows are
    insufficient, so that the first implementation stays small.
45. As a Seam maintainer, I want any optional call-site metadata table justified separately,
    so that the graph does not gain storage complexity prematurely.
46. As a Seam maintainer, I want parser failures to skip one file rather than abort indexing,
    so that protocol extraction follows existing indexer resilience.
47. As a Seam maintainer, I want old indexes to degrade gracefully, so that schema, graph
    search, architecture, and Explorer do not crash when `http_calls` is absent.
48. As a Seam maintainer, I want regression tests for dynamic URL non-extraction, so that
    future changes do not overfit by guessing.
49. As a Seam maintainer, I want regression tests for no route match, so that external HTTP
    calls do not become fake internal edges.
50. As a Seam maintainer, I want docs and API contracts updated, so that agents learn the
    precise trust level of HTTP-call evidence.
51. As a future infra graph implementer, I want HTTP protocol matching rules established
    first, so that service-binding edges can reuse confidence and provenance conventions.
52. As a future cross-repo implementer, I want single-repo HTTP matching stable first, so
    that cross-repo matching does not compound uncertainty.
53. As a future graph-artifact implementer, I want protocol edges to be explicit graph facts,
    so that exported artifacts can include them with clear schema semantics.
54. As a future runtime-trace implementer, I want static HTTP-call evidence separated from
    runtime evidence, so that trace data can be compared rather than conflated.
55. As a future protocol-family implementer, I want HTTP extraction to set the standard for
    gRPC, GraphQL, tRPC, and event edges.

## Implementation Decisions

- Treat this RFC as an improvement to shipped route-node work, not as a replacement for it.
- Keep `http_calls` as the primary graph representation for matched local route calls.
- Do not add a new MCP tool for the first implementation. Reuse schema, graph search,
  architecture, context, snippet, and Explorer surfaces first.
- Keep the implementation local and static. It must not fetch OpenAPI documents, contact
  servers, run applications, import user modules, or execute build tools.
- Keep extraction conservative. If the method or path cannot be known from local static
  evidence, skip the matched edge.
- Centralize HTTP method and path normalization in a small deep module used by language-
  specific extractors.
- Normalize route paths and call paths through the same route-template rules before matching.
- Strip query strings and fragments from call-site paths before matching.
- Preserve leading slash behavior by normalizing relative-looking paths into route-shaped
  paths only when that is already consistent with route-node naming.
- Match HTTP method as part of the route identity. A `POST` caller must not link to a `GET`
  route unless the route metadata explicitly represents that method.
- Represent direct literal calls as high-confidence extracted evidence.
- Represent simple local constant or wrapper-derived calls only if they remain bounded and
  explainable; otherwise skip them.
- Treat dynamic string concatenation, template interpolation with variables, URL builders,
  and runtime config as out of scope for matched internal route edges.
- Treat external absolute URLs as external or unresolved evidence only if the implementation
  adds a clearly separate representation. They must not create local route nodes.
- Prefer no schema migration for the first slice. Existing edge fields can carry source,
  target, kind, line, confidence, receiver, and synthesis provenance.
- If the existing edge row cannot carry enough call-site detail, introduce a separate RFC
  decision for optional HTTP call-site metadata rather than expanding scope silently.
- Keep edge endpoints name-keyed to preserve Seam's independent per-file re-indexing model.
- Do not switch route or edge identity to row IDs in this RFC.
- Continue using route symbols as graph targets so existing graph search, context, snippet,
  and Explorer behavior can work without a new graph model.
- Ensure `http_calls` does not inflate default production impact until a separate impact
  policy decision is made.
- Make graph search the canonical way to inspect `http_calls` in the first implementation.
- Extend architecture only enough to explain HTTP caller evidence and next calls. It should
  not become a full API documentation generator.
- Schema introspection should distinguish route support, route population, and HTTP-call
  edge population.
- Architecture warnings should not claim unsupported route or HTTP-call evidence when the
  schema supports it; warnings must be tied to actual capability and evidence.
- Provenance names should be stable and specific enough to identify the extractor family,
  such as JavaScript fetch literal, TypeScript axios literal, Python requests literal, or
  Python HTTPX literal.
- Extractor provenance should be product-facing enough for agents to interpret, not merely
  internal function names.
- HTTP-call extraction should happen during normal indexing and sync, not on query paths.
- Per-file re-indexing should replace that file's old HTTP-call edges atomically, consistent
  with existing file upsert behavior.
- Whole-index matching should not require rewriting unrelated files unless a later design
  introduces unresolved-to-resolved promotion.
- If call extraction produces an edge to a route that is not currently indexed, the first
  implementation should avoid counting it as a matched internal call. External/unresolved
  inventory is a separate decision.
- Existing route-node tests remain valid and should not be rewritten as part of this RFC.
- The design should leave clear extension points for later protocol families without
  implementing those families.

## Testing Decisions

- Tests should assert externally visible behavior: indexed symbols, edge rows, schema
  capability output, graph-search results, architecture summaries, and CLI/Web/MCP parity
  where relevant.
- Tests should not assert private AST traversal details.
- The core normalization and matching module should have focused unit tests because it is
  the deep module that prevents drift across languages.
- Route matching tests should cover exact method/path matches.
- Route matching tests should cover query-string stripping.
- Route matching tests should cover fragment stripping.
- Route matching tests should cover path parameter normalization where the design supports
  it.
- Route matching tests should cover method mismatch as a non-match.
- Route matching tests should cover unknown routes as non-matched internal calls.
- TypeScript/JavaScript extraction tests should cover direct `fetch` with default `GET`.
- TypeScript/JavaScript extraction tests should cover `fetch` with literal method options.
- TypeScript/JavaScript extraction tests should cover direct axios method calls.
- TypeScript/JavaScript extraction tests should cover axios config-object calls.
- TypeScript/JavaScript extraction tests should cover dynamic `fetch(url)` as skipped.
- TypeScript/JavaScript extraction tests should cover template strings with interpolation
  as skipped.
- Python extraction tests should cover requests literal method calls.
- Python extraction tests should cover HTTPX literal method calls.
- Python extraction tests should cover dynamic path variables as skipped.
- Python extraction tests should cover external absolute URLs as non-matched internal calls.
- Indexer tests should verify that HTTP-call edges are persisted with the expected kind,
  confidence, line, and provenance.
- Graph-search tests should verify outgoing HTTP callers.
- Graph-search tests should verify incoming route callers.
- Graph-search tests should verify previews for `http_calls`.
- Graph-search tests should verify invalid edge-kind behavior remains unchanged.
- Schema tests should verify `has_http_calls` false when routes exist but no HTTP-call edges
  exist.
- Schema tests should verify `has_http_calls` true when at least one HTTP-call edge exists.
- Architecture tests should verify route inventory remains populated independently from
  caller evidence.
- Architecture tests should verify HTTP-call evidence appears in the selected protocol or
  route section once populated.
- Architecture tests should verify warnings do not contradict populated route or HTTP-call
  evidence.
- CLI tests should verify JSON envelope shape for graph-search and architecture examples.
- Web/API tests should be added only if route/protocol data is newly exposed through a web
  endpoint beyond existing graph-search/architecture payloads.
- Explorer tests should focus on visible filtering/rendering behavior only if UI code changes.
- Regression tests should run existing route-node, schema, graph-search, and architecture
  suites because those are the surfaces this RFC touches.

## Out of Scope

- gRPC extraction.
- GraphQL operation/resolver matching.
- tRPC router/client matching.
- WebSocket channel edges.
- Pub/sub, queue, topic, event-emitter, or message-bus protocol edges.
- Cross-repo route matching.
- Runtime trace ingestion.
- OpenAPI fetching or parsing.
- Server probing.
- Executing app code to discover routes.
- Full URL data-flow solving.
- Arbitrary string-constraint solving.
- Inferring route matches from environment variables.
- Secret value extraction.
- Full API documentation generation.
- New default impact traversal semantics for `http_calls`.
- Changing edge endpoints from names to row IDs.
- Adding a broad query language or Cypher.
- Infra graph nodes.
- Graph artifact export/import.
- Installer or distribution work.

## Further Notes

Current audit findings:

- Seam's current fresh schema reports route-table support and route nodes.
- Seam's current fresh schema reports zero `http_calls` edges.
- The existing route extractor already owns conservative route declarations and some
  literal client-call extraction.
- The indexing pipeline already appends route symbols and route edges before atomic file
  upsert.
- Graph search already accepts `route` as a symbol kind and `http_calls` as an edge kind.
- Architecture already reports route metadata as a populated section.
- The most important missing behavior is not route inventory; it is trustworthy, broader,
  product-visible caller evidence.

Recommended first implementation slice:

1. Define the normalization and matching contract.
2. Improve TypeScript/JavaScript literal extraction around existing fetch and axios support.
3. Add Python requests and HTTPX literal extraction.
4. Update schema and architecture wording so route inventory and caller evidence are clearly
   separate.
5. Add tests across extractor, indexer, graph-search, schema, and architecture surfaces.

Recommended follow-up after this PRD:

1. Write the infra graph RFC for Docker Compose and Dockerfile.
2. Write the graph artifact export/import RFC.
3. Defer cross-repo analysis until single-repo protocol matching is proven stable.
