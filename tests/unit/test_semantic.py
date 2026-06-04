"""Unit tests for seam/query/semantic.py (T5).

TDD: Tests written BEFORE implementation (RED phase).

All tests are GATE-SAFE: fully offline, no network, no model download.
Synthetic float32 vectors are injected directly into the DB (struct.pack);
embed_query is monkeypatched where needed — no real fastembed model invoked.

Test groups:
    S1 — rrf_merge: Reciprocal Rank Fusion correctness/ordering (pure, no model).
    S2 — cosine_sim: cosine similarity helper (pure, synthetic vectors).
    S3 — semantic_candidates: ranking with synthetic DB vectors + monkeypatched embedder.
    S4 — semantic_candidates degradation: fastembed absent → []; model mismatch → [].
    S5 — Real model tests (behind pytest.importorskip — skipped in gate).
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from seam.indexer.db import init_db

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes (little-endian struct, no numpy)."""
    return struct.pack(f"{len(values)}f", *values)


def _insert_symbol_with_embedding(
    conn: sqlite3.Connection,
    name: str,
    vector: bytes,
    model: str = "test-model",
    kind: str = "function",
    file_path: str = "/proj/a.py",
) -> int:
    """Insert a symbol + embedding row; return the symbol id."""
    # Ensure file row exists
    conn.execute(
        """
        INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)
        VALUES (?, 'python', 'abc', 1.0, 1.0)
        """,
        (file_path,),
    )
    file_id = conn.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()["id"]

    conn.execute(
        """
        INSERT INTO symbols (file_id, name, kind, start_line, end_line)
        VALUES (?, ?, ?, 1, 5)
        """,
        (file_id, name, kind),
    )
    sym_id = conn.execute(
        "SELECT id FROM symbols WHERE name = ? AND file_id = ?", (name, file_id)
    ).fetchone()["id"]

    dim = len(vector) // 4
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
        (sym_id, model, dim, vector),
    )
    conn.commit()
    return sym_id


# ── S1: rrf_merge (pure, no model) ───────────────────────────────────────────


class TestRrfMerge:
    """S1 — Reciprocal Rank Fusion correctness and ordering (pure logic)."""

    def test_rrf_merge_empty_lists_return_empty(self) -> None:
        """rrf_merge([], []) → empty list."""
        from seam.query.semantic import rrf_merge

        assert rrf_merge([], []) == []

    def test_rrf_merge_fts_only(self) -> None:
        """rrf_merge with no semantic candidates returns all FTS ids (in rank order)."""
        from seam.query.semantic import rrf_merge

        fts = [10, 20, 30]
        result = rrf_merge(fts, [])
        # All FTS ids must be present
        assert set(result) == {10, 20, 30}

    def test_rrf_merge_semantic_only(self) -> None:
        """rrf_merge with no FTS candidates returns all semantic ids."""
        from seam.query.semantic import rrf_merge

        sem = [5, 15, 25]
        result = rrf_merge([], sem)
        assert set(result) == {5, 15, 25}

    def test_rrf_merge_top_item_from_both_ranked_higher(self) -> None:
        """A symbol that ranks first in BOTH lists gets the highest merged score."""
        from seam.query.semantic import rrf_merge

        # Symbol 1 is top in both lists
        fts = [1, 2, 3]
        sem = [1, 4, 5]

        result = rrf_merge(fts, sem)
        # Symbol 1 appears in both → must be ranked first
        assert result[0] == 1

    def test_rrf_merge_preserves_union(self) -> None:
        """The output is exactly the union of FTS and semantic ids, no duplicates."""
        from seam.query.semantic import rrf_merge

        fts = [1, 2, 3]
        sem = [3, 4, 5]  # 3 overlaps

        result = rrf_merge(fts, sem)
        assert set(result) == {1, 2, 3, 4, 5}
        # No duplicates
        assert len(result) == len(set(result))

    def test_rrf_merge_no_duplicates_in_output(self) -> None:
        """rrf_merge never emits the same id twice even when it appears in both lists."""
        from seam.query.semantic import rrf_merge

        shared = [10, 11, 12]
        result = rrf_merge(shared, shared)
        assert len(result) == len(set(result))

    def test_rrf_merge_high_rank_bonus(self) -> None:
        """A symbol ranked 1st gets more reciprocal-rank points than rank 3rd."""
        from seam.query.semantic import rrf_merge

        # Symbol A: rank 1 in FTS only.
        # Symbol B: rank 1 in semantic only.
        # Symbol C: rank 3 in FTS + rank 3 in semantic.
        # C has dual presence but at rank 3 — A and B have rank 1 each.
        fts = [100, 200, 300]
        sem = [200, 100, 300]  # 200 is top in sem; 100 is second; 300 is shared at rank 3

        result = rrf_merge(fts, sem)
        # 100 is rank-1 in fts, rank-2 in sem → high score
        # 200 is rank-2 in fts, rank-1 in sem → high score
        # Both 100 and 200 must appear before 300 (which is rank-3 in both)
        idx_300 = result.index(300)
        idx_100 = result.index(100)
        idx_200 = result.index(200)
        assert idx_300 > idx_100
        assert idx_300 > idx_200

    def test_rrf_merge_k_parameter(self) -> None:
        """Custom k value affects scores but output is still a valid sorted merge."""
        from seam.query.semantic import rrf_merge

        fts = [1, 2, 3]
        sem = [1, 4, 5]

        result_default = rrf_merge(fts, sem)  # k=60 default
        result_k1 = rrf_merge(fts, sem, k=1)

        # Both must contain the union
        assert set(result_default) == set(result_k1) == {1, 2, 3, 4, 5}
        # With k=1, rank differences are amplified vs k=60 (but orderings may differ)
        # Just verify no crash and correct union
        assert len(result_k1) == 5

    def test_rrf_merge_return_type_is_list(self) -> None:
        """rrf_merge always returns a list."""
        from seam.query.semantic import rrf_merge

        result = rrf_merge([1, 2], [3, 4])
        assert isinstance(result, list)

    def test_rrf_merge_stable_across_equal_scores(self) -> None:
        """rrf_merge is deterministic: same inputs → same output order."""
        from seam.query.semantic import rrf_merge

        fts = [10, 20, 30, 40]
        sem = [50, 60, 70, 80]

        r1 = rrf_merge(fts, sem)
        r2 = rrf_merge(fts, sem)
        assert r1 == r2


