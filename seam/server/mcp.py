"""MCP server setup — FastMCP stdio transport, three tools registered.

Creates and configures the MCP server instance.
Tool handlers in tools.py are thin wrappers over query.engine; the MCP
decorators here connect them to the FastMCP framework.

Usage (from cli/main.py):
    server = create_server(conn, root)
    server.run(transport="stdio")

Design:
- One FastMCP instance per process; connection is injected at creation time.
- Tools are defined as closures capturing conn + root so FastMCP's decorator
  pattern (which doesn't pass state through the call signature) stays clean.
- Return types are Any to avoid FastMCP structured-output mode, which wraps
  results in a Pydantic model we don't need.
"""

import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from seam.server.tools import (
    handle_seam_context,
    handle_seam_impact,
    handle_seam_query,
    handle_seam_search,
    handle_seam_trace,
)

# Limit defaults/bounds (mirrors tools.py constants — kept local to avoid circular import)
_QUERY_LIMIT_DEFAULT = 10
_SEARCH_LIMIT_DEFAULT = 20
_IMPACT_DEPTH_DEFAULT = 3
_IMPACT_DIRECTION_DEFAULT = "upstream"
_TRACE_DEPTH_DEFAULT = 10


def create_server(conn: sqlite3.Connection, root: Path) -> FastMCP:
    """Configure and return a FastMCP server with seam_query, seam_context, seam_search.

    Args:
        conn: Open SQLite connection to the Seam index DB.
        root: Project root Path — used to relativize file paths in results.

    Returns:
        A FastMCP instance ready for server.run(transport="stdio").
    """
    mcp: FastMCP = FastMCP(name="seam")

    @mcp.tool()
    def seam_query(concept: str, limit: int = _QUERY_LIMIT_DEFAULT) -> Any:
        """Find all code related to a concept using hybrid search (FTS5 + 1-hop graph expansion).

        Use this when you need to find where a concept lives across the codebase.
        """
        return handle_seam_query(conn, concept, root, limit=limit)

    @mcp.tool()
    def seam_context(symbol: str) -> Any:
        """Get a 360-degree view of a symbol: its callers, callees, file location, and docstring.

        Use before touching any existing function or class.
        """
        return handle_seam_context(conn, symbol, root)

    @mcp.tool()
    def seam_search(text: str, limit: int = _SEARCH_LIMIT_DEFAULT) -> Any:
        """Full-text search across all indexed symbol names and docstrings (FTS5 BM25).

        Use when you know a keyword but not the exact symbol name.
        Supports FTS5 operators: AND, OR, NOT, phrase search in quotes.
        """
        return handle_seam_search(conn, text, root, limit=limit)

    @mcp.tool()
    def seam_impact(
        target: str,
        direction: str = _IMPACT_DIRECTION_DEFAULT,
        max_depth: int = _IMPACT_DEPTH_DEFAULT,
    ) -> Any:
        """Blast-radius analysis — what breaks if I change this symbol?

        Returns all symbols that depend on the target (upstream), that the target
        depends on (downstream), or both — grouped into risk tiers by distance:
          WILL_BREAK       (distance 1) — direct dependents, definitely affected.
          LIKELY_AFFECTED  (distance 2) — indirect dependents, probably affected.
          MAY_NEED_TESTING (distance 3+) — transitive dependents, test to be sure.

        Each entry carries the aggregated path confidence (EXTRACTED | INFERRED | AMBIGUOUS)
        so you know which conclusions to lean on and which to verify by reading.

        Use before editing any symbol to understand the blast radius.
        """
        return handle_seam_impact(conn, target, root, direction=direction, max_depth=max_depth)

    @mcp.tool()
    def seam_trace(
        source: str,
        target: str,
        max_depth: int = _TRACE_DEPTH_DEFAULT,
    ) -> Any:
        """Trace the call/dependency path between two symbols.

        Returns the shortest path from source to target as an ordered list of hops,
        where each hop carries the edge kind (call | import) and per-edge confidence
        (EXTRACTED | INFERRED | AMBIGUOUS).

        Also returns one-hop callers and callees for both symbols so you can see
        the immediate neighborhood alongside the path.

        Use this when you need to understand how control flows from one symbol to
        another, or to answer "how does X reach Y?" without manual grep.

        Returns found=false (paths=[]) when no path exists — this is a real,
        distinguishable "not connected" answer, not an error.

        Per-hop confidence lets you flag any hop that rests on an AMBIGUOUS edge
        (name collision at extraction time) so you know which conclusions are certain
        and which need manual verification.
        """
        return handle_seam_trace(conn, source, target, root, max_depth=max_depth)

    return mcp
