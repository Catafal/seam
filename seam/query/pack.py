"""Context-Pack primitive — Phase 6.

Single public function:
    context_pack(conn, symbol_name) -> ContextPack | None

Orchestrates EXISTING read primitives into one ready-to-paste bundle:
  - target:        engine.context() verbatim (full 360-degree ContextResult)
  - callers:       1-hop callers enriched to NeighborRef (name, file, line, kind, signature)
  - callees:       1-hop callees enriched to NeighborRef
  - why:           comments.why(symbol=...) results, capped
  - cluster_peers: taken directly from target (no extra query)
  - truncated:     {callers, callees, comments} counts of entries dropped by caps

WHY a new module instead of extending engine.py:
  pack.py is deliberately thin orchestration. Keeping it separate makes it clear
  that it adds no extraction logic — it only composes existing primitives.
  engine.py is already large; mixing in cap/truncation logic would obscure the
  core search/query/context path.

Caps are config-driven from seam/config.py:
  SEAM_PACK_NEIGHBOR_LIMIT  — global max per list (callers, callees)
  SEAM_PACK_PER_FILE_CAP    — max entries from any single file (diversity)
  SEAM_PACK_MAX_COMMENTS    — max WHY comments included
"""

import logging
import sqlite3
from typing import TypedDict

import seam.config as config
from seam.analysis.rwr import personalized_pagerank
from seam.analysis.testpaths import is_test_file
from seam.query.comments import CommentHit
from seam.query.comments import why as comments_why
from seam.query.engine import ContextResult, decode_enrichment_fields
from seam.query.engine import context as engine_context
from seam.query.names import edge_match_names as _edge_match_names

logger = logging.getLogger(__name__)

# WHY 900 (not 999): SQLite's hard host-parameter limit is 999 by default.
# Using 900 gives a safety margin for other bindings in the same statement and
# avoids breaking on SQLite builds compiled with a lower SQLITE_MAX_VARIABLE_NUMBER.
# This is a module constant (not a config knob) because it reflects a SQLite
# implementation constraint the user cannot influence at runtime.
_SQLITE_MAX_IN_PARAMS = 900


# ── TypedDicts ────────────────────────────────────────────────────────────────


class NeighborRef(TypedDict):
    """An enriched 1-hop neighbor entry.

    The five PRD-required fields are: name, file, line, kind, signature.
    We include all Phase 4 enrichment fields for null-contract consistency
    with other tools (signature/decorators/is_exported/visibility/qualified_name).
    Fields are None for pre-v5 rows or when extraction was not available.
    """

    name: str
    file: str
    line: int
    kind: str
    signature: str | None
    decorators: list[str]
    is_exported: bool | None
    visibility: str | None
    qualified_name: str | None


class TruncatedCounts(TypedDict):
    """Counts of entries dropped by caps in each list."""

    callers: int
    callees: int
    comments: int


class ContextPack(TypedDict):
    """A ready-to-paste context bundle for a symbol.

    Returned by context_pack(). None means the symbol was not found.

    Fields:
        target:        Full 360-degree ContextResult from engine.context().
        callers:       Enriched 1-hop callers (NeighborRef list, capped).
        callees:       Enriched 1-hop callees (NeighborRef list, capped).
        why:           WHY/HACK/NOTE/TODO/FIXME comments attached to the symbol (capped).
        cluster_peers: Functional-area peers taken directly from target.cluster_peers.
        truncated:     Counts of entries dropped by caps (callers, callees, comments).
    """

    target: ContextResult
    callers: list[NeighborRef]
    callees: list[NeighborRef]
    why: list[CommentHit]
    cluster_peers: list[str]
    truncated: TruncatedCounts


# ── Internal helpers ──────────────────────────────────────────────────────────


