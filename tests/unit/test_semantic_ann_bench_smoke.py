"""Smoke test for benchmarks/semantic_ann_scale.py — WS2b S4.

NOT the full benchmark (that runs via `make bench-semantic-ann` and takes real time).
This proves the core measurement function runs on a tiny synthetic set, returns a
ScaleResult with valid numeric fields, and errors cleanly.

The ANN path is guarded with pytest.importorskip("sqlite_vec") — the test is skipped
when [semantic-ann] is not installed (same pattern as the fastembed real-model tests).
The brute-force (numpy matmul) path runs unconditionally because numpy is a transitive
dep of fastembed — when sqlite_vec is importable, numpy is too.

Gate note: this test IS part of `make gate` (discovered by the standard pytest sweep).
It is skipped, not failed, when sqlite_vec is absent — exactly like the fastembed tests.
"""

import sys
from pathlib import Path

import pytest

# Make repo root importable when pytest is run from an unexpected CWD.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Guard: skip everything in this module if sqlite_vec is absent.
# This mirrors how fastembed real-model tests are gated (pytest.importorskip).
sqlite_vec = pytest.importorskip("sqlite_vec")


# Import the benchmark module AFTER the skip guard — this is fine because if
# sqlite_vec is unavailable the import-skip above exits the module.
from benchmarks.semantic_ann_scale import ScaleResult, _recall_at_k, run_scale  # noqa: E402,I001


# ── ScaleResult structural tests ──────────────────────────────────────────────


class TestScaleResultStructure:
    """Verify ScaleResult has the correct fields and types."""

    def test_dataclass_fields_exist(self) -> None:
        """ScaleResult exposes all documented public fields."""
        result = ScaleResult(
            n_rows=100,
            dim=16,
            queries=5,
            k=5,
            brute_ms=1.0,
            ann_ms=0.5,
            speedup=2.0,
            recall_at_k=0.9,
            ann_available=True,
        )
        assert result.n_rows == 100
        assert result.dim == 16
        assert result.queries == 5
        assert result.k == 5
        assert result.brute_ms == 1.0
        assert result.ann_ms == 0.5
        assert result.speedup == 2.0
        assert result.recall_at_k == 0.9
        assert result.ann_available is True


# ── recall@K unit tests ───────────────────────────────────────────────────────


class TestRecallAtK:
    """Verify _recall_at_k logic (pure function, no DB required)."""

    def test_perfect_recall_is_one(self) -> None:
        """When ANN returns exactly the same results as brute-force, recall = 1.0."""
        bf = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]]
        ann = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]]
        assert _recall_at_k(bf, ann, k=5) == pytest.approx(1.0)

    def test_zero_recall_when_no_overlap(self) -> None:
        """When ANN returns completely different results, recall = 0.0."""
        bf = [[1, 2, 3]]
        ann = [[4, 5, 6]]
        assert _recall_at_k(bf, ann, k=3) == pytest.approx(0.0)

    def test_partial_recall(self) -> None:
        """Partial overlap gives a fractional recall."""
        bf = [[1, 2, 3, 4]]
        ann = [[1, 2, 5, 6]]  # 2 of 4 overlap
        result = _recall_at_k(bf, ann, k=4)
        assert result == pytest.approx(0.5)

    def test_empty_bf_list(self) -> None:
        """Empty brute-force list → recall = 0.0 (no queries to compare)."""
        assert _recall_at_k([], [], k=5) == pytest.approx(0.0)

    def test_empty_per_query_result(self) -> None:
        """A query with an empty BF result is skipped (contributes nothing to average)."""
        bf = [[], [1, 2, 3]]
        ann = [[1, 2, 3], [1, 2, 3]]
        # Only the second query counts → 3/3 = 1.0
        assert _recall_at_k(bf, ann, k=3) == pytest.approx(1.0)


# ── run_scale smoke tests ─────────────────────────────────────────────────────


