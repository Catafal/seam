"""Unit tests for seam/server/graph_api.py — neighborhood graph builder (Task B1).

TDD: Tests written BEFORE implementation. They import build_neighborhood and
assert on the exact API contract from .claude/tasks/seam-explorer-frontend.md.

Coverage:
  T1  homonym_collapse: two symbols with the same name -> one node, definition_count=2
  T2  depth_1_boundary: only direct neighbors returned, not transitive hops
  T3  direction_callees: direction="callees" -> only edges where center is source
  T4  direction_callers: direction="callers" -> only edges where center is target
  T5  direction_both: direction="both" -> union of callers and callees
  T6  confidence_passthrough: EXTRACTED / AMBIGUOUS / INFERRED preserved on edges
  T7  unknown_symbol_safe_return: unknown name -> center present (if exists) else empty nodes
  T8  truly_unknown_symbol: symbol not in DB at all -> empty nodes, empty edges
  T9  node_shape: nodes carry all required fields (id, name, kind, signature,
                  visibility, is_exported, cluster_id, cluster_label, definition_count)
  T10 edge_shape: edges carry id, source, target, kind, confidence
  T11 edge_kind: "call" and "import" edges both preserved
  T12 no_raise_on_empty_db: empty DB returns safe dict (never raises)
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.graph_api import build_constellation, build_neighborhood


def _seed_cluster(conn: sqlite3.Connection, label: str, members: list[str]) -> int:
    """Insert a cluster row and assign `members` (by name) to it. Returns cluster id."""
    cur = conn.execute(
        "INSERT INTO clusters (label, size, naming_source) VALUES (?, ?, 'deterministic')",
        (label, len(members)),
    )
    cid = cur.lastrowid
    for name in members:
        conn.execute("UPDATE symbols SET cluster_id = ? WHERE name = ?", (cid, name))
    conn.commit()
    return int(cid)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(
    name: str,
    file: str,
    kind: str = "function",
    signature: str | None = None,
    visibility: str | None = None,
    is_exported: int | None = None,
) -> Symbol:
    """Build a minimal Symbol for seeding tests. is_exported is 0/1/None (SQLite int)."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=1,
        end_line=2,
        docstring=None,
        signature=signature,
        is_exported=is_exported,  # type: ignore[arg-type]
        visibility=visibility,
    )


def _edge(
    source: str,
    target: str,
    file: str,
    kind: str = "call",
    confidence: str = "EXTRACTED",
) -> Edge:
    """Build a minimal Edge for seeding tests."""
    return Edge(source=source, target=target, kind=kind, file=file, line=1, confidence=confidence)


@pytest.fixture()
def tmp_db() -> tuple[sqlite3.Connection, Path]:
    """Yield (conn, tmp_dir_path) with an initialized in-memory-backed DB."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # Create two stub source files so upsert_file can stat them.
        (tmp_path / "a.py").write_text("# stub\n")
        (tmp_path / "b.py").write_text("# stub\n")

        yield conn, tmp_path
        conn.close()


# ── T1: homonym collapse ──────────────────────────────────────────────────────


def test_homonym_collapse(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Two symbols with the same name share one graph node; definition_count=2."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    b = str(tmp / "b.py")

    # Two files both define "helper"; "center" calls "helper" from file a.
    upsert_file(conn, Path(a), "python", "h1", [_sym("center", a), _sym("helper", a)], [
        _edge("center", "helper", a),
    ])
    upsert_file(conn, Path(b), "python", "h2", [_sym("helper", b)], [])

    result = build_neighborhood(conn, "center", "both")

    assert result["center"] == "center"
    node_names = {n["id"] for n in result["nodes"]}
    # "helper" appears in TWO files but must collapse to ONE node.
    assert "helper" in node_names
    helper_node = next(n for n in result["nodes"] if n["id"] == "helper")
    assert helper_node["definition_count"] == 2


# ── T2: depth-1 boundary ─────────────────────────────────────────────────────


def test_depth_1_boundary(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Only direct (depth-1) neighbors returned, not transitive hops."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("direct", a), _sym("transitive", a),
    ], [
        _edge("center", "direct", a),
        _edge("direct", "transitive", a),
    ])

    result = build_neighborhood(conn, "center", "callees")
    node_names = {n["id"] for n in result["nodes"]}

    # "direct" is depth-1 -> must be present
    assert "direct" in node_names
    # "transitive" is depth-2 -> must NOT be present
    assert "transitive" not in node_names


# ── T3: direction=callees ─────────────────────────────────────────────────────


def test_direction_callees(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """direction='callees' -> only edges where center is source (callees)."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("callee_sym", a), _sym("caller_sym", a),
    ], [
        _edge("center", "callee_sym", a),     # center -> callee: should be included
        _edge("caller_sym", "center", a),     # caller -> center: should be excluded
    ])

    result = build_neighborhood(conn, "center", "callees")
    node_names = {n["id"] for n in result["nodes"]}

    assert "callee_sym" in node_names
    assert "caller_sym" not in node_names


