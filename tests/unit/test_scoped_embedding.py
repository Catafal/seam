"""Unit tests for WS3 Slice 1 scoped-embedding primitives (issue #210).

TDD: each test covers one observable acceptance criterion.

All tests are GATE-SAFE:
  - No fastembed, no model download.
  - Synthetic float32 vectors via struct.pack.
  - embed_texts monkeypatched to return deterministic blobs.
  - is_available always patched to True (so the real fastembed gate is bypassed).

Test groups:
    SC1 — Full embed (None): byte-identical baseline, all symbols embedded.
    SC2 — Scoped embed: only targeted ids get new rows; pre-existing rows unchanged.
    SC3 — Empty scope: returns 0, embedder never called.
    SC4 — Scoped + SEAM_EMBED_BODY=on: body enrichment path works with scoping.
    SC5 — Large scope (>1000 ids): temp-table approach handles it without error.
    SC6 — symbols_needing_embeddings: returns exactly the un-embedded ids for model.
    SC7 — delete_orphan_embeddings: deletes orphans, returns count, no-op when none.
    SC8 — Artifact write: scoped path suppresses artifact; full path writes it.
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seam.indexer.db import init_db
from seam.indexer.embedding_index import (
    delete_orphan_embeddings,
    index_embeddings,
    symbols_needing_embeddings,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

MODEL = "test-model"
DIM = 4


def _f32(values: list[float]) -> bytes:
    """Pack floats as little-endian float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _decode_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _fake_embed_factory(dim: int = DIM):
    """Return a fake embed_texts that encodes the text hash into the first float."""

    def _fake(texts: list[str], model: str) -> list[bytes]:
        result = []
        for text in texts:
            h = abs(hash(text)) % 1000 / 1000.0
            values = [h] + [0.1] * (dim - 1)
            result.append(_f32(values))
        return result

    return _fake


def _seed_symbols(conn: sqlite3.Connection, count: int = 3) -> list[int]:
    """Insert one file + `count` symbols. Returns list of symbol ids."""
    with conn:
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
    fid = conn.execute("SELECT id FROM files WHERE path='/proj/a.py'").fetchone()["id"]

    sym_ids = []
    for i in range(count):
        with conn:
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
                "signature, docstring) VALUES (?, ?, 'function', ?, ?, ?, ?)",
                (fid, f"fn_{i}", i * 2 + 1, i * 2 + 2, f"def fn_{i}()", f"Doc for fn_{i}."),
            )
            sym_ids.append(cur.lastrowid)
    return sym_ids


def _seed_symbols_with_files(conn: sqlite3.Connection, tmp_path: Path) -> list[int]:
    """Seed symbols with real source files (needed for SEAM_EMBED_BODY=on tests)."""
    src = tmp_path / "src.py"
    src.write_text(
        "def fn_0():\n    # WHY: first function\n    return 0\n"
        "\ndef fn_1():\n    return 1\n",
        encoding="utf-8",
    )
    with conn:
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'abc', 1.0, 1.0)",
            (str(src),),
        )
    fid = conn.execute(
        "SELECT id FROM files WHERE path = ?", (str(src),)
    ).fetchone()["id"]

    sym_ids = []
    for i, (sl, el) in enumerate([(1, 3), (5, 6)]):
        with conn:
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
                "signature, docstring) VALUES (?, ?, 'function', ?, ?, ?, ?)",
                (fid, f"fn_{i}", sl, el, f"def fn_{i}()", f"Doc {i}."),
            )
            sym_ids.append(cur.lastrowid)
    return sym_ids


def _stored_vector(conn: sqlite3.Connection, symbol_id: int) -> bytes | None:
    """Fetch the raw vector blob for a symbol_id, or None if absent."""
    row = conn.execute(
        "SELECT vector FROM embeddings WHERE symbol_id = ? AND model = ?",
        (symbol_id, MODEL),
    ).fetchone()
    return bytes(row["vector"]) if row else None


# ── SC1: Full embed (baseline) ────────────────────────────────────────────────


