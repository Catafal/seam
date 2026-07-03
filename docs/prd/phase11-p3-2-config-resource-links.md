# PRD: Phase 11 P3.2 — Config And Resource Links

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P3.2.
> Competitive source: `DeusData/codebase-memory-mcp` models resources and `CONFIGURES`
> edges as first-class graph evidence.
> Status: ready-for-agent.
> GitHub issue: https://github.com/Catafal/seam/issues/148.
> Tracker label: `ready-for-agent`.
> Schema target: additive migration. Config/resource nodes become first-class symbols,
> metadata is stored separately, and config/resource relationship edges are added to the
> graph vocabulary without indexing secret values by default.

## Problem Statement

Seam can now explain code symbols, dependencies, HTTP routes, and literal HTTP calls, but it
still cannot answer a common deployment/debugging question directly: "which environment
variables, config keys, and external resources does this code depend on, and where are they
declared?"

From the user's perspective, this creates repeated friction:

- config evidence is scattered across `.env.example`, JSON/TOML/YAML files, package manifests,
  framework config files, Docker/CI files, and source reads;
- `seam_architecture` has config/resource optional-surface placeholders, but they currently
  report unsupported;
- `seam_graph_search` does not yet accept `kind=config`, `kind=resource`,
  `edge_kind=configures`, `edge_kind=reads_config`, or equivalent typed filters;
- agents must grep for `process.env`, `os.getenv`, `os.environ`, `import.meta.env`, and config
  loaders manually, then reconcile those reads with declaration files by hand;
- agents can miss operational dependencies such as Redis URLs, database DSNs, queues, buckets,
  feature flags, or API tokens because those are not normal call/import edges;
- route and architecture surfaces can describe code boundaries, but not the runtime resources
  those boundaries require;
- future test-edge, exception-edge, and 3D views need resource/config evidence as graph data,
  not as one-off text snippets;
- indexing config incorrectly is risky because raw config values can contain secrets,
  credentials, hostnames, or internal infrastructure details.

Seam needs conservative config/resource extraction that captures keys and relationships while
preserving the local-first trust boundary and avoiding secret-value indexing by default.

## Solution

Add first-class config/resource graph evidence for high-confidence static cases.

The feature should index:

- safe config declaration files, starting with `.env.example`, `.env.sample`, `.env.template`,
  `.env.defaults`, JSON, TOML, YAML/YML, selected project manifests, and selected framework
  config files;
- source-level environment reads in Python and TypeScript/JavaScript, including
  `os.getenv("KEY")`, `os.environ["KEY"]`, `os.environ.get("KEY")`, `process.env.KEY`,
  `process.env["KEY"]`, and `import.meta.env.KEY`;
- simple config-loader reads where the key is a literal and the receiver is locally visible,
  such as `settings.get("key")`, `config.get("key")`, or a parsed config object access when
  the evidence is not ambiguous;
- deterministic resource declarations in known safe files, such as dependency names from
  package manifests, service names in compose files, and resource-shaped config keys such as
  `DATABASE_URL`, `REDIS_URL`, `S3_BUCKET`, or `*_QUEUE_URL` without storing their values.

The graph should expose:

- config key nodes as symbol rows with `kind='config'`;
- resource nodes as symbol rows with `kind='resource'` only when the evidence points to a
  runtime dependency or external system rather than a plain scalar setting;
- config/resource metadata as dedicated read models: key/name, normalized key, source family,
  file/line, declaration kind, value safety state, optional value type/category, confidence,
  and provenance;
- `reads_config` edges from code symbols to config key nodes when code reads a literal config
  key;
- `configures` edges from config key nodes to resource nodes when a key deterministically
  describes a resource dependency;
- optional `uses_resource` edges from code symbols to resource nodes only when the path through
  a config read is high-confidence and useful enough to avoid forcing agents to join two hops;
- schema, graph search, architecture, MCP, CLI, Web, Explorer, and docs updates so the new
  evidence is visible through existing typed surfaces.

