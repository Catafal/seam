"""Whole-index confidence resolver — single source of truth for the
EXTRACTED / AMBIGUOUS / INFERRED rule.

Resolution rule (scope: whole index, evaluated at read time):
  target name appears exactly once in symbols table → EXTRACTED
  target name appears more than once             → AMBIGUOUS
  target name does not appear at all             → INFERRED (external/unindexed)

Design rationale:
  Confidence is a property of *global* state (the full index), not a per-file
  property.  Resolving at read time means it is always fresh after any
  incremental watcher re-index — no write-amplification, no staleness.
  The stored edges.confidence column is a same-file lower-bound hint kept for
  debugging; read-time resolution here is authoritative and overrides it.

Import rules (no circular deps):
  This module imports ONLY stdlib: sqlite3, typing, logging.
  It must NOT import traversal.py, flows.py, or any other seam analysis module.
  traversal.py and flows.py import their confidence constants FROM here.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# ── Canonical confidence constants ────────────────────────────────────────────
# These are the three possible values for the confidence field on edges and hops.
# All other seam modules (traversal, flows) import them from here.

CONFIDENCE_EXTRACTED = "EXTRACTED"   # target name is unique in the full index
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"   # target name matches >1 indexed symbol
CONFIDENCE_INFERRED = "INFERRED"     # target not indexed (external, stdlib, dynamic)


# ── DB helper ─────────────────────────────────────────────────────────────────


def load_name_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Load a name → count map from the symbols table in a single GROUP BY query.

    This is the only DB call this module makes.  Called once per query in
    traversal.walk / flows.trace / flows.callers / flows.callees so that
    confidence is resolved against the full index without per-edge round-trips.

    Args:
        conn: Open SQLite connection (read-only semantics; no writes).

    Returns:
        dict mapping every symbol name to its occurrence count across all files.
        An empty dict when the symbols table has no rows.
    """
    rows = conn.execute("SELECT name, COUNT(*) AS cnt FROM symbols GROUP BY name").fetchall()
    # Positional access works under any row_factory (and db.connect() sets sqlite3.Row).
    result: dict[str, int] = {row[0]: row[1] for row in rows}
    if not result:
        # Empty map → EVERY edge resolves to INFERRED (the exact silent degradation
        # issue #9 fixed). Surface it loudly: an empty symbols table almost always
        # means the index was never built or is mid-rebuild, not that the code is
        # genuinely all-external. Without this, the regression returns invisibly.
        logger.warning(
            "load_name_counts: symbols table is empty — all edge confidence will "
            "resolve to INFERRED. Run 'seam init' to (re)build the index."
        )
    else:
        logger.debug("load_name_counts: %d distinct symbol names loaded", len(result))
    return result


# ── Pure resolver ──────────────────────────────────────────────────────────────


def resolve(target_name: str, name_counts: dict[str, int]) -> str:
    """Resolve confidence for a single edge target against the whole-index name map.

    Pure function — no I/O, no side effects.

    Args:
        target_name:  The edge target (callee / importee) name to resolve.
        name_counts:  Mapping produced by load_name_counts(conn).

    Returns:
        CONFIDENCE_EXTRACTED  if the name appears exactly once in the index.
        CONFIDENCE_AMBIGUOUS  if the name appears more than once.
        CONFIDENCE_INFERRED   if the name is absent (count == 0 or missing key).
    """
    count = name_counts.get(target_name, 0)
    if count == 1:
        return CONFIDENCE_EXTRACTED
    if count > 1:
        return CONFIDENCE_AMBIGUOUS
    # count == 0: name not in index — external library, stdlib, or dynamic call.
    return CONFIDENCE_INFERRED
