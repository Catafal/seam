"""Unit tests for seam/indexer/sync.py — reconcile logic and gating.

Test groups:
    S1 — SyncResult shape: all required keys present with correct types
    S2 — Added file: new on-disk file not in index → indexed, added==1
    S3 — Removed file: tracked file no longer on disk → deleted, removed==1
    S4 — Modified file: tracked file with different content hash → re-indexed, modified==1
    S5 — Unchanged file (exact mtime match): classified unchanged without content read
    S6 — Touch (mtime changed, content identical): classified unchanged via hash confirm, modified==0
    S7 — Cluster gating: zero changes → clusters_recomputed==False, cluster_count==None
    S8 — Cluster gating: ≥1 change → clusters_recomputed==True
    S9 — force_clusters=True with zero changes → clusters_recomputed==True
    S10 — Unreadable file counted in skipped, sync completes normally
    S11 — graph_changed reflects (added + modified + removed) > 0
    S12 — recompute_clusters=False suppresses cluster recompute even with changes
    S13 — Cluster recompute failure (index_clusters returns -1): not reported as success
    S14 — Delete safety: a tracked file still present on disk is NOT removed (exists check)
"""

from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol  # noqa: F401 — used for TypedDict construction
from seam.indexer.sync import SyncResult, sync

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    """Build a minimal Symbol TypedDict for test seeding."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring=None,
        signature=None,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    """Build a minimal Edge TypedDict for test seeding."""
    return Edge(
        source=source,
        target=target,
        kind="call",
        file=file,
        line=1,
        confidence="INFERRED",
    )


def _make_db(tmp_path: Path):
    """Create a fresh initialized DB and return an open connection."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return init_db(db_path)


def _sync_with_defaults(conn, root: Path, **kwargs) -> SyncResult:
    """Call sync() with test-friendly defaults (no LLM, min_size=1)."""
    return sync(
        conn,
        root,
        recompute_clusters=kwargs.pop("recompute_clusters", True),
        force_clusters=kwargs.pop("force_clusters", False),
        naming_mode="deterministic",
        llm_api_key=None,
        llm_model=None,
        min_size=1,
        **kwargs,
    )


# ── S1: SyncResult shape ──────────────────────────────────────────────────────


