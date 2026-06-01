"""Seam CLI entry point — `seam init`, `seam start`, `seam status`."""

import typer

app = typer.Typer(
    name="seam",
    help="Local code intelligence MCP server for AI agents.",
    add_completion=False,
)


@app.command()
def init(
    path: str = typer.Argument(".", help="Project root to index (default: current directory)"),
) -> None:
    """Index the project into .seam/seam.db."""
    # Implementation: see IMPLEMENTATION_PLAN.md step 6.1
    typer.echo(f"[seam] Indexing {path} ... (not yet implemented)")
    raise typer.Exit(code=1)


@app.command()
def start(
    stdio: bool = typer.Option(False, "--stdio", help="Use stdio transport (for MCP)"),
) -> None:
    """Start the MCP server and file watcher."""
    # Implementation: see IMPLEMENTATION_PLAN.md step 8.3
    typer.echo("[seam] Starting MCP server ... (not yet implemented)")
    raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Show index statistics and watcher status."""
    # Implementation: see IMPLEMENTATION_PLAN.md step 6.2
    typer.echo("[seam] Status ... (not yet implemented)")
    raise typer.Exit(code=1)
