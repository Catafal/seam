# PRD: Phase 11 P3.4 — Raises And Exception Edges

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P3.4.
> Competitive source: `DeusData/codebase-memory-mcp` models richer code relationships as graph
> evidence beyond calls/imports.
> Status: ready-for-agent.
> GitHub issue: https://github.com/Catafal/seam/issues/155.
> Tracker label: `ready-for-agent`.
> Schema target: no mandatory migration. Add conservative exception edge vocabulary and read-path
> visibility first; add an optional metadata table only if implementation review proves edge rows
> cannot preserve the necessary evidence.

## Problem Statement

Seam can explain symbols, calls, imports, inheritance, field access, route/config/resource evidence,
and repository structure. It still cannot answer a common maintenance question directly: "which
code can raise this error, and where is it handled?"

From the user's perspective, this creates repeated friction:

- agents must grep for `raise`, `throw`, `except`, and `catch` manually;
- error paths are invisible in `seam_graph_search`, `seam_architecture`, Explorer, and impact
  workflows;
- call graphs can show the happy path while hiding the failure path that actually determines
  behavior;
- API and config surfaces can be visible while their error contracts remain implicit;
- exception-heavy modules look like ordinary code even when they are important control-flow
  boundaries;
- broad exception handlers such as `except Exception` or untyped `catch` blocks are hard to find
  without text search;
- custom exception classes can be defined and used across files, but Seam does not connect the
  defining class to the raising or catching code;
- agents changing low-level functions cannot ask whether their exceptions are part of a wider
  contract;
- future affected-test, architecture, and 3D graph views need error-flow evidence as graph data,
  not as one-off source snippets.

Seam needs conservative raises/exception relationships that make visible error behavior queryable
without pretending to solve full runtime exception propagation.

## Solution

Add exception-flow graph evidence for high-confidence static cases.

The feature should index:

- Python `raise SomeError`, `raise SomeError(...)`, `raise module.SomeError(...)`, and typed
  `except SomeError` or `except (A, B)` handlers;
- TypeScript/JavaScript `throw new Error(...)`, `throw new CustomError(...)`, `throw Error(...)`,
  and conservative `catch` evidence where the caught type can be inferred without guessing;
- optional Java/C#/similar language support only when tree-sitter nodes expose direct thrown or
  caught type names with low implementation cost;
- custom exception definitions that are already normal class symbols, using existing symbol rows
  instead of inventing a parallel exception-node model;
- external/builtin exception names as edge targets when they are explicit in source, even when the
  target symbol is not present in the local index.

The graph should expose:

- `raises` edges from the nearest enclosing function/method/class/module symbol to the explicit
  exception type or constructor name;
- `catches` edges from the nearest enclosing function/method/class/module symbol to explicit
  exception types handled in `except`/typed catch clauses;
- confidence on each edge:
  - `EXTRACTED` for direct literal exception type references;
  - `INFERRED` for simple constructor/call shapes where the type name is visible but not locally
    resolved;
  - `AMBIGUOUS` when multiple local symbols share the same exception target name;
- line/file evidence through the existing edge contract;
- schema, graph search, architecture, MCP, CLI, Web, Explorer, and docs updates so the new edge
  kinds are visible through existing typed surfaces.

The first version is deliberately conservative. It should prefer missing dynamic error behavior
over inventing exception edges. It should not attempt whole-program propagation, variable data
flow, runtime import resolution, or semantic interpretation of arbitrary thrown values.

The intended workflow becomes:

1. Call `seam_schema` and see `raises`/`catches` edge support, counts, and guidance.
2. Call `seam_graph_search --edge-kind raises --preview` to find code that raises explicit
   exceptions.
3. Call `seam_graph_search --edge-kind catches --preview` to find explicit handlers and broad
   catches.
4. Call `seam_architecture --section exceptions` to get a compact error-flow summary.
5. Use `seam_context`, `seam_snippet`, `seam_trace`, `seam_impact`, and Explorer on returned
   symbols exactly like other graph evidence.

## User Stories

1. As an AI coding agent, I want `raises` edges in the Seam graph, so that I can discover code
   that explicitly raises errors.
2. As an AI coding agent, I want `catches` edges in the Seam graph, so that I can discover code
   that explicitly handles errors.
3. As an AI coding agent, I want exception edges to use the existing graph tools, so that I do not
   need a separate exception-specific query language.
4. As an AI coding agent, I want `edge_kind=raises` to work in graph search, so that raising code
   is discoverable through typed filters.
5. As an AI coding agent, I want `edge_kind=catches` to work in graph search, so that handler code
   is discoverable through typed filters.
6. As an AI coding agent, I want Python `raise ValueError(...)` indexed, so that common explicit
   errors are visible.
