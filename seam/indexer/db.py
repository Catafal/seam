"""SQLite read/write operations for the Seam index.

Schema defined in docs/database/schema.sql.
All operations use explicit connections (no connection pool) — caller controls lifetime.

Key design decisions:
- init_db verifies FTS5 availability before running the schema script.
- init_db runs guarded migrations: v1->v2 (edges.confidence), v2->v3 (comments table),
  v3->v4 (clusters + cluster_id), v4->v5 (Phase 4 node enrichment fields),
  v5->v6 (Phase 5 import_mappings table).
- upsert_file is a single transaction: INSERT OR REPLACE the file row,
  DELETE old symbols/edges/comments, then re-insert. Triggers handle FTS sync.
- delete_file DELETE FROM files cascades to symbols/edges/comments via FK; FTS
  triggers fire on each symbol DELETE.
- Edge["source"] -> source_name, Edge["target"] -> target_name (see CONTRACT.md).
- Edge["confidence"] -> edges.confidence (schema v2 addition).
- Comment["marker"/"text"/"line"] -> comments table (schema v3 addition).
- Phase 4 fields: symbols gain signature, decorators (JSON text), is_exported,
  visibility, qualified_name. FTS5 rebuilt to index (name, docstring, signature).
- Phase 5 (schema v6): import_mappings table. Populated by upsert_import_mappings(),
  cleaned up by delete_import_mappings(). Per-file delete-then-insert like upsert_file.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

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
      2. schema_version < current version (5).

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

        if version >= 6:
            return  # Already up to date — no-op.

        # Version is < 6: run pending migrations in order.
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
        _run_migration_v5_to_v6(conn)

    except Exception as exc:  # noqa: BLE001
        # Do NOT raise here — failing to auto-migrate should not crash a read-only
        # command. Log at WARNING so the operator can diagnose, then continue.
        # The OperationalError would surface on the first query anyway if the schema
        # is truly broken, giving a clear diagnostic message.
        logger.warning(
            "connect(): auto-migration attempt failed (%s). Run 'seam init' to rebuild the index.",
            exc,
        )


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


def _run_migration_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Guarded migration: add Phase 4 node-enrichment columns + rebuild FTS (v4 → v5).

    Steps (all guarded — idempotent):
      1. ALTER TABLE symbols ADD COLUMN for the 5 new fields (signature, decorators,
         is_exported, visibility, qualified_name). All nullable; existing rows default NULL.
      2. DROP symbols_fts virtual table and its 3 sync triggers. FTS5 columns cannot
         be altered, so the only way to add "signature" is to drop and recreate.
      3. CREATE the new symbols_fts virtual table with columns (name, docstring, signature).
      4. Recreate the 3 sync triggers (symbols_ai, symbols_ad, symbols_au) for the new
         FTS column set.
      5. Repopulate FTS from the symbols content table (INSERT INTO symbols_fts).
      6. Parity check: assert count(symbols_fts) == count(symbols) after repopulation.
      7. Bump schema_version to '5'.

    ATOMICITY: all structural changes (ALTERs, DROP, CREATE, INSERT) and the version bump
    run inside a SINGLE explicit transaction using BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    Every DDL statement uses conn.execute(), NOT conn.executescript — the batch method
    issues an implicit COMMIT before running, which would make each DDL non-transactional.

    WHY explicit BEGIN IMMEDIATE:
        Python's sqlite3 module with the default isolation_level wraps DML in implicit
        transactions, but DDL statements (DROP TABLE, CREATE TABLE) cause implicit commits
        of the current Python-managed transaction. To make DDL transactional we must issue
        an explicit BEGIN before any DDL and ROLLBACK on failure. This guarantees that if
        the process dies or raises mid-migration, the DROP TABLE is rolled back and the
        DB is left at v4 with the old FTS intact — not in a half-migrated state.

    Guarded: runs only when the stored version is < 5.
    Idempotent: each ALTER TABLE is guarded by a PRAGMA table_info check; DROP IF EXISTS
                prevents double-drop; re-running is safe (no-op when already at v5).

    Raises RuntimeError on any failure so the caller knows the DB is in a bad state.
    """
    row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    version = int(row["value"]) if row else 0

    if version >= 5:
        return  # Already at v5 or newer — skip migration entirely.

    # Capture symbol count BEFORE starting the transaction for observability log.
    symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    logger.info(
        "Starting v%d->v5 migration — rebuilding FTS5 for %d symbols "
        "(adding signature column). DO NOT interrupt.",
        version,
        symbol_count,
    )

    # Start an explicit transaction BEFORE any DDL (DROP/CREATE TABLE/triggers).
    # Python sqlite3's implicit transaction management commits pending state on DDL;
    # an explicit BEGIN makes the DDL itself transactional so ROLLBACK reverts it.
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Step 1: Add the 5 new columns if they don't already exist.
        # Each ALTER is individually guarded so repeated calls don't fail on "duplicate column name".
        col_names = {r["name"] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()}

        new_cols = [
            ("signature", "TEXT"),
            ("decorators", "TEXT"),  # JSON-encoded list of strings
            ("is_exported", "INTEGER"),  # 0/1/NULL — SQLite stores bool as integer
            ("visibility", "TEXT"),
            ("qualified_name", "TEXT"),
        ]
        for col, col_type in new_cols:
            if col not in col_names:
                conn.execute(f"ALTER TABLE symbols ADD COLUMN {col} {col_type}")

        # Step 2: Drop the old FTS5 table and its triggers.
        # FTS5 virtual tables cannot be altered (no ALTER VIRTUAL TABLE support in SQLite),
        # so adding the 'signature' column requires a full DROP + CREATE cycle. Each DDL
        # uses conn.execute() individually — the batch alternative would issue an implicit
        # COMMIT, breaking the surrounding BEGIN IMMEDIATE transaction.
        conn.execute("DROP TRIGGER IF EXISTS symbols_ai")
        conn.execute("DROP TRIGGER IF EXISTS symbols_ad")
        conn.execute("DROP TRIGGER IF EXISTS symbols_au")
        conn.execute("DROP TABLE IF EXISTS symbols_fts")

        # Step 3: Recreate symbols_fts with the new column set (name, docstring, signature).
        conn.execute("""
            CREATE VIRTUAL TABLE symbols_fts USING fts5(
                name,
                docstring,
                signature,
                content='symbols',
                content_rowid='id'
            )
        """)

        # Step 4: Recreate the 3 sync triggers (INSERT/DELETE/UPDATE) for the new column set.
        # Individual execute() calls for the same reason as step 2: the batch alternative
        # commits implicitly, breaking the surrounding BEGIN IMMEDIATE transaction.
        conn.execute("""
            CREATE TRIGGER symbols_ai AFTER INSERT ON symbols BEGIN
                INSERT INTO symbols_fts(rowid, name, docstring, signature)
                VALUES (new.id, new.name, new.docstring, new.signature);
            END
        """)
        conn.execute("""
            CREATE TRIGGER symbols_ad AFTER DELETE ON symbols BEGIN
                INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature)
                VALUES ('delete', old.id, old.name, old.docstring, old.signature);
            END
        """)
        conn.execute("""
            CREATE TRIGGER symbols_au AFTER UPDATE ON symbols BEGIN
                INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature)
                VALUES ('delete', old.id, old.name, old.docstring, old.signature);
                INSERT INTO symbols_fts(rowid, name, docstring, signature)
                VALUES (new.id, new.name, new.docstring, new.signature);
            END
        """)

        # Step 5: Repopulate FTS from the symbols content table.
        # Pre-migration rows have signature=NULL — FTS5 treats NULL as an empty token
        # list for that column, so existing symbols are still searchable by name/docstring.
        conn.execute("""
            INSERT INTO symbols_fts(rowid, name, docstring, signature)
            SELECT id, name, docstring, signature FROM symbols
        """)

        # Step 6: Parity check — abort and roll back if FTS wasn't fully populated.
        # A partial repopulation (e.g. interrupted INSERT) silently drops search hits:
        # FTS would return results but miss any symbol whose row was never inserted.
        # Failing here ensures the ROLLBACK restores the old FTS rather than leaving
        # the index in a silently degraded state.
        fts_count = conn.execute("SELECT COUNT(*) FROM symbols_fts").fetchone()[0]
        if fts_count != symbol_count:
            raise RuntimeError(
                f"FTS repopulation parity mismatch: symbols={symbol_count}, "
                f"symbols_fts={fts_count}. Run 'seam init' to rebuild the index."
            )
        logger.info(
            "v4->v5 migration: FTS repopulated with %d rows (parity OK).",
            fts_count,
        )

        # Step 7: Bump schema_version to '5' as the very last step before commit.
        # Placing this last ensures the version is only advanced when all structural
        # changes have succeeded — a process crash between step 6 and here is safe
        # because the ROLLBACK in the except block reverts everything, leaving the DB
        # at v4 so this migration reruns cleanly on the next connect().
        conn.execute("UPDATE metadata SET value = '5' WHERE key = 'schema_version'")
        conn.execute("COMMIT")

        logger.info(
            "Migrated Seam index v%d->v5 (added Phase 4 node enrichment fields: "
            "signature, decorators, is_exported, visibility, qualified_name; "
            "FTS5 rebuilt to index signature). Run 'seam init' to populate new fields.",
            version,
        )
    except Exception as exc:  # noqa: BLE001
        # Roll back all structural changes (DROP, CREATE, ALTER, INSERT) so the DB
        # is left at v4 with the old FTS intact — not in a half-migrated state.
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass  # Best-effort rollback; if this fails the DB may need a full reinit.
        raise RuntimeError(
            "Seam DB migration v4->v5 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Guarded migration: add import_mappings table (v5 → v6).

    Additive-only: creates import_mappings table and two indexes if absent.
    Does NOT backfill existing rows — only `seam init` / watcher re-index
    populates mappings. Until then, resolution silently falls back to the
    name-count rule (documented gotcha, mirrors the Phase 4 backfill caveat).

    Steps:
      1. CREATE TABLE IF NOT EXISTS import_mappings (idempotent).
      2. CREATE INDEX IF NOT EXISTS for file_id and local_name (idempotent).
      3. Bump schema_version to '6'.

    Guarded: runs only when stored version < 6.
    Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS are safe
    on repeated calls. The version guard prevents double-bumping.
    Fresh-DB-safe: a brand-new DB seeded with schema_version='6' returns early.

    Uses BEGIN IMMEDIATE / COMMIT for atomicity (consistent with v4→v5 pattern).
    Raises RuntimeError on failure so caller knows the DB is in a bad state.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 6:
            return  # Already at v6 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: Create import_mappings table (CREATE TABLE IF NOT EXISTS is idempotent).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS import_mappings (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    local_name    TEXT NOT NULL,
                    exported_name TEXT NOT NULL,
                    source_module TEXT NOT NULL,
                    is_default    INTEGER NOT NULL DEFAULT 0,
                    is_namespace  INTEGER NOT NULL DEFAULT 0,
                    is_wildcard   INTEGER NOT NULL DEFAULT 0,
                    line          INTEGER NOT NULL
                )
            """)

            # Step 2: Create indexes (CREATE INDEX IF NOT EXISTS is idempotent).
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_import_mappings_file_id
                ON import_mappings(file_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_import_mappings_local_name
                ON import_mappings(local_name)
            """)

            # Step 3: Bump schema_version to '6' as the last step before commit.
            conn.execute("UPDATE metadata SET value = '6' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v6 (added import_mappings table). "
                "Run 'seam init' to populate import mappings for existing files.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v5->v6 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v5->v6 failed; run 'seam init' to rebuild the index"
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

    # Run v4->v5 migration guard (adds Phase 4 node enrichment fields + FTS rebuild).
    _run_migration_v4_to_v5(conn)

    # Run v5->v6 migration guard (adds import_mappings table — Phase 5).
    _run_migration_v5_to_v6(conn)

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

        # 4. Insert symbols — includes Phase 4 enrichment fields (schema v5).
        conn.executemany(
            """
            INSERT INTO symbols (
                file_id, name, kind, start_line, end_line, docstring,
                signature, decorators, is_exported, visibility, qualified_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    edge["source"],  # Edge field 'source' -> column source_name
                    edge["target"],  # Edge field 'target' -> column target_name
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
