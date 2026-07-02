"""Seam semantic recall benchmark — T9 + WS1-C.

Measures recall@5, recall@10, and MRR@10 for three retrieval modes over ~15 concept
queries with known target symbols in THIS repository:

  1. FTS-only          — SEAM_SEMANTIC=off (pure FTS5 keyword baseline)
  2. Hybrid body=off   — FTS5 + semantic, embeddings built without body text
  3. Hybrid body=on    — FTS5 + semantic, embeddings built WITH body + comment text

The three columns are reported SIDE BY SIDE so the body-enrichment lift is visible
at a glance (WS1-C acceptance criterion).

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

WS1-C behaviour
---------------
The benchmark rebuilds embeddings TWICE (body=off then body=on) using the model
already stored in the embeddings table.  After the run the index is left in the
body=on state.  To restore body=off, run `seam init --semantic` with the default
SEAM_EMBED_BODY=off setting.

NOTE: This benchmark is NOT part of `make gate` — it requires fastembed installed
and a one-time model download. The gate uses synthetic vectors and offline stubs.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

# Make sure the repo root is on sys.path when run directly.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# fastembed is required — exit cleanly with a helpful message (exit 0 = skip, not error).
try:
    import fastembed  # noqa: F401
except ImportError:
    print(
        "fastembed is not installed — benchmark skipped.\n"
        "To run: pip install 'seam-mcp[semantic]'\n"
        "        seam init --semantic\n"
        "        make bench-semantic",
        file=sys.stderr,
    )
    raise SystemExit(0)

from seam.indexer.db import connect, init_db
from seam.indexer.embedding_index import index_embeddings
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


# ── Metric helpers ────────────────────────────────────────────────────────────


def _recall_at_k(results: list[dict], targets: list[str], k: int) -> int:
    """Return 1 if any target appears in results[:k], else 0."""
    top_k = {r["symbol"] for r in results[:k]}
    return int(bool(top_k & set(targets)))


def _reciprocal_rank(results: list[dict], targets: list[str], k: int) -> float:
    """Return 1/rank of the first matching target in results[:k], or 0.0.

    Used to compute Mean Reciprocal Rank (MRR) across queries.
    """
    target_set = set(targets)
    for rank, r in enumerate(results[:k], 1):
        if r["symbol"] in target_set:
            return 1.0 / rank
    return 0.0


# ── Query runner ──────────────────────────────────────────────────────────────


def _run_queries(conn: sqlite3.Connection, *, semantic_on: bool, k_values: tuple[int, ...]) -> list[dict]:
    """Run all benchmark queries and collect per-query recall and reciprocal rank.

    Patches SEAM_SEMANTIC at query time so the same DB connection can be used
    for all three modes (FTS, hybrid body=off, hybrid body=on) without reconnecting.

    Returns a list of result dicts:
        {query, targets, note, recall@k1, recall@k2, rr@k1, rr@k2, ...}
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
            row[f"rr@{k}"] = _reciprocal_rank(results, targets, k)
        rows.append(row)

    return rows


# ── Embedding rebuild ─────────────────────────────────────────────────────────


def _rebuild_embeddings(conn: sqlite3.Connection, *, model: str, embed_body: bool) -> int:
    """Clear and rebuild the embeddings table with the given body-enrichment setting.

    Patches SEAM_EMBED_BODY in the embedding_index module for the duration of the
    call so that body-slice and comment enrichment are applied (or not) as requested,
    regardless of what the environment variable says.

    WHY clear first: ensures the run measures the exact setting, not a mix of vectors
    from a previous run with a different body setting.

    Returns:
        Count of embedded symbols (≥1) on success, -1 on error, 0 if fastembed absent.
        A return of 0 or -1 indicates a failure that should abort the benchmark.
    """
    setting = "on" if embed_body else "off"
    print(f"\n  Rebuilding embeddings with SEAM_EMBED_BODY={setting}...", end="", flush=True)

    # Wipe existing embeddings so the run starts from a clean state.
    conn.execute("DELETE FROM embeddings")
    conn.commit()

    # Patch the module-level constant that _index_embeddings_impl reads.
    with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", setting):
        count = index_embeddings(conn, model=model, batch=32)

    print(f" {count} symbols embedded")
    return count


# ── Reporting ─────────────────────────────────────────────────────────────────

_TABLE_WIDTH = 110


