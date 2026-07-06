"""WS6.1 — Trace-capture loop curation script.

WHAT THIS IS
------------
Human-in-the-loop curation pipeline for agent-trace-derived eval goldens.

Steps:
  1. derive_from_trace(trace_path, outcome, review_path)
     Reads a captured NDJSON trace file + an outcome symbol set → calls derive_goldens →
     writes a REVIEW file (JSON) with the candidates flagged approved=False by default.
     The human edits the review file and sets approved=True for high-quality candidates.

  2. promote_candidates(review_path, live_golden_path, repo_sha)
     Reads the reviewed file → merges approved candidates into the SEPARATE live golden
     set (repo-keyed; includes repo_sha for audit trail). Idempotent: deduplicated on
     (tool, query) — running twice with the same review file produces no duplicates.

IMPORTANT INVARIANTS
--------------------
- NEVER modifies tests/eval/golden.json — that is the FIXTURE golden set used by the
  deterministic gate recall regression. The live golden set is entirely separate.
- Symbols-only bound is enforced at the recorder layer (seam/analysis/trace_capture.py);
  this script reads trace records but never writes source text or full result bodies.
- Human-in-the-loop: promote is NEVER called automatically. The operator runs it after
  reviewing the candidates in the REVIEW file.

Usage (CLI):
  # Derive candidates from a trace file:
  uv run python benchmarks/trace_loop.py derive \\
      --trace .seam/traces/session-xyz.ndjson \\
      --review .seam/review.json

  # Promote approved candidates to the live golden set:
  uv run python benchmarks/trace_loop.py promote \\
      --review .seam/review.json \\
      --live-golden .seam/live_goldens.json \\
      --repo-sha $(git rev-parse HEAD)

Makefile target:
  make trace-loop-derive   # runs derive step
  make trace-loop-promote  # runs promote step (human-reviewed review file required)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# The FIXTURE golden set. This must NEVER be modified by this module.
_FIXTURE_GOLDEN = Path(__file__).resolve().parents[0].parent / "tests" / "eval" / "golden.json"


# ── Public API ────────────────────────────────────────────────────────────────


def derive_from_trace(
    *,
    trace_path: Path,
    outcome: set[str],
    review_path: Path,
) -> None:
    """Read a captured NDJSON trace file + outcome set → write a review JSON file.

    The review file contains a list of GoldenCandidate dicts, each with an extra
    "approved" field defaulting to False. The human edits this file to approve
    high-quality candidates before calling promote_candidates.

    Never modifies tests/eval/golden.json.
    Never raises — logs warnings on any error.

    Args:
        trace_path:  Path to the captured NDJSON trace file.
        outcome:     Set of qualified symbol names the agent actually edited.
                     (the hindsight outcome signal — typically from derive_outcome_from_diff
                     or passed directly from a known session outcome)
        review_path: Path to write the review JSON file to.
    """
    # Safety guard: refuse to overwrite the fixture golden set.
    _assert_not_fixture(review_path)

    try:
        records = _load_trace_records(trace_path)
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415
        candidates = derive_goldens(records, outcome)

        # Build review payload: candidates with approved=False by default.
        review_data: dict[str, Any] = {
            "trace_path": str(trace_path),
            "candidates": [
                {**c.to_dict(), "approved": False}
                for c in candidates
            ],
        }
        _write_json_atomic(review_path, review_data)
        logger.info(
            "trace_loop: wrote %d candidates to review file %s",
            len(candidates),
            review_path,
        )
    except Exception:  # noqa: BLE001
        logger.warning("trace_loop: derive_from_trace failed", exc_info=True)


def promote_candidates(
    *,
    review_path: Path,
    live_golden_path: Path,
    repo_sha: str,
) -> None:
    """Merge approved candidates from the review file into the live golden set.

    Reads the review file written by derive_from_trace → filters for approved=True →
    merges those candidates into the SEPARATE live golden set at live_golden_path
    (creates it if absent). Idempotent: deduplicates on (tool, query) — running twice
    with the same review file produces no duplicate entries.

    NEVER modifies tests/eval/golden.json.
    Never raises — logs warnings on any error.

    Args:
        review_path:      Path to the review JSON file (written by derive_from_trace,
                          edited by the human to set approved=True on approved candidates).
        live_golden_path: Path to the live golden set JSON (created if absent).
        repo_sha:         The git commit SHA of the repo at the time of promotion.
                          Stored in the live golden set for audit trail.
    """
    # Safety guard: refuse to overwrite the fixture golden set.
    _assert_not_fixture(live_golden_path)

    try:
        review = _read_json(review_path)
        all_candidates = review.get("candidates", [])
        approved = [c for c in all_candidates if c.get("approved") is True]

        if not approved:
            logger.info("trace_loop: no approved candidates in review file — nothing promoted")
            return

        # Load existing live golden set or start fresh.
        existing = _load_live_golden(live_golden_path)
        existing["repo_sha"] = repo_sha  # update to latest promote SHA

        # Dedup: build a map of (tool, query) → existing entry for O(1) lookup.
        existing_queries: list[dict[str, Any]] = existing.setdefault("queries", [])
        existing_index: dict[tuple[str, str], int] = {}
        for i, q in enumerate(existing_queries):
            key = (q.get("tool", ""), q.get("query", ""))
            existing_index[key] = i

        # Merge approved candidates — replace on match, append on new.
        n_added = 0
        n_replaced = 0
        for cand in approved:
            tool = cand.get("tool", "")
            query = cand.get("query", "")
            key = (tool, query)
            entry = _candidate_to_query_entry(cand)
            if key in existing_index:
                existing_queries[existing_index[key]] = entry
                n_replaced += 1
            else:
                existing_index[key] = len(existing_queries)
                existing_queries.append(entry)
                n_added += 1

        _write_json_atomic(live_golden_path, existing)
        logger.info(
            "trace_loop: promoted %d candidates (added=%d, replaced=%d) to %s [sha=%s]",
            len(approved), n_added, n_replaced, live_golden_path, repo_sha,
        )
    except Exception:  # noqa: BLE001
        logger.warning("trace_loop: promote_candidates failed", exc_info=True)


# ── Internal helpers ───────────────────────────────────────────────────────────


def _assert_not_fixture(path: Path) -> None:
    """Raise ValueError if path would resolve to the fixture golden.json.

    Defense-in-depth guard — the fixture golden set must NEVER be modified
    by the curation loop. This check is cheap and runs before any file write.
    """
    try:
        if path.resolve() == _FIXTURE_GOLDEN.resolve():
            raise ValueError(
                f"trace_loop: BUG — refusing to modify the fixture golden.json at {path}. "
                "The trace loop must use a SEPARATE live golden set."
            )
    except OSError:
        pass  # path may not exist yet; that's fine


def _load_trace_records(trace_path: Path) -> list[dict[str, Any]]:
    """Read NDJSON trace records from a file, skipping malformed lines."""
    records: list[dict[str, Any]] = []
    try:
        with open(trace_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        records.append(rec)
                except json.JSONDecodeError:
                    logger.warning("trace_loop: skipping malformed NDJSON line: %.80s", line)
    except OSError:
        logger.warning("trace_loop: could not read trace file %s", trace_path, exc_info=True)
    return records


def _load_live_golden(path: Path) -> dict[str, Any]:
    """Load the live golden set from a file, or return an empty scaffold."""
    if path.exists():
        try:
            return _read_json(path)
        except Exception:  # noqa: BLE001
            logger.warning("trace_loop: could not load live golden set at %s", path, exc_info=True)
    # Return a fresh empty scaffold. No "fixture_hash" key — this is NOT the fixture set.
    return {"queries": []}


def _candidate_to_query_entry(cand: dict[str, Any]) -> dict[str, Any]:
    """Convert a candidate dict (from the review file) to a live golden query entry.

    The shape mirrors the golden.json recall query shape so approved candidates
    can be run through the existing recall_harness / recall@K + MRR metric.
    """
    return {
        "tool": cand.get("tool", ""),
        "query": cand.get("query", ""),
        "k": cand.get("k", 10),
        "expected_symbols": cand.get("expected_symbols", []),
        "gap": cand.get("gap", False),
        "provenance": cand.get("provenance", {}),
    }


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON data to path atomically (temp file + os.replace).

    Creates parent directories as needed. Matches the pattern used throughout
    seam/indexer/artifact.py and seam/cli/file_sink.py.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the SAME directory to avoid cross-device rename errors.
    dir_ = str(path.parent)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── CLI entrypoint ────────────────────────────────────────────────────────────


def _cli() -> None:
    """Minimal CLI for running the trace-capture loop steps from the Makefile."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="trace_loop",
        description="WS6.1 trace-capture loop curation script.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # derive subcommand
    derive_p = sub.add_parser("derive", help="Derive golden candidates from a trace file.")
    derive_p.add_argument("--trace", required=True, type=Path, help="Path to the NDJSON trace file.")
    derive_p.add_argument("--review", required=True, type=Path, help="Path to write the review JSON.")
    derive_p.add_argument(
        "--outcome", nargs="*", default=[],
        help="Outcome symbol names (space-separated). Required for gap detection.",
    )

    # promote subcommand
    promote_p = sub.add_parser("promote", help="Promote approved candidates to the live golden set.")
    promote_p.add_argument("--review", required=True, type=Path, help="Path to the review JSON.")
    promote_p.add_argument("--live-golden", required=True, type=Path, help="Path to the live golden set JSON.")
    promote_p.add_argument("--repo-sha", required=True, help="Git commit SHA for audit trail.")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.command == "derive":
        outcome = set(args.outcome)
        derive_from_trace(
            trace_path=args.trace,
            outcome=outcome,
            review_path=args.review,
        )
    elif args.command == "promote":
        promote_candidates(
            review_path=args.review,
            live_golden_path=args.live_golden,
            repo_sha=args.repo_sha,
        )


if __name__ == "__main__":
    _cli()
