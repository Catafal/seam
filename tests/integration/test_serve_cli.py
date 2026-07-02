"""Integration tests for `seam serve` CLI command.

Tests use CliRunner against the Typer app. Focus on the non-happy paths
and the new auto-init behavior (Slice B).

We do NOT start a real uvicorn server in tests — uvicorn.run is monkeypatched
to a no-op. The happy path (server works) is covered by test_web_api.py.
"""

import sys
import types
from pathlib import Path

import typer
from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_indexed_repo(tmp_path: Path) -> Path:
    """Create a tiny indexed repo so the db exists.

    Returns the project root (which has .seam/seam.db after indexing).
    """
    (tmp_path / "sample.py").write_text(
        "def greet(name):\n"
        '    """Say hello."""\n'
        "    return f'hello {name}'\n"
    )
    from typer.testing import CliRunner as _CliRunner

    from seam.cli.main import app as cli_app

    _runner = _CliRunner()
    res = _runner.invoke(cli_app, ["init", str(tmp_path)])
    assert res.exit_code == 0, f"seam init failed: {res.output}"
    return tmp_path


def _fake_uvicorn_module(calls: list) -> types.ModuleType:
    """Build a fake uvicorn module that records calls instead of blocking."""
    mod = types.ModuleType("uvicorn")
    mod.run = lambda *a, **kw: calls.append(("run", kw))  # type: ignore[attr-defined]
    return mod


def _fake_create_web_app(*a, **kw):  # type: ignore[return]
    """Stub create_web_app that returns a sentinel rather than a real FastAPI app."""
    return object()


# ── T1: Missing web extra prints hint and exits 1 ────────────────────────────


def test_serve_missing_extra_exits_with_hint(tmp_path: Path) -> None:
    """When seam.server.web cannot be imported (extra not installed), exit 1 + hint.

    We mock _load_web_app_factory to simulate the ImportError path — the real
    function prints a hint and raises typer.Exit(1) when the import fails.
    Since [web] is checked FIRST (before any indexing), a missing index is
    irrelevant here — the [web] check fires first.
    """
    import seam.cli.serve as serve_mod  # noqa: PLC0415

    original = serve_mod._load_web_app_factory

    def _failing_factory():  # type: ignore[return]
        serve_mod.console.print(
            "[red]Web server support is not installed.[/red]\n"
            "Install it with:  [bold]pip install 'seam-code\\[web]'[/bold]"
        )
        raise typer.Exit(code=1)

    serve_mod._load_web_app_factory = _failing_factory  # type: ignore[assignment]
    try:
        res = runner.invoke(app, ["serve", str(tmp_path), "--no-open"])
    finally:
        serve_mod._load_web_app_factory = original  # type: ignore[assignment]

    assert res.exit_code == 1, f"Expected exit 1, got {res.exit_code}. Output:\n{res.output}"
    assert "seam-code" in res.output, f"Package hint not found in output:\n{res.output}"
    assert "pip install" in res.output, f"Install hint not found in output:\n{res.output}"


# ── T2: --no-init + missing index → old "run seam init" error ────────────────


def test_serve_no_init_flag_missing_index_exits(tmp_path: Path) -> None:
    """--no-init + missing .seam/seam.db → clear NO_INDEX message, exit 1.

    This preserves the pre-Slice-B behavior for scripting/CI callers who want
    an explicit failure rather than an implicit auto-index build.
    """
    res = runner.invoke(app, ["serve", str(tmp_path), "--no-open", "--no-init"])

    assert res.exit_code == 1, f"Expected exit 1, got {res.exit_code}. Output:\n{res.output}"
    assert "No index" in res.output or "seam init" in res.output, (
        f"Expected NO_INDEX message in output:\n{res.output}"
    )


# ── T3: Default host/port/no-init options are accepted (parse-only check) ────


def test_serve_default_options_are_valid(tmp_path: Path) -> None:
    """Verify that --host / --port / --no-open / --no-init flags parse without error.

    We use --no-init to force a NO_INDEX exit so the server never actually starts.
    The goal is just to confirm the CLI accepts the flags without a parse error (exit 2).
    """
    res = runner.invoke(
        app,
        [
            "serve",
            str(tmp_path),
            "--host",
            "127.0.0.1",
            "--port",
            "7420",
            "--no-open",
            "--no-init",
        ],
    )
    # Exit 1 due to NO_INDEX (--no-init), NOT due to unrecognised options (exit 2).
    assert res.exit_code != 2, f"CLI option parse error:\n{res.output}"


# ── T4: Missing index + default → auto-init runs, server starts ───────────────


