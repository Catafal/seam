# PRD - Phase 11 RFC: Cross-Repo Analysis

> Status: ready for agent.
> Created: 2026-07-04.
> GitHub issue: https://github.com/Catafal/seam/issues/302.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/phase11-rfc-roadmap.md`.
> Parent status matrix: `docs/prd/phase11-codebase-memory-roadmap.md`.
> Previous roadmap slices: protocol edges, infra graph, and graph artifact lifecycle.

## Problem Statement

Seam is intentionally excellent at one repository at a time. A Seam index lives under one
project root, read paths open one SQLite database, and query results are relativized against one
repository. That boundary is one of Seam's strengths: it keeps indexing local, query tools
read-only, and source exposure bounded.

Real products often do not fit inside one repository. A frontend can live in one checkout, an API
server in another, shared packages in a third, and infrastructure declarations in a fourth. The
highest-value questions become cross-repository questions:

1. If this backend route changes, which frontend repo calls it?
2. If this shared package symbol changes, which service repos depend on it?
3. Which repos contain protocol callers for this route, queue, event, or API client?
4. Which infra repo declares the service, image, port, or config key used by another repo?
5. Which repos are stale, unindexed, or on different commits when an answer is produced?
6. Which answers are single-repo facts and which are cross-repo evidence?
7. How does an agent ask cross-repo questions without accidentally exposing unrelated local
   checkouts?

Today Seam has pieces that can help, but no product-level cross-repo model. Repository identity
exists for graph artifact compatibility. Index artifacts can be exported/imported explicitly.
Protocol edges, infra resources, config keys, route nodes, and graph search are typed facts inside
one index. But there is no workspace registry, no multi-index query context, no cross-repo edge
provenance, and no permission model for combining indexes.

The user-facing risk is ambiguity. If an agent runs Seam in the backend repo and sees no local
callers for a route, that does not mean the route is unused. It may mean the callers live in a
frontend repo that Seam was never asked to inspect. Conversely, if Seam silently scans sibling
folders or auto-loads every nearby `.seam` directory, it can expose unrelated projects, private
client work, personal experiments, or credentials-adjacent file paths to the current agent session.

The goal is to make cross-repo intelligence explicit, local, bounded, and auditable.

## Solution

Add a local cross-repo workspace layer that lets users explicitly register multiple already-indexed
repositories and ask opt-in questions across them.

The first implementation should be a read-only federation of local Seam indexes, not a merged mega
database. Each repository keeps its own `.seam/seam.db`, schema version, freshness status, git
identity, root path, and trust boundary. The workspace stores a small registry that says which
repos are allowed to participate together, what human-readable alias each repo has, and what
repository identity was observed when it was registered.

The feature should answer cross-repo questions by opening registered indexes in read-only mode,
checking schema compatibility and freshness, then running bounded typed queries across them. Cross
repo evidence should be marked as cross-repo evidence everywhere it appears.

The first user workflow should be:

1. Initialize or fetch indexes in each repo independently.
2. From one chosen root, create a workspace registry.
3. Explicitly add repos to that workspace by path and alias.
4. Inspect workspace status and see each repo's schema, freshness, git identity, and index path.
5. Run cross-repo graph search or impact-style queries with `--workspace` or equivalent opt-in.
6. Receive results grouped by repo, with stale/unavailable repos called out.
7. Keep default single-repo commands unchanged unless cross-repo mode is explicitly requested.

This PRD covers the first cross-repo slice only:

1. Explicit local workspace registry.
2. Multi-index read-only status and schema compatibility checks.
3. Opt-in cross-repo search for routes, HTTP calls, resources, config keys, and symbols.
4. Opt-in cross-repo impact for a small set of stable edge families.
5. Clear provenance, freshness, and repo identity in every result.

This PRD does not include a remote daemon, hosted index service, automatic sibling-directory
discovery, background watching across many repos, cross-repo write tools, shared auth server,
runtime tracing, or CI-wide dependency graph ingestion.

## User Stories

1. As an AI coding agent, I want to know whether a result came from the current repo or another
   registered repo, so that I do not confuse local evidence with cross-repo evidence.
2. As an AI coding agent, I want cross-repo analysis to be opt-in, so that normal single-repo
   answers stay bounded.
3. As an AI coding agent, I want no sibling directories scanned automatically, so that unrelated
   local checkouts are not exposed.
4. As an AI coding agent, I want a workspace status command, so that I can see which repos are
   registered before asking cross-repo questions.
