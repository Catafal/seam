"""Query engine — read path for all MCP tool queries.

All functions take an open sqlite3.Connection. No connection management here.
Returns typed dicts matching the MCP tool output spec in docs/api-contracts/mcp-tools.yaml.

context(): Tier A Slice 2 — resolves bare names to all qualified definitions and merges
callers/callees. See seam/query/names.py for the qualified<->bare bridging details.
search() / query(): FTS5 BM25 + OR-join + rescore + LIKE/fuzzy fallback (Phase 3).
Hybrid semantic (T6): opt-in RRF merge when SEAM_SEMANTIC=on and embeddings present.
"""

import json
import logging
import sqlite3
from typing import TypedDict

import seam.config as config
from seam.config import SEAM_FUZZY_MAX_CANDIDATES, SEAM_FUZZY_MAX_DIST
from seam.query import fts
from seam.query.clusters import cluster_peers as _cluster_peers
from seam.query.fts import extract_terms as _extract_terms
from seam.query.names import edge_match_names as _edge_match_names
from seam.query.names import resolve_query_to_defs as _resolve_query_to_defs
from seam.query.semantic import rrf_merge, semantic_candidates

logger = logging.getLogger(__name__)

# Module-level flag: emit the "SEAM_SEMANTIC=on but no embeddings" warning at most once
# per process. Without this, every search/query call would spam the same warning.
_hybrid_warned: bool = False


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


# ── Hybrid search helpers ─────────────────────────────────────────────────────


def _hydrate_symbol_rows(
    conn: sqlite3.Connection, symbol_ids: list[int]
) -> dict[int, dict]:
    """Fetch symbol + file data for a list of symbol IDs.

    Returns a dict mapping symbol_id → row dict with keys:
      symbol, file, line, cluster_id, signature.

    Used by the hybrid path to hydrate RRF-merged id lists into full rows.
    WHY: After rrf_merge we have a ranked list of symbol_ids. To produce
    SearchResult objects we need name, file, line etc. One query fetches all
    needed columns at once rather than N per-id lookups.
    """
    if not symbol_ids:
        return {}
    placeholders = ",".join("?" * len(symbol_ids))
    sql = f"""
        SELECT
            s.id            AS id,
            s.name          AS symbol,
            f.path          AS file,
            s.start_line    AS line,
            s.cluster_id    AS cluster_id,
            s.signature     AS signature
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.id IN ({placeholders})
    """
    rows = conn.execute(sql, symbol_ids).fetchall()
    return {row["id"]: dict(row) for row in rows}


def _is_hybrid_enabled(conn: sqlite3.Connection) -> bool:
    """Return True when the hybrid path should be used for this query.

    Conditions (all must hold):
      1. SEAM_SEMANTIC config is 'on'.
      2. The embeddings table has at least one row for the configured model.

    WHY check at query time vs. at startup: the embeddings table can be
    populated between process starts without restarting the server. Checking
    per-query ensures newly indexed embeddings are immediately available without
    requiring a server restart. The check is a single COUNT(*) — negligible cost.
    """
    if config.SEAM_SEMANTIC != "on":
        return False
    count = conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE model = ?",
        (config.SEAM_EMBED_MODEL,),
    ).fetchone()[0]
    if count == 0:
        # SEAM_SEMANTIC=on but no embeddings — warn once per process.
        global _hybrid_warned  # noqa: PLW0603
        if not _hybrid_warned:
            _hybrid_warned = True
            logger.warning(
                "_is_hybrid_enabled: SEAM_SEMANTIC=on but no embeddings found for "
                "model=%r. Run 'seam init --semantic' to build the embedding index.",
                config.SEAM_EMBED_MODEL,
            )
        return False
    return True


# ── Hybrid search results helper ─────────────────────────────────────────────


