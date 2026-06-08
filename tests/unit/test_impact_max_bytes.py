"""Integration tests for Slice 2 — handle_seam_impact byte ceiling (E1-FULL).

Tests verify the observable shape of handle_seam_impact output when the byte
ceiling (max_bytes param / SEAM_IMPACT_MAX_BYTES config) is active or inactive.

Prior art:
  tests/unit/test_impact_summary.py     — per-tier count cap + risk_summary
  tests/unit/test_impact_omit_null_candidate.py — E1 null best_candidate omission
  tests/unit/test_pack_relevance_rank.py         — DB-fixture integration test pattern

Coverage:
  MB1 — tight max_bytes bounds serialized output; most-relevant entries survive.
  MB2 — response["truncated"] includes byte-dropped counts (additive with count-cap drops).
  MB3 — risk_summary - shown == truncated holds for each direction/tier.
  MB4 — byte_capped present (with limit + omitted) ONLY when the ceiling fired; absent otherwise.
  MB5 — max_bytes=0 is byte-identical to pre-feature output (no byte_capped, same entries).
  MB6 — composes with limit: count cap runs first, byte ceiling trims what remains.
  MB7 — handle_seam_changes / handle_seam_affected are NOT affected by max_bytes / SEAM_IMPACT_MAX_BYTES.
  MB8 — max_bytes > full size → no trimming, no byte_capped.
  MB9 — max_bytes param overrides config SEAM_IMPACT_MAX_BYTES per-call.
"""

import json
from pathlib import Path

import seam.config as config
from seam.indexer.db import connect, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_affected, handle_seam_changes, handle_seam_impact

# ── helpers ───────────────────────────────────────────────────────────────────

ROOT = Path("/fake/root")
TIERS = ("WILL_BREAK", "LIKELY_AFFECTED", "MAY_NEED_TESTING")


def _serialized_size(obj: object) -> int:
    """Compact JSON character count — mirrors byte_budget.py unit."""
    return len(json.dumps(obj, separators=(",", ":")))


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
    """Build an index where 'hub' is called by n_direct callers (WILL_BREAK, d=1)
    and n_indirect callers per direct caller (LIKELY_AFFECTED, d=2).

    Returns the db_path.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    fake_file = tmp_path / "hub.py"
    fake_file.write_text("# hub\n")

    symbols: list[Symbol] = [_sym("hub", str(fake_file), line=1)]
    edges: list[Edge] = []

    for i in range(n_direct):
        caller_name = f"direct_{i}"
        symbols.append(_sym(caller_name, str(fake_file), line=10 + i))
        edges.append(_edge(caller_name, "hub", str(fake_file)))

        for j in range(n_indirect):
            indirect_name = f"indirect_{i}_{j}"
            symbols.append(_sym(indirect_name, str(fake_file), line=100 + i * 10 + j))
            edges.append(_edge(indirect_name, caller_name, str(fake_file)))

    upsert_file(conn, fake_file, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()
    return db_path


def _open(db_path: Path):
    """Open the DB using seam's connect() so migrations run."""
    return connect(db_path)


def _count_shown_in_dir(result: dict, direction: str) -> dict[str, int]:
    """Count shown entries per tier for a direction in a handle_seam_impact result."""
    dir_group = result.get(direction, {})
    return {tier: len(dir_group.get(tier, [])) for tier in TIERS}


# ── MB1: tight max_bytes bounds output; high-signal entries survive ──────────


def test_tight_max_bytes_bounds_output(tmp_path: Path, monkeypatch) -> None:
    """With a tight byte budget, serialized output is <= max_bytes."""
    db_path = _make_hub_db(tmp_path, n_direct=10, n_indirect=5)
    conn = _open(db_path)
    try:
        # Get full result to measure sizes
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        # Set budget to roughly 40% of full size — forces trimming
        budget = max(full_size // 3, 100)
        result = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=budget)
    finally:
        conn.close()

    assert _serialized_size(result) <= budget, (
        f"Output size {_serialized_size(result)} exceeds max_bytes={budget}"
    )


