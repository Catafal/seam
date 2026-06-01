"""MCP tool handlers — thin adapters between MCP protocol and query engine.

Each handler: validates input → clamps limits → calls query.engine →
relativizes file paths → returns MCP-compatible response dict.

No business logic here. All logic lives in seam/query/engine.py.

Error conventions (matching mcp-tools.yaml):
  {"error": "INVALID_INPUT", "message": "..."} — blank/whitespace input
  {"error": "INVALID_QUERY", "message": "..."} — bad FTS5 syntax (seam_search only)
"""

import sqlite3
from pathlib import Path
from typing import Any

from seam.query import engine

# ── Limit bounds (from mcp-tools.yaml) ────────────────────────────────────────

_QUERY_LIMIT_MIN = 1
_QUERY_LIMIT_MAX = 50
_QUERY_LIMIT_DEFAULT = 10

_SEARCH_LIMIT_MIN = 1
_SEARCH_LIMIT_MAX = 100
_SEARCH_LIMIT_DEFAULT = 20


# ── Helpers ───────────────────────────────────────────────────────────────────


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
    return {"error": "INVALID_INPUT", "message": message}


def _invalid_query(message: str) -> dict[str, Any]:
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

    results = engine.query(conn, concept.strip(), safe_limit)

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
