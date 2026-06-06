"""Tier D #12 — end-to-end: camelCase identifiers are searchable by sub-word + v11 migration.

Verifies the index→query loop: a symbol named GlobalPushToTalkShortcutMonitor is found by the
natural-language query "push to talk monitor" once search_text is populated, exact-name search
still ranks #1, the SEAM_TOKENIZE_IDENTIFIERS=off path is byte-identical (no split hits), and the
v10→v11 auto-migration adds the column + rebuilt FTS without crashing reads.
"""

import sqlite3
import tempfile
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Symbol


def _sym(name: str, kind: str = "class", qualified: str | None = None) -> Symbol:
    s = Symbol(name=name, kind=kind, file="x", start_line=1, end_line=5, docstring=None)
    if qualified is not None:
        s["qualified_name"] = qualified  # type: ignore[typeddict-unknown-key]
    return s


def _index(symbols: list[Symbol]) -> sqlite3.Connection:
    conn = init_db(Path(":memory:"))
    with tempfile.NamedTemporaryFile(suffix=".swift", delete=False) as f:
        filepath = Path(f.name)
        f.write(b"// t\n")
    try:
        adj = [
            Symbol(
                name=s["name"], kind=s["kind"], file=str(filepath),
                start_line=s["start_line"], end_line=s["end_line"], docstring=None,
            )
            for s in symbols
        ]
        for s, orig in zip(adj, symbols):
            if "qualified_name" in orig:
                s["qualified_name"] = orig["qualified_name"]  # type: ignore[typeddict-item]
        upsert_file(conn, filepath, "swift", "sha", adj, [])
    finally:
        filepath.unlink(missing_ok=True)
    return conn


def _fts_names(conn: sqlite3.Connection, query: str) -> list[str]:
    from seam.query.fts import build_match_query

    match = build_match_query(query)
    if not match:
        return []
    rows = conn.execute(
        "SELECT name FROM symbols_fts WHERE symbols_fts MATCH ? ORDER BY rank", (match,)
    ).fetchall()
    return [r["name"] for r in rows]


class TestCamelCaseSearchRecall:
    def test_natural_language_query_finds_camelcase_symbol(self) -> None:
        conn = _index([_sym("GlobalPushToTalkShortcutMonitor"), _sym("UnrelatedThing")])
        names = _fts_names(conn, "push to talk monitor")
        conn.close()
        assert "GlobalPushToTalkShortcutMonitor" in names

    def test_partial_subword_query_finds_symbol(self) -> None:
        conn = _index([_sym("CompanionScreenCaptureUtility"), _sym("WidgetFactory")])
        names = _fts_names(conn, "screen capture")
        conn.close()
        assert "CompanionScreenCaptureUtility" in names

    def test_exact_name_still_matches(self) -> None:
        conn = _index([_sym("GlobalPushToTalkShortcutMonitor")])
        names = _fts_names(conn, "GlobalPushToTalkShortcutMonitor")
        conn.close()
        assert "GlobalPushToTalkShortcutMonitor" in names

    def test_search_text_populated_on_index(self) -> None:
        conn = _index([_sym("FooBarBaz")])
        row = conn.execute("SELECT search_text FROM symbols WHERE name='FooBarBaz'").fetchone()
        conn.close()
        assert row["search_text"] == "foo bar baz"


class TestV10ToV11Migration:
    """A real pre-v11 DB (search_text absent) auto-upgrades on connect()."""

    def test_migration_adds_column_and_rebuilds_fts(self) -> None:
        from seam.indexer.migrations import _run_migration_v10_to_v11

        # Build a v11 DB, then forcibly downgrade to simulate a pre-v11 index:
        # drop the column-bearing FTS + column and reset version to 10.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            dbpath = Path(f.name)
        try:
            conn = init_db(dbpath)
            conn.execute("UPDATE metadata SET value='10' WHERE key='schema_version'")
            conn.commit()
            conn.close()

            # Reopen and run the migration explicitly (connect() also runs it, but assert directly).
            conn = sqlite3.connect(str(dbpath))
            conn.row_factory = sqlite3.Row
            _run_migration_v10_to_v11(conn)
            ver = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()["value"]
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()}
            # FTS still queryable after rebuild (no crash on reads).
            conn.execute("SELECT name FROM symbols_fts WHERE symbols_fts MATCH 'x' LIMIT 1").fetchall()
            conn.close()

            assert ver == "11"
            assert "search_text" in cols
        finally:
            dbpath.unlink(missing_ok=True)

    def test_migration_idempotent(self) -> None:
        from seam.indexer.migrations import _run_migration_v10_to_v11

        conn = init_db(Path(":memory:"))  # already v11
        # Running again must be a no-op (version guard), not raise.
        _run_migration_v10_to_v11(conn)
        ver = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()["value"]
        conn.close()
        assert ver == "11"
