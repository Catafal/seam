# PRD - Phase 11 RFC: HTTP Call Extraction Quality

> Status: ready-for-agent.
> Created: 2026-07-06.
> GitHub issue: https://github.com/Catafal/seam/issues/395.
> Tracker label: `ready-for-agent`.
> Roadmap source: `.claude/tasks/codememory-inspired-agent-answerability-roadmap.md`.
> Supersedes implementation planning from closed issue #276 without changing the
> broader protocol-edge direction.

## Problem Statement

Seam's next highest-leverage roadmap phase is HTTP call extraction quality.
The reason is practical, not feature parity. Seam already indexes local route
nodes, route metadata, graph-search recipes, architecture route summaries, and
the `http_calls` edge vocabulary. But the current fresh Seam index reports route
inventory without populated HTTP caller evidence. In concrete terms, an agent can
ask "what routes exist?" but still cannot reliably ask "what local code calls
this route?" without falling back to grep.

That gap is directly against Seam's product goal: help commercial coding agents
such as Claude Code, Codex, OpenCode, Cursor, and Gemini navigate a local
codebase with less token spend and stronger evidence than broad text search. HTTP
routes are codebase boundaries. When a route changes, the impacted caller may be
a frontend hook, a generated API client, a test helper, a backend integration
client, or a thin local wrapper around `fetch`, `axios`, `requests`, `httpx`, or
`aiohttp`. Normal function-call graphs often miss this coupling because the
relationship is encoded as method plus URL/path data, not as a symbol call.

The current extracted evidence also creates an agent-answerability mismatch.
Schema and graph-search surfaces advertise `http_calls` as a supported edge kind,
and architecture can describe an HTTP-call section, but on Seam itself the count
is zero. This is honest reporting, but it leaves agents with a dead end: the
tooling knows the concept exists, yet the extractor does not capture enough real
project patterns to make the concept useful in daily work.

The most important known missed pattern is local HTTP wrapper use. Seam's own web
client uses a typed wrapper around `fetch`; callers pass route-shaped literal
paths to that wrapper, while the extractor currently recognizes direct
`fetch`/`axios` forms much better than local wrapper call sites. A human can see
that these wrapper calls are API traffic. Seam should be able to extract the same
evidence when the method and path are static enough to be safe.

This PRD is not asking Seam to infer all network behavior. It is asking Seam to
make static, local, conservative HTTP call evidence good enough that agents can
answer route-caller questions with bounded graph output instead of scanning the
repository.

## Solution

Improve Seam's existing route/protocol extraction so `http_calls` becomes useful
for common single-repo HTTP coupling questions while preserving the current trust
model.

The implementation should expand static HTTP call extraction for Python,
TypeScript, and JavaScript. It should match local route calls when method and path
are explicit or near-literal, normalize paths and methods consistently, and emit
`http_calls` edges from the calling symbol to the matching route node with
confidence and provenance. It should skip or clearly exclude dynamic/external
traffic rather than guessing.

The first implementation should target the patterns most likely to reduce agent
token spend:

1. Local wrapper calls where the wrapper is known in the file or imported from a
   local client module and the call-site path is literal.
2. Direct `fetch` calls with literal URL/path and optional literal method.
3. Direct `axios` method calls and config-object calls with literal URL/path and
   method.
4. Python `requests`, `httpx`, and first-pass `aiohttp` calls with literal
   URL/path and method.
5. Safe path normalization for query strings, fragments, leading slashes,
   same-origin localhost URLs, and route parameters where the existing route
   template model can support it.

The implementation should not create a new user-facing tool. It should improve
the evidence behind existing surfaces:

1. `seam_schema` reports route and HTTP-call capability/population honestly.
2. `seam_graph_search` finds outgoing HTTP callers and incoming route callers.
3. The `http-callers` recipe becomes useful on repos with supported patterns.
4. `seam_architecture` distinguishes route inventory from caller evidence.
5. Explorer and Web continue to consume the same graph/search payloads.
6. Existing workspace route-caller federation continues to work when populated.
7. Answerability scenarios move from "capability absent" to "evidence-backed
   route caller discovery" for supported fixture patterns.

The trust rules stay strict:

1. No network calls.
2. No OpenAPI fetching.
3. No server probing.
4. No application execution.
5. No semantic/similarity matching for dependency evidence.
6. No secret values or request payload capture.
7. False negatives are acceptable; false local route edges are not.
8. Uncertain/dynamic paths must not become confident internal edges.

