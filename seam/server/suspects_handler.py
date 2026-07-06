"""handle_seam_suspects handler for conservative cleanup review.

The query module owns suspect classification. This handler only applies the
standard server contracts: typed error passthrough, index staleness annotation,
and transport-neutral JSON shaping.
"""

import sqlite3
from pathlib import Path
from typing import Any, cast

from seam.query.suspects import SuspectResult, analyze_suspects
from seam.server.handler_common import _maybe_attach_staleness


def handle_seam_suspects(
    conn: sqlite3.Connection,
    root: Path,
    *,
    mode: str = "symbols",
    target: str | None = None,
    file_pattern: str | None = None,
    kind: str | None = None,
    language: str | None = None,
    visibility: str | None = None,
    is_exported: bool | None = None,
    test_scope: str = "source",
    limit: int | None = None,
) -> dict[str, Any]:
    """Handler for conservative cleanup suspect analysis."""
    raw = analyze_suspects(
        conn,
        root,
        mode=mode,  # type: ignore[arg-type]
        target=target,
        file_pattern=file_pattern,
        kind=kind,
        language=language,
        visibility=visibility,
        is_exported=is_exported,
        test_scope=test_scope,  # type: ignore[arg-type]
        limit=limit,
    )
    if "error" in raw:
        return cast(dict[str, Any], raw)
    result = cast(SuspectResult, raw)
    shaped = _maybe_attach_staleness(cast(dict[str, Any], result), conn, root)
    if shaped.get("index_status", {}).get("stale"):
        shaped.setdefault("caveats", []).append(
            "Index is stale; run seam sync before treating suspect evidence as current."
        )
    return shaped
