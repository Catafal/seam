"""Tests for Tier B slice B1: receiver column + Edge field + v9->v10 migration + Python receiver capture.

TDD: Tests written before implementation. Each group covers one behavioral slice:

B1a — Migration:    v9->v10 adds edges.receiver column; fresh-DB guard; idempotent;
                    schema_version bumped to 10; old rows read receiver=NULL.
B1b — Edge model:   Edge TypedDict has receiver: str | None field.
B1c — DB upsert:    upsert_file threads receiver through to the DB; round-trips cleanly.
B1d — Python extractor: attribute calls capture receiver text; bare calls have receiver=None.
B1e — Negative:     import edges have receiver=None; non-attribute call edges have receiver=None.

All migration tests use temp files (stat() needed for upsert_file); Edge model tests
use the TypedDict directly. Extractor tests use the public extract_edges() API.
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.db import (
    _run_migration_v9_to_v10,  # noqa: PLC2701 (private helper exposed for test)
    connect,
    init_db,
    upsert_file,
)
from seam.indexer.graph import Edge, Symbol, extract_edges
from seam.indexer.parser import parse_python

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_symbol(
    name: str = "my_func",
    kind: str = "function",
    file: str = "/tmp/sample.py",
    start_line: int = 1,
    end_line: int = 5,
) -> Symbol:
    """Build a minimal Symbol dict for DB tests."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=start_line,
        end_line=end_line,
        docstring=None,
        signature=None,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=None,
    )


def _make_edge(
    source: str = "caller",
    target: str = "callee",
    kind: str = "call",
    file: str = "/tmp/sample.py",
    line: int = 10,
    confidence: str = "INFERRED",
    receiver: str | None = None,
) -> Edge:
    """Build an Edge dict including the new receiver field."""
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=file,
        line=line,
        confidence=confidence,
        receiver=receiver,
    )


def _edges_from_source(source: str) -> list[Edge]:
    """Parse Python source text and extract call/import edges.

    Writes to a temp file, parses, and cleans up before returning.
    """
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_python(path)
        assert root is not None, f"parse_python returned None for source: {source!r}"
        return extract_edges(root, "python", path)
    finally:
        os.unlink(fname)


# ── B1a — Migration tests ──────────────────────────────────────────────────────


