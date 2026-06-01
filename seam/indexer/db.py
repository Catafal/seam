"""SQLite read/write operations for the Seam index.

Schema defined in docs/database/schema.sql.
All operations use explicit connections (no connection pool) — caller controls lifetime.

Key design decisions:
- init_db verifies FTS5 availability before running the schema script.
- init_db runs guarded migrations: v1->v2 (edges.confidence), v2->v3 (comments table).
- upsert_file is a single transaction: INSERT OR REPLACE the file row,
  DELETE old symbols/edges/comments, then re-insert. Triggers handle FTS sync.
- delete_file DELETE FROM files cascades to symbols/edges/comments via FK; FTS
  triggers fire on each symbol DELETE.
- Edge["source"] -> source_name, Edge["target"] -> target_name (see CONTRACT.md).
- Edge["confidence"] -> edges.confidence (schema v2 addition).
- Comment["marker"/"text"/"line"] -> comments table (schema v3 addition).
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seam.indexer.graph import Comment, Edge, Symbol

logger = logging.getLogger(__name__)

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


def _run_migration_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Guarded migration: add edges.confidence column if this is a v1 database.

    Idempotent: if the column already exists (v2+ db or fresh db), PRAGMA table_info
    detects it and the migration is skipped silently.

    When the column is absent (v1 db), the ALTER TABLE runs exactly once, the
    schema_version is bumped to '2', and ONE info log is emitted recommending a
    re-index (old edges carry DEFAULT 'INFERRED' — conservative, not high-trust).

    Raises RuntimeError on failure so the caller knows the DB is in a bad state
    rather than silently continuing with a broken schema.
    """
    try:
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(edges)").fetchall()}
        if "confidence" not in col_names:
            # v1 db: add column. Legacy edges are conservatively INFERRED (not high-trust);
            # extract under the new resolver via a re-index for accurate tags.
            conn.execute("ALTER TABLE edges ADD COLUMN confidence TEXT NOT NULL DEFAULT 'INFERRED'")
            conn.execute("UPDATE metadata SET value = '2' WHERE key = 'schema_version'")
            # Commit this migration's own work so the chain is order-independent and
            # the ALTER does not rely on a later migration's commit to persist.
            conn.commit()
            logger.info(
                "Migrated Seam index v1->v2 (added edges.confidence). Existing edges marked "
                "INFERRED; run 'seam init' to re-index for accurate confidence tags."
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v1->v2 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Guarded migration: add clusters table + symbols.cluster_id column (v3 → v4).

    The clusters table is created by the schema script's CREATE TABLE IF NOT EXISTS,
    which runs on every init_db call before this function is reached.
    This guard handles the additive ALTER TABLE for cluster_id on existing symbols
    tables, and bumps schema_version to '4' exactly once.

    WHY: symbols tables from v1-v3 were created WITHOUT cluster_id. Since
    CREATE TABLE IF NOT EXISTS is idempotent (it skips if the table exists),
    we must ALTER the existing table to add the column. The schema script's
    CREATE TABLE includes cluster_id for new databases, but existing databases
    need the ALTER TABLE path here.

    Idempotent: if schema_version >= 4 already, skipped silently.
    Fail-loud: raises RuntimeError on any error so the caller knows the DB is bad.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version < 4:
            # Add cluster_id column to symbols if absent (existing DBs won't have it).
            col_names = {r["name"] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()}
            if "cluster_id" not in col_names:
                conn.execute("ALTER TABLE symbols ADD COLUMN cluster_id INTEGER")

            # The clusters table is already created by the schema script above;
            # just bump the version and inform the operator.
            conn.execute("UPDATE metadata SET value = '4' WHERE key = 'schema_version'")
            conn.commit()
            logger.info(
                "Migrated Seam index v%d->v4 (added clusters table + symbols.cluster_id). "
                "Run 'seam init' to compute cluster assignments.",
                version,
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v3->v4 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Guarded migration: bump schema_version from 2 to 3 (adds comments table).

    The comments table itself is created by the schema script's CREATE TABLE IF NOT EXISTS,
    which runs on every init_db call before this function is reached. This guard only
    bumps schema_version exactly once, and logs an info message telling the user to
    re-index to populate the new table.

    Idempotent: if schema_version >= 3 already, the migration is skipped silently.
    Fail-loud: raises RuntimeError on any error so the caller knows the DB is bad.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version < 3:
            # The comments table was already created by the schema script above.
            # Just bump the version and inform the operator.
            conn.execute("UPDATE metadata SET value = '3' WHERE key = 'schema_version'")
            conn.commit()
            logger.info(
                "Migrated Seam index v%d->v3 (added comments table). Existing indexes "
                "have no comments; run 'seam init' to populate semantic comment data.",
                version,
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v2->v3 failed; run 'seam init' to rebuild the index"
        ) from exc


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply the schema.

    Steps:
      1. Open connection via connect() (sets foreign_keys + busy_timeout).
      2. Verify FTS5 is available; raise RuntimeError with a clear message if not.
      3. Execute the schema SQL (idempotent — uses CREATE TABLE IF NOT EXISTS).
      4. Run guarded v1->v2 migration (adds edges.confidence if absent).

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

    # Run v1->v2 migration guard (adds edges.confidence if absent on an existing db).
    _run_migration_v1_to_v2(conn)

    # Run v2->v3 migration guard (bumps schema_version; comments table already created).
    _run_migration_v2_to_v3(conn)

    # Run v3->v4 migration guard (adds clusters table + symbols.cluster_id column).
    _run_migration_v3_to_v4(conn)

    return conn


def upsert_file(
    conn: sqlite3.Connection,
    filepath: Path,
    language: str,
    file_hash: str,
    symbols: "list[Symbol]",
    edges: "list[Edge]",
    comments: "list[Comment] | None" = None,
) -> None:
    """Atomically replace all data for a file. Idempotent: safe to call twice.

    Steps (single transaction):
      1. UPSERT the file row via ON CONFLICT DO UPDATE — keeps a STABLE id
         across re-index (INSERT OR REPLACE would churn the autoincrement id
         and strand child rows). Captures new mtime + indexed_at.
      2. Retrieve the file_id for this path.
      3. DELETE existing edges, symbols, and comments for that file_id. Both
         edges and symbols are deleted explicitly (deleting symbols does NOT
         cascade to edges — edges hang off files, not symbols). FTS triggers
         fire per-symbol DELETE. Comments are FK-cascaded but deleted explicitly
         for clarity and symmetry with the other child tables.
      4. INSERT new symbols.
      5. INSERT new edges mapping Edge['source'] -> source_name,
                                  Edge['target'] -> target_name.
      6. INSERT new comments (schema v3). Each Comment has marker, text, line.
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
        #    Comments are also deleted explicitly for symmetry (FK cascade would
        #    also handle them, but explicit is clearer and consistent).
        conn.execute("DELETE FROM edges WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM comments WHERE file_id = ?", (file_id,))

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
        #                             Edge['confidence'] -> confidence (schema v2)
        conn.executemany(
            """
            INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    edge["source"],      # Edge field 'source' -> column source_name
                    edge["target"],      # Edge field 'target' -> column target_name
                    edge["kind"],
                    file_id,
                    edge["line"],
                    edge["confidence"],  # required field — fail loud if missing
                )
                for edge in edges
            ],
        )

        # 6. Insert comments (schema v3). Defaults to [] when not provided
        #    (e.g. callers that haven't been updated yet for backward compat).
        for comment in (comments or []):
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) VALUES (?, ?, ?, ?)",
                (file_id, comment["line"], comment["marker"], comment["text"]),
            )


def delete_file(conn: sqlite3.Connection, filepath: Path) -> None:
    """Remove a file and all its symbols, edges, and comments from the index.

    Cascade (FK ON DELETE CASCADE) removes symbols, edges, and comments automatically.
    FTS triggers fire on each symbol DELETE, keeping FTS in sync.
    Silently succeeds if the path was never indexed.
    """
    with conn:
        conn.execute("DELETE FROM files WHERE path = ?", (str(filepath),))
