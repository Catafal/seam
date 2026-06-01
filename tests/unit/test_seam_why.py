"""Tests for seam-why: extract_comments (graph.py) + why() (query/comments.py).

TDD: Tests written before implementation. Each class maps to one behavioral slice.

Test groups:
    C1 — extract_comments: Python markers (# WHY:, # hack, # NOTE x)
    C2 — extract_comments: TypeScript markers (// TODO:, /* FIXME: */)
    C3 — extract_comments: case-insensitivity + optional colon
    C4 — extract_comments: false-positive guard (# whyever, # notes NOT matched)
    C5 — extract_comments: plain comments ignored; never raises
    C6 — why(): file mode returns all comments
    C7 — why(): file+line respects RADIUS (in-range / out-of-range boundary)
    C8 — why(): symbol mode returns in-range comments incl. just-above
    C9 — why(): unknown file/symbol -> []; no-arg -> ValueError
    M1 — migration: v2 DB -> schema_version '3', comments table exists; idempotent
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Comment, extract_comments
from seam.indexer.parser import parse_python, parse_typescript

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_PY = FIXTURES_DIR / "sample.py"
SAMPLE_TS = FIXTURES_DIR / "sample.ts"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_py_inline(source: str):  # type: ignore[return]
    """Parse a Python source string via a temp file, return root node."""
    import tempfile as _tmp

    with _tmp.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        tmp_path = Path(f.name)
    try:
        node = parse_python(tmp_path)
        assert node is not None
        return node, tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _parse_ts_inline(source: str):  # type: ignore[return]
    """Parse a TypeScript source string via a temp file, return root node."""
    import tempfile as _tmp

    with _tmp.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(source)
        tmp_path = Path(f.name)
    try:
        node = parse_typescript(tmp_path)
        assert node is not None
        return node, tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _comments_by_marker(comments: list[Comment], marker: str) -> list[Comment]:
    """Filter a comment list by normalized marker string."""
    return [c for c in comments if c["marker"] == marker]


# ── C1: Python markers ────────────────────────────────────────────────────────


class TestExtractCommentsPython:
    """C1: Python # comment markers are correctly extracted."""

    def test_why_colon_extracted(self) -> None:
        """# WHY: explanation -> marker='WHY', text='explanation'."""
        src = "# WHY: retry logic avoids thundering herd\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert len(comments) == 1
            assert comments[0]["marker"] == "WHY"
            assert comments[0]["text"] == "retry logic avoids thundering herd"
            assert comments[0]["line"] == 1
        finally:
            path.unlink(missing_ok=True)

    def test_hack_lowercase_no_colon(self) -> None:
        """# hack workaround -> marker='HACK', text='workaround'."""
        src = "# hack workaround for upstream bug\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            why_comments = [c for c in comments if c["marker"] == "HACK"]
            assert len(why_comments) == 1
            assert why_comments[0]["text"] == "workaround for upstream bug"
        finally:
            path.unlink(missing_ok=True)

    def test_note_space_text(self) -> None:
        """# NOTE keep in sync with Y -> marker='NOTE', text='keep in sync with Y'."""
        src = "# NOTE keep in sync with Y\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            note_comments = [c for c in comments if c["marker"] == "NOTE"]
            assert len(note_comments) == 1
            assert note_comments[0]["text"] == "keep in sync with Y"
        finally:
            path.unlink(missing_ok=True)

    def test_line_number_correct(self) -> None:
        """Comment on line 3 should have line=3."""
        src = "x = 1\ny = 2\n# TODO: fix this later\nz = 3\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            todos = [c for c in comments if c["marker"] == "TODO"]
            assert len(todos) == 1
            assert todos[0]["line"] == 3
        finally:
            path.unlink(missing_ok=True)

    def test_multiple_markers(self) -> None:
        """Multiple different markers in one file are all extracted."""
        src = (
            "# WHY: needed for safety\n"
            "# FIXME: this is broken\n"
            "# NOTE: see docs\n"
            "x = 1\n"
        )
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            markers_found = {c["marker"] for c in comments}
            assert "WHY" in markers_found
            assert "FIXME" in markers_found
            assert "NOTE" in markers_found
        finally:
            path.unlink(missing_ok=True)


