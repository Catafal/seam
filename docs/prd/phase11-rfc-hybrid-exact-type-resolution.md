# PRD - Phase 11 RFC: Hybrid Exact Type-Resolution

> Status: partially shipped in `codex/hybrid-exact-type-resolution`.
> Created: 2026-07-06.
> GitHub issue: https://github.com/Catafal/seam/issues/374.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/codememory-inspired-agent-answerability-roadmap.md`.
> Previous shipped slices: protocol edges, Docker/Compose infra graph, docs grounding.

## Implementation Status

This PRD remains the broader product spec. The first implementation slice ships:

1. Python and TypeScript/JavaScript same-file import-alias canonicalization before receiver target
   qualification.
2. Python `__init__` `self.field: Type = ...` and `self.field = Type()` receiver evidence.
3. TypeScript constructor parameter-property and `this.field = new Type()` receiver evidence.
4. Optional TypeScript receiver parameters remain unpromoted.
5. Exact receiver call-edge provenance values: `python-receiver-type`,
   `typescript-receiver-type`, and `javascript-receiver-type`.
6. `seam_graph_search` query-time confidence normalization for exact receiver provenance:
   selected-scope unique target -> `EXTRACTED`, selected-scope duplicate target -> `AMBIGUOUS`,
   missing target -> stored conservative confidence.
7. `seam_schema` support/population signals: `has_exact_receiver_provenance` means the schema can
   carry exact receiver provenance; `has_exact_receiver_edges` means this index currently contains
   populated exact receiver edges.

Not shipped in this first slice: new import-promotion algorithms, barrel/star/namespace import
exactness, context/impact/trace-specific display changes beyond benefiting from qualified stored
targets, or benchmark automation beyond focused regression tests and the full gate.

## Problem Statement

Seam's core promise is to help coding agents answer codebase-navigation and change-safety
questions with less token spend than broad grep/read loops. The graph already gives agents a
structured view of symbols, calls, imports, routes, config, resources, tests, exceptions, docs, and
architecture. But the quality of that graph still depends heavily on whether a call target can be
resolved to the exact in-repo symbol.

Today Seam deliberately keeps uncertainty visible through confidence levels:

1. `EXTRACTED` means the target is unique or directly resolved.
2. `AMBIGUOUS` means the target name collides across multiple indexed symbols.
3. `INFERRED` means the target is external, builtin, dynamic, or unresolved.

That honesty is good. The remaining problem is that agents still pay too much attention to
avoidable ambiguity. A method called `get`, `send`, `parse`, `run`, `load`, `save`, or `handle` can
exist in many files. When a caller has local type evidence proving that `client.send()` means
`NotificationClient.send`, Seam should not force the agent to read every `send` candidate. When an
import proves that `parse()` came from one local module, Seam should prefer that evidence over a
global name collision. When a receiver annotation proves `repo.load()` is on `UserRepository`, the
graph should preserve that exact target if the indexed symbol exists.

This is not a request to run a language server on every query, infer every dynamic type, or make
probabilistic guesses. The useful product gap is narrower:

> Use stronger local, static, explainable type and import evidence to promote only the edges that
> can be proven to an exact in-repo target, while leaving all uncertain calls ambiguous or inferred.

The user-facing pain is practical:

1. Agents get oversized `context`, `impact`, `trace`, and `graph-search` answers for common
   homonym names.
2. Agents spend extra tokens opening snippets for false candidate callees.
3. `impact` can look broader than the real blast radius when many symbols share a method name.
4. `trace` can include weak hops where local type evidence could have selected one exact target.
5. Maintainers cannot easily see whether a precise edge came from import promotion, receiver type
   inference, constructor binding, self/this field typing, or a fallback name-count rule.

The next roadmap phase should therefore improve exactness, not breadth. Missing edges are
acceptable. Wrong exact edges are not.

## Solution

Build a narrow hybrid exact type-resolution pass that promotes ambiguous or inferred call evidence
only when Seam can prove the exact in-repo target from local static evidence.

The feature should combine two existing sources of evidence:

