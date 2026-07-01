# PRD: Phase 11 P1.1 — Schema Introspection (`seam_schema` / `seam schema`)

> Source roadmap: Phase 11 codebase-memory-inspired roadmap, P1.1.
> Competitive source: `DeusData/codebase-memory-mcp` exposes a graph-schema discovery
> tool that gives agents a safe first call before graph exploration.
> Schema target: no migration required. This is a read-only introspection feature over
> the current SQLite index and tool registry.

## Problem Statement

Agents currently enter a Seam-indexed repo without a machine-readable way to understand
what the index contains, which graph features are populated, which enrichment columns are
available, which MCP/CLI tools exist, and whether the index is fresh. That forces agents
to infer schema from documentation, make exploratory tool calls, or fall back to grep when
they are unsure.

From the user's perspective, this creates avoidable friction:

- an agent may call `seam_context` before knowing whether the index is stale;
- an agent may assume embeddings exist when the index was not built with semantic mode;
- an agent may treat clusters, synthesized edges, import mappings, or enrichment columns
  as available when they are absent or unpopulated;
- an agent may ask for the wrong tool because the available capabilities are described in
  docs rather than in a compact runtime contract;
- a human debugging an index must combine `seam status`, docs, SQL knowledge, and source
  inspection to answer "what does this Seam DB actually know?"

Seam needs a single, read-only schema/introspection primitive that makes the graph's
runtime capabilities explicit.

## Solution

Add a new schema discovery surface:

- MCP tool: `seam_schema`
- CLI command: `seam schema`
- Web/Explorer endpoint: schema payload for diagnostics and UI feature gating
- Core query module: a deep, testable introspection module that all transports call

The feature returns one structured payload describing:

- index identity and freshness;
- database schema version and Seam version;
- table/column availability;
- symbol kind counts;
- edge kind, confidence, and synthesis counts;
- file language counts;
- cluster, comment, import mapping, and embedding population;
- feature flags derived from the current DB shape and current population;
- available Seam tools and when to use them;
- recommended next calls for an agent;
- warnings about stale, empty, partial, missing, or model-mismatched index state.

This is not a new graph model. It does not add tables, mutate metadata, run migrations, or
re-index. It reads the existing index defensively and returns an honest capability map.

The intended first agent interaction becomes:

1. Call `seam_schema`.
2. Check `freshness.stale`.
3. Check whether the desired capability is populated.
4. Pick the recommended next tool.
5. Continue with `seam_search`, `seam_context`, `seam_impact`, `seam_trace`,
   `seam_context_pack`, or later P1 tools.

## User Stories

1. As an AI coding agent, I want to call `seam_schema` first, so that I know what the Seam
   index contains before I choose a deeper tool.
2. As an AI coding agent, I want the schema payload to tell me whether the index is stale,
   so that I do not trust outdated graph results.
3. As an AI coding agent, I want a freshness hint in the schema payload, so that I know
   whether to ask for `seam sync` or `seam init`.
4. As an AI coding agent, I want the database schema version, so that I know which
   enrichment fields and tables may exist.
5. As an AI coding agent, I want the package/runtime Seam version, so that I can include it
   in bug reports and compatibility decisions.
6. As an AI coding agent, I want symbol counts by kind, so that I know whether the graph
   contains functions, methods, classes, interfaces, fields, and types.
7. As an AI coding agent, I want edge counts by kind, so that I know whether the graph is
   mostly calls/imports or has richer relationship data like reads, writes, holds, uses,
   instantiates, extends, and implements.
8. As an AI coding agent, I want edge counts by confidence, so that I know whether graph
   conclusions are mostly extracted, inferred, or ambiguous.
9. As an AI coding agent, I want synthesized-edge counts, so that I know whether dynamic
   dispatch synthesis ran and how much of the graph is heuristic.
10. As an AI coding agent, I want file counts by language, so that I know which languages
    Seam actually indexed in the repo.
11. As an AI coding agent, I want comment marker counts, so that I know whether `seam_why`
    is likely to return meaningful WHY/HACK/NOTE/TODO/FIXME context.
12. As an AI coding agent, I want cluster counts and cluster population, so that I know
    whether cluster-aware tools and Explorer labels are useful.
13. As an AI coding agent, I want import mapping counts, so that I know whether import
    resolution evidence exists.
14. As an AI coding agent, I want embedding counts by model, so that I know whether semantic
    hybrid search can run.
15. As an AI coding agent, I want to know whether stored embedding models match the current
    configured model, so that I do not over-trust stale semantic results.
16. As an AI coding agent, I want a list of known tables and columns, so that I can reason
    about index capability without reading SQL docs.