class TestSyncResultShape:
    """SyncResult has all required keys with correct types."""

    def test_result_has_all_required_keys(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert "added" in result
        assert "modified" in result
        assert "removed" in result
        assert "unchanged" in result
        assert "skipped" in result
        assert "graph_changed" in result
        assert "clusters_recomputed" in result
        assert "cluster_count" in result

    def test_result_counts_are_ints(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert isinstance(result["added"], int)
        assert isinstance(result["modified"], int)
        assert isinstance(result["removed"], int)
        assert isinstance(result["unchanged"], int)
        assert isinstance(result["skipped"], int)

    def test_result_booleans_are_bool(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert isinstance(result["graph_changed"], bool)
        assert isinstance(result["clusters_recomputed"], bool)

    def test_empty_index_all_zero(self, tmp_path: Path) -> None:
        """An empty repo with no indexed files and no disk files → all zero counts."""
        conn = _make_db(tmp_path)
        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["added"] == 0
        assert result["modified"] == 0
        assert result["removed"] == 0
        assert result["unchanged"] == 0
        assert result["skipped"] == 0


# ── S2: Added file ────────────────────────────────────────────────────────────


class TestAddedFile:
    """A new on-disk file not in the index is indexed and counted as added."""

    def test_new_python_file_counted_as_added(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        # Create a new .py file that is NOT in the index yet
        src = tmp_path / "new_file.py"
        src.write_text("def hello(): pass\n")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["added"] == 1
        assert result["modified"] == 0
        assert result["removed"] == 0

    def test_new_file_symbols_present_after_sync(self, tmp_path: Path) -> None:
        """After sync, newly added file's symbols are queryable."""
        conn = _make_db(tmp_path)

        src = tmp_path / "new_file.py"
        src.write_text("def hello(): pass\n")

        _sync_with_defaults(conn, tmp_path)

        # Symbol should now be in the DB
        row = conn.execute(
            "SELECT name FROM symbols WHERE name = 'hello'"
        ).fetchone()
        conn.close()

        assert row is not None, "Symbol 'hello' should be indexed after sync"

    def test_multiple_new_files_all_counted(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        (tmp_path / "a.py").write_text("def a(): pass\n")
        (tmp_path / "b.py").write_text("def b(): pass\n")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["added"] == 2


# ── S3: Removed file ──────────────────────────────────────────────────────────


class TestRemovedFile:
    """A tracked file no longer on disk is removed from the index."""

    def test_deleted_file_counted_as_removed(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        # Seed a file into the index
        src = tmp_path / "to_delete.py"
        src.write_text("def gone(): pass\n")
        upsert_file(conn, src, "python", "abc123", [_sym("gone", str(src))], [])

        # Delete the file from disk
        src.unlink()

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["removed"] == 1
        assert result["modified"] == 0
        assert result["added"] == 0

    def test_deleted_file_symbols_gone_from_db(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "to_delete.py"
        src.write_text("def gone(): pass\n")
        upsert_file(conn, src, "python", "abc123", [_sym("gone", str(src))], [])

        src.unlink()

        _sync_with_defaults(conn, tmp_path)

        # Symbol should no longer be in the DB
        row = conn.execute(
            "SELECT name FROM symbols WHERE name = 'gone'"
        ).fetchone()
        conn.close()

        assert row is None, "Symbol 'gone' should be deleted after file removal"


# ── S4: Modified file ─────────────────────────────────────────────────────────


class TestModifiedFile:
    """A tracked file whose content hash changed is re-indexed as modified."""

    def test_content_changed_file_counted_as_modified(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "modified.py"
        src.write_text("def old_func(): pass\n")
        # Index the original version with the real pipeline (so mtime+hash are stored)
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        # Now change the file content (and bump mtime)
        import time
        time.sleep(0.01)  # ensure mtime differs
        src.write_text("def new_func(): pass\n")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["modified"] == 1
        assert result["added"] == 0
        assert result["removed"] == 0

    def test_modified_file_new_symbols_in_db(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "modified.py"
        src.write_text("def old_func(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        import time
        time.sleep(0.01)
        src.write_text("def new_func(): pass\n")

        _sync_with_defaults(conn, tmp_path)

        # new_func should be in DB, old_func should be gone
        new_row = conn.execute(
            "SELECT name FROM symbols WHERE name = 'new_func'"
        ).fetchone()
        old_row = conn.execute(
            "SELECT name FROM symbols WHERE name = 'old_func'"
        ).fetchone()
        conn.close()

        assert new_row is not None, "new_func should be indexed after sync"
        assert old_row is None, "old_func should be replaced after content change"


# ── S5: Unchanged file (mtime match) ─────────────────────────────────────────


class TestUnchangedFileMtimeMatch:
    """A file whose stored mtime matches on-disk mtime is classified unchanged (no read)."""

    def test_unchanged_file_counted_as_unchanged(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        # Sync without changing the file — mtime matches → unchanged
        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["unchanged"] == 1
        assert result["modified"] == 0
        assert result["added"] == 0
        assert result["removed"] == 0

    def test_unchanged_file_does_not_churn_db(self, tmp_path: Path) -> None:
        """An unchanged file must not update the indexed_at timestamp."""
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        # Get the indexed_at before sync
        row_before = conn.execute(
            "SELECT indexed_at FROM files WHERE path = ?", (str(src),)
        ).fetchone()
        ts_before = row_before["indexed_at"]

        import time
        time.sleep(0.05)

        _sync_with_defaults(conn, tmp_path)

        row_after = conn.execute(
            "SELECT indexed_at FROM files WHERE path = ?", (str(src),)
        ).fetchone()
        ts_after = row_after["indexed_at"]
        conn.close()

        # indexed_at should NOT have changed for an unchanged file
        assert ts_before == ts_after, "Unchanged file must not update indexed_at"


# ── S6: Touch (mtime changed, content identical) ──────────────────────────────


class TestTouchedFileHashSame:
    """A file whose mtime changed but content is identical is classified unchanged via hash confirm."""

    def test_touched_file_not_counted_as_modified(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "touched.py"
        content = "def touched(): pass\n"
        src.write_text(content)
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        # Simulate a touch: update mtime without changing content
        import os
        import time
        time.sleep(0.05)
        current_atime = src.stat().st_atime
        new_mtime = src.stat().st_mtime + 1.0
        os.utime(src, (current_atime, new_mtime))

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        # Content is same → hash matches → unchanged (not modified)
        assert result["modified"] == 0
        assert result["unchanged"] == 1


# ── S7: Cluster gating — zero changes ────────────────────────────────────────


class TestClusterGatingNoChanges:
    """When 0 files change, clusters are NOT recomputed."""

    def test_no_changes_clusters_not_recomputed(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["clusters_recomputed"] is False
        assert result["cluster_count"] is None

    def test_no_changes_graph_changed_false(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["graph_changed"] is False


# ── S8: Cluster gating — ≥1 change ────────────────────────────────────────────


class TestClusterGatingWithChanges:
    """When ≥1 file changes, clusters ARE recomputed."""

    def test_added_file_triggers_cluster_recompute(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "new.py"
        src.write_text("def fresh(): pass\n")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["graph_changed"] is True
        assert result["clusters_recomputed"] is True
        # cluster_count is an int (0 or more) when recomputed
        assert result["cluster_count"] is not None
        assert isinstance(result["cluster_count"], int)

    def test_removed_file_triggers_cluster_recompute(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "to_remove.py"
        src.write_text("def bye(): pass\n")
        upsert_file(conn, src, "python", "deadbeef", [_sym("bye", str(src))], [])
        src.unlink()

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["graph_changed"] is True
        assert result["clusters_recomputed"] is True


# ── S9: force_clusters=True ────────────────────────────────────────────────────


class TestForceClusters:
    """force_clusters=True recomputes clusters even when zero files changed."""

    def test_force_clusters_recomputes_on_zero_changes(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        result = _sync_with_defaults(conn, tmp_path, force_clusters=True)
        conn.close()

        # Zero file changes but force_clusters overrides the gate
        assert result["graph_changed"] is False
        assert result["clusters_recomputed"] is True
        assert result["cluster_count"] is not None

    def test_force_clusters_false_no_override(self, tmp_path: Path) -> None:
        """Default (force_clusters=False) does not recompute when nothing changed."""
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        result = _sync_with_defaults(conn, tmp_path, force_clusters=False)
        conn.close()

        assert result["clusters_recomputed"] is False


# ── S10: Unreadable/unsupported file counted as skipped ───────────────────────


class TestSkippedFile:
    """An unreadable or unsupported file is counted in skipped, sync continues."""

    def test_unsupported_extension_counted_as_skipped_not_added(self, tmp_path: Path) -> None:
        """A .txt file is not in SEAM_LANGUAGE_MAP → walk_project skips it entirely.
        So it never appears in added/skipped from sync. Test that it does not inflate added."""
        conn = _make_db(tmp_path)

        # .txt files are not in SEAM_LANGUAGE_MAP — walk_project won't return them
        (tmp_path / "readme.txt").write_text("not code\n")
        (tmp_path / "real.py").write_text("def real(): pass\n")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        # Only the .py file should be indexed; .txt ignored by walk_project
        assert result["added"] == 1

    def test_sync_continues_after_index_error(self, tmp_path: Path) -> None:
        """Even if one file causes index_one_file to return None, sync completes."""
        conn = _make_db(tmp_path)

        # Create a valid file and a binary file with .py extension (parser will fail)
        good = tmp_path / "good.py"
        good.write_text("def good(): pass\n")

        # Binary content — parser will return None (binary detection)
        bad = tmp_path / "bad.py"
        bad.write_bytes(b"\x00\x01\x02\x03\xff\xfe binary content")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        # Sync must complete; good file should be added; bad may be skipped
        assert result["added"] + result["skipped"] >= 1
        # No exception means sync completed normally


# ── S11: graph_changed reflects counts ────────────────────────────────────────


class TestGraphChanged:
    """graph_changed is True iff (added + modified + removed) > 0."""

    def test_graph_changed_false_when_all_unchanged(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "stable.py"
        src.write_text("def stable(): pass\n")
        from seam.indexer.pipeline import index_one_file
        index_one_file(conn, src)

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["graph_changed"] is False

    def test_graph_changed_true_when_added(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        (tmp_path / "new.py").write_text("def x(): pass\n")

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["graph_changed"] is True


# ── S12: recompute_clusters=False ─────────────────────────────────────────────


class TestRecomputeClustersFalse:
    """recompute_clusters=False suppresses cluster recompute even when files changed."""

    def test_no_cluster_recompute_when_flag_false(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)

        src = tmp_path / "new.py"
        src.write_text("def x(): pass\n")

        result = sync(
            conn,
            tmp_path,
            recompute_clusters=False,
            force_clusters=False,
            naming_mode="deterministic",
            llm_api_key=None,
            llm_model=None,
            min_size=1,
        )
        conn.close()

        # File was added (graph changed) but clusters suppressed
        assert result["added"] == 1
        assert result["graph_changed"] is True
        assert result["clusters_recomputed"] is False
        assert result["cluster_count"] is None


# ── S13: Cluster recompute failure (index_clusters returns -1) ────────────────


class TestClusterRecomputeFailure:
    """index_clusters returns -1 on failure (never raises); sync must NOT report
    that as a healthy recompute. Otherwise an agent branching on the result sees a
    green sync with cluster_count=-1 while clusters are actually stale/broken —
    mirrors the guard `seam init` already applies (total_clusters < 0 → failed).
    """

    def test_cluster_failure_not_reported_as_recomputed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = _make_db(tmp_path)
        (tmp_path / "new.py").write_text("def x(): pass\n")

        # Force the cluster pass to fail (its documented -1 error sentinel).
        monkeypatch.setattr("seam.indexer.sync.index_clusters", lambda *a, **k: -1)

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        # The reconcile itself succeeded (file added)...
        assert result["added"] == 1
        assert result["graph_changed"] is True
        # ...but clustering FAILED — it must not look successfully recomputed.
        assert result["clusters_recomputed"] is False
        # cluster_count preserves the -1 sentinel so callers can detect failure
        # (distinct from None = "not run").
        assert result["cluster_count"] == -1

    def test_cluster_success_still_reports_recomputed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A successful recompute (>= 0) still reports clusters_recomputed=True."""
        conn = _make_db(tmp_path)
        (tmp_path / "new.py").write_text("def x(): pass\n")

        monkeypatch.setattr("seam.indexer.sync.index_clusters", lambda *a, **k: 3)

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["clusters_recomputed"] is True
        assert result["cluster_count"] == 3


# ── S14: Delete safety — exists() double-check ────────────────────────────────


class TestDeleteSafetyExistsCheck:
    """A tracked file that walk_project did NOT return but that still EXISTS on disk
    must NOT be deleted. This is CodeGraph's `existsSync` double-check (roadmap §6.1):
    it prevents a transient walk hiccup, a wrong-directory sync, or a --db-dir
    mismatch from silently wiping the entire index.
    """

    def test_tracked_file_still_on_disk_not_removed(self, tmp_path: Path) -> None:
        # Seed a tracked file that lives OUTSIDE the sync root but still exists.
        # walk_project(root) will not return it, yet it is a real file on disk —
        # so the reconcile must keep it, not delete it.
        outside = tmp_path.parent / f"{tmp_path.name}_outside.py"
        outside.write_text("def outside(): pass\n")
        conn = _make_db(tmp_path)
        upsert_file(conn, outside, "python", "cafe1234", [_sym("outside", str(outside))], [])

        root = tmp_path  # walk_project(root) does NOT include `outside`
        result = _sync_with_defaults(conn, root)

        # File still exists on disk → must be kept, not removed.
        row = conn.execute("SELECT name FROM symbols WHERE name = 'outside'").fetchone()
        conn.close()
        outside.unlink()

        assert result["removed"] == 0, "A tracked file still on disk must not be deleted"
        assert row is not None, "Symbol from the still-existing file must remain indexed"

    def test_genuinely_deleted_file_still_removed(self, tmp_path: Path) -> None:
        """The exists() guard must NOT block legitimate deletes: a tracked file that
        is genuinely gone from disk is still removed."""
        conn = _make_db(tmp_path)
        src = tmp_path / "gone.py"
        src.write_text("def gone(): pass\n")
        upsert_file(conn, src, "python", "abc123", [_sym("gone", str(src))], [])
        src.unlink()  # genuinely deleted

        result = _sync_with_defaults(conn, tmp_path)
        conn.close()

        assert result["removed"] == 1
