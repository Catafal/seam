"""Contract tests for the MCP transport's error + not-found normalization.

WHY these exist: the agentic-readiness audit (2026-06-02) found that app-level
rejections returned `{"error": CODE, "message": ...}` with isError=False — a
protocol-compliant agent that checks isError reads a rejection as success. And
not-found (seam_context on a missing symbol) returned empty content.

`_finalize` (seam/server/mcp.py) fixes both at the MCP closure layer ONLY:
  - error-dict sentinel  → raise ToolError → FastMCP sets isError=True
  - None ("nothing found") → {"found": false}  (structured, not empty; not an error)
  - any other value (success dict / list) → passed through byte-identical

Handlers in tools.py, the CLI, and output.py are untouched — so the CLI envelope
{ok:false,error:{code,message}} and all handler-level tests stay green. The two
transports use the SAME code+message via each one's native error signal.
"""

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session as client_session

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Symbol
from seam.server.mcp import _finalize, create_server

# ── _finalize unit behavior ─────────────────────────────────────────────────


def test_finalize_passes_through_success_dict() -> None:
    """A normal success payload must be returned unchanged (same object)."""
    payload = {"symbol": "x", "file": "a.py", "callers": []}
    assert _finalize(payload) is payload


def test_finalize_passes_through_list() -> None:
    """A list result (e.g. seam_clusters / seam_search) must pass through unchanged."""
    rows = [{"id": 1}, {"id": 2}]
    assert _finalize(rows) is rows


def test_finalize_none_becomes_found_false() -> None:
    """None (handler found nothing) becomes a structured {found: false}, not empty."""
    assert _finalize(None) == {"found": False}


def test_finalize_error_dict_raises_with_code_and_message() -> None:
    """An error-dict sentinel must raise so FastMCP flips isError=True."""
    with pytest.raises(ToolError) as exc:
        _finalize({"error": "INVALID_INPUT", "message": "target must not be empty"})
    text = str(exc.value)
    assert "INVALID_INPUT" in text
    assert "target must not be empty" in text


def test_finalize_dict_without_message_is_not_an_error() -> None:
    """A dict with 'error' but no 'message' is NOT the sentinel — pass through."""
    payload = {"error_count": 0, "ok": True}
    assert _finalize(payload) is payload


# ── End-to-end MCP wiring (in-memory client → real FastMCP server) ──────────


@pytest.fixture()
def seeded_server() -> tuple[sqlite3.Connection, Path]:
    """Seed a tiny DB with one known symbol and return (conn, root)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)

        src = root / "src" / "auth.py"
        src.parent.mkdir(parents=True)
        src.write_text("# stub\n")
        symbols: list[Symbol] = [
            Symbol(
                name="authenticate_user",
                kind="function",
                file=str(src),
                start_line=1,
                end_line=3,
                docstring="Verify credentials.",
            ),
        ]
        upsert_file(conn, src, "python", "abc123", symbols, [])
        yield conn, root  # type: ignore[misc]
        conn.close()


async def _call(server: object, name: str, args: dict[str, object]) -> tuple[bool, str]:
    """Call a tool through the real MCP stack; return (isError, first content text)."""
    async with client_session(server._mcp_server) as client:  # type: ignore[attr-defined]
        res = await client.call_tool(name, args)
        text = res.content[0].text if res.content else ""
        return res.isError, text


def test_app_error_sets_iserror_true(seeded_server: tuple[sqlite3.Connection, Path]) -> None:
    """seam_impact with a blank target must surface as isError=True with the code."""
    conn, root = seeded_server
    server = create_server(conn, root)
    is_error, text = asyncio.run(_call(server, "seam_impact", {"target": "  "}))
    assert is_error is True
    assert "INVALID_INPUT" in text


def test_not_found_returns_found_false(seeded_server: tuple[sqlite3.Connection, Path]) -> None:
    """seam_context on an unknown symbol returns {found: false}, not empty, not an error."""
    conn, root = seeded_server
    server = create_server(conn, root)
    is_error, text = asyncio.run(_call(server, "seam_context", {"symbol": "no_such_xyz"}))
    assert is_error is False
    assert '"found": false' in text


def test_success_is_unchanged(seeded_server: tuple[sqlite3.Connection, Path]) -> None:
    """A real symbol returns its normal payload with isError=False (regression guard)."""
    conn, root = seeded_server
    server = create_server(conn, root)
    is_error, text = asyncio.run(_call(server, "seam_context", {"symbol": "authenticate_user"}))
    assert is_error is False
    assert "authenticate_user" in text
    assert '"found": false' not in text