1. Existing read-time confidence resolution, including import promotion and name-count fallback.
2. Existing index-time receiver/type inference for Python and TypeScript/JavaScript.

The implementation should turn that into a clearer product capability:

1. Preserve current confidence semantics.
2. Promote resolvable receiver method calls to exact qualified targets when the declaring type is
   known and the exact target symbol exists.
3. Preserve unresolved receiver method calls as bare targets with `AMBIGUOUS` or `INFERRED`
   confidence.
4. Expose how an exact edge was resolved through provenance such as import binding, receiver
   annotation, constructor binding, class field binding, self/this binding, or barrel import.
5. Canonicalize imported type aliases before qualifying receiver calls, so `from pkg import Client
   as C; x: C; x.send()` can resolve to `Client.send` when the in-repo target is proven.
6. Add constructor and dependency-injection field evidence for common assignments such as
   `self.client = Client()`, `self.client: Client = ...`, `this.client = new Client()`, and
   TypeScript constructor parameter properties.
7. Align graph-search confidence semantics with read-time confidence resolution, or clearly expose
   the distinction between stored lower-bound confidence and query-time promoted confidence.
8. Keep all heavier work inside indexing, sync, or explicit enrichment. Normal query paths must
   stay read-only and bounded.
9. Keep the first implementation to Python and TypeScript/JavaScript because those languages
   already have the relevant import and receiver inference foundations in Seam.

From an agent's perspective, the intended workflow after this phase is:

1. Ask `seam schema --json` and see that exact type-resolution support exists.
2. Ask `seam context Parser.parse --json` or `seam impact Parser.parse --json` and get fewer
   unrelated `parse` candidates.
3. Ask `seam graph-search --edge-kind call --confidence EXTRACTED --preview --json` and see exact
   call evidence with provenance.
4. Ask `seam trace` between symbols and get fewer weak hops when local type/import evidence proves
   the path.
5. Treat remaining `AMBIGUOUS` edges as intentional caveats rather than implementation gaps.

This PRD is a graph-quality phase. It does not add semantic embeddings, cross-repo analysis,
runtime tracing, a language-server daemon, or a new query language.

## User Stories

1. As an AI coding agent, I want method calls with exact local type evidence to point to the exact
   in-repo method, so that I do not inspect unrelated homonym methods.
2. As an AI coding agent, I want unresolved method calls to remain ambiguous, so that Seam does not
   pretend uncertain code is exact.
3. As an AI coding agent, I want exact call edges to expose resolution provenance, so that I can
   trust why an edge was promoted.
4. As an AI coding agent, I want `impact` to use exact edges when available, so that blast-radius
   output is smaller and more relevant.
5. As an AI coding agent, I want `trace` to prefer exact local targets when available, so that
   dependency paths avoid avoidable name-collision hops.
6. As an AI coding agent, I want `context` callees and callers to distinguish exact receiver-typed
   calls from name-count calls, so that I know which neighbors are high trust.
7. As an AI coding agent, I want `graph-search` to filter or preview exact call evidence, so that
   I can find high-confidence relationships without reading the entire graph.
8. As an AI coding agent, I want schema introspection to state whether exact type-resolution fields
   are supported, so that I can choose how much to rely on call precision.
9. As an AI coding agent, I want ambiguity counts before and after the pass, so that I can assess
   whether the index is precise enough for a change.
10. As an AI coding agent, I want exactness to be deterministic, so that repeated indexing of the
    same code yields the same graph.
11. As an AI coding agent, I want exactness to be local-first and offline, so that Seam remains safe
    inside private repositories.
12. As an AI coding agent, I want no language-server startup on query paths, so that MCP calls stay
    fast and bounded.
13. As an AI coding agent, I want Python `self.method()` to resolve to the enclosing class method
    when the target exists, so that class-internal calls become exact.
14. As an AI coding agent, I want Python `cls.method()` to resolve conservatively when class
    context is known, so that classmethod-style calls are not always bare homonyms.
15. As an AI coding agent, I want Python `self.client.send()` to resolve only when `client` has a
    plain local class type, so that dependency-injected fields become useful graph evidence.