# ── S2: cosine_sim (pure, synthetic vectors) ─────────────────────────────────


class TestCosineSim:
    """S2 — cosine_sim helper: correct values on synthetic vectors."""

    def test_cosine_identical_vectors_is_one(self) -> None:
        """Cosine similarity of a vector with itself is 1.0 (or very close)."""
        from seam.query.semantic import cosine_sim

        v = _f32([1.0, 0.0, 0.0])
        assert abs(cosine_sim(v, v) - 1.0) < 1e-5

    def test_cosine_orthogonal_vectors_is_zero(self) -> None:
        """Orthogonal vectors have cosine similarity 0.0."""
        from seam.query.semantic import cosine_sim

        v1 = _f32([1.0, 0.0])
        v2 = _f32([0.0, 1.0])
        assert abs(cosine_sim(v1, v2)) < 1e-5

    def test_cosine_opposite_vectors_is_minus_one(self) -> None:
        """Opposite vectors (anti-parallel) have cosine similarity -1.0."""
        from seam.query.semantic import cosine_sim

        v1 = _f32([1.0, 0.0])
        v2 = _f32([-1.0, 0.0])
        assert abs(cosine_sim(v1, v2) - (-1.0)) < 1e-5

    def test_cosine_zero_vector_returns_zero(self) -> None:
        """Zero vector has no direction — cosine should return 0.0 (safe default)."""
        from seam.query.semantic import cosine_sim

        zero = _f32([0.0, 0.0, 0.0])
        v = _f32([1.0, 2.0, 3.0])
        assert cosine_sim(zero, v) == 0.0
        assert cosine_sim(v, zero) == 0.0
        assert cosine_sim(zero, zero) == 0.0

    def test_cosine_partial_similarity(self) -> None:
        """Two vectors at 45° have cosine ≈ 0.707."""
        import math

        from seam.query.semantic import cosine_sim

        v1 = _f32([1.0, 0.0])
        v2 = _f32([1.0, 1.0])  # 45° from v1
        result = cosine_sim(v1, v2)
        expected = 1.0 / math.sqrt(2)
        assert abs(result - expected) < 1e-5

    def test_cosine_different_dim_blobs_returns_zero(self) -> None:
        """If blob lengths differ (corrupt data), cosine_sim returns 0.0 gracefully."""
        from seam.query.semantic import cosine_sim

        v1 = _f32([1.0, 2.0])
        v2 = _f32([1.0, 2.0, 3.0])
        # Different dims → safe fallback
        result = cosine_sim(v1, v2)
        assert result == 0.0


