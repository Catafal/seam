"""Integration tests for `seam start` missing-index auto-init."""

from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app
from seam.indexer.init_index import InitResult

runner = CliRunner()


class _FakeWatcherProcess:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def terminate(self) -> None:
        self._calls.append("terminate")


class _FakeServer:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    def run(self, *, transport: str) -> None:
        self._calls.append(("server.run", transport))


def _init_result(db_path: Path, *, indexed_files: int = 1, symbols: int = 1) -> InitResult:
    return InitResult(
        db_path=db_path,
        indexed_files=indexed_files,
        skipped_files=0,
        total_symbols=symbols,
        total_edges=0,
        total_clusters=0,
        total_synthesis=0,
        total_test_edges=0,
        total_embeddings=None,
        total_ann=None,
        llm_naming_summary=None,
    )


def _patch_successful_start_boundaries(monkeypatch, server_calls, watcher_calls) -> None:
    import seam.cli.main as main_mod  # noqa: PLC0415

    def _mock_create_server(conn, root):
        server_calls.append(("create_server", root))
        return _FakeServer(server_calls)

    monkeypatch.setattr(main_mod, "_load_create_server", lambda: _mock_create_server)
    monkeypatch.setattr(
        main_mod.subprocess,
        "Popen",
        lambda *a, **kw: _FakeWatcherProcess(watcher_calls),
    )
    monkeypatch.setattr(main_mod.signal, "signal", lambda *a, **kw: None)


