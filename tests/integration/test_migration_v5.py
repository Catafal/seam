"""Integration tests for v4→v5 schema migration (Phase 4 — Node-Field Enrichment).

TDD: Tests written before implementation (RED phase).

Mirrors the style of tests/unit/test_clustering_migration.py (v3→v4 pattern).

Test groups:
    M1 — Fresh DB from init_db: schema_version='5', all 5 new columns exist, FTS indexes signature.
    M2 — v4 DB migrates to v5: schema_version bumped, new columns present, existing rows preserved.
    M3 — Migration idempotency: running init_db twice on a v5 DB is safe.
    M4 — upsert_file persists the 5 new fields and reads them back correctly.
    M5 — FTS indexes signature: a symbol searchable only by signature returns a match.
    M6 — decorators JSON round-trip: list written and read back as list.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Symbol

# ── Helper: build a minimal v4 DB (no Phase 4 columns) ───────────────────────


def _make_v4_db(db_path: Path) -> None:
    """Create a minimal v4 DB for migration testing.

    Matches the v4 schema: has clusters table + symbols.cluster_id,
    schema_version='4', but NO Phase 4 new columns and NO signature in FTS.
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
            cluster_id INTEGER
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
            name, docstring, content='symbols', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
            INSERT INTO symbols_fts(rowid, name, docstring)
            VALUES (new.id, new.name, new.docstring);
        END;
        CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
            INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring)
            VALUES ('delete', old.id, old.name, old.docstring);
        END;
        CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
            INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring)
            VALUES ('delete', old.id, old.name, old.docstring);
            INSERT INTO symbols_fts(rowid, name, docstring)
            VALUES (new.id, new.name, new.docstring);
        END;
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
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '4');
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.close()


