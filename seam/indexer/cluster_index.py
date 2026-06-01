"""Clustering orchestration — bridge between pure analysis and persistence.

Reads the symbol graph from the DB, runs community detection, labels each
cluster, and writes the results in a single transaction.

This module sits in the indexer layer and is called by cli/main.py after the
full file-indexing loop completes (never per-file, always whole-graph).

Import hierarchy (enforced):
    indexer/cluster_index → analysis.clustering + analysis.cluster_naming + config
    analysis modules → pure, no DB writes
    cli → this module (not the other way)

Design decisions:
    - Reads all symbols + edges from the connection in one pass.
    - Calls detect_communities (pure) then label_cluster per community.
    - Writes in ONE transaction: DELETE clusters, INSERT clusters, UPDATE symbols.
    - NEVER raises: returns -1 on error (signals failure to CLI); any error logs
      a warning and leaves cluster_id NULL. Returns ≥0 on success.
    - Watcher does NOT call this — only seam init does.
    - Deterministic cluster DB IDs: communities are sorted by their smallest
      member name, then assigned IDs 1..N in that order. Same graph → same IDs.
    - SEAM_CLUSTER_MIN_SIZE is enforced: communities smaller than min_size are
      dropped; their symbols get cluster_id=NULL (unclustered).
    - clusters.size is set from the actual DB row count after write-back, not
      from the community node count (handles multi-row homonyms correctly).
    - Write-back is batched: one executemany per cluster (not one UPDATE per node).
"""

import logging
import sqlite3

from seam.analysis.cluster_naming import label_cluster
from seam.analysis.clustering import detect_communities

logger = logging.getLogger(__name__)


def index_clusters(
    conn: sqlite3.Connection,
    naming_mode: str = "deterministic",
    llm_api_key: str | None = None,
    llm_model: str | None = None,
    min_size: int = 1,
) -> int:
    """Detect and persist graph communities for all indexed symbols.

    Reads the full symbol+edge graph, partitions into communities, assigns
    deterministic labels, and stores everything in the clusters table and
    symbols.cluster_id column.

    Called by `seam init` after the indexing loop, NEVER by the watcher.

    Args:
        conn:         Open SQLite connection (must have write access).
        naming_mode:  "deterministic" (default) or "llm".
        llm_api_key:  API key for LLM naming (required if naming_mode="llm").
        llm_model:    LLM model name (optional; uses default if None).
        min_size:     Minimum community size to persist as a cluster.
                      Communities smaller than this get cluster_id=NULL.
                      Default 1 (all communities; effectively no filter).

    Returns:
        Number of clusters created (≥0). Returns -1 on error (never raises).

    WHY: Returning -1 (not 0) on error lets the CLI distinguish "zero clusters
    because the graph has no connected edges" from "clustering failed."
    Never raises so `seam init` can't be aborted by a clustering bug.
    """
    try:
        return _index_clusters_impl(conn, naming_mode, llm_api_key, llm_model, min_size)
    except Exception as exc:
        logger.warning(
            "cluster_index: failed to compute clusters (%s: %s) — "
            "all symbols will have cluster_id=NULL; run 'seam init' again to retry",
            type(exc).__name__,
            exc,
        )
        return -1


