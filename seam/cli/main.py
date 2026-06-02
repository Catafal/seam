"""Seam CLI entry point.

Commands (Phase 0)
------------------
init   — Walk the project and index all symbols + edges into .seam/seam.db.
status — Show index stats (file/symbol/edge counts, freshness, watcher PID).
start  — Start the MCP server (stdio foreground) + file watcher (background).

Commands (Phase 1 — code reasoning)
-------------------------------------
impact  — Blast-radius analysis: what breaks if a symbol changes? Grouped by
           risk tier (WILL_BREAK / LIKELY_AFFECTED / MAY_NEED_TESTING).
trace   — Shortest call/dependency path from one symbol to another, with per-hop
           confidence (EXTRACTED | INFERRED | AMBIGUOUS).
changes — Pre-commit risk check: map git diff to changed symbols, run impact
           analysis, report an overall risk level (low/medium/high/critical).

Commands (Phase 1b — semantic comment nodes)
---------------------------------------------
why     — Show WHY/HACK/NOTE/TODO/FIXME comments near a file location or symbol.

Commands (Phase 2 — graph clustering)
---------------------------------------
clusters — List all clusters (or members of one cluster with --id N).
"""

import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

import seam.config as config
from seam.analysis.changes import (
    DEFAULT_BASE_REF,
    VALID_SCOPES,
    AffectedSymbol,
    NotAGitRepoError,
    detect_changes,
)
from seam.analysis.flows import callees as flows_callees
from seam.analysis.flows import callers as flows_callers
from seam.analysis.flows import trace as flows_trace
from seam.analysis.impact import (
    TIER_LIKELY_AFFECTED,
    TIER_MAY_NEED_TESTING,
    TIER_WILL_BREAK,
)
from seam.cli.output import check_mutual_exclusion, emit_json, emit_json_error, print_quiet
from seam.indexer.cluster_index import get_llm_naming_summary, index_clusters
from seam.indexer.db import connect, init_db
from seam.indexer.pipeline import index_one_file, walk_project
from seam.indexer.sync import sync as sync_project
from seam.query.clusters import cluster_members as query_cluster_members
from seam.query.clusters import list_clusters as query_list_clusters
from seam.query.comments import why as comments_why
from seam.server.mcp import create_server
from seam.server.tools import (
    handle_seam_affected,
    handle_seam_changes,
    handle_seam_clusters,
    handle_seam_context_pack,
    handle_seam_impact,
    handle_seam_trace,
    handle_seam_why,
)

app = typer.Typer(
    name="seam",
    help="Local code intelligence MCP server for AI agents.",
    add_completion=False,
)

console = Console()

# Top-level keys in a handle_seam_impact response that are NOT direction groups.
# Every consumer that iterates the impact result to find tier groups (quiet output,
# the total-entry count, Rich rendering) MUST skip these — otherwise risk_summary /
# truncated (which are {direction: {tier: int}} dicts) get treated as direction
# groups and, in the count path, len() is called on an int → TypeError.
_IMPACT_META_KEYS: frozenset[str] = frozenset(
    {"found", "target", "hidden_tests", "risk_summary", "truncated"}
)


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
    total_clusters = 0
    llm_naming_summary: str | None = None

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

            # Phase 2: Clustering post-pass (whole-graph, runs after all files indexed).
            # WHY: Clustering must see the complete graph (all files), not per-file fragments.
            # This is intentionally AFTER the indexing loop — not inside index_one_file.
            progress.update(task, description="Computing graph clusters...")
            total_clusters = index_clusters(
                conn,
                naming_mode=config.SEAM_CLUSTER_NAMING,
                llm_api_key=config.SEAM_LLM_API_KEY,
                llm_model=config.SEAM_LLM_MODEL,
                min_size=config.SEAM_CLUSTER_MIN_SIZE,
            )

            # Issue #8: LLM naming summary — read after clustering completes.
            # Only relevant when LLM naming was requested.
            if config.SEAM_CLUSTER_NAMING == "llm" and total_clusters > 0:
                llm_naming_summary = get_llm_naming_summary(conn)
        finally:
            conn.close()

    # Issue #7: index_clusters returns -1 on error (not 0) to distinguish failure
    # from "genuinely zero clusters." Display a visible yellow warning in that case.
    clustering_failed = total_clusters < 0
    display_clusters = str(total_clusters) if total_clusters >= 0 else "failed"

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
    table.add_row("clusters", display_clusters)
    table.add_row("elapsed", f"{elapsed:.2f}s")
    console.print(table)

    # Issue #7: Visible yellow warning when clustering failed.
    # Only shown when we indexed symbols — "0 clusters" on an empty repo is fine.
    if clustering_failed and total_symbols > 0:
        console.print(
            "[yellow]clusters: failed[/yellow] "
            "[dim](run with SEAM_LOG_LEVEL=DEBUG to see the error)[/dim]"
        )

    # Issue #8: LLM naming summary line.
    if llm_naming_summary:
        console.print(f"[dim]{llm_naming_summary}[/dim]")

    if skipped_files:
        console.print(
            f"[dim]{skipped_files} file(s) skipped (binary/oversize/parse error). "
            f"Set SEAM_LOG_LEVEL=DEBUG to see which.[/dim]"
        )


