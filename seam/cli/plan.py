"""CLI command for `seam plan` agent change-planning."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

import seam.config as config
from seam.analysis.changes import DEFAULT_BASE_REF, VALID_SCOPES
from seam.cli.output import check_mutual_exclusion, emit_json, emit_json_error
from seam.indexer.readonly import open_readonly_connection
from seam.server.tools import handle_seam_plan

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


def _print_quiet_plan(result: dict) -> None:
    test_files = result.get("test_plan", {}).get("test_files") or []
    if test_files:
        for path in test_files:
            console.print(path)
        return
    for item in result.get("inspection_plan", []):
        location = item.get("file") or ""
        if item.get("line") is not None:
            location = f"{location}:{item['line']}"
        reasons = ",".join(item.get("reasons") or [])
        console.print(f"{item.get('symbol', '')}\t{location}\t{reasons}")


def _render_plan(result: dict) -> None:
    mode = result.get("mode", "unknown")
    risk = result.get("risk", {})
    console.print(
        f"[bold]seam plan[/bold]  mode={mode}  risk={risk.get('level', 'unknown')}  "
        f"inspect={len(result.get('inspection_plan', []))}  "
        f"tests={len(result.get('test_plan', {}).get('test_files') or [])}"
    )

    if target := result.get("target"):
        location = target.get("file") or ""
        if target.get("line") is not None:
            location = f"{location}:{target['line']}"
        console.print(f"target: [bold]{target.get('symbol')}[/bold]  [dim]{location}[/dim]")

    if diff := result.get("diff"):
        console.print(
            f"diff: scope={diff.get('scope')} base_ref={diff.get('base_ref')} "
            f"changed_symbols={len(diff.get('changed_symbols') or [])}"
        )

    table = Table(title="Inspection Plan", show_lines=False)
    table.add_column("risk", style="yellow")
    table.add_column("symbol", style="bold")
    table.add_column("location", style="dim")
    table.add_column("reasons")
    for item in result.get("inspection_plan", []):
        location = item.get("file") or ""
        if item.get("line") is not None:
            location = f"{location}:{item['line']}"
        table.add_row(
            item.get("tier") or "",
            item.get("symbol") or "",
            location,
            ", ".join(item.get("reasons") or []),
        )
    console.print(table)

    test_plan = result.get("test_plan", {})
    if test_plan.get("test_files"):
        console.print("\n[bold]Tests[/bold]")
        console.print((test_plan.get("commands") or ["pytest"])[0])
        for path in test_plan["test_files"]:
            console.print(f"  {path}")

    caveats = result.get("caveats") or []
    if caveats:
        console.print("\n[bold yellow]Caveats[/bold yellow]")
        for caveat in caveats:
            console.print(f"  [yellow]-[/yellow] {caveat}")


def plan_command(
    symbol: str | None = typer.Argument(
        None,
        help="Target symbol for target-mode planning. Omit when --mode diff is used.",
    ),
    path: str = typer.Option(".", "--path", help="Project root to inspect."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    mode: str = typer.Option("target", "--mode", help="Planning mode: target or diff."),
    max_depth: int = typer.Option(3, "--max-depth", min=1, help="Impact depth for target mode."),
    scope: str = typer.Option("working", "--scope", help=f"Diff scope: {', '.join(VALID_SCOPES)}."),
    base_ref: str = typer.Option(
        DEFAULT_BASE_REF, "--base-ref", help="Git base ref for branch mode."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print test files or inspection rows only."),
) -> None:
    """Produce a bounded inspect-and-test plan for a target symbol or current diff."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_plan(
            conn,
            project_root,
            symbol=symbol,
            mode=mode,
            max_depth=max_depth,
            scope=scope,
            base_ref=base_ref,
        )
    finally:
        conn.close()

    if isinstance(result, dict) and result.get("error"):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        _print_quiet_plan(result)
        return
    _render_plan(result)
