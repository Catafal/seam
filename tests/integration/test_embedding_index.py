"""Integration tests for seam/indexer/embedding_index.py (T4).

TDD: Tests written BEFORE implementation (RED phase).

All tests are GATE-SAFE: fully offline, no network, no model download.
Synthetic float32 vectors created with struct.pack (no numpy needed in tests).
- fastembed absent → index_embeddings returns 0 without crashing.
- Storage path tested with SYNTHETIC vectors injected via monkeypatch.
- Real-model tests behind pytest.importorskip("fastembed") — SKIPPED in gate.

Test groups:
    I1 — Degradation: returns 0 when fastembed absent, does NOT touch the DB.
    I2 — Storage with synthetic vectors: rows written with correct model/dim/vector.
    I3 — Failure sentinel: returns -1 on an unexpected exception (mirrors index_clusters).
    I4 — Real model (behind importorskip — skipped in gate).
"""

import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from seam.indexer.db import init_db

# ── Helper: float32 bytes via struct (no numpy) ───────────────────────────────


def _make_float32_bytes(values: list[float]) -> bytes:
    """Create float32 bytes using struct (no numpy dependency)."""
    return struct.pack(f"{len(values)}f", *values)


def _decode_float32_bytes(blob: bytes) -> list[float]:
    """Decode float32 bytes back to floats using struct."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ── Helper: populate DB with test symbols ─────────────────────────────────────


def _seed_db(conn) -> list[int]:  # type: ignore[type-arg]
    """Insert two files + three symbols; return the symbol ids."""
    with conn:
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/proj/b.py', 'python', 'def', 2.0, 2.0)"
        )
    sym_ids = []
    for name, sig, doc, fid in [
        ("alpha", "def alpha()", "Docs for alpha.", 1),
        ("beta", None, None, 1),
        ("gamma", "def gamma(x: int) -> bool", "Gamma checks x.", 2),
    ]:
        with conn:
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
                "signature, docstring) VALUES (?, ?, 'function', 1, 5, ?, ?)",
                (fid, name, sig, doc),
            )
            sym_ids.append(cur.lastrowid)
    return sym_ids


# ── I1: Degradation when fastembed absent ─────────────────────────────────────


class TestEmbeddingIndexDegradation:
    """I1 — index_embeddings returns 0 (skip) when fastembed is unavailable."""

    def test_returns_zero_when_unavailable(self, tmp_path: Path) -> None:
        """index_embeddings returns 0 when fastembed is not available."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            from seam.indexer.embedding_index import index_embeddings

            result = index_embeddings(conn, model="BAAI/bge-small-en-v1.5", batch=32)

        conn.close()
        assert result == 0

    def test_does_not_write_embeddings_when_unavailable(self, tmp_path: Path) -> None:
        """No rows are written to embeddings when fastembed is absent."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            from seam.indexer.embedding_index import index_embeddings

            index_embeddings(conn, model="BAAI/bge-small-en-v1.5", batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert count == 0

    def test_never_raises_when_unavailable(self, tmp_path: Path) -> None:
        """index_embeddings must never raise when fastembed is absent."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            from seam.indexer.embedding_index import index_embeddings

            try:
                index_embeddings(conn, model="BAAI/bge-small-en-v1.5", batch=32)
            except Exception as exc:  # noqa: BLE001
                conn.close()
                pytest.fail(f"index_embeddings raised unexpectedly: {exc}")

        conn.close()

    def test_returns_zero_on_empty_db(self, tmp_path: Path) -> None:
        """index_embeddings returns 0 on a DB with no symbols."""
        conn = init_db(tmp_path / "test.db")

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            from seam.indexer.embedding_index import index_embeddings

            result = index_embeddings(conn, model="BAAI/bge-small-en-v1.5", batch=32)

        conn.close()
        assert result == 0


# ── I2: Storage with synthetic vectors ────────────────────────────────────────


