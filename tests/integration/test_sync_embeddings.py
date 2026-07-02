"""Integration tests for WS3 Slice 2 — sync_embeddings incremental orchestrator (#211).

TDD: each test covers one observable acceptance criterion from the issue.

All tests are GATE-SAFE:
  - No fastembed, no model download.
  - Synthetic float32 vectors via struct.pack.
  - embed_texts monkeypatched at seam.indexer.embedding_index (where it is imported).
  - is_available patched at seam.indexer.embedding_index.is_available.
  - SEAM_VECTOR_STORE controlled via patch("seam.indexer.embedding_index.config").

Test groups:
    SE1 — modify file: only new symbols get embedded; existing rows byte-unchanged.
    SE2 — remove file: orphan embeddings gone; artifact rebuilt without them.
    SE3 — pure-removal sync: 0 symbols embedded but artifact still rebuilt.
    SE4 — artifact staleness token: after incremental sync mmap path stays valid.
    SE5 — equivalence: incremental result matches full re-embed for same code.
    SE6 — SEAM_VECTOR_STORE=off: embeddings table maintained, no artifact written.
    SE7 — fastembed absent: clean skip (returns 0), no error raised.
    SE8 — init --semantic unchanged: still full embed (not incremental).
"""

import sqlite3
import struct
from pathlib import Path
from typing import Any
from unittest.mock import patch

from seam.indexer.db import init_db
from seam.indexer.embedding_index import (
    index_embeddings,
    sync_embeddings,
)
from seam.query.vector_store import (
    compute_index_version,
    get_artifact_dir,
    load_store,
    write_store,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "test-model"
DIM = 4

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack floats as little-endian float32 bytes (no numpy)."""
    return struct.pack(f"{len(values)}f", *values)


def _decode_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _fake_embed_factory(dim: int = DIM):
    """Return a deterministic fake embed_texts that encodes a hash of each text."""

    def _fake(texts: list[str], model: str) -> list[bytes]:
        result = []
        for text in texts:
            h = abs(hash(text)) % 1000 / 1000.0
            values = [h] + [0.1] * (dim - 1)
            result.append(_f32(values))
        return result

    return _fake


def _insert_file(conn: sqlite3.Connection, path: str) -> int:
    """Insert one file row. Returns its ID."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES (?, 'python', 'abc', 1.0, 1.0)",
            (path,),
        )
    row = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    return row["id"]


def _insert_symbol(conn: sqlite3.Connection, fid: int, name: str, *, idx: int = 0) -> int:
    """Insert one symbol row. Returns its ID."""
    with conn:
        cur = conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line,"
            " signature, docstring) VALUES (?, ?, 'function', ?, ?, ?, ?)",
            (fid, name, idx * 2 + 1, idx * 2 + 2, f"def {name}()", f"Doc for {name}."),
        )
    return cur.lastrowid  # type: ignore[return-value]


def _seed_db(conn: sqlite3.Connection, file_path: str, names: list[str]) -> list[int]:
    """Seed one file + symbols. Returns list of symbol ids."""
    fid = _insert_file(conn, file_path)
    return [_insert_symbol(conn, fid, name, idx=i) for i, name in enumerate(names)]


def _embed_all(conn: sqlite3.Connection, sym_ids: list[int], dim: int = DIM) -> None:
    """Directly insert fake embeddings for the given ids (no embedder call)."""
    for sid in sym_ids:
        vec = [float(sid) / 1000.0] + [0.1] * (dim - 1)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
                " VALUES (?, ?, ?, ?)",
                (sid, MODEL, dim, _f32(vec)),
            )


def _get_embedding_blob(conn: sqlite3.Connection, sym_id: int) -> bytes | None:
    """Retrieve the embedding vector blob for a symbol, or None if absent."""
    row = conn.execute(
        "SELECT vector FROM embeddings WHERE symbol_id=? AND model=?",
        (sym_id, MODEL),
    ).fetchone()
    return bytes(row["vector"]) if row else None


def _artifact_dir(conn: sqlite3.Connection) -> Path | None:
    """Return the artifact directory from the DB file path."""
    return get_artifact_dir(conn)