class TestFullEmbedBaseline:
    """SC1 — only_symbol_ids=None embeds all symbols (byte-identical to pre-WS3)."""

    def test_full_embed_returns_count_of_all_symbols(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            result = index_embeddings(conn, model=MODEL, batch=32)

        assert result == len(sym_ids)

    def test_full_embed_writes_all_rows(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            index_embeddings(conn, model=MODEL, batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings WHERE model=?", (MODEL,)).fetchone()[0]
        assert count == len(sym_ids)

    def test_full_embed_each_symbol_has_embedding(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            index_embeddings(conn, model=MODEL, batch=32)

        for sid in sym_ids:
            assert _stored_vector(conn, sid) is not None, f"symbol {sid} missing embedding"


# ── SC2: Scoped embed ────────────────────────────────────────────────────────


class TestScopedEmbed:
    """SC2 — only_symbol_ids=set touches only those ids; others keep their exact vector."""

    def test_scoped_only_targeted_ids_are_updated(self, tmp_path: Path) -> None:
        """Only the scoped ids receive new embedding rows."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=4)
        target_ids = {sym_ids[0], sym_ids[2]}  # embed only first and third

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            result = index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids=target_ids)

        assert result == len(target_ids)
        # Targeted ids have embeddings
        for sid in target_ids:
            assert _stored_vector(conn, sid) is not None
        # Non-targeted ids have no embeddings
        for sid in sym_ids:
            if sid not in target_ids:
                assert _stored_vector(conn, sid) is None

    def test_scoped_preserves_existing_vector_bytes(self, tmp_path: Path) -> None:
        """Pre-existing embeddings for non-scoped symbols are not touched."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)

        # Pre-seed a specific vector for sym_ids[1] directly in the DB.
        sentinel_blob = _f32([9.0, 9.0, 9.0, 9.0])
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (sym_ids[1], MODEL, DIM, sentinel_blob),
            )

        # Scoped embed: only sym_ids[0]
        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids={sym_ids[0]})

        # sym_ids[1] must still have the original sentinel vector, untouched
        stored = _stored_vector(conn, sym_ids[1])
        assert stored is not None
        assert stored == sentinel_blob, "Existing embedding for non-scoped symbol was modified"

    def test_scoped_returns_count_of_scoped_ids(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=5)
        scope = {sym_ids[0], sym_ids[4]}

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            result = index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids=scope)

        assert result == 2


# ── SC3: Empty scope ─────────────────────────────────────────────────────────


class TestEmptyScope:
    """SC3 — empty only_symbol_ids returns 0 without calling embed_texts."""

    def test_empty_scope_returns_zero(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        _seed_symbols(conn, count=3)

        mock_embed = MagicMock()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", mock_embed),
        ):
            result = index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids=set())

        assert result == 0

    def test_empty_scope_never_calls_embedder(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        _seed_symbols(conn, count=3)

        mock_embed = MagicMock()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", mock_embed),
        ):
            index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids=set())

        mock_embed.assert_not_called()

    def test_empty_scope_writes_no_rows(self, tmp_path: Path) -> None:
        conn = init_db(tmp_path / "db.db")
        _seed_symbols(conn, count=3)

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", MagicMock()),
        ):
            index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids=set())

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert count == 0


# ── SC4: Scoped + SEAM_EMBED_BODY=on ─────────────────────────────────────────


