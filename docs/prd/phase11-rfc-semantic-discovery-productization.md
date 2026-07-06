# PRD - Phase 11 RFC: Semantic Discovery Productization

> Status: ready for agent.
> Created: 2026-07-06.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/codememory-inspired-agent-answerability-roadmap.md`.
> Related roadmap: `.claude/tasks/agent-answerability-roadmap.md`.
> Previous shipped slice: Phase 11 hybrid exact type-resolution.

## What Is Next On The Roadmap

The next roadmap phase should be **Semantic Discovery Productization**.

This is the next useful phase because the highest-ranked broad graph surfaces from the
CodeMemory-inspired roadmap have already landed or have product evidence in Seam:

1. HTTP call extraction quality has shipped as a protocol-edge slice.
2. Docker Compose and Dockerfile infra graph support has shipped as the first infra slice.
3. Hybrid exact type-resolution has now shipped its first exact-receiver slice.
4. Graph artifact export/import has shipped as explicit local artifact lifecycle commands.
5. Cross-repo workspace federation has shipped as an explicit local workspace feature.
6. Diagnostics/soak support exists as opt-in local operational instrumentation.
7. Docs/spec grounding exists as a first-class indexed and queryable surface.

The remaining high-leverage gap is not another broad graph domain. It is making semantic
search safe, measurable, and agent-facing enough to close vocabulary mismatch without
polluting dependency semantics.

Seam already has the technical foundation for opt-in semantic search: optional embeddings,
local query embedding, vector candidates, RRF fusion, CLI flags, schema counts, status output,
and tests. But the current product contract is still too implicit for agents. A coding agent
needs to know whether a result came from lexical evidence, graph expansion, semantic similarity,
or a hybrid combination. It also needs a clear guarantee that semantic similarity is only a
discovery lead, never a dependency edge, risk signal, trace hop, impact fact, graph degree, or
change-safety verdict.

This phase should therefore productize semantic discovery. It should not add a new graph
domain, a runtime model service, a remote embedding API, or semantic dependency edges.

## Problem Statement

Seam's core product goal is to help AI coding agents navigate a local codebase with less token
spend than broad grep/read loops, while keeping evidence bounded, local, and trustworthy.

The existing lexical and graph tools are strong when the agent knows the repository's words:

1. `seam search` works when the query shares tokens with symbol names, docstrings, signatures,
   or indexed search text.
2. `seam query` works when lexical seeds can be expanded through nearby graph evidence.
3. `seam graph-search` works when the agent knows which structural question to ask.
4. `seam context`, `seam impact`, `seam trace`, `seam plan`, and `seam pack` work once the
   agent has found the right target.

The recurring discovery failure is vocabulary mismatch. Agents often ask natural language
questions that do not share tokens with the code:

1. "Where is retry logic?" when the implementation is named `_backoff_with_jitter`.
2. "Where is cache invalidation?" when the implementation talks about `evict`, `dirty`, or
   `refresh`.
3. "Where is optimistic locking?" when the implementation uses `version`, `etag`, or
   `compare_and_swap`.
4. "Where is rate limiting?" when the implementation uses `throttle`, `quota`, or `bucket`.
5. "Where is idempotency handled?" when the implementation uses `request_fingerprint` or
   `dedupe_key`.
6. "Where is the retry budget enforced?" when the code is named by a lower-level policy.

Without semantic discovery, the agent falls back to broad grep, repeated search terms, file
listing, and manual source reads. That increases token spend and round trips, and it can lead
the agent to miss the correct starting point.

The risk is the opposite failure: semantic search can look smarter than it is. Similarity is
not proof. A semantically similar symbol may be nearby, related, stale, misleading, or simply
wrong. If Seam exposes semantic-only hits without clear mode, provenance, score, caveat, and
fallback guidance, agents may treat discovery leads as dependency facts.

The current codebase already has semantic infrastructure, but the product contract still needs
to be tightened:

1. Agents cannot consistently see which results are lexical, semantic-only, graph-expanded, or
   hybrid.
2. Semantic-only hits do not carry enough explicit caveat language in every transport.
3. Schema and tool guidance report embeddings, but do not yet define a full semantic discovery
   readiness contract.
4. The answerability benchmark does not yet contain enough vocabulary-mismatch scenarios to
   prove semantic search earns its cost.
5. Tests need to prove semantic enablement cannot change impact, trace, changes, graph degree,
   or dependency risk semantics.
6. Model absence, model mismatch, missing embeddings, stale vector artifacts, and keyword-only
   fallback need to be visible and boring for agents.

The problem is therefore not "build semantic search from scratch." The problem is:

> Turn the existing opt-in semantic infrastructure into a safe agent-facing discovery product,
> with explicit retrieval-mode evidence, measurable answerability lift, and hard semantic
> isolation from dependency and change-safety semantics.

## Solution

Productize semantic discovery as an explicit, opt-in retrieval mode for `seam search` and
`seam query`.

The feature should keep the current architectural direction:

1. Embeddings are built only when the user explicitly indexes semantically, such as with
   `seam init --semantic` or `seam sync --semantic`.
2. Semantic search runs only when semantic configuration is enabled and matching embeddings
   exist.
3. Query-time embedding is local-only and uses the already-cached local model.
4. Keyword-only operation remains the default.
5. Missing semantic dependencies, missing embeddings, model mismatch, stale artifacts, or
   disabled semantic config degrade to keyword-only behavior.
6. Semantic results augment discovery only; they never create dependency facts.

The user-facing behavior should become clearer:

1. `seam schema --json` should tell agents whether semantic discovery is supported, enabled,
   populated, model-matched, and currently usable.
2. `seam search --json` and MCP `seam_search` should expose retrieval-mode metadata per result
   or per response: lexical, semantic-only, hybrid, fallback, or keyword-only.
3. `seam query --json` and MCP `seam_query` should expose which seed symbols came from FTS,
   semantic candidates, or both before graph expansion.
4. Semantic-only results should include caveats that they are discovery leads, not proof of
   dependency, execution, or correctness.
5. Absent embeddings should produce a clear, low-noise status or warning path: "semantic was
   requested or enabled, but no matching embeddings are present; keyword-only fallback was used."
6. Model mismatch should be explicit and actionable: re-run semantic indexing with the configured
   model or change configuration back to the indexed model.
7. The answerability benchmark should include vocabulary-mismatch scenarios and compare whether
   semantic discovery reduces tool calls and token cost versus keyword-only fallback.

The implementation should be a productization and hardening phase. It should mostly modify the
query read model, result contracts, schema/status reporting, benchmark scenarios, tests, and docs.
It should avoid changing the core static dependency graph.

## User Stories

1. As an AI coding agent, I want to find code by concept even when the code uses different words,
   so that I can avoid repeated grep loops.
2. As an AI coding agent, I want "retry logic" to find backoff and jitter implementations, so
   that I can start from likely code instead of guessing symbol names.
3. As an AI coding agent, I want "cache invalidation" to find eviction or refresh code, so that
   vocabulary mismatch does not block discovery.
4. As an AI coding agent, I want "optimistic locking" to find version-checking code, so that I can
   discover concurrency behavior without knowing local naming.
5. As an AI coding agent, I want semantic-only hits labeled as semantic-only, so that I know they
   are leads, not exact matches.
6. As an AI coding agent, I want lexical hits labeled as lexical, so that I can distinguish token
   evidence from similarity evidence.
7. As an AI coding agent, I want hybrid hits labeled as hybrid, so that I know both lexical and
   semantic retrieval found the same symbol.
8. As an AI coding agent, I want `seam query` seeds to explain whether they came from FTS or
   semantic retrieval, so that I can judge graph-expanded neighbors correctly.
9. As an AI coding agent, I want semantic-only results to include a caveat, so that I do not treat
   them as dependency evidence.
10. As an AI coding agent, I want semantic result scores exposed in a stable field, so that I can
    compare semantic-only candidates without relying on list position alone.
11. As an AI coding agent, I want RRF or merged-rank scores explained, so that hybrid ordering is
    not mistaken for cosine-only certainty.
12. As an AI coding agent, I want a keyword-only escape hatch, so that I can reproduce lexical
    behavior exactly when semantic search seems noisy.
13. As an AI coding agent, I want missing embeddings to be visible in schema, so that I know why
    semantic discovery is unavailable.
14. As an AI coding agent, I want model mismatch to be visible in schema, so that I can understand
    why semantic discovery silently fell back to keyword behavior.
15. As an AI coding agent, I want `seam status` and schema to agree about embedding counts and
    model readiness, so that operational guidance is consistent.
16. As an AI coding agent, I want `seam search` output to say when keyword-only fallback was used,
    so that I do not assume semantic search ran.
17. As an AI coding agent, I want MCP search/query payloads to include the same semantic metadata
    as CLI JSON, so that Claude Code, Codex, OpenCode, and other agents get consistent evidence.
18. As an AI coding agent, I want Web/Explorer search to avoid overstating semantic hits, so that
    visual discovery does not become false dependency evidence.
19. As an AI coding agent, I want semantic discovery to keep snippets bounded, so that I do not
    pay more tokens than broad grep would have used.
20. As an AI coding agent, I want semantic-only hits to avoid empty or confusing snippet text where
    possible, so that I can decide whether to inspect the symbol.
21. As an AI coding agent, I want a recommended follow-up call for semantic-only hits, so that I
    know to inspect snippet, context, graph-search, or plan before editing.
22. As an AI coding agent, I want semantic discovery to work without changing `seam impact`, so
    that blast-radius answers remain static graph evidence.
23. As an AI coding agent, I want semantic discovery to work without changing `seam trace`, so that
    dependency paths remain real indexed relationships.
24. As an AI coding agent, I want semantic discovery to work without changing `seam changes`, so
    that pre-commit risk is not influenced by similarity.
25. As an AI coding agent, I want semantic discovery to work without changing graph-search degrees,
    so that hotspots and dead-code suspects remain graph facts.
26. As an AI coding agent, I want semantic discovery to avoid creating `edges` rows, so that
    similarity does not become dependency structure.
27. As an AI coding agent, I want stale semantic artifacts to fall back safely, so that stale vectors
    do not mislead discovery.
28. As an AI coding agent, I want old indexes without embeddings to degrade cleanly, so that base
    Seam remains useful.
29. As an AI coding agent, I want unsupported semantic search to be explicit, so that I can choose
    lexical search, graph-search, or grep as fallback.
30. As an AI coding agent, I want answerability reports to show whether semantic discovery improves
    vocabulary-mismatch questions, so that roadmap decisions are evidence-based.
31. As a human developer, I want semantic search to stay opt-in, so that base indexing stays fast and
    lightweight.
32. As a human developer, I want semantic indexing cost documented, so that I understand the disk,
    CPU, model-download, and sync implications.
33. As a human developer, I want query-time behavior to stay local-only, so that using semantic
    search does not send code or query text to an external service.
34. As a human developer, I want the one-time model download called out clearly, so that network
    behavior is explicit and never happens unexpectedly on query paths.
35. As a human developer, I want model configuration documented, so that teams can choose a model
    deliberately and know when re-indexing is required.
36. As a human developer, I want semantic search to degrade gracefully when the optional extra is not
    installed, so that base installs do not break.
37. As a human developer, I want semantic search to be measurable, so that we do not pay hardware
    pressure for no answerability lift.
38. As a human developer, I want the answerability benchmark to compare semantic-enabled and
    keyword-only retrieval on the same scenarios, so that semantic lift is visible.
39. As a human developer, I want semantic result metadata to be stable across CLI, MCP, and Web, so
    that tools can rely on the contract.
40. As a human developer, I want docs to explain that semantic search is discovery evidence, so that
    new contributors do not use it for impact semantics.
41. As a maintainer, I want retrieval-mode classification behind a small deep module, so that result
    labeling is tested once and reused across search/query transports.
42. As a maintainer, I want readiness/status classification behind a small deep module, so that
    schema, status, handlers, and docs do not drift.
43. As a maintainer, I want semantic-only caveats generated centrally, so that CLI and MCP output
    stay aligned.
44. As a maintainer, I want no semantic facts in the `edges` table, so that migrations and graph
    consumers remain clean.
45. As a maintainer, I want tests proving impact/trace/changes are byte-identical with semantic
    enabled and disabled, so that future changes cannot leak similarity into risk.
46. As a maintainer, I want tests proving graph-search degree filters are unchanged by embeddings,
    so that graph recipes stay trustworthy.
47. As a maintainer, I want tests proving FTS hits are never dropped when semantic candidates exist,
    so that semantic search only adds recall.
48. As a maintainer, I want tests proving lexical hits outrank low-confidence semantic-only hits
    where appropriate, so that exact terms remain strong.
49. As a maintainer, I want tests for missing extra, missing embeddings, model mismatch, stale ANN,
    and keyword-only override, so that every fallback path is explicit.
50. As a maintainer, I want answerability scenario tags to separate semantic discovery from graph
    quality, so that roadmap reports do not misclassify semantic wins as dependency wins.
51. As a future model-swap implementer, I want the contract independent from one embedding model, so
    that model changes do not rewrite agent-facing semantics.
52. As a future ANN implementer, I want the contract independent from vector backend, so that
    sqlite-vec, mmap, or SQL fallback all expose the same result metadata.
53. As a future graph-quality implementer, I want semantic search clearly isolated, so that exact
    dependency work is not hidden behind similarity.
54. As a future docs-grounding implementer, I want semantic doc search kept separate from explicit
    grounding evidence, so that local docs do not become fuzzy dependency claims.
55. As a future release engineer, I want semantic model and vector artifact status reported clearly,
    so that support issues can be diagnosed without source reads.

## Implementation Decisions

- Treat this as a semantic discovery productization phase, not a new semantic-search engine from
  scratch.
- Reuse the existing embedding table, embedding indexer, semantic candidate retrieval, RRF merge,
  `search` and `query` wiring, CLI flags, schema counts, and vector artifact fallbacks where
  possible.
- Add or refine a semantic readiness/readiness-reason contract. The contract should distinguish at
  least: disabled by config, optional extra unavailable, embeddings absent, model mismatch, vector
  artifact stale but SQL fallback usable, populated and usable, and keyword-only override.
- Keep semantic search opt-in through existing configuration and indexing commands. Do not make
  semantic indexing the default.
- Keep query-time behavior local-only. The read path must not download models, call remote APIs,
  fetch docs, contact registries, or emit telemetry.
- Make retrieval mode explicit in search/query results. A result or seed should be classifiable as
  lexical, semantic-only, hybrid, graph-expanded-from-lexical, graph-expanded-from-semantic, or
  keyword-only fallback.
- If per-result retrieval mode creates too much payload churn, add a compact response-level
  `retrieval` block plus per-result mode only for semantic-only/hybrid hits. The chosen contract
  must be the same across CLI JSON, MCP, and Web/API contracts.
- Preserve the current `SearchResult` shape for callers that rely on existing fields, but add
  optional metadata fields in additive fashion.
- `seam query` should make semantic seed origin visible without labeling every graph neighbor as
  semantically relevant. A neighbor reached from a semantic seed is graph-expanded-from-semantic,
  not semantic-only.
- Semantic-only search hits should include an explicit caveat such as "semantic similarity is a
  discovery lead, not dependency evidence."
- Semantic-only search hits should include recommended next calls: snippet for source inspection,
  context for relationships, graph-search for structure, or plan for edit preparation.
- FTS/lexical hits should never be dropped because semantic candidates exist. Hybrid behavior must
  only add recall or alter ranking within documented bounds.
- Lexical evidence should stay privileged when exact query tokens match symbol names, signatures,
  docstrings, or search text. Semantic hits should not bury exact lexical matches without a clear
  hybrid reason.
- RRF score, cosine score, and FTS score should not be conflated. If multiple scores are exposed,
  name them precisely.
- If only one public `score` field can remain, document what it means for keyword-only, semantic-only,
  and hybrid paths, and add raw evidence metadata under separate fields.
- Model mismatch should never silently mix vector spaces. The existing fallback behavior should be
  preserved, but the reason should become visible through schema/status/search/query metadata.
- Missing embeddings should not produce noisy warnings on every query. Prefer a stable response-level
  status and one-time logs where appropriate.
- `--no-semantic` and handler-level `semantic=false` must remain available and must not mutate
  global configuration.
- Keep semantic discovery out of `seam impact`, `seam trace`, `seam changes`, `seam affected`,
  graph-search degree calculations, cleanup suspects, risk tiers, route matching, HTTP-call matching,
  docs grounding confidence, and cross-repo dependency edges.
- Do not add `SEMANTICALLY_RELATED`, `similar_to`, or any similarity edge kind in this PRD.
- Do not persist semantic candidate links as graph facts. Embeddings are retrieval indexes, not
  dependency graph records.
- Do not add a new MCP tool unless the existing search/query contracts cannot express mode and
  caveats cleanly. The preferred product shape is no new tool, just safer search/query output.
- Extend schema introspection with semantic readiness details if current counts/capabilities are
  insufficient. Preserve existing fields for compatibility.
- Update `seam status` only if needed to match schema readiness language. Status and schema should
  not give contradictory semantic instructions.
- Add answerability scenarios for vocabulary mismatch. Required concepts should include retry/backoff,
  cache invalidation, optimistic locking/version checks, rate limiting/throttling, and idempotency/
  deduplication if the fixture can support them.
- The answerability harness should compare keyword-only and semantic-enabled behavior for the same
  question when synthetic or deterministic embeddings can make the test offline.
- If real-model benchmarking is needed, keep it outside the default gate and make network/model
  requirements explicit.
- Add product-gap tags for semantic discovery only where the benchmark proves lexical/graph tools
  alone cannot answer the question.
- Update docs so agents learn the correct workflow: use semantic discovery to find candidate
  symbols, then use snippet/context/plan/impact/trace to verify relationships before editing.
- Update MCP API contracts so commercial coding agents can branch on retrieval mode and semantic
  readiness without reading prose docs.
- Keep all changes additive for old indexes. Old indexes should produce keyword-only behavior with
  clear unsupported/unavailable metadata rather than crashes.
- Keep output byte-budgeted. Semantic metadata should not turn search/query into large diagnostic
  dumps.
- Keep implementation split into deep modules: semantic readiness classification, retrieval-mode
  labeling, answerability benchmark adapters, and transport rendering.

## Testing Decisions

- Good tests should assert externally observable behavior: CLI JSON, MCP handler payloads, schema,
  status, answerability reports, and graph/risk outputs. Tests should not pin private vector backend
  traversal order unless the order is part of the public ranking contract.
- Unit-test semantic readiness classification with these states: config off, optional extra
  unavailable, no embeddings, model mismatch, populated matching embeddings, keyword-only override,
  stale vector artifact with fallback, and backend failure with safe fallback.
- Unit-test retrieval-mode labeling for lexical-only, semantic-only, hybrid, keyword-only fallback,
  and graph-expanded-from-semantic cases.
- Unit-test caveat generation so semantic-only and graph-expanded-from-semantic evidence always
  carries a discovery-not-dependency caveat.
- Unit-test RRF/score reporting so FTS scores, cosine scores, and merged scores are not mislabeled.
- Unit-test that FTS candidates are preserved when semantic candidates are present.
- Unit-test that exact lexical hits are not buried by lower-quality semantic-only hits in the common
  exact-name case.
- Unit-test missing optional semantic extra behavior without importing fastembed at module import
  time.
- Unit-test model mismatch behavior with synthetic embedding rows from two model names.
- Unit-test stale vector artifact fallback if vector artifacts are enabled.
- Integration-test `seam search --json` in keyword-only mode, semantic-enabled mode with synthetic
  vectors, semantic requested but unavailable mode, and `--no-semantic` mode.
- Integration-test `seam query --json` with semantic seeds and verify seed-origin metadata.
- Integration-test MCP `seam_search` and `seam_query` payloads for the same metadata and caveats as
  CLI JSON.
- Integration-test Web/API search payloads if Web schemas expose search result metadata.
- Regression-test `seam impact` with semantic config on/off and synthetic embeddings present. The
  dependency/risk payload should be byte-identical except for unrelated timing or ordering fields
  that are already unstable.
- Regression-test `seam trace` with semantic config on/off and synthetic embeddings present. Paths
  must not change because of embeddings.
- Regression-test `seam changes` with semantic config on/off and synthetic embeddings present.
  Changed-symbol risk must not change because of embeddings.
- Regression-test `seam graph-search` degree, hotspot, dead-code, route, config/resource, test,
  exception, and field-access recipes with semantic config on/off. Results must not change because
  of embeddings.
- Regression-test docs grounding confidence with semantic config on/off. Explicit doc references
  must not be reclassified by semantic similarity.
- Add answerability benchmark scenarios for vocabulary mismatch and ensure reports expose whether
  semantic discovery reduces fallback reads, tool calls, or tokens.
- Add a benchmark/report test that semantic product gaps are separate from graph-quality coherence,
  protocol, infra, docs grounding, or exact type-resolution gaps.
- Add docs/API contract tests if the project has contract snapshots for MCP tools.
- Add old-index compatibility tests for indexes without embedding tables or without new readiness
  metadata.
- Add no-network tests or no-egress checks only if any new path could load a model or backend at
  query time. The expected behavior is no query-time network.
- Run focused semantic tests, search/query handler tests, schema tests, answerability tests, and
  the full backend gate before implementation merge.
- Before committing implementation, run Seam's own `seam sync` and `seam changes --json` checks to
  verify changed-symbol risk.

## Out of Scope

- Making semantic indexing the default.
- Running a remote embedding API.
- Running an LLM judge in the default benchmark.
- Downloading a model during MCP, Web, search, query, impact, trace, changes, graph-search, or any
  other read path.
- Creating semantic dependency edges.
- Adding `SEMANTICALLY_RELATED`, `similar_to`, clone detection, or persisted semantic graph links.
- Using semantic similarity in `seam impact`.
- Using semantic similarity in `seam trace`.
- Using semantic similarity in `seam changes`.
- Using semantic similarity in graph-search degree, hotspot, dead-code, or cleanup-suspect logic.
- Using semantic similarity to upgrade confidence of call, import, route, HTTP, config, resource,
  docs grounding, test, exception, or infra evidence.
- Adding Kubernetes, Helm, Terraform, cloud, runtime, or cross-repo graph extraction.
- Replacing exact graph-quality work with semantic search.
- Replacing docs/spec grounding with semantic doc matching.
- Changing the default MCP tool count unless implementation proves the existing tools cannot carry
  the required metadata.
- Requiring GPU, torch, or heavyweight model runtime for the first productized contract.
- Promising complete recall for vocabulary mismatch.
- Treating semantic absence as evidence that a concept does not exist.
- Storing source bodies or long snippets in embedding metadata beyond the current index policy.
- Telemetry, analytics, hosted search, or any remote service.

## Further Notes

The product value is agent answerability: semantic search should reduce the number of failed
queries and broad grep/read loops when the agent does not know local names.

The product risk is false authority. Semantic search should feel useful but visibly less
authoritative than exact graph evidence. The right workflow is:

1. Use semantic discovery to find candidate symbols when vocabulary is unknown.
2. Use snippet/context/graph-search/plan to verify the candidate.
3. Use impact/trace/changes only for dependency and change-safety evidence.

This phase should also clean up roadmap language around semantic search. The original semantic
search task file was a plan written before much of the implementation landed. The new source of
truth should be this PRD plus the API contracts and answerability scenarios created from it.

Recommended implementation slices:

1. **S1 - Semantic readiness and retrieval-mode contract.** Add the deep readiness classifier,
   retrieval-mode labels, schema/status language, and focused unit tests.
2. **S2 - Search/query transport parity.** Add CLI, MCP, and Web/API result metadata and caveats
   while preserving keyword-only compatibility.
3. **S3 - Semantic isolation hardening.** Add regression tests proving impact, trace, changes,
   graph-search, docs grounding, and suspects are unchanged by semantic enablement.
4. **S4 - Answerability scenarios and docs.** Add vocabulary-mismatch scenarios, update product-gap
   tags, document daily usage, and update MCP/API contracts.

Acceptance criteria:

1. Agents can tell whether semantic discovery is unavailable, disabled, enabled, model-mismatched,
   or populated.
2. Search/query results expose retrieval mode and semantic caveats where needed.
3. Semantic-only hits are clearly labeled as discovery leads.
4. FTS hits are preserved when semantic candidates exist.
5. Impact, trace, changes, graph-search degree/risk semantics, suspects, and docs grounding are
   unchanged by semantic enablement.
6. Answerability benchmark scenarios demonstrate semantic lift on vocabulary mismatch or clearly
   show that a model/configuration did not earn the added cost.
7. Documentation and MCP contracts teach agents to verify semantic leads before editing.
