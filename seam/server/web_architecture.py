"""FastAPI route registration for the architecture overview endpoint."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query

from seam.indexer.readonly import open_readonly_connection
from seam.server.tools import handle_seam_architecture
from seam.server.web_schema import ArchitectureResponse


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


def _split_sections(section: list[str] | None) -> list[str] | None:
    if section is None:
        return None
    parts: list[str] = []
    for value in section:
        parts.extend(part.strip() for part in value.split(",") if part.strip())
    return parts or None


def register_architecture_routes(app: FastAPI, *, db_path: Path, root: Path) -> None:
    """Register architecture routes without opening the DB during app construction."""

    @app.get(
        "/api/architecture",
        response_model=ArchitectureResponse,
        response_model_exclude_none=True,
        tags=["architecture"],
    )
    def get_architecture(
        scope: str | None = Query(None, description="Root-relative path to summarize."),
        section: list[str] | None = Query(None, description="Architecture section to include; repeatable."),
        limit: int = Query(10, ge=1, le=100),
        max_bytes: int = Query(0, ge=0),
    ) -> ArchitectureResponse:
        """Return a bounded architecture overview that chains into precise Seam tools."""
        conn = _get_readonly_conn(db_path)
        try:
            result = handle_seam_architecture(
                conn,
                root,
                scope=scope,
                sections=_split_sections(section),
                limit=limit,
                max_bytes=max_bytes,
            )
        finally:
            conn.close()

        _check_handler_error(result)
        return ArchitectureResponse(**cast(dict[str, Any], result))
