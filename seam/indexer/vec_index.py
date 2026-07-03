"""vec0 KNN table builder — WS2b S2 (forward-compatible scaffold).

Builds (or rebuilds) a sqlite-vec `vec0` virtual table from the persisted
embeddings so the S3 read path can issue KNN queries instead of brute-force
cosine scans.

WHY this is a scaffold, not a performance feature (yet):
  sqlite-vec v0.1.9 performs EXACT brute-force KNN (no HNSW/IVF approximate
  index). Building and querying the vec0 table is currently ~5× SLOWER than
  the numpy matmul mmap path (measured 2026-07-03). This module exists so that
  when sqlite-vec ships a true approximate index, enabling SEAM_VEC_ANN=on will
  transparently upgrade from exact to approximate WITHOUT any code change or
  re-index of embeddings. Until then, SEAM_VEC_ANN should remain "off".

Import hierarchy (same discipline as cluster_index / synthesis_index):
    indexer/vec_index → query.vec_extension (S1 leaf) + query.vector_store + config
    cli / init_index  → this module (never the other way)

Design decisions (see .claude/runs/ws2b-s2-vec-index/implementation-notes.html):
  - Triple-gate: SEAM_VEC_ANN=on AND probe passes AND embedding row-count ≥ MIN_ROWS.
  - Cosine distance: vec0 column declared with `distance_metric=cosine` so KNN ordering
    matches Seam's brute-force / mmap cosine ordering exactly.
  - Idempotency via DROP TABLE IF EXISTS + CREATE: no upsert on virtual tables.
  - Staleness token: stored in a companion ordinary table `vec_meta(model PK,
    index_version, dim)` so the S3 read path can detect a stale vec0 table without
    loading the extension (vec_meta is an ordinary table, readable by any connection).
  - The vec0 table and vec_meta INSERT happen in one transaction for consistency.
  - Extension loading must happen OUTSIDE the transaction (sqlite-vec restriction).
  - Sentinel discipline: -1 = error (never raises), 0 = skipped, ≥1 = rows indexed.
  - Never raises: all errors are caught, logged, and returned as -1.
"""

import logging
import sqlite3

import seam.config as config
from seam.query.vec_extension import load_vec_extension, probe_vec_extension
from seam.query.vector_store import compute_index_version

logger = logging.getLogger(__name__)

# ── Public table names (shared with S3 read path) ────────────────────────────
VEC_TABLE = "vec_embeddings"   # vec0 virtual table; rowid = symbol_id
VEC_META_TABLE = "vec_meta"    # ordinary companion table; stores staleness token


def index_vec(conn: sqlite3.Connection, *, model: str) -> int:
    """Build or rebuild the vec0 ANN index from persisted embedding rows.

    This is the public entry point called by `seam init --semantic` and
    `seam sync --semantic` AFTER index_embeddings / sync_embeddings has
    populated the embeddings table.

    Triple-gate (returns 0 = skipped when any gate fails):
      1. SEAM_VEC_ANN == "on"  (master switch)
      2. probe_vec_extension(conn) returns True  (sqlite-vec available)
      3. embedding row-count for `model` >= SEAM_VEC_ANN_MIN_ROWS  (worth building)

    When all gates pass, (re)builds `vec_embeddings` (vec0, cosine distance)
    and updates `vec_meta` with the current staleness token in ONE transaction.

    Args:
        conn:   Open SQLite connection with write access.
        model:  Embedding model name (e.g. "BAAI/bge-small-en-v1.5"). Used to
                filter embedding rows and to key the vec_meta staleness token.

    Returns:
        Number of rows indexed into vec0 (≥1) on success.
        0 when any gate is not met (clean skip, no error).
        -1 on any unexpected build error (never re-raises).

    WHY -1 sentinel: consistent with index_clusters / index_synthesis; lets
    the CLI distinguish "skipped because gate not met" (0) from "build failed" (-1).
    A failed ANN build must NOT abort the embedding run — init --semantic still
    succeeds, just without the ANN acceleration tier.
    """
    # ── Gate 1: master switch ─────────────────────────────────────────────────
    if config.SEAM_VEC_ANN != "on":
        logger.debug("vec_index: SEAM_VEC_ANN is off — skipping ANN index build")
        return 0

    # ── Gate 2: sqlite-vec capability probe ──────────────────────────────────
    # probe_vec_extension uses its own :memory: connection; no side-effect on conn.
    # WHY per-call (not cached): index_vec is called infrequently (only at init/sync
    # time), so probe overhead is negligible. Per-call avoids stale cached state.
    if not probe_vec_extension(conn):
        logger.debug(
            "vec_index: sqlite-vec probe failed — skipping ANN index build (no extension)"
        )
        return 0

    # ── Gate 3: minimum-row threshold ────────────────────────────────────────
    # WHY this gate: building the vec0 table has real DDL + bulk-INSERT overhead.
    # The 50k default is a forward-compat threshold for when sqlite-vec ships a true
    # approximate index (HNSW/IVF). NOTE: with sqlite-vec v0.1.9 the vec0 tier is
    # currently ~5× SLOWER than numpy matmul at all scales — "faster above 50k" is
    # NOT true today. The gate avoids the DDL overhead for small indexes that would
    # not benefit even when sqlite-vec adds approximate indexing.
    try:
        count_row = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
        ).fetchone()
        row_count = count_row[0] if count_row else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "vec_index: failed to count embedding rows (%s: %s) — skipping ANN build",
            type(exc).__name__,
            exc,
        )
        return 0

    if row_count < config.SEAM_VEC_ANN_MIN_ROWS:
        logger.debug(
            "vec_index: %d embedding row(s) for model=%r < SEAM_VEC_ANN_MIN_ROWS=%d — "
            "skipping ANN index build (brute-force is fast enough at this scale)",
            row_count,
            model,
            config.SEAM_VEC_ANN_MIN_ROWS,
        )
        return 0

    # ── All gates passed — delegate to the guarded inner implementation ───────
    try:
        return _build_vec_index(conn, model=model)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "vec_index: ANN index build failed (%s: %s) — "
            "semantic search will fall back to brute-force; "
            "run 'seam init --semantic' again to retry.",
            type(exc).__name__,
            exc,
        )
        return -1