17. As an AI coding agent, I want column nullability and presence metadata, so that I know
    whether fields like signatures, decorators, visibility, receiver, search text, or
    synthesized provenance may be absent.
18. As an AI coding agent, I want feature booleans derived from DB shape and population, so
    that I can branch on `has_embeddings`, `has_clusters`, or `has_synthesized_edges`
    without interpreting raw counts myself.
19. As an AI coding agent, I want the schema tool to distinguish "table exists but empty"
    from "table missing," so that I know whether a capability is unsupported or merely not
    populated.
20. As an AI coding agent, I want the schema tool to work on older indexes, so that I get
    an upgrade hint instead of a crash.
21. As an AI coding agent, I want the schema tool to work on empty but initialized indexes,
    so that I can diagnose a repo with zero indexed source files.
22. As an AI coding agent, I want the schema tool to return a bounded payload, so that the
    first tool call does not waste the context it is meant to save.
23. As an AI coding agent, I want a lean/default mode that avoids dumping every column when
    I only need high-level capability, so that I can keep the first call small.
24. As an AI coding agent, I want a verbose mode that includes table and column metadata, so
    that I can debug schema/version issues when needed.
25. As an AI coding agent, I want a stable response shape, so that I can rely on it across
    Seam versions.
26. As an AI coding agent, I want stable error codes for no index, DB open failure, and
    invalid inputs, so that I can recover programmatically.
27. As an AI coding agent, I want the MCP tool and CLI command to return the same data, so
    that I can use either transport without divergent behavior.
28. As an AI coding agent, I want `seam schema --json` to use the existing CLI JSON
    envelope, so that it matches the other CLI read commands.
29. As an AI coding agent, I want `seam schema --quiet` to print the few highest-signal
    facts, so that I can do a low-token health check.
30. As an AI coding agent, I want the schema payload to list available tools, so that I can
    choose the correct next call without reading documentation.
31. As an AI coding agent, I want each listed tool to include a short "use when" hint, so
    that I do not call `seam_search` when `seam_impact` or `seam_context_pack` is more
    appropriate.
32. As an AI coding agent, I want the schema payload to recommend next calls, so that I can
    follow the intended Seam workflow.
33. As an AI coding agent, I want warnings when the index is stale, empty, lacks embeddings,
    or has embedding model mismatch, so that I do not silently misuse the graph.
34. As an AI coding agent, I want warnings when synthesis columns exist but no synthesized
    edges are present, so that I know dynamic-dispatch synthesis may not have run or may
    have found nothing.
35. As an AI coding agent, I want warnings when clusters are absent, so that I do not rely
    on cluster peers or architecture grouping.
36. As an AI coding agent, I want warnings when enrichment columns are present but mostly
    null, so that I know the index may need a full re-index.
37. As a human developer, I want `seam schema` to show a readable summary, so that I can
    diagnose an index without opening SQLite.
38. As a human developer, I want `seam schema --json`, so that I can attach the payload to
    issue reports or CI artifacts.
39. As a Seam maintainer, I want a single deep introspection module, so that CLI, MCP, and
    Web schema output cannot drift.
40. As a Seam maintainer, I want the module to use defensive SQL introspection, so that
    older indexes and partially migrated indexes degrade gracefully.
41. As a Seam maintainer, I want the feature to avoid importing MCP dependencies into the
    core CLI path, so that pure-CLI installs remain usable.
42. As a Seam maintainer, I want the MCP tool count expectation updated, so that tests
    reflect the new tool instead of failing on a stale count.
43. As a Seam maintainer, I want docs and API contracts updated, so that the schema tool is
    discoverable to agents and humans.
44. As a Seam maintainer, I want tests that assert behavior rather than SQL internals, so
    that the implementation can change without breaking useful guarantees.
45. As a Seam maintainer, I want the schema tool to never mutate the database, so that it is
    safe as a first call from any agent host.
46. As a Seam maintainer, I want the schema tool to never make network calls, so that it
    preserves Seam's local-first trust boundary.
47. As a Seam maintainer, I want the schema tool to tolerate DB read errors per section, so
    that one missing optional table does not make the whole payload useless.
48. As a Seam Explorer user, I want a web schema endpoint, so that the UI can enable,
    disable, or annotate features based on actual index capabilities.
49. As a Seam Explorer user, I want the UI to show schema/freshness warnings from the same
    payload agents use, so that human and agent diagnostics agree.
50. As a future P1 implementer, I want `seam_schema` to advertise later P1 tools when they
    ship, so that the schema surface remains the agent's first-stop capability map.

## Implementation Decisions

- Build one deep read-only introspection module with a small public interface:
  `describe_schema(connection, root, mode) -> SchemaDescription`.
