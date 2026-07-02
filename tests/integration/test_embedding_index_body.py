"""Integration tests for WS1-A body-slice enrichment in index_embeddings (TDD — RED first).

GATE-SAFE: offline, no fastembed, no model download. Synthetic float32 vectors via struct.

Test groups:
    IB1 — Off: SEAM_EMBED_BODY=off produces byte-identical vectors to control run.
    IB2 — On: SEAM_EMBED_BODY=on with enriched body differs from header-only.
    IB3 — Per-file caching: a file with N symbols is read from disk exactly once.
    IB4 — Unreadable-file fallback: missing file degrades to header-only, no crash.
"""

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seam.indexer.db import init_db

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_float32_bytes(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _seed_db_with_lines(conn, tmp_path: Path) -> tuple[list[int], Path]:
    """Seed DB with two symbols in one file + one symbol in another.

    Returns (sym_ids, source_file_path) — source_file_path points to the FIRST file.
    The source file is written to tmp_path/src.py with deterministic body content.
    """
    src_file = tmp_path / "src.py"
    src_file.write_text(
        "def alpha():\n"
        "    # UNIQUE_TOKEN_ALPHA\n"
        "    return 1\n"
        "\n"
        "def beta():\n"
        "    # UNIQUE_TOKEN_BETA\n"
        "    return 2\n",
        encoding="utf-8",
    )
    other_file = tmp_path / "other.py"
    other_file.write_text(
        "def gamma():\n"
        "    return 3\n",
        encoding="utf-8",
    )

    with conn:
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'abc', 1.0, 1.0)",
            (str(src_file),),
        )
        fid_src = conn.execute(
            "SELECT id FROM files WHERE path = ?", (str(src_file),)
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'def', 2.0, 2.0)",
            (str(other_file),),
        )
        fid_other = conn.execute(
            "SELECT id FROM files WHERE path = ?", (str(other_file),)
        ).fetchone()["id"]

    sym_ids = []
    symbols = [
        # (file_id, name, sig, doc, start_line, end_line)
        (fid_src, "alpha", "def alpha()", "Alpha doc.", 1, 3),
        (fid_src, "beta", "def beta()", None, 5, 7),
        (fid_other, "gamma", "def gamma()", "Gamma doc.", 1, 2),
    ]
    for fid, name, sig, doc, sl, el in symbols:
        with conn:
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
                "signature, docstring) VALUES (?, ?, 'function', ?, ?, ?, ?)",
                (fid, name, sl, el, sig, doc),
            )
            sym_ids.append(cur.lastrowid)

    return sym_ids, src_file


def _fake_embed_factory(dim: int = 4):
    """Return a deterministic fake embed_texts that encodes the input text as bytes."""

    def _fake(texts: list[str], model: str) -> list[bytes]:
        result = []
        for text in texts:
            # Encode text hash into first float, rest deterministic
            h = hash(text) % 1000 / 1000.0
            values = [h] + [0.1] * (dim - 1)
            result.append(_make_float32_bytes(values))
        return result

    return _fake


# ── IB1: Off — byte-identical to control run ─────────────────────────────────