# ── C2: TypeScript markers ────────────────────────────────────────────────────


class TestExtractCommentsTypeScript:
    """C2: TypeScript // and /* */ comment markers are extracted."""

    def test_ts_double_slash_todo(self) -> None:
        """// TODO: fix me -> marker='TODO', text='fix me'."""
        src = "// TODO: fix me\nconst x = 1;\n"
        root, path = _parse_ts_inline(src)
        try:
            comments = extract_comments(root, "typescript", path)
            todos = [c for c in comments if c["marker"] == "TODO"]
            assert len(todos) == 1
            assert todos[0]["text"] == "fix me"
        finally:
            path.unlink(missing_ok=True)

    def test_ts_block_comment_fixme(self) -> None:
        """/* FIXME: broken */ -> marker='FIXME'."""
        src = "/* FIXME: broken */\nconst x = 1;\n"
        root, path = _parse_ts_inline(src)
        try:
            comments = extract_comments(root, "typescript", path)
            fixmes = [c for c in comments if c["marker"] == "FIXME"]
            assert len(fixmes) == 1
        finally:
            path.unlink(missing_ok=True)

    def test_ts_line_number(self) -> None:
        """// WHY on line 2 should have line=2."""
        src = "const x = 1;\n// WHY: historical reason\nconst y = 2;\n"
        root, path = _parse_ts_inline(src)
        try:
            comments = extract_comments(root, "typescript", path)
            whys = [c for c in comments if c["marker"] == "WHY"]
            assert len(whys) == 1
            assert whys[0]["line"] == 2
        finally:
            path.unlink(missing_ok=True)


# ── C3: case-insensitivity + optional colon ───────────────────────────────────


class TestExtractCommentsFormatVariants:
    """C3: Markers are matched case-insensitively; colon is optional."""

    def test_uppercase_why_colon(self) -> None:
        """# WHY: text -> matched."""
        src = "# WHY: because\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert any(c["marker"] == "WHY" for c in comments)
        finally:
            path.unlink(missing_ok=True)

    def test_lowercase_why_no_colon(self) -> None:
        """# why text -> matched, marker normalized to 'WHY'."""
        src = "# why because\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert any(c["marker"] == "WHY" for c in comments)
        finally:
            path.unlink(missing_ok=True)

    def test_mixed_case_hack_colon(self) -> None:
        """# Hack: text -> matched, marker normalized to 'HACK'."""
        src = "# Hack: dirty workaround\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            hacks = [c for c in comments if c["marker"] == "HACK"]
            assert len(hacks) == 1
        finally:
            path.unlink(missing_ok=True)

    def test_marker_alone_no_text(self) -> None:
        """# WHY (no text after) -> matched with empty text."""
        src = "# WHY\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            whys = [c for c in comments if c["marker"] == "WHY"]
            assert len(whys) == 1
            # text should be empty or minimal whitespace stripped
            assert whys[0]["text"] == ""
        finally:
            path.unlink(missing_ok=True)


# ── C4: false-positive guard ──────────────────────────────────────────────────


class TestExtractCommentsFalsePositiveGuard:
    """C4: Marker-like words that are NOT markers should not be extracted."""

    def test_whyever_not_matched(self) -> None:
        """# whyever this works -> should NOT match WHY."""
        src = "# whyever this works\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert not any(c["marker"] == "WHY" for c in comments)
        finally:
            path.unlink(missing_ok=True)

    def test_notes_not_matched(self) -> None:
        """# notes on the algo -> should NOT match NOTE."""
        src = "# notes on the algo\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert not any(c["marker"] == "NOTE" for c in comments)
        finally:
            path.unlink(missing_ok=True)

    def test_hacking_not_matched(self) -> None:
        """# hacking around this -> should NOT match HACK."""
        src = "# hacking around this\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert not any(c["marker"] == "HACK" for c in comments)
        finally:
            path.unlink(missing_ok=True)

    def test_todolist_not_matched(self) -> None:
        """# todolist -> should NOT match TODO."""
        src = "# todolist for today\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert not any(c["marker"] == "TODO" for c in comments)
        finally:
            path.unlink(missing_ok=True)


