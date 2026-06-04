"""Tests for semantic search review punch-list fixes.

Covers: STOP-1, STOP-2, STOP-3, WATCH-1, WATCH-1b, WATCH-2, WATCH-3,
        DRIFT-1, DRIFT-2, DRIFT-3.

All tests are GATE-SAFE: offline, no model download, synthetic vectors via struct.pack.
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from seam.indexer.db import init_db

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes (no numpy)."""
    return struct.pack(f"{len(values)}f", *values)


def _insert_symbol(
    conn: sqlite3.Connection,
    name: str,
    docstring: str = "",
    file_path: str = "/proj/a.py",
    kind: str = "function",
) -> int:
    """Insert a symbol + file row; return symbol id."""
    conn.execute(
        "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (?, 'python', 'abc', 1.0, 1.0)",
        (file_path,),
    )
    file_id = conn.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line, docstring)"
        " VALUES (?, ?, ?, 1, 5, ?)",
        (file_id, name, kind, docstring or None),
    )
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.commit()
    return conn.execute(
        "SELECT id FROM symbols WHERE name = ? AND file_id = ?", (name, file_id)
    ).fetchone()["id"]


def _insert_embedding(
    conn: sqlite3.Connection,
    symbol_id: int,
    vector: bytes,
    model: str = "test-model",
) -> None:
    """Insert a synthetic embedding row."""
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
        " VALUES (?, ?, ?, ?)",
        (symbol_id, model, len(vector) // 4, vector),
    )
    conn.commit()


# ── STOP-1: Numpy vectorised cosine + SEAM_SEMANTIC_SCAN_CAP ─────────────────