7. As an AI coding agent, I want Python `raise CustomError` indexed, so that direct class raises
   are visible.
8. As an AI coding agent, I want Python `raise package.CustomError(...)` indexed conservatively, so
   that module-qualified errors keep their useful target name.
9. As an AI coding agent, I want Python `except CustomError` indexed, so that handled custom errors
   are visible.
10. As an AI coding agent, I want Python `except (A, B)` indexed as separate catch edges, so that
    multi-exception handlers remain queryable.
11. As an AI coding agent, I want Python broad handlers such as `except Exception` indexed, so that
    broad error swallowing can be found.
12. As an AI coding agent, I want bare `raise` handled conservatively, so that re-raises are not
    misrepresented as new exception types.
13. As an AI coding agent, I want TypeScript/JavaScript `throw new Error(...)` indexed, so that
    frontend and Node failure paths are visible.
14. As an AI coding agent, I want TypeScript/JavaScript `throw new CustomError(...)` indexed, so
    that custom error constructors are visible.
15. As an AI coding agent, I want TypeScript/JavaScript `throw Error(...)` indexed, so that common
    non-`new` error construction is covered.
16. As an AI coding agent, I want non-error thrown values skipped or marked low-confidence, so that
    `throw "bad"` does not pollute the exception graph as a fake type.
17. As an AI coding agent, I want explicit exception target names preserved, so that I can search
    for all code raising `NotAGitRepoError`.
18. As an AI coding agent, I want custom exception classes to remain normal class symbols, so that
    snippets/context/impact keep working without a new node model.
19. As an AI coding agent, I want external exception types to appear as unresolved edge targets
    when explicit, so that builtin and dependency errors are still visible.
20. As an AI coding agent, I want line numbers on exception edges, so that I can jump to the exact
    `raise` or `except` site.
21. As an AI coding agent, I want exception edge confidence, so that direct local types are
    distinguishable from unresolved external names.
22. As an AI coding agent, I want ambiguous exception targets marked `AMBIGUOUS`, so that duplicated
    class names do not look resolved.
23. As an AI coding agent, I want exception edge extraction to be local and deterministic, so that
    indexing does not execute project code.
24. As an AI coding agent, I want dynamic exception factories skipped, so that runtime-dependent
    behavior is not guessed.
25. As an AI coding agent, I want `seam_schema` to report exception edge capability, so that I know
    whether the current index was built with P3.4 support.
26. As an AI coding agent, I want old indexes to degrade gracefully, so that P3.4 read paths do not
    crash when exception edges are absent.
27. As an AI coding agent, I want architecture summaries to include exception hotspots, so that
    repo briefings expose important failure boundaries.
28. As an AI coding agent, I want architecture summaries to list broad catches, so that risky
    swallowing or generic handling is easy to inspect.
29. As an AI coding agent, I want architecture summaries to list frequently raised exception types,
    so that repeated error contracts stand out.
30. As an AI coding agent, I want architecture summaries to identify raise-heavy modules, so that
    error-heavy areas can be inspected before edits.
31. As an AI coding agent, I want route handlers with explicit raises to be discoverable, so that
    API error behavior is visible.
32. As an AI coding agent, I want config/resource code that raises setup errors to be discoverable,
    so that operational failure paths are visible.
33. As an AI coding agent, I want exception edges to work without embeddings, so that error-flow
    discovery remains deterministic.
34. As an AI coding agent, I want `seam_trace` to use exception edges when requested, so that I can
    inspect paths involving error contracts.
35. As an AI coding agent, I want `seam_impact` not to over-include exception edges by default if
    that would create noisy blast-radius reports, so that existing impact behavior stays useful.
36. As an AI coding agent, I want an explicit decision on whether exception edges participate in
    default impact traversal, so that graph semantics are predictable.
37. As an AI coding agent, I want Explorer to render `raises` and `catches`, so that error behavior
    is visible in the UI graph.
38. As an AI coding agent, I want Explorer edge filters for exception edges, so that I can isolate
    failure paths from normal calls/imports.
39. As an AI coding agent, I want exception detail payloads to include source file, line,
    confidence, and target name, so that I can verify evidence quickly.
40. As a human developer, I want `seam graph-search --edge-kind raises`, so that I can audit error
    sites from the terminal.
41. As a human developer, I want `seam graph-search --edge-kind catches`, so that I can audit
    handlers from the terminal.
42. As a human developer, I want to find all code raising a custom project exception, so that I can
    understand an error contract before changing it.
43. As a human developer, I want to find all code catching a broad exception type, so that I can
    review potentially risky handlers.
44. As a human developer, I want exception extraction to be conservative, so that I trust the graph
    more than I trust a noisy regex report.
