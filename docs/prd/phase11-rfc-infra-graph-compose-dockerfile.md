# PRD - Phase 11 RFC: Infra Graph, Docker Compose And Dockerfile First

> Status: ready for agent.
> Created: 2026-07-03.
> GitHub issue: https://github.com/Catafal/seam/issues/288.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/phase11-rfc-roadmap.md`.
> Parent status matrix: `docs/prd/phase11-codebase-memory-roadmap.md`.
> External research baseline: `DeusData/codebase-memory-mcp` at commit
> `9cb3cabf76f5f4ad23caf66f641adff1ef0b67c9`.

## Problem Statement

Seam can now model code structure, route nodes, config keys, resource nodes, test evidence,
exception evidence, and selected protocol edges. That gives agents a useful view of source-code
architecture, but it still leaves a major blind spot: deployment declarations.

From the user's perspective, the unanswered questions are operational rather than purely
source-code oriented:

1. Which local services exist in this repository?
2. Which service builds from which Dockerfile?
3. Which service uses which image?
4. Which ports does a service expose to the host or to other containers?
5. Which Dockerfile stages and base images define the runtime?
6. Which services depend on other services?
7. Which config keys are declared in Docker Compose without leaking values?
8. Which volumes and networks shape the local runtime topology?
9. Which package, route, or config evidence points at the same operational dependency?
10. Which infra files are safe for agents to use as graph evidence?

Today Seam has only the first inch of that model. Docker Compose files are included in the safe
config/resource file set, but the current Compose extractor records service names only. It does
not model `build`, `image`, `ports`, `environment`, `env_file`, `depends_on`, `volumes`, or
`networks` as graph evidence. Dockerfiles are not first-class infra resources at all. As a result,
agents still fall back to grep and manual YAML/Dockerfile reading for deployment topology.

The competitive research makes this gap clear. `codebase-memory-mcp` now advertises
infrastructure-as-code indexing for Dockerfiles, Kubernetes manifests, and Kustomize overlays, with
resource-style graph nodes and cross-references. Its implementation also contains focused infra
helpers for detecting Dockerfiles, Compose-style files, Kubernetes manifests, Kustomize overlays,
secret-like bindings, Dockerfile stages, exposed ports, env keys, build args, and cross-manifest
Kubernetes selector links. That is useful product direction, but Seam should not copy the full
scope at once.

Seam needs an infra graph that stays consistent with its own product boundaries:

- local-first;
- SQLite-backed;
- static indexing only;
- no hidden network calls;
- no execution of project code or containers;
- no secret value persistence;
- typed product surfaces over broad query languages;
- conservative extraction with visible confidence and provenance;
- per-file re-indexing without global graph rewrites.

The immediate problem is therefore narrower than "index all infrastructure." Seam needs a first
infra-graph slice that can answer Docker Compose and Dockerfile topology questions accurately
enough to be useful, without expanding into Kubernetes, Terraform, Helm, runtime probing, or
cross-repo deployment analysis.

## Solution

Add a scoped infra graph layer for Docker Compose and Dockerfile declarations.

The feature should extend the existing config/resource graph foundation rather than introduce a
parallel graph model. Infra evidence should appear as normal graph nodes and edges, with
domain-specific metadata stored in dedicated read models only when the existing `resources` and
`config_keys` tables are insufficient.

The first implementation should index:

1. Docker Compose services.
2. Compose service images.
3. Compose service build contexts.
4. Compose service Dockerfile references.
5. Compose service port declarations.
6. Compose service environment key names, without values.
7. Compose service env-file references, without reading unsafe env files by default.
8. Compose service dependencies from `depends_on`.
9. Compose named volumes and service volume references.
10. Compose named networks and service network references.
11. Dockerfile files as infra resources.
12. Dockerfile stages.
13. Dockerfile base images.
14. Dockerfile exposed ports.
15. Dockerfile build args by key name only.
16. Dockerfile env keys by key name only.

The feature should expose this evidence through existing Seam surfaces first:

