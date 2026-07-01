"""Schema v13->v14 migration tests: config/resource metadata tables."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path


def _make_v13_db(db_path: Path) -> None:
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
            qualified_name TEXT,
            entry_score REAL,
            search_text TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'INFERRED',
            receiver TEXT,
            synthesized_by TEXT
        );
        CREATE VIRTUAL TABLE symbols_fts USING fts5(
            name, docstring, signature, search_text,
            content='symbols', content_rowid='id'
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
        CREATE TABLE import_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            local_name TEXT NOT NULL,
            exported_name TEXT NOT NULL,
            source_module TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            is_namespace INTEGER NOT NULL DEFAULT 0,
            is_wildcard INTEGER NOT NULL DEFAULT 0,
            line INTEGER NOT NULL
        );
        CREATE TABLE embeddings (
            symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL
        );
        CREATE TABLE routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            symbol_name TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            normalized_path TEXT NOT NULL,
            framework TEXT NOT NULL,
            handler TEXT,
            line INTEGER NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'INFERRED',
            provenance TEXT NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata(key, value) VALUES ('schema_version', '13');
        INSERT INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.close()


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_v13_db_gains_config_resource_tables_on_init() -> None:
    from seam.indexer.db import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        db_path = Path(file.name)
    try:
        _make_v13_db(db_path)
        pre = sqlite3.connect(str(db_path))
        pre.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
            "VALUES ('/old.py', 'python', 'abc', 1.0, 1.0)"
        )
        pre.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
            "VALUES (1, 'old_handler', 'function', 1, 2)"
        )
        pre.commit()
        pre.close()

        conn = init_db(db_path)
        try:
            assert {
                "file_id",
                "symbol_name",
                "key",
                "normalized_key",
                "source_family",
                "role",
                "value_state",
                "value_category",
                "line",
                "confidence",
                "provenance",
            } <= _cols(conn, "config_keys")
            assert {
                "file_id",
                "symbol_name",
                "name",
                "normalized_name",
                "category",
                "source_family",
                "line",
                "confidence",
                "provenance",
            } <= _cols(conn, "resources")
            version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            assert version is not None
            assert int(version[0]) >= 14
            assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM config_keys").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_v14_migration_is_idempotent() -> None:
    from seam.indexer.db import init_db
    from seam.indexer.migrations import _run_migration_v13_to_v14

    conn = init_db(Path(":memory:"))
    try:
        _run_migration_v13_to_v14(conn)
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        assert version is not None
        assert int(version[0]) >= 14
        assert "symbol_name" in _cols(conn, "config_keys")
        assert "symbol_name" in _cols(conn, "resources")
    finally:
        conn.close()
