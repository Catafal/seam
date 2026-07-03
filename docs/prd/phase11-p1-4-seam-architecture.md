# PRD: Phase 11 P1.4 — Architecture Overview (`seam_architecture` / `seam architecture`)

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P1.4.
> Competitive source: `DeusData/codebase-memory-mcp` exposes `get_architecture`, a compact
> repository briefing tool for agents.
> Status: ready-for-agent.
> GitHub issue: https://github.com/Catafal/seam/issues/134
> Tracker label: `ready-for-agent`.
> Schema target: no migration required. This is a read-only aggregation feature over the
> current Seam index, existing graph analysis primitives, and optional Explorer API output.

## Problem Statement

Agents and humans can already ask Seam precise questions: what can the index answer, where
is this symbol, what source body belongs to this UID, which symbols match this graph shape,
what depends on this target, and how are files physically structured. The missing surface is
the first compact architecture briefing: "What kind of codebase is this, how is it organized,
where are the important entry points and hotspots, and what should I inspect next?"

From the user's perspective, this creates repeated friction:

- an agent entering a repo must combine schema, structure, clusters, graph search, flows,
  search, and manual reading to form an architecture picture;
- a human evaluating Seam must know several tool names before they can get a useful
  repository-level summary;
- architecture questions currently bias agents back toward grep and directory listing even
  though Seam already has most of the indexed evidence;
- there is no single bounded payload for "brief me on this repo before I implement";
- Explorer has rich visual graph data, but the API does not expose the same high-level
  architecture summary as a stable product contract;
- future surfaces such as route edges, config/resource edges, test edges, and 3D
  constellation views need a shared architecture summary instead of each re-deriving one.

Seam needs a read-only `seam_architecture` primitive that composes existing indexed facts
into a deterministic, bounded, sectioned architecture overview.

## Solution

Add a new architecture overview surface:

- MCP tool: `seam_architecture`
- CLI command: `seam architecture`
- Web/Explorer endpoint: architecture payload for overview panels and feature gating
- Core query module: one deep, testable architecture aggregation module used by every
  transport

The feature returns a structured payload describing the repository at a glance:

- index identity and freshness;
- language and file distribution;
- top physical areas by files, symbols, and edges;
- physical structure summary using the existing structure semantics;
- functional-area cluster summaries;
- entry points and flow seeds where available;
- fan-in hotspots;
- fan-out orchestrators;
- cross-area boundaries;
- dependency edge mix and confidence mix;
- test vs production distribution where the existing test-path heuristic can classify it;
- route/config/test/resource sections as empty-but-explicit optional sections until those
  edge kinds ship;
- warnings and gaps that explain what the index cannot currently prove;
- recommended follow-up Seam calls with stable selectors such as `uid`, `cluster_id`,
  symbol name, or path scope.

The output is deterministic. It does not call an LLM, generate embeddings, mutate the
database, re-index source files, infer new edges, or read source bodies. It is a repository
briefing built from indexed metadata, counts, graph topology, and existing read-path
primitives.

The intended agent workflow becomes:

1. Call `seam_schema` to check freshness and capabilities.
2. Call `seam_architecture` to understand the repo shape and risk areas.
3. Use `seam_graph_search` for specific structural questions found in the overview.
4. Use `seam_context`, `seam_snippet`, `seam_impact`, or `seam_trace` on the exact symbols
   highlighted by the overview.

## User Stories

1. As an AI coding agent, I want to call `seam_architecture` after `seam_schema`, so that I
   can understand a repo before editing it.
2. As an AI coding agent, I want the architecture payload to include freshness, so that I do
   not trust stale architecture conclusions.
3. As an AI coding agent, I want schema and capability warnings included, so that I know
   whether missing clusters, embeddings, routes, or edge types limit the summary.
4. As an AI coding agent, I want language distribution, so that I know which languages
   dominate the indexed codebase.
5. As an AI coding agent, I want file distribution by physical area, so that I can see the
   largest packages or directories quickly.
6. As an AI coding agent, I want symbol distribution by physical area, so that I can find
   dense implementation areas rather than just large directories.