## User Stories

1. As an AI coding agent, I want to find local callers of a route, so that I can
   assess an endpoint change without grepping for path strings.
2. As an AI coding agent, I want to find routes called by a symbol, so that I can
   understand what API boundary a frontend hook or client module depends on.
3. As an AI coding agent, I want route caller evidence to include method, path,
   file, line, confidence, and provenance where available, so that I can judge
   whether the evidence is strong enough before editing.
4. As an AI coding agent, I want schema introspection to report `has_http_calls`
   only when edges are actually populated, so that I do not choose a dead-end tool
   path.
5. As an AI coding agent, I want schema guidance to distinguish "routes exist"
   from "HTTP callers were found," so that route inventory is not mistaken for
   route usage.
6. As an AI coding agent, I want graph search to return incoming callers for a
   selected route node, so that I can inspect affected clients with one bounded
   result.
7. As an AI coding agent, I want graph search to return outgoing HTTP calls from a
   selected client symbol, so that I can inspect what API surface the client uses.
8. As an AI coding agent, I want graph-search previews to show the matched route
   identity, so that I can avoid another broad lookup.
9. As an AI coding agent, I want the `http-callers` recipe to work when
   `has_http_calls` is true, so that I can use intent-shaped queries instead of
   constructing filters manually.
10. As an AI coding agent, I want architecture output to show whether HTTP caller
    evidence is populated, so that a repository briefing identifies real protocol
    links.
11. As an AI coding agent, I want architecture output to stay compact, so that
    route caller evidence does not bloat the first architecture briefing.
12. As an AI coding agent, I want direct `fetch` calls with literal paths indexed,
    so that common frontend clients are visible.
13. As an AI coding agent, I want `fetch` calls with literal method options
    indexed, so that method-specific route matching is correct.
14. As an AI coding agent, I want local wrapper calls such as a typed `apiFetch`
    helper indexed when the call-site path is literal, so that real codebase
    patterns are captured.
15. As an AI coding agent, I want wrapper calls to be recognized only when the
    wrapper is explicitly local and HTTP-shaped, so that arbitrary functions with
    path-looking strings do not become network edges.
16. As an AI coding agent, I want `axios.get`, `axios.post`, `axios.put`,
    `axios.patch`, `axios.delete`, and equivalent literal method calls indexed, so
    that common client-library usage is visible.
17. As an AI coding agent, I want axios config-object calls indexed when `url` and
    `method` are literal or method defaults are clear, so that config-style calls
    are not missed.
18. As an AI coding agent, I want Python `requests` literal calls indexed, so that
    backend clients and tests using requests are visible.
19. As an AI coding agent, I want Python HTTPX literal calls indexed, so that sync
    and async Python HTTP clients are visible.
20. As an AI coding agent, I want a first-pass `aiohttp` literal extractor, so that
    common async client sessions are visible when method and path are explicit.
21. As an AI coding agent, I want local client instances recognized only when the
    receiver is known to be an HTTP client, so that ordinary `.get()` or `.post()`
    methods do not become false protocol edges.
22. As an AI coding agent, I want query strings stripped before route matching, so
    that `/api/items?page=1` can match `/api/items` without storing request data.
23. As an AI coding agent, I want URL fragments ignored before route matching, so
    that fragments do not create fake route identities.
24. As an AI coding agent, I want same-origin localhost URLs normalized to their
    path when safe, so that local development URLs can match local route nodes.
25. As an AI coding agent, I want third-party absolute URLs excluded from internal
    route edges, so that external dependencies are not misrepresented as local
    code coupling.
26. As an AI coding agent, I want unsupported dynamic URL construction skipped, so
    that a path built from runtime variables does not become false evidence.
27. As an AI coding agent, I want template strings with arbitrary interpolation
    skipped unless the implementation can prove a static route template, so that
    dynamic clients stay caveated.
28. As an AI coding agent, I want simple literal concatenation considered only
    under narrow rules, so that useful base-path constants can be captured without
    full data-flow analysis.
29. As an AI coding agent, I want path parameters normalized conservatively, so
    that route template matches do not rely on arbitrary string similarity.
30. As an AI coding agent, I want route method mismatches to be non-matches, so
    that `POST /api/items` does not point at `GET /api/items`.
