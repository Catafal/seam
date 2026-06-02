"""seam init must keep its own index out of git.

WHY: the agentic-readiness audit found `seam_changes` reporting `.seam/seam.db`
(and -shm/-wal) as changed "modules" — the tool polluting its own risk report.
The fix writes a self-scoped `.seam/.gitignore` containing `*`, so git ignores the
whole index dir WITHOUT writing anything outside `.seam/` (preserves the repo
cleanliness guarantee). These tests assert that behavior through `seam init`.
"""

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _init_git_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "t")


def test_init_writes_self_scoped_gitignore(tmp_path: Path) -> None:
    """After init, .seam/.gitignore exists and contains '*'."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0

    gitignore = tmp_path / ".seam" / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text().strip() == "*"


def test_init_gitignore_hides_index_from_git(tmp_path: Path) -> None:
    """git status must not report .seam/ once the index is built."""
    _init_git_repo(tmp_path)
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")

    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0

    status = _git(tmp_path, "status", "--porcelain")
    assert ".seam/" not in status
    # the db file itself must be git-ignored
    check = subprocess.run(
        ["git", "check-ignore", ".seam/seam.db"], cwd=tmp_path, capture_output=True, text=True
    )
    assert check.returncode == 0  # 0 = path IS ignored


def test_init_gitignore_is_idempotent(tmp_path: Path) -> None:
    """Re-running init must not duplicate or clobber the .gitignore."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    runner.invoke(app, ["init", str(tmp_path)])
    gitignore = tmp_path / ".seam" / ".gitignore"
    first = gitignore.read_text()

    runner.invoke(app, ["init", str(tmp_path)])
    assert gitignore.read_text() == first
    assert gitignore.read_text().strip() == "*"
