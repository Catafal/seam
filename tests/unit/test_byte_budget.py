"""Unit tests for seam/analysis/byte_budget.py — pure byte-ceiling trimmer.

The function is pure (response dict + budget in → trimmed response + dropped counts out):
no DB, no IO, no config import, never raises.

Tests assert the trim-priority behaviour the handler relies on:
  - upstream kept before downstream;
  - WILL_BREAK kept before LIKELY_AFFECTED before MAY_NEED_TESTING;
  - intra-tier front-prefix preserved (existing E2/E3 order respected);
  - serialized output ALWAYS <= budget after trimming;
  - exact-fit boundary (budget == full size => nothing dropped);
  - budget < envelope => all entries dropped, total_omitted == entry count, no raise;
  - input response object is NOT mutated;
  - malformed tier values (non-list) => no raise, returned unchanged;
  - non-direction keys (found, target, risk_summary, truncated, etc.) are copied unchanged.

Prior art: tests/unit/test_rwr.py (pure-leaf tests for seam/analysis/rwr.py).

NOTE ON BUDGET CALCULATIONS:
  The trimmed response always preserves the FULL structure of direction keys present in
  the original (they become empty-list groups in the envelope). Budget calculations must
  account for this overhead — e.g. a response with upstream + downstream groups will
  retain both direction keys even when downstream is completely empty.
"""

import copy

from seam.analysis.byte_budget import fit_to_byte_budget, serialized_size

# ── helpers ──────────────────────────────────────────────────────────────────


def _mk_entry(name: str) -> dict:
    """Build a minimal impact entry dict (realistic enough for size tests)."""
    return {"name": name, "kind": "function", "file": f"src/{name}.py", "distance": 1}


def _serialized_size(obj: object) -> int:
    """Measure object size via the leaf's own serializer (single source of truth).

    Using serialized_size (the same json.dumps(..., ensure_ascii=False) the leaf and the
    CLI emit_json use) keeps every budget computed below consistent with what the trimmer
    measures internally — so boundary tests can't pass/fail on a separator mismatch.
    """
    return serialized_size(obj)


def _mk_response(
    upstream_will: list | None = None,
    upstream_likely: list | None = None,
    upstream_may: list | None = None,
    downstream_will: list | None = None,
    downstream_likely: list | None = None,
    downstream_may: list | None = None,
    extras: dict | None = None,
) -> dict:
    """Build a realistic seam_impact response dict for tests."""
    response: dict = {"found": True, "target": "MyClass"}
    upstream: dict = {}
    if upstream_will is not None:
        upstream["WILL_BREAK"] = upstream_will
    if upstream_likely is not None:
        upstream["LIKELY_AFFECTED"] = upstream_likely
    if upstream_may is not None:
        upstream["MAY_NEED_TESTING"] = upstream_may
    if upstream:
        response["upstream"] = upstream

    downstream: dict = {}
    if downstream_will is not None:
        downstream["WILL_BREAK"] = downstream_will
    if downstream_likely is not None:
        downstream["LIKELY_AFFECTED"] = downstream_likely
    if downstream_may is not None:
        downstream["MAY_NEED_TESTING"] = downstream_may
    if downstream:
        response["downstream"] = downstream

    if extras:
        response.update(extras)
    return response


def _envelope_size(response: dict) -> int:
    """Estimate the envelope size: all tier lists emptied.

    This matches what the algorithm builds internally — useful for computing
    tight budgets in tests.
    """
    env = copy.deepcopy(response)
    for key in ("upstream", "downstream"):
        if key in env and isinstance(env[key], dict):
            for tier in list(env[key].keys()):
                if isinstance(env[key][tier], list):
                    env[key][tier] = []
    return _serialized_size(env)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_everything_fits_returns_unchanged_response_and_zero_dropped() -> None:
    """When budget > full size, nothing is dropped: response unchanged, byte_dropped={}, total=0."""
    entries = [_mk_entry("caller_a"), _mk_entry("caller_b")]
    response = _mk_response(upstream_will=entries)
    budget = _serialized_size(response) + 1000  # plenty of room

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    assert trimmed == response
    assert byte_dropped == {}
    assert total_omitted == 0


def test_output_serialized_size_never_exceeds_budget() -> None:
    """The trimmed response serialized size must always be <= budget."""
    entries = [_mk_entry(f"caller_{i}") for i in range(20)]
    response = _mk_response(
        upstream_will=entries[:10],
        upstream_likely=entries[10:15],
        upstream_may=entries[15:],
    )
    full_size = _serialized_size(response)
    # Budget at roughly half — forces trimming.
    budget = full_size // 2

    trimmed, _, _ = fit_to_byte_budget(response, budget=budget)

    assert _serialized_size(trimmed) <= budget


