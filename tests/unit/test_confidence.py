"""Tests for Phase 1 / issue #3 — Confidence-tagged edges.

Covers (per spec):
  C1 — Target resolves to exactly one same-file symbol → EXTRACTED
  C2 — Target name shared by two symbols in same-file set → AMBIGUOUS
  C3 — Heuristic resolution (target not in same-file set) → INFERRED
  C4 — context() on a duplicated DB name sets ambiguous=True
  C5 — Migration: opening a v1-style edges table (no confidence col)
       then calling init_db adds the column without crashing.

Style: follows test_hardening.py — hand-built fixtures, real temp files,
init_db + upsert_file as the seeding path, assertions on public interfaces.
"""

import sqlite3
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol, extract_edges, extract_symbols
from seam.indexer.parser import parse_python
from seam.query.engine import context

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function") -> Symbol:
    """Build a minimal Symbol for seeding tests."""
    return Symbol(name=name, kind=kind, file=file, start_line=1, end_line=5, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = "EXTRACTED") -> Edge:
    """Build a minimal Edge for seeding tests."""
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


def _real_file(tmp_path: Path, content: str = "# test\n", suffix: str = ".py") -> Path:
    """Create a real file in tmp_path with the given content."""
    p = tmp_path / f"f{id(content)}{suffix}"
    p.write_text(content)
    return p


# ── C1: EXTRACTED — target resolves to exactly one same-file symbol ──────────


class TestExtractedConfidence:
    """C1: When the target name matches exactly one symbol in the same-file list,
    confidence must be EXTRACTED."""

    def test_call_to_known_local_function_is_extracted(self, tmp_path: Path) -> None:
        """A call edge whose target is a uniquely-named same-file function is EXTRACTED."""
        src = tmp_path / "c1.py"
        src.write_text(
            "def helper():\n"
            "    return 1\n\n"
            "def caller():\n"
            "    helper()\n"
        )
        root = parse_python(src)
        assert root is not None

        symbols = extract_symbols(root, "python", src)
        edges = extract_edges(root, "python", src, symbols=symbols)

        # Find the call edge from caller → helper
        call_edges = [e for e in edges if e["target"] == "helper" and e["kind"] == "call"]
        assert call_edges, "expected a call edge to 'helper'"
        assert all(e["confidence"] == "EXTRACTED" for e in call_edges), (
            f"expected EXTRACTED for call to uniquely-defined 'helper', got: "
            f"{[e['confidence'] for e in call_edges]}"
        )


# ── C2: AMBIGUOUS — target name shared by multiple symbols ───────────────────


class TestAmbiguousConfidence:
    """C2: When the target name matches more than one symbol in the same-file
    symbol list, confidence must be AMBIGUOUS."""

    def test_call_target_matching_two_symbols_is_ambiguous(self, tmp_path: Path) -> None:
        """When the same name appears twice in the symbol list, edges to it are AMBIGUOUS.

        We build the symbol list manually to inject a duplicate name, since Python
        cannot have two top-level defs with the same name in one file at parse time.
        """
        src = tmp_path / "c2.py"
        src.write_text("def process():\n    process()\n")
        root = parse_python(src)
        assert root is not None

        # Manually inject a second 'process' symbol to simulate a name collision
        # (e.g., two files merged, or two overloads). extract_edges sees the list.
        symbols: list[Symbol] = [
            _sym("process", str(src)),
            _sym("process", str(src)),  # duplicate — name collision
        ]

        edges = extract_edges(root, "python", src, symbols=symbols)

        call_edges = [e for e in edges if e["target"] == "process" and e["kind"] == "call"]
        assert call_edges, "expected a call edge to 'process'"
        assert all(e["confidence"] == "AMBIGUOUS" for e in call_edges), (
            f"expected AMBIGUOUS for name collision on 'process', got: "
            f"{[e['confidence'] for e in call_edges]}"
        )


# ── C3: INFERRED — heuristic / target not in same-file symbol set ────────────


class TestInferredConfidence:
    """C3: When the target name is not in the same-file symbol set,
    confidence must be INFERRED (heuristic best-guess)."""

    def test_import_of_stdlib_module_is_inferred(self, tmp_path: Path) -> None:
        """An import edge whose target is not a same-file symbol is INFERRED."""
        src = tmp_path / "c3.py"
        src.write_text("import os\nimport sys\n")
        root = parse_python(src)
        assert root is not None

        # No symbols in this file — all import targets resolve to INFERRED
        symbols = extract_symbols(root, "python", src)  # empty for this file
        edges = extract_edges(root, "python", src, symbols=symbols)

        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges, "expected import edges for 'import os'"
        assert all(e["confidence"] == "INFERRED" for e in import_edges), (
            f"expected INFERRED for stdlib imports, got: "
            f"{[e['confidence'] for e in import_edges]}"
        )

    def test_no_symbols_arg_means_all_inferred(self, tmp_path: Path) -> None:
        """When symbols=None is passed (backward compat), all edges are INFERRED."""
        src = tmp_path / "c3b.py"
        src.write_text("def f():\n    g()\n")
        root = parse_python(src)
        assert root is not None

        edges = extract_edges(root, "python", src)  # no symbols arg

        call_edges = [e for e in edges if e["kind"] == "call"]
        assert call_edges, "expected call edges"
        assert all(e["confidence"] == "INFERRED" for e in call_edges), (
            "without symbols arg, all edges must be INFERRED"
        )


