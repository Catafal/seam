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

import subprocess
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
        "    raise ValueError('bad pw')\n"
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


def _git(args: list[str], cwd: Path) -> None:
    """Run a git command in cwd, raising on failure (test helper)."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        # Deterministic identity so commit works in CI without global git config.
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": str(cwd),
        },
    )


@pytest.fixture()
def git_repo_client(tmp_path: Path) -> TestClient:
    """Indexed repo that IS a git repo with one unstaged edit inside `check`.

    Sequence: write → git init/commit → seam init (indexes the committed code) →
    make a same-line-count working-tree edit so scope='working' diff maps to `check`.
    """
    auth = tmp_path / "auth.py"
    auth.write_text(
        "def authenticate_user(name, pw):\n"
        '    """Verify credentials."""\n'
        "    return check(pw)\n"
        "\n"
        "def check(pw):\n"
        "    return True\n"
    )
    _git(["init"], tmp_path)
    _git(["add", "auth.py"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)

    from typer.testing import CliRunner

    from seam.cli.main import app as cli_app

    res = CliRunner().invoke(cli_app, ["init", str(tmp_path)])
    assert res.exit_code == 0, f"seam init failed: {res.output}"

    # Unstaged edit inside check's body (line count preserved → no line drift).
    auth.write_text(
        "def authenticate_user(name, pw):\n"
        '    """Verify credentials."""\n'
        "    return check(pw)\n"
        "\n"
        "def check(pw):\n"
        "    return False\n"
    )

    from seam import config

    db_path = config.get_db_path(tmp_path)
    return TestClient(create_web_app(db_path=db_path, root=tmp_path))


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
    assert "/api/schema" in schema["paths"]
    assert "/api/architecture" in schema["paths"]
    assert "/api/snippet" in schema["paths"]
    assert "/api/graph/search" in schema["paths"]
    assert "/api/search" in schema["paths"]
    assert "/api/symbol/{name}" in schema["paths"]
    assert "/api/clusters" in schema["paths"]


# ── T1b: GET /api/schema — diagnostics ──────────────────────────────────────


def test_schema_happy_path(client: TestClient) -> None:
    """Schema endpoint returns bounded diagnostics without verbose table metadata by default."""
    resp = client.get("/api/schema")
    assert resp.status_code == 200
    data = resp.json()
    assert "freshness" in data
    assert "counts" in data
    assert "breakdowns" in data
    assert "capabilities" in data
    assert "tools" in data
    assert "recommended_next_calls" in data
    assert "warnings" in data
    assert data["counts"]["symbols"] > 0
    assert data["tables"] is None


def test_schema_verbose_includes_table_metadata(client: TestClient) -> None:
    """Schema endpoint includes DB table/column metadata only when verbose=true."""
    resp = client.get("/api/schema?verbose=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tables"]["symbols"]["exists"] is True
    assert data["tables"]["symbols"]["columns"]["name"]["exists"] is True


def test_schema_no_index(no_index_client: TestClient) -> None:
    """Schema returns 503 NO_INDEX when no index exists."""
    resp = no_index_client.get("/api/schema")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T1c: GET /api/snippet — exact source ────────────────────────────────────


def test_snippet_happy_path(client: TestClient) -> None:
    """Snippet endpoint returns bounded exact source for a unique symbol."""
    resp = client.get("/api/snippet", params={"symbol": "check"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["symbol"] == "check"
    assert data["file"] == "auth.py"
    assert "def check(pw):" in data["source"]
    assert data["warnings"] == []


def test_snippet_invalid_selector_returns_400(client: TestClient) -> None:
    """Invalid selector combinations map to the existing handler error style."""
    resp = client.get("/api/snippet", params={"uid": "deadbeef:1", "symbol": "check"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_INPUT"


def test_snippet_no_index(no_index_client: TestClient) -> None:
    """Snippet returns 503 NO_INDEX when no index exists."""
    resp = no_index_client.get("/api/snippet", params={"symbol": "check"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T1d: GET /api/graph/search — structural graph search ────────────────────


def test_graph_search_happy_path(client: TestClient) -> None:
    """Graph search returns compact structural results with root-relative paths."""
    resp = client.get(
        "/api/graph/search",
        params={"kind": "function", "name_pattern": "check", "include_preview": "true"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["symbol"] == "check"
    assert data["items"][0]["file"] == "auth.py"
    assert data["items"][0]["uid"]
    assert data["items"][0]["degrees"]["incoming"] == 1
    assert "source" not in data["items"][0]
    assert data["query"]["name_pattern"] == "check"


def test_graph_search_invalid_filter_returns_400(client: TestClient) -> None:
    """Invalid typed filters map to handler-style errors."""
    resp = client.get("/api/graph/search", params={"edge_kind": "HTTP_CALLS"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_INPUT"


def test_graph_search_accepts_exception_edges(client: TestClient) -> None:
    """Exception edge kinds flow through the web graph-search endpoint."""
    resp = client.get(
        "/api/graph/search",
        params={"edge_kind": "raises", "direction": "outgoing"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["symbol"] == "check"
    assert data["items"][0]["degrees"]["outgoing"] == 1


def test_graph_search_no_index(no_index_client: TestClient) -> None:
    """Graph search returns 503 NO_INDEX when no index exists."""
    resp = no_index_client.get("/api/graph/search", params={"kind": "function"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


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
    """Symbol detail endpoint returns expected structure.

    S2: callers/callees are now enriched {name, kind, confidence} objects, not bare strings.
    """
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
    # S2: callees are now objects; check by extracting names
    callee_names = [c["name"] for c in data["callees"]]
    assert "check" in callee_names


def test_symbol_callers_enriched_with_kind_confidence(client: TestClient) -> None:
    """Callers and callees carry {name, kind, confidence} objects (S2 enrichment).

    authenticate_user calls check, so check.callers should contain authenticate_user
    with kind and confidence populated from the edges table.
    """
    resp = client.get("/api/symbol/check")
    assert resp.status_code == 200
    data = resp.json()
    callers = data["callers"]
    assert isinstance(callers, list)
    assert len(callers) > 0, "check must have at least one caller (authenticate_user)"
    # Each entry must be an object with name, kind, confidence — not a bare string.
    caller = callers[0]
    assert isinstance(caller, dict), "caller must be an object, not a bare string"
    assert "name" in caller
    assert "kind" in caller
    assert "confidence" in caller
    caller_names = [c["name"] for c in callers]
    assert "authenticate_user" in caller_names
    # kind must be a non-empty string (e.g. 'call')
    assert caller["kind"] != ""


def test_symbol_callees_enriched_with_kind_confidence(client: TestClient) -> None:
    """Callees carry {name, kind, confidence} objects (S2 enrichment).

    authenticate_user calls check, so authenticate_user.callees contains check.
    """
    resp = client.get("/api/symbol/authenticate_user")
    assert resp.status_code == 200
    data = resp.json()
    callees = data["callees"]
    assert isinstance(callees, list)
    callee_names = [c["name"] for c in callees]
    assert "check" in callee_names
    # Each callee must carry kind + confidence
    for callee in callees:
        assert "kind" in callee
        assert "confidence" in callee


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


# ── T7: GET /api/impact ───────────────────────────────────────────────────────


def test_impact_happy_path(client: TestClient) -> None:
    """Impact (upstream) on `check` surfaces `authenticate_user` as WILL_BREAK (d=1).

    `authenticate_user` calls `check`, so a change to `check` will break its caller.
    """
    resp = client.get("/api/impact", params={"symbol": "check", "direction": "upstream"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["target"] == "check"
    assert "risk_summary" in data
    assert data["upstream"] is not None
    will_break = [e["name"] for e in data["upstream"]["WILL_BREAK"]]
    assert "authenticate_user" in will_break
    # Each entry carries the lean field set (no resolved_by/best_candidate) plus optional kind.
    entry = data["upstream"]["WILL_BREAK"][0]
    required_keys = {"name", "distance", "confidence", "tier", "file", "is_test"}
    assert required_keys.issubset(set(entry.keys()))


def test_impact_entry_includes_kind(client: TestClient) -> None:
    """Impact entries include an optional 'kind' field (edge kind of the final hop).

    When SEAM_EDGE_PROVENANCE=on (default), the handler emits 'kind' on every entry.
    The web layer must surface it rather than silently dropping it.
    """
    resp = client.get("/api/impact", params={"symbol": "check", "direction": "upstream"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["upstream"] is not None
    entry = data["upstream"]["WILL_BREAK"][0]
    # 'kind' must be present and non-null (call edge from authenticate_user → check)
    assert "kind" in entry
    assert entry["kind"] is not None
    assert isinstance(entry["kind"], str)


def test_impact_both_directions(client: TestClient) -> None:
    """direction=both returns both upstream and downstream keys."""
    resp = client.get("/api/impact", params={"symbol": "check", "direction": "both"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["upstream"] is not None
    assert data["downstream"] is not None
    assert "upstream" in data["risk_summary"]
    assert "downstream" in data["risk_summary"]


def test_impact_unknown_symbol(client: TestClient) -> None:
    """Unknown target returns found:false with empty tiers (not a 404)."""
    resp = client.get("/api/impact", params={"symbol": "xyz_nonexistent_abc"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False


def test_impact_invalid_direction_returns_422(client: TestClient) -> None:
    """Out-of-set direction is rejected at the boundary (Literal → 422)."""
    resp = client.get("/api/impact", params={"symbol": "check", "direction": "sideways"})
    assert resp.status_code == 422


def test_impact_no_index(no_index_client: TestClient) -> None:
    """Impact returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/impact", params={"symbol": "check"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T8: GET /api/trace ────────────────────────────────────────────────────────


