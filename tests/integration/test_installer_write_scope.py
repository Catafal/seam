"""Write-scope audit for `seam install` / `seam uninstall` (P5.3 S2 + S3).

WHY this file exists:
  Verifies that every invocation of `seam install` / `seam uninstall` writes
  ONLY the expected files and NEVER touches anything outside <root>∪<home>.
  Uses filesystem snapshot diff (FsChanges) rather than named-file assertions so
  any stray write — even to a temp file — is caught by the guard.

HOW isolation works:
  - root = tmp_path/"repo"    — project root passed to `seam install`
  - home = tmp_path/"home"    — fake $HOME (monkeypatched via HOME env var)
  - sibling = tmp_path/"sibling" — decoy dir with a canary file
  snapshot([tmp_path]) wraps all three; diff(before, after) catches any stray write.

S2 — install-side write-scope audit (issue #201)
S3 — uninstall-side round-trip + foreign-content preservation (issue #202)

EMPIRICALLY DISCOVERED UNINSTALL RESIDUE (benign; encoded in S3 tests):
  All residue is empty containers left behind because the installer removes its
  content but never prunes the parent file/structure:

  - claude guidance-only:
      root/CLAUDE.md  (empty — block removed, empty file persists)

  - claude project-mcp:
      root/CLAUDE.md  (empty)
      root/.mcp.json  (content: {"mcpServers": {}})

  - claude user-mcp:
      root/CLAUDE.md  (empty)
      home/.claude.json  (content: {"projects": {"<root>": {"mcpServers": {}}}})

  - cursor guidance-only:
      (no residue — seam.mdc is an owned file, fully removed)

  - cursor project-mcp:
      root/.cursor/mcp.json  (content: {"mcpServers": {}})

  - cursor user-mcp:
      home/.cursor/mcp.json  (content: {"mcpServers": {}})

  - codex guidance-only:
      root/AGENTS.md  (empty — block removed, empty file persists)

  - codex user-mcp:
      root/AGENTS.md  (empty)
      home/.codex/config.toml  (empty string — table deleted)

  Empty parent directories (.claude/, .claude/skills/, .cursor/, .cursor/rules/)
  also remain, but snapshot() only tracks files — directories are NOT flagged.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from seam.cli.main import app
from tests.support.fs_audit import FsChanges, diff, snapshot

runner = CliRunner()

# Canary bytes written to sibling/canary.txt — must be unchanged after every op.
_CANARY_BYTES = b"DO NOT TOUCH THIS FILE - installer write-scope canary"


# ── Setup helpers ─────────────────────────────────────────────────────────────


def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create root, home, sibling dirs + plant the canary file.

    Returns (root, home, sibling, canary_path).
    """
    root = tmp_path / "repo"
    home = tmp_path / "home"
    sibling = tmp_path / "sibling"
    root.mkdir()
    home.mkdir()
    sibling.mkdir()
    canary = sibling / "canary.txt"
    canary.write_bytes(_CANARY_BYTES)
    return root, home, sibling, canary


def _invoke_and_diff(tmp_path: Path, cli_args: list[str]) -> tuple[Result, FsChanges]:
    """Snapshot tmp_path-wide, run CLI, snapshot again, return (result, changes)."""
    before = snapshot([tmp_path])
    result = runner.invoke(app, cli_args)
    after = snapshot([tmp_path])
    return result, diff(before, after)


# ── Shared assertion helpers ───────────────────────────────────────────────────


def _assert_safe(
    changes: FsChanges,
    root: Path,
    home: Path,
    canary: Path,
) -> None:
    """Sensitive-path guard + canary check.

    Any write OUTSIDE root∪home fails the test immediately — this is the primary
    safety property.  Canary bytes must be byte-identical after every operation.
    """
    all_touched = changes.created | changes.modified | changes.deleted
    for p in all_touched:
        ppath = Path(p)
        in_scope = ppath.is_relative_to(root) or ppath.is_relative_to(home)
        assert in_scope, f"STRAY WRITE outside root∪home detected: {p}"

    assert canary.read_bytes() == _CANARY_BYTES, "canary file was touched by the installer!"