1. `seam_schema` should report infra graph capability and counts honestly.
2. `seam_graph_search` should find infra resources and infra edges through typed filters.
3. `seam_architecture` should include a compact infra section when infra evidence exists.
4. `seam_context` and `seam_snippet` should work for returned infra UIDs where possible.
5. MCP and Web API schemas should carry any new typed fields.
6. Explorer should be able to render and filter infra nodes and edges without special cases.
7. Documentation should teach agents that infra graph evidence is static declaration evidence, not
   runtime truth.

This PRD intentionally starts with Docker Compose plus Dockerfile only. Kubernetes, Kustomize,
Helm, Terraform, Pulumi, CloudFormation, OpenAPI-derived service maps, runtime traces, and
cross-repo infra are out of scope. They remain future RFCs after this first node/edge vocabulary
settles.

The intended user workflow is:

1. Run `seam schema --json` and see whether infra graph extraction is supported and populated.
2. Run `seam architecture --section infra --json` to get a deployment declaration briefing.
3. Run graph search for service, image, port, volume, network, Dockerfile, or stage resources.
4. Use previews to move from a service to its build, image, ports, config keys, dependencies,
   volumes, and networks.
5. Use snippets to inspect the exact Compose or Dockerfile declaration line.
6. Treat results as conservative static evidence. If a value is dynamic, omitted, interpolated, or
   unsafe, Seam should mark absence or unknown rather than guessing.

## User Stories

1. As an AI coding agent, I want Seam to list Docker Compose services, so that I can understand
   local runtime topology without grepping YAML.
2. As an AI coding agent, I want each Compose service to be a graph resource, so that I can move
   from service inventory into context, snippets, and graph previews.
3. As an AI coding agent, I want Compose service resources to include provenance, so that I know
   the evidence came from a Compose declaration.
4. As an AI coding agent, I want Compose service resources to include file and line, so that I can
   inspect the source declaration quickly.
5. As an AI coding agent, I want a service-to-image relationship, so that I can tell which
   container image a service runs.
6. As an AI coding agent, I want a service-to-build-context relationship, so that I can tell which
   local path builds a service image.
7. As an AI coding agent, I want a service-to-Dockerfile relationship, so that I can inspect the
   exact Dockerfile used by a Compose service.
8. As an AI coding agent, I want Compose `build` shorthand and object syntax both supported, so
   that common Compose files work.
9. As an AI coding agent, I want Dockerfile references normalized relative to the Compose file and
   build context, so that graph links point to the correct local declaration.
10. As an AI coding agent, I want unresolved Dockerfile references represented as unknown rather
    than guessed, so that stale or dynamic Compose files do not create false links.
11. As an AI coding agent, I want Compose ports represented as resources, so that exposed local
    service boundaries are visible.
12. As an AI coding agent, I want host and container port parts normalized conservatively, so that
    `8080:80` can be understood without over-modeling protocol details.
13. As an AI coding agent, I want port protocol suffixes such as `/tcp` and `/udp` preserved when
    known, so that service surfaces are not collapsed incorrectly.
14. As an AI coding agent, I want dynamic or interpolated ports marked unresolved or skipped, so
    that `\${PORT}:80` does not become a fake fixed endpoint.
15. As an AI coding agent, I want Compose environment keys indexed without values, so that service
    config contracts are visible safely.
16. As an AI coding agent, I want list-style and mapping-style Compose environment declarations
    both handled, so that common Compose syntax is covered.
17. As an AI coding agent, I want Compose environment values redacted or omitted, so that secrets
    do not enter symbols, FTS, metadata, MCP output, Web output, or logs.
18. As an AI coding agent, I want Compose `env_file` references indexed as file references without
    reading unsafe env files by default, so that agents know config may come from a file without
    leaking it.
19. As an AI coding agent, I want safe env template files referenced by Compose to continue using
    the existing no-secret config extraction rules, so that declared keys can be connected when
    safe.
20. As an AI coding agent, I want service `depends_on` declarations represented as graph edges, so
    that startup dependency topology is visible.
21. As an AI coding agent, I want short-form and long-form `depends_on` supported, so that Compose
    v2-style and older-style files work.
22. As an AI coding agent, I want `depends_on` conditions recorded as metadata when present, so
    that healthcheck-gated dependencies can be distinguished from simple ordering.
