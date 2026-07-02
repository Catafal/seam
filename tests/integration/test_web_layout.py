"""Integration tests for GET /api/graph/layout (seam/server/web_layout.py).

TDD: red → green → refactor per slice S1 (issue #169).
Coverage:
  - endpoint returns expected shape with populated index
  - total_nodes is honest (above cap when cap is applied)
  - max_nodes query parameter is accepted and applied
  - no-index directory → 503 NO_INDEX
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _make_client(tmp_path: Path) -> TestClient:
    """Build a tiny real index and return a TestClient for the web app."""
    from tests.unit.test_layout import _make_index

    conn = _make_index(tmp_path)
    conn.close()
    from seam.server.web import create_web_app

    app = create_web_app(db_path=tmp_path / "seam.db", root=tmp_path)
    return TestClient(app)


def test_layout_endpoint_returns_shape(tmp_path: Path) -> None:
    c = _make_client(tmp_path)
    r = c.get("/api/graph/layout?max_nodes=100")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"nodes", "edges", "clusters", "total_nodes"}
    assert body["total_nodes"] >= 4
    if body["nodes"]:
        n = body["nodes"][0]
        assert {"id", "x", "y", "z", "label", "name", "file_path", "size", "color"} <= set(n.keys())


def test_layout_endpoint_clamps_max_nodes(tmp_path: Path) -> None:
    c = _make_client(tmp_path)
    r = c.get("/api/graph/layout?max_nodes=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["nodes"]) <= 1
    # total_nodes is honest — above the cap
    assert body["total_nodes"] >= 4


def test_layout_endpoint_default_max_nodes(tmp_path: Path) -> None:
    c = _make_client(tmp_path)
    r = c.get("/api/graph/layout")
    assert r.status_code == 200
    body = r.json()
    assert body["total_nodes"] >= 4


def test_layout_endpoint_no_index_returns_503(tmp_path: Path) -> None:
    """CR2: missing DB → 503 NO_INDEX (not 404 or 500)."""
    from seam.server.web import create_web_app

    # No seam.db created — directory exists but DB file does not
    app = create_web_app(db_path=tmp_path / "seam.db", root=tmp_path)
    c = TestClient(app)
    r = c.get("/api/graph/layout")
    assert r.status_code == 503
    detail = r.json().get("detail", {})
    # detail may be a dict or a string depending on FastAPI version
    if isinstance(detail, dict):
        assert detail.get("code") == "NO_INDEX"
    else:
        assert "NO_INDEX" in str(detail)
