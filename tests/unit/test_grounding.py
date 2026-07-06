"""Tests for docs/spec grounding."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app
from seam.indexer.db import init_db
from seam.indexer.docs import classify_doc, extract_document
from seam.indexer.pipeline import index_one_file
from seam.query.grounding import query_grounding
from seam.server.mcp import create_server
from seam.server.tools import handle_seam_grounding, handle_seam_schema

runner = CliRunner()


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _make_grounding_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    root = tmp_path.resolve()
    src = root / "app.py"
    docs = root / "docs" / "prd" / "order-processing.md"
    docs.parent.mkdir(parents=True)
    src.write_text(
        "def process_order(order_id: str) -> str:\n"
        "    return order_id\n",
        encoding="utf-8",
    )
    docs.write_text(
        "# Order Processing PRD\n\n"
        "> Status: ready-for-agent.\n\n"
        "## Implementation\n\n"
        "The implementation lives in [app.py](../../app.py).\n"
        "The key symbol is `process_order`.\n"
        "The route contract mentions `/orders/{id}`.\n"
        "The setting name is `ORDER_API_KEY`, but no value is stored.\n"
        "Related issue #371.\n",
        encoding="utf-8",
    )
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, src, root=root) == (1, 0)
    assert index_one_file(conn, docs, root=root) == (0, 0)
    return conn, root


def test_markdown_parser_classifies_status_and_explicit_references(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    doc = root / "docs" / "adr" / "001-choice.md"
    doc.parent.mkdir(parents=True)
    text = (
        "# SQLite Decision\n\n"
        "> Status: shipped.\n\n"
        "See [pipeline](../../seam/indexer/pipeline.py), `init_db`, and #12.\n"
    )
    doc.write_text(text, encoding="utf-8")

    kind, status, title = classify_doc(doc, text)
    extracted, refs = extract_document(doc, root, text)

    assert kind == "adr"
    assert status == "shipped"
    assert title == "SQLite Decision"
    assert extracted["anchors"][0]["heading_path"] == "SQLite Decision"
    assert any(ref.target_kind == "file" for ref in refs)
    assert any(ref.target_kind == "symbol" and ref.target_value == "init_db" for ref in refs)
    assert any(ref.target_kind == "issue" and ref.target_value == "#12" for ref in refs)


def test_grounding_resolves_symbol_file_and_snippet(tmp_path: Path) -> None:
    conn, root = _make_grounding_repo(tmp_path)
    try:
        by_symbol = handle_seam_grounding(
            conn,
            root,
            symbol="process_order",
            include_snippets=True,
        )
        by_file = handle_seam_grounding(conn, root, file="app.py")
        by_query = handle_seam_grounding(conn, root, query="route contract", limit=10)
    finally:
        conn.close()

    assert by_symbol["found"] is True
    first = by_symbol["candidates"][0]
    assert first["doc_path"] == "docs/prd/order-processing.md"
    assert first["doc_kind"] == "prd"
    assert first["status"] == "ready-for-agent"
    assert first["target"]["resolved_value"] == "process_order"
    assert first["confidence"] == "HIGH"
    assert "process_order" in first["snippet"]
    assert by_file["found"] is True
    assert by_file["candidates"][0]["relation_type"] == "mentions_file"
    assert by_query["summary"]["by_relation_type"]["mentions_route"] == 1
    assert "Document grounding is explicit" in by_symbol["caveats"][0]


def test_grounding_filters_doc_path_and_limit_zero(tmp_path: Path) -> None:
    conn, root = _make_grounding_repo(tmp_path)
    try:
        none = handle_seam_grounding(conn, root, doc_path="missing.md")
        zero = handle_seam_grounding(conn, root, query="Order", limit=0)
    finally:
        conn.close()

    assert none["found"] is False
    assert none["summary"]["total"] == 0
    assert zero["candidates"] == []
    assert zero["omitted"]["candidates"] >= 1


def test_grounding_query_degrades_on_old_index() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    try:
        result = query_grounding(conn, Path.cwd(), query="anything")
    finally:
        conn.close()

    assert result["found"] is False
    assert result["warnings"][0]["code"] == "UNSUPPORTED"


def test_v16_migration_adds_document_grounding_tables() -> None:
    from seam.indexer.migrations import _run_migration_v15_to_v16

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO metadata VALUES ('schema_version', '15')")
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
        conn.commit()
        _run_migration_v15_to_v16(conn)
        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()["value"]
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    assert version == "16"
    assert {"document_files", "document_anchors", "document_references"} <= tables


def test_grounding_cli_json_and_quiet(tmp_path: Path) -> None:
    conn, root = _make_grounding_repo(tmp_path)
    conn.close()

    json_result = runner.invoke(
        app,
        [
            "grounding",
            "--path",
            str(root),
            "--symbol",
            "process_order",
            "--json",
        ],
    )
    quiet_result = runner.invoke(
        app,
        [
            "grounding",
            "--path",
            str(root),
            "--symbol",
            "process_order",
            "--quiet",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert '"found": true' in json_result.output
    assert quiet_result.exit_code == 0, quiet_result.output
    assert "docs/prd/order-processing.md" in quiet_result.output


def test_grounding_schema_and_mcp_registration(tmp_path: Path) -> None:
    conn, root = _make_grounding_repo(tmp_path)
    try:
        schema = handle_seam_schema(conn, root)
        tool_names = {tool["name"] for tool in schema["tools"]}
        server = create_server(conn, root)
        mcp_tool_names = {tool.name for tool in server._tool_manager.list_tools()}  # type: ignore[attr-defined]
    finally:
        conn.close()

    assert schema["counts"]["document_files"] == 1
    assert schema["capabilities"]["has_doc_grounding"] is True
    assert "seam_grounding" in tool_names
    assert "seam_grounding" in mcp_tool_names