class TestMigrationV9ToV10:
    """B1a: v9->v10 migration adds edges.receiver; fresh-DB guard; idempotent."""

    def test_fresh_db_has_receiver_column(self) -> None:
        """A freshly initialized DB has the edges.receiver column."""
        conn = init_db(Path(":memory:"))
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(edges)").fetchall()}
        conn.close()
        assert "receiver" in col_names, f"receiver column missing; got {col_names}"

    def test_fresh_db_schema_version_is_10(self) -> None:
        """A freshly initialized DB reports schema_version=10."""
        conn = init_db(Path(":memory:"))
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn.close()
        assert row is not None
        assert int(row["value"]) == 10

    def test_upgrade_from_v9_adds_receiver_column(self) -> None:
        """An existing v9 DB gains the receiver column when connect() auto-migrates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = init_db(db_path)

            # Simulate a v9 DB by removing the receiver column and downgrading schema_version.
            # SQLite doesn't support DROP COLUMN directly; use the CREATE TABLE+RENAME pattern.
            col_names = {r["name"] for r in conn.execute("PRAGMA table_info(edges)").fetchall()}
            if "receiver" in col_names:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""
                    CREATE TABLE edges_v9 (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_name TEXT NOT NULL,
                        target_name TEXT NOT NULL,
                        kind        TEXT NOT NULL,
                        file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                        line        INTEGER NOT NULL,
                        confidence  TEXT NOT NULL DEFAULT 'INFERRED'
                    )
                """)
                conn.execute(
                    "INSERT INTO edges_v9 "
                    "SELECT id, source_name, target_name, kind, file_id, line, confidence FROM edges"
                )
                conn.execute("DROP TABLE edges")
                conn.execute("ALTER TABLE edges_v9 RENAME TO edges")
                conn.execute("UPDATE metadata SET value = '9' WHERE key = 'schema_version'")
                conn.execute("COMMIT")
            conn.close()

            # Re-open via connect() — should auto-migrate v9 -> v10.
            conn2 = connect(db_path)
            col_names2 = {r["name"] for r in conn2.execute("PRAGMA table_info(edges)").fetchall()}
            version = int(
                conn2.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()["value"]
            )
            conn2.close()

        assert "receiver" in col_names2, f"receiver column missing after migration; got {col_names2}"
        assert version == 10

    def test_old_rows_read_receiver_null(self) -> None:
        """Pre-v10 rows (inserted without receiver) read back with receiver=NULL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = init_db(db_path)

            # Manually insert a file and edge WITHOUT specifying receiver (should default to NULL).
            file_id_row = conn.execute(
                "INSERT INTO files (path, language, file_hash, mtime, indexed_at) "
                "VALUES (?, ?, ?, ?, ?) RETURNING id",
                ("/tmp/old.py", "python", "abc123", 1.0, 1.0),
            ).fetchone()
            file_id = file_id_row[0]

            conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?)",
                (file_id, "my_func", "function", 1, 5),
            )

            # Insert edge WITHOUT receiver — receiver defaults to NULL.
            conn.execute(
                "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("caller", "callee", "call", file_id, 10, "INFERRED"),
            )
            conn.commit()

            row = conn.execute("SELECT receiver FROM edges WHERE source_name='caller'").fetchone()
            conn.close()

        assert row is not None
        assert row["receiver"] is None

    def test_migration_idempotent(self) -> None:
        """Running _run_migration_v9_to_v10 twice on a v10 DB is a no-op (no crash)."""
        conn = init_db(Path(":memory:"))
        # Running again must be a no-op — version stays 10, no error raised.
        _run_migration_v9_to_v10(conn)
        version = int(
            conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()["value"]
        )
        conn.close()
        assert version == 10


# ── B1b — Edge TypedDict model ─────────────────────────────────────────────────


class TestEdgeModel:
    """B1b: Edge TypedDict has receiver: str | None field."""

    def test_edge_has_receiver_field_none(self) -> None:
        """An Edge can be constructed with receiver=None."""
        edge = _make_edge(receiver=None)
        assert "receiver" in edge
        assert edge["receiver"] is None

    def test_edge_has_receiver_field_str(self) -> None:
        """An Edge can be constructed with a non-None receiver string."""
        edge = _make_edge(receiver="self")
        assert edge["receiver"] == "self"

    def test_edge_receiver_arbitrary_text(self) -> None:
        """Receiver can hold arbitrary text (e.g., 'obj', 'self.repo')."""
        edge = _make_edge(receiver="self.repo")
        assert edge["receiver"] == "self.repo"


# ── B1c — DB upsert round-trip ─────────────────────────────────────────────────


class TestDbUpsertReceiver:
    """B1c: upsert_file threads receiver through to DB; round-trips correctly."""

    def test_receiver_stored_non_null(self) -> None:
        """An edge with receiver='self' round-trips to the DB and reads back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_file = Path(tmpdir) / "sample.py"
            src_file.write_text("def foo(): pass\n")
            db_path = Path(tmpdir) / "test.db"
            conn = init_db(db_path)

            symbols = [_make_symbol(name="foo", file=str(src_file))]
            edges = [_make_edge(source="foo", target="bar", receiver="self", file=str(src_file))]
            upsert_file(conn, src_file, "python", "abc123", symbols, edges)

            row = conn.execute("SELECT receiver FROM edges WHERE target_name='bar'").fetchone()
            conn.close()

        assert row is not None
        assert row["receiver"] == "self"

    def test_receiver_stored_null(self) -> None:
        """An edge with receiver=None stores NULL in the DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_file = Path(tmpdir) / "sample.py"
            src_file.write_text("def foo(): pass\n")
            db_path = Path(tmpdir) / "test.db"
            conn = init_db(db_path)

            symbols = [_make_symbol(name="foo", file=str(src_file))]
            edges = [_make_edge(source="foo", target="bar", receiver=None, file=str(src_file))]
            upsert_file(conn, src_file, "python", "abc123", symbols, edges)

            row = conn.execute("SELECT receiver FROM edges WHERE target_name='bar'").fetchone()
            conn.close()

        assert row is not None
        assert row["receiver"] is None

    def test_receiver_various_strings(self) -> None:
        """Various receiver strings (self, obj) and None all round-trip correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_file = Path(tmpdir) / "sample.py"
            src_file.write_text("def foo(): pass\n")
            db_path = Path(tmpdir) / "test.db"
            conn = init_db(db_path)

            symbols = [_make_symbol(name="foo", file=str(src_file))]
            edges = [
                _make_edge(source="foo", target="m1", receiver="self", file=str(src_file), line=1),
                _make_edge(source="foo", target="m2", receiver="obj", file=str(src_file), line=2),
                _make_edge(source="foo", target="m3", receiver=None, file=str(src_file), line=3),
            ]
            upsert_file(conn, src_file, "python", "abc123", symbols, edges)

            rows = {
                r["target_name"]: r["receiver"]
                for r in conn.execute("SELECT target_name, receiver FROM edges").fetchall()
            }
            conn.close()

        assert rows["m1"] == "self"
        assert rows["m2"] == "obj"
        assert rows["m3"] is None


