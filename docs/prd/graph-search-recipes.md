# PRD — Graph-Search Recipes for Agent Answerability

> Status: implemented in `feat/graph-search-recipes`.
> Parent issue: #324.
> Child issues: #325, #326, #327, #328, #329.

## Problem

`seam_graph_search` already supports typed structural filters, presets, edge
kinds, pagination, and previews. The problem was usability: an agent often knows
the question it wants to ask, such as "find production hotspots" or "show me
dead-code suspects", but has to remember the exact combination of `kind`,
`edge_kind`, degree filters, `test_scope`, preview settings, and sort mode.

That friction showed up in the answerability roadmap as "graph-search recipe"
pressure. The right fix is not Cypher or arbitrary SQL. Seam needs stable,
transparent intent labels that compile into the existing typed graph-search
contract.

## Goals

- Add a named recipe catalog for daily agent questions.
- Keep recipes transparent by returning applied defaults, overrides, caveats,
  required capabilities, and follow-up calls.
- Preserve the existing graph-search execution path, pagination, validation, and
  read-only/no-SQL boundary.
- Expose the same recipe input across CLI, MCP, Web API, schema guidance, and
  workspace graph search.
- Update the answerability benchmark so scenarios that recipes now cover use
  those recipes directly.

## Non-Goals

- No Cypher-like query language.
- No raw SQL or user-authored graph expressions.
- No new database tables or indexing post-pass.
- No runtime proof for dead code, test coverage, HTTP topology, or exception
  propagation.

## Product Contract

Agents can call:

```bash
seam graph-search --list-recipes
seam graph-search --recipe production-hotspots --json
seam graph-search --recipe class-family --name '*Processor' --json
```

MCP/Web/workspace callers pass the same `recipe` string. The response includes:

- `query.recipe`;
- normal normalized query fields after recipe compilation;
- top-level `recipe.id`, `title`, `use_when`;
- `applied_defaults` for recipe defaults that were accepted;
- `overrides` for caller inputs that narrowed or changed the recipe;
- `required_capabilities`;
- `caveats`;
- `follow_up_calls`;
- `tags`.

Unknown recipe ids return `INVALID_INPUT`.

## Initial Recipe Set

- `production-hotspots`
- `fan-out-orchestrators`
- `dead-code-suspects`
- `isolated-symbols`
- `field-access`
- `inheritance-families`
- `route-entrypoints`
- `http-callers`
- `config-readers`
- `resource-config-links`
- `test-evidence`
- `exception-flow`
- `path-structure`
- `class-family`
- `function-family`

## Implementation Notes

The recipe catalog lives in `seam/query/graph_recipes.py`, outside the SQLite
query path. Recipes compile before graph-search validation, so the rest of the
system continues to use the same validator, preset expansion, degree filtering,
preview generation, and result shaping.

The schema tool advertises the recipe catalog under the existing
`seam_graph_search` tool entry. Architecture next-call guidance uses recipes
for common follow-ups while retaining exact filters such as `edge_kind` where
they are part of the question.

## Acceptance Criteria

- `seam graph-search --list-recipes --json` returns the catalog without opening
  an index.
- `seam graph-search --recipe production-hotspots --json` returns recipe
  metadata and a normalized query with recipe defaults.
- CLI, MCP, Web API, workspace graph-search, schema, and architecture guidance
  all accept or advertise recipes consistently.
- Unknown recipe ids return `INVALID_INPUT`.
- Answerability scenarios for processor-class and validate-data discovery use
  recipes.
- Focused unit/integration tests, `make eval-answerability`, and `make gate`
  pass.
