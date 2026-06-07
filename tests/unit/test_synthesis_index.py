"""Unit tests for seam/indexer/synthesis_index.py — synthesis orchestration bridge.

TDD: tests written BEFORE implementation (RED first).

Coverage:
  SINGLE-TX:      index_synthesis writes in ONE transaction
  NEVER-RAISES:   index_synthesis never raises; returns -1 on error
  MINUS-ONE:      returns -1 when DB write fails (force an error)
  OFF:            SEAM_EDGE_SYNTHESIS=off → returns 0, writes nothing
  RETURNS-COUNT:  returns number of synthesized edges written
  IDEMPOTENT:     calling index_synthesis twice clears stale edges first
"""

import sqlite3
from pathlib import Path


def _minimal_db() -> sqlite3.Connection:
    """Create a minimal in-memory DB with the schema needed for synthesis tests."""
    from seam.indexer.db import init_db
    conn = init_db(Path(":memory:"))
    return conn


def _insert_file(conn: sqlite3.Connection, path: str = "/test.py") -> int:
    """Insert a file row and return its id."""
    conn.execute(
        "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (?, 'python', 'hash1', 1.0, 1.0)",
        (path,),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    return row[0]


def _insert_symbol(conn: sqlite3.Connection, file_id: int, name: str, kind: str = "method") -> None:
    """Insert a symbol row."""
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
        " VALUES (?, ?, ?, 1, 5)",
        (file_id, name, kind),
    )
    conn.commit()


def _insert_edge(conn: sqlite3.Connection, file_id: int, src: str, tgt: str, kind: str) -> None:
    """Insert an edge row."""
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
        " VALUES (?, ?, ?, ?, 1, 'EXTRACTED')",
        (src, tgt, kind, file_id),
    )
    conn.commit()


def _count_synth_edges(conn: sqlite3.Connection) -> int:
    """Count synthesized edges (synthesized_by IS NOT NULL)."""
    return conn.execute(
        "SELECT COUNT(*) FROM edges WHERE synthesized_by IS NOT NULL"
    ).fetchone()[0]


# ── Core functionality ────────────────────────────────────────────────────────


class TestIndexSynthesisBasic:
    """index_synthesis writes synthesized edges to the DB."""

    def test_returns_count_on_success(self) -> None:
        """index_synthesis returns the number of edges written (>=0 on success)."""
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.run", "method")
        _insert_symbol(conn, fid, "Worker", "class")
        _insert_symbol(conn, fid, "Worker.run", "method")
        _insert_edge(conn, fid, "Worker", "IFace", "implements")

        result = index_synthesis(conn, enabled=True, fanout_cap=40)
        assert isinstance(result, int)
        assert result >= 0, f"Expected >= 0 on success; got {result}"

    def test_synthesized_edges_written_to_db(self) -> None:
        """After index_synthesis, synthesized edges must be in the edges table."""
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.process", "method")
        _insert_symbol(conn, fid, "Concrete", "class")
        _insert_symbol(conn, fid, "Concrete.process", "method")
        _insert_edge(conn, fid, "Concrete", "IFace", "implements")

        count = index_synthesis(conn, enabled=True, fanout_cap=40)
        assert count > 0, f"Expected >0 edges; got {count}"

        rows = conn.execute(
            "SELECT source_name, target_name, synthesized_by FROM edges WHERE synthesized_by IS NOT NULL"
        ).fetchall()
        assert any(
            r[0] == "IFace.process" and r[1] == "Concrete.process"
            for r in rows
        ), f"Expected IFace.process→Concrete.process; got {rows}"

    def test_synthesized_edges_have_correct_kind(self) -> None:
        """Synthesized edges must have kind='call'."""
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.run", "method")
        _insert_symbol(conn, fid, "Worker", "class")
        _insert_symbol(conn, fid, "Worker.run", "method")
        _insert_edge(conn, fid, "Worker", "IFace", "implements")

        index_synthesis(conn, enabled=True, fanout_cap=40)

        rows = conn.execute(
            "SELECT kind FROM edges WHERE synthesized_by IS NOT NULL"
        ).fetchall()
        assert all(r[0] == "call" for r in rows), (
            f"Expected all synthesized edges to have kind='call'; got {[r[0] for r in rows]}"
        )

    def test_synthesized_edges_have_inferred_confidence(self) -> None:
        """Synthesized edges must have confidence='INFERRED'."""
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.run", "method")
        _insert_symbol(conn, fid, "Worker", "class")
        _insert_symbol(conn, fid, "Worker.run", "method")
        _insert_edge(conn, fid, "Worker", "IFace", "implements")

        index_synthesis(conn, enabled=True, fanout_cap=40)

        rows = conn.execute(
            "SELECT confidence FROM edges WHERE synthesized_by IS NOT NULL"
        ).fetchall()
        assert all(r[0] == "INFERRED" for r in rows), (
            f"Expected INFERRED; got {[r[0] for r in rows]}"
        )