@app.command()
def status(
    path: str = typer.Argument(
        ".", help="Project root whose index to inspect (default: current directory)"
    ),
    db_dir: str = typer.Option(
        "",
        "--db-dir",
        help="Override DB directory (default: same as project root)",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare values only (one per line)."),
) -> None:
    """Show index statistics and watcher status.

    Reads the DB at <project>/.seam/seam.db and prints:
    - file / symbol / edge counts
    - last indexed_at timestamp
    - watcher PID (if a live watcher is recorded)
    - freshness: newest DB mtime vs newest on-disk file mtime
    """
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    # Mirror `init`: DB lives under the project root unless --db-dir overrides.
    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        if json_:
            emit_json_error(
                "NO_INDEX", "No index found. Run 'seam init' first to create the index."
            )
        console.print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first to create the index."
        )
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # Cluster count (Phase 2). Guard for pre-v4 indexes (no clusters table).
        try:
            cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        except Exception:
            cluster_count = 0

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

    # ── JSON mode — build stats dict inline (no handler exists for status) ────
    if json_:
        stats = {
            "files": file_count,
            "symbols": symbol_count,
            "edges": edge_count,
            "clusters": cluster_count,
            "last_indexed": last_indexed_str,
            "watcher": watcher_status,
            "freshness": freshness,
        }
        emit_json(stats)
        return

    # ── Quiet mode — print freshness (the single load-bearing field for CI gating)
    if quiet:
        sys.stdout.write(freshness + "\n")
        return

    # ── Rich (default) mode — existing rendering, unchanged ──────────────────

    # Print summary table
    table = Table(title="seam status", show_header=False, box=None)
    table.add_column("key", style="bold cyan", width=16)
    table.add_column("value")
    table.add_row("files", str(file_count))
    table.add_row("symbols", str(symbol_count))
    table.add_row("edges", str(edge_count))
    table.add_row("clusters", str(cluster_count))
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
        Console(stderr=True).print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
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
    symbol: str = typer.Argument(
        ..., help="Symbol name to analyze (e.g. 'upsert_file', 'UserService.validate')"
    ),
    direction: str = typer.Option(
        "upstream", "--direction", "-d", help="upstream | downstream | both"
    ),
    depth: int = typer.Option(3, "--depth", help="Max hop depth (1-10)"),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    production_only: bool = typer.Option(
        False,
        "--production-only",
        help="Filter out test-file dependents; show only production blast radius.",
    ),
    lean: bool = typer.Option(
        False,
        "--lean",
        help=(
            "Omit heavy enrichment fields (resolved_by, best_candidate) from every tier entry. "
            "signature and core fields are always kept. Identical to verbose=false in MCP."
        ),
    ),
    limit: int = typer.Option(
        config.SEAM_IMPACT_MAX_RESULTS,
        "--limit",
        help=(
            "Per-tier entry cap. Default: SEAM_IMPACT_MAX_RESULTS (25). "
            "Set to 0 for unlimited — returns the full transitive blast radius. "
            "Identical to the limit parameter in the MCP tool."
        ),
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare values only (one per line)."),
) -> None:
    """Show the blast radius of a symbol — what breaks if you change it.

    Results are grouped into risk tiers:
      WILL_BREAK       (d=1) — direct dependents, definitely affected
      LIKELY_AFFECTED  (d=2) — indirect dependents, probably affected
      MAY_NEED_TESTING (d=3+) — transitive dependents, test to be sure

    Each entry shows the path confidence (EXTRACTED | INFERRED | AMBIGUOUS).
    Test-file dependents are marked [test] in the output. Use --production-only
    to filter them out and see only the production blast radius.
    """
    # WHY: check mutual exclusion before any DB work so the error is immediate.
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        if json_:
            emit_json_error("NO_INDEX", "No index found. Run 'seam init' first.")
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    valid_directions = {"upstream", "downstream", "both"}
    if direction not in valid_directions:
        if json_:
            emit_json_error(
                "INVALID_INPUT",
                f"direction must be one of: upstream, downstream, both; got {direction!r}",
            )
        console.print(
            f"[red]Invalid direction:[/red] {direction!r}. Choose: upstream, downstream, or both."
        )
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # include_tests=False when --production-only is set; True (default) otherwise.
    include_tests = not production_only

    # --lean sets verbose=False; output becomes byte-identical to the MCP tool
    # called with verbose=False — heavy fields absent from every tier entry.
    verbose = not lean

    try:
        # WHY: ALL three modes (--json, --quiet, Rich) route through handle_seam_impact
        # so the --limit cap, --lean strip, risk_summary, and truncated counts apply
        # uniformly. The Rich path previously called impact() directly and so silently
        # ignored --limit and --lean (a confirmed parity bug). One handler = one source
        # of truth; Rich now renders the same capped result --json returns.
        result = handle_seam_impact(
            conn,
            target=symbol,
            root=project_root,
            direction=direction,
            max_depth=depth,
            include_tests=include_tests,
            verbose=verbose,
            limit=limit,
        )
    finally:
        conn.close()

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(result)
        return

    # ── Quiet mode — print one name per dependent entry ───────────────────────
    if quiet:
        for dir_key, tier_group in result.items():
            # Skip metadata keys (risk_summary/truncated are dicts too — without this
            # guard they'd be walked as direction groups and emit garbage).
            if dir_key in _IMPACT_META_KEYS or not isinstance(tier_group, dict):
                continue
            for entries in tier_group.values():
                for entry in entries:
                    print_quiet(entry, field="name")
        # Signal truncation on stderr so stdout stays a pure bare-name list
        # (a `seam impact X --quiet | wc -l` pipeline must not see this line).
        truncated = result.get("truncated")
        if truncated:
            total_omitted = sum(n for tiers in truncated.values() for n in tiers.values())
            if total_omitted > 0:
                sys.stderr.write(
                    f"# {total_omitted} more entr(ies) truncated by --limit; "
                    "use --limit 0 for the full set\n"
                )
        return

    # ── Rich (default) mode — existing rendering, unchanged ──────────────────

    # Distinguish "symbol not in the index" from "indexed but no dependents".
    if not result.get("found", True):
        console.print(
            f"[yellow]Symbol '{symbol}' not found in the index[/yellow]"
            " — check the name or run 'seam init'."
        )
        return

    # Check if any results exist across all directions and tiers.
    # Skip metadata keys: risk_summary/truncated are {tier: int} dicts, so without
    # the guard `len(entries)` would be called on an int and raise TypeError.
    total = sum(
        len(entries)
        for key, tier_group in result.items()
        if isinstance(tier_group, dict) and key not in _IMPACT_META_KEYS
        for entries in tier_group.values()
    )

    # hidden_tests is only present when --production-only filtered test dependents out.
    hidden_tests = result.get("hidden_tests", 0)

    if total == 0:
        if hidden_tests:
            # Critical distinction: this symbol is NOT dead code — it has test
            # dependents that --production-only hid. Saying "no dependents" here
            # would be a dangerous false-safe (an agent might delete/rewrite it).
            console.print(
                f"[yellow]No production dependents for [bold]{symbol}[/bold][/yellow] — "
                f"but {hidden_tests} test dependent(s) hidden by --production-only. "
                "Re-run without the flag to see them."
            )
        else:
            console.print(f"[dim]No dependents found for [bold]{symbol}[/bold].[/dim]")
        return

    # Print a tiered summary per direction.
    tier_order = [TIER_WILL_BREAK, TIER_LIKELY_AFFECTED, TIER_MAY_NEED_TESTING]
    tier_labels = {
        TIER_WILL_BREAK: "[bold red]WILL BREAK[/bold red]         (d=1)",
        TIER_LIKELY_AFFECTED: "[bold yellow]LIKELY AFFECTED[/bold yellow]   (d=2)",
        TIER_MAY_NEED_TESTING: "[dim]MAY NEED TESTING[/dim]  (d=3+)",
    }

    # Iterate only direction keys (skip metadata keys: found/target/hidden_tests
    # and the Phase 8 risk_summary/truncated dicts).
    for direction_key, tier_group in result.items():
        if direction_key in _IMPACT_META_KEYS or not isinstance(tier_group, dict):
            continue
        console.print(
            f"\n[bold cyan]Impact ({direction_key})[/bold cyan] of [bold]{symbol}[/bold]:"
        )
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
                # Show [test] marker when tests are included and this entry is from a test file.
                test_marker = " [dim][test][/dim]" if entry.get("is_test") else ""
                # Phase 5: surface the proximity best_candidate on AMBIGUOUS entries
                # (story 6) so a human sees the likeliest declaration for a homonym.
                # best_candidate is ALREADY relativized by handle_seam_impact — do not
                # re-relativize (that would mangle the already-relative path). Absent in
                # --lean mode, so use .get().
                best = entry.get("best_candidate")
                best_marker = f" [dim](best: {best})[/dim]" if best else ""
                console.print(
                    f"    [bold]{entry['name']}[/bold]  "
                    f"[{confidence_color}]{entry['confidence']}[/{confidence_color}]  "
                    f"[dim]d={entry['distance']}[/dim]{test_marker}{best_marker}"
                )

        # Per-direction truncation footer: when --limit capped this direction's tiers,
        # tell the user how many entries were omitted and how to see them all. Without
        # this the capped Rich output looks complete (the silent-cap parity bug).
        dir_truncated = result.get("truncated", {}).get(direction_key, {})
        omitted = sum(dir_truncated.values())
        if omitted > 0:
            console.print(
                f"\n  [dim]… {omitted} more entr(ies) truncated by --limit "
                f"(showing {limit} per tier; use --limit 0 for the full blast radius).[/dim]"
            )

    # Footer: when production dependents were shown but tests were also hidden,
    # note the hidden count so the blast radius is not silently under-reported.
    if hidden_tests:
        console.print(
            f"\n[dim]({hidden_tests} test dependent(s) hidden by --production-only)[/dim]"
        )