def test_trace_happy_path(client: TestClient) -> None:
    """trace(authenticate_user → check) finds a path (the direct call edge)."""
    resp = client.get(
        "/api/trace", params={"source": "authenticate_user", "target": "check"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["source"] == "authenticate_user"
    assert data["target"] == "check"
    assert len(data["paths"]) >= 1
    hop = data["paths"][0][0]
    assert set(hop.keys()) == {"from_name", "to_name", "kind", "confidence"}
    assert hop["from_name"] == "authenticate_user"


def test_trace_unconnected_returns_empty(client: TestClient) -> None:
    """No reverse path: check does not call authenticate_user."""
    resp = client.get(
        "/api/trace", params={"source": "check", "target": "authenticate_user"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["paths"] == []


def test_trace_blank_source_returns_400(client: TestClient) -> None:
    """Blank source is rejected by the handler as INVALID_INPUT (400)."""
    resp = client.get("/api/trace", params={"source": "   ", "target": "check"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_INPUT"


def test_trace_no_index(no_index_client: TestClient) -> None:
    """Trace returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/trace", params={"source": "a", "target": "b"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T9: GET /api/changes ──────────────────────────────────────────────────────


def test_changes_happy_path(git_repo_client: TestClient) -> None:
    """Working-tree edit inside `check` surfaces it in changed_symbols + a risk level."""
    resp = git_repo_client.get("/api/changes", params={"scope": "working"})
    assert resp.status_code == 200
    data = resp.json()
    changed = [s["name"] for s in data["changed_symbols"]]
    assert "check" in changed
    assert data["risk_level"] in {"low", "medium", "high", "none", "critical"}
    assert data["scope"] == "working"


def test_changes_not_a_git_repo(client: TestClient) -> None:
    """Indexed but non-git dir returns 400 NOT_A_GIT_REPO."""
    resp = client.get("/api/changes", params={"scope": "working"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "NOT_A_GIT_REPO"


def test_changes_invalid_scope_returns_422(client: TestClient) -> None:
    """Out-of-set scope is rejected at the boundary (Literal → 422)."""
    resp = client.get("/api/changes", params={"scope": "bogus"})
    assert resp.status_code == 422


def test_changes_no_index(no_index_client: TestClient) -> None:
    """Changes returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/changes")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T10: GET /api/constellation ───────────────────────────────────────────────


def test_constellation_happy_path(client: TestClient) -> None:
    """Constellation returns clusters + links lists (may be empty for a tiny repo)."""
    resp = client.get("/api/constellation")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["clusters"], list)
    assert isinstance(data["links"], list)


def test_constellation_no_index(no_index_client: TestClient) -> None:
    """Constellation returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/constellation")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T11: GET /api/hubs ────────────────────────────────────────────────────────


def test_hubs_happy_path(client: TestClient) -> None:
    """Hubs returns degree-ranked symbols; authenticate_user (calls check) is present."""
    resp = client.get("/api/hubs", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["symbols"], list)
    names = [s["name"] for s in data["symbols"]]
    # both authenticate_user and check have an edge between them → both are hubs
    assert "authenticate_user" in names or "check" in names
    for s in data["symbols"]:
        assert set(s.keys()) == {"name", "kind", "degree", "path"}
        # path is relativized to the project root — never absolute.
        assert s["path"] is None or not s["path"].startswith("/")


def test_hubs_no_index(no_index_client: TestClient) -> None:
    """Hubs returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/hubs")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "NO_INDEX"


# ── T12: GET /api/structure ───────────────────────────────────────────────────


def test_structure_happy_path(client: TestClient) -> None:
    """Structure returns symbols with relativized paths."""
    resp = client.get("/api/structure")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["symbols"], list)
    names = [s["name"] for s in data["symbols"]]
    assert "authenticate_user" in names
    for s in data["symbols"]:
        # B2 (#232) added an additive fan-in `degree` field to each structure row.
        assert set(s.keys()) == {"path", "name", "kind", "line", "qualified_name", "degree"}
        assert not s["path"].startswith("/")  # relativized


def test_structure_no_index(no_index_client: TestClient) -> None:
    """Structure returns 503 NO_INDEX when no index present."""
    resp = no_index_client.get("/api/structure")
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
