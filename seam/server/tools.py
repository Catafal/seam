"""MCP tool handlers — thin adapters between MCP protocol and query engine.

Each handler: validates input → clamps limits → calls query.engine or analysis →
relativizes file paths → returns MCP-compatible response dict.

No business logic here. Query logic lives in seam/query/engine.py.
Impact logic lives in seam/analysis/impact.py.

Error conventions (matching mcp-tools.yaml):
  {"error": "INVALID_INPUT", "message": "..."} — blank/whitespace input
  {"error": "INVALID_QUERY", "message": "..."} — bad FTS5 syntax (seam_search only)
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

from seam.analysis import flows as flows_module
from seam.analysis import impact as impact_module
from seam.analysis.flows import EdgeHop
from seam.query import engine

logger = logging.getLogger(__name__)

# ── Limit bounds (from mcp-tools.yaml) ────────────────────────────────────────

_QUERY_LIMIT_MIN = 1
_QUERY_LIMIT_MAX = 50
_QUERY_LIMIT_DEFAULT = 10

_SEARCH_LIMIT_MIN = 1
_SEARCH_LIMIT_MAX = 100
_SEARCH_LIMIT_DEFAULT = 20

_IMPACT_DEPTH_DEFAULT = 3
_IMPACT_DIRECTION_DEFAULT = "upstream"

_TRACE_DEPTH_MIN = 1
_TRACE_DEPTH_MAX = 10
_TRACE_DEPTH_DEFAULT = 10


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize_edge_hop(hop: EdgeHop) -> dict[str, Any]:
    """Serialize an EdgeHop TypedDict to a plain dict for JSON-safe output."""
    return {
        "name": hop["name"],
        "kind": hop["kind"],
        "confidence": hop["confidence"],
    }


def _relativize(abs_path: str, root: Path) -> str:
    """Return abs_path relative to root; falls back to abs_path if not under root."""
    try:
        return str(Path(abs_path).relative_to(root))
    except ValueError:
        return abs_path


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp value to [lo, hi] inclusive."""
    return max(lo, min(hi, value))


def _invalid_input(message: str) -> dict[str, Any]:
    # Log so an operator can see what an agent actually sent (the error dict
    # otherwise vanishes into the agent's response with no server-side trace).
    logger.warning("rejected (INVALID_INPUT): %s", message)
    return {"error": "INVALID_INPUT", "message": message}


def _invalid_query(message: str) -> dict[str, Any]:
    logger.warning("rejected (INVALID_QUERY): %s", message)
    return {"error": "INVALID_QUERY", "message": message}


# ── Handlers ──────────────────────────────────────────────────────────────────


