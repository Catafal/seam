"""Integration tests for local graph artifact export, inspect, and import."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import seam.cli.artifacts as artifact_lifecycle
from seam.cli.artifacts import ArtifactLifecycleError, import_index_artifact
from seam.cli.main import app
from seam.indexer.artifact import CHECKSUM_FILENAME
from seam.indexer.db import connect
from seam.indexer.init_index import run_init
from seam.query.engine import query


def _make_indexed_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "module.py").write_text("def greet():\n    return 'hello'\n")
    run_init(root)


def _git_commit_project(root: Path, *, remote: str | None = None) -> None:
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "add", "module.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    if remote is not None:
        subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", remote],
            check=True,
            capture_output=True,
        )


def test_export_and_inspect_json_include_manifest_without_mutating_index(tmp_path: Path) -> None:
    """The artifact preflight path exposes manifest metadata without touching .seam/."""
    project = tmp_path / "project"
    _make_indexed_project(project)
    out = tmp_path / "artifacts"
    runner = CliRunner()

    exported = runner.invoke(app, ["pack-index", str(project), "--dest", str(out), "--json"])

    assert exported.exit_code == 0, exported.output
    export_data = json.loads(exported.output)["data"]
    assert export_data["manifest"]["manifest_version"] == 1
    assert export_data["manifest"]["schema_version"] == 15
    assert export_data["manifest"]["contents"]["has_source_text"] is False
    before = sorted(p.relative_to(project / ".seam") for p in (project / ".seam").rglob("*"))

    inspected = runner.invoke(app, ["inspect-index", export_data["archive"], "--json"])

    assert inspected.exit_code == 0, inspected.output
    inspect_data = json.loads(inspected.output)["data"]
    assert inspect_data["checksum"] == export_data["checksum"]
    assert inspect_data["manifest"]["schema_version"] == 15
    after = sorted(p.relative_to(project / ".seam") for p in (project / ".seam").rglob("*"))
    assert after == before


def test_import_index_lands_queryable_index(tmp_path: Path) -> None:
    """A local artifact can restore .seam/ into the same checkout without network config."""
    project = tmp_path / "project"
    _make_indexed_project(project)
    out = tmp_path / "artifacts"
    runner = CliRunner()
    exported = runner.invoke(app, ["export-index", str(project), "--dest", str(out), "--json"])
    assert exported.exit_code == 0, exported.output
    export_data = json.loads(exported.output)["data"]
    shutil.rmtree(project / ".seam")

    imported = runner.invoke(
        app, ["import-index", export_data["archive"], "--path", str(project), "--json"]
    )

    assert imported.exit_code == 0, imported.output
    import_data = json.loads(imported.output)["data"]
    assert import_data["repo_match"] is True
    assert (project / ".seam" / "seam.db").is_file()
    conn = connect(project / ".seam" / "seam.db")
    try:
        results = query(conn, "greet")
    finally:
        conn.close()
    assert any(row["symbol"] == "greet" for row in results)


def test_import_index_accepts_same_git_repo_from_different_checkout_path(
    tmp_path: Path,
) -> None:
    """Repo compatibility follows git identity before falling back to local path."""
    remote = "https://example.com/acme/seam-fixture.git"
    source = tmp_path / "source"
    target = tmp_path / "target"
    _make_indexed_project(source)
    _git_commit_project(source, remote=remote)
    subprocess.run(["git", "clone", str(source), str(target)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(target), "remote", "set-url", "origin", remote],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "artifacts"
    runner = CliRunner()
    exported = runner.invoke(app, ["export-index", str(source), "--dest", str(out), "--json"])
    assert exported.exit_code == 0, exported.output
    export_data = json.loads(exported.output)["data"]

    imported = runner.invoke(
        app, ["import-index", export_data["archive"], "--path", str(target), "--json"]
    )

    assert imported.exit_code == 0, imported.output
    import_data = json.loads(imported.output)["data"]
    assert import_data["repo_match"] is True
    assert (target / ".seam" / "seam.db").is_file()


def test_import_index_checksum_mismatch_preserves_existing_index(tmp_path: Path) -> None:
    """Failed imports must not damage a working local index."""
    project = tmp_path / "project"
    _make_indexed_project(project)
    out = tmp_path / "artifacts"
    runner = CliRunner()
    exported = runner.invoke(app, ["pack-index", str(project), "--dest", str(out), "--json"])
    assert exported.exit_code == 0, exported.output
    export_data = json.loads(exported.output)["data"]
    sentinel = project / ".seam" / "SENTINEL.txt"
    sentinel.write_text("must survive")
    checksum_path = Path(export_data["checksum_file"])
    checksum_path.write_text("0" * 64 + "  seam-index.tar.gz\n")

    imported = runner.invoke(
        app, ["import-index", export_data["archive"], "--path", str(project), "--json"]
    )

    assert imported.exit_code != 0
    error = json.loads(imported.output)["error"]
    assert error["code"] in {"ARTIFACT_INVALID", "CHECKSUM_MISMATCH"}
    assert sentinel.read_text() == "must survive"


def test_import_index_swap_failure_without_existing_index_leaves_no_partial_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A landing failure cannot leave a partial .seam/ when no prior index existed."""
    project = tmp_path / "project"
    _make_indexed_project(project)
    out = tmp_path / "artifacts"
    runner = CliRunner()
    exported = runner.invoke(app, ["export-index", str(project), "--dest", str(out), "--json"])
    assert exported.exit_code == 0, exported.output
    export_data = json.loads(exported.output)["data"]
    shutil.rmtree(project / ".seam")

    def failing_copytree(src: str, dst: str) -> None:
        partial = Path(dst)
        partial.mkdir(parents=True)
        (partial / "partial").write_text("incomplete")
        raise OSError("copy failed")

    monkeypatch.setattr(artifact_lifecycle.shutil, "copytree", failing_copytree)

    with pytest.raises(ArtifactLifecycleError):
        import_index_artifact(project, Path(export_data["archive"]))

    assert not (project / ".seam").exists()
    assert not (project / ".seam.import.tmp").exists()


