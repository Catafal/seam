"""Seam KNN scale benchmark — WS2b S4.

Measures brute-force (numpy matmul cosine) vs sqlite-vec vec0 KNN query latency
and recall@K parity across configurable synthetic embedding counts.

IMPORTANT — what this benchmark actually shows with sqlite-vec v0.1.9:
  sqlite-vec's vec0 virtual table performs EXACT brute-force KNN — it has NO
  approximate-nearest-neighbour index (no HNSW, no IVF). As a result the vec0
  tier is currently SLOWER than the numpy matmul path at all tested scales, with
  perfect recall@K = 1.000 (exact = brute-force agreement). Example results:
    N=10k:   numpy 0.6ms  vs  vec0 3.9ms  (~5× slower)
    N=100k:  numpy 7.1ms  vs  vec0 41.6ms  (~6× slower)
  This benchmark exists to (a) measure the empirical gap and (b) confirm recall
  parity. Once sqlite-vec ships a true approximate index, speedup > 1× will appear
  here and SEAM_VEC_ANN_MIN_ROWS can be tuned to the real crossover point.

This gives empirical grounding for the current scaffold state and lets operators
track when sqlite-vec transitions from exact to approximate indexing.

HOW TO RUN
----------
Prerequisites:
    pip install 'seam-code[semantic-ann]'   # adds sqlite_vec + numpy

Then:
    make bench-semantic-ann
    # or directly:
    python benchmarks/semantic_ann_scale.py                        # tiny default scales
    python benchmarks/semantic_ann_scale.py --sizes 50000 100000  # larger scales

The benchmark uses SYNTHETIC float32 embeddings — no real model, no network calls,
no existing index required. Fully offline and reproducible (fixed seed).

NOT part of `make gate` (mirrors bench-semantic / soak — local/optional-CI only).
"""

import argparse
import dataclasses
import os
import sqlite3
import struct
import sys
import tempfile
import time
from pathlib import Path

# Make repo root importable when run directly as a script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Availability guards ────────────────────────────────────────────────────────

# Self-skip if sqlite_vec is not importable.
# WHY: if the ANN extension is absent the comparison is impossible — no point
# measuring brute-force alone when the benchmark's goal is the cross-tier comparison.
try:
    import sqlite_vec as _sqlite_vec_check  # noqa: F401
    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    _SQLITE_VEC_AVAILABLE = False

# numpy is required for the brute-force matmul path (the path users actually hit).
try:
    import numpy as np  # noqa: F401 — imported again below in functions
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────

# Default scales for a fast smoke run (completes in seconds).
# Full-scale numbers: 50_000, 100_000, 250_000 — pass via --sizes.
_DEFAULT_SIZES = [1_000, 5_000]

# Embedding dimensionality — matches BAAI/bge-small-en-v1.5 (the default model).
_DEFAULT_DIM = 384

# Number of query vectors to average over per scale.
_DEFAULT_QUERIES = 20

# K for recall@K — number of nearest-neighbour candidates to compare.
_DEFAULT_K = 10

# Synthetic model name — used as the model column in the embeddings table.
_BENCH_MODEL = "seam-bench-synthetic"


# ── Public result dataclass ────────────────────────────────────────────────────


@dataclasses.dataclass
class ScaleResult:
    """Timing and recall measurement for one (n_rows, dim) configuration.

    Fields:
        n_rows:         Number of synthetic embeddings indexed.
        dim:            Embedding dimensionality.
        queries:        Number of query vectors averaged over.
        k:              Top-K used for recall@K parity.
        brute_ms:       Average per-query brute-force latency (ms). Numpy matmul.
        ann_ms:         Average per-query ANN latency (ms). vec0 KNN MATCH.
        speedup:        brute_ms / ann_ms (>1 means ANN is faster).
        recall_at_k:    Average overlap fraction between ANN top-K and BF top-K
                        (1.0 = perfect parity; 0.0 = completely different results).
        ann_available:  True when the ANN tier was successfully used.
    """

    n_rows: int
    dim: int
    queries: int
    k: int
    brute_ms: float
    ann_ms: float
    speedup: float
    recall_at_k: float
    ann_available: bool


