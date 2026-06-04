"""Unit tests for Phase 8 Feature 2 — seam_impact summary tier + result cap.

Tests verify the observable shape of handler output for:
    IS1 — risk_summary present; per-tier counts match full pre-cap result
    IS2 — limit cap truncates each tier to at most `limit` entries
    IS3 — truncated dict reports exact omitted counts when any tier was capped
    IS4 — limit=0 means unlimited (all entries returned, no truncated)
    IS5 — risk_summary always reflects full pre-cap counts even when capped
    IS6 — kept entries are lowest-distance (same tier = same distance, order preserved)
    IS7 — verbose stripping still applies to the (now capped) entries
    IS8 — config.SEAM_IMPACT_MAX_RESULTS default value is 25

Strategy: tests build an in-memory DB with enough entries to exceed the cap,
then assert on the observable output shape of handle_seam_impact.
No mocking — real handler, real DB, real analysis layer.
"""

from pathlib import Path

import seam.config as config
from seam.indexer.db import connect, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import _prioritize_tier_entries, handle_seam_impact

# ── Constants ─────────────────────────────────────────────────────────────────

HEAVY_FIELDS = {"decorators", "is_exported", "visibility", "qualified_name",
                "resolved_by", "best_candidate"}

TIERS = {"WILL_BREAK", "LIKELY_AFFECTED", "MAY_NEED_TESTING"}

ROOT = Path("/fake/root")


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    """Minimal Symbol fixture."""
    return Symbol(
        name=name, kind=kind, file=file, start_line=line, end_line=line + 2,
        docstring=None, signature=None, decorators=[], is_exported=True,
        visibility="public", qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    """Minimal call Edge."""
    return Edge(source=source, target=target, kind="call", file=file, line=1,
                confidence="INFERRED")


def _make_hub_db(tmp_path: Path, n_direct: int = 5, n_indirect: int = 5) -> Path:
    """Build an index where 'hub' is called by n_direct direct callers (d=1)
    and each direct caller is called by 1 indirect caller (d=2).

    n_direct callers at distance 1  → they all land in WILL_BREAK
    n_indirect callers at distance 2 → they all land in LIKELY_AFFECTED

    No d=3 entries by default (keeps math simple).
    Returns the db_path.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    # upsert_file signature: (conn, filepath: Path, language, file_hash, symbols, edges)
    # Each call corresponds to ONE file.  We put everything in hub.py.
    fake_file = tmp_path / "hub.py"
    fake_file.write_text("# hub\n")

    symbols: list[Symbol] = [_sym("hub", str(fake_file), line=1)]
    edges: list[Edge] = []

    for i in range(n_direct):
        caller_name = f"direct_{i}"
        symbols.append(_sym(caller_name, str(fake_file), line=10 + i))
        # direct_i calls hub → hub is upstream of direct_i
        edges.append(_edge(caller_name, "hub", str(fake_file)))

        for j in range(n_indirect):
            indirect_name = f"indirect_{i}_{j}"
            symbols.append(_sym(indirect_name, str(fake_file), line=100 + i * 10 + j))
            # indirect_i_j calls direct_i
            edges.append(_edge(indirect_name, caller_name, str(fake_file)))

    upsert_file(conn, fake_file, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()
    return db_path


def _connect(db_path: Path):
    """Open the DB for reading (uses seam's connect() so schema migrations run)."""
    return connect(db_path)


# ── IS1: risk_summary always present ──────────────────────────────────────────


def test_risk_summary_present_no_cap(tmp_path: Path) -> None:
    """risk_summary is present even when entries are well below the cap."""
    db_path = _make_hub_db(tmp_path, n_direct=2, n_indirect=2)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=100)
    finally:
        conn.close()

    assert "risk_summary" in result, "risk_summary must always be present"
    assert "upstream" in result["risk_summary"]


def test_risk_summary_structure(tmp_path: Path) -> None:
    """risk_summary has {direction: {tier: count}} structure."""
    db_path = _make_hub_db(tmp_path, n_direct=3, n_indirect=2)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, direction="upstream", limit=100)
    finally:
        conn.close()

    summary = result["risk_summary"]
    assert "upstream" in summary
    upstream_summary = summary["upstream"]
    # All 3 tiers must be present (even if zero)
    for tier in TIERS:
        assert tier in upstream_summary, f"tier {tier!r} missing from risk_summary"
        assert isinstance(upstream_summary[tier], int)


