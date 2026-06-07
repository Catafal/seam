"""Integration tests for E2/E3 relevance shaping in handle_seam_impact.

Reproduces the 2026-06-07 neutral re-benchmark failure shape: a CLASS whose own
members cross-reference each other flood the WILL_BREAK tier above external
dependents, so under the per-tier cap the externals fall off. These tests assert
the shaping fixes that — externals survive the cap — while keeping risk_summary
honest and seam_changes/seam_affected byte-stable.

Tests call the handler / analysis layer directly against a seeded SQLite DB,
mirroring test_impact_handler.py.

Coverage:
  R1  externals survive the cap when self-refs flood a tier (default "rank")
  R2  risk_summary still counts the FULL pre-cap blast radius (incl. self-refs)
  R3  RELEVANCE_SORT="off" reverts to prior ordering → externals get capped out
  R4  SELF_REF="hide" drops self-refs + surfaces hidden_self_refs; externals kept
  R5  method target: external callers rank above sibling methods
  R6  free function target (no container): no crash, production-before-test only
  R7  byte-stability: analysis-layer impact() ignores the new knobs entirely
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

import seam.config as config
from seam.analysis import impact as impact_module
from seam.analysis.impact import TIER_WILL_BREAK
from seam.analysis.traversal import CONFIDENCE_EXTRACTED
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_impact

# Number of self-referencing members — chosen to exceed the test cap so the
# "externals buried below self-refs" condition is real.
_N_SELF = 9
_EXTERNALS = ["Zexternal1.handler", "Zexternal2.handler"]
_CAP = 5


def _sym(name: str, file: str, kind: str = "function") -> Symbol:
    return Symbol(
        name=name, kind=kind, file=file, start_line=1, end_line=2, docstring=None,
        signature=None, decorators=[], is_exported=None, visibility=None, qualified_name=None,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call", file=file, line=1,
        confidence=CONFIDENCE_EXTRACTED,
    )


@pytest.fixture()
def benchmark_shaped_db() -> tuple[sqlite3.Connection, Path]:
    """Seed the clicky-CompanionManager shape.

    Class "AAAWidget" (name alphabetically BEFORE the externals, so without
    relevance ordering its members sort first and push externals past the cap)
    with members m1..m10. Members m2..m10 all call m1 → 9 SELF-REF callers of m1.
    Two EXTERNAL methods (Zexternal*) also call m1. All 11 callers land in
    WILL_BREAK (d=1) of an upstream impact on the class.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        widget = tmp_path / "widget.py"
        widget.write_text("# stub\n")
        ext = tmp_path / "ext.py"
        ext.write_text("# stub\n")

        members = [f"AAAWidget.m{i}" for i in range(1, 11)]
        widget_syms = [_sym("AAAWidget", str(widget), kind="class")]
        widget_syms += [_sym(m, str(widget), kind="method") for m in members]

        # m2..m10 call m1 (bare target so it matches the expand_impact_seeds bare member).
        self_edges = [_edge(f"AAAWidget.m{i}", "m1", str(widget)) for i in range(2, 11)]
        assert len(self_edges) == _N_SELF

        ext_syms = [_sym(e, str(ext), kind="method") for e in _EXTERNALS]
        ext_edges = [_edge(e, "m1", str(ext)) for e in _EXTERNALS]

        conn = init_db(db_path)
        upsert_file(conn, widget, "python", "h1", widget_syms, self_edges)
        upsert_file(conn, ext, "python", "h2", ext_syms, ext_edges)

        yield conn, tmp_path  # type: ignore[misc]
        conn.close()


def _will_break_names(result: dict, direction: str = "upstream") -> list[str]:
    return [e["name"] for e in result[direction][TIER_WILL_BREAK]]


# ── R1: externals survive the cap (default "rank") ────────────────────────────


