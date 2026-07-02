"""Semantic search read path — T5.

Leaf module: imports stdlib + seam.config + seam.analysis.embeddings.
numpy is imported LAZILY inside _semantic_candidates_impl only (never at module scope).
NO server/cli imports; NO direct fastembed import.

Public API:
    rrf_merge(fts_ranked, semantic_ranked, k=60) -> list[int]
        Pure Reciprocal Rank Fusion of two ranked id lists.
        Fully unit-testable without a model.

    cosine_sim(a_bytes, b_bytes) -> float
        Cosine similarity between two float32 blobs.
        Returns 0.0 on zero-norm vectors or dimension mismatch (safe fallback).
        Pure-Python (struct.unpack) — importable even when numpy is absent.

    semantic_candidates(conn, query, *, model, limit) -> list[tuple[int, float]]
        Embed query via analysis.embeddings.embed_query, compute cosine similarity
        against stored vectors, return top-k (symbol_id, score) pairs.

        Returns [] if:
          - fastembed unavailable
          - embed_query returns b'' (model error)
          - stored model != configured model (never silently mix models)
          - no embeddings in DB

Design decisions:
- WHY numpy is lazy here (not at module scope): this module must be importable when
  only the base install (no [semantic] extra) is present — numpy may be absent. numpy
  is imported inside _semantic_candidates_impl, gated by is_available() being True
  (which implies fastembed and thus numpy are present). A try/except ImportError falls
  back to pure-Python cosine_sim so the module degrades safely even in pathological
  environments where numpy is somehow missing despite fastembed being importable.
- Model-mismatch guard: the DB stores the model name per row. If no row matches the
  requested model, we treat the entire store as incompatible and return []. This
  prevents silently mixing vectors from different embedding spaces (different spaces
  produce meaningless cosine scores without any visible error). The check runs before
  any vector load or cosine computation.
- Brute-force cosine: O(n * dim). For 1k–20k symbols × 384 dim this is
  ~1–5ms with numpy — no ANN index needed at this scale. sqlite-vec is deferred.
- SEAM_SEMANTIC_SCAN_CAP = 0 (default, unlimited): no cap applied — all stored
  rows are loaded in the SQL fallback path and all matrix rows are considered in
  the mmap path. A positive cap is an optional memory-safety ceiling for operators
  who need a hard bound; rows beyond the cap are invisible to semantic search.
  Logs at DEBUG when a positive cap is active and the scan is actually bounded.
"""

import logging
import sqlite3
import struct

import seam.config as config
from seam.analysis.embeddings import embed_query, is_available
from seam.query.vector_store import (
    compute_index_version,
    get_artifact_dir,
    load_store,
)
from seam.query.vector_store import (
    top_k as vector_store_top_k,
)

logger = logging.getLogger(__name__)


# ── rrf_merge ─────────────────────────────────────────────────────────────────