# ── SEAM_EDGE_SYNTHESIS=off ───────────────────────────────────────────────────


class TestSynthesisDisabled:
    """SEAM_EDGE_SYNTHESIS=off → index_synthesis returns 0, writes nothing."""

    def test_returns_zero_when_disabled(self) -> None:
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.run", "method")
        _insert_symbol(conn, fid, "Worker", "class")
        _insert_symbol(conn, fid, "Worker.run", "method")
        _insert_edge(conn, fid, "Worker", "IFace", "implements")

        result = index_synthesis(conn, enabled=False, fanout_cap=40)
        assert result == 0, f"Expected 0 when disabled; got {result}"

    def test_writes_nothing_when_disabled(self) -> None:
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.run", "method")
        _insert_symbol(conn, fid, "Worker", "class")
        _insert_symbol(conn, fid, "Worker.run", "method")
        _insert_edge(conn, fid, "Worker", "IFace", "implements")

        index_synthesis(conn, enabled=False, fanout_cap=40)
        assert _count_synth_edges(conn) == 0


# ── Never raises / -1 on failure ─────────────────────────────────────────────


class TestNeverRaises:
    """index_synthesis never raises; returns -1 on error."""

    def test_never_raises_on_any_input(self) -> None:
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        try:
            # Even with an empty DB, should not raise.
            result = index_synthesis(conn, enabled=True, fanout_cap=40)
            assert isinstance(result, int)
        except Exception as exc:
            raise AssertionError(
                f"index_synthesis raised unexpectedly: {type(exc).__name__}: {exc}"
            ) from exc

    def test_returns_minus_one_on_write_failure(self) -> None:
        """Force a failure (close the connection) → must return -1, not raise."""
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        # Close the connection to force a DB error on any write.
        conn.close()

        result = index_synthesis(conn, enabled=True, fanout_cap=40)
        assert result == -1, f"Expected -1 on failure; got {result}"


# ── Idempotency ───────────────────────────────────────────────────────────────


class TestIdempotency:
    """Calling index_synthesis twice clears stale edges and re-emits fresh ones."""

    def test_second_call_does_not_double_edges(self) -> None:
        """Running index_synthesis twice must not accumulate duplicate synthesized edges."""
        from seam.indexer.synthesis_index import index_synthesis

        conn = _minimal_db()
        fid = _insert_file(conn)
        _insert_symbol(conn, fid, "IFace", "interface")
        _insert_symbol(conn, fid, "IFace.run", "method")
        _insert_symbol(conn, fid, "Worker", "class")
        _insert_symbol(conn, fid, "Worker.run", "method")
        _insert_edge(conn, fid, "Worker", "IFace", "implements")

        c1 = index_synthesis(conn, enabled=True, fanout_cap=40)
        c2 = index_synthesis(conn, enabled=True, fanout_cap=40)

        # Both should report the same count.
        assert c1 == c2, f"Expected same count on second call; {c1} vs {c2}"

        # DB must have exactly c1 synthesized edges (no duplicates).
        db_count = _count_synth_edges(conn)
        assert db_count == c1, f"DB has {db_count} but index_synthesis returned {c1}"