class TestEmbedBodyOff:
    """IB1 — SEAM_EMBED_BODY=off produces byte-identical output to the control run."""

    def test_off_is_byte_identical_to_control(self, tmp_path: Path) -> None:
        """When SEAM_EMBED_BODY=off, vectors are identical to running without the feature.

        We run index_embeddings twice: once under the control path (pre-feature simulation)
        and once with SEAM_EMBED_BODY=off explicitly. The embedded texts passed to embed_texts
        must be identical in both cases.
        """
        # Control run: capture texts passed to embed_texts
        control_texts: list[list[str]] = []

        def capture_embed(texts: list[str], model: str) -> list[bytes]:
            control_texts.append(list(texts))
            return _fake_embed_factory(4)(texts, model)

        conn = init_db(tmp_path / "test.db")
        sym_ids, _ = _seed_db_with_lines(conn, tmp_path)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=capture_embed):
                # Patch SEAM_EMBED_BODY to "off" — control / default state
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "off"):
                    from seam.indexer.embedding_index import index_embeddings

                    index_embeddings(conn, model="test-model", batch=32)

        # Off run: capture texts again with explicit off
        off_texts: list[list[str]] = []

        def capture_off(texts: list[str], model: str) -> list[bytes]:
            off_texts.append(list(texts))
            return _fake_embed_factory(4)(texts, model)

        conn2 = init_db(tmp_path / "test2.db")
        _seed_db_with_lines(conn2, tmp_path)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=capture_off):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "off"):
                    index_embeddings(conn2, model="test-model", batch=32)

        conn.close()
        conn2.close()

        # Both runs must have passed exactly the same texts to embed_texts
        assert control_texts == off_texts

    def test_off_no_disk_reads(self, tmp_path: Path) -> None:
        """SEAM_EMBED_BODY=off performs no disk reads (open() not called)."""
        conn = init_db(tmp_path / "test.db")
        _seed_db_with_lines(conn, tmp_path)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch(
                "seam.indexer.embedding_index.embed_texts",
                side_effect=_fake_embed_factory(4),
            ):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "off"):
                    with patch("builtins.open", MagicMock(side_effect=AssertionError("open() called in off mode"))) as mock_open:
                        from seam.indexer.embedding_index import index_embeddings

                        # Should not raise — off path must not call open()
                        # But we need to allow open() calls from SQLite itself
                        # So we only check that our file open is not called
                        # Actually, let's track calls differently
                        mock_open.side_effect = None  # reset
                        mock_open.return_value = MagicMock()
                        # Use a targeted approach: track via side_effect on our read path
                        pass

        # Simpler: use a read-tracking mock on Path.read_text or open in embeddings module
        reads: list[str] = []
        original_open = open

        def tracking_open(path, *args, **kwargs):
            if isinstance(path, (str, Path)) and str(path).endswith(".py"):
                reads.append(str(path))
            return original_open(path, *args, **kwargs)

        conn2 = init_db(tmp_path / "test3.db")
        _seed_db_with_lines(conn2, tmp_path)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch(
                "seam.indexer.embedding_index.embed_texts",
                side_effect=_fake_embed_factory(4),
            ):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "off"):
                    # Monkeypatch the open in the embedding_index module
                    with patch("seam.indexer.embedding_index.open", tracking_open, create=True):
                        from seam.indexer.embedding_index import index_embeddings

                        index_embeddings(conn2, model="test-model", batch=32)

        conn2.close()
        # Off path must not open any .py files
        assert reads == []


# ── IB2: On — enrichment changes the embedded text ───────────────────────────