- `mode` supports at least `summary` and `verbose`.
  - `summary` is the default and returns counts, capabilities, warnings, tools, and
    recommended next calls.
  - `verbose` includes table and column metadata for debugging older or partial indexes.
- The module owns all SQL introspection and count queries. Transports must not duplicate
  schema/count logic.
- The module must use defensive database inspection rather than assuming current schema:
  table existence, column existence, and optional feature tables are checked before
  querying them.
- The feature does not require a schema migration. It reads current and older index shapes.
- The feature does not backfill missing data. It reports what exists and what appears
  unpopulated.
- The feature is read-only. It must not run `CREATE`, `ALTER`, `INSERT`, `UPDATE`,
  `DELETE`, migrations, clustering, embedding, or indexing work.
- The feature should treat the current schema version in metadata as data, not as proof
  that every table or column exists. The payload should be honest about actual DB shape.
- The payload should include both raw facts and derived capability booleans. Agents should
  not have to infer core capability from counts alone.
- The schema payload should include a `freshness` object using the same staleness source of
  truth as existing traversal tools and status output.
- The schema payload should include `warnings` as a list of structured objects, not just
  prose strings.
- Warning codes should be stable and specific, for example:
  - `INDEX_STALE`
  - `INDEX_EMPTY`
  - `NO_CLUSTERS`
  - `NO_EMBEDDINGS`
  - `EMBEDDING_MODEL_MISMATCH`
  - `NO_SYNTHESIZED_EDGES`
  - `MISSING_OPTIONAL_TABLE`
  - `MISSING_OPTIONAL_COLUMN`
  - `ENRICHMENT_MOSTLY_NULL`
- The schema payload should include `recommended_next_calls`, ordered by likely usefulness:
  schema first, then status/sync guidance if stale, then search/context/impact/trace/pack
  depending on the user's task.
- Tool guidance should be maintained in a small registry used by the schema module and MCP
  docs generation where practical.
- The initial tool registry should cover all current Seam tools and identify:
  - tool name,
  - transport availability,
  - short description,
  - "use when" guidance,
  - whether the tool is read-only,
  - whether it depends on optional population such as clusters, embeddings, comments, or
    changed files.
- The MCP tool should be named `seam_schema`.
- The CLI command should be named `seam schema`.
- The CLI command should support:
  - `--json` for the existing structured JSON envelope,
  - `--quiet` for a terse health summary,
  - `--verbose` for column/table metadata,
  - the same index path and DB-dir conventions as other read commands.
- The MCP tool should support:
  - `verbose: bool = false`,
  - optional section selection only if needed to keep payloads small.
- The Web/Explorer endpoint should return the same core payload and may use it to gate UI
  affordances.
- The handler layer should stay thin: validate input, call the introspection module,
  relativize or normalize any paths if needed, and return the payload.
- The MCP boundary should use the existing error normalization behavior.
- The CLI should use the existing JSON and quiet output helpers.
- The pure CLI installation profile must not import MCP-only dependencies because of this
  feature.
- The payload must not include absolute source paths by default. It may include the project
  root only in human CLI output if existing status behavior already does so; JSON/MCP should
  prefer relative and capability data.
- The payload must not include source code, docstrings, comments, environment values, or
  secret-like data. Counts and column/table names are enough.
- The payload must not expose embedding vectors.
- The payload should cap or summarize high-cardinality maps. Current expected maps such as
  symbol kinds, edge kinds, languages, marker counts, and embedding models are small; if
  future maps grow, the schema module should truncate honestly.
- The tool count test must be updated from the current count to include `seam_schema`.
- API contract documentation should be updated so agents know `seam_schema` is the intended
  first call.
- CLI/help documentation should describe `seam schema` as an index capability/health
  command, not as a migration or schema-generation command.

Suggested response shape:

```json
{
  "schema_version": 12,
  "seam_version": "0.2.0",
  "freshness": {
    "stale": false,
    "reason": null,
    "hint": null
  },
  "counts": {
    "files": 317,
    "symbols": 5928,
    "edges": 30366,
    "clusters": 516,
    "comments": 0,
    "import_mappings": 0,
    "embeddings": 0
  },
  "breakdowns": {
    "languages": {},
    "symbol_kinds": {},
    "edge_kinds": {},
    "edge_confidence": {},
    "synthesized_edges": {}
  },
  "capabilities": {
    "has_clusters": true,
    "has_comments": false,
    "has_import_mappings": true,
    "has_embeddings": false,
    "has_synthesized_edges": true,
    "has_field_symbols": true,
    "has_receiver_column": true,
    "has_search_text": true
  },
  "tools": [
    {
      "name": "seam_search",
      "read_only": true,
      "use_when": "You know a keyword but not the exact symbol name."
    }
  ],
  "recommended_next_calls": [
    "Use seam_search for keyword discovery.",
    "Use seam_context before editing a symbol.",
    "Use seam_impact before changing an existing symbol."
  ],
  "warnings": []
}
```

