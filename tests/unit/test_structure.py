"""Unit tests for seam/query/structure.py — Slice 1 tracer-bullet.

TDD: Tests written before implementation (red-green-refactor).

Test groups:
    S1 — Core tree shape: nesting, container roll-up, top-level funcs present,
         symbol counts, area=None, truncated=0.
    S2 — Handler: relativization, no absolute paths leak, correct StructureResult shape.
    S3 — CLI: --json envelope, NO_INDEX guard.

Fixtures build a tiny in-memory/temp SQLite index (same pattern as test_flows.py
and test_query_clusters.py). No mocking of the query layer — tests exercise the
actual SQL path end-to-end.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(
    name: str,
    file: str,
    kind: str = "function",
    line: int = 1,
    qualified_name: str | None = None,
) -> Symbol:
    """Build a Symbol TypedDict for test seeding."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring=None,
        signature=None,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=qualified_name or name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind="call",
        file=file,
        line=1,
        confidence="EXTRACTED",
    )


@pytest.fixture()
def tmp_db():
    """Yield (conn, tmp_path) with an initialized DB + stub source files."""
    with tempfile.TemporaryDirectory() as tmp:
        # Resolve to mirror production: `seam init` stores symlink-resolved paths
        # (it does Path(path).resolve() before walking), and every read command
        # resolves project_root too. On macOS the temp dir is under a /var -> /private/var
        # symlink, so an unresolved tmp_path would not match the resolved paths the CLI
        # builds — resolving here keeps the fixture faithful to the real index.
        tmp_path = Path(tmp).resolve()
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)
        (tmp_path / "app.py").write_text("# stub\n")
        (tmp_path / "utils.py").write_text("# stub\n")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "helper.py").write_text("# stub\n")
        yield conn, tmp_path, db_path
        conn.close()


