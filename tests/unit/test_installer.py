"""Unit tests for the `seam install` installer modules.

WHY isolation matters: these tests must NEVER touch the developer's real
~/.claude.json, ~/.cursor/mcp.json, or ~/.codex/config.toml. User-scope tests
monkeypatch HOME to a tmp dir; project-scope tests use tmp_path. All assertions
are on external behavior (file contents / InstallResult), not internals.
"""

import json
from pathlib import Path

import yaml

from seam.installer import get_target, guide, resolve_seam_command
from seam.installer.claude import ClaudeTarget
from seam.installer.codex import CodexTarget
from seam.installer.core import install_entry, uninstall_entry
from seam.installer.cursor import CursorTarget
from seam.installer.gemini import GeminiTarget
from seam.installer.jsonfile import (
    atomic_write_json,
    delete_in,
    get_in,
    load_json,
    set_in,
)
from seam.installer.markdownfile import (
    read_text,
    remove_block,
    remove_file,
    upsert_block,
    write_file,
)
from seam.installer.tomlfile import get_server_table, load_toml
from seam.installer.vscode import VscodeTarget

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


# ── markdownfile leaf: owned files ────────────────────────────────────────────


def test_write_file_created_then_unchanged_then_updated(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "SKILL.md"
    assert write_file(p, "hello") == "created"
    assert p.read_text() == "hello\n"  # trailing newline normalised
    assert write_file(p, "hello") == "unchanged"  # idempotent, no churn
    assert write_file(p, "world") == "updated"
    assert p.read_text() == "world\n"


def test_remove_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "f.md"
    write_file(p, "x")
    assert remove_file(p) == "removed"
    assert remove_file(p) == "not_present"


# ── markdownfile leaf: shared-file marker blocks ──────────────────────────────


def test_upsert_block_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "AGENTS.md"
    assert upsert_block(p, "guide body", marker="seam") == "created"
    text = p.read_text()
    assert "<!-- seam:start -->" in text and "<!-- seam:end -->" in text
    assert "guide body" in text


def test_upsert_block_preserves_foreign_content_and_replaces_in_place(tmp_path: Path) -> None:
    p = tmp_path / "AGENTS.md"
    p.write_text("# My project rules\n\nDo the thing.\n")
    assert upsert_block(p, "v1", marker="seam") == "updated"
    text = p.read_text()
    assert "# My project rules" in text  # foreign content preserved
    assert "Do the thing." in text

    assert upsert_block(p, "v1", marker="seam") == "unchanged"  # idempotent
    assert upsert_block(p, "v2", marker="seam") == "updated"  # content swapped
    text2 = p.read_text()
    assert "v2" in text2 and "v1" not in text2
    # exactly one block — never duplicated
    assert text2.count("<!-- seam:start -->") == 1
    assert "# My project rules" in text2  # still preserved after replace


def test_remove_block_preserves_foreign_content(tmp_path: Path) -> None:
    p = tmp_path / "AGENTS.md"
    p.write_text("# Mine\n")
    upsert_block(p, "seam stuff", marker="seam")
    assert remove_block(p, marker="seam") == "removed"
    text = p.read_text()
    assert "# Mine" in text
    assert "seam:start" not in text and "seam stuff" not in text
    assert remove_block(p, marker="seam") == "not_present"


def test_remove_block_absent_file_is_not_present(tmp_path: Path) -> None:
    assert remove_block(tmp_path / "nope.md", marker="seam") == "not_present"


def test_read_text_absent_returns_none(tmp_path: Path) -> None:
    assert read_text(tmp_path / "nope.md") is None


# ── guide renderers ───────────────────────────────────────────────────────────


def test_render_skill_has_valid_yaml_frontmatter_and_body() -> None:
    skill = guide.render_skill()
    assert skill.startswith("---\n")
    fm = yaml.safe_load(skill.split("---")[1])
    assert fm["name"] == "seam"
    assert "seam" in fm["description"] and fm["description"]
    assert fm["when_to_use"]
    assert "Escalation ladder" in skill  # the body is included


def test_render_cursor_rule_is_agent_requested() -> None:
    mdc = guide.render_cursor_rule()
    fm = yaml.safe_load(mdc.split("---")[1])
    assert fm["alwaysApply"] is False  # progressive, not always-applied
    assert fm["description"]
    assert fm["globs"] is None  # empty globs → description-surfaced
    assert "Escalation ladder" in mdc


def test_render_codex_block_is_the_full_guide_and_hook_is_thin() -> None:
    body = guide.render_codex_block()
    assert "Escalation ladder" in body  # codex gets the full guide
    hook = guide.render_claude_hook()
    assert "Escalation ladder" not in hook  # the CLAUDE.md hook is the thin pointer
    assert "seam" in hook and "skill" in hook


# ── target guidance methods ───────────────────────────────────────────────────


def test_claude_guidance_writes_skill_and_claude_md(tmp_path: Path) -> None:
    res = ClaudeTarget().install_guidance(tmp_path)
    assert [r.action for r in res] == ["created", "created"]
    skill = tmp_path / ".claude" / "skills" / "seam" / "SKILL.md"
    assert "name: seam" in skill.read_text()
    assert "<!-- seam:start -->" in (tmp_path / "CLAUDE.md").read_text()


def test_claude_guidance_preserves_existing_claude_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Project rules\n\nUse tabs.\n")
    ClaudeTarget().install_guidance(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "Use tabs." in text  # foreign content preserved
    assert "<!-- seam:start -->" in text


def test_claude_guidance_uninstall_removes_skill_dir(tmp_path: Path) -> None:
    t = ClaudeTarget()
    t.install_guidance(tmp_path)
    res = t.uninstall_guidance(tmp_path)
    assert [r.action for r in res] == ["removed", "removed"]
    assert not (tmp_path / ".claude" / "skills" / "seam").exists()  # empty dir tidied


def test_cursor_guidance_writes_mdc_rule(tmp_path: Path) -> None:
    res = CursorTarget().install_guidance(tmp_path)
    assert res[0].action == "created"
    rule = tmp_path / ".cursor" / "rules" / "seam.mdc"
    assert rule.exists()
    assert "alwaysApply: false" in rule.read_text()
    assert CursorTarget().uninstall_guidance(tmp_path)[0].action == "removed"


def test_codex_guidance_writes_agents_md_block(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Mine\n")
    res = CodexTarget().install_guidance(tmp_path)
    assert res[0].action == "updated"  # appended to existing file
    text = (tmp_path / "AGENTS.md").read_text()
    assert "# Mine" in text  # preserved
    assert "Escalation ladder" in text  # full guide inline
    assert CodexTarget().uninstall_guidance(tmp_path)[0].action == "removed"


def test_guidance_is_project_scoped_and_idempotent(tmp_path: Path) -> None:
    # Guidance ignores location — it always lives in the repo; second run = no-op.
    CursorTarget().install_guidance(tmp_path)
    assert CursorTarget().install_guidance(tmp_path)[0].action == "unchanged"


# ── VS Code target ────────────────────────────────────────────────────────────


def test_vscode_project_config_path(tmp_path: Path) -> None:
    t = VscodeTarget()
    assert t.config_path(tmp_path, "project") == tmp_path / ".vscode" / "mcp.json"


def test_vscode_supports_project_only() -> None:
    assert VscodeTarget().supported_locations() == ["project"]


def test_vscode_install_created(tmp_path: Path) -> None:
    t = VscodeTarget()
    res = t.install(tmp_path, "project", "/abs/seam", ["start", str(tmp_path)])
    assert res.action == "created"
    cfg = tmp_path / ".vscode" / "mcp.json"
    entry = json.loads(cfg.read_text())["servers"]["seam"]
    # VS Code requires "type": "stdio" and uses "servers" (not "mcpServers").
    assert entry["type"] == "stdio"
    assert entry["command"] == "/abs/seam"
    assert entry["args"] == ["start", str(tmp_path)]


def test_vscode_install_is_idempotent(tmp_path: Path) -> None:
    t = VscodeTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    res = t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    assert res.action == "unchanged"


def test_vscode_install_updated_on_change(tmp_path: Path) -> None:
    t = VscodeTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    res = t.install(tmp_path, "project", "/abs/seam2", ["start", "/r"])
    assert res.action == "updated"


def test_vscode_uninstall_removed_then_not_present(tmp_path: Path) -> None:
    t = VscodeTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    assert t.uninstall(tmp_path, "project").action == "removed"
    assert t.uninstall(tmp_path, "project").action == "not_present"
    # Uninstall leaves {"servers": {}} — the empty parent dict persists.
    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert data.get("servers") == {}


def test_vscode_corrupt_config_backup(tmp_path: Path) -> None:
    cfg = tmp_path / ".vscode" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ broken json")
    t = VscodeTarget()
    res = t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    assert res.backed_up is True
    assert (tmp_path / ".vscode" / "mcp.json.backup").read_text() == "{ broken json"


def test_vscode_render_entry_uses_servers_key(tmp_path: Path) -> None:
    t = VscodeTarget()
    rendered = json.loads(t.render_entry("/abs/seam", ["start", "/r"]))
    # VS Code uses "servers", NOT "mcpServers" — critical shape difference.
    assert "servers" in rendered
    assert "mcpServers" not in rendered
    entry = rendered["servers"]["seam"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "/abs/seam"


def test_vscode_guidance_writes_copilot_instructions(tmp_path: Path) -> None:
    t = VscodeTarget()
    res = t.install_guidance(tmp_path)
    assert res[0].action == "created"
    copilot_md = tmp_path / ".github" / "copilot-instructions.md"
    assert copilot_md.exists()
    text = copilot_md.read_text()
    assert "<!-- seam:start -->" in text
    assert "Escalation ladder" in text  # guide body is included


def test_vscode_guidance_is_idempotent(tmp_path: Path) -> None:
    t = VscodeTarget()
    t.install_guidance(tmp_path)
    res = t.install_guidance(tmp_path)
    assert res[0].action == "unchanged"


def test_vscode_guidance_preserves_foreign_content(tmp_path: Path) -> None:
    copilot_md = tmp_path / ".github" / "copilot-instructions.md"
    copilot_md.parent.mkdir(parents=True)
    copilot_md.write_text("# Project conventions\n\nAlways write tests.\n")
    t = VscodeTarget()
    t.install_guidance(tmp_path)
    text = copilot_md.read_text()
    assert "Always write tests." in text  # foreign content preserved
    assert "Escalation ladder" in text  # seam block appended


def test_vscode_guidance_uninstall_removes_block(tmp_path: Path) -> None:
    t = VscodeTarget()
    t.install_guidance(tmp_path)
    res = t.uninstall_guidance(tmp_path)
    assert res[0].action == "removed"
    # File persists (empty after block removal — shared-file residue).
    copilot_md = tmp_path / ".github" / "copilot-instructions.md"
    assert copilot_md.exists()
    assert "<!-- seam:start -->" not in copilot_md.read_text()


def test_vscode_guidance_previews(tmp_path: Path) -> None:
    previews = VscodeTarget().guidance_previews(tmp_path)
    assert len(previews) == 1
    path_str, content = previews[0]
    assert path_str == str(tmp_path / ".github" / "copilot-instructions.md")
    assert "<!-- seam:start -->" in content
    assert "Escalation ladder" in content


# ── Gemini CLI target ──────────────────────────────────────────────────────────


def test_gemini_project_config_path(tmp_path: Path) -> None:
    t = GeminiTarget()
    assert t.config_path(tmp_path, "project") == tmp_path / ".gemini" / "settings.json"


def test_gemini_user_config_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    t = GeminiTarget()
    assert t.config_path(tmp_path, "user") == Path.home() / ".gemini" / "settings.json"


def test_gemini_supports_project_and_user() -> None:
    assert GeminiTarget().supported_locations() == ["project", "user"]


def test_gemini_install_created(tmp_path: Path) -> None:
    t = GeminiTarget()
    res = t.install(tmp_path, "project", "/abs/seam", ["start", str(tmp_path)])
    assert res.action == "created"
    cfg = tmp_path / ".gemini" / "settings.json"
    entry = json.loads(cfg.read_text())["mcpServers"]["seam"]
    # Gemini uses command/args only — no "type" field (same as Cursor).
    assert "type" not in entry
    assert entry["command"] == "/abs/seam"
    assert entry["args"] == ["start", str(tmp_path)]


def test_gemini_install_is_idempotent(tmp_path: Path) -> None:
    t = GeminiTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    res = t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    assert res.action == "unchanged"


def test_gemini_install_updated_on_change(tmp_path: Path) -> None:
    t = GeminiTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    res = t.install(tmp_path, "project", "/abs/seam2", ["start", "/r"])
    assert res.action == "updated"


def test_gemini_uninstall_removed_then_not_present(tmp_path: Path) -> None:
    t = GeminiTarget()
    t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    assert t.uninstall(tmp_path, "project").action == "removed"
    assert t.uninstall(tmp_path, "project").action == "not_present"
    # Uninstall leaves {"mcpServers": {}} — the empty parent dict persists.
    data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
    assert data.get("mcpServers") == {}


def test_gemini_corrupt_config_backup(tmp_path: Path) -> None:
    cfg = tmp_path / ".gemini" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ broken json")
    t = GeminiTarget()
    res = t.install(tmp_path, "project", "/abs/seam", ["start", "/r"])
    assert res.backed_up is True
    assert (tmp_path / ".gemini" / "settings.json.backup").read_text() == "{ broken json"


def test_gemini_render_entry_uses_mcp_servers_key(tmp_path: Path) -> None:
    t = GeminiTarget()
    rendered = json.loads(t.render_entry("/abs/seam", ["start", "/r"]))
    # Gemini uses "mcpServers" — consistent with Cursor, not VS Code's "servers".
    assert "mcpServers" in rendered
    assert "servers" not in rendered
    entry = rendered["mcpServers"]["seam"]
    assert "type" not in entry
    assert entry["command"] == "/abs/seam"


def test_gemini_guidance_writes_gemini_md(tmp_path: Path) -> None:
    t = GeminiTarget()
    res = t.install_guidance(tmp_path)
    assert res[0].action == "created"
    gemini_md = tmp_path / "GEMINI.md"
    assert gemini_md.exists()
    text = gemini_md.read_text()
    assert "<!-- seam:start -->" in text
    assert "Escalation ladder" in text  # guide body is included


def test_gemini_guidance_is_idempotent(tmp_path: Path) -> None:
    t = GeminiTarget()
    t.install_guidance(tmp_path)
    res = t.install_guidance(tmp_path)
    assert res[0].action == "unchanged"


def test_gemini_guidance_preserves_foreign_content(tmp_path: Path) -> None:
    gemini_md = tmp_path / "GEMINI.md"
    gemini_md.write_text("# My Gemini config\n\nCustom notes here.\n")
    t = GeminiTarget()
    t.install_guidance(tmp_path)
    text = gemini_md.read_text()
    assert "Custom notes here." in text  # foreign content preserved
    assert "Escalation ladder" in text  # seam block appended


def test_gemini_guidance_uninstall_removes_block(tmp_path: Path) -> None:
    t = GeminiTarget()
    t.install_guidance(tmp_path)
    res = t.uninstall_guidance(tmp_path)
    assert res[0].action == "removed"
    # File persists (empty after block removal — shared-file residue).
    gemini_md = tmp_path / "GEMINI.md"
    assert gemini_md.exists()
    assert "<!-- seam:start -->" not in gemini_md.read_text()


def test_gemini_guidance_previews(tmp_path: Path) -> None:
    previews = GeminiTarget().guidance_previews(tmp_path)
    assert len(previews) == 1
    path_str, content = previews[0]
    assert path_str == str(tmp_path / "GEMINI.md")
    assert "<!-- seam:start -->" in content
    assert "Escalation ladder" in content