def _artifact_exists(conn: sqlite3.Connection) -> bool:
    """True when all three mmap artifact files are present."""
    d = _artifact_dir(conn)
    if d is None:
        return False
    return (d / "vectors.f32").exists() and (d / "vectors.meta.json").exists()


def _all_blob_list(conn: sqlite3.Connection, sym_ids: list[int]) -> list[bytes | None]:
    return [_get_embedding_blob(conn, sid) for sid in sym_ids]


# ── SE1: modify file — only new symbols embedded, existing rows byte-unchanged ──


class TestModifyFile:
    """SE1 — After adding new symbols to an already-embedded file, only the NEW
    symbols should get embedding rows; existing rows must be byte-identical."""

    def test_new_symbols_embedded_existing_unchanged(self, tmp_path: Path) -> None:
        """New symbols added after initial embed get embedded; old rows unchanged."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # Seed initial symbols and embed them manually (no embedder call needed).
        file_a = str(tmp_path / "a.py")
        orig_ids = _seed_db(conn, file_a, ["fn_a", "fn_b"])
        _embed_all(conn, orig_ids)

        # Capture the byte content of the existing embeddings before the sync.
        orig_blobs = {sid: _get_embedding_blob(conn, sid) for sid in orig_ids}

        # Add a new symbol (simulates a file modification that added a new function).
        fid = conn.execute("SELECT id FROM files WHERE path=?", (file_a,)).fetchone()["id"]
        new_id = _insert_symbol(conn, fid, "fn_new", idx=10)

        fake_embed = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            n_added = sync_embeddings(conn, model=MODEL, batch=32)

        # Only the new symbol should have been embedded.
        assert n_added == 1, f"Expected 1 new symbol, got {n_added}"

        # The new symbol must now have an embedding row.
        assert _get_embedding_blob(conn, new_id) is not None

        # Existing rows must be byte-identical to before (untouched by scoped embed).
        for sid, orig_blob in orig_blobs.items():
            current_blob = _get_embedding_blob(conn, sid)
            assert current_blob == orig_blob, f"Symbol {sid} blob changed unexpectedly"

    def test_no_new_symbols_returns_zero(self, tmp_path: Path) -> None:
        """When all symbols are already embedded, sync returns 0."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        orig_ids = _seed_db(conn, str(tmp_path / "a.py"), ["fn_a", "fn_b"])
        _embed_all(conn, orig_ids)

        fake_embed = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            n_added = sync_embeddings(conn, model=MODEL, batch=32)

        assert n_added == 0


# ── SE2: remove file — orphan embeddings gone; artifact rebuilt ───────────────


