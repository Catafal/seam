# PRD — Phase 11 RFC: Graph Artifact Export/Import

> Status: ready for review.
> Created: 2026-07-03.
> GitHub issue: https://github.com/Catafal/seam/issues/295.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/phase11-rfc-roadmap.md`.
> Parent status matrix: `phase11-codebase-memory-roadmap.md`.

## Problem Statement

Seam already has the foundation for sharing pre-built indexes. A CI-oriented `fetch`
workflow can download a packed `.seam/` archive, verify it when a checksum sidecar is
available, unpack it, rebase file paths to the local checkout, and sync local deltas.
That is useful for teams, but it is not yet a complete graph artifact product surface.

From the user's perspective, the missing capability is explicit, auditable control over
graph artifacts. A developer or CI agent should be able to answer:

- what exactly is inside this artifact?
- which Seam schema and Seam version produced it?
- which repository root and commit does it claim to represent?
- does it contain source text, embeddings, diagnostics, or only graph/index data?
- can I export an artifact locally without copying internal `.seam/` files by hand?
- can I import an artifact from disk without enabling a network download path?
- will import fail closed when schema, root, checksum, or metadata is unsafe?
- will import preserve my existing `.seam/` index if anything fails?

Today the existing shared-index flow answers some of those questions operationally, but
not as a first-class contract. The archive format is canonical, and fetch has good atomic
swap behavior, but the first-class user surface is network-template driven. There is no
explicit local `export` command, no explicit local `import` command, no manifest-first
inspection path, no documented schema compatibility policy for artifact import, and no
clear decision about artifact contents beyond the current canonical files.

This gap matters because graph artifacts are a trust boundary. A Seam index is not just a
cache file; it is structured knowledge that agents use to make code-change decisions.
Importing an artifact can affect every query result, every impact report, every Explorer
view, and every MCP answer. That makes artifact lifecycle closer to package installation
than ordinary caching. It needs explicit provenance, explicit user intent, and fail-closed
safety behavior.

The problem is not that Seam needs a broad artifact marketplace or automatic index
syncing. The problem is narrower: turn the existing artifact primitives into a
conservative, local-first export/import contract that teams and agents can reason about
without hidden writes or hidden network behavior.

## Solution

Add a scoped graph artifact export/import RFC that formalizes Seam's portable index
artifact lifecycle.

The first implementation should introduce explicit local commands and reusable internals
for:

1. exporting the current local index into a portable artifact bundle;
2. inspecting an artifact manifest without importing it;
3. importing a local artifact bundle into `.seam/` with fail-closed validation;
4. preserving the existing index on every failure;
5. reusing the current path rebase and local sync behavior where appropriate;
6. keeping network-based fetch as a separate convenience workflow built on the same safe
   artifact contracts.

The first user experience should be deliberately simple:

1. A CI job or developer runs an export command after a successful index build.
2. The command writes a canonical compressed artifact, a checksum sidecar, and a manifest.
3. Another developer or agent can inspect the artifact before importing it.
4. Import validates checksum, manifest, schema version, required members, path safety, and
   root/commit metadata before touching the live index.
5. Import stages the artifact in a temporary directory, atomically swaps only after all
   validation succeeds, rebases file paths to the current checkout when requested, and
   optionally syncs local deltas.
6. Query paths remain fully read-only and offline.

The feature should preserve Seam's product philosophy:

- local-first by default;
- explicit writes only;
- no automatic artifact discovery;
- no automatic network calls;
- no query-time downloads;
- no source text in the default artifact;
- no secret values;
- strict path traversal guards;
- schema compatibility checked before import;
- current index preserved on failure;
- structured JSON output for agents;
- CLI/MCP read paths remain read-only.

This PRD covers graph artifact export/import only. It does not include cross-repo
analysis, remote artifact registry UX, signed release infrastructure, automatic package
manager distribution, runtime trace ingestion, or broad installer work.

## User Stories

1. As an AI coding agent, I want to inspect an artifact's manifest before importing it, so
   that I know whether it is safe and relevant to the current repo.
