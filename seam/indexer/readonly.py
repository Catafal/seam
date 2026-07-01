"""Read-only SQLite connection helpers for diagnostic surfaces."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_BUSY_TIMEOUT_MS = 5000


def open_readonly_connection(
    db_path: Path,
    *,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a non-migrating, query-only SQLite connection to an existing index."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA query_only = ON")
    return conn