5. As an AI coding agent, I want every registered repo to have an alias, so that answers are
   readable without leaking long absolute paths by default.
6. As an AI coding agent, I want absolute paths hidden unless explicitly requested, so that output
   can be shared more safely.
7. As an AI coding agent, I want stale repo status shown per repo, so that I can discount answers
   from stale indexes.
8. As an AI coding agent, I want schema compatibility shown per repo, so that unsupported indexes
   are skipped instead of crashing a query.
9. As an AI coding agent, I want missing indexes reported per repo, so that I know which checkout
   needs `seam init`, `seam sync`, `seam fetch`, or `seam import-index`.
10. As an AI coding agent, I want repo identity recorded when a repo is registered, so that moved
    or replaced checkouts can be detected.
11. As an AI coding agent, I want git remote and HEAD shown when available, so that I can compare
    repo states across a workspace.
12. As an AI coding agent, I want non-git repos supported with a local root fingerprint fallback,
    so that local-only projects can still participate explicitly.
13. As an AI coding agent, I want cross-repo answers grouped by repo, so that I can plan changes in
    the right checkout.
14. As an AI coding agent, I want cross-repo answers to include file, line, UID, confidence, and
    provenance, so that I can follow up with context or snippets.
15. As an AI coding agent, I want cross-repo route caller discovery, so that a backend route change
    can reveal frontend callers in another repo.
16. As an AI coding agent, I want cross-repo HTTP call discovery to require static protocol
    evidence, so that Seam does not guess from arbitrary strings.
17. As an AI coding agent, I want cross-repo config/resource discovery, so that operational
    dependencies shared across repos are visible.
18. As an AI coding agent, I want cross-repo infra discovery to remain declaration-based, so that
    runtime topology is not overclaimed.
19. As an AI coding agent, I want cross-repo symbol search, so that shared package symbols can be
    found across registered repos.
20. As an AI coding agent, I want cross-repo impact to be narrower than normal grep, so that only
    typed graph evidence participates.
21. As an AI coding agent, I want cross-repo impact to exclude semantic/similarity evidence by
    default, so that discovery signals do not become dependency facts.
22. As an AI coding agent, I want cross-repo impact to mark unresolved or ambiguous evidence, so
    that I know where manual verification is required.
23. As an AI coding agent, I want cross-repo results to include warnings for skipped repos, so that
    missing data is visible.
24. As an AI coding agent, I want bounded limits per repo and globally, so that MCP and CLI output
    stay compact.
25. As an AI coding agent, I want a cross-repo query to fail closed when the workspace registry is
    invalid, so that corrupted metadata does not produce misleading answers.
26. As an AI coding agent, I want cross-repo mode to be disabled for default MCP tools unless the
    tool request explicitly selects a workspace, so that tool calls remain predictable.
27. As an AI coding agent, I want MCP responses to include workspace metadata, so that downstream
    agents know what boundary they crossed.
28. As an AI coding agent, I want Web/Explorer to show workspace mode distinctly, so that a graph
    view does not silently mix repos.
29. As an AI coding agent, I want Explorer to color or group nodes by repo, so that cross-repo
    topology remains legible.
30. As an AI coding agent, I want snippets from another repo to require that the repo is registered,
    so that source retrieval follows the same permission boundary.
31. As an AI coding agent, I want snippet output to include the repo alias, so that copied context
    remains attributable.
32. As an AI coding agent, I want a route in one repo and HTTP callers in another repo to be linked
    only when method and normalized path match, so that route matching remains conservative.
33. As an AI coding agent, I want protocol matching to work even when no server repo is selected as
    "primary", so that frontend-first workflows can still find candidate backend routes.
34. As an AI coding agent, I want unmatched external HTTP calls to remain unresolved, so that Seam
    does not invent repo edges.
35. As an AI coding agent, I want cross-repo config/resource matching to use normalized keys and
    categories, so that `DATABASE_URL`-style evidence can be compared safely.
36. As an AI coding agent, I want config values excluded from cross-repo analysis, so that secrets
    do not leak across a workspace.
37. As an AI coding agent, I want infra resources matched by safe identifiers only, so that host
    paths and dynamic declarations do not create trusted cross-repo facts.
38. As a human developer, I want to register a frontend and backend repo once, so that future route
    impact checks can consider both.
39. As a human developer, I want to list workspace repos, so that I can audit what the current
    agent is allowed to inspect.
40. As a human developer, I want to remove a repo from a workspace, so that old or unrelated
    checkouts stop participating.