2. As an AI coding agent, I want import to refuse newer schema versions, so that I do not
   load graph data my local Seam cannot interpret correctly.
3. As an AI coding agent, I want import to report the producing Seam version, so that I can
   reason about feature compatibility and extraction quality.
4. As an AI coding agent, I want import to report the artifact's repository identity, so
   that I do not accidentally import a graph from another project.
5. As an AI coding agent, I want import to show whether the artifact root matches my local
   checkout, so that path rebasing is explicit.
6. As an AI coding agent, I want import to preserve the existing index on every failure, so
   that a bad artifact does not destroy working local intelligence.
7. As an AI coding agent, I want import errors to use stable error codes, so that automated
   workflows can decide whether to retry, fall back to `sync`, or run a full `init`.
8. As an AI coding agent, I want export results in structured JSON, so that CI can upload
   the exact files and metadata without parsing prose.
9. As an AI coding agent, I want import results in structured JSON, so that I can confirm
   which artifact was installed and what local reconciliation ran.
10. As an AI coding agent, I want manifest inspection to avoid modifying `.seam/`, so that
    preflight checks are read-only.
11. As an AI coding agent, I want artifact contents summarized before import, so that I can
    distinguish graph-only, graph-plus-embeddings, and future artifact variants.
12. As an AI coding agent, I want default export to exclude source snippets and diagnostics,
    so that artifacts are less likely to leak sensitive local context.
13. As an AI coding agent, I want export to include route, config, resource, infra,
    protocol, test, and exception graph facts when present, so that the artifact represents
    the same indexed graph users already query locally.
14. As an AI coding agent, I want export to include optional embeddings only when the local
    index has compatible embedding files, so that semantic bootstrap stays explicit.
15. As an AI coding agent, I want import to degrade gracefully when embeddings are absent or
    model-incompatible, so that lexical/graph search still works.
16. As an AI coding agent, I want import to run local staleness checks after landing, so
    that I know whether the artifact is older than the working tree.
17. As an AI coding agent, I want import to optionally run local sync after landing, so that
    a fetched or copied artifact can be reconciled to local edits.
18. As an AI coding agent, I want import to expose whether sync was run, so that subsequent
    graph answers can be interpreted correctly.
19. As an AI coding agent, I want export to refuse missing or invalid local indexes, so that
    CI does not publish meaningless artifacts.
20. As an AI coding agent, I want export to fail if the index schema is unknown, so that
    artifact consumers do not receive ambiguous metadata.
21. As a human developer, I want a one-command local export, so that I can hand an index to
    another machine or CI job without copying `.seam/` manually.
22. As a human developer, I want a one-command local import, so that I can bootstrap a
    checkout from an artifact without configuring a URL template.
23. As a human developer, I want a dry-run or inspect mode, so that I can see what import
    would do before it writes.
24. As a human developer, I want import to tell me when the artifact was created, so that I
    can judge freshness.
25. As a human developer, I want import to tell me which commit the artifact claims, so that
    I can compare it with my checked-out commit.
26. As a human developer, I want import to warn when the artifact commit differs from my
    checkout, so that I understand why local sync may need to reconcile changes.
27. As a human developer, I want import to refuse a different repo identity unless I
    explicitly override, so that I do not poison local graph answers.
28. As a human developer, I want import to be fast for large repos, so that artifact
    bootstrap is clearly better than full re-indexing.
29. As a human developer, I want the artifact format documented, so that CI and team tooling
    can publish the right files.
30. As a human developer, I want checksum verification to be mandatory for local import, so
    that accidental corruption is caught before install.
31. As a human developer, I want network fetch and local import to have consistent safety
    behavior, so that both flows preserve my index on failure.
32. As a human developer, I want `fetch` to reuse the same manifest validation as import, so
    that network provisioning is not a weaker path.
33. As a human developer, I want artifact inspection to work without a project checkout when
    possible, so that I can audit a downloaded file in isolation.
34. As a human developer, I want the default artifact to be portable across machines with
    different checkout roots, so that file path rebasing remains a supported workflow.
35. As a human developer, I want cross-OS limitations called out, so that macOS/Linux/Windows
    path assumptions are explicit.
