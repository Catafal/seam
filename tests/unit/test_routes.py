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
        assert "provenance" in _cols(conn, "edges")
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        assert version is not None
        assert int(version["value"]) >= 15
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
        assert previews["loadUser"]["synthesized_by"] is None
        assert previews["loadUser"]["provenance"] == "typescript-fetch-literal"
        assert previews["loadUser"]["route_resolved"] is True
        assert previews["createUser"]["synthesized_by"] is None
        assert previews["createUser"]["provenance"] == "typescript-axios-literal"
        assert previews["createUser"]["route_resolved"] is False
    finally:
        conn.close()


def test_http_calls_normalize_literals_and_skip_untrusted_urls(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = root / "server.ts"
    client = root / "client.ts"
    server.write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "\n"
        "app.get('/api/users', listUsers);\n"
        "function listUsers(req, res) {\n"
        "  return res.json([]);\n"
        "}\n",
        encoding="utf-8",
    )
    client.write_text(
        "export async function withQueryAndFragment() {\n"
        "  return fetch('/api/users?tab=active#top');\n"
        "}\n"
        "export async function external() {\n"
        "  return fetch('https://example.com/api/users');\n"
        "}\n"
        "export async function wrongMethod() {\n"
        "  return fetch('/api/users', { method: 'POST' });\n"
        "}\n"
        "export async function dynamic(url) {\n"
        "  return fetch(url);\n"
        "}\n"
        "export async function interpolated(id) {\n"
        "  return fetch(`/api/${id}`);\n"
        "}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, server)
        index_one_file(conn, client)

        route_search = graph_search(
            conn,
            root=root,
            kind="route",
            edge_kind="http_calls",
            direction="incoming",
            include_preview=True,
        )

        assert route_search["total"] == 1
        route = route_search["items"][0]
        assert route["symbol"] == "ROUTE GET /api/users"
        assert [preview["symbol"] for preview in route["preview"]] == ["withQueryAndFragment"]
        assert route["preview"][0]["route_resolved"] is True

        http_edges = conn.execute(
            """
            SELECT source_name, target_name, synthesized_by, provenance
            FROM edges
            WHERE kind = 'http_calls'
            ORDER BY source_name
            """
        ).fetchall()
        assert [dict(row) for row in http_edges] == [
            {
                "source_name": "withQueryAndFragment",
                "target_name": "ROUTE GET /api/users",
                "synthesized_by": None,
                "provenance": "typescript-fetch-literal",
            },
            {
                "source_name": "wrongMethod",
                "target_name": "ROUTE POST /api/users",
                "synthesized_by": None,
                "provenance": "typescript-fetch-literal",
            },
        ]
        outgoing = graph_search(
            conn,
            root=root,
            edge_kind="http_calls",
            direction="outgoing",
            include_preview=True,
            sort="name",
        )
        outgoing_previews = {item["symbol"]: item["preview"][0] for item in outgoing["items"]}
        assert outgoing_previews["withQueryAndFragment"]["route_resolved"] is True
        assert outgoing_previews["wrongMethod"]["route_resolved"] is False
    finally:
        conn.close()


def test_typescript_and_javascript_http_call_surfaces_report_schema(tmp_path: Path) -> None:
    from seam.query.schema import describe_schema

    root = tmp_path.resolve()
    server = root / "server.js"
    ts_client = root / "client.ts"
    js_client = root / "client.js"
    server.write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "\n"
        "app.post('/api/orders', createOrder);\n"
        "function createOrder(req, res) {\n"
        "  return res.json({});\n"
        "}\n",
        encoding="utf-8",
    )
    ts_client.write_text(
        "import axios from 'axios';\n"
        "export async function createViaFetch(body) {\n"
        "  return fetch('/api/orders', { method: 'POST' });\n"
        "}\n"
        "export async function createViaAxios(body) {\n"
        "  return axios.post('/api/orders', body);\n"
        "}\n",
        encoding="utf-8",
    )
    js_client.write_text(
        "const axios = require('axios');\n"
        "export async function createViaConfig(body) {\n"
        "  return axios({ url: '/api/orders', method: 'POST', data: body });\n"
        "}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, server)
        index_one_file(conn, ts_client)
        index_one_file(conn, js_client)

        schema = describe_schema(conn, root=root)
        assert schema["capabilities"]["has_http_calls"] is True

        callers = graph_search(
            conn,
            root=root,
            edge_kind="http_calls",
            direction="outgoing",
            include_preview=True,
            sort="name",
        )
        assert [item["symbol"] for item in callers["items"]] == [
            "createViaAxios",
            "createViaConfig",
            "createViaFetch",
        ]
        assert all(
            item["preview"][0]["symbol"] == "ROUTE POST /api/orders"
            for item in callers["items"]
        )
        assert {
            item["symbol"]: (
                item["preview"][0]["synthesized_by"],
                item["preview"][0]["provenance"],
            )
            for item in callers["items"]
        } == {
            "createViaAxios": (None, "typescript-axios-literal"),
            "createViaConfig": (None, "javascript-axios-literal"),
            "createViaFetch": (None, "typescript-fetch-literal"),
        }

        routes = graph_search(
            conn,
            root=root,
            kind="route",
            edge_kind="http_calls",
            direction="incoming",
            include_preview=True,
            preview_limit=5,
        )
        assert routes["items"][0]["symbol"] == "ROUTE POST /api/orders"
        assert sorted(preview["symbol"] for preview in routes["items"][0]["preview"]) == [
            "createViaAxios",
            "createViaConfig",
            "createViaFetch",
        ]
    finally:
        conn.close()


def test_python_requests_and_httpx_literal_http_calls(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = root / "api.py"
    client = root / "client.py"
    server.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/api/users')\n"
        "def list_users():\n"
        "    return []\n"
        "\n"
        "@app.post('/api/orders')\n"
        "def create_order():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    client.write_text(
        "import requests\n"
        "import httpx\n"
        "\n"
        "session = requests.Session()\n"
        "client = httpx.Client()\n"
        "\n"
        "def load_users():\n"
        "    return requests.get('/api/users?active=1')\n"
        "\n"
        "def create_remote_order():\n"
        "    return httpx.post('/api/orders')\n"
        "\n"
        "def load_with_session():\n"
        "    return session.get('/api/users')\n"
        "\n"
        "def create_with_client():\n"
        "    return client.post('/api/orders')\n"
        "\n"
        "def external():\n"
        "    return requests.get('https://example.com/api/users')\n"
        "\n"
        "def dynamic(path):\n"
        "    return httpx.get(path)\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, server)
        index_one_file(conn, client)

        callers = graph_search(
            conn,
            root=root,
            edge_kind="http_calls",
            direction="outgoing",
            include_preview=True,
            sort="name",
        )

        assert [item["symbol"] for item in callers["items"]] == [
            "create_remote_order",
            "create_with_client",
            "load_users",
            "load_with_session",
        ]
        previews = {item["symbol"]: item["preview"][0] for item in callers["items"]}
        assert {symbol: preview["symbol"] for symbol, preview in previews.items()} == {
            "create_with_client": "ROUTE POST /api/orders",
            "create_remote_order": "ROUTE POST /api/orders",
            "load_users": "ROUTE GET /api/users",
            "load_with_session": "ROUTE GET /api/users",
        }
        assert {symbol: preview["synthesized_by"] for symbol, preview in previews.items()} == {
            "create_with_client": None,
            "create_remote_order": None,
            "load_users": None,
            "load_with_session": None,
        }
        assert {symbol: preview["provenance"] for symbol, preview in previews.items()} == {
            "create_with_client": "python-httpx-client-literal",
            "create_remote_order": "python-httpx-literal",
            "load_users": "python-requests-literal",
            "load_with_session": "python-requests-client-literal",
        }
    finally:
        conn.close()
