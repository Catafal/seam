"""Golden file generator for the recall regression harness.

Run this script to regenerate tests/eval/golden.json after changing fixture files.
It indexes the current fixture, runs each query through the live handlers, and
writes the resulting expected_symbols + SHA-stamp to golden.json.

Usage:
    make eval-generate
    uv run python tests/eval/gen_golden.py

Design notes:
  - The golden is generated with synthesis ON (SEAM_EDGE_SYNTHESIS=on) so that
    synthesis-sensitive queries (EE + interface-override) record the SYNTHESIZED baseline.
  - The fixture hash is stamped into the golden so test_fixture_hash_matches_golden
    can detect when the golden is stale.
  - This script is NOT run by the gate (make gate → make test → pytest). It is a
    manual tool to update the golden after fixture or engine changes.
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

    tmp_path = Path(tempfile.mkdtemp(prefix="seam_gen_golden_"))
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
    index_synthesis(conn, enabled=True, fanout_cap=cfg.SEAM_SYNTHESIS_FANOUT_CAP)
    return conn


def _run(conn: Any, query_spec: dict[str, Any]) -> list[str]:
    """Run a single query and return ranked symbol names."""
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
            r = handle_seam_search(conn, query_spec["query"], FIXTURE_DIR, semantic=False)
            return [x["symbol"] for x in r] if isinstance(r, list) else []
        elif tool == "query":
            r = handle_seam_query(conn, query_spec["query"], FIXTURE_DIR, semantic=False)
            return [x["symbol"] for x in r] if isinstance(r, list) else []
        elif tool == "context_callers":
            ctx = handle_seam_context(conn, query_spec["symbol"], FIXTURE_DIR)
            return ctx.get("callers", []) if ctx and "error" not in ctx else []
        elif tool == "impact_downstream":
            impact = handle_seam_impact(
                conn, query_spec["symbol"], FIXTURE_DIR,
                direction="downstream", max_depth=3, include_tests=True, limit=0,
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


# ── Query specs (what we want to measure) ────────────────────────────────────
# These define WHICH queries to include in the golden, NOT the expected answers
# (those are computed by running the live index). The tool/symbol/query fields are
# intentional: they define the benchmark coverage.

_QUERY_SPECS: list[dict[str, Any]] = [
    {
        "id": "search-validate-data",
        "tool": "search",
        "query": "validate data",
        "k": 10,
        "note": "locate validate_data function by search",
    },
    {
        "id": "search-payment-processor",
        "tool": "search",
        "query": "payment processor",
        "k": 10,
        "note": "locate PaymentProcessor and implementations",
    },
    {
        "id": "query-pipeline-stage",
        "tool": "query",
        "query": "pipeline stage",
        "k": 10,
        "note": "locate DataPipeline and pipeline functions",
    },
    {
        "id": "query-event-emitter",
        "tool": "query",
        "query": "event emitter subscribe",
        "k": 10,
        "note": "locate EventBus and its subscribe/emit methods",
    },
    {
        "id": "context-callers-format-result",
        "tool": "context_callers",
        "symbol": "format_result",
        "k": 10,
        "note": "callers of format_result via direct call edges",
    },
    {
        "id": "impact-downstream-event-emitter-synthesis",
        "tool": "impact_downstream",
        "symbol": "data_received",
        "k": 10,
        "note": "synthesized event-emitter edge: data_received fires on_data_received_handler",
    },
    {
        "id": "impact-downstream-interface-override-synthesis",
        "tool": "impact_downstream",
        "symbol": "PaymentProcessor.process_payment",
        "k": 10,
        "note": "synthesized interface-override edges: PaymentProcessor.process_payment impls",
    },
    {
        "id": "trace-render-to-format",
        "tool": "trace",
        "source": "render_output",
        "target": "format_result",
        "k": 10,
        "note": "trace: render_output calls format_result (direct call path)",
    },
    {
        "id": "context-callers-validate-data",
        "tool": "context_callers",
        "symbol": "validate_data",
        "k": 10,
        "note": "callers of validate_data via direct call edge from EventBus.process_data",
    },
    {
        "id": "impact-downstream-interface-refund-synthesis",
        "tool": "impact_downstream",
        "symbol": "PaymentProcessor.refund",
        "k": 10,
        "note": "synthesized interface-override: PaymentProcessor.refund has 2 impls",
    },
]


def main() -> None:
    """Generate golden.json from the current fixture + live index."""
    from tests.eval.recall_harness import compute_fixture_hash

    print("[gen_golden] Building index...")
    conn = _build_index()

    print("[gen_golden] Running queries...")
    queries: list[dict[str, Any]] = []
    for spec in _QUERY_SPECS:
        actual = _run(conn, spec)
        k = spec["k"]
        expected = actual[:k]  # The top-K results from the live run ARE the expected symbols.
        print(f"  {spec['id']}: {expected}")
        entry = dict(spec)
        entry["expected_symbols"] = expected
        queries.append(entry)

    fixture_hash = compute_fixture_hash(FIXTURE_DIR)
    golden = {"fixture_hash": fixture_hash, "queries": queries}

    GOLDEN_PATH.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
    print(f"\n[gen_golden] Wrote {GOLDEN_PATH} (fixture_hash={fixture_hash})")


if __name__ == "__main__":
    main()