36. As a CI maintainer, I want export to produce deterministic filenames, so that workflow
    upload steps stay simple.
37. As a CI maintainer, I want export to produce a manifest file, so that artifact metadata
    can be surfaced in CI logs and release pages.
38. As a CI maintainer, I want export to produce a checksum sidecar, so that consumers can
    verify downloads using standard tools.
39. As a CI maintainer, I want export to fail non-zero when no index exists, so that CI does
    not publish empty artifacts.
40. As a CI maintainer, I want export to identify the git commit when available, so that the
    published artifact can be tied to source state.
41. As a CI maintainer, I want export to work without requiring embeddings, so that graph
    artifacts remain cheap by default.
42. As a CI maintainer, I want an optional semantic artifact mode, so that teams that use
    semantic search can bootstrap vector files too.
43. As a CI maintainer, I want import/fetch to validate manifest and archive checksums before
    swap-in, so that bad uploads fail before touching user state.
44. As a CI maintainer, I want import/fetch output to include artifact byte size, checksum,
    schema version, and producer version, so that logs are useful in incidents.
45. As a Seam maintainer, I want one deep artifact module to own manifest creation and
    validation, so that export, import, and fetch do not drift.
46. As a Seam maintainer, I want path traversal checks to remain all-or-nothing, so that
    malicious tar members cannot partially extract.
47. As a Seam maintainer, I want artifact member allowlists to stay small and explicit, so
    that future local files are not accidentally shipped.
48. As a Seam maintainer, I want source text policy explicit in the manifest, so that future
    snippet-carrying artifacts cannot appear silently.
49. As a Seam maintainer, I want artifact import to be outside MCP by default, so that read-
    only MCP guarantees remain intact.
50. As a Seam maintainer, I want import to share existing index swap behavior, so that there
    is only one path to reason about for atomic landing.
51. As a Seam maintainer, I want import to share existing local sync behavior, so that
    artifact landing and local reconciliation behave consistently.
52. As a Seam maintainer, I want fetch checksum leniency revisited under this RFC, so that
    the user-facing trust contract is coherent.
53. As a Seam maintainer, I want old indexes without manifests to have an explicit support
    decision, so that legacy artifact behavior is not accidental.
54. As a Seam maintainer, I want import to reject archives with unexpected files by default,
    so that artifact contents remain constrained.
55. As a Seam maintainer, I want tests for corrupt archives, path traversal, schema mismatch,
    and atomic rollback, so that artifact trust does not regress.
56. As a Seam maintainer, I want export/import docs to distinguish artifact bootstrap from
    query-time behavior, so that local-first no-egress claims stay precise.
57. As a future cross-repo implementer, I want repo identity and artifact metadata stable
    first, so that cross-repo graph bundles have a safe foundation later.
58. As a future graph artifact implementer, I want manifest versioning from day one, so that
    artifact format migrations are possible.
59. As a future release-trust implementer, I want checksum and manifest validation hooks, so
    that signatures or provenance attestations can be added later.
60. As a future Explorer user, I want imported graph data to look identical to locally built
    graph data once landed, so that Explorer does not need a separate artifact mode.

## Implementation Decisions

- Treat this RFC as a productization layer over the existing artifact pack/unpack, rebase,
  and fetch foundations, not as a new graph storage format.
- Add explicit local export/import commands rather than asking users to copy `.seam/`
  directories.
- Keep network fetch separate from local import. Fetch may later call the same validation
  internals, but local import must not require `SEAM_INDEX_ARTIFACT_URL`.
- Keep all query, MCP, and Explorer read paths free of artifact writes and network calls.
- Use a small, deep artifact-manifest module to own manifest creation, manifest parsing,
  schema compatibility checks, content summaries, and validation results.
- Use the existing archive module as the canonical archive member allowlist, but extend the
  format to include a manifest.
- Preserve a flat archive layout unless manifest needs a namespaced internal location. If a
  namespaced layout is chosen, import must keep the path traversal guard equally strict.
- Include a manifest format version so artifact metadata can evolve independently from the
  SQLite schema version.