23. As an AI coding agent, I want named volumes represented as resources, so that persistent data
    dependencies are visible.
24. As an AI coding agent, I want service volume references linked to named volumes, so that I can
    see which services share state.
25. As an AI coding agent, I want bind mounts represented only when the path is local and safe, so
    that host path evidence is useful without indexing outside the repo.
26. As an AI coding agent, I want external volumes marked as external resources, so that Seam does
    not imply the volume is declared in the repo.
27. As an AI coding agent, I want named networks represented as resources, so that service network
    boundaries are visible.
28. As an AI coding agent, I want service network references linked to named networks, so that
    communication topology can be inspected.
29. As an AI coding agent, I want default Compose networks handled without inventing noisy nodes,
    so that the graph stays readable.
30. As an AI coding agent, I want Dockerfiles indexed as infra resources, so that container build
    declarations are discoverable even without Compose.
31. As an AI coding agent, I want Dockerfile stages represented, so that multi-stage builds are
    visible.
32. As an AI coding agent, I want Dockerfile base images represented, so that supply/runtime
    foundations are visible.
33. As an AI coding agent, I want stage aliases from `FROM image AS name` preserved, so that build
    stage references are understandable.
34. As an AI coding agent, I want Dockerfile `COPY --from=<stage>` references linked where static,
    so that multi-stage build flow can be inspected.
35. As an AI coding agent, I want Dockerfile `EXPOSE` declarations represented as port resources,
    so that container-level service surfaces are visible.
36. As an AI coding agent, I want Dockerfile `ARG` names indexed without values, so that build-time
    configuration is visible safely.
37. As an AI coding agent, I want Dockerfile `ENV` names indexed without values, so that runtime
    environment expectations are visible safely.
38. As an AI coding agent, I want Dockerfile `WORKDIR`, `USER`, `CMD`, `ENTRYPOINT`, and
    `HEALTHCHECK` handled only if they can be represented without creating noisy or sensitive
    graph data, so that the first slice stays focused.
39. As an AI coding agent, I want Dockerfile parser failures to skip only the broken file, so that
    one unusual Dockerfile does not abort indexing.
40. As an AI coding agent, I want Compose parser failures to skip only the broken file, so that
    one invalid YAML file does not abort indexing.
41. As an AI coding agent, I want YAML aliases and anchors to be handled conservatively, so that
    common files do not crash and unsupported dynamic merges do not create guessed facts.
42. As an AI coding agent, I want profiles, extension fields, and vendor-specific Compose keys
    ignored unless explicitly supported, so that the graph stays stable.
43. As an AI coding agent, I want generated or vendor infra files excluded by default when they are
    under normal ignored directories, so that the graph does not fill with third-party topology.
44. As an AI coding agent, I want infra graph results to include confidence, so that exact
    declarations and inferred relationships can be distinguished.
45. As an AI coding agent, I want infra graph results to include provenance, so that I know whether
    evidence came from Compose, Dockerfile, config extraction, or a linker.
46. As an AI coding agent, I want `seam_schema` to report infra support separately from generic
    resource support, so that I can tell whether deployment topology extraction exists.
47. As an AI coding agent, I want `seam_schema` to report infra counts, so that empty infra graph
    results are not confused with unsupported tooling.
48. As an AI coding agent, I want `seam_architecture` to have an infra section, so that repository
    briefings include deployment declarations.
49. As an AI coding agent, I want `seam_architecture` infra output to summarize services, images,
    ports, Dockerfiles, volumes, networks, and dependencies, so that I can inspect topology in one
    compact response.
50. As an AI coding agent, I want `seam_architecture` warnings to distinguish unsupported,
    supported-empty, stale, and populated states, so that absence is interpreted correctly.
51. As an AI coding agent, I want graph search to filter infra resources by category, so that I can
    list only services, ports, images, volumes, networks, Dockerfiles, or stages.
52. As an AI coding agent, I want graph search previews from a service to show build, image, ports,
    env keys, dependencies, volumes, and networks, so that I do not need multiple broad searches.
53. As an AI coding agent, I want graph search previews from an image to show which services use
    it, so that shared runtime images are visible.
