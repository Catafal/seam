"""Integration tests for handle_seam_impact (seam/server/tools.py).

Tests call the handler directly against a seeded SQLite DB, mirroring the
style of test_mcp_tools.py. Fixtures build known graphs via the db write path.

Coverage:
  T1  blank target -> INVALID_INPUT
  T2  whitespace-only target -> INVALID_INPUT
  T3  invalid direction -> INVALID_INPUT
  T4  depth clamping (0 -> 1, 100 -> 10)
  T5  happy path upstream: correct tiers returned
  T6  happy path downstream: correct tiers returned
  T7  happy path both: upstream + downstream keys
  T8  unknown symbol -> found=False, empty result (not error)
  T9  result is a plain dict (JSON-able, no TypedDict wrapper issues)
  T10 file paths are relativized to root; file=None passes through as None
  T11 found=True for known symbol (with dependents)
  T12 file field present on tier entries
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.analysis.impact import TIER_WILL_BREAK
from seam.analysis.traversal import CONFIDENCE_EXTRACTED, CONFIDENCE_INFERRED
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_impact

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str, confidence: str = CONFIDENCE_EXTRACTED) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence=confidence)


@pytest.fixture()
def seeded_impact_db() -> tuple[sqlite3.Connection, Path]:
    """Create a DB seeded with a simple call graph:
        C -> B -> A  (A calls nothing; C depends on everything)
    Returns (conn, project_root).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        src = tmp_path / "src.py"
        src.write_text("# stub\n")

        conn = init_db(db_path)
        upsert_file(
            conn,
            src,
            "python",
            "hash1",
            [_sym("A", str(src)), _sym("B", str(src)), _sym("C", str(src))],
            [
                _edge("B", "A", str(src), CONFIDENCE_EXTRACTED),
                _edge("C", "B", str(src), CONFIDENCE_INFERRED),
            ],
        )

        yield conn, tmp_path  # type: ignore[misc]
        conn.close()


# ── T1: blank target -> INVALID_INPUT ─────────────────────────────────────────


def test_blank_target_returns_invalid_input(
    seeded_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_impact must return INVALID_INPUT for a blank target."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T2: whitespace-only target -> INVALID_INPUT ───────────────────────────────


def test_whitespace_target_returns_invalid_input(
    seeded_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Whitespace-only target must also return INVALID_INPUT."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "   ", root)

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T3: invalid direction -> INVALID_INPUT ────────────────────────────────────


def test_invalid_direction_returns_invalid_input(
    seeded_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """An unrecognized direction must return INVALID_INPUT."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, direction="sideways")

    assert isinstance(result, dict)
    assert result.get("error") == "INVALID_INPUT"


# ── T4: depth clamping ────────────────────────────────────────────────────────


def test_depth_clamping_low(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """max_depth=0 must be silently clamped to 1 (not raise)."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, max_depth=0)
    # Should not raise; returns a valid impact dict
    assert isinstance(result, dict)
    assert "error" not in result


def test_depth_clamping_high(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """max_depth=999 must be silently clamped to 10 (not raise)."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, max_depth=999)
    assert isinstance(result, dict)
    assert "error" not in result


# ── T5: happy path upstream ───────────────────────────────────────────────────


def test_upstream_happy_path(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """Upstream impact of A should return B (d=1) and C (d=2) with correct tiers."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=3)

    assert isinstance(result, dict)
    assert "upstream" in result
    assert "downstream" not in result

    tg = result["upstream"]
    assert TIER_WILL_BREAK in tg

    will_break_names = [e["name"] for e in tg[TIER_WILL_BREAK]]
    assert "B" in will_break_names

    likely_names = [e["name"] for e in tg.get("LIKELY_AFFECTED", [])]
    assert "C" in likely_names


# ── T6: happy path downstream ────────────────────────────────────────────────


def test_downstream_happy_path(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """Downstream impact of C should return B (d=1) and A (d=2)."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "C", root, direction="downstream", max_depth=3)

    assert isinstance(result, dict)
    assert "downstream" in result
    assert "upstream" not in result

    tg = result["downstream"]
    will_break_names = [e["name"] for e in tg.get(TIER_WILL_BREAK, [])]
    assert "B" in will_break_names


# ── T7: direction=both ────────────────────────────────────────────────────────


def test_both_direction_returns_two_keys(
    seeded_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """direction='both' must return both 'upstream' and 'downstream' keys."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "B", root, direction="both", max_depth=3)

    assert "upstream" in result
    assert "downstream" in result


# ── T8: unknown symbol -> found=False, empty result ──────────────────────────


def test_unknown_symbol_returns_empty_not_error(
    seeded_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """An unknown symbol must return found=False and empty tiers, not an error."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "no_such_symbol_xyz", root)

    assert isinstance(result, dict)
    assert "error" not in result
    # found=False signals "not in index" (different from "found but no dependents")
    assert result["found"] is False
    assert result["target"] == "no_such_symbol_xyz"
    assert "upstream" in result
    total = sum(len(v) for v in result["upstream"].values())
    assert total == 0


# ── T9: result is JSON-serializable ──────────────────────────────────────────


def test_result_is_json_serializable(
    seeded_impact_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_impact must return a plain dict with JSON-serializable values."""
    import json

    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=3)

    # Should not raise (None is serializable as JSON null)
    json_str = json.dumps(result)
    assert isinstance(json_str, str)


# ── T10: file paths are relativized to root ───────────────────────────────────


def test_file_paths_are_relativized(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """handle_seam_impact must relativize each entry's file path to root.

    For indexed symbols the file must be a relative path; for non-indexed names, None.
    """
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=3)

    assert "error" not in result
    tg = result["upstream"]
    all_entries = [e for tier_list in tg.values() for e in tier_list]

    # At least one entry for B (which is an indexed symbol in src.py).
    b_entry = next((e for e in all_entries if e["name"] == "B"), None)
    assert b_entry is not None

    # The file should be relative (not absolute — does not start with /).
    assert b_entry["file"] is not None
    assert not b_entry["file"].startswith("/"), (
        f"Expected relative path, got absolute: {b_entry['file']!r}"
    )
    # And it should be relative to root (i.e., resolves back to the src file).
    resolved = (root / b_entry["file"]).resolve()
    assert resolved.exists()


# ── T11: found=True for known symbol with dependents ─────────────────────────


def test_found_true_for_known_symbol(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """found=True must be returned when the target is an indexed symbol."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=1)

    assert result["found"] is True
    assert result["target"] == "A"


# ── T12: file field present on tier entries ───────────────────────────────────


def test_tier_entries_have_file_field(seeded_impact_db: tuple[sqlite3.Connection, Path]) -> None:
    """Every TieredEntry in the handler response must include a 'file' key."""
    conn, root = seeded_impact_db
    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=3)

    tg = result["upstream"]
    for tier_list in tg.values():
        for entry in tier_list:
            assert "file" in entry, f"Entry missing 'file' key: {entry}"
