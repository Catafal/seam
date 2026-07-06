"""Read-only index capability introspection for `seam_schema`.

The schema tool is an agent's first safe call: it answers what this index can
support right now without mutating the DB, reading source text, or importing any
transport-specific dependencies.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import seam
import seam.config as config
from seam.analysis.staleness import check_staleness
from seam.indexer.bootstrap import describe_bootstrap
from seam.query import semantic_contract
from seam.query.graph_recipes import list_graph_search_recipes

_OPTIONAL_TABLES = (
    "comments",
    "clusters",
    "import_mappings",
    "embeddings",
    "routes",
    "config_keys",
    "resources",
    "document_files",
    "document_anchors",
    "document_references",
)
_INTROSPECT_TABLES = (
    "files",
    "symbols",
    "edges",
    "comments",
    "clusters",
    "import_mappings",
    "embeddings",
    "metadata",
    "symbols_fts",
    "routes",
    "config_keys",
    "resources",
    "document_files",
    "document_anchors",
    "document_references",
)
_INFRA_RESOURCE_CATEGORIES = {
    "service",
    "image",
    "dockerfile",
    "build_context",
    "port",
    "stage",
    "env_file",
    "volume",
    "network",
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (table,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:  # noqa: BLE001 - older/partial indexes should still describe what they can.
        return set()


def _count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()[0])
    except Exception:  # noqa: BLE001 - missing columns or corrupt optional tables degrade to 0.
        return 0


def _group_counts(
    conn: sqlite3.Connection, table: str, column: str, where: str = ""
) -> dict[str, int]:
    if column not in _column_names(conn, table):
        return {}
    try:
        sql = f"SELECT {column} AS key, COUNT(*) AS count FROM {table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" GROUP BY {column} ORDER BY {column}"
        rows = conn.execute(sql).fetchall()
    except Exception:  # noqa: BLE001 - introspection must not fail because one section is absent.
        return {}
    return {str(row["key"]): int(row["count"]) for row in rows if row["key"] is not None}


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "metadata"):
        return {}
    try:
        rows = conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def _database_path(conn: sqlite3.Connection, root: Path) -> Path:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return config.get_db_path(root)
    for row in rows:
        if str(row["name"]) == "main" and row["file"]:
            return Path(str(row["file"]))
    return config.get_db_path(root)


def _table_metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in _INTROSPECT_TABLES:
        exists = _table_exists(conn, table)
        columns: dict[str, Any] = {}
        if exists:
            try:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                columns[str(row["name"])] = {
                    "exists": True,
                    "type": row["type"] or None,
                    "notnull": bool(row["notnull"]),
                    "default": row["dflt_value"],
                    "primary_key": bool(row["pk"]),
                }
        tables[table] = {"exists": exists, "columns": columns}
    return tables


def _tool_registry() -> list[dict[str, Any]]:
    return [
        {
            "name": "seam_schema",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need to discover index capabilities before deeper graph calls.",
        },
        {
            "name": "seam_search",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You know a keyword but not the exact symbol name.",
        },
        {
            "name": "seam_snippet",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need bounded source text for one exact symbol from any result uid.",
        },
        {
            "name": "seam_graph_search",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need structural discovery by kind, edge, degree, route, config/resource, path, or preset.",
            "depends_on": ["edges"],
            "recipes": list_graph_search_recipes(),
        },
        {
            "name": "seam_architecture",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need a bounded repository architecture briefing before choosing precise follow-up tools.",
            "depends_on": ["edges"],
        },
        {
            "name": "seam_query",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": "You need concept search with graph expansion.",
        },
        {
            "name": "seam_context",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need the 360-degree view of one symbol before editing.",
        },
        {
            "name": "seam_impact",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need blast-radius analysis before changing a symbol.",
        },
        {
            "name": "seam_trace",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need the shortest dependency path between two symbols.",
        },
        {
            "name": "seam_context_pack",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": (
                "You need one enriched bundle for a symbol, its neighbors, direct relationship "
                "evidence, caveats, and recommended follow-up calls."
            ),
        },
        {
            "name": "seam_plan",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": (
                "You need a bounded inspect-and-test plan for a target symbol or current diff "
                "before editing, committing, or handing work to another agent."
            ),
            "depends_on": ["edges"],
        },
        {
            "name": "seam_suspects",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": (
                "You need conservative cleanup review for weakly connected symbols or files. "
                "Suspects are review leads, not deletion proof."
            ),
            "depends_on": ["edges"],
        },
        {
            "name": "seam_grounding",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": (
                "You need local docs, ADRs, PRDs, roadmaps, or implementation notes that "
                "explicitly ground a symbol, file, route, config key, resource, or spec question."
            ),
            "depends_on": ["document_anchors"],
        },
        {
            "name": "seam_structure",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need the repository directory/file/container skeleton.",
        },
        {
            "name": "seam_clusters",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need functional-area clusters or members.",
            "depends_on": ["clusters"],
        },
        {
            "name": "seam_why",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need WHY/HACK/NOTE/TODO/FIXME comments near code.",
            "depends_on": ["comments"],
        },
        {
            "name": "seam_affected",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": "You need impacted test files for changed files.",
        },
        {
            "name": "seam_changes",
            "transports": ["cli", "mcp", "web"],
            "read_only": True,
            "use_when": "You need git diff risk mapped to indexed symbols.",
        },
        {
            "name": "seam_flows",
            "transports": ["cli", "mcp"],
            "read_only": True,
            "use_when": "You need entry points or forward execution-flow expansion.",
        },
    ]


def _warnings(
    *,
    freshness: dict[str, Any],
    counts: dict[str, int],
    capabilities: dict[str, bool],
    missing_tables: list[str],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if freshness["stale"]:
        warnings.append(
            {
                "code": "INDEX_STALE",
                "message": freshness["reason"] or "The index may be stale.",
                "hint": freshness["hint"] or "Run 'seam sync' or 'seam init'.",
            }
        )
    if counts["files"] == 0 or counts["symbols"] == 0:
        warnings.append(
            {
                "code": "INDEX_EMPTY",
                "message": "The index has no real files or symbols.",
                "hint": "Run 'seam init' from the project root.",
            }
        )
    for table in missing_tables:
        warnings.append(
            {
                "code": "MISSING_OPTIONAL_TABLE",
                "message": f"Optional table '{table}' is missing.",
                "hint": "Run 'seam init' with the current Seam version if this capability is needed.",
            }
        )
    if not capabilities["has_clusters"]:
        warnings.append(
            {
                "code": "NO_CLUSTERS",
                "message": "No clusters are populated.",
                "hint": "Cluster-aware context may be unavailable until a full index has clusters.",
            }
        )
    if not capabilities["has_embeddings"]:
        warnings.append(
            {
                "code": "NO_EMBEDDINGS",
                "message": "No embeddings are populated for semantic hybrid search.",
                "hint": "Run 'seam init --semantic' if semantic search is desired.",
            }
        )
    elif not capabilities["embedding_model_matches"]:
        warnings.append(
            {
                "code": "EMBEDDING_MODEL_MISMATCH",
                "message": "Stored embeddings do not match the configured model.",
                "hint": "Run 'seam init --semantic' to rebuild embeddings for the configured model.",
            }
        )
    return warnings


def describe_schema(
    conn: sqlite3.Connection,
    *,
    root: Path,
    verbose: bool = False,
) -> dict[str, Any]:
    """Describe the current index shape and populated capabilities without mutation."""
    meta = _metadata(conn)
    schema_version_raw = meta.get("schema_version", "0")
    try:
        schema_version: int | str = int(schema_version_raw)
    except ValueError:
        schema_version = schema_version_raw

    staleness = check_staleness(conn, root=root, respect_knob=False)
    freshness = {
        "stale": bool(staleness["stale"]),
        "reason": staleness["reason"] or None,
        "hint": staleness["hint"] or None,
    }

    missing_tables = [table for table in _OPTIONAL_TABLES if not _table_exists(conn, table)]
    embedding_model_counts = _group_counts(conn, "embeddings", "model")
    configured_embedding_count = embedding_model_counts.get(config.SEAM_EMBED_MODEL, 0)
    embeddings_count = sum(embedding_model_counts.values())
    synth_edges = _count(conn, "edges", "synthesized_by IS NOT NULL")

    counts = {
        "files": _count(conn, "files", "path NOT LIKE ':%'"),
        "symbols": _count(conn, "symbols"),
        "edges": _count(conn, "edges"),
        "clusters": _count(conn, "clusters"),
        "comments": _count(conn, "comments"),
        "import_mappings": _count(conn, "import_mappings"),
        "embeddings": embeddings_count,
        "routes": _count(conn, "routes"),
        "http_calls": _count(conn, "edges", "kind = 'http_calls'"),
        "config_keys": _count(conn, "config_keys"),
        "resources": _count(conn, "resources"),
        "document_files": _count(conn, "document_files"),
        "document_anchors": _count(conn, "document_anchors"),
        "document_references": _count(conn, "document_references"),
    }
    breakdowns = {
        "languages": _group_counts(conn, "files", "language", "path NOT LIKE ':%'"),
        "symbol_kinds": _group_counts(conn, "symbols", "kind"),
        "edge_kinds": _group_counts(conn, "edges", "kind"),
        "edge_confidence": _group_counts(conn, "edges", "confidence"),
        "edge_provenance": _group_counts(conn, "edges", "provenance"),
        "synthesized_edges": _group_counts(
            conn,
            "edges",
            "synthesized_by",
            "synthesized_by IS NOT NULL",
        ),
        "comment_markers": _group_counts(conn, "comments", "marker"),
        "embedding_models": embedding_model_counts,
        "resource_categories": _group_counts(conn, "resources", "category"),
        "document_kinds": _group_counts(conn, "document_files", "doc_kind"),
        "document_statuses": _group_counts(conn, "document_files", "status"),
        "document_relation_types": _group_counts(conn, "document_references", "relation_type"),
        "document_reference_confidence": _group_counts(conn, "document_references", "confidence"),
    }
    symbols_columns = _column_names(conn, "symbols")
    edges_columns = _column_names(conn, "edges")
    exact_receiver_edges = any(
        breakdowns["edge_provenance"].get(provenance, 0) > 0
        for provenance in (
            "python-receiver-type",
            "typescript-receiver-type",
            "javascript-receiver-type",
        )
    )
    capabilities = {
        "has_clusters": counts["clusters"] > 0,
        "has_comments": counts["comments"] > 0,
        "has_import_mappings": counts["import_mappings"] > 0,
        "has_embeddings": counts["embeddings"] > 0,
        "embedding_model_matches": counts["embeddings"] == 0 or configured_embedding_count > 0,
        "has_synthesized_edges": synth_edges > 0,
        "has_routes_table": _table_exists(conn, "routes"),
        "has_route_nodes": breakdowns["symbol_kinds"].get("route", 0) > 0,
        "has_http_calls": breakdowns["edge_kinds"].get("http_calls", 0) > 0,
        "has_config_keys_table": _table_exists(conn, "config_keys"),
        "has_resources_table": _table_exists(conn, "resources"),
        "has_config_nodes": breakdowns["symbol_kinds"].get("config", 0) > 0,
        "has_resource_nodes": breakdowns["symbol_kinds"].get("resource", 0) > 0,
        "has_doc_anchors": counts["document_anchors"] > 0,
        "has_doc_grounding": counts["document_references"] > 0,
        "has_infra_graph": any(
            breakdowns["resource_categories"].get(category, 0) > 0
            for category in _INFRA_RESOURCE_CATEGORIES
        ),
        "has_reads_config": breakdowns["edge_kinds"].get("reads_config", 0) > 0,
        "has_configures": breakdowns["edge_kinds"].get("configures", 0) > 0,
        "has_exception_edges": (
            breakdowns["edge_kinds"].get("raises", 0) > 0
            or breakdowns["edge_kinds"].get("catches", 0) > 0
        ),
        "has_test_edges": breakdowns["edge_kinds"].get("tests", 0) > 0,
        "has_field_symbols": breakdowns["symbol_kinds"].get("field", 0) > 0,
        "has_receiver_column": "receiver" in edges_columns,
        "has_search_text": "search_text" in symbols_columns,
        "has_signature_column": "signature" in symbols_columns,
        "has_synthesized_by_column": "synthesized_by" in edges_columns,
        "has_edge_provenance_column": "provenance" in edges_columns,
        "has_exact_receiver_provenance": "provenance" in edges_columns,
        "has_exact_receiver_edges": exact_receiver_edges,
    }
    semantic_readiness = semantic_contract.semantic_readiness(
        conn,
        requested=True,
        availability_check=semantic_contract.is_available,
    )
    semantic_retrieval_modes = (
        ["keyword", "hybrid"] if semantic_readiness["usable"] else ["keyword"]
    )

    result: dict[str, Any] = {
        "schema_version": schema_version,
        "seam_version": seam.__version__,
        "index_seam_version": meta.get("seam_version"),
        "freshness": freshness,
        "counts": counts,
        "breakdowns": breakdowns,
        "capabilities": capabilities,
        "semantic": {
            "readiness": semantic_readiness,
            "config": {
                "enabled": config.SEAM_SEMANTIC == "on",
                "model": config.SEAM_EMBED_MODEL,
                "vector_store": config.SEAM_VECTOR_STORE,
                "ann": config.SEAM_VEC_ANN,
                "scan_cap": config.SEAM_SEMANTIC_SCAN_CAP,
            },
            "index": {
                "embedding_count": counts["embeddings"],
                "matching_embedding_count": configured_embedding_count,
                "embedding_models": embedding_model_counts,
                "embedding_model_matches": capabilities["embedding_model_matches"],
            },
            "retrieval": {
                "available_modes": semantic_retrieval_modes,
                "degraded_reason": semantic_readiness["reason"],
                "caveat": semantic_contract.SEMANTIC_DISCOVERY_CAVEAT,
            },
        },
        "bootstrap": describe_bootstrap(
            project_root=root,
            db_path=_database_path(conn, root),
            freshness=freshness,
            semantic_readiness=semantic_readiness,
            artifact_url_configured=bool(config.SEAM_INDEX_ARTIFACT_URL),
        ),
        "tools": _tool_registry(),
        "recommended_next_calls": [
            "Call seam_schema first to inspect index capability and freshness.",
            "Use seam_search for keyword discovery.",
            "Use seam_architecture for a repo-level briefing with physical areas, clusters, hotspots, boundaries, and follow-up calls.",
            "Use seam_graph_search recipe=production-hotspots for shared source hotspots.",
            "Use seam_graph_search recipe=dead-code-suspects for cleanup candidates, then verify with context/snippet.",
            "Use seam_suspects for cleanup candidates when you need blockers, caveats, and follow-up calls instead of raw degree filters.",
            "Use seam_grounding before edits that need local ADR/PRD/docs/spec intent; doc links are not dependency edges.",
            "Use seam_graph_search recipes for field access, inheritance, routes, config/resources, tests, and exceptions before hand-writing filter combinations.",
            "Use seam_graph_search with kind=route or edge_kind=http_calls for HTTP boundary discovery when route data is populated.",
            "Use seam_graph_search with kind=config/resource or edge_kind=reads_config/configures for operational dependency discovery when config data is populated.",
            "Use seam_graph_search with edge_kind=raises,catches for explicit exception-flow discovery when exception edges are populated.",
            "Use seam_graph_search with edge_kind=tests for static test-to-production evidence when test edges are populated.",
            "Use seam_snippet with a search/query/graph-search uid when you need exact source text.",
            "Use seam_context before editing a known symbol.",
            "Use seam_impact before changing an existing symbol.",
        ],
    }
    result["warnings"] = _warnings(
        freshness=freshness,
        counts=counts,
        capabilities=capabilities,
        missing_tables=missing_tables,
    )
    if verbose:
        result["tables"] = _table_metadata(conn)
    return result
