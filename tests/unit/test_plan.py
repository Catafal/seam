"""Tests for the agent change-planning surface."""

import json
import os
import subprocess
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
from seam.server.tools import handle_seam_plan

runner = CliRunner()


def _sym(name: str, file: str, *, line: int = 1, kind: str = "function") -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 2,
        docstring=None,
        signature=f"def {name}()",
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=name,
    )


def _edge(
    source: str,
    target: str,
    file: str,
    *,
    kind: str = "call",
    line: int = 1,
    confidence: str = "EXTRACTED",
) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=file,
        line=line,
        confidence=confidence,
    )


def _make_target_plan_db(tmp_path: Path):
    conn = init_db(tmp_path / "seam.db")
    src = tmp_path / "src.py"
    test_src = tmp_path / "tests" / "test_src.py"
    test_src.parent.mkdir()
    src.write_text("def A(): pass\ndef B(): A()\ndef C(): B()\n")
    test_src.write_text("def test_A(): A()\n")

    upsert_file(
        conn,
        src,
        "python",
        "h-src",
        [_sym("A", str(src), line=1), _sym("B", str(src), line=2), _sym("C", str(src), line=3)],
        [
            _edge("B", "A", str(src), line=2),
            _edge("C", "B", str(src), line=3),
        ],
    )
    upsert_file(
        conn,
        test_src,
        "python",
        "h-test",
        [_sym("test_A", str(test_src), line=1)],
        [_edge("test_A", "A", str(test_src), kind="tests", line=1)],
    )
    return conn, tmp_path


def _git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _make_diff_plan_db(tmp_path: Path):
    root = tmp_path.resolve()
    src = root / "src.py"
    test_src = root / "tests" / "test_src.py"
    test_src.parent.mkdir()
    src.write_text("def A():\n    pass\n\ndef B():\n    A()\n")
    test_src.write_text("def test_B():\n    B()\n")
    (root / ".gitignore").write_text(".seam/\n")

    _git(["init", "--initial-branch=main"], root)
    _git(["config", "user.email", "test@seam.local"], root)
    _git(["config", "user.name", "Test"], root)
    _git(["add", "src.py", "tests/test_src.py", ".gitignore"], root)
    _git(["commit", "-m", "initial"], root)

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        "h-src",
        [_sym("A", str(src), line=1), _sym("B", str(src), line=4)],
        [_edge("B", "A", str(src), line=5)],
    )
    upsert_file(
        conn,
        test_src,
        "python",
        "h-test",
        [_sym("test_B", str(test_src), line=1)],
        [_edge("test_B", "B", str(test_src), line=2)],
    )
    return conn, root, src


def test_target_plan_composes_context_impact_tests_and_caveats(tmp_path: Path) -> None:
    conn, root = _make_target_plan_db(tmp_path)
    try:
        result = handle_seam_plan(conn, root, symbol="A")
    finally:
        conn.close()

    assert result["mode"] == "target"
    assert result["found"] is True
    assert result["target"]["symbol"] == "A"
    assert result["target"]["file"] == "src.py"
    assert result["risk"]["upstream"]["WILL_BREAK"] == 1
    assert result["risk"]["upstream"]["LIKELY_AFFECTED"] == 1

    by_symbol = {item["symbol"]: item for item in result["inspection_plan"]}
    assert by_symbol["A"]["reasons"] == ["target"]
    assert "direct_caller" in by_symbol["B"]["reasons"]
    assert "will_break" in by_symbol["B"]["reasons"]
    assert "likely_affected" in by_symbol["C"]["reasons"]

    assert result["test_plan"]["test_files"] == ["tests/test_src.py"]
    assert result["test_plan"]["commands"] == ["pytest tests/test_src.py"]
    assert any("Static analysis" in caveat for caveat in result["caveats"])
    assert any(call["tool"] == "seam_impact" for call in result["recommended_next_calls"])


def test_target_plan_unknown_symbol_is_not_a_low_risk_plan(tmp_path: Path) -> None:
    conn, root = _make_target_plan_db(tmp_path)
    try:
        result = handle_seam_plan(conn, root, symbol="does_not_exist")
    finally:
        conn.close()

    assert result["mode"] == "target"
    assert result["found"] is False
    assert result["inspection_plan"] == []
    assert result["risk"]["level"] == "unknown"
    assert any("not found" in caveat for caveat in result["caveats"])
    assert [call["tool"] for call in result["recommended_next_calls"]] == [
        "seam_search",
        "seam_query",
    ]


def test_target_plan_rejects_blank_symbol(tmp_path: Path) -> None:
    conn, root = _make_target_plan_db(tmp_path)
    try:
        result = handle_seam_plan(conn, root, symbol="   ")
    finally:
        conn.close()

    assert result["error"] == "INVALID_INPUT"


def test_diff_plan_composes_changes_risk_and_affected_tests(tmp_path: Path) -> None:
    conn, root, src = _make_diff_plan_db(tmp_path)
    lines = src.read_text().splitlines()
    lines[1] = "    return 1"
    src.write_text("\n".join(lines) + "\n")

    try:
        result = handle_seam_plan(conn, root, mode="diff", scope="working")
    finally:
        conn.close()

    assert result["mode"] == "diff"
    assert result["diff"]["scope"] == "working"
    assert result["diff"]["changed_symbols"][0]["name"] == "A"
    assert result["risk"]["level"] == "critical"
    assert result["test_plan"]["test_files"] == ["tests/test_src.py"]
    assert result["test_plan"]["commands"] == ["pytest tests/test_src.py"]

    by_symbol = {item["symbol"]: item for item in result["inspection_plan"]}
    assert "changed_symbol" in by_symbol["A"]["reasons"]
    assert "will_break" in by_symbol["B"]["reasons"]


