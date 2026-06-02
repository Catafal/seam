"""FTS5 query construction and multi-signal rescoring (Phase 3, Slice 1).

Pure leaf module — no imports from query/server/cli layers, no DB access.
Only stdlib + typing. Must not open a database connection.

WHY this module exists:
    Before Phase 3, engine.py passed the raw user text directly into the FTS5
    MATCH clause. FTS5 implicitly ANDs space-separated terms, so a query like
    "parse issues board" returned ZERO hits if 'board' was not indexed — even
    though 'parse' matched many symbols. This module fixes that by building an
    OR-joined prefix-quoted MATCH expression, and adds multi-signal rescoring
    so precision is recovered without requiring all terms to match.

build_match_query(text) -> str
    Tokenise, strip FTS5 operators/specials, wrap each surviving term as "term"*,
    join with OR. Returns a safe sentinel (empty string) on fully-stripped input.

rescore(rows, terms) -> list
    Multi-signal rerank over FTS result rows. Signals (per CodeGraph research §4.2):
      1. Exact name match:    +80
      2. Prefix name match:   +40 (name starts with any query term)
      3. Path relevance:      +10 per term found in file path (directory/filename)
      4. Test-file dampening: -30 when query has no test-signal and file is a test file
      5. Cluster boost:       +20 when row shares the dominant seed's cluster_id

    Score is an UNBOUNDED relevance heuristic — document as "relevance", never as %.
"""

import logging
import re
from typing import Any

from seam.analysis.testpaths import is_test_file

logger = logging.getLogger(__name__)

# ── FTS5 special characters and operators to strip ────────────────────────────

# Characters that are special in FTS5 MATCH syntax.
# We strip these so user input cannot inject MATCH operators or crash the query.
_FTS5_SPECIAL_CHARS: re.Pattern = re.compile(r'["\'\(\)\*\+\-\^\:\.]')

# FTS5 boolean operators and NEAR function — must not survive as query terms.
# Checked case-insensitively against individual tokens after splitting.
_FTS5_OPERATORS: frozenset[str] = frozenset({"AND", "OR", "NOT", "NEAR"})

# Minimum token length to include in the MATCH expression.
# Very short tokens (1-2 chars) produce too many FTS5 matches and hurt precision.
_MIN_TERM_LEN: int = 2

# Sentinel returned when the input strips to nothing — yields zero FTS5 rows safely.
# An empty string is passed through as-is; FTS5 handles it without error.
_EMPTY_SENTINEL: str = ""


# ── Rescoring constants ───────────────────────────────────────────────────────
# These mirror CodeGraph's query-utils.ts weights (§4.2) adapted for Seam's
# data model (cluster_id vs. no cluster concept in CodeGraph).

_BONUS_EXACT_NAME: float = 80.0  # name IS the query term (highest signal)
_BONUS_PREFIX_NAME: float = 40.0  # name STARTS WITH a query term
_BONUS_PATH_PER_TERM: float = 10.0  # query term appears in the file path
_PENALTY_TEST_FILE: float = -30.0  # test-file dampening on non-test queries
_BONUS_CLUSTER_PEER: float = 20.0  # shares cluster with the strongest-score row

# A query is considered "test-aware" if any term is one of these words,
# in which case we suppress the test-file dampening.
_TEST_QUERY_SIGNALS: frozenset[str] = frozenset({"test", "tests", "spec", "fixture"})


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


def build_match_query(text: str) -> str:
    """Build a safe FTS5 MATCH expression from raw user input.

    Algorithm:
      1. Strip FTS5 special characters (quotes, parens, *, ^, etc.).
      2. Split on whitespace into candidate tokens.
      3. Discard FTS5 boolean operators (AND, OR, NOT, NEAR — case-insensitive).
      4. Discard tokens shorter than _MIN_TERM_LEN characters.
      5. Wrap each surviving token as a quoted prefix: "token"*
      6. Join with " OR " so one non-matching word cannot zero the result.

    The OR-join is the core fix for the implicit-AND zero-hit bug:
    "parse issues board" → "parse"* OR "issues"* OR "board"*
    → returns all symbols matching ANY of the three terms.

    Returns:
        A safe FTS5 MATCH expression string, or _EMPTY_SENTINEL ("") when all
        tokens are stripped. The empty string triggers zero FTS5 rows (SQLite
        handles it without raising OperationalError).
    """
    if not text or not text.strip():
        return _EMPTY_SENTINEL

    # Step 1: strip FTS5 special characters
    cleaned = _FTS5_SPECIAL_CHARS.sub(" ", text)

    # Step 2: split into tokens
    tokens = cleaned.split()

    # Steps 3+4: filter operators and short tokens
    surviving: list[str] = []
    for token in tokens:
        # Case-insensitive operator check
        if token.upper() in _FTS5_OPERATORS:
            logger.debug("fts: stripped FTS5 operator %r from query", token)
            continue
        if len(token) < _MIN_TERM_LEN:
            logger.debug("fts: stripped short token %r from query", token)
            continue
        surviving.append(token)

    if not surviving:
        logger.debug("fts: all tokens stripped from query %r — returning sentinel", text)
        return _EMPTY_SENTINEL

    # Step 5+6: build quoted-prefix OR expression
    # Each term wrapped as "term"* so FTS5 matches any token starting with 'term'.
    # This gives prefix matching without requiring exact full-word matches.
    terms = [f'"{t}"*' for t in surviving]
    match_expr = " OR ".join(terms)

    logger.debug("fts: built MATCH expression: %r", match_expr)
    return match_expr


