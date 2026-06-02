"""Smoke tests — verify the package structure is intact.

These tests pass from Day 0 with no implementation.
They verify imports work and the version is set.
"""

import seam


def test_package_version() -> None:
    assert seam.__version__ == "0.2.0"


def test_config_imports() -> None:
    from seam.config import SEAM_DB_PATH, SEAM_LANGUAGE_MAP

    assert SEAM_DB_PATH == ".seam/seam.db"
    assert ".py" in SEAM_LANGUAGE_MAP
    assert ".ts" in SEAM_LANGUAGE_MAP


def test_fts5_available() -> None:
    """Verify SQLite FTS5 extension is available on this machine."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    conn.close()
