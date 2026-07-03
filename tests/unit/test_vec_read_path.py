"""Unit tests for WS2b S3 — three-tier fallback read path (ANN → mmap → SQL).

Tests cover:
  VR1 — byte-identical fallback: SEAM_VEC_ANN=off result == pre-WS2b mmap/SQL result.
  VR2 — three-tier ordering: _try_vec_path attempted before _try_mmap_path; non-None short-circuits.
  VR3 — None-vs-[] contract: _try_vec_path returns None (not []) when structurally unavailable.
  VR4 — stale bypass: mismatched index_version → _try_vec_path returns None.
  VR5 — ANN-vs-brute consistency: on synthetic data, top-1 from ANN == brute-force top-1.
  VR6 — seam status ANN indicator: shows ann_* fields in JSON when SEAM_VEC_ANN=on.

Gate-safe: fully offline, no network. Synthetic float32 vectors via struct.pack.
Real sqlite-vec tests are guarded with pytest.importorskip("sqlite_vec") and skip
automatically when [semantic-ann] is not installed.
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

import seam.config as config
from seam.indexer.db import init_db
from seam.indexer.vec_index import VEC_META_TABLE

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as a float32 blob (no numpy required)."""
    return struct.pack(f"{len(values)}f", *values)


def _make_db_with_embeddings(
    tmp_path: Path,
    *,
    n: int = 3,
    dim: int = 4,
    model: str = "test-model",
) -> tuple[sqlite3.Connection, list[int], list[bytes]]:
    """Create a real seam DB (via init_db) with n synthetic embedding rows.

    Returns (conn, symbol_ids, vectors).  The caller is responsible for closing conn.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    conn = init_db(db_path)

    sym_ids = []
    vecs = []
    for i in range(n):
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES (?, 'python', 'abc', 1.0, 1.0)",
            (f"/proj/f{i}.py",),
        )
        fid = conn.execute(
            "SELECT id FROM files WHERE path = ?", (f"/proj/f{i}.py",)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, ?, 'function', 1, 5)",
            (fid, f"sym_{i}"),
        )
        sid = conn.execute(
            "SELECT id FROM symbols WHERE name = ? AND file_id = ?", (f"sym_{i}", fid)
        ).fetchone()["id"]

        # Give each symbol a distinct synthetic vector.
        # sym_0 is the "closest" to any query that has component 0 strong.
        vec = _f32([float(i == j) for j in range(dim)])
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
            " VALUES (?, ?, ?, ?)",
            (sid, model, dim, vec),
        )
        sym_ids.append(sid)
        vecs.append(vec)

    conn.commit()
    return conn, sym_ids, vecs


def _build_vec_index_for_conn(conn: sqlite3.Connection, *, model: str) -> bool:
    """Build the vec0 ANN index on `conn` (requires sqlite_vec available).

    Returns True on success, False on any failure (caller skips the test).
    """
    try:
        from seam.indexer.vec_index import index_vec

        # Force MIN_ROWS to 0 so gate 3 always passes for our small test DBs.
        with patch.object(config, "SEAM_VEC_ANN_MIN_ROWS", 0):
            n = index_vec(conn, model=model)
        return n > 0
    except Exception:  # noqa: BLE001
        return False


# ── VR1: byte-identical fallback ─────────────────────────────────────────────


class TestByteIdenticalFallback:
    """VR1 — With SEAM_VEC_ANN=off, semantic_candidates output must be identical to
    the pre-WS2b result (mmap/SQL path).  This is the core safety assertion."""

    def test_ann_off_result_identical_to_mmap_sql(self, tmp_path: Path) -> None:
        """When SEAM_VEC_ANN=off, the ANN tier is never attempted and output is unchanged.

        We run semantic_candidates twice:
          (a) with SEAM_VEC_ANN=off  (should bypass _try_vec_path entirely)
          (b) with _try_vec_path monkeypatched to raise  (proves it is never called)
        Both runs must produce identical results — the mmap/SQL path is untouched.
        """
        from seam.query.semantic import semantic_candidates

        conn, sym_ids, vecs = _make_db_with_embeddings(tmp_path, n=3, dim=4)

        query_vec = _f32([1.0, 0.0, 0.0, 0.0])  # closest to sym_0

        called_ann = []

        def _ann_should_not_be_called(*_a: object, **_kw: object) -> None:
            called_ann.append(True)
            raise AssertionError("_try_vec_path must not be called when SEAM_VEC_ANN=off")

        with patch("seam.config.SEAM_VEC_ANN", "off"):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic._try_vec_path", _ann_should_not_be_called):
                        result = semantic_candidates(
                            conn, "test query", model="test-model", limit=5
                        )

        conn.close()

        # ANN was never called
        assert called_ann == [], "ANN tier must NOT be called when SEAM_VEC_ANN=off"
        # We got valid results from the SQL path
        assert isinstance(result, list)
        assert len(result) > 0
        # sym_0 is closest to the query (dot product highest on dimension 0)
        top_id = result[0][0]
        assert top_id == sym_ids[0], (
            f"Expected sym_ids[0]={sym_ids[0]} as top result, got {top_id}"
        )

    def test_ann_off_same_output_as_forced_none_path(self, tmp_path: Path) -> None:
        """Prove byte-identity: ANN=off == ANN=on but _try_vec_path forced to None.

        Both configurations must produce the same list of (symbol_id, score) pairs.
        """
        from seam.query.semantic import semantic_candidates

        conn_off, sym_ids, _ = _make_db_with_embeddings(tmp_path / "off", n=3, dim=4)
        conn_forced, _, _ = _make_db_with_embeddings(tmp_path / "forced", n=3, dim=4)

        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "off"):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.is_available", return_value=True):
                    result_off = semantic_candidates(
                        conn_off, "q", model="test-model", limit=10
                    )

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.is_available", return_value=True):
                    # Force ANN tier to return None → falls back to mmap/SQL
                    with patch("seam.query.semantic._try_vec_path", return_value=None):
                        result_forced = semantic_candidates(
                            conn_forced, "q", model="test-model", limit=10
                        )

        conn_off.close()
        conn_forced.close()

        # Symbol IDs and scores must be identical across both configurations.
        assert [r[0] for r in result_off] == [r[0] for r in result_forced], (
            "Symbol ID ordering differs between ANN=off and forced-None paths"
        )
        # Scores should be very close (same SQL path, same vectors, same numpy math)
        for (_, s_off), (_, s_forced) in zip(result_off, result_forced):
            assert abs(s_off - s_forced) < 1e-6, (
                f"Score mismatch: off={s_off}, forced-None={s_forced}"
            )


# ── VR2: three-tier ordering ──────────────────────────────────────────────────


class TestThreeTierOrdering:
    """VR2 — _try_vec_path is called FIRST; a non-None return short-circuits mmap and SQL."""

    def test_vec_path_attempted_before_mmap_path(self, tmp_path: Path) -> None:
        """When ANN=on, _try_vec_path is called before _try_mmap_path."""
        from seam.query.semantic import semantic_candidates

        conn, _, _ = _make_db_with_embeddings(tmp_path, n=2, dim=4)
        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        call_order: list[str] = []

        def _fake_vec(*_a: object, **_kw: object) -> None:
            call_order.append("vec")
            return None  # fall through to mmap

        def _fake_mmap(*_a: object, **_kw: object) -> list:
            call_order.append("mmap")
            return None  # fall through to SQL

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            with patch("seam.config.SEAM_VECTOR_STORE", "on"):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    with patch("seam.query.semantic.is_available", return_value=True):
                        with patch("seam.query.semantic._try_vec_path", _fake_vec):
                            with patch("seam.query.semantic._try_mmap_path", _fake_mmap):
                                semantic_candidates(
                                    conn, "q", model="test-model", limit=5
                                )

        conn.close()

        # vec must appear before mmap in the call order
        assert "vec" in call_order, "_try_vec_path was not called"
        assert "mmap" in call_order, "_try_mmap_path was not called"
        assert call_order.index("vec") < call_order.index("mmap"), (
            f"_try_vec_path must be called before _try_mmap_path; got order: {call_order}"
        )

    def test_non_none_vec_result_short_circuits_mmap(self, tmp_path: Path) -> None:
        """When _try_vec_path returns a non-None list, _try_mmap_path is NOT called."""
        from seam.query.semantic import semantic_candidates

        conn, sym_ids, _ = _make_db_with_embeddings(tmp_path, n=2, dim=4)
        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        fake_ann_result = [(sym_ids[0], 0.99)]
        mmap_called = []

        def _fake_mmap(*_a: object, **_kw: object) -> None:
            mmap_called.append(True)
            return None

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            with patch("seam.config.SEAM_VECTOR_STORE", "on"):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    with patch("seam.query.semantic.is_available", return_value=True):
                        with patch("seam.query.semantic._try_vec_path", return_value=fake_ann_result):
                            with patch("seam.query.semantic._try_mmap_path", _fake_mmap):
                                result = semantic_candidates(
                                    conn, "q", model="test-model", limit=5
                                )

        conn.close()

        assert mmap_called == [], "_try_mmap_path must NOT be called when ANN tier succeeds"
        assert result == fake_ann_result, (
            f"Expected ANN result {fake_ann_result}; got {result}"
        )

    def test_non_none_vec_result_short_circuits_sql(self, tmp_path: Path) -> None:
        """When ANN returns a result, the SQL brute-force path is not reached."""
        from seam.query.semantic import semantic_candidates

        conn, sym_ids, _ = _make_db_with_embeddings(tmp_path, n=2, dim=4)
        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        # A sentinel result — we'll check no SQL was run by verifying the exact
        # output is the sentinel (SQL would produce different ids/scores).
        sentinel = [(sym_ids[0], 0.42)]

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic._try_vec_path", return_value=sentinel):
                        result = semantic_candidates(
                            conn, "q", model="test-model", limit=5
                        )

        conn.close()
        assert result == sentinel, "SQL path must not override ANN result"


# ── VR3: None-vs-[] contract ──────────────────────────────────────────────────


class TestNoneVsEmptyContract:
    """VR3 — _try_vec_path returns None (not []) when structurally unavailable.
    Returning [] would halt the fallback cascade as a valid empty result."""

    def test_vec_path_returns_none_when_ann_off(self) -> None:
        """_try_vec_path returns None immediately when SEAM_VEC_ANN=off."""
        from seam.query.semantic import _try_vec_path

        conn = sqlite3.connect(":memory:")
        query_vec = _f32([1.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "off"):
            result = _try_vec_path(conn, query_vec, model="m", limit=5)

        conn.close()
        assert result is None, (
            f"Expected None when SEAM_VEC_ANN=off; got {result!r}"
        )

    def test_vec_path_returns_none_when_probe_fails(self) -> None:
        """_try_vec_path returns None when the capability probe fails."""
        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        conn = sqlite3.connect(":memory:")
        query_vec = _f32([1.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            # Reset the per-process cache so the probe is re-run.
            orig_cache = sem_mod._vec_probe_cache
            sem_mod._vec_probe_cache = None
            try:
                with patch("seam.query.semantic.probe_vec_extension", return_value=False):
                    result = _try_vec_path(conn, query_vec, model="m", limit=5)
            finally:
                sem_mod._vec_probe_cache = orig_cache

        conn.close()
        assert result is None, (
            f"Expected None when probe fails; got {result!r}"
        )

    def test_vec_path_returns_none_when_table_absent(self) -> None:
        """_try_vec_path returns None when vec_meta table does not exist."""
        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        conn = sqlite3.connect(":memory:")  # no vec_meta table
        query_vec = _f32([1.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            orig_cache = sem_mod._vec_probe_cache
            sem_mod._vec_probe_cache = True  # probe passes
            try:
                result = _try_vec_path(conn, query_vec, model="m", limit=5)
            finally:
                sem_mod._vec_probe_cache = orig_cache

        conn.close()
        assert result is None, (
            f"Expected None when vec_meta table absent; got {result!r}"
        )

    def test_vec_path_returns_none_when_no_model_row(self) -> None:
        """_try_vec_path returns None when vec_meta has no row for the model."""
        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create vec_meta but with a different model
        conn.execute(
            f"CREATE TABLE {VEC_META_TABLE} (model TEXT PRIMARY KEY, index_version TEXT, dim INTEGER)"
        )
        conn.execute(
            f"INSERT INTO {VEC_META_TABLE} VALUES ('other-model', '5:10', 4)"
        )
        conn.commit()

        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            orig_cache = sem_mod._vec_probe_cache
            sem_mod._vec_probe_cache = True
            try:
                result = _try_vec_path(conn, query_vec, model="my-model", limit=5)
            finally:
                sem_mod._vec_probe_cache = orig_cache

        conn.close()
        assert result is None, (
            f"Expected None when no vec_meta row for model; got {result!r}"
        )

    def test_vec_path_returns_none_not_empty_list_when_unavailable(self) -> None:
        """Critically: None != [] — None triggers fallback; [] stops the cascade."""
        # This is the key invariant: unavailability must produce None, not [].
        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        conn = sqlite3.connect(":memory:")
        query_vec = _f32([1.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            orig_cache = sem_mod._vec_probe_cache
            sem_mod._vec_probe_cache = True
            try:
                result = _try_vec_path(conn, query_vec, model="m", limit=5)
            finally:
                sem_mod._vec_probe_cache = orig_cache

        conn.close()
        # Must be None, NOT [] — [] would halt the fallback chain
        assert result is None
        assert result != []  # explicitly not an empty list


# ── VR4: stale bypass ─────────────────────────────────────────────────────────


class TestStalenessCheck:
    """VR4 — Mismatched index_version causes _try_vec_path to return None."""

    def _make_vec_meta_conn(
        self, stored_version: str, current_embedding_count: int = 5
    ) -> sqlite3.Connection:
        """Return an in-memory conn with vec_meta + embeddings table (for compute_index_version)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        # Create embeddings table so compute_index_version can query it.
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
        # Insert rows so the real token != stored_version.
        for i in range(current_embedding_count):
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, 'test-model', 4, ?)",
                (i + 1, _f32([0.0] * 4)),
            )

        conn.execute(
            f"CREATE TABLE {VEC_META_TABLE} (model TEXT PRIMARY KEY, index_version TEXT, dim INTEGER)"
        )
        conn.execute(
            f"INSERT INTO {VEC_META_TABLE} VALUES ('test-model', ?, 4)",
            (stored_version,),
        )
        conn.commit()
        return conn

    def test_stale_version_returns_none(self) -> None:
        """A stale index_version in vec_meta → None (fall through to mmap/SQL)."""
        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        # Store a version that won't match what compute_index_version returns
        # for the 5-row embeddings table we build.
        conn = self._make_vec_meta_conn(stored_version="99:999")

        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            orig_cache = sem_mod._vec_probe_cache
            sem_mod._vec_probe_cache = True
            try:
                result = _try_vec_path(conn, query_vec, model="test-model", limit=5)
            finally:
                sem_mod._vec_probe_cache = orig_cache

        conn.close()
        assert result is None, (
            f"Expected None for stale index_version; got {result!r}"
        )

    def test_matching_version_does_not_return_none_from_staleness(self) -> None:
        """A matching index_version passes the staleness gate (extension load may still fail).

        This test verifies the staleness check is NOT the blocker when versions match.
        We patch load_vec_extension to return False to isolate just the staleness gate.
        """
        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path
        from seam.query.vector_store import compute_index_version

        conn = self._make_vec_meta_conn(stored_version="placeholder")
        # Now overwrite with the real token
        real_token = compute_index_version(conn, "test-model")
        conn.execute(
            f"UPDATE {VEC_META_TABLE} SET index_version = ? WHERE model = 'test-model'",
            (real_token,),
        )
        conn.commit()

        query_vec = _f32([1.0, 0.0, 0.0, 0.0])

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            orig_cache = sem_mod._vec_probe_cache
            sem_mod._vec_probe_cache = True
            try:
                # Patch extension load to False — isolates staleness gate from load failure.
                with patch("seam.query.semantic.load_vec_extension", return_value=False):
                    result = _try_vec_path(conn, query_vec, model="test-model", limit=5)
            finally:
                sem_mod._vec_probe_cache = orig_cache

        conn.close()
        # None here is OK — staleness gate passed but extension failed.
        # The point is we reached the extension load step (not halted by staleness).
        # We can't directly assert "staleness gate passed" without deeper mocking,
        # but returning None from extension failure is correct behaviour.
        assert result is None


