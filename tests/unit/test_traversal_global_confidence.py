"""Traversal tests for whole-index confidence resolution (Phase 1b — issue #9).

These are the TDD-first regression tests. They will FAIL against the current
same-file-only resolution and pass after the fix.

The HEADLINE REGRESSION (TW1) is the primary motivator:
  A cross-file edge whose target name is unique in the whole index must now
  report EXTRACTED, not INFERRED.  The current code tags it INFERRED because
  the same-file symbol set for the edge's file doesn't contain the target.

Coverage:
  TW1  Cross-file edge, unique target name → EXTRACTED  (THE regression)
  TW2  Target name shared across two files → AMBIGUOUS
  TW3  Target name not indexed at all → INFERRED (unchanged behavior)
  TW4  Multi-hop cross-file path — weakest-hop rule still holds
  TW5  Single-file edge, unique target → still EXTRACTED (no regression)
"""

import tempfile
from pathlib import Path

from seam.analysis.traversal import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
    walk,
)
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ──────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_EXTRACTED) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


def _build_db(tmp_path: Path):
    """Create and return an initialized test DB + a helper to get file paths."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    return conn


# ── TW1: THE HEADLINE REGRESSION — cross-file unique target → EXTRACTED ───────


def test_cross_file_unique_target_is_extracted() -> None:
    """REGRESSION: a cross-file edge to a uniquely-named target must report EXTRACTED.

    Before this fix, same-file resolution tagged it INFERRED because 'target_fn'
    was not in the edge's own file's symbol set. After the fix, the whole-index
    name-count map shows count=1 → EXTRACTED.

    Setup:
      file_a.py  defines: caller_fn
                 edge:    caller_fn → target_fn  (stored as INFERRED, cross-file)
      file_b.py  defines: target_fn              (unique in the index)

    walk(upstream of target_fn) must return caller_fn with EXTRACTED confidence.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_a.write_text("# a\n")
            file_b.write_text("# b\n")

            # Seed file_a: caller_fn is defined here; edge goes to target_fn (cross-file).
            # The stored confidence is INFERRED (same-file resolver saw target_fn not in a.py).
            upsert_file(conn, file_a, "python", "ha", [_sym("caller_fn", str(file_a))],
                        [_edge("caller_fn", "target_fn", str(file_a), confidence=CONFIDENCE_INFERRED)])

            # Seed file_b: target_fn is uniquely defined here.
            upsert_file(conn, file_b, "python", "hb", [_sym("target_fn", str(file_b))], [])

            # Walk upstream of target_fn — should find caller_fn.
            results = walk(conn, ["target_fn"], "upstream", max_depth=1)
            by_name = {r["name"]: r for r in results}

            assert "caller_fn" in by_name, "caller_fn must be reachable upstream of target_fn"
            assert by_name["caller_fn"]["confidence"] == CONFIDENCE_EXTRACTED, (
                f"cross-file edge to unique target must be EXTRACTED, "
                f"got {by_name['caller_fn']['confidence']!r}. "
                f"This is the regression that Phase 1b fixes."
            )
        finally:
            conn.close()


# ── TW2: target name shared across two files → AMBIGUOUS ──────────────────────


def test_cross_file_ambiguous_target_is_ambiguous() -> None:
    """A target name defined in two different files → AMBIGUOUS.

    Setup:
      file_a.py  defines: caller_fn
                 edge:    caller_fn → shared_fn  (stored as INFERRED, cross-file)
      file_b.py  defines: shared_fn (count=1 in b)
      file_c.py  defines: shared_fn (count=1 in c)  ← makes total count=2

    walk(upstream of shared_fn from file_b) must return caller_fn with AMBIGUOUS.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_c = tmp_path / "c.py"
            for f in (file_a, file_b, file_c):
                f.write_text("# stub\n")

            upsert_file(conn, file_a, "python", "ha", [_sym("caller_fn", str(file_a))],
                        [_edge("caller_fn", "shared_fn", str(file_a), confidence=CONFIDENCE_INFERRED)])
            upsert_file(conn, file_b, "python", "hb", [_sym("shared_fn", str(file_b))], [])
            upsert_file(conn, file_c, "python", "hc", [_sym("shared_fn", str(file_c))], [])

            results = walk(conn, ["shared_fn"], "upstream", max_depth=1)
            by_name = {r["name"]: r for r in results}

            assert "caller_fn" in by_name
            assert by_name["caller_fn"]["confidence"] == CONFIDENCE_AMBIGUOUS, (
                f"edge to a name defined in two files must be AMBIGUOUS, "
                f"got {by_name['caller_fn']['confidence']!r}"
            )
        finally:
            conn.close()


# ── TW3: unindexed target → INFERRED (unchanged behavior) ─────────────────────


def test_unindexed_target_stays_inferred() -> None:
    """An edge to a name that is not in the symbols table stays INFERRED.

    This covers external deps (stdlib, third-party) and dynamic calls.
    Whole-index resolution: count=0 → INFERRED.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_a.write_text("# stub\n")

            # caller_fn calls external_lib_fn which is NOT in the symbols table at all.
            upsert_file(conn, file_a, "python", "ha", [_sym("caller_fn", str(file_a))],
                        [_edge("caller_fn", "external_lib_fn", str(file_a), confidence=CONFIDENCE_INFERRED)])

            # Walk downstream of caller_fn — external_lib_fn should be INFERRED.
            results = walk(conn, ["caller_fn"], "downstream", max_depth=1)
            by_name = {r["name"]: r for r in results}

            assert "external_lib_fn" in by_name
            assert by_name["external_lib_fn"]["confidence"] == CONFIDENCE_INFERRED, (
                f"unindexed target must stay INFERRED, got {by_name['external_lib_fn']['confidence']!r}"
            )
        finally:
            conn.close()