def _index_clusters_impl(
    conn: sqlite3.Connection,
    naming_mode: str,
    llm_api_key: str | None,
    llm_model: str | None,
    min_size: int,
) -> int:
    """Inner implementation. May raise — outer function is the guard.

    WHY separate function: the outer wrapper catches ALL exceptions and converts
    them to -1. Having a clean inner function makes the logic easier to reason
    about and test (tests can call the inner function directly if needed).
    """
    # ── Step 0: Always clear stale cluster state FIRST ────────────────────────
    # This must happen before ANY early-return so a previous successful run
    # is never left as a phantom when the current run finds nothing to cluster.
    # WHY: If we returned early without clearing, old cluster rows would persist
    # even though the symbol table is now empty — "ghost" clusters in list output.
    with conn:
        conn.execute("DELETE FROM clusters")
        conn.execute("UPDATE symbols SET cluster_id = NULL")

    # ── Step 1: Read all symbols ──────────────────────────────────────────────
    # We need: name (for the graph), file (for labeling), degree (for label heuristic)
    symbol_rows = conn.execute(
        """
        SELECT s.name, f.path AS file
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        ORDER BY s.name
        """
    ).fetchall()

    if not symbol_rows:
        logger.debug("cluster_index: no symbols in index, skipping clustering")
        return 0

    # Map: symbol name → file path (for labeling)
    # WHY: Multiple symbols can share a name (ambiguous); use first file path seen.
    name_to_file: dict[str, str] = {}
    for row in symbol_rows:
        name = row["name"]
        if name not in name_to_file:
            name_to_file[name] = row["file"]

    all_nodes = list(name_to_file.keys())

    # ── Step 2: Read all edges as undirected pairs ────────────────────────────
    edge_rows = conn.execute(
        "SELECT DISTINCT source_name, target_name FROM edges"
    ).fetchall()
    all_edges = [(row["source_name"], row["target_name"]) for row in edge_rows]

    # ── Step 3: Detect communities (pure function) ────────────────────────────
    community_map: dict[str, int] = detect_communities(all_nodes, all_edges)

    if not community_map:
        logger.debug("cluster_index: detect_communities returned empty map")
        return 0

    # ── Step 4: Group nodes by cluster ID and apply min_size filter ───────────
    # cluster_id (int from detect_communities) → list of node names
    raw_cluster_members: dict[int, list[str]] = {}
    for node, cid in community_map.items():
        raw_cluster_members.setdefault(cid, []).append(node)

    # Keep only communities with at least min_size distinct graph nodes.
    # WHY: min_size=2 (the default in config) kills pure singletons so `seam clusters`
    # shows functional areas, not every unconnected symbol in its own row.
    # Nodes in dropped communities get cluster_id=NULL (already cleared in Step 0).
    cluster_members: dict[int, list[str]] = {
        cid: members
        for cid, members in raw_cluster_members.items()
        if len(members) >= min_size
    }

    if not cluster_members:
        logger.debug(
            "cluster_index: all communities are below min_size=%d — no clusters persisted",
            min_size,
        )
        return 0

    # ── Step 5: Compute degree per node (for labeling heuristic) ─────────────
    # Degree = number of edges touching this symbol (undirected)
    node_degree: dict[str, int] = {n: 0 for n in all_nodes}
    for src, tgt in all_edges:
        if src in node_degree:
            node_degree[src] += 1
        if tgt in node_degree:
            node_degree[tgt] += 1

    # ── Step 6: Order communities deterministically for stable DB IDs ─────────
    # WHY (issue #1): AUTOINCREMENT IDs climb on every DELETE+INSERT, so IDs
    # drift across re-runs even when the graph is unchanged. Instead we INSERT
    # with explicit IDs 1..N. The ordering key is the lexicographically smallest
    # member name of each community — the same key used internally by detect_communities
    # to assign stable algorithm IDs, so this ordering is doubly stable.
    ordered_algo_ids: list[int] = sorted(
        cluster_members.keys(),
        key=lambda cid: min(cluster_members[cid]),  # min member name → deterministic
    )

    # ── Step 7: Compute labels for each persisted community ───────────────────
    cluster_labels: dict[int, tuple[str, str]] = {}
    for algo_cid in ordered_algo_ids:
        members_info = [
            {
                "name": name,
                "file": name_to_file.get(name, ""),
                "degree": node_degree.get(name, 0),
            }
            for name in sorted(cluster_members[algo_cid])  # sorted for determinism
        ]
        label, naming_source = label_cluster(
            members_info,
            naming_mode=naming_mode,
            api_key=llm_api_key,
            model=llm_model,
        )
        cluster_labels[algo_cid] = (label, naming_source)

    # ── Step 8: Persist in one transaction with explicit stable IDs ───────────
    # WHY single transaction: if any INSERT/UPDATE fails the rollback leaves
    # the DB in a consistent state (all cluster_id=NULL from Step 0 clear).
    with conn:
        # Map: algorithm cluster ID → stable DB cluster ID (1-based, ordered by min member)
        algo_to_db_id: dict[int, int] = {}
        for db_id, algo_cid in enumerate(ordered_algo_ids, start=1):
            label, naming_source = cluster_labels[algo_cid]
            # Insert with explicit ID so IDs are identical across re-runs.
            # Size is a placeholder (0) — updated to actual count after write-back (issue #3).
            conn.execute(
                "INSERT INTO clusters (id, label, size, naming_source) VALUES (?, ?, 0, ?)",
                (db_id, label, naming_source),
            )
            algo_to_db_id[algo_cid] = db_id

        # Batch write-back: one executemany per cluster instead of one UPDATE per node.
        # WHY (issue #9): a 100k-symbol repo would run 100k individual UPDATEs without this.
        # Each cluster's members share the same db_id, so we group and write at once.
        for algo_cid, db_id in algo_to_db_id.items():
            member_names = cluster_members[algo_cid]
            # executemany issues one call per row but SQLite batches them in one round-trip
            conn.executemany(
                "UPDATE symbols SET cluster_id = ? WHERE name = ?",
                [(db_id, name) for name in member_names],
            )

        # Fix cluster.size to actual DB member count (issue #3).
        # WHY: UPDATE WHERE name=? stamps EVERY row with that name, so if the same
        # symbol name appears in two files, both rows get the cluster_id. The node
        # count from community detection counts unique NAMES, not rows. We must
        # re-count from the DB to get the true member row count per cluster.
        for db_id in algo_to_db_id.values():
            actual_size = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE cluster_id = ?", (db_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE clusters SET size = ? WHERE id = ?", (actual_size, db_id)
            )

    n_clusters = len(cluster_members)

    # ── Step 9: Surface LLM-naming fallback stats (issue #8) ─────────────────
    # When LLM naming was requested, report how many clusters fell back to deterministic.
    # This is a read-only post-write query; any error is silently ignored.
    llm_summary: str | None = None
    if naming_mode == "llm":
        try:
            source_rows = conn.execute(
                "SELECT naming_source, COUNT(*) AS n FROM clusters GROUP BY naming_source"
            ).fetchall()
            source_counts = {r["naming_source"]: r["n"] for r in source_rows}
            llm_count = source_counts.get("llm", 0)
            det_count = source_counts.get("deterministic", 0)
            fallback = det_count
            if fallback > 0:
                llm_summary = f"{llm_count}/{n_clusters} llm-named, {fallback} fell back to deterministic"
            else:
                llm_summary = f"{llm_count}/{n_clusters} llm-named"
        except Exception:
            pass  # non-critical summary; don't let this break the return value

    logger.info(
        "cluster_index: %d cluster(s) computed (%s naming)%s",
        n_clusters,
        naming_mode,
        f" [{llm_summary}]" if llm_summary else "",
    )
    return n_clusters


def get_llm_naming_summary(conn: sqlite3.Connection) -> str | None:
    """Return a human-readable LLM naming summary string, or None if not applicable.

    Reads the clusters table and returns a string like
    "naming: llm requested, 3/10 fell back to deterministic" for use by the CLI.

    Returns None when the clusters table is empty or inaccessible.
    WHY: Exposed as a separate callable so cli/main.py can print the summary after
    index_clusters() without duplicating the query logic.
    """
    try:
        rows = conn.execute(
            "SELECT naming_source, COUNT(*) AS n FROM clusters GROUP BY naming_source"
        ).fetchall()
        if not rows:
            return None
        source_counts = {r["naming_source"]: r["n"] for r in rows}
        total = sum(source_counts.values())
        llm_n = source_counts.get("llm", 0)
        det_n = source_counts.get("deterministic", 0)
        if det_n > 0:
            return f"naming: llm requested, {det_n}/{total} fell back to deterministic"
        return f"naming: llm ({llm_n}/{total} named by LLM)"
    except Exception:
        return None