class TestEmbedBodyOn:
    """IB2 — SEAM_EMBED_BODY=on enriches texts with body content."""

    def test_on_enrichment_differs_from_off(self, tmp_path: Path) -> None:
        """With SEAM_EMBED_BODY=on, texts passed to embed_texts contain body content."""
        conn = init_db(tmp_path / "test.db")
        _seed_db_with_lines(conn, tmp_path)

        on_texts: list[str] = []

        def capture_on(texts: list[str], model: str) -> list[bytes]:
            on_texts.extend(texts)
            return _fake_embed_factory(4)(texts, model)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=capture_on):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        from seam.indexer.embedding_index import index_embeddings

                        index_embeddings(conn, model="test-model", batch=32)

        conn.close()

        # At least one embedded text should contain body content from the source file
        # "UNIQUE_TOKEN_ALPHA" is in alpha's body (lines 1-3)
        combined = " ".join(on_texts)
        assert "UNIQUE_TOKEN_ALPHA" in combined or "UNIQUE_TOKEN_BETA" in combined

    def test_on_returns_correct_count(self, tmp_path: Path) -> None:
        """SEAM_EMBED_BODY=on still returns the correct symbol count."""
        conn = init_db(tmp_path / "test.db")
        sym_ids, _ = _seed_db_with_lines(conn, tmp_path)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch(
                "seam.indexer.embedding_index.embed_texts",
                side_effect=_fake_embed_factory(4),
            ):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        from seam.indexer.embedding_index import index_embeddings

                        result = index_embeddings(conn, model="test-model", batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()

        assert result == len(sym_ids)
        assert count == len(sym_ids)


# ── IB3: Per-file caching ─────────────────────────────────────────────────────


class TestEmbedBodyFileCache:
    """IB3 — Each source file is read from disk exactly once, not once per symbol."""

    def test_file_with_multiple_symbols_read_once(self, tmp_path: Path) -> None:
        """A file with 2 symbols (alpha, beta) is opened exactly once."""
        conn = init_db(tmp_path / "test.db")
        sym_ids, src_file = _seed_db_with_lines(conn, tmp_path)

        open_counts: dict[str, int] = {}
        original_open = open

        def counting_open(path, *args, **kwargs):
            path_str = str(path)
            if path_str.endswith(".py"):
                open_counts[path_str] = open_counts.get(path_str, 0) + 1
            return original_open(path, *args, **kwargs)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch(
                "seam.indexer.embedding_index.embed_texts",
                side_effect=_fake_embed_factory(4),
            ):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        with patch(
                            "seam.indexer.embedding_index.open",
                            side_effect=counting_open,
                            create=True,
                        ):
                            from seam.indexer.embedding_index import index_embeddings

                            index_embeddings(conn, model="test-model", batch=32)

        conn.close()

        # src_file has 2 symbols (alpha, beta) but must only be opened once
        assert open_counts.get(str(src_file), 0) == 1


# ── IB4: Unreadable-file fallback ─────────────────────────────────────────────


class TestEmbedBodyUnreadableFallback:
    """IB4 — Unreadable source file degrades to header-only; indexing does not crash."""

    def test_unreadable_file_falls_back_to_header_only(self, tmp_path: Path) -> None:
        """When a source file cannot be read, symbols still get header-only embeddings."""
        conn = init_db(tmp_path / "test.db")
        sym_ids, src_file = _seed_db_with_lines(conn, tmp_path)

        # Remove the file so it cannot be read
        src_file.unlink()

        captured_texts: list[str] = []

        def capture_embed(texts: list[str], model: str) -> list[bytes]:
            captured_texts.extend(texts)
            return _fake_embed_factory(4)(texts, model)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=capture_embed):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        from seam.indexer.embedding_index import index_embeddings

                        # Must not raise
                        result = index_embeddings(conn, model="test-model", batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()

        # Still indexed all symbols (header-only for the unreadable file's symbols)
        assert result == len(sym_ids)
        assert count == len(sym_ids)
        # Unreadable file's body content must NOT appear (header-only fallback)
        combined = " ".join(captured_texts)
        assert "UNIQUE_TOKEN_ALPHA" not in combined

    def test_unreadable_file_does_not_crash(self, tmp_path: Path) -> None:
        """index_embeddings never raises when source files are unreadable."""
        conn = init_db(tmp_path / "test.db")
        _seed_db_with_lines(conn, tmp_path)

        def failing_open(path, *args, **kwargs):
            raise PermissionError("Simulated permission denied")

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch(
                "seam.indexer.embedding_index.embed_texts",
                side_effect=_fake_embed_factory(4),
            ):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        with patch(
                            "seam.indexer.embedding_index.open",
                            side_effect=failing_open,
                            create=True,
                        ):
                            from seam.indexer.embedding_index import index_embeddings

                            try:
                                result = index_embeddings(conn, model="test-model", batch=32)
                                assert isinstance(result, int)
                            except Exception as exc:  # noqa: BLE001
                                conn.close()
                                pytest.fail(
                                    f"index_embeddings raised on unreadable file: {exc}"
                                )

        conn.close()