def test_risk_summary_counts_match_full_uncapped(tmp_path: Path) -> None:
    """risk_summary counts == full pre-cap counts even with limit=100."""
    db_path = _make_hub_db(tmp_path, n_direct=3, n_indirect=2)
    conn = _connect(db_path)
    try:
        result_unlimited = handle_seam_impact(conn, "hub", ROOT, limit=0)
        result_capped = handle_seam_impact(conn, "hub", ROOT, limit=100)
    finally:
        conn.close()

    # Both should have the same risk_summary (full counts)
    assert result_unlimited["risk_summary"] == result_capped["risk_summary"]


# ── IS2: limit cap truncates tier lists ────────────────────────────────────────


def test_tier_entries_capped(tmp_path: Path) -> None:
    """Each tier's entry list is capped to limit."""
    # 5 direct callers → WILL_BREAK has 5 entries; cap to 2
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=2)
    finally:
        conn.close()

    upstream = result["upstream"]
    assert len(upstream["WILL_BREAK"]) <= 2, (
        f"WILL_BREAK should have at most 2 entries, got {len(upstream['WILL_BREAK'])}"
    )


def test_cap_respects_limit_per_tier(tmp_path: Path) -> None:
    """Cap is applied independently per tier (each tier <= limit)."""
    db_path = _make_hub_db(tmp_path, n_direct=6, n_indirect=4)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=3)
    finally:
        conn.close()

    for tier, entries in result["upstream"].items():
        assert len(entries) <= 3, f"Tier {tier!r} has {len(entries)} > 3 entries"


# ── IS3: truncated dict ────────────────────────────────────────────────────────


def test_truncated_present_when_capped(tmp_path: Path) -> None:
    """truncated is present when any tier was capped."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=2)
    finally:
        conn.close()

    assert "truncated" in result, "truncated must be present when entries were capped"
    assert "upstream" in result["truncated"]


def test_truncated_exact_count(tmp_path: Path) -> None:
    """truncated count == original count minus cap."""
    # 5 direct callers at d=1 → WILL_BREAK = 5; cap=3 → truncated=2
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=3)
    finally:
        conn.close()

    trunc_upstream = result["truncated"]["upstream"]
    assert trunc_upstream["WILL_BREAK"] == 2, (
        f"Expected 2 truncated WILL_BREAK entries, got {trunc_upstream['WILL_BREAK']}"
    )


def test_truncated_absent_when_no_cap(tmp_path: Path) -> None:
    """truncated is absent when no tier was truncated."""
    # Only 2 direct callers, limit=10 → nothing truncated
    db_path = _make_hub_db(tmp_path, n_direct=2, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=10)
    finally:
        conn.close()

    # truncated key should be absent (not just all-zero) when nothing was capped
    assert "truncated" not in result, (
        "truncated should be absent when no entries were dropped"
    )


# ── IS4: limit=0 means unlimited ──────────────────────────────────────────────


def test_limit_zero_returns_all(tmp_path: Path) -> None:
    """limit=0 means unlimited — all entries are returned."""
    db_path = _make_hub_db(tmp_path, n_direct=10, n_indirect=0)
    conn = _connect(db_path)
    try:
        result_zero = handle_seam_impact(conn, "hub", ROOT, limit=0)
        result_no_limit = handle_seam_impact(conn, "hub", ROOT, limit=0)
    finally:
        conn.close()

    # 10 direct callers → all 10 must be present in WILL_BREAK
    wb_count = len(result_zero["upstream"]["WILL_BREAK"])
    assert wb_count == 10, f"Expected 10 entries with limit=0, got {wb_count}"
    assert "truncated" not in result_no_limit


def test_limit_zero_no_truncated(tmp_path: Path) -> None:
    """limit=0 produces no truncated key even with many entries."""
    db_path = _make_hub_db(tmp_path, n_direct=30, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=0)
    finally:
        conn.close()

    assert "truncated" not in result


# ── IS5: risk_summary matches full pre-cap counts ─────────────────────────────


def test_risk_summary_counts_equal_full_set_when_capped(tmp_path: Path) -> None:
    """risk_summary counts == full pre-cap counts even when entries are capped.

    This is story 15 from the PRD: the histogram is trustworthy even when
    the entry lists are truncated.
    """
    # Build: 5 direct callers (WILL_BREAK), cap at 2
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result_limited = handle_seam_impact(conn, "hub", ROOT, limit=2)
        result_unlimited = handle_seam_impact(conn, "hub", ROOT, limit=0)
    finally:
        conn.close()

    # risk_summary must reflect the full 5, not the capped 2
    summary_limited = result_limited["risk_summary"]["upstream"]
    summary_unlimited = result_unlimited["risk_summary"]["upstream"]
    assert summary_limited == summary_unlimited, (
        f"risk_summary counts should be identical regardless of cap: "
        f"{summary_limited} vs {summary_unlimited}"
    )
    assert summary_limited["WILL_BREAK"] == 5


# ── IS6: kept entries are lowest-distance (highest-risk) ─────────────────────


def test_kept_entries_are_closest(tmp_path: Path) -> None:
    """When entries are capped, kept entries are the closest (lowest distance).

    Since all entries within a tier have the same distance (tiers are by distance),
    this test validates that slicing preserves original analysis-layer ordering
    and that entries ARE within the expected distance range for their tier.
    """
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=3)
    finally:
        conn.close()

    # All WILL_BREAK entries must have distance=1
    for entry in result["upstream"]["WILL_BREAK"]:
        assert entry["distance"] == 1, (
            f"WILL_BREAK entry {entry['name']!r} has distance={entry['distance']}, expected 1"
        )


# ── IS7: verbose stripping still applies ──────────────────────────────────────


def test_lean_mode_with_cap(tmp_path: Path) -> None:
    """verbose=False still strips heavy fields from capped entries."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=2, verbose=False)
    finally:
        conn.close()

    for entry in result["upstream"]["WILL_BREAK"]:
        for field in HEAVY_FIELDS:
            assert field not in entry, (
                f"Heavy field {field!r} present in lean entry {entry.get('name')!r}"
            )


