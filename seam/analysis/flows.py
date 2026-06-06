"""Flow tracing — path-finding between symbols and one-hop caller/callee queries.

Contract
--------
``trace(conn, source, target, max_depth, repo_root=None) -> list[Path]``

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

``callers(conn, symbol, repo_root=None) -> list[EdgeHop]``

    One-hop upstream: who calls or imports `symbol`. Per-edge confidence included.

``callees(conn, symbol, repo_root=None) -> list[EdgeHop]``

    One-hop downstream: what `symbol` calls or imports. Per-edge confidence included.

Public types
------------
``Hop``  — one step in a Path: from_name, to_name, kind, confidence, resolved_by.
``Path`` — list[Hop] (ordered, non-empty; len >= 1 for a connected pair 1 hop apart).
``EdgeHop`` — one-hop result for callers()/callees(): name, kind, confidence, resolved_by.

Per-hop confidence
------------------
Each Hop carries the confidence of that specific edge (not an aggregated path
confidence). This lets callers see which individual hops are AMBIGUOUS.

When repo_root is provided and SEAM_IMPORT_RESOLUTION="on", confidence is resolved
via resolve_edge() with full import-promotion context (Phase 5 homonym fix), and
resolved_by carries the provenance string.  Without repo_root, falls back to the
name-count resolver and resolved_by is None.

The overall path confidence is the weakest hop (same rule as traversal.py), but
it is the callers' job to aggregate that from the Hop list if needed.

Module imports
--------------
stdlib: sqlite3, typing, logging, pathlib.
seam.analysis.confidence: all constants, load_name_counts, load_import_mappings,
    resolve, resolve_edge.
seam.analysis.traversal: _SQL_VAR_BATCH (IN-clause batch limit) and _rank (BFS helper).
No server/cli/query imports.
"""

import logging
import sqlite3
from pathlib import Path as FilePath
from typing import TypedDict

import seam.config as config
from seam.analysis.confidence import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
    Resolution,
    load_import_mappings,
    load_name_counts,
    resolve,
    resolve_edge,
)
from seam.analysis.imports import ImportMapping
from seam.analysis.traversal import (
    _SQL_VAR_BATCH,
    _rank,
)
from seam.query.names import expand_impact_seeds

logger = logging.getLogger(__name__)

# ── Type definitions ───────────────────────────────────────────────────────────


class Hop(TypedDict):
    """One step on a call/dependency path.

    Fields:
        from_name      — source symbol of this edge
        to_name        — target symbol of this edge
        kind           — edge kind: 'call' | 'import'
        confidence     — edge confidence: EXTRACTED | INFERRED | AMBIGUOUS
        resolved_by    — Phase 5: how confidence was decided (see RESOLVED_BY_* in confidence.py).
                         None for fast-path hops (name-count only, no import mapping context).
        best_candidate — Phase 5: for AMBIGUOUS hops, the most-proximate declaring file.
                         None for non-AMBIGUOUS or when proximity data is unavailable.
    """

    from_name: str
    to_name: str
    kind: str
    confidence: str
    resolved_by: str | None
    best_candidate: str | None


# Path is an ordered list of Hops from source to target.
# NOTE: This type alias named 'Path' is intentionally distinct from pathlib.Path
# (which is imported as FilePath in this module to avoid name collision).
# Invariants:
#   - len >= 1 (a direct edge from source to target produces one Hop)
#   - path[0].from_name == source, path[-1].to_name == target
#   - consecutive hops are linked: path[i].to_name == path[i+1].from_name
Path = list[Hop]


