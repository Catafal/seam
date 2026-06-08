"""Claude Code install target.

Project scope → <root>/.mcp.json, key mcpServers.seam (auto-discovered, team-shareable).
User scope    → ~/.claude.json, key projects.<abs-root>.mcpServers.seam (per-project local,
                matching `claude mcp add --scope local`).
Entry shape requires "type": "stdio" (Claude Code requires it; not implied by `command`).
"""

import json
from pathlib import Path

from seam.installer import guide
from seam.installer.core import AgentTarget, InstallResult, install_entry, uninstall_entry
from seam.installer.markdownfile import (
    remove_block,
    remove_file,
    upsert_block,
    wrap_block,
    write_file,
)

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

    # ── CLI guidance: a project skill + a thin CLAUDE.md discovery pointer ─────

    def _skill_path(self, root: Path) -> Path:
        return root / ".claude" / "skills" / _SERVER_NAME / "SKILL.md"

    def _claude_md(self, root: Path) -> Path:
        return root / "CLAUDE.md"

    def install_guidance(self, root: Path) -> list[InstallResult]:
        skill = self._skill_path(root)
        claude_md = self._claude_md(root)
        return [
            InstallResult(write_file(skill, guide.render_skill()), str(skill)),
            InstallResult(
                upsert_block(claude_md, guide.render_claude_hook(), marker=guide.BLOCK_MARKER),
                str(claude_md),
            ),
        ]

    def uninstall_guidance(self, root: Path) -> list[InstallResult]:
        skill = self._skill_path(root)
        claude_md = self._claude_md(root)
        action = remove_file(skill)
        # Tidy the now-empty skill directory we created; ignore if non-empty/missing.
        skill_dir = skill.parent
        if skill_dir.exists() and not any(skill_dir.iterdir()):
            skill_dir.rmdir()
        return [
            InstallResult(action, str(skill)),
            InstallResult(remove_block(claude_md, marker=guide.BLOCK_MARKER), str(claude_md)),
        ]

    def guidance_previews(self, root: Path) -> list[tuple[str, str]]:
        return [
            (str(self._skill_path(root)), guide.render_skill()),
            (
                str(self._claude_md(root)),
                wrap_block(guide.render_claude_hook(), guide.BLOCK_MARKER),
            ),
        ]
