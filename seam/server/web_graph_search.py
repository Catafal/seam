"""FastAPI route registration for structural graph search."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query

from seam.indexer.readonly import open_readonly_connection
from seam.server.tools import handle_seam_graph_search
from seam.server.web_schema import GraphSearchResponse


def _get_readonly_conn(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise HTTPException(
            status_code=503,
            detail={"code": "NO_INDEX", "message": "No index found. Run 'seam init' first."},
        )
    try:
        return open_readonly_connection(db_path)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "DB_ERROR", "message": f"Failed to open database: {exc}"},
        ) from exc


def _check_handler_error(result: Any) -> None:
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(
            status_code=400,
            detail={
                "code": result.get("error", "UNKNOWN"),
                "message": result.get("message", ""),
            },
        )


def register_graph_search_routes(app: FastAPI, *, db_path: Path, root: Path) -> None:
    """Register graph-search routes without growing the already-large web factory."""

    @app.get("/api/graph/search", response_model=GraphSearchResponse, tags=["graph"])
    def get_graph_search(
        kind: str | None = Query(None, description="Symbol kind filter."),
        name_pattern: str | None = Query(None, description="Symbol name glob or regex."),
        qualified_name_pattern: str | None = Query(None, description="Qualified-name glob/regex."),
        file_pattern: str | None = Query(None, description="Root-relative file glob/regex."),
        language: str | None = Query(None, description="Indexed language filter."),
        edge_kind: str | None = Query(None, description="Edge kind or comma-separated edge kinds."),
        direction: str = Query("both", description="incoming | outgoing | both"),
        min_degree: int | None = Query(None, ge=0),
        max_degree: int | None = Query(None, ge=0),
        min_in_degree: int | None = Query(None, ge=0),
        max_in_degree: int | None = Query(None, ge=0),
        min_out_degree: int | None = Query(None, ge=0),
        max_out_degree: int | None = Query(None, ge=0),
        confidence: str | None = Query(None, description="EXTRACTED | INFERRED | AMBIGUOUS"),
        synthesized: str = Query("any", description="any | parser | synthesized"),
        cluster_id: int | None = Query(None, ge=0),
        visibility: str | None = Query(None),
        is_exported: bool | None = Query(None),
        test_scope: str = Query("any", description="any | test | source"),
        preset: str | None = Query(None, description="dead-code | hotspot | field-access | inheritance | isolates"),
        sort: str = Query("default", description="default | in-degree | out-degree | total-degree | name | file | line"),
        limit: int = Query(20, ge=0, le=100),
        offset: int = Query(0, ge=0),
        include_preview: bool = Query(False),
        preview_limit: int = Query(3, ge=0, le=10),
        regex: bool = Query(False),
        recipe: str | None = Query(None, description="Named graph-search recipe id."),
    ) -> GraphSearchResponse:
        """Return typed structural graph-search results without source text."""
        conn = _get_readonly_conn(db_path)
        try:
            result = handle_seam_graph_search(
                conn,
                root,
                kind=kind,
                name_pattern=name_pattern,
                qualified_name_pattern=qualified_name_pattern,
                file_pattern=file_pattern,
                language=language,
                edge_kind=edge_kind,
                direction=direction,
                min_degree=min_degree,
                max_degree=max_degree,
                min_in_degree=min_in_degree,
                max_in_degree=max_in_degree,
                min_out_degree=min_out_degree,
                max_out_degree=max_out_degree,
                confidence=confidence,
                synthesized=synthesized,
                cluster_id=cluster_id,
                visibility=visibility,
                is_exported=is_exported,
                test_scope=test_scope,
                preset=preset,
                sort=sort,
                limit=limit,
                offset=offset,
                include_preview=include_preview,
                preview_limit=preview_limit,
                regex=regex,
                recipe=recipe,
            )
        finally:
            conn.close()

        _check_handler_error(result)
        return GraphSearchResponse(**cast(dict[str, Any], result))
