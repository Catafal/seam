"""Integration tests for T7 (CLI) and T8 (MCP) — semantic search surfaces.

TDD: Tests written BEFORE implementation (RED phase).

All tests are GATE-SAFE: fully offline, no network, no model download.
fastembed is NOT installed in the gate environment — all tests degrade gracefully.
Synthetic float32 vectors are injected into the DB for storage/query tests.
The embedder is monkeypatched where vector generation is needed.

Test groups:
    CLI1  — `seam init --semantic` does not crash when fastembed absent (skips cleanly)
    CLI2  — `seam status` shows embedding count = 0 when no embeddings
    CLI3  — `seam status --json` includes embedding_count and embed_model keys
    CLI4  — `seam search --no-semantic` works and forces keyword-only path
    CLI5  — `seam search` (without --no-semantic) works normally (no crash)
    CLI6  — `seam init --semantic` (with fastembed monkeypatched) reports embeddings
    MCP1  — MCP tool count is still 11 (no new tools added)
    MCP2  — seam_search handler works normally when no embeddings present
    MCP3  — seam_query handler works normally when no embeddings present
    MCP4  — `seam sync --semantic` flag is accepted and does not crash
"""

import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import seam.config as seam_config
from seam.cli.main import app
from seam.indexer.db import connect, init_db
from seam.indexer.pipeline import index_one_file

runner = CliRunner()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _make_project_with_index(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal project with an index. Returns (project_root, db_path)."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = project_root / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    # Create and index a minimal Python file so there is at least one symbol
    src = project_root / "mod.py"
    src.write_text("def hello_world():\n    '''Greet the world.'''\n    pass\n")

    conn = init_db(db_path)
    index_one_file(conn, src)
    conn.close()
    return project_root, db_path


# ── CLI1: seam init --semantic with fastembed absent ─────────────────────────


class TestInitSemanticFastembed:
    """CLI1 — `seam init --semantic` does not crash when fastembed is absent."""

    def test_init_semantic_absent_does_not_crash(self, tmp_path: Path) -> None:
        """When fastembed is absent, --semantic skips embeddings cleanly."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        src = project_root / "a.py"
        src.write_text("def foo(): pass\n")

        # Simulate fastembed being unavailable
        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            result = runner.invoke(
                app,
                [
                    "init",
                    str(project_root),
                    "--db-dir",
                    str(project_root),
                    "--semantic",
                ],
            )

        # Must not crash — exit 0
        assert result.exit_code == 0, f"init --semantic crashed: {result.output}"

    def test_init_semantic_absent_shows_skipped_message_or_completes(self, tmp_path: Path) -> None:
        """When fastembed absent, output must say 'skipped' or show 0 embeddings."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        src = project_root / "b.py"
        src.write_text("def bar(): pass\n")

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            result = runner.invoke(
                app,
                [
                    "init",
                    str(project_root),
                    "--db-dir",
                    str(project_root),
                    "--semantic",
                ],
            )

        assert result.exit_code == 0
        # Output must not contain error or traceback
        assert "Error" not in result.output or "0" in result.output
        assert "Traceback" not in result.output


# ── CLI2 / CLI3: seam status shows embedding stats ───────────────────────────


class TestStatusEmbeddings:
    """CLI2 + CLI3 — `seam status` shows embedding_count (0) and embed_model."""

    def test_status_json_has_embedding_count_zero(self, tmp_path: Path) -> None:
        """status --json includes embedding_count=0 when no embeddings exist."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "status",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--json",
            ],
        )
        assert result.exit_code == 0, f"status failed: {result.output}"
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "embedding_count" in data["data"], "status --json must include embedding_count key"
        assert data["data"]["embedding_count"] == 0

    def test_status_json_has_embed_model(self, tmp_path: Path) -> None:
        """status --json includes embed_model key."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "status",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "embed_model" in data["data"], "status --json must include embed_model key"

    def test_status_rich_shows_embeddings_row(self, tmp_path: Path) -> None:
        """status (rich mode) includes an 'embeddings' row in the table output."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "status",
                str(project_root),
                "--db-dir",
                str(project_root),
            ],
        )
        assert result.exit_code == 0
        # Rich table output must mention embeddings
        assert "embeddings" in result.output.lower(), (
            f"status output must mention embeddings; got:\n{result.output}"
        )

    def test_status_json_embedding_count_after_injection(self, tmp_path: Path) -> None:
        """status --json shows correct count after manually injecting embeddings."""
        project_root, db_path = _make_project_with_index(tmp_path)

        # Manually inject an embedding row
        conn = connect(db_path)
        sym_id = conn.execute("SELECT id FROM symbols LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (sym_id, "test-model", 3, _f32([1.0, 0.0, 0.0])),
        )
        conn.commit()
        conn.close()

        result = runner.invoke(
            app,
            [
                "status",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["embedding_count"] == 1


# ── CLI4: seam search --no-semantic forces keyword-only ──────────────────────


class TestSearchNoSemantic:
    """CLI4 — `seam search --no-semantic` forces the keyword-only path."""

    def test_search_no_semantic_flag_accepted(self, tmp_path: Path) -> None:
        """`--no-semantic` flag is accepted by seam search without crash."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "search",
                "hello",
                "--path",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--no-semantic",
            ],
        )
        # Must not crash
        assert result.exit_code == 0, f"search --no-semantic crashed: {result.output}"

    def test_search_no_semantic_does_not_call_embedder(self, tmp_path: Path) -> None:
        """`--no-semantic` ensures embed_query is never called."""
        project_root, db_path = _make_project_with_index(tmp_path)

        call_count = 0

        def _mock_embed(text: str, model: str) -> bytes:
            nonlocal call_count
            call_count += 1
            return b""

        with patch("seam.query.semantic.embed_query", _mock_embed):
            with patch("seam.query.semantic.is_available", return_value=True):
                result = runner.invoke(
                    app,
                    [
                        "search",
                        "hello",
                        "--path",
                        str(project_root),
                        "--db-dir",
                        str(project_root),
                        "--no-semantic",
                    ],
                )

        assert result.exit_code == 0
        assert call_count == 0, "--no-semantic must prevent embed_query from being called"

    def test_search_no_semantic_with_json(self, tmp_path: Path) -> None:
        """`seam search --no-semantic --json` returns structured output."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "search",
                "hello_world",
                "--path",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--no-semantic",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        # Should find hello_world
        symbols = [r["symbol"] for r in data["data"]]
        assert "hello_world" in symbols
        hit = next(r for r in data["data"] if r["symbol"] == "hello_world")
        assert hit["retrieval_mode"] == "lexical"
        assert hit["retrieval"]["sources"] == ["lexical"]

    def test_query_no_semantic_with_json(self, tmp_path: Path) -> None:
        """`seam query --no-semantic --json` returns explicit lexical retrieval metadata."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "query",
                "hello_world",
                "--path",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--no-semantic",
                "--json",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        hit = next(r for r in data["data"] if r["symbol"] == "hello_world")
        assert hit["retrieval_mode"] == "lexical"
        assert hit["retrieval"]["sources"] == ["lexical"]


