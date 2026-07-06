# PRD - Phase 11 RFC: Trusted Shared Index Bootstrap

> Status: ready-for-agent.
> Created: 2026-07-06.
> GitHub issue: https://github.com/Catafal/seam/issues/389.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/codememory-inspired-agent-answerability-roadmap.md`.
> Supersedes follow-up gaps from `docs/prd/phase11-rfc-graph-artifact-export-import.md`.
> External reference: `DeusData/codebase-memory-mcp` advertises team-shared graph
> artifacts and first-run graph bootstrap as part of its low-token agent workflow.

## Problem Statement

Seam already has the base graph artifact lifecycle: local artifact inspection,
local import, checksums, manifests, schema checks, repository identity checks,
atomic landing, path rebasing, `seam fetch`, and CI-oriented shared-index docs.
That means the original "export/import exists" gap is mostly closed.

The remaining problem is more precise: agents still do not have a compact,
machine-readable answer to "can I trust this index bootstrap path, and what should
I do next?" without understanding several separate commands and implementation
details.

From the user's perspective, graph artifacts are not just cache files. They are
precomputed code intelligence. If an AI agent uses a fetched or imported index,
that index can influence search results, graph traversal, impact analysis,
architecture summaries, Explorer views, and editing plans. When the bootstrap path
is unclear, agents waste tokens inspecting docs, retrying commands, or falling back
to a full `init` even when a safe artifact exists. When the trust contract is too
weak, agents may treat an unverified artifact the same as a verified one.

The current base implementation leaves several answerability gaps:

- `seam schema` reports graph/index capability, freshness, semantic readiness, and
  tool inventory, but it does not report artifact lifecycle capability or local
  artifact provenance.
- `seam fetch` can continue when the checksum sidecar is missing. That keeps older
  CI setups working, but it is not a strong default for automation that wants a
  trusted shared index.
- Artifact import/fetch failures are user-actionable, but agents do not get a
  unified remediation contract across local import, remote fetch, schema, and docs.
- The answerability benchmark does not cover setup-time questions such as "is this
  fetched index verified?", "why did fetch fall back?", or "should I run `fetch`,
  `sync`, `init`, or `import-index`?"
- Seam has no explicit persistent local record that the current `.seam/` was
  produced by artifact import/fetch, which artifact was used, whether it was
  verified, and whether local sync changed it after landing.

This matters because Seam's product bar is lower-token, evidence-backed
navigation for commercial coding agents. A trusted shared index can save a large
cold-start indexing pass, but only if agents can determine trust and freshness from
small structured outputs. The goal is not to add a new graph domain. The goal is to
make the existing shared-index path as answerable and fail-closed as the rest of
Seam's read surfaces.

## Solution

Add a trusted shared-index bootstrap layer on top of the existing artifact
implementation.

This phase should make artifact trust and readiness visible through compact,
structured surfaces while preserving Seam's local-first and read-only MCP model.
The implementation should harden `fetch` for automation, persist safe bootstrap
provenance inside `.seam/`, expose artifact readiness in schema/status surfaces,
and add answerability scenarios for setup-time agent questions.

The intended agent workflow is:

1. Call `seam schema --json`.
2. See whether artifact lifecycle support is available.
3. See whether the current index was built locally, imported, fetched, or unknown.
4. See whether the artifact, if any, was checksum verified.
5. See producer schema/version, manifest version, repository identity match,
   landing time, sync-after-landing status, freshness, semantic compatibility, and
   recommended repair commands.
6. If no index exists, choose between trusted fetch and local init based on a stable
   readiness contract.
7. If fetch/import fails, branch on stable error codes rather than grepping docs.

The user-facing outcome should be a Seam installation that can answer:

- "Can this checkout use a prebuilt index safely?"
- "Was the current index fetched or built locally?"
- "Was the fetched index verified?"
- "Which commit/repo/schema produced this artifact?"
- "Did local sync mutate it after landing?"
- "Is semantic search disabled because the artifact lacks compatible embeddings?"
- "What command should I run next?"