- Include the producing Seam version and index schema version in every exported artifact.
- Include the repository identity fields available locally: root fingerprint, git remote
  fingerprint when available, commit SHA when available, and created timestamp.
- Make root fingerprint a comparison signal, not an authentication mechanism.
- Make checksum verification mandatory for explicit local import.
- Decide whether fetch should remain lenient when checksum is missing or move to fail-closed
  behavior for consistency. The preferred direction is fail-closed for any command that
  claims verification.
- Refuse artifacts from newer schema versions by default.
- Allow same-schema artifacts by default.
- Allow older-schema artifacts only if the existing migration path can safely bring the
  index forward after import; otherwise fail with a clear migration-needed error.
- Do not silently migrate an imported artifact if that would make rollback harder. If
  migration is allowed, it must occur in staging before swap-in.
- Validate manifest and archive members before touching the live index.
- Stage all import work in a temporary directory.
- Keep atomic swap-in: existing index moves aside, staged index lands, and backup is
  restored on failure.
- Delete the backup only after validation, optional migration, rebase, and opening the DB
  succeed.
- Rebase file paths to the current root by default when importing into a checkout.
- Provide an option to skip rebase for advanced debugging, but make the default path local-
  checkout friendly.
- Run local sync after import by default only if that matches existing fetch behavior and
  output clearly says it happened. If import is intended as a pure restore command, expose
  sync as an explicit flag. This RFC should choose one default and document it.
- Preferred default: local import should land and rebase, then report staleness; sync should
  be explicit. Fetch can continue to sync by default because it is a bootstrap workflow.
- Preserve optional semantic vector files when present and manifest-compatible.
- Never require embeddings for graph artifact import.
- If semantic files exist but metadata is missing or model-incompatible, import should land
  the graph and mark semantic unavailable rather than failing the whole import, unless the
  user requested strict semantic mode.
- Exclude diagnostics output, watcher state, WAL/SHM sidecars, temporary files, and local
  logs from the artifact.
- Exclude source snippets by default. If a future artifact mode includes snippets, it needs
  a separate explicit mode and manifest flag.
- Do not add import/export mutation tools to the default MCP server.
- Add JSON and quiet output modes for CLI parity with other automation-friendly commands.
- Make dry-run or inspect mode read-only and usable without an existing local index.
- Keep error codes stable and small: invalid input, artifact missing, checksum mismatch,
  unsafe archive, schema incompatible, repo mismatch, DB error, and swap failed.
- Ensure imported artifacts do not bypass staleness reporting. A successfully imported index
  can still be stale relative to disk, and that must remain visible.
- Keep graph artifact identity separate from future cross-repo workspace identity. This RFC
  should not introduce multi-repo graph semantics.
- Keep artifact export/import out of default impact traversal, graph search, and schema
  capabilities except for documentation of command availability. Artifacts are lifecycle
  operations, not graph facts.

## Testing Decisions

- Tests should assert externally visible behavior: created files, manifest fields,
  structured JSON output, import refusal, atomic rollback, path rebasing, and queryability
  after successful import.
- Tests should not assert private helper call ordering except where needed to prove atomic
  rollback safety.
- The artifact manifest module should have focused unit tests because it will be the deep
  module that prevents export/import/fetch contract drift.
- Archive tests should cover default member allowlist behavior.
- Archive tests should cover manifest inclusion.
- Archive tests should cover unexpected archive member rejection.
- Archive tests should cover absolute member path rejection.
- Archive tests should cover `..` member path rejection.
- Export tests should cover missing `.seam/` refusal.
- Export tests should cover missing database refusal.
- Export tests should cover successful graph-only export.
- Export tests should cover checksum sidecar creation.
- Export tests should cover manifest creation.
- Export tests should cover optional vector file inclusion when present.
- Export tests should cover diagnostics and watcher files excluded from the artifact.
- Export tests should cover JSON output shape.
- Export tests should cover quiet output shape if quiet mode is added.
- Inspect tests should cover reading manifest and checksum without modifying `.seam/`.
- Inspect tests should cover corrupt archive behavior.
- Inspect tests should cover missing manifest behavior.
- Inspect tests should cover legacy artifact decision behavior if legacy support is kept.
- Import tests should cover successful import into an empty checkout.
- Import tests should cover successful import over an existing index.
- Import tests should cover existing index restored on checksum mismatch.
- Import tests should cover existing index restored on unsafe tar member.
- Import tests should cover existing index restored on invalid database.
- Import tests should cover existing index restored on schema incompatibility.
- Import tests should cover same-schema import accepted.
- Import tests should cover newer-schema import refused.
- Import tests should cover older-schema import behavior according to the chosen migration
  policy.
