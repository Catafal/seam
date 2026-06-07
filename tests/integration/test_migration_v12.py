"""Schema v11→v12 migration tests: edges.synthesized_by column.

Covers:
  - Fresh DB from init_db has the edges.synthesized_by column + schema_version >= 12.
  - An old (v11) DB gains the column on connect()/init_db (auto-migration).
  - Migration is idempotent (running twice does not error or downgrade).
  - Additive only: existing edge rows survive (synthesized_by NULL until re-index).
  - Migration never raises on a malformed/empty DB.
  - The _run_migration_v11_to_v12 function itself is idempotent and guarded.
"""

import sqlite3
import tempfile
from pathlib import Path


def _make_v11_db(db_path: Path) -> None:
    """Create a minimal v11 DB (symbols.search_text present, edges WITHOUT synthesized_by).

    Mirrors the _make_v8_db pattern from test_migration_v9.py and
    the v11 schema as of the Tier D #12 milestone.
    """
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
            receiver TEXT
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
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata(key, value) VALUES ('schema_version', '11');
        INSERT INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.close()


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return set of column names for a table."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_db_has_synthesized_by_column() -> None:
    """A brand-new DB created by init_db must have edges.synthesized_by and version >= 12."""
    from seam.indexer.db import init_db

    conn = init_db(Path(":memory:"))
    try:
        assert "synthesized_by" in _cols(conn, "edges"), (
            "Fresh DB must have edges.synthesized_by column"
        )
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) >= 12, f"Expected schema_version >= 12, got {version}"
    finally:
        conn.close()


def test_v11_db_gains_synthesized_by_column() -> None:
    """An old v11 DB must gain the synthesized_by column after connect()/init_db."""
    from seam.indexer.db import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        # Build a real v11 DB on disk.
        _make_v11_db(db_path)

        # Insert a pre-migration edge row to verify additive behavior.
        pre = sqlite3.connect(str(db_path))
        pre.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/old.py', 'python', 'abc123', 1.0, 1.0)"
        )
        pre.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " VALUES ('A', 'B', 'call', 1, 5, 'INFERRED')"
        )
        pre.commit()
        pre.close()

        # Open via init_db — triggers the auto-migration.
        conn = init_db(db_path)
        try:
            assert "synthesized_by" in _cols(conn, "edges"), (
                "v11 DB must gain synthesized_by after migration"
            )
            version = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0]
            assert int(version) >= 12

            # Additive: old edge row survives, synthesized_by is NULL (no backfill).
            row = conn.execute(
                "SELECT synthesized_by FROM edges WHERE source_name='A'"
            ).fetchone()
            assert row is not None, "Pre-migration edge row must still exist"
            assert row[0] is None, "Pre-migration edge must have synthesized_by=NULL"
        finally:
            conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_migration_idempotent() -> None:
    """Running init_db twice on the same v11 DB must not error or downgrade version."""
    from seam.indexer.db import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _make_v11_db(db_path)

        conn1 = init_db(db_path)
        v1 = conn1.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        conn1.close()

        # Second open — migration must be a no-op (version-guarded).
        conn2 = init_db(db_path)
        v2 = conn2.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        conn2.close()

        assert v1 == v2, f"Version changed between two opens: {v1} → {v2}"
    finally:
        db_path.unlink(missing_ok=True)


def test_migration_never_raises_on_already_v12() -> None:
    """Calling _run_migration_v11_to_v12 on an already-v12 DB must be a no-op."""
    from seam.indexer.db import init_db
    from seam.indexer.migrations import _run_migration_v11_to_v12

    conn = init_db(Path(":memory:"))
    try:
        # DB is at v12 already. Running the migration again must not raise.
        _run_migration_v11_to_v12(conn)  # should silently no-op
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) >= 12
    finally:
        conn.close()


def test_migration_function_on_v11_db() -> None:
    """_run_migration_v11_to_v12 adds synthesized_by to a real v11 DB."""
    from seam.indexer.migrations import _run_migration_v11_to_v12

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _make_v11_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Pre-check: column absent.
        assert "synthesized_by" not in _cols(conn, "edges")

        _run_migration_v11_to_v12(conn)

        # Post-check: column present, version bumped.
        assert "synthesized_by" in _cols(conn, "edges")
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) == 12
        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_migration_never_raises_on_malformed_db() -> None:
    """_run_migration_v11_to_v12 must never raise; on failure it raises RuntimeError only."""
    from seam.indexer.migrations import _run_migration_v11_to_v12

    # Minimal in-memory DB with no tables at all — migration should raise RuntimeError
    # (not a raw sqlite3.OperationalError), since that's the contract documented in migrations.py.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _run_migration_v11_to_v12(conn)
    except RuntimeError:
        pass  # Expected — migration fails gracefully with RuntimeError
    except Exception as exc:
        raise AssertionError(
            f"Migration raised unexpected exception type {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        conn.close()
