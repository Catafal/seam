"""Schema migration functions for the Seam SQLite index.

Each function is a guarded, idempotent migration that advances the DB schema
by one version. All take an open sqlite3.Connection and commit their own work.

Design rules (shared with db.py):
- Each migration is version-guarded: reads schema_version and short-circuits
  if already at or beyond the target version.
- Each migration commits its own transaction (BEGIN IMMEDIATE / COMMIT).
- Each migration raises RuntimeError on failure so the caller knows the DB is
  in a bad state rather than silently continuing.
- No external dependencies: stdlib only (sqlite3, logging). Leaf module.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


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


def _run_migration_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Guarded migration: add embeddings table (v6 → v7).

    Additive-only: creates the embeddings table if absent.
    Does NOT backfill — the table is populated ONLY by `seam init --semantic`.
    Until then, read paths that check for embeddings will find an empty table
    and degrade gracefully to FTS5-only (documented gotcha).

    Steps:
      1. CREATE TABLE IF NOT EXISTS embeddings (idempotent).
      2. Bump schema_version to '7'.

    Guarded: runs only when stored version < 7.
    Idempotent: CREATE TABLE IF NOT EXISTS is safe on repeated calls.
                The version guard prevents double-bumping.
    Fresh-DB-safe: a brand-new DB seeded with schema_version='7' returns early.

    Uses BEGIN IMMEDIATE / COMMIT for atomicity (consistent with v5→v6 pattern).
    Raises RuntimeError on failure so caller knows the DB is in a bad state.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 7:
            return  # Already at v7 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: Create embeddings table (CREATE TABLE IF NOT EXISTS is idempotent).
            # symbol_id is PRIMARY KEY (one embedding per symbol row) and a FK to
            # symbols(id) with ON DELETE CASCADE so re-indexing a file automatically
            # removes stale embeddings for deleted/replaced symbol rows.
            # Vector stored as float32 bytes (numpy.array(..., dtype=np.float32).tobytes()).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
                    model     TEXT NOT NULL,
                    dim       INTEGER NOT NULL,
                    vector    BLOB NOT NULL
                )
            """)

            # Step 2: Bump schema_version to '7' as the last step before commit.
            conn.execute("UPDATE metadata SET value = '7' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v7 (added embeddings table for semantic search). "
                "Run 'seam init --semantic' to populate embeddings.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v6->v7 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v6->v7 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Guarded migration: add clusters.cohesion column (v7 → v8).

    Additive-only: adds a nullable REAL column to the clusters table if absent.
    Does NOT backfill — cohesion stays NULL on existing cluster rows until the
    next `seam init` recomputes clusters (which writes the value). Until then,
    the search rescore path treats NULL cohesion as "no bonus" (byte-identical
    ranking), so the additive migration changes nothing for existing indexes.

    Steps:
      1. ALTER TABLE clusters ADD COLUMN cohesion REAL (guarded by table_info).
      2. Bump schema_version to '8'.

    Guarded: runs only when stored version < 8.
    Idempotent: the PRAGMA table_info check skips the ALTER when the column
                already exists; the version guard prevents double-bumping.
    Fresh-DB-safe: a brand-new DB seeded with schema_version='8' returns early.

    Uses BEGIN IMMEDIATE / COMMIT for atomicity (consistent with v6→v7 pattern).
    Raises RuntimeError on failure so caller knows the DB is in a bad state.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 8:
            return  # Already at v8 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: add the nullable cohesion column if it does not already exist.
            col_names = {
                r["name"] for r in conn.execute("PRAGMA table_info(clusters)").fetchall()
            }
            if "cohesion" not in col_names:
                conn.execute("ALTER TABLE clusters ADD COLUMN cohesion REAL")

            # Step 2: bump schema_version to '8' as the last step before commit.
            conn.execute("UPDATE metadata SET value = '8' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v8 (added clusters.cohesion column). "
                "Run 'seam init' to populate cohesion for existing clusters.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v7->v8 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v7->v8 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Guarded migration: add symbols.entry_score column (v8 → v9).

    Additive-only: adds a nullable REAL column to the symbols table if absent.
    Does NOT backfill — entry_score stays NULL on existing symbol rows until the
    next `seam init` re-indexes (upsert_file computes the score from the file
    path pattern + decorator text). Until then, list_entry_points() treats NULL
    entry_score as the neutral baseline (1.0), so the additive migration changes
    nothing for existing indexes (byte-identical ranking).

    Steps:
      1. ALTER TABLE symbols ADD COLUMN entry_score REAL (guarded by table_info).
      2. Bump schema_version to '9'.

    Guarded: runs only when stored version < 9.
    Idempotent: the PRAGMA table_info check skips the ALTER when the column
                already exists; the version guard prevents double-bumping.
    Fresh-DB-safe: a brand-new DB seeded with schema_version='9' returns early.

    Uses BEGIN IMMEDIATE / COMMIT for atomicity (consistent with v7→v8 pattern).
    Raises RuntimeError on failure so caller knows the DB is in a bad state.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 9:
            return  # Already at v9 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: add the nullable entry_score column if it does not already exist.
            col_names = {
                r["name"] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()
            }
            if "entry_score" not in col_names:
                conn.execute("ALTER TABLE symbols ADD COLUMN entry_score REAL")

            # Step 2: bump schema_version to '9' as the last step before commit.
            conn.execute("UPDATE metadata SET value = '9' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v9 (added symbols.entry_score column). "
                "Run 'seam init' to populate entry_score for framework entry-point ranking.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v8->v9 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v8->v9 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Guarded migration: add edges.receiver column (v9 → v10).

    Tier B B1 addition: captures the raw receiver expression text from attribute-call
    edges (e.g., `recv.method()` → receiver='recv'). This enables later receiver-type
    inference (Tier B slices B2+) without requiring another schema change.

    Additive-only: adds a nullable TEXT column to the edges table if absent.
    Does NOT backfill — receiver stays NULL on existing rows until re-index.
    The null-contract mirrors Phase 4/5 fields: NULL means "not yet captured" or
    "not applicable" (import edges, bare calls), not "has no receiver".

    Steps:
      1. ALTER TABLE edges ADD COLUMN receiver TEXT (guarded by table_info).
      2. Bump schema_version to '10'.

    Guarded: runs only when stored version < 10.
    Idempotent: the PRAGMA table_info check skips the ALTER when the column
                already exists; the version guard prevents double-bumping.
    Fresh-DB-safe: a brand-new DB seeded with schema_version='10' returns early.
    Uses BEGIN IMMEDIATE / COMMIT for atomicity (consistent with v8→v9 pattern).
    Raises RuntimeError on failure so caller knows the DB is in a bad state.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 10:
            return  # Already at v10 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: add the nullable receiver column if it does not already exist.
            # Guarded by table_info to make repeated calls safe (idempotent).
            col_names = {
                r["name"] for r in conn.execute("PRAGMA table_info(edges)").fetchall()
            }
            if "receiver" not in col_names:
                conn.execute("ALTER TABLE edges ADD COLUMN receiver TEXT")

            # Step 2: bump schema_version to '10' as the last step before commit.
            # Placing this last guarantees the version is only advanced when the
            # structural change has succeeded.
            conn.execute("UPDATE metadata SET value = '10' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v10 (added edges.receiver column for Tier B "
                "receiver-type inference). Run 'seam init' to populate receiver for "
                "existing Python attribute calls.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v9->v10 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v9->v10 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v11_to_v12(conn: sqlite3.Connection) -> None:
    """Guarded migration: add edges.synthesized_by column (v11 → v12).

    Edge-synthesis post-pass addition: captures which synthesis channel produced an
    edge (e.g. 'interface-override'). NULL = statically extracted by a parser;
    a channel name string = synthesized by the post-pass. Provenance is derived:
    synthesized_by IS NOT NULL ⟹ heuristic. No separate provenance column needed.

    Additive-only: adds a nullable TEXT column to the edges table if absent.
    Does NOT backfill — synthesized_by stays NULL on existing rows. Synthesized
    edges appear only after the next full `seam init` (explicit backfill, same
    null-contract as prior enrichment columns). Toggling SEAM_EDGE_SYNTHESIS
    requires a re-index to take effect.

    Steps:
      1. ALTER TABLE edges ADD COLUMN synthesized_by TEXT (guarded by table_info).
      2. Bump schema_version to '12'.

    Guarded: runs only when stored version < 12.
    Idempotent: the PRAGMA table_info check skips the ALTER when the column
                already exists; the version guard prevents double-bumping.
    Fresh-DB-safe: a brand-new DB seeded with schema_version='12' returns early.
    Uses BEGIN IMMEDIATE / COMMIT for atomicity (consistent with v9→v10 pattern).
    Raises RuntimeError on failure so caller knows the DB is in a bad state.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 12:
            return  # Already at v12 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: add the nullable synthesized_by column if it does not already exist.
            # Guarded by table_info to make repeated calls safe (idempotent).
            col_names = {
                r["name"] for r in conn.execute("PRAGMA table_info(edges)").fetchall()
            }
            if "synthesized_by" not in col_names:
                conn.execute("ALTER TABLE edges ADD COLUMN synthesized_by TEXT")

            # Step 2: bump schema_version to '12' as the last step before commit.
            # Placing this last guarantees the version is only advanced when the
            # structural change has succeeded.
            conn.execute("UPDATE metadata SET value = '12' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v12 (added edges.synthesized_by column for "
                "edge-synthesis post-pass provenance). Run 'seam init' to populate "
                "synthesized_by for synthesized edges.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v11->v12 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v11->v12 failed; run 'seam init' to rebuild the index"
        ) from exc


def _run_migration_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Guarded migration: add symbols.search_text + a 4th symbols_fts column (v10 → v11).

    Tier D #12: identifier compound-split tokenization. Adds a nullable search_text column
    holding camelCase/snake_case-split tokens, surfaced as a 4th FTS5 column so that a
    natural-language query matches a camelCase identifier (the unicode61 tokenizer does not
    split camelCase). Changing the FTS column set requires DROPping and recreating the
    symbols_fts virtual table + its 3 sync triggers, then a 'rebuild'.

    Additive-only / null-contract: search_text is NULL on existing rows after the rebuild —
    full split-token recall arrives only after a `seam init` re-index. Until then, search
    behaves exactly as before (the name/docstring/signature columns are unchanged).

    Steps:
      1. ALTER TABLE symbols ADD COLUMN search_text TEXT (guarded by table_info).
      2. DROP the 3 sync triggers + symbols_fts; recreate symbols_fts with 4 columns and
         the 3 triggers; INSERT ... VALUES('rebuild') to repopulate from the content table.
      3. Bump schema_version to '11'.

    Guarded (version < 11), idempotent (table_info guard + version guard), fresh-DB-safe
    (a new DB seeded with '11' returns early). BEGIN IMMEDIATE / COMMIT for atomicity.
    Raises RuntimeError on failure.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0

        if version >= 11:
            return  # Already at v11 or newer — no-op.

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Step 1: add the nullable search_text column if absent (idempotent).
            col_names = {
                r["name"] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()
            }
            if "search_text" not in col_names:
                conn.execute("ALTER TABLE symbols ADD COLUMN search_text TEXT")

            # Step 2: rebuild the FTS table with the new column set. The FTS5 column list
            # is fixed at creation, so a column addition requires a full drop+recreate.
            # Triggers must be dropped first (they reference the old column list).
            # The 'rebuild' below re-tokenizes every symbol; on a large index this is a
            # multi-second write under BEGIN IMMEDIATE, and it fires on the FIRST process to
            # open the DB after upgrade (often a read command). Log up front so a momentarily
            # slow `seam query`/`start` right after upgrading is explainable, not mysterious.
            logger.info(
                "Migrating Seam index v%d->v11: rebuilding FTS index for camelCase search "
                "(one-time; may take a moment on a large index)...",
                version,
            )
            conn.execute("DROP TRIGGER IF EXISTS symbols_ai")
            conn.execute("DROP TRIGGER IF EXISTS symbols_ad")
            conn.execute("DROP TRIGGER IF EXISTS symbols_au")
            conn.execute("DROP TABLE IF EXISTS symbols_fts")
            conn.execute(
                """
                CREATE VIRTUAL TABLE symbols_fts USING fts5(
                    name, docstring, signature, search_text,
                    content='symbols', content_rowid='id'
                )
                """
            )
            conn.execute(
                """
                CREATE TRIGGER symbols_ai AFTER INSERT ON symbols BEGIN
                    INSERT INTO symbols_fts(rowid, name, docstring, signature, search_text)
                    VALUES (new.id, new.name, new.docstring, new.signature, new.search_text);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER symbols_ad AFTER DELETE ON symbols BEGIN
                    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature, search_text)
                    VALUES ('delete', old.id, old.name, old.docstring, old.signature, old.search_text);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER symbols_au AFTER UPDATE ON symbols BEGIN
                    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature, search_text)
                    VALUES ('delete', old.id, old.name, old.docstring, old.signature, old.search_text);
                    INSERT INTO symbols_fts(rowid, name, docstring, signature, search_text)
                    VALUES (new.id, new.name, new.docstring, new.signature, new.search_text);
                END
                """
            )
            # Repopulate the FTS index from the content table (search_text = NULL on old rows).
            conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")

            # Step 3: bump version last — only advance once the rebuild succeeded.
            conn.execute("UPDATE metadata SET value = '11' WHERE key = 'schema_version'")
            conn.execute("COMMIT")

            logger.info(
                "Migrated Seam index v%d->v11 (added symbols.search_text + 4th symbols_fts "
                "column for camelCase search recall). Run 'seam init' to populate search_text.",
                version,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Seam DB migration v10->v11 failed; run 'seam init' to rebuild the index"
            ) from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Seam DB migration v10->v11 failed; run 'seam init' to rebuild the index"
        ) from exc