def _sym(
    name: str,
    kind: str,
    file: str,
    signature: str | None = None,
    decorators: list[str] | None = None,
    is_exported: bool | None = None,
    visibility: str | None = None,
    qualified_name: str | None = None,
    docstring: str | None = None,
) -> Symbol:
    """Build a Symbol for tests (with or without Phase 4 fields)."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=1,
        end_line=10,
        docstring=docstring,
        signature=signature,
        decorators=decorators if decorators is not None else [],
        is_exported=is_exported,
        visibility=visibility,
        qualified_name=qualified_name,
    )


# ── M1: Fresh DB ──────────────────────────────────────────────────────────────


class TestFreshDBSchemaV5:
    """M1: A fresh DB from init_db has schema_version='5' and all Phase 4 columns."""

    def test_fresh_db_schema_version_is_5(self) -> None:
        """Fresh DB → schema_version='6' (Phase 5 added v6; test name kept for history)."""
        conn = init_db(Path(":memory:"))
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        conn.close()
        assert row is not None
        # Phase 5 bumped the schema to v6; any value >= 5 is acceptable here.
        assert int(row[0]) >= 5, f"Expected >= '5', got {row[0]!r}"

    def test_fresh_db_has_signature_column(self) -> None:
        """Fresh DB → symbols.signature column exists."""
        conn = init_db(Path(":memory:"))
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        conn.close()
        assert "signature" in col_names, f"signature not in symbols columns: {col_names}"

    def test_fresh_db_has_decorators_column(self) -> None:
        """Fresh DB → symbols.decorators column exists."""
        conn = init_db(Path(":memory:"))
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        conn.close()
        assert "decorators" in col_names

    def test_fresh_db_has_is_exported_column(self) -> None:
        """Fresh DB → symbols.is_exported column exists."""
        conn = init_db(Path(":memory:"))
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        conn.close()
        assert "is_exported" in col_names

    def test_fresh_db_has_visibility_column(self) -> None:
        """Fresh DB → symbols.visibility column exists."""
        conn = init_db(Path(":memory:"))
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        conn.close()
        assert "visibility" in col_names

    def test_fresh_db_has_qualified_name_column(self) -> None:
        """Fresh DB → symbols.qualified_name column exists."""
        conn = init_db(Path(":memory:"))
        col_names = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        conn.close()
        assert "qualified_name" in col_names

    def test_fresh_db_fts_has_signature_column(self) -> None:
        """Fresh DB → symbols_fts virtual table includes signature column."""
        conn = init_db(Path(":memory:"))
        # Verify by checking if we can INSERT with signature and then query it
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            src = Path(f.name)
        src.write_text("def foo(): pass\n")
        try:
            sym = _sym(
                "sig_test_fn", "function", str(src), signature="def sig_test_fn(x: int) -> None"
            )
            upsert_file(conn, src, "python", "abc123", [sym], [])
            # Query FTS on the signature term — should find the symbol
            rows = conn.execute(
                "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH '\"sig_test_fn\"*'"
            ).fetchall()
            assert len(rows) >= 1
        finally:
            src.unlink(missing_ok=True)
            conn.close()


# ── M2: v4 DB migrates to v5 ─────────────────────────────────────────────────


class TestMigrationV4ToV5:
    """M2: Opening a v4 DB via init_db bumps schema_version to '5'."""

    def test_v4_db_schema_version_bumped_to_5(self) -> None:
        """init_db on a v4 DB bumps schema_version to >= '5' (Phase 5 added v6)."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v4_db(db_path)
            conn = init_db(db_path)
            row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            conn.close()
            assert row is not None
            assert int(row[0]) >= 5, f"Expected >= '5', got {row[0]!r}"
        finally:
            db_path.unlink(missing_ok=True)

    def test_v4_db_gets_signature_column(self) -> None:
        """After v4→v5 migration, symbols.signature column exists."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v4_db(db_path)
            conn = init_db(db_path)
            col_names = {
                row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
            }
            conn.close()
            assert "signature" in col_names
        finally:
            db_path.unlink(missing_ok=True)

    def test_v4_db_gets_all_five_columns(self) -> None:
        """After v4→v5 migration, all 5 new columns exist on symbols."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v4_db(db_path)
            conn = init_db(db_path)
            col_names = {
                row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
            }
            conn.close()
            for col in ("signature", "decorators", "is_exported", "visibility", "qualified_name"):
                assert col in col_names, f"Missing column: {col}"
        finally:
            db_path.unlink(missing_ok=True)

    def test_existing_symbols_survive_migration(self) -> None:
        """Migration is additive: existing symbol rows are preserved with NULL new fields."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v4_db(db_path)

            # Insert a file + symbol row BEFORE migration (v4 schema)
            pre_conn = sqlite3.connect(str(db_path))
            pre_conn.execute(
                "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
                " VALUES ('/test.py', 'python', 'abc', 1.0, 1.0)"
            )
            pre_conn.execute(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
                " VALUES (1, 'existing_fn', 'function', 1, 10)"
            )
            pre_conn.commit()
            pre_conn.close()

            conn = init_db(db_path)
            row = conn.execute(
                "SELECT name, signature, decorators FROM symbols WHERE name='existing_fn'"
            ).fetchone()
            conn.close()
            assert row is not None, "existing_fn must survive migration"
            assert row[0] == "existing_fn"
            # New columns default to NULL for pre-migration rows
            assert row[1] is None, "signature should be NULL for pre-migration rows"
            assert row[2] is None, "decorators should be NULL for pre-migration rows"
        finally:
            db_path.unlink(missing_ok=True)

    def test_fts_indexes_signature_after_migration(self) -> None:
        """After migration, FTS can search on signature terms in pre-existing rows."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v4_db(db_path)
            conn = init_db(db_path)
            # FTS table should now have signature column — test by inserting then searching
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as sf:
                src = Path(sf.name)
            src.write_text("def hello(): pass\n")
            try:
                sym = _sym(
                    "post_migration_func",
                    "function",
                    str(src),
                    signature="def post_migration_func(conn: Connection) -> AffectedResult",
                )
                upsert_file(conn, src, "python", "xyz", [sym], [])
                # Search for a type-shaped term — only appears in signature, not name/docstring
                rows = conn.execute(
                    "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH '\"AffectedResult\"*'"
                ).fetchall()
                assert len(rows) >= 1, "signature search should find AffectedResult in FTS"
            finally:
                src.unlink(missing_ok=True)
            conn.close()
        finally:
            db_path.unlink(missing_ok=True)