def rrf_merge(
    fts_ranked: list[int],
    semantic_ranked: list[int],
    k: int = 60,
) -> list[int]:
    """Reciprocal Rank Fusion of two ranked id lists.

    Combines a keyword-ranked list and a semantic-ranked list into a single
    merged ranking using the RRF formula:

        score(id) = Σ  1 / (k + rank_i(id))

    where rank_i is the 1-based position of the id in list i (lists that do not
    contain the id contribute 0). The merged list is sorted by score descending.

    Args:
        fts_ranked:      Symbol ids ranked by FTS5 relevance (best first).
        semantic_ranked: Symbol ids ranked by cosine similarity (best first).
        k:               RRF smoothing constant. Default 60 (standard value from
                         the original Cormack et al. paper). Higher k flattens
                         rank differences; lower k amplifies them.

    Returns:
        A deduplicated list of ids sorted by merged RRF score descending.
        Ids that appear in both lists receive a higher score than ids in only one.
        Stable: ties are broken by original list order (fts first, then semantic).

    WHY RRF vs. score interpolation:
        Score interpolation requires normalizing scores across heterogeneous systems
        (BM25 vs cosine). RRF only needs rank positions, which are always comparable.
        It consistently outperforms score interpolation in hybrid retrieval benchmarks
        without any additional tuning. (Cormack, Clarke & Buettcher, SIGIR 2009.)
    """
    if not fts_ranked and not semantic_ranked:
        return []

    # Build a score map: id → accumulated RRF score
    scores: dict[int, float] = {}

    # Add contributions from the FTS list (1-based rank)
    for rank, doc_id in enumerate(fts_ranked, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    # Add contributions from the semantic list
    for rank, doc_id in enumerate(semantic_ranked, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    # Sort by score descending; stable sort preserves insertion order for ties,
    # which effectively prioritises fts_ranked for tie-breaking (fts ids were inserted first).
    return sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)


# ── cosine_sim ────────────────────────────────────────────────────────────────


def cosine_sim(a_bytes: bytes, b_bytes: bytes) -> float:
    """Compute cosine similarity between two float32 byte blobs.

    Uses struct.unpack (stdlib only — no numpy import here) to decode the vectors.
    This keeps the module importable even when numpy is absent.

    Returns:
        float in [-1.0, 1.0]; 0.0 on any degenerate case (zero norm, dim mismatch,
        empty input, corrupt bytes). Never raises.

    WHY struct.unpack over numpy:
        numpy is a fastembed transitive dep — when fastembed is absent, numpy may
        also be absent. Using struct keeps this module dependency-free, ensuring
        it is always importable without extras.
    """
    if not a_bytes or not b_bytes:
        return 0.0

    n_a = len(a_bytes) // 4
    n_b = len(b_bytes) // 4

    if n_a != n_b or n_a == 0:
        return 0.0

    try:
        fmt = f"{n_a}f"
        a = struct.unpack(fmt, a_bytes)
        b = struct.unpack(fmt, b_bytes)
    except struct.error:
        # Corrupt bytes — degrade gracefully
        return 0.0

    dot = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = sum(ai * ai for ai in a) ** 0.5
    norm_b = sum(bi * bi for bi in b) ** 0.5

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)


# ── semantic_candidates ───────────────────────────────────────────────────────


def semantic_candidates(
    conn: sqlite3.Connection,
    query: str,
    *,
    model: str,
    limit: int,
) -> list[tuple[int, float]]:
    """Find the top-k symbol_ids by semantic similarity to the query.

    Steps:
      1. Check fastembed availability — return [] if absent.
      2. Embed the query using analysis.embeddings.embed_query.
      3. Verify DB has embeddings for the configured model (model-mismatch guard).
      4. Load all stored vectors for the configured model.
      5. Compute cosine similarity for each stored vector vs. the query vector.
      6. Return the top-k (symbol_id, score) pairs sorted by score descending.

    Returns [] if:
      - fastembed is not available (graceful degradation)
      - embed_query returns b'' (model load error)
      - stored model != configured model (never silently mix model vectors)
      - no embeddings present in the DB for the configured model
      - any unexpected error occurs (logs a warning, never raises)

    Args:
        conn:   Open SQLite connection with read access.
        query:  The search query string to embed.
        model:  FastEmbed model name (must match the model used at index time).
        limit:  Maximum number of (symbol_id, score) pairs to return.

    Returns:
        list[tuple[int, float]] — at most `limit` entries, sorted by score descending.
        Each tuple is (symbol_id, cosine_score).
    """
    # Step 1: Fast-path when fastembed absent
    if not is_available():
        logger.debug("semantic_candidates: fastembed unavailable — returning []")
        return []

    try:
        return _semantic_candidates_impl(conn, query, model=model, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "semantic_candidates: unexpected error for query %r (model=%r): %s: %s — "
            "falling back to FTS5-only path.",
            query,
            model,
            type(exc).__name__,
            exc,
        )
        return []


