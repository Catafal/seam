"""Local graph artifact lifecycle commands.

This module owns write-path orchestration for local artifact import so the CLI can
stay thin and the read-only MCP/query surfaces never learn mutation behavior.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import seam.config as config
from seam.analysis.staleness import check_staleness
from seam.indexer.artifact import (
    CHECKSUM_FILENAME,
    compute_root_fingerprint,
    inspect_artifact,
    read_repository_identity,
    unpack_index,
)
from seam.indexer.db import connect
from seam.indexer.rebase import rebase_index
from seam.indexer.sync import sync as sync_project

CURRENT_SCHEMA_VERSION = 16


class ArtifactLifecycleError(Exception):
    """User-actionable artifact import/inspect failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def inspect_index_artifact(
    archive_path: Path,
    *,
    checksum_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect an artifact with the checksum sidecar required by local lifecycle commands."""
    checksum = checksum_path or archive_path.parent / CHECKSUM_FILENAME
    if not checksum.is_file():
        raise ArtifactLifecycleError(
            "CHECKSUM_MISSING",
            f"Checksum sidecar not found: {checksum}",
        )
    inspected = inspect_artifact(archive_path, checksum_path=checksum)
    if inspected is None:
        raise ArtifactLifecycleError(
            "ARTIFACT_INVALID",
            "Artifact inspection failed. The archive, checksum, or manifest is invalid.",
        )
    _validate_manifest(inspected["manifest"])
    return inspected


def import_index_artifact(
    project_root: Path,
    archive_path: Path,
    *,
    checksum_path: Path | None = None,
    db_root: Path | None = None,
    allow_repo_mismatch: bool = False,
    rebase: bool = True,
    sync: bool = False,
) -> dict[str, Any]:
    """Validate, stage, optionally rebase, and atomically land a local artifact."""
    project_root = project_root.resolve()
    db_base = db_root.resolve() if db_root is not None else project_root
    inspected = inspect_index_artifact(archive_path, checksum_path=checksum_path)
    manifest = inspected["manifest"]
    repo_match = _repo_matches(manifest, project_root)
    if not repo_match and not allow_repo_mismatch:
        raise ArtifactLifecycleError(
            "REPO_MISMATCH",
            "Artifact repository fingerprint does not match this checkout. "
            "Pass --allow-repo-mismatch only when you intentionally trust this artifact.",
        )

    with tempfile.TemporaryDirectory(prefix="seam-import-") as tmp:
        stage_dir = Path(tmp) / "staged"
        checksum = Path(inspected["checksum_file"])
        if not unpack_index(archive_path, dest_dir=stage_dir, checksum_path=checksum):
            raise ArtifactLifecycleError(
                "ARTIFACT_INVALID",
                "Artifact extraction failed after validation.",
            )

        stage_db = stage_dir / "seam.db"
        _validate_staged_db(stage_db)
        files_rebased = 0
        if rebase:
            conn = connect(stage_db)
            try:
                files_rebased = rebase_index(conn, new_root=str(project_root))
            finally:
                conn.close()

        _swap_staged_index(stage_dir, db_base)

    db_path = config.get_db_path(db_base)
    sync_result = None
    if sync:
        conn = connect(db_path)
        try:
            sync_result = sync_project(
                conn,
                project_root,
                recompute_clusters=True,
                force_clusters=False,
                naming_mode=config.SEAM_CLUSTER_NAMING,
                llm_api_key=config.SEAM_LLM_API_KEY,
                llm_model=config.SEAM_LLM_MODEL,
                min_size=config.SEAM_CLUSTER_MIN_SIZE,
                synthesis_enabled=config.SEAM_EDGE_SYNTHESIS == "on",
                force_synthesis=False,
                fanout_cap=config.SEAM_SYNTHESIS_FANOUT_CAP,
            )
        finally:
            conn.close()

    conn = connect(db_path)
    try:
        freshness = check_staleness(conn, root=project_root, respect_knob=False)
    finally:
        conn.close()

    return {
        "archive": inspected["archive"],
        "checksum": inspected["checksum"],
        "checksum_file": inspected["checksum_file"],
        "manifest": manifest,
        "repo_match": repo_match,
        "files_rebased": files_rebased,
        "sync": dict(sync_result) if sync_result is not None else None,
        "freshness": {
            "stale": bool(freshness["stale"]),
            "reason": freshness["reason"] or None,
            "hint": freshness["hint"] or None,
        },
    }


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("manifest_version") != 1:
        raise ArtifactLifecycleError(
            "MANIFEST_UNSUPPORTED",
            "Unsupported artifact manifest version.",
        )
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, int):
        raise ArtifactLifecycleError(
            "SCHEMA_INCOMPATIBLE",
            "Artifact manifest is missing a valid schema_version.",
        )
    if schema_version > CURRENT_SCHEMA_VERSION:
        raise ArtifactLifecycleError(
            "SCHEMA_INCOMPATIBLE",
            "Artifact was produced by a newer Seam schema. Upgrade Seam before importing.",
        )


def _repo_matches(manifest: dict[str, Any], project_root: Path) -> bool:
    repository = manifest.get("repository", {})
    expected_remote = repository.get("git_remote")
    expected_head = repository.get("git_head")
    actual = read_repository_identity(project_root.resolve())
    actual_remote = actual.get("remote")
    actual_head = actual.get("head")

    if isinstance(expected_remote, str) and isinstance(actual_remote, str):
        if _normalize_git_remote(expected_remote) != _normalize_git_remote(actual_remote):
            return False
        if isinstance(expected_head, str) and isinstance(actual_head, str):
            return expected_head == actual_head
        return True

    if isinstance(expected_head, str) and isinstance(actual_head, str):
        return expected_head == actual_head

    expected_fingerprint = repository.get("root_fingerprint")
    if not isinstance(expected_fingerprint, str):
        return False
    return expected_fingerprint == compute_root_fingerprint(project_root.resolve())


def _normalize_git_remote(remote: str) -> str:
    return remote.removesuffix(".git").rstrip("/")


def _validate_staged_db(db_path: Path) -> None:
    if not db_path.is_file():
        raise ArtifactLifecycleError("DB_ERROR", "Artifact did not contain seam.db.")
    try:
        conn = connect(db_path)
        conn.close()
    except Exception as exc:  # noqa: BLE001
        raise ArtifactLifecycleError(
            "DB_ERROR",
            f"Imported seam.db could not be opened: {exc}",
        ) from exc


def _swap_staged_index(stage_dir: Path, db_base: Path) -> None:
    seam_dir = db_base / ".seam"
    backup_dir = db_base / ".seam.import.bak"
    landing_tmp = db_base / ".seam.import.tmp"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    if landing_tmp.exists():
        shutil.rmtree(landing_tmp, ignore_errors=True)

    had_existing = seam_dir.exists()
    try:
        shutil.copytree(str(stage_dir), str(landing_tmp))
        if had_existing:
            seam_dir.rename(backup_dir)
        landing_tmp.rename(seam_dir)
    except Exception as exc:
        if landing_tmp.exists():
            shutil.rmtree(landing_tmp, ignore_errors=True)
        if not had_existing and seam_dir.exists():
            shutil.rmtree(seam_dir, ignore_errors=True)
        if had_existing and backup_dir.exists():
            if seam_dir.exists():
                shutil.rmtree(seam_dir, ignore_errors=True)
            backup_dir.rename(seam_dir)
        raise ArtifactLifecycleError(
            "SWAP_FAILED",
            f"Failed to land imported index. The original index was preserved: {exc}",
        ) from exc

    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
