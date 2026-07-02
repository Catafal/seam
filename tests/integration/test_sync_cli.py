"""Integration tests for `seam sync` CLI command.

Test groups:
    SC1 — seam sync --json returns {ok:true,data:{added,modified,...}} envelope
    SC2 — seam sync --json on dir with no index → NO_INDEX envelope, exit 1
    SC3 — seam sync --json + --quiet together → INVALID_INPUT envelope, exit 1
    SC4 — seam sync --quiet prints bare key:value lines for all SyncResult fields
    SC5 — seam sync (default) prints a Rich summary table on success
    SC6 — seam sync --json after editing a file → modified count reflected
    SC7 — seam sync --json with --force-clusters flag accepted
    SC8 — seam sync --json on a valid but empty repo → success with all zeros
    SC9 — seam sync on a non-existent directory → exits 1 with error
    SC10 — seam sync --json data keys are the exact SyncResult shape
    SC11 — cluster recompute failure (index_clusters -1) surfaced, not hidden
    SC12 — seam sync materializes static test edges after test files are added
"""

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import seam.config as seam_config
from seam.cli.main import app
from seam.indexer.db import connect, init_db
from seam.indexer.pipeline import index_one_file

runner = CliRunner()

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_indexed_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project dir with a seam.db and one indexed Python file.

    Returns (project_root, db_path).
    WHY: seam sync requires an existing index (connect, not init_db).
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = project_root / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    # Create and index a source file
    src = project_root / "module.py"
    src.write_text("def hello(): pass\n")

    conn = init_db(db_path)
    index_one_file(conn, src)
    conn.commit()
    conn.close()

    return project_root, db_path


def _make_empty_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project with an initialized DB but no files yet."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    db_path = project_root / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    conn = init_db(db_path)
    conn.commit()
    conn.close()

    return project_root, db_path


# ── SC1: --json success envelope ──────────────────────────────────────────────


