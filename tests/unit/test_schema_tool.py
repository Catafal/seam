"""Behavior tests for Phase 11 P1.1 — seam_schema / `seam schema`.

The tests exercise public contracts: shared schema description, CLI envelopes,
MCP registration, and the Web diagnostics endpoint. They avoid asserting SQL
implementation details so the introspection module can be refactored freely.
"""

import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 1,
        docstring=None,
        signature=f"def {name}()",
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=name,
    )


def _edge(source: str, target: str, file: str, kind: str = "call") -> Edge:
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=file,
        line=1,
        confidence="EXTRACTED",
    )


def _make_v11_db(db_path: Path) -> None:
    """Create a minimal v11 index: metadata says 11 and edges lacks synthesized_by."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            language TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            mtime REAL NOT NULL,
            indexed_at REAL NOT NULL
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            docstring TEXT,
            cluster_id INTEGER,
            signature TEXT,
            decorators TEXT,
            is_exported INTEGER,
            visibility TEXT,
            qualified_name TEXT,
            entry_score REAL,
            search_text TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'INFERRED',
            receiver TEXT
        );
        CREATE VIRTUAL TABLE symbols_fts USING fts5(
            name, docstring, signature, search_text,
            content='symbols', content_rowid='id'
        );
        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL,
            marker TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            size INTEGER NOT NULL,
            naming_source TEXT NOT NULL,
            cohesion REAL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata(key, value) VALUES ('schema_version', '11');
        INSERT INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def schema_repo() -> tuple[sqlite3.Connection, Path, Path]:
    """Yield (conn, root, db_path) with a small populated Seam index."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir()
        src = root / "app.py"
        src.write_text("def entry():\n    helper()\n\ndef helper():\n    pass\n")
        conn = init_db(db_path)
        upsert_file(
            conn,
            src,
            "python",
            "hash1",
            [
                _sym("entry", str(src), line=1),
                _sym("helper", str(src), line=4),
                _sym("ROUTE GET /health", str(src), kind="route", line=1),
            ],
            [
                _edge("entry", "helper", str(src)),
                _edge("entry", "ROUTE GET /health", str(src), kind="http_calls"),
                _edge("entry", "ValueError", str(src), kind="raises"),
                _edge("entry", "Exception", str(src), kind="catches"),
            ],
            routes=[
                {
                    "symbol_name": "ROUTE GET /health",
                    "method": "GET",
                    "path": "/health",
                    "normalized_path": "/health",
                    "framework": "fastapi",
                    "handler": "entry",
                    "line": 1,
                    "confidence": "EXTRACTED",
                    "provenance": "python-fastapi-decorator",
                }
            ],
        )
        conn.execute(
            "INSERT INTO comments (file_id, line, marker, text) "
            "SELECT id, 1, 'WHY', 'important reason' FROM files WHERE path = ?",
            (str(src),),
        )
        conn.execute(
            "INSERT INTO import_mappings "
            "(file_id, local_name, exported_name, source_module, is_default, is_namespace, is_wildcard, line) "
            "SELECT id, 'helper', 'helper', 'app', 0, 0, 0, 1 FROM files WHERE path = ?",
            (str(src),),
        )
        conn.execute(
            "INSERT INTO clusters (label, size, naming_source, cohesion) "
            "VALUES ('core', 2, 'deterministic', 1.0)"
        )
        cluster_id = conn.execute("SELECT id FROM clusters WHERE label = 'core'").fetchone()[0]
        conn.execute("UPDATE symbols SET cluster_id = ?", (cluster_id,))
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vector) "
            "SELECT id, 'test-model', 2, ? FROM symbols WHERE name = 'entry'",
            (b"\x00" * 8,),
        )
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence, synthesized_by) "
            "SELECT 'entry', 'helper', 'call', id, 1, 'INFERRED', 'interface-override' "
            "FROM files WHERE path = ?",
            (str(src),),
        )
        conn.commit()
        yield conn, root, db_path
        conn.close()


def test_describe_schema_summary_reports_capabilities(schema_repo, monkeypatch) -> None:
    """Summary mode reports counts, capabilities, warnings, and next-call guidance."""
    from seam.query.schema import describe_schema

    conn, root, _ = schema_repo
    monkeypatch.setattr("seam.config.SEAM_EMBED_MODEL", "test-model")

    result = describe_schema(conn, root=root)

    assert result["schema_version"] >= 12
    assert result["counts"]["files"] == 1
    assert result["counts"]["symbols"] == 3
    assert result["counts"]["edges"] == 5
    assert result["counts"]["clusters"] == 1
    assert result["counts"]["comments"] == 1
    assert result["counts"]["import_mappings"] == 1
    assert result["counts"]["embeddings"] == 1
    assert result["counts"]["routes"] == 1
    assert result["breakdowns"]["symbol_kinds"]["function"] == 2
    assert result["breakdowns"]["symbol_kinds"]["route"] == 1
    assert result["breakdowns"]["edge_kinds"]["call"] == 2
    assert result["breakdowns"]["edge_kinds"]["raises"] == 1
    assert result["breakdowns"]["edge_kinds"]["catches"] == 1
    assert result["breakdowns"]["edge_kinds"]["http_calls"] == 1
    assert result["breakdowns"]["edge_confidence"]["EXTRACTED"] == 4
    assert result["capabilities"]["has_clusters"] is True
    assert result["capabilities"]["has_embeddings"] is True
    assert result["capabilities"]["embedding_model_matches"] is True
    assert result["capabilities"]["has_synthesized_edges"] is True
    assert result["capabilities"]["has_routes_table"] is True
    assert result["capabilities"]["has_route_nodes"] is True
    assert result["capabilities"]["has_http_calls"] is True
    assert result["capabilities"]["has_exception_edges"] is True
    assert result["freshness"]["stale"] is False
    assert any(t["name"] == "seam_schema" for t in result["tools"])
    assert any(t["name"] == "seam_architecture" for t in result["tools"])
    assert any(t["name"] == "seam_snippet" for t in result["tools"])
    assert any(t["name"] == "seam_graph_search" for t in result["tools"])
    assert any("seam_architecture" in call for call in result["recommended_next_calls"])
    assert any("seam_graph_search" in call for call in result["recommended_next_calls"])
    assert any("seam_snippet" in call for call in result["recommended_next_calls"])
    assert result["recommended_next_calls"]
    assert "tables" not in result


