"""Filesystem reconcile for Seam — the engine behind `seam sync`.

Performs a mtime-pre-filter → SHA-1-confirm reconcile of the existing index
against the current on-disk state, then runs a full cluster recompute gated
on whether the graph actually changed.

Import hierarchy:
    cli.main → indexer.sync → indexer.pipeline, indexer.db, indexer.cluster_index
    (never the reverse — this module must not import from cli)

Design decisions:
    - Reconcile is filesystem-based (mtime + SHA-1), NOT git-based.
      Catches non-git repos AND committed changes from pull/checkout/merge.
    - mtime pre-filter: if stored mtime == on-disk st_mtime → unchanged, no read.
    - Hash confirm: if mtime differs but hash matches → unchanged, no re-index.
      (touch-without-content-change pays one hash per sync, never a re-index.)
    - Cluster recompute is FULL (not incremental) and gated on graph_changed.
      One new edge can re-partition unrelated communities → incremental is wrong.
    - Per-file errors are swallowed by index_one_file (returns None → skipped).
      sync() itself never raises for per-file issues.
    - Logging: INFO for the final summary line, DEBUG for per-file decisions.
"""

import logging
import sqlite3
from pathlib import Path
from typing import TypedDict

from seam.indexer.cluster_index import index_clusters
from seam.indexer.db import delete_file
from seam.indexer.pipeline import index_one_file, sha1, walk_project

logger = logging.getLogger(__name__)


class SyncResult(TypedDict):
    """Outcome of one sync() call.

    Keys mirror the PRD §Implementation Decisions shape exactly.
    cluster_count is None when clusters were NOT recomputed this sync.
    """

    added: int              # files present on disk, absent from index → indexed
    modified: int           # tracked files whose content hash changed → re-indexed
    removed: int            # tracked files no longer on disk → deleted
    unchanged: int          # tracked files skipped (mtime match or hash match)
    skipped: int            # files index_one_file declined (unsupported/binary/error)
    graph_changed: bool     # (added + modified + removed) > 0
    clusters_recomputed: bool  # whether index_clusters ran this sync
    cluster_count: int | None  # result of index_clusters when it ran, else None