def _hybrid_search_results(
    conn: sqlite3.Connection,
    text: str,
    fts_rows: list[dict],
    fts_symbol_ids: list[int],
    limit: int,
) -> list[SearchResult] | None:
    """Compute hybrid (FTS + semantic) search results via RRF.

    Returns a list of SearchResult when semantic adds new candidates beyond FTS,
    or None to signal "fall through to the FTS-only path".

    WHY extracted from search(): to keep search() under 200 lines (DRIFT-2).
    WHY returns None: a None return lets search() stay in the normal FTS path
    when semantic adds nothing new — semantically clear vs. returning [].

    Snippet contract (STOP-2):
        FTS rows carry real BM25 snippets. When a merged result was found by FTS,
        we preserve its snippet. Semantic-only results get snippet="" (no FTS row).
    """
    sem_candidates = semantic_candidates(
        conn,
        text,
        model=config.SEAM_EMBED_MODEL,
        limit=config.SEAM_SEMANTIC_LIMIT,
    )
    sem_ids = [sid for sid, _score in sem_candidates]

    # Check if semantic added any new candidates beyond FTS.
    fts_id_set = set(fts_symbol_ids)
    new_sem_ids = [sid for sid in sem_ids if sid not in fts_id_set]

    if not new_sem_ids:
        return None  # No new recall — fall through to FTS rescore path.

    # Build snippet + score lookup for FTS rows (preserve real BM25 snippets).
    fts_id_to_snippet: dict[int, str] = {r["id"]: r.get("snippet", "") for r in fts_rows}

    # RRF merge: combine FTS id list (BM25-ordered) and semantic id list (cosine-ordered).
    merged_ids = rrf_merge(fts_symbol_ids, sem_ids)

    # Hydrate the merged id list into full rows (batch fetch — one SQL query).
    id_to_row = _hydrate_symbol_rows(conn, merged_ids)

    # Build SearchResult list in merged-rank order. RRF score = 1/(k+rank).
    k = config.SEAM_RRF_K
    results: list[SearchResult] = []
    for rank, sym_id in enumerate(merged_ids[:limit], start=1):
        if sym_id not in id_to_row:
            continue
        row = id_to_row[sym_id]
        rrf_score = 1.0 / (k + rank)
        # Use the real FTS snippet for ids that came from FTS; "" for semantic-only.
        snippet = fts_id_to_snippet.get(sym_id, "")
        results.append(
            SearchResult(
                symbol=row["symbol"],
                file=row["file"],
                line=row["line"],
                snippet=snippet,
                score=rrf_score,
            )
        )

    if results:
        logger.debug(
            "search: hybrid path returned %d results (%d FTS + %d new semantic) for %r",
            len(results),
            len(fts_symbol_ids),
            len(new_sem_ids),
            text,
        )

    return results if results else None


# ── search ───────────────────────────────────────────────────────────────────