def _seed_basic(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Seed a basic index:
    app.py          : MyClass (class), MyClass.do_thing (method), standalone_func (function)
    utils.py        : UtilClass (class), UtilClass.helper (method), top_level (function)
    subdir/helper.py: bare_func (function)
    """
    app = str(tmp_path / "app.py")
    utils = str(tmp_path / "utils.py")
    helper = str(tmp_path / "subdir" / "helper.py")

    # app.py symbols: class + method + top-level function
    upsert_file(
        conn,
        Path(app),
        "python",
        "h1",
        [
            _sym("MyClass", app, kind="class", line=1, qualified_name="MyClass"),
            _sym("MyClass.do_thing", app, kind="method", line=5, qualified_name="MyClass.do_thing"),
            _sym(
                "standalone_func", app, kind="function", line=15, qualified_name="standalone_func"
            ),
        ],
        [],
    )

    # utils.py symbols
    upsert_file(
        conn,
        Path(utils),
        "python",
        "h2",
        [
            _sym("UtilClass", utils, kind="class", line=1, qualified_name="UtilClass"),
            _sym(
                "UtilClass.helper", utils, kind="method", line=3, qualified_name="UtilClass.helper"
            ),
            _sym("top_level", utils, kind="function", line=10, qualified_name="top_level"),
        ],
        [],
    )

    # subdir/helper.py
    upsert_file(
        conn,
        Path(helper),
        "python",
        "h3",
        [
            _sym("bare_func", helper, kind="function", line=1, qualified_name="bare_func"),
        ],
        [],
    )


# ── S1: Core tree shape ────────────────────────────────────────────────────────


class TestBuildStructure:
    """S1: build_structure() returns the correct tree shape."""

    def test_returns_structure_result(self, tmp_db) -> None:
        """build_structure returns a dict with 'tree' and 'truncated' keys."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        assert "tree" in result
        assert "truncated" in result

    def test_truncated_is_zero_slice1(self, tmp_db) -> None:
        """Slice 1: no caps, so truncated must always be 0."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        assert result["truncated"] == 0

    def test_area_is_none_slice1(self, tmp_db) -> None:
        """Slice 1: area is None on every node (labels come in a later slice)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        def _all_areas(node: dict) -> list:
            areas = [node.get("area")]
            for child in node.get("children", []):
                areas.extend(_all_areas(child))
            for member_container in node.get("children", []):
                pass  # members is an int, not nodes
            return areas

        result = build_structure(conn, tmp_path)
        areas = _all_areas(result["tree"])
        assert all(a is None for a in areas)

    def test_root_node_is_dir_kind(self, tmp_db) -> None:
        """The root of the tree is a 'dir' node."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        assert result["tree"]["kind"] == "dir"

    def test_file_nodes_present_under_root(self, tmp_db) -> None:
        """File nodes for app.py and utils.py appear as children of the root dir."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        root = result["tree"]
        # app.py and utils.py should be direct children (or in a subdir child)
        names_or_paths = _collect_names_paths(root)
        assert any("app.py" in (n or "") for n in names_or_paths)
        assert any("utils.py" in (n or "") for n in names_or_paths)

    def test_methods_roll_up_into_container_members(self, tmp_db) -> None:
        """Methods (MyClass.do_thing) appear as members count on the container, not as nodes."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        # Find the MyClass container node
        my_class_node = _find_node_by_name(result["tree"], "MyClass")
        assert my_class_node is not None, "MyClass container node not found"
        assert my_class_node["kind"] == "container"
        # members count should include MyClass.do_thing
        assert my_class_node["members"] >= 1

    def test_method_not_a_separate_top_level_node(self, tmp_db) -> None:
        """MyClass.do_thing must NOT appear as a separate file-level node."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        # Traverse only file-level children (not container children)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        child_names = [c["name"] for c in file_node.get("children", [])]
        assert "MyClass.do_thing" not in child_names

    def test_top_level_function_present_under_file(self, tmp_db) -> None:
        """standalone_func appears under its file node when include_functions=True.

        (Standalone functions are suppressed in the overview default — see
        TestOverviewDefault — so this asserts the detailed-view behaviour explicitly.)
        """
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path, include_functions=True)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        child_names = [c["name"] for c in file_node.get("children", [])]
        assert "standalone_func" in child_names

    def test_container_path_is_none(self, tmp_db) -> None:
        """Container nodes have path=None (they are logical, not file-backed)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        my_class_node = _find_node_by_name(result["tree"], "MyClass")
        assert my_class_node is not None
        assert my_class_node["path"] is None

    def test_file_node_path_is_relative(self, tmp_db) -> None:
        """File nodes carry a path relative to root, not an absolute path."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        assert not Path(file_node["path"]).is_absolute(), (
            f"Expected relative path, got absolute: {file_node['path']}"
        )

    def test_subdir_appears_as_nested_dir(self, tmp_db) -> None:
        """subdir/ appears as a nested 'dir' node containing helper.py."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        subdir_node = _find_node_by_name(result["tree"], "subdir")
        assert subdir_node is not None
        assert subdir_node["kind"] == "dir"
        # It should contain helper.py
        child_names = [c["name"] for c in subdir_node.get("children", [])]
        assert any("helper.py" in n for n in child_names)

    def test_symbol_count_on_file_node(self, tmp_db) -> None:
        """symbol_count on file node reflects total symbols in that file."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        # app.py has 3 symbols: MyClass, MyClass.do_thing, standalone_func
        assert file_node["symbol_count"] == 3

    def test_empty_index_returns_safe_tree(self, tmp_db) -> None:
        """An empty index returns a valid tree (not an error, not a raise)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        # No seeding — empty index
        result = build_structure(conn, tmp_path)
        assert "tree" in result
        assert result["truncated"] == 0

    def test_never_raises_on_garbage_db(self) -> None:
        """build_structure never raises even on a partial/empty DB."""
        from seam.query.structure import build_structure

        # Use in-memory DB without full schema — just check no exception escapes
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT, start_line INTEGER)"
        )
        conn.commit()
        try:
            result = build_structure(conn, Path("/tmp"))
        except Exception as exc:
            pytest.fail(f"build_structure raised: {exc}")
        finally:
            conn.close()
        assert "tree" in result


# ── S2: Handler ────────────────────────────────────────────────────────────────


class TestHandleSeamStructure:
    """S2: handle_seam_structure relativizes paths and returns StructureResult shape."""

    def test_handler_returns_tree_and_truncated(self, tmp_db) -> None:
        """handle_seam_structure returns a dict with tree + truncated."""
        from seam.server.tools import handle_seam_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = handle_seam_structure(conn, tmp_path)
        assert "tree" in result
        assert "truncated" in result

    def test_no_absolute_paths_in_output(self, tmp_db) -> None:
        """No absolute paths leak into the handler output."""
        from seam.server.tools import handle_seam_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = handle_seam_structure(conn, tmp_path)
        all_paths = _collect_all_paths(result["tree"])
        for p in all_paths:
            if p is not None:
                assert not Path(p).is_absolute(), f"Absolute path leaked: {p}"

    def test_tool_count_includes_schema(self, tmp_db) -> None:
        """seam_structure and seam_schema are both registered."""
        from seam.server.mcp import create_server

        conn, tmp_path, _ = tmp_db

        server = create_server(conn, tmp_path)
        conn.close()

        tool_names = list(server._tool_manager._tools.keys())
        assert "seam_structure" in tool_names, f"seam_structure not in tools: {sorted(tool_names)}"
        assert len(tool_names) == 13, (
            f"Expected 13 tools, got {len(tool_names)}: {sorted(tool_names)}"
        )


# ── S3: CLI ────────────────────────────────────────────────────────────────────


class TestCLIStructure:
    """S3: `seam structure` CLI command — --json envelope and NO_INDEX guard."""

    def test_json_envelope_shape(self, tmp_db) -> None:
        """seam structure --json emits {ok: true, data: {tree: ..., truncated: ...}}."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        conn.close()  # CLI opens its own connection

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--json"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "tree" in data["data"]
        assert "truncated" in data["data"]

    def test_no_index_error_json(self, tmp_path: Path) -> None:
        """seam structure --json returns NO_INDEX envelope when index is missing."""
        from seam.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["error"]["code"] == "NO_INDEX"

    def test_quiet_mode_produces_output(self, tmp_db) -> None:
        """seam structure --quiet renders without error and produces some output."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        conn.close()

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--quiet"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        # Quiet mode should produce some output (tree rendering)
        assert result.output.strip() != ""


# ── S4: Slice 2 — functional-area annotation ──────────────────────────────────


def _seed_clusters(conn: sqlite3.Connection, label: str, symbol_names: list[str]) -> int:
    """Insert a cluster and assign the named symbols to it. Returns cluster id."""
    cur = conn.execute(
        "INSERT INTO clusters (label, size, naming_source) VALUES (?, ?, 'deterministic')",
        (label, len(symbol_names)),
    )
    cid = cur.lastrowid
    for name in symbol_names:
        conn.execute("UPDATE symbols SET cluster_id = ? WHERE name = ?", (cid, name))
    conn.commit()
    return int(cid)


class TestAreaAnnotation:
    """S4: build_structure() with cluster data populates 'area' on file/dir nodes."""

    def test_file_area_plurality_single_cluster(self, tmp_db) -> None:
        """File node area = label of cluster with most symbols in that file."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        # All 3 symbols in app.py -> cluster "core"
        _seed_clusters(conn, "core", ["MyClass", "MyClass.do_thing", "standalone_func"])

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        assert file_node["area"] == "core"

    def test_file_area_plurality_winner_chosen(self, tmp_db) -> None:
        """File area = the cluster label held by the MOST symbols (plurality)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        # app.py: MyClass + MyClass.do_thing -> "core" (2 symbols), standalone_func -> "util" (1)
        _seed_clusters(conn, "core", ["MyClass", "MyClass.do_thing"])
        _seed_clusters(conn, "util", ["standalone_func"])

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        assert file_node["area"] == "core"  # 2 vs 1 -> core wins

    def test_file_area_tie_deterministic_lowest_cluster_id(self, tmp_db) -> None:
        """On a tie, the area with the lowest cluster id wins deterministically."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        # app.py has 3 symbols; let's give 1 to each of two clusters and leave 1 unclustered.
        # With exactly 1 symbol each in cluster A and cluster B, lowest id wins.
        cid_a = _seed_clusters(conn, "alpha", ["MyClass"])
        cid_b = _seed_clusters(conn, "beta", ["standalone_func"])
        # MyClass.do_thing remains unclustered (cluster_id=NULL)

        # alpha and beta have 1 symbol each in app.py — tie -> lowest id wins
        winner_label = "alpha" if cid_a < cid_b else "beta"

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        assert file_node["area"] == winner_label

    def test_dir_area_plurality_of_child_files(self, tmp_db) -> None:
        """Dir area = plurality of its child files' areas."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        # app.py -> "core", utils.py -> "util", subdir/helper.py -> "util"
        _seed_clusters(conn, "core", ["MyClass", "MyClass.do_thing", "standalone_func"])
        _seed_clusters(conn, "util", ["UtilClass", "UtilClass.helper", "top_level", "bare_func"])

        result = build_structure(conn, tmp_path)
        # Root dir: 1 file (app.py) with "core", 1 file (utils.py) with "util"
        # Plus subdir (which has 1 file -> "util"), so dir children are:
        #   app.py (core), utils.py (util), subdir/ -> has 1 file "util"
        # Root level: core(1), util(1 direct + 1 through subdir)... but area is
        # computed from direct file children only (not recursive). Let's check subdir.
        subdir_node = _find_node_by_name(result["tree"], "subdir")
        assert subdir_node is not None
        assert subdir_node["area"] == "util"

    def test_no_clusters_area_stays_none(self, tmp_db) -> None:
        """When the index has no cluster data, area is None for all nodes."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        # No clusters seeded -> all cluster_id are NULL

        result = build_structure(conn, tmp_path)

        def _all_areas(node: dict) -> list:
            areas = [node.get("area")]
            for child in node.get("children", []):
                areas.extend(_all_areas(child))
            return areas

        areas = _all_areas(result["tree"])
        assert all(a is None for a in areas), f"Expected all None, got: {areas}"

    def test_area_in_json_output(self, tmp_db) -> None:
        """Area appears in --json output when clusters are present."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        _seed_clusters(conn, "core", ["MyClass", "MyClass.do_thing", "standalone_func"])
        conn.close()

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--json"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)
        tree = data["data"]["tree"]

        # Find app.py file node and verify area is set
        file_node = _find_node_by_path_fragment(tree, "app.py")
        assert file_node is not None
        assert file_node.get("area") == "core"

    def test_area_in_quiet_output(self, tmp_db) -> None:
        """Area label appears in --quiet CLI output when clusters are present."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        _seed_clusters(conn, "core", ["MyClass", "MyClass.do_thing", "standalone_func"])
        conn.close()

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--quiet"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        # The word "core" should appear in the output
        assert "core" in result.output, f"Expected 'core' in output:\n{result.output}"


