"""Integration tests for P5.5 S2 — MCP server tool instrumentation.

Drives real tool calls through the FastMCP server built by create_server and
asserts the diagnostics recorder captures one slow_query line per call, with the
security-critical guarantee that NO source text from the indexed fixture leaks
into the NDJSON file. Also verifies the disabled path is a true no-op.

The registered tool function is reachable as tool.fn (the same handle the existing
server tests use), which routes through the _instrument wrapper when diagnostics
is enabled.
"""

import json
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import seam.analysis.diagnostics as diagnostics
import seam.config as config
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.mcp import create_server


@pytest.fixture(autouse=True)
def _reset_diag() -> Iterator[None]:
    """Close + clear the diagnostics singleton around each test (no leaked atexit)."""
    diagnostics.reset_recorder()
    yield
    diagnostics.reset_recorder()

# A recognizable secret-like / source-like string baked into the fixture docstring.
# The redaction contract requires it NEVER appears in the diagnostics NDJSON.
_SECRET_DOCSTRING = "SECRET_TOKEN_sk_live_deadbeef verify credentials return session"


@pytest.fixture()
def seeded_db() -> Iterator[tuple[sqlite3.Connection, Path]]:
    """Temporary DB seeded with one file + symbols whose docstring holds a secret."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)

        src_path = tmp_path / "src" / "auth.py"
        src_path.parent.mkdir(parents=True)
        src_path.write_text("# stub\n")

        symbols: list[Symbol] = [
            Symbol(
                name="authenticate_user",
                kind="function",
                file=str(src_path),
                start_line=10,
                end_line=25,
                docstring=_SECRET_DOCSTRING,
            ),
            Symbol(
                name="UserService",
                kind="class",
                file=str(src_path),
                start_line=30,
                end_line=80,
                docstring="Service layer for user operations.",
            ),
        ]
        edges: list[Edge] = [
            Edge(
                source="UserService",
                target="authenticate_user",
                kind="call",
                file=str(src_path),
                line=50,
                confidence="EXTRACTED",
            ),
        ]
        upsert_file(conn, src_path, "python", "abc123", symbols, edges)
        yield conn, tmp_path
        conn.close()


def _enable_diagnostics(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Point the recorder at a temp NDJSON file and record every query (slow_ms=0)."""
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS", "1")
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_SLOW_MS", 0)


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_tool_calls_record_query_lines(
    seeded_db: tuple[sqlite3.Connection, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each MCP tool call through the server produces one slow_query record."""
    conn, root = seeded_db
    ndjson = tmp_path / "diag.ndjson"
    _enable_diagnostics(monkeypatch, ndjson)

    server = create_server(conn, root)
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    tools["seam_search"].fn(text="authenticate")
    tools["seam_context"].fn(symbol="authenticate_user")
    tools["seam_query"].fn(concept="user")

    lines = _read_lines(ndjson)
    slow = [ln for ln in lines if ln["event"] == "slow_query"]
    assert len(slow) == 3
    assert {ln["tool"] for ln in slow} == {"seam_search", "seam_context", "seam_query"}
    # Each line carries exactly the allowed keys — nothing else.
    for ln in slow:
        assert set(ln.keys()) == {"event", "tool", "duration_ms", "result_chars", "seq", "ts"}
        assert isinstance(ln["result_chars"], int)


def test_no_source_text_leaks(
    seeded_db: tuple[sqlite3.Connection, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secret docstring baked into the fixture must NEVER appear in the NDJSON."""
    conn, root = seeded_db
    ndjson = tmp_path / "diag.ndjson"
    _enable_diagnostics(monkeypatch, ndjson)

    server = create_server(conn, root)
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    # These tools return the docstring in their result body — a naive size-proxy
    # that stored the serialized result would leak it. It must not.
    tools["seam_search"].fn(text="authenticate")
    tools["seam_context"].fn(symbol="authenticate_user")

    raw = ndjson.read_text()
    assert "SECRET_TOKEN" not in raw
    assert "sk_live_deadbeef" not in raw
    assert _SECRET_DOCSTRING not in raw


def test_disabled_is_noop(
    seeded_db: tuple[sqlite3.Connection, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With SEAM_DIAGNOSTICS=0, no file is written and tool count stays stable."""
    conn, root = seeded_db
    ndjson = tmp_path / "diag.ndjson"
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS", "0")
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_PATH", str(ndjson))

    server = create_server(conn, root)
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    assert len(tools) == 18  # instrumentation must not change the tool count

    tools["seam_search"].fn(text="authenticate")
    assert not ndjson.exists()  # disabled path writes nothing


def test_error_result_still_counted(
    seeded_db: tuple[sqlite3.Connection, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool that raises ToolError (error sentinel) is still recorded as a query."""
    conn, root = seeded_db
    ndjson = tmp_path / "diag.ndjson"
    _enable_diagnostics(monkeypatch, ndjson)

    server = create_server(conn, root)
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    # Blank concept → handler returns INVALID_INPUT → _finalize raises ToolError.
    with pytest.raises(Exception):
        tools["seam_query"].fn(concept="   ")

    lines = _read_lines(ndjson)
    slow = [ln for ln in lines if ln["event"] == "slow_query" and ln["tool"] == "seam_query"]
    assert len(slow) == 1
    assert slow[0]["result_chars"] == 0  # error/raise → no result body measured
