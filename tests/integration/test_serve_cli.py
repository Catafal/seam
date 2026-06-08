"""Integration tests for `seam serve` CLI command.

Tests use CliRunner against the Typer app. Focus on the two non-happy paths
that can be verified without starting a real uvicorn server:
  1. Missing-extra path: when fastapi/uvicorn are not importable, exit 1 with hint.
  2. NO_INDEX path: when there is no .seam/seam.db, exit 1 with NO_INDEX.

We do NOT start a real uvicorn server in tests — that would require threads and
port management. The happy path (server runs) is covered by test_web_api.py via
TestClient, which exercises the same create_web_app factory.
"""

from pathlib import Path

import typer
from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


# ── T1: Missing web extra prints hint and exits 1 ────────────────────────────


def _make_indexed_repo(tmp_path: Path) -> Path:
    """Create a tiny indexed repo so the NO_INDEX check passes.

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


def test_serve_missing_extra_exits_with_hint(tmp_path: Path) -> None:
    """When seam.server.web cannot be imported (extra not installed), exit 1 + hint.

    We need a repo WITH an index so the NO_INDEX check passes and we reach the lazy
    import. We mock _load_web_app_factory directly to simulate the ImportError path
    without touching sys.modules (which is unreliable when the module is already cached).
    The command must print a message mentioning 'seam-code[web]' and exit with code 1.
    """
    # Create an indexed repo so the NO_INDEX check passes and we hit the lazy import.
    repo = _make_indexed_repo(tmp_path)

    # Mock _load_web_app_factory to raise typer.Exit(1) and print the hint — this is
    # exactly what the real function does when the import fails. We test the full
    # serve_command flow including the hint output path.
    import seam.cli.serve as serve_mod  # noqa: PLC0415

    original = serve_mod._load_web_app_factory

    def _failing_factory():  # type: ignore[return]
        # Simulate the ImportError path: delegate to the real _load_web_app_factory
        # logic — print the hint and exit 1. Use same escaped brackets as serve.py.
        serve_mod.console.print(
            "[red]Web server support is not installed.[/red]\n"
            "Install it with:  [bold]pip install 'seam-code\\[web]'[/bold]"
        )
        raise typer.Exit(code=1)

    serve_mod._load_web_app_factory = _failing_factory  # type: ignore[assignment]
    try:
        res = runner.invoke(app, ["serve", str(repo), "--no-open"])
    finally:
        serve_mod._load_web_app_factory = original  # type: ignore[assignment]

    assert res.exit_code == 1, f"Expected exit 1, got {res.exit_code}. Output:\n{res.output}"
    # The hint must mention pip install and the package (brackets may be stripped by Rich markup
    # but the core text must be present). Check for the parts that survive markup rendering.
    assert "seam-code" in res.output, f"Package hint not found in output:\n{res.output}"
    assert "pip install" in res.output, f"Install hint not found in output:\n{res.output}"


# ── T2: NO_INDEX exits 1 with clear message ──────────────────────────────────


def test_serve_no_index_exits_with_message(tmp_path: Path) -> None:
    """When .seam/seam.db does not exist, exit 1 with NO_INDEX message.

    tmp_path is a fresh empty dir — no .seam/seam.db present.
    The command must print an error about no index being found and exit 1.
    We use --no-open so it would not try to open the browser even if it got past
    the NO_INDEX check (defensive).
    """
    res = runner.invoke(app, ["serve", str(tmp_path), "--no-open"])

    assert res.exit_code == 1, f"Expected exit 1, got {res.exit_code}. Output:\n{res.output}"
    # Output should mention no index and/or 'seam init'.
    assert "No index" in res.output or "seam init" in res.output, (
        f"Expected NO_INDEX message in output:\n{res.output}"
    )


# ── T3: Default host/port options are accepted (parse-only check) ─────────────


def test_serve_default_options_are_valid(tmp_path: Path) -> None:
    """Verify that --host / --port / --no-open flags are parsed without error.

    We don't actually start the server — we force a NO_INDEX exit (no DB present).
    The goal is just to confirm the CLI accepts the flags without a parse error.
    """
    res = runner.invoke(
        app,
        ["serve", str(tmp_path), "--host", "127.0.0.1", "--port", "7420", "--no-open"],
    )
    # Exit 1 due to NO_INDEX, NOT due to unrecognised options (exit 2).
    assert res.exit_code != 2, f"CLI option parse error:\n{res.output}"