@app.command(name="trace")
def trace_cmd(
    source: str = typer.Argument(..., help="Starting symbol name (e.g. 'init', 'parse_file')"),
    target: str = typer.Argument(
        ..., help="Destination symbol name (e.g. 'upsert_file', 'init_db')"
    ),
    depth: int = typer.Option(10, "--depth", help="Max hop depth (1-10)"),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    lean: bool = typer.Option(
        False,
        "--lean",
        help=(
            "Omit heavy enrichment fields (resolved_by, best_candidate) from every hop. "
            "Identical to verbose=false in MCP."
        ),
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare values only (one per line)."),
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
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

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

    # Clamp depth to [1, 10]
    safe_depth = max(1, min(10, depth))

    # --lean sets verbose=False; output becomes byte-identical to the MCP tool
    # called with verbose=False — heavy fields absent from every hop.
    verbose = not lean

    try:
        # WHY: reuse handle_seam_trace for --json/--quiet to ensure MCP/CLI parity.
        if json_ or quiet:
            result = handle_seam_trace(
                conn, source=source, target=target, root=project_root,
                max_depth=safe_depth, verbose=verbose,
            )
        else:
            # Thread project_root as repo_root for Phase 5 import-promotion so
            # imported homonym bindings resolve as EXTRACTED rather than AMBIGUOUS.
            paths = flows_trace(conn, source, target, max_depth=safe_depth, repo_root=project_root)
            callers_src = flows_callers(conn, source, repo_root=project_root)
            callees_src = flows_callees(conn, source, repo_root=project_root)
            callers_tgt = flows_callers(conn, target, repo_root=project_root)
            callees_tgt = flows_callees(conn, target, repo_root=project_root)
    finally:
        conn.close()

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(result)
        return

    # ── Quiet mode — print hop names from the first path ─────────────────────
    if quiet:
        if result.get("found") and result.get("paths"):
            for hop in result["paths"][0]:
                sys.stdout.write(hop["from_name"] + "\n")
            # Print the final destination
            if result["paths"][0]:
                sys.stdout.write(result["paths"][0][-1]["to_name"] + "\n")
        return

    # ── Rich (default) mode ───────────────────────────────────────────────────

    # Confidence -> rich color mapping, used throughout.
    def _conf_color(c: str) -> str:
        return {"EXTRACTED": "green", "INFERRED": "yellow", "AMBIGUOUS": "red"}.get(c, "white")

    # Phase 5: best_candidate is the proximity-ranked most-likely target attached to
    # AMBIGUOUS hops (story 6). Rendered relative to the project root so a human
    # scanning a homonym sees the likeliest declaration alongside the ambiguity.
    def _best_suffix(d: Mapping[str, Any]) -> str:
        best = d.get("best_candidate")
        if not best:
            return ""
        return f" [dim](best: {os.path.relpath(best, project_root)})[/dim]"

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
            arrow = "  →  "
            for hop in found_path:
                color = _conf_color(hop["confidence"])
                # Phase 5: append resolved_by when present for provenance visibility.
                # Format: "EXTRACTED [via import]" so users can spot promoted hops.
                rby = hop.get("resolved_by")
                rby_suffix = f" [dim][via {rby}][/dim]" if rby else ""
                console.print(
                    f"  [bold]{hop['from_name']}[/bold]{arrow}[bold]{hop['to_name']}[/bold]"
                    f"  [dim]{hop['kind']}[/dim]"
                    f"  [{color}]{hop['confidence']}[/{color}]{rby_suffix}{_best_suffix(hop)}"
                )

    # ── One-hop neighborhood ──────────────────────────────────────────────────
    def _print_hops(label: str, hops: list) -> None:  # type: ignore[type-arg]
        if not hops:
            console.print(f"\n  [dim]{label}: none[/dim]")
            return
        console.print(f"\n  [bold]{label}[/bold]:")
        for h in hops:
            color = _conf_color(h["confidence"])
            # Phase 5: show resolved_by in neighbourhood hops too when present.
            rby = h.get("resolved_by")
            rby_suffix = f" [dim][via {rby}][/dim]" if rby else ""
            console.print(
                f"    [bold]{h['name']}[/bold]  [dim]{h['kind']}[/dim]"
                f"  [{color}]{h['confidence']}[/{color}]{rby_suffix}{_best_suffix(h)}"
            )

    console.print(f"\n[bold cyan]Neighborhood of [bold]{source}[/bold][/bold cyan]:")
    _print_hops(f"callers({source})", callers_src)
    _print_hops(f"callees({source})", callees_src)

    console.print(f"\n[bold cyan]Neighborhood of [bold]{target}[/bold][/bold cyan]:")
    _print_hops(f"callers({target})", callers_tgt)
    _print_hops(f"callees({target})", callees_tgt)


@app.command(name="changes")
def changes_cmd(
    base: str = typer.Option(
        DEFAULT_BASE_REF,
        "--base",
        "-b",
        help=f"Base git ref for branch scope (default: {DEFAULT_BASE_REF})",
    ),
    scope: str = typer.Option(
        "working",
        "--scope",
        "-s",
        help="working | staged | branch",
    ),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare values only (one per line)."),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help=(
            "Read a newline-delimited list of file paths from stdin. "
            "Narrows changed_symbols and new_files to only those files. "
            "affected and risk_level intentionally reflect the FULL git diff "
            "(conservative — never under-reports risk)."
        ),
    ),
) -> None:
    """Pre-commit risk check — show what your changes break.

    Maps git diff to the symbols it touched, runs impact analysis, and prints
    an overall risk level (low / medium / high / critical).

    Scope:
      working — unstaged changes (git diff)
      staged  — staged changes (git diff --cached)
      branch  — all changes on this branch vs base ref (git diff <base>...HEAD)

    Use --stdin to restrict the analysis to a precomputed file list, e.g.:
      git diff --name-only | seam changes --stdin --json
    """
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    # Validate scope early for a helpful message.
    if scope not in VALID_SCOPES:
        if json_:
            emit_json_error(
                "INVALID_INPUT", f"scope must be one of {sorted(VALID_SCOPES)}; got {scope!r}"
            )
        console.print(
            f"[red]Invalid scope:[/red] {scope!r}. "
            f"Choose one of: {', '.join(sorted(VALID_SCOPES))}."
        )
        raise typer.Exit(code=1)

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

    # Read stdin before dispatching to detect_changes/handle_seam_changes because stdin
    # is a one-shot stream — it must be drained before the output-mode branch so the
    # resolved file set is available to both the json/quiet and Rich paths.
    # Paths are resolved here so they match the absolute paths stored in the DB.
    stdin_files: set[str] | None = None
    if stdin:
        raw_lines = sys.stdin.read().splitlines()
        resolved = []
        for line in raw_lines:
            line = line.strip()
            if line:
                resolved.append(
                    str((project_root / line).resolve())
                    if not Path(line).is_absolute()
                    else str(Path(line).resolve())
                )
        stdin_files = set(resolved)

    # WHY `Any`: handle_seam_changes returns dict[str,Any]; detect_changes returns
    # ChangeReport (a TypedDict). Both are accessed with the same string keys at
    # runtime, but mypy cannot unify them — `Any` is the honest annotation here.
    report: Any
    try:
        # WHY: reuse handle_seam_changes for --json/--quiet (MCP/CLI parity).
        if json_ or quiet:
            report = handle_seam_changes(conn, root=project_root, base_ref=base, scope=scope)
        else:
            try:
                report = detect_changes(conn, base_ref=base, scope=scope, repo_root=project_root)
            except NotAGitRepoError as exc:
                console.print(f"[red]Not a git repository:[/red] {exc}")
                raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    # ── Apply stdin file filter (when --stdin was given) ─────────────────────
    # WHY filter after report: detect_changes always runs the full git diff; we then
    # narrow changed_symbols and new_files to the user-provided file subset.
    # risk_level and affected intentionally reflect the FULL diff (conservative —
    # never under-reports risk to the caller).
    # Skip the filter when the report is an error dict (NOT_A_GIT_REPO etc).
    if (
        stdin_files is not None
        and isinstance(report, dict)
        and not report.get("error")
        and "changed_symbols" in report
    ):
        # Determine the key used for file lookup in changed_symbols entries.
        # handle_seam_changes returns relativized paths; detect_changes returns absolute.
        # We filter by checking if the symbol's file appears in stdin_files.
        # For handle_seam_changes (json/quiet paths), file is relative; we need to
        # resolve it to compare with the resolved stdin_files set.
        def _abs_sym_file(sym_file: str | None) -> str | None:
            if sym_file is None:
                return None
            p = Path(sym_file)
            if p.is_absolute():
                return str(p)
            return str((project_root / p).resolve())

        if isinstance(report, dict) and "changed_symbols" in report:
            report = dict(report)  # shallow copy so we don't mutate the TypedDict
            report["changed_symbols"] = [
                s for s in report["changed_symbols"] if _abs_sym_file(s.get("file")) in stdin_files
            ]
            report["new_files"] = [
                f for f in report.get("new_files", []) if _abs_sym_file(f) in stdin_files
            ]

    # ── Error guard — applies to ALL output modes ─────────────────────────────
    # handle_seam_changes returns {"error": "...", "message": "..."} for NOT_A_GIT_REPO.
    # Without this guard, --quiet calls print_quiet(report, "risk_level") → KeyError,
    # and --rich (default) would crash on report["risk_level"] below.
    # Mirror the --json branch: surface the error uniformly regardless of output mode.
    if isinstance(report, dict) and report.get("error"):
        if json_:
            emit_json_error(report["error"], report.get("message", ""))
        # --quiet and rich: print error to stderr and exit non-zero
        sys.stderr.write(f"Error [{report['error']}]: {report.get('message', '')}\n")
        raise typer.Exit(code=1)

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(report)
        return

    # ── Quiet mode — print the risk level ────────────────────────────────────
    if quiet:
        print_quiet(report, field="risk_level")
        return

    # ── Rich (default) mode — existing rendering, unchanged ──────────────────

    # ── Summary header ────────────────────────────────────────────────────────
    risk_color = {
        "low": "green",
        "medium": "yellow",
        "high": "bold yellow",
        "critical": "bold red",
    }.get(report["risk_level"], "white")

    console.print(
        f"\n[bold]seam changes[/bold]  scope=[cyan]{report['scope']}[/cyan]"
        + (f"  base=[cyan]{report['base_ref']}[/cyan]" if scope == "branch" else "")
    )
    console.print(
        f"Risk: [{risk_color}]{report['risk_level'].upper()}[/{risk_color}]"
        + (
            " [yellow](AMBIGUOUS edges — estimate uncertain)[/yellow]"
            if report["ambiguous_warning"]
            else ""
        )
    )

    # Partial verdict marker: printed after the risk line when the impact cap was hit.
    # Format: "⚠ PARTIAL — impact cap (N) hit; M of K symbols analyzed"
    # This makes it immediately obvious the risk verdict only covers a subset.
    # Count only REAL changed symbols (exclude synthetic <module:...>/<new:...> entries):
    # the cap in _collect_impact applies to real names only, so the denominator must
    # match — otherwise the displayed fraction does not reconcile with what was capped.
    if report.get("partial"):
        cap = config.SEAM_MAX_IMPACT_SYMBOLS
        real_total = sum(1 for s in report["changed_symbols"] if not s["name"].startswith("<"))
        analyzed = min(cap, real_total)
        console.print(
            f"[yellow]⚠ PARTIAL[/yellow] — impact cap ({cap}) hit; "
            f"{analyzed} of {real_total} changed symbols analyzed"
        )

    # ── Changed symbols ───────────────────────────────────────────────────────
    if not report["changed_symbols"] and not report["new_files"]:
        console.print("\n[dim]No changes detected.[/dim]")
        return

    if report["new_files"]:
        console.print(
            f"\n[bold cyan]New / untracked files ({len(report['new_files'])}):[/bold cyan]"
        )
        for f in report["new_files"]:
            # Relativize for display
            try:
                rel = str(Path(f).relative_to(project_root))
            except ValueError:
                rel = f
            console.print(f"  [green]+[/green] {rel}")

    if report["changed_symbols"]:
        # Filter out synthetic module-level entries for cleaner display.
        real_syms = [s for s in report["changed_symbols"] if not s["name"].startswith("<")]
        module_syms = [s for s in report["changed_symbols"] if s["name"].startswith("<")]

        if real_syms:
            console.print(f"\n[bold cyan]Changed symbols ({len(real_syms)}):[/bold cyan]")
            for sym in real_syms:
                try:
                    rel_file = str(Path(sym["file"]).relative_to(project_root))
                except ValueError:
                    rel_file = sym["file"]
                lines_str = (
                    f"  lines {sym['changed_lines'][:5]}"
                    + ("…" if len(sym["changed_lines"]) > 5 else "")
                    if sym["changed_lines"]
                    else ""
                )
                console.print(f"  [bold]{sym['name']}[/bold] [dim]{rel_file}{lines_str}[/dim]")

        if module_syms:
            console.print(f"\n[dim]Module-level changes ({len(module_syms)} file(s)).[/dim]")

    # ── Affected (downstream) symbols ─────────────────────────────────────────
    if not report["affected"]:
        console.print("\n[dim]No downstream dependents found.[/dim]")
        return

    tier_order = [TIER_WILL_BREAK, TIER_LIKELY_AFFECTED, TIER_MAY_NEED_TESTING]
    tier_labels = {
        TIER_WILL_BREAK: "[bold red]WILL BREAK[/bold red]         (d=1)",
        TIER_LIKELY_AFFECTED: "[bold yellow]LIKELY AFFECTED[/bold yellow]   (d=2)",
        TIER_MAY_NEED_TESTING: "[dim]MAY NEED TESTING[/dim]  (d=3+)",
    }

    console.print(f"\n[bold cyan]Affected symbols ({len(report['affected'])}):[/bold cyan]")

    # Group by tier for display.
    by_tier: dict[str, list[AffectedSymbol]] = {t: [] for t in tier_order}
    for a in report["affected"]:
        tier = a["tier"]
        if tier in by_tier:
            by_tier[tier].append(a)

    for tier in tier_order:
        entries = by_tier[tier]
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


