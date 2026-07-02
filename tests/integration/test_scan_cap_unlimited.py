"""Integration tests: WS2a Slice 2 — scan-cap unlimited (default 0).

Proves that:
  - cap=0 (unlimited) makes symbols beyond the old cap reachable via both paths.
  - A positive cap still bounds both paths (mmap slice and SQL LIMIT).
  - SEAM_VECTOR_STORE=off + positive cap = correct bounded SQLite scan.

All tests are GATE-SAFE: no fastembed, no model download.
Synthetic float32 vectors inserted directly into the DB via insert_embeddings helper.
embed_query is monkeypatched to return a synthetic query vector.

Test groups:
    SC1 — unlimited cap (0): far rows reachable in both mmap and SQL paths.
    SC2 — positive cap: far rows are NOT reachable (cap truncates).
    SC3 — SEAM_VECTOR_STORE=off + positive cap: correct bounded SQL scan.
    SC4 — top_k scan_cap: vector_store.top_k scan_cap kwarg works independently.
"""

import struct
from pathlib import Path
from unittest.mock import patch

import numpy as np

import seam.config as config
from seam.indexer.db import init_db
from seam.query.semantic import semantic_candidates
from seam.query.vector_store import (
    VectorStore,
    compute_index_version,
    top_k,
    write_store,
)

# ── Test constants ────────────────────────────────────────────────────────────

MODEL = "test-model"
DIM = 4  # tiny dimensionality — fast, no memory concern


# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack floats as float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _unit_vec(index: int, dim: int) -> list[float]:
    """One-hot unit vector with 1.0 at position (index % dim)."""
    v = [0.0] * dim
    v[index % dim] = 1.0
    return v


def _seed_embeddings(conn, num_symbols: int, dim: int = DIM) -> list[int]:
    """Insert symbols + embeddings into an in-memory DB.

    Each symbol gets a distinct one-hot vector so the query can pinpoint any
    specific symbol by targeting its axis. Returns symbol_ids in insertion order.
    """
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
    fid = conn.execute("SELECT id FROM files WHERE path='/proj/a.py'").fetchone()["id"]

    sym_ids: list[int] = []
    blobs: list[bytes] = []
    for i in range(num_symbols):
        with conn:
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                " VALUES (?, ?, 'function', ?, ?)",
                (fid, f"fn_{i}", i * 2 + 1, i * 2 + 2),
            )
            sid = cur.lastrowid

        vec = _unit_vec(i, dim)
        blob = _f32(vec)
        sym_ids.append(sid)
        blobs.append(blob)

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
            " VALUES (?, ?, ?, ?)",
            [(sid, MODEL, dim, blob) for sid, blob in zip(sym_ids, blobs)],
        )

    return sym_ids


def _write_artifact(store_dir: Path, conn, sym_ids: list[int], dim: int = DIM) -> None:
    """Write a vector store artifact for the seeded embeddings."""
    blobs = []
    for sid in sym_ids:
        row = conn.execute(
            "SELECT vector FROM embeddings WHERE symbol_id = ? AND model = ?",
            (sid, MODEL),
        ).fetchone()
        assert row is not None, f"No embedding found for symbol_id={sid}"
        blobs.append(bytes(row[0]))

    index_version = compute_index_version(conn, MODEL)
    write_store(store_dir, sym_ids, blobs, MODEL, dim, index_version)


# ── SC1: Unlimited cap (0) — far rows reachable ───────────────────────────────


