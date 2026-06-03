"""Integration tests for the FastAPI web app (seam/server/web.py).

Tests use FastAPI's TestClient against a tiny tmp repo indexed by 'seam init'.
Follows the fixture pattern from tests/integration/test_cli_read.py.

Coverage:
  - create_web_app works without a DB present (OpenAPI schema dump)
  - GET /api/status — happy path + NO_INDEX
  - GET /api/search — happy path + empty q + NO_INDEX
  - GET /api/graph/neighborhood — happy path + unknown symbol
  - GET /api/symbol/{name} — happy path + unknown symbol 404
  - GET /api/clusters — happy path + NO_INDEX
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seam.server.web import create_web_app

# ── Fixture: tiny indexed repo ────────────────────────────────────────────────


def _make_indexed_repo(tmp_path: Path) -> Path:
    """Create a tiny Python repo and index it with seam init.

    Returns the project root (which has .seam/seam.db after indexing).
    Mirror of _make_repo() in test_cli_read.py.
    """
    (tmp_path / "auth.py").write_text(
        "def authenticate_user(name, pw):\n"
        '    """Verify credentials."""\n'
        "    return check(pw)\n"
        "\n"
        "def check(pw):\n"
        "    return True\n"
    )
    # Use the Typer CLI runner (same approach as test_cli_read.py) to index the repo.
    from typer.testing import CliRunner

    from seam.cli.main import app as cli_app

    runner = CliRunner()
    res = runner.invoke(cli_app, ["init", str(tmp_path)])
    assert res.exit_code == 0, f"seam init failed: {res.output}"
    return tmp_path


@pytest.fixture()
def indexed_repo(tmp_path: Path) -> Path:
    """Indexed tiny repo fixture."""
    return _make_indexed_repo(tmp_path)


@pytest.fixture()
def client(indexed_repo: Path) -> TestClient:
    """TestClient bound to the indexed repo."""
    from seam import config

    db_path = config.get_db_path(indexed_repo)
    app = create_web_app(db_path=db_path, root=indexed_repo)
    return TestClient(app)


@pytest.fixture()
def no_index_client(tmp_path: Path) -> TestClient:
    """TestClient bound to a directory with NO index (no seam.db)."""
    from seam import config

    db_path = config.get_db_path(tmp_path)
    app = create_web_app(db_path=db_path, root=tmp_path)
    return TestClient(app)


# ── T1: OpenAPI schema works without a DB ─────────────────────────────────────


def test_openapi_schema_works_without_db(tmp_path: Path) -> None:
    """create_web_app() must not open the DB at construction time.

    Critical requirement from B2 spec: the OpenAPI schema must be dumpable
    without a DB file present. This is how FastAPI auto-docs work at startup.
    """
    from seam import config

    # No seam init — db_path does not exist
    db_path = config.get_db_path(tmp_path)
    assert not db_path.exists()

    app = create_web_app(db_path=db_path, root=tmp_path)
    # If app construction opened the DB, it would fail here (file missing).
    # The real test is that app.openapi() can be called without raising.
    schema = app.openapi()
    assert "paths" in schema
    assert "/api/status" in schema["paths"]
    assert "/api/search" in schema["paths"]
    assert "/api/symbol/{name}" in schema["paths"]
    assert "/api/clusters" in schema["paths"]


# ── T2: GET /api/status — happy path ─────────────────────────────────────────


def test_status_happy_path(client: TestClient) -> None:
    """Status endpoint returns expected fields with correct types."""
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["symbol_count"], int)
    assert isinstance(data["edge_count"], int)
    assert isinstance(data["cluster_count"], int)
    assert isinstance(data["languages"], list)
    assert data["symbol_count"] > 0  # we indexed authenticate_user and check


def test_status_no_index(no_index_client: TestClient) -> None:
    """Status returns 503 with JSON error when no index exists.

    FastAPI wraps HTTPException detail in {"detail": {...}}.
    """
    resp = no_index_client.get("/api/status")
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["code"] == "NO_INDEX"


# ── T3: GET /api/search ────────────────────────────────────────────────────────


def test_search_happy_path(client: TestClient) -> None:
    """Search returns matching symbols."""
    resp = client.get("/api/search", params={"q": "authenticate"})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    names = [r["name"] for r in data["results"]]
    assert "authenticate_user" in names


def test_search_empty_q_returns_400(client: TestClient) -> None:
    """Empty q parameter returns 400 INVALID_INPUT.

    FastAPI wraps HTTPException detail in {"detail": {...}}.
    """
    resp = client.get("/api/search", params={"q": "   "})
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "INVALID_INPUT"


def test_search_missing_q_returns_422(client: TestClient) -> None:
    """Missing q parameter returns 422 (FastAPI validation)."""
    resp = client.get("/api/search")
    assert resp.status_code == 422


def test_search_no_index(no_index_client: TestClient) -> None:
    """Search returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/search", params={"q": "test"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T4: GET /api/graph/neighborhood ───────────────────────────────────────────