31. As an AI coding agent, I want route caller evidence to be absent rather than
    guessed when multiple route candidates are ambiguous, so that I can trust
    populated edges.
32. As an AI coding agent, I want HTTP-call extraction to run during indexing and
    sync, so that query paths stay fast and read-only.
33. As an AI coding agent, I want HTTP-call extraction to work with semantic search
    disabled, so that graph facts do not depend on embeddings.
34. As an AI coding agent, I want semantic discovery excluded from HTTP dependency
    edges, so that similarity never creates route-caller evidence.
35. As an AI coding agent, I want route-caller evidence to remain separate from
    default impact traversal unless deliberately configured, so that impact output
    does not become noisier without a policy decision.
36. As an AI coding agent, I want existing impact/trace behavior documented if
    `http_calls` is already part of traversal vocabulary, so that tool semantics
    are explicit rather than accidental.
37. As an AI coding agent, I want `seam_context` on a client symbol to expose
    outgoing protocol relationships through existing edge surfaces where relevant,
    so that I can move from symbol context to route context.
38. As an AI coding agent, I want `seam_snippet` to remain the way to inspect the
    exact call site, so that HTTP-call results stay bounded and do not dump files.
39. As a human developer, I want to ask "who calls this endpoint?" and receive
    route caller evidence with file and line, so that API edits are faster.
40. As a human developer, I want to ask "which endpoints does this client call?"
    and receive a compact list, so that frontend/backend coupling is visible.
41. As a human developer, I want architecture output to summarize top HTTP callers
    and called routes only when evidence is populated, so that the briefing stays
    honest.
42. As a human developer, I want unsupported dynamic/external HTTP calls not to be
    hidden behind false confidence, so that I know when grep or manual inspection
    is still needed.
43. As an Explorer user, I want `http_calls` edges to remain filterable separately
    from normal `call` edges, so that protocol boundaries are visually distinct.
44. As an Explorer user, I want route nodes to show incoming HTTP-call evidence
    when available, so that route usage appears in topology views.
45. As a Seam maintainer, I want the matching logic isolated behind a deep module,
    so that URL/method normalization can be tested independently from AST walking.
46. As a Seam maintainer, I want TypeScript/JavaScript extraction helpers to share
    normalization with Python extraction helpers, so that route matching semantics
    do not drift by language.
47. As a Seam maintainer, I want provenance strings to be stable and
    product-facing, so that agents can explain why an edge exists.
48. As a Seam maintainer, I want all new edges to preserve direct extractor
    provenance separately from synthesized-edge provenance, so that static
    extraction and heuristic synthesis are not conflated.
49. As a Seam maintainer, I want per-file re-indexing to replace stale HTTP-call
    edges atomically, so that sync does not leave old route callers behind.
50. As a Seam maintainer, I want old indexes without HTTP-call edges to degrade
    cleanly, so that schema, graph search, architecture, MCP, and Web surfaces do
    not crash.
51. As a Seam maintainer, I want answerability scenarios to prove the agent can
    answer route-caller questions after this work, so that the feature is measured
    by real agent utility.
52. As a Seam maintainer, I want negative fixtures for dynamic URLs, external URLs,
    method mismatch, and unknown routes, so that future extractor broadening does
    not silently add false positives.
53. As a Seam maintainer, I want no schema migration unless edge rows are proven
    insufficient, so that the first quality pass stays small and safe.
54. As a Seam maintainer, I want any unresolved-call inventory to be a separate
    design decision, so that this PRD does not expand into external dependency
    mapping.
55. As a future protocol-edge implementer, I want HTTP extraction to establish
    confidence/provenance conventions, so that GraphQL, gRPC, tRPC, and pub/sub
    work can reuse them later.
56. As a future cross-repo implementer, I want single-repo HTTP matching stable
    first, so that cross-repo route matching does not compound uncertainty.
57. As a future infra-graph implementer, I want HTTP route-caller evidence separate
    from infra service declarations, so that runtime topology and static protocol
    usage remain distinct.

## Implementation Decisions

- The next roadmap phase is HTTP call extraction quality, not a broad new
  protocol system. The closed protocol-edge PRD remains prior art, but this PRD
  narrows implementation to real route-caller answerability gaps.
- Keep the existing route-node and `http_calls` graph model. Matched internal
  calls should continue to be represented as `http_calls` edges from source
  symbols to route symbols.
