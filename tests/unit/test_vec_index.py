"""Unit tests for seam/indexer/vec_index.py — WS2b S2.

TDD: one behavior → one test. All DB-level tests use an in-memory SQLite
connection with a hand-crafted embeddings table and synthetic float32 vectors
(struct.pack) — no fastembed model required.

Tests requiring the real sqlite-vec extension are guarded with
    pytest.importorskip("sqlite_vec")
and skip automatically when [semantic-ann] is not installed.

Test groups:
  VI1 — gate: SEAM_VEC_ANN=off → returns 0 immediately.
  VI2 — gate: probe returns False → returns 0.
  VI3 — gate: row-count below SEAM_VEC_ANN_MIN_ROWS → returns 0.
  VI4 — gate: no embeddings at all → returns 0.
  VI5 — success path: returns ≥1, vec0 table exists, idempotent.
  VI6 — cosine metric: KNN result matches hand-computed cosine top-1.
  VI7 — staleness token: vec_meta is populated and matches compute_index_version.
  VI8 — failure resilience: simulated inner error → returns -1, never raises.
"""

import sqlite3
import struct
from unittest.mock import patch

import pytest

import seam.config as config

# Guard: only import vec_index (and transitively vec_extension/vector_store) once we
# know config is importable. The module itself never raises on import.
from seam.indexer.vec_index import VEC_META_TABLE, VEC_TABLE, index_vec
from seam.query.vector_store import compute_index_version

# ── Helpers ───────────────────────────────────────────────────────────────────


def _conn_with_embeddings(
    *,
    n_rows: int = 5,
    dim: int = 4,
    model: str = "test-model",
) -> sqlite3.Connection:
    """Return an in-memory connection with synthetic embeddings rows.

    Creates a minimal `embeddings` table (same schema as the real DB) and populates
    it with `n_rows` synthetic float32 vectors. Uses struct.pack — no fastembed needed.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE embeddings (
            symbol_id INTEGER PRIMARY KEY,
            model     TEXT NOT NULL,
            dim       INTEGER NOT NULL,
            vector    BLOB NOT NULL
        )
        """
    )
    for i in range(1, n_rows + 1):
        # Simple orthogonal-ish vectors: element i-1 = 1.0, rest 0.0 (unit basis).
        # For dim > n_rows wrap around so every vector is normalised.
        vec = [0.0] * dim
        vec[(i - 1) % dim] = 1.0
        blob = struct.pack(f"{dim}f", *vec)
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (i, model, dim, blob),
        )

    conn.commit()
    return conn


def _require_sqlite_vec() -> None:
    """Skip the test if sqlite-vec is not installed."""
    pytest.importorskip("sqlite_vec")


# ═══════════════════════════════════════════════════════════════════════════════
# VI1 — gate: SEAM_VEC_ANN=off → returns 0 immediately
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI1MasterSwitchOff:
    """index_vec returns 0 immediately when SEAM_VEC_ANN is "off"."""

    def test_vi1_1_returns_zero_when_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """index_vec returns 0 when SEAM_VEC_ANN=off (master switch)."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "off")
        conn = _conn_with_embeddings(n_rows=100_000)
        result = index_vec(conn, model="test-model")
        assert result == 0

    def test_vi1_2_never_raises_when_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """index_vec never raises when SEAM_VEC_ANN=off."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "off")
        conn = _conn_with_embeddings()
        # Must not raise regardless of conn state.
        result = index_vec(conn, model="test-model")
        assert isinstance(result, int)

    def test_vi1_3_no_vec_table_created_when_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When SEAM_VEC_ANN=off, no vec_embeddings table is created on conn."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "off")
        conn = _conn_with_embeddings()
        index_vec(conn, model="test-model")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert VEC_TABLE not in tables, "vec0 table must NOT be created when master switch is off"


# ═══════════════════════════════════════════════════════════════════════════════
# VI2 — gate: probe returns False → returns 0
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI2ProbeFails:
    """index_vec returns 0 when the sqlite-vec probe fails."""

    def test_vi2_1_returns_zero_when_probe_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns 0 when probe_vec_extension returns False."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=2)

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=False):
            result = index_vec(conn, model="test-model")

        assert result == 0

    def test_vi2_2_never_raises_when_probe_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec never raises when probe_vec_extension returns False."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=2)

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=False):
            result = index_vec(conn, model="test-model")

        assert isinstance(result, int)

    def test_vi2_3_no_table_created_when_probe_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No vec0 table is created when the probe fails."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=2)

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=False):
            index_vec(conn, model="test-model")

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert VEC_TABLE not in tables


