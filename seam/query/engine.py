"""Query engine — read path for all MCP tool queries.

All functions take an open sqlite3.Connection. No connection management here.
Returns typed dicts matching the MCP tool output spec in docs/api-contracts/mcp-tools.yaml.

Implementation notes:
- search(): pure FTS5 BM25 ranked query; lets OperationalError propagate (bad syntax).
- query(): FTS5 seed -> 1-hop expansion via edges -> dedupe -> sort by score -> limit.
  Callers/callees counts are computed per symbol after collecting the result set.
- context(): single-symbol lookup; callers = edges where target_name = name,
  callees = edges where source_name = name.
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
    ambiguous: bool  # True when multiple symbols share this name in the index (Phase 1)


class SearchResult(TypedDict):
    symbol: str
    file: str
    line: int
    snippet: str
    score: float


# ── A3 — search ──────────────────────────────────────────────────────────────


def search(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[SearchResult]:
    """Full-text search across symbol names and docstrings (FTS5 BM25).

    Results are ordered best-first. The returned `score` is the NEGATED bm25
    value so it matches the API contract (mcp-tools.yaml): higher = more
    relevant. (Raw bm25 is lower = better; we flip the sign at emission.)
    Raises sqlite3.OperationalError on malformed FTS5 query syntax.
    """
    sql = """
        SELECT
            s.name          AS symbol,
            f.path          AS file,
            s.start_line    AS line,
            snippet(symbols_fts, 0, '<b>', '</b>', '...', 8) AS snippet,
            bm25(symbols_fts) AS score
        FROM symbols_fts
        JOIN symbols s ON s.id = symbols_fts.rowid
        JOIN files   f ON f.id = s.file_id
        WHERE symbols_fts MATCH ?
        ORDER BY score         -- raw bm25 ascending = most relevant first
        LIMIT ?
    """
    rows = conn.execute(sql, (text, limit)).fetchall()
    return [
        SearchResult(
            symbol=row["symbol"],
            file=row["file"],
            line=row["line"],
            snippet=row["snippet"] or "",
            score=-float(row["score"]),  # flip sign: contract wants higher = better
        )
        for row in rows
    ]


# ── A4 — query ───────────────────────────────────────────────────────────────


def query(conn: sqlite3.Connection, concept: str, limit: int = 10) -> list[QueryResult]:
    """Find symbols related to a concept (FTS5 seed + 1-hop graph expansion).

    Algorithm:
      1. FTS5 MATCH to get seed symbols (with BM25 score).
      2. For each seed symbol, collect 1-hop neighbors via edges
         (both direct callees and callers — anything connected).
      3. Deduplicate; seed symbols keep their (negated) BM25 score, neighbors get 0.
      4. For each symbol in the result set, compute callers_count + callees_count.
      5. Sort seeds-first, then by score descending (higher = more relevant per
         the contract), apply limit.

    Raises sqlite3.OperationalError on malformed FTS5 syntax (the caller maps
    this to INVALID_QUERY, mirroring search() — do NOT swallow it here, or a
    malformed concept looks identical to "no matches").
    """
    # Step 1: FTS5 seed query
    seed_sql = """
        SELECT
            s.name          AS name,
            f.path          AS file,
            s.start_line    AS line,
            bm25(symbols_fts) AS score
        FROM symbols_fts
        JOIN symbols s ON s.id = symbols_fts.rowid
        JOIN files   f ON f.id = s.file_id
        WHERE symbols_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """
    # Let OperationalError (malformed FTS5) propagate — caller maps to INVALID_QUERY.
    seed_rows = conn.execute(seed_sql, (concept, limit)).fetchall()

    if not seed_rows:
        return []

    # Build seed map: name -> (file, line, score). Negate bm25 so higher = better.
    seed_map: dict[str, tuple[str, int, float]] = {}
    for row in seed_rows:
        seed_map[row["name"]] = (row["file"], row["line"], -float(row["score"]))

    # Step 2: 1-hop expansion — collect neighbors of seed symbols
    # Neighbors = targets of edges where source_name is a seed,
    #             OR sources of edges where target_name is a seed.
    neighbor_map: dict[str, tuple[str, int, float]] = {}

    seed_names = list(seed_map.keys())
    placeholders = ",".join("?" * len(seed_names))

    neighbor_sql = f"""
        SELECT DISTINCT
            s.name       AS name,
            f.path       AS file,
            s.start_line AS line
        FROM edges e
        JOIN symbols s ON (
            s.name = e.target_name OR s.name = e.source_name
        )
        JOIN files f ON f.id = s.file_id
        WHERE e.source_name IN ({placeholders})
           OR e.target_name IN ({placeholders})
    """
    neighbor_rows = conn.execute(neighbor_sql, seed_names + seed_names).fetchall()
    for row in neighbor_rows:
        name = row["name"]
        if name not in seed_map and name not in neighbor_map:
            neighbor_map[name] = (row["file"], row["line"], 0.0)

    # Step 3: Combine and deduplicate
    combined: dict[str, tuple[str, int, float]] = {**neighbor_map, **seed_map}

    # Step 4: Compute callers/callees counts for each symbol in the result set
    result: list[QueryResult] = []
    for name, (file, line, score) in combined.items():
        callers_row = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_name = ?", (name,)
        ).fetchone()
        callees_row = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_name = ?", (name,)
        ).fetchone()
        result.append(
            QueryResult(
                symbol=name,
                file=file,
                line=line,
                score=score,
                callers_count=int(callers_row[0]),
                callees_count=int(callees_row[0]),
            )
        )

    # Step 5: seeds rank above neighbors; within each group, higher score first.
    seed_name_set = set(seed_map)
    result.sort(key=lambda r: (r["symbol"] in seed_name_set, r["score"]), reverse=True)
    return result[:limit]


# ── A5 — context ─────────────────────────────────────────────────────────────


def context(conn: sqlite3.Connection, symbol_name: str) -> ContextResult | None:
    """Get 360-degree view of a symbol: location, kind, docstring, callers, callees.

    Returns None if the symbol is not in the index.
    When multiple symbols share the same name, returns the first match and sets
    ambiguous=True so the caller knows to disambiguate rather than trust the result.

    The ambiguous flag is a query-layer signal: it detects cross-file name collisions
    that edge extraction cannot see (extraction is per-file). See CONTRACT.md for
    the full contract evolution note.
    """
    # Single atomic query: fetch the first matching symbol and the total count of
    # same-name symbols via a window function. Avoids the count/fetch race of two
    # separate queries and removes the now-dead double-None check.
    row = conn.execute(
        """
        SELECT s.name, f.path AS file, s.start_line, s.end_line, s.kind, s.docstring,
               COUNT(*) OVER () AS dup_count
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.name = ?
        ORDER BY s.id
        LIMIT 1
        """,
        (symbol_name,),
    ).fetchone()

    if row is None:
        return None

    # Callers: edges where target_name = symbol_name -> source_name is the caller
    caller_rows = conn.execute(
        "SELECT source_name FROM edges WHERE target_name = ?", (symbol_name,)
    ).fetchall()

    # Callees: edges where source_name = symbol_name -> target_name is the callee
    callee_rows = conn.execute(
        "SELECT target_name FROM edges WHERE source_name = ?", (symbol_name,)
    ).fetchall()

    return ContextResult(
        symbol=row["name"],
        file=row["file"],
        line=row["start_line"],
        end_line=row["end_line"],
        kind=row["kind"],
        docstring=row["docstring"],
        callers=[r["source_name"] for r in caller_rows],
        callees=[r["target_name"] for r in callee_rows],
        ambiguous=row["dup_count"] > 1,  # True when name collision detected
    )