def _assert_no_tmp(tmp_path: Path) -> None:
    """Assert no *.tmp files leaked under tmp_path (atomic-write cleanup check)."""
    leaked = list(tmp_path.rglob("*.tmp"))
    assert leaked == [], f"Leaked .tmp file(s): {leaked}"


def _abs_root(root: Path, *rel: str) -> str:
    """Absolute string path for a relative path under root."""
    return str(root / Path(*rel))


def _abs_home(home: Path, *rel: str) -> str:
    """Absolute string path for a relative path under home."""
    return str(home / Path(*rel))


# ═══════════════════════════════════════════════════════════════════════════════
# S2 — INSTALL-SIDE write-scope audit (issue #201)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Guidance-only (no --with-mcp) parametrized over all three targets ─────────

_GUIDANCE_CASES: list[tuple[str, set[str]]] = [
    # (target, expected root-relative paths)
    ("claude", {".claude/skills/seam/SKILL.md", "CLAUDE.md"}),
    ("cursor", {".cursor/rules/seam.mdc"}),
    ("codex", {"AGENTS.md"}),
    ("vscode", {".github/copilot-instructions.md"}),
    ("gemini", {"GEMINI.md"}),
    ("zed", {"AGENTS.md"}),
]


@pytest.mark.parametrize("target,root_rel", _GUIDANCE_CASES)
def test_guidance_only_writes_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    root_rel: set[str],
) -> None:
    """Guidance-only install creates EXACTLY the expected root files, nothing else."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    _, changes = _invoke_and_diff(tmp_path, ["install", str(root), "--target", target])

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)

    expected = {_abs_root(root, p) for p in root_rel}
    assert changes.created == expected, f"Unexpected write-set for {target} guidance"
    assert changes.modified == set()
    assert changes.deleted == set()


# ── --with-mcp parametrized over {target × location} ─────────────────────────

_WITH_MCP_CASES: list[tuple[str, str, set[str], set[str]]] = [
    # (target, location, expected root-relative, expected home-relative)
    (
        "claude",
        "project",
        {".claude/skills/seam/SKILL.md", "CLAUDE.md", ".mcp.json"},
        set(),
    ),
    (
        "claude",
        "user",
        {".claude/skills/seam/SKILL.md", "CLAUDE.md"},
        {".claude.json"},
    ),
    (
        "cursor",
        "project",
        {".cursor/rules/seam.mdc", ".cursor/mcp.json"},
        set(),
    ),
    (
        "cursor",
        "user",
        {".cursor/rules/seam.mdc"},
        {".cursor/mcp.json"},
    ),
    (
        "codex",
        "user",
        {"AGENTS.md"},
        {".codex/config.toml"},
    ),
    (
        "vscode",
        "project",
        {".github/copilot-instructions.md", ".vscode/mcp.json"},
        set(),
    ),
    (
        "gemini",
        "project",
        {"GEMINI.md", ".gemini/settings.json"},
        set(),
    ),
    (
        "gemini",
        "user",
        {"GEMINI.md"},
        {".gemini/settings.json"},
    ),
    (
        "zed",
        "project",
        {"AGENTS.md", ".zed/settings.json"},
        set(),
    ),
    (
        "zed",
        "user",
        {"AGENTS.md"},
        {".config/zed/settings.json"},
    ),
]


@pytest.mark.parametrize("target,location,root_rel,home_rel", _WITH_MCP_CASES)
def test_with_mcp_writes_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    location: str,
    root_rel: set[str],
    home_rel: set[str],
) -> None:
    """--with-mcp install creates EXACTLY guidance + one MCP config, nothing else."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    _, changes = _invoke_and_diff(
        tmp_path,
        ["install", str(root), "--target", target, "--with-mcp", "--location", location],
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)

    expected = {_abs_root(root, p) for p in root_rel} | {_abs_home(home, p) for p in home_rel}
    assert changes.created == expected, (
        f"Unexpected write-set for {target}/{location} --with-mcp"
    )
    assert changes.modified == set()
    assert changes.deleted == set()


# ── --target all: guidance union of three targets ─────────────────────────────