# ── C5: plain comments ignored; never raises ──────────────────────────────────


class TestExtractCommentsEdgeCases:
    """C5: Plain comments not extracted; extract_comments never raises."""

    def test_plain_comment_ignored(self) -> None:
        """# This is a plain comment -> not extracted."""
        src = "# This is a plain comment\nx = 1\n"
        root, path = _parse_py_inline(src)
        try:
            comments = extract_comments(root, "python", path)
            assert len(comments) == 0
        finally:
            path.unlink(missing_ok=True)

    def test_returns_empty_on_bad_node(self) -> None:
        """extract_comments(None, 'python', path) -> [] (never raises)."""
        comments = extract_comments(None, "python", SAMPLE_PY)
        assert comments == []

    def test_returns_empty_on_unknown_language(self) -> None:
        """extract_comments with unsupported language -> []."""
        root, path = _parse_py_inline("x = 1\n")
        try:
            comments = extract_comments(root, "go", path)
            assert comments == []
        finally:
            path.unlink(missing_ok=True)

    def test_does_not_raise_on_junk_node(self) -> None:
        """Passing a random object as node must not raise."""
        try:
            result = extract_comments("junk", "python", SAMPLE_PY)  # type: ignore[arg-type]
            assert result == []
        except Exception as exc:
            pytest.fail(f"extract_comments raised unexpectedly: {exc}")


# ── C6: why() file mode ───────────────────────────────────────────────────────


class TestWhyFileMode:
    """C6: why(conn, file=path) returns all comments for that file."""

    @pytest.fixture()
    def seeded_db(self) -> tuple[sqlite3.Connection, Path, Path]:
        """Create a DB with one indexed file containing known comments."""
        from seam.indexer.db import init_db, upsert_file
        from seam.indexer.graph import Comment, Symbol

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / ".seam" / "seam.db"
            db_path.parent.mkdir()

            src_file = tmp_path / "src.py"
            src_file.write_text(
                "# WHY: needed\n"
                "# HACK: workaround\n"
                "# plain comment\n"
                "x = 1\n"
            )

            conn = init_db(db_path)
            comments: list[Comment] = [
                Comment(marker="WHY", text="needed", line=1),
                Comment(marker="HACK", text="workaround", line=2),
            ]
            symbols: list[Symbol] = []
            upsert_file(conn, src_file, "python", "abc123", symbols, [], comments)
            yield conn, src_file, tmp_path

    def test_file_mode_returns_all(self, seeded_db: tuple) -> None:
        """why(file=path) returns all semantic comments in the file."""
        from seam.query.comments import why

        conn, src_file, _ = seeded_db
        results = why(conn, file=str(src_file))
        assert len(results) == 2
        markers = {r["marker"] for r in results}
        assert "WHY" in markers
        assert "HACK" in markers

    def test_file_mode_sorted_by_line(self, seeded_db: tuple) -> None:
        """why(file=path) results are sorted by line number."""
        from seam.query.comments import why

        conn, src_file, _ = seeded_db
        results = why(conn, file=str(src_file))
        lines = [r["line"] for r in results]
        assert lines == sorted(lines)

    def test_file_mode_result_has_required_fields(self, seeded_db: tuple) -> None:
        """Each result has file, line, marker, text fields."""
        from seam.query.comments import why

        conn, src_file, _ = seeded_db
        results = why(conn, file=str(src_file))
        for r in results:
            assert "file" in r
            assert "line" in r
            assert "marker" in r
            assert "text" in r