The first version is deliberately conservative. It should prefer missing dynamic config over
inventing dependencies. It must never index raw config values by default. Values are either not
stored or reduced to a non-sensitive classification such as `present`, `empty`, `example`,
`placeholder`, `boolean`, `number`, `url-like`, or `redacted`.

The intended workflow becomes:

1. Call `seam_schema` and see config/resource support, counts, capabilities, and edge kinds.
2. Call `seam_architecture --section configs` or `--section resources` to get a compact
   operational-dependency summary.
3. Call `seam_graph_search --kind config` to list known config keys.
4. Call `seam_graph_search --edge-kind reads_config --preview` to find code reading a key.
5. Call `seam_graph_search --edge-kind configures --preview` to find keys that configure
   resources.
6. Use `seam_snippet`, `seam_context`, `seam_impact`, Explorer, and route surfaces on returned
   UIDs exactly like other graph nodes.

## User Stories

1. As an AI coding agent, I want config keys in the Seam graph, so that I can inspect runtime
   inputs without grepping config files.
2. As an AI coding agent, I want resource nodes in the Seam graph, so that deployment
   dependencies are visible alongside code dependencies.
3. As an AI coding agent, I want config/resource nodes to have stable UIDs, so that I can chain
   from discovery into snippets, context, and Explorer.
4. As an AI coding agent, I want `kind=config` to work in graph search, so that config keys are
   discoverable through the same typed surface as functions, fields, and routes.
5. As an AI coding agent, I want `kind=resource` to work in graph search, so that runtime
   resources can be listed without custom queries.
6. As an AI coding agent, I want `edge_kind=reads_config` to work in graph search, so that I can
   find all code that reads a specific environment variable or config key.
7. As an AI coding agent, I want `edge_kind=configures` to work in graph search, so that I can
   see which keys configure which resources.
8. As an AI coding agent, I want Python `os.getenv("KEY")` reads linked to config nodes, so that
   backend environment dependencies are explicit.
9. As an AI coding agent, I want Python `os.environ["KEY"]` reads linked to config nodes, so
   that required variables are visible.
10. As an AI coding agent, I want Python `os.environ.get("KEY")` reads linked to config nodes,
    so that optional variables are visible.
11. As an AI coding agent, I want TypeScript/JavaScript `process.env.KEY` reads linked to config
    nodes, so that Node runtime dependencies are explicit.
12. As an AI coding agent, I want `process.env["KEY"]` reads linked to config nodes, so that
    bracket-style access is covered.
13. As an AI coding agent, I want `import.meta.env.KEY` reads linked to config nodes, so that
    Vite/frontend config keys are visible.
14. As an AI coding agent, I want `.env.example` keys indexed without values, so that declared
    environment contracts are discoverable safely.
15. As an AI coding agent, I want `.env.sample` and `.env.template` keys indexed without values,
    so that common template files are supported.
16. As an AI coding agent, I want JSON config keys indexed when files are selected as safe, so
    that project-level config contracts are visible.
17. As an AI coding agent, I want TOML config keys indexed when files are selected as safe, so
    that Python/Rust/project metadata can contribute config evidence.
18. As an AI coding agent, I want YAML config keys indexed when files are selected as safe, so
    that CI, compose, and framework files can contribute config evidence.
19. As an AI coding agent, I want package manifest dependencies represented as resource evidence
    where useful, so that runtime packages can be included in architecture summaries.
20. As an AI coding agent, I want Docker Compose service names represented as resources, so that
    local service dependencies become visible.
21. As an AI coding agent, I want config keys such as `DATABASE_URL` categorized as database
    resources, so that operational dependencies are easier to scan.
22. As an AI coding agent, I want config keys such as `REDIS_URL` categorized as cache/queue
    resources, so that infrastructure dependencies are easier to identify.
23. As an AI coding agent, I want config keys such as `S3_BUCKET` categorized as storage
    resources, so that cloud storage dependencies are visible without raw values.