def _fetch_local_subgraph(
    conn: sqlite3.Connection,
    seeds: set[str],
    *,
    max_depth: int,
    max_nodes: int,
) -> dict[str, set[str]]:
    """Collect a bounded UNDIRECTED local subgraph around `seeds` from the edges table.

    Depth-capped, node-capped BFS: from the current frontier, fetch every edge whose
    source_name OR target_name is in the frontier, add both endpoints as undirected
    neighbours, and advance the frontier — stopping at `max_depth` hops or once `max_nodes`
    distinct nodes have been seen. This keeps the RWR walk O(subgraph), never a whole-graph load.

    `seeds` is the symbol's edge_match_names (qualified + bare) so the walk is rooted at both
    storage forms. Returns {name: set(neighbour names)} (always includes the seeds). Returns the
    seed-only adjacency on any DB error — RWR then degrades to no ranking, never raises.
    """
    adjacency: dict[str, set[str]] = {s: set() for s in seeds}
    expanded: set[str] = set()  # frontier nodes already queried (avoid re-expanding)
    frontier: set[str] = set(seeds)
    # Chunk the IN(...) to respect SQLite's host-parameter limit. The same name set is bound
    # twice (source_name + target_name), so each chunk may use up to 2*len(chunk) params.
    half = max(1, _SQLITE_MAX_IN_PARAMS // 2)
    try:
        for _ in range(max(0, max_depth)):
            if not frontier or len(adjacency) >= max_nodes:
                break
            expanded |= frontier
            next_frontier: set[str] = set()
            frontier_list = sorted(frontier)
            for start in range(0, len(frontier_list), half):
                chunk = frontier_list[start : start + half]
                ph = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT source_name, target_name FROM edges "
                    f"WHERE source_name IN ({ph}) OR target_name IN ({ph})",
                    chunk + chunk,
                ).fetchall()
                for row in rows:
                    s, t = row["source_name"], row["target_name"]
                    if s == t:
                        continue  # self-loop: irrelevant to ranking
                    # Undirected: record both directions.
                    adjacency.setdefault(s, set()).add(t)
                    adjacency.setdefault(t, set()).add(s)
                    for endpoint in (s, t):
                        if endpoint not in expanded:
                            next_frontier.add(endpoint)
                if len(adjacency) >= max_nodes:
                    break  # hard stop on hub fan-out within a single depth level
            frontier = next_frontier
    except Exception:  # noqa: BLE001 — read path must never raise.
        logger.debug("_fetch_local_subgraph: DB error for seeds=%r", seeds, exc_info=True)
        return {s: set() for s in seeds}
    return adjacency


def _neighbor_scores(conn: sqlite3.Connection, seed: str) -> dict[str, float]:
    """Personalized-PageRank relevance scores for nodes near `seed` (E3).

    Roots the walk at the seed's edge_match_names (qualified + bare) so neighbours reachable via
    either storage form are scored against the same logical seed. Returns {} when ranking is
    disabled (callers then fall back to min_id order) or on any failure. Never raises.
    """
    if config.SEAM_PACK_RELEVANCE_RANK != "on":
        return {}
    try:
        try:
            seeds = set(_edge_match_names(conn, seed)) or {seed}
        except Exception:  # noqa: BLE001
            seeds = {seed}
        adjacency = _fetch_local_subgraph(
            conn, seeds,
            max_depth=config.SEAM_RWR_MAX_DEPTH,
            max_nodes=config.SEAM_RWR_MAX_NODES,
        )
        return personalized_pagerank(adjacency, seeds)
    except Exception:  # noqa: BLE001 — ranking is best-effort; never break the pack.
        logger.debug("_neighbor_scores: degraded for seed=%r", seed, exc_info=True)
        return {}