class TestScopedWithBodyEnrichment:
    """SC4 — scoped path still honors SEAM_EMBED_BODY=on for body+comment enrichment."""

    def test_scoped_body_on_embeds_targeted_ids(self, tmp_path: Path) -> None:
        """With SEAM_EMBED_BODY=on and a scope, only targeted ids get rows."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols_with_files(conn, tmp_path)
        scope = {sym_ids[0]}  # only first symbol

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
            patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"),
        ):
            result = index_embeddings(conn, model=MODEL, batch=32, only_symbol_ids=scope)

        assert result == 1
        assert _stored_vector(conn, sym_ids[0]) is not None
        assert _stored_vector(conn, sym_ids[1]) is None  # not in scope

    def test_scoped_body_on_text_differs_from_header_only(self, tmp_path: Path) -> None:
        """With SEAM_EMBED_BODY=on the embedding input is different from header-only.

        The fake embedder encodes the input text hash into the first float, so if the
        body is included the vector will differ from the header-only embedding for the
        same symbol — this proves the body path is active.
        """
        conn_body = init_db(tmp_path / "body.db")
        sym_ids_body = _seed_symbols_with_files(conn_body, tmp_path)

        conn_header = init_db(tmp_path / "header.db")
        # Seed with the SAME file content for apples-to-apples comparison
        src = tmp_path / "src.py"
        with conn_header:
            conn_header.execute(
                "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
                "VALUES (?, 'python', 'abc', 1.0, 1.0)",
                (str(src),),
            )
        fid_h = conn_header.execute(
            "SELECT id FROM files WHERE path = ?", (str(src),)
        ).fetchone()["id"]
        sym_ids_header = []
        for i, (sl, el) in enumerate([(1, 3), (5, 6)]):
            with conn_header:
                cur = conn_header.execute(
                    "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
                    "signature, docstring) VALUES (?, ?, 'function', ?, ?, ?, ?)",
                    (fid_h, f"fn_{i}", sl, el, f"def fn_{i}()", f"Doc {i}."),
                )
                sym_ids_header.append(cur.lastrowid)

        fake = _fake_embed_factory()

        # Body-on run
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
            patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"),
        ):
            index_embeddings(
                conn_body, model=MODEL, batch=32, only_symbol_ids={sym_ids_body[0]}
            )

        # Header-only run (SEAM_EMBED_BODY=off is the default)
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            index_embeddings(
                conn_header, model=MODEL, batch=32, only_symbol_ids={sym_ids_header[0]}
            )

        vec_body = _stored_vector(conn_body, sym_ids_body[0])
        vec_header = _stored_vector(conn_header, sym_ids_header[0])

        assert vec_body is not None
        assert vec_header is not None
        # Body-enriched text differs from header-only → different vector
        assert vec_body != vec_header, (
            "SEAM_EMBED_BODY=on did not produce a different vector from header-only "
            "(the body enrichment path may not be active in the scoped path)"
        )


# ── SC5: Large scope set ─────────────────────────────────────────────────────


class TestLargeScopeSet:
    """SC5 — A scope of >1000 ids works correctly via the temp-table JOIN approach."""

    def test_large_scope_embeds_all_targeted_ids(self, tmp_path: Path) -> None:
        """A scope set larger than SQLite's default param limit (999) embeds correctly."""
        count_symbols = 1100  # > 999 to exercise the temp-table path
        conn = init_db(tmp_path / "db.db")

        with conn:
            conn.execute(
                "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
                "VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
            )
        fid = conn.execute("SELECT id FROM files WHERE path='/proj/a.py'").fetchone()["id"]

        sym_ids = []
        for i in range(count_symbols):
            with conn:
                cur = conn.execute(
                    "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
                    "VALUES (?, ?, 'function', ?, ?)",
                    (fid, f"fn_{i}", i * 2 + 1, i * 2 + 2),
                )
                sym_ids.append(cur.lastrowid)

        scope = set(sym_ids)  # all count_symbols ids

        fake = _fake_embed_factory()
        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact"),
        ):
            result = index_embeddings(conn, model=MODEL, batch=100, only_symbol_ids=scope)

        assert result == count_symbols
        actual = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model=?", (MODEL,)
        ).fetchone()[0]
        assert actual == count_symbols

    def test_large_scope_does_not_raise(self, tmp_path: Path) -> None:
        """Scoped embed with >999 ids must not raise any SQLite variable-limit error."""
        count_symbols = 1050
        conn = init_db(tmp_path / "db.db")

        with conn:
            conn.execute(
                "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
                "VALUES ('/proj/b.py', 'python', 'xyz', 1.0, 1.0)"
            )
        fid = conn.execute("SELECT id FROM files WHERE path='/proj/b.py'").fetchone()["id"]

        sym_ids = []
        for i in range(count_symbols):
            with conn:
                cur = conn.execute(
                    "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
                    "VALUES (?, ?, 'function', ?, ?)",
                    (fid, f"g_{i}", i + 1, i + 2),
                )
                sym_ids.append(cur.lastrowid)

        fake = _fake_embed_factory()
        try:
            with (
                patch("seam.indexer.embedding_index.is_available", return_value=True),
                patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
                patch("seam.indexer.embedding_index._write_artifact"),
            ):
                index_embeddings(conn, model=MODEL, batch=100, only_symbol_ids=set(sym_ids))
        except Exception as exc:
            pytest.fail(f"Large-scope scoped embed raised unexpectedly: {exc}")


