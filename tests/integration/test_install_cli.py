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
    assert actions == {"claude": "created", "cursor": "created", "codex": "created"}
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / ".claude.json").exists()
    assert (tmp_path / ".cursor" / "mcp.json").exists()


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
