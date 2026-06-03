"""FastAPI web app factory for the Seam Explorer.

Exposed as: create_web_app(db_path: Path, root: Path) -> FastAPI

Design:
- App construction NEVER opens the DB or reads any files. Connections are opened
  PER REQUEST inside route handlers. This ensures create_web_app() and .openapi()
  work with no DB file present (required for OpenAPI schema dumping at startup and
  for FastAPI auto-docs).
- Routes delegate all business logic to existing handle_seam_* handlers (tools.py)
  and build_neighborhood (graph_api.py) — zero query-logic duplication.
- Pydantic response models are the source of truth for TS codegen (openapi-typescript).
- StaticFiles mounted at '/' serves seam/_web/ (built SPA). If the directory is absent
  (dev before first build), a small HTML page tells the user to run 'make build-web'.
- 127.0.0.1-only binding is enforced by the CLI (seam serve), not by this module.
  This module is transport-agnostic — only the factory and routes live here.
- Error mapping:
    NO_INDEX    → 503 Service Unavailable (index not ready)
    INVALID_INPUT → 400 Bad Request
    unknown symbol → 404 {"found": false}

IMPORT NOTE: 'import fastapi' at the top of this module is intentional. This module
is only imported via the lazy CLI path (`seam serve` / `create_web_app`). The `fastapi`
package is an optional extra (`seam-mcp[web]`) and a dev group dep — it is never imported
at CLI startup, only when this file is imported. See CLAUDE.md "FastAPI import is lazy".
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from seam.indexer.db import connect
from seam.server.graph_api import build_neighborhood
from seam.server.tools import (
    handle_seam_clusters,
    handle_seam_context,
    handle_seam_search,
    handle_seam_why,
)

# ── Pydantic response models (source of truth for TS codegen) ─────────────────
# These models define the exact JSON shape that openapi-typescript will consume.
# Keep field names snake_case — the TS codegen will use them verbatim.


class StatusResponse(BaseModel):
    """Response for GET /api/status."""

    root: str
    symbol_count: int
    edge_count: int
    cluster_count: int
    last_indexed: str | None
    languages: list[str]


class SearchResultItem(BaseModel):
    """One item in a search result list."""

    name: str
    kind: str
    file: str
    line: int
    signature: str | None
    cluster_id: int | None
    cluster_label: str | None


class SearchResponse(BaseModel):
    """Response for GET /api/search."""

    results: list[SearchResultItem]


class GraphNode(BaseModel):
    """A node in the neighborhood graph (one per unique symbol NAME)."""

    id: str
    name: str
    kind: str
    signature: str | None
    visibility: str | None
    is_exported: bool | None
    cluster_id: int | None
    cluster_label: str | None
    definition_count: int


class GraphEdge(BaseModel):
    """An edge in the neighborhood graph."""

    id: int
    source: str
    target: str
    kind: str
    confidence: str


class NeighborhoodResponse(BaseModel):
    """Response for GET /api/graph/neighborhood."""

    center: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class SymbolDefinition(BaseModel):
    """One definition (file-level occurrence) of a symbol."""

    file: str
    line: int
    signature: str | None
    docstring: str | None
    visibility: str | None
    is_exported: bool | None
    qualified_name: str | None
    decorators: list[str]


class ClusterInfo(BaseModel):
    """Cluster identity for a symbol."""

    id: int
    label: str | None


class WhyComment(BaseModel):
    """A WHY/HACK/NOTE/TODO/FIXME comment near a symbol."""

    kind: str
    text: str
    file: str
    line: int


class SymbolResponse(BaseModel):
    """Response for GET /api/symbol/{name}."""

    name: str
    definitions: list[SymbolDefinition]
    callers: list[str]
    callees: list[str]
    cluster: ClusterInfo | None
    peers: list[str]
    why: list[WhyComment]


class ClusterItem(BaseModel):
    """One cluster in the cluster list.

    `representative` is a member symbol NAME the UI can center the graph on when
    the cluster is clicked as an entry point — clusters themselves are not symbols,
    so the landing page needs a real symbol to open. None only if the cluster has
    no clustered symbols (shouldn't happen, but degrades safely to label fallback).
    """

    cluster_id: int
    label: str | None
    size: int
    representative: str | None


class ClustersResponse(BaseModel):
    """Response for GET /api/clusters."""

    clusters: list[ClusterItem]


class ErrorResponse(BaseModel):
    """Standard error body for 4xx/5xx responses."""

    code: str
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

# HTML shown when seam/_web/ is absent (dev, before first `make build-web`).
_BUILD_HINT_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Seam Explorer</title></head>
<body>
  <h1>Seam Explorer — frontend not built yet</h1>
  <p>Run <code>make build-web</code> to build the frontend, then restart <code>seam serve</code>.</p>
  <p>The <strong>API</strong> is available at <a href="/api/status">/api/status</a>.</p>
</body>
</html>
"""


def _get_conn(db_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection for this request.

    WHY per-request: app construction must not touch the DB (OpenAPI schema dump
    must work with no DB file present). Opening here ensures each request gets an
    isolated connection that is closed after the request completes.

    Raises HTTPException 503 when no index exists (db_path absent).
    Raises HTTPException 503 on DB open failure.
    """
    if not db_path.exists():
        # The index has not been created yet — tell the caller to run seam init.
        raise HTTPException(
            status_code=503,
            detail={"code": "NO_INDEX", "message": "No index found. Run 'seam init' first."},
        )
    try:
        return connect(db_path)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "DB_ERROR", "message": f"Failed to open database: {exc}"},
        ) from exc


def _check_handler_error(result: Any) -> None:
    """Raise HTTPException if the handler returned an error dict.

    Handlers return {"error": "CODE", "message": "..."} on invalid input.
    Map these to HTTP 4xx responses rather than 200s with error payloads.

    Error code → HTTP status:
        INVALID_INPUT  → 400
        INVALID_QUERY  → 400
        *              → 400 (safe default for handler errors)
    """
    if isinstance(result, dict) and "error" in result:
        code = result.get("error", "UNKNOWN")
        message = result.get("message", "")
        raise HTTPException(
            status_code=400,
            detail={"code": code, "message": message},
        )


def _fetch_all_symbol_definitions(
    conn: sqlite3.Connection,
    symbol_name: str,
    root: Path,
) -> list[dict[str, Any]]:
    """Fetch all definitions (file-level rows) for a symbol name.

    Handles homonyms: multiple files can define the same name. Returns one entry
    per (file, line) pair so the detail panel can list all of them.
    """
    rows = conn.execute(
        """
        SELECT
            f.path      AS file,
            s.start_line AS line,
            s.signature,
            s.docstring,
            s.visibility,
            s.is_exported,
            s.qualified_name,
            s.decorators
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name = ?
        ORDER BY s.id
        """,
        (symbol_name,),
    ).fetchall()

    result = []
    for row in rows:
        # Relativize file path so the UI gets portable paths
        try:
            file_rel = str(Path(row["file"]).relative_to(root))
        except ValueError:
            file_rel = row["file"]

        # Decode is_exported (stored as 0/1/NULL)
        raw_exp = row["is_exported"]
        is_exported: bool | None = None if raw_exp is None else bool(raw_exp)

        # Decode decorators (stored as JSON text, NULL for pre-v5 rows)
        raw_dec = row["decorators"]
        decorators: list[str] = []
        if raw_dec is not None:
            try:
                decorators = json.loads(raw_dec)
            except (json.JSONDecodeError, TypeError, ValueError):
                decorators = []

        result.append({
            "file": file_rel,
            "line": row["line"],
            "signature": row["signature"],
            "docstring": row["docstring"],
            "visibility": row["visibility"],
            "is_exported": is_exported,
            "qualified_name": row["qualified_name"],
            "decorators": decorators,
        })
    return result


def _fetch_languages(conn: sqlite3.Connection) -> list[str]:
    """Return the distinct languages present in the index."""
    rows = conn.execute("SELECT DISTINCT language FROM files WHERE language IS NOT NULL").fetchall()
    return sorted(row["language"] for row in rows)


# ── App factory ───────────────────────────────────────────────────────────────


def create_web_app(db_path: Path, root: Path) -> FastAPI:
    """Create and configure the Seam Explorer FastAPI application.

    CRITICAL: do NOT open the DB or read any files here. Only call this function
    during app construction. All DB access happens inside route handlers (per request).

    Args:
        db_path: Absolute path to the seam.db SQLite file.
        root:    Project root used for file path relativization in responses.

    Returns:
        Configured FastAPI application. Mount and run with uvicorn.
    """
    # FastAPI instance with metadata for the generated OpenAPI schema.
    # The schema is consumed by openapi-typescript to generate src/api/types.ts.
    app = FastAPI(
        title="Seam Explorer API",
        description="Local code-intelligence graph explorer for Seam indexes.",
        version="1.0.0",
    )

    # ── Route: GET /api/status ────────────────────────────────────────────────

    @app.get("/api/status", response_model=StatusResponse, tags=["status"])
    def get_status() -> StatusResponse:
        """Return index statistics and metadata.

        Returns symbol_count, edge_count, cluster_count, last_indexed timestamp,
        and the list of languages present in the index.
        """
        conn = _get_conn(db_path)
        try:
            symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            # Guard for pre-v4 indexes (no clusters table)
            try:
                cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
            except Exception:
                cluster_count = 0

            # Most recent indexed_at across all files
            last_indexed_row = conn.execute("SELECT MAX(indexed_at) FROM files").fetchone()[0]
            last_indexed: str | None = None
            if last_indexed_row is not None:
                last_indexed = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(last_indexed_row)
                )

            languages = _fetch_languages(conn)
        finally:
            conn.close()

        return StatusResponse(
            root=str(root),
            symbol_count=symbol_count,
            edge_count=edge_count,
            cluster_count=cluster_count,
            last_indexed=last_indexed,
            languages=languages,
        )

    # ── Route: GET /api/search ────────────────────────────────────────────────

    @app.get("/api/search", response_model=SearchResponse, tags=["search"])
    def get_search(
        q: str = Query(..., description="Search query text"),
        limit: int = Query(20, ge=1, le=100, description="Maximum results to return"),
    ) -> SearchResponse:
        """Full-text search over symbol names, docstrings, and signatures.

        Reuses handle_seam_search which applies FTS5 + rescoring + LIKE/fuzzy fallback.
        Returns matching symbols with their file location, kind, and cluster info.
        """
        conn = _get_conn(db_path)
        try:
            result = handle_seam_search(conn, q, root, limit=limit)
        finally:
            conn.close()

        # Handler returns error dict on invalid input
        _check_handler_error(result)

        # Build response: handle_seam_search returns SearchResult dicts.
        # SearchResult has: symbol, file, line, snippet, score.
        # We need to enrich with kind, signature, cluster_id, cluster_label.
        # Rather than re-query, accept that search results carry only the core FTS fields;
        # the frontend can call /api/symbol/{name} for full detail on selection.
        # Cast: _check_handler_error confirmed this is not an error dict, so it's a list.
        search_rows = cast(list[dict[str, Any]], result)
        items: list[SearchResultItem] = []
        for r in search_rows:
            items.append(SearchResultItem(
                name=str(r["symbol"]),
                kind="",           # SearchResult doesn't include kind — caller uses symbol endpoint
                file=str(r["file"]),
                line=int(r["line"]),
                signature=None,    # SearchResult doesn't include signature
                cluster_id=None,   # SearchResult doesn't include cluster
                cluster_label=None,
            ))
        return SearchResponse(results=items)

    # ── Route: GET /api/graph/neighborhood ────────────────────────────────────

    @app.get(
        "/api/graph/neighborhood",
        response_model=NeighborhoodResponse,
        tags=["graph"],
    )
    def get_neighborhood(
        symbol: str = Query(..., description="Symbol name to center the graph on"),
        # Literal constrains the param at the boundary: FastAPI returns 422 for any
        # value outside the set, rather than build_neighborhood silently treating a
        # typo'd direction as "both" (the else branch). Defensive — the SPA only ever
        # sends valid values, but a public localhost API shouldn't accept garbage.
        direction: Literal["both", "callers", "callees"] = Query(
            "both",
            description="Edge direction: 'both' | 'callers' | 'callees'",
        ),
    ) -> NeighborhoodResponse:
        """Return a depth-1 neighborhood graph for a symbol.

        Nodes = unique symbol NAMES (homonym-collapse, consistent with seam_impact/trace).
        Edges carry kind (call/import) and confidence (EXTRACTED/INFERRED/AMBIGUOUS).
        Unknown symbol returns empty nodes/edges (not a 404 — the client may be expanding
        a node whose declaration is outside the indexed files).
        """
        conn = _get_conn(db_path)
        try:
            neighborhood = build_neighborhood(conn, symbol, direction=direction)
        finally:
            conn.close()

        return NeighborhoodResponse(
            center=neighborhood["center"],
            nodes=[GraphNode(**n) for n in neighborhood["nodes"]],
            edges=[GraphEdge(**e) for e in neighborhood["edges"]],
        )

    # ── Route: GET /api/symbol/{name} ─────────────────────────────────────────

    @app.get(
        "/api/symbol/{name}",
        response_model=SymbolResponse,
        responses={404: {"model": dict, "description": "Symbol not found"}},
        tags=["symbol"],
    )
    def get_symbol(name: str) -> SymbolResponse:
        """Return full detail for a symbol name.

        Returns ALL definitions (for homonyms), callers, callees, cluster info,
        peers, and WHY/HACK/NOTE/TODO/FIXME comments.

        Reuses handle_seam_context (360-degree view) and handle_seam_why.
        404 with {"found": false} when the symbol is not in the index.
        """
        conn = _get_conn(db_path)
        try:
            ctx = handle_seam_context(conn, name, root)
            if ctx is None:
                # Unknown symbol
                raise HTTPException(status_code=404, detail={"found": False})
            _check_handler_error(ctx)

            # Fetch all definitions (handle_seam_context returns only the canonical one)
            definitions = _fetch_all_symbol_definitions(conn, name, root)

            # WHY comments for this symbol
            why_raw = handle_seam_why(conn, root, symbol=name)
            _check_handler_error(why_raw)
        finally:
            conn.close()

        # Build cluster info (None when symbol is not clustered)
        cluster: ClusterInfo | None = None
        if ctx.get("cluster_id") is not None:
            cluster = ClusterInfo(
                id=ctx["cluster_id"],
                label=ctx.get("cluster_label"),
            )

        # Build WHY comment list.
        # Cast: _check_handler_error confirmed why_raw is not an error dict, so it's a list.
        why_comments: list[WhyComment] = []
        for w in cast(list[dict[str, Any]], why_raw or []):
            why_comments.append(WhyComment(
                kind=str(w.get("marker", "")),
                text=str(w.get("text", "")),
                file=str(w.get("file", "")),
                line=int(w.get("line", 0)),
            ))

        return SymbolResponse(
            name=name,
            definitions=[SymbolDefinition(**d) for d in definitions],
            callers=ctx.get("callers", []),
            callees=ctx.get("callees", []),
            cluster=cluster,
            peers=ctx.get("cluster_peers", []),
            why=why_comments,
        )

    # ── Route: GET /api/clusters ──────────────────────────────────────────────

    @app.get("/api/clusters", response_model=ClustersResponse, tags=["clusters"])
    def get_clusters() -> ClustersResponse:
        """Return all functional-area clusters in the index.

        Each cluster has an id, label (deterministic or LLM-generated), and size
        (number of symbols). Returns an empty list when no clusters exist (e.g.
        fresh index with SEAM_CLUSTER_MIN_SIZE=2 and very few edges).
        """
        conn = _get_conn(db_path)
        try:
            raw = handle_seam_clusters(conn, root)
            # Pick one representative member symbol per cluster so the landing-page
            # entry points open a real neighborhood (clusters aren't symbols). The
            # name comes from the MIN(id) row per cluster → deterministic across runs.
            rep_rows = conn.execute(
                "SELECT cluster_id, name, MIN(id) FROM symbols "
                "WHERE cluster_id IS NOT NULL GROUP BY cluster_id"
            ).fetchall()
        finally:
            conn.close()

        representatives = {row["cluster_id"]: row["name"] for row in rep_rows}
        items: list[ClusterItem] = []
        for c in raw:  # type: ignore[union-attr]
            items.append(ClusterItem(
                cluster_id=c["id"],
                label=c.get("label"),
                size=c.get("size", 0),
                representative=representatives.get(c["id"]),
            ))
        return ClustersResponse(clusters=items)

    # ── Static SPA mount ─────────────────────────────────────────────────────
    # Mount the built SPA at '/'. This must come AFTER all /api/* routes so FastAPI
    # resolves API routes before falling through to static files.
    # html=True enables index.html serving for client-side routing (SPA fallback).

    web_dir = Path(__file__).parent.parent / "_web"
    if web_dir.exists() and web_dir.is_dir():
        # Built SPA present — serve it.
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="static")
    else:
        # Frontend not built yet — serve a helpful hint page at '/'.
        # WHY a separate route instead of StaticFiles: StaticFiles requires the
        # directory to exist; a plain GET route is the fallback that always works.
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def spa_root() -> HTMLResponse:
            """Fallback landing page when seam/_web/ is absent."""
            return HTMLResponse(_BUILD_HINT_HTML)

    return app