def test_target_all_guidance_writes_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--target all guidance creates the union of all three targets' guidance files."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    _, changes = _invoke_and_diff(
        tmp_path, ["install", str(root), "--target", "all"]
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)

    expected = {
        _abs_root(root, ".claude/skills/seam/SKILL.md"),
        _abs_root(root, "CLAUDE.md"),
        _abs_root(root, ".cursor/rules/seam.mdc"),
        _abs_root(root, "AGENTS.md"),
        _abs_root(root, ".github/copilot-instructions.md"),
        _abs_root(root, "GEMINI.md"),
    }
    assert changes.created == expected
    assert changes.modified == set()
    assert changes.deleted == set()


def test_target_all_with_mcp_user_writes_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--target all --with-mcp --location user creates guidance + all three user MCP configs."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    _, changes = _invoke_and_diff(
        tmp_path,
        ["install", str(root), "--target", "all", "--with-mcp", "--location", "user"],
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)

    expected = {
        _abs_root(root, ".claude/skills/seam/SKILL.md"),
        _abs_root(root, "CLAUDE.md"),
        _abs_home(home, ".claude.json"),
        _abs_root(root, ".cursor/rules/seam.mdc"),
        _abs_home(home, ".cursor/mcp.json"),
        _abs_root(root, "AGENTS.md"),
        _abs_home(home, ".codex/config.toml"),
        # vscode: project-only; user location is not supported → MCP skipped;
        # guidance still written to root.
        _abs_root(root, ".github/copilot-instructions.md"),
        # gemini: supports user scope → MCP written to ~/.gemini/settings.json.
        _abs_root(root, "GEMINI.md"),
        _abs_home(home, ".gemini/settings.json"),
        # zed: supports user scope → MCP written to ~/.config/zed/settings.json.
        # Guidance writes to AGENTS.md (same file as codex — already counted above).
        _abs_home(home, ".config/zed/settings.json"),
    }
    assert changes.created == expected
    assert changes.modified == set()
    assert changes.deleted == set()


# ── Idempotent second run produces zero mutations ─────────────────────────────


@pytest.mark.parametrize("target", ["claude", "cursor", "codex", "vscode", "gemini", "zed"])
def test_idempotent_second_install_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A second identical guidance install touches nothing on disk."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # First install (populates files)
    runner.invoke(app, ["install", str(root), "--target", target])

    # Second install — must produce zero mutations
    _, changes = _invoke_and_diff(tmp_path, ["install", str(root), "--target", target])

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), (
        f"Second install for {target} caused unexpected mutations: {changes}"
    )


# ── Corrupt pre-existing config: backup created ───────────────────────────────


def test_corrupt_config_creates_backup_and_normal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the target config file is corrupt, a .backup copy is made alongside normal writes."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # Seed .mcp.json with unparseable bytes before install
    mcp_json = root / ".mcp.json"
    mcp_json.write_bytes(b"!!!not valid json!!!")

    _, changes = _invoke_and_diff(
        tmp_path, ["install", str(root), "--target", "claude", "--with-mcp"]
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)

    backup = root / ".mcp.json.backup"
    # Backup of the corrupt file must be among newly created files
    assert str(backup) in changes.created, "Expected .mcp.json.backup in created set"
    # Guidance files also created
    assert _abs_root(root, ".claude/skills/seam/SKILL.md") in changes.created
    assert _abs_root(root, "CLAUDE.md") in changes.created
    # The original corrupt file was overwritten (modified, not created)
    assert str(mcp_json) in changes.modified
    # No surprise paths outside the expected set
    expected_created = {
        _abs_root(root, ".claude/skills/seam/SKILL.md"),
        _abs_root(root, "CLAUDE.md"),
        str(backup),
    }
    assert changes.created == expected_created


# ── --print-config (preview) mutates nothing ─────────────────────────────────


def test_print_config_guidance_zero_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--print-config guidance preview writes absolutely nothing to disk."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    _, changes = _invoke_and_diff(
        tmp_path, ["install", str(root), "--target", "all", "--print-config"]
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), (
        f"--print-config wrote files: {changes}"
    )


def test_print_config_with_mcp_zero_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--print-config --with-mcp preview writes absolutely nothing to disk."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    _, changes = _invoke_and_diff(
        tmp_path,
        ["install", str(root), "--target", "all", "--with-mcp", "--print-config"],
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), (
        f"--print-config --with-mcp wrote files: {changes}"
    )