def handle_seam_query(
    conn: sqlite3.Connection,
    concept: str,
    root: Path,
    limit: int = _QUERY_LIMIT_DEFAULT,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_query MCP tool.

    Finds symbols related to a concept using hybrid FTS5 + 1-hop graph expansion.
    Returns a list of QueryResult dicts, or an error dict on bad input.

    Limit is clamped to [1, 50].
    """
    # Validate: concept must not be empty or whitespace-only
    if not concept or not concept.strip():
        return _invalid_input("concept must not be empty or whitespace-only")

    # Clamp limit to spec bounds
    safe_limit = _clamp(limit, _QUERY_LIMIT_MIN, _QUERY_LIMIT_MAX)

    # Mirror seam_search: a malformed FTS5 concept maps to INVALID_QUERY rather
    # than silently returning [] (which an agent would read as "no such code").
    try:
        results = engine.query(conn, concept.strip(), safe_limit)
    except sqlite3.OperationalError as exc:
        return _invalid_query(f"FTS5 query syntax error: {exc}")

    # Relativize file paths so consumers get portable paths
    return [
        {
            "symbol": r["symbol"],
            "file": _relativize(r["file"], root),
            "line": r["line"],
            "score": r["score"],
            "callers_count": r["callers_count"],
            "callees_count": r["callees_count"],
        }
        for r in results
    ]


def handle_seam_context(
    conn: sqlite3.Connection,
    symbol: str,
    root: Path,
) -> dict[str, Any] | None:
    """Handler for the seam_context MCP tool.

    Returns a ContextResult dict for a known symbol, None for unknown symbols,
    or an error dict on blank input.
    """
    # Validate: symbol must not be empty or whitespace-only
    if not symbol or not symbol.strip():
        return _invalid_input("symbol must not be empty or whitespace-only")

    result = engine.context(conn, symbol.strip())

    if result is None:
        return None

    return {
        "symbol": result["symbol"],
        "file": _relativize(result["file"], root),
        "line": result["line"],
        "end_line": result["end_line"],
        "kind": result["kind"],
        "docstring": result["docstring"],
        "callers": result["callers"],
        "callees": result["callees"],
        "ambiguous": result["ambiguous"],  # Phase 1: True when name collision detected
    }


def handle_seam_search(
    conn: sqlite3.Connection,
    text: str,
    root: Path,
    limit: int = _SEARCH_LIMIT_DEFAULT,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_search MCP tool.

    Full-text search across symbol names and docstrings (FTS5 BM25).
    Returns a list of SearchResult dicts, or an error dict on bad input.

    Limit is clamped to [1, 100].
    Maps sqlite3.OperationalError (FTS5 syntax error) to INVALID_QUERY.
    """
    # Validate: text must not be empty or whitespace-only
    if not text or not text.strip():
        return _invalid_input("text must not be empty or whitespace-only")

    # Clamp limit to spec bounds
    safe_limit = _clamp(limit, _SEARCH_LIMIT_MIN, _SEARCH_LIMIT_MAX)

    try:
        results = engine.search(conn, text.strip(), safe_limit)
    except sqlite3.OperationalError as exc:
        # FTS5 rejects malformed query syntax with OperationalError
        return _invalid_query(f"FTS5 query syntax error: {exc}")

    # Relativize file paths
    return [
        {
            "symbol": r["symbol"],
            "file": _relativize(r["file"], root),
            "line": r["line"],
            "snippet": r["snippet"],
            "score": r["score"],
        }
        for r in results
    ]


def handle_seam_impact(
    conn: sqlite3.Connection,
    target: str,
    root: Path,
    direction: str = _IMPACT_DIRECTION_DEFAULT,
    max_depth: int = _IMPACT_DEPTH_DEFAULT,
) -> dict[str, Any]:
    """Handler for the seam_impact MCP tool.

    Computes blast radius for a target symbol: which symbols are affected if the
    target changes, grouped into risk tiers by distance.

    Args:
        conn:       Open SQLite connection.
        target:     Symbol name to analyze (must not be blank/whitespace).
        root:       Project root for path relativization. Each TieredEntry includes a
                    `file` field (absolute path from the analysis layer) which is
                    relativized to root before returning.
        direction:  "upstream" | "downstream" | "both". Default: "upstream".
        max_depth:  Max hops. Clamped to [1, 10]. Default: 3.

    Returns:
        A JSON-able dict with the impact result, or an error dict on bad input.
        Top-level keys always include `found` (bool) and `target` (str).
        Shape for direction="upstream":
            {"found": bool, "target": str,
             "upstream": {"WILL_BREAK": [...], "LIKELY_AFFECTED": [...], "MAY_NEED_TESTING": [...]}}
        Shape for direction="both":
            {"found": bool, "target": str,
             "upstream": {...tiers...}, "downstream": {...tiers...}}

        Each entry in a tier list includes a `file` field:
            file (str | None) — relative path from project root for indexed symbols;
                                None for names not in the symbols table.

    Error shapes:
        {"error": "INVALID_INPUT", "message": "..."} — blank target or invalid direction.
    """
    # Validate: target must not be empty or whitespace-only.
    if not target or not target.strip():
        return _invalid_input("target must not be empty or whitespace-only")

    # Validate direction before passing to impact (impact raises ValueError on bad direction,
    # but we want the standard INVALID_INPUT shape here in the handler).
    valid_directions = {"upstream", "downstream", "both"}
    if direction not in valid_directions:
        return _invalid_input(
            f"direction must be one of: {sorted(valid_directions)}; got {direction!r}"
        )

    # Clamp max_depth via impact module's own clamp helper (single source of truth).
    safe_depth = impact_module.clamp_depth(max_depth)

    raw = impact_module.impact(
        conn,
        target=target.strip(),
        direction=direction,
        max_depth=safe_depth,
    )

    # Build the response: pass found/target through, relativize file paths in entries.
    response: dict[str, Any] = {
        "found": raw["found"],
        "target": raw["target"],
    }

    # Relativize each TieredEntry's `file` field using the provided root.
    # `file` is an absolute path (or None) from the analysis layer.
    for dir_key in ("upstream", "downstream"):
        if dir_key not in raw:
            continue
        tier_group = raw[dir_key]
        response[dir_key] = {
            tier: [
                {
                    "name": entry["name"],
                    "distance": entry["distance"],
                    "confidence": entry["confidence"],
                    "tier": entry["tier"],
                    "file": _relativize(entry["file"], root) if entry["file"] is not None else None,
                }
                for entry in entries
            ]
            for tier, entries in tier_group.items()
        }

    return response


def handle_seam_trace(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    root: Path,
    max_depth: int = _TRACE_DEPTH_DEFAULT,
) -> dict[str, Any]:
    """Handler for the seam_trace MCP tool.

    Finds the shortest call/dependency path from source to target, and also
    returns one-hop callers and callees for both symbols so the agent can see
    the immediate neighborhood alongside the path.

    Args:
        conn:       Open SQLite connection.
        source:     Starting symbol name (must not be blank/whitespace).
        target:     Destination symbol name (must not be blank/whitespace).
        root:       Project root for path relativization (not currently used —
                    paths in flows are symbol names, not file paths; kept for
                    API consistency with other handlers).
        max_depth:  Max hops for path finding. Clamped to [1, 10]. Default: 10.

    Returns:
        A JSON-able dict with:
            found       (bool)      — True if a path was found.
            source      (str)       — the queried source name (echoed back).
            target      (str)       — the queried target name (echoed back).
            paths       (list)      — list of paths; each path is a list of Hop dicts.
                                      Empty list when source and target are not connected.
            callers_source (list)   — one-hop callers of source (EdgeHop dicts).
            callees_source (list)   — one-hop callees of source (EdgeHop dicts).
            callers_target (list)   — one-hop callers of target (EdgeHop dicts).
            callees_target (list)   — one-hop callees of target (EdgeHop dicts).

        Each Hop dict:
            from_name   (str) — source of this edge
            to_name     (str) — target of this edge
            kind        (str) — 'call' | 'import'
            confidence  (str) — EXTRACTED | INFERRED | AMBIGUOUS

        Each EdgeHop dict:
            name        (str) — neighboring symbol
            kind        (str) — 'call' | 'import'
            confidence  (str) — EXTRACTED | INFERRED | AMBIGUOUS

    Error shapes:
        {"error": "INVALID_INPUT", "message": "..."} — blank source or target.
    """
    # Validate: source and target must not be empty or whitespace-only.
    if not source or not source.strip():
        return _invalid_input("source must not be empty or whitespace-only")
    if not target or not target.strip():
        return _invalid_input("target must not be empty or whitespace-only")

    # Clamp depth to valid range.
    safe_depth = _clamp(max_depth, _TRACE_DEPTH_MIN, _TRACE_DEPTH_MAX)

    clean_source = source.strip()
    clean_target = target.strip()

    # Find the shortest path from source to target.
    paths = flows_module.trace(conn, clean_source, clean_target, max_depth=safe_depth)

    # Gather one-hop neighborhood for both symbols — useful context for the agent.
    callers_source = flows_module.callers(conn, clean_source)
    callees_source = flows_module.callees(conn, clean_source)
    callers_target = flows_module.callers(conn, clean_target)
    callees_target = flows_module.callees(conn, clean_target)

    # Serialize paths: each Path is list[Hop]; Hop is already a plain TypedDict.
    # Convert to plain dicts for JSON-safety.
    serialized_paths = [
        [
            {
                "from_name": hop["from_name"],
                "to_name": hop["to_name"],
                "kind": hop["kind"],
                "confidence": hop["confidence"],
            }
            for hop in path
        ]
        for path in paths
    ]

    return {
        "found": len(paths) > 0,
        "source": clean_source,
        "target": clean_target,
        "paths": serialized_paths,
        "callers_source": [_serialize_edge_hop(h) for h in callers_source],
        "callees_source": [_serialize_edge_hop(h) for h in callees_source],
        "callers_target": [_serialize_edge_hop(h) for h in callers_target],
        "callees_target": [_serialize_edge_hop(h) for h in callees_target],
    }