# ── T4: direction=callers ─────────────────────────────────────────────────────


def test_direction_callers(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """direction='callers' -> only edges where center is target (callers)."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("callee_sym", a), _sym("caller_sym", a),
    ], [
        _edge("center", "callee_sym", a),    # center -> callee: should be excluded
        _edge("caller_sym", "center", a),    # caller -> center: should be included
    ])

    result = build_neighborhood(conn, "center", "callers")
    node_names = {n["id"] for n in result["nodes"]}

    assert "caller_sym" in node_names
    assert "callee_sym" not in node_names


# ── T5: direction=both ────────────────────────────────────────────────────────


def test_direction_both(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """direction='both' -> union of callers and callees."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("callee_sym", a), _sym("caller_sym", a),
    ], [
        _edge("center", "callee_sym", a),
        _edge("caller_sym", "center", a),
    ])

    result = build_neighborhood(conn, "center", "both")
    node_names = {n["id"] for n in result["nodes"]}

    assert "callee_sym" in node_names
    assert "caller_sym" in node_names


# ── T6: confidence passthrough ────────────────────────────────────────────────


def test_confidence_passthrough(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """EXTRACTED / AMBIGUOUS / INFERRED confidences are preserved on returned edges."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("nodeA", a), _sym("nodeB", a), _sym("nodeC", a),
    ], [
        _edge("center", "nodeA", a, confidence="EXTRACTED"),
        _edge("center", "nodeB", a, confidence="AMBIGUOUS"),
        _edge("center", "nodeC", a, confidence="INFERRED"),
    ])

    result = build_neighborhood(conn, "center", "callees")
    conf_map = {e["target"]: e["confidence"] for e in result["edges"]}

    assert conf_map.get("nodeA") == "EXTRACTED"
    assert conf_map.get("nodeB") == "AMBIGUOUS"
    assert conf_map.get("nodeC") == "INFERRED"


# ── T7: unknown-symbol safe return (center node exists in DB) ─────────────────


def test_unknown_symbol_no_edges(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """A symbol that exists but has no edges -> nodes=[center_node], edges=[]."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [_sym("lonely", a)], [])

    result = build_neighborhood(conn, "lonely", "both")

    assert result["center"] == "lonely"
    # The center node should be present even with no edges.
    node_names = {n["id"] for n in result["nodes"]}
    assert "lonely" in node_names
    assert result["edges"] == []


# ── T8: truly unknown symbol (not in DB at all) ───────────────────────────────


def test_truly_unknown_symbol(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Symbol not in DB at all -> {center, nodes:[], edges:[]}. Must NOT raise."""
    conn, tmp = tmp_db

    result = build_neighborhood(conn, "ghost_symbol_xyz", "both")

    assert result["center"] == "ghost_symbol_xyz"
    assert result["nodes"] == []
    assert result["edges"] == []


# ── T9: node shape ────────────────────────────────────────────────────────────


def test_node_shape(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Each node carries all required fields per the API contract."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a, signature="def center() -> None"),
        _sym("neighbor", a, kind="class", visibility="public", is_exported=1),
    ], [
        _edge("center", "neighbor", a),
    ])

    result = build_neighborhood(conn, "center", "both")
    # Include center node in check
    all_nodes = {n["id"]: n for n in result["nodes"]}

    required_keys = {"id", "name", "kind", "signature", "visibility", "is_exported",
                     "cluster_id", "cluster_label", "definition_count"}

    for node in all_nodes.values():
        missing = required_keys - set(node.keys())
        assert not missing, f"node {node['id']!r} missing keys: {missing}"

    # Spot-check values on neighbor
    neighbor = all_nodes.get("neighbor")
    assert neighbor is not None
    assert neighbor["kind"] == "class"
    assert neighbor["visibility"] == "public"
    assert neighbor["is_exported"] is True   # 1 -> True
    assert neighbor["definition_count"] == 1


# ── T10: edge shape ───────────────────────────────────────────────────────────


def test_edge_shape(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Each edge carries: id, source, target, kind, confidence."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("dep", a),
    ], [
        _edge("center", "dep", a, kind="call", confidence="EXTRACTED"),
    ])

    result = build_neighborhood(conn, "center", "both")
    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    required_keys = {"id", "source", "target", "kind", "confidence"}
    assert set(edge.keys()) >= required_keys
    assert edge["source"] == "center"
    assert edge["target"] == "dep"
    assert edge["kind"] == "call"
    assert edge["confidence"] == "EXTRACTED"


# ── T11: edge kind preservation ───────────────────────────────────────────────


def test_edge_kind_import(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """'import' kind edges are preserved (not just 'call' edges)."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("center", a), _sym("lib", a),
    ], [
        _edge("center", "lib", a, kind="import", confidence="INFERRED"),
    ])

    result = build_neighborhood(conn, "center", "both")
    assert len(result["edges"]) == 1
    assert result["edges"][0]["kind"] == "import"


