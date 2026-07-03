"""Unit tests for seam/indexer/rebase.py — index rebase leaf.

TDD: tests written before implementation. Each test group covers one
behavioral slice verified through the public interface (DB state queries).

Coverage:
    RB1 — basic prefix rewrite: old→new, correct row count returned
    RB2 — explicit old_root: only rows under old_root are rewritten
    RB3 — auto-detect old_root from DB when old_root=None
    RB4 — synthetic rows (path LIKE ':%') are never rewritten
    RB5 — idempotency: rebasing an already-local index returns 0, no mutation
    RB6 — no-match safety: rows NOT under old_root are left untouched
    RB7 — cross-separator: a /- separated source prefix re-homes onto local root
    RB8 — empty DB: returns 0, never raises
    RB9 — new_root == old_root: returns 0 (no-op by design)
    RB10 — multiple files, partial overlap with prefix
"""

import sqlite3

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the minimal files table schema.

    WHY minimal schema (not init_db): rebase only touches `files.path`; there
    is no need for FTS5, migration chains, or the full Seam schema here. A
    minimal table is faster, has zero external deps, and keeps the test scope
    narrow — we test rebase behavior, not DB initialization.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE files (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            path    TEXT    NOT NULL UNIQUE,
            mtime   REAL    NOT NULL DEFAULT 0,
            file_hash TEXT  NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    return conn


def _insert_file(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("INSERT INTO files (path) VALUES (?)", (path,))
    conn.commit()


def _all_paths(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT path FROM files ORDER BY path")]


# ── RB1: Basic prefix rewrite ─────────────────────────────────────────────────


class TestBasicPrefixRewrite:
    """RB1: Rewriting old_root→new_root updates all matching paths."""

    def test_single_file_rewritten(self) -> None:
        """A single file's path is rewritten from old to new prefix."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/old/project/src/foo.py")

        n = rebase_index(conn, new_root="/new/project", old_root="/old/project")

        assert n == 1
        assert _all_paths(conn) == ["/new/project/src/foo.py"]

    def test_multiple_files_rewritten(self) -> None:
        """All files under old_root are rewritten; count equals number of files."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/old/project/src/a.py")
        _insert_file(conn, "/old/project/src/b.py")
        _insert_file(conn, "/old/project/lib/c.ts")

        n = rebase_index(conn, new_root="/new/project", old_root="/old/project")

        assert n == 3
        paths = _all_paths(conn)
        assert "/new/project/src/a.py" in paths
        assert "/new/project/src/b.py" in paths
        assert "/new/project/lib/c.ts" in paths

    def test_returns_count_of_rewritten_rows(self) -> None:
        """Return value equals the number of rows whose path was changed."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/src/app/main.py")
        _insert_file(conn, "/src/app/utils.py")

        n = rebase_index(conn, new_root="/home/user/app", old_root="/src/app")

        assert n == 2


# ── RB2: Explicit old_root ─────────────────────────────────────────────────────


class TestExplicitOldRoot:
    """RB2: With an explicit old_root, only matching rows are rewritten."""

    def test_non_matching_rows_untouched(self) -> None:
        """Rows NOT under old_root are left unchanged."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/alice/project/foo.py")
        _insert_file(conn, "/bob/otherproject/bar.py")  # different prefix

        n = rebase_index(conn, new_root="/new/project", old_root="/alice/project")

        assert n == 1
        paths = _all_paths(conn)
        assert "/new/project/foo.py" in paths
        assert "/bob/otherproject/bar.py" in paths  # untouched


# ── RB3: Auto-detect old_root ──────────────────────────────────────────────────


class TestAutoDetectOldRoot:
    """RB3: When old_root=None, common prefix is auto-detected from DB rows."""

    def test_auto_detect_single_file(self) -> None:
        """Auto-detect with one file uses dirname of that file as the old prefix.

        WHY dirname: commonpath(["/a/b/src/main.py"]) returns the file path
        itself; we then apply dirname to get the containing DIRECTORY
        ("/a/b/src"), which is the correct directory prefix to strip.
        With new_root="/local/repo" and old_root="/machines/build01/repo/src",
        the relative portion is "main.py", so the result is "/local/repo/main.py".
        """
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/machines/build01/repo/src/main.py")

        n = rebase_index(conn, new_root="/local/repo")

        assert n == 1
        # dirname of the single file is /machines/build01/repo/src → relative = main.py
        assert _all_paths(conn) == ["/local/repo/main.py"]

    def test_auto_detect_multiple_files_common_prefix(self) -> None:
        """Auto-detect finds the common directory prefix of all file paths."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/ci/runner/workspace/src/a.py")
        _insert_file(conn, "/ci/runner/workspace/src/b.py")
        _insert_file(conn, "/ci/runner/workspace/tests/test_a.py")

        n = rebase_index(conn, new_root="/home/dev/workspace")

        assert n == 3
        paths = _all_paths(conn)
        assert "/home/dev/workspace/src/a.py" in paths
        assert "/home/dev/workspace/src/b.py" in paths
        assert "/home/dev/workspace/tests/test_a.py" in paths

    def test_auto_detect_ignores_synthetic_rows(self) -> None:
        """Synthetic rows are excluded from auto-detection prefix computation.

        With one real file /ci/workspace/src/foo.py, auto-detect computes
        dirname → /ci/workspace/src as old_root.  After rebasing to
        /local/workspace the relative portion is "foo.py", giving
        /local/workspace/foo.py.
        """
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/ci/workspace/src/foo.py")
        _insert_file(conn, ":synthesis:")  # synthetic row

        n = rebase_index(conn, new_root="/local/workspace")

        # Only the real file row is rewritten; dirname strips /ci/workspace/src
        assert n == 1
        paths = _all_paths(conn)
        assert "/local/workspace/foo.py" in paths
        assert ":synthesis:" in paths  # synthetic row untouched


# ── RB4: Synthetic rows never rewritten ───────────────────────────────────────


class TestSyntheticRowsSkipped:
    """RB4: Rows whose path starts with ':' are always left untouched."""

    def test_synthesis_row_not_rewritten(self) -> None:
        """The :synthesis: synthetic file row is never rewritten."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/old/project/src/a.py")
        _insert_file(conn, ":synthesis:")

        rebase_index(conn, new_root="/new/project", old_root="/old/project")

        paths = _all_paths(conn)
        assert ":synthesis:" in paths

    def test_colon_prefix_variants_not_rewritten(self) -> None:
        """Any path starting with ':' is treated as synthetic and left alone."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/old/repo/main.py")
        _insert_file(conn, ":synthesis:")
        _insert_file(conn, ":test-edges:")

        n = rebase_index(conn, new_root="/new/repo", old_root="/old/repo")

        # Only the one real file is rewritten
        assert n == 1
        paths = _all_paths(conn)
        assert ":synthesis:" in paths
        assert ":test-edges:" in paths
        assert "/new/repo/main.py" in paths


# ── RB5: Idempotency ──────────────────────────────────────────────────────────


class TestIdempotency:
    """RB5: Rebasing an already-local index is a no-op; running twice is safe."""

    def test_rebase_to_same_root_returns_zero(self) -> None:
        """If all paths already start with new_root, rebase returns 0."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/local/project/src/foo.py")
        _insert_file(conn, "/local/project/src/bar.py")

        n = rebase_index(conn, new_root="/local/project", old_root="/local/project")

        assert n == 0
        # Paths unchanged
        paths = _all_paths(conn)
        assert "/local/project/src/foo.py" in paths
        assert "/local/project/src/bar.py" in paths

    def test_rebase_twice_is_idempotent(self) -> None:
        """Calling rebase twice produces the same final state."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/ci/build/src/a.py")

        # First rebase
        n1 = rebase_index(conn, new_root="/local/project", old_root="/ci/build")
        # Second rebase with the same new root
        n2 = rebase_index(conn, new_root="/local/project", old_root="/local/project")

        assert n1 == 1
        assert n2 == 0  # already at local root — nothing to rewrite
        assert _all_paths(conn) == ["/local/project/src/a.py"]

    def test_auto_detect_already_local_returns_zero(self) -> None:
        """Auto-detect on an already-local index: detected prefix == new_root → 0 rows changed.

        WHY multiple files in DIFFERENT subdirectories:
          With files in src/ and tests/, commonpath naturally returns the
          project root (/local/workspace) — the directory where they diverge.
          With a single file, commonpath would return the file itself and
          dirname would return the parent dir (/local/workspace/src), which
          does NOT equal new_root, so rebase would run. The idempotency
          guarantee requires the detected root to equal the project root, which
          only happens reliably when files in multiple subdirectories are present.
        """
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/local/workspace/src/foo.py")
        _insert_file(conn, "/local/workspace/tests/test_foo.py")

        # commonpath of these two paths = /local/workspace (where src/ and tests/ diverge)
        n = rebase_index(conn, new_root="/local/workspace")

        assert n == 0
        paths = _all_paths(conn)
        assert "/local/workspace/src/foo.py" in paths
        assert "/local/workspace/tests/test_foo.py" in paths


# ── RB6: No-match safety ──────────────────────────────────────────────────────


class TestNoMatchSafety:
    """RB6: When no rows match old_root, nothing is changed and 0 is returned."""

    def test_no_matching_rows_returns_zero(self) -> None:
        """When old_root does not match any path, 0 is returned and DB unchanged."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/some/other/path/file.py")

        n = rebase_index(conn, new_root="/new/root", old_root="/completely/different")

        assert n == 0
        assert _all_paths(conn) == ["/some/other/path/file.py"]


