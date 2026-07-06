from __future__ import annotations

from pathlib import Path


def test_bootstrap_provenance_persists_allowlisted_artifact_fields(tmp_path: Path) -> None:
    from seam.indexer.bootstrap import read_bootstrap_provenance, write_artifact_bootstrap

    seam_dir = tmp_path / ".seam"
    seam_dir.mkdir()
    write_artifact_bootstrap(
        seam_dir,
        source="fetch",
        verified=True,
        checksum="a" * 64,
        manifest={
            "manifest_version": 1,
            "schema_version": 16,
            "producer": {"version": "0.5.0"},
            "repository": {
                "git_head": "abc123",
                "git_remote": "https://token@example.com/acme/project.git",
                "root_fingerprint": "/ci/runner/work/project",
            },
            "contents": {
                "has_source_text": False,
                "has_diagnostics": False,
                "files": ["seam.db", "manifest.json"],
            },
        },
        files_rebased=3,
        sync={"added": 1, "modified": 2, "removed": 0, "unchanged": 4},
        semantic_sync={"requested": True, "status": "failed"},
        artifact_url="https://token@example.com/private/seam-index.tar.gz",
    )

    raw = (seam_dir / "bootstrap.json").read_text(encoding="utf-8")
    assert "token" not in raw
    assert "/ci/runner" not in raw
    assert "private/seam-index" not in raw

    record = read_bootstrap_provenance(seam_dir)
    assert record is not None
    assert record["source"] == "fetch"
    assert record["verified"] is True
    assert record["checksum"] == "a" * 64
    assert record["manifest_version"] == 1
    assert record["schema_version"] == 16
    assert record["producer_version"] == "0.5.0"
    assert record["git_sha"] == "abc123"
    assert record["remote_fingerprint"]
    assert record["files_rebased"] == 3
    assert record["sync"]["ran"] is True
    assert record["sync"]["summary"]["added"] == 1
    assert record["semantic_sync"]["status"] == "failed"
    assert record["contents"]["has_source_text"] is False


def test_bootstrap_readiness_reports_verified_artifact_and_next_calls(tmp_path: Path) -> None:
    from seam.indexer.bootstrap import describe_bootstrap, write_artifact_bootstrap

    seam_dir = tmp_path / ".seam"
    seam_dir.mkdir()
    db_path = seam_dir / "seam.db"
    db_path.write_bytes(b"SQLite format 3\x00")
    write_artifact_bootstrap(
        seam_dir,
        source="import",
        verified=True,
        checksum="b" * 64,
        manifest={
            "manifest_version": 1,
            "schema_version": 16,
            "producer": {"version": "0.5.0"},
            "repository": {"git_head": "def456", "git_remote": None},
            "contents": {"has_source_text": False, "files": ["seam.db"]},
        },
        files_rebased=1,
        sync=None,
    )

    status = describe_bootstrap(
        project_root=tmp_path,
        db_path=db_path,
        freshness={"stale": False, "reason": None, "hint": None},
        semantic_readiness={"status": "disabled", "reason": "config_off"},
        artifact_url_configured=False,
    )

    assert status["artifacts_supported"] is True
    assert status["readiness"]["status"] == "verified_artifact"
    assert status["readiness"]["reason"] == "fresh"
    assert status["provenance"]["source"] == "import"
    assert status["provenance"]["verified"] is True
    assert any("seam schema" in call for call in status["recommended_next_calls"])


def test_bootstrap_readiness_reports_unknown_old_index(tmp_path: Path) -> None:
    from seam.indexer.bootstrap import describe_bootstrap

    seam_dir = tmp_path / ".seam"
    seam_dir.mkdir()
    db_path = seam_dir / "seam.db"
    db_path.write_bytes(b"SQLite format 3\x00")

    status = describe_bootstrap(
        project_root=tmp_path,
        db_path=db_path,
        freshness={"stale": False, "reason": None, "hint": None},
        semantic_readiness={"status": "disabled", "reason": "config_off"},
        artifact_url_configured=True,
    )

    assert status["readiness"]["status"] == "unknown"
    assert status["readiness"]["reason"] == "provenance_missing"
    assert "seam fetch" in " ".join(status["recommended_next_calls"])


def test_mark_local_bootstrap_replaces_artifact_origin(tmp_path: Path) -> None:
    from seam.indexer.bootstrap import (
        mark_local_bootstrap,
        read_bootstrap_provenance,
        write_artifact_bootstrap,
    )

    seam_dir = tmp_path / ".seam"
    seam_dir.mkdir()
    write_artifact_bootstrap(
        seam_dir,
        source="fetch",
        verified=False,
        checksum=None,
        manifest=None,
        files_rebased=0,
        sync=None,
    )

    mark_local_bootstrap(seam_dir)

    record = read_bootstrap_provenance(seam_dir)
    assert record is not None
    assert record["source"] == "local_init"
    assert record["verified"] is True
    assert "checksum" not in record