This PRD intentionally avoids new graph edges. It does not add cross-repo
intelligence, runtime probing, artifact registries, MCP write tools, or source-text
artifacts.

## User Stories

1. As an AI coding agent, I want `seam schema --json` to report artifact lifecycle
   support, so that I know shared-index bootstrap is a supported path.
2. As an AI coding agent, I want schema output to report whether the current index
   was built locally, imported, fetched, or unknown, so that I can interpret graph
   evidence correctly.
3. As an AI coding agent, I want schema output to report whether the current index
   came from a checksum-verified artifact, so that I do not treat unverified and
   verified graph data equally.
4. As an AI coding agent, I want artifact readiness to include stable status values,
   so that I can branch without parsing prose.
5. As an AI coding agent, I want artifact readiness to include stable reason codes,
   so that I know whether the blocker is no index, no URL, no checksum, schema
   mismatch, repo mismatch, stale index, missing manifest, or semantic mismatch.
6. As an AI coding agent, I want artifact readiness to include recommended next
   commands, so that I can choose `seam fetch`, `seam sync`, `seam init`, or
   `seam import-index` without re-reading docs.
7. As an AI coding agent, I want `seam fetch` to have a strict mode that requires a
   checksum sidecar, manifest, schema compatibility, and repository identity match,
   so that CI/agent automation can fail closed.
8. As an AI coding agent, I want strict fetch failures to preserve the existing
   `.seam/` directory, so that a bad artifact never destroys a usable index.
9. As an AI coding agent, I want non-strict fetch behavior to be explicitly named
   legacy or permissive, so that leniency is not mistaken for verification.
10. As an AI coding agent, I want fetch/import outputs to say whether sync ran after
    artifact landing, so that I can distinguish the published artifact from the
    artifact plus local delta.
11. As an AI coding agent, I want fetch/import outputs to report `files_rebased`, so
    that I know path rebasing happened for this checkout.
12. As an AI coding agent, I want artifact status to report manifest version and
    index schema version, so that compatibility is obvious.
13. As an AI coding agent, I want artifact status to report the producing Seam
    version when available, so that I can reason about extractor behavior.
14. As an AI coding agent, I want artifact status to report the producing git SHA
    and remote fingerprint when available, so that repository identity is compactly
    inspectable.
15. As an AI coding agent, I want artifact status to avoid leaking artifact URLs
    that may contain tokens, so that trust metadata does not expose credentials.
16. As an AI coding agent, I want artifact status to avoid storing local absolute
    paths from CI machines, so that bootstrap metadata stays portable and private.
17. As an AI coding agent, I want semantic readiness to explain artifact-related
    embedding absence or model mismatch, so that I do not confuse keyword-only
    search with a broken index.
18. As an AI coding agent, I want imported artifact provenance to survive subsequent
    schema calls, so that I can answer bootstrap questions after the original command
    output is gone.
19. As an AI coding agent, I want local `init` to clear or replace stale artifact
    provenance, so that a locally rebuilt index is not reported as still fetched.
20. As an AI coding agent, I want local `sync` after artifact landing to preserve
    artifact provenance while marking that local reconciliation occurred, so that
    the status remains honest.
21. As an AI coding agent, I want `inspect-index` output to match the same readiness
    vocabulary as schema/fetch/import, so that artifact inspection has one trust
    language.
22. As an AI coding agent, I want artifact readiness to distinguish "supported but no
    artifact configured" from "unsupported old Seam", so that absence is not a hard
    error.
23. As an AI coding agent, I want setup-time answerability scenarios, so that future
    changes prove Seam answers bootstrap questions with few tokens.
24. As an AI coding agent, I want the answerability benchmark to include "can I trust
    this fetched index?", so that artifact work is measured against the product bar.
25. As an AI coding agent, I want the benchmark to include "what should I run next
    when fetch is unavailable?", so that remediation guidance is tested.
26. As an AI coding agent, I want artifact status to remain available without
    semantic embeddings, so that graph-only artifacts are first-class.