def search(
    conn: sqlite3.Connection,
    text: str,
    limit: int = 20,
    *,
    semantic: bool = True,
) -> list[SearchResult]:
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

    Semantic search (T6):
      - When SEAM_SEMANTIC=on AND embeddings exist for the configured model:
          1. FTS5 candidates (id list, ranked by BM25) are combined with semantic
             candidates (id list, ranked by cosine) via rrf_merge.
          2. The merged id list is hydrated into SearchResult rows.
          3. Score in SearchResult is the RRF rank (1/(k+rank), higher=better).
      - When semantic is off or unavailable: existing pure-FTS5 path is used.
        Behavior is byte-identical to pre-T6 in this case.

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

    # Also collect FTS symbol IDs for the hybrid path (if needed)
    fts_symbol_ids: list[int] = []

    if match_expr:
        # Let OperationalError (genuinely malformed FTS5) propagate to caller.
        # With OR-join, this only fires on actual syntax errors, not multi-word queries.
        # Phase 4: SELECT s.signature so that fts.rescore() Signal-6 (signature boost)
        # can fire. Without s.signature in the row, rescore() always sees None and the
        # boost is permanently dead code. This column is nullable — pre-v5 rows have NULL.
        sql = """
            SELECT
                s.id            AS id,
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
                "id": row["id"],
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
        # Collect FTS symbol IDs (BM25-order = best first) for RRF merge
        fts_symbol_ids = [r["id"] for r in fts_rows]

    # ── Hybrid path: merge FTS and semantic candidates ────────────────────────
    # When SEAM_SEMANTIC=on AND embeddings exist AND semantic=True (caller opt-in):
    # combine both recall sources. Semantic ONLY ADDS recall — FTS hits never dropped.
    # `semantic=False` lets callers (e.g. CLI --no-semantic) bypass hybrid without
    # mutating global config (DRIFT-1 fix).
    if semantic and _is_hybrid_enabled(conn):
        hybrid_results = _hybrid_search_results(
            conn, text, fts_rows, fts_symbol_ids, limit
        )
        if hybrid_results is not None:
            return hybrid_results

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


def query(
    conn: sqlite3.Connection,
    concept: str,
    limit: int = 10,
    *,
    semantic: bool = True,
) -> list[QueryResult]:
    """Find symbols related to a concept (FTS5 seed + 1-hop graph expansion).

    Algorithm:
      1. Build MATCH expression via fts.build_match_query() (OR-join fix).
      2. FTS5 MATCH to get seed symbols (with BM25 score).
      3. Rescore seeds via fts.rescore().
      3b. [Semantic] When SEAM_SEMANTIC=on and embeddings exist: semantic candidates are
          injected into seed_map with score=0.5 (below FTS rescored seeds, above neighbors).
          WHY score=0.5: FTS rescored seeds have scores in roughly [0.5, 5.0]; graph
          neighbors have score=0.0. Placing semantic seeds at 0.5 makes them rank below
          confident FTS matches but above pure-graph neighbors — semantically relevant but
          not as strong as keyword-matched seeds.
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

    # ── Hybrid augmentation for query(): inject semantic seeds ───────────────
    # When SEAM_SEMANTIC=on AND embeddings exist, semantic candidates are fetched
    # and their symbol names are added to seed_map as additional seeds (score=0.5
    # — below FTS rescored seeds but above 1-hop graph neighbors at score=0.0).
    # This lets semantic-only symbols appear as peers of FTS seeds, going through
    # the same 1-hop expansion and callers/callees enrichment path.
    if semantic and _is_hybrid_enabled(conn):
        sem_candidates = semantic_candidates(
            conn,
            concept,
            model=config.SEAM_EMBED_MODEL,
            limit=config.SEAM_SEMANTIC_LIMIT,
        )
        if sem_candidates:
            sem_ids = [sid for sid, _s in sem_candidates]
            id_to_row = _hydrate_symbol_rows(conn, sem_ids)
            for sym_id, _sem_score in sem_candidates:
                if sym_id not in id_to_row:
                    continue
                row = id_to_row[sym_id]
                name = row["symbol"]
                # Add to seed_map only if not already present (FTS seeds take priority)
                if name not in seed_map:
                    # Score 0.5: above graph neighbors (0.0), below FTS rescored seeds.
                    seed_map[name] = (row["file"], row["line"], 0.5)

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

    Tier A Slice 2: resolves bare names to all qualified definitions and merges
    callers/callees across them. Sets ambiguous=True when resolution spans >1 definition.
    Exact single-def match stays byte-stable. Returns None when nothing is found.
    Per-edge confidence is preserved (union never invents confidence).
    """
    # resolve_query_to_defs handles: exact match, bare-name suffix scan, qualified-not-found.
    def_rows = _resolve_query_to_defs(conn, symbol_name)
    if not def_rows:
        return None

    # is_exact_match: the returned defs have the same name as the query (not a bare resolution).
    is_exact_match = def_rows[0]["name"] == symbol_name

    # Fast path: single exact-match def → byte-stable, dup_count drives ambiguous.
    if is_exact_match and len(def_rows) == 1:
        dup_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE name = ?", (symbol_name,)
        ).fetchone()[0]
        return _build_context_result(conn, def_rows[0], dup_count=dup_count)

    # Multi-def path: bare-name homonym or exact-name collision → merge and mark ambiguous.
    return _build_merged_context_result(conn, def_rows)


def context_at(
    conn: sqlite3.Connection, file_path: str, start_line: int
) -> ContextResult | None:
    """P6c: resolve the EXACT symbol at (file_path, start_line) — bypasses name lookup.

    Returns None when no symbol is at that exact location (unknown/stale UID).
    """
    row = conn.execute(
        """
        SELECT s.name, f.path AS file, s.start_line, s.end_line, s.kind, s.docstring,
               s.signature, s.decorators, s.is_exported, s.visibility, s.qualified_name
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.path = ? AND s.start_line = ?
        ORDER BY s.id
        LIMIT 1
        """,
        (file_path, start_line),
    ).fetchone()

    if row is None:
        return None

    dup_count = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE name = ?", (row["name"],)
    ).fetchone()[0]

    return _build_context_result(conn, row, dup_count=dup_count)


def _collect_edges_for_names(
    conn: sqlite3.Connection,
    match_names: list[str],
) -> tuple[set[str], set[str]]:
    """Return (callers_set, callees_set) for match_names via DISTINCT edge lookups.

    Used by both single-def (_build_context_result) and multi-def aggregation paths.
    Per-edge confidence from the DB is preserved — the union never invents confidence.
    """
    if not match_names:
        return set(), set()
    ph = ",".join("?" * len(match_names))
    callers = {
        r["source_name"]
        for r in conn.execute(
            f"SELECT DISTINCT source_name FROM edges WHERE target_name IN ({ph})",
            match_names,
        ).fetchall()
    }
    callees = {
        r["target_name"]
        for r in conn.execute(
            f"SELECT DISTINCT target_name FROM edges WHERE source_name IN ({ph})",
            match_names,
        ).fetchall()
    }
    return callers, callees


def _build_merged_context_result(
    conn: sqlite3.Connection,
    def_rows: list[sqlite3.Row],
) -> ContextResult:
    """Merge callers/callees across multiple defs (Slice 2 multi-def path).

    Primary def (lowest id) supplies location/kind/enrichment. ambiguous=True when >1
    def or exact-name collision; False for unique bare-name resolution (1 qualified def).
    """
    primary = def_rows[0]
    all_callers: set[str] = set()
    all_callees: set[str] = set()
    for row in def_rows:
        match_names = _edge_match_names(conn, row["name"])
        callers, callees = _collect_edges_for_names(conn, match_names)
        all_callers |= callers
        all_callees |= callees

    cluster_info = _cluster_peers(conn, primary["name"])
    c_id, c_label, c_peers = cluster_info if cluster_info is not None else (None, None, [])
    decoded_decorators, is_exported = decode_enrichment_fields(primary)

    return ContextResult(
        symbol=primary["name"],
        file=primary["file"],
        line=primary["start_line"],
        end_line=primary["end_line"],
        kind=primary["kind"],
        docstring=primary["docstring"],
        callers=sorted(all_callers),
        callees=sorted(all_callees),
        ambiguous=len(def_rows) > 1,
        cluster_id=c_id,
        cluster_label=c_label,
        cluster_peers=c_peers,
        signature=primary["signature"],
        decorators=decoded_decorators,
        is_exported=is_exported,
        visibility=primary["visibility"],
        qualified_name=primary["qualified_name"],
    )


def _build_context_result(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    dup_count: int | None = None,
) -> ContextResult:
    """Single symbol row → ContextResult. Used by context_at and the single-def fast path.

    dup_count: explicit collision count (or row["dup_count"] window value if None).
    """
    symbol_name = row["name"]
    # Slice 1 bridging: [qualified, bare] so call edges with bare target are found.
    callers, callees = _collect_edges_for_names(conn, _edge_match_names(conn, symbol_name))
    effective_dup = dup_count if dup_count is not None else row["dup_count"]
    cluster_info = _cluster_peers(conn, symbol_name)
    c_id, c_label, c_peers = cluster_info if cluster_info is not None else (None, None, [])
    decoded_decorators, is_exported = decode_enrichment_fields(row)

    return ContextResult(
        symbol=row["name"],
        file=row["file"],
        line=row["start_line"],
        end_line=row["end_line"],
        kind=row["kind"],
        docstring=row["docstring"],
        callers=sorted(callers),
        callees=sorted(callees),
        ambiguous=effective_dup > 1,
        cluster_id=c_id,
        cluster_label=c_label,
        cluster_peers=c_peers,
        signature=row["signature"],
        decorators=decoded_decorators,
        is_exported=is_exported,
        visibility=row["visibility"],
        qualified_name=row["qualified_name"],
    )