class TestStop1NumpyFastPath:
    """STOP-1 — numpy vectorised cosine path and SEAM_SEMANTIC_SCAN_CAP."""

    def test_scan_cap_config_knob_exists(self) -> None:
        """SEAM_SEMANTIC_SCAN_CAP config knob exists with a positive default."""
        import seam.config as cfg

        assert hasattr(cfg, "SEAM_SEMANTIC_SCAN_CAP")
        assert cfg.SEAM_SEMANTIC_SCAN_CAP > 0

    def test_rrf_k_config_knob_exists(self) -> None:
        """SEAM_RRF_K config knob exists with default 60."""
        import seam.config as cfg

        assert hasattr(cfg, "SEAM_RRF_K")
        assert cfg.SEAM_RRF_K == 60

    def test_cosine_sim_still_importable_and_correct(self) -> None:
        """cosine_sim remains importable and works with pure-Python struct path."""
        from seam.query.semantic import cosine_sim

        v = _f32([1.0, 0.0, 0.0])
        result = cosine_sim(v, v)
        assert abs(result - 1.0) < 1e-5

    def test_scan_cap_limits_loaded_rows(self, tmp_path: Path) -> None:
        """With cap=2, at most 2 rows are loaded even when 5 are stored."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        for i in range(5):
            sym_id = _insert_symbol(conn, f"fn_{i}", file_path=f"/proj/f{i}.py")
            _insert_embedding(conn, sym_id, _f32([float(i + 1), 0.0]), model="m")

        query_vec = _f32([1.0, 0.0])

        with patch("seam.query.semantic.config") as mock_cfg:
            mock_cfg.SEAM_SEMANTIC_SCAN_CAP = 2
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    result = semantic_candidates(conn, "test", model="m", limit=10)

        conn.close()
        # Only 2 rows loaded → at most 2 results
        assert len(result) <= 2

    def test_scan_cap_debug_log_when_truncated(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """DEBUG is logged when scan cap truncates the result set."""
        import logging

        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        for i in range(5):
            sym_id = _insert_symbol(conn, f"cap_fn_{i}", file_path=f"/proj/cap{i}.py")
            _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="cap-m")

        query_vec = _f32([1.0, 0.0])

        with patch("seam.query.semantic.config") as mock_cfg:
            mock_cfg.SEAM_SEMANTIC_SCAN_CAP = 3
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    with caplog.at_level(logging.DEBUG, logger="seam.query.semantic"):
                        semantic_candidates(conn, "test", model="cap-m", limit=10)

        conn.close()
        assert any("capped" in r.message.lower() for r in caplog.records), (
            "Expected DEBUG log mentioning 'capped' when scan is truncated"
        )

    def test_numpy_path_correct_top_result(self, tmp_path: Path) -> None:
        """numpy fast path returns sym with highest cosine at position 0."""
        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        model = "np-test-m"

        id_a = _insert_symbol(conn, "np_best", file_path="/proj/a.py")
        _insert_embedding(conn, id_a, _f32([1.0, 0.0, 0.0]), model=model)

        id_b = _insert_symbol(conn, "np_worst", file_path="/proj/b.py")
        _insert_embedding(conn, id_b, _f32([0.0, 1.0, 0.0]), model=model)

        query_vec = _f32([1.0, 0.0, 0.0])  # aligned with np_best

        with patch("seam.query.semantic.config") as mock_cfg:
            mock_cfg.SEAM_SEMANTIC_SCAN_CAP = 20000
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    result = semantic_candidates(conn, "test", model=model, limit=5)

        conn.close()
        assert len(result) > 0
        assert result[0][0] == id_a, "Highest cosine symbol should rank first"


# ── STOP-2: FTS snippet preservation in hybrid path ───────────────────────────


class TestStop2SnippetPreservation:
    """STOP-2 — hybrid path preserves FTS snippets for FTS hits."""

    def test_hybrid_search_results_helper_is_importable(self) -> None:
        """_hybrid_search_results can be imported from seam.query.engine."""
        from seam.query.engine import _hybrid_search_results  # noqa: F401

        assert callable(_hybrid_search_results)

    def test_hybrid_helper_returns_none_when_no_new_candidates(
        self, tmp_path: Path
    ) -> None:
        """_hybrid_search_results returns None when semantic adds no new ids vs FTS."""
        from seam.query.engine import _hybrid_search_results

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "already_in_fts_sym")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="m2")

        # FTS already has this symbol — semantic won't add new ones
        fts_rows = [{"id": sym_id, "snippet": "existing snippet", "score": 1.5}]
        fts_symbol_ids = [sym_id]
        query_vec = _f32([1.0, 0.0])

        with patch("seam.query.semantic.is_available", return_value=True):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.config") as mock_cfg:
                    mock_cfg.SEAM_SEMANTIC_SCAN_CAP = 20000
                    mock_cfg.SEAM_EMBED_MODEL = "m2"
                    mock_cfg.SEAM_SEMANTIC_LIMIT = 20
                    with patch("seam.config.SEAM_EMBED_MODEL", "m2"):
                        with patch("seam.config.SEAM_SEMANTIC_LIMIT", 20):
                            with patch("seam.config.SEAM_RRF_K", 60):
                                result = _hybrid_search_results(
                                    conn, "q", fts_rows, fts_symbol_ids, 10
                                )

        conn.close()
        assert result is None, "Should return None when semantic adds nothing new"

    def test_semantic_only_result_has_empty_snippet(self, tmp_path: Path) -> None:
        """A symbol found only by semantic gets snippet='' in hybrid results."""
        from seam.query.engine import _hybrid_search_results

        conn = init_db(tmp_path / "test.db")
        sem_id = _insert_symbol(conn, "semantic_only_sym", file_path="/proj/s.py")
        _insert_embedding(conn, sem_id, _f32([1.0, 0.0]), model="m3")

        # FTS has nothing; semantic finds sem_id
        fts_rows: list[dict] = []
        fts_symbol_ids: list[int] = []
        query_vec = _f32([1.0, 0.0])

        with patch("seam.query.semantic.is_available", return_value=True):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.config") as mock_cfg:
                    mock_cfg.SEAM_SEMANTIC_SCAN_CAP = 20000
                    mock_cfg.SEAM_EMBED_MODEL = "m3"
                    mock_cfg.SEAM_SEMANTIC_LIMIT = 20
                    with patch("seam.config.SEAM_EMBED_MODEL", "m3"):
                        with patch("seam.config.SEAM_SEMANTIC_LIMIT", 20):
                            with patch("seam.config.SEAM_RRF_K", 60):
                                result = _hybrid_search_results(
                                    conn, "q", fts_rows, fts_symbol_ids, 10
                                )

        conn.close()
        if result:
            for r in result:
                assert r["snippet"] == "", (
                    f"semantic-only result must have empty snippet; got {r['snippet']!r}"
                )


# ── STOP-3: Single transaction in embedding_index ────────────────────────────


class TestStop3SingleTransaction:
    """STOP-3 — partial failure rolls back ALL embeddings (single outer transaction)."""

    def test_partial_failure_leaves_embeddings_empty(self, tmp_path: Path) -> None:
        """When second batch fails, embeddings table stays empty (no partial commit)."""
        from seam.indexer.embedding_index import index_embeddings

        conn = init_db(tmp_path / "test.db")

        # Insert 4 symbols → 2 batches of 2
        for i in range(4):
            conn.execute(
                "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
                f" VALUES ('/proj/f{i}.py', 'python', 'abc', 1.0, 1.0)"
            )
            fid = conn.execute(
                f"SELECT id FROM files WHERE path = '/proj/f{i}.py'"
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                f" VALUES ({fid}, 'sym_{i}', 'function', 1, 5)"
            )
        conn.commit()

        batch_call_count = 0

        def failing_embed(texts: list, model: str) -> list[bytes]:
            nonlocal batch_call_count
            batch_call_count += 1
            if batch_call_count >= 2:
                return []  # second batch fails
            return [_f32([1.0, 0.0]) for _ in texts]

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=failing_embed):
                result = index_embeddings(conn, model="m", batch=2)

        assert result == -1, "Should return -1 on partial failure"

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert count == 0, (
            f"Embeddings table must be empty after rollback; found {count} rows"
        )

    def test_zip_truncation_guard_returns_minus_one(self, tmp_path: Path) -> None:
        """embed_texts returning fewer blobs than texts triggers -1 sentinel."""
        from seam.indexer.embedding_index import index_embeddings

        conn = init_db(tmp_path / "test.db")
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
        fid = conn.execute(
            "SELECT id FROM files WHERE path = '/proj/a.py'"
        ).fetchone()["id"]
        for sym in ("sym_x", "sym_y"):
            conn.execute(
                f"INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                f" VALUES ({fid}, '{sym}', 'function', 1, 5)"
            )
        conn.commit()

        # Returns 1 blob for 2 texts → zip-truncation
        def truncating_embed(texts: list, model: str) -> list[bytes]:
            return [_f32([1.0, 0.0])]  # 1 not 2

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            with patch("seam.indexer.embedding_index.embed_texts", side_effect=truncating_embed):
                result = index_embeddings(conn, model="m", batch=32)

        assert result == -1

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert count == 0


# ── WATCH-1: Model-mismatch warning level ────────────────────────────────────


class TestWatch1ModelMismatchWarning:
    """WATCH-1 — model mismatch logs at WARNING, not DEBUG."""

    def test_model_mismatch_logs_at_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """semantic_candidates logs WARNING when DB has embeddings for a different model."""
        import logging

        from seam.query.semantic import semantic_candidates

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "fn_warn_test")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="model-stored")

        query_vec = _f32([1.0, 0.0])

        with patch("seam.query.semantic.is_available", return_value=True):
            with patch("seam.query.semantic.embed_query", return_value=query_vec):
                with patch("seam.query.semantic.config") as mock_cfg:
                    mock_cfg.SEAM_SEMANTIC_SCAN_CAP = 20000
                    with caplog.at_level(logging.DEBUG, logger="seam.query.semantic"):
                        result = semantic_candidates(
                            conn, "test", model="model-configured-different", limit=10
                        )

        conn.close()
        assert result == []
        # Must log at WARNING (not just DEBUG)
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) > 0, "Expected at least one WARNING record"
        assert any(
            "mismatch" in r.message.lower() or "different model" in r.message.lower()
            for r in warning_records
        )


# ── WATCH-1b: _is_hybrid_enabled one-time warning ────────────────────────────


class TestWatch1bHybridWarnOnce:
    """WATCH-1b — SEAM_SEMANTIC=on but no embeddings emits WARNING once."""

    def test_hybrid_warns_when_semantic_on_no_embeddings(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_is_hybrid_enabled logs WARNING when SEAM_SEMANTIC=on but 0 embeddings."""
        import logging

        import seam.query.engine as engine_mod
        from seam.query.engine import _is_hybrid_enabled

        conn = init_db(tmp_path / "test.db")
        # No embeddings in DB

        orig_warned = engine_mod._hybrid_warned
        engine_mod._hybrid_warned = False
        try:
            with patch("seam.config.SEAM_SEMANTIC", "on"):
                with patch("seam.config.SEAM_EMBED_MODEL", "nonexistent-model"):
                    with caplog.at_level(logging.WARNING, logger="seam.query.engine"):
                        result = _is_hybrid_enabled(conn)
        finally:
            engine_mod._hybrid_warned = orig_warned

        conn.close()
        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("seam init --semantic" in m for m in warning_msgs), (
            f"Expected WARNING with 'seam init --semantic'; got: {warning_msgs}"
        )