def _enrich_neighbors(
    conn: sqlite3.Connection,
    names: list[str],
    *,
    neighbor_limit: int,
    per_file_cap: int,
    scores: dict[str, float] | None = None,
) -> tuple[list[NeighborRef], int]:
    """Enrich a list of neighbor names to NeighborRef dicts and apply caps.

    Returns (enriched_list, truncated_count).

    truncated_count counts ONLY entries dropped by caps (global limit or per-file
    cap). Names with no symbols row (external/unindexed symbols) are silently
    skipped and NOT counted — a higher cap never retrieves them so counting them
    would mislead agents into running fallback queries for phantom symbols.

    Algorithm:
    1. Deduplicate the input names while preserving order.
    2. Batch-lookup all distinct names in chunked WHERE name IN (...) queries.
       Chunks are at most _SQLITE_MAX_IN_PARAMS wide to avoid SQLite's hard
       host-parameter limit (default 999). Tie-break: first match per name
       by lowest symbol id (mirrors context()).
    3. Build all_refs in min_id order (ORDER BY min_id in the SQL, preserved
       via dict insertion order).
    4. Apply per-file cap in min_id order.
    5. Apply global neighbor_limit.
    6. Count dropped entries (cap drops only, NOT unindexed skips).

    WHY batch: caller/callee lists can be large for hot utilities. N+1 queries
    (one context() call per name) would be O(n) round-trips. The batched IN(...)
    is O(1) round-trips regardless of list size.

    WHY per-file before global: the PRD spec (§4.5a) requires per-file cap
    applied first so the global limit sees an already-diverse list.
    """
    if not names:
        return [], 0

    # Step 1: deduplicate while preserving order
    seen: set[str] = set()
    unique_names: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique_names.append(n)

    if not unique_names:
        return [], 0

    # Step 2: batch-lookup in chunks of _SQLITE_MAX_IN_PARAMS.
    # WHY chunks: SQLite's SQLITE_MAX_VARIABLE_NUMBER is 999 by default.
    # One hot utility can have thousands of callers; chunking prevents
    # OperationalError that would otherwise be swallowed by the context_pack
    # except block, silently returning empty callers/callees for the busiest symbols.
    # We select ALL Phase 4 enrichment fields for null-contract consistency.
    sql_template = """
        SELECT
            s.name,
            f.path        AS file,
            s.start_line  AS line,
            s.kind,
            s.signature,
            s.decorators,
            s.is_exported,
            s.visibility,
            s.qualified_name,
            MIN(s.id)     AS min_id
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name IN ({placeholders})
        GROUP BY s.name
        ORDER BY min_id
    """

    # Build a lookup: name -> NeighborRef (first match per name, lowest id wins).
    # Each name appears in exactly one chunk, and each chunk is ORDER BY min_id, so
    # ordering is exact min_id within a chunk. With ≤_SQLITE_MAX_IN_PARAMS distinct
    # names (the overwhelmingly common case) there is a single chunk and the global
    # order is exact; across multiple chunks the order is still fully deterministic
    # for a given index (chunk slice order is fixed), just not globally min_id-sorted.
    name_to_ref: dict[str, NeighborRef] = {}

    for chunk_start in range(0, len(unique_names), _SQLITE_MAX_IN_PARAMS):
        chunk = unique_names[chunk_start : chunk_start + _SQLITE_MAX_IN_PARAMS]
        placeholders = ",".join("?" * len(chunk))
        sql = sql_template.format(placeholders=placeholders)
        rows = conn.execute(sql, chunk).fetchall()

        for row in rows:
            # Reuse the shared decode helper from engine.py — single source of truth
            # for 0/1/NULL→bool and JSON TEXT→list[str] semantics.
            decorators, is_exported = decode_enrichment_fields(row)

            name_to_ref[row["name"]] = NeighborRef(
                name=row["name"],
                file=row["file"],
                line=row["line"],
                kind=row["kind"],
                signature=row["signature"],
                decorators=decorators,
                is_exported=is_exported,
                visibility=row["visibility"],
                qualified_name=row["qualified_name"],
            )

    # Log unindexed skips at DEBUG level for observability.
    # These are NOT cap drops — they are edges to external/unindexed symbols.
    unindexed_count = len(unique_names) - len(name_to_ref)
    if unindexed_count > 0:
        logger.debug(
            "_enrich_neighbors: skipped %d unindexed name(s) (external symbols, not in index)",
            unindexed_count,
        )

    # Step 3: Build all_refs in insertion order (= min_id order within the single
    # common-case chunk; deterministic across chunks). dict insertion order is
    # preserved (Python 3.7+), so values() reflects the Step-2 ordering.
    all_refs: list[NeighborRef] = list(name_to_ref.values())

    # Step 3b (E3): relevance-rank before the caps so the kept N are the most relevant neighbors,
    # not the lowest-symbol-id ones. Key = (-PPR score, is_test, min_id):
    #   - higher personalized-PageRank score (relevance to the seed) first;
    #   - production before test on an equal score (usability tie-break);
    #   - min_id last — Python's stable sort preserves the Step-3 order within full ties, so an
    #     empty `scores` map (ranking disabled or RWR degraded) is a byte-identical no-op.
    if scores:
        all_refs.sort(
            key=lambda r: (-scores.get(r["name"], 0.0), is_test_file(r["file"]))
        )

    # Step 4: Per-file cap — count entries per file; drop once cap is hit.
    # Iterate in min_id order (already correct from Step 3).
    file_counts: dict[str, int] = {}
    capped_refs: list[NeighborRef] = []
    for ref in all_refs:
        file_key = ref["file"]
        count = file_counts.get(file_key, 0)
        if count < per_file_cap:
            capped_refs.append(ref)
            file_counts[file_key] = count + 1
        # else: silently drop (counted in truncated below)

    # Step 5: Global limit
    final = capped_refs[:neighbor_limit]

    # Step 6: truncated counts ONLY cap drops (per-file + global).
    # Unindexed skips are NOT counted — a higher cap never retrieves them.
    truncated = (len(all_refs) - len(capped_refs)) + (len(capped_refs) - len(final))

    return final, truncated


