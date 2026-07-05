# Graph-Search Recipes Issues

This task map tracks the PRD split for Graph-Search Recipes for Agent
Answerability.

## Parent

- #324 — PRD: Graph-Search Recipes for Agent Answerability

## Child Issues

- #325 — Graph Recipes S1: Recipe catalog tracer bullet
  - Add the recipe catalog.
  - Compile one recipe into existing typed graph-search filters.
  - Return transparent recipe metadata.

- #326 — Graph Recipes S2: Daily agent recipe set
  - Cover production hotspots, fan-out orchestrators, dead-code suspects,
    isolated symbols, field access, inheritance, routes, HTTP callers,
    config/resource links, test evidence, exception flow, path-scoped search,
    class families, and function families.

- #327 — Graph Recipes S3: CLI MCP Web schema parity
  - Expose `recipe` through CLI, MCP, Web API, workspace graph-search, schema,
    and architecture next-call guidance.
  - Keep the output contract consistent across transports.

- #328 — Graph Recipes S4: Answerability scenarios and docs
  - Update the deterministic answerability benchmark so recipe-covered
    questions actually use recipes.
  - Document usage, catalog, caveats, and follow-up behavior.

- #329 — Graph Recipes S5: Hardening, review, and release readiness
  - Run focused tests, benchmark, type/lint gates, Seam changed-symbol checks,
    review, and documentation pass.
  - Land via PR and close all issues.