# ── WATCH-2: Status embedding model breakdown ────────────────────────────────


class TestWatch2StatusModels:
    """WATCH-2 — seam status JSON includes per-model counts + mismatch indicator."""

    def _make_status_db(self, tmp_path: Path, model: str = "test-model") -> tuple[Path, int]:
        """Create a DB with one symbol+embedding; return (db_dir, sym_id)."""
        from seam.indexer.db import init_db as _init_db

        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        db_path = db_dir / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = _init_db(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
        fid = conn.execute(
            "SELECT id FROM files WHERE path = '/proj/a.py'"
        ).fetchone()["id"]
        conn.execute(
            f"INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            f" VALUES ({fid}, 'fn_stat', 'function', 1, 5)"
        )
        conn.commit()
        sym_id = conn.execute("SELECT id FROM symbols WHERE name = 'fn_stat'").fetchone()["id"]
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
            " VALUES (?, ?, 2, ?)",
            (sym_id, model, _f32([1.0, 0.0])),
        )
        conn.commit()
        conn.close()
        return db_dir, sym_id

    def test_status_json_includes_embedding_models_key(self, tmp_path: Path) -> None:
        """status --json payload has 'embedding_models' dict."""
        import json

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, _ = self._make_status_db(tmp_path, model="watch2-model")

        result = runner.invoke(
            app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
        )
        assert result.exit_code == 0, f"status failed: {result.output}"
        data = json.loads(result.output)
        payload = data.get("data", data)

        assert "embedding_models" in payload, (
            f"'embedding_models' key missing from status JSON: {payload}"
        )
        assert payload["embedding_models"].get("watch2-model", 0) > 0

    def test_status_json_shows_mismatch_flag_when_models_differ(
        self, tmp_path: Path
    ) -> None:
        """status --json has embedding_model_mismatch=True when stored != configured."""
        import json

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, _ = self._make_status_db(tmp_path, model="stored-old-model")

        with patch("seam.config.SEAM_EMBED_MODEL", "configured-new-model"):
            result = runner.invoke(
                app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
            )

        assert result.exit_code == 0, f"status failed: {result.output}"
        data = json.loads(result.output)
        payload = data.get("data", data)
        assert payload.get("embedding_model_mismatch") is True, (
            f"Expected embedding_model_mismatch=True; got: {payload}"
        )

    def test_status_json_no_mismatch_when_models_match(self, tmp_path: Path) -> None:
        """status --json has embedding_model_mismatch=False when models match."""
        import json

        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        db_dir, _ = self._make_status_db(tmp_path, model="same-model")

        with patch("seam.config.SEAM_EMBED_MODEL", "same-model"):
            result = runner.invoke(
                app, ["status", "--json", "--db-dir", str(db_dir), str(db_dir)]
            )

        assert result.exit_code == 0, f"status failed: {result.output}"
        data = json.loads(result.output)
        payload = data.get("data", data)
        assert payload.get("embedding_model_mismatch") is False