27. As an AI coding agent, I want artifact status to remain available when no
    infra/http/test edges are populated, so that artifact readiness does not depend
    on optional graph domains.
28. As a human developer, I want a compact doctor-like artifact section in schema or
    status, so that I can diagnose shared-index bootstrap without reading logs.
29. As a human developer, I want strict fetch to be usable in CI, so that a missing
    checksum fails the job instead of landing an unverifiable index.
30. As a human developer, I want permissive fetch to remain available only if it is
    clearly marked, so that existing setups can migrate intentionally.
31. As a human developer, I want import/fetch output to include artifact byte size and
    checksum, so that CI logs can be audited.
32. As a human developer, I want import/fetch output to include manifest content flags,
    so that I know whether vectors or other optional data were included.
33. As a human developer, I want import/fetch output to include a freshness result, so
    that I know whether local sync or init is still needed.
34. As a human developer, I want a stable JSON contract for fetch/import errors, so
    that scripts can fall back cleanly.
35. As a human developer, I want artifact status to explain repo mismatch clearly, so
    that I do not accidentally import another project's graph.
36. As a human developer, I want artifact status to explain schema-too-new clearly, so
    that I know to upgrade Seam rather than rebuild blindly.
37. As a human developer, I want artifact status to explain missing manifest clearly,
    so that legacy artifacts can be regenerated.
38. As a human developer, I want docs to tell teams when to use artifact fetch versus
    local init, so that shared-index bootstrap does not become ritual.
39. As a CI maintainer, I want a documented strict fetch mode, so that CI consumers
    can enforce checksum and manifest requirements.
40. As a CI maintainer, I want artifact producer docs to state that checksum sidecars
    are mandatory for trusted consumption, so that published artifacts are complete.
41. As a CI maintainer, I want artifact metadata to be stable across machines, so that
    agents on developer machines can trust CI-produced indexes.
42. As a CI maintainer, I want artifact status to report the artifact git SHA and
    local git SHA separately, so that ancestor fallback and local deltas are visible.
43. As a Seam maintainer, I want artifact readiness classification behind a deep
    module, so that schema, fetch, import, docs, and tests do not drift.
44. As a Seam maintainer, I want persisted artifact provenance stored in a small
    metadata file or metadata table with an allowlisted shape, so that it is useful
    but not leaky.
45. As a Seam maintainer, I want the persisted provenance record to exclude URLs by
    default, so that signed or tokenized artifact URLs are not written to disk.
46. As a Seam maintainer, I want all artifact status fields to be serializable through
    CLI, MCP schema, Web status, and generated Web types where relevant, so that
    transports remain aligned.
47. As a Seam maintainer, I want no new MCP mutation tool, so that MCP stays read-only
    for artifact lifecycle.
48. As a Seam maintainer, I want strict-mode tests to cover missing checksum,
    corrupted checksum, missing manifest, schema-too-new, repo mismatch, and preserved
    existing index.
49. As a Seam maintainer, I want compatibility tests to cover old indexes without
    artifact provenance, so that schema still works.
50. As a Seam maintainer, I want docs to call artifact evidence "bootstrap
    provenance", not dependency evidence, so that agents do not confuse artifact
    metadata with graph facts.
51. As a future release-hardening implementer, I want artifact checksum semantics to
    be strict and explicit, so that signatures or attestations can layer on top.
52. As a future diagnostics implementer, I want artifact readiness to compose with a
    broader `seam doctor` surface, so that health checks do not need to rediscover
    artifact state.
53. As a future team-workflow implementer, I want trusted artifacts to remain
    single-repo, so that cross-repo intelligence is not introduced accidentally.
54. As a future semantic-search implementer, I want artifact status to include vector
    content flags and model compatibility, so that semantic bootstrap can be
    debugged safely.
55. As a future Explorer user, I want Explorer status to distinguish local init from
    fetched index when surfaced, so that UI trust signals match CLI trust signals.

## Implementation Decisions

