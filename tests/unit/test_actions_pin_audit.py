"""Unit tests for tests/support/actions_pin_audit.py.

Tests exercise the public API only: classify_uses_ref, scan_workflow_text, main.
No implementation internals are tested — tests would survive a full rewrite
of the internals as long as the observable contract holds.

WHY: actions_pin_audit is the P5.2 S1 primitive that enforces SHA-pinned
GitHub Actions refs.  Locking down the contract here (pure unit + repo-invariant
test) means the gate fails deterministically when anyone adds a mutable ref.

Test naming: ``test_<behavior>`` from the caller's perspective.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.actions_pin_audit import (
    classify_uses_ref,
    main,
    scan_workflow_files,
    scan_workflow_text,
)

# ── Repo root (used for repo-invariant gate test) ────────────────────────────

# tests/unit/ → tests/ → repo root
_REPO_ROOT = Path(__file__).parent.parent.parent


# ── classify_uses_ref unit tests ─────────────────────────────────────────────


def test_sha40_ref_is_pinned() -> None:
    """A ref whose @-suffix is exactly 40 hex chars is PINNED."""
    sha = "a" * 40
    assert classify_uses_ref(f"actions/checkout@{sha}") == "pinned"


def test_sha40_ref_with_trailing_comment_is_pinned() -> None:
    """A 40-hex SHA with a trailing # vN comment should still be treated as PINNED.

    Note: the comment is stripped by the line parser before classify_uses_ref is
    called, so this tests the value-only form (no # comment).  The round-trip
    test lives in scan_workflow_text tests below.
    """
    sha = "df4cb1c069e1874edd31b4311f1884172cec0e10"
    assert classify_uses_ref(f"actions/checkout@{sha}") == "pinned"


def test_tag_v1_is_mutable() -> None:
    """@v1 is a mutable tag — NOT a commit SHA."""
    assert classify_uses_ref("actions/checkout@v1") == "mutable"


def test_tag_v6_is_mutable() -> None:
    """@v6 is a mutable tag — the common semver-like tag pattern."""
    assert classify_uses_ref("actions/checkout@v6") == "mutable"


def test_branch_main_is_mutable() -> None:
    """@main is a mutable branch ref."""
    assert classify_uses_ref("actions/checkout@main") == "mutable"


def test_branch_release_v1_is_mutable() -> None:
    """@release/v1 is a mutable branch ref (pypa/gh-action-pypi-publish pattern)."""
    assert classify_uses_ref("pypa/gh-action-pypi-publish@release/v1") == "mutable"


def test_local_action_is_pinned() -> None:
    """A local composite action starting with ./ is always PINNED (same-repo)."""
    assert classify_uses_ref("./local/action") == "pinned"


def test_local_action_dot_slash_only_is_pinned() -> None:
    """Even bare ./ is treated as a local action — PINNED."""
    assert classify_uses_ref("./") == "pinned"


def test_39_hex_chars_is_mutable() -> None:
    """A 39-char hex string is NOT a valid SHA — MUTABLE (fail-closed)."""
    short_sha = "a" * 39
    assert classify_uses_ref(f"actions/checkout@{short_sha}") == "mutable"


def test_41_hex_chars_is_mutable() -> None:
    """A 41-char hex string is NOT a valid SHA — MUTABLE (fail-closed)."""
    long_sha = "a" * 41
    assert classify_uses_ref(f"actions/checkout@{long_sha}") == "mutable"


def test_no_at_sign_is_mutable() -> None:
    """A uses: value with no @ separator is ambiguous — treated as MUTABLE."""
    assert classify_uses_ref("actions/checkout") == "mutable"


def test_subdir_action_sha40_is_pinned() -> None:
    """owner/repo/subdir@<40-hex> is a valid pinned subdir action."""
    sha = "b" * 40
    assert classify_uses_ref(f"owner/repo/subdir@{sha}") == "pinned"


def test_subdir_action_tag_is_mutable() -> None:
    """owner/repo/subdir@v3 is mutable — tag, not SHA."""
    assert classify_uses_ref("owner/repo/subdir@v3") == "mutable"


# ── scan_workflow_text unit tests ─────────────────────────────────────────────

_CLEAN_WORKFLOW = """\
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{sha}  # v6
      - uses: astral-sh/setup-uv@{sha}  # v7
""".format(sha="a" * 40)

_DIRTY_WORKFLOW = """\
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@main
      - uses: ./local-action
""".format()


def test_scan_clean_workflow_returns_empty() -> None:
    """A workflow file with only SHA-pinned refs produces no offenders."""
    offenders = scan_workflow_text(_CLEAN_WORKFLOW, source="ci.yml")
    assert offenders == []


def test_scan_dirty_workflow_returns_mutable_refs() -> None:
    """A workflow with @v6 and @main tags yields those two refs as offenders."""
    offenders = scan_workflow_text(_DIRTY_WORKFLOW, source="ci.yml")
    refs = [ref for _, ref in offenders]
    assert "actions/checkout@v6" in refs
    assert "astral-sh/setup-uv@main" in refs


def test_scan_local_action_not_in_offenders() -> None:
    """Local ./… actions must NOT appear in the offender list."""
    offenders = scan_workflow_text(_DIRTY_WORKFLOW, source="ci.yml")
    refs = [ref for _, ref in offenders]
    assert "./local-action" not in refs


def test_scan_non_uses_lines_are_ignored() -> None:
    """Lines that are not ``uses:`` entries must be ignored."""
    text = """\
name: CI
run: echo "uses: actions/checkout@v6"
# uses: actions/checkout@v6
"""
    offenders = scan_workflow_text(text, source="fake.yml")
    # The ``run:`` value contains 'uses:' as a string and the comment line
    # starts with #.  Neither should be treated as a step uses: directive.
    assert offenders == []


def test_scan_sha_with_comment_is_clean() -> None:
    """A SHA ref followed by an inline ``# vN`` comment is PINNED."""
    sha = "df4cb1c069e1874edd31b4311f1884172cec0e10"
    text = f"      - uses: actions/checkout@{sha}  # v6\n"
    offenders = scan_workflow_text(text, source="ci.yml")
    assert offenders == []


def test_scan_source_label_in_offenders() -> None:
    """The source label passed to scan_workflow_text appears in offender tuples."""
    text = "      - uses: actions/checkout@v6\n"
    offenders = scan_workflow_text(text, source="ci.yml")
    assert len(offenders) == 1
    assert offenders[0][0] == "ci.yml"


# ── main() CLI tests ──────────────────────────────────────────────────────────


def test_main_exits_0_for_clean_file(tmp_path: Path) -> None:
    """main() returns 0 when all refs in the given file are SHA-pinned."""
    sha = "a" * 40
    wf = tmp_path / "clean.yml"
    wf.write_text(f"      - uses: actions/checkout@{sha}  # v6\n")
    assert main([str(wf)]) == 0


def test_main_exits_1_for_dirty_file(tmp_path: Path) -> None:
    """main() returns 1 when at least one mutable ref is present."""
    wf = tmp_path / "dirty.yml"
    wf.write_text("      - uses: actions/checkout@v6\n")
    assert main([str(wf)]) == 1


def test_main_prints_offender_line(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """main() prints 'mutable action ref: <file>: <ref>' for each offender."""
    wf = tmp_path / "dirty.yml"
    wf.write_text("      - uses: actions/checkout@v6\n")
    main([str(wf)])
    out = capsys.readouterr().out
    assert "mutable action ref:" in out
    assert "actions/checkout@v6" in out


def test_main_module_invocation_clean(tmp_path: Path) -> None:
    """``python -m tests.support.actions_pin_audit <clean>`` exits 0."""
    sha = "a" * 40
    wf = tmp_path / "clean.yml"
    wf.write_text(f"      - uses: actions/checkout@{sha}\n")
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.actions_pin_audit", str(wf)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_main_module_invocation_dirty(tmp_path: Path) -> None:
    """``python -m tests.support.actions_pin_audit <dirty>`` exits 1."""
    wf = tmp_path / "dirty.yml"
    wf.write_text("      - uses: actions/checkout@v6\n")
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.actions_pin_audit", str(wf)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


# ── Repo-invariant gate test ─────────────────────────────────────────────────


def test_all_workflow_files_are_sha_pinned() -> None:
    """GATE: every .github/workflows/*.yml must use only SHA-pinned action refs.

    This test fails immediately and deterministically if anyone adds a mutable
    ``uses:`` ref to any workflow.  It runs as part of ``make gate`` and in CI.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    workflow_files = sorted(workflows_dir.glob("*.yml"))
    assert workflow_files, f"No workflow files found in {workflows_dir}"

    offenders = scan_workflow_files(workflow_files)

    # Build a readable failure message listing every offending file + ref.
    if offenders:
        lines = ["Mutable action refs found — pin every uses: to a 40-hex SHA:"]
        for source, ref in offenders:
            lines.append(f"  {source}: {ref}")
        pytest.fail("\n".join(lines))
