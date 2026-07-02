"""FastAPI route for the 3D constellation layout (Phase 11 P2.1).

Read-only. Mirrors the register_*_routes pattern used by web_graph_search.py and
web_architecture.py. Delegates all layout computation to seam.query.layout.

WHY a separate module (not inline in web.py)?
    web.py already has multiple route families (search, graph/neighborhood,
    symbol, clusters, impact, trace, changes, constellation). Keeping each
    family in its own register_*_routes module keeps every file under 1000
    lines and makes it easy to audit "what does the layout endpoint do" without
    reading the full FastAPI app factory.

WHY Layout* Pydantic model names?
    The existing web.py already defines GraphNode (2D neighborhood graph nodes).
    Using LayoutNode / LayoutEdge / LayoutCluster avoids a name collision in the
    OpenAPI schema that would confuse the gen:types TypeScript codegen step.

WHY open a fresh connection per request (not a shared connection)?
    SQLite connections are not thread-safe across threads. FastAPI / uvicorn
    dispatches requests across a thread pool. A module-level shared connection
    would cause "database is locked" / "recursive use of cursor" errors under
    concurrent requests. The cost is one open() call per request (~0.1 ms); the
    layout engine itself is cached so the expensive query work is not repeated.

Endpoint: GET /api/graph/layout?max_nodes=N
Response: LayoutResponse (Pydantic) → OpenAPI → TypeScript types via gen:types
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from seam import config
from seam.indexer.readonly import open_readonly_connection
from seam.query.layout import compute_layout

# ── Pydantic response models (Layout* namespace to avoid 2D GraphNode collision) ──


class LayoutNodeModel(BaseModel):
    """One positioned node in the constellation layout."""

    id: int
    x: float
    y: float
    z: float
    label: str          # symbol kind (function, class, method, …)
    name: str           # qualified symbol name
    file_path: str | None
    size: float
    color: str          # hex color from the stellar scale


class LayoutEdgeModel(BaseModel):
    """One directed edge between layout nodes, referenced by node id."""

    source: int
    target: int
    type: str           # edge kind (call, import, holds, reads, writes, …)


class LayoutClusterModel(BaseModel):
    """Functional-area cluster summary for halo rendering."""

    cluster_id: int
    label: str | None   # deterministic or LLM cluster label, or None
    centroid: list[float]   # [x, y, z] mean position
    radius: float           # max member distance from centroid (min 60*1.2 for singletons)
    color: str              # teal accent hex


class LayoutResponse(BaseModel):
    """Response body for GET /api/graph/layout."""

    nodes: list[LayoutNodeModel]
    edges: list[LayoutEdgeModel]
    clusters: list[LayoutClusterModel]
    total_nodes: int   # honest count before the max_nodes cap


# ── Route registration ────────────────────────────────────────────────────────


def _get_readonly_conn(db_path: Path) -> sqlite3.Connection:
    """Open a read-only connection, raising 503 on missing or broken DB.

    Mirrors the identical helper in web_graph_search.py (CR2). The caller
    is responsible for closing the returned connection in a finally block.
    """
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


def register_layout_routes(app: FastAPI, *, db_path: Path, root: Path) -> None:
    """Register GET /api/graph/layout on the given FastAPI application.

    Args:
        app:     The FastAPI application instance.
        db_path: Absolute path to the SQLite database file.
        root:    Project root (provided for API consistency with other route modules;
                 not currently used by the layout engine).
    """

    @app.get("/api/graph/layout", response_model=LayoutResponse, tags=["graph"])
    def get_layout(
        max_nodes: int = Query(
            config.SEAM_LAYOUT_MAX_NODES,
            ge=1,
            le=config.SEAM_LAYOUT_MAX_SAFE_NODES,
            description=(
                "Maximum number of nodes to include in the layout. "
                "Nodes are selected by degree DESC (most-connected first). "
                "total_nodes in the response reflects the true count before this cap."
            ),
        ),
    ) -> LayoutResponse:
        """Compute a whole-repo 3D constellation layout (server-side positions).

        Returns pre-positioned nodes, edges, and cluster summaries for the 3D
        Constellation Explorer tab. Positions are deterministic and cached per
        (MAX(files.indexed_at), max_nodes) with a TTL of SEAM_STALENESS_TTL_SECONDS.

        The O(n²) numpy ForceAtlas2 kernel is only re-run when the index changes or
        the cache entry expires. Cluster halos are derived from node positions.

        Responds with 503 NO_INDEX when the index does not exist.
        """
        # CR2: plain connection — must close in finally block
        conn = _get_readonly_conn(db_path)
        try:
            result = compute_layout(conn, max_nodes=max_nodes)
        finally:
            conn.close()

        # model_validate coerces TypedDicts to Pydantic models (Pydantic v2)
        return LayoutResponse.model_validate(result)