# ── Database helpers ───────────────────────────────────────────────────────────


def _make_bench_db(db_path: str, embeddings: list[bytes], *, dim: int) -> sqlite3.Connection:
    """Create a minimal embeddings DB with synthetic rows for the benchmark.

    Uses the same table/column structure as the real Seam embeddings table so
    that index_vec._build_vec_index() and the SQL brute-force path both work
    without any adaptation.

    Args:
        db_path:     Filesystem path for the SQLite DB (not :memory: — vec0 needs a file).
        embeddings:  List of float32 blobs (one per row), already normalised.
        dim:         Embedding dimensionality (stored in the dim column).

    Returns:
        Open, writable sqlite3.Connection (row_factory = sqlite3.Row).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Minimal schema: just what vec_index._build_vec_index and the brute-force path need.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            symbol_id  INTEGER PRIMARY KEY,
            model      TEXT    NOT NULL,
            dim        INTEGER NOT NULL,
            vector     BLOB    NOT NULL
        )
        """
    )
    conn.commit()

    # Bulk-insert synthetic embeddings.
    # symbol_id = 1-based row index; model = bench sentinel; dim = embedding dim.
    conn.executemany(
        "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
        [
            (i + 1, _BENCH_MODEL, dim, blob)
            for i, blob in enumerate(embeddings)
        ],
    )
    conn.commit()
    return conn


# ── Brute-force timing ────────────────────────────────────────────────────────


def _run_brute_force(
    conn: sqlite3.Connection,
    query_vecs: list[bytes],
    *,
    k: int,
) -> tuple[float, list[list[int]]]:
    """Measure the brute-force numpy matmul path and collect top-K results.

    This mirrors what _semantic_candidates_impl does: load all rows from the
    embeddings table, build a (N, dim) float32 matrix, compute cosine for each
    query vector via matmul, return the top-K symbol_ids.

    WHY inline numpy matmul (not calling _semantic_candidates_impl):
        _semantic_candidates_impl includes model-mismatch guard, is_available()
        check, and fallback paths that add noise to the timing. The matmul is
        the actual bottleneck — isolating it gives a clean measurement that
        matches what the production code does internally.

    Returns:
        (avg_ms_per_query, list_of_top_k_id_lists)
        avg_ms_per_query: average wall-clock ms for the scoring step per query.
        list_of_top_k_id_lists: one list of symbol_id ints per query (for recall).
    """
    import numpy as np  # noqa: PLC0415 (lazy — guard already checked _NUMPY_AVAILABLE)

    # Load all embedding rows once (outside the per-query loop).
    rows = conn.execute(
        "SELECT symbol_id, vector FROM embeddings WHERE model = ?", (_BENCH_MODEL,)
    ).fetchall()

    sym_ids = [int(row["symbol_id"]) for row in rows]
    # Build (N, dim) matrix — the same operation _semantic_candidates_impl does.
    mat = np.stack([np.frombuffer(bytes(row["vector"]), dtype=np.float32) for row in rows])

    # Pre-normalise matrix rows for cosine computation: mat_norm[i] = ||mat[i]||.
    mat_norms = np.linalg.norm(mat, axis=1)  # (N,)

    all_top_k: list[list[int]] = []
    total_ms = 0.0

    for q_bytes in query_vecs:
        q = np.frombuffer(q_bytes, dtype=np.float32)
        norm_q = float(np.linalg.norm(q))
        if norm_q == 0.0:
            all_top_k.append([])
            continue

        # Time only the scoring step (matmul + argsort) — same as the hot path.
        t0 = time.perf_counter()
        dots = mat @ q  # (N,)
        with np.errstate(invalid="ignore", divide="ignore"):
            cosines = np.where(mat_norms == 0.0, 0.0, dots / (mat_norms * norm_q))
        top_k_indices = np.argsort(-cosines)[:k]
        total_ms += (time.perf_counter() - t0) * 1000.0

        all_top_k.append([sym_ids[i] for i in top_k_indices])

    n = max(len(query_vecs), 1)
    return total_ms / n, all_top_k