# ═══════════════════════════════════════════════════════════════════════════════
# VI3 — gate: row-count below SEAM_VEC_ANN_MIN_ROWS → returns 0
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI3BelowMinRows:
    """index_vec returns 0 when embedding row-count < SEAM_VEC_ANN_MIN_ROWS."""

    def test_vi3_1_returns_zero_below_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns 0 when row-count is below the minimum threshold."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 100)  # threshold: 100

        # Only 5 rows — well below 100.
        conn = _conn_with_embeddings(n_rows=5)

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            result = index_vec(conn, model="test-model")

        assert result == 0

    def test_vi3_2_returns_zero_at_threshold_minus_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns 0 when row-count == MIN_ROWS - 1 (exclusive lower bound)."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 10)

        conn = _conn_with_embeddings(n_rows=9)  # one short

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            result = index_vec(conn, model="test-model")

        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════════
# VI4 — gate: no embeddings at all → returns 0
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI4NoEmbeddings:
    """index_vec returns 0 when the embeddings table is empty for the model."""

    def test_vi4_1_returns_zero_with_empty_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns 0 when there are no embeddings rows for the model."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        # Empty embeddings table.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE embeddings (
                symbol_id INTEGER PRIMARY KEY,
                model     TEXT NOT NULL,
                dim       INTEGER NOT NULL,
                vector    BLOB NOT NULL
            )
            """
        )

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            result = index_vec(conn, model="test-model")

        assert result == 0

    def test_vi4_2_returns_zero_for_wrong_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns 0 when embeddings exist but for a different model."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        # Embeddings exist, but for model "other", not "test-model".
        conn = _conn_with_embeddings(n_rows=5, model="other")

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            result = index_vec(conn, model="test-model")

        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════════
# VI5 — success path: returns ≥1, vec0 table exists, idempotent
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI5SuccessPath:
    """Success-path tests that require the real sqlite-vec extension."""

    def setup_method(self) -> None:
        _require_sqlite_vec()

    def _make_conn_above_threshold(self, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
        """Return a conn with embeddings above the threshold."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 3)  # low threshold for tests
        conn = _conn_with_embeddings(n_rows=5, dim=4, model="test-model")
        conn.row_factory = sqlite3.Row
        return conn

    def test_vi5_1_returns_row_count_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns the number of rows indexed (≥1) on success."""
        conn = self._make_conn_above_threshold(monkeypatch)
        result = index_vec(conn, model="test-model")
        assert result == 5  # 5 embeddings → 5 rows in vec0

    def test_vi5_2_vec_table_exists_after_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After index_vec, the vec_embeddings virtual table exists on conn."""
        conn = self._make_conn_above_threshold(monkeypatch)
        index_vec(conn, model="test-model")

        # The vec0 table is visible in sqlite_master as a virtual table.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (VEC_TABLE,),
        ).fetchone()
        assert row is not None, f"Expected {VEC_TABLE!r} table to exist after index_vec"

    def test_vi5_3_idempotent_second_call_same_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling index_vec twice yields the same row count, no duplicates."""
        conn = self._make_conn_above_threshold(monkeypatch)

        result1 = index_vec(conn, model="test-model")
        result2 = index_vec(conn, model="test-model")

        assert result1 == result2 == 5, (
            f"Second call must rebuild to same count: first={result1}, second={result2}"
        )

    def test_vi5_4_idempotent_no_row_duplication(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After two index_vec calls, the vec0 table has exactly n rows (no duplicates)."""
        conn = self._make_conn_above_threshold(monkeypatch)
        index_vec(conn, model="test-model")
        index_vec(conn, model="test-model")

        # Count rows in vec0 via a KNN query with a large LIMIT.
        # sqlite-vec does not support COUNT(*) directly on vec0 virtual tables,
        # so we use SELECT rowid … with a large LIMIT to enumerate all rows.
        query_vec = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        rows = conn.execute(
            f"SELECT rowid FROM {VEC_TABLE} WHERE embedding MATCH ? ORDER BY distance LIMIT 100",
            (query_vec,),
        ).fetchall()
        assert len(rows) == 5, f"Expected 5 unique rows, got {len(rows)}"

    def test_vi5_5_never_raises_on_success_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec never raises on the success path."""
        conn = self._make_conn_above_threshold(monkeypatch)
        result = index_vec(conn, model="test-model")
        assert isinstance(result, int) and result >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# VI6 — cosine metric: KNN result matches hand-computed cosine top-1
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI6CosineMetric:
    """Verify that vec0 uses cosine distance and its ordering matches brute-force."""

    def setup_method(self) -> None:
        _require_sqlite_vec()

    def test_vi6_1_knn_nearest_matches_cosine_top1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KNN result's top-1 matches the brute-force cosine top-1 on a small set."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        # Three 4-dim vectors; query is v1.
        # v1 = [1,0,0,0]  — identical to query → cosine_sim = 1.0 (dist = 0)
        # v2 = [0,1,0,0]  — orthogonal          → cosine_sim = 0.0 (dist = 1)
        # v3 = [0.9,0.1,0,0] — close to query  → cosine_sim ≈ 0.994 (dist ≈ 0.006)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE embeddings (
                symbol_id INTEGER PRIMARY KEY,
                model     TEXT NOT NULL,
                dim       INTEGER NOT NULL,
                vector    BLOB NOT NULL
            )
            """
        )
        vecs = {
            1: struct.pack("4f", 1.0, 0.0, 0.0, 0.0),
            2: struct.pack("4f", 0.0, 1.0, 0.0, 0.0),
            3: struct.pack("4f", 0.9, 0.1, 0.0, 0.0),
        }
        for sid, blob in vecs.items():
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (sid, "test-model", 4, blob),
            )
        conn.commit()

        index_vec(conn, model="test-model")

        # KNN query: nearest to v1 (symbol_id=1).
        query_vec = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        rows = conn.execute(
            f"SELECT rowid, distance FROM {VEC_TABLE} "
            f"WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
            (query_vec,),
        ).fetchall()

        assert len(rows) >= 1, "KNN query returned no rows"
        top1_rowid = rows[0][0]
        assert top1_rowid == 1, (
            f"KNN top-1 should be symbol_id=1 (identical vector), got rowid={top1_rowid}"
        )

    def test_vi6_2_ordering_consistent_with_cosine_similarity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KNN ordering is ascending distance (= descending cosine similarity)."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE embeddings (
                symbol_id INTEGER PRIMARY KEY,
                model     TEXT NOT NULL,
                dim       INTEGER NOT NULL,
                vector    BLOB NOT NULL
            )
            """
        )
        # v1 identical to query → dist=0, v3 close → dist≈0.006, v2 orthogonal → dist=1
        for sid, vals in [(1, (1.0, 0.0, 0.0, 0.0)),
                          (2, (0.0, 1.0, 0.0, 0.0)),
                          (3, (0.9, 0.1, 0.0, 0.0))]:
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (sid, "test-model", 4, struct.pack("4f", *vals)),
            )
        conn.commit()

        index_vec(conn, model="test-model")

        query_vec = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        rows = conn.execute(
            f"SELECT rowid, distance FROM {VEC_TABLE} "
            f"WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
            (query_vec,),
        ).fetchall()

        row_ids = [r[0] for r in rows]
        distances = [r[1] for r in rows]

        # rowid order must be: 1 (dist=0), 3 (dist≈0.006), 2 (dist=1.0)
        assert row_ids == [1, 3, 2], f"Expected [1, 3, 2] got {row_ids}"
        # distances must be strictly non-decreasing.
        assert distances[0] <= distances[1] <= distances[2], (
            f"Distances must be non-decreasing: {distances}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# VI7 — staleness token: vec_meta is populated and matches compute_index_version
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI7StalenessToken:
    """Verify that vec_meta is written with the correct staleness token."""

    def setup_method(self) -> None:
        _require_sqlite_vec()

    def test_vi7_1_vec_meta_table_exists_after_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After index_vec, the vec_meta ordinary table exists on conn."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)
        conn = _conn_with_embeddings(n_rows=3, dim=4, model="test-model")
        index_vec(conn, model="test-model")

        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (VEC_META_TABLE,),
        ).fetchone()
        assert row is not None, f"Expected {VEC_META_TABLE!r} table to exist after index_vec"

    def test_vi7_2_stored_token_matches_compute_index_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The staleness token in vec_meta equals compute_index_version(conn, model)."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)
        conn = _conn_with_embeddings(n_rows=4, dim=4, model="test-model")
        index_vec(conn, model="test-model")

        stored_row = conn.execute(
            f"SELECT index_version FROM {VEC_META_TABLE} WHERE model = ?",
            ("test-model",),
        ).fetchone()
        assert stored_row is not None, "vec_meta must have a row for the model"

        expected_token = compute_index_version(conn, "test-model")
        assert stored_row[0] == expected_token, (
            f"Stored token {stored_row[0]!r} != expected {expected_token!r}"
        )

    def test_vi7_3_token_updates_on_second_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After adding an embedding and rebuilding, the stored token is refreshed."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)
        conn = _conn_with_embeddings(n_rows=3, dim=4, model="test-model")
        index_vec(conn, model="test-model")

        # Add a new embedding row.
        new_vec = struct.pack("4f", 0.5, 0.5, 0.0, 0.0)
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (99, "test-model", 4, new_vec),
        )
        conn.commit()

        # Rebuild.
        index_vec(conn, model="test-model")

        stored_row = conn.execute(
            f"SELECT index_version FROM {VEC_META_TABLE} WHERE model = ?",
            ("test-model",),
        ).fetchone()
        assert stored_row is not None

        expected_token = compute_index_version(conn, "test-model")
        assert stored_row[0] == expected_token, (
            f"Token after rebuild {stored_row[0]!r} != expected {expected_token!r}"
        )

    def test_vi7_4_dim_stored_in_vec_meta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """vec_meta stores the correct embedding dimensionality."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)
        conn = _conn_with_embeddings(n_rows=3, dim=8, model="test-model")
        index_vec(conn, model="test-model")

        stored_row = conn.execute(
            f"SELECT dim FROM {VEC_META_TABLE} WHERE model = ?",
            ("test-model",),
        ).fetchone()
        assert stored_row is not None
        assert stored_row[0] == 8, f"Expected dim=8, got {stored_row[0]}"