class TestRemoveFile:
    """SE2 — After a file is removed from the index, its embedding rows should be
    removed (via FK CASCADE or orphan sweep), and the artifact must be rebuilt."""

    def test_orphan_embeddings_removed_and_artifact_rebuilt(self, tmp_path: Path) -> None:
        """Symbols deleted from the index lose their embeddings; artifact rebuilt."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # Seed two files, embed all symbols.
        ids_a = _seed_db(conn, str(tmp_path / "a.py"), ["fn_a1", "fn_a2"])
        ids_b = _seed_db(conn, str(tmp_path / "b.py"), ["fn_b1"])
        _embed_all(conn, ids_a + ids_b)

        # Build initial artifact covering all symbols.
        index_version = compute_index_version(conn, MODEL)
        d = _artifact_dir(conn)
        assert d is not None
        write_store(
            d,
            ids_a + ids_b,
            [_get_embedding_blob(conn, sid) for sid in ids_a + ids_b],  # type: ignore[misc]
            model=MODEL,
            dim=DIM,
            index_version=index_version,
        )

        # Simulate removing file b (FK CASCADE will delete its embedding rows).
        fid_b = conn.execute(
            "SELECT id FROM files WHERE path=?", (str(tmp_path / "b.py"),)
        ).fetchone()["id"]
        with conn:
            conn.execute("DELETE FROM symbols WHERE file_id=?", (fid_b,))
            conn.execute("DELETE FROM files WHERE id=?", (fid_b,))

        fake_embed = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "on"
            n_added = sync_embeddings(conn, model=MODEL, batch=32)

        # No new symbols to embed (all remaining symbols were already embedded).
        assert n_added == 0

        # Embedding rows for the removed file's symbols must be gone.
        for sid in ids_b:
            assert _get_embedding_blob(conn, sid) is None, f"Orphan row still present for {sid}"

        # Embeddings for file a must still be present.
        for sid in ids_a:
            assert _get_embedding_blob(conn, sid) is not None

        # Artifact must have been rebuilt (staleness token mismatch → rebuild).
        assert _artifact_exists(conn), "Artifact was not rebuilt after file removal"

        # The rebuilt artifact must load cleanly.
        store = load_store(d, MODEL)
        assert store is not None, "Rebuilt artifact failed to load"

        # Artifact must NOT contain the deleted file-b symbol.
        assert ids_b[0] not in store.symbol_ids, "Deleted symbol still in artifact"

        # Artifact staleness token must match current DB state.
        assert store.index_version == compute_index_version(conn, MODEL)


# ── SE3: pure-removal sync — artifact rebuilt even when 0 symbols embedded ───


class TestPureRemovalSync:
    """SE3 — A pure-removal sync (all remaining symbols already embedded, some orphans
    deleted via FK cascade) must rebuild the artifact even though n_added==0."""

    def test_artifact_rebuilt_on_pure_removal(self, tmp_path: Path) -> None:
        """Artifact is rebuilt when the DB state diverges from the artifact (n_added=0)."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        ids_a = _seed_db(conn, str(tmp_path / "a.py"), ["fn_a"])
        ids_b = _seed_db(conn, str(tmp_path / "b.py"), ["fn_b"])
        _embed_all(conn, ids_a + ids_b)

        # Write initial artifact containing BOTH file_a and file_b symbols.
        d = _artifact_dir(conn)
        assert d is not None
        version_before = compute_index_version(conn, MODEL)
        write_store(
            d,
            ids_a + ids_b,
            [_get_embedding_blob(conn, sid) for sid in ids_a + ids_b],  # type: ignore[misc]
            model=MODEL,
            dim=DIM,
            index_version=version_before,
        )

        # Verify the initial artifact contains both symbol sets.
        store_before = load_store(d, MODEL)
        assert store_before is not None
        assert ids_b[0] in store_before.symbol_ids, "File-b symbol should be in initial artifact"

        # Remove file b symbols via FK CASCADE (symbols deleted → embedding CASCADE-deleted).
        fid_b = conn.execute(
            "SELECT id FROM files WHERE path=?", (str(tmp_path / "b.py"),)
        ).fetchone()["id"]
        with conn:
            conn.execute("DELETE FROM symbols WHERE file_id=?", (fid_b,))
            conn.execute("DELETE FROM files WHERE id=?", (fid_b,))

        # Track embedder calls: it should NOT be called (all remaining symbols embedded).
        embed_call_count = 0

        def _counting_embed(texts: list[str], model: str) -> list[bytes]:
            nonlocal embed_call_count
            embed_call_count += len(texts)
            return _fake_embed_factory()(texts, model)

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=_counting_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "on"
            n_added = sync_embeddings(conn, model=MODEL, batch=32)

        assert n_added == 0, "Expected 0 new embeddings (pure-removal sync)"
        assert embed_call_count == 0, "Embedder should not have been called on pure-removal sync"

        # Artifact must still exist after rebuild.
        assert _artifact_exists(conn), "Artifact missing after pure-removal sync"

        # The rebuilt artifact must no longer contain the deleted file-b symbol.
        store_after = load_store(d, MODEL)
        assert store_after is not None, "Rebuilt artifact failed to load"
        assert ids_b[0] not in store_after.symbol_ids, (
            "File-b symbol still present in artifact after pure-removal sync"
        )
        # File-a symbols must still be in the artifact.
        assert ids_a[0] in store_after.symbol_ids, "File-a symbol should remain in artifact"

        # The artifact staleness token must match the current DB state.
        db_version_after = compute_index_version(conn, MODEL)
        assert store_after.index_version == db_version_after


