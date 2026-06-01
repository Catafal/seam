"""Graph traversal engine — shared core for impact, trace, and detect_changes.

Contract
--------
``walk(conn, seeds, direction, max_depth) -> list[Reached]``

    Walk the edges table starting from ``seeds``, returning every reachable
    symbol with its minimum distance from any seed and the aggregated path
    confidence at that distance.

Direction semantics:
    upstream   — who depends on the seed: follow edges where target_name == seed
                 back to source_name (callers/importers).
    downstream — what the seed depends on: follow edges where source_name == seed
                 forward to target_name.

Reached TypedDict:
    name        — symbol name (string, from edges table)
    distance    — hops from any seed (1-based; seeds themselves are NOT in output)
    confidence  — aggregated path confidence at this distance (see path-confidence rule)

Path-confidence rule (from PRD):
    The confidence of a path is its WEAKEST hop.
    Ordering (weakest first): AMBIGUOUS < INFERRED < EXTRACTED.
    When multiple paths reach the same symbol at the same distance, we report
    the STRONGEST confidence among them (best available path).

Confidence rank mapping:
    AMBIGUOUS = 0 (weakest)
    INFERRED  = 1
    EXTRACTED = 2 (strongest)

Cycle safety:
    We use Python-side BFS with an explicit ``visited`` set.
    A pure recursive CTE with UNION would theoretically terminate,
    but is error-prone to bound precisely across SQLite versions when
    self-edges and multi-hop cycles coexist. Python BFS is trivially
    cycle-safe and easy to test. Max round-trips = max_depth (typically 3–10).

Self-edges:
    If source_name == target_name, the edge is skipped to prevent a symbol
    from being "reached" from itself in 1 hop.

Module imports:
    Only sqlite3, typing, and logging (stdlib). No server/cli/query imports.
"""

import logging
import sqlite3
from typing import TypedDict

logger = logging.getLogger(__name__)

# ── Confidence constants ───────────────────────────────────────────────────────

# String values stored in the edges.confidence column.
CONFIDENCE_EXTRACTED = "EXTRACTED"
CONFIDENCE_INFERRED = "INFERRED"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"

# Integer rank: higher = stronger. Used for min/max comparisons.
_CONFIDENCE_RANK: dict[str, int] = {
    CONFIDENCE_AMBIGUOUS: 0,
    CONFIDENCE_INFERRED: 1,
    CONFIDENCE_EXTRACTED: 2,
}

# Reverse lookup: rank -> confidence string (for converting back after arithmetic).
_RANK_CONFIDENCE: dict[int, str] = {v: k for k, v in _CONFIDENCE_RANK.items()}

# Default rank for unknown/missing confidence values (conservative = INFERRED).
_DEFAULT_RANK = _CONFIDENCE_RANK[CONFIDENCE_INFERRED]

# Maximum number of SQL bind parameters per IN-clause batch.
# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on Linux/CI.
# We use 900 to stay safely below that limit even if a query adds extra params.
_SQL_VAR_BATCH = 900

# ── Public types ───────────────────────────────────────────────────────────────


class Reached(TypedDict):
    """A symbol reachable from the walk seeds, with its distance and path confidence.

    Fields:
        name        — symbol name (string, matches edges.source_name / target_name)
        distance    — number of hops from the nearest seed (1-based)
        confidence  — aggregated path confidence at this distance:
                      EXTRACTED if all hops are EXTRACTED;
                      INFERRED if any hop is INFERRED (no AMBIGUOUS);
                      AMBIGUOUS if any hop is AMBIGUOUS.
                      When multiple paths reach the same symbol at the same distance,
                      the STRONGEST confidence among those paths is reported.
    """

    name: str
    distance: int
    confidence: str


# ── Internal helpers ───────────────────────────────────────────────────────────


def _rank(confidence: str) -> int:
    """Return the integer rank for a confidence string (unknown -> INFERRED rank).

    Logs a debug message for unknown values to aid debugging without spamming logs.
    """
    if confidence not in _CONFIDENCE_RANK:
        # Unknown confidence value — treat conservatively as INFERRED, log at debug.
        logger.debug("unknown confidence value %r, treating as INFERRED", confidence)
    return _CONFIDENCE_RANK.get(confidence, _DEFAULT_RANK)


def _min_rank(a: int, b: int) -> int:
    """Propagate weakest-hop rule: return the weaker (lower) rank."""
    return min(a, b)


