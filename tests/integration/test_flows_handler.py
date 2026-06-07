"""Integration tests for handle_seam_flows + the seam_flows MCP registration.

  F1 — list mode: {"entry_points": [...]} with relativized file paths
  F2 — drill mode: a Flow tree for a known entry
  F3 — unknown entry: handler returns None (MCP boundary → {"found": false})
  F4 — blank entry: INVALID_INPUT sentinel
  F5 — seam_flows is registered (tool count 12 with seam_structure) and callable via the server
"""

import sqlite3
from pathlib import Path

from seam.indexer.db import init_db
from seam.server.mcp import create_server
from seam.server.tools import handle_seam_flows


def _seed(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Seed: main -> step1 -> step2 (main is a root). Returns (conn, project_root)."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir()
    src = tmp_path / "app.py"
    src.write_text("x = 1\n")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (?, 'python', 'abc', 1.0, 1.0)",
        (str(src),),
    )
    fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for name in ["main", "step1", "step2"]:
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, ?, 'function', 1, 5)",
            (fid, name),
        )
    for s, t in [("main", "step1"), ("step1", "step2")]:
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " VALUES (?, ?, 'call', ?, 1, 'EXTRACTED')",
            (s, t, fid),
        )
    conn.commit()
    return conn, tmp_path


def test_flows_list_mode(tmp_path: Path) -> None:
    conn, root = _seed(tmp_path)
    try:
        result = handle_seam_flows(conn, root=root, entry=None)
    finally:
        conn.close()
    assert "entry_points" in result
    names = [p["name"] for p in result["entry_points"]]
    assert "main" in names
    main = next(p for p in result["entry_points"] if p["name"] == "main")
    assert main["file"] == "app.py"  # relativized
    assert main["reach"] == 2


def test_flows_drill_mode(tmp_path: Path) -> None:
    conn, root = _seed(tmp_path)
    try:
        flow = handle_seam_flows(conn, root=root, entry="main")
    finally:
        conn.close()
    assert flow["entry"] == "main"
    assert [s["name"] for s in flow["steps"]] == ["step1"]
    assert [c["name"] for c in flow["steps"][0]["children"]] == ["step2"]


def test_flows_unknown_entry_returns_none(tmp_path: Path) -> None:
    conn, root = _seed(tmp_path)
    try:
        assert handle_seam_flows(conn, root=root, entry="ghost") is None
    finally:
        conn.close()


def test_flows_blank_entry_invalid_input(tmp_path: Path) -> None:
    conn, root = _seed(tmp_path)
    try:
        result = handle_seam_flows(conn, root=root, entry="   ")
    finally:
        conn.close()
    assert result == {"error": "INVALID_INPUT", "message": result["message"]}


def test_seam_flows_registered(tmp_path: Path) -> None:
    conn, root = _seed(tmp_path)
    server = create_server(conn, root)
    conn.close()
    tool_names = list(server._tool_manager._tools.keys())
    assert "seam_flows" in tool_names
    assert len(tool_names) == 12