41. As a human developer, I want workspace registration to write only to an explicit Seam-owned
    metadata file, so that setup is reversible.
42. As a human developer, I want no changes written into child repos when registering them, so that
    adding a repo does not mutate someone else's checkout.
43. As a human developer, I want moved repo paths to show as missing or moved, so that stale
    registry entries are easy to fix.
44. As a human developer, I want cross-repo status to recommend `seam sync` for stale repos, so
    that repairs are obvious.
45. As a human developer, I want cross-repo status to recommend artifact import/fetch only when an
    index is missing, so that indexing strategy remains explicit.
46. As a human developer, I want cross-repo output to be stable JSON, so that scripts can consume
    it safely.
47. As a human developer, I want CLI text output to be readable and grouped, so that ad hoc use is
    practical.
48. As a Seam maintainer, I want a workspace registry module with a small public interface, so that
    registry validation is isolated and testable.
49. As a Seam maintainer, I want a multi-index reader module with a small public interface, so that
    cross-repo tools do not duplicate connection and compatibility logic.
50. As a Seam maintainer, I want a cross-repo matcher module with pure functions, so that protocol
    and resource matching can be tested without CLI/MCP/Web code.
51. As a Seam maintainer, I want workspace query handlers to compose existing single-repo query
    handlers where possible, so that behavior stays consistent.
52. As a Seam maintainer, I want cross-repo code to open indexes read-only, so that query paths
    cannot migrate or mutate registered repos.
53. As a Seam maintainer, I want unsupported schema versions skipped with warnings, so that older
    indexes do not crash the workspace.
54. As a Seam maintainer, I want newer schema versions rejected or skipped explicitly, so that
    unknown graph semantics are not misread.
55. As a Seam maintainer, I want cross-repo tests to use temporary fixture repos, so that behavior
    is reproducible and isolated.
56. As a Seam maintainer, I want no test fixture to contain real-looking secrets, so that the test
    suite preserves no-secret discipline.
57. As a Seam maintainer, I want path-redaction tests, so that aliases are the default external
    representation.
58. As a Seam maintainer, I want stale-index tests per repo, so that result warnings remain honest.
59. As a Seam maintainer, I want missing-index tests per repo, so that one broken repo does not
    fail an entire workspace unless the command requires fail-closed behavior.
60. As a Seam maintainer, I want generated Web types updated only if Web contracts change, so that
    frontend drift is visible.
61. As a future graph-artifact user, I want cross-repo workspaces to accept imported indexes, so
    that a large workspace can bootstrap from artifacts without re-indexing every repo locally.
62. As a future CI user, I want this design to leave room for CI-produced per-repo artifacts, so
    that cross-repo workspaces can be assembled repeatably later.
63. As a future remote-workspace designer, I want the local workspace model settled first, so that
    remote services do not inherit ambiguous permissions.
64. As a future runtime-trace implementer, I want static cross-repo evidence separated from runtime
    evidence, so that traces can validate static links later.
65. As a future semantic-search implementer, I want semantic discovery excluded from dependency
    impact by default, so that cross-repo answers remain trustworthy.

## Implementation Decisions

- Treat this as the final follow-up RFC from the Phase 11 roadmap: Cross-Repo Analysis after
  protocol edges, infra graph, and graph artifact lifecycle.
- Keep the first implementation local-only. No hosted index, remote daemon, LAN discovery, cloud
  sync, or background network access.
- Require explicit workspace registration. No automatic sibling scanning, no recursive parent
  scanning, and no auto-loading every `.seam` directory under a folder.
- Store a workspace registry as Seam-owned metadata. The registry should include workspace version,
  workspace id, repo alias, repo root path, index path, observed repository identity, creation time,
  and last validation summary.
- Prefer aliases in user-facing output. Absolute paths may appear in debug/status output only when
  explicitly requested or when needed to repair a registry.
- Keep each repo's active index separate. The first implementation should federate read-only
  connections rather than merge all rows into one database.
- Open registered indexes with query-only read connections. Cross-repo query paths must not run
  migrations, sync, artifact import, or index writes.
- Validate every repo before query execution: index exists, schema can be described, schema is not
  newer than the current reader, and freshness is known.
- Report repo validation state in every cross-repo result: ready, stale, missing index, unreadable
  index, schema too old, schema too new, path moved, or identity changed.
- Treat stale repos as queryable with warnings by default. Commands may add a strict mode that
  fails if any repo is stale.