45. As a human developer, I want exception results to be sorted and paginated like other graph
    results, so that large repos remain usable.
46. As a Seam Explorer user, I want exception edge colors and filters, so that failure paths are
    visually distinct from normal execution edges.
47. As a Seam Explorer user, I want exception edges to appear in previews, so that I can inspect
    local error neighborhoods without opening source first.
48. As a Seam maintainer, I want exception extraction in a deep module, so that language-specific
    raise/catch behavior can be tested without DB or transport setup.
49. As a Seam maintainer, I want exception extraction to reuse existing symbol attribution helpers,
    so that raise/catch edges attach to the same owners as call/config/route evidence.
50. As a Seam maintainer, I want no mandatory schema migration for the first pass, so that P3.4
    stays cheap and additive.
51. As a Seam maintainer, I want a migration only if edge rows cannot encode required evidence, so
    that schema complexity is justified by a concrete read-path need.
52. As a Seam maintainer, I want parser tests for Python raises and except handlers, so that the
    most common backend language is covered behaviorally.
53. As a Seam maintainer, I want parser tests for TypeScript/JavaScript throws, so that frontend
    and Node errors are covered behaviorally.
54. As a Seam maintainer, I want graph-search tests for new edge kinds, so that typed discovery
    remains stable.
55. As a Seam maintainer, I want schema tests for edge capability reporting, so that agents get
    truthful guidance.
56. As a Seam maintainer, I want architecture tests for exception summaries, so that the feature is
    visible in repo briefings.
57. As a Seam maintainer, I want web API and generated TypeScript types updated if contracts change,
    so that Explorer remains type-safe.
58. As a Seam maintainer, I want docs and API contracts updated, so that agents learn to use
    exception edges instead of broad text search.
59. As a future test-edge implementer, I want exception edges to be separate from coverage edges, so
    that test behavior and runtime failure behavior are not conflated.
60. As a future full-Cypher evaluator, I want exception questions covered by typed tools first, so
    arbitrary graph queries are not added just to answer error-flow questions.

## Implementation Decisions

- Build one deep exception-flow extraction module that accepts language, AST root, file path, and
  extracted symbols, then returns exception edges.
- Keep the module transport-neutral and testable without CLI, MCP, Web, watcher, or database code.
- Run exception extraction from the indexing pipeline after normal symbols are known, so raise/catch
  edges can attach to the nearest enclosing function, method, class, or module-level owner.
- Add `raises` and `catches` to the accepted edge vocabulary.
- Do not add `exception` as a new symbol kind in the first pass. Project-defined exceptions are
  already class symbols; builtin and dependency exceptions can remain unresolved edge targets.
- Do not add a dedicated exception metadata table in the first pass unless implementation review
  proves that the existing edge contract cannot support required source/line/confidence evidence.
- Use deterministic target names:
  - constructor or class name for `raise ErrorType(...)` / `throw new ErrorType(...)`;
  - dotted or final component for module-qualified errors, with the choice documented and tested;
  - separate targets for tuple/multi-catch handlers.
- Treat bare Python `raise` as a re-raise. Skip it unless the enclosing handler has a statically
  known caught type and the implementation can represent the edge without ambiguity.
- Skip dynamic exception factories, variable-only thrown values, conditional exception type
  selection, and arbitrary expression targets.
- Confidence rules should be conservative:
  - `EXTRACTED` when the target name is visible directly in the raise/catch syntax;
  - `INFERRED` when the target is visible but unresolved outside the local symbol set;
  - `AMBIGUOUS` when same-file or whole-index resolution finds multiple possible local targets.
- The authoritative read-time confidence layer should continue to refine stored confidence where
  existing graph read paths already do that.
- Python support should start with `raise Name`, `raise Name(...)`, `raise module.Name(...)`,
  `except Name`, `except module.Name`, `except (A, B)`, and broad `except Exception`.
- TypeScript/JavaScript support should start with `throw new Name(...)`, `throw Name(...)`, and
  optionally typed catch forms only where the parser exposes a direct static type.
- Java/C#/Swift/Ruby/PHP exception support should be considered opportunistic only if it follows
  the same direct-name rule without destabilizing the first pass.
- `seam_schema` should report exception edge support through edge kind counts, capabilities,
  warnings, and tool guidance.
- `seam_graph_search` should accept `edge_kind=raises` and `edge_kind=catches`; previews,
  pagination, confidence filters, test/source filters, degree filters, and sorting should work
  without special transport logic.
- Add an `exceptions` architecture section that summarizes raised types, caught types, broad
  catches, raise-heavy symbols, and catch-heavy symbols from indexed edge rows.
- Decide explicitly whether `exceptions` belongs in the default architecture section list. The
  recommended first pass is to include it by default only if the summary stays compact and bounded.