# ── T12: no raise on empty DB ─────────────────────────────────────────────────


def test_no_raise_on_empty_db(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """build_neighborhood on an empty (but initialized) DB never raises."""
    conn, _ = tmp_db
    result = build_neighborhood(conn, "anything", "both")

    assert result["center"] == "anything"
    assert result["nodes"] == []
    assert result["edges"] == []


# ── Constellation (Task B4) ────────────────────────────────────────────────────


def test_constellation_cross_cluster_link(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """A cross-cluster edge produces one link with the right weight."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    # alpha (cluster A) calls beta (cluster B) twice (two edges).
    upsert_file(conn, Path(a), "python", "h1", [_sym("alpha", a), _sym("beta", a)], [
        _edge("alpha", "beta", a),
        _edge("alpha", "beta", a, kind="import"),
    ])
    ca = _seed_cluster(conn, "A", ["alpha"])
    cb = _seed_cluster(conn, "B", ["beta"])

    result = build_constellation(conn)

    cluster_ids = {c["cluster_id"] for c in result["clusters"]}
    assert {ca, cb} <= cluster_ids
    links = result["links"]
    assert len(links) == 1
    assert links[0]["source"] == ca
    assert links[0]["target"] == cb
    assert links[0]["weight"] == 2


def test_constellation_intra_cluster_no_link(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """An edge between two members of the SAME cluster produces no link."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [_sym("x", a), _sym("y", a)], [
        _edge("x", "y", a),
    ])
    _seed_cluster(conn, "A", ["x", "y"])

    result = build_constellation(conn)
    assert result["links"] == []


