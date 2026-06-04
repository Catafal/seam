"""Integration tests for v6→v7 schema migration (semantic search foundation).

TDD: Tests written before implementation (RED phase).

Mirrors the style of tests/integration/test_migration_v6.py.

Test groups:
    M1 — Fresh DB from init_db: schema_version='7', embeddings table exists with correct cols.
    M2 — v6 DB migrates to v7: schema_version bumped, embeddings table created, old data preserved.
    M3 — Migration idempotency: running connect() / init_db twice on a v7 DB is safe.
    M4 — connect() auto-migrates a v6 DB to v7 without breaking existing reads.
"""

import sqlite3
import struct
from pathlib import Path

from seam.indexer.db import connect, init_db

# ── Helper: build a minimal v6 DB (no embeddings table) ─────────────────


def _make_v6_db(db_path: Path) -> None:
    """Create a minimal v6 DB for migration testing.

    Has all v6 schema (import_mappings table, schema_version='6'),
    but NO embeddings table.
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
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '6');
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.commit()
    conn.close()


# ── M1: Fresh DB ──────────────────────────────────────────────────────────────


class TestFreshDbSchemaV7:
    """M1 — Fresh DB from init_db has schema_version='7' and embeddings table."""

    def test_fresh_db_schema_version_is_7(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn.close()
        assert row is not None
        assert int(row[0]) >= 7

    def test_fresh_db_embeddings_table_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "embeddings" in tables

    def test_fresh_db_embeddings_columns(self, tmp_path: Path) -> None:
        """embeddings table has symbol_id, model, dim, vector columns."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(embeddings)").fetchall()
        }
        conn.close()
        expected = {"symbol_id", "model", "dim", "vector"}
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_fresh_db_embeddings_is_empty(self, tmp_path: Path) -> None:
        """Fresh DB has an empty embeddings table (no rows)."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert count == 0


# ── M2: v6 → v7 migration ─────────────────────────────────────────────────────


class TestMigrationV6ToV7:
    """M2 — A v6 DB migrates to v7 correctly on connect()."""

    def test_v6_db_migrates_schema_version(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)

        conn = connect(db_path)
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn.close()
        assert int(row[0]) >= 7

    def test_v6_db_gets_embeddings_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)

        conn = connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "embeddings" in tables

    def test_v6_data_preserved_after_migration(self, tmp_path: Path) -> None:
        """Existing files + symbols are preserved after v6→v7 migration."""
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)

        # Insert a file and symbol into the v6 DB before migration
        raw = sqlite3.connect(str(db_path))
        raw.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/proj/bar.py', 'python', 'def456', 1.0, 1.0)"
        )
        raw.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
            "VALUES (1, 'existing_symbol', 'function', 1, 5)"
        )
        raw.commit()
        raw.close()

        # Connect (auto-migrate) and verify data still present
        conn = connect(db_path)
        sym = conn.execute(
            "SELECT name FROM symbols WHERE name='existing_symbol'"
        ).fetchone()
        conn.close()
        assert sym is not None
        assert sym[0] == "existing_symbol"

    def test_migration_is_idempotent_from_v6(self, tmp_path: Path) -> None:
        """Running connect() twice on a migrated v7 DB must not crash."""
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)

        conn1 = connect(db_path)
        conn1.close()
        conn2 = connect(db_path)
        row = conn2.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn2.close()
        # Should still be v7 after second open
        assert int(row[0]) >= 7

    def test_embeddings_table_can_insert_and_query(self, tmp_path: Path) -> None:
        """After migration, we can write and read synthetic vectors to embeddings."""
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)

        # Insert a symbol first (embeddings.symbol_id is a FK to symbols.id)
        raw = sqlite3.connect(str(db_path))
        raw.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/proj/baz.py', 'python', 'abc', 1.0, 1.0)"
        )
        raw.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
            "VALUES (1, 'embed_me', 'function', 1, 5)"
        )
        raw.commit()
        sym_id = raw.execute("SELECT id FROM symbols WHERE name='embed_me'").fetchone()[0]
        raw.close()

        conn = connect(db_path)
        # Write a synthetic float32 vector (3 dims)
        vec = struct.pack("3f", 0.1, 0.2, 0.3)
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (sym_id, "test-model", 3, vec),
        )
        conn.commit()

        row = conn.execute(
            "SELECT model, dim, vector FROM embeddings WHERE symbol_id = ?",
            (sym_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["model"] == "test-model"
        assert row["dim"] == 3
        # Verify the stored bytes match what we inserted
        recovered = struct.unpack("3f", row["vector"])
        assert abs(recovered[0] - 0.1) < 1e-5
        assert abs(recovered[1] - 0.2) < 1e-5
        assert abs(recovered[2] - 0.3) < 1e-5


# ── M3: Migration idempotency ─────────────────────────────────────────────────


class TestMigrationV7Idempotent:
    """M3 — Running init_db / connect() twice on a v7 DB is safe."""

    def test_init_db_twice_safe(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v7.db"
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        row = conn2.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn2.close()
        assert int(row[0]) >= 7

    def test_connect_twice_safe_on_v7(self, tmp_path: Path) -> None:
        """connect() is idempotent on a v7 DB — no double-migration, no crash."""
        db_path = tmp_path / "v7.db"
        c1 = init_db(db_path)
        c1.close()
        c2 = connect(db_path)
        c2.close()
        c3 = connect(db_path)
        row = c3.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        c3.close()
        assert int(row[0]) >= 7


# ── M4: connect() auto-migrates a v6 DB ────────────────────────────────────────


class TestConnectAutoMigratesV7:
    """M4 — connect() auto-migrates a v6 DB to v7 without breaking reads."""

    def test_connect_on_v6_db_returns_valid_conn(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)
        conn = connect(db_path)
        # Existing tables must still be readable
        rows = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
        conn.close()
        assert rows[0] == 0

    def test_connect_on_v6_db_creates_embeddings_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)
        conn = connect(db_path)
        # embeddings table should exist after auto-migration
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert count == 0  # empty but queryable

    def test_connect_on_v6_db_does_not_drop_import_mappings(self, tmp_path: Path) -> None:
        """v6→v7 migration must not destroy the import_mappings table."""
        db_path = tmp_path / "v6.db"
        _make_v6_db(db_path)
        conn = connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM import_mappings").fetchone()[0]
        conn.close()
        assert count == 0  # empty but still there
