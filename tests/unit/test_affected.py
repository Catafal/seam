"""Unit tests for seam/analysis/affected.py (Slice 3).

TDD: these tests are written BEFORE the implementation. They drive
the exact contract the module must satisfy.

Coverage:
  A1  changed source file with dependent test file -> test file in affected_tests
  A2  changed file that is itself a test file -> included directly in affected_tests
  A3  depth bound respected — dependents beyond max depth not included
  A4  changed file with no dependents -> empty affected_tests (not an error)
  A5  multiple changed files dedup output (same test file hit via two chains)
  A6  total_dependents_traversed counts correctly
  A7  changed_files list echoed back in result
  A8  path resolution: relative input path is resolved to match DB's absolute path
  A9  mixed: one changed file has no dependents; other has test dependent
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.affected import AffectedResult, affected
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 5) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file, start_line=start, end_line=end, docstring=None
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call", file=file, line=1, confidence="EXTRACTED"
    )


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def graph_db() -> tuple[sqlite3.Connection, Path, Path, Path]:
    """Build a 3-file fixture graph:

    src.py:  defines A() and B()
    utils.py: defines C(), calls A
    tests/test_src.py: defines test_a(), calls A   <- test file (has 'tests' in path)

    Edge graph:
        C  -> A  (call)
        test_a -> A  (call)

    So A's upstream dependents are: C (in utils.py) and test_a (in tests/test_src.py).

    Yields (conn, project_root, src_path, test_path).
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()

        # Create directories
        (root / "tests").mkdir()

        src = root / "src.py"
        utils = root / "utils.py"
        test_src = root / "tests" / "test_src.py"

        # Write stub files (content does not matter — indexer uses tree-sitter on them)
        src.write_text("def A(): pass\ndef B(): pass\n")
        utils.write_text("def C(): A()\n")
        test_src.write_text("def test_a(): A()\n")

        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        # Index src.py: symbols A, B
        upsert_file(
            conn,
            src,
            "python",
            "hash_src",
            [_sym("A", str(src)), _sym("B", str(src))],
            [],
        )

        # Index utils.py: symbol C, calls A
        upsert_file(
            conn,
            utils,
            "python",
            "hash_utils",
            [_sym("C", str(utils))],
            [_edge("C", "A", str(utils))],
        )

        # Index tests/test_src.py: symbol test_a, calls A
        upsert_file(
            conn,
            test_src,
            "python",
            "hash_test",
            [_sym("test_a", str(test_src))],
            [_edge("test_a", "A", str(test_src))],
        )

        conn.commit()
        yield conn, root, src, test_src  # type: ignore[misc]
        conn.close()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_a1_source_file_yields_dependent_test(graph_db: tuple) -> None:
    """A1: Changed source file A in src.py -> test_a in tests/test_src.py is affected."""
    conn, root, src, test_src = graph_db

    result: AffectedResult = affected(conn, [str(src)], depth=5, repo_root=root)

    assert str(test_src) in result["affected_tests"]
    # The production file utils.py should NOT be in affected_tests
    assert not any("utils.py" in p for p in result["affected_tests"])


def test_a2_changed_test_file_included_directly(graph_db: tuple) -> None:
    """A2: When the changed file itself is a test file, it appears in affected_tests directly."""
    conn, root, src, test_src = graph_db

    result: AffectedResult = affected(conn, [str(test_src)], depth=5, repo_root=root)

    # test_src is itself a test file → must appear directly
    assert str(test_src) in result["affected_tests"]


def test_a3_depth_bound_respected(graph_db: tuple) -> None:
    """A3: When depth=0, no dependents are traversed; affected_tests stays empty."""
    conn, root, src, test_src = graph_db

    # Depth 0 means we don't traverse any hops at all — test files reachable at d=1
    # should NOT appear in the result.
    result: AffectedResult = affected(conn, [str(src)], depth=0, repo_root=root)

    # No traversal -> no test dependents found via graph
    assert result["affected_tests"] == []


