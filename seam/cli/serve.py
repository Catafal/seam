"""'seam serve' command — start the local Seam Explorer web server.

WHY a separate module: main.py is large (>1000 lines). Keeping per-command logic in
sibling modules mirrors the pattern established by seam/cli/read.py (query/search/context)
and seam/cli/install.py.

Lazy imports
------------
fastapi and uvicorn are imported INSIDE serve_command(), not at module top-level.
This mirrors the _load_create_server() pattern in main.py for the MCP server:
the entire CLI must remain usable when the `web` extra is not installed. Only `seam serve`
needs fastapi/uvicorn — a missing extra yields a clear install hint rather than a crash
at CLI startup.

run_init is imported at top level (always available; not an optional extra).

Binding
-------
127.0.0.1-only is the default and only supported host. The `--host` flag accepts any
string but the CLAUDE.md non-negotiable says "127.0.0.1-only". A user who changes
--host to 0.0.0.0 does so at their own risk; the serve command does not add
network-level auth, so the docs and default strongly discourage it.
"""

import webbrowser
from pathlib import Path

import typer
from rich.console import Console

import seam.config as config
from seam.indexer.init_index import run_init  # shared indexing pipeline (Slice B)

console = Console()
err_console = Console(stderr=True)


def _load_web_app_factory():  # type: ignore[return]
    """Lazy-import seam.server.web and return create_web_app.

    WHY lazy: `fastapi` is an optional extra (`seam-code[web]`). Importing at module
    top-level would crash the CLI on startup for users who installed without [web].
    Only `seam serve` needs it, so the import lives here, not at the top of the file.

    Returns create_web_app on success.
    Prints a red error + install hint and raises typer.Exit(1) on ImportError.
    """
    try:
        from seam.server.web import create_web_app  # noqa: PLC0415

        return create_web_app
    except ImportError:
        # WHY escaped brackets (\\[): Rich interprets [web] as a markup tag and drops it.
        # Using \\[ tells Rich to render a literal '[' so the install command is displayed
        # correctly in the terminal. CliRunner captures the rendered (markup-stripped) text.
        err_console.print(
            "[red]Web server support is not installed.[/red]\n"
            "Install it with:  [bold]pip install 'seam-code\\[web]'[/bold]\n"
            "  (from source: [bold]uv sync --extra web[/bold])"
        )
        # Also write to stdout so CliRunner captures it in res.output during tests.
        console.print(
            "[red]Web server support is not installed.[/red]\n"
            "Install it with:  [bold]pip install 'seam-code\\[web]'[/bold]"
        )
        raise typer.Exit(code=1)


def _ensure_index(project_root: Path, db_path: Path, no_init: bool) -> None:
    """Ensure the index exists, auto-initializing if allowed and the index is missing.

    WHY extracted: keeps serve_command under 200 lines and makes the auto-init
    logic independently testable via monkeypatching.

    Behavior:
    - Index present → no-op (never re-init a present-but-stale index; staleness
      is surfaced by the freshness banner and resolved by `seam sync`).
    - Index missing + no_init=True → print the "run seam init" error, exit 1.
    - Index missing + no_init=False → print a one-time progress message, call
      run_init, then return. On run_init failure, print a clear error and exit 1.

    Args:
        project_root: Resolved project root directory.
        db_path:      Expected path to the .seam/seam.db database.
        no_init:      If True, error out instead of auto-indexing.
    """
    if db_path.exists():
        return  # Happy path: index present, nothing to do.

    if no_init:
        # Preserve the pre-Slice-B "no index" error for scripting/CI callers.
        console.print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first to create the index."
        )
        raise typer.Exit(code=1)

    # Auto-init: announce the one-time cost so the user knows the command isn't hung.
    console.print(
        f"[yellow]No index found — indexing [bold]{project_root}[/bold] first "
        "(one-time)…[/yellow]"
    )

    def _progress(msg: str) -> None:
        """Forward plain-text status lines to the console during indexing."""
        console.print(f"[dim]{msg}[/dim]")

    try:
        result = run_init(
            project_root,
            db_dir=None,
            semantic=False,
            progress_cb=_progress,
        )
    except Exception as exc:
        # run_init is designed to never raise, but we catch defensively.
        console.print(
            f"[red]Auto-init failed:[/red] {exc}\n"
            "Fix the error above, then run [bold]seam init[/bold] manually."
        )
        raise typer.Exit(code=1)

    if not result.db_path.exists():
        # run_init returned a result but the DB was not created — unusual failure mode.
        console.print(
            "[red]Auto-init did not produce an index.[/red] "
            "Run [bold]seam init[/bold] manually to diagnose."
        )
        raise typer.Exit(code=1)

    console.print(
        f"[green]Indexed[/green] {result.indexed_files} file(s), "
        f"{result.total_symbols} symbol(s) — starting server."
    )