16. As an AI coding agent, I want Python function parameters annotated with plain local types to
    help resolve receiver calls, so that `repo.load()` can become `UserRepository.load`.
17. As an AI coding agent, I want Python local variables assigned from plain constructors to help
    resolve receiver calls, so that `parser.parse()` after `parser = Parser()` becomes exact.
18. As an AI coding agent, I want Python optional, union, generic, container, dotted, and string
    annotations skipped, so that unsafe type evidence does not create false edges.
19. As an AI coding agent, I want TypeScript `this.method()` to resolve to the enclosing class
    method when static class context is known, so that class-local calls are precise.
20. As an AI coding agent, I want TypeScript constructor parameter properties to bind receiver
    types when they are plain local class/interface names, so that injected services can be traced.
21. As an AI coding agent, I want TypeScript class fields with plain type annotations to bind
    receiver types, so that `this.client.fetch()` can resolve exactly.
22. As an AI coding agent, I want TypeScript locals created with `new Client()` to bind receiver
    types, so that local object construction sharpens call edges.
23. As an AI coding agent, I want TypeScript union, generic, array, primitive, namespace, and
    chained unknown receivers skipped, so that exactness remains conservative.
24. As an AI coding agent, I want JavaScript files to receive only syntax-proven constructor and
    `this` evidence where safe, so that untyped JavaScript does not get speculative type guesses.
25. As an AI coding agent, I want aliased imports to keep resolving exact targets, so that
    `parse as parseConfig` still leads to the declaring function when safe.
26. As an AI coding agent, I want barrel re-export resolution to remain bounded and cycle-safe, so
    that exact imports do not hang on complex frontend module layouts.
27. As an AI coding agent, I want star imports and namespace imports to avoid exact promotion unless
    a separate safe rule is designed, so that broad imports do not create false precision.
28. As an AI coding agent, I want third-party imports to remain inferred or unresolved, so that Seam
    does not invent local targets for external libraries.
29. As an AI coding agent, I want exact receiver edges to keep raw receiver text, so that I can
    debug or explain a promoted edge.
30. As an AI coding agent, I want exact target qualification to be stable, so that stored graph
    facts do not depend on display formatting.
31. As an AI coding agent, I want duplicate method names across classes to stop causing ambiguous
    impact when receiver type proves the class, so that changes to one class do not look broader
    than they are.
32. As an AI coding agent, I want remaining ambiguous edges to include a best candidate hint only
    as a hint, so that proximity is not mistaken for proof.
33. As an AI coding agent, I want exactness to improve output size, so that MCP responses stay
    compact enough for commercial coding-agent workflows.
34. As an AI coding agent, I want exactness to improve answerability benchmark scenarios, so that
    graph-quality gains are measured instead of assumed.
35. As an AI coding agent, I want `seam plan` to benefit from exact edges, so that generated
    inspection and test plans are less noisy.
36. As an AI coding agent, I want `seam changes` to preserve its risk semantics while using better
    evidence, so that high-risk reports are not inflated by avoidable homonyms.
37. As an AI coding agent, I want old indexes without exact-resolution provenance to degrade
    gracefully, so that existing repositories keep working.
38. As an AI coding agent, I want exactness to require re-indexing only when extractor behavior
    changes, so that normal reads do not mutate state.
39. As an AI coding agent, I want imported type aliases to canonicalize to their exported in-repo
    type names before receiver qualification, so that alias syntax does not create fake unresolved
    targets.
40. As an AI coding agent, I want constructor-injected Python fields to count as receiver evidence
    when they are assigned from plain local constructors, so that `self.client.send()` in common
    service classes can be traced.
41. As an AI coding agent, I want constructor-injected TypeScript fields and parameter properties
    to count as receiver evidence when they are plain local types, so that dependency-injected
    service classes are not treated as opaque.
42. As an AI coding agent, I want optional TypeScript parameters to follow the same conservatism
    contract as other nullable/union evidence, so that optional receivers are not over-promoted.
43. As an AI coding agent, I want graph-search confidence output to avoid contradicting
    context/impact/trace confidence, so that I do not misread stored edge confidence as final
    read-time confidence.
