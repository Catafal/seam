"""Tests for conservative cleanup suspect analysis."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import seam.config as config
from seam.analysis.staleness import _cache
from seam.cli.main import app
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.mcp import create_server
from seam.server.tools import handle_seam_suspects

runner = CliRunner()


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sym(
    name: str,
    file: Path,
    *,
    kind: str = "function",
    line: int = 1,
    is_exported: bool | None = False,
    visibility: str | None = "private",
    qualified_name: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=str(file),
        start_line=line,
        end_line=line + 1,
        docstring=None,
        signature=f"def {name}()",
        decorators=[],
        is_exported=is_exported,
        visibility=visibility,
        qualified_name=qualified_name or name,
    )


def _edge(
    source: str,
    target: str,
    file: Path,
    *,
    kind: str = "call",
    line: int = 1,
    confidence: str = "EXTRACTED",
    synthesized_by: str | None = None,
) -> Edge:
    edge = Edge(
        source=source,
        target=target,
        kind=kind,
        file=str(file),
        line=line,
        confidence=confidence,  # type: ignore[typeddict-item]
    )
    if synthesized_by is not None:
        edge["synthesized_by"] = synthesized_by
    return edge


def _make_suspect_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    root = tmp_path.resolve()
    src = root / "app.py"
    fields = root / "models.py"
    tests = root / "tests" / "test_app.py"
    orphan = root / "orphan.py"
    empty = root / "empty.py"
    tests.parent.mkdir()

    src_text = (
        "def private_helper():\n"
        "    return 1\n"
        "\n"
        "def public_api():\n"
        "    return 2\n"
        "\n"
        "def called():\n"
        "    return private_helper()\n"
        "\n"
        "def caller():\n"
        "    return called()\n"
    )
    fields_text = (
        "class DataPipeline:\n"
        "    def __init__(self):\n"
        "        self.stages = []\n"
        "\n"
        "    def run(self):\n"
        "        return self.stages\n"
    )
    tests_text = "def test_public_api():\n    public_api()\n"
    orphan_text = "def lonely():\n    return 1\n"
    empty_text = "CONSTANT = 1\n"
    src.write_text(src_text, encoding="utf-8")
    fields.write_text(fields_text, encoding="utf-8")
    tests.write_text(tests_text, encoding="utf-8")
    orphan.write_text(orphan_text, encoding="utf-8")
    empty.write_text(empty_text, encoding="utf-8")

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        _hash(src_text),
        [
            _sym("private_helper", src, line=1),
            _sym(
                "public_api",
                src,
                line=4,
                is_exported=True,
                visibility="public",
                qualified_name="app.public_api",
            ),
            _sym("called", src, line=7),
            _sym("caller", src, line=10),
        ],
        [
            _edge("called", "private_helper", src, line=8),
            _edge("caller", "called", src, line=11),
        ],
    )
    upsert_file(
        conn,
        fields,
        "python",
        _hash(fields_text),
        [
            _sym("DataPipeline", fields, kind="class", line=1, is_exported=True, visibility="public"),
            _sym("DataPipeline.stages", fields, kind="field", line=3),
            _sym("DataPipeline.run", fields, kind="method", line=5),
        ],
        [
            _edge("DataPipeline.__init__", "DataPipeline.stages", fields, kind="writes", line=3),
            _edge("DataPipeline.run", "DataPipeline.stages", fields, kind="reads", line=6),
        ],
    )
    upsert_file(
        conn,
        tests,
        "python",
        _hash(tests_text),
        [_sym("test_public_api", tests, line=1)],
        [_edge("test_public_api", "public_api", tests, kind="tests", line=2, synthesized_by="test-call")],
    )
    upsert_file(conn, orphan, "python", _hash(orphan_text), [_sym("lonely", orphan, line=1)], [])
    upsert_file(conn, empty, "python", _hash(empty_text), [], [])
    return conn, root


def test_symbol_suspects_are_conservative_about_public_and_test_evidence(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    try:
        result = handle_seam_suspects(conn, root, mode="symbols", limit=20)
    finally:
        conn.close()

    assert result["mode"] == "symbols"
    assert result["query"]["target"] is None
    assert result["summary"]["returned"] == len(result["candidates"])
    assert result["summary"]["by_suspect_strength"]["strong"] >= 1
    by_symbol = {item["symbol"]: item for item in result["candidates"]}

    assert by_symbol["lonely"]["suspect_strength"] == "strong"
    assert by_symbol["lonely"]["removal_risk"] == "unknown"
    assert "no_incoming_production_edges" in by_symbol["lonely"]["reasons"]
    assert by_symbol["lonely"]["blockers"] == []

    assert by_symbol["public_api"]["suspect_strength"] in {"weak", "moderate"}
    assert by_symbol["public_api"]["removal_risk"] == "high"
    assert "public_api_surface" in by_symbol["public_api"]["blockers"]
    assert "static_test_evidence" in by_symbol["public_api"]["blockers"]

    assert "private_helper" not in by_symbol
    assert "Static graph evidence is not deletion proof." in result["caveats"]


def test_field_readers_and_writers_block_unused_field_claims(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    try:
        result = handle_seam_suspects(conn, root, mode="symbols", target="DataPipeline.stages")
    finally:
        conn.close()

    candidate = result["candidates"][0]
    assert candidate["symbol"] == "DataPipeline.stages"
    assert candidate["suspect_strength"] == "weak"
    assert "field_access_evidence" in candidate["blockers"]
    assert any(ev["edge_kind"] == "reads" for ev in candidate["evidence"])
    assert any(ev["edge_kind"] == "writes" for ev in candidate["evidence"])


def test_symbol_target_accepts_uid_qualified_name_and_file_path(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    try:
        public = handle_seam_suspects(conn, root, mode="symbols", target="app.public_api")
        stage_uid = handle_seam_suspects(conn, root, mode="symbols", target="models.py")
        stage = next(item for item in stage_uid["candidates"] if item["symbol"] == "DataPipeline.stages")
        by_uid = handle_seam_suspects(conn, root, mode="symbols", target=stage["uid"])
    finally:
        conn.close()

    assert public["found"] is True
    assert public["candidates"][0]["symbol"] == "public_api"
    assert any(item["symbol"] == "DataPipeline.stages" for item in stage_uid["candidates"])
    assert by_uid["found"] is True
    assert by_uid["candidates"][0]["symbol"] == "DataPipeline.stages"


def test_file_suspects_distinguish_orphan_files_from_unindexed_files(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    try:
        result = handle_seam_suspects(conn, root, mode="files", limit=20)
    finally:
        conn.close()

    by_file = {item["file"]: item for item in result["candidates"]}
    assert by_file["orphan.py"]["suspect_strength"] == "strong"
    assert "file_has_no_incoming_imports" in by_file["orphan.py"]["reasons"]
    assert by_file["empty.py"]["suspect_strength"] == "weak"
    assert "no_indexed_symbols" in by_file["empty.py"]["blockers"]
    assert any("No indexed symbols" in caveat for caveat in by_file["empty.py"]["caveats"])


def test_file_suspects_do_not_block_on_ambiguous_homonym_edges(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    other = root / "other.py"
    caller = root / "caller.py"
    other_text = "def lonely():\n    return 2\n"
    caller_text = "def call_lonely():\n    return lonely()\n"
    other.write_text(other_text, encoding="utf-8")
    caller.write_text(caller_text, encoding="utf-8")
    try:
        upsert_file(conn, other, "python", _hash(other_text), [_sym("lonely", other, line=1)], [])
        upsert_file(
            conn,
            caller,
            "python",
            _hash(caller_text),
            [_sym("call_lonely", caller, line=1)],
            [_edge("call_lonely", "lonely", caller, line=2)],
        )
        result = handle_seam_suspects(conn, root, mode="files", target="orphan.py")
    finally:
        conn.close()

    candidate = result["candidates"][0]
    assert candidate["file"] == "orphan.py"
    assert "contained_symbol_usage" not in candidate["blockers"]
    assert any("ambiguous" in caveat for caveat in candidate["caveats"])


def test_suspects_reject_invalid_mode_and_unknown_target(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    try:
        invalid = handle_seam_suspects(conn, root, mode="wat")
        missing = handle_seam_suspects(conn, root, mode="symbols", target="missing")
    finally:
        conn.close()

    assert invalid["error"] == "INVALID_INPUT"
    assert missing["found"] is False
    assert missing["candidates"] == []
    assert any("not found" in caveat for caveat in missing["caveats"])


def test_suspects_attaches_stale_index_status_and_caveat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)
    _cache.clear()
    conn, root = _make_suspect_repo(tmp_path)
    src = root / "app.py"
    src.write_text(src.read_text(encoding="utf-8") + "\n# stale edit\n", encoding="utf-8")
    future = time.time() + 1000.0
    os.utime(src, (future, future))
    try:
        result = handle_seam_suspects(conn, root, mode="symbols", target="public_api")
    finally:
        conn.close()
        _cache.clear()

    assert result["index_status"]["stale"] is True
    assert any("Index is stale" in caveat for caveat in result["caveats"])


def test_suspects_cli_json_and_quiet_modes(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    conn.close()

    json_result = runner.invoke(app, ["suspects", "--path", str(root), "--json", "--limit", "5"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)["data"]
    assert payload["mode"] == "symbols"
    assert payload["candidates"][0]["symbol"]

    quiet_result = runner.invoke(app, ["suspects", "--path", str(root), "--quiet", "--limit", "2"])
    assert quiet_result.exit_code == 0, quiet_result.output
    assert "\t" in quiet_result.output


def test_suspects_schema_and_mcp_registration(tmp_path: Path) -> None:
    conn, root = _make_suspect_repo(tmp_path)
    try:
        tools = create_server(conn, root)._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}
    finally:
        conn.close()

    assert "seam_suspects" in tool_names

    schema_result = runner.invoke(app, ["schema", str(root), "--json"])
    assert schema_result.exit_code == 0, schema_result.output
    schema = json.loads(schema_result.output)["data"]
    assert "seam_suspects" in {tool["name"] for tool in schema["tools"]}