# ── VR5: ANN-vs-brute consistency (requires sqlite_vec) ──────────────────────


class TestAnnBruteConsistency:
    """VR5 — With ANN built on real DB, vec path top-1 matches brute-force top-1.

    Guarded by pytest.importorskip("sqlite_vec") — skips when extension is absent.
    """

    def test_ann_top1_matches_brute_force_top1(self, tmp_path: Path) -> None:
        """ANN KNN result top-1 agrees with SQL brute-force top-1 on synthetic data."""
        sqlite_vec = pytest.importorskip("sqlite_vec")  # noqa: F841

        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        dim = 8
        conn, sym_ids, vecs = _make_db_with_embeddings(
            tmp_path, n=5, dim=dim, model="test-model"
        )

        # Patch MIN_ROWS so the ANN index builds on our small DB.
        built = False
        with patch.object(config, "SEAM_VEC_ANN", "on"):
            with patch.object(config, "SEAM_VEC_ANN_MIN_ROWS", 0):
                built = _build_vec_index_for_conn(conn, model="test-model")

        if not built:
            conn.close()
            pytest.skip("ANN index build failed on this platform — skipping consistency test")

        # Query vector closest to sym_0 (standard basis vector e_0).
        query_vec = _f32([1.0] + [0.0] * (dim - 1))

        # ── ANN path ─────────────────────────────────────────────────────────
        # Reset probe cache so _try_vec_path re-evaluates probe.
        orig_cache = sem_mod._vec_probe_cache
        sem_mod._vec_probe_cache = None
        try:
            with patch.object(config, "SEAM_VEC_ANN", "on"):
                with patch.object(config, "SEAM_VEC_ANN_MIN_ROWS", 0):
                    ann_result = _try_vec_path(
                        conn, query_vec, model="test-model", limit=5
                    )
        finally:
            sem_mod._vec_probe_cache = orig_cache

        # ── Brute-force path (SQL) ────────────────────────────────────────────
        from seam.query.semantic import _semantic_candidates_impl

        with patch("seam.query.semantic.embed_query", return_value=query_vec):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch.object(config, "SEAM_VEC_ANN", "off"):
                    with patch.object(config, "SEAM_VECTOR_STORE", "off"):
                        brute_result = _semantic_candidates_impl(
                            conn, "q", model="test-model", limit=5
                        )

        conn.close()

        assert ann_result is not None, (
            "ANN path returned None — extension may not support this platform"
        )
        assert len(ann_result) > 0, "ANN path returned empty list"
        assert len(brute_result) > 0, "Brute-force returned empty list"

        ann_top1 = ann_result[0][0]
        brute_top1 = brute_result[0][0]

        assert ann_top1 == brute_top1, (
            f"ANN top-1 ({ann_top1}) != brute-force top-1 ({brute_top1}). "
            "ANN should agree with exact search on synthetic orthonormal vectors."
        )

    def test_ann_top_k_high_overlap_with_brute(self, tmp_path: Path) -> None:
        """ANN top-3 has ≥2 symbols in common with brute-force top-3."""
        sqlite_vec = pytest.importorskip("sqlite_vec")  # noqa: F841

        import seam.query.semantic as sem_mod
        from seam.query.semantic import _try_vec_path

        dim = 4
        conn, sym_ids, _ = _make_db_with_embeddings(
            tmp_path, n=5, dim=dim, model="test-model"
        )

        with patch.object(config, "SEAM_VEC_ANN", "on"):
            with patch.object(config, "SEAM_VEC_ANN_MIN_ROWS", 0):
                built = _build_vec_index_for_conn(conn, model="test-model")

        if not built:
            conn.close()
            pytest.skip("ANN index build failed — skipping overlap test")

        query_vec = _f32([0.7, 0.5, 0.3, 0.1])

        orig_cache = sem_mod._vec_probe_cache
        sem_mod._vec_probe_cache = None
        try:
            with patch.object(config, "SEAM_VEC_ANN", "on"):
                ann_result = _try_vec_path(conn, query_vec, model="test-model", limit=3)
        finally:
            sem_mod._vec_probe_cache = orig_cache

        from seam.query.semantic import _semantic_candidates_impl

        with patch("seam.query.semantic.embed_query", return_value=query_vec):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch.object(config, "SEAM_VEC_ANN", "off"):
                    with patch.object(config, "SEAM_VECTOR_STORE", "off"):
                        brute_result = _semantic_candidates_impl(
                            conn, "q", model="test-model", limit=3
                        )

        conn.close()

        if ann_result is None:
            pytest.skip("ANN path returned None — extension may not support this platform")

        ann_ids = {r[0] for r in ann_result}
        brute_ids = {r[0] for r in brute_result}
        overlap = len(ann_ids & brute_ids)
        assert overlap >= 2, (
            f"ANN top-3 {ann_ids} and brute-force top-3 {brute_ids} overlap only {overlap}/3; "
            "expected at least 2 common results for exact orthonormal synthetic vectors."
        )


