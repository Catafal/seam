"""Unit tests for seam/analysis/processes.py — execution flows.

Covers:
  entry-point detection (roots ranked by downstream reach; tests excluded),
  flow tree construction, depth + breadth caps (truncated), cycle safety,
  unknown-entry -> None, empty index -> [], never-raises, path relativization.
"""

import tempfile
from pathlib import Path

import pytest

from seam.analysis.processes import build_flow, list_entry_points
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


def _sym(name: str, file: str, kind: str = "function") -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=1,
        end_line=2,
        docstring=None,
        signature=None,
        is_exported=None,  # type: ignore[arg-type]
        visibility=None,
    )


def _edge(source: str, target: str, file: str, kind: str = "call") -> Edge:
    return Edge(source=source, target=target, kind=kind, file=file, line=1, confidence="EXTRACTED")


@pytest.fixture()
def tmp_db():
    """Yield (conn, tmp_path) with an initialized DB + stub source files."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)
        (tmp_path / "app.py").write_text("# stub\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_app.py").write_text("# stub\n")
        yield conn, tmp_path
        conn.close()


# ── list_entry_points ──────────────────────────────────────────────────────────


def test_entry_points_ranked_by_reach(tmp_db) -> None:
    """A root that orchestrates a chain ranks above a root that calls one leaf."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    # main -> step1 -> step2 -> step3 (reach 3); small -> leaf (reach 1).
    syms = [_sym(n, a) for n in ("main", "step1", "step2", "step3", "small", "leaf")]
    edges = [
        _edge("main", "step1", a),
        _edge("step1", "step2", a),
        _edge("step2", "step3", a),
        _edge("small", "leaf", a),
    ]
    upsert_file(conn, Path(a), "python", "h1", syms, edges)

    points = list_entry_points(conn, repo_root=tmp)
    names = [p["name"] for p in points]
    # main and small are roots (never called); step*/leaf are not roots.
    assert names[0] == "main"  # highest reach
    assert "small" in names
    assert "step1" not in names and "leaf" not in names
    main = next(p for p in points if p["name"] == "main")
    assert main["reach"] == 3
    assert main["file"] == "app.py"  # relativized to repo_root


def test_entry_points_exclude_tests(tmp_db) -> None:
    """A root defined in a test file is not a program entry point."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    t = str(tmp / "tests" / "test_app.py")
    # App entry point `run` is a root (nothing calls it in code).
    upsert_file(conn, Path(a), "python", "h1", [_sym("run", a), _sym("helper", a)], [
        _edge("run", "helper", a),
    ])
    # A test-file root (nothing calls it) that exercises a helper.
    upsert_file(conn, Path(t), "python", "h2", [_sym("test_run", t)], [
        _edge("test_run", "assert_thing", t),
    ])

    names = [p["name"] for p in list_entry_points(conn, repo_root=tmp)]
    assert "run" in names
    assert "test_run" not in names  # excluded — defined in a test file


def test_entry_points_empty_index_safe(tmp_db) -> None:
    """No edges -> no entry points, never raises."""
    conn, _ = tmp_db
    assert list_entry_points(conn) == []


# ── build_flow ───────────────────────────────────────────────────────────────


def test_build_flow_tree(tmp_db) -> None:
    """Flow expands forward: entry -> direct callees -> their callees."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    syms = [_sym(n, a) for n in ("main", "a1", "a2", "b1")]
    edges = [
        _edge("main", "a1", a),
        _edge("main", "a2", a),
        _edge("a1", "b1", a),
    ]
    upsert_file(conn, Path(a), "python", "h1", syms, edges)

    flow = build_flow(conn, "main", repo_root=tmp)
    assert flow is not None
    assert flow["entry"] == "main"
    assert flow["file"] == "app.py"
    top = {s["name"] for s in flow["steps"]}
    assert top == {"a1", "a2"}
    a1 = next(s for s in flow["steps"] if s["name"] == "a1")
    assert [c["name"] for c in a1["children"]] == ["b1"]
    assert flow["total_steps"] == 3  # a1, a2, b1
    assert flow["truncated"] is False


def test_build_flow_depth_cap(tmp_db) -> None:
    """max_depth cuts the tree and marks truncated."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    syms = [_sym(n, a) for n in ("e", "d1", "d2", "d3")]
    edges = [_edge("e", "d1", a), _edge("d1", "d2", a), _edge("d2", "d3", a)]
    upsert_file(conn, Path(a), "python", "h1", syms, edges)

    flow = build_flow(conn, "e", max_depth=1, repo_root=tmp)
    assert flow is not None
    assert [s["name"] for s in flow["steps"]] == ["d1"]  # only depth-1
    assert flow["steps"][0]["children"] == []
    assert flow["steps"][0]["truncated"] is True  # d1's children were cut
    assert flow["truncated"] is True


def test_build_flow_breadth_cap(tmp_db) -> None:
    """max_breadth caps fan-out and marks truncated."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    syms = [_sym(n, a) for n in ("hub", "c1", "c2", "c3", "c4")]
    edges = [_edge("hub", f"c{i}", a) for i in range(1, 5)]
    upsert_file(conn, Path(a), "python", "h1", syms, edges)

    flow = build_flow(conn, "hub", max_breadth=2, repo_root=tmp)
    assert flow is not None
    assert len(flow["steps"]) == 2  # capped from 4
    assert flow["truncated"] is True


def test_build_flow_cycle_safe(tmp_db) -> None:
    """A cycle a->b->a terminates; each symbol appears once."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    syms = [_sym(n, a) for n in ("a", "b")]
    edges = [_edge("a", "b", a), _edge("b", "a", a)]
    upsert_file(conn, Path(a), "python", "h1", syms, edges)

    flow = build_flow(conn, "a", repo_root=tmp)
    assert flow is not None
    assert [s["name"] for s in flow["steps"]] == ["b"]
    assert flow["steps"][0]["children"] == []  # b->a pruned (a already visited)


def test_build_flow_unknown_entry_returns_none(tmp_db) -> None:
    """An entry name absent from symbols and the graph -> None."""
    conn, tmp = tmp_db
    a = str(tmp / "app.py")
    upsert_file(conn, Path(a), "python", "h1", [_sym("x", a)], [_edge("x", "y", a)])
    assert build_flow(conn, "nope", repo_root=tmp) is None
