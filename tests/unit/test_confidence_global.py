"""Unit tests for seam/analysis/confidence.py — whole-index confidence resolution.

These are the TDD-first tests. They will FAIL until confidence.py is implemented.

Coverage:
  R1  resolve() — unique name in map → EXTRACTED
  R2  resolve() — name duplicated (count > 1) → AMBIGUOUS
  R3  resolve() — name absent from map (count == 0) → INFERRED
  R4  resolve() — empty map → INFERRED (every name absent)
  R5  load_name_counts() — returns correct counts from a real DB connection
  R6  load_name_counts() — empty symbols table returns empty dict
  R7  Constants are exactly the right strings (regression guard)

Fixture style follows test_traversal.py and test_confidence.py:
  hand-built rows via init_db + upsert_file, assertions on public interfaces only.
"""

import tempfile
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Symbol

# ── Helpers ──────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function") -> Symbol:
    """Minimal Symbol for seeding tests."""
    return Symbol(name=name, kind=kind, file=file, start_line=1, end_line=2, docstring=None)


# ── R7: constants are the right strings ──────────────────────────────────────


def test_constant_values() -> None:
    """Confidence constants must be the exact canonical strings."""
    from seam.analysis.confidence import (
        CONFIDENCE_AMBIGUOUS,
        CONFIDENCE_EXTRACTED,
        CONFIDENCE_INFERRED,
    )

    assert CONFIDENCE_EXTRACTED == "EXTRACTED"
    assert CONFIDENCE_INFERRED == "INFERRED"
    assert CONFIDENCE_AMBIGUOUS == "AMBIGUOUS"


# ── R1: unique name → EXTRACTED ───────────────────────────────────────────────


def test_resolve_unique_name_is_extracted() -> None:
    """A name that appears exactly once in the map resolves to EXTRACTED."""
    from seam.analysis.confidence import CONFIDENCE_EXTRACTED, resolve

    name_counts = {"foo": 1, "bar": 2, "baz": 0}
    assert resolve("foo", name_counts) == CONFIDENCE_EXTRACTED


# ── R2: duplicated name → AMBIGUOUS ──────────────────────────────────────────


def test_resolve_duplicated_name_is_ambiguous() -> None:
    """A name that appears more than once in the map resolves to AMBIGUOUS."""
    from seam.analysis.confidence import CONFIDENCE_AMBIGUOUS, resolve

    name_counts = {"shared": 3}
    assert resolve("shared", name_counts) == CONFIDENCE_AMBIGUOUS


def test_resolve_count_two_is_ambiguous() -> None:
    """Count of exactly 2 (minimum ambiguous) also resolves to AMBIGUOUS."""
    from seam.analysis.confidence import CONFIDENCE_AMBIGUOUS, resolve

    name_counts = {"dup": 2}
    assert resolve("dup", name_counts) == CONFIDENCE_AMBIGUOUS


# ── R3: absent name → INFERRED ───────────────────────────────────────────────


def test_resolve_absent_name_is_inferred() -> None:
    """A name not present in the map at all resolves to INFERRED."""
    from seam.analysis.confidence import CONFIDENCE_INFERRED, resolve

    name_counts = {"other": 1}
    assert resolve("missing", name_counts) == CONFIDENCE_INFERRED


# ── R4: empty map → INFERRED ─────────────────────────────────────────────────


def test_resolve_empty_map_is_inferred() -> None:
    """An empty name_counts map means every name is absent → INFERRED."""
    from seam.analysis.confidence import CONFIDENCE_INFERRED, resolve

    assert resolve("anything", {}) == CONFIDENCE_INFERRED


# ── R5: load_name_counts returns correct counts ───────────────────────────────


def test_load_name_counts_returns_correct_counts() -> None:
    """load_name_counts() returns a dict mapping each symbol name to its occurrence count.

    We seed two files: file A defines 'unique_fn' (count=1) and 'shared_fn' (count=1).
    File B defines 'shared_fn' (count=1). The returned map must show shared_fn=2, unique_fn=1.
    """
    from seam.analysis.confidence import load_name_counts

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "test.db"

        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text("# a\n")
        file_b.write_text("# b\n")

        conn = init_db(db_path)
        try:
            upsert_file(conn, file_a, "python", "ha", [_sym("unique_fn", str(file_a)), _sym("shared_fn", str(file_a))], [])
            upsert_file(conn, file_b, "python", "hb", [_sym("shared_fn", str(file_b))], [])

            counts = load_name_counts(conn)

            assert counts["unique_fn"] == 1, f"unique_fn should have count 1, got {counts.get('unique_fn')}"
            assert counts["shared_fn"] == 2, f"shared_fn should have count 2, got {counts.get('shared_fn')}"
            # No phantom keys
            assert "nonexistent" not in counts
        finally:
            conn.close()


# ── R6: empty symbols table → empty dict ─────────────────────────────────────


def test_load_name_counts_empty_db_returns_empty_dict() -> None:
    """load_name_counts() on an empty symbols table returns an empty dict, not an error."""
    from seam.analysis.confidence import load_name_counts

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "empty.db"

        conn = init_db(db_path)
        try:
            counts = load_name_counts(conn)
            assert counts == {}
        finally:
            conn.close()
