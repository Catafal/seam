"""SQLite read/write operations for the Seam index.

Schema defined in docs/database/schema.sql.
All operations use explicit connections (no connection pool) — caller controls lifetime.

Key design decisions:
- init_db verifies FTS5 availability before running the schema script.
- init_db runs guarded migrations: v1->v2 (edges.confidence), v2->v3 (comments table),
  v3->v4 (clusters + cluster_id), v4->v5 (Phase 4 node enrichment fields),
  v5->v6 (Phase 5 import_mappings table), v6->v7 (semantic embeddings table),
  v9->v10 (Tier B B1: edges.receiver column for call-edge receiver capture).
- upsert_file is a single transaction: INSERT OR REPLACE the file row,
  DELETE old symbols/edges/comments, then re-insert. Triggers handle FTS sync.
- delete_file DELETE FROM files cascades to symbols/edges/comments via FK; FTS
  triggers fire on each symbol DELETE.
- Edge["source"] -> source_name, Edge["target"] -> target_name (see CONTRACT.md).
- Edge["confidence"] -> edges.confidence (schema v2 addition).
- Edge["receiver"] -> edges.receiver (schema v10 addition; NULL for non-attribute edges).
- Comment["marker"/"text"/"line"] -> comments table (schema v3 addition).
- Phase 4 fields: symbols gain signature, decorators (JSON text), is_exported,
  visibility, qualified_name. FTS5 rebuilt to index (name, docstring, signature).
- Phase 5 (schema v6): import_mappings table. Populated by upsert_import_mappings(),
  cleaned up by delete_import_mappings(). Per-file delete-then-insert like upsert_file.
- Semantic search (schema v7): embeddings table. Populated ONLY by `seam init --semantic`.
  Not backfilled by migration — falls back to FTS5-only when absent. ON DELETE CASCADE
  keeps embeddings in sync with symbol deletions automatically.
- Tier B B1 (schema v10): edges.receiver TEXT column. NULL on pre-v10 rows (null-contract,
  mirrors Phase 4/5 fields). Populated at index time for Python attribute calls.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from seam.analysis.processes import compute_entry_score
from seam.indexer.migrations import (
    _run_migration_v1_to_v2,
    _run_migration_v2_to_v3,
    _run_migration_v3_to_v4,
    _run_migration_v4_to_v5,
    _run_migration_v5_to_v6,
    _run_migration_v6_to_v7,
    _run_migration_v7_to_v8,
    _run_migration_v8_to_v9,
    _run_migration_v9_to_v10,
)

if TYPE_CHECKING:
    from seam.analysis.imports import ImportMapping
    from seam.indexer.graph import Comment, Edge, Symbol

logger = logging.getLogger(__name__)

# Schema SQL location. Two layouts must both work:
#   - INSTALLED wheel: shipped inside the package at seam/_data/schema.sql (via hatch
#     force-include in pyproject) — docs/ is NOT packaged, so the dev path is absent.
#   - DEV checkout / editable install: the canonical file at <repo>/docs/database/schema.sql.
# Packaged-first so a real `pip install` works; falls back to the repo copy in dev.
_PACKAGED_SCHEMA_PATH = Path(__file__).parent.parent / "_data" / "schema.sql"
_DEV_SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "database" / "schema.sql"
_SCHEMA_PATH = _PACKAGED_SCHEMA_PATH if _PACKAGED_SCHEMA_PATH.exists() else _DEV_SCHEMA_PATH

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

    Auto-migration: if the DB has an existing schema (metadata table present)
    but is older than the current version, pending guarded migrations are run.
    This prevents "no such column: signature" errors when a user upgrades Seam
    and opens a pre-v5 index via `seam start`, `seam query`, `seam context`, etc.
    without re-running `seam init`.

    Fresh-DB guard: a brand-new empty file (no metadata table) is NOT migrated
    here — init_db handles fresh DBs. The guard checks for metadata table
    existence before reading schema_version.

    Args:
        db_path: Path to the SQLite file.
        check_same_thread: Pass False for connections shared across threads
            (the watcher's observer/timer threads share one connection).
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")

    # Auto-migrate: run pending guarded migrations on an already-initialized DB.
    # Guard: only attempt when the metadata table exists (i.e. this is not a fresh
    # empty file). A fresh empty file has no metadata table yet and must go through
    # init_db() which runs the full schema creation + migrations in the right order.
    _run_pending_migrations_if_needed(conn)

    return conn


def _run_pending_migrations_if_needed(conn: sqlite3.Connection) -> None:
    """Run any pending guarded migrations on an already-initialized DB.

    WHY: connect() is called by all read-path entry points (seam start, seam query,
    seam context, seam status, the watcher, etc.). Before this function existed,
    a user with a pre-v5 DB who ran any of those commands WITHOUT re-running
    `seam init` would get OperationalError: no such column: signature because the
    new engine SELECTs include Phase 4 columns.

    Guard: only run when:
      1. The metadata table EXISTS (i.e. this is an initialized DB, not a fresh
         empty file with no schema yet).
      2. schema_version < current version (7).

    Fresh-DB safety: a brand-new empty file (no metadata table yet) is left alone —
    init_db() will create the schema and run all migrations in the correct order.

    These migrations are idempotent + version-guarded, so running them here is safe
    even if init_db() has already applied them (they become no-ops at version >= N).

    NOTE: The v1→v2, v2→v3, and v3→v4 migrations also need the schema script output
    (clusters table) to exist before they can run. For the connect() path on a v4 DB
    we only need to run v4→v5 (the only missing migration for a Phase 4 upgrade).
    The prior migrations already ran when the user first ran `seam init` at their
    respective versions, so the schema is already at v4 for a v4 DB.
    """
    try:
        # Guard 1: check if the metadata table exists at all.
        # SQLite stores table names in sqlite_master; this is a single cheap query.
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "metadata" not in tables:
            # Fresh empty file — no migrations to run (init_db handles this path).
            return

        # Guard 2: read current schema_version.
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 10:
            return  # Already up to date — no-op.

        # Version is < 10: run pending migrations in order.
        # Each migration is guarded by its own version check — safe to call
        # when already at or above that version (they become no-ops).
        if version < 2:
            _run_migration_v1_to_v2(conn)
        if version < 3:
            _run_migration_v2_to_v3(conn)
        if version < 4:
            _run_migration_v3_to_v4(conn)
        # v4→v5: Phase 4 migration (adds enrichment columns + FTS rebuild).
        if version < 5:
            _run_migration_v4_to_v5(conn)
        # v5→v6: Phase 5 migration (adds import_mappings table).
        if version < 6:
            _run_migration_v5_to_v6(conn)
        # v6→v7: Semantic search migration (adds embeddings table).
        if version < 7:
            _run_migration_v6_to_v7(conn)
        # v7→v8: P2 cluster quality migration (adds clusters.cohesion column).
        if version < 8:
            _run_migration_v7_to_v8(conn)
        # v8→v9: P6b framework entry-point scoring (adds symbols.entry_score column).
        if version < 9:
            _run_migration_v8_to_v9(conn)
        # v9→v10: Tier B B1 receiver capture (adds edges.receiver column).
        if version < 10:
            _run_migration_v9_to_v10(conn)

    except Exception as exc:  # noqa: BLE001
        # Do NOT raise here — failing to auto-migrate should not crash a read-only
        # command. Log at WARNING so the operator can diagnose, then continue.
        # The OperationalError would surface on the first query anyway if the schema
        # is truly broken, giving a clear diagnostic message.
        logger.warning(
            "connect(): auto-migration attempt failed (%s). Run 'seam init' to rebuild the index.",
            exc,
        )


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

    # Run v4->v5 migration guard (adds Phase 4 node enrichment fields + FTS rebuild).
    _run_migration_v4_to_v5(conn)

    # Run v5->v6 migration guard (adds import_mappings table — Phase 5).
    _run_migration_v5_to_v6(conn)

    # Run v6->v7 migration guard (adds embeddings table — semantic search foundation).
    _run_migration_v6_to_v7(conn)

    # Run v7->v8 migration guard (adds clusters.cohesion column — P2 cluster quality).
    _run_migration_v7_to_v8(conn)

    # Run v8->v9 migration guard (adds symbols.entry_score column — P6b framework scoring).
    _run_migration_v8_to_v9(conn)

    # Run v9->v10 migration guard (adds edges.receiver column — Tier B B1 receiver capture).
    _run_migration_v9_to_v10(conn)

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

        # 4. Insert symbols — includes Phase 4 enrichment fields (schema v5) and the
        #    P6b framework entry_score (schema v9, computed at index time from the
        #    file path pattern + decorator text — see processes.compute_entry_score).
        file_path_str = str(filepath)
        conn.executemany(
            """
            INSERT INTO symbols (
                file_id, name, kind, start_line, end_line, docstring,
                signature, decorators, is_exported, visibility, qualified_name,
                entry_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    file_id,
                    sym["name"],
                    sym["kind"],
                    sym["start_line"],
                    sym["end_line"],
                    sym.get("docstring"),
                    # Phase 4 fields — .get() so Symbol dicts without these keys remain valid
                    # (e.g. callers that bypass extract_node_fields or pre-Phase-4 test fixtures).
                    sym.get("signature"),
                    # decorators is a list; JSON-encode it for TEXT storage. Preserves order and
                    # round-trips cleanly via json.loads() in the read path (engine.context).
                    json.dumps(sym.get("decorators"))
                    if sym.get("decorators") is not None
                    else "[]",
                    # SQLite has no native bool type; 1/0/NULL maps to True/False/unknown.
                    # The (1 if x else 0) form avoids mypy's complaint about assigning bool to int.
                    (1 if sym["is_exported"] else 0)
                    if sym.get("is_exported") is not None
                    else None,
                    sym.get("visibility"),
                    sym.get("qualified_name"),
                    # P6b: framework entry-point multiplier. Pure + never-raises; the
                    # neutral baseline 1.0 is stored when nothing matches so ranking is
                    # byte-identical to raw reach for non-entry symbols.
                    compute_entry_score(file_path_str, sym.get("decorators")),
                )
                for sym in symbols
            ],
        )

        # 5. Insert edges — contract: Edge['source'] -> source_name,
        #                             Edge['target'] -> target_name
        #                             Edge['confidence'] -> confidence (schema v2)
        #                             Edge['receiver'] -> receiver (schema v10, nullable)
        conn.executemany(
            """
            INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence, receiver)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    edge["source"],    # Edge field 'source' -> column source_name
                    edge["target"],    # Edge field 'target' -> column target_name
                    edge["kind"],
                    file_id,
                    edge["line"],
                    edge["confidence"],         # required field — fail loud if missing
                    edge.get("receiver"),       # nullable: None for import/bare-call edges
                )
                for edge in edges
            ],
        )

        # 6. Insert comments (schema v3). Defaults to [] when not provided
        #    (e.g. callers that haven't been updated yet for backward compat).
        for comment in comments or []:
            conn.execute(
                "INSERT INTO comments (file_id, line, marker, text) VALUES (?, ?, ?, ?)",
                (file_id, comment["line"], comment["marker"], comment["text"]),
            )


def delete_file(conn: sqlite3.Connection, filepath: Path) -> None:
    """Remove a file and all its symbols, edges, and comments from the index.

    Cascade (FK ON DELETE CASCADE) removes symbols, edges, comments, and
    import_mappings automatically. FTS triggers fire on each symbol DELETE,
    keeping FTS in sync. Silently succeeds if the path was never indexed.
    """
    with conn:
        conn.execute("DELETE FROM files WHERE path = ?", (str(filepath),))


def upsert_import_mappings(
    conn: sqlite3.Connection,
    filepath: Path,
    mappings: "list[ImportMapping]",
) -> None:
    """Replace all import mappings for a file (delete-then-insert, single transaction).

    This mirrors the upsert_file delete-children pattern: delete old rows for this
    file_id then insert the new set. Idempotent: safe to call repeatedly for the
    same file.

    Called by index_one_file() after upsert_file() so that the file row and its
    file_id already exist. Silently no-ops if the file is not in the DB.

    Args:
        conn:     Open SQLite connection with write access.
        filepath: Absolute path of the indexed source file.
        mappings: Import mapping records extracted from the file's AST.
    """
    row = conn.execute("SELECT id FROM files WHERE path = ?", (str(filepath),)).fetchone()
    if row is None:
        # File not in index — no-op (could happen if indexing was skipped).
        return

    file_id: int = row["id"]
    with conn:
        # Delete existing mappings for this file (idempotent on re-index)
        conn.execute("DELETE FROM import_mappings WHERE file_id = ?", (file_id,))
        # Insert new mappings
        if mappings:
            conn.executemany(
                """
                INSERT INTO import_mappings
                    (file_id, local_name, exported_name, source_module,
                     is_default, is_namespace, is_wildcard, line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        file_id,
                        m["local_name"],
                        m["exported_name"],
                        m["source_module"],
                        1 if m["is_default"] else 0,
                        1 if m["is_namespace"] else 0,
                        1 if m["is_wildcard"] else 0,
                        m["line"],
                    )
                    for m in mappings
                ],
            )


def delete_import_mappings(conn: sqlite3.Connection, filepath: Path) -> None:
    """Remove all import mappings for a file from the index.

    Silently succeeds if the path was never indexed or has no mappings.
    Called by watcher on file delete to clean up stale mappings before
    the FK cascade has a chance to handle it (belt-and-suspenders).

    Args:
        conn:     Open SQLite connection with write access.
        filepath: Absolute path of the source file being removed.
    """
    row = conn.execute("SELECT id FROM files WHERE path = ?", (str(filepath),)).fetchone()
    if row is None:
        return
    file_id: int = row["id"]
    with conn:
        conn.execute("DELETE FROM import_mappings WHERE file_id = ?", (file_id,))