def test_auto_print_config_zero_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--auto --print-config is an installer plan only; it must not write files."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    result, changes = _invoke_and_diff(
        tmp_path, ["install", str(root), "--auto", "--print-config"]
    )

    assert result.exit_code == 0
    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), f"--auto --print-config wrote files: {changes}"


def test_auto_print_config_with_mcp_zero_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--auto --print-config --with-mcp must only report MCP paths/config."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    result, changes = _invoke_and_diff(
        tmp_path,
        ["install", str(root), "--auto", "--print-config", "--with-mcp"],
    )

    assert result.exit_code == 0
    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), (
        f"--auto --print-config --with-mcp wrote files: {changes}"
    )


def test_auto_without_print_config_zero_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed auto mode should reject before any installer write path runs."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    result, changes = _invoke_and_diff(tmp_path, ["install", str(root), "--auto"])

    assert result.exit_code == 1
    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), f"--auto failure wrote files: {changes}"


# ── Corrupt pre-existing vscode MCP config: backup created ───────────────────


def test_vscode_corrupt_config_creates_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When .vscode/mcp.json is corrupt, a .backup copy is made alongside normal writes."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # Seed .vscode/mcp.json with unparseable bytes before install
    vscode_dir = root / ".vscode"
    vscode_dir.mkdir(parents=True)
    vscode_mcp = vscode_dir / "mcp.json"
    vscode_mcp.write_bytes(b"!!!not valid json!!!")

    _, changes = _invoke_and_diff(
        tmp_path,
        ["install", str(root), "--target", "vscode", "--with-mcp"],
    )

    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)

    backup = vscode_dir / "mcp.json.backup"
    # Backup of the corrupt file must be among newly created files
    assert str(backup) in changes.created, "Expected .vscode/mcp.json.backup in created set"
    # Guidance file also created
    assert _abs_root(root, ".github/copilot-instructions.md") in changes.created
    # The original corrupt file was overwritten (modified, not created)
    assert str(vscode_mcp) in changes.modified


# ── vscode + user + --with-mcp: exit 1, zero writes ──────────────────────────


def test_vscode_user_with_mcp_rejected_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vscode --location user --with-mcp is invalid (project-only); exits 1 with no writes."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    result, changes = _invoke_and_diff(
        tmp_path,
        [
            "install",
            str(root),
            "--target",
            "vscode",
            "--location",
            "user",
            "--with-mcp",
        ],
    )

    assert result.exit_code == 1, "Expected exit code 1 for vscode user --with-mcp"
    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), (
        f"vscode user --with-mcp should write nothing but wrote: {changes}"
    )


# ── codex + project + --with-mcp: exit 1, zero writes ─────────────────────────