def serve_command(
    path: str = typer.Argument(".", help="Project root to serve (default: current directory)"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind (default: 127.0.0.1)"),
    port: int = typer.Option(7420, "--port", help="Port to listen on (default: 7420)"),
    no_open: bool = typer.Option(
        False,
        "--no-open",
        help="Do not open the browser automatically after starting.",
    ),
    no_init: bool = typer.Option(
        False,
        "--no-init",
        help=(
            "Do not auto-index when the project has no index; error instead. "
            "Use this in scripting/CI to fail fast rather than trigger an implicit index build."
        ),
    ),
) -> None:
    """Start the Seam Explorer web server (read-only, 127.0.0.1 only).

    Opens a local browser window pointing at the visual graph explorer unless
    --no-open is given. The server exposes the /api/* endpoints backed by the
    Seam index at .seam/seam.db and serves the built SPA at '/'.

    If no index exists, seam serve automatically indexes the project first
    (a one-time step). Use --no-init to disable this and get an explicit error.

    Requires the [web] extra:  pip install 'seam-code[web]'
    """
    # Resolve paths the same way every other read command does (mirrors read.py).
    project_root = Path(path).resolve()
    db_path = config.get_db_path(project_root)

    # Security guard: warn loudly on a non-loopback bind. The server is READ-ONLY but
    # has NO auth, so binding to a routable address exposes the indexed source code to
    # anyone on the network. ADR-010 mandates 127.0.0.1-only; we warn rather than hard-
    # reject so intentional container/devcontainer use (where 0.0.0.0 is required) stays
    # possible — but the user is told explicitly what they are exposing.
    if host not in ("127.0.0.1", "localhost", "::1"):
        err_console.print(
            f"[bold yellow]⚠ Warning:[/bold yellow] binding to [bold]{host}[/bold] exposes "
            "your indexed source code on the network with NO authentication.\n"
            "[dim]Seam Explorer is designed for 127.0.0.1 only. Use a non-loopback host "
            "only on a trusted network you control.[/dim]"
        )

    # Check [web] availability FIRST — before any slow indexing.
    # WHY before _ensure_index: a missing extra should be reported immediately so the
    # user never waits for a full index only to hit a missing-dependency error at the end.
    create_web_app = _load_web_app_factory()

    # Ensure the index exists (auto-init if allowed, or error if --no-init).
    _ensure_index(project_root, db_path, no_init)

    # Re-resolve db_path after _ensure_index in case auto-init placed the DB
    # at a non-default location (db_dir override; not currently used by serve
    # but resolved defensively to keep parity with run_init's contract).
    db_path = config.get_db_path(project_root)

    # Build the FastAPI app.
    web_app = create_web_app(db_path=db_path, root=project_root)

    # Open the browser BEFORE blocking on uvicorn so the window appears promptly.
    # WHY before uvicorn: uvicorn.run() blocks; the browser.open() would never execute
    # if placed after it. The tiny race (browser opens before the server is ready) is
    # acceptable — browsers retry on "connection refused" within ~1 s.
    url = f"http://{host}:{port}"
    if not no_open:
        webbrowser.open(url)

    console.print(
        f"[bold green]Seam Explorer[/bold green] running at [cyan]{url}[/cyan]\n"
        "[dim]Press Ctrl+C to stop.[/dim]"
    )

    # Lazy-import uvicorn — also guarded by _load_web_app_factory above (fastapi is a
    # transitive dep of uvicorn in the [web] extra), but explicit here for clarity.
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        err_console.print(
            "[red]uvicorn is not installed.[/red] "
            "Install it with:  [bold]pip install 'seam-code[web]'[/bold]"
        )
        raise typer.Exit(code=1)

    # Run uvicorn programmatically (blocks until Ctrl+C / SIGTERM).
    uvicorn.run(web_app, host=host, port=port, log_level="warning")
