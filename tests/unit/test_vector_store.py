"""Unit tests for seam/query/vector_store.py — WS2a Slice 1.

TDD: vertical slices — one test → one implementation → repeat.

All tests are GATE-SAFE: no fastembed, no model download.
Synthetic float32 vectors built with struct.pack or numpy (when available via the
[semantic] extra already present in the gate environment: numpy is installed as a
transitive dependency of [web] / [semantic]).

Test groups:
    VS1 — round-trip: write_store → load_store → matrix values, ids, meta match.
    VS2 — top_k correctness: same ordered (symbol_id, score) as SQL brute-force path.
    VS3 — atomic write: an interrupted write does not leave a readable partial artifact.
    VS4 — None branches: absent / corrupt / truncated / model-mismatch / dtype-mismatch / stale.
    VS5 — get_artifact_dir: file-backed DB → parent path; in-memory DB → None.
    VS6 — compute_index_version: correct token from DB; "0:0" fallback.
"""

import json
import sqlite3
import struct
from pathlib import Path

import numpy as np
import pytest

from seam.indexer.db import init_db
from seam.query.vector_store import (
    compute_index_version,
    get_artifact_dir,
    load_store,
    top_k,
    write_store,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _make_matrix(rows: list[list[float]]) -> np.ndarray:
    """Build a float32 numpy matrix from a list of row vectors."""
    return np.array(rows, dtype=np.float32)


def _cosine(a: list[float], b: list[float]) -> float:
    """Reference cosine similarity for test assertions."""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── VS1: Round-trip ──────────────────────────────────────────────────────────


class TestRoundTrip:
    """VS1 — write_store → load_store → values match exactly."""

    def test_matrix_values_preserved(self, tmp_path: Path) -> None:
        """Round-trip: loaded matrix values match written values."""
        rows = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        mat = _make_matrix(rows)
        ids = [10, 20, 30]

        write_store(tmp_path, ids, mat, model="test-model", dim=3, index_version="3:30")
        store = load_store(tmp_path, model="test-model")

        assert store is not None
        # Matrix values must match exactly (float32 round-trip)
        np.testing.assert_array_equal(store.matrix, mat)

    def test_symbol_ids_aligned(self, tmp_path: Path) -> None:
        """Round-trip: loaded symbol_ids align row-for-row with the matrix."""
        mat = _make_matrix([[1.0, 2.0], [3.0, 4.0]])
        ids = [101, 202]

        write_store(tmp_path, ids, mat, model="test-model", dim=2, index_version="2:202")
        store = load_store(tmp_path, model="test-model")

        assert store is not None
        assert list(store.symbol_ids) == ids

    def test_metadata_fields_match(self, tmp_path: Path) -> None:
        """Round-trip: metadata fields on the store handle match written values."""
        mat = _make_matrix([[0.5, 0.5, 0.5]])
        ids = [42]

        write_store(
            tmp_path, ids, mat, model="my-model", dim=3, index_version="1:42"
        )
        store = load_store(tmp_path, model="my-model")

        assert store is not None
        assert store.model == "my-model"
        assert store.dim == 3
        assert store.nrows == 1
        assert store.index_version == "1:42"

    def test_write_from_blobs_round_trips(self, tmp_path: Path) -> None:
        """write_store accepts a list of float32 byte blobs (not just numpy arrays)."""
        vecs = [[1.0, 0.0], [0.0, 1.0]]
        blobs = [_f32(v) for v in vecs]
        ids = [5, 6]

        write_store(tmp_path, ids, blobs, model="blob-model", dim=2, index_version="2:6")
        store = load_store(tmp_path, model="blob-model")

        assert store is not None
        np.testing.assert_allclose(store.matrix[0], [1.0, 0.0])
        np.testing.assert_allclose(store.matrix[1], [0.0, 1.0])


# ── VS2: top_k correctness ───────────────────────────────────────────────────


class TestTopK:
    """VS2 — top_k results match the SQL brute-force cosine path."""

    def test_top_k_ordering(self, tmp_path: Path) -> None:
        """top_k returns results sorted by cosine score descending."""
        # Row 0 is perfectly aligned with query → score 1.0
        # Row 1 is orthogonal → score 0.0
        mat = _make_matrix([[1.0, 0.0], [0.0, 1.0]])
        ids = [10, 20]

        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="2:20")
        store = load_store(tmp_path, model="m")
        assert store is not None

        query = _f32([1.0, 0.0])
        results = top_k(store, query, k=2)

        assert len(results) == 2
        assert results[0][0] == 10  # best match
        assert results[0][1] == pytest.approx(1.0, abs=1e-6)
        assert results[1][0] == 20
        assert results[1][1] == pytest.approx(0.0, abs=1e-6)

    def test_top_k_parity_with_sql_brute_force(self, tmp_path: Path) -> None:
        """top_k scores match the SQL brute-force path for the same inputs."""
        # 5 random-ish vectors (3 dims each)
        vecs = [
            [0.6, 0.8, 0.0],
            [0.0, 0.6, 0.8],
            [0.8, 0.0, 0.6],
            [0.3, 0.3, 0.9],
            [0.9, 0.3, 0.3],
        ]
        ids = [1, 2, 3, 4, 5]
        query_vec = [0.7, 0.7, 0.0]

        mat = _make_matrix(vecs)
        write_store(tmp_path, ids, mat, model="m", dim=3, index_version="5:5")
        store = load_store(tmp_path, model="m")
        assert store is not None

        results = top_k(store, _f32(query_vec), k=5)
        result_ids = [r[0] for r in results]
        result_scores = [r[1] for r in results]

        # Reference: brute-force cosine over the same vecs
        ref = sorted(
            [(i + 1, _cosine(v, query_vec)) for i, v in enumerate(vecs)],
            key=lambda t: -t[1],
        )
        ref_ids = [r[0] for r in ref]
        ref_scores = [r[1] for r in ref]

        assert result_ids == ref_ids
        for actual, expected in zip(result_scores, ref_scores):
            assert actual == pytest.approx(expected, abs=1e-5)

    def test_top_k_respects_k_limit(self, tmp_path: Path) -> None:
        """top_k returns at most k results even if more rows exist."""
        mat = _make_matrix([[1.0, 0.0]] * 10)
        ids = list(range(10))

        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="10:9")
        store = load_store(tmp_path, model="m")
        assert store is not None

        results = top_k(store, _f32([1.0, 0.0]), k=3)
        assert len(results) == 3

    def test_top_k_returns_empty_on_zero_norm_query(self, tmp_path: Path) -> None:
        """top_k returns [] when the query vector is all zeros (zero norm)."""
        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]

        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="1:1")
        store = load_store(tmp_path, model="m")
        assert store is not None

        results = top_k(store, _f32([0.0, 0.0]), k=5)
        assert results == []


