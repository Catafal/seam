"""Claude Code install target.

Project scope → <root>/.mcp.json, key mcpServers.seam (auto-discovered, team-shareable).
User scope    → ~/.claude.json, key projects.<abs-root>.mcpServers.seam (per-project local,
                matching `claude mcp add --scope local`).
Entry shape requires "type": "stdio" (Claude Code requires it; not implied by `command`).
"""

import json
from pathlib import Path

from seam.installer.core import AgentTarget, InstallResult, install_entry, uninstall_entry

_SERVER_NAME = "seam"


class ClaudeTarget(AgentTarget):
    name = "claude"

    def supported_locations(self) -> list[str]:
        return ["project", "user"]

    def config_path(self, root: Path, location: str) -> Path:
        if location == "project":
            return root / ".mcp.json"
        return Path.home() / ".claude.json"

    def _key_path(self, root: Path, location: str) -> list[str]:
        if location == "project":
            return ["mcpServers", _SERVER_NAME]
        # User scope nests servers per absolute project path.
        return ["projects", str(root), "mcpServers", _SERVER_NAME]

    def _entry(self, command: str, args: list[str]) -> dict[str, object]:
        return {"type": "stdio", "command": command, "args": args}

    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        return install_entry(
            self.config_path(root, location),
            self._key_path(root, location),
            self._entry(command, args),
        )

    def uninstall(self, root: Path, location: str) -> InstallResult:
        return uninstall_entry(self.config_path(root, location), self._key_path(root, location))

    def render_entry(self, command: str, args: list[str]) -> str:
        return json.dumps({"mcpServers": {_SERVER_NAME: self._entry(command, args)}}, indent=2)
