"""Edge-synthesis orchestration — bridge between pure engine and DB persistence.

Reads symbols+edges from the DB, calls synthesize_edges() (pure engine), and writes
the synthesized edges in a single transaction.

This module sits in the indexer layer and is called by cli/main.py after clustering
(never per-file, always whole-graph). It mirrors the pattern of cluster_index.py
exactly: a public never-raising wrapper around an inner implementation that may raise.

Import hierarchy (enforced):
    indexer/synthesis_index → analysis.synthesis + config
    analysis modules → pure, no DB writes
    cli → this module (not the other way)

Design decisions:
  - Reads all symbols + edges from the connection in one pass.
  - Calls synthesize_edges (pure) then writes in ONE transaction.
  - Synthesized edges are stored under a special synthetic "file" row (':synthesis:')
    that is NOT a real file on disk. This avoids relaxing the edges FK constraint while
    keeping edges.file_id a valid FK reference. The synthetic row is upserted once and
    is never deleted by the normal file-cascade mechanism (its path is ':synthesis:').
  - ON EACH CALL: deletes ALL existing synthesized edges first (WHERE synthesized_by
    IS NOT NULL), then re-inserts fresh ones. This ensures idempotency: calling
    index_synthesis twice yields the same set of edges without duplicates.
  - NEVER raises: returns -1 on error (signals failure to CLI); any error logs
    a warning. Returns 0 when enabled=False. Returns count >= 0 on success.
  - SEAM_EDGE_SYNTHESIS=off: returns 0 immediately, writes nothing.
  - Watcher does NOT call this — only seam init and seam sync do.
"""

import logging
import sqlite3

from seam.analysis.synthesis import synthesize_edges

logger = logging.getLogger(__name__)

# Sentinel file path for synthesized edges — not a real on-disk file.
# Using a colon-prefixed path to ensure it can never collide with a real repo path.
_SYNTHESIS_FILE_PATH = ":synthesis:"


def _ensure_synthesis_file_row(conn: sqlite3.Connection) -> int:
    """Upsert the synthetic file row and return its id.

    Synthesized edges are not file-scoped, but edges.file_id has a FK to files(id).
    We use a permanent synthetic file row (path=':synthesis:') that is never
    cascade-deleted by normal re-indexing. This preserves FK integrity without
    relaxing constraints or adding nullable columns.

    Returns the file_id of the synthetic row.
    """
    conn.execute(
        """
        INSERT INTO files (path, language, file_hash, mtime, indexed_at)
        VALUES (?, '', '', 0.0, 0.0)
        ON CONFLICT(path) DO NOTHING
        """,
        (_SYNTHESIS_FILE_PATH,),
    )
    row = conn.execute(
        "SELECT id FROM files WHERE path = ?", (_SYNTHESIS_FILE_PATH,)
    ).fetchone()
    return row[0]


def index_synthesis(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    fanout_cap: int,
) -> int:
    """Synthesize dynamic-dispatch edges and persist them to the DB.

    Reads the full symbol+edge graph, calls the synthesis engine, and stores
    the resulting synthesized edges in a single transaction.

    Called by `seam init` after clustering and by `seam sync` when the graph
    changed (or when --force-synthesis is passed). NEVER called by the watcher.

    Args:
        conn:        Open SQLite connection (must have write access).
        enabled:     When False, skip synthesis entirely and return 0 (SEAM_EDGE_SYNTHESIS=off).
        fanout_cap:  Per-channel fan-out cap passed to synthesize_edges (from config).

    Returns:
        Number of synthesized edges written (>= 0). Returns -1 on error (never raises).
        Returns 0 when enabled=False.

    WHY -1 (not 0) on error: lets the CLI distinguish "zero synthesized edges because
    no interface-override patterns exist" from "synthesis failed." Same contract as
    index_clusters (which also returns -1 on failure, never raises).
    """
    if not enabled:
        logger.debug("synthesis_index: SEAM_EDGE_SYNTHESIS=off — skipping synthesis pass")
        return 0

    try:
        return _index_synthesis_impl(conn, fanout_cap)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "synthesis_index: failed to synthesize edges (%s: %s) — "
            "synthesized edges will be absent; run 'seam init' again to retry",
            type(exc).__name__,
            exc,
        )
        return -1


def _index_synthesis_impl(conn: sqlite3.Connection, fanout_cap: int) -> int:
    """Inner implementation. May raise — outer function is the guard.

    WHY separate function: the outer wrapper catches ALL exceptions and converts
    them to -1. Having a clean inner function makes the logic easier to reason
    about and test (tests can call the inner function directly if needed).
    """
    # ── Step 0: Ensure the synthetic file row exists ──────────────────────────
    # Must happen before the transaction since it is its own upsert.
    # The ':synthesis:' row is permanent — it is NOT deleted between runs.
    with conn:
        synth_file_id = _ensure_synthesis_file_row(conn)

    # ── Step 1: Read all symbols ──────────────────────────────────────────────
    # We need: name (for qualified method lookup), kind (to identify methods vs classes).
    symbol_rows = conn.execute(
        """
        SELECT s.name, s.kind
        FROM symbols s
        ORDER BY s.name
        """
    ).fetchall()

    if not symbol_rows:
        logger.debug("synthesis_index: no symbols in index, skipping synthesis")
        # Still delete stale synthesized edges (from a previous run when symbols existed).
        with conn:
            conn.execute("DELETE FROM edges WHERE synthesized_by IS NOT NULL")
        return 0

    symbols = [{"name": row["name"], "kind": row["kind"]} for row in symbol_rows]

    # ── Step 2: Read all edges (only the ones needed for synthesis) ────────────
    # We need: source, target, kind (to identify extends/implements pairs).
    # Only read statically-extracted edges (synthesized_by IS NULL) to avoid
    # feeding synthesized edges back into the engine (no feedback loop).
    edge_rows = conn.execute(
        """
        SELECT DISTINCT source_name, target_name, kind
        FROM edges
        WHERE synthesized_by IS NULL
        """
    ).fetchall()

    edges = [
        {"source": row["source_name"], "target": row["target_name"], "kind": row["kind"]}
        for row in edge_rows
    ]

    # ── Step 3: Run the pure synthesis engine ────────────────────────────────
    # synthesize_edges is pure and never raises (it degrades to [] on error).
    # file_sources is empty — the A2 channel does not use source text.
    synth_edges = synthesize_edges(
        symbols,
        edges,
        file_sources={},
        fanout_cap=fanout_cap,
    )

    # ── Step 4: Write in ONE transaction ─────────────────────────────────────
    # DELETE all previous synthesized edges first (idempotency: second call same result).
    # Then INSERT fresh edges under the synthetic file row.
    with conn:
        # Clear all previous synthesized edges (from ALL channels) so this pass
        # is idempotent: running index_synthesis twice produces the same result.
        conn.execute("DELETE FROM edges WHERE synthesized_by IS NOT NULL")

        if synth_edges:
            conn.executemany(
                """
                INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence, synthesized_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e["source"],
                        e["target"],
                        e["kind"],
                        synth_file_id,
                        e.get("line", 0),
                        e["confidence"],
                        e["synthesized_by"],
                    )
                    for e in synth_edges
                ],
            )

    count = len(synth_edges)
    logger.info(
        "synthesis_index: wrote %d synthesized edges (%s file_id=%d)",
        count,
        _SYNTHESIS_FILE_PATH,
        synth_file_id,
    )
    return count
