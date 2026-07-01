"""CLI command for `seam snippet` exact source retrieval."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console

import seam.config as config
from seam.cli.output import check_mutual_exclusion, emit_json, emit_json_error
from seam.indexer.readonly import open_readonly_connection
from seam.server.tools import handle_seam_snippet

console = Console()


def _open_index(path: str, db_dir: str, json_: bool) -> tuple[sqlite3.Connection, Path]:
    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        if json_:
            emit_json_error("NO_INDEX", "No index found. Run 'seam init' first.")
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    try:
        conn = open_readonly_connection(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    return conn, project_root


def _emit_error_dict(result: dict, json_: bool) -> NoReturn:
    if json_:
        emit_json_error(result["error"], result.get("message", ""))
    console.print(f"[red]Error:[/red] {result.get('message', result['error'])}")
    raise typer.Exit(code=1)


def _render_snippet(result: dict) -> None:
    if not result.get("found"):
        console.print(f"[yellow]{result.get('message', 'Snippet not found.')}[/yellow]")
        for warning in result.get("warnings", []):
            console.print(f"  [yellow]{warning['code']}[/yellow]: {warning['message']}")
        return
    console.print(
        f"[bold]{result['symbol']}[/bold]  [dim]{result['kind']} "
        f"{result['file']}:{result['start_line']}-{result['end_line']}[/dim]"
    )
    for warning in result.get("warnings", []):
        console.print(f"[yellow]{warning['code']}[/yellow]: {warning['message']}")
    console.print(result["source"], end="")


def snippet_command(
    path: str = typer.Argument(".", help="Project root to inspect (default: current directory)."),
    uid: str | None = typer.Option(None, "--uid", help="Exact symbol UID from seam_search/query."),
    symbol: str | None = typer.Option(None, "--symbol", help="Symbol name to retrieve."),
    file: str | None = typer.Option(None, "--file", help="Root-relative or absolute file path."),
    line: int | None = typer.Option(None, "--line", help="1-based source line for file+line lookup."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    context_lines: int = typer.Option(0, "--context-lines", help="Context lines around symbol."),
    max_lines: int = typer.Option(200, "--max-lines", help="Maximum source lines to return."),
    max_bytes: int = typer.Option(20_000, "--max-bytes", help="Maximum UTF-8 bytes to return."),
    include_neighbors: bool = typer.Option(
        False,
        "--neighbors",
        help="Include previous/next indexed symbols from the same file.",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print source text only."),
) -> None:
    """Expose exact source reads without requiring users to run the MCP server."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_snippet(
            conn,
            project_root,
            uid=uid,
            symbol=symbol,
            file=file,
            line=line,
            context_lines=context_lines,
            max_lines=max_lines,
            max_bytes=max_bytes,
            include_neighbors=include_neighbors,
        )
    finally:
        conn.close()

    if isinstance(result, dict) and result.get("error"):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        if result.get("found"):
            console.print(result["source"], end="")
            return
        console.print(result.get("message", "Snippet not found."))
        raise typer.Exit(code=1)
    _render_snippet(result)
