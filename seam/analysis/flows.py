"""Flow tracing — path-finding between symbols and one-hop caller/callee queries.

Contract
--------
``trace(conn, source, target, max_depth) -> list[Path]``

    Find call/dependency path(s) from source to target over the edges table.
    A Path is an ordered list of hops; each hop carries from-name, to-name,
    edge kind, and edge confidence.

    Returns [] when no path exists — this is a distinguishable "not connected"
    result, not an error.

    Decision: returns **shortest path only** (single BFS front-to-back).
    Rationale: BFS by construction finds the shortest path first. Returning all
    simple paths would require DFS with backtracking and can explode exponentially
    on dense graphs. The single shortest path is what almost all callers want; if
    multiple shortest paths exist at the same length, the lexicographically first
    is returned (deterministic ordering).

    If you need all simple paths up to a cap, use the lower-level edges table
    directly — the BFS here does not enumerate them.

    CYCLE-SAFE and bounded by max_depth.

``callers(conn, symbol) -> list[EdgeHop]``

    One-hop upstream: who calls or imports `symbol`. Per-edge confidence included.

``callees(conn, symbol) -> list[EdgeHop]``

    One-hop downstream: what `symbol` calls or imports. Per-edge confidence included.

Public types
------------
``Hop``  — one step in a Path: from_name, to_name, kind, confidence.
``Path`` — list[Hop] (ordered, non-empty; len >= 1 for a connected pair 1 hop apart).
``EdgeHop`` — one-hop result for callers()/callees(): name, kind, confidence.

Per-hop confidence
------------------
Each Hop carries the confidence of that specific edge (not an aggregated path
confidence). This lets callers see which individual hops are AMBIGUOUS.

The overall path confidence is the weakest hop (same rule as traversal.py), but
it is the callers' job to aggregate that from the Hop list if needed.

Module imports
--------------
stdlib: sqlite3, typing, logging.
seam.analysis.confidence: EXTRACTED/AMBIGUOUS/INFERRED constants, load_name_counts, resolve.
seam.analysis.traversal: _SQL_VAR_BATCH (IN-clause batch limit) and _rank (BFS arithmetic helper).
No server/cli/query imports.
"""

import logging
import sqlite3
from typing import TypedDict

from seam.analysis.confidence import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
    load_name_counts,
    resolve,
)
from seam.analysis.traversal import (
    _SQL_VAR_BATCH,
    _rank,
)

logger = logging.getLogger(__name__)

# ── Type definitions ───────────────────────────────────────────────────────────


class Hop(TypedDict):
    """One step on a call/dependency path.

    Fields:
        from_name   — source symbol of this edge
        to_name     — target symbol of this edge
        kind        — edge kind: 'call' | 'import'
        confidence  — edge confidence: EXTRACTED | INFERRED | AMBIGUOUS
    """

    from_name: str
    to_name: str
    kind: str
    confidence: str


# A Path is an ordered list of Hops from source to target.
# Invariants:
#   - len >= 1 (a direct edge from source to target produces one Hop)
#   - path[0].from_name == source, path[-1].to_name == target
#   - consecutive hops are linked: path[i].to_name == path[i+1].from_name
Path = list[Hop]


class EdgeHop(TypedDict):
    """One-hop result for callers() / callees().

    Fields:
        name        — the neighboring symbol name
        kind        — edge kind: 'call' | 'import'
        confidence  — edge confidence: EXTRACTED | INFERRED | AMBIGUOUS
    """

    name: str
    kind: str
    confidence: str


# ── Internal helpers ───────────────────────────────────────────────────────────

# Re-export confidence constants for convenience (callers can import from here).
__all__ = [
    "Hop",
    "Path",
    "EdgeHop",
    "trace",
    "callers",
    "callees",
    "CONFIDENCE_EXTRACTED",
    "CONFIDENCE_INFERRED",
    "CONFIDENCE_AMBIGUOUS",
]