- Treat missing or unreadable indexes as skipped with warnings for broad status/search commands.
  Targeted commands that name one repo may fail with a clear error.
- Reuse existing repository identity helpers from artifact work where possible: git remote, git
  HEAD, and non-git root fingerprint.
- Do not require every registered repo to share a git remote. Cross-repo workspaces are about
  local product topology, not one monorepo origin.
- Detect identity drift. If a registered path now points at a different remote or fingerprint, mark
  it as changed and exclude it from query results unless an explicit refresh command accepts the
  new identity.
- Do not write into registered child repos when adding them to a workspace. Registration belongs to
  the workspace root or an explicitly selected workspace metadata directory.
- Do not add cross-repo evidence into default single-repo indexes in the first version. Cross-repo
  links are query-time federated facts, not persisted into each repo's `.seam/seam.db`.
- If cross-repo links are later persisted, they need their own RFC because persistence changes
  freshness, deletion, and exposure semantics.
- Keep default `seam query`, `seam graph-search`, `seam impact`, `seam context`, and MCP tool
  behavior single-repo unless cross-repo/workspace mode is explicitly selected.
- Add cross-repo CLI commands or flags only where the existing product language remains clear.
  Prefer a small workspace command group for registration/status and opt-in flags for query
  surfaces.
- Do not add a broad graph query language. Keep typed product surfaces: schema/status, graph
  search, route caller discovery, resource/config discovery, impact, context/snippet.
- Include repo alias and repo identity in cross-repo UIDs or result envelopes. A UID that is stable
  only inside one repo must not be treated as globally unique without a repo prefix.
- Define a workspace symbol reference shape that includes repo alias, local UID, symbol name, kind,
  file, line, confidence, and provenance.
- Define a workspace edge reference shape that includes source repo alias, target repo alias, edge
  kind, confidence, provenance, and whether the edge is stored or query-derived.
- Keep first cross-repo matching conservative:
  - route to HTTP caller by method and normalized path;
  - config/resource by normalized key or category only when source-family semantics match;
  - package/shared symbol evidence only when import/resource metadata is explicit;
  - infra service/resource references only when identifiers are static and non-secret.
- Mark query-derived cross-repo edges separately from stored single-repo edges.
- Do not include cross-repo edges in default single-repo impact. Add an explicit cross-repo impact
  mode that reports local impact and cross-repo evidence separately.
- Exclude semantic/similarity edges from cross-repo impact by default. Semantic results may be
  future discovery hints, not dependency facts.
- Keep protocol matching one family at a time. The first useful cross-repo protocol is HTTP
  route/caller matching because route nodes and HTTP call edges already exist.
- Keep infra matching declaration-only. Compose/Dockerfile facts describe static declarations, not
  live runtime topology.
- Keep config matching no-secret. Values must not be read, joined, printed, or used as match keys.
- Keep source snippets opt-in. Cross-repo search can return metadata; source text retrieval from
  another repo should require an explicit context/snippet request against a registered repo.
- Web/Explorer support should make workspace mode visually distinct before rendering mixed-repo
  graphs. Repo grouping, color, filters, and stale badges are product requirements, not polish.
- MCP support should remain read-only. Any workspace registration or removal command should be CLI
  first unless a separate permission story is designed for mutating MCP tools.
- Diagnostics should count per-repo query time and skipped repo counts without storing source text.
- Cross-repo query budgets should have both per-repo and global caps.
- Cross-repo results should include truncation metadata per repo and globally.
- Documentation should teach that a workspace is an explicit trust set, not a project discovery
  engine.
- Documentation should state that cross-repo absence is not proof of absence when repos are stale,
  missing, unregistered, or unsupported.
- The recommended deep modules are:
  - workspace registry validation;
  - registered index reader and compatibility checker;
  - workspace reference/UID formatter;
  - cross-repo route/HTTP matcher;
  - cross-repo resource/config matcher;
  - cross-repo result merger and budgeter.

## Testing Decisions

- Tests should assert external behavior: workspace registry files, CLI JSON output, query result
  envelopes, warnings, schema compatibility, freshness handling, and no unwanted writes.
- Tests should not assert private SQL traversal details when a public query contract can be
  asserted instead.
- Registry tests should cover create, add repo, list repos, remove repo, moved path, duplicate
  alias, duplicate path, invalid alias, and corrupted registry JSON.
- Permission tests should verify that adding a repo writes only to the workspace registry location,
  not into the registered repo.