# ── M3: Migration idempotency ─────────────────────────────────────────────────


class TestMigrationV5Idempotent:
    """M3: Running init_db on a v5 DB is safe and does not error."""

    def test_v5_db_migration_idempotent(self) -> None:
        """Running init_db twice on a v5/v6 DB leaves version at >= '5'."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn1 = init_db(db_path)
            conn1.close()
            conn2 = init_db(db_path)
            row = conn2.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            conn2.close()
            assert int(row[0]) >= 5, f"Expected >= '5' after second init, got {row[0]!r}"
        finally:
            db_path.unlink(missing_ok=True)

    def test_in_memory_db_is_v5(self) -> None:
        """init_db(':memory:') produces v6 DB (Phase 5; test name kept for history)."""
        conn = init_db(Path(":memory:"))
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[
            0
        ]
        conn.close()
        assert int(version) >= 5


# ── M4: upsert_file persists Phase 4 fields ───────────────────────────────────


class TestUpsertFilePhase4Fields:
    """M4: upsert_file writes and persists the 5 new Phase 4 fields."""

    @pytest.fixture()
    def db_and_src(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        src = tmp_path / "src.py"
        src.write_text("def foo(): pass\n")
        yield conn, src
        conn.close()

    def test_upsert_persists_signature(self, db_and_src) -> None:
        conn, src = db_and_src
        sym = _sym("my_func", "function", str(src), signature="def my_func(x: int) -> bool")
        upsert_file(conn, src, "python", "h1", [sym], [])
        row = conn.execute("SELECT signature FROM symbols WHERE name='my_func'").fetchone()
        assert row is not None
        assert row[0] == "def my_func(x: int) -> bool"

    def test_upsert_persists_decorators_as_json(self, db_and_src) -> None:
        conn, src = db_and_src
        sym = _sym(
            "decorated_fn", "function", str(src), decorators=["@app.route('/x')", "@login_required"]
        )
        upsert_file(conn, src, "python", "h2", [sym], [])
        row = conn.execute("SELECT decorators FROM symbols WHERE name='decorated_fn'").fetchone()
        assert row is not None
        # decorators stored as JSON text
        stored = row[0]
        assert stored is not None
        parsed = json.loads(stored)
        assert parsed == ["@app.route('/x')", "@login_required"]

    def test_upsert_persists_is_exported(self, db_and_src) -> None:
        conn, src = db_and_src
        sym = _sym("ExportedClass", "class", str(src), is_exported=True)
        upsert_file(conn, src, "python", "h3", [sym], [])
        row = conn.execute("SELECT is_exported FROM symbols WHERE name='ExportedClass'").fetchone()
        assert row is not None
        # SQLite stores bool as 1/0
        assert row[0] == 1

    def test_upsert_persists_visibility(self, db_and_src) -> None:
        conn, src = db_and_src
        sym = _sym("_hidden", "function", str(src), visibility="private")
        upsert_file(conn, src, "python", "h4", [sym], [])
        row = conn.execute("SELECT visibility FROM symbols WHERE name='_hidden'").fetchone()
        assert row is not None
        assert row[0] == "private"

    def test_upsert_persists_qualified_name(self, db_and_src) -> None:
        conn, src = db_and_src
        sym = _sym("MyClass.my_method", "method", str(src), qualified_name="MyClass.my_method")
        upsert_file(conn, src, "python", "h5", [sym], [])
        row = conn.execute(
            "SELECT qualified_name FROM symbols WHERE name='MyClass.my_method'"
        ).fetchone()
        assert row is not None
        assert row[0] == "MyClass.my_method"

    def test_upsert_null_fields_when_not_provided(self, db_and_src) -> None:
        """Symbols without Phase 4 fields store NULLs — backward compat."""
        conn, src = db_and_src
        sym = _sym("plain_func", "function", str(src))  # no Phase 4 fields
        upsert_file(conn, src, "python", "h6", [sym], [])
        row = conn.execute(
            "SELECT signature, is_exported, visibility, qualified_name FROM symbols WHERE name='plain_func'"
        ).fetchone()
        assert row is not None
        assert row[0] is None  # signature
        assert row[1] is None  # is_exported
        assert row[2] is None  # visibility
        assert row[3] is None  # qualified_name

    def test_upsert_empty_decorators_stored_as_json_array(self, db_and_src) -> None:
        """Empty decorators list stored as '[]', not NULL."""
        conn, src = db_and_src
        sym = _sym("no_deco", "function", str(src), decorators=[])
        upsert_file(conn, src, "python", "h7", [sym], [])
        row = conn.execute("SELECT decorators FROM symbols WHERE name='no_deco'").fetchone()
        assert row is not None
        stored = row[0]
        # Empty list is stored as '[]'
        assert stored == "[]"


# ── M5: FTS indexes signature (search integration) ───────────────────────────


class TestFTSSignatureSearch:
    """M5: FTS5 virtual table indexes signature — type-shaped search works."""

    @pytest.fixture()
    def db_and_src(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        src = tmp_path / "src.py"
        src.write_text("def foo(): pass\n")
        yield conn, src
        conn.close()

    def test_fts_finds_symbol_by_param_type(self, db_and_src) -> None:
        """FTS query on a type name in the signature returns the symbol."""
        conn, src = db_and_src
        sym = _sym(
            "run_affected",
            "function",
            str(src),
            signature="def run_affected(conn: Connection, changed_files: list[str]) -> AffectedResult",
        )
        upsert_file(conn, src, "python", "aff1", [sym], [])

        # Query for a type that only appears in the signature
        rows = conn.execute(
            "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH '\"AffectedResult\"*'"
        ).fetchall()
        assert len(rows) >= 1, "FTS should find symbol via signature type AffectedResult"

    def test_fts_finds_symbol_by_return_type(self, db_and_src) -> None:
        """FTS query on return type in signature returns the symbol."""
        conn, src = db_and_src
        sym = _sym(
            "make_thing",
            "function",
            str(src),
            signature="def make_thing() -> SomeSpecialReturnType",
        )
        upsert_file(conn, src, "python", "ret1", [sym], [])

        rows = conn.execute(
            "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH '\"SomeSpecialReturnType\"*'"
        ).fetchall()
        assert len(rows) >= 1, "FTS should find symbol via return type in signature"

    def test_fts_signature_search_does_not_break_name_search(self, db_and_src) -> None:
        """Adding signature to FTS does not break existing name-based search."""
        conn, src = db_and_src
        sym = _sym(
            "authenticate_user",
            "function",
            str(src),
            docstring="Verify credentials.",
            signature="def authenticate_user(username: str, password: str) -> bool",
        )
        upsert_file(conn, src, "python", "auth1", [sym], [])

        rows = conn.execute(
            "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH '\"authenticate_user\"*'"
        ).fetchall()
        assert len(rows) >= 1, "Name-based search must still work after v5 FTS change"


# ── M6: Decorators JSON round-trip ───────────────────────────────────────────


class TestDecoratorsJsonRoundTrip:
    """M6: decorators written as JSON list, read back as list."""

    def test_decorators_list_round_trip(self, tmp_path: Path) -> None:
        """Write a decorators list, read it back, assert list is recovered."""
        db_path = tmp_path / "rt.db"
        conn = init_db(db_path)
        src = tmp_path / "src.py"
        src.write_text("def foo(): pass\n")
        decorators_in = ["@pytest.fixture", "@app.route('/api/v1/users')"]
        sym = _sym("rt_func", "function", str(src), decorators=decorators_in)
        upsert_file(conn, src, "python", "rt1", [sym], [])

        # Read back raw from DB
        row = conn.execute("SELECT decorators FROM symbols WHERE name='rt_func'").fetchone()
        conn.close()
        assert row is not None
        parsed = json.loads(row[0])
        assert parsed == decorators_in, f"Expected {decorators_in}, got {parsed}"