def rescore(rows: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    """Multi-signal rerank over FTS result rows.

    Applies five signals on top of the FTS BM25 score:
      1. Exact name match bonus (+80): symbol name equals a query term (case-insensitive)
      2. Prefix name match bonus (+40): symbol name starts with a query term
      3. Path relevance (+10/term): query term appears in the file path
      4. Test-file dampening (-30): if query has no test-signal and file is a test file
      5. Cluster boost (+20): row shares cluster_id with the row that has the highest
         raw FTS score (the "strongest seed").

    The final score is the base FTS score + all applicable bonuses/penalties.
    It is an UNBOUNDED relevance heuristic — callers MUST NOT render it as a %.

    Args:
        rows:  List of result dicts. Each row must have keys: symbol (str), file (str),
               score (float). Optional key: cluster_id (int | None).
        terms: The query terms as plain strings (already stripped of FTS5 operators).
               Used for name/path relevance checks.

    Returns:
        The same rows with updated 'score' fields, sorted highest-first.
        Returns a new list; the input is not mutated.
    """
    if not rows:
        return []

    # Normalise terms to lowercase for case-insensitive comparison
    lower_terms = [t.lower() for t in terms if t]

    # Determine whether the query itself has test-awareness
    # (suppresses dampening so test-related queries surface test files normally)
    is_test_query = bool(_TEST_QUERY_SIGNALS & frozenset(lower_terms))

    # Signal 5 (cluster boost): identify the dominant cluster from the highest-scoring row.
    # The first row has the highest raw FTS score (FTS returns best-first).
    dominant_cluster_id: int | None = None
    if rows:
        first_cluster = rows[0].get("cluster_id")
        if first_cluster is not None:
            dominant_cluster_id = int(first_cluster)

    # Apply signals to each row (work on copies to avoid mutating input)
    scored: list[dict[str, Any]] = []
    for row in rows:
        # Shallow copy: we only replace 'score', rest stays identical
        new_row = dict(row)
        base_score = float(row.get("score", 0.0))
        bonus = 0.0

        name_lower = str(row.get("symbol", "")).lower()
        file_lower = str(row.get("file", "")).lower()

        if lower_terms:
            # Signal 1: exact name match — name IS one of the query terms
            if name_lower in lower_terms:
                bonus += _BONUS_EXACT_NAME
                logger.debug(
                    "fts.rescore: exact-name bonus +%.0f for %r", _BONUS_EXACT_NAME, row["symbol"]
                )

            # Signal 2: prefix name match — name starts with any query term
            elif any(name_lower.startswith(t) for t in lower_terms):
                bonus += _BONUS_PREFIX_NAME
                logger.debug(
                    "fts.rescore: prefix-name bonus +%.0f for %r", _BONUS_PREFIX_NAME, row["symbol"]
                )

            # Signal 3: path relevance — query term appears in the file path
            path_bonus = sum(_BONUS_PATH_PER_TERM for t in lower_terms if t in file_lower)
            bonus += path_bonus

        # Signal 4: test-file dampening — only on non-test queries
        if not is_test_query and is_test_file(row.get("file")):
            bonus += _PENALTY_TEST_FILE
            logger.debug(
                "fts.rescore: test-file penalty %.0f for %r", _PENALTY_TEST_FILE, row["symbol"]
            )

        # Signal 5: cluster boost — shares cluster with the strongest seed
        row_cluster = row.get("cluster_id")
        if (
            dominant_cluster_id is not None
            and row_cluster is not None
            and int(row_cluster) == dominant_cluster_id
        ):
            bonus += _BONUS_CLUSTER_PEER
            logger.debug(
                "fts.rescore: cluster boost +%.0f for %r", _BONUS_CLUSTER_PEER, row["symbol"]
            )

        new_row["score"] = base_score + bonus
        scored.append(new_row)

    # Sort by final score descending (higher = more relevant per API contract)
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored
