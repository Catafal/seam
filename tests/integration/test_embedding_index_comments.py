"""Integration tests for WS1-B comment enrichment in index_embeddings (TDD — RED first).

GATE-SAFE: offline, no fastembed, no model download. Synthetic float32 vectors via struct.

Key design principle:
  Comments are inserted directly into the DB comments table with text that does NOT
  appear anywhere in the source file body. This isolates the DB-join contribution from
  the body-slice contribution (slice-A). If comment text appears in embedded texts, it
  can ONLY come from the DB join — not from the body.

  WHY this matters: the source file body already includes raw comment text (e.g.
  "# WHY: reason"). The DB `comments.text` column holds the STRIPPED version ("reason").
  Both contain the same words. To test the DISTINCT contribution of the DB join, we
  insert comments with tokens that are NOT present anywhere in the source file.

Tests:
    IC1 — DB comment inside span: unique token only in DB → reaches embedding.
    IC2 — DB comment outside all spans: not attributed to any symbol.
    IC3 — No DB comments: output byte-identical to body-only (no dangling newline).
    IC4 — Off gate: comment join NOT performed → unique DB token absent.
    IC5 — Multiple DB comments joined: both tokens present when budget permits.
"""

import struct
from pathlib import Path
from unittest.mock import patch

from seam.indexer.db import init_db

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_float32_bytes(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _fake_embed_factory(dim: int = 4):
    """Deterministic fake embed_texts: encodes text hash as first float."""

    def _fake(texts: list[str], model: str) -> list[bytes]:
        result = []
        for text in texts:
            h = hash(text) % 1000 / 1000.0
            values = [h] + [0.1] * (dim - 1)
            result.append(_make_float32_bytes(values))
        return result

    return _fake


def _seed_clean_source(tmp_path: Path, name: str, content: str) -> Path:
    """Write a source file with NO comment tokens in it — body is comment-free."""
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    return src


def _insert_file_and_symbol(conn, src_path: Path, sym_name: str, start: int, end: int) -> tuple[int, int]:
    """Insert file + symbol rows; return (file_id, symbol_id)."""
    with conn:
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'h1', 1.0, 1.0)",
            (str(src_path),),
        )
        fid = conn.execute(
            "SELECT id FROM files WHERE path = ?", (str(src_path),)
        ).fetchone()["id"]

    with conn:
        cur = conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
            "signature, docstring) VALUES (?, ?, 'function', ?, ?, NULL, NULL)",
            (fid, sym_name, start, end),
        )
        sid = cur.lastrowid

    return fid, sid


def _run_embedding(conn, tmp_path: Path, embed_body: str = "on") -> list[str]:
    """Run index_embeddings and return captured texts."""
    captured: list[str] = []

    def cap(texts: list[str], model: str) -> list[bytes]:
        captured.extend(texts)
        return _fake_embed_factory(4)(texts, model)

    with patch("seam.indexer.embedding_index.is_available", return_value=True):
        with patch("seam.indexer.embedding_index.embed_texts", side_effect=cap):
            with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", embed_body):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                    from seam.indexer.embedding_index import index_embeddings
                    index_embeddings(conn, model="test-model", batch=32)

    return captured


# ── IC1: DB comment inside span → token reaches embedding ─────────────────────


class TestCommentInsideSpan:
    """IC1 — A DB comment with a unique token (not in source body) reaches the embedding."""

    def test_db_comment_inside_span_reaches_embedding(self, tmp_path: Path) -> None:
        """DB comment text for a symbol reaches the embedded text via DB join.

        Source file has NO comment text — only the DB comments table has the unique token.
        If the token appears in the embedding, it can ONLY come from the DB join.
        """
        # Source with NO comment lines — clean body
        src = _seed_clean_source(
            tmp_path,
            "alpha.py",
            "def alpha():\n"
            "    x = 1\n"
            "    return x\n",
        )
        conn = init_db(tmp_path / "test.db")
        fid, _sid = _insert_file_and_symbol(conn, src, "alpha", 1, 3)

        # Insert a DB comment at line 2 (inside alpha lines 1-3) with a unique token
        # NOT present anywhere in the source body
        with conn:
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 2, 'WHY', 'UNIQUE_WHY_TOKEN_ALPHA')",
                (fid,),
            )

        captured = _run_embedding(conn, tmp_path, embed_body="on")
        conn.close()

        combined = " ".join(captured)
        assert "UNIQUE_WHY_TOKEN_ALPHA" in combined, (
            "DB WHY comment inside alpha's span must appear in embedded text via DB join. "
            "Token 'UNIQUE_WHY_TOKEN_ALPHA' is NOT in the source file body — "
            "it can only reach the embedding through the comment DB join."
        )

    def test_db_comment_at_boundary_line_inside_span(self, tmp_path: Path) -> None:
        """DB comment at the exact last line of a symbol's span is included."""
        src = _seed_clean_source(
            tmp_path,
            "boundary.py",
            "def fn():\n"
            "    pass\n",     # line 2 = last line of symbol span
        )
        conn = init_db(tmp_path / "test.db")
        fid, _sid = _insert_file_and_symbol(conn, src, "fn", 1, 2)

        with conn:
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 2, 'NOTE', 'BOUNDARY_NOTE_TOKEN')",
                (fid,),
            )

        captured = _run_embedding(conn, tmp_path, embed_body="on")
        conn.close()

        assert "BOUNDARY_NOTE_TOKEN" in " ".join(captured), (
            "DB comment at the last line of symbol span must be included"
        )


# ── IC2: DB comment outside all spans not misattributed ──────────────────────


class TestCommentOutsideSpan:
    """IC2 — DB comment with unique token outside all symbol spans is not attributed."""

    def test_top_level_db_comment_not_in_any_embedding(self, tmp_path: Path) -> None:
        """DB comment at a line outside all symbol spans is not attributed to any symbol."""
        # Source: two functions + blank lines + a top-level area after line 7
        src = _seed_clean_source(
            tmp_path,
            "src.py",
            "def alpha():\n"    # line 1
            "    return 1\n"    # line 2
            "\n"                # line 3
            "def beta():\n"    # line 4
            "    return 2\n"   # line 5
            "\n"               # line 6
            "\n",              # line 7
        )
        conn = init_db(tmp_path / "test.db")
        fid, _ = _insert_file_and_symbol(conn, src, "alpha", 1, 2)

        # Add beta as a second symbol
        with conn:
            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line, "
                "signature, docstring) VALUES (?, 'beta', 'function', 4, 5, NULL, NULL)",
                (fid,),
            )
            # Comment at line 7 — outside BOTH alpha (1-2) and beta (4-5)
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 7, 'HACK', 'TOPLEVEL_HACK_TOKEN')",
                (fid,),
            )

        captured = _run_embedding(conn, tmp_path, embed_body="on")
        conn.close()

        combined = " ".join(captured)
        assert "TOPLEVEL_HACK_TOKEN" not in combined, (
            "DB comment at line 7 (outside all symbol spans) must NOT be attributed to any symbol"
        )

    def test_comment_just_past_end_of_span_not_included(self, tmp_path: Path) -> None:
        """DB comment at start_line - 1 / end_line + 1 is not included in the symbol."""
        src = _seed_clean_source(
            tmp_path,
            "edge.py",
            "def fn():\n"   # line 1 — start
            "    pass\n"   # line 2 — end
            "x = 1\n",    # line 3 — just outside
        )
        conn = init_db(tmp_path / "test.db")
        fid, _ = _insert_file_and_symbol(conn, src, "fn", 1, 2)

        with conn:
            # Comment at line 3 — just outside fn's span [1, 2]
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 3, 'WHY', 'OUTSIDE_SPAN_TOKEN')",
                (fid,),
            )

        captured = _run_embedding(conn, tmp_path, embed_body="on")
        conn.close()

        assert "OUTSIDE_SPAN_TOKEN" not in " ".join(captured), (
            "DB comment at line 3 (one past end of symbol span [1,2]) must NOT appear"
        )


# ── IC3: No DB comments = byte-identical to body-only ────────────────────────


class TestNoDbComments:
    """IC3 — Symbol with no DB comments embeds identically to body-only (no dangling sep)."""

    def test_no_db_comment_no_dangling_separator(self, tmp_path: Path) -> None:
        """When no DB comments match a symbol, embedded text has no trailing newline."""
        src = _seed_clean_source(tmp_path, "solo.py", "def solo():\n    return 99\n")
        conn = init_db(tmp_path / "test.db")
        _insert_file_and_symbol(conn, src, "solo", 1, 2)
        # No comments inserted

        captured = _run_embedding(conn, tmp_path, embed_body="on")
        conn.close()

        assert len(captured) == 1
        assert not captured[0].endswith("\n"), (
            "Symbol with no DB comments must not have a trailing newline"
        )

    def test_no_db_comment_byte_identical_to_body_only(self, tmp_path: Path) -> None:
        """No DB comments → embedded text equals the body-only output (no extra sep)."""
        src_content = "def fn():\n    x = 1\n    return x\n"
        src = _seed_clean_source(tmp_path, "fn.py", src_content)

        # Run 1: body-only (SEAM_EMBED_BODY=on, no comments in DB)
        conn1 = init_db(tmp_path / "body_only.db")
        _insert_file_and_symbol(conn1, src, "fn", 1, 3)

        texts_body: list[str] = []

        def cap1(texts: list[str], model: str) -> list[bytes]:
            texts_body.extend(texts)
            return _fake_embed_factory(4)(texts, model)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=cap1):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        from seam.indexer.embedding_index import index_embeddings
                        index_embeddings(conn1, model="test-model", batch=32)

        # Run 2: same but also try to join comments (none in DB)
        conn2 = init_db(tmp_path / "with_comments_on.db")
        _insert_file_and_symbol(conn2, src, "fn", 1, 3)
        # No comments inserted to conn2

        texts_comments: list[str] = []

        def cap2(texts: list[str], model: str) -> list[bytes]:
            texts_comments.extend(texts)
            return _fake_embed_factory(4)(texts, model)

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=cap2):
                with patch("seam.indexer.embedding_index.SEAM_EMBED_BODY", "on"):
                    with patch("seam.indexer.embedding_index.SEAM_EMBED_INPUT_MAX_CHARS", 2000):
                        index_embeddings(conn2, model="test-model", batch=32)

        conn1.close()
        conn2.close()

        assert texts_body == texts_comments, (
            "Symbol with no DB comments must embed identically to body-only run"
        )


# ── IC4: Off gate — no comment join ──────────────────────────────────────────


class TestOffGateNoCommentJoin:
    """IC4 — SEAM_EMBED_BODY=off: DB comment join not performed → unique token absent."""

    def test_off_mode_db_comment_token_absent(self, tmp_path: Path) -> None:
        """With SEAM_EMBED_BODY=off, DB comment token must NOT appear in embedded texts."""
        src = _seed_clean_source(tmp_path, "src.py", "def fn():\n    pass\n")
        conn = init_db(tmp_path / "test.db")
        fid, _ = _insert_file_and_symbol(conn, src, "fn", 1, 2)

        with conn:
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 1, 'WHY', 'OFF_GATE_UNIQUE_TOKEN')",
                (fid,),
            )

        captured = _run_embedding(conn, tmp_path, embed_body="off")
        conn.close()

        combined = " ".join(captured)
        assert "OFF_GATE_UNIQUE_TOKEN" not in combined, (
            "SEAM_EMBED_BODY=off must produce no comment join; "
            "unique DB token must be absent from embedded texts"
        )


# ── IC5: Multiple DB comments joined ─────────────────────────────────────────


class TestMultipleDbCommentsJoined:
    """IC5 — Multiple DB comments inside a symbol's span are all joined and appended."""

    def test_multiple_db_comments_all_appear(self, tmp_path: Path) -> None:
        """Two DB comment tokens inside a symbol's span both appear in the embedding."""
        # Source with NO comment lines
        src = _seed_clean_source(
            tmp_path,
            "multi.py",
            "def rich_fn():\n"   # line 1
            "    x = 1\n"        # line 2
            "    y = 2\n"        # line 3
            "    return x + y\n",  # line 4
        )
        conn = init_db(tmp_path / "test.db")
        fid, _ = _insert_file_and_symbol(conn, src, "rich_fn", 1, 4)

        with conn:
            # Two DB comments with UNIQUE tokens not present in source
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 2, 'WHY', 'DB_REASON_TOKEN_ONE')",
                (fid,),
            )
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) "
                "VALUES (?, 3, 'NOTE', 'DB_NOTE_TOKEN_TWO')",
                (fid,),
            )

        captured = _run_embedding(conn, tmp_path, embed_body="on")
        conn.close()

        assert len(captured) == 1
        combined = captured[0]
        assert "DB_REASON_TOKEN_ONE" in combined, "First DB comment must appear"
        assert "DB_NOTE_TOKEN_TWO" in combined, "Second DB comment must appear"
