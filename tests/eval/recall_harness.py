"""Pure metric module for recall@K and MRR computation.

LAYER: leaf — imports only stdlib. No DB access, no network, no LLM.

This module provides two metrics for evaluating information retrieval quality:
  - recall@K:  fraction of expected symbols found in the top-K results
  - MRR:       Mean Reciprocal Rank of the first expected hit

Both functions are deterministic, stateless, and never raise.

Used by:
  - tests/eval/test_recall_regression.py (gate-wired regression test)
  - make eval (standalone metric report)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# ── Core metric functions ─────────────────────────────────────────────────────


def recall_at_k(expected: list[str], ranked_actual: list[str], k: int) -> float:
    """Compute recall@K: fraction of expected symbols in the top-K ranked results.

    Args:
        expected:     Set of symbol names that SHOULD appear (ground truth).
        ranked_actual: Symbol names from a query response, in rank order (best first).
        k:            Cutoff rank. Only the first K entries in ranked_actual are checked.

    Returns:
        A float in [0.0, 1.0]. Returns 0.0 when expected is empty (no divide-by-zero).
        Returns 1.0 when all expected symbols appear in the top-K.

    WHY treat expected as a set (not list): duplicate expected symbols would inflate
    the denominator. Deduplication makes the metric well-defined regardless of golden
    file format.
    """
    if not expected:
        # No ground truth → metric is undefined; return 0.0 as the safe default.
        return 0.0

    # Deduplicate expected (ground truth may have duplicates from generous labelling).
    expected_set = set(expected)
    top_k = set(ranked_actual[:k])

    found = expected_set & top_k
    return len(found) / len(expected_set)


def mrr(expected: list[str], ranked_actual: list[str]) -> float:
    """Compute MRR: reciprocal rank of the FIRST expected symbol in ranked_actual.

    For a single query, MRR = 1/rank_of_first_hit. If no expected symbol appears,
    MRR = 0.0.

    Args:
        expected:     Set of symbol names that SHOULD appear (ground truth).
        ranked_actual: Symbol names from a query response, in rank order (best first).

    Returns:
        A float in [0.0, 1.0]. 1.0 if the top result is expected. 0.0 if no hit.

    NOTE: This is the PER-QUERY MRR (reciprocal rank of first hit). The standard
    aggregate MRR over a set of queries is the mean of per-query MRR values — that
    aggregation happens in compute_metrics(), not here.
    """
    if not expected:
        return 0.0

    expected_set = set(expected)
    for rank_0based, symbol in enumerate(ranked_actual):
        if symbol in expected_set:
            # Rank is 1-indexed: first position = rank 1 → RR = 1/1 = 1.0
            return 1.0 / (rank_0based + 1)
    return 0.0  # No expected symbol found anywhere in the result list


# ── Aggregate over a query set ────────────────────────────────────────────────


def compute_metrics(
    cases: list[dict[str, Any]],
    k: int = 10,
) -> dict[str, float]:
    """Compute aggregate recall@K and MRR over a list of evaluation cases.

    Args:
        cases: List of dicts, each with keys:
                 expected_symbols: list[str]  — ground-truth symbol names
                 actual_symbols:   list[str]  — ranked query results
               Any additional keys (query, tool, etc.) are ignored.
        k:     Recall@K cutoff. Default 10 (top-10 results checked).

    Returns:
        Dict with keys:
          recall_at_k:  float — mean recall@K across all cases
          mrr:          float — mean MRR across all cases
          n:            int   — number of cases evaluated
          k:            int   — the K used for recall

    Returns zeros for an empty cases list (never raises).
    """
    if not cases:
        return {"recall_at_k": 0.0, "mrr": 0.0, "n": 0, "k": k}

    recall_scores: list[float] = []
    mrr_scores: list[float] = []

    for case in cases:
        expected = case.get("expected_symbols", [])
        actual = case.get("actual_symbols", [])
        recall_scores.append(recall_at_k(expected, actual, k))
        mrr_scores.append(mrr(expected, actual))

    mean_recall = sum(recall_scores) / len(recall_scores)
    mean_mrr = sum(mrr_scores) / len(mrr_scores)

    return {
        "recall_at_k": round(mean_recall, 4),
        "mrr": round(mean_mrr, 4),
        "n": len(cases),
        "k": k,
    }


# ── SHA-stamp helpers ─────────────────────────────────────────────────────────


def compute_fixture_hash(fixture_dir: Path) -> str:
    """Compute a deterministic SHA-256 hash over all fixture source files.

    Files are sorted by path for determinism. The hash covers both the path and the
    content of every Python source file in the fixture directory (non-recursive,
    top-level only — our fixture is deliberately flat).

    Returns a 16-char hex prefix (sufficient for collision resistance at this scale).
    Deterministic: same fixture content → same hash, regardless of OS or filesystem.
    """
    digest = hashlib.sha256()
    # Sort to guarantee a deterministic order across platforms.
    source_files = sorted(fixture_dir.glob("*.py"))
    for path in source_files:
        # Hash the relative filename so renames are detected.
        digest.update(path.name.encode())
        # Hash the file content so content changes are detected.
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def check_fixture_hash(golden_path: Path, fixture_dir: Path) -> tuple[bool, str, str]:
    """Verify that the golden file's fixture hash matches the current fixtures.

    Returns:
        (matches: bool, stored_hash: str, current_hash: str)

    Returns (True, '', '') if the golden has no fixture_hash key (legacy golden;
    treated as if hash check is skipped — the regression test will still run but
    won't fail on hash mismatch alone). This maintains backward compat when adding
    the SHA stamp to an existing golden.
    """
    try:
        data = json.loads(golden_path.read_text(encoding="utf-8"))
    except Exception:
        return False, "<unreadable>", compute_fixture_hash(fixture_dir)

    stored_hash = data.get("fixture_hash", "")
    if not stored_hash:
        # No hash in golden — skip the check (treated as 'pass').
        return True, "", compute_fixture_hash(fixture_dir)

    current_hash = compute_fixture_hash(fixture_dir)
    return stored_hash == current_hash, stored_hash, current_hash
