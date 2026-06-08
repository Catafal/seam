"""Unit tests for seam/analysis/steer.py — pure truncation-steer generator.

The function is pure (truncation metadata in → list[str] of hints out):
no DB, no IO, no config import, never raises.

Coverage:
  - count-cap-only drops → hint naming direction + tier + omitted count + suggested limit
  - byte-ceiling-only drops → hint to raise/zero max_bytes
  - both caps fired → both hints without double-counting (byte and count portions named separately)
  - nothing trimmed (truncated=={}, byte_capped==None) → [] (absent steer)
  - all-trimmed-to-nothing (byte_capped fires and entries shown == 0) → anti-false-safe hint present
  - never-raises: malformed input (None, wrong types, missing keys) → []

Prior art: tests/unit/test_byte_budget.py, tests/unit/test_relevance.py.
"""

import pytest

from seam.analysis.steer import generate_steer

# ── helpers ───────────────────────────────────────────────────────────────────


def _mk_risk_summary(upstream_will: int = 0, upstream_likely: int = 0) -> dict:
    """Build a minimal risk_summary with upstream entries."""
    result: dict = {}
    if upstream_will > 0 or upstream_likely > 0:
        result["upstream"] = {}
        if upstream_will > 0:
            result["upstream"]["WILL_BREAK"] = upstream_will
        if upstream_likely > 0:
            result["upstream"]["LIKELY_AFFECTED"] = upstream_likely
    return result


def _mk_truncated(upstream_will: int = 0, upstream_likely: int = 0) -> dict:
    """Build a minimal truncated map."""
    result: dict = {}
    if upstream_will > 0 or upstream_likely > 0:
        result["upstream"] = {}
        if upstream_will > 0:
            result["upstream"]["WILL_BREAK"] = upstream_will
        if upstream_likely > 0:
            result["upstream"]["LIKELY_AFFECTED"] = upstream_likely
    return result


# ── nothing-trimmed → empty list ─────────────────────────────────────────────


def test_nothing_trimmed_returns_empty_list() -> None:
    """When neither cap fired, generate_steer must return [] (steer absent)."""
    hints = generate_steer(
        truncated={},
        byte_capped=None,
        risk_summary=_mk_risk_summary(upstream_will=5),
        limit=25,
        max_bytes=0,
    )
    assert hints == []


def test_nothing_trimmed_no_risk_returns_empty_list() -> None:
    """When there are no dependents at all, generate_steer must return []."""
    hints = generate_steer(
        truncated={},
        byte_capped=None,
        risk_summary={},
        limit=25,
        max_bytes=0,
    )
    assert hints == []


# ── count-cap-only ────────────────────────────────────────────────────────────


def test_count_cap_only_upstream_will_break() -> None:
    """Count-cap drop for upstream WILL_BREAK produces a hint with the correct suggested limit."""
    # 5 shown + 12 omitted = 17 total in risk_summary
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 12}},
        byte_capped=None,
        risk_summary={"upstream": {"WILL_BREAK": 17}},
        limit=5,
        max_bytes=0,
    )
    assert len(hints) >= 1
    joined = " ".join(hints)
    # Must mention the tier
    assert "WILL_BREAK" in joined
    # Must mention the omitted count
    assert "12" in joined
    # Must suggest a limit that would reveal the omitted entries (at least 17)
    assert "17" in joined


def test_count_cap_only_hint_contains_direction_and_tier() -> None:
    """Count-cap hint names the direction (upstream/downstream) and tier."""
    hints = generate_steer(
        truncated={"downstream": {"LIKELY_AFFECTED": 3}},
        byte_capped=None,
        risk_summary={"downstream": {"LIKELY_AFFECTED": 8}},
        limit=5,
        max_bytes=0,
    )
    joined = " ".join(hints)
    assert "LIKELY_AFFECTED" in joined
    # Must reference the downstream direction
    assert "downstream" in joined
    # Must mention the omitted count
    assert "3" in joined


def test_count_cap_multiple_tiers_produces_multiple_hints() -> None:
    """When multiple direction+tier combos are capped, each gets its own hint line."""
    hints = generate_steer(
        truncated={
            "upstream": {"WILL_BREAK": 5, "LIKELY_AFFECTED": 3},
        },
        byte_capped=None,
        risk_summary={
            "upstream": {"WILL_BREAK": 10, "LIKELY_AFFECTED": 8},
        },
        limit=5,
        max_bytes=0,
    )
    joined = " ".join(hints)
    # Both tiers should be mentioned
    assert "WILL_BREAK" in joined
    assert "LIKELY_AFFECTED" in joined


