"""Behavior tests for Phase 11 P1.4 — seam_architecture / `seam architecture`."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import seam.config as config
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sym(
    name: str,
    file: Path,
    *,
    kind: str = "function",
    start: int = 1,
    end: int = 2,
    signature: str | None = None,
    qualified_name: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=str(file),
        start_line=start,
        end_line=end,
        docstring=None,
        signature=signature or f"def {name}()",
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=qualified_name or name,
    )


def _edge(
    source: str,
    target: str,
    file: Path,
    *,
    kind: str = "call",
    line: int = 1,
    confidence: str = "EXTRACTED",
    synthesized_by: str | None = None,
) -> Edge:
    edge = Edge(
        source=source,
        target=target,
        kind=kind,
        file=str(file),
        line=line,
        confidence=confidence,  # type: ignore[typeddict-item]
    )
    if synthesized_by is not None:
        edge["synthesized_by"] = synthesized_by
    return edge


def _make_architecture_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    root = tmp_path.resolve()
    src = root / "src"
    tests = root / "tests"
    src.mkdir()
    tests.mkdir()
    api = src / "api.py"
    service = src / "service.py"
    test_api = tests / "test_api.py"
    api_src = "def entry():\n    orchestrate()\n\ndef route_handler():\n    return entry()\n"
    service_src = "def orchestrate():\n    helper()\n\ndef helper():\n    return True\n"
    test_src = "def test_entry():\n    entry()\n"
    api.write_text(api_src, encoding="utf-8")
    service.write_text(service_src, encoding="utf-8")
    test_api.write_text(test_src, encoding="utf-8")

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(
        conn,
        api,
        "python",
        _hash(api_src),
        [
            _sym("entry", api, start=1, end=2),
            _sym("route_handler", api, start=4, end=5),
        ],
        [
            _edge("entry", "orchestrate", api, line=2),
            _edge("route_handler", "entry", api, line=5),
        ],
    )
    upsert_file(
        conn,
        service,
        "python",
        _hash(service_src),
        [
            _sym("orchestrate", service, start=1, end=2),
            _sym("helper", service, start=4, end=5),
        ],
        [
            _edge("orchestrate", "helper", service, line=2),
            _edge("helper", "helper", service, line=5),
            _edge(
                "orchestrate",
                "helper",
                service,
                line=2,
                confidence="INFERRED",
                synthesized_by="interface-override",
            ),
        ],
    )
    upsert_file(
        conn,
        test_api,
        "python",
        _hash(test_src),
        [_sym("test_entry", test_api, start=1, end=2)],
        [_edge("test_entry", "entry", test_api, line=2)],
    )
    conn.execute(
        "INSERT INTO clusters (label, size, naming_source, cohesion) "
        "VALUES ('runtime', 4, 'deterministic', 0.9)"
    )
    conn.execute(
        "INSERT INTO clusters (label, size, naming_source, cohesion) "
        "VALUES ('tests', 1, 'deterministic', 1.0)"
    )
    runtime_id = conn.execute("SELECT id FROM clusters WHERE label = 'runtime'").fetchone()[0]
    tests_id = conn.execute("SELECT id FROM clusters WHERE label = 'tests'").fetchone()[0]
    conn.execute(
        "UPDATE symbols SET cluster_id = ? WHERE name IN ('entry', 'route_handler', 'orchestrate', 'helper')",
        (runtime_id,),
    )
    conn.execute("UPDATE symbols SET cluster_id = ? WHERE name = 'test_entry'", (tests_id,))
    conn.commit()
    return conn, root


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def test_architecture_summary_reports_counts_edge_mix_and_next_calls(tmp_path: Path) -> None:
    """The tracer payload gives agents a repo briefing before precise follow-up tools."""
    from seam.query.architecture import describe_architecture

    conn, root = _make_architecture_repo(tmp_path)

    result = describe_architecture(conn, root=root)

    assert result["identity"]["schema_version"] >= 12
    assert result["freshness"]["stale"] is False
    assert result["scope"] == {"path": None, "applied": False}
    assert result["counts"]["files"] == 3
    assert result["counts"]["symbols"] == 5
    assert result["counts"]["edges"] == 6
    assert result["counts"]["clusters"] == 2
    assert result["counts"]["test_files"] == 1
    assert result["counts"]["production_files"] == 2
    assert result["sections"]["languages"]["items"] == [
        {"language": "python", "files": 3, "symbols": 5}
    ]
    assert result["sections"]["edge_mix"]["edge_kinds"]["call"] == 6
    assert result["sections"]["edge_mix"]["confidence"]["EXTRACTED"] == 5
    assert result["sections"]["edge_mix"]["confidence"]["INFERRED"] == 1
    assert result["sections"]["edge_mix"]["synthesized"]["interface-override"] == 1
    assert result["sections"]["optional_surfaces"]["routes"]["status"] == "unsupported"
    assert any(call["tool"] == "seam_graph_search" for call in result["next_calls"])
    assert any(w["code"] == "NO_ROUTE_EDGES" for w in result["warnings"])
    assert not _contains_key(result, "source_text")
    assert not _contains_key(result, "source_code")


def test_architecture_reports_physical_areas_and_cluster_representatives(tmp_path: Path) -> None:
    """Physical and cluster sections compare filesystem layout with logical areas."""
    from seam.query.architecture import describe_architecture

    conn, root = _make_architecture_repo(tmp_path)

    result = describe_architecture(conn, root=root)

    physical = result["sections"]["physical"]
    assert physical["top_areas"][0]["path"] == "src"
    assert physical["top_areas"][0]["files"] == 2
    assert physical["top_areas"][0]["symbols"] == 4
    assert physical["top_areas"][0]["edges"] == 5
    assert physical["structure"]["tree"]["name"] == root.name
    assert physical["structure"]["truncated"] == 0

    clusters = result["sections"]["clusters"]["items"]
    assert clusters[0]["label"] == "runtime"
    assert clusters[0]["size"] == 4
    assert clusters[0]["representative"]["symbol"] == "entry"
    assert clusters[0]["representative"]["uid"]
    assert clusters[0]["top_physical_areas"][0]["path"] == "src"


def test_architecture_reports_entry_points_hotspots_and_orchestrators(tmp_path: Path) -> None:
    """Topology sections are ranked separately for roots, fan-in, and fan-out."""
    from seam.query.architecture import describe_architecture

    conn, root = _make_architecture_repo(tmp_path)

    result = describe_architecture(conn, root=root)

    entry_points = result["sections"]["entry_points"]["items"]
    assert entry_points[0]["symbol"] == "route_handler"
    assert entry_points[0]["uid"]
    assert entry_points[0]["file"] == "src/api.py"

    hotspots = result["sections"]["hotspots"]["items"]
    assert hotspots[0]["symbol"] == "entry"
    assert hotspots[0]["degrees"]["incoming"] == 2
    assert hotspots[0]["is_test"] is False

    orchestrators = result["sections"]["orchestrators"]["items"]
    assert orchestrators[0]["symbol"] == "orchestrate"
    assert orchestrators[0]["degrees"]["outgoing"] == 2
    assert orchestrators[0]["edge_kinds"]["call"] == 2

    assert any(
        call["tool"] == "seam_context" and call["params"]["symbol"] == "entry" and call["params"]["uid"]
        for call in result["next_calls"]
    )


def test_architecture_scope_sections_boundaries_and_byte_budget(tmp_path: Path) -> None:
    """Scoping and section selection keep architecture output bounded and honest."""
    from seam.query.architecture import describe_architecture

    conn, root = _make_architecture_repo(tmp_path)

    scoped = describe_architecture(conn, root=root, scope="src", sections=["languages", "hotspots", "boundaries"])

    assert scoped["scope"] == {"path": "src", "applied": True}
    assert scoped["counts"]["files"] == 2
    assert scoped["counts"]["symbols"] == 4
    assert scoped["counts"]["test_files"] == 0
    assert set(scoped["sections"]) == {"languages", "hotspots", "boundaries"}
    assert scoped["sections"]["hotspots"]["items"][0]["symbol"] == "helper"

    limited = describe_architecture(conn, root=root, sections=["hotspots"], limit=1)
    assert len(limited["sections"]["hotspots"]["items"]) == 1
    assert limited["sections"]["hotspots"]["truncated"] > 0

    whole = describe_architecture(conn, root=root)
    boundary = whole["sections"]["boundaries"]["items"][0]
    assert boundary["source_area"] == "tests"
    assert boundary["target_area"] == "src"
    assert boundary["edge_count"] == 1
    assert boundary["representative"]["source"] == "test_entry"
    assert boundary["representative"]["target"] == "entry"

    capped = describe_architecture(conn, root=root, max_bytes=900)
    assert capped["truncation"]["byte_budget"]["limit"] == 900
    assert capped["truncation"]["byte_budget"]["omitted"] > 0
    assert capped["truncation"]["byte_budget"]["unit"] == "compact_json_bytes"
    assert len(json.dumps(capped, separators=(",", ":"), sort_keys=True).encode("utf-8")) <= 900
    assert len(str(capped)) < len(str(whole))


def test_architecture_warns_on_out_of_root_scope(tmp_path: Path) -> None:
    """Out-of-root scopes are rejected without leaking arbitrary filesystem paths."""
    from seam.query.architecture import describe_architecture

    conn, root = _make_architecture_repo(tmp_path)

    result = describe_architecture(conn, root=root, scope="../outside")

    assert result["scope"] == {"path": "../outside", "applied": False}
    assert result["counts"]["files"] == 0
    assert any(w["code"] == "SCOPE_OUTSIDE_ROOT" for w in result["warnings"])


def test_architecture_handler_mcp_cli_and_web_surfaces(tmp_path: Path) -> None:
    """All transports expose the same architecture overview without widening core logic."""
    from seam.cli.main import app
    from seam.server.mcp import create_server
    from seam.server.tools import handle_seam_architecture
    from seam.server.web import create_web_app

    conn, root = _make_architecture_repo(tmp_path)

    handled = handle_seam_architecture(conn, root, sections=["languages"])
    assert set(handled["sections"]) == {"languages"}

    server = create_server(conn, root)
    tool_names = list(server._tool_manager._tools.keys())
    assert "seam_architecture" in tool_names
    assert len(tool_names) == 16
    conn.close()

    runner = CliRunner()
    json_result = runner.invoke(
        app,
        ["architecture", str(root), "--section", "languages", "--json"],
    )
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert set(payload["data"]["sections"]) == {"languages"}

    quiet_result = runner.invoke(app, ["architecture", str(root), "--quiet"])
    assert quiet_result.exit_code == 0, quiet_result.output
    assert "files=3" in quiet_result.output
    assert "symbols=5" in quiet_result.output

    client = TestClient(create_web_app(db_path=config.get_db_path(root), root=root))
    response = client.get("/api/architecture", params=[("section", "languages")])
    assert response.status_code == 200
    assert set(response.json()["sections"]) == {"languages"}