def test_import_index_refuses_repo_mismatch_by_default(tmp_path: Path) -> None:
    """Root fingerprints are a guardrail against accidentally landing the wrong graph."""
    source = tmp_path / "source"
    target = tmp_path / "target"
    _make_indexed_project(source)
    target.mkdir()
    out = tmp_path / "artifacts"
    runner = CliRunner()
    exported = runner.invoke(app, ["pack-index", str(source), "--dest", str(out), "--json"])
    assert exported.exit_code == 0, exported.output
    export_data = json.loads(exported.output)["data"]

    imported = runner.invoke(
        app, ["import-index", export_data["archive"], "--path", str(target), "--json"]
    )

    assert imported.exit_code != 0
    assert json.loads(imported.output)["error"]["code"] == "REPO_MISMATCH"
    assert not (target / ".seam").exists()


def test_inspect_index_refuses_newer_schema_manifest(tmp_path: Path) -> None:
    """Newer artifacts fail before import because local Seam cannot interpret them."""
    archive = tmp_path / "seam-index.tar.gz"
    manifest = {
        "manifest_version": 1,
        "artifact_format": "seam-index",
        "schema_version": 999,
        "producer": {"name": "seam-code", "version": "future"},
        "repository": {"root_fingerprint": "x", "git_head": None, "git_remote": None},
        "contents": {"files": ["seam.db"], "has_source_text": False},
    }
    with tarfile.open(archive, "w:gz") as tf:
        for name, data in {
            "seam.db": b"SQLite format 3\x00",
            "manifest.json": json.dumps(manifest).encode("utf-8"),
        }.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / CHECKSUM_FILENAME).write_text(f"{digest}  seam-index.tar.gz\n")

    result = CliRunner().invoke(app, ["inspect-index", str(archive), "--json"])

    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "SCHEMA_INCOMPATIBLE"