# ── SE4: artifact staleness token valid after incremental sync ────────────────


class TestArtifactStalenessToken:
    """SE4 — After incremental sync, compute_index_version(DB) matches the stored
    artifact version so the mmap path stays valid and no SQL fallback is triggered."""

    def test_artifact_token_matches_db_after_incremental_sync(self, tmp_path: Path) -> None:
        """After sync_embeddings, the artifact staleness token matches the DB."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        ids_a = _seed_db(conn, str(tmp_path / "a.py"), ["fn_a", "fn_b"])
        _embed_all(conn, ids_a)

        # Add a new symbol to trigger embedding.
        fid = conn.execute(
            "SELECT id FROM files WHERE path=?", (str(tmp_path / "a.py"),)
        ).fetchone()["id"]
        new_id = _insert_symbol(conn, fid, "fn_c", idx=10)

        fake_embed = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "on"
            n_added = sync_embeddings(conn, model=MODEL, batch=32)

        assert n_added == 1

        # The artifact staleness token must match what is in the DB.
        d = _artifact_dir(conn)
        assert d is not None
        store = load_store(d, MODEL)
        assert store is not None, "Artifact not found or failed to load"

        db_version = compute_index_version(conn, MODEL)
        assert store.index_version == db_version, (
            f"Artifact version {store.index_version!r} != DB version {db_version!r}"
        )

        # The mmap artifact must contain the new symbol's vector.
        assert new_id in store.symbol_ids


# ── SE5: incremental result equivalent to full re-embed ──────────────────────


class TestIncrementalEquivalence:
    """SE5 — The embedding content produced by an incremental sync (embed only missing
    symbols) must equal what a full re-embed would have produced for the same input."""

    def test_incremental_result_matches_full_reembed(self, tmp_path: Path) -> None:
        """Incrementally-embedded vectors are identical to those from a full re-embed."""
        db_path_inc = tmp_path / ".seam_inc" / "seam.db"
        db_path_full = tmp_path / ".seam_full" / "seam.db"
        db_path_inc.parent.mkdir()
        db_path_full.parent.mkdir()

        conn_inc = init_db(db_path_inc)
        conn_full = init_db(db_path_full)

        # Seed identical symbols in both DBs.
        def _seed(conn: sqlite3.Connection) -> list[int]:
            fid = _insert_file(conn, str(tmp_path / "a.py"))
            return [_insert_symbol(conn, fid, f"fn_{i}", idx=i) for i in range(4)]

        ids_inc = _seed(conn_inc)
        ids_full = _seed(conn_full)

        # In the incremental DB, pre-embed the first two symbols (already done).
        _embed_all(conn_inc, ids_inc[:2])

        fake_embed = _fake_embed_factory()

        # Incremental path: sync_embeddings should embed only symbols 2 and 3.
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            n_added = sync_embeddings(conn_inc, model=MODEL, batch=32)
        assert n_added == 2

        # Full embed path: embed all 4 in the second DB.
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            n_full = index_embeddings(conn_full, model=MODEL, batch=32)
        assert n_full == 4

        # Vectors for symbols 2 and 3 must be byte-identical in both DBs.
        # (Symbols 0 and 1 were pre-embedded manually with different logic — not compared.)
        for inc_id, full_id in zip(ids_inc[2:], ids_full[2:]):
            inc_blob = _get_embedding_blob(conn_inc, inc_id)
            full_blob = _get_embedding_blob(conn_full, full_id)
            assert inc_blob == full_blob, (
                f"Incremental blob {_decode_f32(inc_blob or b'')} != "
                f"full blob {_decode_f32(full_blob or b'')} for pair {inc_id}/{full_id}"
            )


# ── SE6: SEAM_VECTOR_STORE=off — embeddings table maintained, no artifact ─────


class TestVectorStoreOff:
    """SE6 — When SEAM_VECTOR_STORE=off, sync_embeddings still maintains the
    embeddings table (orphan sweep + new embeds) but writes no artifact file."""

    def test_embeddings_maintained_no_artifact_written(self, tmp_path: Path) -> None:
        """SEAM_VECTOR_STORE=off: new symbols embedded, no artifact written."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # Seed an already-embedded symbol + one un-embedded new symbol.
        ids_old = _seed_db(conn, str(tmp_path / "a.py"), ["fn_old"])
        _embed_all(conn, ids_old)

        fid = conn.execute(
            "SELECT id FROM files WHERE path=?", (str(tmp_path / "a.py"),)
        ).fetchone()["id"]
        new_id = _insert_symbol(conn, fid, "fn_new", idx=5)

        fake_embed = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            n_added = sync_embeddings(conn, model=MODEL, batch=32)

        assert n_added == 1
        assert _get_embedding_blob(conn, new_id) is not None, "New symbol not embedded"

        # No artifact files should have been written.
        assert not _artifact_exists(conn), "Artifact written despite SEAM_VECTOR_STORE=off"