class TestEmbeddingIndexStorage:
    """I2 — Rows written with correct model/dim/vector when embed_texts is mocked."""

    def _make_synthetic_embed_texts(self, dim: int = 4):
        """Return a fake embed_texts that generates deterministic float32 vectors."""

        def _fake_embed_texts(texts: list[str], model: str) -> list[bytes]:
            """Produce one synthetic float32 vector per text (deterministic)."""
            result = []
            for i, _ in enumerate(texts):
                # Deterministic: fill with (i+1) * 0.1 for each dim
                values = [(i + 1) * 0.1] * dim
                result.append(_make_float32_bytes(values))
            return result

        return _fake_embed_texts

    def test_storage_writes_correct_row_count(self, tmp_path: Path) -> None:
        """index_embeddings writes one embedding row per symbol."""
        conn = init_db(tmp_path / "test.db")
        sym_ids = _seed_db(conn)  # 3 symbols

        fake_embed = self._make_synthetic_embed_texts(dim=4)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed):
                from seam.indexer.embedding_index import index_embeddings

                result = index_embeddings(conn, model="test-model", batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()

        assert result == len(sym_ids)
        assert count == len(sym_ids)

    def test_storage_writes_correct_model(self, tmp_path: Path) -> None:
        """embeddings.model column matches the model parameter."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        fake_embed = self._make_synthetic_embed_texts(dim=4)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed):
                from seam.indexer.embedding_index import index_embeddings

                index_embeddings(conn, model="my-test-model", batch=32)

        rows = conn.execute("SELECT DISTINCT model FROM embeddings").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "my-test-model"

    def test_storage_writes_correct_dim(self, tmp_path: Path) -> None:
        """embeddings.dim column matches the actual vector dimension."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        dim = 8
        fake_embed = self._make_synthetic_embed_texts(dim=dim)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed):
                from seam.indexer.embedding_index import index_embeddings

                index_embeddings(conn, model="test-model", batch=32)

        rows = conn.execute("SELECT DISTINCT dim FROM embeddings").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == dim

    def test_storage_writes_correct_vector_bytes(self, tmp_path: Path) -> None:
        """Vector bytes stored in DB decode back to the original float32 values."""
        conn = init_db(tmp_path / "test.db")
        sym_ids = _seed_db(conn)  # 3 symbols

        dim = 4
        fake_embed = self._make_synthetic_embed_texts(dim=dim)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed):
                from seam.indexer.embedding_index import index_embeddings

                index_embeddings(conn, model="test-model", batch=32)

        rows = conn.execute(
            "SELECT symbol_id, dim, vector FROM embeddings ORDER BY symbol_id"
        ).fetchall()
        conn.close()

        assert len(rows) == len(sym_ids)
        for i, row in enumerate(rows):
            assert row["dim"] == dim
            vec = _decode_float32_bytes(bytes(row["vector"]))
            assert len(vec) == dim
            # Each vector was filled with (i+1)*0.1 — verify approximate equality
            expected = (i + 1) * 0.1
            for val in vec:
                assert abs(val - expected) < 1e-5

    def test_storage_is_idempotent(self, tmp_path: Path) -> None:
        """Calling index_embeddings twice overwrites rows without duplicating them."""
        conn = init_db(tmp_path / "test.db")
        sym_ids = _seed_db(conn)

        fake_embed = self._make_synthetic_embed_texts(dim=4)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed):
                from seam.indexer.embedding_index import index_embeddings

                index_embeddings(conn, model="test-model", batch=32)

            fake_embed2 = self._make_synthetic_embed_texts(dim=4)
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed2):
                index_embeddings(conn, model="test-model", batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        # Still exactly one row per symbol — not doubled
        assert count == len(sym_ids)

    def test_storage_respects_batch_size(self, tmp_path: Path) -> None:
        """batch parameter is respected — embed_texts is called in batches."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)  # 3 symbols

        dim = 4
        batch_calls: list[list[str]] = []

        def tracking_embed(texts: list[str], model: str) -> list[bytes]:
            batch_calls.append(list(texts))
            return [_make_float32_bytes([0.1] * dim) for _ in texts]

        # batch=2 with 3 symbols → 2 calls: [2, 1]
        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=tracking_embed):
                from seam.indexer.embedding_index import index_embeddings

                index_embeddings(conn, model="test-model", batch=2)

        conn.close()
        assert len(batch_calls) == 2
        assert len(batch_calls[0]) == 2
        assert len(batch_calls[1]) == 1


# ── I3: Failure sentinel ──────────────────────────────────────────────────────


class TestEmbeddingIndexFailureSentinel:
    """I3 — index_embeddings returns -1 on unexpected failure (mirrors index_clusters)."""

    def test_returns_minus_one_on_embed_failure(self, tmp_path: Path) -> None:
        """If embed_texts raises, index_embeddings returns -1 (never re-raises)."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        def exploding_embed(texts: list[str], model: str) -> list[bytes]:
            raise RuntimeError("Simulated model failure")

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=exploding_embed):
                from seam.indexer.embedding_index import index_embeddings

                result = index_embeddings(conn, model="test-model", batch=32)

        conn.close()
        assert result == -1

    def test_does_not_raise_on_failure(self, tmp_path: Path) -> None:
        """index_embeddings never re-raises exceptions — always returns int."""
        conn = init_db(tmp_path / "test.db")
        _seed_db(conn)

        def exploding_embed(texts: list[str], model: str) -> list[bytes]:
            raise ValueError("Unexpected error")

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=exploding_embed):
                from seam.indexer.embedding_index import index_embeddings

                try:
                    result = index_embeddings(conn, model="test-model", batch=32)
                    assert isinstance(result, int)
                except Exception as exc:  # noqa: BLE001
                    conn.close()
                    pytest.fail(f"index_embeddings re-raised unexpectedly: {exc}")

        conn.close()


# ── I4: Real model (behind importorskip — skipped in gate) ────────────────────


class TestEmbeddingIndexRealModel:
    """I4 — Real-model tests. Skipped unless fastembed is installed."""

    def test_real_index_embeddings_returns_count(self, tmp_path: Path) -> None:
        """Real index_embeddings returns the symbol count when fastembed is installed."""
        pytest.importorskip("fastembed")
        from seam.indexer.embedding_index import index_embeddings

        conn = init_db(tmp_path / "test.db")
        sym_ids = _seed_db(conn)

        result = index_embeddings(conn, model="BAAI/bge-small-en-v1.5", batch=32)
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()

        assert result == len(sym_ids)
        assert count == len(sym_ids)