44. As a human developer, I want fewer false callees in `seam context`, so that I can inspect code
    relationships quickly from the terminal.
45. As a human developer, I want fewer false affected symbols in `seam impact`, so that I can judge
    refactor scope faster.
46. As a human developer, I want provenance on promoted exact calls, so that I can debug a wrong or
    surprising edge.
47. As a human developer, I want clear caveats when type resolution is skipped, so that I know when
    to fall back to snippets.
48. As a Seam maintainer, I want receiver/type inference behind a deep module interface, so that it
    can be tested without database or transport code.
49. As a Seam maintainer, I want import promotion and receiver promotion to share a resolution
    vocabulary, so that docs and API contracts stay coherent.
50. As a Seam maintainer, I want no broad schema churn unless provenance cannot be represented
    safely in existing edge fields, so that the graph model stays stable.
51. As a Seam maintainer, I want fixtures for homonym-heavy code, so that precision gains are
    observable.
52. As a Seam maintainer, I want regression tests proving uncertain receiver cases stay ambiguous,
    so that future work does not over-promote.
53. As a Seam maintainer, I want integration tests for Python and TypeScript/JavaScript, so that
    exactness is tested where agents use it.
54. As a Seam maintainer, I want output-contract tests for `context`, `impact`, `trace`,
    `graph-search`, and `schema`, so that caller-facing behavior is stable.
55. As a Seam maintainer, I want performance tests or fixtures with many homonyms, so that the pass
    does not add expensive query-time work.
56. As a future semantic-search implementer, I want exact graph precision stable first, so that
    semantic discovery does not compensate for avoidable graph ambiguity.
57. As a future cross-repo implementer, I want single-repo exactness stable first, so that
    workspace-level matching does not compound local ambiguity.
58. As a future graph-artifact implementer, I want exact-resolution provenance in artifacts, so
    that imported indexes preserve trust semantics.

## Implementation Decisions

- Treat this as a graph-quality phase, not a new user-facing product surface.
- Keep the first scope to Python and TypeScript/JavaScript.
- Preserve the three existing confidence levels: `EXTRACTED`, `AMBIGUOUS`, and `INFERRED`.
- Promote only exact in-repo targets that can be proven from local static evidence.
- Never use proximity, name similarity, semantic search, or path naming as proof of exact type.
- Keep proximity as an ambiguous-edge hint only.
- Continue resolving global name-count and import promotion at read time where that is the existing
  source of truth.
- Continue capturing receiver/type evidence during indexing where source syntax provides it.
- Add or tighten provenance for exact receiver promotion so agents can see why an edge is exact.
- Keep raw receiver text on call edges even after exact qualification.
- Prefer additive metadata over changing existing edge identity.
- Do not change edge endpoints to row IDs in this phase.
- Avoid new MCP tools. Improve existing schema, graph search, context, impact, trace, plan, and
  docs surfaces.
- If a schema field is required, make it additive and backward-compatible.
- If existing edge provenance can carry the explanation cleanly, prefer that over a new table.
- If a dedicated resolution metadata table is required, keep it keyed to existing edge evidence and
  treat it as a read model, not a new graph.
- Keep exact type-resolution out of semantic embeddings.
- Keep exact type-resolution independent from docs grounding.
- Keep query paths bounded and read-only.
- Keep all optional heavier analysis behind index/sync/enrichment operations.
- Do not run a language server, type checker, package manager, compiler, bundler, or project code
  during normal indexing.
- Do not inspect third-party package source to prove targets in the first implementation.
- Python exact receiver evidence should include self/cls class context, plain parameter
  annotations, plain local variable annotations, plain constructor assignments, and order-
  independent class field pre-scans.
- Python should skip optional, union, generic, container, string, dotted, and dynamic annotations.
- TypeScript exact receiver evidence should include this class context, plain field annotations,
  plain parameter annotations, constructor parameter properties where supported, assignment to
  `this.field = new ClassName()`, and plain `new ClassName()` locals.
- TypeScript should skip primitive, union, generic, array, namespace, imported namespace, and
  dynamic receiver shapes unless a later RFC proves a safe exact rule.
