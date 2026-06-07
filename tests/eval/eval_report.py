"""Standalone recall@K + MRR report for `make eval`.

Prints per-query recall and aggregate metrics WITHOUT running the full gate.
The same fixture and queries are used by the gate-wired regression test —
this module just presents them in a human-readable table.

Usage:
    make eval
    uv run python -m tests.eval.eval_report
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures"
GOLDEN_PATH = Path(__file__).parent / "golden.json"


def _build_index() -> Any:
    """Index the fixture and return an open DB connection."""
    import seam.config as cfg
    from seam.indexer.cluster_index import index_clusters
    from seam.indexer.db import init_db
    from seam.indexer.pipeline import index_one_file, walk_project
    from seam.indexer.synthesis_index import index_synthesis

    tmp_path = Path(tempfile.mkdtemp(prefix="seam_eval_report_"))
    db_path = tmp_path / "seam.db"
    conn = init_db(db_path)

    files = walk_project(FIXTURE_DIR)
    for fpath in files:
        index_one_file(conn, fpath)

    index_clusters(
        conn,
        naming_mode="deterministic",
        llm_api_key=None,
        llm_model=None,
        min_size=2,
    )
    n_synth = index_synthesis(
        conn,
        enabled=True,
        fanout_cap=cfg.SEAM_SYNTHESIS_FANOUT_CAP,
    )
    print(f"[eval] Indexed {len(files)} files, synthesized {n_synth} edges.")
    return conn


def _run_query(conn: Any, query_spec: dict[str, Any]) -> list[str]:
    """Run a golden query and return ranked symbol names."""
    from seam.server.tools import (
        handle_seam_context,
        handle_seam_impact,
        handle_seam_query,
        handle_seam_search,
        handle_seam_trace,
    )

    tool = query_spec["tool"]
    try:
        if tool == "search":
            result = handle_seam_search(conn, query_spec["query"], FIXTURE_DIR, semantic=False)
            return [r["symbol"] for r in result] if isinstance(result, list) else []
        elif tool == "query":
            result = handle_seam_query(conn, query_spec["query"], FIXTURE_DIR, semantic=False)
            return [r["symbol"] for r in result] if isinstance(result, list) else []
        elif tool == "context_callers":
            ctx = handle_seam_context(conn, query_spec["symbol"], FIXTURE_DIR)
            return ctx.get("callers", []) if ctx and "error" not in ctx else []
        elif tool == "impact_downstream":
            impact = handle_seam_impact(
                conn,
                query_spec["symbol"],
                FIXTURE_DIR,
                direction="downstream",
                max_depth=3,
                include_tests=True,
                limit=0,
            )
            names: list[str] = []
            for tier_entries in impact.get("downstream", {}).values():
                names.extend(e["name"] for e in tier_entries)
            return names
        elif tool == "trace":
            trace = handle_seam_trace(conn, query_spec["source"], query_spec["target"], FIXTURE_DIR)
            return [x["name"] for x in trace.get("callees_source", [])]
        else:
            return []
    except Exception:  # noqa: BLE001
        return []


def main() -> None:
    """Print a human-readable recall@K / MRR report."""
    from tests.eval.recall_harness import compute_metrics, mrr, recall_at_k

    if not GOLDEN_PATH.exists():
        print(f"[eval] ERROR: golden.json not found at {GOLDEN_PATH}")
        print("[eval] Run 'make eval-generate' to generate it.")
        return

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    conn = _build_index()

    print()
    print(f"{'Query ID':<50} {'K':>3} {'R@K':>6} {'MRR':>6}  Expected")
    print("-" * 100)

    cases: list[dict[str, Any]] = []
    for query_spec in golden.get("queries", []):
        actual = _run_query(conn, query_spec)
        expected = query_spec["expected_symbols"]
        k = query_spec.get("k", 10)

        r = recall_at_k(expected, actual, k)
        m = mrr(expected, actual)
        status = "OK" if r >= 1.0 else "REGRESSION"

        print(
            f"{query_spec['id']:<50} {k:>3} {r:>6.3f} {m:>6.3f}  {status}"
        )
        cases.append({"expected_symbols": expected, "actual_symbols": actual})

    metrics = compute_metrics(cases, k=10)
    print("-" * 100)
    print(
        f"{'AGGREGATE':<50} {'10':>3} {metrics['recall_at_k']:>6.3f} {metrics['mrr']:>6.3f}"
        f"  n={metrics['n']}"
    )
    print()

    if metrics["recall_at_k"] >= 1.0 and metrics["mrr"] >= 1.0:
        print("[eval] PASS — no regression detected.")
    else:
        print("[eval] REGRESSION detected — check per-query output above.")


if __name__ == "__main__":
    main()