# ── seam why ──────────────────────────────────────────────────────────────────


def _parse_why_target(target: str) -> tuple[str, int | None]:
    """Parse a CLI target string into (file_path, line | None).

    Accepts:
      'path/to/file.py'      → ('path/to/file.py', None)
      'path/to/file.py:42'   → ('path/to/file.py', 42)

    Splits on the LAST ':' only. If the part after the last ':' is not a valid
    integer, the entire string is treated as a file path with no line.
    """
    # Split on the last ':' to handle paths that may contain ':' (e.g. Windows drives)
    last_colon = target.rfind(":")
    if last_colon != -1:
        maybe_line = target[last_colon + 1 :]
        try:
            line = int(maybe_line)
            return target[:last_colon], line
        except ValueError:
            pass
    return target, None


@app.command(name="why")
def why_cmd(
    target: str = typer.Argument(
        "",
        help="File path or 'file:line'. Examples: app.py, app.py:42. "
        "Optional when --symbol is given.",
    ),
    symbol: str = typer.Option(
        "",
        "--symbol",
        "-s",
        help="Symbol name (alternative to a file target)",
    ),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare values only (one per line)."),
) -> None:
    """Show semantic comments (WHY/HACK/NOTE/TODO/FIXME) near a file location or symbol.

    Examples:
      seam why app.py                  -- all semantic comments in app.py
      seam why app.py:42               -- comments near line 42
      seam why --symbol my_func        -- comments inside or above my_func
    """
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        if json_:
            emit_json_error("NO_INDEX", "No index found. Run 'seam init' first.")
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    # Parse the positional target into file + optional line
    file_arg, line_arg = _parse_why_target(target)

    # `target` is an OPTIONAL positional (default "") so `seam why --symbol foo`
    # works without a file. At least one of file/symbol must be provided — we
    # validate that below, after parsing.
    resolved_symbol = symbol.strip() if symbol else None

    # Resolve the file path to absolute so it matches DB stored paths
    resolved_file: str | None = None
    if file_arg:
        resolved_file = str((project_root / file_arg).resolve())

    # If neither resolved_file nor resolved_symbol is set after parsing, error out
    if not resolved_file and not resolved_symbol:
        if json_:
            emit_json_error("INVALID_INPUT", "Provide a file path or --symbol.")
        console.print("[red]Error:[/red] Provide a file path or --symbol.")
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # WHY `Any`: handle_seam_why returns list[dict[str,Any]] | dict[str,Any];
    # comments_why returns list[CommentHit]. Both are accessed with same string
    # keys at runtime; `Any` is the honest annotation to unify them for mypy.
    hits: Any
    try:
        # WHY: reuse handle_seam_why for --json/--quiet (MCP/CLI parity).
        if json_ or quiet:
            hits = handle_seam_why(
                conn,
                root=project_root,
                file=file_arg if file_arg else None,
                line=line_arg,
                symbol=resolved_symbol,
            )
        else:
            hits = comments_why(
                conn,
                file=resolved_file,
                line=line_arg,
                symbol=resolved_symbol,
            )
    finally:
        conn.close()

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(hits)
        return

    # ── Quiet mode — print marker: text per hit ───────────────────────────────
    if quiet:
        for hit in hits:
            sys.stdout.write(f"{hit['marker']}: {hit['text']}\n")
        return

    # ── Rich (default) mode — existing rendering, unchanged ──────────────────

    if not hits:
        console.print("[dim]No semantic comments found[/dim]")
        return

    # Render results: marker  line  text (file is context, usually already known)
    for hit in hits:
        # Relativize path for display
        try:
            rel_file = str(Path(hit["file"]).relative_to(project_root))
        except ValueError:
            rel_file = hit["file"]

        marker_color = {
            "WHY": "cyan",
            "HACK": "yellow",
            "NOTE": "blue",
            "TODO": "green",
            "FIXME": "red",
        }.get(hit["marker"], "white")

        console.print(
            f"[{marker_color}]{hit['marker']}[/{marker_color}]"
            f"  [dim]line {hit['line']}[/dim]"
            f"  [dim]{rel_file}[/dim]"
            f"  {hit['text']}"
        )