- TypeScript optional parameters should be treated as nullable evidence unless the implementation
  can prove the receiver is definitely assigned before use. The first slice should skip optional
  receiver promotion rather than over-promote.
- JavaScript support should remain narrower than TypeScript and should only promote syntax-proven
  constructor/self evidence that cannot reasonably imply another target.
- Import promotion should remain bounded, including barrel/re-export depth and candidate caps.
- Imported type aliases should be canonicalized before target qualification. The first slice uses
  same-file alias syntax as the canonicalization source, then relies on query-time target uniqueness
  before surfacing exact receiver edges as `EXTRACTED`. A future import-promotion slice can tighten
  this to require a uniquely resolved in-repo declaring import source before canonicalization.
- Namespace imports and star imports remain non-promoting unless a later design adds a proof rule.
- Graph search currently reads stored edge confidence while traversal-style tools perform read-time
  promotion. This PRD should either align graph-search confidence with the read-time resolver for
  call edges, or expose both fields clearly enough that agents cannot confuse them.
- Existing field-access and instantiation extraction should not be broadened as part of this PRD
  unless required to preserve call-resolution correctness.
- Existing route, HTTP-call, config/resource, infra, exception, test, docs, and semantic behavior
  should remain unchanged except where they consume more precise call confidence.
- `impact` should benefit from better edge confidence without changing its public risk vocabulary.
- `trace` should prefer stronger exact paths where existing traversal ranking already supports
  stronger confidence.
- `context` and `context_pack` should expose promoted exact evidence without hiding caveats for
  remaining weak edges.
- `schema` should report support for exact receiver/type provenance separately from generic
  confidence support if a new capability is added.
- Documentation should teach agents that exact type-resolution means "static local proof," not
  runtime truth.

## Testing Decisions

- Tests should assert external behavior: persisted edges, confidence/provenance, and query output.
- Tests should not assert private AST traversal order.
- The receiver/type inference module should have focused unit tests because it is the deep module
  that controls conservatism.
- Import-promotion tests should continue to prove imported homonyms can become exact.
- Import-promotion tests should prove unimported homonyms remain ambiguous.
- Import-promotion tests should prove third-party imports remain inferred or unresolved.
- Import-promotion tests should prove star imports do not promote exact calls.
- Import-promotion tests should prove alias imports preserve target evidence.
- Barrel tests should continue to prove bounded depth, cycle safety, and no over-promotion.
- Python tests should cover `self.method()` resolving to an enclosing class method.
- Python tests should cover `cls.method()` only when class context is known.
- Python tests should cover parameter annotation receiver resolution.
- Python tests should cover local annotated variable receiver resolution.
- Python tests should cover local constructor assignment receiver resolution.
- Python tests should cover class field annotation and constructor field evidence.
- Python tests should cover `self.field = Client()` and `self.field: Client = ...` inside
  `__init__` when those patterns are supported.
- Python tests should cover imported type alias canonicalization before receiver qualification.
- Python tests should cover unresolved receivers staying bare and ambiguous/inferred.
- Python tests should cover optional, union, generic, container, dotted, and string annotations
  being skipped.
- Python tests should cover duplicate method names across two classes where typed receiver evidence
  selects only one class.
- TypeScript tests should cover `this.method()` resolving to the enclosing class method.
- TypeScript tests should cover typed class fields resolving receiver calls.
- TypeScript tests should cover typed parameters resolving receiver calls.
- TypeScript tests should cover constructor parameter properties if supported.
- TypeScript tests should cover `this.field = new Client()` inside a constructor or method when
  that pattern is supported.
- TypeScript tests should cover optional receiver parameters being skipped.
- TypeScript tests should cover imported type alias canonicalization before receiver qualification.
- TypeScript tests should cover local `new ClassName()` assignments resolving receiver calls.
- TypeScript tests should cover duplicate method names across classes with exact typed receiver
  selection.
- TypeScript tests should cover union, generic, array, primitive, namespace, and unknown chained
  receivers being skipped.
- JavaScript tests should cover only conservative constructor/self cases that are explicitly
  supported.