# ── C7: why() file+line radius ────────────────────────────────────────────────


class TestWhyFileLineMode:
    """C7: why(conn, file=..., line=...) respects RADIUS proximity."""

    @pytest.fixture()
    def proximity_db(self) -> tuple[sqlite3.Connection, Path]:
        """DB with comments at lines 1, 20, 40 — use radius to distinguish."""
        from seam.indexer.db import init_db, upsert_file
        from seam.indexer.graph import Comment

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / ".seam" / "seam.db"
            db_path.parent.mkdir()

            src_file = tmp_path / "src.py"
            # Write enough lines to place comments far apart
            lines = ["x = 0\n"] * 50
            src_file.write_text("".join(lines))

            conn = init_db(db_path)
            # Comments at lines 1, 20, 40
            comments: list[Comment] = [
                Comment(marker="WHY", text="at line 1", line=1),
                Comment(marker="NOTE", text="at line 20", line=20),
                Comment(marker="HACK", text="at line 40", line=40),
            ]
            upsert_file(conn, src_file, "python", "hash1", [], [], comments)
            yield conn, src_file

    def test_line_mode_includes_in_radius(self, proximity_db: tuple) -> None:
        """Query line=20, RADIUS=15: line 20 (distance 0) is included."""
        from seam.query.comments import why

        conn, src_file = proximity_db
        # Center on line 20; RADIUS=15 means [5, 35]
        results = why(conn, file=str(src_file), line=20)
        lines = {r["line"] for r in results}
        assert 20 in lines

    def test_line_mode_excludes_out_of_radius(self, proximity_db: tuple) -> None:
        """Query line=20, RADIUS=15: line 40 (distance 20) is excluded."""
        from seam.query.comments import why

        conn, src_file = proximity_db
        results = why(conn, file=str(src_file), line=20)
        lines = {r["line"] for r in results}
        assert 40 not in lines

    def test_line_mode_boundary_inside(self, proximity_db: tuple) -> None:
        """Line 1 at distance 19 from line 20 is outside RADIUS=15 -> excluded."""
        from seam.query.comments import why

        conn, src_file = proximity_db
        # RADIUS=15: window is [5, 35]. Line 1 is outside.
        results = why(conn, file=str(src_file), line=20)
        lines = {r["line"] for r in results}
        assert 1 not in lines


# ── C8: why() symbol mode ────────────────────────────────────────────────────


class TestWhySymbolMode:
    """C8: why(conn, symbol=name) returns comments in/above the symbol range."""

    @pytest.fixture()
    def symbol_db(self) -> tuple[sqlite3.Connection, Path]:
        """DB with a symbol at lines 10-20 and comments at 5, 8, 15, 30."""
        from seam.indexer.db import init_db, upsert_file
        from seam.indexer.graph import Comment, Symbol

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / ".seam" / "seam.db"
            db_path.parent.mkdir()

            src_file = tmp_path / "src.py"
            src_file.write_text("x = 0\n" * 35)

            conn = init_db(db_path)
            symbols: list[Symbol] = [
                Symbol(
                    name="my_func",
                    kind="function",
                    file=str(src_file),
                    start_line=10,
                    end_line=20,
                    docstring=None,
                )
            ]
            # LEAD=5 means we look from (start_line - LEAD)=5 to end_line=20
            # Comment at line 5: included (start_line - LEAD = 5, exactly on boundary)
            # Comment at line 8: included (inside [5, 20])
            # Comment at line 15: included (inside body)
            # Comment at line 30: excluded (beyond end_line=20)
            comments: list[Comment] = [
                Comment(marker="WHY", text="at line 5 (just above)", line=5),
                Comment(marker="NOTE", text="at line 8", line=8),
                Comment(marker="HACK", text="inside body line 15", line=15),
                Comment(marker="TODO", text="far below line 30", line=30),
            ]
            upsert_file(conn, src_file, "python", "hash2", symbols, [], comments)
            yield conn, src_file

    def test_symbol_includes_just_above(self, symbol_db: tuple) -> None:
        """Comments at start_line - LEAD should be included."""
        from seam.query.comments import why

        conn, src_file = symbol_db
        results = why(conn, symbol="my_func")
        lines = {r["line"] for r in results}
        # Line 5 = start_line(10) - LEAD(5) -> boundary included
        assert 5 in lines

    def test_symbol_includes_body_comment(self, symbol_db: tuple) -> None:
        """Comments inside the symbol body are included."""
        from seam.query.comments import why

        conn, src_file = symbol_db
        results = why(conn, symbol="my_func")
        lines = {r["line"] for r in results}
        assert 15 in lines

    def test_symbol_excludes_far_below(self, symbol_db: tuple) -> None:
        """Comments beyond end_line are excluded."""
        from seam.query.comments import why

        conn, src_file = symbol_db
        results = why(conn, symbol="my_func")
        lines = {r["line"] for r in results}
        assert 30 not in lines