def test_serve_auto_init_creates_index_and_starts_server(
    tmp_path: Path, monkeypatch
) -> None:
    """Default behavior: missing index → auto-init → server run invoked.

    We monkeypatch:
    - run_init in seam.cli.serve to avoid actual indexing (fast) and to
      track whether it was called.
    - _load_web_app_factory to return a stub factory (avoids full FastAPI boot).
    - sys.modules["uvicorn"] to record the uvicorn.run call without blocking.
    """
    import seam.cli.serve as serve_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415

    uvicorn_calls: list = []

    # Patch run_init to create the DB file cheaply and record the call.
    def _mock_run_init(root, *, db_dir=None, semantic=False, progress_cb=None):
        from seam.indexer.init_index import InitResult  # noqa: PLC0415

        db_root = db_dir if db_dir is not None else root
        db_path = cfg.get_db_path(db_root)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()  # create a minimal (empty) db file so db_path.exists() is True
        if progress_cb:
            progress_cb("Indexing 1 file(s)...")
        return InitResult(
            db_path=db_path,
            indexed_files=1,
            skipped_files=0,
            total_symbols=3,
            total_edges=1,
            total_clusters=1,
            total_synthesis=0,
            total_test_edges=0,
            total_embeddings=None,
            llm_naming_summary=None,
        )

    monkeypatch.setattr(serve_mod, "run_init", _mock_run_init)
    monkeypatch.setattr(serve_mod, "_load_web_app_factory", lambda: _fake_create_web_app)
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn_module(uvicorn_calls))

    res = runner.invoke(app, ["serve", str(tmp_path), "--no-open"])

    # Server run was reached (uvicorn.run was called).
    assert res.exit_code == 0, f"Unexpected exit {res.exit_code}. Output:\n{res.output}"
    assert len(uvicorn_calls) == 1, (
        f"Expected uvicorn.run to be called once; calls={uvicorn_calls}.\nOutput:\n{res.output}"
    )
    # Index DB was created by the mocked run_init.
    db_path = cfg.get_db_path(tmp_path)
    assert db_path.exists(), f"Expected DB to exist at {db_path} after auto-init."
    # Output should mention that indexing happened.
    assert "index" in res.output.lower(), f"Expected indexing message in output:\n{res.output}"


# ── T5: Existing index → no auto-init, server starts ─────────────────────────


def test_serve_existing_index_no_reinit(tmp_path: Path, monkeypatch) -> None:
    """When the index already exists, run_init must NOT be called.

    Existing index → bypass auto-init → server run invoked with the existing DB.
    """
    import seam.cli.serve as serve_mod  # noqa: PLC0415

    uvicorn_calls: list = []
    run_init_calls: list = []

    def _should_not_be_called(*a, **kw):  # type: ignore[return]
        run_init_calls.append(True)
        raise AssertionError("run_init should NOT be called when the index exists")

    monkeypatch.setattr(serve_mod, "run_init", _should_not_be_called)
    monkeypatch.setattr(serve_mod, "_load_web_app_factory", lambda: _fake_create_web_app)
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn_module(uvicorn_calls))

    # Pre-create the index so the db_path.exists() check passes.
    repo = _make_indexed_repo(tmp_path)

    res = runner.invoke(app, ["serve", str(repo), "--no-open"])

    assert res.exit_code == 0, f"Unexpected exit {res.exit_code}. Output:\n{res.output}"
    assert len(run_init_calls) == 0, "run_init was unexpectedly called on an existing index."
    assert len(uvicorn_calls) == 1, (
        f"Expected uvicorn.run to be called once; calls={uvicorn_calls}.\nOutput:\n{res.output}"
    )


# ── T6: Missing [web] extra reported BEFORE indexing ─────────────────────────


def test_serve_missing_web_reported_before_indexing(tmp_path: Path, monkeypatch) -> None:
    """When [web] is missing, the dependency error is reported BEFORE any indexing.

    Specifically: no DB should be created even when the index is missing.
    This verifies the ordering guarantee: [web] check → (possibly) auto-init.
    """
    import seam.cli.serve as serve_mod  # noqa: PLC0415
    import seam.config as cfg  # noqa: PLC0415

    run_init_calls: list = []

    def _track_run_init(*a, **kw):
        run_init_calls.append(True)
        return None  # shouldn't be reached

    monkeypatch.setattr(serve_mod, "run_init", _track_run_init)

    # Simulate [web] not being available — patches the factory to fail.
    def _failing_factory():  # type: ignore[return]
        serve_mod.console.print(
            "Web server support is not installed.\n"
            "Install it with:  pip install 'seam-code[web]'"
        )
        raise typer.Exit(code=1)

    monkeypatch.setattr(serve_mod, "_load_web_app_factory", _failing_factory)

    res = runner.invoke(app, ["serve", str(tmp_path), "--no-open"])

    assert res.exit_code == 1, f"Expected exit 1, got {res.exit_code}. Output:\n{res.output}"
    # run_init must NOT have been called.
    assert len(run_init_calls) == 0, (
        "run_init was called even though [web] was unavailable — ordering violation."
    )
    # No DB should have been created.
    db_path = cfg.get_db_path(tmp_path)
    assert not db_path.exists(), (
        f"DB was created at {db_path} even though [web] check failed — ordering violation."
    )
    # The output should mention the install command.
    assert "seam-code" in res.output or "pip install" in res.output, (
        f"Expected install hint in output:\n{res.output}"
    )


# ── T7: auto-init failure → clear error, exit 1, server NOT started ──────────


def test_serve_auto_init_failure_exits_cleanly(tmp_path: Path, monkeypatch) -> None:
    """When run_init raises, serve exits 1 with a clear error; server never starts."""
    import seam.cli.serve as serve_mod  # noqa: PLC0415

    uvicorn_calls: list = []

    def _failing_run_init(*a, **kw):  # type: ignore[return]
        raise RuntimeError("disk full — indexing failed")

    monkeypatch.setattr(serve_mod, "run_init", _failing_run_init)
    monkeypatch.setattr(serve_mod, "_load_web_app_factory", lambda: _fake_create_web_app)
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn_module(uvicorn_calls))

    res = runner.invoke(app, ["serve", str(tmp_path), "--no-open"])

    assert res.exit_code == 1, f"Expected exit 1, got {res.exit_code}. Output:\n{res.output}"
    # Server must NOT have started.
    assert len(uvicorn_calls) == 0, (
        f"uvicorn.run was called even though auto-init failed. Output:\n{res.output}"
    )
    # Output must mention the failure clearly (not just a traceback).
    output_lower = res.output.lower()
    assert "fail" in output_lower or "error" in output_lower, (
        f"Expected clear failure message in output:\n{res.output}"
    )
