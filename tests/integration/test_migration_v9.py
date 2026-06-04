"""P6b — v8→v9 migration: symbols.entry_score column (framework entry-point scoring).

Covers:
  - Fresh DB from init_db has the symbols.entry_score column + schema_version >= 9.
  - An old (v8) DB gains the column on connect()/init_db.
  - Migration is idempotent (running twice does not error or downgrade).
  - Additive only: existing symbol rows survive (entry_score NULL until re-index).
"""

import sqlite3
import tempfile
from pathlib import Path


def _make_v8_db(db_path: Path) -> None:
    """Create a minimal v8 DB (clusters.cohesion present, symbols WITHOUT entry_score)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            language TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            mtime REAL NOT NULL,
            indexed_at REAL NOT NULL
        );
        CREATE TABLE symbols (
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
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'INFERRED'
        );
        CREATE VIRTUAL TABLE symbols_fts USING fts5(
            name, docstring, signature, content='symbols', content_rowid='id'
        );
        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            marker TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            size INTEGER NOT NULL,
            naming_source TEXT NOT NULL,
            cohesion REAL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata(key, value) VALUES ('schema_version', '8');
        INSERT INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.close()


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_db_has_entry_score_column() -> None:
    from seam.indexer.db import init_db

    conn = init_db(Path(":memory:"))
    try:
        assert "entry_score" in _cols(conn, "symbols")
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) >= 9
    finally:
        conn.close()


def test_v8_db_gains_entry_score_column() -> None:
    from seam.indexer.db import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _make_v8_db(db_path)
        # Insert a pre-migration row so we can verify additive behaviour.
        pre = sqlite3.connect(str(db_path))
        pre.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/v.py', 'python', 'abc', 1.0, 1.0)"
        )
        pre.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (1, 'old_sym', 'function', 1, 2)"
        )
        pre.commit()
        pre.close()

        conn = init_db(db_path)
        assert "entry_score" in _cols(conn, "symbols")
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) >= 9
        # Additive: old row survives, entry_score NULL (no backfill).
        row = conn.execute(
            "SELECT entry_score FROM symbols WHERE name='old_sym'"
        ).fetchone()
        assert row is not None
        assert row[0] is None
        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_migration_idempotent() -> None:
    from seam.indexer.db import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _make_v8_db(db_path)
        conn1 = init_db(db_path)
        v1 = conn1.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        conn1.close()
        conn2 = init_db(db_path)
        v2 = conn2.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        conn2.close()
        assert v1 == v2
    finally:
        db_path.unlink(missing_ok=True)
