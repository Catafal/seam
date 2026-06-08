"""Cursor install target.

Project scope → <root>/.cursor/mcp.json; user/global scope → ~/.cursor/mcp.json.
Both use {"mcpServers": {"seam": {"command", "args"}}}. Unlike Claude, Cursor does
NOT use a "type" field — a command-based entry is stdio by definition.
"""

import json
from pathlib import Path

from seam.installer import guide
from seam.installer.core import AgentTarget, InstallResult, install_entry, uninstall_entry
from seam.installer.markdownfile import remove_file, write_file

_SERVER_NAME = "seam"


class CursorTarget(AgentTarget):
    name = "cursor"

    def supported_locations(self) -> list[str]:
        return ["project", "user"]

    def config_path(self, root: Path, location: str) -> Path:
        if location == "project":
            return root / ".cursor" / "mcp.json"
        return Path.home() / ".cursor" / "mcp.json"

    def _key_path(self) -> list[str]:
        return ["mcpServers", _SERVER_NAME]

    def _entry(self, command: str, args: list[str]) -> dict[str, object]:
        return {"command": command, "args": args}

    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        return install_entry(
            self.config_path(root, location), self._key_path(), self._entry(command, args)
        )

    def uninstall(self, root: Path, location: str) -> InstallResult:
        return uninstall_entry(self.config_path(root, location), self._key_path())

    def render_entry(self, command: str, args: list[str]) -> str:
        return json.dumps({"mcpServers": {_SERVER_NAME: self._entry(command, args)}}, indent=2)

    # ── CLI guidance: an "Agent Requested" project rule (progressive) ─────────

    def _rule_path(self, root: Path) -> Path:
        # Must be `.mdc` (a plain `.md` in .cursor/rules/ is ignored by Cursor).
        return root / ".cursor" / "rules" / f"{_SERVER_NAME}.mdc"

    def install_guidance(self, root: Path) -> list[InstallResult]:
        rule = self._rule_path(root)
        return [InstallResult(write_file(rule, guide.render_cursor_rule()), str(rule))]

    def uninstall_guidance(self, root: Path) -> list[InstallResult]:
        rule = self._rule_path(root)
        return [InstallResult(remove_file(rule), str(rule))]

    def guidance_previews(self, root: Path) -> list[tuple[str, str]]:
        return [(str(self._rule_path(root)), guide.render_cursor_rule())]
