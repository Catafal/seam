"""'seam serve' command — start the local Seam Explorer web server.

WHY a separate module: main.py is large (>1000 lines). Keeping per-command logic in
sibling modules mirrors the pattern established by seam/cli/read.py (query/search/context)
and seam/cli/install.py.

Lazy imports
------------
fastapi, uvicorn, and seam.server.web are imported INSIDE serve_command(), not at module
top-level. This mirrors the _load_create_server() pattern in main.py for the MCP server:
the entire CLI must remain usable when the `web` extra is not installed. Only `seam serve`
needs fastapi/uvicorn — a missing extra yields a clear install hint rather than a crash
at CLI startup.

Binding
-------
127.0.0.1-only is the default and only supported host. The `--host` flag is intentionally
restricted here (it accepts any string but the CLAUDE.md non-negotiable says "127.0.0.1-only").
A user who changes --host to 0.0.0.0 does so at their own risk; the serve command does not
add network-level auth, so the docs and default strongly discourage it.
"""

import webbrowser
from pathlib import Path

import typer
from rich.console import Console

import seam.config as config

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


def serve_command(
    path: str = typer.Argument(".", help="Project root to serve (default: current directory)"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind (default: 127.0.0.1)"),
    port: int = typer.Option(7420, "--port", help="Port to listen on (default: 7420)"),
    no_open: bool = typer.Option(
        False,
        "--no-open",
        help="Do not open the browser automatically after starting.",
    ),
) -> None:
    """Start the Seam Explorer web server (read-only, 127.0.0.1 only).

    Opens a local browser window pointing at the visual graph explorer unless
    --no-open is given. The server exposes the /api/* endpoints backed by the
    Seam index at .seam/seam.db and serves the built SPA at '/'.

    Requires the [web] extra:  pip install 'seam-code[web]'
    """
    # Resolve paths the same way every other read command does (mirrors read.py).
    project_root = Path(path).resolve()
    db_path = config.get_db_path(project_root)

    # Guard: no index → emit NO_INDEX and exit 1 before importing fastapi/uvicorn.
    # WHY check the index BEFORE the lazy import: a missing index is a user error that
    # should be reported clearly regardless of whether [web] is installed. We report it
    # first (cheap path) so the user gets the right fix hint ("seam init") without also
    # needing to install the [web] extra first.
    if not db_path.exists():
        console.print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first to create the index."
        )
        raise typer.Exit(code=1)

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

    # Lazy-import the web factory — will exit 1 with a hint if [web] not installed.
    # WHY after index check: if the index is missing we surface that error first (it's
    # cheaper and does not depend on [web]), then require the extra only for the actual run.
    create_web_app = _load_web_app_factory()

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