7. As an AI coding agent, I want edge distribution by physical area, so that I can identify
   highly connected areas.
8. As an AI coding agent, I want a compact physical structure section, so that I can
   understand the repo layout without a full directory dump.
9. As an AI coding agent, I want functional-area cluster summaries, so that I can understand
   logical groupings that cut across folders.
10. As an AI coding agent, I want each cluster summary to include a representative symbol,
    so that I can open a concrete follow-up target.
11. As an AI coding agent, I want entry points from flow analysis, so that I can start from
    likely runtime roots.
12. As an AI coding agent, I want fan-in hotspots, so that I can identify shared utilities
    and risky dependency centers.
13. As an AI coding agent, I want fan-out orchestrators, so that I can identify coordination
    code, handlers, pipelines, and high-blast-radius control points.
14. As an AI coding agent, I want cross-area boundary summaries, so that I can see where
    functional areas depend on each other.
15. As an AI coding agent, I want dependency edge-kind mix, so that I can tell whether the
    architecture is dominated by calls, imports, field access, inheritance, or synthesized
    dispatch.
16. As an AI coding agent, I want confidence mix, so that I know how much of the architecture
    briefing is extracted versus inferred or ambiguous.
17. As an AI coding agent, I want synthesized-edge counts surfaced, so that I can distinguish
    static evidence from heuristic dynamic-dispatch evidence.
18. As an AI coding agent, I want production/test distribution, so that test helper hotspots
    do not distort my mental model of production architecture.
19. As an AI coding agent, I want test-heavy warnings, so that I do not misread fixture helper
    fan-in as production centrality.
20. As an AI coding agent, I want a path scope option, so that I can request architecture for
    one package or subsystem.
21. As an AI coding agent, I want scoped summaries to clearly state their scope, so that I do
    not confuse package architecture with whole-repo architecture.
22. As an AI coding agent, I want section selection, so that I can ask only for hotspots,
    clusters, boundaries, or physical structure when context is tight.
23. As an AI coding agent, I want a hard byte budget, so that architecture output never
    consumes too much context.
24. As an AI coding agent, I want honest truncation metadata per section, so that I know when
    an omitted list may matter.
25. As an AI coding agent, I want stable item IDs and UIDs where possible, so that I can chain
    directly into `seam_context` or `seam_snippet`.
26. As an AI coding agent, I want follow-up recommendations, so that the overview turns into
    useful next actions rather than passive reporting.
27. As an AI coding agent, I want recommended next calls to name the exact Seam tool, so that
    I do not invent an invalid command.
28. As an AI coding agent, I want empty optional sections to say why they are empty, so that I
    know whether routes/config/test edges are unsupported, absent, or out of scope.
29. As an AI coding agent, I want architecture output to avoid source code bodies, so that I
    can request snippets only for the symbols I choose.
30. As an AI coding agent, I want the CLI and MCP outputs to share the same core data, so that
    I can switch transports without behavior drift.
31. As an AI coding agent, I want web output to share the same core data, so that Explorer and
    agent diagnostics agree.
32. As an AI coding agent, I want the tool to work on empty indexes, so that I get a useful
    diagnostic instead of a crash.
33. As an AI coding agent, I want the tool to work on older indexes, so that it degrades to
    available sections and emits upgrade hints.
34. As an AI coding agent, I want the tool to work without embeddings, so that architecture
    briefing does not depend on semantic mode.
35. As an AI coding agent, I want the tool to work without clusters, so that physical and
    graph-topology sections still provide value.
36. As an AI coding agent, I want the tool to avoid arbitrary query languages, so that answers
    remain typed and reliable.
37. As an AI coding agent, I want route summary placeholders, so that future route edges can
    slot into the same contract later.
38. As an AI coding agent, I want config/resource placeholders, so that future config/resource
    edges can slot into the same contract later.
39. As an AI coding agent, I want test-edge placeholders, so that future test coverage edges
    can slot into the same contract later.
