"""SQLite read/write operations for the Seam index.

Schema defined in docs/database/schema.sql.
All operations use explicit connections (no connection pool) — caller controls lifetime.

Key design decisions:
- init_db verifies FTS5 availability before running the schema script.
- upsert_file is a single transaction: INSERT OR REPLACE the file row,
  DELETE old symbols/edges, then re-insert. Triggers handle FTS sync.
- delete_file DELETE FROM files cascades to symbols/edges via FK; FTS
  triggers fire on each symbol DELETE.
- Edge["source"] -> source_name, Edge["target"] -> target_name (see CONTRACT.md).
"""

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seam.indexer.graph import Edge, Symbol

# Schema SQL relative to this file: ../../docs/database/schema.sql
_SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "database" / "schema.sql"

# Busy timeout (ms) so a concurrent reader (MCP server) doesn't make a
# concurrent writer (watcher) fail immediately with "database is locked".
_BUSY_TIMEOUT_MS = 5000


def connect(db_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection with Seam's required per-connection PRAGMAs.

    CRITICAL: `PRAGMA foreign_keys` is per-connection, not stored in the DB file.
    Every connection that writes (init, watcher) MUST enable it or ON DELETE
    CASCADE is silently off — `delete_file` and re-index would orphan rows.
    All callers (init_db, watcher, server, status) go through here so no
    connection can accidentally run without FK enforcement.

    Args:
        db_path: Path to the SQLite file.
        check_same_thread: Pass False for connections shared across threads
            (the watcher's observer/timer threads share one connection).
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply the schema.

    Steps:
      1. Open connection via connect() (sets foreign_keys + busy_timeout).
      2. Verify FTS5 is available; raise RuntimeError with a clear message if not.
      3. Execute the schema SQL (idempotent — uses CREATE TABLE IF NOT EXISTS).

    Returns the open connection. Caller is responsible for closing it.
    Raises RuntimeError if FTS5 is unavailable.
    """
    conn = connect(db_path)

    # Verify FTS5 — attempt to create a temp virtual table then drop it.
    try:
        conn.execute("CREATE VIRTUAL TABLE _seam_fts5_check USING fts5(content)")
        conn.execute("DROP TABLE IF EXISTS _seam_fts5_check")
    except sqlite3.OperationalError as exc:
        conn.close()
        raise RuntimeError(
            "SQLite FTS5 extension is not available. "
            "Seam requires a SQLite build compiled with FTS5 support. "
            f"Original error: {exc}"
        ) from exc

    # Apply schema (CREATE TABLE IF NOT EXISTS — safe to run multiple times).
    schema_sql = _SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    return conn


def upsert_file(
    conn: sqlite3.Connection,
    filepath: Path,
    language: str,
    file_hash: str,
    symbols: "list[Symbol]",
    edges: "list[Edge]",
) -> None:
    """Atomically replace all data for a file. Idempotent: safe to call twice.

    Steps (single transaction):
      1. UPSERT the file row via ON CONFLICT DO UPDATE — keeps a STABLE id
         across re-index (INSERT OR REPLACE would churn the autoincrement id
         and strand child rows). Captures new mtime + indexed_at.
      2. Retrieve the file_id for this path.
      3. DELETE existing edges AND symbols for that file_id. Both are deleted
         explicitly (deleting symbols does NOT cascade to edges — edges hang
         off files, not symbols). FTS triggers fire per-symbol DELETE.
      4. INSERT new symbols.
      5. INSERT new edges mapping Edge['source'] -> source_name,
                                  Edge['target'] -> target_name.
    """
    mtime = filepath.stat().st_mtime
    indexed_at = time.time()

    with conn:  # single transaction — commit on success, rollback on error
        # 1. Upsert the file row, preserving its id across re-index.
        conn.execute(
            """
            INSERT INTO files (path, language, file_hash, mtime, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                language   = excluded.language,
                file_hash  = excluded.file_hash,
                mtime      = excluded.mtime,
                indexed_at = excluded.indexed_at
            """,
            (str(filepath), language, file_hash, mtime, indexed_at),
        )

        # 2. Retrieve file_id (stable across re-index thanks to the upsert above)
        row = conn.execute("SELECT id FROM files WHERE path = ?", (str(filepath),)).fetchone()
        file_id: int = row["id"]

        # 3. Delete old children explicitly. Edges must be deleted directly:
        #    deleting symbols does NOT remove edges (edges reference files, not
        #    symbols). FTS triggers fire on each symbol DELETE to stay in sync.
        conn.execute("DELETE FROM edges WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))

        # 4. Insert symbols
        conn.executemany(
            """
            INSERT INTO symbols (file_id, name, kind, start_line, end_line, docstring)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    file_id,
                    sym["name"],
                    sym["kind"],
                    sym["start_line"],
                    sym["end_line"],
                    sym.get("docstring"),
                )
                for sym in symbols
            ],
        )

        # 5. Insert edges — contract: Edge['source'] -> source_name,
        #                             Edge['target'] -> target_name
        conn.executemany(
            """
            INSERT INTO edges (source_name, target_name, kind, file_id, line)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    edge["source"],  # Edge field 'source' -> column source_name
                    edge["target"],  # Edge field 'target' -> column target_name
                    edge["kind"],
                    file_id,
                    edge["line"],
                )
                for edge in edges
            ],
        )


def delete_file(conn: sqlite3.Connection, filepath: Path) -> None:
    """Remove a file and all its symbols/edges from the index.

    Cascade (FK ON DELETE CASCADE) removes symbols and edges automatically.
    FTS triggers fire on each symbol DELETE, keeping FTS in sync.
    Silently succeeds if the path was never indexed.
    """
    with conn:
        conn.execute("DELETE FROM files WHERE path = ?", (str(filepath),))
