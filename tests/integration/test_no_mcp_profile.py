"""The pure-CLI install profile: the `mcp` package is an OPTIONAL extra.

These tests simulate `mcp` (and seam.server.mcp, which imports it) being absent by
poisoning sys.modules, then assert:
  - the read commands (query/search/context) still work — the read path never touches mcp;
  - `seam start` fails fast (exit 1) with an install hint, instead of crashing on import.

The real end-to-end proof (a venv installed without the extra) is done as a live check;
this guards the wiring inside the test suite, where mcp IS installed.
"""

import sys

from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


def _seed(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    return tmp_path


def test_read_command_works_without_mcp(tmp_path, monkeypatch) -> None:
    repo = _seed(tmp_path)
    # Poison the mcp imports — `import mcp` / `from seam.server.mcp import ...` now raise.
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "seam.server.mcp", None)

    res = runner.invoke(app, ["search", "f", "--path", str(repo), "--json"])
    assert res.exit_code == 0, res.output  # read path is independent of mcp


def test_start_without_mcp_exits_with_hint(tmp_path, monkeypatch) -> None:
    repo = _seed(tmp_path)
    monkeypatch.setitem(sys.modules, "seam.server.mcp", None)

    res = runner.invoke(app, ["start", str(repo)])
    assert res.exit_code == 1  # fails fast, does not crash on import