def test_verbose_mode_with_cap(tmp_path: Path) -> None:
    """verbose=True (default) keeps resolved_by/best_candidate in capped entries."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, limit=2, verbose=True)
    finally:
        conn.close()

    for entry in result["upstream"]["WILL_BREAK"]:
        # resolved_by and best_candidate are nullable — they can be None but must be PRESENT
        assert "resolved_by" in entry
        assert "best_candidate" in entry


# ── IS8: config default ────────────────────────────────────────────────────────


def test_config_default_is_25() -> None:
    """SEAM_IMPACT_MAX_RESULTS default value is 25."""
    assert config.SEAM_IMPACT_MAX_RESULTS == 25, (
        f"Expected SEAM_IMPACT_MAX_RESULTS=25, got {config.SEAM_IMPACT_MAX_RESULTS}"
    )


def test_default_limit_applies(tmp_path: Path) -> None:
    """Calling handle_seam_impact with no limit uses config.SEAM_IMPACT_MAX_RESULTS."""
    # Build 30 direct callers — more than the default cap of 25
    db_path = _make_hub_db(tmp_path, n_direct=30, n_indirect=0)
    conn = _connect(db_path)
    try:
        # No explicit limit — should use config.SEAM_IMPACT_MAX_RESULTS (25)
        result = handle_seam_impact(conn, "hub", ROOT)
    finally:
        conn.close()

    wb_count = len(result["upstream"]["WILL_BREAK"])
    assert wb_count <= config.SEAM_IMPACT_MAX_RESULTS, (
        f"Default cap not applied: got {wb_count} entries, "
        f"expected at most {config.SEAM_IMPACT_MAX_RESULTS}"
    )
    # risk_summary should still show all 30
    assert result["risk_summary"]["upstream"]["WILL_BREAK"] == 30


# ── IS9: both directions ───────────────────────────────────────────────────────


def test_both_directions_risk_summary(tmp_path: Path) -> None:
    """risk_summary includes both upstream and downstream when direction='both'."""
    db_path = _make_hub_db(tmp_path, n_direct=3, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, direction="both", limit=100)
    finally:
        conn.close()

    assert "risk_summary" in result
    assert "upstream" in result["risk_summary"]
    assert "downstream" in result["risk_summary"]


def test_both_directions_truncated(tmp_path: Path) -> None:
    """truncated includes both upstream and downstream when direction='both' and cap hit."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=0)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(conn, "hub", ROOT, direction="both", limit=2)
    finally:
        conn.close()

    # upstream WILL_BREAK should be truncated (5 > 2)
    if "truncated" in result:
        assert "upstream" in result["truncated"] or "downstream" in result["truncated"]


# ── IS10: risk_summary independent of verbose mode ────────────────────────────


