"""CLI command for conservative cleanup suspect analysis."""

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
from seam.server.tools import handle_seam_suspects

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


def _print_quiet(result: dict) -> None:
    for item in result.get("candidates", []):
        identity = item.get("symbol") or item.get("file") or ""
        location = item.get("file") or ""
        if item.get("line") is not None:
            location = f"{location}:{item['line']}"
        print(f"{identity}\t{location}\t{item.get('suspect_strength')}\t{item.get('removal_risk')}")


def _render(result: dict) -> None:
    console.print(
        f"[bold]seam suspects[/bold]  mode={result.get('mode')}  "
        f"candidates={len(result.get('candidates', []))}"
    )
    table = Table(title="Cleanup Suspects", show_lines=False)
    table.add_column("strength", style="yellow")
    table.add_column("risk", style="red")
    table.add_column("candidate", style="bold")
    table.add_column("location", style="dim")
    table.add_column("top signals")
    for item in result.get("candidates", []):
        identity = item.get("symbol") or item.get("file") or ""
        location = item.get("file") or ""
        if item.get("line") is not None:
            location = f"{location}:{item['line']}"
        signals = ", ".join((item.get("blockers") or item.get("reasons") or [])[:3])
        table.add_row(
            item.get("suspect_strength", ""),
            item.get("removal_risk", ""),
            identity,
            location,
            signals,
        )
    console.print(table)
    caveats = result.get("caveats") or []
    if caveats:
        console.print("\n[bold yellow]Caveats[/bold yellow]")
        for caveat in caveats:
            console.print(f"  [yellow]-[/yellow] {caveat}")


def suspects_command(
    path: str = typer.Option(".", "--path", help="Project root to inspect."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    mode: str = typer.Option("symbols", "--mode", help="Suspect mode: symbols or files."),
    target: str | None = typer.Option(None, "--target", help="Exact symbol or root-relative file."),
    file_pattern: str | None = typer.Option(None, "--file", help="Root-relative file glob."),
    kind: str | None = typer.Option(None, "--kind", help="Symbol kind filter for symbol mode."),
    language: str | None = typer.Option(None, "--language", help="Indexed language filter."),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility filter."),
    is_exported: bool | None = typer.Option(None, "--exported/--not-exported", help="Export filter."),
    test_scope: str = typer.Option("source", "--test-scope", help="source | test | any."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum candidates to return."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print candidate rows only."),
) -> None:
    """Find conservative cleanup suspects; never treats static evidence as deletion proof."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_suspects(
            conn,
            project_root,
            mode=mode,
            target=target,
            file_pattern=file_pattern,
            kind=kind,
            language=language,
            visibility=visibility,
            is_exported=is_exported,
            test_scope=test_scope,
            limit=limit,
        )
    finally:
        conn.close()
    if isinstance(result, dict) and result.get("error"):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        _print_quiet(result)
        return
    _render(result)
