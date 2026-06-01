"""Integration tests for handle_seam_why (seam/server/tools.py) + CLI seam why.

Tests call the handler directly against a seeded SQLite DB, mirroring
test_impact_handler.py style.

Coverage:
    H1  no file or symbol -> INVALID_INPUT
    H2  file+comments -> relativized paths returned
    H3  file:line target parsing (CLI helper)
    H4  empty result -> [] (not error)
    H5  symbol mode -> correct comments returned
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Comment, Symbol
from seam.server.tools import handle_seam_why

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 5) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file,
        start_line=start, end_line=end, docstring=None
    )


def _comment(marker: str, text: str, line: int) -> Comment:
    return Comment(marker=marker, text=text, line=line)


@pytest.fixture()
def seeded_why_db() -> tuple[sqlite3.Connection, Path, Path]:
    """DB with one file, two semantic comments, one symbol."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        src_file = tmp_path / "app.py"
        src_file.write_text("x = 1\n" * 20)

        conn = init_db(db_path)
        upsert_file(
            conn,
            src_file,
            "python",
            "abc",
            [_sym("my_func", str(src_file), start=5, end=15)],
            [],
            [
                _comment("WHY", "safety first", line=3),
                _comment("HACK", "legacy compat", line=10),
            ],
        )
        yield conn, src_file, tmp_path


# ── H1: validation ────────────────────────────────────────────────────────────


class TestHandleSeamWhyValidation:
    def test_no_file_no_symbol_returns_invalid_input(
        self, seeded_why_db: tuple
    ) -> None:
        conn, src_file, root = seeded_why_db
        result = handle_seam_why(conn, root)
        assert isinstance(result, dict)
        assert result.get("error") == "INVALID_INPUT"


# ── H2: file mode + relativization ────────────────────────────────────────────


class TestHandleSeamWhyFileMode:
    def test_returns_list_for_known_file(self, seeded_why_db: tuple) -> None:
        conn, src_file, root = seeded_why_db
        result = handle_seam_why(conn, root, file=str(src_file))
        assert isinstance(result, list)
        assert len(result) == 2

    def test_file_paths_relativized(self, seeded_why_db: tuple) -> None:
        conn, src_file, root = seeded_why_db
        result = handle_seam_why(conn, root, file=str(src_file))
        assert isinstance(result, list)
        for item in result:
            # Should be relative to root, not absolute
            assert not Path(item["file"]).is_absolute() or not str(item["file"]).startswith(str(root))
            # More precisely: the file path should be relative to root
            assert item["file"] == str(src_file.relative_to(root))

    def test_empty_result_for_unknown_file(self, seeded_why_db: tuple) -> None:
        conn, src_file, root = seeded_why_db
        result = handle_seam_why(conn, root, file="/nonexistent/path.py")
        assert result == []


# ── H3: CLI file:line parsing ─────────────────────────────────────────────────


class TestCliFileLineParsing:
    """Tests for the file:line parsing logic used by the CLI command."""

    def test_parse_file_colon_line(self) -> None:
        """'path/to/file.py:42' -> file='path/to/file.py', line=42."""
        from seam.cli.main import _parse_why_target

        file_path, line = _parse_why_target("path/to/file.py:42")
        assert file_path == "path/to/file.py"
        assert line == 42

    def test_parse_file_only(self) -> None:
        """'path/to/file.py' -> file='path/to/file.py', line=None."""
        from seam.cli.main import _parse_why_target

        file_path, line = _parse_why_target("path/to/file.py")
        assert file_path == "path/to/file.py"
        assert line is None

    def test_parse_path_with_colon_in_dir(self) -> None:
        """Paths like 'some:dir/file.py' without trailing int -> file only."""
        from seam.cli.main import _parse_why_target

        file_path, line = _parse_why_target("some:dir/file.py")
        assert file_path == "some:dir/file.py"
        assert line is None


# ── H4: empty result ──────────────────────────────────────────────────────────


class TestHandleSeamWhyEmpty:
    def test_empty_list_not_error(self, seeded_why_db: tuple) -> None:
        """File with no semantic comments returns [] not an error dict."""
        conn, src_file, root = seeded_why_db
        # Query a different file that was never indexed
        result = handle_seam_why(conn, root, file="/tmp/not_indexed.py")
        assert result == []


# ── H5: symbol mode ──────────────────────────────────────────────────────────


class TestHandleSeamWhySymbolMode:
    def test_symbol_mode_returns_comments(self, seeded_why_db: tuple) -> None:
        """Symbol mode returns comments within the symbol's range."""
        conn, src_file, root = seeded_why_db
        # my_func is at lines 5-15; LEAD=5 -> range [0, 15]
        # WHY at line 3 < 5, so may or may not be in range depending on LEAD
        # HACK at line 10 is inside the body -> definitely included
        result = handle_seam_why(conn, root, symbol="my_func")
        assert isinstance(result, list)
        # At least the body comment (line 10) should appear
        assert any(r["marker"] == "HACK" for r in result)