- Import tests should cover repo mismatch refusal by default.
- Import tests should cover explicit repo mismatch override if such an override is added.
- Import tests should cover path rebasing to the local checkout.
- Import tests should cover synthetic file rows not being rebased.
- Import tests should cover staleness reporting after import.
- Import tests should cover optional local sync flag if import does not sync by default.
- Fetch regression tests should cover that fetch uses the same manifest/checksum validation
  path once it is wired to the new artifact contract.
- CLI tests should cover JSON envelopes for export, inspect, and import.
- CLI tests should cover non-zero exit codes and stable error codes.
- Documentation tests are not required, but docs should be reviewed against the command
  behavior because this feature is heavily trust-contract oriented.
- Existing fetch integration tests should remain green or be intentionally updated if the
  checksum policy changes.
- No web UI tests are needed in the first scope unless Explorer surfaces artifact status.
- No MCP tests are needed unless a future explicit mutation-capable MCP surface is designed.

## Out of Scope

- Cross-repo graph analysis.
- Cross-repo workspace registration.
- Automatic sibling-repo discovery.
- Runtime trace artifact ingestion.
- OpenAPI artifact ingestion.
- Remote artifact registry design.
- Background artifact polling.
- Query-time downloads.
- Automatic import during server startup.
- Automatic import during MCP startup.
- Source text or snippet artifact mode.
- Secret value export.
- Diagnostics export.
- Watcher state export.
- Signed attestations or Sigstore integration.
- Package-manager distribution work.
- General backup/restore for arbitrary `.seam/` files.
- Full SQLite dump format redesign.
- Row-ID graph identity redesign.
- Resolved edge endpoint migration.
- Semantic/similarity edge policy changes.
- New graph query language.
- Explorer artifact-management UI.

## Further Notes

Current audit findings:

- Seam already has a canonical archive pack/unpack module with a small member allowlist.
- The current archive format includes the SQLite index and optional semantic vector files.
- The current archive format excludes diagnostics, watcher state, temporary files, and
  SQLite WAL/SHM sidecars.
- The current unpack path validates path traversal before extraction.
- The current fetch workflow stages downloads and preserves the existing index on failure.
- The current fetch workflow can rebase file paths from CI roots to local roots.
- The current fetch workflow syncs local deltas after landing the downloaded index.
- The current fetch workflow is network-template based and intentionally excluded from
  query-time no-egress claims.
- The current fetch workflow allows missing checksum sidecars and proceeds with a warning.
- There is no first-class local export command.
- There is no first-class local import command.
- There is no manifest-first artifact inspection command.
- There is no manifest version, root fingerprint, artifact contents summary, or formal
  schema compatibility policy attached to exported artifacts.

Recommended first implementation slice:

1. Define the manifest shape and compatibility policy.
2. Extend artifact packing/unpacking to include and validate the manifest.
3. Add local export and inspect commands.
4. Add local import with fail-closed validation and atomic rollback.
5. Reuse path rebase and local staleness reporting.
6. Decide whether import sync is default or explicit; preferred default is explicit sync.
7. Update fetch to share the manifest/checksum validation path.
8. Update README, architecture docs, and API/CLI contract docs around artifact trust.

Recommended follow-up after this PRD:

1. Close or update the protocol and infra PRD tracker issues if their implementation has
   already landed.
2. Write the cross-repo analysis RFC after artifact identity and import safety are stable.
3. Consider artifact signing/provenance only after checksum and manifest validation are
   fail-closed and documented.
