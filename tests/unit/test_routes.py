from __future__ import annotations

import sqlite3
from pathlib import Path

from seam.indexer.db import init_db
from seam.indexer.pipeline import index_one_file
from seam.query.graph_search import graph_search


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_db_has_route_metadata_table() -> None:
    conn = init_db(Path(":memory:"))
    try:
        assert {
            "file_id",
            "symbol_name",
            "method",
            "path",
            "normalized_path",
            "framework",
            "handler",
            "line",
            "confidence",
            "provenance",
        } <= _cols(conn, "routes")
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        assert version is not None
        assert int(version["value"]) >= 13
    finally:
        conn.close()


def test_fastapi_route_indexes_as_route_symbol_with_handler_edge(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    src = root / "api.py"
    src.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/users/{user_id}')\n"
        "def get_user(user_id: str):\n"
        "    return {'id': user_id}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        assert index_one_file(conn, src) == (2, 2)

        routes = graph_search(conn, root=root, kind="route", include_preview=True)

        assert routes["total"] == 1
        route = routes["items"][0]
        assert route["symbol"] == "ROUTE GET /users/{param}"
        assert route["kind"] == "route"
        assert route["file"] == "api.py"
        assert route["line"] == 4
        assert route["preview"][0]["symbol"] == "get_user"
        assert route["preview"][0]["edge_kind"] == "call"

        row = conn.execute(
            """
            SELECT method, path, normalized_path, framework, handler, confidence, provenance
            FROM routes
            WHERE symbol_name = ?
            """,
            ("ROUTE GET /users/{param}",),
        ).fetchone()
        assert dict(row) == {
            "method": "GET",
            "path": "/users/{user_id}",
            "normalized_path": "/users/{param}",
            "framework": "fastapi",
            "handler": "get_user",
            "confidence": "EXTRACTED",
            "provenance": "python-fastapi-decorator",
        }
    finally:
        conn.close()


def test_python_route_variants_and_dynamic_paths_are_conservative(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    src = root / "api.py"
    src.write_text(
        "from fastapi import APIRouter\n"
        "from flask import Flask\n"
        "router = APIRouter()\n"
        "app = Flask(__name__)\n"
        "prefix = '/dynamic'\n"
        "\n"
        "@router.api_route('/things', methods=['GET', 'POST'])\n"
        "def things():\n"
        "    return {}\n"
        "\n"
        "@app.route('/hello/<name>', methods=['POST'])\n"
        "def hello(name):\n"
        "    return name\n"
        "\n"
        "@router.get(prefix)\n"
        "def skipped():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, src)

        rows = conn.execute(
            "SELECT symbol_name, method, path, normalized_path, framework, handler, provenance "
            "FROM routes ORDER BY symbol_name"
        ).fetchall()

        assert [row["symbol_name"] for row in rows] == [
            "ROUTE GET /things",
            "ROUTE POST /hello/{param}",
            "ROUTE POST /things",
        ]
        assert {row["symbol_name"]: row["normalized_path"] for row in rows} == {
            "ROUTE GET /things": "/things",
            "ROUTE POST /hello/{param}": "/hello/{param}",
            "ROUTE POST /things": "/things",
        }
        assert {row["symbol_name"]: row["framework"] for row in rows} == {
            "ROUTE GET /things": "fastapi",
            "ROUTE POST /hello/{param}": "flask",
            "ROUTE POST /things": "fastapi",
        }
        assert {row["symbol_name"]: row["provenance"] for row in rows} == {
            "ROUTE GET /things": "python-fastapi-decorator",
            "ROUTE POST /hello/{param}": "python-flask-decorator",
            "ROUTE POST /things": "python-fastapi-decorator",
        }

        routes = graph_search(conn, root=root, kind="route", sort="name")
        assert routes["total"] == 3
        assert "ROUTE GET /dynamic" not in [item["symbol"] for item in routes["items"]]
    finally:
        conn.close()


def test_python_route_decorators_require_framework_receiver_evidence(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    src = root / "not_routes.py"
    src.write_text(
        "class Cache:\n"
        "    def get(self, key):\n"
        "        def decorate(fn):\n"
        "            return fn\n"
        "        return decorate\n"
        "\n"
        "cache = Cache()\n"
        "\n"
        "@cache.get('/internal-key')\n"
        "def cached():\n"
        "    return None\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, src)

        count = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
        assert count == 0
        routes = graph_search(conn, root=root, kind="route")
        assert routes["total"] == 0
    finally:
        conn.close()


def test_typescript_express_routes_and_literal_http_calls(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = root / "server.ts"
    client = root / "client.ts"
    server.write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "\n"
        "app.get('/api/users/:id', getUser);\n"
        "function getUser(req, res) {\n"
        "  return res.json({});\n"
        "}\n",
        encoding="utf-8",
    )
    client.write_text(
        "import axios from 'axios';\n"
        "export async function loadUser() {\n"
        "  return fetch('/api/users/:id');\n"
        "}\n"
        "export async function createUser(body) {\n"
        "  return axios.post('/api/users/:id', body);\n"
        "}\n"
        "export async function skipped(url) {\n"
        "  return fetch(url);\n"
        "}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, server)
        index_one_file(conn, client)

        route_rows = conn.execute(
            "SELECT symbol_name, method, path, normalized_path, framework, handler, provenance "
            "FROM routes ORDER BY symbol_name"
        ).fetchall()
        assert [dict(row) for row in route_rows] == [
            {
                "symbol_name": "ROUTE GET /api/users/{param}",
                "method": "GET",
                "path": "/api/users/:id",
                "normalized_path": "/api/users/{param}",
                "framework": "express",
                "handler": "getUser",
                "provenance": "typescript-express-registration",
            }
        ]

        route_search = graph_search(conn, root=root, kind="route", include_preview=True)
        assert route_search["total"] == 1
        assert any(
            preview["symbol"] == "getUser" and preview["edge_kind"] == "call"
            for preview in route_search["items"][0]["preview"]
        )

        http_search = graph_search(
            conn,
            root=root,
            edge_kind="http_calls",
            name_pattern="*User",
            direction="outgoing",
            include_preview=True,
            sort="name",
        )
        assert [item["symbol"] for item in http_search["items"]] == ["createUser", "loadUser"]
        previews = {item["symbol"]: item["preview"][0] for item in http_search["items"]}
        assert previews["loadUser"]["symbol"] == "ROUTE GET /api/users/{param}"
        assert previews["createUser"]["symbol"] == "ROUTE POST /api/users/{param}"
        assert previews["loadUser"]["edge_kind"] == "http_calls"
    finally:
        conn.close()
