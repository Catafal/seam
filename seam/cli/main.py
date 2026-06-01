"""Seam CLI entry point — `seam init`, `seam start`, `seam status`.

Commands
--------
init   — Index a project directory into .seam/seam.db.
status — Show index stats and watcher state.
start  — Start the MCP server (stdio) + file watcher in the background.
"""

import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

import seam.config as config
from seam.analysis.flows import callees as flows_callees
from seam.analysis.flows import callers as flows_callers
from seam.analysis.flows import trace as flows_trace
from seam.analysis.impact import (
    TIER_LIKELY_AFFECTED,
    TIER_MAY_NEED_TESTING,
    TIER_WILL_BREAK,
    impact,
)
from seam.indexer.db import connect, init_db
from seam.indexer.pipeline import index_one_file, walk_project
from seam.server.mcp import create_server

app = typer.Typer(
    name="seam",
    help="Local code intelligence MCP server for AI agents.",
    add_completion=False,
)

console = Console()


def _watcher_is_alive(pid_file: Path) -> int | None:
    """Return the PID if a live watcher process is recorded, else None.

    Reads the PID file and probes the process with os.kill(pid, 0). A stale
    PID file (process gone) returns None so callers can safely overwrite it.
    """
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, doesn't actually signal
    except OSError:
        return None  # no such process (or not ours) — treat as dead
    return pid


# ── Commands ──────────────────────────────────────────────────────────────────


@app.command()
def init(
    path: str = typer.Argument(".", help="Project root to index (default: current directory)"),
    db_dir: str = typer.Option(
        "",
        "--db-dir",
        help="Override DB directory (used in tests; default: same as project root)",
    ),
) -> None:
    """Index the project into .seam/seam.db.

    Walks the project root, skips dot-dirs and common build/cache dirs,
    selects files by extension (SEAM_LANGUAGE_MAP), skips files > SEAM_MAX_FILE_BYTES,
    and writes all symbols + edges into .seam/seam.db.
    """
    start_ts = time.monotonic()
    project_root = Path(path).resolve()

    if not project_root.is_dir():
        console.print(f"[red]Error:[/red] '{project_root}' is not a directory.")
        raise typer.Exit(code=1)

    # Determine DB root: --db-dir overrides for test isolation
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect files to index
    files = walk_project(project_root)

    total_symbols = 0
    total_edges = 0
    indexed_files = 0
    skipped_files = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Initialising database...", total=None)

        conn = init_db(db_path)
        try:
            progress.update(task, description=f"Indexing {len(files)} files...")
            for file_path in files:
                progress.update(task, description=f"Indexing {file_path.name}...")
                # None = skipped (unsupported/binary/error); (s, e) = indexed,
                # even if (0, 0) for a valid-but-empty file.
                result = index_one_file(conn, file_path)
                if result is None:
                    skipped_files += 1
                    continue
                indexed_files += 1
                total_symbols += result[0]
                total_edges += result[1]
        finally:
            conn.close()

    elapsed = time.monotonic() - start_ts

    # Summary table
    table = Table(title="seam init — complete", show_header=False, box=None)
    table.add_column("key", style="bold cyan", width=16)
    table.add_column("value")
    table.add_row("root", str(project_root))
    table.add_row("db", str(db_path))
    table.add_row("files found", str(len(files)))
    table.add_row("files indexed", str(indexed_files))
    table.add_row("files skipped", str(skipped_files))
    table.add_row("symbols", str(total_symbols))
    table.add_row("edges", str(total_edges))
    table.add_row("elapsed", f"{elapsed:.2f}s")
    console.print(table)
    if skipped_files:
        console.print(
            f"[dim]{skipped_files} file(s) skipped (binary/oversize/parse error). "
            f"Set SEAM_LOG_LEVEL=DEBUG to see which.[/dim]"
        )


