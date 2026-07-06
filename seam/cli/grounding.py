"""CLI command for docs/spec grounding."""

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
from seam.server.tools import handle_seam_grounding

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
        line = item.get("line_range", {}).get("start")
        print(
            f"{item.get('doc_path')}\t{line}\t{item.get('confidence')}\t"
            f"{item.get('relation_type')}\t{item.get('target', {}).get('value')}"
        )


def _render(result: dict) -> None:
    console.print(
        f"[bold]seam grounding[/bold]  candidates={len(result.get('candidates', []))}"
    )
    table = Table(title="Document Grounding", show_lines=False)
    table.add_column("confidence", style="green")
    table.add_column("doc", style="bold")
    table.add_column("kind/status")
    table.add_column("heading")
    table.add_column("target")
    for item in result.get("candidates", []):
        target = item.get("target", {})
        line = item.get("line_range", {}).get("start")
        table.add_row(
            item.get("confidence", ""),
            f"{item.get('doc_path')}:{line}",
            f"{item.get('doc_kind')}/{item.get('status')}",
            item.get("heading_path") or "",
            f"{target.get('kind')}:{target.get('value')}",
        )
    console.print(table)
    caveats = result.get("caveats") or []
    if caveats:
        console.print("\n[bold yellow]Caveats[/bold yellow]")
        for caveat in caveats:
            console.print(f"  [yellow]-[/yellow] {caveat}")


def grounding_command(
    path: str = typer.Option(".", "--path", help="Project root to inspect."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    symbol: str | None = typer.Option(None, "--symbol", help="Find docs grounding a symbol."),
    file: str | None = typer.Option(None, "--file", help="Find docs grounding a root-relative file."),
    route: str | None = typer.Option(None, "--route", help="Find docs grounding a route path."),
    config_key: str | None = typer.Option(None, "--config", help="Find docs grounding a config key."),
    resource: str | None = typer.Option(None, "--resource", help="Find docs grounding a resource."),
    doc_path: str | None = typer.Option(None, "--doc", help="Restrict to one doc path."),
    query: str | None = typer.Option(None, "--query", help="Docs-first grounding search text."),
    doc_kind: str | None = typer.Option(None, "--doc-kind", help="Filter by doc kind."),
    status: str | None = typer.Option(None, "--status", help="Filter by document status."),
    relation_type: str | None = typer.Option(None, "--relation", help="Filter by relation type."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum candidates to return."),
    include_snippets: bool = typer.Option(False, "--snippets", help="Include bounded doc snippets."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print candidate rows only."),
) -> None:
    """Find local docs/spec anchors that explicitly ground code or roadmap questions."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_grounding(
            conn,
            project_root,
            symbol=symbol,
            file=file,
            route=route,
            config_key=config_key,
            resource=resource,
            doc_path=doc_path,
            query=query,
            doc_kind=doc_kind,
            status=status,
            relation_type=relation_type,
            limit=limit,
            include_snippets=include_snippets,
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