class TestJsonEnvelope:
    """seam sync --json returns {ok:true,data:...} on success."""

    def test_json_ok_true_on_success(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        assert result.exit_code == 0, f"Expected exit 0, got: {result.output}"

        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert "data" in envelope

    def test_json_data_has_sync_result_keys(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        envelope = json.loads(result.output)
        data = envelope["data"]

        expected_keys = {
            "added", "modified", "removed", "unchanged", "skipped",
            "graph_changed", "clusters_recomputed", "cluster_count",
        }
        assert expected_keys <= set(data.keys()), (
            f"Missing keys: {expected_keys - set(data.keys())}"
        )


# ── SC2: NO_INDEX on missing DB ───────────────────────────────────────────────


class TestNoIndex:
    """seam sync on a directory with no .seam/seam.db → NO_INDEX, exit 1."""

    def test_no_index_json_error_envelope(self, tmp_path: Path) -> None:
        no_db_dir = tmp_path / "no_index"
        no_db_dir.mkdir()

        result = runner.invoke(app, ["sync", str(no_db_dir), "--json"])
        assert result.exit_code == 1

        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "NO_INDEX"

    def test_no_index_exits_1_without_json(self, tmp_path: Path) -> None:
        no_db_dir = tmp_path / "no_index"
        no_db_dir.mkdir()

        result = runner.invoke(app, ["sync", str(no_db_dir)])
        assert result.exit_code == 1


# ── SC3: --json + --quiet mutual exclusion ────────────────────────────────────


class TestMutualExclusion:
    """--json and --quiet together → INVALID_INPUT, exit 1."""

    def test_json_and_quiet_together_is_error(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(
            app, ["sync", str(project_root), "--json", "--quiet"]
        )
        assert result.exit_code == 1

        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "INVALID_INPUT"


# ── SC4: --quiet output ────────────────────────────────────────────────────────


class TestQuietOutput:
    """seam sync --quiet prints bare key:value lines."""

    def test_quiet_output_is_not_json(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--quiet"])
        assert result.exit_code == 0
        # Should NOT be a JSON envelope
        assert not result.output.startswith("{")

    def test_quiet_output_contains_counts(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--quiet"])
        assert result.exit_code == 0

        # Quiet mode should contain the key fields
        output = result.output
        # The output should reference the sync result fields
        assert "added" in output
        assert "modified" in output
        assert "removed" in output
        assert "unchanged" in output


# ── SC5: Default (rich) mode ──────────────────────────────────────────────────


class TestDefaultMode:
    """seam sync (no flags) renders a Rich summary table."""

    def test_default_mode_not_json(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root)])
        assert result.exit_code == 0
        # Should not be a JSON envelope
        assert not result.output.startswith("{")

    def test_default_mode_exit_zero(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root)])
        assert result.exit_code == 0


# ── SC6: --json after editing a file ──────────────────────────────────────────


class TestJsonAfterEdit:
    """seam sync --json after editing a file reflects modified count."""

    def test_modified_file_reflected_in_json(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        # Edit the file to change its content
        src = project_root / "module.py"
        time.sleep(0.05)  # ensure mtime changes
        src.write_text("def goodbye(): pass\n")

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        assert result.exit_code == 0

        envelope = json.loads(result.output)
        data = envelope["data"]
        assert data["modified"] >= 1, "Modified count should reflect the edit"

    def test_sync_updates_db_after_edit(self, tmp_path: Path) -> None:
        """After sync, the edited file's new symbols appear in the DB."""
        project_root, _ = _make_indexed_project(tmp_path)

        src = project_root / "module.py"
        time.sleep(0.05)
        src.write_text("def goodbye(): pass\n")

        runner.invoke(app, ["sync", str(project_root), "--json"])

        db_path = seam_config.get_db_path(project_root)
        conn = connect(db_path)
        row = conn.execute(
            "SELECT name FROM symbols WHERE name = 'goodbye'"
        ).fetchone()
        conn.close()

        assert row is not None, "New symbol 'goodbye' should be in DB after sync"

    def test_sync_materializes_static_test_edges(self, tmp_path: Path) -> None:
        """Adding a test file makes sync rebuild the derived test-edge surface."""
        project_root, db_path = _make_indexed_project(tmp_path)
        test_file = project_root / "tests" / "test_module.py"
        test_file.parent.mkdir()
        test_file.write_text(
            "from module import hello\n"
            "\n"
            "def test_hello():\n"
            "    hello()\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]

        assert data["test_edges_recomputed"] is True
        assert data["test_edge_count"] == 1

        conn = connect(db_path)
        row = conn.execute(
            """
            SELECT source_name, target_name, kind, synthesized_by
            FROM edges
            WHERE kind = 'tests'
            """
        ).fetchone()
        conn.close()
        assert dict(row) == {
            "source_name": "test_hello",
            "target_name": "hello",
            "kind": "tests",
            "synthesized_by": "test-call",
        }


# ── SC7: --force-clusters flag ────────────────────────────────────────────────


class TestForceClusters:
    """seam sync --force-clusters is accepted and recomputes clusters."""

    def test_force_clusters_flag_accepted(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(
            app, ["sync", str(project_root), "--force-clusters", "--json"]
        )
        assert result.exit_code == 0

        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        data = envelope["data"]
        # With --force-clusters, clusters_recomputed must be True
        assert data["clusters_recomputed"] is True


# ── SC8: Empty project (no source files) ──────────────────────────────────────


class TestEmptyProject:
    """seam sync on a project with no source files → success, all zeros."""

    def test_empty_project_success(self, tmp_path: Path) -> None:
        project_root, _ = _make_empty_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        assert result.exit_code == 0

        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        data = envelope["data"]
        assert data["added"] == 0
        assert data["modified"] == 0
        assert data["removed"] == 0


# ── SC9: Non-existent directory ───────────────────────────────────────────────


class TestNonExistentDir:
    """seam sync on a non-existent path → exits 1."""

    def test_nonexistent_path_exits_1(self, tmp_path: Path) -> None:
        ghost_dir = tmp_path / "does_not_exist"

        result = runner.invoke(app, ["sync", str(ghost_dir)])
        assert result.exit_code == 1


# ── SC10: SyncResult data key types ───────────────────────────────────────────


class TestSyncResultDataTypes:
    """seam sync --json data values have correct types."""

    def test_count_fields_are_ints(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        data = json.loads(result.output)["data"]

        assert isinstance(data["added"], int)
        assert isinstance(data["modified"], int)
        assert isinstance(data["removed"], int)
        assert isinstance(data["unchanged"], int)
        assert isinstance(data["skipped"], int)

    def test_bool_fields_are_bools(self, tmp_path: Path) -> None:
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        data = json.loads(result.output)["data"]

        assert isinstance(data["graph_changed"], bool)
        assert isinstance(data["clusters_recomputed"], bool)

    def test_cluster_count_is_none_when_no_changes(self, tmp_path: Path) -> None:
        """When no changes, cluster_count is None (clusters not recomputed)."""
        project_root, _ = _make_indexed_project(tmp_path)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        data = json.loads(result.output)["data"]

        # No changes to the already-indexed project → cluster_count is None
        assert data["cluster_count"] is None


# ── SC11: Cluster recompute failure is surfaced, not hidden ───────────────────


class TestClusterFailureSurfaced:
    """When the gated cluster recompute fails (index_clusters returns its -1
    sentinel), the CLI must not present a healthy-looking result: --json carries
    cluster_count=-1 + clusters_recomputed=false, and the default table shows
    'failed' with a visible warning (mirrors `seam init`)."""

    def test_json_surfaces_cluster_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_root, _ = _make_indexed_project(tmp_path)
        # Add a file so the gate opens, then force the cluster pass to fail.
        (project_root / "extra.py").write_text("def extra(): pass\n")
        monkeypatch.setattr("seam.indexer.sync.index_clusters", lambda *a, **k: -1)

        result = runner.invoke(app, ["sync", str(project_root), "--json"])
        # Reconcile succeeded → envelope is still ok:true, exit 0...
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        # ...but the cluster failure is visible in the data, not hidden.
        assert data["cluster_count"] == -1
        assert data["clusters_recomputed"] is False

    def test_table_shows_failed_and_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_root, _ = _make_indexed_project(tmp_path)
        (project_root / "extra.py").write_text("def extra(): pass\n")
        monkeypatch.setattr("seam.indexer.sync.index_clusters", lambda *a, **k: -1)

        result = runner.invoke(app, ["sync", str(project_root)])
        assert result.exit_code == 0
        assert "failed" in result.output
        # Warning text guides the operator to rebuild.
        assert "seam init" in result.output
