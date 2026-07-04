"""Workspace registry for explicit cross-repo trust sets.

The registry is intentionally a small JSON sidecar instead of a SQLite migration.
Cross-repo membership is user-selected metadata, not a fact extracted from one repo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import seam.config as config
from seam.indexer.artifact import compute_root_fingerprint, read_repository_identity

REGISTRY_VERSION = 1
REGISTRY_FILENAME = "workspace.json"
ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")


class WorkspaceError(Exception):
    """Raised when workspace registry input is invalid or unsafe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RegisteredRepo:
    alias: str
    root: Path
    index_path: Path
    git_remote: str | None
    git_head: str | None
    root_fingerprint: str
    added_at: str

    def to_public_dict(self, *, include_absolute_path: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "alias": self.alias,
            "index": _display_path(self.index_path, self.root),
            "identity": {
                "git_remote": self.git_remote,
                "git_head": self.git_head,
                "root_fingerprint": self.root_fingerprint,
            },
            "added_at": self.added_at,
        }
        if include_absolute_path:
            result["root"] = str(self.root)
            result["index_path"] = str(self.index_path)
        return result

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "root": str(self.root),
            "index_path": str(self.index_path),
            "git_remote": self.git_remote,
            "git_head": self.git_head,
            "root_fingerprint": self.root_fingerprint,
            "added_at": self.added_at,
        }


def registry_path(workspace_root: Path) -> Path:
    return workspace_root.resolve() / ".seam" / REGISTRY_FILENAME


def create_workspace(workspace_root: Path) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    path = registry_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _write_registry(path, {"version": REGISTRY_VERSION, "repos": []})
    return workspace_payload(workspace_root, repos=[])


def add_repo(workspace_root: Path, alias: str, repo_root: Path) -> dict[str, Any]:
    _validate_alias(alias)
    workspace_root = workspace_root.resolve()
    repo_root = repo_root.resolve()
    path = registry_path(workspace_root)
    data = _read_or_create_registry(path)
    repos = _load_repos(data)
    if any(repo.alias == alias for repo in repos):
        raise WorkspaceError("DUPLICATE_ALIAS", f"Workspace already has a repo named {alias!r}.")
    if any(repo.root == repo_root for repo in repos):
        raise WorkspaceError("DUPLICATE_REPO", "Workspace already contains this repo path.")

    identity = read_repository_identity(repo_root)
    repo = RegisteredRepo(
        alias=alias,
        root=repo_root,
        index_path=config.get_db_path(repo_root),
        git_remote=identity.get("remote"),
        git_head=identity.get("head"),
        root_fingerprint=compute_root_fingerprint(repo_root),
        added_at=datetime.now(UTC).isoformat(),
    )
    repos.append(repo)
    _write_registry(path, {"version": REGISTRY_VERSION, "repos": [r.to_json_dict() for r in repos]})
    return {"workspace": workspace_payload(workspace_root, repos=repos)["workspace"], "repo": repo.to_public_dict(include_absolute_path=True)}


def remove_repo(workspace_root: Path, alias: str) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    path = registry_path(workspace_root)
    data = _read_registry(path)
    repos = _load_repos(data)
    kept = [repo for repo in repos if repo.alias != alias]
    if len(kept) == len(repos):
        raise WorkspaceError("UNKNOWN_REPO", f"Workspace has no repo named {alias!r}.")
    _write_registry(path, {"version": REGISTRY_VERSION, "repos": [repo.to_json_dict() for repo in kept]})
    return workspace_payload(workspace_root, repos=kept)


def list_repos(workspace_root: Path, *, include_absolute_paths: bool = False) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    repos = load_repos(workspace_root)
    payload = workspace_payload(workspace_root, repos=repos)
    payload["repos"] = [
        repo.to_public_dict(include_absolute_path=include_absolute_paths) for repo in repos
    ]
    return payload


def load_repos(workspace_root: Path) -> list[RegisteredRepo]:
    data = _read_registry(registry_path(workspace_root.resolve()))
    return _load_repos(data)


def workspace_payload(workspace_root: Path, *, repos: list[RegisteredRepo]) -> dict[str, Any]:
    return {
        "workspace": {
            "root": str(workspace_root.resolve()),
            "registry": str(registry_path(workspace_root.resolve())),
            "version": REGISTRY_VERSION,
            "repo_count": len(repos),
        }
    }


def repo_by_alias(workspace_root: Path, alias: str) -> RegisteredRepo:
    for repo in load_repos(workspace_root):
        if repo.alias == alias:
            return repo
    raise WorkspaceError("UNKNOWN_REPO", f"Workspace has no repo named {alias!r}.")


def _validate_alias(alias: str) -> None:
    if not ALIAS_RE.match(alias):
        raise WorkspaceError(
            "INVALID_ALIAS",
            "Repo alias must start with a letter and contain only letters, numbers, dot, dash, or underscore.",
        )


def _read_or_create_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_registry(path, {"version": REGISTRY_VERSION, "repos": []})
    return _read_registry(path)


def _read_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkspaceError("NO_WORKSPACE", "No workspace registry found. Run 'seam workspace init' first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError("WORKSPACE_INVALID", f"Failed to read workspace registry: {exc}") from exc
    if data.get("version") != REGISTRY_VERSION or not isinstance(data.get("repos"), list):
        raise WorkspaceError("WORKSPACE_INVALID", "Unsupported or malformed workspace registry.")
    return data


def _write_registry(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_repos(data: dict[str, Any]) -> list[RegisteredRepo]:
    repos: list[RegisteredRepo] = []
    for raw in data.get("repos", []):
        try:
            repos.append(
                RegisteredRepo(
                    alias=str(raw["alias"]),
                    root=Path(str(raw["root"])).resolve(),
                    index_path=Path(str(raw["index_path"])).resolve(),
                    git_remote=raw.get("git_remote"),
                    git_head=raw.get("git_head"),
                    root_fingerprint=str(raw["root_fingerprint"]),
                    added_at=str(raw["added_at"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError("WORKSPACE_INVALID", "Workspace registry contains an invalid repo entry.") from exc
    return repos


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name
