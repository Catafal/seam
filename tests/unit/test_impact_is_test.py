"""Unit tests for is_test tagging and include_tests filter on impact().

Tests verify EXTERNAL behavior through the public impact() interface.
All tests build real graphs via the db write path; no mocking internals.

Coverage:
  IT1  is_test=True for entries whose file path lives under 'tests/' dir
  IT2  is_test=False for entries whose file path is a production file
  IT3  is_test=False for entries with file=None (unresolved/external symbol)
  IT4  include_tests=False removes ONLY test-file entries from WILL_BREAK
  IT5  include_tests=False removes test entries from ALL three tiers
  IT6  include_tests=True (default) keeps all entries including test ones
  IT7  is_test field is always present in every TieredEntry (even prod files)
  IT8  include_tests=False reports hidden_tests count (filtered test dependents)
  IT9  include_tests=True omits hidden_tests (no filtering happened)
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.impact import (
    TIER_LIKELY_AFFECTED,
    TIER_MAY_NEED_TESTING,
    TIER_WILL_BREAK,
    impact,
)
from seam.analysis.traversal import CONFIDENCE_EXTRACTED
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_EXTRACTED) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


@pytest.fixture()
def mixed_db() -> tuple[sqlite3.Connection, str, str]:
    """DB with both a production file and a tests/ file, linked to target 'A'.

    Graph:
        prod_caller (prod.py)   -> A    (d=1, prod file)
        test_caller (tests/t.py) -> A   (d=1, test file)

    Returns (conn, prod_path, test_path).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        prod_file = tmp_path / "prod.py"
        prod_file.write_text("# prod\n")

        # Create a tests/ directory so the path has the 'tests' segment.
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_thing.py"
        test_file.write_text("# test\n")

        conn = init_db(db_path)

        # Index target A in prod file.
        upsert_file(
            conn,
            prod_file,
            "python",
            "hash_prod",
            [_sym("A", str(prod_file)), _sym("prod_caller", str(prod_file))],
            [_edge("prod_caller", "A", str(prod_file))],
        )

        # Index test_caller in the tests/ file; edge points to A.
        upsert_file(
            conn,
            test_file,
            "python",
            "hash_test",
            [_sym("test_caller", str(test_file))],
            [_edge("test_caller", "A", str(test_file))],
        )

        yield conn, str(prod_file), str(test_file)  # type: ignore[misc]
        conn.close()


def _all_entries(result: dict, direction: str = "upstream") -> list[dict]:
    """Flatten all tier entries from a given direction."""
    return [entry for entries in result[direction].values() for entry in entries]


# ── IT1: is_test=True for test-file entries ───────────────────────────────────


def test_is_test_true_for_test_file_entry(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """Entries from a file under tests/ must have is_test=True."""
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=1)
    entries = _all_entries(result)

    test_entry = next((e for e in entries if e["name"] == "test_caller"), None)
    assert test_entry is not None, "test_caller must appear in upstream entries"
    assert test_entry["is_test"] is True, (
        f"test_caller lives in tests/ — expected is_test=True, got {test_entry['is_test']!r}"
    )


# ── IT2: is_test=False for production-file entries ────────────────────────────


def test_is_test_false_for_prod_file_entry(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """Entries from a production file must have is_test=False."""
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=1)
    entries = _all_entries(result)

    prod_entry = next((e for e in entries if e["name"] == "prod_caller"), None)
    assert prod_entry is not None, "prod_caller must appear in upstream entries"
    assert prod_entry["is_test"] is False, (
        f"prod_caller lives in prod.py — expected is_test=False, got {prod_entry['is_test']!r}"
    )


# ── IT3: is_test=False for entries with file=None ────────────────────────────