24. As an AI coding agent, I want config reads to include confidence and provenance, so that I
    can tell direct literal reads from heuristic wrapper matches.
25. As an AI coding agent, I want config declarations to include provenance, so that I can tell
    whether a key came from an env template, JSON, TOML, YAML, compose file, or source read.
26. As an AI coding agent, I want absence of a config declaration to be explicit, so that a code
    read can be distinguished from a declared key.
27. As an AI coding agent, I want declared-but-unused config keys to be queryable, so that cleanup
    candidates can be identified.
28. As an AI coding agent, I want read-but-undeclared config keys to be queryable, so that
    missing env documentation can be fixed.
29. As an AI coding agent, I want config keys normalized consistently, so that `DATABASE_URL` and
    equivalent dotted config keys have stable graph names.
30. As an AI coding agent, I want raw values omitted by default, so that snippets and graph
    payloads do not leak secrets into model context.
31. As an AI coding agent, I want suspicious value-bearing files such as `.env` to be skipped by
    default, so that real secrets are not indexed.
32. As an AI coding agent, I want safe example values classified rather than stored, so that
    useful shape is preserved without secret exposure.
33. As an AI coding agent, I want dynamic key construction skipped or marked heuristic, so that I
    do not over-trust guessed config links.
34. As an AI coding agent, I want architecture summaries to include config and resource sections,
    so that repo briefings include operational dependencies.
35. As an AI coding agent, I want architecture warnings to distinguish unsupported, supported but
    empty, and stale/unrebuilt states, so that absence is interpreted correctly.
36. As an AI coding agent, I want `seam_schema` to report config/resource capabilities, so that I
    know whether the current index was built with P3.2 support.
37. As an AI coding agent, I want Explorer to show config and resource nodes, so that operational
    dependencies are visible in the graph.
38. As an AI coding agent, I want Explorer filters for config/resource node kinds and edge kinds,
    so that operational edges can be isolated from normal code edges.
39. As an AI coding agent, I want config/resource detail panels to show key, category, source,
    declaration file, reader count, and value safety state, so that I can inspect evidence
    without opening raw config files.
40. As an AI coding agent, I want config/resource nodes to participate in degree filters, so that
    highly shared configuration can be identified.
41. As an AI coding agent, I want route nodes and resource nodes to coexist, so that API endpoints
    can be analyzed together with their environment dependencies.
42. As an AI coding agent, I want old indexes to degrade gracefully, so that P3.2 read paths do
    not crash on pre-config-resource databases.
43. As an AI coding agent, I want config/resource extraction to work without embeddings, so that
    the feature stays deterministic and local.
44. As a human developer, I want `seam graph-search --kind config`, so that I can list environment
    and config contracts from the terminal.
45. As a human developer, I want `seam graph-search --kind resource`, so that I can list runtime
    dependencies from the terminal.
46. As a human developer, I want `seam architecture --section configs`, so that I can see a compact
    summary of config keys and readers.
47. As a human developer, I want `seam architecture --section resources`, so that I can see a
    compact summary of external resource dependencies.
48. As a human developer, I want read-but-undeclared config keys called out, so that onboarding
    docs and env templates can be improved.
49. As a human developer, I want declared-but-unused config keys called out, so that stale config
    can be cleaned up.
50. As a human developer, I want the feature to avoid indexing `.env` secrets, so that I can run
    Seam safely on real repos.
51. As a Seam Explorer user, I want config/resource nodes in the graph, so that deployment
    dependencies are visually discoverable.
52. As a Seam Explorer user, I want edge-kind coloring for `reads_config`, `configures`, and
    optional `uses_resource`, so that operational links stand apart from calls/imports.
53. As a Seam maintainer, I want config/resource extraction in deep modules, so that key parsing,
    source reads, resource classification, and edge linking can be tested in isolation.
54. As a Seam maintainer, I want config/resource metadata stored in additive tables, so that
    future fields do not widen the generic symbol contract.
