"""Handler for docs/spec grounding queries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from seam.query.grounding import query_grounding
from seam.server.handler_common import _maybe_attach_staleness


def handle_seam_grounding(
    conn: sqlite3.Connection,
    root: Path,
    *,
    symbol: str | None = None,
    file: str | None = None,
    route: str | None = None,
    config_key: str | None = None,
    resource: str | None = None,
    doc_path: str | None = None,
    query: str | None = None,
    doc_kind: str | None = None,
    status: str | None = None,
    relation_type: str | None = None,
    limit: int | None = None,
    include_snippets: bool = False,
) -> dict[str, Any]:
    """Return document anchors that explicitly ground code/spec questions."""
    result = query_grounding(
        conn,
        root,
        symbol=symbol,
        file=file,
        route=route,
        config_key=config_key,
        resource=resource,
        doc_path=doc_path,
        query=query,
        doc_kind=doc_kind,
        status=status,
        relation_type=relation_type,
        limit=limit,
        include_snippets=include_snippets,
    )
    shaped = _maybe_attach_staleness(cast(dict[str, Any], result), conn, root)
    if shaped.get("index_status", {}).get("stale"):
        shaped.setdefault("caveats", []).append(
            "Index is stale; run seam sync before treating document grounding as current."
        )
    return cast(dict[str, Any], shaped)
