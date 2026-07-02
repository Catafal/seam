"""Integration test: seam/query/semantic.py fallback parity — WS2a Slice 1.

Proves that semantic_candidates returns byte-identical top-k results whether
the mmap artifact is present (mmap path) or deleted (SQL fallback path).

All tests are GATE-SAFE: no fastembed, no model download.
Synthetic float32 vectors inserted directly into the DB and via write_store.
embed_query is monkeypatched at seam.query.semantic to return a synthetic query vector.

Test groups:
    FP1 — parity: mmap path vs SQL path return the same (symbol_id, score) list.
    FP2 — stale artifact detection: a stale version token forces SQL fallback.
    FP3 — SEAM_VECTOR_STORE=off: no artifact read, always SQL path.
"""

import struct
from pathlib import Path
from unittest.mock import patch

from seam.indexer.db import init_db
from seam.query.vector_store import compute_index_version, write_store

# ── Helpers ───────────────────────────────────────────────────────────────────

MODEL = "test-model"


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _seed_embeddings(conn, num_symbols: int = 5, dim: int = 3) -> list[int]:
    """Insert symbols + embeddings into the DB. Returns symbol_ids in insertion order."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
    fid = conn.execute("SELECT id FROM files WHERE path='/proj/a.py'").fetchone()["id"]

    sym_ids = []
    blobs = []
    for i in range(num_symbols):
        with conn:
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                " VALUES (?, ?, 'function', ?, ?)",
                (fid, f"fn_{i}", i * 2 + 1, i * 2 + 2),
            )
            sid = cur.lastrowid

        # Each symbol gets a distinct vector: one-hot-like in dim-space, normalized
        vec = [0.0] * dim
        vec[i % dim] = 1.0
        blob = _f32(vec)
        sym_ids.append(sid)
        blobs.append(blob)

    # Insert embeddings in a single batch
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
            " VALUES (?, ?, ?, ?)",
            [(sid, MODEL, dim, blob) for sid, blob in zip(sym_ids, blobs)],
        )

    return sym_ids


def _get_db_dir(conn) -> Path:
    """Get the parent dir of the DB file."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if row[1] == "main" and row[2]:
            return Path(row[2]).parent
    raise RuntimeError("DB has no file-backed main database")


# ── FP1: mmap path vs SQL path parity ────────────────────────────────────────


class TestFallbackParity:
    """FP1 — mmap and SQL paths return byte-identical top-k for the same query."""

    def test_mmap_path_matches_sql_fallback(self, tmp_path: Path) -> None:
        """semantic_candidates returns the same results with and without the artifact."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "seam.db")
        _seed_embeddings(conn, num_symbols=5, dim=3)

        # Query vector: (1, 0, 0)
        query_vec = _f32([1.0, 0.0, 0.0])

        # ── SQL-only path (no artifact) ───────────────────────────────────
        # Ensure no artifact exists
        assert not (tmp_path / "vectors.f32").exists()

        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "off"),
        ):
            sql_results = semantic_candidates(conn, "query", model=MODEL, limit=5)

        # ── mmap path: write artifact, then query ─────────────────────────
        # Build the artifact from the DB rows (mirrors what _write_artifact does)
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model=? ORDER BY symbol_id",
            (MODEL,),
        ).fetchall()
        blobs = [bytes(r["vector"]) for r in rows]
        persisted_ids = [r["symbol_id"] for r in rows]
        index_version = compute_index_version(conn, MODEL)

        write_store(
            tmp_path,
            persisted_ids,
            blobs,
            model=MODEL,
            dim=3,
            index_version=index_version,
        )
        assert (tmp_path / "vectors.f32").exists()

        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "on"),
        ):
            mmap_results = semantic_candidates(conn, "query", model=MODEL, limit=5)

        # Both paths must return the same ordered list
        assert sql_results == mmap_results, (
            f"mmap results differ from SQL results:\n"
            f"  mmap: {mmap_results}\n"
            f"  SQL:  {sql_results}"
        )

    def test_artifact_deleted_falls_back_to_sql(self, tmp_path: Path) -> None:
        """Deleting the artifact causes semantic_candidates to fall back to SQL."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "seam.db")
        _seed_embeddings(conn, num_symbols=3, dim=2)
        query_vec = _f32([1.0, 0.0])

        # Get baseline from SQL path (no artifact)
        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "off"),
        ):
            sql_results = semantic_candidates(conn, "query", model=MODEL, limit=3)

        # Write artifact, verify it loads
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model=? ORDER BY symbol_id",
            (MODEL,),
        ).fetchall()
        write_store(
            tmp_path,
            [r["symbol_id"] for r in rows],
            [bytes(r["vector"]) for r in rows],
            model=MODEL,
            dim=2,
            index_version=compute_index_version(conn, MODEL),
        )

        # Delete the artifact
        (tmp_path / "vectors.f32").unlink()
        (tmp_path / "vectors.ids.i64").unlink()
        (tmp_path / "vectors.meta.json").unlink()

        # Query with VECTOR_STORE=on but no artifact → should fall back to SQL
        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "on"),
        ):
            fallback_results = semantic_candidates(conn, "query", model=MODEL, limit=3)

        assert fallback_results == sql_results