55. As a Seam maintainer, I want config/resource nodes stored in the normal symbols table, so that
    existing search, snippet, graph search, context, and Explorer surfaces can reuse them.
56. As a Seam maintainer, I want migrations for the new schema, so that old databases continue to
    open safely.
57. As a Seam maintainer, I want parser tests for config files, so that env/JSON/TOML/YAML
    extraction is covered behaviorally.
58. As a Seam maintainer, I want AST tests for Python env reads, so that `os.getenv` and
    `os.environ` patterns are covered.
59. As a Seam maintainer, I want AST tests for TS/JS env reads, so that `process.env` and
    `import.meta.env` patterns are covered.
60. As a Seam maintainer, I want resource-classification tests, so that resource nodes are useful
    without over-modeling every scalar config key.
61. As a Seam maintainer, I want graph-search tests for `kind=config`, `kind=resource`, and new
    edge kinds, so that typed discovery works end to end.
62. As a Seam maintainer, I want schema tests for config/resource counts and capabilities, so that
    agent guidance stays accurate.
63. As a Seam maintainer, I want architecture tests for configs/resources sections, so that the
    existing unsupported placeholders are replaced by honest populated or empty states.
64. As a Seam maintainer, I want web API and generated TypeScript types updated, so that Explorer
    can consume config/resource metadata without loose typing.
65. As a Seam maintainer, I want docs and API contracts updated, so that agents learn to use typed
    tools instead of grepping secrets.
66. As a future test-edge implementer, I want config/resource extraction to follow the P3.1
    route-node pattern, so that graph model expansion remains consistent.
67. As a future no-egress/security-hardening implementer, I want this feature to preserve
    no-network indexing, so that config/resource discovery never probes external services.
68. As a future full-Cypher evaluator, I want config/resource questions covered by typed tools
    first, so that arbitrary graph queries are not added merely to compensate for missing
    product primitives.
69. As a future 3D Explorer implementer, I want config/resource node and edge kinds to be
    explicit, so that operational dependencies can be visually highlighted.
70. As a future installer/distribution implementer, I want config/resource extraction to avoid
    broad auto-write behavior, so that Seam's trust boundary stays clear.

## Implementation Decisions

- Build config/resource support as two deep extraction modules plus one linker:
  - a config-file extractor for safe declaration files;
  - a source config-read extractor for Python and TypeScript/JavaScript;
  - a resource classifier/linker that turns high-confidence config evidence into resource
    nodes and `configures`/optional `uses_resource` edges.
- Keep the modules transport-neutral and testable without CLI, MCP, Web, or watcher code.
- Run config/resource extraction from the indexing pipeline after normal symbols are known, so
  code reads can be attached to the nearest enclosing function/method/class when possible.
- Support non-source config files through a bounded file-discovery path. The normal language
  parser dispatch only covers source files, so P3.2 must explicitly decide how `.env.example`,
  JSON, TOML, and YAML files enter the index without pretending they are normal code files.
- Treat `.env`, `.env.local`, `.env.production`, `.env.development`, and similarly value-bearing
  env files as unsafe by default. They should be skipped unless a later opt-in policy explicitly
  allows redacted indexing.
- Index keys, not values. If value shape is useful, store only a safety classification or
  redacted shape. Do not persist raw secret values in symbols, metadata, search text, docs, logs,
  tests, web payloads, or MCP payloads.
- Config key nodes should be inserted into the existing symbols table with `kind='config'`.
- Resource nodes should be inserted into the existing symbols table with `kind='resource'`.
- Config node names should be deterministic and human-readable, using a stable shape such as
  `CONFIG <NORMALIZED_KEY>`.
- Resource node names should be deterministic and human-readable, using a stable shape such as
  `RESOURCE <CATEGORY> <NAME_OR_KEY>`, with collision handling by file/line/source family.
- Add a dedicated config metadata table keyed to config symbol evidence. It should store
  key/name, normalized key, source family, declaration/read role, file, line, value safety state,
  optional value category, confidence, and provenance.