def test_externals_survive_cap_under_default_ranking(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "on")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "rank")
    conn, root = benchmark_shaped_db

    result = handle_seam_impact(conn, "AAAWidget", root, limit=_CAP)
    names = _will_break_names(result)

    # Both external dependents must be present despite 9 self-refs flooding the tier.
    for ext in _EXTERNALS:
        assert ext in names, f"{ext} should survive the cap under relevance ranking"
    assert len(names) == _CAP  # cap honoured


# ── R2: risk_summary counts the full pre-cap blast radius ─────────────────────


def test_risk_summary_counts_full_blast_radius(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "on")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "rank")
    conn, root = benchmark_shaped_db

    result = handle_seam_impact(conn, "AAAWidget", root, limit=_CAP)
    # 9 self-refs + 2 externals all call m1 at d=1.
    assert result["risk_summary"]["upstream"][TIER_WILL_BREAK] == _N_SELF + len(_EXTERNALS)
    # The cap dropped entries → truncated reported.
    assert result["truncated"]["upstream"][TIER_WILL_BREAK] == (_N_SELF + len(_EXTERNALS)) - _CAP


# ── R3: RELEVANCE_SORT="off" reverts → externals get capped out ───────────────


def test_relevance_off_reverts_and_buries_externals(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "off")
    conn, root = benchmark_shaped_db

    result = handle_seam_impact(conn, "AAAWidget", root, limit=_CAP)
    names = _will_break_names(result)

    # With relevance off, the alphabetically-first self-refs (AAAWidget.*) fill the
    # cap and the externals are pushed out — this is the pre-E2/E3 failure mode.
    assert all(n.startswith("AAAWidget.") for n in names)
    for ext in _EXTERNALS:
        assert ext not in names


# ── R4: SELF_REF="hide" drops self-refs + surfaces hidden_self_refs ───────────


def test_hide_mode_drops_self_refs_and_counts_them(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "on")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "hide")
    conn, root = benchmark_shaped_db

    result = handle_seam_impact(conn, "AAAWidget", root, limit=_CAP)
    names = _will_break_names(result)

    # No self-refs in the output; only externals remain.
    assert sorted(names) == sorted(_EXTERNALS)
    assert result["hidden_self_refs"] == _N_SELF
    # risk_summary still reports the honest full total.
    assert result["risk_summary"]["upstream"][TIER_WILL_BREAK] == _N_SELF + len(_EXTERNALS)


# ── R5: method target — external callers rank above sibling methods ───────────


def test_method_target_ranks_externals_above_siblings(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "on")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "rank")
    conn, root = benchmark_shaped_db

    # Querying the single method "AAAWidget.m1" — siblings (AAAWidget.m2..) are self-refs.
    result = handle_seam_impact(conn, "AAAWidget.m1", root, limit=_CAP)
    names = _will_break_names(result)
    for ext in _EXTERNALS:
        assert ext in names


# ── R6: free function target (no container) — no crash, graceful ──────────────


def test_free_function_target_no_container(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "on")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "rank")
    conn, root = benchmark_shaped_db

    # "m1" is a bare name with no container → no self-refs, must not raise.
    result = handle_seam_impact(conn, "m1", root, limit=_CAP)
    assert result["found"] is True
    assert "upstream" in result


# ── R7: byte-stability — analysis-layer impact() ignores the new knobs ────────


def test_analysis_impact_byte_stable_across_knobs(
    benchmark_shaped_db: tuple[sqlite3.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seam_changes / seam_affected call impact() directly (below the handler).

    The new knobs live only in the handler, so the analysis-layer result must be
    byte-identical regardless of how they are set — proving downstream consumers
    are unaffected.
    """
    conn, root = benchmark_shaped_db

    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "on")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "hide")
    res_on = impact_module.impact(conn, target="AAAWidget", direction="upstream", repo_root=root)

    monkeypatch.setattr(config, "SEAM_IMPACT_RELEVANCE_SORT", "off")
    monkeypatch.setattr(config, "SEAM_IMPACT_SELF_REF", "show")
    res_off = impact_module.impact(conn, target="AAAWidget", direction="upstream", repo_root=root)

    assert res_on == res_off
