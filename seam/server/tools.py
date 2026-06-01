"""MCP tool handlers — thin adapters between MCP protocol and query engine.

Each handler: validates input → calls query.engine → returns MCP-compatible response.
No business logic here. All logic lives in seam/query/engine.py.
"""

from typing import Any

# Implementations: see IMPLEMENTATION_PLAN.md step 8.2


def handle_seam_query(concept: str, limit: int = 10) -> dict[str, Any]:
    """Handler for seam_query MCP tool."""
    raise NotImplementedError("Implement in step 8.2")


def handle_seam_context(symbol: str) -> dict[str, Any]:
    """Handler for seam_context MCP tool."""
    raise NotImplementedError("Implement in step 8.2")


def handle_seam_search(text: str, limit: int = 20) -> dict[str, Any]:
    """Handler for seam_search MCP tool."""
    raise NotImplementedError("Implement in step 8.2")