def test_codex_project_with_mcp_rejected_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex --location project --with-mcp is invalid; exits 1 with no writes."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    result, changes = _invoke_and_diff(
        tmp_path,
        [
            "install",
            str(root),
            "--target",
            "codex",
            "--location",
            "project",
            "--with-mcp",
        ],
    )

    assert result.exit_code == 1, "Expected exit code 1 for codex project --with-mcp"
    _assert_safe(changes, root, home, canary)
    _assert_no_tmp(tmp_path)
    assert changes == FsChanges(set(), set(), set()), (
        f"codex project --with-mcp should write nothing but wrote: {changes}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S3 — UNINSTALL-SIDE round-trip + foreign-content preservation (issue #202)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Round-trip: post-uninstall state equals pre-install state + known residue ─

# Residue map: (target, location, with_mcp) → (root-relative residue, home-relative residue)
# All residue is empty containers (empty file / empty JSON structure) left by the installer.
# WHY containers survive: the installer removes only ITS block/entry from shared files
# (CLAUDE.md, AGENTS.md, .mcp.json, .claude.json, config.toml) to preserve any foreign
# content, but it does not delete the now-empty file.
# Empty directories (.claude/, .cursor/ etc.) also persist but snapshot() only tracks files.
_ROUND_TRIP_CASES: list[tuple[str, str, bool, set[str], set[str]]] = [
    # claude guidance-only: empty CLAUDE.md persists
    ("claude", "project", False, {"CLAUDE.md"}, set()),
    # claude project-mcp: empty .mcp.json + empty CLAUDE.md persist
    ("claude", "project", True, {"CLAUDE.md", ".mcp.json"}, set()),
    # claude user-mcp: empty CLAUDE.md in root + .claude.json with empty structure in home
    ("claude", "user", True, {"CLAUDE.md"}, {".claude.json"}),
    # cursor guidance-only: ZERO residue — seam.mdc is an OWNED file (installer creates +
    # fully controls it), so uninstall deletes it entirely rather than emptying a shared file.
    ("cursor", "project", False, set(), set()),
    # cursor project-mcp: empty .cursor/mcp.json persists
    ("cursor", "project", True, {".cursor/mcp.json"}, set()),
    # cursor user-mcp: empty .cursor/mcp.json in home persists
    ("cursor", "user", True, set(), {".cursor/mcp.json"}),
    # codex guidance-only: empty AGENTS.md persists
    ("codex", "user", False, {"AGENTS.md"}, set()),
    # codex user-mcp: empty AGENTS.md + empty config.toml persist
    ("codex", "user", True, {"AGENTS.md"}, {".codex/config.toml"}),
    # vscode guidance-only: empty copilot-instructions.md persists (shared-file block removed)
    ("vscode", "project", False, {".github/copilot-instructions.md"}, set()),
    # vscode project-mcp: copilot-instructions.md + empty .vscode/mcp.json persist
    ("vscode", "project", True, {".github/copilot-instructions.md", ".vscode/mcp.json"}, set()),
    # gemini guidance-only: empty GEMINI.md persists (shared-file block removed)
    ("gemini", "project", False, {"GEMINI.md"}, set()),
    # gemini project-mcp: empty GEMINI.md + empty .gemini/settings.json persist
    ("gemini", "project", True, {"GEMINI.md", ".gemini/settings.json"}, set()),
    # gemini user-mcp: empty GEMINI.md in root + empty ~/.gemini/settings.json persist
    ("gemini", "user", True, {"GEMINI.md"}, {".gemini/settings.json"}),
    # zed guidance-only: empty AGENTS.md persists (shared-file block removed)
    ("zed", "project", False, {"AGENTS.md"}, set()),
    # zed project-mcp: empty AGENTS.md + empty .zed/settings.json persist
    ("zed", "project", True, {"AGENTS.md", ".zed/settings.json"}, set()),
    # zed user-mcp: empty AGENTS.md in root + empty ~/.config/zed/settings.json persist
    ("zed", "user", True, {"AGENTS.md"}, {".config/zed/settings.json"}),
]


@pytest.mark.parametrize("target,location,with_mcp,root_residue,home_residue", _ROUND_TRIP_CASES)
def test_uninstall_round_trip_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    location: str,
    with_mcp: bool,
    root_residue: set[str],
    home_residue: set[str],
) -> None:
    """Install then uninstall: filesystem diff == EXACTLY the documented benign residue.

    Residue consists of empty container files (empty JSON / empty markdown)
    that the installer leaves behind after removing its content. This is
    intentional — the installer preserves the parent structure for idempotency.
    """
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # Record filesystem state before install
    pre_install = snapshot([tmp_path])

    # Install
    install_args = ["install", str(root), "--target", target, "--location", location]
    if with_mcp:
        install_args.append("--with-mcp")
    runner.invoke(app, install_args)

    # Uninstall
    runner.invoke(app, ["uninstall", str(root), "--target", target, "--location", location])

    # Snapshot after uninstall
    post_uninstall = snapshot([tmp_path])

    # The diff from pre-install to post-uninstall IS the residue
    residue = diff(pre_install, post_uninstall)

    _assert_safe(residue, root, home, canary)
    _assert_no_tmp(tmp_path)

    # Residue must equal EXACTLY the documented benign files
    expected_residue_created = {_abs_root(root, p) for p in root_residue} | {
        _abs_home(home, p) for p in home_residue
    }
    assert residue.created == expected_residue_created, (
        f"Unexpected residue for {target}/{location} with_mcp={with_mcp}. "
        f"Got: {residue.created!r}"
    )
    # Nothing should be deleted relative to pre-install (we started clean)
    assert residue.deleted == set()
    # Residue files should only be created, not modified (nothing was there before)
    assert residue.modified == set()