40. As a human developer, I want `seam architecture` to print a readable repo briefing, so
    that I can orient myself without opening the web UI.
41. As a human developer, I want `seam architecture --json`, so that I can attach the payload
    to PRs, issues, or CI artifacts.
42. As a human developer, I want a quiet mode, so that I can get the highest-signal summary
    without a long report.
43. As a human developer, I want a scoped path option, so that I can understand one subsystem.
44. As a Seam Explorer user, I want an architecture overview endpoint, so that the UI can show
    a stable overview panel.
45. As a Seam Explorer user, I want architecture hotspots to link into existing graph and
    detail workflows, so that overview is connected to exploration.
46. As a Seam Explorer user, I want functional areas and physical areas summarized together,
    so that I can compare logical and filesystem organization.
47. As a Seam maintainer, I want one deep architecture aggregation module, so that CLI, MCP,
    and Web do not drift.
48. As a Seam maintainer, I want the module to be read-only, so that it is safe as an agent
    first-pass briefing.
49. As a Seam maintainer, I want the module to reuse existing analysis and query primitives,
    so that architecture logic does not duplicate graph-search, structure, cluster, or flow
    behavior.
50. As a Seam maintainer, I want expensive sections bounded, so that the tool remains fast on
    large repositories.
51. As a Seam maintainer, I want section-level errors to degrade to warnings, so that one
    optional table cannot break the entire briefing.
52. As a Seam maintainer, I want deterministic sorting, so that tests are stable and agents
    can compare architecture snapshots.
53. As a Seam maintainer, I want tests to cover empty, normal, scoped, and partially migrated
    indexes, so that the tool is robust across user states.
54. As a Seam maintainer, I want schema/tool guidance updated, so that `seam_schema`
    advertises `seam_architecture` once it exists.
55. As a Seam maintainer, I want the MCP tool count and API contracts updated, so that public
    docs match the shipped surface.
56. As a Seam maintainer, I want the web OpenAPI schema and generated TypeScript types updated,
    so that frontend code stays typed.
57. As a future route-edge implementer, I want architecture to already have a route section
    contract, so that route extraction can fill it without inventing a new output shape.
58. As a future 3D constellation implementer, I want architecture summaries to expose top
    areas, boundaries, and hotspots, so that the 3D UI can use a shared briefing source.
59. As a future documentation writer, I want architecture output to be deterministic, so that
    docs can cite it without depending on an LLM.
60. As a future CI user, I want architecture JSON to be stable enough for trend snapshots, so
    that architecture drift can later be detected.

## Implementation Decisions

- Build one deep read-only architecture aggregation module with a small public interface
  that accepts a database connection, project root, optional scope path, section selection,
  limits, and a byte budget.
- The architecture module owns the summary assembly. Transports must only validate inputs,
  call the module, map handler errors, and render the result.
- The feature does not require a schema migration. It reads the current index and degrades
  when optional tables or columns are missing.
- The feature does not add new graph extraction. Route, config, resource, and test-edge
  sections are represented as absent or unavailable until those edge families ship.
- The feature is read-only. It must not create, alter, insert, update, delete, migrate,
  index, cluster, synthesize, embed, or write files.
- The feature does not read source bodies. It may return symbol identity, UID, file, line,
  kind, signature, cluster, and degree metadata for follow-up calls.
- The feature should not call an LLM. Any prose summary should be deterministic templated
  text derived from the structured payload.
- The default mode should return a compact architecture briefing, not every possible row.
- A `sections` selector should allow callers to request only selected sections. Initial
  section names should include summary, languages, physical, clusters, entry_points,
  hotspots, orchestrators, boundaries, edge_mix, tests, optional_surfaces, and next_calls.
- A `scope` selector should restrict physical, symbol, edge, cluster, hotspot, and boundary
  calculations to indexed files under a root-relative path. Unknown or out-of-root scopes
  should return an empty scoped summary with a structured warning, not an exception.
- A `max_sections`, per-section limit, or equivalent limit model should keep lists bounded.
  The default should favor high-signal top-N summaries.