54. As an AI coding agent, I want graph search previews from a port to show which services expose
    it, so that local port conflicts can be detected manually.
55. As an AI coding agent, I want graph search previews from a volume to show sharing services, so
    that state coupling is visible.
56. As an AI coding agent, I want graph search previews from a network to show participating
    services, so that communication boundaries can be inspected.
57. As an AI coding agent, I want graph search previews from a Dockerfile to show which services
    build from it, so that build ownership is clear.
58. As an AI coding agent, I want infra evidence to be excluded from default code-impact traversal
    until explicitly designed, so that risk reports are not polluted by deployment declarations.
59. As an AI coding agent, I want infra evidence available through explicit graph-search and
    architecture sections, so that it is discoverable without changing existing impact semantics.
60. As an AI coding agent, I want old indexes to degrade gracefully, so that pre-infra databases do
    not crash schema, architecture, graph search, MCP, Web, or Explorer.
61. As an AI coding agent, I want a full re-index to populate infra evidence, so that migrations do
    not attempt unsafe backfills from old generic symbols.
62. As an AI coding agent, I want per-file re-indexing to replace infra evidence for changed
    Compose or Dockerfile files, so that watcher correctness is preserved.
63. As an AI coding agent, I want deleting an infra file to remove its infra nodes, metadata, and
    edges, so that stale deployment declarations do not linger.
64. As a human developer, I want to run `seam graph-search --kind resource` and see service
    resources, so that local topology is visible from the terminal.
65. As a human developer, I want to run an architecture infra section, so that I can quickly review
    deployment shape before changing code.
66. As a human developer, I want service dependency edges to make local startup order visible, so
    that debugging Compose issues is faster.
67. As a human developer, I want exposed ports visible without opening every Dockerfile and Compose
    file, so that local conflicts are easier to notice.
68. As a human developer, I want build contexts visible, so that I can understand which source
    directory produces which container.
69. As a human developer, I want Dockerfile base images visible, so that upgrade and security
    review work has structured starting points.
70. As a human developer, I want the feature to avoid indexing real `.env` files, so that I can run
    Seam safely on real projects.
71. As a Seam Explorer user, I want infra resources visible in the graph, so that deployment nodes
    can be inspected alongside code nodes.
72. As a Seam Explorer user, I want infra edges visually distinguishable from call/import/test
    edges, so that operational links do not look like code execution.
73. As a Seam Explorer user, I want infra filters, so that I can isolate services, images, ports,
    volumes, networks, and config links.
74. As a Seam Explorer user, I want service detail panels to show build, image, ports, env keys,
    dependencies, volumes, networks, file, line, confidence, and provenance, so that I can trust
    the evidence.
75. As a Seam maintainer, I want infra parsing behind deep module interfaces, so that Compose
    parsing, Dockerfile parsing, normalization, and graph linking can be tested in isolation.
76. As a Seam maintainer, I want to reuse existing config/resource safety rules, so that secret
    handling stays consistent.
77. As a Seam maintainer, I want additive schema changes only if existing metadata cannot express
    infra detail cleanly, so that schema churn stays justified.
78. As a Seam maintainer, I want any new edge kinds documented in the central edge vocabulary, so
    that CLI, MCP, Web, Explorer, and docs stay coherent.
79. As a Seam maintainer, I want any new node categories documented in schema and architecture
    output, so that agents know how to query them.
80. As a Seam maintainer, I want fixture tests for Compose and Dockerfile files, so that behavior
    is external and reproducible.
81. As a Seam maintainer, I want no tests with real-looking secrets, so that fixtures do not teach
    unsafe patterns.
82. As a Seam maintainer, I want tests that assert raw values are not persisted, so that regression
    coverage protects the trust boundary.
83. As a Seam maintainer, I want migration tests if metadata tables change, so that old databases
    stay readable.
84. As a Seam maintainer, I want architecture tests for populated and empty infra sections, so that
    warnings stay honest.
85. As a Seam maintainer, I want graph-search tests for infra resources and edges, so that typed
    discovery works end to end.
86. As a Seam maintainer, I want Web schema tests if API contracts change, so that generated types
    remain aligned.