def _report(
    fts_rows: list[dict],
    hyb_rows: list[dict],
    k_values: tuple[int, ...],
) -> None:
    """Print the original FTS vs hybrid comparison (unchanged from T9).

    Preserved for backward compatibility with any tooling that parses this output.
    """
    n = len(fts_rows)
    assert n == len(hyb_rows)

    print("\n" + "=" * 90)
    print("Seam Semantic Recall Benchmark — FTS vs Hybrid")
    print("=" * 90)
    print(f"{'Query':<45} {'FTS r@5':>7} {'Hyb r@5':>7} {'FTS r@10':>8} {'Hyb r@10':>8}")
    print("-" * 90)

    for fts_row, hyb_row in zip(fts_rows, hyb_rows):
        q = fts_row["query"][:44]
        cols = []
        for k in k_values:
            cols.append(fts_row[f"recall@{k}"])
            cols.append(hyb_row[f"recall@{k}"])
        print(f"{q:<45} {cols[0]:>7} {cols[1]:>7} {cols[2]:>8} {cols[3]:>8}")

    print("-" * 90)

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
        fts_hit5 = fts_row["recall@5"]
        hyb_hit5 = hyb_row["recall@5"]
        if not fts_hit5 and hyb_hit5:
            q = fts_row["query"][:60]
            print(f"  SEMANTIC LIFT: {q!r} → found: {hyb_row['targets']}")
        elif not fts_hit5 and not hyb_hit5:
            q = fts_row["query"][:60]
            print(f"  BOTH MISSED:   {q!r} → target: {fts_row['targets']}")

    print("=" * 90)