def _load_tracked(conn: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    """Load all tracked files from the DB as {abs_path: (mtime, file_hash)}.

    WHY: one bulk read at reconcile start is cheaper than per-file queries.
    Returns absolute string paths as keys so comparison with walk_project output
    (which also returns absolute paths) is direct.
    """
    rows = conn.execute("SELECT path, mtime, file_hash FROM files").fetchall()
    return {row["path"]: (float(row["mtime"]), row["file_hash"]) for row in rows}


def sync(
    conn: sqlite3.Connection,
    root: Path,
    *,
    recompute_clusters: bool = True,
    force_clusters: bool = False,
    naming_mode: str,
    llm_api_key: str | None,
    llm_model: str | None,
    min_size: int,
) -> SyncResult:
    """Reconcile the existing index against the current on-disk state.

    Algorithm (per PRD):
      1. Load tracked {abs_path: (mtime, hash)} from files table.
      2. walk_project(root) → current indexable files.
      3. For each current file:
           - Not in tracked → index_one_file → added (None → skipped).
           - In tracked, st_mtime == stored mtime → unchanged (no read).
           - In tracked, mtime differs, read+sha1 → if hash same → unchanged
             (touched but not changed); if hash differs → index_one_file → modified
             (None → skipped).
      4. For each tracked path absent from current set → delete_file → removed.
      5. Gate: graph_changed = (added + modified + removed) > 0.
         Run index_clusters iff recompute_clusters and (graph_changed or force_clusters).

    Per-file errors: index_one_file already returns None on any per-file failure;
    sync counts them in `skipped` and continues. DB-level errors propagate to the CLI.

    Args:
        conn:               Open write-access SQLite connection.
        root:               Project root to reconcile against.
        recompute_clusters: Master switch — when False, clusters are never recomputed.
        force_clusters:     Override gate — recompute even when graph_changed is False.
        naming_mode:        Passed verbatim to index_clusters (from config).
        llm_api_key:        Passed verbatim to index_clusters (from config).
        llm_model:          Passed verbatim to index_clusters (from config).
        min_size:           Passed verbatim to index_clusters (from config).

    Returns:
        SyncResult with counts and gate outcomes.
    """
    added = 0
    modified = 0
    removed = 0
    unchanged = 0
    skipped = 0

    # ── Step 1: Load current DB state ─────────────────────────────────────────
    # One bulk read is much cheaper than per-file queries in the reconcile loop.
    tracked = _load_tracked(conn)

    # ── Step 2: Walk on-disk files ────────────────────────────────────────────
    current_paths = walk_project(root)
    # Build a set of abs-string paths for fast "is this tracked?" lookup.
    current_set = {str(p) for p in current_paths}

    # ── Step 3: Reconcile each on-disk file ───────────────────────────────────
    for path in current_paths:
        abs_str = str(path)

        if abs_str not in tracked:
            # New file — not in index at all → index it.
            logger.debug("sync: NEW %s", path)
            result = index_one_file(conn, path)
            if result is None:
                skipped += 1
                logger.debug("sync: SKIPPED (index failed) %s", path)
            else:
                added += 1
        else:
            stored_mtime, stored_hash = tracked[abs_str]

            # Cheap mtime pre-filter: if mtime matches → definitely unchanged.
            try:
                disk_mtime = path.stat().st_mtime
            except OSError as exc:
                # File disappeared between walk and stat — treat as skipped.
                # It will show as removed in step 4 on the next sync.
                logger.debug("sync: stat failed for %s: %s — skipping", path, exc)
                skipped += 1
                continue

            if disk_mtime == stored_mtime:
                # mtime matches → unchanged without reading content.
                logger.debug("sync: UNCHANGED (mtime match) %s", path)
                unchanged += 1
                continue

            # mtime differs — need to read and hash to decide.
            try:
                content = path.read_bytes()
            except OSError as exc:
                logger.debug("sync: read failed for %s: %s — skipping", path, exc)
                skipped += 1
                continue

            disk_hash = sha1(content)

            if disk_hash == stored_hash:
                # mtime changed but content is identical (e.g. `touch`).
                # WHY: we do NOT update stored mtime here — that would cost
                # a write for zero benefit. On the next sync this file re-hashes
                # once, still no re-index. Documented accepted inefficiency.
                logger.debug("sync: UNCHANGED (hash match, touch) %s", path)
                unchanged += 1
            else:
                # Content actually changed → re-index.
                logger.debug("sync: MODIFIED %s", path)
                result = index_one_file(conn, path)
                if result is None:
                    skipped += 1
                    logger.debug("sync: SKIPPED (re-index failed) %s", path)
                else:
                    modified += 1

    # ── Step 4: Delete tracked files no longer on disk ────────────────────────
    # Double-check the file is ACTUALLY gone before deleting (CodeGraph's
    # existsSync guard, roadmap §6.1). A tracked path can be absent from the
    # walk set for benign reasons — a transient FS/permission hiccup, a
    # wrong-directory sync, or a --db-dir pointed at another project's index.
    # Trusting the walk set alone would let any of those silently wipe the
    # entire index. We only remove a file once it genuinely no longer exists.
    for abs_str in tracked:
        if abs_str not in current_set and not Path(abs_str).exists():
            logger.debug("sync: REMOVED %s", abs_str)
            delete_file(conn, Path(abs_str))
            removed += 1

    # ── Step 5: Gate cluster recompute ────────────────────────────────────────
    graph_changed = (added + modified + removed) > 0

    clusters_recomputed = False
    cluster_count: int | None = None

    if recompute_clusters and (graph_changed or force_clusters):
        logger.debug(
            "sync: running index_clusters (graph_changed=%s, force=%s)",
            graph_changed,
            force_clusters,
        )
        cluster_count = index_clusters(
            conn,
            naming_mode=naming_mode,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            min_size=min_size,
        )
        # index_clusters returns -1 on failure (it never raises). "Recomputed"
        # must mean "clusters were successfully refreshed" — a failed pass leaves
        # them stale, so do NOT claim success. The -1 sentinel is preserved in
        # cluster_count so callers can tell "ran but failed" (-1) apart from
        # "did not run" (None), and the CLI surfaces it as a visible failure
        # (mirroring `seam init`'s clustering_failed guard).
        clusters_recomputed = cluster_count >= 0

    logger.info(
        "sync: added=%d modified=%d removed=%d unchanged=%d skipped=%d "
        "graph_changed=%s clusters_recomputed=%s cluster_count=%s",
        added,
        modified,
        removed,
        unchanged,
        skipped,
        graph_changed,
        clusters_recomputed,
        cluster_count,
    )

    return SyncResult(
        added=added,
        modified=modified,
        removed=removed,
        unchanged=unchanged,
        skipped=skipped,
        graph_changed=graph_changed,
        clusters_recomputed=clusters_recomputed,
        cluster_count=cluster_count,
    )