# ── seam clusters ─────────────────────────────────────────────────────────────


@app.command(name="clusters")
def clusters_cmd(
    cluster_id: int = typer.Option(
        -1,
        "--id",
        help="Cluster ID to list members of. Omit to list all clusters.",
    ),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(False, "--quiet", help="Print bare values only (one per line)."),
) -> None:
    """List all clusters or members of one cluster.

    Examples:
      seam clusters              -- list all clusters with id, label, size
      seam clusters --id 3       -- list all member symbols of cluster 3
    """
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

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

    try:
        # WHY: reuse handle_seam_clusters for --json/--quiet (MCP/CLI parity).
        if json_ or quiet:
            cid = cluster_id if cluster_id >= 0 else None
            cluster_data = handle_seam_clusters(conn, root=project_root, cluster_id=cid)
        else:
            if cluster_id >= 0:
                members = query_cluster_members(conn, cluster_id)
            else:
                members = None
                clusters = query_list_clusters(conn)
    finally:
        conn.close()

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(cluster_data)
        return

    # ── Quiet mode — print labels (cluster list) or names (member list) ───────
    if quiet:
        if cluster_id >= 0:
            # Quiet for members: print symbol names
            for item in cluster_data:
                sys.stdout.write(item["name"] + "\n")
        else:
            # Quiet for cluster list: print labels
            for item in cluster_data:
                sys.stdout.write(item["label"] + "\n")
        return

    # ── Rich (default) mode — existing rendering, unchanged ──────────────────

    if cluster_id >= 0:
        # Display members
        if not members:
            console.print(
                f"[yellow]No members found for cluster {cluster_id}[/yellow] "
                "— check the ID or run 'seam init' first."
            )
            return

        table = Table(title=f"Cluster {cluster_id} members", show_header=True)
        table.add_column("name", style="bold")
        table.add_column("kind", style="dim")
        table.add_column("file", style="dim")
        table.add_column("line", style="dim")
        for m in members:
            # Relativize file path for display
            try:
                rel_file = str(Path(m["file"]).relative_to(project_root))
            except ValueError:
                rel_file = m["file"]
            table.add_row(m["name"], m["kind"], rel_file, str(m["line"]))
        console.print(table)
    else:
        # Display all clusters
        if not clusters:
            console.print(
                "[yellow]No clusters found.[/yellow] "
                "Run [bold]seam init[/bold] to compute clusters."
            )
            return

        table = Table(title="Clusters", show_header=True)
        table.add_column("id", style="bold cyan", width=6)
        table.add_column("label")
        table.add_column("size", style="dim", width=6)
        for c in clusters:
            table.add_row(str(c["id"]), c["label"], str(c["size"]))
        console.print(table)


