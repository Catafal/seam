"""Seam semantic recall benchmark — T9.

Measures recall@5 and recall@10 for FTS-only vs hybrid (FTS + semantic) on
~15 concept queries with known target symbols in THIS repository.

HOW TO RUN
----------
Prerequisites:
    pip install 'seam-mcp[semantic]'   # adds fastembed (~67 MB ONNX model)
    seam init --semantic               # index the repo AND build embeddings

Then:
    make bench-semantic
    # or directly:
    uv run python benchmarks/semantic_recall.py

The benchmark does a one-time model download on first run (fastembed caches the
model in ~/.cache/huggingface/). Subsequent runs are 100% local (no network).

WHAT IS MEASURED
----------------
Recall@K = fraction of queries where at least one expected target symbol appears
           in the top-K results.

Two modes are compared:
  FTS-only:  SEAM_SEMANTIC=off — pure FTS5 keyword search (the baseline).
  Hybrid:    SEAM_SEMANTIC=on  — FTS5 + semantic cosine-RRF merge.

The benchmark exposes the "vocabulary gap": queries where the user's words do not
appear in the symbol name/docstring (e.g. "retry logic" vs "_backoff_with_jitter").

NOTE: This benchmark is NOT part of `make gate` — it requires fastembed installed
and a one-time model download. The gate uses synthetic vectors and offline stubs.
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Make sure the repo root is on sys.path when run directly.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# fastembed is required — fail fast with a helpful message before any imports.
try:
    import fastembed  # noqa: F401
except ImportError:
    print(
        "fastembed is not installed.\n"
        "Run: pip install 'seam-mcp[semantic]'\n"
        "Then: seam init --semantic\n"
        "Then re-run: make bench-semantic",
        file=sys.stderr,
    )
    raise SystemExit(1)

from seam.indexer.db import connect, init_db
from seam.query.engine import search


# ── Benchmark queries ─────────────────────────────────────────────────────────
# Each entry:
#   "query"    — the natural-language concept query (how an agent would phrase it)
#   "targets"  — list of symbol names that SHOULD appear in top-K results
#   "note"     — WHY this tests semantic lift (the vocabulary gap it exercises)
#
# Targets are exact symbol names from this repo (seam package). If a name does not
# exist in the index, recall is 0 for that query — which itself is a valid signal.
#
# Selection criteria:
#   - At least 1/3 of queries should be "vocabulary-gap" queries where the query
#     terms do NOT appear verbatim in the target symbol name (semantic lift test).
#   - Cover all major subsystems: indexer, query engine, analysis, config, CLI.

QUERIES: list[dict] = [
    # ── Keyword-friendly queries (FTS should handle these) ────────────────────
    {
        "query": "upsert file into database",
        "targets": ["upsert_file"],
        "note": "Direct keyword match — both FTS and hybrid should find this.",
    },
    {
        "query": "init database schema",
        "targets": ["init_db"],
        "note": "Keyword 'init' + 'db' appear in the target name.",
    },
    {
        "query": "connect to SQLite database",
        "targets": ["connect"],
        "note": "Keyword match on function name.",
    },
    {
        "query": "walk project files",
        "targets": ["walk_project"],
        "note": "Exact keyword match.",
    },
    {
        "query": "parse source code with tree sitter",
        "targets": ["parse_file"],
        "note": "Keyword 'parse' appears in target.",
    },
    {
        "query": "FTS5 full text search query",
        "targets": ["build_match_query"],
        "note": "Keyword 'query' appears — FTS should handle.",
    },
    {
        "query": "detect clustering communities",
        "targets": ["detect_communities"],
        "note": "Keywords 'detect' + 'communities' appear verbatim.",
    },
    {
        "query": "extract graph edges from symbols",
        "targets": ["extract_edges"],
        "note": "Keywords 'extract' + 'edges' match directly.",
    },
    # ── Vocabulary-gap queries (semantic lift expected) ───────────────────────
    {
        "query": "retry logic with exponential backoff",
        "targets": ["_backoff_with_jitter"],
        "note": "Classic vocabulary gap: 'retry' and 'backoff' are different tokens "
                "but semantically synonymous. FTS should miss; hybrid should find.",
    },
    {
        "query": "memoization caching store",
        "targets": ["_lru_store"],
        "note": "No 'lru' in query; no 'memoization' in name. Pure semantic signal.",
    },
    {
        "query": "reciprocal rank fusion merge",
        "targets": ["rrf_merge"],
        "note": "Acronym: 'RRF' vs 'reciprocal rank fusion' — FTS may or may not expand.",
    },
    {
        "query": "cosine vector similarity distance",
        "targets": ["cosine_sim"],
        "note": "'cosine_sim' vs 'cosine similarity distance' — mild vocabulary gap.",
    },
    {
        "query": "symbol text for embedding",
        "targets": ["symbol_text"],
        "note": "Moderate vocabulary match — tests the semantic encoding pipeline.",
    },
    {
        "query": "local embedding model availability check",
        "targets": ["is_available"],
        "note": "Vocabulary gap: 'availability check' vs function name 'is_available'.",
    },
    {
        "query": "blast radius impact analysis",
        "targets": ["impact"],
        "note": "Domain vocabulary: 'blast radius' is a common metaphor for impact analysis.",
    },
]


# ── Result collection ─────────────────────────────────────────────────────────


def _recall_at_k(results: list[dict], targets: list[str], k: int) -> int:
    """Return 1 if any target appears in results[:k], else 0."""
    top_k = {r["symbol"] for r in results[:k]}
    return int(bool(top_k & set(targets)))


def _run_queries(conn, *, semantic_on: bool, k_values: tuple[int, ...]) -> list[dict]:
    """Run all benchmark queries and collect per-query recall.

    Returns a list of result dicts:
        {query, targets, note, recall@k1, recall@k2, ...}
    """
    rows = []
    mode = "on" if semantic_on else "off"
    for entry in QUERIES:
        query_text = entry["query"]
        targets = entry["targets"]

        with patch("seam.config.SEAM_SEMANTIC", mode):
            results = search(conn, query_text, limit=max(k_values))

        row: dict = {"query": query_text, "targets": targets, "note": entry["note"]}
        for k in k_values:
            row[f"recall@{k}"] = _recall_at_k(results, targets, k)
        rows.append(row)

    return rows


# ── Reporting ─────────────────────────────────────────────────────────────────


def _report(
    fts_rows: list[dict],
    hyb_rows: list[dict],
    k_values: tuple[int, ...],
) -> None:
    """Print a comparison table and aggregate recall scores."""
    n = len(fts_rows)
    assert n == len(hyb_rows)

    print("\n" + "=" * 90)
    print("Seam Semantic Recall Benchmark")
    print("=" * 90)
    print(f"{'Query':<45} {'FTS r@5':>7} {'Hyb r@5':>7} {'FTS r@10':>8} {'Hyb r@10':>8}")
    print("-" * 90)

    for fts_row, hyb_row in zip(fts_rows, hyb_rows):
        q = fts_row["query"][:44]  # truncate for alignment
        cols = []
        for k in k_values:
            cols.append(fts_row[f"recall@{k}"])
            cols.append(hyb_row[f"recall@{k}"])
        print(f"{q:<45} {cols[0]:>7} {cols[1]:>7} {cols[2]:>8} {cols[3]:>8}")

    print("-" * 90)

    # Aggregate recall per tier
    for k in k_values:
        fts_recall = sum(r[f"recall@{k}"] for r in fts_rows) / n
        hyb_recall = sum(r[f"recall@{k}"] for r in hyb_rows) / n
        lift = hyb_recall - fts_recall
        print(
            f"Recall@{k:2d}  FTS={fts_recall:.1%}  Hybrid={hyb_recall:.1%}  "
            f"Lift={lift:+.1%}  ({'IMPROVED' if lift > 0 else 'SAME' if lift == 0 else 'WORSE'})"
        )

    print()
    print("Vocabulary-gap queries (where semantic lift is expected):")
    for fts_row, hyb_row in zip(fts_rows, hyb_rows):
        # Identify vocabulary-gap queries: any where FTS@5 missed but hybrid@5 hit
        fts_hit5 = fts_row["recall@5"]
        hyb_hit5 = hyb_row["recall@5"]
        if not fts_hit5 and hyb_hit5:
            q = fts_row["query"][:60]
            print(f"  SEMANTIC LIFT: {q!r} → found: {hyb_row['targets']}")
        elif not fts_hit5 and not hyb_hit5:
            q = fts_row["query"][:60]
            print(f"  BOTH MISSED:   {q!r} → target: {fts_row['targets']}")

    print("=" * 90)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    """Run the semantic recall benchmark and print results."""
    db_path = _ROOT / ".seam" / "seam.db"
    if not db_path.exists():
        print(
            f"No index at {db_path}.\n"
            "Run: seam init --semantic\n"
            "Then: make bench-semantic",
            file=sys.stderr,
        )
        return 1

    # Auto-migrate schema; get a read connection.
    init_db(db_path).close()
    conn = connect(db_path)

    # Check that embeddings exist (required for the hybrid path)
    emb_count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if emb_count == 0:
        print(
            "No embeddings in index.\n"
            "Run: seam init --semantic\n"
            "Then: make bench-semantic",
            file=sys.stderr,
        )
        conn.close()
        return 1

    print(f"Index: {db_path}")
    print(f"Embeddings: {emb_count}")
    print(f"Queries: {len(QUERIES)}")

    k_values = (5, 10)

    # Run FTS-only (baseline)
    print("\nRunning FTS-only queries...", end="", flush=True)
    fts_rows = _run_queries(conn, semantic_on=False, k_values=k_values)
    print(" done")

    # Run hybrid (FTS + semantic)
    print("Running hybrid queries...", end="", flush=True)
    hyb_rows = _run_queries(conn, semantic_on=True, k_values=k_values)
    print(" done")

    conn.close()

    _report(fts_rows, hyb_rows, k_values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
