"""Tests for v3→v4 schema migration and cluster table creation.

TDD: Tests written before implementation.

Test groups:
    M1 — Fresh DB from init_db: schema_version='4', clusters table exists,
          symbols.cluster_id column exists.
    M2 — v3 DB migrates to v4: schema_version bumped, tables/columns added.
    M3 — Migration is idempotent: running init_db on a v4 DB is safe.
    M4 — symbols.cluster_id column is nullable (allows NULL for unpopulated rows).
"""

import sqlite3
import tempfile
from pathlib import Path

# ── Helper: build a minimal v3 DB (no clusters table, no cluster_id column) ──

def _make_v3_db(db_path: Path) -> None:
    """Create a minimal v3 DB for migration testing.

    Matches the v3 schema: has comments table, schema_version='3',
    but NO clusters table and NO symbols.cluster_id column.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            language TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            mtime REAL NOT NULL,
            indexed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            docstring TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'INFERRED'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
            name, docstring, content='symbols', content_rowid='id'
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            marker TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '3');
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('seam_version', '0.1.0');
    """)
    conn.close()


# ── M1: Fresh DB ──────────────────────────────────────────────────────────────


class TestFreshDBSchemaV4:
    """M1: A fresh DB from init_db has schema_version='4' and v4 tables."""

    def test_fresh_db_schema_version_is_4(self) -> None:
        """Fresh DB → schema_version='4'."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "4"

    def test_fresh_db_has_clusters_table(self) -> None:
        """Fresh DB → clusters table exists."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "clusters" in tables, f"Expected 'clusters' table, found: {tables}"

    def test_fresh_db_symbols_has_cluster_id_column(self) -> None:
        """Fresh DB → symbols table has cluster_id column."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        col_names = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
        }
        conn.close()
        assert "cluster_id" in col_names, f"Expected cluster_id in symbols, found: {col_names}"

    def test_fresh_db_clusters_table_schema(self) -> None:
        """clusters table has id, label, size, naming_source columns."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        col_names = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(clusters)").fetchall()
        }
        conn.close()
        assert "id" in col_names
        assert "label" in col_names
        assert "size" in col_names
        assert "naming_source" in col_names


# ── M2: v3 DB migrates to v4 ─────────────────────────────────────────────────


class TestMigrationV3ToV4:
    """M2: Opening a v3 DB via init_db bumps schema_version to '4'."""

    def test_v3_db_schema_version_bumped_to_4(self) -> None:
        """init_db on a v3 DB bumps schema_version to '4'."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            _make_v3_db(db_path)
            conn = init_db(db_path)
            row = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[0] == "4", f"Expected '4', got {row[0]!r}"
        finally:
            db_path.unlink(missing_ok=True)

    def test_v3_db_gets_clusters_table(self) -> None:
        """After v3→v4 migration, clusters table exists."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            _make_v3_db(db_path)
            conn = init_db(db_path)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            conn.close()
            assert "clusters" in tables
        finally:
            db_path.unlink(missing_ok=True)

    def test_v3_db_symbols_gets_cluster_id_column(self) -> None:
        """After migration, symbols.cluster_id column exists."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            _make_v3_db(db_path)
            conn = init_db(db_path)
            col_names = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
            }
            conn.close()
            assert "cluster_id" in col_names
        finally:
            db_path.unlink(missing_ok=True)

    def test_existing_symbols_survive_migration(self) -> None:
        """Migration is additive: existing symbol rows are preserved."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            _make_v3_db(db_path)

            # Insert a file + symbol row before migration
            pre_conn = sqlite3.connect(str(db_path))
            pre_conn.execute(
                "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
                " VALUES ('/test.py', 'python', 'abc', 1.0, 1.0)"
            )
            pre_conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                " VALUES (1, 'my_fn', 'function', 1, 10)"
            )
            pre_conn.commit()
            pre_conn.close()

            conn = init_db(db_path)
            count = conn.execute("SELECT COUNT(*) FROM symbols WHERE name='my_fn'").fetchone()[0]
            conn.close()
            assert count == 1, "Existing symbols must survive migration"
        finally:
            db_path.unlink(missing_ok=True)


# ── M3: Migration idempotent ──────────────────────────────────────────────────


class TestMigrationIdempotent:
    """M3: Running init_db on a v4 DB is safe and does not error."""

    def test_v4_db_migration_idempotent(self) -> None:
        """Running init_db twice on a v4 DB doesn't error or downgrade version."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            # First call creates v4 DB
            conn1 = init_db(db_path)
            conn1.close()

            # Second call must not raise and must leave version at '4'
            conn2 = init_db(db_path)
            row = conn2.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            conn2.close()
            assert row[0] == "4"
        finally:
            db_path.unlink(missing_ok=True)

    def test_in_memory_db_idempotent(self) -> None:
        """init_db(':memory:') called once produces v4 DB without error."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        conn.close()
        assert version == "4"


# ── M4: symbols.cluster_id is nullable ───────────────────────────────────────


class TestClusterIdNullable:
    """M4: symbols.cluster_id must be nullable (pre-clustering symbols have NULL)."""

    def test_cluster_id_can_be_null(self) -> None:
        """Inserting a symbol without cluster_id stores NULL successfully."""
        from seam.indexer.db import init_db, upsert_file
        from seam.indexer.graph import Symbol

        conn = init_db(Path(":memory:"))

        # Use upsert_file which doesn't set cluster_id (it's set by clustering post-pass)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            tmp = Path(f.name)
        tmp.write_text("x = 1\n")
        try:
            syms = [Symbol(name="my_fn", kind="function", file=str(tmp),
                           start_line=1, end_line=1, docstring=None)]
            upsert_file(conn, tmp, "python", "abc", syms, [], [])
            row = conn.execute("SELECT cluster_id FROM symbols WHERE name='my_fn'").fetchone()
            assert row is not None
            assert row[0] is None, f"cluster_id should be NULL before clustering, got {row[0]}"
        finally:
            tmp.unlink(missing_ok=True)
            conn.close()