def _semantic_candidates_impl(
    conn: sqlite3.Connection,
    query: str,
    *,
    model: str,
    limit: int,
) -> list[tuple[int, float]]:
    """Inner implementation — may raise; outer function catches everything.

    WHY split: the outer guard converts any exception to [] and logs a warning,
    keeping the read path non-crashing. Inner logic is cleaner without try/except.

    Performance:
    - SEAM_SEMANTIC_SCAN_CAP: SQL LIMIT caps how many rows are loaded.
    - numpy fast path: when numpy is importable (it is a fastembed transitive dep,
      so it is available whenever is_available() is True), all stored vectors are
      decoded into a single (N, dim) float32 matrix and cosine is computed in one
      matmul — ~1–5ms for 10k × 384-dim vs ~10–50ms for pure-Python per-row loop.
    - Falls back to pure-Python cosine_sim if numpy import fails (defensive fallback).
    """
    # Step 2: Embed the query
    query_vec = embed_query(query, model)
    if not query_vec:
        logger.debug(
            "semantic_candidates: embed_query returned empty bytes for %r — returning []",
            query,
        )
        return []

    # Step 3: Model-mismatch guard.
    # Check whether ANY embeddings row exists for the configured model.
    # If only rows from a different model are present, return [] immediately — do not
    # mix vectors from different embedding spaces (silently wrong scores).
    model_check = conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
    ).fetchone()[0]

    if model_check == 0:
        # No rows for this model. Could be: (a) no embeddings at all, (b) model mismatch.
        total_embeddings = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        if total_embeddings > 0:
            # Model mismatch is a user misconfiguration — warn so it's visible.
            logger.warning(
                "semantic_candidates: model mismatch — DB has %d embedding(s) for a "
                "different model; configured model=%r. "
                "Run 'seam init --semantic' to rebuild embeddings with the current model.",
                total_embeddings,
                model,
            )
        else:
            logger.debug(
                "semantic_candidates: no embeddings in DB for model=%r — returning [].",
                model,
            )
        return []

    # Step 4: WS2a — prefer the mmap vector store when SEAM_VECTOR_STORE=on.
    # The mmap path is faster (no per-query blob decode) and recall-complete (no
    # SEAM_SEMANTIC_SCAN_CAP truncation — the artifact was written from ALL rows at
    # embed time). Falls back transparently to the SQL path on any issue.
    if config.SEAM_VECTOR_STORE == "on":
        mmap_result = _try_mmap_path(conn, query_vec, model=model, limit=limit)
        if mmap_result is not None:
            return mmap_result
        # mmap_result is None → store unavailable/stale/corrupt → fall through to SQL

    # Step 5: SQL brute-force fallback.
    # Load stored vectors for this model.
    # SEAM_SEMANTIC_SCAN_CAP = 0 (default) means unlimited: no LIMIT is applied and
    # all rows for the model are loaded. A positive cap applies a LIMIT so at most
    # cap rows are fetched — an optional safety ceiling for memory-constrained operators.
    # SQLite treats LIMIT -1 as no limit, but we branch explicitly for clarity.
    scan_cap = config.SEAM_SEMANTIC_SCAN_CAP
    if scan_cap > 0:
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model = ? LIMIT ?",
            (model, scan_cap),
        ).fetchall()
    else:
        # Unlimited: fetch all rows for this model (no LIMIT clause).
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model = ?",
            (model,),
        ).fetchall()

    if not rows:
        return []

    # Log at DEBUG when a positive cap is set and the scan was actually bounded.
    # With scan_cap=0 (unlimited), no log — all rows were considered and there is
    # no cap-induced recall loss to warn about.
    if scan_cap > 0:
        actual_count = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
        ).fetchone()[0]
        if actual_count > scan_cap:
            logger.debug(
                "semantic_candidates: scan capped at %d rows (index has %d for model=%r); "
                "set SEAM_SEMANTIC_SCAN_CAP=0 to scan all vectors (unlimited).",
                scan_cap,
                actual_count,
                model,
            )

    # Step 6: Compute cosine similarity — numpy fast path when available.
    # numpy is a fastembed transitive dep: if is_available() is True, numpy is importable.
    # The try/except ImportError is a defensive fallback for pathological environments.
    try:
        import numpy as np

        # Decode query vector: (dim,) float32
        q_arr = np.frombuffer(query_vec, dtype=np.float32)  # shape (dim,)

        # Build (N, dim) matrix from all stored blobs in one pass.
        mat = np.stack(
            [np.frombuffer(bytes(row["vector"]), dtype=np.float32) for row in rows]
        )  # shape (N, dim)

        sym_ids = [row["symbol_id"] for row in rows]

        # Cosine similarity: (mat @ q) / (||mat|| * ||q||) per row.
        # dot: shape (N,)
        dots = mat @ q_arr
        norms_mat = np.linalg.norm(mat, axis=1)  # (N,)
        norm_q = float(np.linalg.norm(q_arr))

        if norm_q == 0.0:
            return []

        # Zero-norm rows get cosine=0 (safe: norms_mat == 0 → division → 0 via clip).
        with np.errstate(invalid="ignore", divide="ignore"):
            cosines = np.where(norms_mat == 0.0, 0.0, dots / (norms_mat * norm_q))

        # Pair (symbol_id, score) and get top-k via argsort.
        top_k_indices = np.argsort(-cosines)[:limit]
        return [(int(sym_ids[i]), float(cosines[i])) for i in top_k_indices]

    except ImportError:
        # numpy absent (defensive fallback — should not happen when is_available() is True).
        logger.debug(
            "semantic_candidates: numpy not available — falling back to pure-Python cosine."
        )

    # Pure-Python fallback (slower but correct; keeps the function working without numpy).
    scored: list[tuple[int, float]] = []
    for row in rows:
        score = cosine_sim(query_vec, bytes(row["vector"]))
        scored.append((row["symbol_id"], score))

    # Step 7: Sort by score descending, return top-k
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:limit]