The exact field names may be refined during implementation, but the contract must preserve
the same categories: identity, freshness, counts, breakdowns, capabilities, tools,
recommendations, warnings, and verbose table/column metadata.

## Testing Decisions

A good test for this feature asserts the externally visible schema description, not the
exact SQL used to compute it. The behavior that matters is whether the payload honestly
describes a given database shape and population level.

Test the deep introspection module directly:

- fresh initialized DB returns current schema version and empty counts without crashing;
- populated fixture returns correct file, symbol, edge, cluster, comment, import mapping,
  and embedding counts;
- symbol kind counts are grouped correctly;
- edge kind counts are grouped correctly;
- edge confidence counts are grouped correctly;
- synthesized-edge counts distinguish static edges from synthesized edges;
- embedding model counts and configured-model mismatch are reported correctly;
- missing optional tables are reported as unavailable, not as unhandled exceptions;
- missing optional columns are reported as unavailable, not as unhandled exceptions;
- table exists but empty is distinguishable from table missing;
- stale index returns a freshness warning and hint;
- empty index returns an `INDEX_EMPTY` warning;
- enrichment columns that are present but mostly null produce an advisory warning only
  when the signal is strong enough to avoid noise;
- verbose mode includes table and column metadata;
- summary mode omits verbose table/column metadata.

Test the handler/MCP boundary:

- `handle_seam_schema` returns the module payload unchanged except for transport-level
  normalization;
- invalid mode returns `INVALID_INPUT`;
- MCP server registers `seam_schema`;
- total tool-count expectations are updated;
- MCP error behavior follows the existing handler error convention;
- the tool is read-only and does not require MCP dependencies outside the MCP server path.

Test the CLI:

- `seam schema --json` emits the standard success envelope;
- `seam schema --quiet` emits a small deterministic health summary;
- `seam schema --verbose --json` includes table/column metadata;
- no index returns the existing `NO_INDEX` style failure;
- DB open failure returns the existing `DB_ERROR` style failure;
- `--json` and `--quiet` remain mutually exclusive;
- command path and DB-dir resolution match other read commands.

Test the Web/Explorer surface:

- schema endpoint returns the same payload category as the core module;
- UI can detect stale, missing embeddings, and missing clusters from the payload;
- endpoint never leaks source code or absolute source paths.

Prior art:

- existing structure tests for handler shape, CLI JSON, MCP registration, and tool count;
- existing staleness tests for freshness verdicts;
- existing schema packaging and migration tests for old/current DB behavior;
- existing semantic and embedding tests for model mismatch and embedding population;
- existing CLI read command tests for `--json`, `--quiet`, `NO_INDEX`, and DB errors.

Required gates before implementation is considered done:

- type checking passes;
- linter passes;
- unit tests for the introspection module pass;
- CLI behavior tests pass;
- MCP registration tests pass;
- Web endpoint tests pass if the endpoint is included in the implementation slice;
- no test requires network access.

## Out of Scope

- No schema migration.
- No table creation.
- No index backfill.
- No re-indexing.
- No semantic embedding generation.
- No clustering recomputation.
- No graph search filters; those belong to P1.3 structural graph search.
- No snippet retrieval; that belongs to P1.2 `seam_snippet`.
- No architecture summary; that belongs to P1.4 `seam_architecture`.
- No Cypher or arbitrary SQL exposure.
- No source code retrieval.
- No docstring/comment text export.
- No secret or environment-value inspection.
- No network calls.
- No automatic repair of stale indexes.
- No changes to existing search, context, impact, trace, affected, pack, or structure
  semantics beyond advertising them in tool guidance.

## Further Notes

This is the correct first implementation slice for Phase 11 because it improves every
later slice. `seam_snippet`, structural graph search, architecture summary, richer edge
families, and the 3D Explorer all benefit from a single capability map that tells agents
and UI code what exists in the current index.

The implementation should be conservative. A useful first version can be shipped with
identity, freshness, counts, breakdowns, capabilities, warnings, tool guidance, CLI, and
MCP. Verbose table/column metadata and Web endpoint wiring can be included in the same
issue if small, but should remain cleanly separable behind the core introspection module.

The non-negotiable product contract is honesty: if embeddings are absent, say absent; if a
column is missing, say missing; if the DB is stale, say stale; if a feature is supported by
schema but unpopulated, say that separately. The whole point of `seam_schema` is to stop
agents from guessing.
