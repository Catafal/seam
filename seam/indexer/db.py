"""SQLite read/write operations for the Seam index.

Schema defined in docs/database/schema.sql.
All operations use explicit connections (no connection pool) — caller controls lifetime.
"""

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seam.indexer.graph import Edge, Symbol

# Implementations: see IMPLEMENTATION_PLAN.md steps 2.1 and 2.2


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create the database and schema if they don't exist. Returns open connection.

    Verifies FTS5 is available; raises RuntimeError if not.
    """
    raise NotImplementedError("Implement in step 2.1")


def upsert_file(
    conn: sqlite3.Connection,
    filepath: Path,
    language: str,
    file_hash: str,
    symbols: "list[Symbol]",
    edges: "list[Edge]",
) -> None:
    """Atomically replace all data for a file. Idempotent: safe to call twice."""
    raise NotImplementedError("Implement in step 2.1")


def delete_file(conn: sqlite3.Connection, filepath: Path) -> None:
    """Remove all symbols and edges for a file (cascades via foreign key)."""
    raise NotImplementedError("Implement in step 2.1")