# ── S5: Slice 3 — scoping (path) + bounds (depth/caps/truncation) ─────────────


def _seed_deep(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Seed a deep nested structure: root/a/b/c/deep.py with one function.

    Layout:
        app.py          : standalone_func
        a/mid.py        : mid_func
        a/b/inner.py    : inner_func
        a/b/c/deep.py   : deep_func
    """
    app = str(tmp_path / "app.py")
    mid = str(tmp_path / "a" / "mid.py")
    inner = str(tmp_path / "a" / "b" / "inner.py")
    deep = str(tmp_path / "a" / "b" / "c" / "deep.py")

    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "a" / "b").mkdir(exist_ok=True)
    (tmp_path / "a" / "b" / "c").mkdir(exist_ok=True)
    for fp in [app, mid, inner, deep]:
        Path(fp).write_text("# stub\n")

    upsert_file(conn, Path(app), "python", "h_app", [_sym("standalone_func", app)], [])
    upsert_file(conn, Path(mid), "python", "h_mid", [_sym("mid_func", mid)], [])
    upsert_file(conn, Path(inner), "python", "h_inner", [_sym("inner_func", inner)], [])
    upsert_file(conn, Path(deep), "python", "h_deep", [_sym("deep_func", deep)], [])


class TestSlice3Bounds:
    """S5: Slice 3 — depth cap, node cap, truncation reporting."""

    def test_max_depth_collapses_deep_nesting(self, tmp_db) -> None:
        """max_depth=1 means only the root dir and its DIRECT children are kept."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_deep(conn, tmp_path)

        # depth=1: root node (depth 0) + its direct children (depth 1); deeper cut.
        result = build_structure(conn, tmp_path, max_depth=1)
        tree = result["tree"]
        assert tree["kind"] == "dir"

        # All children of root should be depth 1 — none should have children themselves
        # (except that file children of a dir at depth 1 may still be present, but
        # no dir at depth >= 2 should appear).
        def _max_dir_depth(node: dict, cur: int = 0) -> int:
            if node["kind"] == "dir" and node.get("children"):
                return max(_max_dir_depth(c, cur + 1) for c in node["children"])
            return cur

        # With max_depth=1 the deepest dir we reach is depth 1 — no nested dirs beyond.
        assert _max_dir_depth(tree) <= 1

    def test_max_nodes_truncates_and_reports_count(self, tmp_db) -> None:
        """max_nodes=2 limits nodes emitted; truncated reflects omitted count."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        # With max_nodes=2 we should get exactly 2 nodes and truncated > 0
        result = build_structure(conn, tmp_path, max_nodes=2)
        assert result["truncated"] > 0

        def _count_nodes(node: dict) -> int:
            return 1 + sum(_count_nodes(c) for c in node.get("children", []))

        # Total nodes (excluding root which is always present) should be <= max_nodes
        node_count = _count_nodes(result["tree"])
        # truncated + node_count should equal the total without caps
        full_result = build_structure(conn, tmp_path)
        full_count = _count_nodes(full_result["tree"])
        assert result["truncated"] + node_count == full_count

    def test_truncated_zero_when_no_cap_exceeded(self, tmp_db) -> None:
        """truncated=0 when max_nodes is large enough to hold the full tree."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path, max_nodes=99999)
        assert result["truncated"] == 0

    def test_path_scopes_to_subdirectory(self, tmp_db) -> None:
        """path=subdir scopes the tree to that subtree only."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        subdir = tmp_path / "subdir"
        result = build_structure(conn, tmp_path, path=subdir)
        tree = result["tree"]

        # Only subdir content should appear — app.py and utils.py should NOT be present
        all_paths = _collect_all_paths(tree)
        assert not any("app.py" in (p or "") for p in all_paths), (
            "app.py should not appear when scoped to subdir"
        )
        assert not any("utils.py" in (p or "") for p in all_paths), (
            "utils.py should not appear when scoped to subdir"
        )
        # helper.py IS in subdir, so it should appear
        assert any("helper.py" in (p or "") for p in all_paths), (
            "helper.py should appear when scoped to subdir"
        )

    def test_unknown_path_returns_empty_tree(self, tmp_db) -> None:
        """A path that doesn't exist in the index returns an empty (safe) tree."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        nonexistent = tmp_path / "does_not_exist"
        result = build_structure(conn, tmp_path, path=nonexistent)
        assert "tree" in result
        assert "truncated" in result
        # The tree may be empty (no files under the unknown path)
        tree = result["tree"]
        assert tree["kind"] == "dir"
        # No file children
        all_paths = _collect_all_paths(tree)
        # Filter out None and the root dir path (which is also None)
        non_none = [p for p in all_paths if p is not None]
        assert len(non_none) == 0, f"Expected empty tree, found paths: {non_none}"

    def test_out_of_tree_path_degrades_cleanly(self, tmp_db) -> None:
        """An out-of-tree path (outside repo root) does not crash."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        out_of_tree = Path("/tmp/completely_outside_repo")
        try:
            result = build_structure(conn, tmp_path, path=out_of_tree)
        except Exception as exc:
            pytest.fail(f"build_structure raised on out-of-tree path: {exc}")
        assert "tree" in result


class TestSlice3CLI:
    """S5: CLI --depth flag and path argument for seam structure."""

    def test_depth_flag_via_cli_json(self, tmp_db) -> None:
        """seam structure --depth 1 --json limits tree depth."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_deep(conn, tmp_path)
        conn.close()

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--depth", "1", "--json"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)
        assert data["ok"] is True
        tree = data["data"]["tree"]

        # No dir node should have depth > 1 (only direct children of root)
        def _max_dir_depth(node: dict, cur: int = 0) -> int:
            if node["kind"] == "dir" and node.get("children"):
                return max(_max_dir_depth(c, cur + 1) for c in node["children"])
            return cur

        assert _max_dir_depth(tree) <= 1, "depth=1 should prevent dirs at depth>=2"

    def test_path_scoping_via_cli_json(self, tmp_db) -> None:
        """seam structure <subdir> --json scopes to that subdirectory."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        conn.close()

        subdir = str(tmp_path / "subdir")
        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--scope", subdir, "--json"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)
        assert data["ok"] is True
        tree = data["data"]["tree"]

        # Only subdir content visible
        all_paths = _collect_all_paths(tree)
        assert not any("app.py" in (p or "") for p in all_paths)
        assert any("helper.py" in (p or "") for p in all_paths)


# ── Tree-walking helpers ───────────────────────────────────────────────────────


def _collect_names_paths(node: dict) -> list[str | None]:
    """Collect all name and path strings recursively."""
    result = [node.get("name"), node.get("path")]
    for child in node.get("children", []):
        result.extend(_collect_names_paths(child))
    return result


def _find_node_by_name(node: dict, name: str) -> dict | None:
    """DFS search for a node with the given name."""
    if node.get("name") == name:
        return node
    for child in node.get("children", []):
        found = _find_node_by_name(child, name)
        if found is not None:
            return found
    return None


def _find_node_by_path_fragment(node: dict, fragment: str) -> dict | None:
    """DFS search for a node whose path contains the given fragment."""
    path = node.get("path") or ""
    if fragment in path:
        return node
    for child in node.get("children", []):
        found = _find_node_by_path_fragment(child, fragment)
        if found is not None:
            return found
    return None


def _collect_all_paths(node: dict) -> list[str | None]:
    """Collect all 'path' values recursively."""
    result = [node.get("path")]
    for child in node.get("children", []):
        result.extend(_collect_all_paths(child))
    return result


# ── Post-review hardening: scope semantics, found:false, unlimited cap ──────────


class TestReviewHardening:
    """Behaviours added/changed during /review + /backend-taste of Tier D11.

    Covers: root-relative (not cwd-relative) scope resolution, the {found:false}
    MCP contract on an empty/out-of-tree scope, the `nodes` MCP cap param, and the
    `max_nodes <= 0 == unlimited` convention.
    """

    def test_relative_scope_resolves_against_root_not_cwd(self, tmp_db) -> None:
        """A RELATIVE scope path joins to `root`, not the process cwd.

        Regression: the original code did Path(scope).resolve() (cwd-relative), so
        `seam structure /repo --scope src/` silently scoped to $CWD/src and returned
        an empty tree whenever the inspected repo was not the working directory.
        """
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        # Relative scope "subdir" must resolve under tmp_path regardless of cwd.
        result = build_structure(conn, tmp_path, path=Path("subdir"))
        all_paths = _collect_all_paths(result["tree"])
        assert any("helper.py" in (p or "") for p in all_paths), (
            "relative scope 'subdir' should resolve against root and include helper.py"
        )
        assert not any("app.py" in (p or "") for p in all_paths), (
            "files outside the scoped subdir must be excluded"
        )

    def test_handler_relative_scope_resolves_against_root(self, tmp_db) -> None:
        """handle_seam_structure forwards a relative scope to be root-joined."""
        from seam.server.tools import handle_seam_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = handle_seam_structure(conn, tmp_path, path=Path("subdir"))
        all_paths = _collect_all_paths(result["tree"])
        assert any("helper.py" in (p or "") for p in all_paths)
        assert not any("app.py" in (p or "") for p in all_paths)

    def test_max_nodes_zero_is_unlimited(self, tmp_db) -> None:
        """max_nodes=0 means UNLIMITED (no cap), not 'drop everything'.

        Regression: the previous `if max_nodes <= 0: drop all` branch silently
        emptied the tree when an operator set MAX_NODES=0/-1 expecting no bound.
        """
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        full = build_structure(conn, tmp_path, max_nodes=999999)
        unlimited = build_structure(conn, tmp_path, max_nodes=0)

        assert unlimited["truncated"] == 0, "max_nodes=0 must not truncate"
        # Same node population as an effectively-uncapped run.
        assert _count_tree_nodes(unlimited["tree"]) == _count_tree_nodes(full["tree"])
        assert _count_tree_nodes(unlimited["tree"]) > 1, "tree must be non-empty"

    def test_negative_max_nodes_is_unlimited(self, tmp_db) -> None:
        """A negative max_nodes is also treated as unlimited (no silent empty)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path, max_nodes=-1)
        assert result["truncated"] == 0
        assert _count_tree_nodes(result["tree"]) > 1

    def test_mcp_tool_advertises_nodes_param(self, tmp_db) -> None:
        """The seam_structure MCP tool exposes the `nodes` cap param (parity with depth)."""
        from seam.server.mcp import create_server

        conn, tmp_path, _ = tmp_db
        server = create_server(conn, tmp_path)
        conn.close()

        tools = {t.name: t for t in server._tool_manager.list_tools()}
        props = tools["seam_structure"].parameters.get("properties", {})
        assert "nodes" in props, f"seam_structure must advertise 'nodes'; got {sorted(props)}"
        assert "depth" in props and "path" in props

    def test_mcp_empty_scope_returns_found_false(self, tmp_db) -> None:
        """An out-of-tree scope yields {found: false} at the MCP boundary (not an empty tree)."""
        from seam.server.mcp import create_server

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        server = create_server(conn, tmp_path)

        tools = {t.name: t for t in server._tool_manager.list_tools()}
        # A path outside the repo root must normalize to the not-found sentinel.
        out = tools["seam_structure"].fn(path="/definitely/not/in/this/repo")
        conn.close()
        assert out == {"found": False}, f"expected found:false sentinel, got {out!r}"