# ── Foreign-content preservation (representative: one per target) ──────────────


def test_foreign_content_claude_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude: foreign CLAUDE.md prose + another project's ~/.claude.json server survive install+uninstall."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # Seed foreign CLAUDE.md with user's own prose
    foreign_prose = "# My Project\n\nSome design notes that must survive install + uninstall.\n"
    claude_md = root / "CLAUDE.md"
    claude_md.write_text(foreign_prose, encoding="utf-8")

    # Seed ~/.claude.json with another project's MCP server
    claude_json = home / ".claude.json"
    foreign_claude_data = {
        "projects": {
            "/some/other/project": {
                "mcpServers": {"other-tool": {"type": "stdio", "command": "other", "args": []}}
            }
        }
    }
    claude_json.write_text(json.dumps(foreign_claude_data, indent=2), encoding="utf-8")

    # Install then uninstall (user scope so we exercise ~/.claude.json)
    runner.invoke(
        app,
        ["install", str(root), "--target", "claude", "--with-mcp", "--location", "user"],
    )
    runner.invoke(app, ["uninstall", str(root), "--target", "claude", "--location", "user"])

    # Foreign CLAUDE.md prose must survive (block removed, prose retained)
    after_claude_md = claude_md.read_text(encoding="utf-8")
    assert "My Project" in after_claude_md, "Foreign CLAUDE.md prose was clobbered"
    assert "design notes" in after_claude_md, "Foreign CLAUDE.md prose was clobbered"
    assert "<!-- seam:start -->" not in after_claude_md, "Seam block not removed on uninstall"

    # The other project's server in ~/.claude.json must survive
    after_data = json.loads(claude_json.read_text(encoding="utf-8"))
    other_project_servers = (
        after_data.get("projects", {})
        .get("/some/other/project", {})
        .get("mcpServers", {})
    )
    assert "other-tool" in other_project_servers, (
        "Foreign ~/.claude.json server was clobbered by install+uninstall"
    )

    _assert_no_tmp(tmp_path)


def test_foreign_content_cursor_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor: a pre-existing .cursor/mcp.json server entry survives install+uninstall."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # Seed .cursor/mcp.json with an existing server entry
    cursor_mcp = root / ".cursor" / "mcp.json"
    cursor_mcp.parent.mkdir(parents=True)
    foreign_cursor_data = {
        "mcpServers": {"other-cursor-tool": {"command": "oc", "args": ["--port", "9000"]}}
    }
    cursor_mcp.write_text(json.dumps(foreign_cursor_data, indent=2), encoding="utf-8")

    # Install then uninstall (project scope MCP)
    runner.invoke(app, ["install", str(root), "--target", "cursor", "--with-mcp"])
    runner.invoke(app, ["uninstall", str(root), "--target", "cursor", "--location", "project"])

    # Foreign server must survive
    after_data = json.loads(cursor_mcp.read_text(encoding="utf-8"))
    assert "other-cursor-tool" in after_data.get("mcpServers", {}), (
        "Foreign cursor MCP server was clobbered by install+uninstall"
    )
    # Seam entry must be gone
    assert "seam" not in after_data.get("mcpServers", {}), (
        "Seam cursor MCP server was not removed by uninstall"
    )

    _assert_no_tmp(tmp_path)