# ── seam affected ─────────────────────────────────────────────────────────────


@app.command(name="affected")
def affected_cmd(
    files: list[str] = typer.Argument(
        default=None,
        help="Changed file paths to analyze. Mutually exclusive with --stdin.",
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help=(
            "Read changed file paths from stdin (newline-delimited). "
            "Mutually exclusive with positional file arguments."
        ),
    ),
    depth: int = typer.Option(
        config.SEAM_AFFECTED_DEPTH,
        "--depth",
        help="Max upstream traversal depth (default: SEAM_AFFECTED_DEPTH env var).",
    ),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Print bare test-file paths one per line (for piping into pytest).",
    ),
) -> None:
    """Find which test files are impacted by changed source files.

    Given changed files (as positional args or via --stdin), traverses the reverse-
    dependency graph to find all test files that depend on symbols in those files.

    Examples:
      seam affected src/foo.py src/bar.py --json
      git diff --name-only | seam affected --stdin --quiet | xargs pytest
      seam affected src/foo.py --quiet   # bare test paths, one per line

    A changed file that is itself a test file is always included in the output.
    Files not in the index are silently skipped.
    """
    # Mutual exclusion check (--json + --quiet is not allowed)
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    project_root = Path(path).resolve()
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        if json_:
            emit_json_error("NO_INDEX", "No index found. Run 'seam init' first.")
        console.print("[red]No index found.[/red] Run [bold]seam init[/bold] first.")
        raise typer.Exit(code=1)

    # ── Determine input file list ─────────────────────────────────────────────
    # Enforce mutual exclusion explicitly — if both are given, error cleanly rather
    # than silently discarding the positional args (which would confuse the caller
    # into thinking their file list was analyzed when it was not).
    if stdin and files:
        if json_:
            emit_json_error(
                "INVALID_INPUT",
                "Provide file paths as positional arguments OR use --stdin, not both.",
            )
        console.print(
            "[red]Error:[/red] Use positional arguments OR [bold]--stdin[/bold], not both."
        )
        raise typer.Exit(code=1)

    if stdin:
        raw_lines = sys.stdin.read().splitlines()
        input_files = [ln.strip() for ln in raw_lines if ln.strip()]
    else:
        input_files = list(files) if files else []

    if not input_files:
        if json_:
            emit_json_error(
                "INVALID_INPUT",
                "Provide file paths as arguments or use --stdin to read from stdin.",
            )
        console.print(
            "[red]Error:[/red] Provide file paths as arguments or use [bold]--stdin[/bold]."
        )
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        # Always route through handle_seam_affected so CLI output is byte-identical to
        # the MCP tool response (MCP/CLI parity). The handler also relativizes paths,
        # applies the file-list size cap, and returns a structured error dict on INVALID_INPUT
        # rather than raising — which the error guard below relies on.
        result = handle_seam_affected(conn, input_files, project_root, depth=depth)
    finally:
        conn.close()

    # ── Error guard — applies to ALL output modes ─────────────────────────────
    # handle_seam_affected returns {"error": ..., "message": ...} on INVALID_INPUT.
    # Without this guard, --quiet and rich modes silently degrade to "No affected test
    # files found" even though the handler actually errored (e.g. oversized file list).
    if isinstance(result, dict) and result.get("error"):
        if json_:
            emit_json_error(result["error"], result.get("message", ""))
        # --quiet and rich: write error to stderr and exit non-zero
        sys.stderr.write(f"Error [{result['error']}]: {result.get('message', '')}\n")
        raise typer.Exit(code=1)

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(result)
        return

    # ── Quiet mode — bare test-file paths for piping into pytest ─────────────
    if quiet:
        for test_path in result.get("affected_tests", []):
            sys.stdout.write(test_path + "\n")
        return

    # ── Rich (default) mode ───────────────────────────────────────────────────
    affected_tests = result.get("affected_tests", [])
    total = result.get("total_dependents_traversed", 0)

    if not affected_tests:
        console.print(
            f"[dim]No affected test files found.[/dim] [dim]({total} dependent(s) traversed)[/dim]"
        )
        return

    console.print(
        f"\n[bold cyan]Affected tests[/bold cyan] "
        f"for [bold]{len(input_files)}[/bold] changed file(s) "
        f"[dim]({total} dependent(s) traversed):[/dim]"
    )
    for test_path in affected_tests:
        console.print(f"  [green]•[/green] {test_path}")
    console.print(f"\n[dim]Run with:[/dim] [bold]pytest {' '.join(affected_tests)}[/bold]")