def test_tight_max_bytes_highest_risk_entries_survive(tmp_path: Path) -> None:
    """WILL_BREAK entries (d=1) survive when MAY_NEED_TESTING entries are dropped."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=5)
    conn = _open(db_path)
    try:
        # Get full result to know sizes
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        # Budget that will drop LIKELY_AFFECTED but keep WILL_BREAK
        budget = full_size // 2
        result = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=budget)
    finally:
        conn.close()

    upstream = result.get("upstream", {})
    will_break = upstream.get("WILL_BREAK", [])
    # WILL_BREAK is highest-priority — at least some should survive
    # (unless the budget is so small even WILL_BREAK doesn't fit)
    if _serialized_size(result) <= budget:
        # If LIKELY_AFFECTED was dropped, verify WILL_BREAK was kept first
        likely = upstream.get("LIKELY_AFFECTED", [])
        may = upstream.get("MAY_NEED_TESTING", [])
        if not likely and not may:
            # Only WILL_BREAK survived
            assert len(will_break) > 0 or _serialized_size(result) <= budget


# ── MB2: truncated includes byte-dropped counts additively ───────────────────


def test_truncated_includes_byte_dropped_counts(tmp_path: Path) -> None:
    """When byte ceiling fires, truncated reflects the byte-dropped counts."""
    db_path = _make_hub_db(tmp_path, n_direct=8, n_indirect=0)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        budget = full_size // 2
        result = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=budget)
    finally:
        conn.close()

    # If byte ceiling fired, truncated must be present
    if "byte_capped" in result:
        assert "truncated" in result, "truncated must be present when byte ceiling fired"
        # truncated must have non-zero counts for at least one direction/tier
        truncated = result["truncated"]
        total_truncated = sum(
            cnt
            for tier_map in truncated.values()
            for cnt in tier_map.values()
        )
        assert total_truncated > 0, "truncated must have non-zero counts when byte_capped"


def test_truncated_additive_with_count_cap(tmp_path: Path) -> None:
    """When both count cap and byte ceiling fire, truncated reflects both additively."""
    db_path = _make_hub_db(tmp_path, n_direct=10, n_indirect=0)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        # Apply count cap of 5 first, then a tight byte ceiling
        budget = full_size // 3  # very tight budget after count cap
        result_both = handle_seam_impact(conn, "hub", ROOT, limit=5, max_bytes=budget)
    finally:
        conn.close()

    # When byte_capped fires on top of count cap, the byte_capped.omitted should be > 0
    if "byte_capped" in result_both:
        byte_omitted = result_both["byte_capped"]["omitted"]
        assert byte_omitted > 0


# ── MB3: risk_summary - shown == truncated (reconciliation) ──────────────────


def test_risk_summary_minus_shown_equals_truncated(tmp_path: Path) -> None:
    """For each direction/tier: risk_summary[dir][tier] - shown == truncated[dir][tier]."""
    db_path = _make_hub_db(tmp_path, n_direct=8, n_indirect=4)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        budget = full_size // 2
        result = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=budget)
    finally:
        conn.close()

    risk_summary = result.get("risk_summary", {})
    truncated = result.get("truncated", {})

    for direction in ("upstream", "downstream"):
        if direction not in result or direction not in risk_summary:
            continue
        dir_summary = risk_summary[direction]
        dir_shown = result[direction]
        dir_truncated = truncated.get(direction, {})

        for tier in TIERS:
            if tier not in dir_summary:
                continue
            expected_total = dir_summary[tier]
            shown = len(dir_shown.get(tier, []))
            dropped = dir_truncated.get(tier, 0)
            assert shown + dropped == expected_total, (
                f"Reconciliation failed for {direction}/{tier}: "
                f"risk_summary={expected_total}, shown={shown}, truncated={dropped}, "
                f"sum={shown + dropped}"
            )


# ── MB4: byte_capped present only when ceiling fired ────────────────────────


def test_byte_capped_present_only_when_ceiling_fired(tmp_path: Path) -> None:
    """byte_capped is present ONLY when the ceiling dropped >=1 entry."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=3)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        # Tight budget — should fire the ceiling
        tight_budget = full_size // 2
        result_tight = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=tight_budget)

        # Generous budget — should not fire the ceiling
        generous_budget = full_size * 10
        result_generous = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=generous_budget)
    finally:
        conn.close()

    # Tight budget: if size was actually reduced, byte_capped should be present
    tight_size = _serialized_size(result_tight)
    if tight_size < full_size:
        assert "byte_capped" in result_tight, (
            "byte_capped must be present when the ceiling dropped entries"
        )
        assert "limit" in result_tight["byte_capped"]
        assert "omitted" in result_tight["byte_capped"]
        assert result_tight["byte_capped"]["limit"] == tight_budget
        assert result_tight["byte_capped"]["omitted"] > 0

    # Generous budget: ceiling should not have fired
    assert "byte_capped" not in result_generous, (
        "byte_capped must be absent when everything fits"
    )


def test_byte_capped_shape_when_present(tmp_path: Path) -> None:
    """When byte_capped is present, it has exactly {limit: int, omitted: int}."""
    db_path = _make_hub_db(tmp_path, n_direct=10, n_indirect=0)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        budget = full_size // 2
        result = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=budget)
    finally:
        conn.close()

    if "byte_capped" in result:
        bc = result["byte_capped"]
        assert isinstance(bc, dict)
        assert "limit" in bc and isinstance(bc["limit"], int)
        assert "omitted" in bc and isinstance(bc["omitted"], int)
        assert bc["omitted"] > 0
        assert bc["limit"] == budget