def test_neighborhood_happy_path(client: TestClient) -> None:
    """Neighborhood returns center, nodes, and edges for a known symbol."""
    resp = client.get("/api/graph/neighborhood", params={"symbol": "authenticate_user"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["center"] == "authenticate_user"
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    # center node must be present
    node_ids = [n["id"] for n in data["nodes"]]
    assert "authenticate_user" in node_ids


def test_neighborhood_unknown_symbol_returns_empty(client: TestClient) -> None:
    """Unknown symbol returns empty nodes/edges (NOT a 404)."""
    resp = client.get("/api/graph/neighborhood", params={"symbol": "xyz_nonexistent_abc"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["center"] == "xyz_nonexistent_abc"
    assert data["nodes"] == []
    assert data["edges"] == []


def test_neighborhood_no_index(no_index_client: TestClient) -> None:
    """Neighborhood returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/graph/neighborhood", params={"symbol": "foo"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


def test_neighborhood_direction_filter(client: TestClient) -> None:
    """direction parameter is accepted; both/callers/callees all return 200."""
    for direction in ("both", "callers", "callees"):
        resp = client.get(
            "/api/graph/neighborhood",
            params={"symbol": "authenticate_user", "direction": direction},
        )
        assert resp.status_code == 200, f"direction={direction} failed"


def test_neighborhood_invalid_direction_returns_422(client: TestClient) -> None:
    """An out-of-set direction is rejected at the boundary (Literal → 422), not
    silently treated as 'both'."""
    resp = client.get(
        "/api/graph/neighborhood",
        params={"symbol": "authenticate_user", "direction": "garbage"},
    )
    assert resp.status_code == 422


# ── T5: GET /api/symbol/{name} ────────────────────────────────────────────────


def test_symbol_happy_path(client: TestClient) -> None:
    """Symbol detail endpoint returns expected structure."""
    resp = client.get("/api/symbol/authenticate_user")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "authenticate_user"
    assert isinstance(data["definitions"], list)
    assert len(data["definitions"]) > 0
    assert isinstance(data["callers"], list)
    assert isinstance(data["callees"], list)
    assert isinstance(data["peers"], list)
    assert isinstance(data["why"], list)
    # check has no callees in our fixture (it's the leaf) but authenticate_user calls check
    assert "check" in data["callees"]


def test_symbol_unknown_returns_404(client: TestClient) -> None:
    """Unknown symbol returns 404 with {"found": false} in the detail field."""
    resp = client.get("/api/symbol/xyz_nonexistent_abc")
    assert resp.status_code == 404
    body = resp.json()
    # FastAPI wraps HTTPException detail in {"detail": ...}
    assert body["detail"] == {"found": False}


def test_symbol_no_index(no_index_client: TestClient) -> None:
    """Symbol endpoint returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/symbol/foo")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T6: GET /api/clusters ─────────────────────────────────────────────────────


def test_clusters_happy_path(client: TestClient) -> None:
    """Clusters returns a list (may be empty for tiny repo)."""
    resp = client.get("/api/clusters")
    assert resp.status_code == 200
    data = resp.json()
    assert "clusters" in data
    assert isinstance(data["clusters"], list)


def test_clusters_no_index(no_index_client: TestClient) -> None:
    """Clusters returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/clusters")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"