- Add a dedicated resource metadata table keyed to resource symbol evidence. It should store
  resource name, category, normalized identifier, source family, file, line, confidence, and
  provenance.
- Add `reads_config` and `configures` to the accepted edge vocabulary.
- Add `uses_resource` only if implementation review confirms that the shortcut does not create
  redundant or misleading graph noise. Otherwise keep code-to-resource traversal as a two-hop
  path through `reads_config` and `configures`.
- Existing edges remain string-name-keyed for compatibility with the current graph model.
- Confidence rules should be conservative:
  - `EXTRACTED` for literal env/config reads and keys from explicitly safe declaration files;
  - `INFERRED` for resource category classification from naming conventions;
  - `AMBIGUOUS` when multiple config/resource nodes match the same key evidence.
- Provenance should name the extractor family, such as `env-template-key`,
  `json-config-key`, `toml-config-key`, `yaml-config-key`, `python-os-getenv`,
  `python-os-environ`, `typescript-process-env`, `typescript-import-meta-env`,
  `compose-service`, or `manifest-dependency`.
- Python support should start with `os.getenv`, `os.environ[...]`, `os.environ.get(...)`, and
  simple imported aliases when static evidence is local and unambiguous.
- TypeScript/JavaScript support should start with `process.env.X`, `process.env["X"]`, and
  `import.meta.env.X`.
- Wrapper/config-object support should be narrowly scoped. Only accept literal keys when the
  receiver is named with common config identifiers or is locally bound to a parsed config object.
- JSON/TOML/YAML extraction should flatten object paths into normalized dotted keys while
  preserving the original key path in metadata.
- Dependency manifests should be treated as resource/dependency evidence, not as generic config
  unless the key is an actual runtime setting.
- Docker Compose service names should be resource nodes; environment variable declarations inside
  compose files should be config key declarations without values.
- `seam_schema` should report config/resource support through counts, capabilities, table
  metadata, symbol kind counts, edge kind counts, warnings, and tool guidance.
- `seam_graph_search` should accept `kind='config'`, `kind='resource'`, `edge_kind='reads_config'`,
  `edge_kind='configures'`, and optionally `edge_kind='uses_resource'`; degree filters, previews,
  pagination, confidence filters, and sorting should work without special transport logic.
- `seam_architecture` should replace the current config/resource unsupported warnings with
  populated sections when metadata exists. If the schema exists but no config/resource evidence is
  indexed, it should report an honest empty state.
- Explorer should consume config/resource nodes and edges through existing graph/search APIs where
  possible, with typed detail additions for metadata.
- The MCP, CLI, and Web transports should expose config/resource data through existing tools first.
  A separate `seam_configs` or `seam_resources` tool is out of scope unless implementation proves
  existing surfaces cannot represent the data cleanly.
- The migration must be additive and guarded. Opening old indexes must not crash; config/resource
  tables may be absent and read paths must degrade.
- Full re-index should populate config/resource data. Migration should not attempt to backfill
  config/resource rows from old symbol rows.
- The feature must preserve Seam's local-first trust boundary: no network calls, no dependency
  execution, no service probing, no secrets manager calls, and no live infrastructure discovery.
- Add a configuration gate only if needed for safety or byte-compatibility. If added, it must live
  in the central config module and follow the existing `on`/`off` extraction-time pattern.

## Testing Decisions

- Tests should assert external behavior and graph contracts, not private AST traversal details.
- Good tests should use small fixtures with fake keys and fake placeholder values only. Never add
  realistic secrets, tokens, DSNs, or credentials to fixtures.
- Unit tests should cover the config-file extractor as a deep module:
  - `.env.example` key extraction without value persistence;
  - unsafe `.env` file skipping;
  - JSON key flattening;
  - TOML key flattening;
  - YAML key flattening or graceful degradation if YAML support is unavailable;
  - comment/blank-line handling;
  - duplicate keys and provenance.