@app.command()
def status(
    path: str = typer.Argument(".", help="Project root whose index to inspect (default: current directory)"),
    db_dir: str = typer.Option(
        "",
        "--db-dir",
        help="Override DB directory (default: same as project root)",
    ),
) -> None:
    """Show index statistics and watcher status.

    Reads the DB at <project>/.seam/seam.db and prints:
    - file / symbol / edge counts
    - last indexed_at timestamp
    - watcher PID (if a live watcher is recorded)
    - freshness: newest DB mtime vs newest on-disk file mtime
    """
    # Mirror `init`: DB lives under the project root unless --db-dir overrides.
    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        console.print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first to create the index."
        )
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # Most recent indexed_at across all files
        last_indexed_row = conn.execute("SELECT MAX(indexed_at) FROM files").fetchone()[0]
        last_indexed_str = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_indexed_row))
            if last_indexed_row is not None
            else "never"
        )

        # Freshness: compare newest DB mtime vs newest on-disk mtime for indexed paths
        db_newest_mtime: float = conn.execute("SELECT MAX(mtime) FROM files").fetchone()[0] or 0.0

        # Check on-disk mtimes for files we know about
        disk_newest_mtime = 0.0
        paths_rows = conn.execute("SELECT path FROM files").fetchall()
        for row in paths_rows:
            p = Path(row["path"])
            try:
                mtime = p.stat().st_mtime
                if mtime > disk_newest_mtime:
                    disk_newest_mtime = mtime
            except OSError:
                pass  # file deleted — stale entry

        # Heuristic: only detects modified/added tracked files. Deletions and
        # brand-new untracked files are not reflected here (the live watcher
        # handles those in real time). See lessons.md.
        freshness = "fresh" if disk_newest_mtime <= db_newest_mtime else "stale"

    finally:
        conn.close()

    # Watcher PID — only report it if the process is actually alive.
    pid_file = db_path.parent / "watcher.pid"
    alive_pid = _watcher_is_alive(pid_file)
    watcher_status = f"PID {alive_pid}" if alive_pid is not None else "not running"

    # Print summary table
    table = Table(title="seam status", show_header=False, box=None)
    table.add_column("key", style="bold cyan", width=16)
    table.add_column("value")
    table.add_row("files", str(file_count))
    table.add_row("symbols", str(symbol_count))
    table.add_row("edges", str(edge_count))
    table.add_row("last indexed", last_indexed_str)
    table.add_row("watcher", watcher_status)
    table.add_row("freshness", freshness)
    console.print(table)