# ── WATCH-3: Install hint when fastembed absent ───────────────────────────────


class TestWatch3InstallHint:
    """WATCH-3 — seam init --semantic prints install hint when fastembed absent."""

    def test_init_semantic_prints_install_hint_when_skipped(
        self, tmp_path: Path
    ) -> None:
        """When --semantic given but fastembed absent (returns 0), print install hint.

        We need at least one symbol to be indexed so the "symbols present" condition
        fires. We achieve this by creating a minimal .py fixture file in the project dir.
        """
        from typer.testing import CliRunner

        from seam.cli.main import app

        runner = CliRunner()
        # Create a minimal Python file so at least 1 symbol is indexed
        (tmp_path / "main.py").write_text(
            "def hello():\n    pass\n", encoding="utf-8"
        )
        # Monkeypatch index_embeddings to return 0 (fastembed absent / skip path)
        with patch("seam.cli.main.index_embeddings", return_value=0):
            result = runner.invoke(
                app, ["init", "--semantic", str(tmp_path)]
            )

        assert "seam-mcp[semantic]" in result.output, (
            f"Expected 'seam-mcp[semantic]' install hint in output; got:\n{result.output}"
        )


# ── DRIFT-1: --no-semantic threads semantic param, no config mutation ─────────


class TestDrift1NoSemanticParam:
    """DRIFT-1 — --no-semantic passes semantic=False without mutating global config."""

    def test_engine_search_semantic_false_skips_embed(self, tmp_path: Path) -> None:
        """engine.search(..., semantic=False) never calls embed_query."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "target_drift1", "Test docstring.")

        embed_calls = []

        def tracking_embed(text: str, model: str) -> bytes:
            embed_calls.append(text)
            return _f32([1.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", side_effect=tracking_embed):
                    search(conn, "target_drift1", semantic=False)

        conn.close()
        assert len(embed_calls) == 0, "embed_query must not be called when semantic=False"

    def test_engine_query_semantic_false_skips_embed(self, tmp_path: Path) -> None:
        """engine.query(..., semantic=False) never calls embed_query."""
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "concept_drift1", "A concept function.")

        embed_calls = []

        def tracking_embed(text: str, model: str) -> bytes:
            embed_calls.append(text)
            return _f32([1.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", side_effect=tracking_embed):
                    query(conn, "concept_drift1", semantic=False)

        conn.close()
        assert len(embed_calls) == 0

    def test_no_semantic_flag_does_not_mutate_config(self, tmp_path: Path) -> None:
        """After search(..., semantic=False), seam.config.SEAM_SEMANTIC is unchanged."""
        from typer.testing import CliRunner

        import seam.config as cfg
        from seam.cli.main import app
        from seam.indexer.db import init_db as _init_db

        runner = CliRunner()
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = _init_db(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/proj/a.py', 'python', 'abc', 1.0, 1.0)"
        )
        fid = conn.execute(
            "SELECT id FROM files WHERE path = '/proj/a.py'"
        ).fetchone()["id"]
        conn.execute(
            f"INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            f" VALUES ({fid}, 'fn_nosem', 'function', 1, 5)"
        )
        conn.commit()
        conn.close()

        original = cfg.SEAM_SEMANTIC
        runner.invoke(app, ["search", "--no-semantic", "fn_nosem", "--path", str(tmp_path)])
        assert cfg.SEAM_SEMANTIC == original, (
            f"SEAM_SEMANTIC mutated: expected {original!r}, got {cfg.SEAM_SEMANTIC!r}"
        )

    def test_query_command_has_no_semantic_flag(self, tmp_path: Path) -> None:
        """seam query --no-semantic is accepted (DRIFT-1 symmetry)."""
        from typer.testing import CliRunner

        from seam.cli.main import app
        from seam.indexer.db import init_db as _init_db

        runner = CliRunner()
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        _init_db(db_path)

        result = runner.invoke(
            app, ["query", "--no-semantic", "anything", "--path", str(tmp_path)]
        )
        # Should not error with "No such option: --no-semantic"
        assert "No such option" not in result.output, (
            f"--no-semantic not accepted by query command: {result.output}"
        )


# ── DRIFT-2: _hybrid_search_results extracted helper ─────────────────────────


class TestDrift2HybridHelper:
    """DRIFT-2 — _hybrid_search_results exists as a top-level importable helper."""

    def test_hybrid_search_results_callable(self) -> None:
        """_hybrid_search_results is importable and callable."""
        from seam.query.engine import _hybrid_search_results

        assert callable(_hybrid_search_results)

    def test_search_function_under_200_lines(self) -> None:
        """search() function body is ≤200 lines (DRIFT-2 function-length limit)."""
        import inspect

        from seam.query import engine

        source = inspect.getsource(engine.search)
        lines = source.split("\n")
        assert len(lines) <= 200, (
            f"search() has {len(lines)} lines; must be ≤200 (DRIFT-2)"
        )


# ── DRIFT-3: config import alias + v6→v7 guard pattern ───────────────────────


class TestDrift3Consistency:
    """DRIFT-3 — engine.py uses 'config' not '_cfg'; db.py uses 'if version < 7' guard."""

    def test_engine_uses_config_not_cfg_alias(self) -> None:
        """engine.py imports seam.config as 'config', not '... as _cfg'."""
        import seam.query.engine as eng

        source = open(eng.__file__).read()
        assert "as _cfg" not in source, (
            "engine.py must not use '_cfg' alias; use 'config'"
        )
        assert "import seam.config as config" in source

    def test_db_v6_to_v7_uses_version_guard(self) -> None:
        """_run_pending_migrations_if_needed uses 'if version < 7' before calling v6→v7."""
        import seam.indexer.db as db_mod

        source = open(db_mod.__file__).read()
        # Check the pattern: 'if version < 7' appears in the migration chain
        assert "if version < 7" in source, (
            "db.py must use 'if version < 7: _run_migration_v6_to_v7(conn)' pattern"
        )

    def test_rrf_k_used_from_config_not_hardcoded(self) -> None:
        """engine.py uses config.SEAM_RRF_K, not hardcoded k=60 in the hybrid helper."""
        import seam.query.engine as eng

        source = open(eng.__file__).read()
        # The hybrid helper should reference SEAM_RRF_K from config, not a bare "k = 60"
        assert "SEAM_RRF_K" in source, (
            "engine.py must use config.SEAM_RRF_K, not a hardcoded k=60"
        )