# ── ANN timing ────────────────────────────────────────────────────────────────


def _run_ann(
    conn: sqlite3.Connection,
    query_vecs: list[bytes],
    *,
    k: int,
) -> tuple[float, list[list[int]]]:
    """Measure the sqlite-vec vec0 KNN MATCH path and collect top-K results.

    Loads the extension once (outside the query loop) then issues one KNN MATCH
    query per synthetic query vector. Times only the MATCH query itself.

    WHY not call _try_vec_path:
        _try_vec_path includes a per-process probe cache check, vec_meta staleness
        check, and extension load — all relevant for production but noise for timing.
        For the benchmark we load the extension once, then isolate the KNN query.

    Returns:
        (avg_ms_per_query, list_of_top_k_id_lists)
    """
    from seam.indexer.vec_index import VEC_TABLE  # noqa: PLC0415
    from seam.query.vec_extension import load_vec_extension  # noqa: PLC0415

    # Load extension once — it stays active for the connection lifetime.
    if not load_vec_extension(conn):
        raise RuntimeError("ANN benchmark: failed to load sqlite-vec extension")

    all_top_k: list[list[int]] = []
    total_ms = 0.0

    for q_bytes in query_vecs:
        t0 = time.perf_counter()
        rows = conn.execute(
            f"SELECT rowid, distance FROM {VEC_TABLE} "
            f"WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (q_bytes, k),
        ).fetchall()
        total_ms += (time.perf_counter() - t0) * 1000.0
        all_top_k.append([int(row[0]) for row in rows])

    n = max(len(query_vecs), 1)
    return total_ms / n, all_top_k


# ── Recall@K computation ──────────────────────────────────────────────────────


def _recall_at_k(bf_results: list[list[int]], ann_results: list[list[int]], k: int) -> float:
    """Compute the mean recall@K (overlap fraction) between ANN and brute-force results.

    recall@K for one query = |ANN top-K ∩ BF top-K| / K.
    Averaged over all queries.

    WHY this metric:
        It measures how well the ANN approximation matches the exact result.
        A score of 1.0 means ANN found exactly the same top-K neighbours as
        brute-force (perfect recall). Lower means approximation error is visible.
        Standard ANN benchmark metric (mirrors ann-benchmarks.com convention).
    """
    total = 0.0
    n = 0
    for bf, ann in zip(bf_results, ann_results):
        if not bf:
            continue
        overlap = len(set(bf[:k]) & set(ann[:k]))
        total += overlap / k
        n += 1
    return total / max(n, 1)


# ── ANN index build (bypasses MIN_ROWS gate for the benchmark) ────────────────


def _build_ann_index(conn: sqlite3.Connection) -> None:
    """Build the vec0 ANN index on the benchmark DB, bypassing the MIN_ROWS gate.

    WHY bypass the gate:
        index_vec() gates on SEAM_VEC_ANN_MIN_ROWS (default 50k) to avoid the
        build overhead at small scales. The benchmark intentionally measures at
        small scales too, so we call _build_vec_index directly (the inner
        implementation that does the actual work). This is a benchmark-only
        bypass — production always goes through index_vec()'s gate.
    """
    from seam.indexer.vec_index import _build_vec_index  # noqa: PLC0415

    n = _build_vec_index(conn, model=_BENCH_MODEL)
    if n <= 0:
        raise RuntimeError(f"ANN index build returned {n} — sqlite-vec may not be available")


# ── Main measurement function (public — used by smoke test) ───────────────────


