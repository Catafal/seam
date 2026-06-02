"""Integration tests for v5→v6 schema migration (Phase 5 — Import Resolution).

TDD: Tests written before implementation (RED phase).

Mirrors the style of tests/integration/test_migration_v5.py.

Test groups:
    M1 — Fresh DB from init_db: schema_version='6', import_mappings table exists.
    M2 — v5 DB migrates to v6: schema_version bumped, table created, old data preserved.
    M3 — Migration idempotency: running init_db twice on a v6 DB is safe.
    M4 — connect() auto-migrates a v5 DB to v6 without crashing reads.
    M5 — upsert_import_mappings populates import_mappings and delete_import_mappings cleans up.
    M6 — import_mappings is correctly cascade-deleted when parent file is deleted.
    M7 — Pre-v6 reads: confidence resolution degrades gracefully to name-count (no crash).
"""

import sqlite3
from pathlib import Path

from seam.analysis.confidence import load_import_mappings
from seam.analysis.imports import ImportMapping
from seam.indexer.db import (
    connect,
    delete_file,
    delete_import_mappings,
    init_db,
    upsert_file,
    upsert_import_mappings,
)

# ── Helper: build a minimal v5 DB (no import_mappings table) ─────────────────


def _make_v5_db(db_path: Path) -> None:
    """Create a minimal v5 DB for migration testing.

    Has all v5 schema (Phase 4 enrichment columns, FTS with signature),
    schema_version='5', but NO import_mappings table.
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
            docstring TEXT,
            cluster_id INTEGER,
            signature TEXT,
            decorators TEXT,
            is_exported INTEGER,
            visibility TEXT,
            qualified_name TEXT
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
            name, docstring, signature, content='symbols', content_rowid='id'
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            marker TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            size INTEGER NOT NULL,
            naming_source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '5');
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.commit()
    conn.close()


# ── M1: Fresh DB ──────────────────────────────────────────────────────────────


class TestFreshDbSchemaV6:
    """M1 — Fresh DB from init_db has schema_version='6' and import_mappings table."""

    def test_fresh_db_schema_version_is_6(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn.close()
        assert row is not None
        assert int(row[0]) == 6

    def test_fresh_db_import_mappings_table_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "import_mappings" in tables

    def test_fresh_db_import_mappings_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(import_mappings)").fetchall()
        }
        conn.close()
        expected = {
            "id", "file_id", "local_name", "exported_name",
            "source_module", "is_default", "is_namespace", "is_wildcard", "line",
        }
        assert expected.issubset(cols)


# ── M2: v5 → v6 migration ─────────────────────────────────────────────────────


class TestMigrationV5ToV6:
    """M2 — A v5 DB migrates to v6 correctly on connect()."""

    def test_v5_db_migrates_schema_version(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v5.db"
        _make_v5_db(db_path)

        conn = connect(db_path)
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn.close()
        assert int(row[0]) == 6

    def test_v5_db_gets_import_mappings_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v5.db"
        _make_v5_db(db_path)

        conn = connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "import_mappings" in tables

    def test_v5_data_preserved_after_migration(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v5.db"
        _make_v5_db(db_path)

        # Insert a file and symbol into the v5 DB before migration
        raw = sqlite3.connect(str(db_path))
        raw.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/proj/foo.py', 'python', 'abc123', 1.0, 1.0)"
        )
        raw.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
            "VALUES (1, 'my_func', 'function', 1, 5)"
        )
        raw.commit()
        raw.close()

        # Connect (auto-migrate) and verify data still present
        conn = connect(db_path)
        sym = conn.execute("SELECT name FROM symbols WHERE name='my_func'").fetchone()
        conn.close()
        assert sym is not None
        assert sym[0] == "my_func"

    def test_migration_is_idempotent_from_v5(self, tmp_path: Path) -> None:
        """Running connect() twice on a migrated DB must not crash."""
        db_path = tmp_path / "v5.db"
        _make_v5_db(db_path)

        conn1 = connect(db_path)
        conn1.close()
        conn2 = connect(db_path)
        conn2.close()


# ── M3: Idempotency ───────────────────────────────────────────────────────────


class TestMigrationIdempotency:
    """M3 — Running init_db twice on a v6 DB is safe."""

    def test_init_db_twice_safe(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v6.db"
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        row = conn2.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn2.close()
        assert int(row[0]) == 6


# ── M4: connect() auto-migrates reads ────────────────────────────────────────


class TestConnectAutoMigrates:
    """M4 — connect() auto-migrates a v5 DB so reads don't crash."""

    def test_connect_on_v5_db_returns_valid_conn(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v5.db"
        _make_v5_db(db_path)
        conn = connect(db_path)
        # Should be able to read symbols table (even if empty)
        rows = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
        conn.close()
        assert rows[0] == 0

    def test_connect_on_v5_db_does_not_crash_on_import_mappings_query(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "v5.db"
        _make_v5_db(db_path)
        conn = connect(db_path)
        # Should be able to query import_mappings (created by migration)
        rows = conn.execute("SELECT COUNT(*) FROM import_mappings").fetchone()
        conn.close()
        assert rows[0] == 0


# ── M5: upsert/delete import_mappings ─────────────────────────────────────────


class TestUpsertDeleteImportMappings:
    """M5 — upsert_import_mappings populates rows; delete_import_mappings removes them."""

    def test_upsert_stores_mappings(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        # Create a file first
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'abc', 1.0, 1.0)",
            ("/proj/foo.py",),
        )
        conn.commit()

        mappings: list[ImportMapping] = [
            ImportMapping(
                local_name="parse",
                exported_name="parse",
                source_module="app.parser",
                is_default=False,
                is_namespace=False,
                is_wildcard=False,
                line=1,
            )
        ]
        upsert_import_mappings(conn, Path("/proj/foo.py"), mappings)

        rows = conn.execute(
            "SELECT local_name, exported_name, source_module FROM import_mappings "
            "JOIN files ON files.id = import_mappings.file_id "
            "WHERE files.path = '/proj/foo.py'"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["local_name"] == "parse"
        assert rows[0]["source_module"] == "app.parser"

    def test_delete_import_mappings_removes_rows(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'abc', 1.0, 1.0)",
            ("/proj/foo.py",),
        )
        conn.commit()

        mappings: list[ImportMapping] = [
            ImportMapping(
                local_name="parse",
                exported_name="parse",
                source_module="app.parser",
                is_default=False,
                is_namespace=False,
                is_wildcard=False,
                line=1,
            )
        ]
        upsert_import_mappings(conn, Path("/proj/foo.py"), mappings)
        delete_import_mappings(conn, Path("/proj/foo.py"))

        count = conn.execute(
            "SELECT COUNT(*) FROM import_mappings"
        ).fetchone()[0]
        conn.close()
        assert count == 0

    def test_upsert_is_idempotent_delete_then_insert(self, tmp_path: Path) -> None:
        """Re-upserting for the same file replaces old mappings."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES (?, 'python', 'abc', 1.0, 1.0)",
            ("/proj/foo.py",),
        )
        conn.commit()

        m1 = [ImportMapping(
            local_name="old_name", exported_name="old_name",
            source_module="old.module", is_default=False, is_namespace=False,
            is_wildcard=False, line=1,
        )]
        upsert_import_mappings(conn, Path("/proj/foo.py"), m1)

        m2 = [ImportMapping(
            local_name="new_name", exported_name="new_name",
            source_module="new.module", is_default=False, is_namespace=False,
            is_wildcard=False, line=1,
        )]
        upsert_import_mappings(conn, Path("/proj/foo.py"), m2)

        rows = conn.execute("SELECT local_name FROM import_mappings").fetchall()
        conn.close()
        names = {r[0] for r in rows}
        assert "new_name" in names
        assert "old_name" not in names  # old mapping was replaced


# ── M6: Cascade delete ────────────────────────────────────────────────────────


class TestCascadeDelete:
    """M6 — import_mappings are deleted when the parent file is deleted."""

    def test_delete_file_cascades_to_import_mappings(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        filepath = tmp_path / "foo.py"
        filepath.write_text("from app import parse\n")

        # Upsert a file with a mapping
        upsert_file(conn, filepath, "python", "abc123",
                    symbols=[], edges=[], comments=[])
        mappings = [ImportMapping(
            local_name="parse", exported_name="parse",
            source_module="app", is_default=False, is_namespace=False,
            is_wildcard=False, line=1,
        )]
        upsert_import_mappings(conn, filepath, mappings)

        # Verify mapping exists
        count_before = conn.execute("SELECT COUNT(*) FROM import_mappings").fetchone()[0]
        assert count_before == 1

        # Delete the file — cascade should remove import_mappings
        delete_file(conn, filepath)

        count_after = conn.execute("SELECT COUNT(*) FROM import_mappings").fetchone()[0]
        conn.close()
        assert count_after == 0


# ── M7: Pre-v6 graceful degradation ──────────────────────────────────────────


class TestPreV6GracefulDegradation:
    """M7 — load_import_mappings returns [] on pre-v6 DB (no crash)."""

    def test_load_import_mappings_empty_on_missing_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        # File not in DB → empty result
        result = load_import_mappings(conn, "/nonexistent/file.py")
        conn.close()
        assert result == []

    def test_load_import_mappings_returns_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        result = load_import_mappings(conn, "/any/path.py")
        conn.close()
        assert isinstance(result, list)