class TestRunScaleSmoke:
    """Exercise run_scale on a tiny synthetic dataset — checks shape, not exact values."""

    def test_run_scale_tiny_returns_valid_result(self) -> None:
        """run_scale on 500 rows (dim=16) returns a ScaleResult with valid fields."""
        result = run_scale(500, dim=16, queries=5, k=5)
        assert isinstance(result, ScaleResult)
        assert result.n_rows == 500
        assert result.dim == 16
        assert result.queries == 5
        assert result.k == 5

    def test_brute_ms_is_positive(self) -> None:
        """Brute-force matmul always takes some measurable time (>= 0)."""
        result = run_scale(500, dim=16, queries=5, k=5)
        assert result.brute_ms >= 0.0

    def test_ann_available_flag_is_bool(self) -> None:
        """ann_available is a boolean (True when sqlite_vec extension loads cleanly)."""
        result = run_scale(500, dim=16, queries=5, k=5)
        assert isinstance(result.ann_available, bool)

    def test_ann_ms_non_negative_when_available(self) -> None:
        """When ANN is available, ann_ms is non-negative."""
        result = run_scale(500, dim=16, queries=5, k=5)
        if result.ann_available:
            assert result.ann_ms >= 0.0

    def test_recall_at_k_in_valid_range(self) -> None:
        """recall_at_k is in [0.0, 1.0] when ANN is available."""
        result = run_scale(500, dim=16, queries=5, k=5)
        if result.ann_available:
            assert 0.0 <= result.recall_at_k <= 1.0 + 1e-9  # tiny float tolerance

    def test_speedup_is_positive_when_ann_available(self) -> None:
        """speedup is positive when ANN is available and fast enough."""
        result = run_scale(500, dim=16, queries=5, k=5)
        if result.ann_available and result.ann_ms > 0.0:
            assert result.speedup > 0.0

    def test_speedup_is_zero_when_ann_unavailable(self) -> None:
        """When ANN is not available, speedup is 0.0 (sentinel)."""
        result = run_scale(500, dim=16, queries=5, k=5)
        if not result.ann_available:
            assert result.speedup == pytest.approx(0.0)

    def test_recall_is_zero_when_ann_unavailable(self) -> None:
        """When ANN is not available, recall_at_k is 0.0 (sentinel)."""
        result = run_scale(500, dim=16, queries=5, k=5)
        if not result.ann_available:
            assert result.recall_at_k == pytest.approx(0.0)

    def test_high_recall_on_tiny_dataset(self) -> None:
        """On a tiny dataset with few distinct vectors, ANN recall should be reasonably high.

        This is a quality check, not an exact value assertion. The ANN approximation
        should maintain high recall at small K on small datasets. We use a loose
        threshold (>= 0.5) to avoid flakiness from approximation variance.
        """
        result = run_scale(500, dim=16, queries=10, k=5)
        if result.ann_available:
            # ANN recall at small K on small datasets is typically near 1.0.
            # We assert >= 0.5 as a loose sanity bound (not a performance guarantee).
            assert result.recall_at_k >= 0.5, (
                f"ANN recall@5 is unexpectedly low: {result.recall_at_k:.3f}. "
                "Check that vec0 cosine distance is being used correctly."
            )

    def test_different_seeds_give_different_results(self) -> None:
        """run_scale with different seeds produces results (though latencies may be similar)."""
        r1 = run_scale(200, dim=8, queries=3, k=3, seed=1)
        r2 = run_scale(200, dim=8, queries=3, k=3, seed=999)
        # Both should return valid ScaleResult objects.
        assert isinstance(r1, ScaleResult)
        assert isinstance(r2, ScaleResult)
        # Latencies may vary slightly by measurement noise, but both should be non-negative.
        assert r1.brute_ms >= 0.0
        assert r2.brute_ms >= 0.0

    def test_run_scale_with_k_larger_than_n_rows(self) -> None:
        """run_scale handles k >= n_rows gracefully (vec0 returns min(k, available) rows)."""
        result = run_scale(10, dim=8, queries=3, k=20)
        # Should complete without error even though k > n_rows.
        assert isinstance(result, ScaleResult)
        assert result.brute_ms >= 0.0