# ── TW4: multi-hop cross-file path — weakest-hop still holds ──────────────────


def test_multi_hop_cross_file_weakest_hop_aggregation() -> None:
    """Multi-hop path where first hop is EXTRACTED and second hop to an unindexed name.

    Setup:
      file_a: A → B  (B is unique → EXTRACTED hop)
      file_b: B → C  (C is not indexed → INFERRED hop)

    walk(downstream of A, depth=2):
      B at d=1: confidence=EXTRACTED (B is unique)
      C at d=2: confidence=INFERRED (weakest of EXTRACTED + INFERRED = INFERRED)
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_b = tmp_path / "b.py"
            file_a.write_text("# a\n")
            file_b.write_text("# b\n")

            # A → B (B is uniquely in file_b)
            upsert_file(conn, file_a, "python", "ha", [_sym("A", str(file_a))],
                        [_edge("A", "B", str(file_a), confidence=CONFIDENCE_INFERRED)])
            # B → C (C is not indexed anywhere)
            upsert_file(conn, file_b, "python", "hb", [_sym("B", str(file_b))],
                        [_edge("B", "C", str(file_b), confidence=CONFIDENCE_INFERRED)])

            results = walk(conn, ["A"], "downstream", max_depth=2)
            by_name = {r["name"]: r for r in results}

            # B: unique in index → EXTRACTED at d=1
            assert "B" in by_name
            assert by_name["B"]["confidence"] == CONFIDENCE_EXTRACTED, (
                f"B is unique → expected EXTRACTED at d=1, got {by_name['B']['confidence']!r}"
            )

            # C: not indexed → INFERRED at d=2; path B→C weakest is min(EXTRACTED, INFERRED)=INFERRED
            assert "C" in by_name
            assert by_name["C"]["confidence"] == CONFIDENCE_INFERRED, (
                f"C is unindexed → path confidence INFERRED at d=2, got {by_name['C']['confidence']!r}"
            )
        finally:
            conn.close()


# ── TW5: same-file unique edge still EXTRACTED (no regression) ────────────────


def test_same_file_unique_target_still_extracted() -> None:
    """Existing same-file EXTRACTED behavior must not regress after whole-index wiring.

    file_a: caller_fn → helper_fn (both in file_a; helper_fn is unique in index)
    walk upstream of helper_fn must return caller_fn with EXTRACTED.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conn = _build_db(tmp_path)
        try:
            file_a = tmp_path / "a.py"
            file_a.write_text("# a\n")

            # Both symbols in same file; stored as EXTRACTED by same-file resolver.
            upsert_file(
                conn, file_a, "python", "ha",
                [_sym("caller_fn", str(file_a)), _sym("helper_fn", str(file_a))],
                [_edge("caller_fn", "helper_fn", str(file_a), confidence=CONFIDENCE_EXTRACTED)],
            )

            results = walk(conn, ["helper_fn"], "upstream", max_depth=1)
            by_name = {r["name"]: r for r in results}

            assert "caller_fn" in by_name
            assert by_name["caller_fn"]["confidence"] == CONFIDENCE_EXTRACTED, (
                f"same-file unique target must still be EXTRACTED, "
                f"got {by_name['caller_fn']['confidence']!r}"
            )
        finally:
            conn.close()