def _try_mmap_path(
    conn: sqlite3.Connection,
    query_vec: bytes,
    *,
    model: str,
    limit: int,
) -> list[tuple[int, float]] | None:
    """Attempt to serve semantic candidates from the mmap vector store.

    Returns:
        A list of (symbol_id, score) tuples on success.
        None when the store is unavailable, stale, or any issue occurs —
        the caller then falls back to the SQL brute-force path.

    WHY return None vs []:
        None = "no artifact, please use SQL fallback".
        [] = "we searched but found nothing" (a valid semantic result).
        The distinction is critical for the fallback logic.

    Staleness check:
        Computes the current index-version token from the DB and compares it
        to the token stored in the artifact's metadata. A mismatch means the
        artifact was built from a different DB state → treat as absent.
    """
    artifact_dir = get_artifact_dir(conn)
    if artifact_dir is None:
        return None  # In-memory DB or no file path → SQL fallback

    store = load_store(artifact_dir, model)
    if store is None:
        logger.debug("_try_mmap_path: artifact unavailable for model=%r — using SQL", model)
        return None

    # Staleness check: recompute the index-version token from the current DB state.
    # If it differs from the stored token, the artifact is stale → SQL fallback.
    current_version = compute_index_version(conn, model)
    if current_version != store.index_version:
        logger.debug(
            "_try_mmap_path: stale artifact for model=%r "
            "(stored version=%r, current=%r) — using SQL fallback",
            model,
            store.index_version,
            current_version,
        )
        return None

    # Pass scan_cap so a positive cap slices the matrix rows considered in the mmap
    # path too. With the default cap=0 (unlimited), all rows in the artifact are
    # considered — no recall loss. A positive cap slices store.matrix[:scan_cap].
    scan_cap = config.SEAM_SEMANTIC_SCAN_CAP
    result = vector_store_top_k(store, query_vec, limit, scan_cap=scan_cap)
    logger.debug(
        "_try_mmap_path: mmap path returned %d result(s) for model=%r",
        len(result),
        model,
    )
    return result