class TestUnlimitedCap:
    """SC1 — cap=0 (unlimited) makes rows beyond old cap reachable."""

    # We use a tiny cap=3 as a stand-in for the old 20000 limit.
    # The actual row count is cap+2, so the "far" row is at index cap+1.
    TINY_CAP = 3

    def _far_query_vec(self) -> bytes:
        """Query vector pointing at the LAST axis of the (TINY_CAP+1)th symbol.

        Symbol at index TINY_CAP+1 has its 1.0 at position (TINY_CAP+1) % DIM.
        """
        # TINY_CAP = 3, DIM = 4 → symbol index 4, axis 4%4 = 0
        # But axis 0 overlaps symbol 0. Use TINY_CAP=3 so the far sym is index 4
        # and its one-hot is at position 4 % DIM = 0. That overlaps index 0.
        # Instead: use DIM large enough so the far sym has a unique axis.
        # Here DIM=4 and TINY_CAP=3 → far sym index = 4, axis = 4%4 = 0 (clash).
        # So we'll query for index 3 (the first BEYOND a cap of 3) at axis 3%4=3.
        v = [0.0] * DIM
        v[self.TINY_CAP % DIM] = 1.0
        return _f32(v)

    def test_sql_unlimited_reaches_far_row(self, tmp_path: Path) -> None:
        """SQL path with cap=0 fetches all rows — symbol at index TINY_CAP is found."""
        conn = init_db(tmp_path / "seam.db")

        num = self.TINY_CAP + 1  # one symbol BEYOND the stand-in cap
        sym_ids = _seed_embeddings(conn, num)
        far_id = sym_ids[self.TINY_CAP]  # would be invisible under cap=TINY_CAP

        query_bytes = self._far_query_vec()

        # patch: embed_query returns our synthetic query vector
        # patch: SEAM_VECTOR_STORE=off so only SQL path runs
        # patch: SEAM_SEMANTIC_SCAN_CAP=0 (unlimited)
        with (
            patch("seam.query.semantic.embed_query", return_value=query_bytes),
            patch("seam.query.semantic.is_available", return_value=True),
            patch.object(config, "SEAM_VECTOR_STORE", "off"),
            patch.object(config, "SEAM_SEMANTIC_SCAN_CAP", 0),
        ):
            results = semantic_candidates(conn, "anything", model=MODEL, limit=10)

        result_ids = [r[0] for r in results]
        assert far_id in result_ids, (
            f"far_id={far_id} should be reachable with unlimited cap; got {result_ids}"
        )

    def test_sql_positive_cap_hides_far_row(self, tmp_path: Path) -> None:
        """SQL path with a positive cap EXCLUDES the far row (cap truncates)."""
        conn = init_db(tmp_path / "seam.db")

        num = self.TINY_CAP + 1
        sym_ids = _seed_embeddings(conn, num)
        far_id = sym_ids[self.TINY_CAP]

        query_bytes = self._far_query_vec()

        with (
            patch("seam.query.semantic.embed_query", return_value=query_bytes),
            patch("seam.query.semantic.is_available", return_value=True),
            patch.object(config, "SEAM_VECTOR_STORE", "off"),
            patch.object(config, "SEAM_SEMANTIC_SCAN_CAP", self.TINY_CAP),
        ):
            results = semantic_candidates(conn, "anything", model=MODEL, limit=10)

        result_ids = [r[0] for r in results]
        assert far_id not in result_ids, (
            f"far_id={far_id} should be HIDDEN by cap={self.TINY_CAP}; got {result_ids}"
        )

    def test_mmap_unlimited_reaches_far_row(self, tmp_path: Path) -> None:
        """mmap path with cap=0 considers all artifact rows — far row found."""
        conn = init_db(tmp_path / "seam.db")

        num = self.TINY_CAP + 1
        sym_ids = _seed_embeddings(conn, num)
        far_id = sym_ids[self.TINY_CAP]

        _write_artifact(tmp_path, conn, sym_ids)
        query_bytes = self._far_query_vec()

        with (
            patch("seam.query.semantic.embed_query", return_value=query_bytes),
            patch("seam.query.semantic.is_available", return_value=True),
            patch.object(config, "SEAM_VECTOR_STORE", "on"),
            patch.object(config, "SEAM_SEMANTIC_SCAN_CAP", 0),
        ):
            results = semantic_candidates(conn, "anything", model=MODEL, limit=10)

        result_ids = [r[0] for r in results]
        assert far_id in result_ids, (
            f"far_id={far_id} should be reachable via mmap with cap=0; got {result_ids}"
        )

    def test_mmap_positive_cap_hides_far_row(self, tmp_path: Path) -> None:
        """mmap path with a positive cap slices the matrix — far row is NOT found."""
        conn = init_db(tmp_path / "seam.db")

        num = self.TINY_CAP + 1
        sym_ids = _seed_embeddings(conn, num)
        far_id = sym_ids[self.TINY_CAP]

        _write_artifact(tmp_path, conn, sym_ids)
        query_bytes = self._far_query_vec()

        with (
            patch("seam.query.semantic.embed_query", return_value=query_bytes),
            patch("seam.query.semantic.is_available", return_value=True),
            patch.object(config, "SEAM_VECTOR_STORE", "on"),
            patch.object(config, "SEAM_SEMANTIC_SCAN_CAP", self.TINY_CAP),
        ):
            results = semantic_candidates(conn, "anything", model=MODEL, limit=10)

        result_ids = [r[0] for r in results]
        assert far_id not in result_ids, (
            f"far_id={far_id} should be HIDDEN by mmap cap={self.TINY_CAP}; got {result_ids}"
        )