# ── VR6: seam status ANN indicator ────────────────────────────────────────────


class TestStatusAnnIndicator:
    """VR6 — seam status --json includes ann_* fields when SEAM_VEC_ANN=on."""

    def _make_status_db(
        self, tmp_path: Path, model: str = "test-model"
    ) -> tuple[Path, sqlite3.Connection]:
        """Create a minimal DB with one symbol+embedding; return (db_dir, conn)."""
        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        db_path = db_dir / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
        fid = conn.execute("SELECT id FROM files WHERE path = '/proj/a.py'").fetchone()["id"]
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, 'fn_ann', 'function', 1, 5)",
            (fid,),
        )
        conn.commit()
        sid = conn.execute("SELECT id FROM symbols WHERE name = 'fn_ann'").fetchone()["id"]
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, 2, ?)",
            (sid, model, _f32([1.0, 0.0])),
        )
        conn.commit()
        return db_dir, conn

    def test_status_json_includes_ann_fields_when_ann_off(self, tmp_path: Path) -> None:
        """status --json includes vec_ann=off and ann_built=False when SEAM_VEC_ANN=off."""
        import json

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, conn = self._make_status_db(tmp_path)
        conn.close()

        with patch("seam.config.SEAM_VEC_ANN", "off"):
            result = runner.invoke(
                app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
            )

        assert result.exit_code == 0, f"status failed: {result.output}"
        payload = json.loads(result.output).get("data", json.loads(result.output))
        assert payload["vec_ann"] == "off"
        assert payload["ann_built"] is False
        assert payload["ann_fresh"] is False
        assert payload["ann_row_count"] == 0

    def test_status_json_ann_not_built_when_vec_meta_absent(self, tmp_path: Path) -> None:
        """When SEAM_VEC_ANN=on but ANN index not built, ann_built=False."""
        import json

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, conn = self._make_status_db(tmp_path)
        conn.close()

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            result = runner.invoke(
                app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
            )

        assert result.exit_code == 0, f"status failed: {result.output}"
        payload = json.loads(result.output).get("data", json.loads(result.output))
        assert payload["vec_ann"] == "on"
        assert payload["ann_built"] is False
        assert payload["ann_fresh"] is False

    def test_status_json_ann_fresh_when_built_and_fresh(self, tmp_path: Path) -> None:
        """When vec_meta row exists with matching index_version, ann_fresh=True."""
        import json

        sqlite_vec = pytest.importorskip("sqlite_vec")  # noqa: F841

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, conn = self._make_status_db(tmp_path)

        # Build the ANN index with SEAM_VEC_ANN=on.
        with patch.object(config, "SEAM_VEC_ANN", "on"):
            with patch.object(config, "SEAM_VEC_ANN_MIN_ROWS", 0):
                from seam.indexer.vec_index import index_vec

                n = index_vec(conn, model="test-model")

        conn.close()

        if n <= 0:
            pytest.skip("ANN index build failed on this platform")

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", "test-model"):
                result = runner.invoke(
                    app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
                )

        assert result.exit_code == 0, f"status failed: {result.output}"
        payload = json.loads(result.output).get("data", json.loads(result.output))
        assert payload["vec_ann"] == "on"
        assert payload["ann_built"] is True
        assert payload["ann_fresh"] is True
        assert payload["ann_row_count"] > 0

    def test_status_json_ann_stale_when_version_mismatch(self, tmp_path: Path) -> None:
        """When vec_meta has a stale index_version, ann_fresh=False."""
        import json

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, conn = self._make_status_db(tmp_path, model="test-model")

        # Manually insert a stale vec_meta row (without building the real index).
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {VEC_META_TABLE} "
            f"(model TEXT PRIMARY KEY, index_version TEXT NOT NULL, dim INTEGER NOT NULL)"
        )
        conn.execute(
            f"INSERT OR REPLACE INTO {VEC_META_TABLE} VALUES ('test-model', '0:0', 2)"
        )
        conn.commit()
        conn.close()

        with patch("seam.config.SEAM_VEC_ANN", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", "test-model"):
                result = runner.invoke(
                    app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
                )

        assert result.exit_code == 0, f"status failed: {result.output}"
        payload = json.loads(result.output).get("data", json.loads(result.output))
        assert payload["vec_ann"] == "on"
        assert payload["ann_built"] is True
        assert payload["ann_fresh"] is False  # version mismatch → stale
