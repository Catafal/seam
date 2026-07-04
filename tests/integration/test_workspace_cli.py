from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from seam.cli.main import app
from seam.indexer.db import init_db
from seam.indexer.pipeline import index_one_file

runner = CliRunner()


def _indexed_repo(root: Path, filename: str = "app.py") -> Path:
    root.mkdir(parents=True)
    source = root / filename
    source.write_text(
        "def public_symbol():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    try:
        index_one_file(conn, source)
    finally:
        conn.close()
    return root


def _indexed_repo_with_source(root: Path, filename: str, text: str) -> Path:
    root.mkdir(parents=True)
    source = root / filename
    source.write_text(text, encoding="utf-8")
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    try:
        index_one_file(conn, source)
    finally:
        conn.close()
    return root


def _payload(result) -> dict:
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    return envelope["data"]


def test_workspace_registry_status_is_explicit_and_does_not_mutate_child_repo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    child = _indexed_repo(tmp_path / "api")

    created = _payload(runner.invoke(app, ["workspace", "init", str(workspace), "--json"]))
    assert created["workspace"]["root"] == str(workspace.resolve())

    added = _payload(
        runner.invoke(app, ["workspace", "add", "api", str(child), str(workspace), "--json"])
    )
    assert added["repo"]["alias"] == "api"
    assert added["repo"]["root"] == str(child.resolve())

    listed = _payload(runner.invoke(app, ["workspace", "list", str(workspace), "--json"]))
    assert [repo["alias"] for repo in listed["repos"]] == ["api"]
    assert "absolute_path" not in listed["repos"][0]

    status = _payload(runner.invoke(app, ["workspace", "status", str(workspace), "--json"]))
    assert status["repos"][0]["alias"] == "api"
    assert status["repos"][0]["state"] == "ready"
    assert status["repos"][0]["schema_version"] >= 15
    assert status["repos"][0]["freshness"]["stale"] is False

    assert (workspace / ".seam" / "workspace.json").exists()
    assert not (child / ".seam" / "workspace.json").exists()


def test_workspace_graph_search_and_snippet_are_repo_qualified(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    api = _indexed_repo_with_source(
        tmp_path / "api",
        "api.py",
        "def api_helper():\n"
        "    return 'api'\n"
        "\n"
        "def api_symbol():\n"
        "    return api_helper()\n",
    )
    web = _indexed_repo_with_source(
        tmp_path / "web",
        "web.py",
        "def web_symbol():\n"
        "    return 'web'\n",
    )
    _payload(runner.invoke(app, ["workspace", "init", str(workspace), "--json"]))
    _payload(runner.invoke(app, ["workspace", "add", "api", str(api), str(workspace), "--json"]))
    _payload(runner.invoke(app, ["workspace", "add", "web", str(web), str(workspace), "--json"]))

    search = _payload(
        runner.invoke(
            app,
            [
                "workspace",
                "graph-search",
                str(workspace),
                "--kind",
                "function",
                "--name",
                "*_symbol",
                "--preview",
                "--json",
            ],
        )
    )

    assert {repo["alias"] for repo in search["repos"]} == {"api", "web"}
    assert {item["repo"]["alias"] for item in search["items"]} == {"api", "web"}
    api_item = next(item for item in search["items"] if item["symbol"] == "api_symbol")
    assert api_item["uid"].startswith("api:")
    assert api_item["local_uid"] in api_item["uid"]
    assert api_item["preview"]
    assert all(preview["uid"].startswith("api:") for preview in api_item["preview"])
    assert all(preview["local_uid"] in preview["uid"] for preview in api_item["preview"])

    snippet = _payload(
        runner.invoke(
            app,
            ["workspace", "snippet", str(workspace), "--uid", api_item["uid"], "--json"],
        )
    )
    assert snippet["repo"]["alias"] == "api"
    assert snippet["symbol"] == "api_symbol"
    assert "return api_helper()" in snippet["source"]


def test_workspace_graph_search_truncated_only_when_flat_results_exceed_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    api = _indexed_repo_with_source(
        tmp_path / "api",
        "api.py",
        "def one_symbol():\n"
        "    return 1\n",
    )
    web = _indexed_repo_with_source(
        tmp_path / "web",
        "web.py",
        "def two_symbol():\n"
        "    return 2\n",
    )
    worker = _indexed_repo_with_source(
        tmp_path / "worker",
        "worker.py",
        "def three_symbol():\n"
        "    return 3\n",
    )
    _payload(runner.invoke(app, ["workspace", "init", str(workspace), "--json"]))
    for alias, repo in (("api", api), ("web", web), ("worker", worker)):
        _payload(runner.invoke(app, ["workspace", "add", alias, str(repo), str(workspace), "--json"]))

    exact = _payload(
        runner.invoke(
            app,
            [
                "workspace",
                "graph-search",
                str(workspace),
                "--kind",
                "function",
                "--name",
                "*_symbol",
                "--limit",
                "3",
                "--json",
            ],
        )
    )
    assert len(exact["items"]) == 3
    assert exact["truncated"] is False

    over = _payload(
        runner.invoke(
            app,
            [
                "workspace",
                "graph-search",
                str(workspace),
                "--kind",
                "function",
                "--name",
                "*_symbol",
                "--limit",
                "2",
                "--json",
            ],
        )
    )
    assert len(over["items"]) == 2
    assert over["truncated"] is True


def test_workspace_route_callers_match_http_edges_across_repos(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    api = _indexed_repo_with_source(
        tmp_path / "api",
        "api.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/api/users')\n"
        "def list_users():\n"
        "    return []\n",
    )
    web = _indexed_repo_with_source(
        tmp_path / "web",
        "client.ts",
        "function loadUsers() {\n"
        "  return fetch('/api/users?active=1')\n"
        "}\n"
        "\n"
        "function dynamic(path: string) {\n"
        "  return fetch(path)\n"
        "}\n",
    )
    _payload(runner.invoke(app, ["workspace", "init", str(workspace), "--json"]))
    _payload(runner.invoke(app, ["workspace", "add", "api", str(api), str(workspace), "--json"]))
    _payload(runner.invoke(app, ["workspace", "add", "web", str(web), str(workspace), "--json"]))

    result = _payload(
        runner.invoke(
            app,
            [
                "workspace",
                "route-callers",
                str(workspace),
                "--method",
                "GET",
                "--path",
                "/api/users",
                "--json",
            ],
        )
    )

    assert len(result["links"]) == 1
    link = result["links"][0]
    assert link["route"]["repo"]["alias"] == "api"
    assert link["caller"]["repo"]["alias"] == "web"
    assert link["route"]["symbol"] == "ROUTE GET /api/users"
    assert link["caller"]["symbol"] == "loadUsers"
    assert link["edge_kind"] == "http_calls"
    assert link["derived"] is True

    impact = _payload(
        runner.invoke(
            app,
            ["workspace", "impact", str(workspace), "ROUTE GET /api/users", "--json"],
        )
    )
    evidence = [
        item
        for repo in impact["repos"]
        for item in repo["cross_repo_evidence"]
        if item["kind"] == "http_calls"
    ]
    assert evidence
    assert evidence[0]["source_repo"] == "web"
    assert evidence[0]["target_repo"] == "api"


def test_workspace_route_callers_preserve_duplicate_routes_across_repos(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    api_a = _indexed_repo_with_source(
        tmp_path / "api_a",
        "api.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/api/users')\n"
        "def list_users_a():\n"
        "    return []\n",
    )
    api_b = _indexed_repo_with_source(
        tmp_path / "api_b",
        "api.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/api/users')\n"
        "def list_users_b():\n"
        "    return []\n",
    )
    web = _indexed_repo_with_source(
        tmp_path / "web",
        "client.ts",
        "function loadUsers() {\n"
        "  return fetch('/api/users')\n"
        "}\n",
    )
    _payload(runner.invoke(app, ["workspace", "init", str(workspace), "--json"]))
    for alias, repo in (("api_a", api_a), ("api_b", api_b), ("web", web)):
        _payload(runner.invoke(app, ["workspace", "add", alias, str(repo), str(workspace), "--json"]))

    result = _payload(
        runner.invoke(
            app,
            [
                "workspace",
                "route-callers",
                str(workspace),
                "--method",
                "GET",
                "--path",
                "/api/users",
                "--json",
            ],
        )
    )

    assert len(result["routes"]) == 2
    assert len(result["links"]) == 2
    assert {link["route"]["repo"]["alias"] for link in result["links"]} == {"api_a", "api_b"}


def test_workspace_matches_and_impact_keep_config_values_out_of_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    api = _indexed_repo_with_source(
        tmp_path / "api",
        "service.py",
        "def public_symbol():\n"
        "    return 1\n",
    )
    worker = _indexed_repo(tmp_path / "worker", "worker.py")
    for repo in (api, worker):
        env = repo / ".env.example"
        env.write_text("DATABASE_URL=postgres://secret.invalid/app\n", encoding="utf-8")
        conn = init_db(repo / ".seam" / "seam.db")
        try:
            index_one_file(conn, env)
        finally:
            conn.close()

    _payload(runner.invoke(app, ["workspace", "init", str(workspace), "--json"]))
    _payload(runner.invoke(app, ["workspace", "add", "api", str(api), str(workspace), "--json"]))
    _payload(runner.invoke(app, ["workspace", "add", "worker", str(worker), str(workspace), "--json"]))

    matches = _payload(
        runner.invoke(
            app,
            ["workspace", "matches", str(workspace), "--config-key", "DATABASE_URL", "--json"],
        )
    )
    encoded = json.dumps(matches)
    assert {item["repo"]["alias"] for item in matches["configs"]} == {"api", "worker"}
    assert "DATABASE_URL" in encoded
    assert "postgres://secret.invalid/app" not in encoded

    impact = _payload(
        runner.invoke(
            app,
            ["workspace", "impact", str(workspace), "DATABASE_URL", "--json"],
        )
    )
    assert {repo["alias"] for repo in impact["repos"]} == {"api", "worker"}
    assert all("cross_repo_evidence" in repo for repo in impact["repos"])
    assert any(
        evidence["kind"] == "shared_config_key"
        for repo in impact["repos"]
        for evidence in repo["cross_repo_evidence"]
    )
    assert "postgres://secret.invalid/app" not in json.dumps(impact)