def run_scale(
    n_rows: int,
    *,
    dim: int = _DEFAULT_DIM,
    queries: int = _DEFAULT_QUERIES,
    k: int = _DEFAULT_K,
    seed: int = 42,
) -> ScaleResult:
    """Run the brute-force vs ANN comparison for one scale point.

    Generates synthetic float32 embeddings, builds a temp DB + ANN index,
    then measures both paths over `queries` synthetic query vectors.

    Args:
        n_rows:  Number of synthetic embedding rows to index.
        dim:     Embedding dimensionality.
        queries: Number of random query vectors to average over.
        k:       Top-K for both retrieval and recall@K.
        seed:    RNG seed for reproducibility.

    Returns:
        ScaleResult with latency, speedup, and recall@K fields.
        If ANN is unavailable, ann_ms and speedup are 0.0 and ann_available is False.

    Raises:
        ImportError: if numpy is absent (required for both paths).
        RuntimeError: if ANN index build fails (should not happen when sqlite_vec is available).
    """
    import numpy as np  # noqa: PLC0415

    rng = np.random.default_rng(seed)

    # Generate N synthetic normalised float32 embedding vectors.
    # WHY normalised: cosine similarity and vec0 cosine distance are both defined for
    # unit vectors. Normalising upfront matches what fastembed outputs (normalised embeddings).
    raw = rng.standard_normal((n_rows, dim)).astype(np.float32)
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normed = raw / norms  # (N, dim) normalised float32

    # Pack each row into a float32 blob for SQLite storage.
    blobs = [struct.pack(f"{dim}f", *row) for row in normed]

    # Generate Q normalised query vectors with a different seed (no overlap with corpus).
    q_raw = rng.standard_normal((queries, dim)).astype(np.float32)
    q_norms = np.linalg.norm(q_raw, axis=1, keepdims=True)
    q_norms = np.where(q_norms == 0, 1.0, q_norms)
    q_normed = q_raw / q_norms
    query_vecs = [struct.pack(f"{dim}f", *row) for row in q_normed]

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "bench.db")
        conn = _make_bench_db(db_path, blobs, dim=dim)

        # ── Brute-force measurement (always available) ─────────────────────
        brute_ms, bf_results = _run_brute_force(conn, query_vecs, k=k)

        # ── ANN measurement (only if sqlite_vec is available) ─────────────
        ann_available = _SQLITE_VEC_AVAILABLE
        ann_ms = 0.0
        ann_results: list[list[int]] = []

        if ann_available:
            try:
                _build_ann_index(conn)
                ann_ms, ann_results = _run_ann(conn, query_vecs, k=k)
            except Exception as exc:  # noqa: BLE001
                # ANN setup or query failed — fall back gracefully.
                print(
                    f"  [warn] ANN measurement failed at N={n_rows}: {exc}",
                    file=sys.stderr,
                )
                ann_available = False
                ann_ms = 0.0
                ann_results = []

        conn.close()

    # ── Compute derived metrics ────────────────────────────────────────────
    speedup = (brute_ms / ann_ms) if (ann_available and ann_ms > 0.0) else 0.0
    recall = _recall_at_k(bf_results, ann_results, k) if ann_available else 0.0

    return ScaleResult(
        n_rows=n_rows,
        dim=dim,
        queries=queries,
        k=k,
        brute_ms=brute_ms,
        ann_ms=ann_ms,
        speedup=speedup,
        recall_at_k=recall,
        ann_available=ann_available,
    )


# ── Pretty-print helpers ──────────────────────────────────────────────────────

_COL_WIDTH = 90


def _print_header(k: int) -> None:
    print("\n" + "=" * _COL_WIDTH)
    print("Seam ANN Scale Benchmark — brute-force (numpy matmul) vs ANN (sqlite-vec vec0 KNN)")
    print("=" * _COL_WIDTH)
    print(
        f"{'N rows':>9}  {'dim':>5}  {'BF ms':>8}  {'ANN ms':>8}  "
        f"{'speedup':>8}  {'recall@' + str(k):>10}  {'ANN?':>5}"
    )
    print("-" * _COL_WIDTH)


def _print_row(result: ScaleResult) -> None:
    ann_tag = "yes" if result.ann_available else "no"
    ann_ms_str = f"{result.ann_ms:.3f}" if result.ann_available else "n/a"
    speedup_str = f"{result.speedup:.1f}x" if result.ann_available else "n/a"
    recall_str = f"{result.recall_at_k:.3f}" if result.ann_available else "n/a"
    print(
        f"{result.n_rows:>9,}  {result.dim:>5}  {result.brute_ms:>8.3f}  "
        f"{ann_ms_str:>8}  {speedup_str:>8}  {recall_str:>10}  {ann_tag:>5}"
    )


