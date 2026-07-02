"""Persisted mmap vector store — WS2a Slice 1.

A LEAF module: imports only stdlib + numpy + seam.config. No server, no CLI, no DB coupling
beyond the connection-to-path helper (which uses PRAGMA database_list and reads no data).

WHY this exists:
  The existing semantic read path rebuilds the full (N, dim) float32 matrix from SQLite blobs on
  EVERY query. On a large codebase that is both slow (per-query decode overhead) and limited by
  SEAM_SEMANTIC_SCAN_CAP (a memory guard masquerading as a correctness limit that silently drops
  symbols beyond the cap from all semantic results). This module writes a compact on-disk artifact
  that can be memory-mapped zero-copy, so:
    1. The matrix is loaded once by the OS page cache — no per-query decode.
    2. All rows written at embed time are available — no cap-induced recall loss.

Artifact layout (three sibling files in the .seam/ directory):
  vectors.f32       — raw C-order float32 matrix, shape (N, dim). Memory-mappable directly.
  vectors.ids.i64   — raw int64 sidecar, shape (N,). Row-aligned with the matrix.
  vectors.meta.json — JSON metadata: model, dim, count, index_version, dtype, byteorder.

Public API:
  get_artifact_dir(conn)                             → Path | None
  write_store(store_dir, symbol_ids, matrix_or_blobs, model, dim, index_version) → None
  load_store(store_dir, model)                       → VectorStore | None
  top_k(store, query_vec_bytes, k)                  → list[tuple[int, float]]

Design decisions (see implementation-notes.html for full rationale):
  - numpy at module top is acceptable here: the vector store is only reached when the [semantic]
    extra is active (callers are gated by is_available()). Still guarded by try/except ImportError
    at module top so missing numpy degrades to None-returns throughout.
  - VectorStore is a NamedTuple: immutable, stdlib, lighter than dataclass; only ever read.
  - index_version = f"{count}:{max_id}": cheap, deterministic staleness token.
  - Atomic writes: temp file + os.replace per file, in order (meta last so load_store cannot
    see a meta that references a matrix not yet written).
  - Never raises: all public functions catch exceptions and log warnings, returning None/[].
"""

import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ── Numpy guard ───────────────────────────────────────────────────────────────
# numpy is imported at module scope because this module is ONLY reached when the
# [semantic] extra is installed (is_available() gates callers). The try/except
# ensures the module is importable in all environments — functions degrade to
# None/[] when numpy is absent rather than raising ImportError at import time.
try:
    import numpy as np

    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False
    np = None  # type: ignore[assignment]

# ── Artifact filenames ────────────────────────────────────────────────────────
_MATRIX_FILE = "vectors.f32"
_IDS_FILE = "vectors.ids.i64"
_META_FILE = "vectors.meta.json"

# ── VectorStore handle ────────────────────────────────────────────────────────


class VectorStore(NamedTuple):
    """Immutable handle to a loaded mmap vector store.

    Fields:
        matrix:        numpy memmap of shape (nrows, dim), dtype float32.
                       Zero-copy: backed by the OS page cache, not the Python heap.
        symbol_ids:    numpy int64 array of shape (nrows,), row-aligned with matrix.
        model:         Embedding model name (e.g. "BAAI/bge-small-en-v1.5").
        dim:           Embedding dimensionality (e.g. 384).
        nrows:         Number of rows in the matrix.
        index_version: Staleness token — compare against a freshly computed token to
                       detect whether the artifact matches the current DB state.

    WHY 'nrows' instead of 'count': tuple.count is a built-in method; shadowing it
        with a NamedTuple field of the same name causes a mypy type error. 'nrows'
        is equally clear and avoids the conflict.
    """

    matrix: "np.ndarray"  # type: ignore[type-arg]
    symbol_ids: "np.ndarray"  # type: ignore[type-arg]
    model: str
    dim: int
    nrows: int
    index_version: str


# ── get_artifact_dir ──────────────────────────────────────────────────────────


