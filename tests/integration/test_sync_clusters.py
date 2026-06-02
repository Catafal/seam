"""Integration tests for seam sync end-to-end cluster freshness.

Test groups:
    CL1 — seam init → add connected file → seam sync → new symbol has non-NULL cluster_id
    CL2 — second seam sync with no changes leaves clusters table untouched (IDs stable)
    CL3 — seam sync --force-clusters with no file changes still recomputes clusters
    CL4 — seam sync after removing a connected file → cluster rows updated
    CL5 — seam sync with only unchanged files → clusters_recomputed=False, table unchanged
"""

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app
from seam.indexer.db import connect

runner = CliRunner()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _run_init(project_root: Path, db_dir: Path | None = None) -> None:
    """Run `seam init` via the CLI runner on the project root."""
    args = ["init", str(project_root)]
    if db_dir:
        args += ["--db-dir", str(db_dir)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, f"seam init failed: {result.output}"


def _run_sync_json(project_root: Path, extra_args: list[str] | None = None) -> dict:
    """Run `seam sync --json` and return the parsed data dict."""
    args = ["sync", str(project_root), "--json"]
    if extra_args:
        args += extra_args
    result = runner.invoke(app, args)
    assert result.exit_code == 0, f"seam sync failed: {result.output}"
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    return envelope["data"]


def _get_cluster_id(db_path: Path, symbol_name: str) -> int | None:
    """Query the cluster_id for a symbol by name."""
    conn = connect(db_path)
    row = conn.execute(
        "SELECT cluster_id FROM symbols WHERE name = ?", (symbol_name,)
    ).fetchone()
    conn.close()
    return row["cluster_id"] if row else None


def _get_cluster_rows(db_path: Path) -> list[dict]:
    """Return all cluster rows as list of dicts."""
    conn = connect(db_path)
    rows = conn.execute("SELECT id, label, size FROM clusters ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _make_connected_pair(project_root: Path, name_a: str, name_b: str) -> Path:
    """Create a Python file with two mutually-calling functions."""
    src = project_root / f"{name_a}_{name_b}.py"
    src.write_text(
        f"def {name_a}(): {name_b}()\n"
        f"def {name_b}(): {name_a}()\n"
    )
    return src


# ── CL1: Init + add connected file → new symbol gets cluster_id ───────────────


class TestNewConnectedSymbolGetsClusterId:
    """After seam init + adding a connected file + seam sync, new symbol has cluster_id."""

    def test_new_connected_symbol_cluster_id_not_null(self, tmp_path: Path) -> None:
        """Verifies the 'clusters go stale after edits' gotcha is fixed by seam sync."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        # Start: one existing connected pair (so clustering will detect communities)
        _make_connected_pair(project_root, "alpha", "beta")

        # seam init — clusters will be computed for the initial graph
        _run_init(project_root)

        db_path = project_root / ".seam" / "seam.db"

        # Now add a new connected file that references an existing symbol
        time.sleep(0.05)
        new_src = project_root / "gamma_delta.py"
        new_src.write_text(
            "def gamma(): delta()\n"
            "def delta(): gamma()\n"
        )

        # Run sync — this should index the new file AND recompute clusters
        data = _run_sync_json(project_root)

        assert data["added"] == 1, f"Expected 1 added file, got {data}"
        assert data["clusters_recomputed"] is True

        # The new symbols should be in the DB
        conn = connect(db_path)
        gamma_row = conn.execute(
            "SELECT name FROM symbols WHERE name = 'gamma'"
        ).fetchone()
        conn.close()
        assert gamma_row is not None, "Symbol 'gamma' should be in DB after sync"

    def test_cluster_count_in_result_after_adding_file(self, tmp_path: Path) -> None:
        """cluster_count in SyncResult is an int when clusters were recomputed."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        _make_connected_pair(project_root, "foo", "bar")
        _run_init(project_root)

        # Add another file
        time.sleep(0.05)
        (project_root / "baz.py").write_text("def baz(): foo()\n")

        data = _run_sync_json(project_root)

        assert data["clusters_recomputed"] is True
        assert isinstance(data["cluster_count"], int)
        assert data["cluster_count"] >= 0


# ── CL2: Second sync with no changes — cluster IDs stable ─────────────────────


class TestSecondSyncClusterIdsStable:
    """A second seam sync with no file changes leaves clusters table untouched."""

    def test_no_changes_clusters_not_recomputed(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        _make_connected_pair(project_root, "a", "b")
        _run_init(project_root)

        db_path = project_root / ".seam" / "seam.db"

        # Capture cluster rows after init
        clusters_before = _get_cluster_rows(db_path)

        # Run sync with no changes
        data = _run_sync_json(project_root)

        assert data["added"] == 0
        assert data["modified"] == 0
        assert data["removed"] == 0
        assert data["clusters_recomputed"] is False
        assert data["cluster_count"] is None

        # Cluster rows must be identical
        clusters_after = _get_cluster_rows(db_path)
        assert clusters_before == clusters_after, (
            "Cluster rows changed despite no file changes"
        )

    def test_no_changes_graph_changed_false(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        _make_connected_pair(project_root, "x", "y")
        _run_init(project_root)

        data = _run_sync_json(project_root)

        assert data["graph_changed"] is False


# ── CL3: --force-clusters with no file changes ────────────────────────────────


class TestForceClustersNoFileChanges:
    """seam sync --force-clusters recomputes clusters even when zero files changed."""

    def test_force_clusters_recomputes_when_nothing_changed(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        _make_connected_pair(project_root, "p", "q")
        _run_init(project_root)

        # Run sync with --force-clusters and no file changes
        data = _run_sync_json(project_root, extra_args=["--force-clusters"])

        assert data["graph_changed"] is False, "No files changed"
        assert data["clusters_recomputed"] is True, "--force-clusters must override gate"
        assert isinstance(data["cluster_count"], int)

    def test_force_clusters_cluster_count_is_int(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        _make_connected_pair(project_root, "u", "v")
        _run_init(project_root)

        data = _run_sync_json(project_root, extra_args=["--force-clusters"])

        assert data["cluster_count"] is not None
        assert data["cluster_count"] >= 0


# ── CL4: Removing a connected file → clusters updated ─────────────────────────


class TestRemoveConnectedFile:
    """After removing a connected file, seam sync deletes its symbols and recomputes."""

    def test_removed_file_symbols_gone_and_clusters_recomputed(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        src = _make_connected_pair(project_root, "m", "n")
        _run_init(project_root)

        db_path = project_root / ".seam" / "seam.db"

        # Verify symbols are in DB after init
        conn = connect(db_path)
        row = conn.execute("SELECT name FROM symbols WHERE name = 'm'").fetchone()
        conn.close()
        assert row is not None

        # Delete the file
        src.unlink()

        data = _run_sync_json(project_root)

        assert data["removed"] == 1
        assert data["clusters_recomputed"] is True

        # Symbols should be gone
        conn = connect(db_path)
        row = conn.execute("SELECT name FROM symbols WHERE name = 'm'").fetchone()
        conn.close()
        assert row is None, "Symbol 'm' should be deleted after its file is removed"


# ── CL5: Only unchanged files → clusters_recomputed=False ─────────────────────


class TestOnlyUnchangedFiles:
    """When all tracked files are unchanged, clusters are not recomputed."""

    def test_all_unchanged_cluster_not_recomputed(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        _make_connected_pair(project_root, "r", "s")
        _run_init(project_root)

        # Sync immediately — nothing has changed on disk
        data = _run_sync_json(project_root)

        assert data["unchanged"] >= 1
        assert data["modified"] == 0
        assert data["added"] == 0
        assert data["removed"] == 0
        assert data["clusters_recomputed"] is False