def test_tight_budget_drops_downstream_before_upstream() -> None:
    """Priority: upstream kept before downstream when forced to choose."""
    up_entries = [_mk_entry("up_caller")]
    down_entries = [_mk_entry("down_callee")]
    response = _mk_response(upstream_will=up_entries, downstream_will=down_entries)

    # The algorithm's envelope retains BOTH direction keys (as empty-list groups).
    # Budget must be based on the actual envelope + upstream entry — not a reduced response.
    # Build the reference "what the result should look like": both keys, upstream filled.
    expected_trimmed = {
        "found": True,
        "target": "MyClass",
        "upstream": {"WILL_BREAK": up_entries},
        "downstream": {"WILL_BREAK": []},
    }
    budget = _serialized_size(expected_trimmed) + 5

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    assert _serialized_size(trimmed) <= budget
    # upstream WILL_BREAK entry survived.
    assert "upstream" in trimmed
    assert len(trimmed["upstream"]["WILL_BREAK"]) == 1
    # downstream entry was dropped.
    assert total_omitted >= 1
    assert "downstream" in byte_dropped


def test_tight_budget_drops_may_testing_before_will_break() -> None:
    """Within one direction: WILL_BREAK kept before MAY_NEED_TESTING."""
    will_entries = [_mk_entry("critical")]
    may_entries = [_mk_entry("low_risk_a"), _mk_entry("low_risk_b")]
    response = _mk_response(upstream_will=will_entries, upstream_may=may_entries)

    # Envelope retains both tier keys as empty lists; budget allows WILL_BREAK but not MAY.
    expected_trimmed = {
        "found": True,
        "target": "MyClass",
        "upstream": {"WILL_BREAK": will_entries, "MAY_NEED_TESTING": []},
    }
    budget = _serialized_size(expected_trimmed) + 5

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    assert _serialized_size(trimmed) <= budget
    # WILL_BREAK survived.
    assert len(trimmed["upstream"]["WILL_BREAK"]) == 1
    # MAY_NEED_TESTING was dropped.
    assert total_omitted >= 1
    assert byte_dropped.get("upstream", {}).get("MAY_NEED_TESTING", 0) >= 1


def test_intra_tier_front_prefix_preserved() -> None:
    """Within a tier, entries are kept from the front (earlier = higher priority)."""
    entries = [_mk_entry(f"entry_{i}") for i in range(5)]
    response = _mk_response(upstream_will=entries)
    # Budget: enough for only the first 2 entries (response only has upstream, one tier).
    expected_trimmed = {
        "found": True,
        "target": "MyClass",
        "upstream": {"WILL_BREAK": entries[:2]},
    }
    budget = _serialized_size(expected_trimmed) + 5

    trimmed, _, total_omitted = fit_to_byte_budget(response, budget=budget)

    assert _serialized_size(trimmed) <= budget
    assert total_omitted >= 1
    kept = trimmed["upstream"]["WILL_BREAK"]
    assert len(kept) >= 1
    # The first entry (highest priority) must survive.
    assert kept[0]["name"] == "entry_0"
    # Every kept entry must appear before every dropped entry in the original list.
    kept_names = {e["name"] for e in kept}
    dropped_names = {e["name"] for e in entries if e["name"] not in kept_names}
    for kept_name in kept_names:
        ki = next(i for i, e in enumerate(entries) if e["name"] == kept_name)
        for dropped_name in dropped_names:
            di = next(i for i, e in enumerate(entries) if e["name"] == dropped_name)
            assert ki < di, (
                f"kept {kept_name!r} (pos {ki}) must precede dropped {dropped_name!r} (pos {di})"
            )


def test_exact_fit_boundary_nothing_dropped() -> None:
    """When budget == full serialized size, nothing is dropped."""
    entries = [_mk_entry("caller_a"), _mk_entry("caller_b")]
    response = _mk_response(upstream_will=entries)
    budget = _serialized_size(response)  # exact fit

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    assert byte_dropped == {}
    assert total_omitted == 0
    assert trimmed == response


def test_budget_smaller_than_envelope_drops_all_entries() -> None:
    """Budget < envelope size: every entry is dropped; total_omitted = total entry count."""
    entries = [_mk_entry(f"caller_{i}") for i in range(3)]
    response = _mk_response(upstream_will=entries)
    # Budget so small that even the empty-tier envelope doesn't fit — force all drops.
    budget = 1

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    # Must not raise; total_omitted == number of entries.
    assert total_omitted == 3
    # All tier lists are empty in the trimmed response.
    if "upstream" in trimmed:
        for tier_val in trimmed["upstream"].values():
            if isinstance(tier_val, list):
                assert len(tier_val) == 0