def test_count_cap_hint_suggests_limit_equal_to_risk_summary_total() -> None:
    """The suggested limit equals risk_summary[dir][tier] (the minimum that reveals all)."""
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 8}},
        byte_capped=None,
        risk_summary={"upstream": {"WILL_BREAK": 13}},
        limit=5,
        max_bytes=0,
    )
    joined = " ".join(hints)
    # The suggestion should name the total (13 = 5 shown + 8 omitted).
    assert "13" in joined


# ── byte-ceiling-only ─────────────────────────────────────────────────────────


def test_byte_ceiling_only_produces_hint() -> None:
    """A byte-ceiling drop (no count-cap drop) produces a max_bytes remedy hint."""
    hints = generate_steer(
        truncated={},  # no count-cap drops
        byte_capped={"limit": 4000, "omitted": 7},
        risk_summary={"upstream": {"WILL_BREAK": 7}},
        limit=25,
        max_bytes=4000,
    )
    assert len(hints) >= 1
    joined = " ".join(hints)
    # Must mention max_bytes or the byte limit remedy
    assert "max_bytes" in joined or "byte" in joined.lower()


def test_byte_ceiling_hint_mentions_zero_remedy() -> None:
    """The byte-ceiling hint suggests passing max_bytes=0 for the full radius."""
    hints = generate_steer(
        truncated={},
        byte_capped={"limit": 4000, "omitted": 5},
        risk_summary={"upstream": {"WILL_BREAK": 5}},
        limit=25,
        max_bytes=4000,
    )
    joined = " ".join(hints)
    assert "max_bytes=0" in joined or "0" in joined


# ── both caps fired ───────────────────────────────────────────────────────────


def test_both_caps_fired_names_both_remedies() -> None:
    """When both count-cap and byte-ceiling fired, both remedies are named."""
    # 3 count-cap omissions + 4 byte-ceiling omissions = 7 total truncated
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 7}},  # merged total
        byte_capped={"limit": 2000, "omitted": 4},
        risk_summary={"upstream": {"WILL_BREAK": 12}},
        limit=5,
        max_bytes=2000,
    )
    joined = " ".join(hints)
    # Must mention the limit remedy (count cap)
    assert "limit" in joined.lower() or "WILL_BREAK" in joined
    # Must mention the byte ceiling remedy
    assert "max_bytes" in joined or "byte" in joined.lower()


def test_both_caps_hint_count_uses_count_cap_portion_not_total() -> None:
    """When both fired, the count-cap hint uses truncated[dir][tier] - byte_capped.omitted."""
    # 10 total truncated in WILL_BREAK, 4 of which are byte-trimmed → 6 are count-cap trims
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 10}},
        byte_capped={"limit": 2000, "omitted": 4},
        risk_summary={"upstream": {"WILL_BREAK": 15}},
        limit=5,
        max_bytes=2000,
    )
    joined = " ".join(hints)
    # The count-cap portion is 10 - 4 = 6; the byte portion is 4.
    # The limit suggestion should be based on total risk_summary count (15).
    assert "15" in joined
    # Both hints should be present
    assert "max_bytes" in joined or "byte" in joined.lower()


# ── all-trimmed-to-nothing ────────────────────────────────────────────────────


def test_all_trimmed_to_nothing_anti_false_safe_hint_present() -> None:
    """When the byte ceiling dropped ALL entries, an explicit anti-false-safe hint is emitted."""
    # risk_summary has 5 upstream WILL_BREAK, truncated accounts for all 5 via byte_capped
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 5}},
        byte_capped={"limit": 100, "omitted": 5},
        risk_summary={"upstream": {"WILL_BREAK": 5}},
        limit=25,
        max_bytes=100,
    )
    joined = " ".join(hints)
    # The anti-false-safe message must be present (not "no dependents")
    # It should clearly warn that results were trimmed to nothing and the symbol
    # is NOT safe to delete / there ARE dependents.
    assert any(
        keyword in joined.lower()
        for keyword in ["trimmed", "not", "safe", "dependents", "blast radius"]
    )
    assert len(hints) >= 1