def test_constellation_unclustered_edge_ignored(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Edges touching an unclustered (cluster_id NULL) symbol contribute no link."""
    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [_sym("p", a), _sym("q", a)], [
        _edge("p", "q", a),
    ])
    _seed_cluster(conn, "A", ["p"])  # q stays unclustered

    result = build_constellation(conn)
    assert result["links"] == []


def test_constellation_empty_db_safe(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Empty (no clusters) DB returns a safe empty envelope, never raises."""
    conn, _ = tmp_db
    result = build_constellation(conn)
    assert result == {"clusters": [], "links": []}


# ── top_hub_symbols (landing entry points) ──────────────────────────────────────


def test_top_hub_symbols_ranks_by_degree(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """The most-connected defined symbol ranks first; degree counts both directions."""
    from seam.server.graph_api import top_hub_symbols

    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    # hub is called by x and y, and calls z → degree 3. leaf only appears once.
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("hub", a), _sym("x", a), _sym("y", a), _sym("z", a), _sym("leaf", a),
    ], [
        _edge("x", "hub", a),
        _edge("y", "hub", a),
        _edge("hub", "z", a),
        _edge("z", "leaf", a),
    ])

    hubs = top_hub_symbols(conn, limit=10)
    names = [h["name"] for h in hubs]
    assert names[0] == "hub"  # degree 3, highest
    assert hubs[0]["degree"] == 3
    assert hubs[0]["kind"] == "function"
    # Each hub carries a representative declaring path (for area bucketing).
    assert hubs[0]["path"] == a


