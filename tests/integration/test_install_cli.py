"""Integration tests for `seam install` / `seam uninstall`.

CLI-first: bare `seam install` writes guidance (a skill / rule / AGENTS.md block);
`--with-mcp` ALSO writes the MCP config. Isolation: project scope uses tmp_path;
user scope monkeypatches HOME to a tmp dir. These tests must NEVER touch the
developer's real agent configs.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


# ── guidance (the default) ────────────────────────────────────────────────────


def test_default_install_writes_guidance_not_mcp(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "claude"])
    assert res.exit_code == 0
    # Guidance written…
    assert (tmp_path / ".claude" / "skills" / "seam" / "SKILL.md").exists()
    assert "<!-- seam:start -->" in (tmp_path / "CLAUDE.md").read_text()
    # …but NO MCP config by default.
    assert not (tmp_path / ".mcp.json").exists()


def test_default_install_guidance_is_idempotent(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path)])
    res = runner.invoke(app, ["install", str(tmp_path), "--json"])
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    claude = next(r for r in payload["data"]["results"] if r["target"] == "claude")
    assert all(g["action"] == "unchanged" for g in claude["guidance"])
    assert claude["mcp"] is None  # no MCP without --with-mcp


def test_default_install_all_writes_guidance_for_three_targets(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "all", "--json"])
    payload = json.loads(res.stdout)
    by_target = {r["target"]: r for r in payload["data"]["results"]}
    assert all(g["action"] == "created" for g in by_target["claude"]["guidance"])
    # Codex guidance is project-scoped (AGENTS.md) even though its MCP is user-only.
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / ".cursor" / "rules" / "seam.mdc").exists()
    assert all(r["mcp"] is None for r in payload["data"]["results"])


# ── --with-mcp ────────────────────────────────────────────────────────────────


def test_with_mcp_writes_both_guidance_and_mcp(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "claude", "--with-mcp", "--json"]
    )
    payload = json.loads(res.stdout)
    claude = next(r for r in payload["data"]["results"] if r["target"] == "claude")
    assert claude["mcp"]["action"] == "created"
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["seam"]
    assert entry["type"] == "stdio"
    assert entry["args"] == ["start", str(tmp_path)]
    assert (tmp_path / ".claude" / "skills" / "seam" / "SKILL.md").exists()  # guidance too


def test_with_mcp_all_user_scope_writes_mcp_for_three(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    res = runner.invoke(
        app, ["install", str(root), "--target", "all", "--location", "user", "--with-mcp", "--json"]
    )
    payload = json.loads(res.stdout)
    actions = {r["target"]: r["mcp"]["action"] for r in payload["data"]["results"]}
    # claude/cursor/codex/gemini/zed support user scope; vscode is project-only → skipped.
    assert actions["claude"] == "created"
    assert actions["cursor"] == "created"
    assert actions["codex"] == "created"
    assert actions["vscode"] == "skipped"
    assert actions["gemini"] == "created"
    assert actions["zed"] == "created"
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / ".claude.json").exists()
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".gemini" / "settings.json").exists()
    assert (tmp_path / ".config" / "zed" / "settings.json").exists()


def test_with_mcp_all_project_skips_codex_mcp_but_writes_guidance(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "all", "--with-mcp", "--json"]
    )
    payload = json.loads(res.stdout)
    codex = next(r for r in payload["data"]["results"] if r["target"] == "codex")
    assert codex["mcp"]["action"] == "skipped"  # codex MCP supports user only
    assert codex["guidance"][0]["action"] == "created"  # guidance still written
    assert (tmp_path / "AGENTS.md").exists()


# ── print-config ──────────────────────────────────────────────────────────────


def test_print_config_shows_guidance_writes_nothing(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--print-config"])
    assert res.exit_code == 0
    assert not (tmp_path / ".claude" / "skills" / "seam" / "SKILL.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert "Escalation ladder" in res.stdout  # guidance content previewed


def test_print_config_with_mcp_shows_mcp_servers(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--with-mcp", "--print-config"])
    assert res.exit_code == 0
    assert not (tmp_path / ".mcp.json").exists()
    assert "mcpServers" in res.stdout


def test_auto_print_config_json_returns_preview_plan(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--auto", "--print-config", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is True

    data = payload["data"]
    assert data["auto"] is True
    assert data["print_config"] is True
    assert data["with_mcp"] is False
    assert {r["target"] for r in data["results"]} == {
        "claude",
        "cursor",
        "codex",
        "vscode",
        "gemini",
        "zed",
    }

    claude = next(r for r in data["results"] if r["target"] == "claude")
    assert claude["status"] == "supported"
    assert claude["supported_locations"] == ["project", "user"]
    assert claude["selected_location"] == "project"
    assert claude["evidence"] == []
    assert any(p.endswith("CLAUDE.md") for p in claude["guidance_preview_paths"])
    assert claude["mcp_preview"] is None
    assert "seam install" in claude["recommended_next_call"]


def test_auto_without_print_config_fails_closed(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--auto", "--json"])
    assert res.exit_code == 1
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert "--auto" in payload["error"]["message"]
    assert not (tmp_path / "CLAUDE.md").exists()


def test_install_help_describes_auto_preview_only() -> None:
    res = runner.invoke(app, ["install", "--help"])
    assert res.exit_code == 0
    assert "--auto" in res.stdout
    assert "requires --print-config" in res.stdout
    assert "writes nothing" in res.stdout


# ── warnings + validation ─────────────────────────────────────────────────────


def test_install_warns_when_no_index(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--json"])
    payload = json.loads(res.stdout)
    assert payload["data"]["index_present"] is False
    assert any("seam init" in w for w in payload["data"]["warnings"])


def test_unknown_target_is_invalid_input(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "emacs", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"


def test_codex_project_with_mcp_is_invalid_input(tmp_path: Path) -> None:
    # --with-mcp + explicit codex + project is rejected upfront (before any write).
    res = runner.invoke(
        app,
        ["install", str(tmp_path), "--target", "codex", "--location", "project", "--with-mcp", "--json"],
    )
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"
    assert not (tmp_path / "AGENTS.md").exists()  # nothing written on the upfront fail


def test_codex_project_without_mcp_writes_guidance(tmp_path: Path) -> None:
    # Without --with-mcp the same command succeeds: guidance is project-scoped.
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "codex", "--json"])
    assert res.exit_code == 0
    assert (tmp_path / "AGENTS.md").exists()


# ── uninstall ─────────────────────────────────────────────────────────────────


def test_uninstall_removes_guidance_and_mcp(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path), "--target", "claude", "--with-mcp"])
    res = runner.invoke(
        app, ["uninstall", str(tmp_path), "--target", "claude", "--location", "project", "--json"]
    )
    payload = json.loads(res.stdout)
    claude = payload["data"]["results"][0]
    assert claude["mcp"]["action"] == "removed"
    assert all(g["action"] == "removed" for g in claude["guidance"])
    assert not (tmp_path / ".claude" / "skills" / "seam" / "SKILL.md").exists()
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"] == {}


# ── VS Code target ─────────────────────────────────────────────────────────────


def test_vscode_guidance_only_writes_copilot_instructions(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "vscode"])
    assert res.exit_code == 0
    copilot_md = tmp_path / ".github" / "copilot-instructions.md"
    assert copilot_md.exists()
    assert "Escalation ladder" in copilot_md.read_text()
    # No MCP config without --with-mcp.
    assert not (tmp_path / ".vscode" / "mcp.json").exists()


def test_vscode_with_mcp_writes_servers_key(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "vscode", "--with-mcp", "--json"]
    )
    payload = json.loads(res.stdout)
    vscode = next(r for r in payload["data"]["results"] if r["target"] == "vscode")
    assert vscode["mcp"]["action"] == "created"

    mcp_json = tmp_path / ".vscode" / "mcp.json"
    assert mcp_json.exists()
    data = json.loads(mcp_json.read_text())
    # VS Code uses "servers" (not "mcpServers") — the critical shape difference.
    assert "servers" in data
    assert "mcpServers" not in data
    entry = data["servers"]["seam"]
    assert entry["type"] == "stdio"
    assert entry["args"] == ["start", str(tmp_path)]


def test_vscode_with_mcp_project_is_valid(tmp_path: Path) -> None:
    # VS Code supports project location — must not be rejected.
    res = runner.invoke(
        app,
        ["install", str(tmp_path), "--target", "vscode", "--location", "project", "--with-mcp", "--json"],
    )
    assert res.exit_code == 0
    assert (tmp_path / ".vscode" / "mcp.json").exists()


def test_vscode_with_mcp_user_is_invalid(tmp_path: Path) -> None:
    # VS Code user scope is not supported for MVP — must exit 1 with INVALID_INPUT.
    res = runner.invoke(
        app,
        ["install", str(tmp_path), "--target", "vscode", "--location", "user", "--with-mcp", "--json"],
    )
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"
    # No files should have been written (upfront validation fails before any write).
    assert not (tmp_path / ".vscode" / "mcp.json").exists()


def test_vscode_print_config_shows_servers_key(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "vscode", "--with-mcp", "--print-config"]
    )
    assert res.exit_code == 0
    assert not (tmp_path / ".vscode" / "mcp.json").exists()  # nothing written
    assert "servers" in res.stdout
    assert "stdio" in res.stdout


def test_vscode_target_all_includes_vscode(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "all", "--json"])
    payload = json.loads(res.stdout)
    targets = {r["target"] for r in payload["data"]["results"]}
    assert "vscode" in targets
    # vscode guidance must have been written
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()


def test_vscode_uninstall_removes_guidance_and_mcp(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path), "--target", "vscode", "--with-mcp"])
    res = runner.invoke(
        app, ["uninstall", str(tmp_path), "--target", "vscode", "--location", "project", "--json"]
    )
    payload = json.loads(res.stdout)
    vscode = payload["data"]["results"][0]
    assert vscode["mcp"]["action"] == "removed"
    assert vscode["guidance"][0]["action"] == "removed"
    # MCP file persists with empty "servers" dict (shared-file residue).
    mcp_json = tmp_path / ".vscode" / "mcp.json"
    assert json.loads(mcp_json.read_text())["servers"] == {}


# ── Gemini CLI target ──────────────────────────────────────────────────────────


def test_gemini_guidance_only_writes_gemini_md(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "gemini"])
    assert res.exit_code == 0
    gemini_md = tmp_path / "GEMINI.md"
    assert gemini_md.exists()
    assert "Escalation ladder" in gemini_md.read_text()
    # No MCP config without --with-mcp.
    assert not (tmp_path / ".gemini" / "settings.json").exists()


def test_gemini_with_mcp_project_writes_mcp_servers(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "gemini", "--with-mcp", "--json"]
    )
    payload = json.loads(res.stdout)
    gemini = next(r for r in payload["data"]["results"] if r["target"] == "gemini")
    assert gemini["mcp"]["action"] == "created"

    settings = tmp_path / ".gemini" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    # Gemini uses "mcpServers" — consistent with Cursor.
    assert "mcpServers" in data
    assert "servers" not in data
    entry = data["mcpServers"]["seam"]
    assert "type" not in entry
    assert entry["args"] == ["start", str(tmp_path)]


def test_gemini_with_mcp_user_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    res = runner.invoke(
        app,
        ["install", str(root), "--target", "gemini", "--location", "user", "--with-mcp", "--json"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    gemini = next(r for r in payload["data"]["results"] if r["target"] == "gemini")
    assert gemini["mcp"]["action"] == "created"
    # User-scope MCP written to ~/.gemini/settings.json.
    user_settings = tmp_path / ".gemini" / "settings.json"
    assert user_settings.exists()
    assert json.loads(user_settings.read_text())["mcpServers"]["seam"]["args"] == ["start", str(root)]


def test_gemini_print_config_shows_mcp_servers_key(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "gemini", "--with-mcp", "--print-config"]
    )
    assert res.exit_code == 0
    assert not (tmp_path / ".gemini" / "settings.json").exists()  # nothing written
    assert "mcpServers" in res.stdout


def test_gemini_target_all_includes_gemini(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "all", "--json"])
    payload = json.loads(res.stdout)
    targets = {r["target"] for r in payload["data"]["results"]}
    assert "gemini" in targets
    # gemini guidance must have been written
    assert (tmp_path / "GEMINI.md").exists()


def test_gemini_uninstall_removes_guidance_and_mcp(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path), "--target", "gemini", "--with-mcp"])
    res = runner.invoke(
        app, ["uninstall", str(tmp_path), "--target", "gemini", "--location", "project", "--json"]
    )
    payload = json.loads(res.stdout)
    gemini = payload["data"]["results"][0]
    assert gemini["mcp"]["action"] == "removed"
    assert gemini["guidance"][0]["action"] == "removed"
    # MCP file persists with empty "mcpServers" dict (shared-file residue).
    settings = tmp_path / ".gemini" / "settings.json"
    assert json.loads(settings.read_text())["mcpServers"] == {}


# ── Zed target ─────────────────────────────────────────────────────────────────


def test_zed_guidance_only_writes_agents_md(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "zed"])
    assert res.exit_code == 0
    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.exists()
    assert "Escalation ladder" in agents_md.read_text()
    # No MCP config without --with-mcp.
    assert not (tmp_path / ".zed" / "settings.json").exists()


def test_zed_with_mcp_project_writes_context_servers(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "zed", "--with-mcp", "--json"]
    )
    payload = json.loads(res.stdout)
    zed = next(r for r in payload["data"]["results"] if r["target"] == "zed")
    assert zed["mcp"]["action"] == "created"

    settings = tmp_path / ".zed" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    # Zed uses "context_servers" with "source": "custom".
    assert "context_servers" in data
    assert "mcpServers" not in data
    entry = data["context_servers"]["seam"]
    assert entry["source"] == "custom"
    assert entry["args"] == ["start", str(tmp_path)]


def test_zed_with_mcp_user_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    res = runner.invoke(
        app,
        ["install", str(root), "--target", "zed", "--location", "user", "--with-mcp", "--json"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    zed = next(r for r in payload["data"]["results"] if r["target"] == "zed")
    assert zed["mcp"]["action"] == "created"
    # User-scope MCP written to ~/.config/zed/settings.json.
    user_settings = tmp_path / ".config" / "zed" / "settings.json"
    assert user_settings.exists()
    data = json.loads(user_settings.read_text())
    assert data["context_servers"]["seam"]["args"] == ["start", str(root)]


def test_zed_print_config_shows_context_servers_key(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["install", str(tmp_path), "--target", "zed", "--with-mcp", "--print-config"]
    )
    assert res.exit_code == 0
    assert not (tmp_path / ".zed" / "settings.json").exists()  # nothing written
    assert "context_servers" in res.stdout


def test_zed_target_all_includes_zed(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "all", "--json"])
    payload = json.loads(res.stdout)
    targets = {r["target"] for r in payload["data"]["results"]}
    assert "zed" in targets
    # zed guidance must have been written (shares AGENTS.md with codex)
    assert (tmp_path / "AGENTS.md").exists()


def test_zed_uninstall_removes_guidance_and_mcp(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path), "--target", "zed", "--with-mcp"])
    res = runner.invoke(
        app, ["uninstall", str(tmp_path), "--target", "zed", "--location", "project", "--json"]
    )
    payload = json.loads(res.stdout)
    zed = payload["data"]["results"][0]
    assert zed["mcp"]["action"] == "removed"
    assert zed["guidance"][0]["action"] == "removed"
    # MCP file persists with empty "context_servers" dict (shared-file residue).
    settings = tmp_path / ".zed" / "settings.json"
    assert json.loads(settings.read_text())["context_servers"] == {}