def _report_three_way(
    fts_rows: list[dict],
    off_rows: list[dict],
    on_rows: list[dict],
    k_values: tuple[int, ...],
) -> None:
    """Print the WS1-C three-way comparison: FTS | Hybrid body=off | Hybrid body=on.

    Shows recall@K and MRR@K for each mode side by side so the body-enrichment lift
    is immediately visible.  The 'Δ' column is body=on minus body=off (the WS1 signal).
    """
    n = len(fts_rows)
    assert n == len(off_rows) == len(on_rows)

    # Use the largest k for MRR (most stable; mirrors the benchmark community norm).
    mrr_k = max(k_values)

    print("\n" + "=" * _TABLE_WIDTH)
    print("WS1-C: Body enrichment lift — body=off vs body=on (hybrid semantic)")
    print("=" * _TABLE_WIDTH)

    # Header: one column group per mode, MRR only at mrr_k.
    hdr1 = f"{'Query':<45}"
    hdr2_parts = []
    for label in ("FTS-only", "Hybrid body=off", "Hybrid body=on"):
        hdr2_parts.append(f"{label:^22}")
    print(hdr1 + "  " + "  ".join(hdr2_parts))

    # Sub-header: @5, @10, MRR per group.
    sub1 = " " * 45 + "  "
    sub2 = f"{'@5':>4} {'@10':>4} {'MRR':>6}    " * 3
    print(sub1 + sub2)
    print("-" * _TABLE_WIDTH)

    # Per-query rows.
    for fts, off, on in zip(fts_rows, off_rows, on_rows):
        q = fts["query"][:44]
        row_parts = []
        for r in (fts, off, on):
            r5 = r["recall@5"]
            r10 = r[f"recall@{mrr_k}"]
            mrr = r[f"rr@{mrr_k}"]
            row_parts.append(f"{r5:>4} {r10:>4} {mrr:>6.3f}  ")
        print(f"{q:<45}  {'  '.join(row_parts)}")

    print("-" * _TABLE_WIDTH)

    # Aggregate row: recall + MRR for each mode.
    def _agg(rows: list[dict], k: int) -> tuple[float, float]:
        rec = sum(r[f"recall@{k}"] for r in rows) / n
        mrr = sum(r[f"rr@{k}"] for r in rows) / n
        return rec, mrr

    fts5, fts5_mrr = _agg(fts_rows, 5)
    fts10, fts10_mrr = _agg(fts_rows, mrr_k)
    off5, off5_mrr = _agg(off_rows, 5)
    off10, off10_mrr = _agg(off_rows, mrr_k)
    on5, on5_mrr = _agg(on_rows, 5)
    on10, on10_mrr = _agg(on_rows, mrr_k)

    def _fmt_group(r5: float, r10: float, mrr: float) -> str:
        return f"{r5:>4.1%} {r10:>4.1%} {mrr:>6.3f}  "

    print(
        f"{'AGGREGATE':<45}  "
        + _fmt_group(fts5, fts10, fts10_mrr)
        + "  "
        + _fmt_group(off5, off10, off10_mrr)
        + "  "
        + _fmt_group(on5, on10, on10_mrr)
    )

    # Delta row: body=on minus body=off (the WS1 signal).
    d5 = on5 - off5
    d10 = on10 - off10
    dmrr = on10_mrr - off10_mrr
    sign = lambda v: "+" if v >= 0 else ""  # noqa: E731

    print(
        f"{'Δ (body=on − body=off)':<45}  "
        f"{'':>17}    "   # FTS column blank — delta is body comparison only
        f"{'base':>4} {'base':>4} {'base':>6}  "
        f"  {sign(d5)}{d5:>4.1%} {sign(d10)}{d10:>4.1%} {sign(dmrr)}{dmrr:>6.3f}  "
    )

    print("=" * _TABLE_WIDTH)

    # Highlight vocabulary-gap queries where body enrichment helps.
    print("\nBody-on lift over body-off (vocabulary-gap analysis):")
    any_lift = False
    for off, on in zip(off_rows, on_rows):
        off_hit5 = off["recall@5"]
        on_hit5 = on["recall@5"]
        q = off["query"][:65]
        if not off_hit5 and on_hit5:
            print(f"  BODY-ON LIFTS: {q!r} → found: {on['targets']}")
            any_lift = True
        elif off_hit5 and not on_hit5:
            print(f"  BODY-ON HURTS: {q!r} → lost: {on['targets']}")
            any_lift = True
    if not any_lift:
        print("  (no per-query recall change at @5 between body=off and body=on)")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    """Run the semantic recall benchmark and print results.

    WS1-C: rebuilds embeddings twice (body=off then body=on) to produce the
    three-way recall+MRR comparison table.  The index is left in body=on state.
    """
    db_path = _ROOT / ".seam" / "seam.db"
    if not db_path.exists():
        print(
            f"No index at {db_path}.\n"
            "Run: seam init --semantic\n"
            "Then: make bench-semantic",
            file=sys.stderr,
        )
        return 1

    # Auto-migrate schema; get a read/write connection (needed for embedding rebuild).
    init_db(db_path).close()
    conn = connect(db_path)

    # Check that embeddings exist (required to know which model to use for rebuild).
    model_row = conn.execute("SELECT DISTINCT model FROM embeddings LIMIT 1").fetchone()
    if model_row is None:
        print(
            "No embeddings in index.\n"
            "Run: seam init --semantic\n"
            "Then: make bench-semantic",
            file=sys.stderr,
        )
        conn.close()
        return 1

    model = model_row[0]
    emb_count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    print(f"Index:       {db_path}")
    print(f"Model:       {model}")
    print(f"Embeddings:  {emb_count}")
    print(f"Queries:     {len(QUERIES)}")

    k_values: tuple[int, ...] = (5, 10)

    # ── Step 1: FTS baseline (no rebuild needed) ──────────────────────────────
    print("\nRunning FTS-only queries...", end="", flush=True)
    fts_rows = _run_queries(conn, semantic_on=False, k_values=k_values)
    print(" done")

    # ── Step 2: Hybrid with body=off embeddings ───────────────────────────────
    count_off = _rebuild_embeddings(conn, model=model, embed_body=False)
    if count_off <= 0:
        print(
            f"Embedding rebuild (body=off) returned {count_off} — aborting.",
            file=sys.stderr,
        )
        conn.close()
        return 1
    print("  Running hybrid queries (body=off)...", end="", flush=True)
    off_rows = _run_queries(conn, semantic_on=True, k_values=k_values)
    print(" done")

    # ── Step 3: Hybrid with body=on embeddings ────────────────────────────────
    count_on = _rebuild_embeddings(conn, model=model, embed_body=True)
    if count_on <= 0:
        print(
            f"Embedding rebuild (body=on) returned {count_on} — aborting.",
            file=sys.stderr,
        )
        conn.close()
        return 1
    print("  Running hybrid queries (body=on)...", end="", flush=True)
    on_rows = _run_queries(conn, semantic_on=True, k_values=k_values)
    print(" done")

    conn.close()

    # ── Reports ───────────────────────────────────────────────────────────────
    # Classic FTS vs hybrid (body=off as the pre-WS1 hybrid baseline).
    _report(fts_rows, off_rows, k_values)

    # WS1-C: three-way comparison with MRR.
    _report_three_way(fts_rows, off_rows, on_rows, k_values)

    print("NOTE: The index is now in SEAM_EMBED_BODY=on state.")
    print("      Run 'seam init --semantic' (with default SEAM_EMBED_BODY=off) to restore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
