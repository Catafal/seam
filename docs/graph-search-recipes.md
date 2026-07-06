# Graph Search Recipes

Graph-search recipes are named, stable shortcuts for recurring codebase questions.
They do not add a query language and they do not bypass typed graph-search filters.
Each recipe compiles into the same bounded `seam_graph_search` parameters an agent
could have supplied by hand, then returns the applied defaults, caller overrides,
caveats, required capabilities, and recommended follow-up calls.

Use them when the agent knows the intent but not the exact combination of graph
filters.

For cleanup decisions, prefer `seam suspects` / `seam_suspects` after discovery.
The `dead-code-suspects` and `isolated-symbols` recipes expose structural absence;
the suspects tool adds blockers, removal risk, caveats, and follow-up calls.

```bash
seam graph-search --list-recipes
seam graph-search --recipe production-hotspots --json
seam graph-search --recipe dead-code-suspects --limit 50 --json
seam graph-search --recipe class-family --name '*Processor' --json
```

In MCP, pass the same stable id:

```json
{
  "recipe": "test-evidence",
  "limit": 10
}
```

The response keeps the normalized query and adds a `recipe` block. That makes
recipes transparent: an agent can see which defaults were applied, which caller
inputs overrode the recipe, and what caveats still apply.

## Catalog

| Recipe | Use when | Important defaults |
|---|---|---|
| `production-hotspots` | Find source symbols with many incoming relationships before changing shared code. | `preset=hotspot`, `test_scope=source`, `sort=in-degree` |
| `fan-out-orchestrators` | Find source symbols that coordinate many outgoing relationships. | `direction=outgoing`, `min_out_degree=2`, `sort=out-degree`, `test_scope=source` |
| `dead-code-suspects` | Find source functions with no inbound call edges before cleanup review. | `kind=function`, `preset=dead-code`, `test_scope=source`, `sort=name` |
| `isolated-symbols` | Find source symbols with no matching indexed relationships. | `preset=isolates`, `test_scope=source`, `sort=name` |
| `field-access` | Find field symbols and read/write relationships before changing data shape. | `kind=field`, `preset=field-access`, `include_preview=true` |
| `inheritance-families` | Find inheritance, implementation, and override relationships around interface-like code. | `preset=inheritance`, `include_preview=true` |
| `route-entrypoints` | List indexed HTTP route nodes before API or Explorer endpoint work. | `kind=route`, `sort=name` |
| `http-callers` | Find static client-to-route HTTP call evidence when the index has it. | `edge_kind=http_calls`, `direction=outgoing`, `include_preview=true` |
| `config-readers` | Find configuration keys with incoming `reads_config` evidence. | `kind=config`, `edge_kind=reads_config`, `direction=incoming`, `include_preview=true` |
| `resource-config-links` | Find resource/configuration relationships that may affect runtime dependencies. | `kind=resource`, `edge_kind=configures`, `direction=incoming`, `include_preview=true` |
| `test-evidence` | Find production symbols with indexed static test evidence. | `edge_kind=tests`, `direction=incoming`, `include_preview=true`, `test_scope=source` |
| `exception-flow` | Find symbols connected to explicit `raises` and `catches` edges. | `edge_kind=raises,catches`, `direction=outgoing`, `include_preview=true` |
| `path-structure` | Narrow a structural graph question to one package, feature area, or test directory. | `sort=file` |
| `class-family` | Find related classes by name pattern, such as processors, services, adapters, or handlers. | `kind=class`, `sort=name` |
| `function-family` | Find related functions or methods by name pattern before focused code search. | `kind=function`, `sort=name` |

## Caveats

- Recipes are discovery aids, not proof. For example, `dead-code-suspects` means
  "no inbound static calls were observed", not "safe to delete".
- Use `seam_suspects` for deletion review. It treats raw absence as one signal and
  checks blockers such as tests, public APIs, imports, routes, resources, fields,
  inheritance, and contained-symbol usage before assigning suspect strength.
- Required capabilities are surfaced in recipe metadata, but unsupported
  capabilities still need normal schema and warning handling. For example,
  `http-callers` depends on `has_http_calls`.
- `http-callers` is literal-only evidence. It covers direct `fetch`, supported
  literal Axios calls, local wrappers named `apiFetch`, and literal Python
  module-import calls through `requests`/`httpx`/`aiohttp`; dynamic URLs, third-party
  absolute URLs, and `from httpx import get`-style calls are deliberately omitted.
- Source text is not included. Use the returned `follow_up_calls`, typically
  `seam_snippet` and `seam_context`, before editing.
- Caller-provided non-default filters remain visible as recipe `overrides`; this
  is intentional so agents can explain why a result set was narrowed.