# ── Public API ────────────────────────────────────────────────────────────────


def context_pack(
    conn: sqlite3.Connection,
    symbol_name: str,
) -> ContextPack | None:
    """Build a ready-to-paste context bundle for a symbol.

    Returns None when the symbol is not in the index (same contract as context()).
    Every sub-lookup degrades to empty rather than raising — NEVER raises.

    Args:
        conn:        Open SQLite connection to the Seam index (read-only).
        symbol_name: The symbol name to look up.

    Returns:
        A ContextPack TypedDict, or None if the symbol is not found.
    """
    # Step 1: Fetch full 360-degree target context.
    # context() returns None for unknown symbols — propagate that contract.
    try:
        target = engine_context(conn, symbol_name)
    except Exception:
        # engine.context() should never raise for valid DB, but degrade gracefully.
        logger.warning("context_pack: engine.context() raised for %r", symbol_name, exc_info=True)
        return None

    if target is None:
        return None

    # Step 2: Gather caller/callee names from target (bare string lists).
    caller_names: list[str] = target["callers"]
    callee_names: list[str] = target["callees"]

    # Step 3: Enrich callers and callees in batched lookups.
    # Degrade gracefully on any DB error (empty list, not a crash).
    neighbor_limit = config.SEAM_PACK_NEIGHBOR_LIMIT
    per_file_cap = config.SEAM_PACK_PER_FILE_CAP

    # E3: compute personalized-PageRank relevance scores ONCE (one bounded subgraph + one walk,
    # personalized to the seed) and reuse for both caller and callee ranking. Empty when ranking
    # is disabled or RWR degrades → enrichment falls back to min_id order. Never raises.
    scores = _neighbor_scores(conn, target["symbol"])

    try:
        enriched_callers, callers_dropped = _enrich_neighbors(
            conn, caller_names,
            neighbor_limit=neighbor_limit,
            per_file_cap=per_file_cap,
            scores=scores,
        )
    except Exception:
        logger.warning("context_pack: caller enrichment failed for %r", symbol_name, exc_info=True)
        enriched_callers, callers_dropped = [], 0

    try:
        enriched_callees, callees_dropped = _enrich_neighbors(
            conn, callee_names,
            neighbor_limit=neighbor_limit,
            per_file_cap=per_file_cap,
            scores=scores,
        )
    except Exception:
        logger.warning("context_pack: callee enrichment failed for %r", symbol_name, exc_info=True)
        enriched_callees, callees_dropped = [], 0

    # Step 4: Fetch WHY comments, apply comment cap.
    max_comments = config.SEAM_PACK_MAX_COMMENTS
    try:
        all_comments = comments_why(conn, symbol=symbol_name)
    except Exception:
        logger.warning("context_pack: comments.why() raised for %r", symbol_name, exc_info=True)
        all_comments = []

    comments_dropped = max(0, len(all_comments) - max_comments)
    capped_comments = all_comments[:max_comments]

    # Step 5: Cluster peers come directly from target (no extra query needed).
    cluster_peers: list[str] = target.get("cluster_peers") or []

    return ContextPack(
        target=target,
        callers=enriched_callers,
        callees=enriched_callees,
        why=capped_comments,
        cluster_peers=cluster_peers,
        truncated=TruncatedCounts(
            callers=callers_dropped,
            callees=callees_dropped,
            comments=comments_dropped,
        ),
    )
