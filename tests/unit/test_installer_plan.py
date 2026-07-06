import json
from pathlib import Path

from seam.installer import TARGETS
from seam.installer.plan import build_install_plan


def _plan(root: Path, *, location: str = "project", with_mcp: bool = False) -> list[dict]:
    return build_install_plan(
        root,
        location,
        "seam",
        ["start", str(root)],
        TARGETS,
        with_mcp=with_mcp,
    )


def _target(plan: list[dict], name: str) -> dict:
    return next(item for item in plan if item["target"] == name)


def test_project_hint_marks_target_detected(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# local rules\n")

    claude = _target(_plan(tmp_path), "claude")

    assert claude["status"] == "detected"
    assert claude["evidence"] == [{"kind": "agent_project_hint", "path": str(tmp_path / "CLAUDE.md")}]


def test_existing_guidance_marks_target_configured(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("<!-- seam:start -->\nUse seam.\n<!-- seam:end -->\n")

    claude = _target(_plan(tmp_path), "claude")

    assert claude["status"] == "configured"
    assert {"kind": "seam_guidance_present", "path": str(tmp_path / "CLAUDE.md")} in claude[
        "evidence"
    ]


def test_shared_markdown_name_seam_text_is_only_project_hint(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nproject name: seam demo\n")

    claude = _target(_plan(tmp_path), "claude")

    assert claude["status"] == "detected"
    assert {"kind": "seam_guidance_present", "path": str(tmp_path / "CLAUDE.md")} not in claude[
        "evidence"
    ]


def test_existing_project_mcp_marks_target_configured_when_mcp_previewed(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"seam": {"type": "stdio", "command": "seam", "args": []}}})
    )

    claude = _target(_plan(tmp_path, with_mcp=True), "claude")

    assert claude["status"] == "configured"
    assert {"kind": "project_mcp_config", "path": str(tmp_path / ".mcp.json")} in claude[
        "evidence"
    ]
    assert {"kind": "seam_mcp_present", "path": str(tmp_path / ".mcp.json")} in claude["evidence"]


def test_corrupt_config_is_blocked_not_absent(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{ not json")

    claude = _target(_plan(tmp_path, with_mcp=True), "claude")

    assert claude["status"] == "blocked"
    assert {"kind": "config_unparseable", "path": str(tmp_path / ".mcp.json")} in claude[
        "evidence"
    ]


def test_user_only_and_project_only_targets_report_skipped_locations(tmp_path: Path) -> None:
    project_mcp = _plan(tmp_path, location="project", with_mcp=True)
    user_mcp = _plan(tmp_path, location="user", with_mcp=True)

    assert _target(project_mcp, "codex")["status"] == "skipped"
    assert _target(user_mcp, "vscode")["status"] == "skipped"


def test_skipped_mcp_targets_recommend_supported_locations(tmp_path: Path) -> None:
    project_mcp = _plan(tmp_path, location="project", with_mcp=True)
    user_mcp = _plan(tmp_path, location="user", with_mcp=True)

    assert _target(project_mcp, "codex")["recommended_next_call"].endswith(
        "--target codex --with-mcp --location user"
    )
    assert _target(user_mcp, "vscode")["recommended_next_call"].endswith(
        "--target vscode --with-mcp --location project"
    )