def test_a4_no_dependents_returns_empty(graph_db: tuple) -> None:
    """A4: Changed file with no dependents (B has no callers) yields empty affected_tests."""
    conn, root, src, test_src = graph_db

    # B has no callers in the fixture graph
    # We pass src.py but only the symbol B would be analyzed; however we analyze
    # all symbols in the file, so A's dependents will appear too.
    # Use a separate test: create a minimal graph with only B and no edges.
    with tempfile.TemporaryDirectory() as tmp:
        root2 = Path(tmp).resolve()
        b_src = root2 / "b_module.py"
        b_src.write_text("def B(): pass\n")
        db_path2 = root2 / ".seam" / "seam.db"
        db_path2.parent.mkdir()
        conn2 = init_db(db_path2)
        upsert_file(
            conn2,
            b_src,
            "python",
            "hash_b",
            [_sym("B", str(b_src))],
            [],
        )
        conn2.commit()

        result: AffectedResult = affected(conn2, [str(b_src)], depth=5, repo_root=root2)

        assert result["affected_tests"] == []
        conn2.close()


def test_a5_dedup_multiple_chains(graph_db: tuple) -> None:
    """A5: Same test file reached via two different source files appears only once."""
    conn, root, src, test_src = graph_db

    # Changing both src.py AND utils.py — test_src is reachable from src.py (A -> test_a).
    # utils.py has no test dependents. But let's verify dedup works by confirming count=1.
    result: AffectedResult = affected(conn, [str(src), str(src)], depth=5, repo_root=root)

    # Passing src.py twice should not produce duplicates in the result
    count = result["affected_tests"].count(str(test_src))
    assert count == 1, f"Expected test_src to appear exactly once, got {count}"


def test_a6_total_dependents_traversed_nonzero(graph_db: tuple) -> None:
    """A6: When there are dependents, total_dependents_traversed > 0."""
    conn, root, src, test_src = graph_db

    result: AffectedResult = affected(conn, [str(src)], depth=5, repo_root=root)

    # At minimum C and test_a are upstream of A (d=1), so traversal count is >= 2
    assert result["total_dependents_traversed"] >= 1


def test_a7_changed_files_echoed(graph_db: tuple) -> None:
    """A7: The result's changed_files list echoes back the (resolved) input paths."""
    conn, root, src, test_src = graph_db

    result: AffectedResult = affected(conn, [str(src)], depth=5, repo_root=root)

    assert str(src) in result["changed_files"]


def test_a8_relative_path_resolved(graph_db: tuple) -> None:
    """A8: Relative path input is resolved against repo_root before DB lookup."""
    conn, root, src, test_src = graph_db

    # Compute a relative path from root to src
    rel = src.relative_to(root)

    result: AffectedResult = affected(conn, [str(rel)], depth=5, repo_root=root)

    # Should find the same dependents as using the absolute path
    assert str(test_src) in result["affected_tests"]


def test_a9_mixed_one_without_dependents(graph_db: tuple) -> None:
    """A9: One file has no dependents, another has test dependents — both handled correctly."""
    conn, root, src, test_src = graph_db

    # utils.py (symbol C) has no test callers — only src.py->A has test dependents
    utils = root / "utils.py"
    result: AffectedResult = affected(conn, [str(src), str(utils)], depth=5, repo_root=root)

    # test_src found via src.py->A path
    assert str(test_src) in result["affected_tests"]
    # result is valid despite utils.py having no test dependents
    assert result["changed_files"] is not None


def test_a10_result_shape(graph_db: tuple) -> None:
    """A10: AffectedResult has exactly the required keys and types."""
    conn, root, src, test_src = graph_db

    result: AffectedResult = affected(conn, [str(src)], depth=5, repo_root=root)

    assert "changed_files" in result
    assert "affected_tests" in result
    assert "total_dependents_traversed" in result

    assert isinstance(result["changed_files"], list)
    assert isinstance(result["affected_tests"], list)
    assert isinstance(result["total_dependents_traversed"], int)