class EdgeHop(TypedDict):
    """One-hop result for callers() / callees().

    Fields:
        name           — the neighboring symbol name
        kind           — edge kind: 'call' | 'import'
        confidence     — edge confidence: EXTRACTED | INFERRED | AMBIGUOUS
        resolved_by    — Phase 5: how confidence was decided. None for fast-path hops
                         (name-count only — full import-promotion context not available here).
        best_candidate — Phase 5: for AMBIGUOUS hops, the most-proximate declaring file.
                         None for non-AMBIGUOUS or when proximity data is unavailable.
    """

    name: str
    kind: str
    confidence: str
    resolved_by: str | None
    best_candidate: str | None


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
) -> list[tuple[str, str, str, str, str]]:
    """Fetch all outgoing edges from any name in `names`, with file context for Phase 5.

    Returns list of (source_name, target_name, kind, ref_file_path, language).
    Self-edges (source == target) are excluded.

    ref_file_path and language come from the edges.file_id → files JOIN, providing
    the referencing file context required by resolve_edge() for import promotion.

    The stored edges.confidence column is intentionally NOT selected: per-hop
    confidence is resolved whole-index from target_name at read time (see
    confidence.resolve_edge), so the stored same-file value is never surfaced here.

    Batches IN-clause in groups of _SQL_VAR_BATCH to avoid the
    SQLITE_MAX_VARIABLE_NUMBER (999) limit on Linux/CI.
    """
    if not names:
        return []

    names_list = list(names)
    all_rows: list[tuple[str, str, str, str, str]] = []

    for batch_start in range(0, len(names_list), _SQL_VAR_BATCH):
        batch = names_list[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        # Downstream: follow edges from source to target, join files for context.
        sql = f"""
            SELECT e.source_name, e.target_name, e.kind,
                   f.path AS ref_file_path, f.language
            FROM edges e
            JOIN files f ON f.id = e.file_id
            WHERE e.source_name IN ({placeholders})
              AND e.source_name != e.target_name
        """
        rows = conn.execute(sql, batch).fetchall()
        all_rows.extend(
            (
                row["source_name"],
                row["target_name"],
                row["kind"],
                row["ref_file_path"],
                row["language"],
            )
            for row in rows
        )

    return all_rows


def _fetch_incoming_edges(
    conn: sqlite3.Connection,
    names: set[str],
) -> list[tuple[str, str, str, str, str]]:
    """Fetch all incoming edges to any name in `names`, with file context for Phase 5.

    Returns list of (source_name, target_name, kind, ref_file_path, language).
    Self-edges (source == target) are excluded.

    ref_file_path and language come from the edges.file_id → files JOIN, providing
    the referencing file context required by resolve_edge() for import promotion.

    Stored confidence is not selected — see _fetch_outgoing_edges for why.

    Batches IN-clause in groups of _SQL_VAR_BATCH.
    """
    if not names:
        return []

    names_list = list(names)
    all_rows: list[tuple[str, str, str, str, str]] = []

    for batch_start in range(0, len(names_list), _SQL_VAR_BATCH):
        batch = names_list[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        # Upstream: edges pointing AT these names, join files for context.
        sql = f"""
            SELECT e.source_name, e.target_name, e.kind,
                   f.path AS ref_file_path, f.language
            FROM edges e
            JOIN files f ON f.id = e.file_id
            WHERE e.target_name IN ({placeholders})
              AND e.source_name != e.target_name
        """
        rows = conn.execute(sql, batch).fetchall()
        all_rows.extend(
            (
                row["source_name"],
                row["target_name"],
                row["kind"],
                row["ref_file_path"],
                row["language"],
            )
            for row in rows
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
    repo_root: FilePath | None = None,
) -> list[Path]:
    """Find the shortest call/dependency path from source to target.

    Args:
        conn:       Open SQLite connection (read-only; no writes).
        source:     Starting symbol name. If source == target, returns [[]].
        target:     Destination symbol name.
        max_depth:  Maximum number of hops allowed. Must be >= 1.
                    The caller is responsible for clamping (e.g. to [1, 10]).
        repo_root:  Repository root. When provided and SEAM_IMPORT_RESOLUTION="on",
                    each hop's confidence is resolved via resolve_edge() with import
                    promotion (Phase 5 homonym fix), and resolved_by carries provenance.
                    When None or SEAM_IMPORT_RESOLUTION="off", falls back to name-count.

    Returns:
        A list containing the single shortest Path from source to target,
        or [] if no path exists within max_depth hops.

        A Path is list[Hop] where each Hop carries from_name, to_name, kind,
        confidence, and resolved_by. The path is ordered: path[0].from_name == source,
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

    # Expand the target to a set of alias names — bridges the qualified/bare edge gap.
    # Seam's extractor stores edge target_name as bare "method" while the symbol is
    # stored as "Class.method". The BFS checks tgt_name IN target_aliases so a hop
    # that reaches bare "parse" is treated as reaching "Parser.parse".
    # expand_impact_seeds("Parser.parse") -> ["Parser.parse", "parse"]
    # The target_aliases set includes the original target too (exact match preserved).
    target_seeds = expand_impact_seeds(conn, target)
    target_aliases: set[str] = set(target_seeds)
    # Always include the exact target — expand_impact_seeds returns it first but be explicit.
    target_aliases.add(target)
    logger.debug(
        "trace: target=%r expanded aliases=%s", target, sorted(target_aliases)
    )

    # Load whole-index name-count map ONCE per trace() call.
    name_counts = load_name_counts(conn)

    # Determine whether full import-promotion is enabled for this trace call.
    use_import_promotion = repo_root is not None and config.SEAM_IMPORT_RESOLUTION == "on"

    # Per-file import mapping cache: path -> list[ImportMapping].
    # Prevents repeated load_import_mappings calls for the same file within one trace.
    _import_cache: dict[str, list[ImportMapping]] = {}

    # Per-(file, target) resolution-result cache keyed on (ref_file_path, tgt_name).
    # Avoids re-running the declaration-check and proximity SELECT in resolve_edge
    # for repeated (file, target) pairs — common when multiple BFS levels cross the
    # same edges (e.g. a frequently-called utility in many files).
    _resolution_cache: dict[tuple[str, str], Resolution] = {}

    def _get_import_mappings(file_path: str) -> list[ImportMapping]:
        if file_path not in _import_cache:
            _import_cache[file_path] = load_import_mappings(conn, file_path)
        return _import_cache[file_path]

    # BFS state.
    visited: set[str] = {source}
    frontier: set[str] = {source}

    # Maps a discovered node to the (parent_name, Hop) that first reached it.
    parent_map: dict[str, tuple[str, Hop]] = {}

    for _level in range(max_depth):
        if not frontier:
            break

        # Fetch ALL outgoing edges for the entire frontier in one batched query.
        # Returns (source_name, target_name, kind, ref_file_path, language).
        outgoing = _fetch_outgoing_edges(conn, frontier)

        # Sort for determinism: (target_name, kind) lexicographic order ensures
        # that when multiple parents can reach the same node at the same level,
        # we pick the lexicographically-first (source_name, kind) path.
        outgoing.sort(key=lambda r: (r[1], r[2]))

        next_frontier: set[str] = set()

        for src_name, tgt_name, kind, ref_file_path, language in outgoing:
            # Resolve per-hop confidence via full Phase 5 resolver when available,
            # using the cache to avoid re-running declaration-check and proximity SELECTs
            # for repeated (file, target) pairs across BFS levels.
            if use_import_promotion and ref_file_path and language:
                cache_key = (ref_file_path, tgt_name)
                if cache_key not in _resolution_cache:
                    import_mappings = _get_import_mappings(ref_file_path)
                    _resolution_cache[cache_key] = resolve_edge(
                        target_name=tgt_name,
                        name_counts=name_counts,
                        language=language,
                        import_mappings=import_mappings,
                        referencing_file=FilePath(ref_file_path),
                        repo_root=repo_root,
                        conn=conn,
                        max_import_candidates=config.SEAM_MAX_IMPORT_CANDIDATES,
                        max_proximity_candidates=config.SEAM_PROXIMITY_MAX_CANDIDATES,
                    )
                resolution = _resolution_cache[cache_key]
                hop_confidence = resolution["confidence"]
                hop_resolved_by = resolution["resolved_by"]
                hop_best_candidate: str | None = resolution.get("best_candidate")
            else:
                hop_confidence = resolve(tgt_name, name_counts)
                hop_resolved_by = None
                hop_best_candidate = None

            hop = Hop(
                from_name=src_name,
                to_name=tgt_name,
                kind=kind,
                confidence=hop_confidence,
                resolved_by=hop_resolved_by,
                best_candidate=hop_best_candidate,
            )

            # Found the target — record its parent and return immediately (BFS = shortest).
            # tgt_name IN target_aliases handles the bare/qualified asymmetry:
            # e.g. tgt_name="parse" matches target_aliases={"Parser.parse", "parse"}.
            # _reconstruct_path uses tgt_name as the key (the actual edge target stored),
            # so the returned path reflects the real edge, not the alias.
            if tgt_name in target_aliases:
                parent_map[tgt_name] = (src_name, hop)
                path = _reconstruct_path(tgt_name, parent_map)
                logger.debug(
                    "trace(%r -> %r): found path of length %d via alias %r (import_promotion=%s)",
                    source,
                    target,
                    len(path),
                    tgt_name,
                    use_import_promotion,
                )
                return [path]

            # Skip already-discovered symbols (cycle safety + BFS shortest-first).
            if tgt_name in visited:
                continue

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
    repo_root: FilePath | None = None,
) -> list[EdgeHop]:
    """Return all one-hop upstream neighbors (who calls or imports `symbol`).

    Args:
        conn:      Open SQLite connection (read-only).
        symbol:    Symbol name to look up. Empty string returns [].
        repo_root: Repository root for Phase 5 import-promotion resolution.
                   When provided and SEAM_IMPORT_RESOLUTION="on", each hop's
                   confidence is resolved via resolve_edge() so imported bindings
                   of homonyms promote to EXTRACTED 'import'.  None -> name-count only.

    Returns:
        List of EdgeHop dicts, each with:
            name        — the caller/importer symbol name
            kind        — 'call' | 'import'
            confidence  — EXTRACTED | INFERRED | AMBIGUOUS
            resolved_by — Phase 5 provenance string (or None for fast-path)

        Results are sorted by name alphabetically for determinism.
        Returns [] if the symbol has no callers or does not exist in the index.
    """
    if not symbol:
        logger.debug("callers(): empty symbol — returning []")
        return []

    name_counts = load_name_counts(conn)
    use_import_promotion = repo_root is not None and config.SEAM_IMPORT_RESOLUTION == "on"

    # Per-file import mapping cache for this callers() call.
    _import_cache: dict[str, list[ImportMapping]] = {}

    # Per-(file, target) resolution-result cache: avoids re-running resolve_edge
    # for repeated (file, target) pairs in the incoming-edge rows.
    _resolution_cache: dict[tuple[str, str], Resolution] = {}

    def _get_import_mappings(file_path: str) -> list[ImportMapping]:
        if file_path not in _import_cache:
            _import_cache[file_path] = load_import_mappings(conn, file_path)
        return _import_cache[file_path]

    rows = _fetch_incoming_edges(conn, {symbol})

    # Deduplicate by (source_name, kind) — keep strongest (confidence, resolved_by, best_candidate).
    best: dict[tuple[str, str], tuple[str, str | None, str | None]] = {}

    for src, tgt, kind, ref_file_path, language in rows:
        # Resolve confidence, using the cache to avoid repeated declaration SELECTs.
        if use_import_promotion and ref_file_path and language:
            cache_key = (ref_file_path, tgt)
            if cache_key not in _resolution_cache:
                import_mappings = _get_import_mappings(ref_file_path)
                _resolution_cache[cache_key] = resolve_edge(
                    target_name=tgt,
                    name_counts=name_counts,
                    language=language,
                    import_mappings=import_mappings,
                    referencing_file=FilePath(ref_file_path),
                    repo_root=repo_root,
                    conn=conn,
                    max_import_candidates=config.SEAM_MAX_IMPORT_CANDIDATES,
                    max_proximity_candidates=config.SEAM_PROXIMITY_MAX_CANDIDATES,
                )
            resolution = _resolution_cache[cache_key]
            resolved_confidence = resolution["confidence"]
            resolved_by = resolution["resolved_by"]
            best_candidate: str | None = resolution.get("best_candidate")
        else:
            resolved_confidence = resolve(tgt, name_counts)
            resolved_by = None
            best_candidate = None

        key = (src, kind)
        existing = best.get(key)
        if existing is None:
            best[key] = (resolved_confidence, resolved_by, best_candidate)
        else:
            # Keep stronger confidence (higher rank); update resolved_by and best_candidate.
            if _rank(resolved_confidence) > _rank(existing[0]):
                best[key] = (resolved_confidence, resolved_by, best_candidate)

    result: list[EdgeHop] = [
        EdgeHop(name=name, kind=kind, confidence=conf, resolved_by=rby, best_candidate=bc)
        for (name, kind), (conf, rby, bc) in sorted(best.items())
    ]
    logger.debug(
        "callers(%r): %d one-hop callers (import_promotion=%s)",
        symbol,
        len(result),
        use_import_promotion,
    )
    return result


def callees(
    conn: sqlite3.Connection,
    symbol: str,
    repo_root: FilePath | None = None,
) -> list[EdgeHop]:
    """Return all one-hop downstream neighbors (what `symbol` calls or imports).

    Args:
        conn:      Open SQLite connection (read-only).
        symbol:    Symbol name to look up. Empty string returns [].
        repo_root: Repository root for Phase 5 import-promotion resolution.
                   When provided and SEAM_IMPORT_RESOLUTION="on", each hop's
                   confidence is resolved via resolve_edge() so imported bindings
                   of homonyms promote to EXTRACTED 'import'.  None -> name-count only.

    Returns:
        List of EdgeHop dicts, each with:
            name        — the callee/importee symbol name
            kind        — 'call' | 'import'
            confidence  — EXTRACTED | INFERRED | AMBIGUOUS
            resolved_by — Phase 5 provenance string (or None for fast-path)

        Results are sorted by name alphabetically for determinism.
        Returns [] if the symbol calls nothing or does not exist in the index.
    """
    if not symbol:
        logger.debug("callees(): empty symbol — returning []")
        return []

    name_counts = load_name_counts(conn)
    use_import_promotion = repo_root is not None and config.SEAM_IMPORT_RESOLUTION == "on"

    _import_cache: dict[str, list[ImportMapping]] = {}

    # Per-(file, target) resolution-result cache: avoids re-running resolve_edge
    # for repeated (file, target) pairs in the outgoing-edge rows.
    _resolution_cache: dict[tuple[str, str], Resolution] = {}

    def _get_import_mappings(file_path: str) -> list[ImportMapping]:
        if file_path not in _import_cache:
            _import_cache[file_path] = load_import_mappings(conn, file_path)
        return _import_cache[file_path]

    rows = _fetch_outgoing_edges(conn, {symbol})

    # Deduplicate by (target_name, kind) — keep strongest (confidence, resolved_by, best_candidate).
    best: dict[tuple[str, str], tuple[str, str | None, str | None]] = {}

    for _src, tgt, kind, ref_file_path, language in rows:
        if use_import_promotion and ref_file_path and language:
            cache_key = (ref_file_path, tgt)
            if cache_key not in _resolution_cache:
                import_mappings = _get_import_mappings(ref_file_path)
                _resolution_cache[cache_key] = resolve_edge(
                    target_name=tgt,
                    name_counts=name_counts,
                    language=language,
                    import_mappings=import_mappings,
                    referencing_file=FilePath(ref_file_path),
                    repo_root=repo_root,
                    conn=conn,
                    max_import_candidates=config.SEAM_MAX_IMPORT_CANDIDATES,
                    max_proximity_candidates=config.SEAM_PROXIMITY_MAX_CANDIDATES,
                )
            resolution = _resolution_cache[cache_key]
            resolved_confidence = resolution["confidence"]
            resolved_by = resolution["resolved_by"]
            best_candidate_c: str | None = resolution.get("best_candidate")
        else:
            resolved_confidence = resolve(tgt, name_counts)
            resolved_by = None
            best_candidate_c = None

        key = (tgt, kind)
        existing = best.get(key)
        if existing is None:
            best[key] = (resolved_confidence, resolved_by, best_candidate_c)
        else:
            if _rank(resolved_confidence) > _rank(existing[0]):
                best[key] = (resolved_confidence, resolved_by, best_candidate_c)

    result: list[EdgeHop] = [
        EdgeHop(name=name, kind=kind, confidence=conf, resolved_by=rby, best_candidate=bc)
        for (name, kind), (conf, rby, bc) in sorted(best.items())
    ]
    logger.debug(
        "callees(%r): %d one-hop callees (import_promotion=%s)",
        symbol,
        len(result),
        use_import_promotion,
    )
    return result