- Do not add a new MCP tool. Existing schema, graph-search, architecture,
  context, snippet, Web, Explorer, and workspace surfaces are sufficient for the
  first quality pass.
- Create or extract a deep HTTP route-call matching module with a small interface
  that accepts static call evidence and indexed route identities and returns a
  match verdict. The interface should hide URL/method normalization complexity
  from language-specific AST walkers.
- Keep language-specific AST extraction as thin adapters. Their job is to find
  candidate call sites with method/path evidence and provenance; the shared
  matcher decides whether the call can safely become a local route edge.
- Preserve the current no-execution indexing model. Extractors may inspect source
  text and syntax trees only.
- HTTP-call extraction happens during `init` and `sync`, never during query-time
  MCP calls.
- The first matching target is same-repo route symbols already indexed by Seam.
  Do not create synthetic route nodes for unknown paths in this PRD.
- Direct literal paths are the highest-confidence case. They should become
  extracted edges when the normalized method/path matches an indexed route.
- Local wrapper paths are in scope only under narrow rules: the called wrapper is
  explicitly known to be local HTTP transport, and the call-site path/method are
  static enough to normalize.
- Wrapper recognition should be allowlisted or structurally proven from local
  import/function evidence. A random function that accepts a string path must not
  become an HTTP edge.
- Simple base-path constants may be supported only when they are local literals
  in a bounded scope and can be combined without evaluating arbitrary code.
- Arbitrary data-flow, interprocedural constant propagation, environment
  variable resolution, config loading, and build-time alias resolution are out of
  scope.
- Query strings and fragments should be removed before route matching. They are
  request details, not route identity.
- Same-origin localhost absolute URLs may normalize to local paths when the URL is
  literal and the route path exists locally.
- Third-party absolute URLs must not become internal route edges. This PRD may
  skip them entirely; if external-call inventory is added later it must be
  clearly separate from `http_calls` to local routes.
- Method is part of identity. Default method should be `GET` only when the
  library semantics make that default clear.
- Route parameter matching must be conservative. Prefer exact route-template
  matching where the route extractor already knows the template; do not infer
  templates from arbitrary sample values.
- Edge confidence and provenance must be visible enough for agents to explain the
  match. Suggested provenance families include direct fetch literal, local
  wrapper literal, axios method literal, axios config literal, requests literal,
  httpx literal, and aiohttp literal.
- Direct extractor provenance belongs in the direct provenance channel, not in
  synthesized-edge provenance.
- Do not broaden default impact semantics as an accidental side effect. If current
  traversal already includes `http_calls`, document and regression-test the
  intended behavior. If the implementation changes policy, that change must be
  explicit and covered by impact tests.
- Architecture output should report HTTP-call evidence only when populated and
  should recommend graph-search follow-ups. It should not become an API
  documentation generator.
- Schema output should continue to distinguish supported-empty from populated
  evidence. `has_http_calls:false` is valid when no matched edges exist.
- Graph-search previews should preserve route resolution and bounded caller/callee
  summaries.
- Workspace federation can consume the populated `http_calls` evidence but should
  not drive the first implementation. Single-repo route-caller quality comes
  first.
- No new database schema is expected for the first pass. If a candidate
  implementation requires unresolved call storage or extra call metadata, that
  should become a scoped follow-up instead of being folded into this PRD.
- Per-file sync must remove stale HTTP-call edges from the changed file before
  adding new ones, following existing upsert behavior.
- Existing route extraction tests should continue to pass. Route inventory and
  route caller evidence are related but separate product facts.
- Existing docs/API contracts should be updated only where behavior or trust
  language changes.

## Testing Decisions

- Good tests assert externally visible behavior: indexed edges, schema
  capabilities, graph-search results, architecture summaries, answerability
  scenarios, and transport parity where surfaces are affected.
- Tests should not assert private tree-sitter traversal shape. They should use
  representative source fixtures and inspect the graph/results that Seam emits.
- Unit tests should cover the deep matching module directly: method normalization,
  path normalization, query stripping, fragment stripping, same-origin URL
  handling, external URL rejection, method mismatch, unknown route, and ambiguous
  route candidates.
- TypeScript/JavaScript extraction tests should cover direct `fetch` default
  method, `fetch` with literal method options, direct axios method calls, axios
  config-object calls, local wrapper calls with literal paths, and wrapper calls
  with literal method override if supported.
