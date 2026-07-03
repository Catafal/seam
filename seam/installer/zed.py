"""Zed install target.

Project scope → <root>/.zed/settings.json; user/global scope →
~/.config/zed/settings.json. Both use:
  {"context_servers": {"seam": {"source": "custom", "command", "args"}}}

Zed requires "source": "custom" for local stdio MCP servers that are not
registered in its extension registry.

CLI guidance is written to <root>/AGENTS.md as a marker-delimited block,
reusing the same codex-block renderer as Codex. Both Codex and Zed share the
SAME <!-- seam:start/end --> block in AGENTS.md — upsert_block is idempotent
so installing both targets (or either order) yields ONE owned block, no duplication.
"""

import json
from pathlib import Path

from seam.installer import guide
from seam.installer.core import AgentTarget, InstallResult, install_entry, uninstall_entry
from seam.installer.markdownfile import remove_block, upsert_block, wrap_block

_SERVER_NAME = "seam"


class ZedTarget(AgentTarget):
    name = "zed"

    def supported_locations(self) -> list[str]:
        return ["project", "user"]

    def config_path(self, root: Path, location: str) -> Path:
        if location == "project":
            return root / ".zed" / "settings.json"
        # Zed user settings: ~/.config/zed/settings.json (macOS and Linux).
        return Path.home() / ".config" / "zed" / "settings.json"

    def _key_path(self) -> list[str]:
        return ["context_servers", _SERVER_NAME]

    def _entry(self, command: str, args: list[str]) -> dict[str, object]:
        # Zed requires "source": "custom" for stdio servers not in the extension registry.
        return {"source": "custom", "command": command, "args": args}

    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        return install_entry(
            self.config_path(root, location), self._key_path(), self._entry(command, args)
        )

    def uninstall(self, root: Path, location: str) -> InstallResult:
        return uninstall_entry(self.config_path(root, location), self._key_path())

    def render_entry(self, command: str, args: list[str]) -> str:
        return json.dumps(
            {"context_servers": {_SERVER_NAME: self._entry(command, args)}}, indent=2
        )

    # ── CLI guidance: shared AGENTS.md block (same file as Codex) ─────────────
    # Zed reads AGENTS.md from the project root. The same <!-- seam:start/end -->
    # marker ensures Codex + Zed installs converge to ONE block, never duplicate.

    def _agents_md(self, root: Path) -> Path:
        return root / "AGENTS.md"

    def install_guidance(self, root: Path) -> list[InstallResult]:
        path = self._agents_md(root)
        action = upsert_block(path, guide.render_codex_block(), marker=guide.BLOCK_MARKER)
        return [InstallResult(action, str(path))]

    def uninstall_guidance(self, root: Path) -> list[InstallResult]:
        path = self._agents_md(root)
        return [InstallResult(remove_block(path, marker=guide.BLOCK_MARKER), str(path))]

    def guidance_previews(self, root: Path) -> list[tuple[str, str]]:
        path = self._agents_md(root)
        return [(str(path), wrap_block(guide.render_codex_block(), guide.BLOCK_MARKER))]