87. As a future Kubernetes implementer, I want Docker Compose and Dockerfile vocabulary settled
    first, so that Kubernetes can reuse service, image, port, config, and dependency concepts.
88. As a future graph artifact implementer, I want infra evidence to be explicit graph facts, so
    that exported indexes can include deployment topology with clear schema semantics.
89. As a future cross-repo implementer, I want single-repo infra identity stable first, so that
    cross-repo deployment analysis does not compound ambiguity.
90. As a future runtime-trace implementer, I want static infra declarations separated from runtime
    evidence, so that traces can validate or contradict declarations later.

## Implementation Decisions

- Treat this as RFC 2 from the Phase 11 follow-up roadmap: Infra Graph after protocol-edge work.
- Scope the first implementation to Docker Compose and Dockerfile declarations only.
- Reuse the shipped config/resource graph foundation wherever possible.
- Keep infra evidence local and static: no Docker invocation, no image pulls, no container
  inspection, no Compose execution, no build execution, no network calls, no secrets-manager calls.
- Prefer false negatives over false positives. If a field is dynamic, interpolated, merged through
  unsupported YAML features, or outside the selected root, skip it or mark it unknown.
- Keep raw values out of the index. Persist key names, normalized identifiers, categories,
  redacted safety shape, confidence, and provenance only.
- Model Compose services as resource nodes with a service category and Compose provenance.
- Model Dockerfiles as resource nodes with a Dockerfile category and Dockerfile provenance.
- Model Dockerfile stages as resource nodes only when the stage identity is static.
- Model images as resource nodes. Local build outputs and remote image names should be
  distinguished by category or metadata because they have different trust semantics.
- Model ports as resource nodes or dedicated infra metadata only if the representation supports
  host/container/protocol without stuffing lossy strings into generic names.
- Model named volumes and networks as resource nodes because they are shared operational resources.
- Model config keys found in Compose and Dockerfile declarations using the existing config node
  pattern, not a new infra-only key model.
- Do not read real `.env` files by default. Compose `env_file` should be recorded as a reference;
  safe env template files may continue through existing config extraction rules.
- Do not store Compose environment values or Dockerfile `ENV` values. Store only key names and
  safe value-state metadata such as omitted, key-only, redacted, or unknown.
- Add infra-specific metadata only if the existing `resources` and `config_keys` tables cannot
  express required fields such as service role, port host/container/protocol, Dockerfile stage
  alias, or dependency condition.
- If a new metadata table is added, make it additive, guarded by migration, and keyed to resource
  symbol evidence in the same style as existing domain metadata tables.
- Keep existing name-keyed edges for compatibility with independent per-file re-indexing.
- Do not introduce resolved edge IDs in this RFC.
- Add new edge kinds only where existing `configures` is semantically insufficient.
- Candidate edge vocabulary should be deliberately small:
  - service uses image;
  - service builds from Dockerfile or build context;
  - service exposes port;
  - service depends on service;
  - service mounts volume;
  - service joins network;
  - Dockerfile stage uses base image;
  - Dockerfile stage copies from another stage;
  - service or stage declares config key.
- If the implementation can express some relationships through existing `uses`, `configures`, or
  `reads_config` without ambiguity, prefer reuse over edge-kind expansion.
- Keep infra edges out of default impact traversal until a separate impact policy is designed.
- Surface infra evidence explicitly through schema, graph-search, architecture, and Explorer.
- Add `infra` as an architecture section if that is cleaner than overloading the existing
  `resources` section.
- The existing `resources` architecture section should remain useful for generic resources; the
  new infra section should summarize deployment topology.
- Schema output should distinguish "resource support exists" from "infra graph extraction exists."
- Graph-search should support filtering by resource category, source family, provenance, or an
  equivalent typed filter so agents can query services without matching every dependency package.
- Explorer should consume infra evidence through existing graph APIs first. A dedicated infra
  endpoint is out of scope unless existing surfaces cannot provide the required data compactly.
- CLI, MCP, and Web contracts must stay aligned; any new fields need API contract documentation and
  generated web type updates.
- Docker Compose parsing should support common service keys first: `image`, `build`, `ports`,
  `environment`, `env_file`, `depends_on`, `volumes`, and `networks`.