- Unit tests should cover the source config-read extractor as a deep module:
  - Python `os.getenv("KEY")`;
  - Python `os.environ["KEY"]`;
  - Python `os.environ.get("KEY")`;
  - TypeScript/JavaScript `process.env.KEY`;
  - TypeScript/JavaScript `process.env["KEY"]`;
  - TypeScript/JavaScript `import.meta.env.KEY`;
  - skipped dynamic keys;
  - nearest enclosing symbol attribution.
- Unit tests should cover the resource classifier/linker:
  - database-like keys;
  - cache/Redis-like keys;
  - queue-like keys;
  - bucket/storage-like keys;
  - dependency/manifest resources;
  - compose service resources;
  - scalar config keys that should not become resource nodes.
- Migration tests should follow existing migration-test style and verify old DBs gain config and
  resource metadata tables while preserving files, symbols, edges, comments, clusters, imports,
  embeddings, routes, and metadata.
- Indexer tests should verify config/resource symbols are inserted, metadata is stored, old rows
  are replaced per file, and new edges are persisted with confidence and provenance.
- Graph-search tests should verify `kind=config`, `kind=resource`, new edge kinds, previews,
  invalid inputs, pagination, degree filters, confidence filters, and old-index warnings.
- Schema tests should verify config/resource counts and capability reporting on populated, empty,
  and old indexes.
- Architecture tests should verify configs/resources sections are populated when metadata exists
  and remain explicitly unsupported/unavailable on old indexes.
- Web API tests should verify config/resource metadata appears in generated schemas or detail
  payloads without breaking existing endpoints.
- Explorer tests should verify config/resource nodes and edges render/filter through existing graph
  behavior. Keep UI assertions focused on visible behavior and typed data plumbing.
- Regression tests should verify existing route, call, import, field, inheritance, graph-search,
  snippet, schema, and architecture behavior remains stable after config/resource support is
  enabled.
- Security-oriented tests should verify raw config values do not appear in symbols, FTS text,
  metadata payloads, graph-search results, architecture summaries, web API responses, MCP
  responses, or logs.
- Validation before merge should include focused tests, full backend gate, frontend typecheck/test
  if web contracts change, OpenAPI type regeneration if web schemas change, and `seam sync` on the
  repository.

## Out of Scope

- Indexing raw secret values by default.
- Reading `.env` or environment-specific value files by default.
- Calling external services, cloud APIs, secrets managers, metadata servers, or databases.
- Executing project code to discover runtime config.
- Full data-flow analysis from config reads through arbitrary variables.
- Dynamic key construction solving across string concatenation, template interpolation, loops, or
  runtime object paths.
- Full Kubernetes, Terraform, Helm, CloudFormation, Pulumi, or service-mesh resource modeling.
  Those may be future infrastructure graph phases.
- OpenAPI, route, or service discovery beyond what P3.1 already indexes.
- A dedicated config/resource MCP tool unless existing typed surfaces prove insufficient.
- Full Cypher support.
- Installer changes, npm shim work, release hardening, or no-egress proof work.
- Test-edge extraction; that belongs to P3.3.
- Exception/raises extraction; that belongs to P3.4.

## Further Notes

- P3.1 established the right pattern for first-class non-code graph nodes: put route/config/resource
  nodes in `symbols`, keep domain-specific fields in metadata tables, and expose the result through
  schema, graph search, architecture, web contracts, and Explorer.
- The main product risk is secret exposure. The implementation should be biased toward missing
  evidence rather than leaking values or inventing links.
- The main graph-quality risk is over-modeling. Not every config key is a resource. Plain scalar
  settings should remain config nodes; only operational dependencies should become resource nodes.
- The main implementation risk is trying to parse every config ecosystem. Start with safe env
  templates, JSON/TOML/YAML flattening, Python env reads, TS/JS env reads, compose services, and
  manifest dependencies. Add deeper ecosystem-specific support only when the typed graph questions
  justify it.
- Existing architecture optional-surface placeholders for configs/resources give the implementation
  a clear read-path target: unsupported should become supported-empty or supported-populated with
  honest warnings.