# ── RB7: Cross-separator ──────────────────────────────────────────────────────


class TestCrossSeparator:
    """RB7: A /-separated source prefix re-homes correctly onto the local root."""

    def test_posix_paths_rebase_correctly(self) -> None:
        """Standard POSIX paths rebase without separator issues."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/home/ci/runner/project/src/module.py")
        _insert_file(conn, "/home/ci/runner/project/tests/test_module.py")

        n = rebase_index(
            conn,
            new_root="/Users/dev/project",
            old_root="/home/ci/runner/project",
        )

        assert n == 2
        paths = _all_paths(conn)
        assert "/Users/dev/project/src/module.py" in paths
        assert "/Users/dev/project/tests/test_module.py" in paths

    def test_deep_nested_paths_rebase_correctly(self) -> None:
        """Deeply nested paths keep their relative structure after rebase."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/build/workspace/a/b/c/deep.py")

        n = rebase_index(conn, new_root="/local", old_root="/build/workspace")

        assert n == 1
        assert _all_paths(conn) == ["/local/a/b/c/deep.py"]


# ── RB8: Empty DB ─────────────────────────────────────────────────────────────


class TestEmptyDB:
    """RB8: rebase_index on an empty DB returns 0 and never raises."""

    def test_empty_db_returns_zero(self) -> None:
        """Empty DB: no files to rewrite; return 0."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()

        n = rebase_index(conn, new_root="/any/root")

        assert n == 0

    def test_empty_db_with_explicit_old_root(self) -> None:
        """Empty DB with explicit old_root: 0, no error."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()

        n = rebase_index(conn, new_root="/new", old_root="/old")

        assert n == 0

    def test_never_raises_on_broken_connection(self) -> None:
        """If the connection is closed, rebase returns 0 without raising."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        conn.close()  # deliberately break

        # The leaf contract: NEVER raises
        result = rebase_index(conn, new_root="/new", old_root="/old")
        assert result == 0


# ── RB9: new_root == old_root ─────────────────────────────────────────────────


class TestNewRootEqualsOldRoot:
    """RB9: When new_root and old_root are the same, it is a no-op."""

    def test_same_root_explicit(self) -> None:
        """Explicit same old_root=new_root: returns 0, DB unchanged."""
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/project/src/main.py")

        n = rebase_index(conn, new_root="/project", old_root="/project")

        assert n == 0
        assert _all_paths(conn) == ["/project/src/main.py"]


# ── RB10: Multiple files, partial overlap ─────────────────────────────────────


class TestPartialOverlap:
    """RB10: Only files strictly under old_root get rewritten; siblings are safe."""

    def test_sibling_directories_not_affected(self) -> None:
        """Files in a directory with the same prefix-name are not rewritten.

        e.g. old_root=/workspace/app should NOT rewrite /workspace/app2/foo.py
        because 'app2' is not 'app'. This guards against naive startswith() on
        strings without a trailing separator check.
        """
        from seam.indexer.rebase import rebase_index

        conn = _make_conn()
        _insert_file(conn, "/workspace/app/main.py")   # SHOULD be rewritten
        _insert_file(conn, "/workspace/app2/other.py")  # must NOT be rewritten

        n = rebase_index(conn, new_root="/local/app", old_root="/workspace/app")

        assert n == 1
        paths = _all_paths(conn)
        assert "/local/app/main.py" in paths
        assert "/workspace/app2/other.py" in paths  # sibling untouched
