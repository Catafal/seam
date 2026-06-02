"""Integration tests for the CLI-only read commands: seam query / search / context.

These reuse the transport-agnostic handlers and query SQLite directly — no MCP
server. Each test indexes a tiny tmp repo via `seam init`, then drives the command.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app

runner = CliRunner()


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "auth.py").write_text(
        "def authenticate_user(name, pw):\n"
        '    """Verify credentials."""\n'
        "    return check(pw)\n"
        "\n"
        "def check(pw):\n"
        "    return True\n"
    )
    res = runner.invoke(app, ["init", str(tmp_path)])
    assert res.exit_code == 0, res.output
    return tmp_path


def test_search_json(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    res = runner.invoke(app, ["search", "authenticate", "--path", str(repo), "--json"])
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert any(r["symbol"] == "authenticate_user" for r in payload["data"])


def test_search_blank_is_invalid_input(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    res = runner.invoke(app, ["search", "   ", "--path", str(repo), "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"


def test_query_json_has_graph_counts(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    res = runner.invoke(app, ["query", "authenticate", "--path", str(repo), "--json"])
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert all("callers_count" in r and "callees_count" in r for r in payload["data"])


def test_context_json(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    res = runner.invoke(app, ["context", "authenticate_user", "--path", str(repo), "--json"])
    data = json.loads(res.stdout)["data"]
    assert data["symbol"] == "authenticate_user"
    assert "check" in data["callees"]


def test_context_not_found_returns_found_false(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    res = runner.invoke(app, ["context", "nope_xyz", "--path", str(repo), "--json"])
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"found": False, "symbol": "nope_xyz"}


def test_context_lean_omits_enrichment(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    full = json.loads(
        runner.invoke(app, ["context", "authenticate_user", "--path", str(repo), "--json"]).stdout
    )["data"]
    lean = json.loads(
        runner.invoke(
            app, ["context", "authenticate_user", "--path", str(repo), "--lean", "--json"]
        ).stdout
    )["data"]
    assert "visibility" in full  # full carries enrichment
    assert "visibility" not in lean  # lean strips the heavy fields
    assert lean["signature"] == full["signature"]  # signature always kept


def test_no_index_is_no_index_error(tmp_path: Path) -> None:
    res = runner.invoke(app, ["search", "x", "--path", str(tmp_path), "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "NO_INDEX"


def test_json_quiet_mutually_exclusive(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    res = runner.invoke(app, ["query", "x", "--path", str(repo), "--json", "--quiet"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "INVALID_INPUT"
