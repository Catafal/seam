"""CLI command for `seam architecture` repository briefing."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

import seam.config as config
from seam.cli.output import check_mutual_exclusion, emit_json, emit_json_error
from seam.indexer.readonly import open_readonly_connection
from seam.server.tools import handle_seam_architecture

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


def _render_architecture(result: dict) -> None:
    counts = result["counts"]
    table = Table(title="seam architecture", show_header=False, box=None)
    table.add_column("key", style="bold cyan", width=18)
    table.add_column("value")
    table.add_row("files", str(counts["files"]))
    table.add_row("symbols", str(counts["symbols"]))
    table.add_row("edges", str(counts["edges"]))
    table.add_row("clusters", str(counts["clusters"]))
    table.add_row("scope", result["scope"]["path"] or ".")
    console.print(table)

    hotspots = result.get("sections", {}).get("hotspots", {}).get("items", [])
    if hotspots:
        console.print("\n[bold cyan]Top hotspots:[/bold cyan]")
        for item in hotspots[:5]:
            console.print(f"  {item['symbol']} ({item['degrees']['incoming']} in) — {item.get('file')}")

    for warning in result.get("warnings", []):
        console.print(f"[yellow]{warning['code']}[/yellow]: {warning['message']}")


def _print_quiet_architecture(result: dict) -> None:
    counts = result["counts"]
    lines = [
        f"files={counts['files']}",
        f"symbols={counts['symbols']}",
        f"edges={counts['edges']}",
        f"clusters={counts['clusters']}",
        f"warnings={len(result.get('warnings') or [])}",
    ]
    for line in lines:
        console.print(line)


def architecture_command(
    path: str = typer.Argument(".", help="Project root to inspect (default: current directory)."),
    scope: str | None = typer.Option(None, "--scope", help="Root-relative path to summarize."),
    sections: list[str] | None = typer.Option(None, "--section", help="Architecture section to include; repeatable."),
    limit: int = typer.Option(10, "--limit", min=1, max=100, help="Default per-section item limit."),
    max_bytes: int = typer.Option(0, "--max-bytes", min=0, help="Hard compact-JSON byte budget; 0 disables."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print a terse architecture summary."),
) -> None:
    """Brief an agent or human on the indexed repository architecture."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_architecture(
            conn,
            project_root,
            scope=scope,
            sections=sections,
            limit=limit,
            max_bytes=max_bytes,
        )
    finally:
        conn.close()

    if isinstance(result, dict) and result.get("error"):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        _print_quiet_architecture(result)
        return
    _render_architecture(result)