def get_artifact_dir(conn: sqlite3.Connection) -> Path | None:
    """Derive the .seam/ artifact directory from the SQLite connection.

    Uses PRAGMA database_list to find the 'main' DB file path, then returns
    its parent directory (the .seam/ folder beside the DB file).

    Returns None (never raises) when:
      - The connection is to an in-memory DB (empty file path).
      - The PRAGMA fails or the 'main' database is not found.
      - Any other unexpected error occurs.

    WHY derive from the connection: both the embedding indexer and the semantic
    read path can call this without needing an explicit directory argument, so
    neither their signatures nor their callers change.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for row in rows:
            # PRAGMA database_list columns: seq, name, file
            # 'main' is always the primary attached database.
            if row[1] == "main" and row[2]:
                return Path(row[2]).parent
        # In-memory or unattached DB — no artifact directory.
        logger.debug("get_artifact_dir: no file-backed 'main' database found — skipping artifact")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_artifact_dir: PRAGMA database_list failed (%s: %s) — skipping artifact",
            type(exc).__name__,
            exc,
        )
        return None


# ── write_store ───────────────────────────────────────────────────────────────


def write_store(
    store_dir: Path,
    symbol_ids: list[int],
    matrix_or_blobs: "list[bytes] | np.ndarray",
    model: str,
    dim: int,
    index_version: str,
) -> None:
    """Write (or refresh) the vector store artifact atomically.

    Writes three sibling files into store_dir:
      vectors.f32       — raw float32 matrix, C-order, shape (N, dim)
      vectors.ids.i64   — raw int64 sidecar, shape (N,), row-aligned
      vectors.meta.json — JSON metadata header

    The write is ATOMIC: each file is written to a temp file in store_dir
    and then os.replace'd into place. A failed/interrupted write never leaves
    a readable partial artifact (the meta file — required for load_store — is
    written LAST so any earlier failure leaves the old meta intact).

    Args:
        store_dir:        Directory where artifacts are stored (the .seam/ dir).
        symbol_ids:       List of int symbol IDs, row-aligned with matrix rows.
        matrix_or_blobs:  Either a (N, dim) float32 numpy array OR a list of N
                          float32 byte blobs (each len == dim * 4).
        model:            Embedding model name.
        dim:              Embedding dimensionality.
        index_version:    Staleness token (e.g. "123:456"). Stored in meta and
                          compared at load time to detect stale artifacts.

    Never raises — callers treat a missing/stale artifact as a transparent
    fallback to the SQL path. Logs a warning on any failure.
    """
    if not _NP_AVAILABLE:
        logger.debug("write_store: numpy unavailable — skipping artifact write")
        return

    try:
        _write_store_impl(store_dir, symbol_ids, matrix_or_blobs, model, dim, index_version)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "write_store: failed to write vector artifact to %s (%s: %s) — "
            "semantic search will use the SQLite fallback path.",
            store_dir,
            type(exc).__name__,
            exc,
        )


def _write_store_impl(
    store_dir: Path,
    symbol_ids: list[int],
    matrix_or_blobs: "list[bytes] | np.ndarray",
    model: str,
    dim: int,
    index_version: str,
) -> None:
    """Inner write implementation — may raise; outer function catches everything."""
    count = len(symbol_ids)
    if count == 0:
        logger.debug("write_store: 0 symbols — skipping artifact write")
        return

    # ── Build the numpy matrix ─────────────────────────────────────────────
    # Accept either a pre-built numpy array or a list of float32 byte blobs.
    # Blobs are the natural output of embed_texts(); a numpy array is accepted
    # for callers that already have the matrix (e.g. tests).
    if isinstance(matrix_or_blobs, list):
        # Decode blobs → numpy float32 matrix
        mat = np.stack(
            [np.frombuffer(b, dtype=np.float32) for b in matrix_or_blobs]
        ).astype(np.float32, copy=False)
    else:
        # Already a numpy array — ensure float32 C-order for safe mmap
        mat = np.ascontiguousarray(matrix_or_blobs, dtype=np.float32)

    ids_arr = np.array(symbol_ids, dtype=np.int64)

    # Sanity-check alignment: matrix rows must equal ids length
    if mat.shape != (count, dim):
        raise ValueError(
            f"write_store: matrix shape {mat.shape} != expected ({count}, {dim})"
        )
    if len(ids_arr) != count:
        raise ValueError(
            f"write_store: ids length {len(ids_arr)} != matrix rows {count}"
        )

    # ── Atomic write: temp file → os.replace ──────────────────────────────
    # Write matrix + ids first, then meta LAST.
    # If the matrix write fails, the old meta (if any) remains intact → load_store
    # will still validate the old artifact correctly. The meta is the sentinel for
    # "this artifact is complete" — we never write it until matrix+ids are on disk.
    #
    # WHY try/finally for temp-file cleanup:
    #   NamedTemporaryFile(delete=False) creates the file immediately; if any step
    #   between creation and os.replace raises, the temp file is left on disk and
    #   never cleaned by the OS. The try/finally tracks each pending temp path and
    #   sets it to None after a successful os.replace (which moves/removes the temp).
    #   The finally block unlinks any surviving temp files so disk leaks don't
    #   accumulate in .seam/ on repeated write failures (e.g. disk-full).

    matrix_path = store_dir / _MATRIX_FILE
    ids_path = store_dir / _IDS_FILE
    meta_path = store_dir / _META_FILE

    meta = {
        "model": model,
        "dim": dim,
        "count": count,
        "index_version": index_version,
        "dtype": "float32",
        "byteorder": "little",
    }

    mat_tmp: str | None = None
    ids_tmp: str | None = None
    meta_tmp: str | None = None
    try:
        # Write matrix
        with tempfile.NamedTemporaryFile(
            dir=store_dir, suffix=".tmp", delete=False
        ) as f:
            mat_tmp = f.name
            mat.tofile(f)

        # Write ids
        with tempfile.NamedTemporaryFile(
            dir=store_dir, suffix=".tmp", delete=False
        ) as f:
            ids_tmp = f.name
            ids_arr.tofile(f)

        # Write meta (LAST — so load_store never sees a meta without a valid matrix)
        with tempfile.NamedTemporaryFile(
            dir=store_dir, suffix=".tmp", delete=False, mode="w", encoding="utf-8"
        ) as f:
            meta_tmp = f.name
            json.dump(meta, f)

        # Atomic rename: all three files in order.
        # After each successful os.replace, the temp path no longer exists on disk
        # (rename consumed it), so we null it out to skip the finally cleanup.
        os.replace(mat_tmp, matrix_path)
        mat_tmp = None
        os.replace(ids_tmp, ids_path)
        ids_tmp = None
        os.replace(meta_tmp, meta_path)
        meta_tmp = None
    finally:
        # Clean up any temp files that were created but not yet moved to their
        # final names. On success all three are None (moved); on failure one or
        # more may still exist on disk.
        for _tmp in (mat_tmp, ids_tmp, meta_tmp):
            if _tmp is not None:
                try:
                    os.unlink(_tmp)
                except OSError:
                    pass  # Best-effort cleanup; ignore errors (e.g. already gone)

    logger.info(
        "write_store: wrote %d vectors (dim=%d, model=%r) to %s",
        count,
        dim,
        model,
        store_dir,
    )


# ── load_store ────────────────────────────────────────────────────────────────


def load_store(store_dir: Path, model: str) -> VectorStore | None:
    """Load the vector store artifact from store_dir, returning None on any problem.

    Validates:
      - All three artifact files exist.
      - Meta JSON is parseable and has all required fields.
      - Meta model matches the requested model (never silently mix embedding spaces).
      - Meta dtype == "float32" and byteorder == "little" (cross-platform safety).
      - Matrix file size == count * dim * 4 bytes (not truncated/corrupt).
      - Ids file size == count * 8 bytes (int64 is 8 bytes, not truncated/corrupt).

    Returns:
        A VectorStore (with mmap'd matrix) on success.
        None on ANY problem — absent files, corrupt/truncated, model mismatch, numpy absent.
        NEVER raises.

    WHY mmap: numpy.memmap is zero-copy — the OS page cache backs the array. A long-lived
    MCP server reuses the mapping across calls (no per-query decode). A CLI one-shot still
    avoids the per-query decode by reading the prebuilt file rather than re-decoding blobs.
    """
    if not _NP_AVAILABLE:
        logger.debug("load_store: numpy unavailable — returning None")
        return None

    try:
        return _load_store_impl(store_dir, model)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "load_store: failed to load vector artifact from %s (%s: %s) — "
            "semantic search will use the SQLite fallback path.",
            store_dir,
            type(exc).__name__,
            exc,
        )
        return None


def _load_store_impl(store_dir: Path, model: str) -> VectorStore | None:
    """Inner load implementation — may raise; outer function catches everything."""
    matrix_path = store_dir / _MATRIX_FILE
    ids_path = store_dir / _IDS_FILE
    meta_path = store_dir / _META_FILE

    # ── Step 1: Check file presence ───────────────────────────────────────
    if not meta_path.exists():
        logger.debug("load_store: meta file absent at %s — no artifact", meta_path)
        return None
    if not matrix_path.exists():
        logger.debug("load_store: matrix file absent at %s — no artifact", matrix_path)
        return None
    if not ids_path.exists():
        logger.debug("load_store: ids file absent at %s — no artifact", ids_path)
        return None

    # ── Step 2: Parse and validate metadata ───────────────────────────────
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "load_store: cannot read meta at %s (%s: %s) — falling back to SQL",
            meta_path,
            type(exc).__name__,
            exc,
        )
        return None

    required_fields = {"model", "dim", "count", "index_version", "dtype", "byteorder"}
    if not required_fields.issubset(meta):
        logger.warning(
            "load_store: meta at %s missing fields %s — falling back to SQL",
            meta_path,
            required_fields - set(meta),
        )
        return None

    stored_model = meta["model"]
    dim = int(meta["dim"])
    count = int(meta["count"])
    index_version = str(meta["index_version"])
    dtype = meta["dtype"]
    byteorder = meta["byteorder"]

    # ── Step 3: Model mismatch guard ─────────────────────────────────────
    if stored_model != model:
        logger.debug(
            "load_store: model mismatch (artifact=%r, requested=%r) — falling back to SQL",
            stored_model,
            model,
        )
        return None

    # ── Step 4: Dtype / byteorder safety ─────────────────────────────────
    # Only trust an artifact built with float32 little-endian (numpy default).
    # A foreign or wrong-endian file is rejected rather than read silently wrong.
    if dtype != "float32" or byteorder != "little":
        logger.warning(
            "load_store: artifact at %s has dtype=%r byteorder=%r (expected float32/little) "
            "— falling back to SQL",
            store_dir,
            dtype,
            byteorder,
        )
        return None

    # ── Step 5: Size validation (truncation / corruption check) ───────────
    expected_matrix_bytes = count * dim * 4  # float32 = 4 bytes
    expected_ids_bytes = count * 8  # int64 = 8 bytes

    actual_matrix_bytes = matrix_path.stat().st_size
    actual_ids_bytes = ids_path.stat().st_size

    if actual_matrix_bytes != expected_matrix_bytes:
        logger.warning(
            "load_store: matrix file size mismatch at %s "
            "(expected %d bytes for %d×%d float32, got %d) — falling back to SQL",
            matrix_path,
            expected_matrix_bytes,
            count,
            dim,
            actual_matrix_bytes,
        )
        return None

    if actual_ids_bytes != expected_ids_bytes:
        logger.warning(
            "load_store: ids file size mismatch at %s "
            "(expected %d bytes for %d int64, got %d) — falling back to SQL",
            ids_path,
            expected_ids_bytes,
            count,
            actual_ids_bytes,
        )
        return None

    # ── Step 6: mmap the matrix and ids ───────────────────────────────────
    # numpy.memmap is zero-copy: the OS page cache backs the array. The file
    # must not be deleted while the memmap is alive (standard mmap contract).
    matrix = np.memmap(str(matrix_path), dtype=np.float32, mode="r", shape=(count, dim))
    symbol_ids_arr = np.fromfile(str(ids_path), dtype=np.int64)

    logger.debug(
        "load_store: loaded %d vectors (dim=%d, model=%r) from %s",
        count,
        dim,
        model,
        store_dir,
    )
    return VectorStore(
        matrix=matrix,
        symbol_ids=symbol_ids_arr,
        model=model,
        dim=dim,
        nrows=count,
        index_version=index_version,
    )


# ── top_k ─────────────────────────────────────────────────────────────────────


def top_k(
    store: VectorStore,
    query_vec_bytes: bytes,
    k: int,
    *,
    scan_cap: int = 0,
) -> list[tuple[int, float]]:
    """Return the k most-similar symbol ids from the mmap store.

    Computes cosine similarity between the query vector and every row in the
    store's matrix using a single numpy matmul — the same arithmetic as the
    existing SQL brute-force path in seam/query/semantic.py, so results are
    byte-identical for the same inputs.

    Args:
        store:           A loaded VectorStore (from load_store).
        query_vec_bytes: Float32 bytes of the query embedding (len == dim * 4).
        k:               Maximum number of results to return.
        scan_cap:        Row ceiling for the matrix scan. 0 (default) = unlimited
                         (consider all rows in the artifact). A positive value slices
                         store.matrix[:scan_cap], mirroring SEAM_SEMANTIC_SCAN_CAP in
                         the SQL fallback path. Use 0 unless memory is constrained.

    Returns:
        list[tuple[int, float]] — at most k entries, sorted by cosine score
        descending. Each tuple is (symbol_id, cosine_score). Returns [] on any
        problem (dimension mismatch, zero-norm query, numpy absent). Never raises.

    WHY same math as SQL path:
        Correctness requires that the mmap path and the SQL fallback produce
        identical results for the same query on the same data. Both use:
          cosine = (mat @ q) / (norm(mat, axis=1) * norm(q))
        with np.errstate(invalid="ignore", divide="ignore") for zero norms.
    """
    if not _NP_AVAILABLE:
        return []

    try:
        return _top_k_impl(store, query_vec_bytes, k, scan_cap=scan_cap)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "top_k: unexpected error (%s: %s) — returning []",
            type(exc).__name__,
            exc,
        )
        return []


def _top_k_impl(
    store: VectorStore,
    query_vec_bytes: bytes,
    k: int,
    *,
    scan_cap: int = 0,
) -> list[tuple[int, float]]:
    """Inner top_k implementation — may raise; outer function catches everything."""
    if not query_vec_bytes:
        return []

    # Decode query vector
    q_arr = np.frombuffer(query_vec_bytes, dtype=np.float32)

    # Dimension check: query must match the store's dimensionality
    if len(q_arr) != store.dim:
        logger.warning(
            "top_k: query dim %d != store dim %d — returning []",
            len(q_arr),
            store.dim,
        )
        return []

    norm_q = float(np.linalg.norm(q_arr))
    if norm_q == 0.0:
        return []

    # Apply optional row ceiling: scan_cap > 0 slices the matrix to at most scan_cap
    # rows, mirroring the SQL LIMIT in the brute-force path. scan_cap = 0 = unlimited.
    mat = store.matrix[:scan_cap] if scan_cap > 0 else store.matrix  # float32 memmap
    sym_ids = store.symbol_ids[:scan_cap] if scan_cap > 0 else store.symbol_ids  # int64

    # Cosine similarity: (mat @ q) / (||mat_row|| * ||q||) — same formula as SQL path
    dots = mat @ q_arr  # shape (nrows,)
    norms_mat = np.linalg.norm(mat, axis=1)  # shape (nrows,)

    with np.errstate(invalid="ignore", divide="ignore"):
        cosines = np.where(norms_mat == 0.0, 0.0, dots / (norms_mat * norm_q))

    # Top-k via argsort descending
    top_k_indices = np.argsort(-cosines)[:k]
    return [(int(sym_ids[i]), float(cosines[i])) for i in top_k_indices]


# ── compute_index_version ──────────────────────────────────────────────────────


def compute_index_version(conn: sqlite3.Connection, model: str) -> str:
    """Compute a cheap staleness token from the embeddings table.

    Token = f"{count}:{max_symbol_id}" for the given model. This is:
      - Cheap: two SQL aggregates in one pass.
      - Deterministic: same DB state → same token.
      - Sufficient: detects any row addition or removal for the model.

    Returns "0:0" when there are no rows for the model (never raises).

    Limitation: does NOT detect in-place vector updates (same count/max_id,
    different content). This scenario does not occur in Seam's write path
    (full re-index via INSERT OR REPLACE always writes fresh rows).
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*), MAX(symbol_id) FROM embeddings WHERE model = ?",
            (model,),
        ).fetchone()
        count = row[0] or 0
        max_id = row[1] or 0
        return f"{count}:{max_id}"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compute_index_version: SQL error for model=%r (%s: %s) — returning '0:0'",
            model,
            type(exc).__name__,
            exc,
        )
        return "0:0"
