"""CLI read commands that complete the terminal-only surface: `seam query`,
`seam search`, `seam context`.

These three were previously MCP-only. They reuse the same transport-agnostic
handlers that power the MCP tools (handle_seam_query / _search / _context in
seam/server/tools.py), which query the SQLite index directly — so they work with
NO MCP server running. Defined here (not main.py) because main.py is already large;
registered onto the app in main.py.
"""

import sqlite3
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

import seam.config as config
from seam.cli.output import (
    check_mutual_exclusion,
    emit_json,
    emit_json_error,
    print_quiet,
)
from seam.indexer.db import connect
from seam.server.tools import handle_seam_context, handle_seam_query, handle_seam_search

console = Console()

_QUERY_LIMIT_DEFAULT = 10
_SEARCH_LIMIT_DEFAULT = 20


def _open_index(path: str, db_dir: str, json_: bool) -> tuple[sqlite3.Connection, Path]:
    """Resolve the index, guard NO_INDEX/DB_ERROR, and return (conn, project_root).

    Mirrors the resolution + error envelope used by every other read command so
    the CLI contract (NO_INDEX / DB_ERROR codes, --json on stdout) stays uniform.
    """
    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        if json_:
            emit_json_error("NO_INDEX", "No index found. Run 'seam init' first.")
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    return conn, project_root


def _emit_error_dict(result: dict, json_: bool) -> NoReturn:
    """A handler returned an {error, message} dict (blank/invalid input) → exit 1 (always raises)."""
    if json_:
        emit_json_error(result["error"], result.get("message", ""))
    console.print(f"[red]Error:[/red] {result.get('message', result['error'])}")
    raise typer.Exit(code=1)


def _render_hits(rows: list[dict], title: str) -> None:
    """Rich table for query/search results (symbol · location · score)."""
    if not rows:
        console.print("[dim]No matches.[/dim]")
        return
    table = Table(title=title)
    table.add_column("symbol", style="bold")
    table.add_column("location", style="dim")
    table.add_column("score", justify="right")
    for row in rows:
        loc = f"{row.get('file', '?')}:{row.get('line', '?')}"
        table.add_row(str(row.get("symbol", "")), loc, f"{row.get('score', 0):.1f}")
    console.print(table)


def search_command(
    text: str = typer.Argument(..., help="Keywords to full-text search (FTS5)."),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    limit: int = typer.Option(_SEARCH_LIMIT_DEFAULT, "--limit", help="Max results."),
    no_semantic: bool = typer.Option(
        False,
        "--no-semantic",
        help=(
            "Force keyword-only FTS5 search, even when embeddings exist. "
            "Useful for comparing keyword vs. hybrid results."
        ),
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare symbol names, one per line."),
) -> None:
    """Full-text search over indexed symbol names, docstrings, and signatures (no MCP needed).

    By default auto-uses semantic hybrid search when embeddings exist (SEAM_SEMANTIC=on).
    Use --no-semantic to force keyword-only FTS5, regardless of whether embeddings are present.
    """
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        # --no-semantic: pass semantic=False to bypass the hybrid path without mutating
        # global config. This is safe for both single-threaded CLI use and any future
        # concurrent context. (DRIFT-1 fix: no more module-attribute patching.)
        result = handle_seam_search(
            conn, text, project_root, limit=limit, semantic=not no_semantic
        )
    finally:
        conn.close()

    # search/query return a list of hits OR an {error,message} dict — any dict is an error.
    if isinstance(result, dict):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        print_quiet(result, field="symbol")
        return
    _render_hits(result, f"search: {text}")


def query_command(
    concept: str = typer.Argument(..., help="Concept/keywords (hybrid FTS + 1-hop graph)."),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    limit: int = typer.Option(_QUERY_LIMIT_DEFAULT, "--limit", help="Max results."),
    no_semantic: bool = typer.Option(
        False,
        "--no-semantic",
        help=(
            "Force keyword-only FTS5 search, even when embeddings exist. "
            "Useful for comparing keyword vs. hybrid results."
        ),
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare symbol names, one per line."),
) -> None:
    """Hybrid search (FTS5 + 1-hop graph expansion) for code related to a concept (no MCP needed)."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        result = handle_seam_query(
            conn, concept, project_root, limit=limit, semantic=not no_semantic
        )
    finally:
        conn.close()

    # search/query return a list of hits OR an {error,message} dict — any dict is an error.
    if isinstance(result, dict):
        _emit_error_dict(result, json_)
    if json_:
        emit_json(result)
        return
    if quiet:
        print_quiet(result, field="symbol")
        return
    _render_hits(result, f"query: {concept}")


def context_command(
    symbol: str = typer.Argument(..., help="Symbol name to get the 360-degree view for."),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)."),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory."),
    lean: bool = typer.Option(
        False,
        "--lean",
        help="Omit heavy enrichment (decorators, is_exported, visibility, qualified_name).",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print key facts, one per line."),
) -> None:
    """360-degree view of a symbol: callers, callees, location, cluster, enrichment (no MCP needed)."""
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    conn, project_root = _open_index(path, db_dir, json_)
    try:
        # --lean maps to verbose=False — byte-identical to the MCP tool with verbose=False.
        result = handle_seam_context(conn, symbol, project_root, verbose=not lean)
    finally:
        conn.close()

    if isinstance(result, dict) and result.get("error"):
        _emit_error_dict(result, json_)

    # Not found: success envelope {found:false} (NOT an error code) — mirrors the MCP
    # seam_context contract so a missing symbol is a valid, parseable answer.
    if result is None:
        if json_:
            emit_json({"found": False, "symbol": symbol})
            return
        console.print(
            f"[yellow]Symbol '{symbol}' not found in the index[/yellow]"
            " — check the name or run 'seam init'."
        )
        return

    if json_:
        emit_json(result)
        return
    if quiet:
        # Terse: the most useful identity facts for a human scanning output.
        for key in ("symbol", "kind", "file", "line", "signature"):
            if result.get(key) is not None:
                console.print(f"{key}: {result[key]}")
        return
    _render_context(result)


def _render_context(ctx: dict) -> None:
    """Rich rendering of a single seam_context result."""
    console.print(f"[bold]{ctx.get('symbol')}[/bold]  [dim]{ctx.get('kind')}[/dim]")
    console.print(f"  [dim]{ctx.get('file')}:{ctx.get('line')}[/dim]")
    if ctx.get("signature"):
        console.print(f"  signature: {ctx['signature']}")
    if ctx.get("docstring"):
        console.print(f"  doc: {ctx['docstring']}")
    if ctx.get("cluster_label"):
        console.print(f"  cluster: {ctx['cluster_label']}")
    console.print(f"  callers ({len(ctx.get('callers', []))}): {', '.join(ctx.get('callers', [])) or '—'}")
    console.print(f"  callees ({len(ctx.get('callees', []))}): {', '.join(ctx.get('callees', [])) or '—'}")