def test_describe_schema_verbose_reports_missing_optional_table(schema_repo) -> None:
    """Verbose mode reports table/column metadata and missing optional capabilities."""
    from seam.query.schema import describe_schema

    conn, root, _ = schema_repo
    conn.execute("DROP TABLE embeddings")
    conn.commit()

    result = describe_schema(conn, root=root, verbose=True)

    assert "tables" in result
    assert result["tables"]["symbols"]["exists"] is True
    assert result["tables"]["symbols"]["columns"]["signature"]["exists"] is True
    assert result["tables"]["embeddings"]["exists"] is False
    assert result["capabilities"]["has_embeddings"] is False
    assert any(w["code"] == "MISSING_OPTIONAL_TABLE" for w in result["warnings"])


def test_describe_schema_warns_when_index_is_stale(schema_repo) -> None:
    """The schema payload uses the shared staleness detector and surfaces warnings."""
    from seam.query.schema import describe_schema

    conn, root, _ = schema_repo
    src = root / "app.py"
    conn.execute("UPDATE files SET mtime = ? WHERE path = ?", (time.time() - 100, str(src)))
    conn.commit()

    result = describe_schema(conn, root=root)

    assert result["freshness"]["stale"] is True
    assert any(w["code"] == "INDEX_STALE" for w in result["warnings"])


def test_schema_cli_json_and_quiet(schema_repo) -> None:
    """`seam schema` supports the same JSON and quiet CLI contracts as read commands."""
    from seam.cli.main import app

    conn, root, _ = schema_repo
    conn.close()
    runner = CliRunner()

    json_result = runner.invoke(app, ["schema", str(root), "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert payload["data"]["counts"]["symbols"] == 3

    quiet_result = runner.invoke(app, ["schema", str(root), "--quiet"])
    assert quiet_result.exit_code == 0, quiet_result.output
    assert "freshness=fresh" in quiet_result.output
    assert "symbols=3" in quiet_result.output


def test_schema_cli_verbose_and_no_index(schema_repo, tmp_path: Path) -> None:
    """Verbose mode adds table metadata; no-index keeps the standard error envelope."""
    from seam.cli.main import app

    conn, root, _ = schema_repo
    conn.close()
    runner = CliRunner()

    verbose_result = runner.invoke(app, ["schema", str(root), "--json", "--verbose"])
    assert verbose_result.exit_code == 0, verbose_result.output
    payload = json.loads(verbose_result.output)
    assert payload["data"]["tables"]["symbols"]["exists"] is True

    no_index_result = runner.invoke(app, ["schema", str(tmp_path), "--json"])
    assert no_index_result.exit_code == 1
    error = json.loads(no_index_result.output)
    assert error["ok"] is False
    assert error["error"]["code"] == "NO_INDEX"


def test_schema_cli_and_web_do_not_migrate_v11_index(tmp_path: Path) -> None:
    """Schema diagnostics must inspect an old index without auto-running migrations."""
    from fastapi.testclient import TestClient

    from seam.cli.main import app
    from seam.server.web import create_web_app

    root = tmp_path
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    _make_v11_db(db_path)
    runner = CliRunner()

    cli_result = runner.invoke(app, ["schema", str(root), "--json"])
    assert cli_result.exit_code == 0, cli_result.output
    cli_payload = json.loads(cli_result.output)
    assert cli_payload["data"]["schema_version"] == 11
    assert cli_payload["data"]["capabilities"]["has_synthesized_by_column"] is False

    web_client = TestClient(create_web_app(db_path=db_path, root=root))
    web_result = web_client.get("/api/schema")
    assert web_result.status_code == 200
    assert web_result.json()["schema_version"] == 11

    raw = sqlite3.connect(str(db_path))
    try:
        version = raw.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
        edge_columns = {row[1] for row in raw.execute("PRAGMA table_info(edges)").fetchall()}
    finally:
        raw.close()
    assert version == "11"
    assert "synthesized_by" not in edge_columns


def test_schema_mcp_registration(schema_repo) -> None:
    """The MCP server advertises seam_schema with the rest of the read tools."""
    from seam.server.mcp import create_server

    conn, root, _ = schema_repo
    server = create_server(conn, root)

    tool_names = list(server._tool_manager._tools.keys())
    assert "seam_schema" in tool_names
    assert "seam_architecture" in tool_names
    assert "seam_snippet" in tool_names
    assert "seam_graph_search" in tool_names
    assert len(tool_names) == 16


def test_schema_web_endpoint(schema_repo) -> None:
    """The Explorer API exposes the same diagnostics payload without source leakage."""
    from fastapi.testclient import TestClient

    from seam.server.web import create_web_app

    conn, root, db_path = schema_repo
    conn.close()

    client = TestClient(create_web_app(db_path=db_path, root=root))
    response = client.get("/api/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["symbols"] == 3
    assert body["capabilities"]["has_clusters"] is True
    assert "important reason" not in json.dumps(body)

    verbose = client.get("/api/schema?verbose=true")
    assert verbose.status_code == 200
    assert verbose.json()["tables"]["symbols"]["exists"] is True
