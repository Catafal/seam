"""Behavior tests for Phase 11 P1.2 — seam_snippet / `seam snippet`."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Symbol
from seam.server.tools import compute_uid


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sym(
    name: str,
    file: Path,
    *,
    kind: str = "function",
    start: int = 1,
    end: int = 2,
    signature: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=str(file),
        start_line=start,
        end_line=end,
        docstring="Indexed intent.",
        signature=signature or f"def {name}()",
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=name,
    )


def _make_snippet_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path]:
    root = tmp_path.resolve()
    src = root / "app.py"
    source = (
        "def entry():\n"
        "    helper()\n"
        "\n"
        "def helper():\n"
        "    return 'ok'\n"
    )
    src.write_text(source, encoding="utf-8")
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        _hash(source),
        [
            _sym("entry", src, start=1, end=2, signature="def entry()"),
            _sym("helper", src, start=4, end=5, signature="def helper()"),
        ],
        [],
    )
    return conn, root, src


def _make_homonym_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path, Path]:
    root = tmp_path.resolve()
    a = root / "a.py"
    b = root / "pkg" / "b.py"
    b.parent.mkdir()
    a_src = "def helper():\n    return 'a'\n"
    b_src = "def helper():\n    return 'b'\n"
    a.write_text(a_src, encoding="utf-8")
    b.write_text(b_src, encoding="utf-8")
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(conn, a, "python", _hash(a_src), [_sym("helper", a, start=1, end=2)], [])
    upsert_file(conn, b, "python", _hash(b_src), [_sym("helper", b, start=1, end=2)], [])
    return conn, root, a, b


def test_snippet_by_uid_returns_exact_source(tmp_path: Path) -> None:
    """A search/query UID can be followed by an exact source retrieval call."""
    from seam.server.tools import handle_seam_snippet

    conn, root, src = _make_snippet_repo(tmp_path)
    uid = compute_uid(str(src), 4)

    result = handle_seam_snippet(conn, root, uid=uid)

    assert result["found"] is True
    assert result["symbol"] == "helper"
    assert result["uid"] == uid
    assert result["kind"] == "function"
    assert result["file"] == "app.py"
    assert result["start_line"] == 4
    assert result["end_line"] == 5
    assert result["source_start_line"] == 4
    assert result["source_end_line"] == 5
    assert result["signature"] == "def helper()"
    assert result["docstring"] == "Indexed intent."
    assert result["source"] == "def helper():\n    return 'ok'\n"
    assert result["truncated"]["by_lines"] is False
    assert result["truncated"]["by_bytes"] is False
    assert result["freshness"]["file_hash_matches"] is True
    assert result["freshness"]["index_stale"] is False
    assert result["neighbors"] == []
    assert result["warnings"] == []


def test_snippet_can_include_same_file_neighbors(tmp_path: Path) -> None:
    """Neighbors are opt-in metadata hints, not additional source bodies."""
    from seam.server.tools import handle_seam_snippet

    conn, root, src = _make_snippet_repo(tmp_path)
    uid = compute_uid(str(src), 4)

    result = handle_seam_snippet(conn, root, uid=uid, include_neighbors=True)

    assert result["found"] is True
    assert result["neighbors"] == [
        {
            "symbol": "entry",
            "uid": compute_uid(str(src), 1),
            "kind": "function",
            "file": "app.py",
            "start_line": 1,
            "end_line": 2,
            "signature": "def entry()",
        }
    ]


def test_snippet_unknown_uid_returns_structured_not_found(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, _src = _make_snippet_repo(tmp_path)

    result = handle_seam_snippet(conn, root, uid="deadbeef:999")

    assert result["found"] is False
    assert result["reason"] == "UNKNOWN_UID"
    assert result["warnings"][0]["code"] == "UNKNOWN_UID"


def test_snippet_rejects_conflicting_selectors(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, _src = _make_snippet_repo(tmp_path)

    result = handle_seam_snippet(conn, root, symbol="helper", line=4)
    with_extra = handle_seam_snippet(conn, root, uid=compute_uid(str(_src), 4), file="app.py")

    assert result["error"] == "INVALID_INPUT"
    assert with_extra["error"] == "INVALID_INPUT"


def test_snippet_cli_json_and_quiet(tmp_path: Path) -> None:
    from seam.cli.main import app

    conn, root, src = _make_snippet_repo(tmp_path)
    conn.close()
    uid = compute_uid(str(src), 1)
    runner = CliRunner()

    json_result = runner.invoke(app, ["snippet", str(root), "--uid", uid, "--neighbors", "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert payload["data"]["symbol"] == "entry"
    assert payload["data"]["source"] == "def entry():\n    helper()\n"
    assert payload["data"]["neighbors"][0]["symbol"] == "helper"

    quiet_result = runner.invoke(app, ["snippet", str(root), "--uid", uid, "--quiet"])
    assert quiet_result.exit_code == 0, quiet_result.output
    assert quiet_result.output == "def entry():\n    helper()\n"


def test_snippet_mcp_registration(tmp_path: Path) -> None:
    from seam.server.mcp import create_server

    conn, root, _src = _make_snippet_repo(tmp_path)
    server = create_server(conn, root)

    tool_names = list(server._tool_manager._tools.keys())
    assert "seam_snippet" in tool_names
    assert "seam_plan" in tool_names
    assert len(tool_names) == 17


def test_snippet_unique_symbol_selector(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, _src = _make_snippet_repo(tmp_path)

    result = handle_seam_snippet(conn, root, symbol="helper")

    assert result["found"] is True
    assert result["symbol"] == "helper"
    assert result["source"] == "def helper():\n    return 'ok'\n"


def test_snippet_ambiguous_symbol_returns_candidates(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, _a, _b = _make_homonym_repo(tmp_path)

    result = handle_seam_snippet(conn, root, symbol="helper")

    assert result["found"] is False
    assert result["ambiguous"] is True
    assert result["reason"] == "AMBIGUOUS_SYMBOL"
    assert {c["file"] for c in result["candidates"]} == {"a.py", "pkg/b.py"}
    assert all(c["uid"] for c in result["candidates"])


def test_snippet_symbol_plus_file_disambiguates(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, _a, _b = _make_homonym_repo(tmp_path)

    result = handle_seam_snippet(conn, root, symbol="helper", file="pkg/b.py")

    assert result["found"] is True
    assert result["file"] == "pkg/b.py"
    assert "return 'b'" in result["source"]


def test_snippet_file_line_selects_narrowest_symbol(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, src = _make_snippet_repo(tmp_path)

    result = handle_seam_snippet(conn, root, file=str(src), line=4)

    assert result["found"] is True
    assert result["symbol"] == "helper"


def test_snippet_file_line_without_symbol_returns_candidates(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, _src = _make_snippet_repo(tmp_path)

    result = handle_seam_snippet(conn, root, file="app.py", line=3)

    assert result["found"] is False
    assert result["reason"] == "NO_SYMBOL_AT_LOCATION"
    assert [c["symbol"] for c in result["candidates"]] == ["helper", "entry"]


def test_snippet_rejects_source_outside_root(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    source = "def escape():\n    pass\n"
    outside.write_text(source, encoding="utf-8")
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(conn, outside, "python", _hash(source), [_sym("escape", outside, start=1, end=2)], [])

    result = handle_seam_snippet(conn, root, symbol="escape")

    assert result["found"] is False
    assert result["reason"] == "SOURCE_OUTSIDE_ROOT"
    assert str(tmp_path) not in result["file"]


def test_snippet_reports_missing_stale_eof_decode_and_truncation(tmp_path: Path) -> None:
    from seam.server.tools import handle_seam_snippet

    conn, root, src = _make_snippet_repo(tmp_path)
    uid = compute_uid(str(src), 4)
    src.write_bytes(b"def helper():\n\xff\n")

    result = handle_seam_snippet(conn, root, uid=uid)

    codes = {w["code"] for w in result["warnings"]}
    assert result["found"] is True
    assert "SOURCE_MAY_BE_STALE" in codes
    assert "SOURCE_RANGE_PAST_EOF" in codes

    src.write_text(
        "def entry():\n"
        "    helper()\n"
        "\n"
        "def helper():\n"
        "    value = 'long long long'\n"
        "    return value\n",
        encoding="utf-8",
    )
    truncated = handle_seam_snippet(conn, root, uid=uid, max_lines=1, max_bytes=8)
    truncate_codes = {w["code"] for w in truncated["warnings"]}
    assert "SNIPPET_TRUNCATED_LINES" in truncate_codes
    assert "SNIPPET_TRUNCATED_BYTES" in truncate_codes

    src.write_bytes(b"def entry():\n    helper()\n\ndef helper():\n\xff\n")
    decoded = handle_seam_snippet(conn, root, uid=uid)
    decode_codes = {w["code"] for w in decoded["warnings"]}
    assert "SOURCE_DECODE_REPLACED" in decode_codes

    src.unlink()
    missing = handle_seam_snippet(conn, root, uid=uid)
    assert missing["found"] is False
    assert missing["reason"] == "SOURCE_FILE_MISSING"
