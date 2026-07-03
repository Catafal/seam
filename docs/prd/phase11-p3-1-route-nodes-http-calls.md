# PRD: Phase 11 P3.1 — Route Nodes And `HTTP_CALLS`

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P3.1.
> Competitive source: `DeusData/codebase-memory-mcp` surfaces HTTP routes and cross-service links
> as first-class graph evidence.
> Status: ready-for-agent.
> GitHub issue: https://github.com/Catafal/seam/issues/141.
> Tracker label: `ready-for-agent`.
> Schema target: additive migration. Route nodes become first-class symbols, with route
> metadata stored separately and `http_calls` edges added to the graph vocabulary.

## Problem Statement

Seam can already tell an agent what symbols exist, what calls what, which symbols are entry
points, and how the repository is organized. It cannot yet answer one of the highest-value
application architecture questions directly: "what HTTP surface does this code expose, and
which code calls across that surface?"

From the user's perspective, this creates repeated friction:

- web routes are only implicit in decorators, file names, and framework conventions;
- `seam_architecture` has a route section, but it currently reports routes as unsupported;
- `seam_graph_search` rejects `kind=route` and `edge_kind=http_calls`, so agents cannot ask
  typed route questions;
- Explorer can show call/import/field/inheritance edges, but it cannot show API boundaries;
- frontend-to-backend navigation still requires grep for URL literals, manual route matching,
  and source reading;
- agents can over-focus on internal fan-in/fan-out while missing the true product boundary:
  HTTP endpoints and visible client calls;
- future 3D and architecture views need route data as graph evidence, not as one-off UI
  inference.

Seam needs conservative route extraction and HTTP-call relationships so routes become a
normal part of the local code graph.

## Solution

Add first-class route nodes and `http_calls` edges for the common Python and
TypeScript/JavaScript cases where static evidence is visible.

The feature should index:

- FastAPI route decorators such as `@app.get(...)`, `@router.post(...)`, and
  `@router.api_route(...)`;
- Flask route decorators such as `@app.route(...)` and `@blueprint.route(...)`;
- Express and router registrations such as `app.get(...)`, `router.post(...)`,
  `server.use(...)` when a literal path and handler are visible;
- frontend/client HTTP calls where URL literals are visible, including common `fetch(...)`,
  `axios.get(...)`, `axios.post(...)`, and simple wrapper-call patterns when the wrapper
  path argument is a literal.

The graph should expose:

- route nodes as symbol rows with `kind='route'`;
- route metadata as a dedicated read model: HTTP method, path template, normalized path,
  handler symbol, source framework, declaration file/line, confidence, and provenance;
- `call` or equivalent existing edges from the route node to its handler when the handler is
  known;
- `http_calls` edges from client/requesting symbols to matching route nodes when the method
  and path can be matched conservatively;
- warnings and unsupported-state behavior when route extraction data is absent or old indexes
  have not been rebuilt.

The first version is deliberately conservative. It should prefer missing a dynamic route over
inventing one. Dynamic URL construction, framework-specific router prefix composition, and
cross-repo service discovery can be added later behind explicit provenance and confidence.

The intended workflow becomes:

1. Call `seam_schema` and see `route` symbols and `http_calls` edges when the index supports
   them.
2. Call `seam_architecture --section routes` and get top route surfaces instead of an
   unsupported placeholder.
3. Call `seam_graph_search --kind route` to list route nodes.
4. Call `seam_graph_search --edge-kind http_calls --preview` to find client/server links.
5. Use `seam_context`, `seam_snippet`, `seam_impact`, and Explorer on returned UIDs exactly
   like other graph nodes.

## User Stories

1. As an AI coding agent, I want route nodes in the Seam graph, so that I can inspect API
   surface area without grepping decorators.
2. As an AI coding agent, I want route nodes to have stable UIDs, so that I can chain from
   route discovery into snippets and context.
3. As an AI coding agent, I want `kind=route` to work in graph search, so that routes are
   discoverable through the same typed search surface as functions and classes.
4. As an AI coding agent, I want `edge_kind=http_calls` to work in graph search, so that I can
   discover client/server relationships without arbitrary Cypher.
