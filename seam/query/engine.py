"""Query engine — read path for all MCP tool queries.

All functions take an open sqlite3.Connection. No connection management here.
Returns typed dicts matching the MCP tool output spec in docs/api-contracts/mcp-tools.yaml.

Implementation notes:
- search(): FTS5 BM25 + fts.build_match_query() OR-join + fts.rescore() + LIKE/fuzzy fallback.
- query(): FTS5 seed via build_match_query() -> 1-hop expansion -> dedupe -> rescore -> limit.
  Callers/callees counts are computed per symbol after collecting the result set.
- context(): single-symbol lookup; callers = edges where target_name = name,
  callees = edges where source_name = name.
  Phase 2: enriched with cluster_id, cluster_label, cluster_peers when clustering
  data is present. Fields are None/[] when the index has no cluster assignments.

Phase 3 changes (Slice 1):
- search() and query() build MATCH via fts.build_match_query() instead of raw text.
  This is the OR-join fix: one non-matching word can no longer zero the result set.
- Both pass FTS rows through fts.rescore() for multi-signal ranking.
- LIKE→fuzzy fallback: when FTS returns zero rows, fall back to LIKE %term% substring
  query; if still empty and term is ≥3 chars, a bounded Damerau-Levenshtein fuzzy
  match over distinct symbol names is attempted.
- The existing OperationalError propagation is preserved: genuinely malformed input
  still surfaces distinctly. After OR-join, most user text won't be malformed, but
  the propagation path must remain so callers can still map it to INVALID_QUERY.
"""

import json
import logging
import sqlite3
from typing import TypedDict

from seam.config import SEAM_FUZZY_MAX_CANDIDATES, SEAM_FUZZY_MAX_DIST
from seam.query import fts
from seam.query.clusters import cluster_peers as _cluster_peers
from seam.query.fts import extract_terms as _extract_terms

logger = logging.getLogger(__name__)


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
    cluster_id: int | None  # Phase 2: cluster this symbol belongs to (None if not clustered)
    cluster_label: str | None  # Phase 2: human-readable cluster label
    cluster_peers: list[str]  # Phase 2: other symbols in the same cluster (may be empty)
    # Phase 4 enrichment fields. All None for pre-v5 rows (no migration yet) or
    # unsupported languages — callers should treat None as "unknown", not as absent.
    signature: str | None
    decorators: list[str]       # [] when none extracted or for pre-v5 rows
    is_exported: bool | None
    visibility: str | None
    qualified_name: str | None


class SearchResult(TypedDict):
    symbol: str
    file: str
    line: int
    snippet: str
    score: float


# ── Shared Phase 4 enrichment decoder ────────────────────────────────────────


def decode_enrichment_fields(row: sqlite3.Row) -> tuple[list[str], bool | None]:
    """Decode the Phase 4 SQLite enrichment columns from a DB row.

    Returns (decorators, is_exported) ready for use in ContextResult or NeighborRef.

    WHY extracted: context() and pack._enrich_neighbors both need to decode the
    same two SQLite-encoded fields. Keeping the logic in one place ensures they
    always agree on null-contract semantics and avoids drift over time.

    Rules:
      decorators:  NULL     → []   (pre-v5 row or nothing extracted)
                   JSON TEXT → decoded list (corrupted JSON → [] gracefully)
      is_exported: NULL     → None  (pre-v5 row or extraction unavailable)
                   0        → False
                   1        → True
    """
    raw_dec = row["decorators"]
    raw_exp = row["is_exported"]

    # Decode decorators: stored as JSON TEXT; pre-v5 rows have NULL.
    if raw_dec is None:
        decorators: list[str] = []
    else:
        try:
            decorators = json.loads(raw_dec)
        except (json.JSONDecodeError, TypeError, ValueError):
            # Corrupted JSON — degrade gracefully, never crash the read path.
            decorators = []

    # Decode is_exported: SQLite has no native bool; stored as 0/1/NULL.
    if raw_exp is None:
        is_exported: bool | None = None
    else:
        is_exported = bool(raw_exp)

    return decorators, is_exported


# ── Damerau-Levenshtein edit distance (pure-Python, bounded) ─────────────────