# ── seam pack ─────────────────────────────────────────────────────────────────


@app.command(name="pack")
def pack_cmd(
    symbol: str = typer.Argument(..., help="Symbol name to build context pack for."),
    path: str = typer.Option(".", "--path", help="Project root (default: current directory)"),
    db_dir: str = typer.Option("", "--db-dir", help="Override DB directory"),
    lean: bool = typer.Option(
        False,
        "--lean",
        help=(
            "Omit heavy enrichment fields (decorators, is_exported, visibility, qualified_name) "
            "from target and all neighbors. Identical to verbose=false in MCP."
        ),
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Print terse human rendering without the JSON envelope.",
    ),
) -> None:
    """Get a ready-to-paste context bundle for a symbol.

    Returns target info, enriched callers/callees (with file, line, kind,
    signature), WHY/HACK/NOTE comments, cluster peers, and truncation counts —
    all in one call.

    Examples:
      seam pack my_func
      seam pack my_func --json
      seam pack my_func --lean --json
      seam pack my_func --quiet
    """
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

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

    # --lean sets verbose=False; output becomes byte-identical to the MCP tool
    # called with verbose=False — heavy fields absent from target and all neighbors.
    verbose = not lean

    try:
        # WHY: always route through handle_seam_context_pack so MCP and CLI
        # produce the identical bundle (same paths, same caps, same truncation).
        result = handle_seam_context_pack(conn, symbol, project_root, verbose=verbose)
    finally:
        conn.close()

    # ── Error dict from handler (blank input) ────────────────────────────────
    if isinstance(result, dict) and result.get("error"):
        if json_:
            emit_json_error(result["error"], result.get("message", ""))
        console.print(f"[red]Error:[/red] {result.get('message', result['error'])}")
        raise typer.Exit(code=1)

    # ── Symbol not found ─────────────────────────────────────────────────────
    # WHY success envelope (not error): mirrors seam_context's contract.
    # A missing symbol is a valid answer — the agent reads found:false and
    # knows to check the name or run 'seam init'. Using emit_json_error with
    # a NOT_FOUND code would invent an undocumented error code AND diverge
    # from seam_context, which returns null (not an error) for missing symbols.
    if result is None:
        if json_:
            emit_json({"found": False, "symbol": symbol})
            return
        console.print(
            f"[yellow]Symbol '{symbol}' not found in the index[/yellow]"
            " — check the name or run 'seam init'."
        )
        return

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(result)
        return

    # ── Quiet mode — terse rendering ─────────────────────────────────────────
    if quiet:
        target = result["target"]
        sys.stdout.write(
            f"{target['symbol']}  {target['kind']}  {target['file']}:{target['line']}\n"
        )
        if result["callers"]:
            sys.stdout.write(
                "callers: " + ", ".join(nb["name"] for nb in result["callers"]) + "\n"
            )
        if result["callees"]:
            sys.stdout.write(
                "callees: " + ", ".join(nb["name"] for nb in result["callees"]) + "\n"
            )
        for hit in result["why"]:
            sys.stdout.write(f"{hit['marker']}: {hit['text']}\n")
        return

    # ── Rich (default) mode ───────────────────────────────────────────────────
    target = result["target"]
    trunc = result["truncated"]

    # ── Target header ─────────────────────────────────────────────────────────
    ambig_marker = " [yellow](ambiguous)[/yellow]" if target.get("ambiguous") else ""
    console.print(
        f"\n[bold cyan]seam pack[/bold cyan] "
        f"[bold]{target['symbol']}[/bold]{ambig_marker}"
        f"  [dim]{target['kind']}  {target['file']}:{target['line']}[/dim]"
    )

    if target.get("signature"):
        console.print(f"  [dim]sig:[/dim] {target['signature']}")
    if target.get("docstring"):
        console.print(f"  [dim]doc:[/dim] {target['docstring'][:100]}")

    # ── Enriched callers/callees table ────────────────────────────────────────
    def _print_neighbors(label: str, neighbors: list, dropped: int) -> None:
        if not neighbors and not dropped:
            console.print(f"\n  [dim]{label}: none[/dim]")
            return
        suffix = f" [dim](+{dropped} truncated)[/dim]" if dropped else ""
        console.print(f"\n  [bold]{label}[/bold]{suffix}:")
        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("name", style="bold", width=28)
        table.add_column("kind", style="dim", width=10)
        table.add_column("file:line", style="dim")
        table.add_column("signature", style="dim")
        for nb in neighbors:
            sig = (nb.get("signature") or "")[:50]
            table.add_row(
                nb["name"],
                nb["kind"],
                f"{nb['file']}:{nb['line']}",
                sig,
            )
        console.print(table)

    _print_neighbors("callers", result["callers"], trunc["callers"])
    _print_neighbors("callees", result["callees"], trunc["callees"])

    # ── WHY comments ─────────────────────────────────────────────────────────
    why_hits = result["why"]
    if why_hits:
        suffix = f" [dim](+{trunc['comments']} truncated)[/dim]" if trunc["comments"] else ""
        console.print(f"\n  [bold]why[/bold]{suffix}:")
        for hit in why_hits:
            marker_color = {
                "WHY": "cyan", "HACK": "yellow", "NOTE": "blue",
                "TODO": "green", "FIXME": "red",
            }.get(hit["marker"], "white")
            console.print(
                f"    [{marker_color}]{hit['marker']}[/{marker_color}]"
                f"  [dim]line {hit['line']}[/dim]  {hit['text']}"
            )
    else:
        console.print("\n  [dim]why: no semantic comments[/dim]")

    # ── Cluster peers ─────────────────────────────────────────────────────────
    peers = result.get("cluster_peers", [])
    if peers:
        console.print(
            f"\n  [bold]cluster peers[/bold]: {', '.join(peers[:8])}"
            + (f" +{len(peers) - 8} more" if len(peers) > 8 else "")
        )