5. As an AI coding agent, I want FastAPI `@app.get` and `@router.post` decorators extracted,
   so that common Python APIs become visible in the graph.
6. As an AI coding agent, I want Flask `@app.route` decorators extracted, so that Flask apps
   expose their URL surface in architecture summaries.
7. As an AI coding agent, I want Express `app.get` and `router.post` registrations extracted,
   so that TypeScript/JavaScript API routes are visible.
8. As an AI coding agent, I want HTTP method and path stored separately, so that I can filter
   and reason about routes without parsing display names.
9. As an AI coding agent, I want route nodes to link to handler symbols, so that I can move
   from `GET /users` to the implementation body.
10. As an AI coding agent, I want frontend `fetch('/api/users')` calls linked to matching
    route nodes when the match is static, so that I can understand frontend/backend coupling.
11. As an AI coding agent, I want `axios.post('/api/orders')` calls linked to matching route
    nodes, so that common client libraries are represented.
12. As an AI coding agent, I want unsupported dynamic URLs to be skipped or marked heuristic,
    so that I do not over-trust inferred API links.
13. As an AI coding agent, I want confidence on route evidence, so that literal static routes
    are distinguishable from heuristic wrapper matches.
14. As an AI coding agent, I want provenance on route evidence, so that I can tell whether a
    route came from a Python decorator, an Express call, or a client URL literal.
15. As an AI coding agent, I want route extraction to be local and read-only at query time, so
    that route questions do not trigger source scanning or network calls.
16. As an AI coding agent, I want schema introspection to report route capability, so that I
    know whether the current index was built with route support.
17. As an AI coding agent, I want architecture summaries to include routes once indexed, so
    that repo briefings identify API boundaries.
18. As an AI coding agent, I want architecture warnings to say when routes are unsupported or
    unpopulated, so that absence is not confused with "this app has no routes."
19. As an AI coding agent, I want route results to include file and line, so that I can jump
    to the declaration quickly.
20. As an AI coding agent, I want route results to include handler name and UID when known, so
    that I can inspect implementation without another search.
21. As an AI coding agent, I want route path normalization, so that `/users/:id`, `/users/{id}`,
    and framework variants can be matched conservatively where safe.
22. As an AI coding agent, I want path-template matching to avoid matching unrelated paths, so
    that `GET /users` does not incorrectly link to `/users/settings`.
23. As an AI coding agent, I want HTTP method matching, so that `POST /orders` does not link to
    `GET /orders` unless the route accepts all methods.
24. As an AI coding agent, I want route nodes to participate in Explorer, so that I can see API
    boundaries in the graph canvas.
25. As an AI coding agent, I want route edges to be colorable/filterable by edge kind, so that
    HTTP boundaries stand apart from normal function calls.
26. As an AI coding agent, I want route extraction to preserve existing call/import/field edge
    behavior, so that adding routes does not regress existing graph tools.
27. As an AI coding agent, I want route extraction to work when embeddings are absent, so that
    HTTP graph support remains local and deterministic.
28. As an AI coding agent, I want older indexes to degrade gracefully, so that opening a pre-route
    DB does not crash schema, graph search, architecture, or Explorer.
29. As a human developer, I want `seam graph-search --kind route`, so that I can list API
    endpoints from the terminal.
30. As a human developer, I want `seam architecture --section routes`, so that I can get a
    compact route overview for a repo or subsystem.
31. As a human developer, I want route search output to show method and path, so that it reads
    like an API inventory.
32. As a human developer, I want route handler snippets to work through normal UID-based
    retrieval, so that I can inspect the implementation behind an endpoint.
33. As a human developer, I want frontend HTTP calls linked to backend routes when safe, so
    that I can trace user-facing flows across the stack.
34. As a Seam Explorer user, I want route nodes in the graph, so that API boundaries are visible
    in the UI.
35. As a Seam Explorer user, I want route filters, so that I can focus the graph on exposed
    HTTP surfaces.
36. As a Seam Explorer user, I want route details in the detail panel, so that method, path,
    framework, handler, and confidence are visible without reading source.
37. As a Seam maintainer, I want route extraction in a deep module, so that extractor behavior
    can be tested without transport code.