def test_foreign_content_codex_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex: a pre-existing ~/.codex/config.toml server table + comment survive install+uninstall."""
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    # Seed ~/.codex/config.toml with another server + a user comment
    codex_toml = home / ".codex" / "config.toml"
    codex_toml.parent.mkdir(parents=True)
    # tomlkit-compatible TOML with a comment line that MUST survive
    foreign_toml = (
        "# My Codex config — DO NOT DELETE\n"
        "\n"
        "[mcp_servers.other-codex-tool]\n"
        'command = "oct"\n'
        "args = []\n"
    )
    codex_toml.write_text(foreign_toml, encoding="utf-8")

    # Install then uninstall (user scope)
    runner.invoke(
        app,
        ["install", str(root), "--target", "codex", "--with-mcp", "--location", "user"],
    )
    runner.invoke(app, ["uninstall", str(root), "--target", "codex", "--location", "user"])

    after_text = codex_toml.read_text(encoding="utf-8")

    # User comment must survive
    assert "DO NOT DELETE" in after_text, "User comment in config.toml was clobbered"
    # Foreign server table must survive
    assert "other-codex-tool" in after_text, (
        "Foreign codex server table was clobbered by install+uninstall"
    )
    # Seam table must be gone
    assert "[mcp_servers.seam]" not in after_text, (
        "Seam codex server table was not removed by uninstall"
    )

    _assert_no_tmp(tmp_path)


# ── Shared AGENTS.md block: Codex + Zed converge to ONE block ────────────────


def test_codex_zed_share_agents_md_block_uninstall_removes_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DOCUMENTS the Codex/Zed shared AGENTS.md trade-off and verifies the contract.

    Codex and Zed both write CLI guidance into AGENTS.md under the same
    <!-- seam:start/end --> marker. This is intentional: both agents read AGENTS.md;
    one shared block avoids duplication.

    Trade-off: uninstalling EITHER target removes the shared block (the other agent
    loses its guidance too). Escape hatch: re-run `seam install --target <other>`.

    This test encodes and verifies:
      1. install codex then zed → AGENTS.md has exactly ONE <!-- seam:start --> (no dup).
      2. uninstall zed → shared block is removed (codex guidance gone too).
      3. re-install codex guidance → block is restored (escape hatch works).
    """
    root, home, _sib, canary = _setup_dirs(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    agents_md = root / "AGENTS.md"

    # Step 1: install codex guidance, then zed guidance
    runner.invoke(app, ["install", str(root), "--target", "codex"])
    runner.invoke(app, ["install", str(root), "--target", "zed"])

    # AGENTS.md must exist with EXACTLY ONE seam:start block (no duplication)
    assert agents_md.exists(), "AGENTS.md not created after codex + zed install"
    content = agents_md.read_text(encoding="utf-8")
    assert content.count("<!-- seam:start -->") == 1, (
        "Expected exactly ONE <!-- seam:start --> in AGENTS.md after codex + zed install; "
        f"got {content.count('<!-- seam:start -->')} (duplication bug?)"
    )

    # Step 2: uninstall zed → shared block removed (documented trade-off)
    runner.invoke(app, ["uninstall", str(root), "--target", "zed"])
    content_after_uninstall = agents_md.read_text(encoding="utf-8")
    assert "<!-- seam:start -->" not in content_after_uninstall, (
        "Seam block in AGENTS.md was NOT removed after `seam uninstall --target zed` "
        "(the shared block should be gone — both codex and zed guidance removed)"
    )

    # Step 3: re-install codex guidance → block is restored (escape hatch)
    runner.invoke(app, ["install", str(root), "--target", "codex"])
    content_restored = agents_md.read_text(encoding="utf-8")
    assert "<!-- seam:start -->" in content_restored, (
        "Seam block in AGENTS.md was NOT restored after re-running `seam install --target codex`"
    )

    _assert_no_tmp(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Meta-tests: verify the guard helpers themselves are load-bearing
# ═══════════════════════════════════════════════════════════════════════════════


def test_assert_safe_raises_on_stray_write() -> None:
    """Negative meta-test: _assert_safe raises AssertionError for a path outside root∪home.

    A guard that never fires is worthless — this confirms the sensitive-path
    check is real and would catch any stray write outside the declared scope.
    """
    fake_root = Path("/tmp/fake_root_scope_guard_test")
    fake_home = Path("/tmp/fake_home_scope_guard_test")

    # Stray path is not under fake_root or fake_home — the guard must reject it.
    stray_changes = FsChanges(
        created={"/tmp/completely_elsewhere/stray_write.txt"},
        modified=set(),
        deleted=set(),
    )

    with pytest.raises(AssertionError, match="STRAY WRITE"):
        # canary arg is never reached because the stray-path assert fires first
        _assert_safe(stray_changes, fake_root, fake_home, Path("/unreachable/canary.txt"))