- TypeScript/JavaScript negative tests should cover dynamic `fetch(url)`,
  template interpolation with runtime variables, unknown wrapper functions,
  external absolute URLs, and local-looking strings passed to non-HTTP helpers.
- Python extraction tests should cover `requests` method calls, `requests.request`
  with literal method, HTTPX sync calls, HTTPX async calls where statically
  visible, and first-pass `aiohttp` session calls.
- Python negative tests should cover ordinary `.get()` methods on non-HTTP
  receivers, dynamic path variables, external absolute URLs, and unknown route
  paths.
- Indexer tests should verify `http_calls` edges are persisted with the expected
  source symbol, target route, line, confidence, and provenance.
- Sync tests should verify stale HTTP-call edges are removed when a call site is
  edited or removed.
- Schema tests should verify `has_route_nodes:true` with `has_http_calls:false`
  when routes exist but no caller evidence exists.
- Schema tests should verify `has_http_calls:true` when at least one supported
  call-site edge is populated.
- Graph-search tests should verify outgoing HTTP callers, incoming route callers,
  the `http-callers` recipe, previews, pagination/limits, and invalid edge-kind
  behavior.
- Architecture tests should verify route inventory remains populated separately
  from HTTP-call evidence.
- Architecture tests should verify populated HTTP-call evidence appears in a
  bounded section with confidence/provenance and next-call guidance.
- Architecture tests should verify empty HTTP-call evidence is caveated honestly
  without contradicting route inventory.
- Impact/trace tests should be added only if this PRD changes or clarifies
  `http_calls` traversal semantics. If semantics are unchanged, add a regression
  test documenting the intended current behavior.
- Answerability scenarios should include at least one natural-language question
  like "which local code calls this route?" over a fixture with supported local
  calls. The expected answer should require file/line/provenance evidence and
  should compare favorably to a broad grep fallback.
- Existing workspace route-caller tests should stay green because workspace
  federation reads the same edge family.
- Web/API generated types should be regenerated only if payload shapes change.
- Explorer visual/browser tests are not required unless UI code changes.
- Full gate should run before implementation merge because this feature touches
  indexing, schema, graph-search, architecture, and possibly impact semantics.

## Out of Scope

- gRPC extraction.
- GraphQL resolver/operation matching.
- tRPC router/client matching.
- WebSocket channel extraction.
- Pub/sub, queue, topic, and event-bus protocol edges.
- Cross-repo HTTP matching beyond existing workspace read surfaces.
- Runtime trace ingestion.
- OpenAPI fetching or parsing.
- Running servers or probing localhost.
- Executing app code, importing user modules, or running build tools.
- Full data-flow analysis.
- Arbitrary string-constraint solving.
- Environment-variable or secret-value resolution.
- Request body, query parameter value, header value, cookie, auth token, or
  payload capture.
- External dependency inventory unless separately designed.
- New route nodes for unknown URLs.
- New default impact traversal policy unless explicitly scoped and tested.
- Changing edge endpoints from name-keyed route symbols to row IDs.
- New MCP tools.
- New graph query language.
- Infra graph extraction.
- Graph artifact export/import.
- Installer, distribution, or release work.

## Further Notes

Current research findings:

- The fresh local index is schema v16 and reports populated route nodes but zero
  HTTP-call edges.
- The existing route extractor already supports route metadata and some literal
  direct client-call forms.
- Graph search already accepts `http_calls`, and the `http-callers` recipe exists.
- Architecture already has separate route and HTTP-call concepts.
- Web and MCP contracts already understand the edge kind.
- Workspace federation can read populated HTTP-call evidence.
- The main practical missing pattern in Seam itself is local wrapper calls from
  the web client to local API routes.

Recommended first implementation slices:

1. Define and test the shared method/path matching contract.
2. Add TypeScript/JavaScript wrapper and direct-call extraction cases.
3. Add Python requests, HTTPX, and first-pass aiohttp literal extraction cases.
4. Wire regression tests through indexer, schema, graph-search, architecture, and
   answerability scenarios.
5. Update docs/API language so agents understand supported-empty vs populated
   HTTP-call evidence.

Recommended follow-up after this PRD:

1. Docker/Compose infra graph productization, if still next by roadmap after HTTP
   evidence is improved.
2. Hybrid exact type-resolution quality if wrapper detection exposes receiver
   precision limits.
3. Semantic query productization as opt-in discovery, still excluded from
   dependency evidence.