38. As a Seam maintainer, I want route metadata stored in an additive table, so that future route
    fields do not overload symbol names.
39. As a Seam maintainer, I want route symbols stored in the normal symbols table, so that
    existing search, snippet, context, graph search, and Explorer surfaces can reuse them.
40. As a Seam maintainer, I want migration tests for the new schema, so that old databases
    continue to open safely.
41. As a Seam maintainer, I want parser tests for Python route decorators, so that framework
    patterns are covered by behavior rather than incidental snapshots.
42. As a Seam maintainer, I want parser tests for TypeScript/JavaScript route registrations, so
    that Express/router support is explicit.
43. As a Seam maintainer, I want matcher tests for HTTP call literals, so that path/method
    matching is deterministic and conservative.
44. As a Seam maintainer, I want graph-search tests for `kind=route` and `edge_kind=http_calls`,
    so that typed discovery works end to end.
45. As a Seam maintainer, I want architecture tests for the routes section, so that the current
    unsupported placeholder is replaced only when real route data exists.
46. As a Seam maintainer, I want web API and generated TypeScript types updated, so that Explorer
    can consume route data without loose typing.
47. As a Seam maintainer, I want docs and API contracts updated, so that agents learn to use the
    route surface through typed tools.
48. As a future config/resource implementer, I want route extraction to establish the pattern for
    first-class non-code graph nodes, so that later graph model expansions are consistent.
49. As a future full-Cypher evaluator, I want route questions covered by typed tools first, so
    that Cypher is not added merely to compensate for missing product primitives.
50. As a future 3D Explorer implementer, I want route and `http_calls` edge kinds to be explicit,
    so that API boundaries can be visually highlighted.

## Implementation Decisions

- Build one deep route extraction module that accepts language, AST root, file path, and the
  file's extracted symbols, then returns route nodes, route metadata, and route/HTTP edges.
- Route extraction should be called from the existing indexing pipeline after normal symbols
  are known, so handler references can be linked to existing symbols when possible.
- Route nodes should be inserted into the existing symbols table with `kind='route'`.
- Route node names should be deterministic and human-readable, using a stable shape such as
  `ROUTE <METHOD> <PATH>` or `<METHOD> <PATH>`, with collision handling that preserves unique
  symbol rows by file and line.
- Add a dedicated route metadata table keyed to route symbols or symbol identity evidence. It
  should store method, path template, normalized path, framework, handler name, handler UID when
  available, confidence, provenance, file, and line.
- Add `http_calls` to the accepted edge vocabulary. Existing edges remain string-name-keyed for
  compatibility with the current graph model.
- Add a route-to-handler relationship using the existing `call` edge kind when the route node
  deterministically points at a handler symbol. If that overstates runtime semantics in review,
  use a narrower `handles` edge only if all read paths are updated to expose it consistently.
- Add client-to-route `http_calls` edges only when the HTTP method and URL/path can be resolved
  from static literals or very small, explicitly supported wrapper patterns.
- Confidence rules should be conservative:
  - `EXTRACTED` for direct literal framework declarations and direct literal client calls;
  - `INFERRED` for simple wrapper calls or partially normalized matches;
  - `AMBIGUOUS` when multiple route nodes match the same method/path evidence.
- Provenance should name the extractor family, such as `python-fastapi-decorator`,
  `python-flask-decorator`, `typescript-express-registration`, `typescript-fetch-literal`, or
  `typescript-axios-literal`.
- Dynamic route inference is out of scope unless the result is explicitly marked heuristic and
  bounded. The first implementation should skip dynamic cases rather than guess.
- FastAPI router prefixes, Flask blueprint prefixes, Express mounted routers, and Next.js
  filesystem route inference should be treated as staged follow-ups unless there is a simple,
  local, literal prefix available in the same file.
- `seam_schema` should report route support through counts, capabilities, symbol kind counts,
  edge kind counts, and tool guidance.
- `seam_graph_search` should accept `kind='route'` and `edge_kind='http_calls'`; degree filters,
  previews, pagination, and sorting should work without special transport logic.
