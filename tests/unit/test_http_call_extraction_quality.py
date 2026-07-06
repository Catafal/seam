from __future__ import annotations

from pathlib import Path

from seam.indexer.db import init_db
from seam.indexer.pipeline import index_one_file
from seam.query.graph_search import graph_search
from seam.query.schema import describe_schema


def test_typescript_local_api_wrapper_literal_calls_resolve_to_routes(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = root / "server.ts"
    client = root / "client.ts"
    server.write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "\n"
        "app.get('/api/schema', getSchema);\n"
        "app.post('/api/search', search);\n"
        "function getSchema(req, res) { return res.json({}); }\n"
        "function search(req, res) { return res.json([]); }\n",
        encoding="utf-8",
    )
    client.write_text(
        "import { apiFetch } from './client';\n"
        "\n"
        "export async function loadSchema() {\n"
        "  return apiFetch('/api/schema?fresh=1');\n"
        "}\n"
        "\n"
        "export async function runSearch() {\n"
        "  return apiFetch('/api/search', { method: 'POST' });\n"
        "}\n"
        "\n"
        "export async function dynamic(path) {\n"
        "  return apiFetch(path);\n"
        "}\n"
        "\n"
        "export async function notHttp() {\n"
        "  return localHelper('/api/schema');\n"
        "}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, server)
        index_one_file(conn, client)

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
        assert [item["symbol"] for item in callers["items"]] == ["loadSchema", "runSearch"]
        previews = {item["symbol"]: item["preview"][0] for item in callers["items"]}
        assert previews["loadSchema"]["symbol"] == "ROUTE GET /api/schema"
        assert previews["loadSchema"]["route_resolved"] is True
        assert previews["loadSchema"]["provenance"] == "typescript-local-wrapper-literal"
        assert previews["runSearch"]["symbol"] == "ROUTE POST /api/search"
        assert previews["runSearch"]["route_resolved"] is True
        assert previews["runSearch"]["provenance"] == "typescript-local-wrapper-literal"
    finally:
        conn.close()


def test_python_request_generic_and_aiohttp_literal_http_calls(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = root / "api.py"
    client = root / "client.py"
    server.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.put('/api/users/{user_id}')\n"
        "def update_user(user_id: str):\n"
        "    return {}\n"
        "\n"
        "@app.delete('/api/users/{user_id}')\n"
        "def delete_user(user_id: str):\n"
        "    return {}\n",
        encoding="utf-8",
    )
    client.write_text(
        "import requests\n"
        "import aiohttp\n"
        "\n"
        "async def update_with_request():\n"
        "    return requests.request('PUT', '/api/users/{user_id}')\n"
        "\n"
        "async def delete_with_aiohttp():\n"
        "    session = aiohttp.ClientSession()\n"
        "    return await session.delete('/api/users/{user_id}#confirm')\n"
        "\n"
        "async def dynamic_with_aiohttp(path):\n"
        "    session = aiohttp.ClientSession()\n"
        "    return await session.delete(path)\n",
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
            "delete_with_aiohttp",
            "update_with_request",
        ]
        previews = {item["symbol"]: item["preview"][0] for item in callers["items"]}
        assert previews["update_with_request"]["symbol"] == "ROUTE PUT /api/users/{param}"
        assert previews["update_with_request"]["route_resolved"] is True
        assert previews["update_with_request"]["provenance"] == "python-requests-request-literal"
        assert previews["delete_with_aiohttp"]["symbol"] == "ROUTE DELETE /api/users/{param}"
        assert previews["delete_with_aiohttp"]["route_resolved"] is True
        assert previews["delete_with_aiohttp"]["provenance"] == "python-aiohttp-client-literal"
    finally:
        conn.close()


def test_python_client_receiver_scope_does_not_leak_between_functions(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = root / "api.py"
    client = root / "client.py"
    server.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/api/scope')\n"
        "def scope_status():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    client.write_text(
        "import aiohttp\n"
        "import requests\n"
        "\n"
        "session = requests.Session()\n"
        "\n"
        "def module_receiver():\n"
        "    return session.get('/api/scope')\n"
        "\n"
        "def parameter_shadow(session):\n"
        "    return session.get('/api/scope')\n"
        "\n"
        "def assignment_shadow():\n"
        "    session = object()\n"
        "    return session.get('/api/scope')\n"
        "\n"
        "async def local_aiohttp_client():\n"
        "    client = aiohttp.ClientSession()\n"
        "    return await client.get('/api/scope')\n"
        "\n"
        "async def cross_scope_leak():\n"
        "    return await client.get('/api/scope')\n",
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
            "local_aiohttp_client",
            "module_receiver",
        ]
        previews = {item["symbol"]: item["preview"][0] for item in callers["items"]}
        assert previews["module_receiver"]["provenance"] == "python-requests-client-literal"
        assert previews["local_aiohttp_client"]["provenance"] == "python-aiohttp-client-literal"
    finally:
        conn.close()