# ── SE7: fastembed absent — clean skip ────────────────────────────────────────


class TestFastembedAbsent:
    """SE7 — When fastembed is not installed, sync_embeddings returns 0 cleanly
    (no error, no crash, no DB writes)."""

    def test_returns_zero_when_fastembed_absent(self, tmp_path: Path) -> None:
        """sync_embeddings returns 0 (clean skip) when fastembed is unavailable."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        _seed_db(conn, str(tmp_path / "a.py"), ["fn_a", "fn_b"])

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            result = sync_embeddings(conn, model=MODEL, batch=32)

        assert result == 0

        # No embedding rows should have been written.
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert count == 0

    def test_never_raises_when_embedder_fails(self, tmp_path: Path) -> None:
        """sync_embeddings returns -1 (not raises) when the embedder errors."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        _seed_db(conn, str(tmp_path / "a.py"), ["fn_a"])

        def _failing_embed(texts: list[str], model: str) -> list[bytes]:
            raise RuntimeError("model exploded")

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=_failing_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            result = sync_embeddings(conn, model=MODEL, batch=32)

        # Must return -1 sentinel, not raise.
        assert result == -1


# ── SE8: init --semantic unchanged (still full embed) ────────────────────────


class TestInitSemanticUnchanged:
    """SE8 — 'seam init --semantic' must still use index_embeddings (full embed),
    NOT sync_embeddings. Verified by checking init_index imports and behavior."""

    def test_init_index_uses_full_embed(self, tmp_path: Path) -> None:
        """run_init with semantic=True calls index_embeddings (full embed path)."""
        from seam.indexer.init_index import run_init

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        # Create a minimal Python file to index.
        src = tmp_path / "a.py"
        src.write_text("def hello(): pass\n", encoding="utf-8")

        fake_embed = _fake_embed_factory()
        full_embed_calls: list[int] = []

        # Get the real index_embeddings for delegation.
        original_index_embeddings = __import__(
            "seam.indexer.embedding_index", fromlist=["index_embeddings"]
        ).index_embeddings

        def _tracked_full_embed(
            conn: Any, *, model: str, batch: int = 32, only_symbol_ids: Any = None
        ) -> int:
            full_embed_calls.append(1)
            # Delegate to the real implementation with mocked fastembed.
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=fake_embed):
                return original_index_embeddings(
                    conn, model=model, batch=batch, only_symbol_ids=only_symbol_ids
                )

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.init_index.index_embeddings", side_effect=_tracked_full_embed),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            result = run_init(
                tmp_path,
                db_dir=db_path.parent,
                semantic=True,
            )

        # init --semantic should have called the full index_embeddings, not sync_embeddings.
        assert len(full_embed_calls) >= 1, "init --semantic did not call index_embeddings"
        # The result should carry a total_embeddings count (not None).
        assert result.total_embeddings is not None
        assert result.total_embeddings >= 0

    def test_sync_embeddings_not_imported_in_init_index(self) -> None:
        """seam.indexer.init_index does not import sync_embeddings (guard against regression)."""
        import inspect

        import seam.indexer.init_index as init_mod

        src = inspect.getsource(init_mod)
        assert "sync_embeddings" not in src, (
            "init_index imports sync_embeddings — init --semantic must stay as full embed"
        )
