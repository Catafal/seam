"""Unit tests for the recall_harness metric module.

TDD: tests written BEFORE implementation (then implementation made them pass).

Coverage:
  - recall@K: basic hit, partial hit, full miss, K larger than result list
  - mrr: first hit, late hit, no hit
  - compute_metrics: aggregate over multiple cases
  - SHA-stamp: hash changes on content mutation, hash stable on no-op
  - SHA-stamp mismatch detected by check_fixture_hash
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.recall_harness import (
    check_fixture_hash,
    compute_fixture_hash,
    compute_metrics,
    mrr,
    recall_at_k,
)

# ── recall_at_k ──────────────────────────────────────────────────────────────


class TestRecallAtK:
    """recall_at_k correctness tests."""

    def test_perfect_recall_first_position(self) -> None:
        """Expected symbol at rank 1 → recall = 1.0."""
        assert recall_at_k(["Foo"], ["Foo", "Bar", "Baz"], k=5) == 1.0

    def test_perfect_recall_within_k(self) -> None:
        """All expected symbols in top-K → recall = 1.0."""
        assert recall_at_k(["A", "B"], ["X", "A", "B", "C"], k=5) == 1.0

    def test_partial_recall(self) -> None:
        """Half of expected found in top-K → recall = 0.5."""
        assert recall_at_k(["A", "B"], ["A", "C", "D"], k=5) == 0.5

    def test_zero_recall_miss(self) -> None:
        """No expected symbol in top-K → recall = 0.0."""
        assert recall_at_k(["A", "B"], ["X", "Y", "Z"], k=5) == 0.0

    def test_recall_k_cutoff(self) -> None:
        """Symbol at rank K+1 is not counted when k is tight."""
        # 'A' is at position 3 (0-indexed 2), K=2 → should NOT be found
        assert recall_at_k(["A"], ["X", "Y", "A", "B"], k=2) == 0.0

    def test_recall_k_boundary_included(self) -> None:
        """Symbol at exactly rank K IS counted (boundary is inclusive)."""
        # 'A' is at rank 2, K=2 → should be found
        assert recall_at_k(["A"], ["X", "A", "B", "C"], k=2) == 1.0

    def test_empty_expected_returns_zero(self) -> None:
        """Empty expected list → 0.0 (no divide-by-zero)."""
        assert recall_at_k([], ["A", "B", "C"], k=5) == 0.0

    def test_empty_actual_returns_zero(self) -> None:
        """No results → recall = 0.0."""
        assert recall_at_k(["A"], [], k=5) == 0.0

    def test_k_larger_than_results(self) -> None:
        """K larger than the result list → all results checked (no index error)."""
        # Only 2 results, K=100 → 'A' is found, recall = 1.0
        assert recall_at_k(["A"], ["A", "B"], k=100) == 1.0

    def test_duplicate_expected_deduplication(self) -> None:
        """Duplicate entries in expected are deduplicated (count once)."""
        # 'A' appears twice in expected → denominator is 1 (not 2)
        assert recall_at_k(["A", "A"], ["A", "B", "C"], k=5) == 1.0

    def test_removed_edge_lowers_recall(self) -> None:
        """Key test: removing a synthesized edge should lower recall.

        This test acts as a canary: if the synthesis is turned off and 'B' no
        longer appears in results, recall drops from 1.0 → 0.5.
        """
        # Full case: both expected symbols present
        full_recall = recall_at_k(["A", "B"], ["A", "B", "C"], k=5)
        assert full_recall == 1.0

        # Degraded case: 'B' missing (simulate a removed edge)
        reduced_recall = recall_at_k(["A", "B"], ["A", "C", "D"], k=5)
        assert reduced_recall == 0.5

        # The degraded recall is strictly lower
        assert reduced_recall < full_recall


# ── mrr ──────────────────────────────────────────────────────────────────────


class TestMRR:
    """MRR (per-query reciprocal rank) correctness tests."""

    def test_first_hit_at_rank_1(self) -> None:
        """First result is expected → MRR = 1.0."""
        assert mrr(["A"], ["A", "B", "C"]) == 1.0

    def test_first_hit_at_rank_2(self) -> None:
        """Expected symbol at rank 2 → MRR = 0.5."""
        assert mrr(["A"], ["X", "A", "B"]) == pytest.approx(0.5)

    def test_first_hit_at_rank_3(self) -> None:
        """Expected symbol at rank 3 → MRR = 1/3."""
        assert mrr(["A"], ["X", "Y", "A"]) == pytest.approx(1 / 3)

    def test_no_hit(self) -> None:
        """No expected symbol found → MRR = 0.0."""
        assert mrr(["A"], ["X", "Y", "Z"]) == 0.0

    def test_empty_expected(self) -> None:
        """Empty expected → MRR = 0.0 (not an error)."""
        assert mrr([], ["A", "B"]) == 0.0

    def test_empty_actual(self) -> None:
        """No results → MRR = 0.0."""
        assert mrr(["A"], []) == 0.0

    def test_multiple_expected_first_hit_wins(self) -> None:
        """When multiple expected symbols exist, rank of the FIRST hit counts."""
        # 'B' is at rank 1, 'A' is at rank 3 → MRR = 1.0 (rank 1 hit)
        assert mrr(["A", "B"], ["B", "X", "A"]) == 1.0

    def test_removed_edge_lowers_mrr(self) -> None:
        """Removing a result that was at rank 1 drops MRR to < 1.0."""
        # Original: found at rank 1 → MRR = 1.0
        original = mrr(["A"], ["A", "B"])
        assert original == 1.0

        # Degraded: 'A' pushed to rank 2 → MRR = 0.5
        degraded = mrr(["A"], ["X", "A", "B"])
        assert degraded == pytest.approx(0.5)

        assert degraded < original


# ── compute_metrics ───────────────────────────────────────────────────────────


class TestComputeMetrics:
    """Aggregate compute_metrics correctness tests."""

    def test_single_perfect_case(self) -> None:
        """Single case with perfect recall+MRR → both = 1.0."""
        cases = [{"expected_symbols": ["A"], "actual_symbols": ["A", "B"]}]
        result = compute_metrics(cases, k=5)
        assert result["recall_at_k"] == 1.0
        assert result["mrr"] == 1.0
        assert result["n"] == 1
        assert result["k"] == 5

    def test_average_over_multiple_cases(self) -> None:
        """Two cases: one perfect, one total miss → average = 0.5."""
        cases = [
            {"expected_symbols": ["A"], "actual_symbols": ["A"]},  # recall=1.0, mrr=1.0
            {"expected_symbols": ["B"], "actual_symbols": ["X"]},  # recall=0.0, mrr=0.0
        ]
        result = compute_metrics(cases, k=5)
        assert result["recall_at_k"] == pytest.approx(0.5)
        assert result["mrr"] == pytest.approx(0.5)
        assert result["n"] == 2

    def test_empty_cases(self) -> None:
        """Empty cases list → all zeros, no crash."""
        result = compute_metrics([], k=5)
        assert result["recall_at_k"] == 0.0
        assert result["mrr"] == 0.0
        assert result["n"] == 0

    def test_k_parameter_passed_through(self) -> None:
        """k parameter is reflected in the returned dict."""
        result = compute_metrics(
            [{"expected_symbols": ["A"], "actual_symbols": ["A"]}],
            k=20,
        )
        assert result["k"] == 20

    def test_missing_actual_symbols_key(self) -> None:
        """Case without 'actual_symbols' key → treated as empty results."""
        cases = [{"expected_symbols": ["A"]}]
        result = compute_metrics(cases, k=5)
        assert result["recall_at_k"] == 0.0
        assert result["mrr"] == 0.0


# ── SHA-stamp tests ───────────────────────────────────────────────────────────


class TestSHAStamp:
    """SHA stamp helpers are deterministic and detect content mutations."""

    def test_hash_stable_on_same_content(self, tmp_path: Path) -> None:
        """Same fixture content → same hash on two calls (deterministic)."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        (fx / "a.py").write_text("def foo(): pass\n")
        h1 = compute_fixture_hash(fx)
        h2 = compute_fixture_hash(fx)
        assert h1 == h2

    def test_hash_changes_on_content_mutation(self, tmp_path: Path) -> None:
        """Changing a file's content changes the hash."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        f = fx / "a.py"
        f.write_text("def foo(): pass\n")
        h1 = compute_fixture_hash(fx)
        f.write_text("def bar(): pass\n")  # Different content
        h2 = compute_fixture_hash(fx)
        assert h1 != h2

    def test_hash_changes_on_new_file(self, tmp_path: Path) -> None:
        """Adding a new file changes the hash."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        (fx / "a.py").write_text("x = 1\n")
        h1 = compute_fixture_hash(fx)
        (fx / "b.py").write_text("y = 2\n")
        h2 = compute_fixture_hash(fx)
        assert h1 != h2

    def test_empty_fixture_dir_produces_hash(self, tmp_path: Path) -> None:
        """Empty fixture directory → stable hash (empty string digest prefix)."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        h = compute_fixture_hash(fx)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_check_fixture_hash_mismatch_detected(self, tmp_path: Path) -> None:
        """check_fixture_hash returns (False, ...) when fixture was mutated after golden."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        (fx / "a.py").write_text("def foo(): pass\n")
        original_hash = compute_fixture_hash(fx)

        # Write golden with the ORIGINAL hash
        golden_path = tmp_path / "golden.json"
        golden_path.write_text(
            json.dumps({"fixture_hash": original_hash, "queries": []}),
            encoding="utf-8",
        )

        # Mutate the fixture after the golden was written
        (fx / "a.py").write_text("def bar(): pass\n")

        matches, stored, current = check_fixture_hash(golden_path, fx)
        assert not matches
        assert stored == original_hash
        assert current != original_hash

    def test_check_fixture_hash_match(self, tmp_path: Path) -> None:
        """check_fixture_hash returns (True, ...) when fixture matches golden."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        (fx / "a.py").write_text("def foo(): pass\n")
        current_hash = compute_fixture_hash(fx)

        golden_path = tmp_path / "golden.json"
        golden_path.write_text(
            json.dumps({"fixture_hash": current_hash, "queries": []}),
            encoding="utf-8",
        )

        matches, stored, current = check_fixture_hash(golden_path, fx)
        assert matches
        assert stored == current_hash
        assert current == current_hash

    def test_check_fixture_hash_no_hash_in_golden(self, tmp_path: Path) -> None:
        """Golden without fixture_hash key → treated as matching (backward compat)."""
        fx = tmp_path / "fixtures"
        fx.mkdir()
        (fx / "a.py").write_text("def foo(): pass\n")

        golden_path = tmp_path / "golden.json"
        golden_path.write_text(json.dumps({"queries": []}), encoding="utf-8")

        matches, stored, current = check_fixture_hash(golden_path, fx)
        assert matches
        assert stored == ""