# ── FP2: Stale artifact detection ────────────────────────────────────────────


class TestStaleArtifact:
    """FP2 — a stale index-version token forces SQL fallback, still correct results."""

    def test_stale_artifact_forces_sql_fallback(self, tmp_path: Path) -> None:
        """A stale artifact (wrong index_version) causes silent fallback to SQL path."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "seam.db")
        _seed_embeddings(conn, num_symbols=3, dim=2)
        query_vec = _f32([1.0, 0.0])

        # Write artifact with a deliberately stale version token
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model=? ORDER BY symbol_id",
            (MODEL,),
        ).fetchall()
        write_store(
            tmp_path,
            [r["symbol_id"] for r in rows],
            [bytes(r["vector"]) for r in rows],
            model=MODEL,
            dim=2,
            index_version="STALE:999",  # won't match current DB state
        )

        # Get SQL baseline for comparison
        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "off"),
        ):
            sql_results = semantic_candidates(conn, "query", model=MODEL, limit=3)

        # Query with stale artifact present → stale detection → SQL fallback
        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "on"),
        ):
            stale_results = semantic_candidates(conn, "query", model=MODEL, limit=3)

        # Should still return correct results (via SQL fallback)
        assert stale_results == sql_results


# ── FP3: SEAM_VECTOR_STORE=off ───────────────────────────────────────────────


class TestVectorStoreOff:
    """FP3 — SEAM_VECTOR_STORE=off is byte-identical to the SQL-only path."""

    def test_off_does_not_write_artifact(self, tmp_path: Path) -> None:
        """With SEAM_VECTOR_STORE=off, embedding_index does NOT write the artifact."""
        from seam.indexer.embedding_index import index_embeddings

        conn = init_db(tmp_path / "seam.db")
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
                " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
            )
            fid = conn.execute("SELECT id FROM files WHERE path='/proj/a.py'").fetchone()["id"]
            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                " VALUES (?, 'fn', 'function', 1, 2)",
                (fid,),
            )

        fake_blob = _f32([0.5, 0.5, 0.5])

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", return_value=[fake_blob]),
            patch("seam.indexer.embedding_index.symbol_text", return_value="fn"),
            patch("seam.config.SEAM_VECTOR_STORE", "off"),
        ):
            result = index_embeddings(conn, model=MODEL, batch=32)

        assert result == 1  # embedding succeeded
        # Artifact must NOT have been written
        assert not (tmp_path / "vectors.f32").exists()
        assert not (tmp_path / "vectors.meta.json").exists()

    def test_off_does_not_read_artifact(self, tmp_path: Path) -> None:
        """With SEAM_VECTOR_STORE=off, semantic_candidates uses SQL even when artifact exists."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "seam.db")
        _seed_embeddings(conn, num_symbols=3, dim=2)
        query_vec = _f32([1.0, 0.0])

        # Write the artifact
        rows = conn.execute(
            "SELECT symbol_id, vector FROM embeddings WHERE model=? ORDER BY symbol_id",
            (MODEL,),
        ).fetchall()
        write_store(
            tmp_path,
            [r["symbol_id"] for r in rows],
            [bytes(r["vector"]) for r in rows],
            model=MODEL,
            dim=2,
            index_version=compute_index_version(conn, MODEL),
        )

        # Track whether load_store was called
        load_calls = []

        original_load = __import__(
            "seam.query.vector_store", fromlist=["load_store"]
        ).load_store

        def _tracking_load(store_dir: Path, model: str):
            load_calls.append((store_dir, model))
            return original_load(store_dir, model)

        with (
            patch("seam.query.semantic.is_available", return_value=True),
            patch("seam.query.semantic.embed_query", return_value=query_vec),
            patch("seam.config.SEAM_VECTOR_STORE", "off"),
            patch("seam.query.semantic.load_store", side_effect=_tracking_load),
        ):
            semantic_candidates(conn, "query", model=MODEL, limit=3)

        # load_store must NOT have been called (SEAM_VECTOR_STORE=off bypasses the mmap path)
        assert load_calls == [], f"load_store was called unexpectedly: {load_calls}"