# ── VS3: Atomic write ────────────────────────────────────────────────────────


class TestAtomicWrite:
    """VS3 — interrupted/failed writes do not leave readable partial artifacts."""

    def test_failed_write_leaves_no_meta(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the write fails after writing the matrix but before writing meta,
        load_store returns None (no meta → no artifact)."""
        import os as _os

        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]

        call_count = [0]
        real_replace = _os.replace

        def _fail_on_meta(src: str, dst: str) -> None:
            call_count[0] += 1
            # Third os.replace is the meta file (matrix=1, ids=2, meta=3)
            if call_count[0] == 3:
                raise OSError("simulated meta-write failure")
            real_replace(src, dst)

        monkeypatch.setattr(_os, "replace", _fail_on_meta)

        # write_store must not raise even on failure
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="1:1")

        # meta was not written → load_store returns None
        result = load_store(tmp_path, model="m")
        assert result is None

    def test_write_is_idempotent(self, tmp_path: Path) -> None:
        """Calling write_store twice succeeds and the second write replaces the first."""
        mat1 = _make_matrix([[1.0, 0.0]])
        mat2 = _make_matrix([[0.0, 1.0]])
        ids = [42]

        write_store(tmp_path, ids, mat1, model="m", dim=2, index_version="1:42")
        write_store(tmp_path, ids, mat2, model="m", dim=2, index_version="1:42")

        store = load_store(tmp_path, model="m")
        assert store is not None
        np.testing.assert_array_equal(store.matrix, mat2)


# ── VS4: None branches ───────────────────────────────────────────────────────


class TestNoneBranches:
    """VS4 — load_store returns None (never raises) on every invalid artifact state."""

    def test_returns_none_when_artifact_absent(self, tmp_path: Path) -> None:
        """load_store returns None when no artifact files exist."""
        result = load_store(tmp_path, model="any-model")
        assert result is None

    def test_returns_none_when_meta_corrupt(self, tmp_path: Path) -> None:
        """load_store returns None when meta JSON is corrupt/unparseable."""
        # Write valid matrix + ids but corrupt meta
        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="1:1")

        # Overwrite meta with garbage
        (tmp_path / "vectors.meta.json").write_text("NOT_JSON{{{", encoding="utf-8")

        result = load_store(tmp_path, model="m")
        assert result is None

    def test_returns_none_on_model_mismatch(self, tmp_path: Path) -> None:
        """load_store returns None when meta model != requested model."""
        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]
        write_store(tmp_path, ids, mat, model="model-A", dim=2, index_version="1:1")

        result = load_store(tmp_path, model="model-B")
        assert result is None

    def test_returns_none_on_truncated_matrix(self, tmp_path: Path) -> None:
        """load_store returns None when the matrix file is truncated."""
        mat = _make_matrix([[1.0, 0.0], [0.0, 1.0]])
        ids = [1, 2]
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="2:2")

        # Truncate the matrix file
        matrix_path = tmp_path / "vectors.f32"
        full = matrix_path.read_bytes()
        matrix_path.write_bytes(full[: len(full) // 2])

        result = load_store(tmp_path, model="m")
        assert result is None

    def test_returns_none_on_truncated_ids(self, tmp_path: Path) -> None:
        """load_store returns None when the ids file is truncated."""
        mat = _make_matrix([[1.0, 0.0], [0.0, 1.0]])
        ids = [1, 2]
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="2:2")

        # Truncate the ids file
        ids_path = tmp_path / "vectors.ids.i64"
        full = ids_path.read_bytes()
        ids_path.write_bytes(full[: len(full) // 2])

        result = load_store(tmp_path, model="m")
        assert result is None

    def test_returns_none_on_dtype_mismatch(self, tmp_path: Path) -> None:
        """load_store returns None when meta specifies a non-float32 dtype."""
        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="1:1")

        # Overwrite meta with wrong dtype
        meta_path = tmp_path / "vectors.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["dtype"] = "float64"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        result = load_store(tmp_path, model="m")
        assert result is None

    def test_returns_none_on_byteorder_mismatch(self, tmp_path: Path) -> None:
        """load_store returns None when meta specifies wrong byteorder."""
        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="1:1")

        # Overwrite meta with wrong byteorder
        meta_path = tmp_path / "vectors.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["byteorder"] = "big"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        result = load_store(tmp_path, model="m")
        assert result is None

    def test_load_store_never_raises_on_missing_meta_field(self, tmp_path: Path) -> None:
        """load_store returns None (never raises) when meta is missing required fields."""
        mat = _make_matrix([[1.0, 0.0]])
        ids = [1]
        write_store(tmp_path, ids, mat, model="m", dim=2, index_version="1:1")

        # Write meta with a missing field (e.g. no 'dim')
        meta_path = tmp_path / "vectors.meta.json"
        meta = {"model": "m", "count": 1, "index_version": "1:1", "dtype": "float32"}
        # 'dim' and 'byteorder' are missing
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        result = load_store(tmp_path, model="m")
        assert result is None


# ── VS5: get_artifact_dir ────────────────────────────────────────────────────


class TestGetArtifactDir:
    """VS5 — get_artifact_dir returns parent of DB file or None for in-memory DBs."""

    def test_returns_parent_of_db_file(self, tmp_path: Path) -> None:
        """File-backed DB → returns the parent directory (the .seam/ dir)."""
        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)

        result = get_artifact_dir(conn)

        assert result is not None
        assert result == tmp_path
        conn.close()

    def test_returns_none_for_in_memory_db(self) -> None:
        """In-memory DB → returns None (no artifact directory)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        result = get_artifact_dir(conn)

        assert result is None
        conn.close()


# ── VS6: compute_index_version ───────────────────────────────────────────────


class TestComputeIndexVersion:
    """VS6 — compute_index_version returns correct token from DB state."""

    def test_returns_count_and_max_id(self, tmp_path: Path) -> None:
        """Returns f'{count}:{max_symbol_id}' for the given model."""
        conn = init_db(tmp_path / "test.db")
        # Insert two embedding rows
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at) "
                "VALUES ('/a.py', 'python', 'x', 1.0, 1.0)"
            )
            fid = conn.execute("SELECT id FROM files WHERE path='/a.py'").fetchone()["id"]
            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
                "VALUES (?, 'fn', 'function', 1, 2)",
                (fid,),
            )
            s1 = conn.execute("SELECT id FROM symbols WHERE name='fn'").fetchone()["id"]
            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
                "VALUES (?, 'fn2', 'function', 3, 4)",
                (fid,),
            )
            s2 = conn.execute("SELECT id FROM symbols WHERE name='fn2'").fetchone()["id"]
            conn.executemany(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, 'mdl', 2, ?)",
                [(s1, _f32([1.0, 0.0])), (s2, _f32([0.0, 1.0]))],
            )

        version = compute_index_version(conn, "mdl")
        max_id = max(s1, s2)
        assert version == f"2:{max_id}"

    def test_returns_zero_zero_when_no_embeddings(self, tmp_path: Path) -> None:
        """Returns '0:0' when no embeddings exist for the model."""
        conn = init_db(tmp_path / "test.db")
        version = compute_index_version(conn, "missing-model")
        assert version == "0:0"

    def test_different_model_not_counted(self, tmp_path: Path) -> None:
        """Counts only rows for the requested model, not other models."""
        conn = init_db(tmp_path / "test.db")
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at) "
                "VALUES ('/a.py', 'python', 'x', 1.0, 1.0)"
            )
            fid = conn.execute("SELECT id FROM files WHERE path='/a.py'").fetchone()["id"]
            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
                "VALUES (?, 'fn', 'function', 1, 2)",
                (fid,),
            )
            s1 = conn.execute("SELECT id FROM symbols WHERE name='fn'").fetchone()["id"]
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, 'other-model', 2, ?)",
                (s1, _f32([1.0, 0.0])),
            )

        version = compute_index_version(conn, "my-model")
        assert version == "0:0"