# ── seam sync ─────────────────────────────────────────────────────────────────


@app.command(name="sync")
def sync_cmd(
    path: str = typer.Argument(".", help="Project root to sync (default: current directory)"),
    db_dir: str = typer.Option(
        "",
        "--db-dir",
        help="Override DB directory (default: same as project root)",
    ),
    force_clusters: bool = typer.Option(
        False,
        "--force-clusters",
        help=(
            "Recompute clusters even when zero files changed "
            "(useful after the watcher already indexed your edits)."
        ),
    ),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope to stdout."),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Print bare key:value output, one per line (for hook use).",
    ),
) -> None:
    """Incrementally refresh the Seam index by reconciling against the filesystem.

    Detects files that changed since the last index (via mtime + SHA-1), re-indexes
    only those files, removes deleted files, and recomputes clusters if the graph
    changed. Much faster than `seam init` when only a few files changed.

    Requires an existing index (run `seam init` first).

    Examples:
      seam sync                       -- sync current directory
      seam sync /path/to/project      -- sync a specific project
      seam sync --force-clusters      -- recompute clusters even if nothing changed
      seam sync -q >/dev/null 2>&1    -- quiet for git hook use
      seam sync --json                -- structured output for CI / agents
    """
    # WHY: check mutual exclusion before any DB work so the error is immediate.
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))

    project_root = Path(path).resolve()

    if not project_root.is_dir():
        if json_:
            emit_json_error("INVALID_INPUT", f"'{project_root}' is not a directory.")
        console.print(f"[red]Error:[/red] '{project_root}' is not a directory.")
        raise typer.Exit(code=1)

    # Mirror `init`'s path/db-dir resolution.
    db_root = Path(db_dir).resolve() if db_dir else project_root
    db_path = config.get_db_path(db_root)

    # sync requires an existing index — use connect() (NOT init_db).
    # WHY: sync's responsibility is "reconcile an existing index"; bootstrapping
    # a new one is init's job. Failing clearly here prevents silent data loss
    # (e.g. the user accidentally runs sync in the wrong directory and gets an
    # empty index instead of an error).
    if not db_path.exists():
        if json_:
            emit_json_error(
                "NO_INDEX",
                f"No index found at '{db_path}'. Run 'seam init' first to create the index.",
            )
        console.print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first to create the index."
        )
        raise typer.Exit(code=1)

    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        if json_:
            emit_json_error("DB_ERROR", f"Failed to open database: {exc}")
        console.print(f"[red]Failed to open database:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        result = sync_project(
            conn,
            project_root,
            recompute_clusters=True,
            force_clusters=force_clusters,
            naming_mode=config.SEAM_CLUSTER_NAMING,
            llm_api_key=config.SEAM_LLM_API_KEY,
            llm_model=config.SEAM_LLM_MODEL,
            min_size=config.SEAM_CLUSTER_MIN_SIZE,
        )
    except sqlite3.Error as exc:
        # A genuine database-layer failure (lock, corruption, disk full mid-write).
        if json_:
            emit_json_error("DB_ERROR", f"Sync failed: {exc}")
        console.print(f"[red]Sync failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        # Catch-all so an unexpected error (e.g. an OSError walking the tree) still
        # produces a structured envelope instead of a raw traceback — the --json
        # contract must never be broken. DB_ERROR is the closest data-layer bucket
        # in the documented code set (NO_INDEX/INVALID_INPUT/INVALID_QUERY/
        # NOT_A_GIT_REPO/DB_ERROR); we do not invent a new code. The message keeps
        # the real error visible for diagnosis.
        if json_:
            emit_json_error("DB_ERROR", f"Sync failed: {exc}")
        console.print(f"[red]Sync failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if json_:
        emit_json(result)
        return

    # ── Quiet mode — key:value pairs, one per line, for hook use ─────────────
    # WHY key:value (not bare values): with 8 mixed int/bool fields, bare positional
    # values are ambiguous to parse. "key: value\n" lets hooks do `grep "^added:"`.
    if quiet:
        sys.stdout.write(f"added: {result['added']}\n")
        sys.stdout.write(f"modified: {result['modified']}\n")
        sys.stdout.write(f"removed: {result['removed']}\n")
        sys.stdout.write(f"unchanged: {result['unchanged']}\n")
        sys.stdout.write(f"skipped: {result['skipped']}\n")
        sys.stdout.write(f"graph_changed: {result['graph_changed']}\n")
        sys.stdout.write(f"clusters_recomputed: {result['clusters_recomputed']}\n")
        sys.stdout.write(f"cluster_count: {result['cluster_count']}\n")
        return

    # ── Rich (default) mode — summary table ───────────────────────────────────
    # cluster_count: None = recompute skipped (gate false); -1 = recompute RAN but
    # FAILED (index_clusters' error sentinel); >= 0 = success. Mirror `seam init`'s
    # display so a failed recompute reads as "failed", never a misleading "-1".
    cluster_count = result["cluster_count"]
    clustering_failed = cluster_count is not None and cluster_count < 0
    if cluster_count is None:
        cluster_display = "skipped"
    elif clustering_failed:
        cluster_display = "failed"
    else:
        cluster_display = str(cluster_count)

    table = Table(title="seam sync — complete", show_header=False, box=None)
    table.add_column("key", style="bold cyan", width=20)
    table.add_column("value")
    table.add_row("root", str(project_root))
    table.add_row("added", str(result["added"]))
    table.add_row("modified", str(result["modified"]))
    table.add_row("removed", str(result["removed"]))
    table.add_row("unchanged", str(result["unchanged"]))
    table.add_row("skipped", str(result["skipped"]))
    table.add_row("clusters", cluster_display)
    console.print(table)

    # Visible failure warning when the gated cluster recompute failed — without
    # this the operator sees only "clusters: failed" in the table and might miss
    # that the index's clusters are now stale. Mirrors `seam init`'s guard.
    if clustering_failed:
        console.print(
            "[yellow]clusters: recompute failed[/yellow] "
            "[dim](clusters may be stale — run 'seam init' to rebuild; "
            "set SEAM_LOG_LEVEL=DEBUG to see the error)[/dim]"
        )

    if result["skipped"] > 0:
        console.print(
            f"[dim]{result['skipped']} file(s) skipped (binary/oversize/parse error). "
            "Set SEAM_LOG_LEVEL=DEBUG to see which.[/dim]"
        )
