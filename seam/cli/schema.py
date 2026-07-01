"""CLI command for `seam schema` index capability introspection."""

import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

import seam.config as config
from seam.cli.output import check_mutual_exclusion, emit_json, emit_json_error
from seam.indexer.readonly import open_readonly_connection
from seam.server.tools import handle_seam_schema

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


def _render_schema(result: dict) -> None:
    table = Table(title="seam schema", show_header=False, box=None)
    table.add_column("key", style="bold cyan", width=18)
    table.add_column("value")
    freshness = "stale" if result["freshness"]["stale"] else "fresh"
    counts = result["counts"]
    table.add_row("schema version", str(result["schema_version"]))
    table.add_row("seam version", str(result["seam_version"]))
    table.add_row("freshness", freshness)
    table.add_row("files", str(counts["files"]))
    table.add_row("symbols", str(counts["symbols"]))
    table.add_row("edges", str(counts["edges"]))
    table.add_row("clusters", str(counts["clusters"]))
    table.add_row("embeddings", str(counts["embeddings"]))
    console.print(table)

    warnings = result.get("warnings") or []
    if warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for warning in warnings:
            console.print(f"  [yellow]{warning['code']}[/yellow]: {warning['message']}")


def _print_quiet_schema(result: dict) -> None:
    freshness = "stale" if result["freshness"]["stale"] else "fresh"
    counts = result["counts"]
    lines = [
        f"freshness={freshness}",
        f"schema_version={result['schema_version']}",
        f"symbols={counts['symbols']}",
        f"edges={counts['edges']}",
        f"embeddings={counts['embeddings']}",
        f"warnings={len(result.get('warnings') or [])}",
    ]
    for line in lines:
        console.print(line)


def schema_command(
    path: str = typer.Argument(".", help="Project root to inspect (default: current directory)."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Include verbose table and column metadata for schema diagnostics.",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print a terse health summary."),
) -> None:
    """Describe the current Seam index capabilities without mutating the DB."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_schema(conn, project_root, verbose=verbose)
    finally:
        conn.close()

    if json_:
        emit_json(result)
        return
    if quiet:
        _print_quiet_schema(result)
        return
    _render_schema(result)
