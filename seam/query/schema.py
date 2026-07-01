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

_OPTIONAL_TABLES = ("comments", "clusters", "import_mappings", "embeddings")
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
)


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


def _group_counts(conn: sqlite3.Connection, table: str, column: str, where: str = "") -> dict[str, int]:
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
            "transports": ["mcp"],
            "read_only": True,
            "use_when": "You need one enriched bundle for a symbol and its neighbors.",
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
        warnings.append({
            "code": "INDEX_STALE",
            "message": freshness["reason"] or "The index may be stale.",
            "hint": freshness["hint"] or "Run 'seam sync' or 'seam init'.",
        })
    if counts["files"] == 0 or counts["symbols"] == 0:
        warnings.append({
            "code": "INDEX_EMPTY",
            "message": "The index has no real files or symbols.",
            "hint": "Run 'seam init' from the project root.",
        })
    for table in missing_tables:
        warnings.append({
            "code": "MISSING_OPTIONAL_TABLE",
            "message": f"Optional table '{table}' is missing.",
            "hint": "Run 'seam init' with the current Seam version if this capability is needed.",
        })
    if not capabilities["has_clusters"]:
        warnings.append({
            "code": "NO_CLUSTERS",
            "message": "No clusters are populated.",
            "hint": "Cluster-aware context may be unavailable until a full index has clusters.",
        })
    if not capabilities["has_embeddings"]:
        warnings.append({
            "code": "NO_EMBEDDINGS",
            "message": "No embeddings are populated for semantic hybrid search.",
            "hint": "Run 'seam init --semantic' if semantic search is desired.",
        })
    elif not capabilities["embedding_model_matches"]:
        warnings.append({
            "code": "EMBEDDING_MODEL_MISMATCH",
            "message": "Stored embeddings do not match the configured model.",
            "hint": "Run 'seam init --semantic' to rebuild embeddings for the configured model.",
        })
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
    }
    breakdowns = {
        "languages": _group_counts(conn, "files", "language", "path NOT LIKE ':%'"),
        "symbol_kinds": _group_counts(conn, "symbols", "kind"),
        "edge_kinds": _group_counts(conn, "edges", "kind"),
        "edge_confidence": _group_counts(conn, "edges", "confidence"),
        "synthesized_edges": _group_counts(
            conn,
            "edges",
            "synthesized_by",
            "synthesized_by IS NOT NULL",
        ),
        "comment_markers": _group_counts(conn, "comments", "marker"),
        "embedding_models": embedding_model_counts,
    }
    symbols_columns = _column_names(conn, "symbols")
    edges_columns = _column_names(conn, "edges")
    capabilities = {
        "has_clusters": counts["clusters"] > 0,
        "has_comments": counts["comments"] > 0,
        "has_import_mappings": counts["import_mappings"] > 0,
        "has_embeddings": counts["embeddings"] > 0,
        "embedding_model_matches": counts["embeddings"] == 0 or configured_embedding_count > 0,
        "has_synthesized_edges": synth_edges > 0,
        "has_field_symbols": breakdowns["symbol_kinds"].get("field", 0) > 0,
        "has_receiver_column": "receiver" in edges_columns,
        "has_search_text": "search_text" in symbols_columns,
        "has_signature_column": "signature" in symbols_columns,
        "has_synthesized_by_column": "synthesized_by" in edges_columns,
    }

    result: dict[str, Any] = {
        "schema_version": schema_version,
        "seam_version": seam.__version__,
        "index_seam_version": meta.get("seam_version"),
        "freshness": freshness,
        "counts": counts,
        "breakdowns": breakdowns,
        "capabilities": capabilities,
        "tools": _tool_registry(),
        "recommended_next_calls": [
            "Call seam_schema first to inspect index capability and freshness.",
            "Use seam_search for keyword discovery.",
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