# ── SC6: symbols_needing_embeddings ──────────────────────────────────────────


class TestSymbolsNeedingEmbeddings:
    """SC6 — symbols_needing_embeddings returns exactly the un-embedded ids for model."""

    def test_all_symbols_missing_when_no_embeddings(self, tmp_path: Path) -> None:
        """When embeddings table is empty, all symbol ids are returned."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=4)

        missing = symbols_needing_embeddings(conn, MODEL)
        assert missing == set(sym_ids)

    def test_only_unenembedded_symbols_returned(self, tmp_path: Path) -> None:
        """Only symbols without an embedding row for MODEL are returned."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=4)

        # Pre-embed sym_ids[0] and sym_ids[2]
        embedded = {sym_ids[0], sym_ids[2]}
        for sid in embedded:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (sid, MODEL, DIM, _f32([1.0, 0.0, 0.0, 0.0])),
                )

        missing = symbols_needing_embeddings(conn, MODEL)
        expected = {sym_ids[1], sym_ids[3]}
        assert missing == expected

    def test_empty_set_when_all_embedded(self, tmp_path: Path) -> None:
        """Returns empty set when all symbols have an embedding for MODEL."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)

        for sid in sym_ids:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (sid, MODEL, DIM, _f32([0.5, 0.5, 0.5, 0.5])),
                )

        missing = symbols_needing_embeddings(conn, MODEL)
        assert missing == set()

    def test_different_model_counts_as_missing(self, tmp_path: Path) -> None:
        """A symbol embedded for a DIFFERENT model is still missing for MODEL."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=2)

        # Embed both symbols under a different model
        other_model = "other-model"
        for sid in sym_ids:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (sid, other_model, DIM, _f32([1.0, 0.0, 0.0, 0.0])),
                )

        # Both should be "missing" for MODEL
        missing = symbols_needing_embeddings(conn, MODEL)
        assert missing == set(sym_ids)

    def test_empty_db_returns_empty_set(self, tmp_path: Path) -> None:
        """Returns empty set when there are no symbols."""
        conn = init_db(tmp_path / "db.db")
        missing = symbols_needing_embeddings(conn, MODEL)
        assert missing == set()

    def test_never_raises(self, tmp_path: Path) -> None:
        """symbols_needing_embeddings must not raise even on a corrupt call."""
        conn = init_db(tmp_path / "db.db")
        # Close the connection to force an error path
        conn.close()
        # Should not raise — should return empty set
        try:
            result = symbols_needing_embeddings(conn, MODEL)
            # If it doesn't raise, result should be a set (possibly empty)
            assert isinstance(result, set)
        except Exception as exc:
            pytest.fail(f"symbols_needing_embeddings raised unexpectedly: {exc}")


# ── SC7: delete_orphan_embeddings ─────────────────────────────────────────────