def _fetch_outgoing_edges(
    conn: sqlite3.Connection,
    names: set[str],
) -> list[tuple[str, str, str]]:
    """Fetch all outgoing edges from any name in `names`.

    Returns list of (source_name, target_name, kind).
    Self-edges (source == target) are excluded.

    The stored edges.confidence column is intentionally NOT selected: per-hop
    confidence is resolved whole-index from target_name at read time (see
    confidence.resolve), so the stored same-file value is never surfaced here.

    Batches IN-clause in groups of _SQL_VAR_BATCH to avoid the
    SQLITE_MAX_VARIABLE_NUMBER (999) limit on Linux/CI.
    """
    if not names:
        return []

    names_list = list(names)
    all_rows: list[tuple[str, str, str]] = []

    for batch_start in range(0, len(names_list), _SQL_VAR_BATCH):
        batch = names_list[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        # Downstream: follow edges from source to target.
        sql = f"""
            SELECT source_name, target_name, kind
            FROM edges
            WHERE source_name IN ({placeholders})
              AND source_name != target_name
        """
        rows = conn.execute(sql, batch).fetchall()
        all_rows.extend(
            (row["source_name"], row["target_name"], row["kind"]) for row in rows
        )

    return all_rows


def _fetch_incoming_edges(
    conn: sqlite3.Connection,
    names: set[str],
) -> list[tuple[str, str, str]]:
    """Fetch all incoming edges to any name in `names`.

    Returns list of (source_name, target_name, kind).
    Self-edges (source == target) are excluded.

    Stored confidence is not selected — see _fetch_outgoing_edges for why.

    Batches IN-clause in groups of _SQL_VAR_BATCH.
    """
    if not names:
        return []

    names_list = list(names)
    all_rows: list[tuple[str, str, str]] = []

    for batch_start in range(0, len(names_list), _SQL_VAR_BATCH):
        batch = names_list[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        # Upstream: edges pointing AT these names.
        sql = f"""
            SELECT source_name, target_name, kind
            FROM edges
            WHERE target_name IN ({placeholders})
              AND source_name != target_name
        """
        rows = conn.execute(sql, batch).fetchall()
        all_rows.extend(
            (row["source_name"], row["target_name"], row["kind"]) for row in rows
        )

    return all_rows


# ── Public interface ───────────────────────────────────────────────────────────


def _reconstruct_path(
    target: str,
    parent_map: dict[str, tuple[str, Hop]],
) -> Path:
    """Reconstruct the shortest path from parent-pointer map.

    parent_map maps child_name -> (parent_name, Hop that produced child_name).
    Walks backwards from target to rebuild the ordered hop sequence.
    """
    path: list[Hop] = []
    current = target
    while current in parent_map:
        parent_name, hop = parent_map[current]
        path.append(hop)
        current = parent_name
    path.reverse()
    return path


def trace(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    max_depth: int = 10,
) -> list[Path]:
    """Find the shortest call/dependency path from source to target.

    Args:
        conn:       Open SQLite connection (read-only; no writes).
        source:     Starting symbol name. If source == target, returns [[]].
        target:     Destination symbol name.
        max_depth:  Maximum number of hops allowed. Must be >= 1.
                    The caller is responsible for clamping (e.g. to [1, 10]).

    Returns:
        A list containing the single shortest Path from source to target,
        or [] if no path exists within max_depth hops.

        A Path is list[Hop] where each Hop carries from_name, to_name, kind,
        and per-edge confidence. The path is ordered: path[0].from_name == source,
        path[-1].to_name == target.

        If source == target: returns [[]] — a one-element list containing an
        empty path (trivially connected to itself, zero hops).

    Cycle safety:
        BFS uses a `visited` set. Each symbol is visited at most once.
        A cycle (A->B->A) terminates because A is in visited before B is expanded.

    Performance:
        Level-by-level batched BFS — each BFS level issues ONE batched SQL query
        for the entire frontier (via _fetch_outgoing_edges batching), rather than
        one query per node. This avoids O(N) queries on large disconnected graphs.

    SQLite variable limit:
        IN-clauses are batched in groups of _SQL_VAR_BATCH (900) per BFS level.
    """
    if max_depth < 1:
        return []

    # Trivial case: source and target are the same symbol.
    if source == target:
        return [[]]  # empty path — zero hops, trivially connected

    # Load whole-index name-count map ONCE per trace() call.
    # Used to resolve per-hop confidence from the edge's target_name against the full index,
    # overriding the stored edges.confidence (same-file lower-bound hint).
    name_counts = load_name_counts(conn)

    # BFS state:
    #   frontier: set of symbol names at the current BFS level
    #   visited:  symbols already discovered (prevents cycles and re-visiting)
    #   parent_map: child_name -> (parent_name, Hop) — used for path reconstruction
    #
    # Level-by-level approach: one SQL call per BFS level (frontier batch query),
    # rather than one query per node. This cuts SQL round-trips from O(N) to O(depth).
    visited: set[str] = {source}
    frontier: set[str] = {source}

    # Maps a discovered node to the (parent_name, Hop) that first reached it.
    # Populated on first discovery; never overwritten (BFS guarantees shortest).
    parent_map: dict[str, tuple[str, Hop]] = {}

    for _level in range(max_depth):
        if not frontier:
            break

        # Fetch ALL outgoing edges for the entire frontier in one batched query.
        outgoing = _fetch_outgoing_edges(conn, frontier)

        # Sort for determinism: (target_name, kind) lexicographic order ensures
        # that when multiple parents can reach the same node at the same level,
        # we pick the lexicographically-first (source_name, kind) path.
        outgoing.sort(key=lambda r: (r[1], r[2]))

        next_frontier: set[str] = set()

        for src_name, tgt_name, kind in outgoing:
            # Resolve hop confidence from the edge's target_name against the whole index.
            hop_confidence = resolve(tgt_name, name_counts)

            # Build the Hop for this edge with whole-index resolved confidence.
            hop = Hop(
                from_name=src_name,
                to_name=tgt_name,
                kind=kind,
                confidence=hop_confidence,
            )

            # Found the target — record its parent and return immediately.
            # BFS guarantees this is the shortest path.
            if tgt_name == target:
                parent_map[tgt_name] = (src_name, hop)
                path = _reconstruct_path(target, parent_map)
                logger.debug(
                    "trace(%r -> %r): found path of length %d",
                    source,
                    target,
                    len(path),
                )
                return [path]

            # Skip already-discovered symbols (cycle safety + BFS shortest-first).
            if tgt_name in visited:
                continue

            # Record parent on first discovery only; skip if already in next_frontier
            # (another node in the current frontier reached it first — sorted order
            # ensures we keep the lexicographically-first path).
            if tgt_name not in next_frontier:
                parent_map[tgt_name] = (src_name, hop)
                next_frontier.add(tgt_name)

        # Advance frontier; mark all new nodes as visited.
        visited.update(next_frontier)
        frontier = next_frontier

    # No path found within max_depth.
    logger.debug("trace(%r -> %r): no path found (max_depth=%d)", source, target, max_depth)
    return []


def callers(
    conn: sqlite3.Connection,
    symbol: str,
) -> list[EdgeHop]:
    """Return all one-hop upstream neighbors (who calls or imports `symbol`).

    Args:
        conn:   Open SQLite connection (read-only).
        symbol: Symbol name to look up. Empty string returns [].

    Returns:
        List of EdgeHop dicts, each with:
            name       — the caller/importer symbol name
            kind       — 'call' | 'import'
            confidence — EXTRACTED | INFERRED | AMBIGUOUS

        Results are sorted by name alphabetically for determinism.
        Returns [] if the symbol has no callers or does not exist in the index.
    """
    if not symbol:
        logger.debug("callers(): empty symbol — returning []")
        return []

    # Load whole-index name-count map once per callers() call.
    # Resolve confidence from the edge's target_name (always `symbol` for incoming edges).
    name_counts = load_name_counts(conn)

    rows = _fetch_incoming_edges(conn, {symbol})
    # Deduplicate by (source_name, kind) — keep strongest confidence for dupes.
    best: dict[tuple[str, str], str] = {}
    for src, tgt, kind in rows:
        # For incoming edges (callers), the edge target is always `symbol`.
        # Resolve confidence from the whole-index map keyed on target_name.
        resolved_confidence = resolve(tgt, name_counts)
        key = (src, kind)
        existing = best.get(key)
        if existing is None:
            best[key] = resolved_confidence
        else:
            # Keep stronger confidence (higher rank).
            if _rank(resolved_confidence) > _rank(existing):
                best[key] = resolved_confidence

    result: list[EdgeHop] = [
        EdgeHop(name=name, kind=kind, confidence=conf)
        for (name, kind), conf in sorted(best.items())
    ]
    logger.debug("callers(%r): %d one-hop callers", symbol, len(result))
    return result


def callees(
    conn: sqlite3.Connection,
    symbol: str,
) -> list[EdgeHop]:
    """Return all one-hop downstream neighbors (what `symbol` calls or imports).

    Args:
        conn:   Open SQLite connection (read-only).
        symbol: Symbol name to look up. Empty string returns [].

    Returns:
        List of EdgeHop dicts, each with:
            name       — the callee/importee symbol name
            kind       — 'call' | 'import'
            confidence — EXTRACTED | INFERRED | AMBIGUOUS

        Results are sorted by name alphabetically for determinism.
        Returns [] if the symbol calls nothing or does not exist in the index.
    """
    if not symbol:
        logger.debug("callees(): empty symbol — returning []")
        return []

    # Load whole-index name-count map once per callees() call.
    # Resolve confidence from the edge's target_name (the callee).
    name_counts = load_name_counts(conn)

    rows = _fetch_outgoing_edges(conn, {symbol})
    # Deduplicate by (target_name, kind) — keep strongest confidence for dupes.
    best: dict[tuple[str, str], str] = {}
    for _src, tgt, kind in rows:
        # For outgoing edges (callees), resolve confidence keyed on target_name (the callee).
        resolved_confidence = resolve(tgt, name_counts)
        key = (tgt, kind)
        existing = best.get(key)
        if existing is None:
            best[key] = resolved_confidence
        else:
            if _rank(resolved_confidence) > _rank(existing):
                best[key] = resolved_confidence

    result: list[EdgeHop] = [
        EdgeHop(name=name, kind=kind, confidence=conf)
        for (name, kind), conf in sorted(best.items())
    ]
    logger.debug("callees(%r): %d one-hop callees", symbol, len(result))
    return result