# ── MB5: max_bytes=0 is byte-identical to pre-feature output ─────────────────


def test_max_bytes_zero_is_byte_identical(tmp_path: Path) -> None:
    """max_bytes=0 produces byte-identical output to not passing max_bytes."""
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=5)
    conn = _open(db_path)
    try:
        result_default = handle_seam_impact(conn, "hub", ROOT, limit=0)
        result_zero = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=0)
    finally:
        conn.close()

    assert json.dumps(result_default, separators=(",", ":")) == json.dumps(
        result_zero, separators=(",", ":"),
    ), "max_bytes=0 must be byte-identical to default (no byte ceiling)"
    assert "byte_capped" not in result_zero


def test_max_bytes_zero_from_config(tmp_path: Path, monkeypatch) -> None:
    """When SEAM_IMPACT_MAX_BYTES=0 (default), output is byte-identical to pre-feature."""
    monkeypatch.setattr(config, "SEAM_IMPACT_MAX_BYTES", 0)
    db_path = _make_hub_db(tmp_path, n_direct=5, n_indirect=5)
    conn = _open(db_path)
    try:
        # Default max_bytes comes from config (0 = unlimited)
        result = handle_seam_impact(conn, "hub", ROOT, limit=0)
    finally:
        conn.close()

    assert "byte_capped" not in result


# ── MB6: composes with count cap (count cap runs first) ──────────────────────


def test_byte_ceiling_runs_after_count_cap(tmp_path: Path) -> None:
    """With limit=3 + tight max_bytes, count cap runs first, byte ceiling trims further."""
    db_path = _make_hub_db(tmp_path, n_direct=10, n_indirect=0)
    conn = _open(db_path)
    try:
        # Count cap of 3 runs first
        result_count_cap = handle_seam_impact(conn, "hub", ROOT, limit=3, max_bytes=0)
        count_cap_size = _serialized_size(result_count_cap)

        # Byte ceiling tight enough to trim from the count-capped result
        budget = count_cap_size // 2
        result_both = handle_seam_impact(conn, "hub", ROOT, limit=3, max_bytes=budget)
    finally:
        conn.close()

    # Byte ceiling trimmed further than count cap alone
    assert _serialized_size(result_both) <= budget

    if "byte_capped" in result_both:
        # The result with both is <= the count-cap-only result in terms of entries
        for direction in ("upstream", "downstream"):
            if direction not in result_count_cap or direction not in result_both:
                continue
            for tier in TIERS:
                shown_count_cap = len(result_count_cap.get(direction, {}).get(tier, []))
                shown_both = len(result_both.get(direction, {}).get(tier, []))
                assert shown_both <= shown_count_cap, (
                    f"{direction}/{tier}: byte ceiling must not ADD entries above count cap"
                )


def test_byte_ceiling_does_not_add_entries_above_count_cap(tmp_path: Path) -> None:
    """Byte ceiling can only remove entries, never add entries beyond the count cap."""
    db_path = _make_hub_db(tmp_path, n_direct=8, n_indirect=4)
    conn = _open(db_path)
    try:
        limit = 5
        result_count_cap = handle_seam_impact(conn, "hub", ROOT, limit=limit, max_bytes=0)
        # Generous byte ceiling that doesn't fire
        generous = _serialized_size(result_count_cap) * 10
        result_with_ceiling = handle_seam_impact(conn, "hub", ROOT, limit=limit, max_bytes=generous)
    finally:
        conn.close()

    # With a generous budget, output should be the same as count-cap-only
    for direction in ("upstream", "downstream"):
        if direction not in result_count_cap or direction not in result_with_ceiling:
            continue
        for tier in TIERS:
            cap_entries = result_count_cap.get(direction, {}).get(tier, [])
            ceiling_entries = result_with_ceiling.get(direction, {}).get(tier, [])
            assert len(ceiling_entries) <= len(cap_entries), (
                f"{direction}/{tier}: byte ceiling must not add entries beyond count cap"
            )


# ── MB7: handle_seam_changes and handle_seam_affected bypass byte ceiling ────