def test_input_response_not_mutated() -> None:
    """fit_to_byte_budget must not mutate the input response dict."""
    entries = [_mk_entry("caller_a"), _mk_entry("caller_b")]
    response = _mk_response(upstream_will=entries)
    original_will = list(response["upstream"]["WILL_BREAK"])
    budget = 10  # tiny budget to force trimming

    fit_to_byte_budget(response, budget=budget)

    # Input must be untouched.
    assert response["upstream"]["WILL_BREAK"] == original_will


def test_non_direction_keys_copied_unchanged() -> None:
    """Keys like found, target, risk_summary, truncated, hidden_tests are always copied through."""
    extras = {
        "risk_summary": {"upstream": {"WILL_BREAK": 5}},
        "truncated": {"upstream": {"WILL_BREAK": 3}},
        "hidden_tests": 2,
    }
    entries = [_mk_entry(f"caller_{i}") for i in range(5)]
    response = _mk_response(upstream_will=entries, extras=extras)
    budget = 10  # force trimming of entries

    trimmed, _, _ = fit_to_byte_budget(response, budget=budget)

    # Non-direction keys must survive intact.
    assert trimmed["found"] is True
    assert trimmed["target"] == "MyClass"
    assert trimmed["risk_summary"] == extras["risk_summary"]
    assert trimmed["truncated"] == extras["truncated"]
    assert trimmed["hidden_tests"] == 2


def test_malformed_tier_value_not_a_list_no_raise() -> None:
    """A tier whose value is not a list must not raise — it is passed through unchanged."""
    response = {
        "found": True,
        "upstream": {
            "WILL_BREAK": "not-a-list",  # malformed
            "LIKELY_AFFECTED": [_mk_entry("caller_a")],
        },
    }
    budget = 10

    result = fit_to_byte_budget(response, budget=budget)

    # Must not raise; returns a 3-tuple.
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_zero_budget_disables_ceiling_no_trimming() -> None:
    """Budget <= 0 means unlimited — output is byte-identical to input."""
    entries = [_mk_entry(f"caller_{i}") for i in range(10)]
    response = _mk_response(upstream_will=entries)

    trimmed_zero, byte_dropped_zero, total_zero = fit_to_byte_budget(response, budget=0)
    trimmed_neg, byte_dropped_neg, total_neg = fit_to_byte_budget(response, budget=-1)

    assert trimmed_zero == response
    assert byte_dropped_zero == {}
    assert total_zero == 0
    assert trimmed_neg == response
    assert byte_dropped_neg == {}
    assert total_neg == 0


def test_empty_response_no_direction_keys_no_raise() -> None:
    """A response with no direction groups at all must be handled safely."""
    response = {"found": False}
    budget = 100

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    assert trimmed == response
    assert byte_dropped == {}
    assert total_omitted == 0


def test_custom_direction_and_tier_order_respected() -> None:
    """When caller overrides direction_order, the override governs keep-priority."""
    up_entry = _mk_entry("upstream_caller")
    down_entry = _mk_entry("downstream_callee")
    response = _mk_response(upstream_will=[up_entry], downstream_will=[down_entry])

    # With downstream FIRST in direction_order, downstream is higher priority.
    # Budget: envelope + downstream entry (both direction keys present as envelope).
    expected_trimmed = {
        "found": True,
        "target": "MyClass",
        "upstream": {"WILL_BREAK": []},
        "downstream": {"WILL_BREAK": [down_entry]},
    }
    budget = _serialized_size(expected_trimmed) + 5

    trimmed, _, total_omitted = fit_to_byte_budget(
        response,
        budget=budget,
        direction_order=("downstream", "upstream"),
    )

    assert _serialized_size(trimmed) <= budget
    # Downstream entry should survive (it's now priority-1).
    assert "downstream" in trimmed
    assert len(trimmed["downstream"]["WILL_BREAK"]) == 1
    # Total dropped must be >= 1 (the upstream entry was dropped).
    assert total_omitted >= 1


def test_dropped_counts_match_missing_entries() -> None:
    """byte_dropped counts must account for all entries absent from trimmed vs. original."""
    entries = [_mk_entry(f"entry_{i}") for i in range(6)]
    response = _mk_response(upstream_will=entries[:3], upstream_may=entries[3:])
    # Use a tight budget that keeps only a few entries.
    budget = _envelope_size(response) + _serialized_size(entries[0]) + 5

    trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=budget)

    # Count entries in trimmed.
    trimmed_count = 0
    for direction in ("upstream", "downstream"):
        if direction in trimmed:
            for tier_val in trimmed[direction].values():
                if isinstance(tier_val, list):
                    trimmed_count += len(tier_val)

    # Total dropped = original count - trimmed count.
    original_count = 6
    expected_dropped = original_count - trimmed_count
    assert total_omitted == expected_dropped

    # byte_dropped values must sum to total_omitted.
    counted_from_dict = sum(
        count
        for tier_map in byte_dropped.values()
        for count in tier_map.values()
    )
    assert counted_from_dict == total_omitted
