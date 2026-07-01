"""CLI command for `seam graph-search` structural discovery."""

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
from seam.server.tools import handle_seam_graph_search

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


def _render_graph_search(result: dict) -> None:
    items = result.get("items", [])
    if not items:
        console.print("[dim]No matches.[/dim]")
        return
    table = Table(title="graph-search")
    table.add_column("symbol", style="bold")
    table.add_column("uid", style="cyan")
    table.add_column("kind")
    table.add_column("location", style="dim")
    table.add_column("degree", justify="right")
    for item in items:
        degrees = item["degrees"]
        table.add_row(
            item["symbol"],
            item["uid"],
            item["kind"],
            f"{item['file']}:{item['line']}",
            f"{degrees['incoming']}/{degrees['outgoing']}/{degrees['total']}",
        )
    console.print(table)
    if result.get("has_more"):
        console.print(
            f"[dim]Showing {len(items)} of {result['total']}."
            f" Re-run with --offset {result['offset'] + result['limit']}.[/dim]"
        )
    for warning in result.get("warnings", []):
        console.print(f"[yellow]{warning['code']}[/yellow]: {warning['message']}")


def graph_search_command(
    path: str = typer.Argument(".", help="Project root to inspect (default: current directory)."),
    kind: str | None = typer.Option(None, "--kind", help="Symbol kind filter."),
    name_pattern: str | None = typer.Option(None, "--name", help="Symbol name glob or regex."),
    qualified_name_pattern: str | None = typer.Option(
        None,
        "--qualified-name",
        help="Qualified-name glob or regex.",
    ),
    file_pattern: str | None = typer.Option(None, "--file", help="Root-relative file glob/regex."),
    language: str | None = typer.Option(None, "--language", help="Indexed language filter."),
    edge_kind: str | None = typer.Option(
        None,
        "--edge-kind",
        help="Edge kind filter, or comma-separated kinds such as reads,writes.",
    ),
    direction: str = typer.Option("both", "--direction", help="incoming | outgoing | both."),
    min_degree: int | None = typer.Option(None, "--min-degree", help="Minimum total degree."),
    max_degree: int | None = typer.Option(None, "--max-degree", help="Maximum total degree."),
    min_in_degree: int | None = typer.Option(None, "--min-in-degree", help="Minimum inbound degree."),
    max_in_degree: int | None = typer.Option(None, "--max-in-degree", help="Maximum inbound degree."),
    min_out_degree: int | None = typer.Option(None, "--min-out-degree", help="Minimum outbound degree."),
    max_out_degree: int | None = typer.Option(None, "--max-out-degree", help="Maximum outbound degree."),
    confidence: str | None = typer.Option(None, "--confidence", help="EXTRACTED | INFERRED | AMBIGUOUS."),
    synthesized: str = typer.Option("any", "--synthesized", help="any | parser | synthesized."),
    cluster_id: int | None = typer.Option(None, "--cluster-id", help="Cluster id filter."),
    visibility: str | None = typer.Option(None, "--visibility", help="Visibility filter."),
    is_exported: bool | None = typer.Option(None, "--exported/--not-exported", help="Export filter."),
    test_scope: str = typer.Option("any", "--test-scope", help="any | test | source."),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help="dead-code | hotspot | field-access | inheritance | isolates.",
    ),
    sort: str = typer.Option("default", "--sort", help="default | in-degree | out-degree | total-degree | name | file | line."),
    limit: int = typer.Option(20, "--limit", help="Maximum results to return."),
    offset: int = typer.Option(0, "--offset", help="Pagination offset."),
    include_preview: bool = typer.Option(False, "--preview", help="Include bounded one-hop previews."),
    preview_limit: int = typer.Option(3, "--preview-limit", help="Maximum preview edges per result."),
    regex: bool = typer.Option(False, "--regex", help="Interpret patterns as regular expressions."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print matching locations, one per line."),
) -> None:
    """Find symbols by graph shape: dead-code suspects, hotspots, field access, inheritance."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_graph_search(
            conn,
            project_root,
            kind=kind,
            name_pattern=name_pattern,
            qualified_name_pattern=qualified_name_pattern,
            file_pattern=file_pattern,
            language=language,
            edge_kind=edge_kind,
            direction=direction,
            min_degree=min_degree,
            max_degree=max_degree,
            min_in_degree=min_in_degree,
            max_in_degree=max_in_degree,
            min_out_degree=min_out_degree,
            max_out_degree=max_out_degree,
            confidence=confidence,
            synthesized=synthesized,
            cluster_id=cluster_id,
            visibility=visibility,
            is_exported=is_exported,
            test_scope=test_scope,
            preset=preset,
            sort=sort,
            limit=limit,
            offset=offset,
            include_preview=include_preview,
            preview_limit=preview_limit,
            regex=regex,
        )
    finally:
        conn.close()

    if isinstance(result, dict) and result.get("error"):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        for item in result.get("items", []):
            console.print(f"{item['file']}:{item['line']}:{item['symbol']}")
        return
    _render_graph_search(result)