- Compose `build` should handle string shorthand and object syntax with `context` and `dockerfile`.
- Compose `depends_on` should handle list syntax and mapping syntax.
- Compose `environment` should handle mapping syntax, list `KEY=value` syntax, and list `KEY`
  syntax. Values must be discarded.
- Compose `ports` should handle short string syntax conservatively. Long object syntax should be
  supported if it can be normalized cleanly.
- Compose `volumes` should distinguish named volumes from bind mounts when the syntax makes that
  clear.
- Compose `networks` should distinguish named networks from implicit defaults when clear.
- Dockerfile parsing should support `FROM`, optional `AS`, `EXPOSE`, `ARG`, `ENV`, and static
  `COPY --from`.
- Dockerfile `RUN`, `CMD`, `ENTRYPOINT`, `HEALTHCHECK`, `USER`, and `WORKDIR` should not create
  graph edges in the first version unless implementation review proves a compact, non-sensitive
  representation.
- Dockerfile line continuations should be handled for the supported instructions when feasible;
  unsupported multiline forms should be skipped rather than guessed.
- Generated or ignored directories should continue to be excluded by normal project walking rules.
- The first slice should not add Kubernetes, Kustomize, Helm, Terraform, HCL, CloudFormation,
  Pulumi, or service-mesh parsing.
- The first slice should not add runtime trace ingest, Docker daemon integration, image scanning,
  SBOM extraction, or vulnerability analysis.
- Documentation should state clearly that infra graph evidence is declaration evidence, not a live
  runtime inventory.

## Testing Decisions

- Tests should assert external behavior and persisted graph contracts, not private parser helper
  details.
- Good tests should use small fixture projects with fake service names and placeholder values only.
- Test fixtures must not include real-looking secrets, production hostnames, credentials, tokens,
  private registry names, or real internal network names.
- Unit tests should cover Compose service extraction from minimal valid Compose files.
- Unit tests should cover Compose `image` extraction and service-to-image linking.
- Unit tests should cover Compose `build` string shorthand.
- Unit tests should cover Compose `build.context` and `build.dockerfile` object syntax.
- Unit tests should cover Compose `ports` short syntax, including host/container/protocol
  normalization.
- Unit tests should cover Compose `ports` dynamic interpolation being skipped or marked unknown.
- Unit tests should cover Compose `environment` mapping syntax with values discarded.
- Unit tests should cover Compose `environment` list syntax with values discarded.
- Unit tests should cover Compose `env_file` reference handling without reading unsafe env files.
- Unit tests should cover Compose `depends_on` list syntax.
- Unit tests should cover Compose `depends_on` mapping syntax and optional condition metadata.
- Unit tests should cover named volume declarations and service volume references.
- Unit tests should cover bind mounts being skipped or represented only when safely inside the
  project root.
- Unit tests should cover named network declarations and service network references.
- Unit tests should cover unsupported Compose extension fields being ignored.
- Unit tests should cover YAML parse failure or unsupported constructs degrading gracefully.
- Unit tests should cover Dockerfile resource extraction from a minimal Dockerfile.
- Unit tests should cover Dockerfile `FROM` base image extraction.
- Unit tests should cover Dockerfile stage alias extraction.
- Unit tests should cover Dockerfile multi-stage `COPY --from` links when static.
- Unit tests should cover Dockerfile `EXPOSE` port extraction.
- Unit tests should cover Dockerfile `ARG` key extraction without values.
- Unit tests should cover Dockerfile `ENV` key extraction without values.
- Unit tests should cover Dockerfile secret-looking `ARG` or `ENV` keys being omitted or redacted
  according to the shared safety policy.
- Unit tests should cover Dockerfile parser resilience for comments, blank lines, lowercase or
  mixed-case instructions, and unsupported instructions.
- Indexer tests should verify infra symbols, metadata, and edges are persisted on full indexing.
- Indexer tests should verify re-indexing a changed Compose file replaces old infra evidence.
- Indexer tests should verify deleting an infra file removes its infra evidence.
- Schema tests should verify infra capabilities and counts for populated, empty, and old indexes.
- Graph-search tests should verify resource category filtering or equivalent service/image/port
  discovery.