def _build_vec_index(conn: sqlite3.Connection, *, model: str) -> int:
    """Inner implementation — may raise; index_vec is the guard.

    Steps:
      1. Infer embedding dimensionality from the stored dim column.
      2. Load the sqlite-vec extension onto conn (outside any transaction —
         sqlite-vec cannot be loaded inside an active transaction).
      3. Read all (symbol_id, vector) rows for the model in symbol-id order.
      4. In one transaction:
         a. Ensure vec_meta ordinary table exists.
         b. DROP vec_embeddings IF EXISTS and CREATE it fresh (idempotent rebuild).
         c. Bulk-insert all (rowid=symbol_id, embedding) rows.
         d. Upsert the staleness token into vec_meta.
      5. Return the row count.

    WHY read rows inside the transaction after loading the extension but before
    the DROP-CREATE: loading the extension first is required (DDL needs vec0
    registered); reading rows first (before DDL) means we hold no implicit
    read transaction when calling enable_load_extension (avoids potential
    "cannot load extension inside transaction" edge cases on some SQLite builds).
    """
    # ── Step 1: infer dim ─────────────────────────────────────────────────────
    dim_row = conn.execute(
        "SELECT dim FROM embeddings WHERE model = ? LIMIT 1", (model,)
    ).fetchone()
    if dim_row is None:
        # No embeddings for this model at all — should not reach here (gate 3 checked
        # row_count >= MIN_ROWS), but be defensive.
        logger.debug("vec_index: no embedding rows found for model=%r — skipping", model)
        return 0

    dim = int(dim_row[0])

    # ── Step 2: load extension onto conn (OUTSIDE any transaction) ────────────
    # load_vec_extension enables → loads → disables extension loading so conn can
    # issue CREATE VIRTUAL TABLE USING vec0 for the rest of its lifetime.
    if not load_vec_extension(conn):
        # load_vec_extension already logged a WARNING.
        raise RuntimeError("load_vec_extension returned False — cannot build ANN index")

    # ── Step 3: read embedding rows in symbol-id order ────────────────────────
    # Fetch BEFORE the DROP-CREATE transaction so we have data ready to insert.
    # WHY symbol-id order: deterministic; consistent with how the mmap artifact is
    # written so any future cross-comparison is straightforward.
    rows = conn.execute(
        "SELECT symbol_id, vector FROM embeddings WHERE model = ? ORDER BY symbol_id",
        (model,),
    ).fetchall()

    if not rows:
        logger.debug("vec_index: no embedding rows fetched for model=%r — skipping", model)
        return 0

    # ── Step 4: rebuild vec0 table + update vec_meta in ONE transaction ───────
    # WHY single transaction: if the INSERT fails halfway through, the DROP has
    # already happened. Without a transaction the table would be gone with only
    # partial rows. With a transaction the entire rebuild either commits or rolls
    # back, leaving the previous table intact (actually: it was already dropped in
    # the same tx, so rollback restores it). This is the correct atomicity contract.
    with conn:
        # 4a — ensure the companion metadata table exists (ordinary, not virtual).
        # Created on demand; never a schema migration.
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {VEC_META_TABLE} (
                model         TEXT PRIMARY KEY,
                index_version TEXT NOT NULL,
                dim           INTEGER NOT NULL
            )
            """
        )

        # 4b — drop the old vec0 table (if any) and recreate it fresh.
        # DROP IF EXISTS is idempotent; CREATE declares the cosine metric so KNN
        # results are ordered by cosine distance (lowest = highest similarity).
        # symbol_id is stored as rowid (INTEGER) so KNN results map directly to symbols.
        conn.execute(f"DROP TABLE IF EXISTS {VEC_TABLE}")
        conn.execute(
            f"CREATE VIRTUAL TABLE {VEC_TABLE} "
            f"USING vec0(embedding float[{dim}] distance_metric=cosine)"
        )

        # 4c — bulk-insert all embedding rows.
        # rowid = symbol_id (the natural key for join-back to the symbols table).
        # vector is already a raw float32 blob from the embeddings table.
        conn.executemany(
            f"INSERT INTO {VEC_TABLE}(rowid, embedding) VALUES (?, ?)",
            [(row["symbol_id"], bytes(row["vector"])) for row in rows],
        )

        # 4d — upsert the staleness token so the S3 read path can validate.
        # compute_index_version returns "count:max_symbol_id" — cheap and sufficient
        # to detect any embedding row addition or removal for this model.
        index_version = compute_index_version(conn, model)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {VEC_META_TABLE} (model, index_version, dim)
            VALUES (?, ?, ?)
            """,
            (model, index_version, dim),
        )

    n_rows = len(rows)
    logger.info(
        "vec_index: built ANN index with %d row(s) for model=%r (dim=%d, cosine, token=%r)",
        n_rows,
        model,
        dim,
        index_version,
    )
    return n_rows