- A `max_bytes` budget should be enforced after section assembly. If the payload must be
  trimmed, trimming should remove lowest-priority list entries first and report exactly what
  was truncated.
- The payload should include top-level identity: schema version, Seam version, freshness,
  scope, generated timestamp or deterministic index timestamp if already available, and
  section selection.
- The payload should include top-level counts: files, symbols, edges, clusters, comments,
  import mappings, embeddings, languages, test files, production files, and unknown-scope
  files where available.
- The language section should summarize file count and symbol count per language.
- The physical section should summarize top directories or packages by files, symbols, and
  edge volume, using root-relative paths only.
- The physical section should reuse the existing structure semantics for directories, files,
  containers, functions, members, area labels, depth limits, node limits, and truncation.
- The cluster section should summarize largest or most-connected functional areas with
  cluster id, label, size, representative symbol, representative UID when resolvable, and
  top physical areas.
- The entry-point section should reuse existing flow entry-point detection where possible.
  It should expose name, kind, file, line, UID, and a small forward-degree summary.
- The hotspot section should identify high fan-in symbols. It should allow production-only
  ranking by default or at least tag test symbols clearly so test helpers do not dominate.
- The orchestrator section should identify high fan-out symbols independently of any current
  graph-search preset. It should expose outgoing degree, incoming degree, edge-kind mix, and
  whether the item is test code.
- The boundary section should summarize cross-area or cross-directory dependencies. It
  should identify source area, target area, edge count, dominant edge kinds, confidence mix,
  and representative symbols.
- The edge-mix section should include edge-kind counts, confidence counts, synthesized-edge
  counts, and warnings when inferred or ambiguous edges dominate.
- The tests section should use the existing test-path heuristic to summarize production vs
  test files and symbols. It must not claim full coverage until explicit test edges exist.
- Optional-surface sections for routes, configs, resources, and test edges should distinguish
  unsupported, supported-but-empty, and supported-with-data states.
- Warnings should be structured and stable. Candidate warning codes include
  `INDEX_STALE`, `INDEX_EMPTY`, `NO_CLUSTERS`, `NO_ENTRY_POINTS`, `NO_ROUTE_EDGES`,
  `NO_CONFIG_EDGES`, `NO_TEST_EDGES`, `SCOPE_EMPTY`, `SCOPE_OUTSIDE_ROOT`,
  `SECTION_UNAVAILABLE`, `SECTION_TRUNCATED`, `BYTE_BUDGET_EXCEEDED`,
  `TEST_HELPERS_DOMINATE_HOTSPOTS`, `HIGH_INFERRED_EDGE_RATIO`, and
  `HIGH_AMBIGUOUS_EDGE_RATIO`.
- Recommended next calls should be derived from the payload. Examples: inspect a hotspot with
  context, retrieve a representative symbol snippet, run impact for a high fan-in symbol,
  run graph search for an edge family, or open a scoped structure view.
- Recommended next calls should be machine-actionable enough for agents: include tool name,
  reason, and parameters where safe.
- The MCP tool should be named `seam_architecture`.
- The CLI command should be named `seam architecture`.
- The CLI command should support JSON output, quiet output, scoped paths, section selection,
  per-section limits, and a max-byte option consistent with existing read commands.
- The MCP tool should expose only typed parameters, not arbitrary SQL, Cypher, or regular
  expressions beyond any existing safe filter surfaces it delegates to.
- The Web endpoint should return the same core architecture payload with Pydantic response
  models as the OpenAPI source of truth.
- Explorer can consume the endpoint later for a summary panel, but the PRD implementation
  should include the endpoint and generated types even if the full UI panel is staged behind
  a minimal view.
- `seam_schema` tool guidance should be updated so `seam_architecture` is advertised as the
  correct first architecture/repo-briefing call.
- The MCP/API contract documentation should mark `seam_architecture` as implemented when the
  work lands.
- Architecture docs should be updated to include the new tool in the read-path map.
- The feature should avoid absolute paths in JSON/MCP output. Root-relative paths are the
  default.
