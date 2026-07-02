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

WS1-A: SEAM_EMBED_BODY=on gated body path:
- Fetches file_id, start_line, end_line, and files.path alongside each symbol.
- Reads each source file at most once (per-file dict cache; read → splitlines()).
- A file that cannot be read degrades that file's symbols to header-only (logs warning).
- Passes body slice + SEAM_EMBED_INPUT_MAX_CHARS to symbol_text() for enrichment.
- When SEAM_EMBED_BODY=off (default): no disk reads, no body, byte-identical vectors.

WS1-B: SEAM_EMBED_BODY=on also associates DB comments to each symbol by line-range:
- For each symbol, fetches comments WHERE file_id = symbol.file_id AND
  line BETWEEN symbol.start_line AND symbol.end_line.
- WHY/HACK/NOTE comment texts are joined (space-separated) and passed as the
  `comments` kwarg to symbol_text().
- Gated by the same SEAM_EMBED_BODY knob — no new config knob.
- When SEAM_EMBED_BODY=off: no comment join, byte-identical to pre-WS1-B.
- Fetched in a single SQL pass via GROUP_CONCAT per symbol before the text-build loop.

WS3 Slice 2 — Incremental embed orchestrator (issue #211):
- sync_embeddings(conn, *, model, batch) -> int: full incremental refresh behind ONE call.
  Orchestrates: orphan sweep → missing-set computation → scoped embed → artifact rebuild.
  Called by 'seam sync --semantic' (NOT by init --semantic, which keeps the full embed).
  Returns n_added (≥0), 0 = nothing new / fastembed absent, -1 = failed. Never raises.

WS3 Slice 1 — Scoped embedding primitives (issue #210):
- only_symbol_ids: set[int] | None = None on index_embeddings / _index_embeddings_impl.
  None (default) = full embed (byte-identical to pre-WS3 init --semantic).
  A non-empty set = embed ONLY those symbols via a TEMP TABLE JOIN to avoid the SQLite
  variable-number limit (default 999). Empty set = returns 0, embedder not called.
  CRITICAL: scoped path does NOT write the mmap artifact (_write_artifact is suppressed).
  The Slice 2 orchestrator owns artifact rebuild. Full-embed path (None) is unchanged.
- symbols_needing_embeddings(conn, model) → set[int]: LEFT JOIN to find un-embedded ids.
- delete_orphan_embeddings(conn) → int: delete embedding rows whose symbol is gone.
"""

import logging
import sqlite3
from pathlib import Path

import seam.config as config
from seam.analysis.embeddings import embed_texts, extract_body_slice, is_available, symbol_text
from seam.config import SEAM_EMBED_BODY, SEAM_EMBED_INPUT_MAX_CHARS
from seam.query.vector_store import (
    VectorStore,
    compute_index_version,
    get_artifact_dir,
    load_store,
    write_store,
)

logger = logging.getLogger(__name__)


def index_embeddings(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch: int = 32,
    only_symbol_ids: set[int] | None = None,
) -> int:
    """Embed indexed symbols and persist vectors to the embeddings table.

    Reads symbols (all or a scoped subset), batches texts through the local fastembed
    model, and upserts one row per symbol into `embeddings(symbol_id, model, dim, vector)`.

    Called by `seam init --semantic` after the full indexing + cluster pass (full embed).
    Also callable by the incremental sync orchestrator (WS3 Slice 2) with a scoped set.

    Args:
        conn:             Open SQLite connection with write access.
        model:            FastEmbed model name (e.g. "BAAI/bge-small-en-v1.5").
        batch:            Number of texts to embed per fastembed call. Larger values use
                          more RAM but reduce per-call overhead. Default 32 is safe.
        only_symbol_ids:  When None (default), embeds ALL symbols (byte-identical to
                          pre-WS3 init --semantic). When a non-empty set, embeds ONLY
                          those symbol IDs using a TEMP TABLE JOIN (avoids SQLite's
                          variable-number limit). An empty set returns 0 immediately
                          without calling the embedder.

    Returns:
        Number of symbols embedded and upserted (≥0).
        Returns 0 when fastembed is unavailable (skip cleanly — no error).
        Returns 0 when only_symbol_ids is an empty set.
        Returns -1 on any unexpected error (never re-raises — mirrors index_clusters).

    WHY -1 sentinel: consistent with index_clusters; lets the CLI distinguish
    "zero embeddings because fastembed absent" (0) from "embedding failed" (-1).

    WHY scoped path suppresses _write_artifact: the mmap artifact covers ALL embeddings
    for the model. Writing it from a partial scope would corrupt the full artifact.
    The Slice 2 orchestrator owns artifact rebuild after incremental runs.
    """
    # Fast skip when fastembed is not installed — no error, no log noise.
    if not is_available():
        logger.debug("embedding_index: fastembed not available — skipping semantic indexing")
        return 0

    # Empty scope: nothing to embed — return 0 without calling the embedder.
    if only_symbol_ids is not None and not only_symbol_ids:
        logger.debug("embedding_index: empty only_symbol_ids — nothing to embed")
        return 0

    try:
        return _index_embeddings_impl(
            conn, model=model, batch=batch, only_symbol_ids=only_symbol_ids
        )
    except Exception as exc:
        logger.warning(
            "embedding_index: failed to compute embeddings (%s: %s) — "
            "embeddings table will be empty; run 'seam init --semantic' again to retry.",
            type(exc).__name__,
            exc,
        )
        return -1


def _read_file_lines(path: str) -> list[str] | None:
    """Read a source file and return its lines as a list, or None on any error.

    WHY separate helper: keeps the read-and-split logic isolated and easily mockable
    in tests that target the open() call path.

    Returns:
        List of lines (from str.splitlines()) on success, None on any IO error.
        Never raises — degrades to None so callers can fall back gracefully.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "embedding_index: cannot read source file %r (%s: %s) — "
            "symbols in this file will use header-only embeddings.",
            path,
            type(exc).__name__,
            exc,
        )
        return None


def _create_scope_temp_table(conn: sqlite3.Connection, ids: set[int]) -> None:
    """Create (or recreate) the TEMP TABLE _scope_ids with the given symbol IDs.

    WHY a temp table instead of IN (?, ?, ...):
        SQLite's SQLITE_LIMIT_VARIABLE_NUMBER defaults to 999 (may be lower on some
        builds). A scoped set of >999 IDs would raise "too many SQL variables". A TEMP
        TABLE JOIN has no parameter limit and uses the same query plan as a full scan
        for large sets. DROP IF EXISTS before CREATE makes this idempotent within one
        connection lifetime (e.g. repeated calls from tests).
    """
    conn.execute("DROP TABLE IF EXISTS temp._scope_ids")
    conn.execute("CREATE TEMP TABLE _scope_ids (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO _scope_ids (id) VALUES (?)", [(i,) for i in ids])


def _index_embeddings_impl(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch: int,
    only_symbol_ids: set[int] | None,
) -> int:
    """Inner implementation — may raise; outer function is the guard.

    WHY separate function: the outer wrapper catches ALL exceptions and converts
    them to -1. Having a clean inner function makes the logic easier to reason
    about and test without needing to trigger the guard.

    Steps:
      1. (Optional) Create TEMP TABLE _scope_ids when only_symbol_ids is given.
      2. Fetch symbols (all or scoped via JOIN) including body fields when SEAM_EMBED_BODY=on.
      3. Build canonical text for each symbol via symbol_text() (+ body when on).
      4. Embed in batches using embed_texts().
      5. Upsert each embedding into the embeddings table (INSERT OR REPLACE).
      6. Write the mmap artifact — ONLY for the full-embed path (only_symbol_ids is None).
      7. Return the total count of upserted rows.
    """
    embed_body = SEAM_EMBED_BODY == "on"
    scoped = only_symbol_ids is not None  # True = partial embed; False = full embed

    # ── Effective char budget ─────────────────────────────────────────────────
    # SEAM_EMBED_INPUT_MAX_CHARS=0 = unlimited (mirrors SEAM_IMPACT_MAX_BYTES
    # discipline: 0 = unlimited = no cap on content beyond the header).
    # symbol_text(max_chars=None) disables the body path entirely; we use a
    # large-but-finite sentinel so body + comments are included without truncation.
    _unlimited_sentinel = 1_000_000  # 1 M chars — effectively no cap
    effective_max_chars = (
        SEAM_EMBED_INPUT_MAX_CHARS if SEAM_EMBED_INPUT_MAX_CHARS > 0 else _unlimited_sentinel
    )

    # ── Step 1 (scoped path): populate TEMP TABLE for JOIN-based scoping ─────
    # WHY before the symbol query: the JOIN reference to _scope_ids must exist
    # when we execute the SELECT. DROP IF EXISTS + CREATE = idempotent within a
    # connection (safe for repeated test calls).
    if scoped:
        # only_symbol_ids is non-None and non-empty here (empty was caught in outer).
        _create_scope_temp_table(conn, only_symbol_ids)  # type: ignore[arg-type]

    # ── Step 2: Read symbols (all or scoped) ─────────────────────────────────
    # When body enrichment is on, also fetch file_id, start_line, end_line, and
    # the files.path so we can read source files once per file.
    # The scoped path adds "JOIN _scope_ids sc ON sc.id = s.id" to filter rows.
    if embed_body:
        if scoped:
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.signature, s.docstring,
                       s.file_id, s.start_line, s.end_line, f.path AS file_path
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                JOIN _scope_ids sc ON sc.id = s.id
                ORDER BY s.id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.signature, s.docstring,
                       s.file_id, s.start_line, s.end_line, f.path AS file_path
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                ORDER BY s.id
                """
            ).fetchall()
    else:
        if scoped:
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.signature, s.docstring
                FROM symbols s
                JOIN _scope_ids sc ON sc.id = s.id
                ORDER BY s.id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, signature, docstring FROM symbols ORDER BY id"
            ).fetchall()

    if not rows:
        logger.debug("embedding_index: no symbols in index — nothing to embed")
        return 0

    # ── Step 2b (body path only): build per-file line cache ──────────────────
    # Read each source file at most once; missing files degrade to None (header-only).
    # Key: absolute file path string → list[str] of lines (or None = unreadable).
    file_line_cache: dict[str, list[str] | None] = {}
    if embed_body:
        for row in rows:
            path = row["file_path"]
            if path not in file_line_cache:
                file_line_cache[path] = _read_file_lines(path)

    # ── Step 2c (body path only): fetch per-symbol comment texts from DB ──────
    # WS1-B: associate comments to symbols by line-range using a single SQL pass.
    # GROUP_CONCAT aggregates all matched comment texts per symbol in one query.
    # Key: symbol_id → joined comment string (or None if no comments).
    # WHY a dict keyed by symbol_id: the rows list is already our authoritative
    # iteration order; we look up comments per row to keep the loop below simple.
    symbol_comments: dict[int, str | None] = {}
    if embed_body:
        if scoped:
            comment_rows = conn.execute(
                """
                SELECT s.id AS symbol_id,
                       GROUP_CONCAT(c.text, ' ') AS joined_comments
                FROM symbols s
                JOIN _scope_ids sc ON sc.id = s.id
                LEFT JOIN comments c
                  ON c.file_id = s.file_id
                 AND c.line BETWEEN s.start_line AND s.end_line
                GROUP BY s.id
                ORDER BY s.id
                """
            ).fetchall()
        else:
            comment_rows = conn.execute(
                """
                SELECT s.id AS symbol_id,
                       GROUP_CONCAT(c.text, ' ') AS joined_comments
                FROM symbols s
                LEFT JOIN comments c
                  ON c.file_id = s.file_id
                 AND c.line BETWEEN s.start_line AND s.end_line
                GROUP BY s.id
                ORDER BY s.id
                """
            ).fetchall()
        # Build lookup dict; GROUP_CONCAT returns None when no comments match.
        for crow in comment_rows:
            symbol_comments[crow["symbol_id"]] = crow["joined_comments"]

    # ── Step 2: Build canonical texts ─────────────────────────────────────────
    # Precompute (symbol_id, text) pairs; keep IDs aligned with text list.
    symbol_ids: list[int] = []
    texts: list[str] = []
    for row in rows:
        symbol_ids.append(row["id"])

        if embed_body:
            # Build body slice from cached file lines; fall back to None if unreadable
            src_lines = file_line_cache.get(row["file_path"])
            body: str | None = None
            if src_lines is not None:
                body = extract_body_slice(src_lines, row["start_line"], row["end_line"])

            # WS1-B: retrieve pre-joined comment text for this symbol (may be None)
            comments = symbol_comments.get(row["id"])

            texts.append(
                symbol_text(
                    row["name"],
                    row["signature"],  # may be None
                    row["docstring"],  # may be None
                    body=body,
                    max_chars=effective_max_chars,
                    comments=comments,
                )
            )
        else:
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
        "embedding_index: %d symbol(s) embedded with model %r (dim=%s)%s",
        total,
        model,
        inferred_dim,
        " [scoped]" if scoped else "",
    )

    # ── WS2a / WS3: Write / refresh the mmap artifact (FULL embed only) ──────
    # After the embed+commit succeeds, write the vector store artifact from the
    # PERSISTED rows in symbol-id order. Re-reading from DB (not from the in-memory
    # blobs) ensures the artifact is authoritative: it matches what is stored.
    # Artifact-write failure is caught, logged, and MUST NOT fail the embed run.
    #
    # WHY skip artifact for scoped path:
    #   The mmap artifact covers ALL embeddings for the model — it is keyed on
    #   COUNT + MAX(symbol_id) as its staleness token. Writing it from a partial
    #   scope would overwrite a complete artifact with an incomplete one, silently
    #   corrupting recall for symbols not in the scope. The Slice 2 orchestrator
    #   (seam sync --semantic) owns artifact rebuild after incremental embed runs.
    if not scoped and config.SEAM_VECTOR_STORE == "on" and inferred_dim is not None and total > 0:
        _write_artifact(conn, model=model, dim=inferred_dim)

    return total


def _write_artifact(
    conn: sqlite3.Connection,
    *,
    model: str,
    dim: int,
) -> None:
    """Write the mmap vector store artifact after a successful embed pass.

    Re-reads persisted embeddings from the DB in symbol-id order so the artifact
    is provably consistent with what is stored in SQLite (not the in-memory batch
    state from the embed loop, which could differ on partial reruns).

    Failure is silently swallowed (logged at WARNING) — the embed run's return value
    is unaffected. The SQL path remains the authoritative fallback.
    """
    artifact_dir = get_artifact_dir(conn)
    if artifact_dir is None:
        logger.debug(
            "_write_artifact: no artifact directory (in-memory DB?) — skipping"
        )
        return

    try:
        # Read the persisted rows in symbol-id order: authoritative source of truth.
        # WHY re-read from DB: guarantees the artifact matches what is persisted.
        # The in-memory batch blobs from the embed loop could diverge on retries.
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model = ? ORDER BY symbol_id",
            (model,),
        ).fetchall()

        if not rows:
            logger.debug("_write_artifact: no embeddings rows for model=%r — skipping", model)
            return

        sym_ids = [row["symbol_id"] for row in rows]
        blobs = [bytes(row["vector"]) for row in rows]

        # Compute a cheap staleness token so load_store can detect stale artifacts.
        # Token = f"{count}:{max_symbol_id}" — sufficient to detect any add/remove.
        index_version = compute_index_version(conn, model)

        write_store(
            artifact_dir,
            sym_ids,
            blobs,
            model=model,
            dim=dim,
            index_version=index_version,
        )
        logger.info(
            "_write_artifact: wrote mmap artifact for %d vector(s) (model=%r, dim=%d)",
            len(sym_ids),
            model,
            dim,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_write_artifact: failed to write vector artifact (%s: %s) — "
            "semantic search will use the SQLite fallback path.",
            type(exc).__name__,
            exc,
        )


# ── WS3 Slice 1 helper functions ──────────────────────────────────────────────


def symbols_needing_embeddings(conn: sqlite3.Connection, model: str) -> set[int]:
    """Return the set of symbol IDs that have no embedding row for the given model.

    Uses a LEFT JOIN (not NOT IN) to avoid SQL NULL pitfalls: NOT IN (subquery)
    returns NULL when the subquery contains a NULL, silently dropping rows. LEFT JOIN
    WHERE e.symbol_id IS NULL is unambiguous and scales better on large tables.

    The model filter ensures that symbols already embedded under a DIFFERENT model are
    correctly included — they are un-embedded for this model and must be re-embedded.

    Returns:
        set[int] of symbol IDs that need embedding. Empty set when all symbols are
        embedded or when the symbols table is empty.
        Returns an empty set (never raises) on any DB error.
    """
    try:
        rows = conn.execute(
            """
            SELECT s.id
            FROM symbols s
            LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
            WHERE e.symbol_id IS NULL
            """,
            (model,),
        ).fetchall()
        return {row[0] for row in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "symbols_needing_embeddings: SQL error (%s: %s) — returning empty set",
            type(exc).__name__,
            exc,
        )
        return set()


def sync_embeddings(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch: int = 32,
) -> int:
    """Incremental embedding refresh for 'seam sync --semantic'.

    Orchestrates the full incremental embed cycle in a single call:
      1. Defensive orphan sweep — remove embedding rows whose symbol was deleted.
      2. Compute the missing-embeddings set (symbols with no row for this model).
      3. Scoped embed of exactly that missing set (no wasted re-embed of stable symbols).
      4. Rebuild the mmap vector-store artifact when the embedding set CHANGED —
         i.e. when n_added > 0 OR n_removed > 0.  A pure-removal sync (n_added==0 but
         n_removed>0) MUST still rebuild the artifact so stale vectors are evicted.

    Called by 'seam sync --semantic'. Leave 'seam init --semantic' UNTOUCHED — it still
    calls index_embeddings(only_symbol_ids=None) for a full embed.

    Args:
        conn:   Open SQLite connection (read-write).
        model:  FastEmbed model name (e.g. "BAAI/bge-small-en-v1.5").
        batch:  Texts per embed call. Default 32 (safe memory baseline).

    Returns:
        Count of NEWLY embedded symbols (n_added ≥ 0).
        0 when fastembed is unavailable (clean skip, no error).
        0 when the embedding set is already up-to-date and no orphans were found.
        -1 on any unexpected failure (never re-raises — mirrors index_clusters).

    WHY separate from index_embeddings (full embed):
        init --semantic ALWAYS re-embeds everything (clean slate after full re-index).
        sync --semantic should be fast and incremental — only embed what is new/missing.
        The two paths have different semantics; a single over-loaded flag would be opaque.
    """
    # Step 0 — fast skip when fastembed not installed; mirrors index_embeddings gate.
    if not is_available():
        logger.debug("sync_embeddings: fastembed not available — skipping incremental embed")
        return 0

    try:
        return _sync_embeddings_impl(conn, model=model, batch=batch)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sync_embeddings: incremental embed failed (%s: %s) — "
            "run 'seam init --semantic' to do a full re-embed.",
            type(exc).__name__,
            exc,
        )
        return -1


def _sync_embeddings_impl(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch: int,
) -> int:
    """Inner implementation — may raise; sync_embeddings is the guard.

    Steps:
      1. Orphan sweep: delete embedding rows with no matching symbol.
      2. Compute missing set: symbol IDs with no embedding row for this model.
      3. If missing set is non-empty: scoped embed (index_embeddings with only_symbol_ids).
      4. Rebuild the mmap artifact when the DB state diverges from the stored artifact.
         WHY compare staleness tokens (not just n_added/n_removed):
           SQLite FK CASCADE may have already deleted embedding rows BEFORE this function
           was called (e.g. when symbols were removed during sync_project). In that case
           n_removed==0 (the orphan sweep found nothing) but the artifact is still stale
           (it contains vectors for deleted symbols). Comparing the artifact's stored
           index_version against the freshly computed DB token is the authoritative check.
      5. Return n_added (count of newly embedded symbols).
    """
    # Step 1 — defensive orphan sweep (handles deleted symbols / cascaded deletes that
    # may have been bypassed when FK enforcement was off).
    n_removed = delete_orphan_embeddings(conn)

    # Step 2 — find symbols without an embedding row for this model.
    missing_ids = symbols_needing_embeddings(conn, model)

    # Step 3 — scoped embed of missing symbols only.
    # index_embeddings returns 0 when missing_ids is empty (fast path, no embedder call).
    # It returns -1 on failure — re-raise so the outer guard catches it.
    n_added = index_embeddings(conn, model=model, batch=batch, only_symbol_ids=missing_ids)
    if n_added < 0:
        # Propagate failure signal — index_embeddings already logged the warning.
        raise RuntimeError("index_embeddings returned -1 (scoped embed failed)")

    # Step 4 — rebuild the mmap artifact when the DB embedding state diverges from it.
    # We do NOT rely solely on n_added/n_removed because FK CASCADE deletes (triggered
    # by symbol deletion during sync_project) happen BEFORE this function runs, leaving
    # n_removed==0 even though the artifact is stale. The staleness-token comparison is
    # the authoritative and complete check: it catches ALL causes of divergence.
    if config.SEAM_VECTOR_STORE == "on":
        _maybe_rebuild_artifact(conn, model=model, n_added=n_added, n_removed=n_removed)

    logger.info(
        "sync_embeddings: incremental embed complete — "
        "%d new symbol(s) embedded, %d orphan(s) removed (model=%r)",
        n_added,
        n_removed,
        model,
    )
    return n_added


def _load_store_safe(artifact_dir: Path, model: str) -> VectorStore | None:
    """Load the mmap store without raising; returns None on any failure.

    WHY thin wrapper: load_store already never raises, but we want a clear name
    that signals "best-effort read for staleness check, not a hard dependency".
    """
    try:
        return load_store(artifact_dir, model)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_load_store_safe: failed to load artifact (%s) — treating as absent", exc)
        return None


def _maybe_rebuild_artifact(
    conn: sqlite3.Connection,
    *,
    model: str,
    n_added: int,
    n_removed: int,
) -> None:
    """Rebuild the mmap artifact if the DB embedding state diverges from the stored artifact.

    Strategy:
      - If n_added > 0 or n_removed > 0 from THIS sync: definitely changed → rebuild.
      - Otherwise: compare the artifact's stored index_version against the DB's current
        version.  A mismatch (or absent artifact) means FK CASCADE or another path changed
        the embeddings without going through the orphan sweep → rebuild.
      - When no embeddings exist for the model at all: skip (nothing to write).

    WHY compare tokens on the "no local changes" path:
      FK CASCADE deletes embeddings atomically when their symbol is removed.
      sync_project may delete symbols (via delete_file) BEFORE sync_embeddings runs.
      In that case n_removed==0 (orphan sweep found nothing) yet the artifact is stale.
    """
    artifact_dir = get_artifact_dir(conn)
    if artifact_dir is None:
        logger.debug("_maybe_rebuild_artifact: no artifact dir (in-memory DB?) — skipping")
        return

    if n_added == 0 and n_removed == 0:
        # No changes detected through the normal orphan-sweep path.
        # Check whether the artifact is still consistent with the DB.
        db_version = compute_index_version(conn, model)
        store = _load_store_safe(artifact_dir, model)
        if store is not None and store.index_version == db_version:
            logger.debug(
                "_maybe_rebuild_artifact: artifact is current (version=%r) — skipping",
                db_version,
            )
            return
        # Either: no artifact yet, or artifact is stale (token mismatch).
        logger.debug(
            "_maybe_rebuild_artifact: artifact stale or absent (db_version=%r) — rebuilding",
            db_version,
        )

    # Rebuild. Infer dim from a persisted row; skip if no embeddings for this model.
    dim_row = conn.execute(
        "SELECT dim FROM embeddings WHERE model = ? LIMIT 1", (model,)
    ).fetchone()
    if dim_row is None:
        logger.debug(
            "_maybe_rebuild_artifact: no embeddings for model=%r — skipping artifact write",
            model,
        )
        return
    _write_artifact(conn, model=model, dim=dim_row["dim"])


def delete_orphan_embeddings(conn: sqlite3.Connection) -> int:
    """Delete embedding rows whose symbol_id no longer exists in the symbols table.

    Idempotent: safe to call repeatedly. Returns 0 when there are no orphans
    (no-op). ON DELETE CASCADE on the FK handles most cases at write time, but
    this provides a defensive maintenance sweep for any case where FK enforcement
    was bypassed (e.g. PRAGMA foreign_keys was off during some write).

    Returns:
        Number of rows deleted (≥0). Returns 0 (never raises) on any DB error.
    """
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM embeddings WHERE symbol_id NOT IN (SELECT id FROM symbols)"
            )
            deleted = cur.rowcount
        if deleted > 0:
            logger.info(
                "delete_orphan_embeddings: removed %d orphan embedding row(s)", deleted
            )
        else:
            logger.debug("delete_orphan_embeddings: no orphans found — no-op")
        return deleted
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "delete_orphan_embeddings: SQL error (%s: %s) — returning 0",
            type(exc).__name__,
            exc,
        )
        return 0
