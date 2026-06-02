"""Unit tests for the `seam install` installer modules.

WHY isolation matters: these tests must NEVER touch the developer's real
~/.claude.json, ~/.cursor/mcp.json, or ~/.codex/config.toml. User-scope tests
monkeypatch HOME to a tmp dir; project-scope tests use tmp_path. All assertions
are on external behavior (file contents / InstallResult), not internals.
"""

import json
from pathlib import Path

from seam.installer import get_target, resolve_seam_command
from seam.installer.claude import ClaudeTarget
from seam.installer.codex import CodexTarget
from seam.installer.core import install_entry, uninstall_entry
from seam.installer.cursor import CursorTarget
from seam.installer.jsonfile import (
    atomic_write_json,
    delete_in,
    get_in,
    load_json,
    set_in,
)
from seam.installer.tomlfile import get_server_table, load_toml

# ── jsonfile leaf ────────────────────────────────────────────────────────────


def test_load_json_absent_returns_none(tmp_path: Path) -> None:
    assert load_json(tmp_path / "nope.json") is None


def test_load_json_corrupt_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{ not json ")
    assert load_json(p) is None


def test_atomic_write_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "c.json"
    atomic_write_json(p, {"a": {"b": 1}})
    assert json.loads(p.read_text()) == {"a": {"b": 1}}


def test_nested_get_set_delete() -> None:
    data: dict = {}
    set_in(data, ["x", "y", "z"], 5)
    assert get_in(data, ["x", "y", "z"]) == 5
    assert get_in(data, ["x", "missing"]) is None
    assert delete_in(data, ["x", "y", "z"]) is True
    assert delete_in(data, ["x", "y", "z"]) is False


# ── core JSON install/uninstall ───────────────────────────────────────────────

_KEY = ["mcpServers", "seam"]
_ENTRY = {"type": "stdio", "command": "/abs/seam", "args": ["start", "/repo"]}


def test_install_creates_file(tmp_path: Path) -> None:
    p = tmp_path / ".mcp.json"
    res = install_entry(p, _KEY, _ENTRY)
    assert res.action == "created"
    assert load_json(p)["mcpServers"]["seam"] == _ENTRY


def test_install_preserves_other_servers(tmp_path: Path) -> None:
    p = tmp_path / ".mcp.json"
    atomic_write_json(p, {"mcpServers": {"other": {"command": "x", "args": []}}})
    res = install_entry(p, _KEY, _ENTRY)
    assert res.action == "updated"
    data = load_json(p)
    assert data["mcpServers"]["other"] == {"command": "x", "args": []}
    assert data["mcpServers"]["seam"] == _ENTRY


def test_install_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / ".mcp.json"
    install_entry(p, _KEY, _ENTRY)
    mtime = p.stat().st_mtime_ns
    res = install_entry(p, _KEY, _ENTRY)
    assert res.action == "unchanged"
    assert p.stat().st_mtime_ns == mtime  # no rewrite


def test_install_backs_up_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / ".mcp.json"
    p.write_text("{ broken")
    res = install_entry(p, _KEY, _ENTRY)
    assert res.backed_up is True
    assert (tmp_path / ".mcp.json.backup").read_text() == "{ broken"
    assert load_json(p)["mcpServers"]["seam"] == _ENTRY


def test_uninstall_removes_and_reports_not_present(tmp_path: Path) -> None:
    p = tmp_path / ".mcp.json"
    install_entry(p, _KEY, _ENTRY)
    assert uninstall_entry(p, _KEY).action == "removed"
    assert uninstall_entry(p, _KEY).action == "not_present"
    assert load_json(p).get("mcpServers") == {}


# ── Claude target ─────────────────────────────────────────────────────────────


def test_claude_project_path_and_entry(tmp_path: Path) -> None:
    t = ClaudeTarget()
    res = t.install(tmp_path, "project", "/abs/seam", ["start", str(tmp_path)])
    cfg = tmp_path / ".mcp.json"
    assert res.action == "created"
    entry = json.loads(cfg.read_text())["mcpServers"]["seam"]
    assert entry["type"] == "stdio"  # Claude requires type
    assert entry["command"] == "/abs/seam"
    assert entry["args"] == ["start", str(tmp_path)]


def test_claude_user_scope_nests_under_projects(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    t = ClaudeTarget()
    t.install(root, "user", "/abs/seam", ["start", str(root)])
    data = json.loads((tmp_path / ".claude.json").read_text())
    assert data["projects"][str(root)]["mcpServers"]["seam"]["command"] == "/abs/seam"


# ── Cursor target ─────────────────────────────────────────────────────────────


def test_cursor_entry_has_no_type(tmp_path: Path) -> None:
    t = CursorTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    entry = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["seam"]
    assert "type" not in entry
    assert entry == {"command": "/abs/seam", "args": ["start", "/r"]}


# ── Codex target (TOML) ───────────────────────────────────────────────────────


def test_codex_writes_mcp_servers_table(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    t = CodexTarget()
    res = t.install(tmp_path, "user", "seam", ["start", "/r"])
    assert res.action == "created"
    cfg = tmp_path / ".codex" / "config.toml"
    doc = load_toml(cfg)
    assert get_server_table(doc, "seam") == {"command": "seam", "args": ["start", "/r"]}


def test_codex_preserves_existing_content_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('# my config\nmodel = "o3"\n\n[mcp_servers.other]\ncommand = "x"\nargs = []\n')
    t = CodexTarget()
    t.install(tmp_path, "user", "seam", ["start", "/r"])

    text = cfg.read_text()
    assert "# my config" in text  # comment preserved
    assert 'model = "o3"' in text  # other keys preserved
    doc = load_toml(cfg)
    assert get_server_table(doc, "other") == {"command": "x", "args": []}

    res = t.install(tmp_path, "user", "seam", ["start", "/r"])  # second run
    assert res.action == "unchanged"


def test_codex_uninstall(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    t = CodexTarget()
    t.install(tmp_path, "user", "seam", ["start", "/r"])
    assert t.uninstall(tmp_path, "user").action == "removed"
    assert t.uninstall(tmp_path, "user").action == "not_present"


def test_codex_supports_user_only() -> None:
    assert CodexTarget().supported_locations() == ["user"]


# ── registry + command resolution ─────────────────────────────────────────────


def test_get_target_unknown_returns_none() -> None:
    assert get_target("emacs") is None
    assert get_target("claude") is not None


def test_resolve_seam_command_returns_tuple() -> None:
    cmd, found = resolve_seam_command()
    assert isinstance(cmd, str) and cmd
    assert isinstance(found, bool)