@app.command()
def start(
    path: str = typer.Argument(".", help="Project root to watch (default: current directory)"),
    db_dir: str = typer.Option(
        "",
        "--db-dir",
        help="Override DB directory (default: same as project root)",
    ),
) -> None:
    """Start the MCP server (stdio) and file watcher daemon.

    Watcher runs in a background subprocess; MCP server runs in the foreground
    using stdio transport so the calling process (e.g. Claude Desktop) can
    communicate with it directly.

    The watcher subprocess writes its own .seam/watcher.pid (single writer).
    The MCP server does not have a separate PID file — it occupies the foreground.
    """
    project_root = Path(path).resolve()

    if not project_root.is_dir():
        console.print(f"[red]Error:[/red] '{project_root}' is not a directory.")
        raise typer.Exit(code=1)

    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        Console(stderr=True).print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first."
        )
        raise typer.Exit(code=1)

    # Refuse to spawn a second watcher if a live one is already recorded —
    # two writers on one DB is exactly the corruption we just designed out.
    pid_file = db_path.parent / "watcher.pid"
    existing = _watcher_is_alive(pid_file)
    if existing is not None:
        Console(stderr=True).print(
            f"[yellow]A watcher is already running (PID {existing}).[/yellow] Not starting another."
        )
        raise typer.Exit(code=1)

    logging.basicConfig(level=getattr(logging, config.SEAM_LOG_LEVEL, logging.INFO))

    # ── Launch watcher as a clean module entry point ──────────────────────────
    # `python -m seam.watcher <db> <root>` passes paths via argv (NOT interpolated
    # into a -c source string), so paths with spaces/quotes/backslashes are safe.
    # The subprocess writes its own PID file via SeamWatcher.start().
    watcher_proc = subprocess.Popen(  # noqa: S603 — controlled internal command, no shell
        [sys.executable, "-m", "seam.watcher", str(db_path), str(project_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # ── Open DB connection (read path) for the MCP server ─────────────────────
    conn = connect(db_path)

    # Single idempotent teardown used by both the signal path and normal exit.
    _torn_down = False

    def _teardown() -> None:
        nonlocal _torn_down
        if _torn_down:
            return
        _torn_down = True
        watcher_proc.terminate()
        conn.close()

    def _on_signal(signum: int, frame: object) -> None:  # noqa: ARG001
        _teardown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # ── Run MCP server in the foreground (stdio) until the client disconnects ──
    try:
        server = create_server(conn, project_root)
        server.run(transport="stdio")
    finally:
        _teardown()


@app.command(name="impact")
def impact_cmd(
    symbol: str = typer.Argument(..., help="Symbol name to analyze (e.g. 'upsert_file', 'UserService.validate')"),
    direction: str = typer.Option("upstream", "--direction", "-d", help="upstream | downstream | both"),
    depth: int = typer.Option(3, "--depth", help="Max hop depth (1-10)"),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
) -> None:
    """Show the blast radius of a symbol — what breaks if you change it.

    Results are grouped into risk tiers:
      WILL_BREAK       (d=1) — direct dependents, definitely affected
      LIKELY_AFFECTED  (d=2) — indirect dependents, probably affected
      MAY_NEED_TESTING (d=3+) — transitive dependents, test to be sure

    Each entry shows the path confidence (EXTRACTED | INFERRED | AMBIGUOUS).
    """
    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    valid_directions = {"upstream", "downstream", "both"}
    if direction not in valid_directions:
        console.print(f"[red]Invalid direction:[/red] {direction!r}. Choose: upstream, downstream, or both.")
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        result = impact(conn, target=symbol, direction=direction, max_depth=depth)
    finally:
        conn.close()

    # Distinguish "symbol not in the index" from "indexed but no dependents".
    if not result.get("found", True):
        console.print(
            f"[yellow]Symbol '{symbol}' not found in the index[/yellow]"
            " — check the name or run 'seam init'."
        )
        return

    # Check if any results exist across all directions and tiers.
    total = sum(
        len(entries)
        for key, tier_group in result.items()
        if isinstance(tier_group, dict) and key not in ("found", "target")
        for entries in tier_group.values()
    )

    if total == 0:
        console.print(f"[dim]No dependents found for [bold]{symbol}[/bold].[/dim]")
        return

    # Print a tiered summary per direction.
    tier_order = [TIER_WILL_BREAK, TIER_LIKELY_AFFECTED, TIER_MAY_NEED_TESTING]
    tier_labels = {
        TIER_WILL_BREAK: "[bold red]WILL BREAK[/bold red]         (d=1)",
        TIER_LIKELY_AFFECTED: "[bold yellow]LIKELY AFFECTED[/bold yellow]   (d=2)",
        TIER_MAY_NEED_TESTING: "[dim]MAY NEED TESTING[/dim]  (d=3+)",
    }

    # Iterate only direction keys (skip metadata keys like "found" and "target").
    for direction_key, tier_group in result.items():
        if direction_key in ("found", "target") or not isinstance(tier_group, dict):
            continue
        console.print(f"\n[bold cyan]Impact ({direction_key})[/bold cyan] of [bold]{symbol}[/bold]:")
        any_in_direction = any(len(entries) > 0 for entries in tier_group.values())
        if not any_in_direction:
            console.print("  [dim]No dependents.[/dim]")
            continue

        for tier in tier_order:
            entries = tier_group.get(tier, [])
            if not entries:
                continue
            console.print(f"\n  {tier_labels[tier]}")
            for entry in entries:
                confidence_color = {
                    "EXTRACTED": "green",
                    "INFERRED": "yellow",
                    "AMBIGUOUS": "red",
                }.get(entry["confidence"], "white")
                console.print(
                    f"    [bold]{entry['name']}[/bold]  "
                    f"[{confidence_color}]{entry['confidence']}[/{confidence_color}]  "
                    f"[dim]d={entry['distance']}[/dim]"
                )


@app.command(name="trace")
def trace_cmd(
    source: str = typer.Argument(..., help="Starting symbol name (e.g. 'init', 'parse_file')"),
    target: str = typer.Argument(..., help="Destination symbol name (e.g. 'upsert_file', 'init_db')"),
    depth: int = typer.Option(10, "--depth", help="Max hop depth (1-10)"),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
) -> None:
    """Trace the call/dependency path from one symbol to another.

    Shows each hop with its edge kind (call | import) and confidence level
    (EXTRACTED | INFERRED | AMBIGUOUS). Confidence colors:
      green  = EXTRACTED (definitely this edge)
      yellow = INFERRED  (heuristic best-guess)
      red    = AMBIGUOUS (name collision — verify manually)

    Also shows direct callers and callees of both source and target.

    Exits with a clear message when no path exists between the symbols.
    """
    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # Clamp depth to [1, 10]
    safe_depth = max(1, min(10, depth))

    try:
        paths = flows_trace(conn, source, target, max_depth=safe_depth)
        callers_src = flows_callers(conn, source)
        callees_src = flows_callees(conn, source)
        callers_tgt = flows_callers(conn, target)
        callees_tgt = flows_callees(conn, target)
    finally:
        conn.close()

    # Confidence -> rich color mapping, used throughout.
    def _conf_color(c: str) -> str:
        return {"EXTRACTED": "green", "INFERRED": "yellow", "AMBIGUOUS": "red"}.get(c, "white")

    # ── Path result ───────────────────────────────────────────────────────────
    if not paths:
        console.print(
            f"\n[yellow]No path found[/yellow] from [bold]{source}[/bold] to [bold]{target}[/bold] "
            f"within {safe_depth} hop(s).\n"
            "[dim]Try increasing --depth or check the symbol names with 'seam search'.[/dim]"
        )
    else:
        # paths[0] is the shortest path (BFS returns a single-element list).
        found_path = paths[0]
        if not found_path:
            # Trivial self-path (source == target).
            console.print(f"\n[dim]{source} == {target} (same symbol, zero hops)[/dim]")
        else:
            console.print(
                f"\n[bold cyan]Path[/bold cyan] from [bold]{source}[/bold] to [bold]{target}[/bold] "
                f"({len(found_path)} hop(s)):"
            )
            # Print each hop as: from → to  (kind)  CONFIDENCE
            arrow = "  →  "
            for hop in found_path:
                color = _conf_color(hop["confidence"])
                console.print(
                    f"  [bold]{hop['from_name']}[/bold]{arrow}[bold]{hop['to_name']}[/bold]"
                    f"  [dim]{hop['kind']}[/dim]"
                    f"  [{color}]{hop['confidence']}[/{color}]"
                )

    # ── One-hop neighborhood ──────────────────────────────────────────────────
    def _print_hops(label: str, hops: list) -> None:  # type: ignore[type-arg]
        if not hops:
            console.print(f"\n  [dim]{label}: none[/dim]")
            return
        console.print(f"\n  [bold]{label}[/bold]:")
        for h in hops:
            color = _conf_color(h["confidence"])
            console.print(
                f"    [bold]{h['name']}[/bold]  [dim]{h['kind']}[/dim]"
                f"  [{color}]{h['confidence']}[/{color}]"
            )

    console.print(f"\n[bold cyan]Neighborhood of [bold]{source}[/bold][/bold cyan]:")
    _print_hops(f"callers({source})", callers_src)
    _print_hops(f"callees({source})", callees_src)

    console.print(f"\n[bold cyan]Neighborhood of [bold]{target}[/bold][/bold cyan]:")
    _print_hops(f"callers({target})", callers_tgt)
    _print_hops(f"callees({target})", callees_tgt)