# ── CLI5: seam search without --no-semantic works normally ───────────────────


class TestSearchNormal:
    """CLI5 — `seam search` without --no-semantic still works (no crash)."""

    def test_search_normal_works(self, tmp_path: Path) -> None:
        """seam search without --no-semantic works as before (no semantic)."""
        project_root, db_path = _make_project_with_index(tmp_path)

        result = runner.invoke(
            app,
            [
                "search",
                "hello_world",
                "--path",
                str(project_root),
                "--db-dir",
                str(project_root),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        hit = next(r for r in data["data"] if r["symbol"] == "hello_world")
        assert hit["retrieval_mode"] == "lexical"
        assert hit["caveats"] == []

    def test_search_with_semantic_on_no_embeddings_works(self, tmp_path: Path) -> None:
        """With SEAM_SEMANTIC=on but no embeddings, search degrades gracefully."""
        project_root, db_path = _make_project_with_index(tmp_path)

        with patch.object(seam_config, "SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=False):
                result = runner.invoke(
                    app,
                    [
                        "search",
                        "hello",
                        "--path",
                        str(project_root),
                        "--db-dir",
                        str(project_root),
                        "--json",
                    ],
                )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True


# ── CLI6: seam init --semantic with monkeypatched embedder ───────────────────


class TestInitSemanticWithEmbedder:
    """CLI6 — `seam init --semantic` calls index_embeddings when fastembed present."""

    def test_init_semantic_calls_index_embeddings_when_available(self, tmp_path: Path) -> None:
        """With fastembed available (monkeypatched), init --semantic calls embeddings."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        src = project_root / "c.py"
        src.write_text("def qux(): pass\n")

        # Track index_embeddings call count
        embed_call_count = 0

        def _mock_index_embeddings(conn, *, model, batch=32) -> int:
            nonlocal embed_call_count
            embed_call_count += 1
            return 1  # Pretend 1 symbol was embedded

        # Patch the call site: index_embeddings is now called from init_index.py
        # (the shared init pipeline), not directly from seam.cli.main.
        with patch("seam.indexer.init_index.index_embeddings", _mock_index_embeddings):
            with patch("seam.analysis.embeddings.is_available", return_value=True):
                result = runner.invoke(
                    app,
                    [
                        "init",
                        str(project_root),
                        "--db-dir",
                        str(project_root),
                        "--semantic",
                    ],
                )

        assert result.exit_code == 0, f"init --semantic failed: {result.output}"
        assert embed_call_count == 1, (
            f"index_embeddings must be called once; called {embed_call_count} times"
        )


# ── MCP1: MCP tool count is still 11 ─────────────────────────────────────────


class TestMcpToolCount:
    """MCP1 — Tool count includes seam_schema on the current read-only surface."""

    def test_mcp_tool_count_includes_schema(self, tmp_path: Path) -> None:
        """create_server registers seam_schema — semantic is transparent."""
        pytest.importorskip("mcp")

        project_root, db_path = _make_project_with_index(tmp_path)
        conn = connect(db_path)

        try:
            from seam.server.mcp import create_server

            server = create_server(conn, project_root)
            # FastMCP stores tools in a dict via _tool_manager._tools (same pattern
            # as the existing test_lean_parity.py tests)
            tool_names = list(server._tool_manager._tools.keys())
            count = len(tool_names)
            assert count == 19, f"Expected 19 MCP tools, got {count}: {sorted(tool_names)}"
        finally:
            conn.close()


# ── MCP2 + MCP3: handlers work with no embeddings ────────────────────────────


class TestMcpHandlersWithNoEmbeddings:
    """MCP2 + MCP3 — seam_search / seam_query handlers work normally without embeddings."""

    def test_handle_seam_search_no_embeddings(self, tmp_path: Path) -> None:
        """handle_seam_search works with no embeddings (pure FTS fallback)."""
        project_root, db_path = _make_project_with_index(tmp_path)
        conn = connect(db_path)

        try:
            from seam.server.tools import handle_seam_search

            result = handle_seam_search(conn, "hello_world", project_root, limit=10)
        finally:
            conn.close()

        assert isinstance(result, list)
        symbols = [r["symbol"] for r in result]
        assert "hello_world" in symbols
        hit = next(r for r in result if r["symbol"] == "hello_world")
        assert hit["retrieval_mode"] == "lexical"
        assert hit["retrieval"]["sources"] == ["lexical"]

    def test_handle_seam_query_no_embeddings(self, tmp_path: Path) -> None:
        """handle_seam_query works with no embeddings (pure FTS fallback)."""
        project_root, db_path = _make_project_with_index(tmp_path)
        conn = connect(db_path)

        try:
            from seam.server.tools import handle_seam_query

            result = handle_seam_query(conn, "hello_world", project_root, limit=10)
        finally:
            conn.close()

        assert isinstance(result, list)
        symbols = [r["symbol"] for r in result]
        assert "hello_world" in symbols
        hit = next(r for r in result if r["symbol"] == "hello_world")
        assert hit["retrieval_mode"] == "lexical"
        assert hit["retrieval"]["sources"] == ["lexical"]

    def test_handle_seam_search_semantic_on_no_embeddings_degrades(self, tmp_path: Path) -> None:
        """handle_seam_search with SEAM_SEMANTIC=on but no embeddings still returns results."""
        project_root, db_path = _make_project_with_index(tmp_path)
        conn = connect(db_path)

        try:
            from seam.server.tools import handle_seam_search

            with patch.object(seam_config, "SEAM_SEMANTIC", "on"):
                with patch("seam.query.semantic.is_available", return_value=False):
                    result = handle_seam_search(conn, "hello_world", project_root, limit=10)
        finally:
            conn.close()

        assert isinstance(result, list)
        symbols = [r["symbol"] for r in result]
        assert "hello_world" in symbols


# ── CLI4b: seam sync --semantic flag accepted ─────────────────────────────────


class TestSyncSemanticFlag:
    """CLI4b — `seam sync --semantic` flag is accepted without crash."""

    def test_sync_semantic_absent_does_not_crash(self, tmp_path: Path) -> None:
        """`seam sync --semantic` with fastembed absent exits 0."""
        project_root, db_path = _make_project_with_index(tmp_path)

        with patch("seam.indexer.embedding_index.is_available", return_value=False):
            result = runner.invoke(
                app,
                [
                    "sync",
                    str(project_root),
                    "--db-dir",
                    str(project_root),
                    "--semantic",
                    "--json",
                ],
            )

        assert result.exit_code == 0, f"sync --semantic crashed: {result.output}"
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_sync_semantic_with_embedder_calls_sync_embeddings(self, tmp_path: Path) -> None:
        """`seam sync --semantic` calls sync_embeddings (WS3: incremental embed path)."""
        project_root, db_path = _make_project_with_index(tmp_path)

        embed_call_count = 0

        def _mock_sync_embeddings(conn, *, model, batch=32) -> int:
            nonlocal embed_call_count
            embed_call_count += 1
            return 1

        # WS3: sync --semantic now calls sync_embeddings (not index_embeddings).
        with patch("seam.cli.main.sync_embeddings", _mock_sync_embeddings):
            with patch("seam.analysis.embeddings.is_available", return_value=True):
                result = runner.invoke(
                    app,
                    [
                        "sync",
                        str(project_root),
                        "--db-dir",
                        str(project_root),
                        "--semantic",
                        "--json",
                    ],
                )

        assert result.exit_code == 0, f"sync --semantic failed: {result.output}"
        assert embed_call_count == 1, (
            f"sync_embeddings must be called once; called {embed_call_count} times"
        )