def test_is_test_false_for_unresolved_entry() -> None:
    """Entries with no indexed file (file=None) must have is_test=False.

    Unresolved/external symbol names should never masquerade as production code
    or test code — is_test=False is the safe default for uncertainty.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        prod_file = tmp_path / "prod.py"
        prod_file.write_text("# prod\n")

        conn = init_db(db_path)

        # A is indexed; external_fn is not a symbol, only appears as an edge source.
        upsert_file(
            conn,
            prod_file,
            "python",
            "hash_x",
            [_sym("A", str(prod_file))],
            [],
        )
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " SELECT 'external_fn', 'A', 'call', id, 1, 'INFERRED' FROM files WHERE path = ? LIMIT 1",
            (str(prod_file),),
        )
        conn.commit()

        result = impact(conn, "A", direction="upstream", max_depth=1)
        entries = _all_entries(result)

        ext_entry = next((e for e in entries if e["name"] == "external_fn"), None)
        assert ext_entry is not None, "external_fn must appear in upstream entries"
        assert ext_entry["file"] is None, "external_fn must have file=None"
        assert ext_entry["is_test"] is False, (
            f"Unresolved symbol must have is_test=False, got {ext_entry['is_test']!r}"
        )

        conn.close()


# ── IT4: include_tests=False removes test entries from WILL_BREAK ─────────────


def test_include_tests_false_removes_test_entries_will_break(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """include_tests=False must remove test-file entries from WILL_BREAK tier."""
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=1, include_tests=False)
    will_break = result["upstream"][TIER_WILL_BREAK]

    names = [e["name"] for e in will_break]
    assert "test_caller" not in names, (
        f"test_caller must be filtered out with include_tests=False; got: {names}"
    )
    # prod_caller must still be present.
    assert "prod_caller" in names, (
        f"prod_caller must remain with include_tests=False; got: {names}"
    )


# ── IT5: include_tests=False removes test entries from all tiers ──────────────


def test_include_tests_false_removes_from_all_tiers() -> None:
    """include_tests=False must remove test entries from ALL three tiers.

    Build a deeper graph so test entries appear in LIKELY_AFFECTED and
    MAY_NEED_TESTING as well, then verify all are filtered.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        prod_file = tmp_path / "prod.py"
        prod_file.write_text("# prod\n")

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_all.py"
        test_file.write_text("# test\n")

        conn = init_db(db_path)

        # Chain: test_d1 -> A (d=1, test); test_d2 -> test_d1 (d=2, test);
        #        test_d3 -> test_d2 (d=3, test); prod_d1 -> A (d=1, prod)
        upsert_file(
            conn,
            prod_file,
            "python",
            "h_prod",
            [_sym("A", str(prod_file)), _sym("prod_d1", str(prod_file))],
            [_edge("prod_d1", "A", str(prod_file))],
        )
        upsert_file(
            conn,
            test_file,
            "python",
            "h_test",
            [
                _sym("test_d1", str(test_file)),
                _sym("test_d2", str(test_file)),
                _sym("test_d3", str(test_file)),
            ],
            [
                _edge("test_d1", "A", str(test_file)),       # d=1 from A
                _edge("test_d2", "test_d1", str(test_file)), # d=2 from A
                _edge("test_d3", "test_d2", str(test_file)), # d=3 from A
            ],
        )

        result = impact(conn, "A", direction="upstream", max_depth=3, include_tests=False)

        will_break = [e["name"] for e in result["upstream"][TIER_WILL_BREAK]]
        likely = [e["name"] for e in result["upstream"][TIER_LIKELY_AFFECTED]]
        may_test = [e["name"] for e in result["upstream"][TIER_MAY_NEED_TESTING]]

        # All test entries must be gone.
        for test_name in ("test_d1", "test_d2", "test_d3"):
            assert test_name not in will_break, f"{test_name} must be filtered from WILL_BREAK"
            assert test_name not in likely, f"{test_name} must be filtered from LIKELY_AFFECTED"
            assert test_name not in may_test, f"{test_name} must be filtered from MAY_NEED_TESTING"

        # Production entry must remain.
        assert "prod_d1" in will_break, "prod_d1 must remain in WILL_BREAK"

        conn.close()


# ── IT6: include_tests=True (default) keeps test entries ──────────────────────


def test_include_tests_true_keeps_all_entries(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """Default include_tests=True must keep both prod and test entries."""
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=1)
    entries = _all_entries(result)
    names = [e["name"] for e in entries]

    assert "prod_caller" in names, "prod_caller must be present with default include_tests"
    assert "test_caller" in names, "test_caller must be present with default include_tests"


# ── IT7: is_test always present in every TieredEntry ─────────────────────────


def test_is_test_field_always_present(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """Every TieredEntry must carry the 'is_test' key (bool)."""
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=3)
    entries = _all_entries(result)

    assert len(entries) > 0, "Fixture must produce at least one entry"
    for entry in entries:
        assert "is_test" in entry, f"Entry missing 'is_test' key: {entry}"
        assert isinstance(entry["is_test"], bool), (
            f"is_test must be bool, got {type(entry['is_test'])!r}: {entry}"
        )


# ── IT8: include_tests=False reports hidden_tests count ───────────────────────


def test_include_tests_false_reports_hidden_count(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """include_tests=False must report how many test dependents were filtered.

    This is the anti-false-safe signal: an agent must be able to tell
    "no dependents" from "all dependents were tests and got hidden".
    The fixture has exactly one test caller (test_caller) of A.
    """
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=1, include_tests=False)

    assert result["hidden_tests"] == 1, (
        f"one test dependent should be reported hidden, got {result.get('hidden_tests')!r}"
    )
    # And the test entry is genuinely gone from the tiers.
    names = [e["name"] for e in _all_entries(result)]
    assert "test_caller" not in names


# ── IT9: include_tests=True omits hidden_tests ────────────────────────────────


def test_include_tests_true_omits_hidden_count(
    mixed_db: tuple[sqlite3.Connection, str, str],
) -> None:
    """Default include_tests=True must NOT add hidden_tests (no filtering happened)."""
    conn, _prod, _test = mixed_db

    result = impact(conn, "A", direction="upstream", max_depth=1)

    assert "hidden_tests" not in result, (
        "hidden_tests must be absent when tests are included (no filtering)"
    )