# ── SC2: SEAM_VECTOR_STORE=off + positive cap = bounded SQL ───────────────────


class TestVectorStoreOffPositiveCap:
    """SC3 — SEAM_VECTOR_STORE=off + positive cap: correct bounded SQL scan."""

    def test_store_off_cap_bounds_sql(self, tmp_path: Path) -> None:
        """With VECTOR_STORE=off and a cap, SQL LIMIT is respected."""
        cap = 3
        num = cap + 2  # 5 symbols total

        conn = init_db(tmp_path / "seam.db")

        sym_ids = _seed_embeddings(conn, num)
        # The far symbol (index cap) is beyond the cap — it must NOT appear.
        far_id = sym_ids[cap]

        # Query for the far symbol's axis
        v = [0.0] * DIM
        v[cap % DIM] = 1.0
        query_bytes = _f32(v)

        with (
            patch("seam.query.semantic.embed_query", return_value=query_bytes),
            patch("seam.query.semantic.is_available", return_value=True),
            patch.object(config, "SEAM_VECTOR_STORE", "off"),
            patch.object(config, "SEAM_SEMANTIC_SCAN_CAP", cap),
        ):
            results = semantic_candidates(conn, "anything", model=MODEL, limit=10)

        result_ids = [r[0] for r in results]
        # With LIMIT=cap, only the first cap rows (by rowid) are loaded.
        assert len(result_ids) <= cap, (
            f"SQL with cap={cap} should return at most {cap} results; got {len(result_ids)}"
        )
        # Far row is beyond the cap, so it must not appear
        assert far_id not in result_ids, (
            f"far_id={far_id} should be EXCLUDED by cap={cap}; got {result_ids}"
        )


# ── SC4: top_k scan_cap kwarg ─────────────────────────────────────────────────


class TestTopKScanCap:
    """SC4 — vector_store.top_k scan_cap kwarg works independently of global config."""

    def _build_store(self, num_rows: int, dim: int = DIM) -> VectorStore:
        """Build an in-memory VectorStore with num_rows one-hot vectors."""
        sym_ids = list(range(num_rows))
        mat = np.zeros((num_rows, dim), dtype=np.float32)
        for i in range(num_rows):
            mat[i, i % dim] = 1.0

        # Build a VectorStore directly (without disk write) for unit testing
        return VectorStore(
            matrix=mat,
            symbol_ids=np.array(sym_ids, dtype=np.int64),
            model=MODEL,
            dim=dim,
            nrows=num_rows,
            index_version="test",
        )

    def test_scan_cap_zero_considers_all_rows(self) -> None:
        """scan_cap=0 (unlimited) considers all rows in the store."""
        num = 8
        store = self._build_store(num)

        # Query for the last symbol's axis
        v = _unit_vec(num - 1, DIM)
        q_bytes = _f32(v)

        result = top_k(store, q_bytes, k=10, scan_cap=0)
        result_ids = [r[0] for r in result]
        assert num - 1 in result_ids, (
            f"Symbol {num - 1} should be reachable with scan_cap=0; got {result_ids}"
        )

    def test_scan_cap_positive_slices_matrix(self) -> None:
        """scan_cap=N slices the matrix to the first N rows."""
        num = 8
        cap = 4
        store = self._build_store(num)

        # Query for a symbol BEYOND the cap
        far_idx = cap  # index 4 is the first beyond a cap of 4
        v = _unit_vec(far_idx, DIM)
        q_bytes = _f32(v)

        result = top_k(store, q_bytes, k=10, scan_cap=cap)
        result_ids = [r[0] for r in result]
        assert far_idx not in result_ids, (
            f"Symbol {far_idx} should be HIDDEN by scan_cap={cap}; got {result_ids}"
        )
        # Only cap rows were scanned
        assert len(result_ids) <= cap

    def test_scan_cap_larger_than_store_uses_all_rows(self) -> None:
        """scan_cap larger than store.nrows safely uses all rows (no IndexError)."""
        num = 3
        store = self._build_store(num)

        v = _unit_vec(num - 1, DIM)
        q_bytes = _f32(v)

        # cap > num is safe — numpy slice [:10] on a 3-row array returns all 3 rows
        result = top_k(store, q_bytes, k=10, scan_cap=10)
        result_ids = [r[0] for r in result]
        assert num - 1 in result_ids, (
            f"All {num} rows should be considered when scan_cap > nrows; got {result_ids}"
        )
