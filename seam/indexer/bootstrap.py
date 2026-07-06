"""Safe bootstrap provenance for shared Seam index artifacts.

This module owns the small trust record that explains how the current `.seam/`
index landed locally. It deliberately stays outside the graph database: artifact
provenance is bootstrap evidence, not code dependency evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BOOTSTRAP_FILENAME = "bootstrap.json"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _path(seam_dir: Path) -> Path:
    return seam_dir / BOOTSTRAP_FILENAME


def _remote_fingerprint(remote: Any) -> str | None:
    if not isinstance(remote, str) or not remote.strip():
        return None
    normalized = remote.removesuffix(".git").rstrip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _safe_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _manifest_summary(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    repository = manifest.get("repository")
    if not isinstance(repository, dict):
        repository = {}
    producer = manifest.get("producer")
    if not isinstance(producer, dict):
        producer = {}
    contents = manifest.get("contents")
    if not isinstance(contents, dict):
        contents = {}

    summary: dict[str, Any] = {}
    for key in ("manifest_version", "schema_version"):
        value = _safe_int(manifest.get(key))
        if value is not None:
            summary[key] = value
    if isinstance(producer.get("version"), str):
        summary["producer_version"] = producer["version"]
    if isinstance(repository.get("git_head"), str):
        summary["git_sha"] = repository["git_head"]
    fingerprint = _remote_fingerprint(repository.get("git_remote"))
    if fingerprint is not None:
        summary["remote_fingerprint"] = fingerprint

    content_summary: dict[str, Any] = {}
    for key in ("has_source_text", "has_diagnostics", "has_vectors"):
        value = _safe_bool(contents.get(key))
        if value is not None:
            content_summary[key] = value
    files = contents.get("files")
    if isinstance(files, list):
        content_summary["files"] = [str(file) for file in files if isinstance(file, str)]
    if content_summary:
        summary["contents"] = content_summary
    return summary


def _sync_summary(sync: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(sync, dict):
        return {"ran": False, "summary": None}
    allowed = {
        key: sync[key]
        for key in (
            "added",
            "modified",
            "removed",
            "unchanged",
            "skipped",
            "graph_changed",
        )
        if key in sync and isinstance(sync[key], (bool, int))
    }
    return {"ran": True, "summary": allowed}


def _write(seam_dir: Path, record: dict[str, Any]) -> None:
    seam_dir.mkdir(parents=True, exist_ok=True)
    _path(seam_dir).write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def write_artifact_bootstrap(
    seam_dir: Path,
    *,
    source: str,
    verified: bool,
    checksum: str | None,
    manifest: dict[str, Any] | None,
    files_rebased: int,
    sync: dict[str, Any] | None,
    semantic_sync: dict[str, Any] | None = None,
    artifact_url: str | None = None,
) -> dict[str, Any]:
    """Persist the safe subset of artifact landing metadata.

    `artifact_url` is accepted only to make accidental call-site leakage testable;
    it is intentionally never copied into the record.
    """
    _ = artifact_url
    record: dict[str, Any] = {
        "source": source,
        "landed_at": _now(),
        "verified": bool(verified),
        "files_rebased": int(files_rebased),
        "sync": _sync_summary(sync),
    }
    if isinstance(checksum, str) and checksum:
        record["checksum"] = checksum
    record.update(_manifest_summary(manifest))
    if isinstance(semantic_sync, dict):
        record["semantic_sync"] = {
            key: value
            for key, value in semantic_sync.items()
            if key in {"requested", "status"} and isinstance(value, (bool, str))
        }
    _write(seam_dir, record)
    return record


def mark_local_bootstrap(seam_dir: Path) -> dict[str, Any]:
    record = {
        "source": "local_init",
        "landed_at": _now(),
        "verified": True,
        "sync": {"ran": False, "summary": None},
    }
    _write(seam_dir, record)
    return record


def read_bootstrap_provenance(seam_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(_path(seam_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def describe_bootstrap(
    *,
    project_root: Path,
    db_path: Path,
    freshness: dict[str, Any],
    semantic_readiness: Mapping[str, Any],
    artifact_url_configured: bool,
) -> dict[str, Any]:
    """Return the read-only bootstrap status agents can branch on."""
    _ = project_root
    record = read_bootstrap_provenance(db_path.parent)
    if not db_path.exists():
        readiness = {
            "status": "blocked",
            "reason": "no_index",
            "message": "No Seam index exists for this checkout.",
        }
        next_calls = ["Run seam fetch --strict if a trusted artifact is configured.", "Run seam init."]
    elif freshness.get("stale"):
        readiness = {
            "status": "stale",
            "reason": "index_stale",
            "message": freshness.get("reason") or "The current index may be stale.",
        }
        next_calls = ["Run seam sync to reconcile local changes.", "Run seam schema --json again."]
    elif record is None:
        readiness = {
            "status": "unknown",
            "reason": "provenance_missing",
            "message": "This index predates bootstrap provenance or was copied manually.",
        }
        next_calls = ["Run seam fetch --strict to land a trusted artifact." if artifact_url_configured else "Run seam init to rebuild with local provenance.", "Run seam schema --json again."]
    else:
        source = str(record.get("source") or "unknown")
        verified = bool(record.get("verified"))
        if source in {"fetch", "import"}:
            status = "verified_artifact" if verified else "unverified_artifact"
        elif source == "local_init":
            status = "local_index"
        else:
            status = "unknown"
        readiness = {
            "status": status,
            "reason": "fresh",
            "message": "Bootstrap provenance is available for this index.",
        }
        next_calls = ["Run seam schema --json to inspect index capabilities.", "Use seam query/search/context for read-only code intelligence."]

    semantic_reason = semantic_readiness.get("reason")
    if semantic_reason in {"no_embeddings", "model_mismatch"}:
        next_calls.append("Run seam sync --semantic or seam init --semantic for semantic discovery.")

    return {
        "artifacts_supported": True,
        "readiness": readiness,
        "provenance": record,
        "fetch": {
            "artifact_url_configured": artifact_url_configured,
            "strict_recommended": True,
        },
        "recommended_next_calls": next_calls,
    }
