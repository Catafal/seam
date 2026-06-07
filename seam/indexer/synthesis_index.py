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
from pathlib import Path

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


def _load_file_sources(conn: sqlite3.Connection) -> dict[str, str]:
    """Load source text for all real indexed files.

    Reads the 'files' table for all non-synthetic paths, then reads each file from
    disk. Silently skips files that are missing, unreadable, or exceed the size limit.

    WHY here and not in the engine: the synthesis engine is a pure leaf (no DB/IO);
    IO must happen in the bridge layer. We pass the loaded sources to synthesize_edges().

    Returns a dict mapping file path string → source text (possibly empty if all failed).
    Never raises.
    """
    from seam import config as cfg  # lazy import to keep leaf contract on synthesis.py

    sources: dict[str, str] = {}
    # Total-corpus budget: stop loading once cumulative source size crosses the cap so
    # a huge monorepo cannot OOM the final init step. 0 = unlimited (see config knob).
    max_total = cfg.SEAM_SYNTHESIS_MAX_SOURCE_BYTES
    total_bytes = 0
    loaded = 0
    tracked = 0
    capped = False
    try:
        path_rows = conn.execute(
            "SELECT path FROM files WHERE path != ? ORDER BY path",
            (_SYNTHESIS_FILE_PATH,),
        ).fetchall()
        tracked = len(path_rows)

        for row in path_rows:
            raw_path = row[0] if isinstance(row, (list, tuple)) else row["path"]
            if not raw_path or raw_path == _SYNTHESIS_FILE_PATH:
                continue
            try:
                p = Path(raw_path)
                if not p.exists() or not p.is_file():
                    continue
                # Respect the max file size limit to avoid loading huge generated files.
                size = p.stat().st_size
                if size > cfg.SEAM_MAX_FILE_BYTES:
                    continue
                # Stop before crossing the total-corpus budget (bounded memory).
                if max_total > 0 and total_bytes + size > max_total:
                    capped = True
                    break
                # Read with errors="replace" to handle encoding issues gracefully.
                sources[raw_path] = p.read_text(encoding="utf-8", errors="replace")
                total_bytes += size
                loaded += 1
            except Exception:  # noqa: BLE001
                # Missing / unreadable file — skip silently (never-raise contract).
                continue

        # Observability: never silently under-produce. A capped scan is a WARNING
        # (synthesis will be partial); a complete scan logs a DEBUG summary so an
        # operator can see how many of the tracked files actually fed the channels.
        if capped:
            logger.warning(
                "synthesis_index: source-load budget reached (%d bytes, %d/%d files) — "
                "source-text channels see a PARTIAL corpus; raise "
                "SEAM_SYNTHESIS_MAX_SOURCE_BYTES to scan more",
                total_bytes,
                loaded,
                tracked,
            )
        else:
            logger.debug(
                "synthesis_index: loaded %d/%d source files (%d bytes) for synthesis",
                loaded,
                tracked,
                total_bytes,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "synthesis_index: failed to load file sources (%s: %s) — "
            "source-text channels will have no input",
            type(exc).__name__,
            exc,
        )
    return sources


def _index_synthesis_impl(conn: sqlite3.Connection, fanout_cap: int) -> int:
    """Inner implementation. May raise — outer function is the guard.

    WHY separate function: the outer wrapper catches ALL exceptions and converts
    them to -1. Having a clean inner function makes the logic easier to reason
    about and test (tests can call the inner function directly if needed).
    """
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

    # ── Step 3: Load source text for source-text-based channels ─────────────
    # The A1a/A1b channels (closure-collection, event-emitter) need the actual
    # source text to scan for dispatch patterns. We read from the 'files' table —
    # each row has a 'path' column with the on-disk path. We skip missing/unreadable
    # files silently (never-raise contract), and skip the synthetic ':synthesis:' row.
    file_sources: dict[str, str] = _load_file_sources(conn)

    # ── Step 4: Run the pure synthesis engine ────────────────────────────────
    # synthesize_edges is pure and never raises (it degrades to [] on error).
    synth_edges = synthesize_edges(
        symbols,
        edges,
        file_sources=file_sources,
        fanout_cap=fanout_cap,
    )

    # ── Step 5: Write in ONE transaction ─────────────────────────────────────
    # Upsert the synthetic file row, DELETE all previous synthesized edges, then
    # INSERT the fresh ones — atomically. Folding the synthetic-row upsert in here
    # (rather than a separate earlier transaction) keeps the whole write a single
    # consistent unit: a crash can't leave the ':synthesis:' row with no edges.
    # Idempotent: the DELETE makes a second call produce the same result.
    with conn:
        synth_file_id = _ensure_synthesis_file_row(conn)
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