def _print_footer(results: list[ScaleResult]) -> None:
    print("=" * _COL_WIDTH)

    # Interpretation hint: find the crossover (smallest N where ANN is faster).
    crossover = next(
        (r.n_rows for r in results if r.ann_available and r.speedup > 1.0),
        None,
    )
    if crossover is not None:
        print(f"\nCrossover: ANN is faster than brute-force above ~{crossover:,} rows.")
        print(
            f"SEAM_VEC_ANN_MIN_ROWS={crossover} is the measured minimum for a net benefit "
            f"on this hardware."
        )
    else:
        print(
            "\nNo crossover observed in the measured range — "
            "try larger N (e.g. --sizes 50000 100000 250000)."
        )

    if any(r.ann_available for r in results):
        avg_recall = sum(r.recall_at_k for r in results if r.ann_available) / max(
            sum(1 for r in results if r.ann_available), 1
        )
        print(f"Mean recall@{results[0].k} (ANN vs brute-force): {avg_recall:.3f}")

    print(
        "\nNOTE: Results are for SYNTHETIC normalised float32 embeddings, not real code "
        "embeddings. Latencies scale with hardware. Use --sizes for larger-scale measurement."
    )
    print(
        "NOTE: sqlite-vec v0.1.9 performs EXACT brute-force KNN (no HNSW/IVF ANN index). "
        "speedup < 1 is expected — the vec0 tier is a forward-compatible scaffold, not a "
        "performance feature yet. Re-run after upgrading sqlite-vec to check for approximate "
        "indexing (speedup > 1 will appear when HNSW/IVF is available)."
    )
    print()


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses args, runs the benchmark, prints the summary table."""
    parser = argparse.ArgumentParser(
        description="Seam ANN scale benchmark (WS2b S4). "
        "Measures brute-force vs ANN latency and recall@K on synthetic embeddings."
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=_DEFAULT_SIZES,
        metavar="N",
        help=(
            f"Embedding counts to benchmark (default: {_DEFAULT_SIZES}). "
            "Pass multiple values for a multi-scale run, e.g. --sizes 10000 50000 100000."
        ),
    )
    parser.add_argument(
        "--dim",
        type=int,
        default=_DEFAULT_DIM,
        help=f"Embedding dimensionality (default: {_DEFAULT_DIM}, matches bge-small-en-v1.5).",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=_DEFAULT_QUERIES,
        help=f"Number of query vectors to average latency over (default: {_DEFAULT_QUERIES}).",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=_DEFAULT_K,
        help=f"Top-K for retrieval and recall@K (default: {_DEFAULT_K}).",
    )
    args = parser.parse_args(argv)

    # ── Availability checks ──────────────────────────────────────────────
    if not _NUMPY_AVAILABLE:
        print(
            "numpy is not installed — benchmark skipped.\n"
            "To run: pip install 'seam-code[semantic-ann]'",
            file=sys.stderr,
        )
        return 0  # skip, not error (mirrors bench-semantic convention)

    if not _SQLITE_VEC_AVAILABLE:
        print(
            "sqlite_vec is not installed — benchmark skipped.\n"
            "To run: pip install 'seam-code[semantic-ann]'\n"
            "        make bench-semantic-ann",
            file=sys.stderr,
        )
        return 0  # skip, not error

    sizes = sorted(set(max(1, n) for n in args.sizes))
    _print_header(args.k)

    results: list[ScaleResult] = []
    for n in sizes:
        print(
            f"  Measuring N={n:,} rows (dim={args.dim}, {args.queries} queries)...",
            end="",
            flush=True,
        )
        result = run_scale(n, dim=args.dim, queries=args.queries, k=args.k)
        print(" done")
        _print_row(result)
        results.append(result)

    _print_footer(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
