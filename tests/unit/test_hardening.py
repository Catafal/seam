"""Regression tests for the post-review hardening pass.

Each test pins a fix surfaced by /backend-taste or /review so it can't regress:
- foreign_keys enabled per connection (re-index/delete cascade correctly)
- clean re-index (no orphaned/duplicate symbols or edges)
- index_one_file distinguishes skipped (None) from indexed-but-empty ((0, 0))
- malformed FTS5 surfaces as INVALID_QUERY in seam_query (not silent [])
- graph: nested functions are not mis-tagged as methods
- graph: decorated classes (@dataclass) are extracted
- graph: docstrings with boundary quote characters are preserved
"""

import sqlite3
from pathlib import Path

import pytest

from seam.indexer.db import connect, delete_file, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol, extract_symbols
from seam.indexer.parser import parse_python
from seam.indexer.pipeline import index_one_file
from seam.query import engine
from seam.server import tools


def _sym(name: str, kind: str, file: str) -> Symbol:
    return Symbol(name=name, kind=kind, file=file, start_line=1, end_line=2, docstring=None)


# ── Storage / concurrency hardening ─────────────────────────────────────────


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    """connect() must turn FK enforcement ON for every connection (it's per-connection)."""
    conn = connect(tmp_path / "t.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_reindex_replaces_cleanly_no_orphans(tmp_path: Path) -> None:
    """Re-indexing a file with new content must not leave orphaned symbols/edges."""
    src = tmp_path / "m.py"
    src.write_text("x = 1\n")
    conn = init_db(tmp_path / "idx.db")
    try:
        upsert_file(conn, src, "python", "h1",
                    [_sym("a", "function", str(src))],
                    [Edge(source="m", target="os", kind="import", file=str(src), line=1)])
        # Re-index the SAME path with entirely different symbols and no edges.
        upsert_file(conn, src, "python", "h2", [_sym("b", "function", str(src))], [])

        names = [r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()]
        assert names == ["b"]  # 'a' is gone, not duplicated
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0  # old edge gone
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1  # stable id, one row
    finally:
        conn.close()


def test_delete_file_cascades(tmp_path: Path) -> None:
    """delete_file must remove the file AND its symbols AND its edges (FK cascade)."""
    src = tmp_path / "m.py"
    src.write_text("x = 1\n")
    conn = init_db(tmp_path / "idx.db")
    try:
        upsert_file(conn, src, "python", "h1",
                    [_sym("a", "function", str(src))],
                    [Edge(source="m", target="os", kind="import", file=str(src), line=1)])
        delete_file(conn, src)
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
    finally:
        conn.close()


# ── Pipeline: skipped vs indexed-but-empty ──────────────────────────────────


def test_index_one_file_none_for_skip_tuple_for_empty(tmp_path: Path) -> None:
    """None == skipped (unsupported ext); (0, 0) == indexed valid-but-empty file."""
    conn = init_db(tmp_path / "idx.db")
    try:
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        assert index_one_file(conn, txt) is None  # unsupported extension -> skipped

        empty = tmp_path / "__init__.py"
        empty.write_text("")
        assert index_one_file(conn, empty) == (0, 0)  # valid, indexed, zero symbols
        # The empty file IS recorded in files (it was indexed, not skipped).
        assert conn.execute(
            "SELECT COUNT(*) FROM files WHERE path = ?", (str(empty),)
        ).fetchone()[0] == 1
    finally:
        conn.close()


# ── Query error consistency ─────────────────────────────────────────────────


def test_query_maps_malformed_fts_to_invalid_query(tmp_path: Path) -> None:
    """seam_query must return INVALID_QUERY (not silent []) on malformed FTS5."""
    conn = init_db(tmp_path / "idx.db")
    try:
        # Unbalanced quote is a hard FTS5 syntax error.
        result = tools.handle_seam_query(conn, 'foo"', tmp_path)
        assert isinstance(result, dict) and result.get("error") == "INVALID_QUERY"
        # engine.query itself raises (does not swallow) so the handler can map it.
        with pytest.raises(sqlite3.OperationalError):
            engine.query(conn, 'foo"', 10)
    finally:
        conn.close()


# ── Graph extraction hardening ──────────────────────────────────────────────


def test_nested_function_not_tagged_as_method(tmp_path: Path) -> None:
    """A function nested inside a method is a local function, not a class method."""
    src = tmp_path / "c.py"
    src.write_text("class C:\n    def m(self):\n        def inner():\n            return 1\n")
    syms = extract_symbols(parse_python(src), "python", src)
    by_name = {s["name"]: s["kind"] for s in syms}
    assert by_name.get("C") == "class"
    assert by_name.get("C.m") == "method"
    assert by_name.get("inner") == "function"  # NOT "C.inner", NOT a method
    assert "C.inner" not in by_name


def test_decorated_class_is_extracted(tmp_path: Path) -> None:
    """A decorated class (@dataclass) and its methods must be indexed, not dropped."""
    src = tmp_path / "d.py"
    src.write_text(
        "import dataclasses\n\n"
        "@dataclasses.dataclass\n"
        "class Foo:\n"
        "    x: int\n"
        "    def bar(self):\n"
        "        return self.x\n"
    )
    syms = extract_symbols(parse_python(src), "python", src)
    by_name = {s["name"]: s["kind"] for s in syms}
    assert by_name.get("Foo") == "class"
    assert by_name.get("Foo.bar") == "method"


def test_docstring_with_quotes_preserved(tmp_path: Path) -> None:
    """Docstring content with boundary quotes must survive extraction intact."""
    src = tmp_path / "q.py"
    src.write_text('def f():\n    """\'quoted\' word"""\n    return 1\n')
    syms = extract_symbols(parse_python(src), "python", src)
    f = next(s for s in syms if s["name"] == "f")
    assert f["docstring"] == "'quoted' word"  # leading/trailing apostrophes kept
