"""Query engine — read path for all MCP tool queries.

All functions take an open sqlite3.Connection. No connection management here.
Returns typed dicts matching the MCP tool output spec in docs/api-contracts/mcp-tools.yaml.
"""

import sqlite3
from typing import TypedDict


class QueryResult(TypedDict):
    symbol: str
    file: str
    line: int
    score: float
    callers_count: int
    callees_count: int


class ContextResult(TypedDict):
    symbol: str
    file: str
    line: int
    end_line: int
    kind: str
    docstring: str | None
    callers: list[str]
    callees: list[str]


class SearchResult(TypedDict):
    symbol: str
    file: str
    line: int
    snippet: str
    score: float


# Implementations: see IMPLEMENTATION_PLAN.md steps 5.1, 5.2, 5.3


def query(conn: sqlite3.Connection, concept: str, limit: int = 10) -> list[QueryResult]:
    """Find symbols related to a concept (FTS5 + 1-hop graph expansion)."""
    raise NotImplementedError("Implement in step 5.2")


def context(conn: sqlite3.Connection, symbol_name: str) -> ContextResult | None:
    """Get 360° view of a symbol: location, callers, callees, docstring."""
    raise NotImplementedError("Implement in step 5.3")


def search(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[SearchResult]:
    """Full-text search across symbol names and docstrings (FTS5 BM25)."""
    raise NotImplementedError("Implement in step 5.1")