def test_start_auto_init_creates_index_and_runs_mcp_server(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing index defaults to graph-only init, then starts the normal MCP path."""
    import seam.cli.main as main_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415
    from seam.indexer.db import init_db  # noqa: PLC0415

    run_init_calls: list[dict[str, object]] = []
    watcher_calls: list[str] = []
    server_calls: list[tuple[str, object]] = []

    def _mock_run_init(root, *, db_dir=None, semantic=False, progress_cb=None):
        run_init_calls.append({"root": root, "db_dir": db_dir, "semantic": semantic})
        db_root = db_dir if db_dir is not None else root
        db_path = cfg.get_db_path(db_root)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = init_db(db_path)
        conn.close()
        if progress_cb is not None:
            progress_cb("Indexing 1 file(s)...")
        return _init_result(db_path)

    monkeypatch.setattr(main_mod, "run_init", _mock_run_init)
    _patch_successful_start_boundaries(monkeypatch, server_calls, watcher_calls)

    res = runner.invoke(app, ["start", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert run_init_calls == [{"root": tmp_path.resolve(), "db_dir": None, "semantic": False}]
    assert cfg.get_db_path(tmp_path).exists()
    assert ("create_server", tmp_path.resolve()) in server_calls
    assert ("server.run", "stdio") in server_calls
    assert watcher_calls == ["terminate"]


def test_start_no_init_missing_index_exits_without_creating_db(
    tmp_path: Path, monkeypatch
) -> None:
    """Strict mode keeps the old explicit failure for scripts and CI."""
    import seam.cli.main as main_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415

    run_init_calls: list[object] = []

    monkeypatch.setattr(main_mod, "run_init", lambda *a, **kw: run_init_calls.append((a, kw)))
    monkeypatch.setattr(main_mod, "_load_create_server", lambda: object())

    res = runner.invoke(app, ["start", str(tmp_path), "--no-init"])

    assert res.exit_code == 1
    assert "No index" in res.output
    assert run_init_calls == []
    assert not cfg.get_db_path(tmp_path).exists()


def test_start_missing_mcp_dependency_fails_before_auto_init(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing MCP support should not create an index as a side effect."""
    import seam.cli.main as main_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415

    run_init_calls: list[object] = []

    def _missing_server():
        raise SystemExit(1)

    monkeypatch.setattr(main_mod, "run_init", lambda *a, **kw: run_init_calls.append((a, kw)))
    monkeypatch.setattr(main_mod, "_load_create_server", _missing_server)

    res = runner.invoke(app, ["start", str(tmp_path)])

    assert res.exit_code == 1
    assert run_init_calls == []
    assert not cfg.get_db_path(tmp_path).exists()


def test_start_existing_index_does_not_reinit(tmp_path: Path, monkeypatch) -> None:
    """Warm startup should stay fast and avoid rewriting an existing index."""
    import seam.cli.main as main_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415
    from seam.indexer.db import init_db  # noqa: PLC0415

    watcher_calls: list[str] = []
    server_calls: list[tuple[str, object]] = []
    db_path = cfg.get_db_path(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)
    conn.close()

    def _should_not_reinit(*a, **kw):  # type: ignore[return]
        raise AssertionError("run_init should not be called when an index already exists")

    monkeypatch.setattr(main_mod, "run_init", _should_not_reinit)
    _patch_successful_start_boundaries(monkeypatch, server_calls, watcher_calls)

    res = runner.invoke(app, ["start", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert ("server.run", "stdio") in server_calls
    assert watcher_calls == ["terminate"]


def test_start_auto_init_failure_exits_before_watcher_or_server(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed first-run init must not leave a watcher or partial MCP server."""
    import seam.cli.main as main_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415

    watcher_calls: list[object] = []
    server_calls: list[object] = []

    def _failing_run_init(*a, **kw):  # type: ignore[return]
        raise RuntimeError("disk full")

    monkeypatch.setattr(main_mod, "run_init", _failing_run_init)
    monkeypatch.setattr(main_mod, "_load_create_server", lambda: server_calls.append)
    monkeypatch.setattr(main_mod.subprocess, "Popen", lambda *a, **kw: watcher_calls.append((a, kw)))

    res = runner.invoke(app, ["start", str(tmp_path)])

    assert res.exit_code == 1
    assert "Auto-init failed" in res.output
    assert not cfg.get_db_path(tmp_path).exists()
    assert watcher_calls == []
    assert server_calls == []


def test_start_auto_init_missing_db_result_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """A nominal init result without a DB is unusable and must not start MCP."""
    import seam.cli.main as main_mod  # noqa: PLC0415

    watcher_calls: list[object] = []
    server_calls: list[object] = []

    def _missing_db_result(root, *, db_dir=None, semantic=False, progress_cb=None):
        return _init_result(tmp_path / ".seam" / "missing.db")

    monkeypatch.setattr(main_mod, "run_init", _missing_db_result)
    monkeypatch.setattr(main_mod, "_load_create_server", lambda: server_calls.append)
    monkeypatch.setattr(main_mod.subprocess, "Popen", lambda *a, **kw: watcher_calls.append((a, kw)))

    res = runner.invoke(app, ["start", str(tmp_path)])

    assert res.exit_code == 1
    assert "did not produce an index" in res.output
    assert watcher_calls == []
    assert server_calls == []


def test_start_auto_init_respects_db_dir(tmp_path: Path, monkeypatch) -> None:
    """Auto-init must write the same DB location that startup will connect to."""
    import seam.cli.main as main_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415
    from seam.indexer.db import init_db  # noqa: PLC0415

    repo = tmp_path / "repo"
    repo.mkdir()
    db_root = tmp_path / "index-root"
    run_init_calls: list[dict[str, object]] = []
    watcher_calls: list[str] = []
    server_calls: list[tuple[str, object]] = []

    def _mock_run_init(root, *, db_dir=None, semantic=False, progress_cb=None):
        run_init_calls.append({"root": root, "db_dir": db_dir, "semantic": semantic})
        db_path = cfg.get_db_path(db_dir)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = init_db(db_path)
        conn.close()
        return _init_result(db_path)

    monkeypatch.setattr(main_mod, "run_init", _mock_run_init)
    _patch_successful_start_boundaries(monkeypatch, server_calls, watcher_calls)

    res = runner.invoke(app, ["start", str(repo), "--db-dir", str(db_root)])

    assert res.exit_code == 0, res.output
    assert run_init_calls == [
        {"root": repo.resolve(), "db_dir": db_root.resolve(), "semantic": False}
    ]
    assert cfg.get_db_path(db_root).exists()
    assert not cfg.get_db_path(repo).exists()
    assert ("server.run", "stdio") in server_calls