- Read-only tests should verify that cross-repo query paths open indexes read-only and do not
  migrate old schemas.
- Compatibility tests should cover current schema, older supported schema, newer unsupported
  schema, missing optional tables, and unreadable/corrupt database files.
- Freshness tests should cover fresh repo, stale repo, missing file, modified indexed file, and
  stale warnings grouped per repo.
- Identity tests should cover git remote/head match, git head changed, git remote changed, non-git
  fingerprint match, non-git path moved, and explicit identity refresh.
- Cross-repo graph-search tests should use at least two temporary fixture repos and assert grouped
  results by repo alias.
- Cross-repo route/HTTP tests should include one backend repo with route nodes and one frontend
  repo with HTTP call edges.
- Cross-repo route/HTTP tests should cover method mismatch, path mismatch, query-string
  normalization, dynamic URL non-match, and unresolved external URL behavior.
- Cross-repo config/resource tests should cover normalized key matching without persisting or
  printing values.
- Cross-repo infra tests should cover static service/image/port/resource evidence and dynamic
  declarations being skipped.
- Cross-repo impact tests should verify that single-repo impact output is unchanged without
  workspace mode.
- Cross-repo impact tests should verify that workspace mode separates local impact from cross-repo
  evidence.
- Snippet/context tests should verify that a repo alias is required or returned for cross-repo
  source retrieval.
- Redaction tests should verify that default output uses aliases rather than absolute paths.
- No-secret tests should search stored registry data, query results, warnings, and logs for
  fixture secret values and assert they are absent.
- MCP tests should verify that read-only cross-repo tools or flags cannot mutate the workspace
  registry.
- Web API tests should be added if Web contracts change. Generated TypeScript types should be
  updated only when the API schema changes.
- Explorer tests should verify that mixed-repo graphs render grouped nodes and stale badges if UI
  work is included in the implementation slice.
- Regression tests should verify existing single-repo schema, graph-search, architecture, context,
  snippet, impact, affected, artifact import/export, infra, protocol, and no-egress behavior.
- Validation before merge should include focused workspace tests, focused cross-repo query tests,
  full backend gate, Web checks if contracts change, `seam sync`, and `seam changes --json`.

## Out of Scope

- Automatic sibling-directory discovery.
- Scanning a parent folder for every `.seam` directory.
- Remote or hosted cross-repo index service.
- Background watcher spanning multiple registered repos.
- Writing cross-repo edges into each repo's active index.
- Mutating registered child repos during workspace registration.
- Cross-repo code modification tools.
- Cross-repo MCP mutation tools.
- Network discovery, service probing, OpenAPI fetching, Docker daemon inspection, or runtime trace
  collection.
- Reading secret values from env files, Compose files, Dockerfiles, config files, or source code.
- Matching on config values or URLs containing credentials.
- Full Cypher or arbitrary SQL over multiple indexes.
- Semantic/similarity edges participating in dependency impact.
- Cross-repo package-manager resolution beyond explicit local graph/resource evidence.
- CI artifact orchestration beyond accepting already-imported local indexes.
- Access control for a remote team service.
- Global monorepo database migration.
- Kubernetes/Terraform/cloud topology beyond whatever each repo already indexes locally.

## Further Notes

- What is next on the roadmap: Cross-Repo Analysis is the remaining high-boundary RFC after
  protocol edges, infra graph, and graph artifacts. It should be designed before any implementation
  because it changes Seam's trust model more than the previous slices.
- Current Seam state: query surfaces are built around one SQLite connection and one repository
  root. This is correct and should remain the default.
- Existing reusable pieces: repository identity from artifact manifests, read-only SQLite helpers,
  schema/freshness introspection, route and HTTP-call facts, infra resources, config keys, graph
  search, context, snippet, and architecture summaries.
- Main product risk: accidental exposure of unrelated local repositories. The design must make
  workspace membership explicit, inspectable, and reversible.
- Main graph-quality risk: false cross-repo links. The first implementation should miss links
  rather than infer them from weak string similarity.
- Main implementation risk: turning every query handler into a multi-repo special case. The design
  should isolate federation, reference formatting, matching, and result merging in deep modules.
- Recommended first acceptance fixture: two temp repos named `frontend` and `api`; the API repo
  has one indexed route; the frontend repo has one literal HTTP call to it; workspace status shows
  both repos fresh; cross-repo route caller search returns one query-derived edge with both repo
  aliases; normal single-repo graph search remains unchanged.
