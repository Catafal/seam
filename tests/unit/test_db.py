"""Tests for seam/indexer/db.py — Storage layer (Track A).

All tests use in-memory SQLite or real temp files so stat() works.
Symbols and edges are hand-built — do NOT import parser or graph.

Test groups:
    A1 — init_db: schema creation, idempotency, FTS5 availability check
    A2 — upsert_file + delete_file: counts, FTS sync, cascades
"""

import sqlite3
import tempfile
from pathlib import Path

from seam.indexer.db import delete_file, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ──────────────────────────────────────────────────────────────────


def make_symbol(
    name: str = "my_func",
    kind: str = "function",
    file: str = "/tmp/sample.py",
    start_line: int = 1,
    end_line: int = 5,
    docstring: str | None = None,
) -> Symbol:
    """Build a minimal Symbol dict for seeding tests."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=start_line,
        end_line=end_line,
        docstring=docstring,
    )


def make_edge(
    source: str = "caller",
    target: str = "callee",
    kind: str = "call",
    file: str = "/tmp/sample.py",
    line: int = 10,
) -> Edge:
    """Build a minimal Edge dict for seeding tests."""
    return Edge(source=source, target=target, kind=kind, file=file, line=line)


# ── A1 — init_db ─────────────────────────────────────────────────────────────


class TestInitDb:
    """A1: schema creation, FTS5 check, idempotency."""

    def test_creates_files_table(self) -> None:
        """init_db with :memory: creates the 'files' table."""
        conn = init_db(Path(":memory:"))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r[0] for r in rows}
        assert "files" in table_names
        conn.close()

    def test_creates_symbols_table(self) -> None:
        conn = init_db(Path(":memory:"))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r[0] for r in rows}
        assert "symbols" in table_names
        conn.close()

    def test_creates_edges_table(self) -> None:
        conn = init_db(Path(":memory:"))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r[0] for r in rows}
        assert "edges" in table_names
        conn.close()

    def test_creates_metadata_table(self) -> None:
        conn = init_db(Path(":memory:"))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r[0] for r in rows}
        assert "metadata" in table_names
        conn.close()

    def test_creates_symbols_fts_virtual_table(self) -> None:
        """init_db creates the FTS5 virtual table symbols_fts."""
        conn = init_db(Path(":memory:"))
        # FTS5 virtual tables appear in sqlite_master with type='table'
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='symbols_fts'"
        ).fetchall()
        assert len(rows) == 1
        conn.close()

    def test_idempotent_call_twice(self) -> None:
        """Calling init_db twice on the same :memory: path must not raise."""
        # Use an actual file so both calls hit the same DB
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        # Check tables still intact after second init
        rows = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r[0] for r in rows}
        assert "files" in table_names
        conn2.close()
        db_path.unlink(missing_ok=True)

    def test_returns_open_connection(self) -> None:
        """init_db returns an open, usable sqlite3.Connection."""
        conn = init_db(Path(":memory:"))
        assert isinstance(conn, sqlite3.Connection)
        # If the connection is open, this should succeed
        result = conn.execute("SELECT 1").fetchone()
        assert result is not None
        conn.close()

    def test_row_factory_set(self) -> None:
        """init_db sets row_factory = sqlite3.Row for named access."""
        conn = init_db(Path(":memory:"))
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_metadata_seeded(self) -> None:
        """init_db seeds schema_version and seam_version in metadata."""
        conn = init_db(Path(":memory:"))
        rows = conn.execute("SELECT key FROM metadata").fetchall()
        keys = {r["key"] for r in rows}
        assert "schema_version" in keys
        assert "seam_version" in keys
        conn.close()


# ── A2 — upsert_file + delete_file ───────────────────────────────────────────


class TestUpsertFile:
    """A2: insert counts, FTS sync, idempotency, edge field mapping."""

    def _real_file(self) -> Path:
        """Create a real temp file (needed for stat())."""
        f = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        f.write(b"# test\n")
        f.flush()
        f.close()
        return Path(f.name)

    def test_inserts_file_row(self) -> None:
        """upsert_file inserts exactly one row in files."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        try:
            upsert_file(conn, filepath, "python", "abc123", [], [])
            count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            assert count == 1
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_inserts_symbols(self) -> None:
        """upsert_file inserts the correct number of symbols."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        syms = [
            make_symbol(name="func_a", file=str(filepath)),
            make_symbol(name="func_b", file=str(filepath)),
        ]
        try:
            upsert_file(conn, filepath, "python", "abc123", syms, [])
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert count == 2
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_inserts_edges(self) -> None:
        """upsert_file inserts edges with correct source_name/target_name mapping."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        edges = [make_edge(source="func_a", target="func_b", file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", [], edges)
            row = conn.execute(
                "SELECT source_name, target_name FROM edges LIMIT 1"
            ).fetchone()
            assert row is not None
            # Contract: Edge['source'] → source_name, Edge['target'] → target_name
            assert row["source_name"] == "func_a"
            assert row["target_name"] == "func_b"
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_fts_row_exists_after_upsert(self) -> None:
        """FTS5 index has a row for each inserted symbol (trigger fires)."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        syms = [make_symbol(name="searchable_func", file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", syms, [])
            # Search via FTS should return at least one result
            rows = conn.execute(
                "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH 'searchable_func'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_upsert_idempotent_same_counts(self) -> None:
        """Calling upsert_file twice produces the same row counts (not doubled)."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        syms = [make_symbol(name="func_a", file=str(filepath))]
        edges = [make_edge(file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", syms, edges)
            upsert_file(conn, filepath, "python", "abc123", syms, edges)
            assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_upsert_stores_file_hash(self) -> None:
        """upsert_file stores the provided file_hash in the files row."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        try:
            upsert_file(conn, filepath, "python", "deadbeef42", [], [])
            row = conn.execute("SELECT file_hash FROM files LIMIT 1").fetchone()
            assert row["file_hash"] == "deadbeef42"
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_upsert_stores_language(self) -> None:
        """upsert_file stores the correct language in the files row."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        try:
            upsert_file(conn, filepath, "typescript", "abc123", [], [])
            row = conn.execute("SELECT language FROM files LIMIT 1").fetchone()
            assert row["language"] == "typescript"
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()


class TestDeleteFile:
    """A2: cascade delete removes symbols, edges, and FTS rows."""

    def _real_file(self) -> Path:
        f = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        f.write(b"# test\n")
        f.flush()
        f.close()
        return Path(f.name)

    def test_delete_removes_file_row(self) -> None:
        """delete_file removes the files row."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        syms = [make_symbol(name="f", file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", syms, [])
            delete_file(conn, filepath)
            count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            assert count == 0
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_delete_cascades_to_symbols(self) -> None:
        """Deleting a file cascades to its symbols (foreign key ON DELETE CASCADE)."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        syms = [make_symbol(name="func_a", file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", syms, [])
            delete_file(conn, filepath)
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert count == 0
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_delete_cascades_to_edges(self) -> None:
        """Deleting a file cascades to its edges."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        edges = [make_edge(file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", [], edges)
            delete_file(conn, filepath)
            count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            assert count == 0
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_delete_removes_fts_row(self) -> None:
        """After delete_file, FTS5 no longer returns the deleted symbol."""
        conn = init_db(Path(":memory:"))
        filepath = self._real_file()
        syms = [make_symbol(name="gone_func", file=str(filepath))]
        try:
            upsert_file(conn, filepath, "python", "abc123", syms, [])
            delete_file(conn, filepath)
            rows = conn.execute(
                "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH 'gone_func'"
            ).fetchall()
            assert len(rows) == 0
        finally:
            filepath.unlink(missing_ok=True)
            conn.close()

    def test_delete_nonexistent_file_no_error(self) -> None:
        """delete_file on a path that was never indexed must not raise."""
        conn = init_db(Path(":memory:"))
        # Should silently succeed (no row to delete)
        delete_file(conn, Path("/tmp/never_existed.py"))
        conn.close()