- Treat this PRD as a hardening follow-up to shipped graph artifact export/import,
  not as a replacement for the existing archive format.
- Preserve the current canonical artifact files: `seam.db`, optional vector files,
  `manifest.json`, and `seam-index.sha256`.
- Keep artifact lifecycle write operations CLI-only. Do not add MCP write tools for
  fetch, import, or export.
- Add a small deep module for artifact readiness classification. The module should
  consume the current project root, index DB path, artifact provenance record when
  present, schema/freshness facts, semantic readiness facts, and relevant fetch
  configuration. It should return a stable, serializable readiness object.
- Use one readiness vocabulary across schema, fetch, import, inspect, docs, and
  tests. Example statuses should include `unsupported`, `not_configured`,
  `local_index`, `verified_artifact`, `unverified_artifact`, `stale`,
  `blocked`, and `unknown`.
- Use stable reason codes rather than prose-only messages. Example reasons should
  include `no_index`, `artifact_url_missing`, `checksum_missing`,
  `checksum_failed`, `manifest_missing`, `manifest_unsupported`,
  `schema_too_new`, `repo_mismatch`, `semantic_vectors_missing`,
  `semantic_model_mismatch`, and `fresh`.
- Add `recommended_next_calls` or equivalent command guidance to the readiness
  object, matching the existing schema/semantic pattern.
- Persist safe artifact provenance after successful import/fetch. The record should
  be stored inside `.seam/` and should be excluded from graph artifacts by default
  unless explicitly included as safe metadata.
- Persist only allowlisted fields: bootstrap source kind, landed-at timestamp,
  verified boolean, checksum, manifest version, schema version, producing Seam
  version, git SHA, normalized remote fingerprint, content flags, files rebased,
  sync-ran boolean, sync summary counts, and semantic-sync status.
- Do not persist raw artifact URLs by default. If future debugging needs URL
  persistence, it must be redacted or explicitly opted in.
- Do not persist CI absolute paths or local machine roots beyond the existing root
  fingerprint semantics already in manifests.
- Update local `init` to clear artifact provenance or mark the current index as
  local-built after a successful full rebuild.
- Update local `sync` to preserve artifact provenance but record that local
  reconciliation has occurred when sync follows artifact landing.
- Add strict fetch mode. The command surface can be `--strict` or a config flag,
  but there must be a machine-usable way to require checksum and manifest
  verification.
- In strict fetch mode, missing checksum must fail before extraction or landing.
- In strict fetch mode, missing manifest must fail before extraction or landing.
- In strict fetch mode, schema-too-new must fail before landing.
- In strict fetch mode, repository mismatch must fail unless explicitly overridden.
- Preserve permissive fetch only if it is clearly named in output and docs. The
  preferred long-term direction is for trusted automation to use strict fetch.
- Keep local import strict by default, matching the existing checksum-required
  behavior.
- Make fetch reuse as much of the local artifact inspection/import validation path
  as possible, so fetch is not weaker than local import.
- Keep atomic landing behavior unchanged: validate, stage, open staged DB, rebase,
  swap, then remove backup only after success.
- If fetch performs post-landing sync, output and persisted provenance must say so.
- If semantic sync is requested and fails non-fatally, output and persisted
  provenance must record that semantic sync failed without invalidating the graph
  index.
- Extend schema output with an `artifacts` or `bootstrap` block. It should be small,
  stable, and transport-safe.
- Extend Web status and generated types only if the Web status currently mirrors
  schema health. Keep CLI/MCP/Web vocabulary aligned.
- Add answerability scenarios for artifact bootstrap. These should test the final
  output contract rather than private helper behavior.
- Update docs to distinguish three concepts: artifact format, artifact lifecycle
  commands, and artifact readiness/status.
- Continue to state that query-time tools are offline and read-only after artifact
  landing.
- Do not add signature verification, artifact registries, or release attestations in
  this PRD. The readiness shape should leave room for those fields later.
- Do not turn artifact provenance into graph nodes or edges.
- Do not treat imported artifact metadata as code dependency evidence.