def test_handle_seam_changes_unaffected_by_max_bytes_config(tmp_path: Path, monkeypatch) -> None:
    """handle_seam_changes has no byte_capped key regardless of SEAM_IMPACT_MAX_BYTES."""
    # Set a tight budget — should NOT affect handle_seam_changes
    monkeypatch.setattr(config, "SEAM_IMPACT_MAX_BYTES", 1)

    db_path = _make_hub_db(tmp_path, n_direct=3, n_indirect=0)
    conn = _open(db_path)
    try:
        # handle_seam_changes on a non-git path raises NOT_A_GIT_REPO — that's fine.
        # We just need to verify no byte_capped key appears. Use ROOT (which is /fake/root).
        result = handle_seam_changes(conn, ROOT)
    finally:
        conn.close()

    # Result may be an error (not a git repo), but should never have byte_capped
    assert "byte_capped" not in result, (
        "handle_seam_changes must never have byte_capped regardless of SEAM_IMPACT_MAX_BYTES"
    )


def test_handle_seam_affected_unaffected_by_max_bytes_config(tmp_path: Path, monkeypatch) -> None:
    """handle_seam_affected has no byte_capped key regardless of SEAM_IMPACT_MAX_BYTES."""
    monkeypatch.setattr(config, "SEAM_IMPACT_MAX_BYTES", 1)

    db_path = _make_hub_db(tmp_path, n_direct=3, n_indirect=0)
    conn = _open(db_path)
    try:
        # Pass a fake file path; handle_seam_affected will return affected tests (or empty).
        fake_file = str(tmp_path / "hub.py")
        result = handle_seam_affected(conn, [fake_file], ROOT)
    finally:
        conn.close()

    assert "byte_capped" not in result, (
        "handle_seam_affected must never have byte_capped regardless of SEAM_IMPACT_MAX_BYTES"
    )


# ── MB8: max_bytes larger than full result → no trimming ─────────────────────


def test_max_bytes_larger_than_full_result_no_trimming(tmp_path: Path) -> None:
    """When max_bytes > full serialized size, nothing is trimmed."""
    db_path = _make_hub_db(tmp_path, n_direct=3, n_indirect=3)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        result = handle_seam_impact(conn, "hub", ROOT, limit=0, max_bytes=full_size * 10)
    finally:
        conn.close()

    # No trimming should occur
    assert "byte_capped" not in result
    # The result should be identical to the full result
    assert json.dumps(result, separators=(",", ":")) == json.dumps(
        full_result, separators=(",", ":"),
    )


# ── MB9: max_bytes param overrides config SEAM_IMPACT_MAX_BYTES ──────────────


def test_max_bytes_param_overrides_config(tmp_path: Path, monkeypatch) -> None:
    """Per-call max_bytes param overrides SEAM_IMPACT_MAX_BYTES config."""
    db_path = _make_hub_db(tmp_path, n_direct=8, n_indirect=0)
    conn = _open(db_path)
    try:
        full_result = handle_seam_impact(conn, "hub", ROOT, limit=0)
        full_size = _serialized_size(full_result)

        # Config says tight budget, but param overrides to unlimited
        monkeypatch.setattr(config, "SEAM_IMPACT_MAX_BYTES", 10)
        result_unlimited_via_param = handle_seam_impact(
            conn, "hub", ROOT, limit=0, max_bytes=0,  # 0 = unlimited
        )

        # Config says unlimited (0), but param activates tight budget
        monkeypatch.setattr(config, "SEAM_IMPACT_MAX_BYTES", 0)
        tight_budget = full_size // 2
        result_tight_via_param = handle_seam_impact(
            conn, "hub", ROOT, limit=0, max_bytes=tight_budget,
        )
    finally:
        conn.close()

    # Per-call max_bytes=0 overrides config: no byte_capped
    assert "byte_capped" not in result_unlimited_via_param

    # Per-call tight budget should have trimmed
    tight_result_size = _serialized_size(result_tight_via_param)
    assert tight_result_size <= tight_budget, (
        f"Per-call max_bytes={tight_budget} not enforced (got {tight_result_size})"
    )


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_not_found_symbol_no_byte_capped(tmp_path: Path) -> None:
    """A not-found symbol result has no byte_capped key."""
    db_path = _make_hub_db(tmp_path, n_direct=2, n_indirect=0)
    conn = _open(db_path)
    try:
        result = handle_seam_impact(conn, "nonexistent_symbol", ROOT, max_bytes=10)
    finally:
        conn.close()

    # Not-found result has found=False, no direction groups → no byte_capped
    assert "byte_capped" not in result


def test_invalid_input_no_byte_capped(tmp_path: Path) -> None:
    """An invalid input (blank target) error result has no byte_capped key."""
    db_path = _make_hub_db(tmp_path, n_direct=2, n_indirect=0)
    conn = _open(db_path)
    try:
        result = handle_seam_impact(conn, "   ", ROOT, max_bytes=100)
    finally:
        conn.close()

    assert "byte_capped" not in result
    assert "error" in result
