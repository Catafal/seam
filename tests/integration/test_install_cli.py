"""Integration tests for `seam install` / `seam uninstall`.

Isolation: project scope uses tmp_path; user scope monkeypatches HOME to a tmp dir.
These tests must NEVER touch the developer's real agent configs.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


def test_install_claude_project_writes_mcp_json(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "claude", "--location", "project"])
    assert res.exit_code == 0
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["seam"]
    assert entry["type"] == "stdio"
    assert entry["args"] == ["start", str(tmp_path)]


def test_install_is_idempotent(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path)])
    res = runner.invoke(app, ["install", str(tmp_path), "--json"])
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    actions = {r["target"]: r["action"] for r in payload["data"]["results"]}
    assert actions["claude"] == "unchanged"


def test_print_config_writes_nothing(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--print-config"])
    assert res.exit_code == 0
    assert not (tmp_path / ".mcp.json").exists()
    assert "mcpServers" in res.stdout


def test_install_all_user_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    res = runner.invoke(app, ["install", str(root), "--target", "all", "--location", "user", "--json"])
    payload = json.loads(res.stdout)
    actions = {r["target"]: r["action"] for r in payload["data"]["results"]}
    assert actions["claude"] == "created"
    assert actions["cursor"] == "created"
    assert actions["codex"] == "created"
    # Codex wrote TOML; Claude/Cursor wrote JSON.
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / ".claude.json").exists()
    assert (tmp_path / ".cursor" / "mcp.json").exists()


def test_install_all_project_skips_codex(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "all", "--location", "project", "--json"])
    payload = json.loads(res.stdout)
    actions = {r["target"]: r["action"] for r in payload["data"]["results"]}
    assert actions["claude"] == "created"
    assert actions["cursor"] == "created"
    assert actions["codex"] == "skipped"  # codex supports user only


def test_install_warns_when_no_index(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--json"])
    payload = json.loads(res.stdout)
    assert payload["data"]["index_present"] is False
    assert any("seam init" in w for w in payload["data"]["warnings"])


def test_unknown_target_is_invalid_input(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "emacs", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"


def test_codex_project_location_is_invalid_input(tmp_path: Path) -> None:
    res = runner.invoke(app, ["install", str(tmp_path), "--target", "codex", "--location", "project", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"


def test_uninstall_removes_entry(tmp_path: Path) -> None:
    runner.invoke(app, ["install", str(tmp_path), "--target", "claude", "--location", "project"])
    res = runner.invoke(app, ["uninstall", str(tmp_path), "--target", "claude", "--location", "project", "--json"])
    payload = json.loads(res.stdout)
    assert payload["data"]["results"][0]["action"] == "removed"
    # entry gone, file still valid JSON
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"] == {}
