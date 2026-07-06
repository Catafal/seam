"""Read-only installer planning for preview-only auto-detection.

The installer target classes own write paths and rendered config shapes. This
module only inspects those targets so `--auto --print-config` can stay useful
without ever crossing into the write path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from seam.installer.core import AgentTarget
from seam.installer.jsonfile import get_in, load_json
from seam.installer.markdownfile import read_text
from seam.installer.tomlfile import get_server_table, load_toml

_SEAM_MARKER = "<!-- seam:start -->"
_SERVER_NAME = "seam"

_PROJECT_HINTS: dict[str, tuple[str, ...]] = {
    "claude": ("CLAUDE.md", ".claude", ".mcp.json"),
    "cursor": (".cursor", ".cursor/rules", ".cursor/mcp.json"),
    "codex": ("AGENTS.md",),
    "vscode": (".vscode", ".github/copilot-instructions.md"),
    "gemini": ("GEMINI.md", ".gemini"),
    "zed": ("AGENTS.md", ".zed"),
}


def build_install_plan(
    root: Path,
    location: str,
    command: str,
    args: list[str],
    targets: dict[str, AgentTarget],
    *,
    with_mcp: bool,
) -> list[dict[str, Any]]:
    """Return a serializable preview plan for all registered installer targets."""

    return [
        _target_plan(tgt, root, location, command, args, with_mcp=with_mcp)
        for tgt in targets.values()
    ]


def _target_plan(
    tgt: AgentTarget,
    root: Path,
    location: str,
    command: str,
    args: list[str],
    *,
    with_mcp: bool,
) -> dict[str, Any]:
    guidance_previews = tgt.guidance_previews(root)
    guidance_paths = [path for path, _content in guidance_previews]
    supported_locations = tgt.supported_locations()
    evidence = _guidance_evidence(tgt, root, guidance_paths)
    warnings: list[str] = []

    mcp_preview: dict[str, Any] | None = None
    if with_mcp:
        if location in supported_locations:
            mcp_path = tgt.config_path(root, location)
            mcp_preview = {"path": str(mcp_path), "config": tgt.render_entry(command, args)}
            evidence.extend(_mcp_evidence(tgt, root, location, mcp_path))
        else:
            warnings.append(
                f"MCP location '{location}' unsupported (supports {supported_locations})"
            )

    location_supported = location in supported_locations
    status = _status(
        evidence,
        warnings,
        with_mcp=with_mcp,
        location_supported=location_supported,
    )
    return {
        "target": tgt.name,
        "status": status,
        "evidence": evidence,
        "supported_locations": supported_locations,
        "selected_location": location,
        "guidance_preview_paths": guidance_paths,
        "mcp_preview": mcp_preview,
        "would_write": False,
        "warnings": warnings,
        "recommended_next_call": _recommended_next_call(
            tgt.name,
            root,
            _recommended_location(location, supported_locations, with_mcp=with_mcp),
            with_mcp,
        ),
    }


def _guidance_evidence(tgt: AgentTarget, root: Path, guidance_paths: list[str]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for path_str in guidance_paths:
        path = Path(path_str)
        if _seam_guidance_present(path):
            evidence.append({"kind": "seam_guidance_present", "path": str(path)})

    for rel in _PROJECT_HINTS.get(tgt.name, ()):
        path = root / rel
        if path.exists() and not any(item["path"] == str(path) for item in evidence):
            evidence.append({"kind": "agent_project_hint", "path": str(path)})
    return evidence


def _seam_guidance_present(path: Path) -> bool:
    if not path.exists():
        return False
    text = read_text(path)
    if text is None:
        return False
    return _SEAM_MARKER in text


def _mcp_evidence(tgt: AgentTarget, root: Path, location: str, path: Path) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    if not path.exists():
        return evidence

    evidence.append({"kind": _mcp_config_kind(location), "path": str(path)})
    entry = _read_mcp_entry(tgt, root, location, path)
    if entry == "unparseable":
        evidence.append({"kind": "config_unparseable", "path": str(path)})
    elif entry is not None:
        evidence.append({"kind": "seam_mcp_present", "path": str(path)})
    else:
        evidence.append({"kind": _agent_hint_kind(location), "path": str(path)})
    return evidence


def _read_mcp_entry(tgt: AgentTarget, root: Path, location: str, path: Path) -> object | None:
    if tgt.name == "codex":
        doc = load_toml(path)
        if doc is None:
            return "unparseable"
        return get_server_table(doc, _SERVER_NAME)

    data = load_json(path)
    if data is None:
        return "unparseable"
    key_path = _json_mcp_key_path(tgt.name, root, location)
    return get_in(data, key_path) if key_path else None


def _json_mcp_key_path(target_name: str, root: Path, location: str) -> list[str] | None:
    if target_name == "claude" and location == "user":
        return ["projects", str(root), "mcpServers", _SERVER_NAME]
    if target_name == "vscode":
        return ["servers", _SERVER_NAME]
    if target_name == "zed":
        return ["context_servers", _SERVER_NAME]
    if target_name in {"claude", "cursor", "gemini"}:
        return ["mcpServers", _SERVER_NAME]
    return None


def _mcp_config_kind(location: str) -> str:
    return "user_mcp_config" if location == "user" else "project_mcp_config"


def _agent_hint_kind(location: str) -> str:
    return "agent_user_hint" if location == "user" else "agent_project_hint"


def _status(
    evidence: list[dict[str, str]],
    warnings: list[str],
    *,
    with_mcp: bool,
    location_supported: bool,
) -> str:
    kinds = {item["kind"] for item in evidence}
    if "config_unparseable" in kinds:
        return "blocked"
    if "seam_guidance_present" in kinds or "seam_mcp_present" in kinds:
        return "configured"
    if with_mcp and not location_supported:
        return "skipped"
    if kinds:
        return "detected"
    if warnings:
        return "skipped"
    return "supported"


def _recommended_location(location: str, supported_locations: list[str], *, with_mcp: bool) -> str:
    if not with_mcp or location in supported_locations or not supported_locations:
        return location
    return supported_locations[0]


def _recommended_next_call(target: str, root: Path, location: str, with_mcp: bool) -> str:
    parts = ["seam", "install", str(root), "--target", target]
    if with_mcp:
        parts.extend(["--with-mcp", "--location", location])
    return " ".join(parts)