# ═══════════════════════════════════════════════════════════════════════════════
# VI8 — failure resilience: simulated inner error → returns -1, never raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestVI8FailureResilience:
    """index_vec returns -1 and never raises when the inner build fails."""

    def test_vi8_1_returns_minus_one_on_inner_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec returns -1 when _build_vec_index raises an unexpected exception."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=5)

        # Simulate load_vec_extension failing in a way that causes the inner impl to raise.
        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            with patch(
                "seam.indexer.vec_index.load_vec_extension",
                side_effect=RuntimeError("simulated crash"),
            ):
                result = index_vec(conn, model="test-model")

        assert result == -1, f"Expected -1 on failure, got {result}"

    def test_vi8_2_never_raises_on_inner_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """index_vec never propagates exceptions — always returns an int."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=5)

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            with patch(
                "seam.indexer.vec_index.load_vec_extension",
                side_effect=RuntimeError("simulated crash"),
            ):
                # Must not raise — must return -1.
                result = index_vec(conn, model="test-model")

        assert isinstance(result, int)

    def test_vi8_3_failure_does_not_affect_embeddings_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed ANN build leaves the embeddings table intact (no corruption)."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=5, model="test-model")
        before_count = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model='test-model'"
        ).fetchone()[0]

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            with patch(
                "seam.indexer.vec_index.load_vec_extension",
                side_effect=RuntimeError("simulated crash"),
            ):
                index_vec(conn, model="test-model")

        after_count = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model='test-model'"
        ).fetchone()[0]
        assert after_count == before_count, (
            "Embeddings table must not be modified by a failed ANN build"
        )

    def test_vi8_4_logs_warning_on_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """index_vec logs exactly one WARNING when the inner build fails."""
        monkeypatch.setattr(config, "SEAM_VEC_ANN", "on")
        monkeypatch.setattr(config, "SEAM_VEC_ANN_MIN_ROWS", 1)

        conn = _conn_with_embeddings(n_rows=5)

        with patch("seam.indexer.vec_index.probe_vec_extension", return_value=True):
            with patch(
                "seam.indexer.vec_index.load_vec_extension",
                side_effect=RuntimeError("simulated crash"),
            ):
                with caplog.at_level("WARNING", logger="seam.indexer.vec_index"):
                    index_vec(conn, model="test-model")

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) >= 1, "Expected at least one WARNING log on build failure"