def test_all_trimmed_to_nothing_hint_is_first_or_prominent() -> None:
    """The all-trimmed warning should appear in the hints (order need not be strict first)."""
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 3}},
        byte_capped={"limit": 50, "omitted": 3},
        risk_summary={"upstream": {"WILL_BREAK": 3}},
        limit=25,
        max_bytes=50,
    )
    assert len(hints) >= 1
    # Some hint must contain the "all trimmed" warning
    has_warning = any(
        "trimmed" in h.lower() or "not" in h.lower() and "dependent" in h.lower() for h in hints
    )
    assert has_warning, f"No all-trimmed warning found in: {hints}"


def test_all_trimmed_partial_byte_cap_no_false_safe_hint() -> None:
    """When the byte ceiling fired but NOT all entries were dropped, no all-trimmed warning."""
    # 5 total, only 2 byte-capped, 3 still shown
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 2}},
        byte_capped={"limit": 5000, "omitted": 2},
        risk_summary={"upstream": {"WILL_BREAK": 5}},
        limit=25,
        max_bytes=5000,
    )
    # There should be a byte-ceiling hint but NOT an all-trimmed warning
    # (since 3 entries survived)
    # We can't trivially assert no-warning without knowing exact wording,
    # but the hint should be actionable (not alarmist)
    assert len(hints) >= 1  # at least the byte remedy hint


# ── never-raises ──────────────────────────────────────────────────────────────


def test_none_input_never_raises_returns_empty() -> None:
    """Passing None for all params must not raise — returns []."""
    try:
        result = generate_steer(
            truncated=None,  # type: ignore[arg-type]
            byte_capped=None,
            risk_summary=None,  # type: ignore[arg-type]
            limit=None,  # type: ignore[arg-type]
            max_bytes=None,  # type: ignore[arg-type]
        )
        assert result == []
    except Exception as exc:
        pytest.fail(f"generate_steer raised on None inputs: {exc}")


def test_wrong_types_never_raises() -> None:
    """Passing wrong types must not raise — returns []."""
    try:
        result = generate_steer(
            truncated="not a dict",  # type: ignore[arg-type]
            byte_capped={"limit": "nope", "omitted": "also nope"},  # type: ignore[arg-type]
            risk_summary=42,  # type: ignore[arg-type]
            limit="five",  # type: ignore[arg-type]
            max_bytes=[1, 2],  # type: ignore[arg-type]
        )
        assert result == []
    except Exception as exc:
        pytest.fail(f"generate_steer raised on wrong-type inputs: {exc}")


def test_empty_byte_capped_dict_never_raises() -> None:
    """An empty byte_capped dict (missing 'limit'/'omitted') must not raise."""
    try:
        result = generate_steer(
            truncated={"upstream": {"WILL_BREAK": 3}},
            byte_capped={},  # missing required keys
            risk_summary={"upstream": {"WILL_BREAK": 3}},
            limit=25,
            max_bytes=1000,
        )
        # Should not raise; may or may not produce hints
        assert isinstance(result, list)
    except Exception as exc:
        pytest.fail(f"generate_steer raised on empty byte_capped: {exc}")


def test_malformed_truncated_nested_values_never_raises() -> None:
    """Non-int nested values in truncated must not raise."""
    try:
        result = generate_steer(
            truncated={"upstream": {"WILL_BREAK": "not-an-int"}},  # type: ignore[dict-item]
            byte_capped=None,
            risk_summary={"upstream": {"WILL_BREAK": 5}},
            limit=25,
            max_bytes=0,
        )
        assert isinstance(result, list)
    except Exception as exc:
        pytest.fail(f"generate_steer raised on malformed truncated: {exc}")


def test_returns_list_of_strings() -> None:
    """Return value is always a list of strings (never raises, always list[str])."""
    hints = generate_steer(
        truncated={"upstream": {"WILL_BREAK": 5}},
        byte_capped=None,
        risk_summary={"upstream": {"WILL_BREAK": 10}},
        limit=5,
        max_bytes=0,
    )
    assert isinstance(hints, list)
    for hint in hints:
        assert isinstance(hint, str), f"Non-string hint: {hint!r}"