# ── C9: why() validation / unknown ────────────────────────────────────────────


class TestWhyValidation:
    """C9: Unknown file/symbol -> []; no-arg -> ValueError."""

    @pytest.fixture()
    def empty_db(self) -> sqlite3.Connection:
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        yield conn
        conn.close()

    def test_unknown_file_returns_empty(self, empty_db: sqlite3.Connection) -> None:
        from seam.query.comments import why

        result = why(empty_db, file="/nonexistent/path.py")
        assert result == []

    def test_unknown_symbol_returns_empty(self, empty_db: sqlite3.Connection) -> None:
        from seam.query.comments import why

        result = why(empty_db, symbol="no_such_symbol")
        assert result == []

    def test_no_args_raises_value_error(self, empty_db: sqlite3.Connection) -> None:
        from seam.query.comments import why

        with pytest.raises(ValueError):
            why(empty_db)


# ── M1: migration guard ───────────────────────────────────────────────────────


class TestMigrationV2ToV3:
    """M1: Opening a v2 DB bumps schema_version to '3'; idempotent on fresh/v3."""

    def _make_v2_db(self, path: Path) -> sqlite3.Connection:
        """Create a minimal v2 DB (no comments table, schema_version='2')."""
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
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
                line INTEGER NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'INFERRED'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
                name, docstring, content='symbols', content_rowid='id'
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '2');
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('seam_version', '0.1.0');
        """)
        conn.close()
        return sqlite3.connect(str(path))

    def test_v2_db_migrated_to_v3(self) -> None:
        """Opening a v2 DB via init_db migrates through v3 to the current version ('4')."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            # Seed a v2 DB
            self._make_v2_db(db_path).close()

            # init_db should migrate all the way through to the current schema version.
            # WHY: The migration chain is v1->v2->v3->v4, so a v2 DB ends up at '4'.
            # The test name refers to the v2->v3 migration step, but the final version
            # is the current head (updated from '3' to '4' when Phase 2 landed).
            conn = init_db(db_path)
            row = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[0] == "4"
        finally:
            db_path.unlink(missing_ok=True)

    def test_v2_db_gets_comments_table(self) -> None:
        """After migration, the comments table exists."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            self._make_v2_db(db_path).close()
            conn = init_db(db_path)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            conn.close()
            assert "comments" in tables
        finally:
            db_path.unlink(missing_ok=True)

    def test_fresh_db_schema_version_is_3(self) -> None:
        """A fresh DB from init_db has the current schema_version ('4' since Phase 2)."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert row is not None
        # WHY: Test name preserved for history, but fresh DBs are now seeded at v4.
        assert row[0] == "4"

    def test_migration_idempotent_on_v3(self) -> None:
        """Running init_db twice on an existing DB does not error or duplicate rows."""
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            conn = init_db(db_path)
            conn.close()
            # Second call should not raise
            conn2 = init_db(db_path)
            row = conn2.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            conn2.close()
            # WHY: Test name preserved for history; DB is now at v4.
            assert row[0] == "4"
        finally:
            db_path.unlink(missing_ok=True)