- Graph-search tests should verify previews for service relationships.
- Graph-search tests should verify invalid or unavailable infra filters degrade clearly.
- Architecture tests should verify the infra section for populated repos.
- Architecture tests should verify supported-empty output for repos without Compose or Dockerfile
  files.
- Architecture tests should verify old-index behavior if schema changes.
- Web schema tests should be added if API response models change.
- Explorer tests should verify infra nodes and edges are renderable and filterable if UI contracts
  change.
- Security-oriented tests should search stored text across symbols, FTS, metadata, edges, schema,
  architecture output, graph-search output, MCP-like payloads, and Web payloads to prove raw values
  are absent.
- Regression tests should verify existing config/resource extraction still works for env templates,
  JSON/TOML/YAML config, package manifests, and source config reads.
- Regression tests should verify existing route, HTTP-call, exception, test-edge, schema,
  architecture, and graph-search behavior is not broken.
- Validation before merge should include focused backend tests, full backend gate, web typecheck and
  tests if contracts change, OpenAPI type regeneration if Web schemas change, `seam sync`, and
  `seam changes --json`.

## Out of Scope

- Kubernetes manifest parsing.
- Kustomize overlay parsing.
- Helm chart parsing.
- Terraform, HCL, CloudFormation, Pulumi, CDK, or service-mesh parsing.
- Docker daemon calls.
- Docker Compose execution.
- Docker image pulls.
- Container inspection.
- SBOM generation.
- Vulnerability scanning.
- Runtime trace ingestion.
- OpenAPI fetching.
- Service probing.
- Network discovery.
- Reading real `.env` files by default.
- Secret value extraction.
- Persisting raw environment values.
- Persisting raw Dockerfile `ENV` values.
- Persisting raw Compose environment values.
- Full YAML anchor and merge resolution if it requires broad YAML semantics beyond the simple safe
  subset.
- Cross-repo deployment topology.
- Resolved edge IDs.
- Full Cypher.
- A dedicated infra MCP tool unless existing typed surfaces cannot represent the data.
- Changing default impact traversal to include infra edges.
- Installer, release, or package-manager changes.
- Graph artifact export/import.

## Further Notes

- What is next on the roadmap: RFC 1 covered protocol edges, starting with HTTP calls. The next
  logical roadmap item is RFC 2, Infra Graph, starting with Docker Compose and Dockerfile. After
  this PRD, the remaining roadmap RFCs are graph artifact export/import and cross-repo analysis.
- Current Seam state: schema v16 is fresh in this checkout, config/resource extraction is shipped,
  route nodes are shipped, and `http_calls` is supported but empty in the current Seam repository
  snapshot.
- Current Seam gap: Compose extraction currently records service names only. Dockerfile extraction
  is not first-class infra graph evidence.
- External research signal: `codebase-memory-mcp` advertises Dockerfile, Kubernetes, and Kustomize
  infra indexing. Its latest inspected source includes Dockerfile detection/parsing, secret-like
  binding filters, Kubernetes manifest detection, Kustomize module linking, and Kubernetes
  Service-to-workload selector matching. Seam should absorb the product idea, not the whole scope.
- The product risk is secret exposure. This feature should miss evidence rather than persist values
  that may be credentials, internal endpoints, or environment-specific runtime details.
- The graph-quality risk is over-modeling. Compose and Dockerfile declarations are not runtime
  truth; they are static declarations that may be overridden by profiles, environment, CLI flags,
  or orchestration systems.
- The implementation risk is vocabulary sprawl. The first implementation should keep edge kinds and
  metadata minimal, then expand only when user questions prove the need.
- The recommended deep modules are:
  - a Compose declaration extractor;
  - a Dockerfile declaration extractor;
  - an infra normalizer for service names, image names, ports, paths, volumes, and networks;
  - a graph linker that converts extracted declarations into resource/config symbols, metadata,
    and edges.
- The recommended first acceptance test is an end-to-end fixture with one Compose file, one
  Dockerfile, two services, one dependency, one image, one build context, one exposed port, one
  env key, one named volume, and one network. Schema, graph-search, architecture, and stored-text
  safety assertions should all pass against that fixture.