def test_top_hub_symbols_excludes_undefined_names(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Edge targets with no row in symbols (e.g. builtins) are excluded."""
    from seam.server.graph_api import top_hub_symbols

    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    # `caller` calls `print` 3× — print is NOT defined in symbols → must be excluded.
    upsert_file(conn, Path(a), "python", "h1", [_sym("caller", a)], [
        _edge("caller", "print", a),
        _edge("caller", "print", a),
        _edge("caller", "print", a),
    ])

    names = [h["name"] for h in top_hub_symbols(conn, limit=10)]
    assert "print" not in names
    assert "caller" in names


def test_top_hub_symbols_empty_db_safe(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Empty index returns [] and never raises."""
    conn, _ = tmp_db
    from seam.server.graph_api import top_hub_symbols

    assert top_hub_symbols(conn) == []


# ── top_hub_symbols — test-exclusion (A1) ────────────────────────────────────────


def test_top_hub_symbols_excludes_test_paths_by_default(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """A1: default show_tests=False excludes symbols whose declaring file is in a test path.

    WHY: the landing 'Key symbols' should show real hubs (e.g. init_db, parse_file),
    not test helpers (_sym, _walk, _text) that are highly connected only because the
    test suite itself calls them in many tests.
    """
    from seam.server.graph_api import top_hub_symbols

    conn, tmp = tmp_db
    src_file = str(tmp / "a.py")
    test_file = str(tmp / "tests" / "conftest.py")
    # Create the test directory + file so upsert_file can stat them.
    (tmp / "tests").mkdir(exist_ok=True)
    (tmp / "tests" / "conftest.py").write_text("# stub\n")

    # source hub: degree 4 (called by 4 others).
    upsert_file(conn, Path(src_file), "python", "h1", [
        _sym("src_hub", src_file),
        _sym("a", src_file),
        _sym("b", src_file),
        _sym("c", src_file),
        _sym("d", src_file),
    ], [
        _edge("a", "src_hub", src_file),
        _edge("b", "src_hub", src_file),
        _edge("c", "src_hub", src_file),
        _edge("d", "src_hub", src_file),
    ])

    # test hub: degree 5 (higher than src_hub but lives in tests/).
    upsert_file(conn, Path(test_file), "python", "h2", [
        _sym("test_hub", test_file),
        _sym("t1", test_file),
        _sym("t2", test_file),
        _sym("t3", test_file),
        _sym("t4", test_file),
        _sym("t5", test_file),
    ], [
        _edge("t1", "test_hub", test_file),
        _edge("t2", "test_hub", test_file),
        _edge("t3", "test_hub", test_file),
        _edge("t4", "test_hub", test_file),
        _edge("t5", "test_hub", test_file),
    ])

    # Default: show_tests=False — test_hub must be absent even though it has higher degree.
    names = [h["name"] for h in top_hub_symbols(conn, limit=10)]
    assert "src_hub" in names, "source hub should appear by default"
    assert "test_hub" not in names, "test hub must be excluded by default (show_tests=False)"


def test_top_hub_symbols_show_tests_includes_test_path(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """A1: show_tests=True re-includes symbols from test paths.

    WHY: a developer may want to see which test helpers are hottest. The toggle
    makes the exclusion opt-out, not permanent.
    """
    from seam.server.graph_api import top_hub_symbols

    conn, tmp = tmp_db
    test_file = str(tmp / "tests" / "conftest.py")
    (tmp / "tests").mkdir(exist_ok=True)
    (tmp / "tests" / "conftest.py").write_text("# stub\n")

    upsert_file(conn, Path(test_file), "python", "h1", [
        _sym("test_helper", test_file),
        _sym("x", test_file),
    ], [
        _edge("x", "test_helper", test_file),
    ])

    # With show_tests=True the helper must be included.
    names_with = [h["name"] for h in top_hub_symbols(conn, limit=10, show_tests=True)]
    assert "test_helper" in names_with, "show_tests=True should include test-path hubs"

    # Without it (default) it must be absent.
    names_without = [h["name"] for h in top_hub_symbols(conn, limit=10)]
    assert "test_helper" not in names_without, "show_tests=False (default) should exclude test-path hubs"


def test_top_hub_symbols_source_hub_ranked_before_test_hub_with_show_tests(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """A1: when show_tests=True, ordering is still degree DESC (source or test, best hub first)."""
    from seam.server.graph_api import top_hub_symbols

    conn, tmp = tmp_db
    src_file = str(tmp / "a.py")
    test_file = str(tmp / "tests" / "helpers.py")
    (tmp / "tests").mkdir(exist_ok=True)
    (tmp / "tests" / "helpers.py").write_text("# stub\n")

    # src_hub degree 3, test_hub degree 2 — src_hub must come first.
    upsert_file(conn, Path(src_file), "python", "h1", [
        _sym("src_hub", src_file), _sym("a", src_file), _sym("b", src_file), _sym("c", src_file),
    ], [
        _edge("a", "src_hub", src_file),
        _edge("b", "src_hub", src_file),
        _edge("c", "src_hub", src_file),
    ])
    upsert_file(conn, Path(test_file), "python", "h2", [
        _sym("test_hub", test_file), _sym("t1", test_file), _sym("t2", test_file),
    ], [
        _edge("t1", "test_hub", test_file),
        _edge("t2", "test_hub", test_file),
    ])

    hubs = top_hub_symbols(conn, limit=10, show_tests=True)
    names = [h["name"] for h in hubs]
    assert names.index("src_hub") < names.index("test_hub"), "src_hub (degree 3) must rank above test_hub (degree 2)"


# ── list_structure (treemap source) ─────────────────────────────────────────────


def test_list_structure_returns_path_and_nesting(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Every symbol is returned with its file path, kind, line, qualified_name."""
    from seam.server.graph_api import list_structure

    conn, tmp = tmp_db
    a = str(tmp / "a.py")
    upsert_file(conn, Path(a), "python", "h1", [
        _sym("Widget", a, kind="class"),
        _sym("render", a, kind="method"),
    ], [])

    rows = list_structure(conn)
    names = {r["name"] for r in rows}
    assert {"Widget", "render"} <= names
    for r in rows:
        assert set(r.keys()) == {"path", "name", "kind", "line", "qualified_name"}
        assert r["path"] == a


def test_list_structure_empty_db_safe(tmp_db: tuple[sqlite3.Connection, Path]) -> None:
    """Empty index returns [] and never raises."""
    conn, _ = tmp_db
    from seam.server.graph_api import list_structure

    assert list_structure(conn) == []