- `seam_architecture` should replace the current routes unsupported warning with a populated
  routes section when route metadata exists. If the schema exists but no routes are indexed, it
  should report an honest empty state.
- Explorer should consume route nodes and `http_calls` edges through existing graph/search APIs
  where possible, with typed detail additions for route metadata.
- The MCP, CLI, and Web transports should expose route data through existing tools first. A
  separate `seam_routes` tool is out of scope for this PRD unless implementation proves the
  existing surfaces cannot represent the data cleanly.
- The migration must be additive and guarded. Opening old indexes must not crash; route tables
  may be absent and read paths must degrade.
- Full re-index should populate routes. Migration should not attempt to backfill route data from
  old symbol rows.
- Route extraction must preserve Seam's local-first trust boundary: no network calls, no server
  probing, no OpenAPI fetching, and no dependency execution.

## Testing Decisions

- Tests should assert external behavior and graph contracts, not private AST walking details.
- Unit tests should cover the route extraction module as a deep module:
  - FastAPI decorator extraction;
  - Flask decorator extraction;
  - Express/router registration extraction;
  - fetch and axios literal call extraction;
  - skipped dynamic URLs;
  - method/path normalization;
  - ambiguous route matches.
- Migration tests should follow existing migration-test style and verify old DBs gain the route
  metadata table while preserving existing files, symbols, edges, comments, clusters, imports,
  embeddings, and metadata.
- Indexer tests should verify route symbols are inserted with `kind='route'`, route metadata is
  stored, and `http_calls` edges are persisted with confidence and provenance.
- Graph-search tests should verify `kind='route'`, `edge_kind='http_calls'`, previews, invalid
  inputs, pagination, and degree filters.
- Schema tests should verify route counts and route capability reporting on populated, empty,
  and old indexes.
- Architecture tests should verify the route section is populated when route metadata exists and
  remains explicitly unsupported/unavailable on old indexes.
- Web API tests should verify route metadata appears in generated schemas or route detail payloads
  without breaking existing endpoints.
- Explorer tests should verify route nodes and `http_calls` edges render/filter through existing
  graph behavior. Keep UI assertions focused on visible behavior and typed data plumbing.
- Regression tests should verify existing call/import/field/inheritance behavior remains stable
  after route support is enabled.
- Good tests should create small source fixtures with literal routes and calls. Avoid large
  snapshots and avoid relying on exact internal AST traversal order beyond deterministic public
  output sorting.

## Out of Scope

- Full Cypher or arbitrary graph query language.
- Dynamic URL construction solving across variables, template concatenation, runtime config, or
  environment-dependent base URLs.
- Runtime route discovery by launching servers or importing application modules.
- OpenAPI/Swagger fetching or generation.
- Cross-repository or microservice discovery.
- Complete support for every framework in the first pass.
- Next.js/SvelteKit filesystem route extraction unless it is added as a separate follow-up.
- Kubernetes ingress, service mesh, or infrastructure route resources.
- Config/resource graph extraction; that belongs to P3.2.
- Test coverage edges; that belongs to P3.3.
- Exception/raise edges; that belongs to P3.4.
- A new route-specific MCP tool unless existing typed tools cannot expose the data cleanly.
- Replacing existing entry-point scoring. Route nodes should complement entry scoring, not
  remove it.

## Further Notes

- This feature is the first P3 graph model expansion and should establish a repeatable pattern
  for non-code or boundary nodes: additive schema, conservative extractor, explicit confidence,
  provenance, schema visibility, graph-search support, architecture summary, and Explorer
  rendering.
- The current `seam_architecture` route section is intentionally a placeholder. P3.1 should be
  considered complete only when that section can report real route data from the index.
- Existing entry-point scoring already recognizes route-like paths and decorators as ranking
  signals. P3.1 should not duplicate that as another heuristic-only summary; it should produce
  real graph nodes and relationships.
- Because Seam still has name-keyed edges, route node naming and collision behavior should be
  designed carefully. The metadata table is required so agents do not have to parse method/path
  back out of display names.
- The first implementation should prefer a smaller, trustworthy matrix over broad framework
  coverage. Static literal evidence beats impressive but noisy inference.