def _bounded_edit_distance(a: str, b: str, max_dist: int) -> int:
    """Compute the Damerau-Levenshtein edit distance between a and b.

    Returns the true distance, or max_dist+1 if it exceeds max_dist.
    The early-exit bound keeps the inner loop cost manageable when scanning
    many candidates.

    WHY Damerau-Levenshtein (not plain Levenshtein):
        Transpositions (e.g. 'authenitcate' → 'authenticate') are the most
        common real-world typo pattern. DL handles them as cost=1 rather than
        cost=2, giving better recall for common typos.
    """
    la, lb = len(a), len(b)
    # Early bound: if lengths differ by more than max_dist, impossible to match
    if abs(la - lb) > max_dist:
        return max_dist + 1

    # DP matrix: (la+1) x (lb+1)
    # Row represents current character of a; column represents current char of b.
    prev_prev = list(range(lb + 1))
    prev = [0] * (lb + 1)
    curr = [0] * (lb + 1)

    for i in range(1, la + 1):
        curr[0] = i
        row_min = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,  # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
            # Transposition: swap adjacent chars
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                curr[j] = min(curr[j], prev_prev[j - 2] + cost)
            row_min = min(row_min, curr[j])

        # Early-exit: if the entire row is above max_dist, no path can succeed
        if row_min > max_dist:
            return max_dist + 1

        prev_prev, prev, curr = prev, curr, [0] * (lb + 1)

    return prev[lb]


# ── LIKE→fuzzy fallback ───────────────────────────────────────────────────────


def _escape_like(term: str) -> str:
    """Escape LIKE metacharacters in a term so they are treated as literals.

    SQLite LIKE has three special characters: % (any sequence), _ (any single char),
    and the escape char itself (here: backslash). Escaping prevents a query for
    'get_user' from matching 'getXuser' due to the _ wildcard.

    Must be paired with ESCAPE '\\' in the SQL clause.
    """
    # Order matters: escape backslash first so we do not double-escape
    term = term.replace("\\", "\\\\")
    term = term.replace("%", "\\%")
    term = term.replace("_", "\\_")
    return term


def _like_fallback(
    conn: sqlite3.Connection,
    term: str,
    limit: int,
) -> list[dict]:
    """LIKE %term% substring query — second tier fallback after FTS returns zero rows.

    Returns rows in the same dict shape as the FTS query (symbol, file, line,
    snippet="", score=0.0, cluster_id) so they can pass through rescore().

    WHY escape: SQLite LIKE treats '_' as a single-char wildcard and '%' as a
    multi-char wildcard. Without escaping, a search for 'get_user' matches
    'getXuser', 'get1user', etc. — over-broadening the fallback results.
    """
    escaped = _escape_like(term)
    # Phase 4: SELECT s.signature so rescore() Signal-6 can fire for LIKE fallback rows.
    sql = """
        SELECT
            s.name          AS symbol,
            f.path          AS file,
            s.start_line    AS line,
            s.cluster_id    AS cluster_id,
            s.signature     AS signature
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name LIKE ? ESCAPE '\\'
        LIMIT ?
    """
    rows = conn.execute(sql, (f"%{escaped}%", limit)).fetchall()
    return [
        {
            "symbol": row["symbol"],
            "file": row["file"],
            "line": row["line"],
            "snippet": "",
            "score": 0.0,
            "cluster_id": row["cluster_id"],
            # Phase 4: include signature for rescore Signal-6 (nullable — pre-v5 rows have NULL).
            "signature": row["signature"],
        }
        for row in rows
    ]


