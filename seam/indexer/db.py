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


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply the schema.

    Steps:
      1. Open connection, set row_factory = sqlite3.Row.
      2. Verify FTS5 is available; raise RuntimeError with a clear message if not.
      3. Execute the schema SQL (idempotent — uses CREATE TABLE IF NOT EXISTS).

    Returns the open connection. Caller is responsible for closing it.
    Raises RuntimeError if FTS5 is unavailable.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

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
      1. INSERT OR REPLACE into files (captures mtime + indexed_at).
      2. Retrieve the file_id for this path.
      3. DELETE existing symbols for that file_id (FTS trigger fires per row;
         CASCADE on edges.file_id also removes edges).
      4. INSERT new symbols.
      5. INSERT new edges mapping Edge['source'] -> source_name,
                                  Edge['target'] -> target_name.
    """
    mtime = filepath.stat().st_mtime
    indexed_at = time.time()

    with conn:  # single transaction — commit on success, rollback on error
        # 1. Upsert the file row
        conn.execute(
            """
            INSERT OR REPLACE INTO files (path, language, file_hash, mtime, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(filepath), language, file_hash, mtime, indexed_at),
        )

        # 2. Retrieve file_id (after upsert the row exists with the correct id)
        row = conn.execute("SELECT id FROM files WHERE path = ?", (str(filepath),)).fetchone()
        file_id: int = row["id"]

        # 3. Delete old symbols for this file (CASCADE removes edges too;
        #    FTS triggers fire per-symbol DELETE to keep FTS in sync).
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