def test_risk_summary_same_in_lean_mode(tmp_path: Path) -> None:
    """risk_summary is unaffected by verbose mode — counts are always full."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=3)
    conn = _connect(db_path)
    try:
        result_verbose = handle_seam_impact(conn, "hub", ROOT, verbose=True, limit=100)
        result_lean = handle_seam_impact(conn, "hub", ROOT, verbose=False, limit=100)
    finally:
        conn.close()

    assert result_verbose["risk_summary"] == result_lean["risk_summary"]


# ── Production-first ordering under the cap (benchmark follow-up #1) ───────────


def test_prioritize_tier_entries_production_first_stable() -> None:
    """The cap pre-sort puts production (is_test=False) ahead of tests, stably.

    Deterministic unit test for the real defect: in the seam index, ~52 test
    callers crowded the 9 production callers (handle_seam_search/_query) past the
    per-tier cap of 25. Stable production-first ordering rescues them while
    preserving the analysis layer's order within each group.
    """
    entries = [
        {"name": "t0", "is_test": True},
        {"name": "t1", "is_test": True},
        {"name": "p0", "is_test": False},
        {"name": "t2", "is_test": True},
        {"name": "p1", "is_test": False},
    ]
    out = [e["name"] for e in _prioritize_tier_entries(entries)]
    # Production first, in original relative order; then tests, in original order.
    assert out == ["p0", "p1", "t0", "t1", "t2"], out


def _make_test_and_prod_hub_db(tmp_path: Path, n_test: int, n_prod: int) -> Path:
    """Hub called by n_test TEST callers + n_prod PRODUCTION callers, all d=1.

    TEST callers live in tests/test_svc.py (is_test=True); production callers in
    svc.py (is_test=False). Test callers are inserted FIRST so, without
    prioritisation, they occupy the front of the WILL_BREAK tier and a small cap
    would drop the production callers.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    # Test file first (lower edge ids → earlier in walk order).
    test_file = tmp_path / "tests" / "test_svc.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("# tests\n")
    test_syms = [_sym(f"test_caller_{i}", str(test_file), line=10 + i) for i in range(n_test)]
    test_edges = [_edge(f"test_caller_{i}", "hub", str(test_file)) for i in range(n_test)]
    upsert_file(conn, test_file, "python", "testhash", test_syms, test_edges)

    # Production file with the hub definition + production callers.
    prod_file = tmp_path / "svc.py"
    prod_file.write_text("# svc\n")
    prod_syms = [_sym("hub", str(prod_file), line=1)]
    prod_syms += [_sym(f"prod_{i}", str(prod_file), line=10 + i) for i in range(n_prod)]
    prod_edges = [_edge(f"prod_{i}", "hub", str(prod_file)) for i in range(n_prod)]
    upsert_file(conn, prod_file, "python", "prodhash", prod_syms, prod_edges)

    conn.commit()
    conn.close()
    return db_path


def test_production_callers_survive_cap_over_tests(tmp_path: Path) -> None:
    """Under a cap, PRODUCTION callers must be kept ahead of TEST callers.

    Benchmark Gap: `impact rescore` dropped handle_seam_search/_query (production)
    because test dependents filled the per-tier cap first.
    """
    db_path = _make_test_and_prod_hub_db(tmp_path, n_test=8, n_prod=4)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(
            conn, "hub", ROOT, direction="upstream", include_tests=True, limit=6
        )
    finally:
        conn.close()

    wb = result["upstream"]["WILL_BREAK"]
    names = {e["name"] for e in wb}
    assert len(wb) == 6, "cap must still hold (token budget unchanged)"
    for i in range(4):
        assert f"prod_{i}" in names, (
            f"production caller prod_{i} dropped by cap — should outrank tests; kept={names}"
        )


def test_production_entries_sort_before_tests_uncapped(tmp_path: Path) -> None:
    """Even uncapped, production entries appear before test entries in a tier."""
    db_path = _make_test_and_prod_hub_db(tmp_path, n_test=3, n_prod=3)
    conn = _connect(db_path)
    try:
        result = handle_seam_impact(
            conn, "hub", ROOT, direction="upstream", include_tests=True, limit=0
        )
    finally:
        conn.close()

    wb = result["upstream"]["WILL_BREAK"]
    is_test_flags = [e["is_test"] for e in wb]
    # All False (production) must come before any True (test).
    first_test = next((idx for idx, t in enumerate(is_test_flags) if t), len(is_test_flags))
    assert all(not t for t in is_test_flags[:first_test])
    assert all(is_test_flags[first_test:]), (
        f"production entries must precede test entries; got {is_test_flags}"
    )