- Query tests should verify `context` callers/callees become smaller or more precise in homonym
  fixtures.
- Impact tests should verify avoidable ambiguous blast radius is reduced in exact receiver
  fixtures.
- Trace tests should verify stronger exact paths are preferred over ambiguous alternatives when
  both are available.
- Graph-search tests should verify confidence and provenance filtering or preview output for exact
  call edges, including the chosen contract for stored versus read-time confidence.
- Schema tests should verify any new capability or provenance field in populated, empty, and old
  index states.
- API contract tests should verify MCP/Web schemas if output models change.
- Regression tests should verify existing route, HTTP-call, config/resource, infra, exception,
  test-edge, docs-grounding, and graph-search behavior still passes.
- Answerability benchmark tests should add at least two graph-quality scenarios: one Python
  homonym receiver case and one TypeScript homonym receiver case.
- Performance tests should include a homonym-heavy fixture and prove no per-edge query explosion.
- Good tests should prefer tiny synthetic projects with clear local symbols over snapshots of real
  applications.

## Out of Scope

- Running a language server daemon.
- Running mypy, pyright, tsc, eslint, bundlers, package managers, or project code.
- Importing user modules.
- Runtime trace ingestion.
- Semantic embeddings as dependency evidence.
- Probabilistic target selection.
- Proximity promotion from ambiguous to exact.
- Cross-repo type resolution.
- Third-party package source indexing.
- Full Python data-flow analysis.
- Full TypeScript type-system evaluation.
- TypeScript generic instantiation solving.
- Union narrowing.
- Control-flow-sensitive type refinement.
- Attribute-chain solving beyond the existing conservative self/this field contract.
- Reflection, dependency injection container, decorator, metaclass, or framework magic solving.
- Changing default impact risk vocabulary.
- Replacing confidence semantics.
- Replacing name-keyed graph edges with resolved row IDs.
- New Cypher/query-language support.
- New semantic graph edges.
- UI-only Explorer redesign.
- Docker/Compose infra graph changes.
- Graph artifact export/import changes.
- Installer changes.

## Further Notes

- Current roadmap position: HTTP protocol edges and Docker/Compose infra graph have closed PRD
  issues and shipped implementation surfaces. The next ranked CodeMemory-inspired recommendation is
  Hybrid Exact Type-Resolution.
- Current Seam state: schema v16 supports edge confidence, receiver columns, import mappings,
  search text, synthesized edges, routes, HTTP calls, config/resources, infra, docs grounding,
  tests, and exceptions. This PRD should improve graph precision without expanding the product
  boundary.
- Current foundation: Seam already has read-time confidence resolution, import promotion, barrel
  chasing, proximity hints for residual ambiguity, and conservative receiver/type inference for
  Python and TypeScript/JavaScript.
- Current gap: exactness exists in pieces, but it is not yet productized as a measured graph-quality
  phase with capability reporting, provenance consistency, answerability scenarios, and explicit
  guardrails against over-promotion.
- Sub-agent audit finding: graph-search currently uses stored edge confidence while
  traversal-style tools use read-time confidence promotion. The implementation must make this
  contract explicit or align the surfaces so agents do not receive contradictory confidence
  signals.
- Sub-agent audit finding: imported type aliases and constructor/DI field evidence are the highest
  leverage first-slice gaps because they address common real-world homonym cases without requiring
  a language-server integration.
- The recommended deep modules are:
  - a receiver/type evidence module that remains pure and testable;
  - an exact-target promotion policy that maps evidence to confidence/provenance;
  - a query-surface adapter that exposes exactness without changing traversal contracts;
  - an answerability fixture set for homonym-heavy Python and TypeScript/JavaScript projects.
- The first acceptance gate should be an end-to-end fixture with two classes that share method
  names, one caller with exact typed receiver evidence, one caller without enough evidence, and
  assertions across context, impact, trace, graph search, schema, and stored provenance.
- The failure mode to optimize against is a wrong exact edge. A missed exact edge is acceptable and
  should stay visible as `AMBIGUOUS` or `INFERRED`.
