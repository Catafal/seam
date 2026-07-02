"""`seam --version` prints the installed package version and exits 0.

WHY this exists: a released CLI must be able to report its own version (used by
the release smoke-install as the post-install assertion). The version is read
from package metadata, so this test derives the expected value the same way
rather than hardcoding it — it can never drift from pyproject `version`.
"""

import importlib.metadata

from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


def test_version_flag_prints_installed_version_and_exits_zero() -> None:
    expected = importlib.metadata.version("seam-code")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"seam {expected}" in result.stdout


def test_version_flag_short_circuits_before_any_subcommand() -> None:
    # --version is eager: it must not require (or run) a subcommand.
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "seam" in result.stdout
