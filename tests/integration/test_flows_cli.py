"""CLI tests for `seam flows` (list + drill, --json/--quiet).

  C1 — flows (no arg) --json: success envelope with entry_points
  C2 — flows <entry> --json: success envelope with a flow tree
  C3 — flows <unknown> --json: success envelope with {"found": false}
  C4 — flows --quiet: bare entry-point names, one per line
  C5 — flows on missing index: NO_INDEX error envelope, exit 1
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.cli.main import app
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

runner = CliRunner()


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence="EXTRACTED")


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """Seed main -> step1 -> step2 (main is a root). Returns the project/db root."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    src = tmp_path / "app.py"
    src.write_text("# stub\n")
    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        "h1",
        [_sym("main", str(src)), _sym("step1", str(src)), _sym("step2", str(src))],
        [_edge("main", "step1", str(src)), _edge("step1", "step2", str(src))],
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_flows_list_json(seeded_db: Path) -> None:
    result = runner.invoke(app, ["flows", "--json", "--path", str(seeded_db)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    names = [p["name"] for p in env["data"]["entry_points"]]
    assert "main" in names


def test_flows_drill_json(seeded_db: Path) -> None:
    result = runner.invoke(app, ["flows", "main", "--json", "--path", str(seeded_db)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["data"]["entry"] == "main"
    assert [s["name"] for s in env["data"]["steps"]] == ["step1"]


def test_flows_unknown_json(seeded_db: Path) -> None:
    result = runner.invoke(app, ["flows", "ghost", "--json", "--path", str(seeded_db)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["data"] == {"found": False}


def test_flows_quiet_lists_names(seeded_db: Path) -> None:
    result = runner.invoke(app, ["flows", "--quiet", "--path", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert "main" in result.output.splitlines()


def test_flows_missing_index_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flows", "--json", "--path", str(tmp_path)])
    assert result.exit_code == 1
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"]["code"] == "NO_INDEX"
