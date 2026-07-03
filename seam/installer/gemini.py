"""Gemini CLI install target.

Project scope → <root>/.gemini/settings.json; user/global scope →
~/.gemini/settings.json. Both use {"mcpServers": {"seam": {"command", "args"}}}.
Near-identical to CursorTarget — Gemini also omits "type" and uses mcpServers.

CLI guidance is written to <root>/GEMINI.md (Gemini CLI's context file) as a
marker-delimited block, reusing the same codex-block renderer as Codex/ZedTarget.
"""

import json
from pathlib import Path

from seam.installer import guide
from seam.installer.core import AgentTarget, InstallResult, install_entry, uninstall_entry
from seam.installer.markdownfile import remove_block, upsert_block, wrap_block

_SERVER_NAME = "seam"


class GeminiTarget(AgentTarget):
    name = "gemini"

    def supported_locations(self) -> list[str]:
        return ["project", "user"]

    def config_path(self, root: Path, location: str) -> Path:
        if location == "project":
            return root / ".gemini" / "settings.json"
        return Path.home() / ".gemini" / "settings.json"

    def _key_path(self) -> list[str]:
        return ["mcpServers", _SERVER_NAME]

    def _entry(self, command: str, args: list[str]) -> dict[str, object]:
        # Gemini CLI uses command/args only — no "type" field (same as Cursor).
        return {"command": command, "args": args}

    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        return install_entry(
            self.config_path(root, location), self._key_path(), self._entry(command, args)
        )

    def uninstall(self, root: Path, location: str) -> InstallResult:
        return uninstall_entry(self.config_path(root, location), self._key_path())

    def render_entry(self, command: str, args: list[str]) -> str:
        return json.dumps({"mcpServers": {_SERVER_NAME: self._entry(command, args)}}, indent=2)

    # ── CLI guidance: block in GEMINI.md (Gemini CLI's context file) ─────────
    # Gemini CLI reads GEMINI.md from the project root as its context file.
    # Marker-block upsert preserves any existing project notes in the file.

    def _gemini_md(self, root: Path) -> Path:
        return root / "GEMINI.md"

    def install_guidance(self, root: Path) -> list[InstallResult]:
        path = self._gemini_md(root)
        action = upsert_block(path, guide.render_codex_block(), marker=guide.BLOCK_MARKER)
        return [InstallResult(action, str(path))]

    def uninstall_guidance(self, root: Path) -> list[InstallResult]:
        path = self._gemini_md(root)
        return [InstallResult(remove_block(path, marker=guide.BLOCK_MARKER), str(path))]

    def guidance_previews(self, root: Path) -> list[tuple[str, str]]:
        path = self._gemini_md(root)
        return [(str(path), wrap_block(guide.render_codex_block(), guide.BLOCK_MARKER))]