class TestDeleteOrphanEmbeddings:
    """SC7 — delete_orphan_embeddings deletes orphans, returns count, is a no-op when none."""

    def test_noop_returns_zero_when_no_orphans(self, tmp_path: Path) -> None:
        """Returns 0 when all embeddings have live symbols."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)
        for sid in sym_ids:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (sid, MODEL, DIM, _f32([1.0, 0.0, 0.0, 0.0])),
                )

        result = delete_orphan_embeddings(conn)
        assert result == 0

    def test_deletes_orphan_rows_when_symbol_gone(self, tmp_path: Path) -> None:
        """Orphan embedding rows (symbol deleted) are removed and count is returned."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)
        for sid in sym_ids:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (sid, MODEL, DIM, _f32([0.5, 0.5, 0.5, 0.5])),
                )

        # Insert an orphan embedding (symbol_id that doesn't exist in symbols)
        orphan_id = 99999
        with conn:
            # Must bypass FK check or insert directly
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (orphan_id, MODEL, DIM, _f32([0.1, 0.1, 0.1, 0.1])),
            )
            conn.execute("PRAGMA foreign_keys = ON")

        # Verify orphan is there
        before = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert before == len(sym_ids) + 1

        result = delete_orphan_embeddings(conn)

        after = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert result == 1
        assert after == len(sym_ids)

    def test_idempotent_second_call_returns_zero(self, tmp_path: Path) -> None:
        """Calling delete_orphan_embeddings twice is safe; second call returns 0."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=2)
        for sid in sym_ids:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (sid, MODEL, DIM, _f32([1.0, 0.0, 0.0, 0.0])),
                )

        orphan_id = 88888
        with conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (orphan_id, MODEL, DIM, _f32([0.2, 0.2, 0.2, 0.2])),
            )
            conn.execute("PRAGMA foreign_keys = ON")

        first = delete_orphan_embeddings(conn)
        second = delete_orphan_embeddings(conn)

        assert first == 1
        assert second == 0

    def test_deletes_multiple_orphans(self, tmp_path: Path) -> None:
        """Multiple orphan rows are all deleted in one call."""
        conn = init_db(tmp_path / "db.db")
        _seed_symbols(conn, count=1)  # one live symbol, but we add 3 orphans

        with conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for orphan_id in [77001, 77002, 77003]:
                conn.execute(
                    "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                    (orphan_id, MODEL, DIM, _f32([0.3, 0.3, 0.3, 0.3])),
                )
            conn.execute("PRAGMA foreign_keys = ON")

        result = delete_orphan_embeddings(conn)
        assert result == 3
        after = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert after == 0

    def test_never_raises_on_closed_connection(self, tmp_path: Path) -> None:
        """delete_orphan_embeddings must not raise even on error."""
        conn = init_db(tmp_path / "db.db")
        conn.close()
        try:
            result = delete_orphan_embeddings(conn)
            assert isinstance(result, int)
        except Exception as exc:
            pytest.fail(f"delete_orphan_embeddings raised unexpectedly: {exc}")


# ── SC8: Artifact write behavior ─────────────────────────────────────────────


class TestArtifactWriteBehavior:
    """SC8 — scoped path suppresses _write_artifact; full path still calls it."""

    def test_scoped_path_does_not_write_artifact(self, tmp_path: Path) -> None:
        """index_embeddings with only_symbol_ids must NOT call _write_artifact."""
        conn = init_db(tmp_path / "db.db")
        sym_ids = _seed_symbols(conn, count=3)

        fake = _fake_embed_factory()
        mock_write = MagicMock()

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact", mock_write),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "on"
            index_embeddings(
                conn, model=MODEL, batch=32, only_symbol_ids={sym_ids[0]}
            )

        mock_write.assert_not_called()

    def test_full_embed_calls_write_artifact_when_store_on(self, tmp_path: Path) -> None:
        """index_embeddings with only_symbol_ids=None calls _write_artifact when store=on."""
        conn = init_db(tmp_path / "db.db")
        _seed_symbols(conn, count=2)

        fake = _fake_embed_factory()
        mock_write = MagicMock()

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact", mock_write),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "on"
            index_embeddings(conn, model=MODEL, batch=32)  # only_symbol_ids=None

        mock_write.assert_called_once()

    def test_full_embed_skips_write_artifact_when_store_off(self, tmp_path: Path) -> None:
        """index_embeddings with only_symbol_ids=None skips _write_artifact when store=off."""
        conn = init_db(tmp_path / "db.db")
        _seed_symbols(conn, count=2)

        fake = _fake_embed_factory()
        mock_write = MagicMock()

        with (
            patch("seam.indexer.embedding_index.is_available", return_value=True),
            patch("seam.indexer.embedding_index.embed_texts", side_effect=fake),
            patch("seam.indexer.embedding_index._write_artifact", mock_write),
            patch("seam.indexer.embedding_index.config") as mock_cfg,
        ):
            mock_cfg.SEAM_VECTOR_STORE = "off"
            index_embeddings(conn, model=MODEL, batch=32)

        mock_write.assert_not_called()