# ── B1d — Python extractor: attribute call receiver capture ────────────────────


class TestPythonReceiverCapture:
    """B1d: Python extractor captures receiver text for attribute calls."""

    def test_attribute_call_captures_receiver(self) -> None:
        """obj.method() → edge with target='method', receiver='obj'."""
        source = "def caller():\n    obj.method()\n"
        edges = _edges_from_source(source)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "method"]
        assert call_edges, f"No call edge for 'method'; edges={edges}"
        assert call_edges[0]["receiver"] == "obj"

    def test_self_call_captures_receiver(self) -> None:
        """self.helper() inside a method → edge with target='helper', receiver='self'."""
        source = (
            "class MyClass:\n"
            "    def run(self):\n"
            "        self.helper()\n"
            "    def helper(self):\n"
            "        pass\n"
        )
        edges = _edges_from_source(source)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "helper"]
        assert call_edges, f"No call edge for 'helper'; edges={edges}"
        assert call_edges[0]["receiver"] == "self"

    def test_chained_attribute_call_captures_receiver(self) -> None:
        """a.b.method() → target='method', receiver='a.b' (LHS of the final dot)."""
        source = "def caller():\n    a.b.method()\n"
        edges = _edges_from_source(source)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "method"]
        assert call_edges, f"No call edge for 'method'; edges={edges}"
        assert call_edges[0]["receiver"] is not None
        assert call_edges[0]["receiver"] == "a.b"

    def test_bare_call_has_no_receiver(self) -> None:
        """foo() → call edge with receiver=None (no attribute access)."""
        source = "def caller():\n    foo()\n"
        edges = _edges_from_source(source)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "foo"]
        assert call_edges, f"No call edge for 'foo'; edges={edges}"
        assert call_edges[0]["receiver"] is None


# ── B1e — Negative / conservative tests ────────────────────────────────────────


class TestReceiverNegative:
    """B1e: import edges and bare calls have receiver=None; never wrong values."""

    def test_import_edge_has_no_receiver(self) -> None:
        """import edges always have receiver=None."""
        edges = _edges_from_source("import os\n")
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges, "No import edges found"
        for e in import_edges:
            assert e.get("receiver") is None, (
                f"Import edge should have receiver=None; got {e.get('receiver')}"
            )

    def test_from_import_edge_has_no_receiver(self) -> None:
        """from X import Y edges always have receiver=None."""
        edges = _edges_from_source("from os import path\n")
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges, "No import edges found"
        for e in import_edges:
            assert e.get("receiver") is None

    def test_bare_call_receiver_none(self) -> None:
        """Bare function call foo() has receiver=None (not an attribute call)."""
        edges = _edges_from_source("def caller():\n    foo()\n")
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "foo"]
        assert call_edges, "No call edge found for 'foo'"
        assert call_edges[0].get("receiver") is None