def _fuzzy_fallback(
    conn: sqlite3.Connection,
    term: str,
    max_dist: int,
    candidate_cap: int,
    limit: int,
) -> list[dict]:
    """Bounded Damerau-Levenshtein fuzzy match over distinct symbol names.

    Third-tier fallback. Reads at most candidate_cap distinct symbol names from
    the DB, computes edit distance against `term`, and returns those within max_dist.

    WHY bounded candidate cap: this is O(n * |term|) per call. On large indexes
    (thousands of symbols), scanning all names would be slow. The cap keeps the
    tail-case bounded; SEAM_FUZZY_MAX_CANDIDATES controls it via env var.

    WHY length-window pre-filter: only names whose length is within max_dist of the
    term length can possibly achieve edit-distance <= max_dist. Filtering in SQL
    before the Python DL loop avoids scanning irrelevant names (e.g. a 20-char name
    can never match a 3-char term at dist=1). This also makes the cap more meaningful:
    all names that CAN match are eligible, not just the first N in rowid order.

    WHY ORDER BY name: deterministic ordering means repeated calls with the same
    inputs return the same result set (reproducible for agents/tests).
    """
    term_lower = term.lower()
    term_len = len(term_lower)

    # Pre-filter by length window: |len(name) - len(term)| <= max_dist is a necessary
    # (not sufficient) condition for edit-distance <= max_dist. Filter in SQL so we
    # only pull names that can possibly match, then order deterministically.
    name_rows = conn.execute(
        """
        SELECT DISTINCT name FROM symbols
        WHERE length(name) BETWEEN ? AND ?
        ORDER BY name
        LIMIT ?
        """,
        (max(1, term_len - max_dist), term_len + max_dist, candidate_cap),
    ).fetchall()

    matches: list[str] = []
    for row in name_rows:
        name = row["name"]
        dist = _bounded_edit_distance(term_lower, name.lower(), max_dist)
        if dist <= max_dist:
            matches.append(name)

    if not matches:
        return []

    # Fetch full rows for all matched symbol names.
    # Phase 4: SELECT s.signature so rescore() Signal-6 can fire for fuzzy fallback rows.
    placeholders = ",".join("?" * len(matches))
    sql = f"""
        SELECT
            s.name          AS symbol,
            f.path          AS file,
            s.start_line    AS line,
            s.cluster_id    AS cluster_id,
            s.signature     AS signature
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name IN ({placeholders})
        LIMIT ?
    """
    rows = conn.execute(sql, matches + [limit]).fetchall()
    return [
        {
            "symbol": row["symbol"],
            "file": row["file"],
            "line": row["line"],
            "snippet": "",
            "score": 0.0,
            "cluster_id": row["cluster_id"],
            # Phase 4: include signature for rescore Signal-6 (nullable — pre-v5 rows have NULL).
            "signature": row["signature"],
        }
        for row in rows
    ]


# _extract_terms is imported from seam.query.fts rather than duplicated here because
# both the MATCH expression builder (build_match_query) and the rescore signal
# computation must tokenise identically. A local copy would silently drift.


# ── search ───────────────────────────────────────────────────────────────────


