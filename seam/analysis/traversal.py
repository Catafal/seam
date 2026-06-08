"""Graph traversal engine — shared core for impact, trace, and detect_changes.

Contract
--------
``walk(conn, seeds, direction, max_depth, repo_root=None) -> list[Reached]``

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
    resolved_by — Phase 5: how the final hop's confidence was decided
                  (from resolve_edge when repo_root is available, else from name-count).

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
    Only sqlite3, typing, logging, pathlib (stdlib). No server/cli/query imports.
"""

import logging
import sqlite3
from pathlib import Path
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

logger = logging.getLogger(__name__)

# ── Confidence constants ───────────────────────────────────────────────────────
# Re-export the canonical strings from confidence.py so existing callers that
# import them from this module continue to work without changes.
# The definitions live in seam.analysis.confidence — that is the single source of truth.
__all__ = [
    "CONFIDENCE_EXTRACTED",
    "CONFIDENCE_INFERRED",
    "CONFIDENCE_AMBIGUOUS",
    "Reached",
    "walk",
]

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
        name           — symbol name (string, matches edges.source_name / target_name)
        distance       — number of hops from the nearest seed (1-based)
        confidence     — aggregated path confidence at this distance:
                         EXTRACTED if all hops are EXTRACTED;
                         INFERRED if any hop is INFERRED (no AMBIGUOUS);
                         AMBIGUOUS if any hop is AMBIGUOUS.
                         When multiple paths reach the same symbol at the same distance,
                         the STRONGEST confidence among those paths is reported.
        resolved_by    — Phase 5: how the final hop's confidence was decided.
                         None for fast-path walk (name-count only, no import context).
                         NOTE: resolved_by comes from the FINAL hop of the winning path,
                         while confidence is the WEAKEST hop — so resolved_by='import'
                         can accompany confidence='AMBIGUOUS' on multi-hop paths.
        best_candidate — Phase 5: for AMBIGUOUS final-hop entries, the most
                         file-path-proximate declaring file (absolute path string).
                         None for non-AMBIGUOUS hops or when proximity data is unavailable.
        kind           — E4: edge kind of the FINAL hop of the winning (strongest-confidence)
                         path to this symbol. Full closed vocabulary:
                         call | import | extends | implements | instantiates | holds |
                         reads | writes | uses.
                         Same provenance source as resolved_by — all four fields describe
                         one coherent edge. Empty string for degenerate BFS cases.
        synthesized_by — E4: synthesis channel name when the final hop is a heuristic
                         synthesized edge (e.g. 'interface-override', 'closure-collection',
                         'event-emitter'). None when the final hop is statically extracted.
                         Same null-contract as resolved_by/best_candidate: null ≡ static.
    """

    name: str
    distance: int
    confidence: str
    resolved_by: str | None
    best_candidate: str | None
    kind: str
    synthesized_by: str | None


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
) -> list[tuple[str, str, str, str, str, str, str | None]]:
    """Fetch one-hop neighbors for a set of symbol names, with file context for Phase 5.

    Returns a list of:
        (neighbor_name, parent_name, edge_target_name, ref_file_path, language,
         edge_kind, edge_synthesized_by)

    The 3rd column — edge_target_name — is always the edge's target_name column,
    regardless of traversal direction.  This lets the caller resolve confidence
    against the whole-index name map keyed on the callee/importee name.

    4th column — ref_file_path — is the absolute path of the file the edge was extracted
    from (edges.file_id → files.path).  Used by resolve_edge() for import-promotion.

    5th column — language — is files.language for the referencing file.  Used by
    resolve_edge() for builtin-check and import source resolution.

    6th column — edge_kind — the edge.kind value ('call'|'import'|'holds'|...).
    7th column — edge_synthesized_by — the edge.synthesized_by value (None for static edges;
                  channel name string for synthesized edges).

    The stored edges.confidence column is intentionally NOT selected: hop
    confidence is resolved whole-index from edge_target_name at read time
    (see confidence.resolve_edge), so the stored same-file value never feeds traversal.

    Splits the IN-clause into batches of _SQL_VAR_BATCH to avoid hitting
    SQLite's SQLITE_MAX_VARIABLE_NUMBER limit (999 on Linux/CI).

    direction='upstream'   → edges where target_name IN names;
                             neighbor=source_name, parent=target_name, edge_target_name=target_name
    direction='downstream' → edges where source_name IN names;
                             neighbor=target_name, parent=source_name, edge_target_name=target_name
    """
    if not names:
        return []

    names_list = list(names)
    all_rows: list[tuple[str, str, str, str, str, str, str | None]] = []

    # Process in batches to avoid SQLITE_MAX_VARIABLE_NUMBER (999 on most builds).
    for batch_start in range(0, len(names_list), _SQL_VAR_BATCH):
        batch = names_list[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        if direction == "upstream":
            # Who depends on us? → edges pointing at us → source_name is the caller.
            # edge_target_name = target_name (the callee, i.e. the seed symbol).
            # JOIN files to get the referencing file path and language for resolve_edge.
            # E4: also select e.kind and e.synthesized_by for provenance threading.
            sql = f"""
                SELECT e.source_name    AS neighbor,
                       e.target_name    AS parent,
                       e.target_name    AS edge_target_name,
                       f.path           AS ref_file_path,
                       f.language       AS language,
                       e.kind           AS edge_kind,
                       e.synthesized_by AS edge_synthesized_by
                FROM edges e
                JOIN files f ON f.id = e.file_id
                WHERE e.target_name IN ({placeholders})
                  AND e.source_name != e.target_name
            """
        else:
            # What do we depend on? → edges going out from us → target_name is dep.
            # edge_target_name = target_name (the callee, i.e. the neighbor).
            # E4: also select e.kind and e.synthesized_by for provenance threading.
            sql = f"""
                SELECT e.target_name    AS neighbor,
                       e.source_name    AS parent,
                       e.target_name    AS edge_target_name,
                       f.path           AS ref_file_path,
                       f.language       AS language,
                       e.kind           AS edge_kind,
                       e.synthesized_by AS edge_synthesized_by
                FROM edges e
                JOIN files f ON f.id = e.file_id
                WHERE e.source_name IN ({placeholders})
                  AND e.source_name != e.target_name
            """

        rows = conn.execute(sql, batch).fetchall()
        all_rows.extend(
            (
                row["neighbor"],
                row["parent"],
                row["edge_target_name"],
                row["ref_file_path"],
                row["language"],
                row["edge_kind"],
                row["edge_synthesized_by"],
            )
            for row in rows
        )

    return all_rows


# ── Public interface ───────────────────────────────────────────────────────────


def walk(
    conn: sqlite3.Connection,
    seeds: list[str],
    direction: str,
    max_depth: int,
    repo_root: Path | None = None,
) -> list[Reached]:
    """Walk the edge graph from seed symbols, returning reachable symbols.

    Args:
        conn:       Open SQLite connection (read-only semantics; no writes).
        seeds:      Symbol names to start from. Empty list -> returns [].
        direction:  "upstream" (callers/importers) or "downstream" (callees/importees).
        max_depth:  Maximum number of hops from any seed (1-based). Must be >= 1.
                    Clamping to [1, 10] is the CALLER's responsibility (impact.py does this).
        repo_root:  Repository root. When provided (and SEAM_IMPORT_RESOLUTION="on"),
                    each hop's confidence is resolved via resolve_edge() with full
                    import-promotion context (Phase 5 homonym fix).  When None or when
                    SEAM_IMPORT_RESOLUTION="off", falls back to name-count resolution.

    Returns:
        List of Reached dicts — one per reachable symbol (excluding the seeds themselves).
        Ordered by distance ascending, then name alphabetically.
        Empty list if seeds is empty, no edges, or all reachable are already seeds.

    Phase 5 resolved_by:
        When repo_root is provided and SEAM_IMPORT_RESOLUTION="on", each Reached entry
        has resolved_by populated from the final-hop's Resolution["resolved_by"]:
        "import" for import-promoted hops, "name-unique"/"name-collision"/"builtin"/
        "unresolved" for name-count-resolved hops, or None on any failure (degrade).

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

    # Load whole-index name-count map ONCE per walk call.
    # This single GROUP BY query is the foundation of whole-index confidence resolution.
    name_counts = load_name_counts(conn)

    # Determine whether full import-promotion is enabled for this walk call.
    # Both repo_root AND SEAM_IMPORT_RESOLUTION="on" are required.
    use_import_promotion = repo_root is not None and config.SEAM_IMPORT_RESOLUTION == "on"

    # Per-file import mapping cache: path -> list[ImportMapping].
    # Populated lazily on first access per file within this walk call.
    # This prevents N re-queries of load_import_mappings for every hop in large graphs.
    _import_cache: dict[str, list[ImportMapping]] = {}

    # Per-(file, target) resolution-result cache keyed on (ref_file_path, target_name).
    # A hub symbol (many callers from the same file) produces many edge rows with the
    # same pair, so without this cache each row would re-run the declaration-check and
    # proximity SELECT in resolve_edge — O(edge rows) DB queries instead of O(distinct pairs).
    _resolution_cache: dict[tuple[str, str], Resolution] = {}

    def _get_import_mappings(file_path: str) -> list[ImportMapping]:
        """Lazily load and cache import mappings for a referencing file path."""
        if file_path not in _import_cache:
            _import_cache[file_path] = load_import_mappings(conn, file_path)
        return _import_cache[file_path]

    # BFS state:
    #   visited: symbols already processed (prevents revisiting and cycles)
    #   current_frontier: set of (name, path_rank) for the current BFS level
    #   results: name -> (distance, best_path_rank, resolved_by_for_best_path, best_candidate,
    #                     kind_for_best_path, synthesized_by_for_best_path)
    seed_set = set(seeds)
    visited: set[str] = set(seeds)  # seeds are "visited" — we don't return them

    # Initial frontier: all seeds at EXTRACTED rank (perfect start — the seed IS the seed).
    current_frontier: list[tuple[str, int]] = [(s, 2) for s in seeds]

    # Results map: name -> (distance, best_path_rank, resolved_by, best_candidate, kind, synthesized_by)
    # best_candidate, kind, and synthesized_by all come from the final hop of the winning
    # (strongest-confidence) path — so all four provenance fields describe one coherent edge.
    results: dict[str, tuple[int, int, str | None, str | None, str, str | None]] = {}

    for depth in range(1, max_depth + 1):
        if not current_frontier:
            break

        # Build a map: parent_name -> path_rank so we can propagate confidence.
        parent_rank: dict[str, int] = {}
        for name, pr in current_frontier:
            if name not in parent_rank or pr > parent_rank[name]:
                parent_rank[name] = pr

        # Fetch one-hop neighbors for all symbols in the current frontier.
        # Returns (neighbor, parent, edge_target_name, ref_file_path, language, edge_kind, edge_synthesized_by).
        frontier_names = {name for name, _ in current_frontier}
        rows = _fetch_neighbors_with_parents(conn, frontier_names, direction)

        if not rows:
            break

        # next_frontier tracks: name -> (best_path_rank, resolved_by, best_candidate, kind, synthesized_by)
        next_frontier: dict[str, tuple[int, str | None, str | None, str, str | None]] = {}

        for neighbor, parent, edge_target_name, ref_file_path, language, edge_kind, edge_synth_by in rows:
            # Skip seeds — they are never returned as reachable.
            if neighbor in seed_set:
                continue

            # Resolve hop confidence using full Phase 5 resolver when available.
            # Falls back to plain name-count when import promotion is disabled/unavailable.
            # Resolution cache: (ref_file_path, edge_target_name) pairs recur
            # on hub symbols — cache results to collapse O(edge rows) → O(distinct pairs).
            hop_best_candidate: str | None = None
            if use_import_promotion and ref_file_path and language:
                cache_key = (ref_file_path, edge_target_name)
                if cache_key not in _resolution_cache:
                    import_mappings = _get_import_mappings(ref_file_path)
                    _resolution_cache[cache_key] = resolve_edge(
                        target_name=edge_target_name,
                        name_counts=name_counts,
                        language=language,
                        import_mappings=import_mappings,
                        referencing_file=Path(ref_file_path),
                        repo_root=repo_root,
                        conn=conn,
                        max_import_candidates=config.SEAM_MAX_IMPORT_CANDIDATES,
                        max_proximity_candidates=config.SEAM_PROXIMITY_MAX_CANDIDATES,
                    )
                resolution = _resolution_cache[cache_key]
                hop_confidence = resolution["confidence"]
                hop_resolved_by = resolution["resolved_by"]
                # best_candidate is only present on AMBIGUOUS hops (proximity tie-break);
                # captured here so it surfaces in the final Reached output.
                hop_best_candidate = resolution.get("best_candidate")
            else:
                # Fast-path: name-count only (SEAM_IMPORT_RESOLUTION="off" or no context).
                hop_confidence = resolve(edge_target_name, name_counts)
                hop_resolved_by = None

            # Propagate weakest-hop rule: path confidence = min(parent_rank, this_hop_rank).
            parent_path_rank = parent_rank.get(parent, _DEFAULT_RANK)
            hop_rank = _rank(hop_confidence)
            path_rank = _min_rank(parent_path_rank, hop_rank)

            if neighbor in visited:
                # Already at a shorter distance (BFS guarantees minimum-distance-first).
                continue

            # Keep the strongest path rank for this neighbor at this BFS level.
            # When replacing with a stronger path, update resolved_by, best_candidate,
            # kind, and synthesized_by from the winning hop — all four provenance fields
            # come from the same final hop of the strongest-confidence path.
            existing = next_frontier.get(neighbor)
            if existing is None or path_rank > existing[0]:
                next_frontier[neighbor] = (
                    path_rank,
                    hop_resolved_by,
                    hop_best_candidate,
                    edge_kind,
                    edge_synth_by,
                )

        # Commit next_frontier to results and advance visited set.
        new_frontier: list[tuple[str, int]] = []
        for name, (pr, resolved_by, best_candidate, kind, synth_by) in next_frontier.items():
            visited.add(name)
            results[name] = (depth, pr, resolved_by, best_candidate, kind, synth_by)
            new_frontier.append((name, pr))

        current_frontier = new_frontier

    # Convert results to Reached list, sorted by distance then name.
    # resolved_by, kind, and synthesized_by all reflect the FINAL hop of the winning
    # (strongest-confidence) path. confidence is the WEAKEST hop — so resolved_by='import'
    # can accompany confidence='AMBIGUOUS' on multi-hop paths where an earlier hop was
    # ambiguous. best_candidate is from the final hop's Resolution (only set for AMBIGUOUS).
    reached: list[Reached] = [
        Reached(
            name=name,
            distance=distance,
            confidence=_RANK_CONFIDENCE.get(best_rank, CONFIDENCE_INFERRED),
            resolved_by=resolved_by,
            best_candidate=best_candidate,
            kind=kind,
            synthesized_by=synth_by,
        )
        for name, (distance, best_rank, resolved_by, best_candidate, kind, synth_by) in results.items()
    ]
    reached.sort(key=lambda r: (r["distance"], r["name"]))

    logger.debug(
        "walk(seeds=%s, direction=%s, max_depth=%d, import_promotion=%s) -> %d reached",
        seeds,
        direction,
        max_depth,
        use_import_promotion,
        len(reached),
    )

    return reached