## Testing Decisions

- Tests should assert externally visible behavior and safety guarantees, not private
  implementation details.
- Unit-test the new artifact readiness classifier as a pure/deep module. Cover no
  index, local-built index, verified artifact, unverified artifact, missing
  checksum, missing manifest, schema-too-new, repo mismatch, stale index, semantic
  vector absence, and semantic model mismatch.
- Unit-test persisted provenance read/write with redaction invariants. Confirm raw
  URLs, token-like strings, source snippets, and CI absolute paths are not written.
- Extend artifact lifecycle tests to assert successful import/fetch writes the
  safe provenance record.
- Extend artifact lifecycle tests to assert local `init` clears or replaces
  artifact provenance.
- Extend sync tests to assert sync after artifact landing records local
  reconciliation without erasing artifact origin.
- Extend fetch tests with strict-mode missing-checksum failure. The existing index
  must be preserved.
- Extend fetch tests with strict-mode missing-manifest failure. The existing index
  must be preserved.
- Extend fetch tests with strict-mode schema-too-new failure. The existing index
  must be preserved.
- Extend fetch tests with strict-mode repo-mismatch failure unless the explicit
  override is supplied.
- Keep permissive fetch tests if permissive mode remains supported, but assert the
  output clearly marks the artifact as unverified.
- Extend schema tests to cover artifact readiness for old indexes with no
  provenance record.
- Extend schema tests to cover artifact readiness for a verified imported index.
- Extend schema tests to cover artifact readiness when no index exists, if schema
  has a no-index JSON path.
- Extend Web API/type tests if artifact readiness is exposed through Web status.
- Add answerability benchmark scenarios for setup-time artifact questions:
  - "Can I trust this fetched index?"
  - "Why did Seam refuse this artifact?"
  - "Should I run fetch, sync, init, or import-index next?"
  - "Why is semantic search unavailable after artifact bootstrap?"
- The benchmark should score answer quality, evidence quality, token cost, tool
  calls, and false confidence, consistent with existing answerability harness
  conventions.
- Run the focused artifact/fetch/schema/answerability tests for implementation
  work, then `make gate` before merging.

## Out of Scope

- Building the base artifact export/import commands from scratch. They already
  exist and this PRD hardens the remaining trust/readiness gaps.
- Cross-repo graph intelligence or persisted cross-repo edges.
- Artifact registry UX, artifact marketplace UX, or remote discovery beyond the
  existing configured fetch URL.
- Fetch/import through MCP. MCP remains read-only.
- Runtime probing, Docker execution, OpenAPI fetching, server introspection, or
  network discovery during query-time tools.
- Source-text artifacts by default.
- Secret persistence, raw `.env` values, query text, snippets, diagnostics logs, or
  tokenized URLs in artifact provenance.
- Signature verification, Sigstore/cosign, SLSA attestations, or PyPI release
  provenance. Those can layer on top of strict checksum/manifest semantics later.
- New graph nodes or graph edges for artifact metadata.
- Making semantic embeddings mandatory for shared-index artifacts.
- Changing impact, trace, graph-search, or Explorer graph semantics based on
  artifact provenance.

## Further Notes

The CodeMemory comparison is useful here because it validates the product value of
shared graph bootstrap: agents should spend fewer tokens getting to a usable graph.
Seam's version should be more conservative than CodeMemory's advertised
team-shared artifact flow: checksum and manifest verification should be visible,
strict automation should fail closed, and MCP should stay read-only.

This PRD is intentionally about answerability rather than archive compression.
The artifact implementation already knows how to pack, inspect, verify, unpack,
rebase, and land an index. The missing product affordance is a small, trustworthy
status contract that lets agents answer bootstrap questions with one structured
call instead of reading implementation details.

If this phase lands cleanly, the next roadmap item can be either a broader
`seam doctor` readiness surface that composes schema/freshness/artifact/semantic
state, or a strict release-trust follow-up that adds signatures/attestations to
the already strict artifact contract.