- The feature should not expose environment variables, source text, embedding vectors, or
  secret-like values.
- Errors should follow existing handler conventions: invalid input returns structured
  `INVALID_INPUT`, missing index returns `NO_INDEX`, database failures return `DB_ERROR`, and
  unknown/empty scoped results remain successful responses with warnings when possible.
- Section calculations should be resilient. A section-specific failure should produce a
  warning and an empty section instead of failing the whole architecture briefing, unless the
  database cannot be opened at all.

## Testing Decisions

- Good tests assert external behavior: response shape, section availability, deterministic
  ordering, warnings, scoping, bounds, and transport parity. They should not assert internal
  SQL strings or helper-function call sequences.
- Unit tests should cover the architecture aggregation module on small fixture indexes with
  known files, symbols, edges, clusters, test files, and cross-area dependencies.
- Unit tests should cover an empty initialized index: the result should be valid, low-noise,
  and include `INDEX_EMPTY`.
- Unit tests should cover a missing or unpopulated clusters table: physical, language,
  hotspot, and edge-mix sections should still work, and cluster-specific warnings should be
  structured.
- Unit tests should cover path scoping: valid scope, empty scope, and out-of-root scope.
- Unit tests should cover fan-in hotspot ranking and fan-out orchestrator ranking separately.
- Unit tests should cover test-helper dominance so test-only symbols are tagged and do not
  silently distort production architecture.
- Unit tests should cover cross-area boundary aggregation with deterministic tie-breaking.
- Unit tests should cover section selection so omitted sections are absent or explicitly
  omitted according to the final contract.
- Unit tests should cover byte-budget trimming and per-section truncation metadata.
- Unit tests should cover warning codes for unavailable route/config/test-edge sections.
- Handler tests should verify `handle_seam_architecture` delegates to the core module and
  returns transport-neutral output.
- MCP tests should verify `seam_architecture` is registered and follows the existing
  not-found/error normalization contract.
- CLI tests should verify `seam architecture --json`, quiet output, invalid section names,
  invalid scope, and max-byte behavior.
- Web integration tests should verify the architecture endpoint returns the same core data
  shape and maps handler errors to existing HTTP error conventions.
- OpenAPI/type-generation tests should verify the Explorer API types include the new
  architecture endpoint.
- Regression tests should update the current tool-count expectations from fifteen to
  sixteen read-only MCP tools.
- Documentation tests, if any are present for examples, should be updated so examples prefer
  `seam_schema` then `seam_architecture` before deeper calls.
- Prior art for these tests exists in the schema, snippet, graph-search, structure, web API,
  MCP registration, and CLI JSON test families.

## Out of Scope

- No new database schema migration.
- No route extraction or route edge implementation.
- No config/resource edge implementation.
- No test coverage edge implementation.
- No full Cypher or arbitrary graph query language.
- No LLM-generated architecture narrative.
- No source-code body retrieval; callers should use `seam_snippet`.
- No embedding generation or semantic clustering.
- No watcher changes.
- No write-capable MCP tools.
- No broad installer changes.
- No replacement of the current Explorer graph canvas.
- No 3D constellation UI in this PRD, though this payload should support that later work.
- No CI architecture-drift gate, though stable JSON should make that possible later.

## Further Notes

This feature should be implemented immediately after P1.1, P1.2, and P1.3 because it is
mostly composition. `seam_schema` tells an agent what is available, `seam_graph_search`
finds structural candidates, `seam_snippet` retrieves exact source, and
`seam_architecture` should become the compact repo-level briefing that points agents toward
the right next precise call.

The current Seam index already has enough evidence for a valuable first version: fresh
schema v12, thousands of symbols and edges, clusters, import mappings, field-access edges,
and synthesized edges. It does not have embeddings in the default index, and it does not yet
have route/config/test-resource edge families. The architecture tool should say that
plainly instead of pretending those sections exist.

The most important implementation risk is shallow aggregation: a report that merely dumps
counts would not justify a new tool. The value comes from ranked, bounded, follow-up-ready
sections that help an agent decide where to inspect next.
