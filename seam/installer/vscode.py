"""VS Code install target.

Project scope only → <root>/.vscode/mcp.json. VS Code uses "servers" (NOT
"mcpServers") and requires a "type" field for stdio entries. User-profile path
is OS-specific and not repo-relative; user scope is deferred for MVP.

CLI guidance is written to <root>/.github/copilot-instructions.md — VS Code
Copilot's conventions file — as a marker-delimited block.
"""

import json
from pathlib import Path

from seam.installer import guide
from seam.installer.core import AgentTarget, InstallResult, install_entry, uninstall_entry
from seam.installer.markdownfile import remove_block, upsert_block, wrap_block

_SERVER_NAME = "seam"


class VscodeTarget(AgentTarget):
    name = "vscode"

    def supported_locations(self) -> list[str]:
        # User-profile path is OS-specific (not repo-relative); project only for MVP.
        return ["project"]

    def config_path(self, root: Path, location: str) -> Path:
        # VS Code reads <root>/.vscode/mcp.json for project-scoped MCP servers.
        return root / ".vscode" / "mcp.json"

    def _key_path(self) -> list[str]:
        # VS Code uses "servers" — NOT "mcpServers" (the shape outlier vs Cursor/Claude).
        return ["servers", _SERVER_NAME]

    def _entry(self, command: str, args: list[str]) -> dict[str, object]:
        # "type": "stdio" is required by VS Code (unlike Cursor which omits it).
        return {"type": "stdio", "command": command, "args": args}

    def install(self, root: Path, location: str, command: str, args: list[str]) -> InstallResult:
        return install_entry(
            self.config_path(root, location), self._key_path(), self._entry(command, args)
        )

    def uninstall(self, root: Path, location: str) -> InstallResult:
        return uninstall_entry(self.config_path(root, location), self._key_path())

    def render_entry(self, command: str, args: list[str]) -> str:
        return json.dumps({"servers": {_SERVER_NAME: self._entry(command, args)}}, indent=2)

    # ── CLI guidance: block in .github/copilot-instructions.md ────────────────
    # Copilot reads .github/copilot-instructions.md as a repo-level context file.
    # Marker-block upsert preserves any existing project notes in the file.

    def _copilot_instructions(self, root: Path) -> Path:
        return root / ".github" / "copilot-instructions.md"

    def install_guidance(self, root: Path) -> list[InstallResult]:
        path = self._copilot_instructions(root)
        action = upsert_block(path, guide.render_codex_block(), marker=guide.BLOCK_MARKER)
        return [InstallResult(action, str(path))]

    def uninstall_guidance(self, root: Path) -> list[InstallResult]:
        path = self._copilot_instructions(root)
        return [InstallResult(remove_block(path, marker=guide.BLOCK_MARKER), str(path))]

    def guidance_previews(self, root: Path) -> list[tuple[str, str]]:
        path = self._copilot_instructions(root)
        return [(str(path), wrap_block(guide.render_codex_block(), guide.BLOCK_MARKER))]