def _count_tree_nodes(node: dict) -> int:
    """Count all nodes in a tree including the root."""
    return 1 + sum(_count_tree_nodes(c) for c in node.get("children", []))


# ── Overview default: module/area view suppresses standalone functions ──────────


def _collect_kinds(node: dict) -> set:
    """Collect the set of node kinds present in the tree."""
    out = {node["kind"]}
    for c in node.get("children", []):
        out |= _collect_kinds(c)
    return out


class TestOverviewDefault:
    """The DEFAULT structure view is a module/area overview: dir → file → container.

    Standalone (module-level) functions are suppressed by default — they buried the
    'main modules' answer (a function-heavy file dumped 33 nodes, hiding the module
    breadth). `include_functions=True` (CLI --symbols / MCP symbols=True) restores them.
    Classes/interfaces/types (structural landmarks) are always kept.
    """

    def test_default_suppresses_standalone_functions(self, tmp_db) -> None:
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        tree = build_structure(conn, tmp_path)["tree"]
        kinds = _collect_kinds(tree)
        assert "function" not in kinds, "overview default must NOT list standalone functions"
        assert "container" in kinds, "overview must keep classes/containers as landmarks"
        assert "file" in kinds and "dir" in kinds

    def test_default_file_count_still_includes_functions(self, tmp_db) -> None:
        """Suppressing function NODES must not change the file's symbol_count."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        tree = build_structure(conn, tmp_path)["tree"]
        # app.py has MyClass + MyClass.do_thing + standalone_func = 3 rows total
        app = _find_node_by_path_fragment(tree, "app.py")
        assert app is not None and app["symbol_count"] == 3, (
            "file symbol_count must still count the suppressed function"
        )

    def test_include_functions_true_restores_function_nodes(self, tmp_db) -> None:
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)
        tree = build_structure(conn, tmp_path, include_functions=True)["tree"]
        assert "function" in _collect_kinds(tree), (
            "include_functions=True must restore standalone function nodes"
        )

    def test_mcp_tool_advertises_symbols_param(self, tmp_db) -> None:
        from seam.server.mcp import create_server

        conn, tmp_path, _ = tmp_db
        server = create_server(conn, tmp_path)
        conn.close()
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        props = tools["seam_structure"].parameters.get("properties", {})
        assert "symbols" in props, f"seam_structure must advertise 'symbols'; got {sorted(props)}"
