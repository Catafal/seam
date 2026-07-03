"""Agent-config installer for `seam install` / `seam uninstall`.

Writes an MCP stdio server entry ("seam") into a coding agent's config so the
agent auto-discovers the local Seam server. CLI-only — the MCP server itself is
read-only, so there is no `seam_install` tool.

Layering (leaf → composite):
  jsonfile.py / tomlfile.py  — pure file format ops (stdlib json / tomlkit), no Seam deps
  core.py                    — AgentTarget ABC, InstallResult, shared JSON install/uninstall
  claude.py / cursor.py / codex.py — one AgentTarget each (own path + format)
  this module                — TARGETS registry + helpers (get_target, resolve_seam_command)

Adding an agent later = one target file + one registry entry.
"""

import shutil
import sys
from pathlib import Path

from seam.installer.claude import ClaudeTarget
from seam.installer.codex import CodexTarget
from seam.installer.core import AgentTarget, InstallResult
from seam.installer.cursor import CursorTarget
from seam.installer.gemini import GeminiTarget
from seam.installer.vscode import VscodeTarget

# Registry — keys are the `--target` values the CLI accepts (besides "all").
TARGETS: dict[str, AgentTarget] = {
    "claude": ClaudeTarget(),
    "cursor": CursorTarget(),
    "codex": CodexTarget(),
    "vscode": VscodeTarget(),
    "gemini": GeminiTarget(),
}


def get_target(name: str) -> AgentTarget | None:
    """Return the target for `name`, or None if unknown (CLI maps None → INVALID_INPUT)."""
    return TARGETS.get(name)


def resolve_seam_command() -> tuple[str, bool]:
    """Resolve the absolute executable to put in the agent config.

    Returns (command, found). The agent spawns the server with an unknown working
    directory and PATH, so we want an ABSOLUTE, self-contained command. Resolution
    order, most-to-least reliable:
      1. sys.argv[0] — when invoked as the `seam` console script, this IS that
         script's path (no PATH/cwd assumptions). Only trusted when it exists and is
         named seam* (under pytest/`python -m` it is the test runner — skip it then).
      2. shutil.which("seam") — the installed console script on PATH.
      3. bare "seam" with found=False — correct once published / on PATH; CLI warns.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidate = Path(argv0)
        if candidate.name.startswith("seam") and candidate.exists():
            return str(candidate.resolve()), True
    found = shutil.which("seam")
    if found:
        return found, True
    return "seam", False


__all__ = [
    "TARGETS",
    "AgentTarget",
    "InstallResult",
    "get_target",
    "resolve_seam_command",
]