- `seam_impact` should not silently change its default traversal semantics if that would widen
  blast-radius reports. If exception edges are excluded from default impact traversal, document how
  to include or inspect them through graph search.
- Explorer should consume `raises` and `catches` through the existing graph/search APIs where
  possible. UI work should be limited to typed edge labels, colors, filters, and detail rendering.
- The MCP, CLI, and Web transports should expose exception data through existing tools first. A
  separate `seam_exceptions` tool is out of scope unless implementation proves existing surfaces
  cannot represent the data cleanly.
- Full re-index should populate exception edges. Existing indexes should simply report zero
  exception edges until re-indexed.
- The feature must preserve Seam's local-first trust boundary: no code execution, no imports, no
  runtime exception probing, no test execution during indexing, and no network calls.

## Testing Decisions

- Tests should assert external behavior and graph contracts, not private AST traversal details.
- Good tests should use small fixtures with fake exception classes and minimal functions. Avoid
  large snapshots and avoid relying on incidental AST traversal order beyond deterministic public
  output sorting.
- Unit tests should cover the exception extraction module as a deep module:
  - Python direct raises;
  - Python constructor raises;
  - Python module-qualified raises;
  - Python direct except handlers;
  - Python tuple/multi-except handlers;
  - Python broad handlers;
  - Python bare re-raise behavior;
  - TypeScript/JavaScript `throw new Error`;
  - TypeScript/JavaScript `throw new CustomError`;
  - TypeScript/JavaScript `throw Error`;
  - skipped dynamic thrown values.
- Indexer tests should verify `raises` and `catches` edges are persisted with file, line,
  confidence, and target name.
- Graph-search tests should verify `edge_kind=raises`, `edge_kind=catches`, previews, invalid
  inputs, pagination, confidence filters, degree filters, and test/source filters.
- Schema tests should verify exception edge counts and capability reporting on populated, empty,
  and old indexes.
- Architecture tests should verify the exceptions section reports populated, empty, and unsupported
  states honestly.
- Web API tests should verify new edge kinds flow through existing graph payloads without breaking
  generated contracts.
- Explorer tests should verify exception edge filters and labels through visible behavior and typed
  data plumbing.
- Regression tests should verify existing call/import/field/inheritance/route/config/resource
  behavior remains stable after exception edge support is enabled.
- If no schema migration is added, tests should explicitly verify that opening a pre-P3.4 schema
  works and simply lacks the new edge rows.
- If a schema migration becomes necessary, migration tests should follow existing migration-test
  style and verify old databases preserve files, symbols, edges, comments, clusters, imports,
  embeddings, routes, configs, and resources.
- Validation before merge should include focused extractor/indexer/query tests, full backend gate,
  frontend typecheck/test if web contracts change, OpenAPI type regeneration if web schemas change,
  `seam sync`, and `seam changes --json`.

## Out of Scope

- Full runtime exception propagation through call graphs.
- Whole-program data-flow analysis for exception variables or factories.
- Inferring exception types from arbitrary thrown/caught variables.
- Modeling every possible language/framework exception idiom in the first pass.
- Creating a new `exception` symbol kind unless later evidence proves it is necessary.
- Adding a dedicated exception metadata table unless the edge contract is insufficient.
- Treating broad text matches in comments or strings as exception evidence.
- Executing project code, tests, imports, or server entry points to discover runtime exceptions.
- Network calls, dependency probing, package introspection, or external documentation lookup during
  indexing.
- Replacing call/import impact semantics with exception propagation semantics.
- Test coverage edges; that belongs to P3.3.
- Route extraction; that belongs to P3.1.
- Config/resource extraction; that belongs to P3.2.
- Full Cypher or arbitrary graph query language.
- A dedicated exception MCP tool unless typed graph/search/architecture surfaces prove
  insufficient.

## Further Notes

- P3.4 should be intentionally smaller than P3.1 and P3.2. The highest-value version is a
  trustworthy edge family, not a broad static-analysis engine.
- The main graph-quality risk is overclaiming. Static `raise CustomError` is useful evidence;
  guessing that a function can raise every exception thrown by every callee is not appropriate for
  this phase.
- The main implementation risk is mixing language-specific parser logic into transport or query
  code. Keep extraction as a deep module and let existing read surfaces consume normal edge rows.
- Exception edges should be especially useful for reviewing CLI/MCP/server error contracts, route
  handler behavior, and operational config failures once combined with the existing route and
  config/resource graph evidence.
- If P3.3 test edges land before P3.4 implementation begins, implementation should consider whether
  affected-test workflows can use exception edges as optional context. It should not block P3.4 on
  test-edge availability.