def search(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[SearchResult]:
    """Full-text search across symbol names and docstrings.

    Phase 3 (Slice 1) changes vs. the original:
      - Builds the MATCH expression via fts.build_match_query() (OR-join of prefix
        terms) instead of passing raw text. This prevents one non-matching word from
        zeroing the entire result set (the implicit-AND bug).
      - Passes FTS rows through fts.rescore() for multi-signal ranking.
      - LIKE→fuzzy fallback: when FTS returns zero rows:
          1. Falls back to LIKE %term% per term.
          2. If still empty and term ≥3 chars, tries bounded Damerau-Levenshtein
             fuzzy match over distinct symbol names (capped at SEAM_FUZZY_MAX_CANDIDATES).

    Result ordering: highest score first. The returned `score` is the NEGATED bm25
    value + rescore bonuses, so higher = more relevant. FTS rows carry meaningful
    BM25 scores; fallback rows start at 0.0 before rescore bonuses are applied.

    Raises sqlite3.OperationalError on genuinely malformed FTS5 syntax.
    NOTE: After OR-join, most user input won't be malformed. But the propagation
    path is preserved so callers (MCP handlers) can still map it to INVALID_QUERY.
    """
    terms = _extract_terms(text)

    # Step 1: try FTS5 with the safe OR-join expression
    match_expr = fts.build_match_query(text)
    fts_rows: list[dict] = []

    if match_expr:
        # Let OperationalError (genuinely malformed FTS5) propagate to caller.
        # With OR-join, this only fires on actual syntax errors, not multi-word queries.
        # Phase 4: SELECT s.signature so that fts.rescore() Signal-6 (signature boost)
        # can fire. Without s.signature in the row, rescore() always sees None and the
        # boost is permanently dead code. This column is nullable — pre-v5 rows have NULL.
        sql = """
            SELECT
                s.name          AS symbol,
                f.path          AS file,
                s.start_line    AS line,
                snippet(symbols_fts, 0, '<b>', '</b>', '...', 8) AS snippet,
                bm25(symbols_fts) AS score,
                s.cluster_id    AS cluster_id,
                s.signature     AS signature
            FROM symbols_fts
            JOIN symbols s ON s.id = symbols_fts.rowid
            JOIN files   f ON f.id = s.file_id
            WHERE symbols_fts MATCH ?
            ORDER BY score         -- raw bm25 ascending = most relevant first
            LIMIT ?
        """
        raw_rows = conn.execute(sql, (match_expr, limit)).fetchall()
        fts_rows = [
            {
                "symbol": row["symbol"],
                "file": row["file"],
                "line": row["line"],
                "snippet": row["snippet"] or "",
                # Flip bm25 sign: contract wants higher = better; raw bm25 is lower = better
                "score": -float(row["score"]),
                "cluster_id": row["cluster_id"],
                # Phase 4: include signature so rescore() Signal-6 can fire.
                "signature": row["signature"],
            }
            for row in raw_rows
        ]

    if fts_rows:
        # Rescore and return FTS results (the common happy path)
        rescored = fts.rescore(fts_rows, terms)
        return [
            SearchResult(
                symbol=r["symbol"],
                file=r["file"],
                line=r["line"],
                snippet=r.get("snippet", ""),
                score=r["score"],
            )
            for r in rescored
        ]

    # ── Step 2: LIKE fallback — FTS returned zero rows ────────────────────────
    # WHY: FTS5 requires tokens to be indexed. A query term that was never seen
    # (a typo like 'autenticate') gets zero FTS hits. LIKE %term% catches substrings.
    if not match_expr:
        # match_expr was empty sentinel — all tokens were stripped (operators/short).
        # Log at INFO to distinguish "query discarded" from "genuine no-match".
        logger.info(
            "search: match_expr empty (query discarded) — all tokens stripped from %r; "
            "returning []",
            text,
        )
        return []
    logger.debug("search: FTS returned zero rows for %r — trying LIKE fallback", text)

    like_rows: list[dict] = []
    for term in terms:
        found = _like_fallback(conn, term, limit)
        # Deduplicate by symbol name across multiple term searches
        seen = {r["symbol"] for r in like_rows}
        like_rows.extend(r for r in found if r["symbol"] not in seen)
        if len(like_rows) >= limit:
            break

    if like_rows:
        logger.debug("search: LIKE fallback found %d rows", len(like_rows))
        rescored = fts.rescore(like_rows, terms)
        return [
            SearchResult(
                symbol=r["symbol"],
                file=r["file"],
                line=r["line"],
                snippet=r.get("snippet", ""),
                score=r["score"],
            )
            for r in rescored[:limit]
        ]

    # ── Step 3: fuzzy fallback — LIKE also returned nothing ───────────────────
    # Only attempt for terms that are long enough for edit-distance to be meaningful.
    logger.debug("search: LIKE returned zero rows for %r — trying fuzzy fallback", text)

    fuzzy_rows: list[dict] = []
    for term in terms:
        if len(term) < 3:
            continue
        found = _fuzzy_fallback(
            conn,
            term,
            max_dist=SEAM_FUZZY_MAX_DIST,
            candidate_cap=SEAM_FUZZY_MAX_CANDIDATES,
            limit=limit,
        )
        seen = {r["symbol"] for r in fuzzy_rows}
        fuzzy_rows.extend(r for r in found if r["symbol"] not in seen)

    if fuzzy_rows:
        logger.debug("search: fuzzy fallback found %d rows", len(fuzzy_rows))
        rescored = fts.rescore(fuzzy_rows, terms)
        return [
            SearchResult(
                symbol=r["symbol"],
                file=r["file"],
                line=r["line"],
                snippet=r.get("snippet", ""),
                score=r["score"],
            )
            for r in rescored[:limit]
        ]

    # All three tiers exhausted — genuine miss (not a query-discard).
    logger.info(
        "search: all tiers ran (FTS + LIKE + fuzzy), genuine miss for %r — returning []",
        text,
    )
    return []


# ── query ────────────────────────────────────────────────────────────────────


def query(conn: sqlite3.Connection, concept: str, limit: int = 10) -> list[QueryResult]:
    """Find symbols related to a concept (FTS5 seed + 1-hop graph expansion).

    Algorithm:
      1. Build MATCH expression via fts.build_match_query() (OR-join fix).
      2. FTS5 MATCH to get seed symbols (with BM25 score).
      3. Rescore seeds via fts.rescore().
      4. For each seed symbol, collect 1-hop neighbors via edges
         (both direct callees and callers — anything connected).
      5. Deduplicate; seed symbols keep their (rescored) score, neighbors get 0.
      6. For each symbol in the result set, compute callers_count + callees_count.
      7. Sort seeds-first, then by score descending, apply limit.

    Raises sqlite3.OperationalError on malformed FTS5 syntax (the caller maps
    this to INVALID_QUERY, mirroring search() — do NOT swallow it here, or a
    malformed concept looks identical to "no matches").
    """
    terms = _extract_terms(concept)

    # Step 1+2: FTS5 seed query with OR-join
    match_expr = fts.build_match_query(concept)

    seed_map: dict[str, tuple[str, int, float]] = {}

    if match_expr:
        # Phase 4: include s.signature so rescore() Signal-6 (signature boost) fires.
        seed_sql = """
            SELECT
                s.name          AS name,
                f.path          AS file,
                s.start_line    AS line,
                bm25(symbols_fts) AS score,
                s.cluster_id    AS cluster_id,
                s.signature     AS signature
            FROM symbols_fts
            JOIN symbols s ON s.id = symbols_fts.rowid
            JOIN files   f ON f.id = s.file_id
            WHERE symbols_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """
        # Let OperationalError (malformed FTS5) propagate — same contract as search():
        # caller maps it to INVALID_QUERY so it is distinct from "no matches found".
        seed_rows_raw = conn.execute(seed_sql, (match_expr, limit)).fetchall()

        if seed_rows_raw:
            # Rescore to apply name/path/cluster/signature signals
            seed_dicts = [
                {
                    "symbol": row["name"],
                    "file": row["file"],
                    "line": row["line"],
                    "snippet": "",
                    "score": -float(
                        row["score"]
                    ),  # negate: contract wants higher=better; raw bm25 is negative (lower=better)
                    "cluster_id": row["cluster_id"],
                    # Phase 4: include signature for rescore Signal-6.
                    "signature": row["signature"],
                }
                for row in seed_rows_raw
            ]
            rescored_seeds = fts.rescore(seed_dicts, terms)

            # Build seed map: name -> (file, line, score)
            for row in rescored_seeds:
                seed_map[row["symbol"]] = (row["file"], row["line"], row["score"])

    if not seed_map:
        if not match_expr:
            # Empty sentinel: query was discarded (all tokens stripped).
            logger.info(
                "query: match_expr empty (query discarded) — all tokens stripped from %r; "
                "returning []",
                concept,
            )
        else:
            # FTS ran but found nothing — genuine miss.
            logger.info(
                "query: FTS5 ran, genuine miss for %r — returning []",
                concept,
            )
        return []

    # Step 3: 1-hop expansion — collect neighbors of seed symbols
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

    # Step 4: Combine and deduplicate
    combined: dict[str, tuple[str, int, float]] = {**neighbor_map, **seed_map}

    # Step 5: Compute callers/callees counts for each symbol in the result set
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

    # Step 6: seeds rank above neighbors; within each group, higher score first.
    seed_name_set = set(seed_map)
    result.sort(key=lambda r: (r["symbol"] in seed_name_set, r["score"]), reverse=True)
    return result[:limit]


# ── context ──────────────────────────────────────────────────────────────────


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
    # separate queries and removes the now-dead double-None check. Pre-v5 rows return
    # NULL for the Phase 4 enrichment columns — the read path handles that below.
    row = conn.execute(
        """
        SELECT s.name, f.path AS file, s.start_line, s.end_line, s.kind, s.docstring,
               COUNT(*) OVER () AS dup_count,
               s.signature, s.decorators, s.is_exported, s.visibility, s.qualified_name
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

    # Phase 2: Enrich with cluster information.
    # cluster_peers() handles pre-v4 indexes gracefully (returns None when unavailable).
    cluster_info = _cluster_peers(conn, symbol_name)
    if cluster_info is not None:
        c_id, c_label, c_peers = cluster_info
    else:
        c_id, c_label, c_peers = None, None, []

    # Decode Phase 4 enrichment columns via shared helper — single source of truth
    # for the 0/1/NULL→bool and JSON TEXT→list[str] decode that pack.py also needs.
    decoded_decorators, is_exported = decode_enrichment_fields(row)

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
        cluster_id=c_id,
        cluster_label=c_label,
        cluster_peers=c_peers,
        # Phase 4 enrichment fields
        signature=row["signature"],
        decorators=decoded_decorators,
        is_exported=is_exported,
        visibility=row["visibility"],
        qualified_name=row["qualified_name"],
    )
