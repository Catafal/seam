"""Codex install target.

User scope → ~/.codex/config.toml, table [mcp_servers.seam] with command + args.
Codex's project-scoped .codex/config.toml only applies in "trusted projects", so
the MVP supports user scope only. Uses tomlkit (tomlfile) to preserve the user's
existing comments/formatting on round-trip.
"""

from pathlib import Path

import tomlkit

from seam.installer import guide
from seam.installer.core import AgentTarget, InstallResult
from seam.installer.markdownfile import remove_block, upsert_block, wrap_block
from seam.installer.tomlfile import (
    atomic_write_toml,
    delete_server_table,
    get_server_table,
    load_toml,
    set_server_table,
)

_SERVER_NAME = "seam"


class CodexTarget(AgentTarget):
    name = "codex"

    def supported_locations(self) -> list[str]:
        return ["user"]

    def config_path(self, root: Path, location: str) -> Path:
        return Path.home() / ".codex" / "config.toml"

    def _entry(self, command: str, args: list[str]) -> dict[str, object]:
        return {"command": command, "args": args}

    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        path = self.config_path(root, location)
        existed = path.exists()
        backed_up = _backup_if_corrupt(path)
        entry = self._entry(command, args)

        doc = load_toml(path) or tomlkit.document()
        if get_server_table(doc, _SERVER_NAME) == entry:
            return InstallResult("unchanged", str(path), entry, backed_up)

        set_server_table(doc, _SERVER_NAME, entry)
        atomic_write_toml(path, doc)
        return InstallResult("created" if not existed else "updated", str(path), entry, backed_up)

    def uninstall(self, root: Path, location: str) -> InstallResult:
        path = self.config_path(root, location)
        doc = load_toml(path)
        if doc is None:
            return InstallResult("not_present", str(path))
        if delete_server_table(doc, _SERVER_NAME):
            atomic_write_toml(path, doc)
            return InstallResult("removed", str(path))
        return InstallResult("not_present", str(path))

    def render_entry(self, command: str, args: list[str]) -> str:
        doc = tomlkit.document()
        set_server_table(doc, _SERVER_NAME, self._entry(command, args))
        return tomlkit.dumps(doc).strip()

    # ── CLI guidance: an inline AGENTS.md block (Codex has no skill/rule) ──────
    # Project-scoped (repo root), unlike Codex's user-only MCP config.

    def _agents_md(self, root: Path) -> Path:
        return root / "AGENTS.md"

    def install_guidance(self, root: Path) -> list[InstallResult]:
        agents = self._agents_md(root)
        action = upsert_block(agents, guide.render_codex_block(), marker=guide.BLOCK_MARKER)
        return [InstallResult(action, str(agents))]

    def uninstall_guidance(self, root: Path) -> list[InstallResult]:
        agents = self._agents_md(root)
        return [InstallResult(remove_block(agents, marker=guide.BLOCK_MARKER), str(agents))]

    def guidance_previews(self, root: Path) -> list[tuple[str, str]]:
        agents = self._agents_md(root)
        return [(str(agents), wrap_block(guide.render_codex_block(), guide.BLOCK_MARKER))]


def _backup_if_corrupt(path: Path) -> bool:
    """Back up a present-but-unparseable config.toml before we overwrite it."""
    if path.exists() and load_toml(path) is None:
        backup = path.with_suffix(path.suffix + ".backup")
        backup.write_bytes(path.read_bytes())
        return True
    return False