def _fetch_neighbors_with_parents(
    conn: sqlite3.Connection,
    names: set[str],
    direction: str,
) -> list[tuple[str, str, str]]:
    """Fetch one-hop neighbors for a set of symbol names, including parent info.

    Returns a list of (neighbor_name, parent_name, confidence) tuples.
    Self-edges (source == target) are excluded.

    Splits the IN-clause into batches of _SQL_VAR_BATCH to avoid hitting
    SQLite's SQLITE_MAX_VARIABLE_NUMBER limit (999 on Linux/CI).

    direction='upstream'   → find edges where target_name IN names;
                             return (source_name, target_name, confidence)
    direction='downstream' → find edges where source_name IN names;
                             return (target_name, source_name, confidence)
    """
    if not names:
        return []

    names_list = list(names)
    all_rows: list[tuple[str, str, str]] = []

    # Process in batches to avoid SQLITE_MAX_VARIABLE_NUMBER (999 on most builds).
    for batch_start in range(0, len(names_list), _SQL_VAR_BATCH):
        batch = names_list[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        if direction == "upstream":
            # Who depends on us? → edges pointing at us → source_name is the caller.
            sql = f"""
                SELECT source_name AS neighbor, target_name AS parent, confidence
                FROM edges
                WHERE target_name IN ({placeholders})
                  AND source_name != target_name
            """
        else:
            # What do we depend on? → edges going out from us → target_name is dep.
            sql = f"""
                SELECT target_name AS neighbor, source_name AS parent, confidence
                FROM edges
                WHERE source_name IN ({placeholders})
                  AND source_name != target_name
            """

        rows = conn.execute(sql, batch).fetchall()
        all_rows.extend((row["neighbor"], row["parent"], row["confidence"]) for row in rows)

    return all_rows


# ── Public interface ───────────────────────────────────────────────────────────


def walk(
    conn: sqlite3.Connection,
    seeds: list[str],
    direction: str,
    max_depth: int,
) -> list[Reached]:
    """Walk the edge graph from seed symbols, returning reachable symbols.

    Args:
        conn:       Open SQLite connection (read-only semantics; no writes).
        seeds:      Symbol names to start from. Empty list -> returns [].
        direction:  "upstream" (callers/importers) or "downstream" (callees/importees).
        max_depth:  Maximum number of hops from any seed (1-based). Must be >= 1.
                    Clamping to [1, 10] is the CALLER's responsibility (impact.py does this).

    Returns:
        List of Reached dicts — one per reachable symbol (excluding the seeds themselves).
        Ordered by distance ascending, then name alphabetically.
        Empty list if seeds is empty, no edges, or all reachable are already seeds.

    Cycle safety:
        Uses a ``visited`` set to avoid revisiting symbols. A symbol is visited
        at the first (minimum-distance) encounter. If the same symbol appears via
        multiple paths at the same distance, we keep the strongest confidence
        among those paths (see path-confidence rule in module docstring).

    SQLite variable limit:
        The IN-clause is batched in groups of _SQL_VAR_BATCH (900) to avoid
        hitting SQLite's SQLITE_MAX_VARIABLE_NUMBER limit (999 on Linux/CI).
        The batching is transparent — results are identical to an unbatched query.
    """
    if not seeds or max_depth < 1:
        return []

    # BFS state:
    #   visited: symbols already processed (prevents revisiting and cycles)
    #   current_frontier: set of (name, path_rank) for the current BFS level
    #   results: name -> (distance, best_path_rank_at_that_distance)
    seed_set = set(seeds)
    visited: set[str] = set(seeds)  # seeds are "visited" — we don't return them

    # For the initial frontier, each seed has a "perfect" path rank (it IS the seed).
    # Confidence of the path to a seed is irrelevant since seeds are excluded from output.
    # We track path_rank as a per-symbol accumulated rank from the seed to this symbol.
    # Initial frontier: all seeds, no hops yet.
    current_frontier: list[tuple[str, int]] = [(s, 2) for s in seeds]  # 2 = EXTRACTED rank (perfect start)

    results: dict[str, tuple[int, int]] = {}  # name -> (distance, best_path_rank)

    for depth in range(1, max_depth + 1):
        if not current_frontier:
            break

        # Build a map: parent_name -> path_rank so we can propagate confidence.
        parent_rank: dict[str, int] = {}
        for name, pr in current_frontier:
            # If a name appears multiple times in frontier (shouldn't, but defensive),
            # keep the strongest rank.
            if name not in parent_rank or pr > parent_rank[name]:
                parent_rank[name] = pr

        # Fetch one-hop neighbors for all symbols in the current frontier.
        # Single batched query (with _SQL_VAR_BATCH chunking) per BFS level.
        frontier_names = {name for name, _ in current_frontier}
        rows = _fetch_neighbors_with_parents(conn, frontier_names, direction)

        if not rows:
            break

        next_frontier: dict[str, int] = {}  # name -> best_path_rank for this depth

        for neighbor, parent, hop_confidence in rows:
            # Skip seeds — they are never returned as reachable.
            if neighbor in seed_set:
                continue

            # Compute path rank: propagate weakest-hop rule from the parent's path rank.
            parent_path_rank = parent_rank.get(parent, _DEFAULT_RANK)
            hop_rank = _rank(hop_confidence)
            path_rank = _min_rank(parent_path_rank, hop_rank)

            if neighbor in visited:
                # Already recorded at a shorter distance. Skip (BFS guarantees first
                # encounter is minimum distance). But if same distance, we might see
                # the same neighbor again via a different path in this BFS level —
                # handled via next_frontier deduplication below.
                continue

            # Track the best (strongest) path rank for this neighbor at this depth.
            if neighbor not in next_frontier or path_rank > next_frontier[neighbor]:
                next_frontier[neighbor] = path_rank

        # Commit next_frontier to results and update visited.
        new_frontier: list[tuple[str, int]] = []
        for name, pr in next_frontier.items():
            visited.add(name)
            results[name] = (depth, pr)
            new_frontier.append((name, pr))

        current_frontier = new_frontier

    # Convert results to Reached list, sorted by distance then name.
    reached: list[Reached] = [
        Reached(
            name=name,
            distance=distance,
            confidence=_RANK_CONFIDENCE.get(best_rank, CONFIDENCE_INFERRED),
        )
        for name, (distance, best_rank) in results.items()
    ]
    reached.sort(key=lambda r: (r["distance"], r["name"]))

    logger.debug(
        "walk(seeds=%s, direction=%s, max_depth=%d) -> %d reached",
        seeds,
        direction,
        max_depth,
        len(reached),
    )

    return reached