# ── C4: context() ambiguous flag on DB name collision ────────────────────────


class TestContextAmbiguous:
    """C4: context() on a name shared by multiple DB rows must set ambiguous=True.

    This covers cross-file name collision, which extract_edges cannot detect
    (per-file only). engine.context() is the query-layer signal.
    """

    def test_unique_name_is_not_ambiguous(self, tmp_path: Path) -> None:
        """context() on a uniquely-named symbol must set ambiguous=False."""
        conn = init_db(tmp_path / "idx.db")
        try:
            src = _real_file(tmp_path, "# test\n")
            upsert_file(conn, src, "python", "h1", [_sym("unique_fn", str(src))], [])
            result = context(conn, "unique_fn")
            assert result is not None
            assert result["ambiguous"] is False
        finally:
            conn.close()

    def test_duplicated_name_is_ambiguous(self, tmp_path: Path) -> None:
        """context() on a name defined in two different files must set ambiguous=True."""
        conn = init_db(tmp_path / "idx.db")
        try:
            # Two separate files, each defining 'shared_fn' — simulates cross-file collision
            src_a = _real_file(tmp_path, "# a\n", suffix=".py")
            src_b = tmp_path / "b.py"
            src_b.write_text("# b\n")

            upsert_file(conn, src_a, "python", "h1", [_sym("shared_fn", str(src_a))], [])
            upsert_file(conn, src_b, "python", "h2", [_sym("shared_fn", str(src_b))], [])

            result = context(conn, "shared_fn")
            assert result is not None, "context() must return a result, not None"
            assert result["ambiguous"] is True, (
                "ambiguous must be True when multiple definitions share the same name"
            )
        finally:
            conn.close()

    def test_context_returns_none_for_unknown_symbol(self, tmp_path: Path) -> None:
        """context() returns None when the symbol is not in the index."""
        conn = init_db(tmp_path / "idx.db")
        try:
            result = context(conn, "nonexistent_symbol")
            assert result is None
        finally:
            conn.close()


# ── C5: Migration — v1 db (no confidence col) gets the column added ──────────


class TestMigration:
    """C5: Opening a v1-style edges table (without confidence column) and calling
    init_db must add the column without crashing, and old edges get the DEFAULT value.
    """

    def test_migration_adds_confidence_column_to_v1_db(self, tmp_path: Path) -> None:
        """A real DB file with a v1 edges schema (no confidence) is migrated safely."""
        db_path = tmp_path / "v1.db"

        # Step 1: Bootstrap a v1-style DB directly — create tables without the confidence
        # column, matching the Phase-0 schema shape.
        conn_raw = sqlite3.connect(str(db_path))
        conn_raw.execute("PRAGMA foreign_keys = ON")
        conn_raw.executescript(
            """
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
                docstring TEXT
            );
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                target_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                line INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '1');
            CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
                name, docstring, content='symbols', content_rowid='id'
            );
            """
        )
        conn_raw.commit()
        # Insert a v1 edge row (no confidence column)
        conn_raw.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/v1_file.py', 'python', 'abc', 1.0, 1.0)"
        )
        conn_raw.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line)"
            " VALUES ('caller', 'callee', 'call', 1, 10)"
        )
        conn_raw.commit()
        conn_raw.close()

        # Step 2: Open via init_db — migration must run without error.
        conn = init_db(db_path)
        try:
            # Verify the column now exists
            col_names = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(edges)").fetchall()
            }
            assert "confidence" in col_names, (
                "init_db migration must add 'confidence' column to v1 edges table"
            )

            # Verify the old edge row survived with the DEFAULT value 'INFERRED'
            # (conservative: migrated edges are marked INFERRED, not high-trust EXTRACTED;
            #  run 'seam init' to re-index for accurate confidence tags).
            row = conn.execute("SELECT confidence FROM edges LIMIT 1").fetchone()
            assert row is not None, "old edge row must still exist after migration"
            assert row["confidence"] == "INFERRED", (
                f"migrated row must have DEFAULT confidence 'INFERRED', got {row['confidence']!r}"
            )
        finally:
            conn.close()

    def test_migration_on_fresh_db_does_not_crash(self, tmp_path: Path) -> None:
        """init_db on a brand-new DB (no prior schema) must not raise."""
        conn = init_db(tmp_path / "fresh.db")
        try:
            # Column must exist in a fresh db too
            col_names = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(edges)").fetchall()
            }
            assert "confidence" in col_names
        finally:
            conn.close()