# ── S3: semantic_candidates with synthetic DB vectors ────────────────────────


class TestSemanticCandidates:
    """S3 — semantic_candidates ranking with synthetic vectors in the DB."""

    def _make_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a fresh v7 DB."""
        return init_db(tmp_path / "test.db")

    def test_returns_list(self, tmp_path: Path) -> None:
        """semantic_candidates always returns a list."""
        from seam.query.semantic import semantic_candidates

        conn = self._make_db(tmp_path)

        query_blob = _f32([1.0, 0.0, 0.0])
        # Patch at the seam.query.semantic module level (where names are bound)
        with patch("seam.query.semantic.embed_query", return_value=query_blob):
            with patch("seam.query.semantic.is_available", return_value=True):
                result = semantic_candidates(conn, "test", model="test-model", limit=10)

        conn.close()
        assert isinstance(result, list)

    def test_empty_when_no_embeddings(self, tmp_path: Path) -> None:
        """semantic_candidates returns [] when no embeddings exist in DB."""
        from seam.query.semantic import semantic_candidates

        conn = self._make_db(tmp_path)
        query_blob = _f32([1.0, 0.0, 0.0])

        with patch("seam.query.semantic.embed_query", return_value=query_blob):
            with patch("seam.query.semantic.is_available", return_value=True):
                result = semantic_candidates(conn, "test", model="test-model", limit=10)

        conn.close()
        assert result == []

    def test_returns_top_k_by_cosine(self, tmp_path: Path) -> None:
        """Returns the top-k symbols by cosine similarity, correctly ordered."""
        from seam.query.semantic import semantic_candidates

        conn = self._make_db(tmp_path)

        # Symbol A: vector pointing in the x direction (will match query perfectly)
        id_a = _insert_symbol_with_embedding(conn, "sym_a", _f32([1.0, 0.0, 0.0]))
        # Symbol B: orthogonal vector (low cosine)
        _insert_symbol_with_embedding(conn, "sym_b", _f32([0.0, 1.0, 0.0]))
        # Symbol C: opposite direction (cosine = -1)
        _insert_symbol_with_embedding(conn, "sym_c", _f32([-1.0, 0.0, 0.0]))

        # Query vector in the x direction → sym_a should rank first
        query_blob = _f32([1.0, 0.0, 0.0])

        with patch("seam.query.semantic.embed_query", return_value=query_blob):
            with patch("seam.query.semantic.is_available", return_value=True):
                result = semantic_candidates(
                    conn, "something", model="test-model", limit=3
                )

        conn.close()

        # Result is list of (symbol_id, score) tuples
        ids_ordered = [r[0] for r in result]
        scores = [r[1] for r in result]

        # sym_a must come first (highest cosine)
        assert ids_ordered[0] == id_a

        # Scores must be descending
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    def test_limit_respected(self, tmp_path: Path) -> None:
        """semantic_candidates returns at most `limit` results."""
        from seam.query.semantic import semantic_candidates

        conn = self._make_db(tmp_path)

        # Insert 5 symbols with vectors
        for i in range(5):
            vec = _f32([float(i + 1), 0.0, 0.0])
            _insert_symbol_with_embedding(conn, f"sym_{i}", vec)

        query_blob = _f32([1.0, 0.0, 0.0])

        with patch("seam.query.semantic.embed_query", return_value=query_blob):
            with patch("seam.query.semantic.is_available", return_value=True):
                result = semantic_candidates(
                    conn, "test", model="test-model", limit=3
                )

        conn.close()
        assert len(result) <= 3

    def test_result_contains_tuples_of_int_float(self, tmp_path: Path) -> None:
        """Each result is a (symbol_id: int, score: float) tuple."""
        from seam.query.semantic import semantic_candidates

        conn = self._make_db(tmp_path)
        _insert_symbol_with_embedding(conn, "target", _f32([1.0, 0.0]))

        query_blob = _f32([1.0, 0.0])

        with patch("seam.query.semantic.embed_query", return_value=query_blob):
            with patch("seam.query.semantic.is_available", return_value=True):
                result = semantic_candidates(
                    conn, "q", model="test-model", limit=5
                )

        conn.close()
        assert len(result) == 1
        sym_id, score = result[0]
        assert isinstance(sym_id, int)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0 + 1e-6  # cosine is in [-1, 1]; clamp to [0, 1]


# ── S4: semantic_candidates degradation ──────────────────────────────────────


class TestSemanticCandidatesDegradation:
    """S4 — semantic_candidates returns [] on all degradation paths."""

    def test_empty_when_fastembed_unavailable(self, tmp_path: Path) -> None:
        """Returns [] when fastembed is not installed."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")

        # Patch at seam.query.semantic module level (where is_available is bound)
        with patch("seam.query.semantic.is_available", return_value=False):
            result = semantic_candidates(conn, "retry logic", model="test-model", limit=10)

        conn.close()
        assert result == []

    def test_empty_when_embed_query_returns_empty_bytes(self, tmp_path: Path) -> None:
        """Returns [] when embed_query fails and returns b''."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        _insert_symbol_with_embedding(conn, "any_sym", _f32([1.0, 0.0]))

        # embed_query returns b'' → no valid query vector
        with patch("seam.query.semantic.is_available", return_value=True):
            with patch("seam.query.semantic.embed_query", return_value=b""):
                result = semantic_candidates(
                    conn, "test", model="test-model", limit=10
                )

        conn.close()
        assert result == []

    def test_empty_when_model_mismatch(self, tmp_path: Path) -> None:
        """Returns [] when stored model != configured model (never mix models).

        This is the critical model-mismatch guard: if the DB was indexed with
        'model-A' but the config says 'model-B', the vectors are incompatible
        and semantic_candidates must return [] rather than silently mixing them.
        """
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        # Store embeddings with model-A
        _insert_symbol_with_embedding(
            conn, "indexed_with_a", _f32([1.0, 0.0]), model="model-A"
        )

        query_blob = _f32([1.0, 0.0])

        with patch("seam.query.semantic.is_available", return_value=True):
            with patch("seam.query.semantic.embed_query", return_value=query_blob):
                # Query with model-B → mismatch → must return []
                result = semantic_candidates(
                    conn, "test", model="model-B", limit=10
                )

        conn.close()
        assert result == []

    def test_never_raises_on_any_path(self, tmp_path: Path) -> None:
        """semantic_candidates must never raise — always returns list."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")

        # Even with unavailable fastembed, must not raise
        with patch("seam.query.semantic.is_available", return_value=False):
            try:
                result = semantic_candidates(conn, "anything", model="m", limit=5)
                assert isinstance(result, list)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"semantic_candidates raised unexpectedly: {exc}")

        conn.close()

    def test_empty_when_no_embeddings_for_model(self, tmp_path: Path) -> None:
        """Returns [] when DB has embeddings but none match the configured model."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        # Insert with model-X
        _insert_symbol_with_embedding(conn, "sym", _f32([0.5, 0.5]), model="model-X")

        query_blob = _f32([1.0, 0.0])

        with patch("seam.query.semantic.is_available", return_value=True):
            with patch("seam.query.semantic.embed_query", return_value=query_blob):
                # model-Y has no embeddings in DB
                result = semantic_candidates(
                    conn, "test", model="model-Y", limit=10
                )

        conn.close()
        assert result == []


# ── S5: Real model tests (skipped in gate — fastembed absent) ─────────────────


class TestRealModelSemanticCandidates:
    """S5 — Real-model tests. Skipped unless fastembed is installed."""

    def test_real_semantic_candidates_returns_results(self, tmp_path: Path) -> None:
        """Real semantic_candidates returns non-empty results for a concept query."""
        pytest.importorskip("fastembed")

        from seam.analysis.embeddings import embed_texts, symbol_text
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")

        # Index a symbol with a real embedding
        model = "BAAI/bge-small-en-v1.5"
        text = symbol_text("_backoff_with_jitter", "def _backoff_with_jitter(attempt)", "Retry with exponential backoff.")
        blobs = embed_texts([text], model)
        assert len(blobs) == 1

        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/r.py', 'python', 'abc', 1.0, 1.0)"
        )
        file_id = conn.execute("SELECT id FROM files WHERE path='/proj/r.py'").fetchone()["id"]
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, '_backoff_with_jitter', 'function', 1, 10)",
            (file_id,),
        )
        sym_id = conn.execute(
            "SELECT id FROM symbols WHERE name='_backoff_with_jitter'"
        ).fetchone()["id"]

        dim = len(blobs[0]) // 4
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
            " VALUES (?, ?, ?, ?)",
            (sym_id, model, dim, blobs[0]),
        )
        conn.commit()

        result = semantic_candidates(conn, "retry logic", model=model, limit=5)
        conn.close()

        assert len(result) > 0
        assert result[0][0] == sym_id