# ── R1: multi-line block comment markers (review fix) ─────────────────────────


class TestBlockCommentMultiline:
    """Review fix: a marker on line 2+ of a /* */ block is detected with the
    correct line number (was: only the first non-empty line was scanned)."""

    def test_marker_on_second_line_of_block_detected(self) -> None:
        """JSDoc-style block: summary on line 1, WHY on line 2 -> WHY captured."""
        src = "const x = 1;\n/*\n * WHY: historical reason\n */\nconst y = 2;\n"
        root, path = _parse_ts_inline(src)
        try:
            comments = extract_comments(root, "typescript", path)
            whys = [c for c in comments if c["marker"] == "WHY"]
            assert len(whys) == 1, f"WHY on block line 2 must be detected, got {comments}"
            assert whys[0]["text"] == "historical reason"
            # The '/*' is on line 2; the WHY line is line 3 (1-based) in the source.
            assert whys[0]["line"] == 3, (
                f"line must point at the marker (3), not the /* opener, got {whys[0]['line']}"
            )
        finally:
            path.unlink(missing_ok=True)

    def test_multiple_markers_in_one_block(self) -> None:
        """A block can carry several markers, each its own entry at its own line."""
        src = "/*\n * NOTE: a\n * HACK: b\n */\n"
        root, path = _parse_ts_inline(src)
        try:
            markers = {(c["marker"], c["text"], c["line"]) for c in extract_comments(root, "typescript", path)}
            assert ("NOTE", "a", 2) in markers
            assert ("HACK", "b", 3) in markers
        finally:
            path.unlink(missing_ok=True)


# ── R2: comments persistence — re-index + cascade (review fix) ────────────────


class TestCommentsPersistence:
    """Review fix: guard the re-index DELETE and the delete_file FK cascade."""

    def _seed(self, db_path: Path):  # type: ignore[return]
        from seam.indexer.db import init_db, upsert_file

        src = db_path.parent / "src.py"
        src.write_text("# stub\n")
        conn = init_db(db_path)
        comments = [Comment(marker="WHY", text="r", line=1)]
        upsert_file(conn, src, "python", "h1", [], [], comments)
        return conn, src

    def test_reindex_does_not_duplicate_comments(self) -> None:
        """Upserting the same file twice must REPLACE comments, not accumulate them."""
        from seam.indexer.db import upsert_file

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "seam.db"
            conn, src = self._seed(db_path)
            comments = [Comment(marker="WHY", text="r", line=1)]
            upsert_file(conn, src, "python", "h2", [], [], comments)  # re-index
            count = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
            conn.close()
            assert count == 1, f"re-index must not double comments, got {count}"

    def test_delete_file_cascades_to_comments(self) -> None:
        """delete_file removes the file's comments (FK ON DELETE CASCADE)."""
        from seam.indexer.db import delete_file

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "seam.db"
            conn, src = self._seed(db_path)
            assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 1
            delete_file(conn, src)
            count = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
            conn.close()
            assert count == 0, f"delete_file must cascade-remove comments, got {count}"


# ── R3: why() degrades on a pre-1b index with no comments table (review fix) ──


class TestWhyMissingCommentsTable:
    """Review fix: why() must return [] (not raise) when the comments table is
    absent — a pre-1b index opened via connect() (which does not run the schema)."""

    def test_why_returns_empty_when_table_missing(self) -> None:
        # Build a v2-style DB WITHOUT the comments table, reusing the migration
        # test's seeder, and open it with a BARE connection (no schema/migration).
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            TestMigrationV2ToV3()._make_v2_db(db_path).close()
            from seam.query.comments import why

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                # File and symbol modes both degrade gracefully, no OperationalError.
                assert why(conn, file="/whatever.py") == []
                assert why(conn, symbol="anything") == []
            finally:
                conn.close()
        finally:
            db_path.unlink(missing_ok=True)