def test_diff_plan_includes_modified_test_file_without_changed_symbol(tmp_path: Path) -> None:
    conn, root, _src = _make_diff_plan_db(tmp_path)
    test_src = root / "tests" / "test_src.py"
    test_src.write_text("def test_B():\n    B()\n\nMODULE_FIXTURE = 1\n")

    try:
        result = handle_seam_plan(conn, root, mode="diff", scope="working")
    finally:
        conn.close()

    assert result["test_plan"]["test_files"] == ["tests/test_src.py"]
    assert result["test_plan"]["commands"] == ["pytest tests/test_src.py"]


def test_plan_reports_omitted_inspection_items_when_capped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_PLAN_MAX_INSPECTION_ITEMS", 2)
    conn, root = _make_target_plan_db(tmp_path)
    try:
        result = handle_seam_plan(conn, root, symbol="A")
    finally:
        conn.close()

    assert len(result["inspection_plan"]) == 2
    assert result["omitted"]["inspection_items"] > 0
    assert any("omitted" in caveat for caveat in result["caveats"])


def test_plan_reports_omitted_test_files_when_capped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_PLAN_MAX_TEST_FILES", 1)
    conn, root = _make_target_plan_db(tmp_path)
    extra_test = root / "tests" / "test_src_extra.py"
    extra_test.write_text("def test_A_extra(): A()\n")
    upsert_file(
        conn,
        extra_test,
        "python",
        "h-test-extra",
        [_sym("test_A_extra", str(extra_test), line=1)],
        [_edge("test_A_extra", "A", str(extra_test), kind="tests", line=1)],
    )

    try:
        result = handle_seam_plan(conn, root, symbol="A")
    finally:
        conn.close()

    assert result["test_plan"]["test_files"] == ["tests/test_src.py"]
    assert result["test_plan"]["omitted"] == 1
    assert result["omitted"]["test_files"] == 1
    assert any("test file" in caveat for caveat in result["caveats"])


def test_plan_reports_omitted_enriched_context_when_capped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_PLAN_MAX_ENRICHED_TARGETS", 1)
    conn, root = _make_target_plan_db(tmp_path)
    extra_src = root / "extra.py"
    extra_src.write_text("def Leaf(): pass\ndef D(): A()\n")
    upsert_file(
        conn,
        extra_src,
        "python",
        "h-extra",
        [_sym("Leaf", str(extra_src), line=1), _sym("D", str(extra_src), line=2)],
        [
            _edge("A", "Leaf", str(extra_src), line=1),
            _edge("D", "A", str(extra_src), line=2),
        ],
    )

    try:
        result = handle_seam_plan(conn, root, symbol="A")
    finally:
        conn.close()

    assert result["omitted"]["inspection_items"] > 0
    assert any("SEAM_PLAN_MAX_ENRICHED_TARGETS" in caveat for caveat in result["caveats"])


def test_diff_plan_invalid_scope_returns_invalid_input(tmp_path: Path) -> None:
    conn, root = _make_target_plan_db(tmp_path)
    try:
        result = handle_seam_plan(conn, root, mode="diff", scope="sideways")
    finally:
        conn.close()

    assert result["error"] == "INVALID_INPUT"


def test_diff_plan_non_git_root_returns_not_a_git_repo(tmp_path: Path) -> None:
    conn, root = _make_target_plan_db(tmp_path)
    try:
        result = handle_seam_plan(conn, root, mode="diff", scope="working")
    finally:
        conn.close()

    assert result["error"] == "NOT_A_GIT_REPO"


def test_plan_adds_caveat_when_index_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)
    _cache.clear()
    conn, root, src = _make_diff_plan_db(tmp_path)
    src.write_text("def A():\n    return 2\n\ndef B():\n    A()\n")
    future = time.time() + 1000.0
    os.utime(src, (future, future))

    try:
        result = handle_seam_plan(conn, root, symbol="A")
    finally:
        conn.close()
        _cache.clear()

    assert result["index_status"]["stale"] is True
    assert any("stale" in caveat.lower() for caveat in result["caveats"])


def test_plan_cli_json_matches_handler_payload(tmp_path: Path) -> None:
    conn, root, _src = _make_diff_plan_db(tmp_path)
    try:
        expected = handle_seam_plan(conn, root, symbol="A")
    finally:
        conn.close()

    result = runner.invoke(
        app,
        ["plan", "A", "--json", "--path", str(root)],
    )

    assert result.exit_code == 0, result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"] == expected


def test_plan_cli_rich_output_shows_risk_tier_and_test_command(tmp_path: Path) -> None:
    conn, root, _src = _make_diff_plan_db(tmp_path)
    test_src = root / "tests" / "test_src.py"
    upsert_file(
        conn,
        test_src,
        "python",
        "h-test",
        [_sym("test_B", str(test_src), line=1)],
        [_edge("test_B", "A", str(test_src), kind="tests", line=2)],
    )
    try:
        conn.close()
        result = runner.invoke(app, ["plan", "A", "--path", str(root)])
    finally:
        pass

    assert result.exit_code == 0, result.stdout
    assert "WILL_BREAK" in result.stdout
    assert "pytest tests/test_src.py" in result.stdout


def test_plan_mcp_registration_exposes_mode_and_symbol(tmp_path: Path) -> None:
    conn, root = _make_target_plan_db(tmp_path)
    try:
        server = create_server(conn, root)
        tool_names = list(server._tool_manager._tools.keys())
        tool = server._tool_manager._tools["seam_plan"]
    finally:
        conn.close()

    assert "seam_plan" in tool_names
    assert len(tool_names) == 17
    params = tool.parameters.get("properties", {})
    assert "mode" in params
    assert "symbol" in params
