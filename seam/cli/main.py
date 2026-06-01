"""Seam CLI entry point — `seam init`, `seam start`, `seam status`.

Commands
--------
init   — Index a project directory into .seam/seam.db.
status — Show index stats and watcher state.
start  — Start the MCP server (stdio) + file watcher in the background.
"""

import hashlib
import logging
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
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import extract_edges, extract_symbols
from seam.indexer.parser import parse_javascript, parse_python, parse_typescript

app = typer.Typer(
    name="seam",
    help="Local code intelligence MCP server for AI agents.",
    add_completion=False,
)

console = Console()

# Directories to skip when walking the project tree.
# Dot-dirs are skipped by default; this list catches common non-dot dirs.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".seam",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)


def _sha1(content: bytes) -> str:
    """Return the SHA-1 hex digest of file content bytes."""
    return hashlib.sha1(content).hexdigest()  # noqa: S324 — SHA-1 used for change detection only


def _dispatch_parser(path: Path, language: str):  # type: ignore[return]
    """Call the correct parser function for a given language string.

    Returns tree-sitter root Node or None (parsers never raise).
    """
    if language == "python":
        return parse_python(path)
    elif language == "typescript":
        return parse_typescript(path)
    elif language == "javascript":
        return parse_javascript(path)
    return None


def index_one_file(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """Parse, extract, and upsert a single source file into the DB.

    Dispatch: extension -> SEAM_LANGUAGE_MAP -> parser -> extract_symbols/edges -> upsert.

    Returns (symbol_count, edge_count) on success, (0, 0) when the file is
    skipped (unknown extension, over size limit, parse returns None, or any error).
    Never raises — all errors are silently suppressed to keep the indexer resilient.
    """
    try:
        ext = path.suffix.lower()
        language = config.SEAM_LANGUAGE_MAP.get(ext)
        if language is None:
            return 0, 0  # unsupported extension

        # Size guard — mirrors the parser's own guard; check early to skip cheap
        try:
            if path.stat().st_size > config.SEAM_MAX_FILE_BYTES:
                return 0, 0
        except OSError:
            return 0, 0

        # Parse
        root = _dispatch_parser(path, language)
        if root is None:
            return 0, 0  # binary, unreadable, or over-size detected by parser

        # Read bytes for SHA-1 (already checked readable above)
        try:
            content = path.read_bytes()
        except OSError:
            return 0, 0

        file_hash = _sha1(content)
        symbols = extract_symbols(root, language, path)
        edges = extract_edges(root, language, path)

        upsert_file(conn, path, language, file_hash, symbols, edges)
        return len(symbols), len(edges)

    except Exception:  # noqa: BLE001 — never let one bad file abort the whole index run
        return 0, 0


def _walk_project(root: Path) -> list[Path]:
    """Walk root recursively, skipping ignored dirs, returning indexable files.

    Rules:
      - Skip any directory whose name starts with '.' (hidden dirs).
      - Skip any directory in _SKIP_DIRS.
      - Collect files whose suffix is in config.SEAM_LANGUAGE_MAP.
    """
    files: list[Path] = []
    for item in root.rglob("*"):
        # Skip if any path component is a skip-dir or hidden dir
        if any(
            part.startswith(".") or part in _SKIP_DIRS
            for part in item.parts[len(root.parts) :]  # relative parts only
        ):
            continue
        if item.is_file() and item.suffix.lower() in config.SEAM_LANGUAGE_MAP:
            files.append(item)
    return files


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
    files = _walk_project(project_root)

    total_symbols = 0
    total_edges = 0
    indexed_files = 0

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
                sym_count, edge_count = index_one_file(conn, file_path)
                if sym_count > 0 or edge_count > 0:
                    indexed_files += 1
                total_symbols += sym_count
                total_edges += edge_count
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
    table.add_row("symbols", str(total_symbols))
    table.add_row("edges", str(total_edges))
    table.add_row("elapsed", f"{elapsed:.2f}s")
    console.print(table)


@app.command()
def status(
    db_dir: str = typer.Option(
        "",
        "--db-dir",
        help="Override DB directory (used in tests; default: current directory)",
    ),
) -> None:
    """Show index statistics and watcher status.

    Reads the DB at .seam/seam.db and prints:
    - file / symbol / edge counts
    - last indexed_at timestamp
    - watcher PID (if .seam/watcher.pid exists)
    - freshness: newest DB mtime vs newest on-disk file mtime
    """
    db_root = Path(db_dir).resolve() if db_dir else Path.cwd()
    db_path = config.get_db_path(db_root)

    if not db_path.exists():
        console.print(
            "[red]No index found.[/red] Run [bold]seam init[/bold] first to create the index."
        )
        raise typer.Exit(code=1)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
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

        freshness = "fresh" if disk_newest_mtime <= db_newest_mtime else "stale"

    finally:
        conn.close()

    # Watcher PID
    pid_file = db_root / ".seam" / "watcher.pid"
    watcher_status = "not running"
    if pid_file.exists():
        try:
            watcher_status = f"PID {pid_file.read_text().strip()}"
        except OSError:
            watcher_status = "unknown"

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

    A PID file for the watcher is written to .seam/watcher.pid.
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

    # ── Launch watcher in a background subprocess ─────────────────────────────
    # We spawn a fresh interpreter so the watcher runs independently of the MCP
    # server's asyncio event loop.  The subprocess inherits stdout/stderr so its
    # logging is visible when running interactively.
    watcher_cmd = [
        sys.executable,
        "-c",
        (
            "from seam.watcher.daemon import SeamWatcher; from pathlib import Path; "
            f"w = SeamWatcher(db_path=Path('{db_path}'), root_path=Path('{project_root}')); "
            "import signal, sys; "
            "w.start(); "
            "[signal.pause() if hasattr(signal, 'pause') else None]; "
        ),
    ]
    watcher_proc = subprocess.Popen(  # noqa: S603 — controlled internal command
        watcher_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Write watcher PID file to .seam/watcher.pid for `seam status`
    pid_file = db_path.parent / "watcher.pid"
    try:
        pid_file.write_text(str(watcher_proc.pid))
    except OSError as exc:
        Console(stderr=True).print(
            f"[yellow]Warning:[/yellow] could not write watcher PID file: {exc}"
        )

    logging.basicConfig(level=getattr(logging, config.SEAM_LOG_LEVEL, logging.INFO))

    # ── Open DB connection for the MCP server ─────────────────────────────────
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # ── Create and run MCP server in the foreground (stdio) ───────────────────
    # Runs until the parent process closes stdin (e.g. client disconnects).
    # On SIGTERM/SIGINT, clean up the watcher subprocess.
    def _cleanup(signum: int, frame: object) -> None:  # noqa: ARG001
        watcher_proc.terminate()
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        conn.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    try:
        from seam.server.mcp import create_server

        server = create_server(conn, project_root)
        server.run(transport="stdio")
    finally:
        watcher_proc.terminate()
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        conn.close()
