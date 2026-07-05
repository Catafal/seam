"""Named graph-search recipes for recurring agent questions.

Recipes stay outside the SQLite query path because they are product guidance:
stable intent labels that compile into the typed graph-search filters agents
could already have supplied by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GRAPH_SEARCH_DEFAULTS: dict[str, Any] = {
    "kind": None,
    "name_pattern": None,
    "qualified_name_pattern": None,
    "file_pattern": None,
    "language": None,
    "edge_kind": None,
    "direction": "both",
    "min_degree": None,
    "max_degree": None,
    "min_in_degree": None,
    "max_in_degree": None,
    "min_out_degree": None,
    "max_out_degree": None,
    "confidence": None,
    "synthesized": "any",
    "cluster_id": None,
    "visibility": None,
    "is_exported": None,
    "test_scope": "any",
    "preset": None,
    "sort": "default",
    "limit": 20,
    "offset": 0,
    "include_preview": False,
    "preview_limit": 3,
    "regex": False,
}


@dataclass(frozen=True)
class GraphSearchRecipe:
    id: str
    title: str
    use_when: str
    defaults: dict[str, Any]
    caveats: tuple[str, ...]
    follow_up_calls: tuple[dict[str, Any], ...]
    required_capabilities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "use_when": self.use_when,
            "defaults": dict(self.defaults),
            "required_capabilities": list(self.required_capabilities),
            "caveats": list(self.caveats),
            "follow_up_calls": [dict(call) for call in self.follow_up_calls],
            "tags": list(self.tags),
        }


_COMMON_FOLLOW_UPS = (
    {
        "tool": "seam_snippet",
        "reason": "Read bounded source for a selected result uid before editing.",
        "params": {"uid": "<uid>"},
    },
    {
        "tool": "seam_context",
        "reason": "Inspect callers, callees, field access, and test evidence for one selected symbol.",
        "params": {"symbol": "<symbol>"},
    },
)


RECIPES: tuple[GraphSearchRecipe, ...] = (
    GraphSearchRecipe(
        id="production-hotspots",
        title="Production fan-in hotspots",
        use_when="Find source symbols with many incoming relationships before changing shared code.",
        defaults={"preset": "hotspot", "test_scope": "source", "sort": "in-degree"},
        caveats=(
            "Incoming degree is static evidence, not runtime traffic.",
            "Source scoping excludes test symbols from results but static callers can still include generated or framework code.",
        ),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("hotspots", "change-safety", "architecture"),
    ),
    GraphSearchRecipe(
        id="fan-out-orchestrators",
        title="Fan-out orchestrators",
        use_when="Find source symbols that coordinate many outgoing relationships.",
        defaults={"direction": "outgoing", "min_out_degree": 2, "sort": "out-degree", "test_scope": "source"},
        caveats=("Outgoing degree is static structure, not proof that every branch runs together.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("orchestrators", "navigation", "architecture"),
    ),
    GraphSearchRecipe(
        id="dead-code-suspects",
        title="Dead-code suspects",
        use_when="Find source functions with no inbound call edges before a cleanup review.",
        defaults={"kind": "function", "preset": "dead-code", "test_scope": "source", "sort": "name"},
        caveats=(
            "No inbound static calls is a cleanup signal, not deletion proof.",
            "Dynamic imports, framework entry points, exported APIs, generated code, and external callers may be invisible.",
        ),
        follow_up_calls=(
            {
                "tool": "seam_context",
                "reason": "Inspect callers, tests, and field access before deleting any suspect.",
                "params": {"symbol": "<symbol>"},
            },
            *_COMMON_FOLLOW_UPS,
        ),
        tags=("cleanup-risk", "suspect"),
    ),
    GraphSearchRecipe(
        id="isolated-symbols",
        title="Isolated symbols",
        use_when="Find source symbols with no matching indexed relationships.",
        defaults={"preset": "isolates", "test_scope": "source", "sort": "name"},
        caveats=("Isolation is relative to indexed static edges and should be reviewed before cleanup.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("cleanup-risk", "suspect"),
    ),
    GraphSearchRecipe(
        id="field-access",
        title="Field readers and writers",
        use_when="Find field symbols and their read/write relationships before changing data shape.",
        defaults={"kind": "field", "preset": "field-access", "include_preview": True},
        caveats=("Field access is static and may miss dynamic attribute access.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_field_symbols",),
        tags=("field-access", "change-safety"),
    ),
    GraphSearchRecipe(
        id="inheritance-families",
        title="Inheritance and implementation families",
        use_when="Find inheritance, implementation, and override relationships around interface-like code.",
        defaults={"preset": "inheritance", "include_preview": True},
        caveats=("Inheritance edges are static and may include synthesized override evidence.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("inheritance", "interfaces", "change-safety"),
    ),
    GraphSearchRecipe(
        id="route-entrypoints",
        title="Route entry points",
        use_when="List indexed HTTP route nodes before API or Explorer endpoint work.",
        defaults={"kind": "route", "sort": "name"},
        caveats=("Route coverage depends on supported framework extractors.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_route_nodes",),
        tags=("routes", "protocol"),
    ),
    GraphSearchRecipe(
        id="http-callers",
        title="HTTP caller evidence",
        use_when="Find static client-to-route HTTP call evidence when the index has it.",
        defaults={"edge_kind": "http_calls", "direction": "outgoing", "include_preview": True},
        caveats=("HTTP-call coverage is intentionally partial and must not be treated as complete service topology.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_http_calls",),
        tags=("protocol", "http-calls"),
    ),
    GraphSearchRecipe(
        id="config-readers",
        title="Config readers",
        use_when="Find configuration keys with incoming reads_config evidence.",
        defaults={"kind": "config", "edge_kind": "reads_config", "direction": "incoming", "include_preview": True},
        caveats=("Config evidence stores keys and relationships, not secret values.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_config_nodes", "has_reads_config"),
        tags=("config", "operations"),
    ),
    GraphSearchRecipe(
        id="resource-config-links",
        title="Resource and configuration links",
        use_when="Find resource/configuration relationships that may affect runtime dependencies.",
        defaults={"kind": "resource", "edge_kind": "configures", "direction": "incoming", "include_preview": True},
        caveats=("Resource evidence is declaration/reference metadata, not runtime reachability.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_resource_nodes", "has_configures"),
        tags=("resources", "operations"),
    ),
    GraphSearchRecipe(
        id="test-evidence",
        title="Static test-to-production evidence",
        use_when="Find production symbols with indexed static test evidence.",
        defaults={"edge_kind": "tests", "direction": "incoming", "include_preview": True, "test_scope": "source"},
        caveats=("Test edges are confidence evidence and should not inflate production caller risk.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_test_edges",),
        tags=("tests", "verification"),
    ),
    GraphSearchRecipe(
        id="exception-flow",
        title="Explicit exception-flow evidence",
        use_when="Find symbols connected to explicit raises and catches edges.",
        defaults={"edge_kind": "raises,catches", "direction": "outgoing", "include_preview": True},
        caveats=("Exception edges are static and do not replace runtime trace evidence.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        required_capabilities=("has_exception_edges",),
        tags=("exceptions", "risk"),
    ),
    GraphSearchRecipe(
        id="path-structure",
        title="Path-scoped structural search",
        use_when="Narrow a structural graph question to one package, feature area, or test directory.",
        defaults={"sort": "file"},
        caveats=("Provide a file pattern override to narrow the result set; otherwise this is repository-wide.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("path-scope", "navigation"),
    ),
    GraphSearchRecipe(
        id="class-family",
        title="Class family discovery",
        use_when="Find related classes by name pattern, such as processors, services, adapters, or handlers.",
        defaults={"kind": "class", "sort": "name"},
        caveats=("Name families are naming evidence; use context or impact to verify relationships.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("discovery", "classes"),
    ),
    GraphSearchRecipe(
        id="function-family",
        title="Function family discovery",
        use_when="Find related functions or methods by name pattern before a focused code search.",
        defaults={"kind": "function", "sort": "name"},
        caveats=("Name families are naming evidence; use context or impact to verify relationships.",),
        follow_up_calls=_COMMON_FOLLOW_UPS,
        tags=("discovery", "functions"),
    ),
)

_RECIPES_BY_ID = {recipe.id: recipe for recipe in RECIPES}


def list_graph_search_recipes() -> list[dict[str, Any]]:
    return [recipe.summary() for recipe in RECIPES]


def get_graph_search_recipe(recipe_id: str) -> GraphSearchRecipe | None:
    return _RECIPES_BY_ID.get(recipe_id)


def compile_graph_search_recipe(
    recipe_id: str,
    params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | dict[str, str]:
    recipe = get_graph_search_recipe(recipe_id)
    if recipe is None:
        return {
            "error": "INVALID_INPUT",
            "message": f"unknown graph-search recipe: {recipe_id}",
        }

    compiled = dict(params)
    applied_defaults: dict[str, Any] = {}
    overrides: dict[str, Any] = {}

    for key, value in recipe.defaults.items():
        current = compiled.get(key, GRAPH_SEARCH_DEFAULTS.get(key))
        if current == GRAPH_SEARCH_DEFAULTS.get(key):
            compiled[key] = value
            applied_defaults[key] = value
        elif current != value:
            overrides[key] = current

    for key, current in compiled.items():
        if key in recipe.defaults:
            continue
        if current != GRAPH_SEARCH_DEFAULTS.get(key):
            overrides[key] = current

    metadata = {
        "id": recipe.id,
        "title": recipe.title,
        "use_when": recipe.use_when,
        "applied_defaults": applied_defaults,
        "overrides": overrides,
        "required_capabilities": list(recipe.required_capabilities),
        "caveats": list(recipe.caveats),
        "follow_up_calls": [dict(call) for call in recipe.follow_up_calls],
        "tags": list(recipe.tags),
    }
    return compiled, metadata
