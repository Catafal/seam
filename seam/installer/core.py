"""Installer core — the AgentTarget contract, InstallResult, and the shared
idempotent JSON merge used by the JSON-format targets (Claude, Cursor).

The TOML target (Codex) shares InstallResult but implements its own merge in
codex.py via tomlfile — TOML table editing differs enough from nested-dict JSON
that forcing it through this path would be more convoluted than separate code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seam.installer.jsonfile import (
    atomic_write_json,
    delete_in,
    get_in,
    load_json,
    set_in,
)


@dataclass
class InstallResult:
    """Outcome of a single install/uninstall against one target+location.

    action:
      created      — config file did not exist; we created it with the entry
      updated      — config file existed; we added or changed our entry
      unchanged    — our entry already deep-equals the desired entry (no write)
      removed      — uninstall deleted an existing entry
      not_present  — uninstall found nothing to remove
    backed_up: a present-but-corrupt config was copied to <path>.backup before write.
    """

    action: str
    path: str
    entry: dict[str, Any] | None = None
    backed_up: bool = False


class AgentTarget(ABC):
    """A coding agent Seam can install itself into. One concrete class per agent."""

    name: str

    @abstractmethod
    def supported_locations(self) -> list[str]:
        """Which `--location` values this target accepts (e.g. ["project", "user"])."""

    @abstractmethod
    def config_path(self, root: Path, location: str) -> Path:
        """Absolute path of the config file this target writes for `location`."""

    @abstractmethod
    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        """Write/merge the seam MCP entry. Idempotent."""

    @abstractmethod
    def uninstall(self, root: Path, location: str) -> InstallResult:
        """Remove the seam MCP entry if present."""

    @abstractmethod
    def render_entry(self, command: str, args: list[str]) -> str:
        """Human-readable preview of the entry this target would write (for --print-config)."""


def _backup_if_corrupt(path: Path) -> bool:
    """If `path` exists but does not parse as JSON, copy it to <path>.backup.

    Protects a user's hand-edited config from being silently destroyed when we
    have to overwrite an unparseable file. Returns True iff a backup was made.
    """
    if path.exists() and load_json(path) is None:
        backup = path.with_suffix(path.suffix + ".backup")
        backup.write_bytes(path.read_bytes())
        return True
    return False


def install_entry(path: Path, key_path: list[str], entry: dict[str, Any]) -> InstallResult:
    """Idempotently set `entry` at `key_path` in the JSON file at `path`.

    Preserves every other key in the file. Deep-equal entry → unchanged (no write).
    """
    existed = path.exists()
    backed_up = _backup_if_corrupt(path)
    data = load_json(path) or {}

    if get_in(data, key_path) == entry:
        return InstallResult("unchanged", str(path), entry, backed_up)

    set_in(data, key_path, entry)
    atomic_write_json(path, data)
    return InstallResult("created" if not existed else "updated", str(path), entry, backed_up)


def uninstall_entry(path: Path, key_path: list[str]) -> InstallResult:
    """Remove the leaf at `key_path` from the JSON file at `path`, if present."""
    data = load_json(path)
    if data is None:
        # Absent or unparseable → nothing we recognize to remove.
        return InstallResult("not_present", str(path))
    if delete_in(data, key_path):
        atomic_write_json(path, data)
        return InstallResult("removed", str(path))
    return InstallResult("not_present", str(path))
