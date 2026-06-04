"""Embedding index orchestration — bridge between analysis.embeddings and persistence (T4).

Mirrors the structure of seam/indexer/cluster_index.py:
- Public function wraps an inner implementation in an exception guard.
- Returns -1 on error (sentinel), ≥0 on success, 0 when skipped (fastembed absent).
- Never raises — callers (`seam init --semantic`) can always inspect the return value.

Import hierarchy (enforced):
    indexer/embedding_index → analysis.embeddings (leaf) + config
    analysis.embeddings → stdlib + fastembed(lazy); no numpy import, no DB
    cli → this module (not the other way)

Design decisions:
- Reads ALL symbols in one pass (id, name, signature, docstring).
- Embeds in batches of `batch` texts (default from caller — CLI passes config value).
- Upserts via INSERT OR REPLACE so repeated calls are idempotent (no duplicates).
- dim is inferred from the first returned vector (len(blob) // 4) — avoids hard-coding.
- Returns the count of upserted rows, or 0 when skipped, or -1 on failure.
"""

import logging
import sqlite3

from seam.analysis.embeddings import embed_texts, is_available, symbol_text

logger = logging.getLogger(__name__)


def index_embeddings(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch: int = 32,
) -> int:
    """Embed all indexed symbols and persist vectors to the embeddings table.

    Reads the full symbol table, batches texts through the local fastembed model,
    and upserts one row per symbol into `embeddings(symbol_id, model, dim, vector)`.

    Called by `seam init --semantic` after the full indexing + cluster pass.
    NOT called by the file watcher or `seam sync` (incremental re-embedding is a
    future enhancement; full re-index is the safe baseline).

    Args:
        conn:   Open SQLite connection with write access.
        model:  FastEmbed model name (e.g. "BAAI/bge-small-en-v1.5").
        batch:  Number of texts to embed per fastembed call. Larger values use
                more RAM but reduce per-call overhead. Default 32 is a safe default.

    Returns:
        Number of symbols embedded and upserted (≥0).
        Returns 0 when fastembed is unavailable (skip cleanly — no error).
        Returns -1 on any unexpected error (never re-raises — mirrors index_clusters).

    WHY -1 sentinel: consistent with index_clusters; lets the CLI distinguish
    "zero embeddings because fastembed absent" (0) from "embedding failed" (-1).
    """
    # Fast skip when fastembed is not installed — no error, no log noise.
    if not is_available():
        logger.debug("embedding_index: fastembed not available — skipping semantic indexing")
        return 0

    try:
        return _index_embeddings_impl(conn, model=model, batch=batch)
    except Exception as exc:
        logger.warning(
            "embedding_index: failed to compute embeddings (%s: %s) — "
            "embeddings table will be empty; run 'seam init --semantic' again to retry.",
            type(exc).__name__,
            exc,
        )
        return -1


def _index_embeddings_impl(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch: int,
) -> int:
    """Inner implementation — may raise; outer function is the guard.

    WHY separate function: the outer wrapper catches ALL exceptions and converts
    them to -1. Having a clean inner function makes the logic easier to reason
    about and test without needing to trigger the guard.

    Steps:
      1. Fetch all symbols (id, name, signature, docstring) in one query.
      2. Build canonical text for each symbol via symbol_text().
      3. Embed in batches using embed_texts().
      4. Upsert each embedding into the embeddings table (INSERT OR REPLACE).
      5. Return the total count of upserted rows.
    """
    # ── Step 1: Read all symbols ──────────────────────────────────────────────
    rows = conn.execute(
        "SELECT id, name, signature, docstring FROM symbols ORDER BY id"
    ).fetchall()

    if not rows:
        logger.debug("embedding_index: no symbols in index — nothing to embed")
        return 0

    # ── Step 2: Build canonical texts ─────────────────────────────────────────
    # Precompute (symbol_id, text) pairs; keep IDs aligned with text list.
    symbol_ids: list[int] = []
    texts: list[str] = []
    for row in rows:
        symbol_ids.append(row["id"])
        texts.append(
            symbol_text(
                row["name"],
                row["signature"],  # may be None
                row["docstring"],  # may be None
            )
        )

    # ── Step 3 + 4: Embed in batches + upsert ─────────────────────────────────
    # All batches run inside a SINGLE outer transaction: if ANY batch fails
    # mid-run, the entire set of inserts is rolled back so the embeddings table
    # stays empty (clean-retry state). Without the single transaction, a partial
    # failure would leave half-populated embeddings that look valid but are not.
    # Batching remains for memory — we still process in `batch`-sized windows;
    # we just don't commit after each batch.
    total = 0
    inferred_dim: int | None = None

    with conn:
        for batch_start in range(0, len(texts), batch):
            batch_texts = texts[batch_start : batch_start + batch]
            batch_ids = symbol_ids[batch_start : batch_start + batch]
            batch_end = batch_start + len(batch_texts)

            # embed_texts returns [] on any failure — treat as a hard error at this level
            # so the outer guard can return -1 (empty batch = model crashed mid-run).
            blobs = embed_texts(batch_texts, model)

            # Zip-truncation guard: blobs and batch_texts must be the same length.
            # If embed_texts returns fewer vectors than texts (silent truncation),
            # raise immediately so the outer guard returns -1 rather than silently
            # storing incomplete data.
            if not blobs:
                raise RuntimeError(
                    f"embed_texts returned no vectors for batch "
                    f"[{batch_start}:{batch_end}] — fastembed may have failed silently"
                )
            if len(blobs) != len(batch_texts):
                logger.warning(
                    "embedding_index: zip-truncation detected in batch [%d:%d] — "
                    "embed_texts returned %d vectors for %d texts; "
                    "raising to trigger -1 sentinel.",
                    batch_start,
                    batch_end,
                    len(blobs),
                    len(batch_texts),
                )
                raise RuntimeError(
                    f"embed_texts returned {len(blobs)} vectors for {len(batch_texts)} texts "
                    f"in batch [{batch_start}:{batch_end}] — zip-truncation guard triggered"
                )

            # Infer dimension from the first blob: len(bytes) / 4 bytes-per-float32.
            if inferred_dim is None:
                inferred_dim = len(blobs[0]) // 4

            conn.executemany(
                """
                INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (sid, model, inferred_dim, blob)
                    for sid, blob in zip(batch_ids, blobs)
                ],
            )
            total += len(blobs)

    logger.info(
        "embedding_index: %d symbol(s) embedded with model %r (dim=%s)",
        total,
        model,
        inferred_dim,
    )
    return total
