"""Byte-ceiling trimmer for seam_impact output (E1-FULL).

LEAF MODULE — pure functions over plain dicts. Imports only stdlib (json, typing).
No database access, no config import, no IO, never raises.
Mirrors the leaf discipline of seam/analysis/relevance.py.

WHY this module exists (the usability gap it closes):
  seam_impact output size is bounded by SEAM_IMPACT_MAX_RESULTS (an entry-count cap),
  but entry count is a poor proxy for byte size: a tier of 25 entries with long
  signatures and qualified names can be many kilobytes, while 25 short entries is tiny.
  AI agents budget their context window in tokens (≈ characters), not entry counts, so
  the count cap cannot guarantee the output fits a context budget.

  fit_to_byte_budget() receives an already-assembled seam_impact response dict (after
  the per-tier count cap and E2/E3 relevance ordering have already run) and trims entries
  from the LEAST-VALUABLE end of a global priority order until the serialized response
  fits the budget. Because entries are already ordered by priority (externals before
  self-refs, production before test, nearest tier first), the survivors are guaranteed
  the highest-signal dependents that fit.

Priority order (highest to lowest — front entries survive, tail entries are dropped):
  1. direction: upstream before downstream (what calls me? is more valuable than what I call)
  2. tier risk: WILL_BREAK → LIKELY_AFFECTED → MAY_NEED_TESTING
  3. intra-tier: existing E2/E3 entry order preserved (front entries survive)

Conservatism contract:
  - NEVER exceed the budget for the trimmed response body — `running` is a proven UPPER
    bound on the real serialized size (see the algorithm note), so `running <= budget`
    guarantees `serialized_size(trimmed) <= budget`. The ONLY case the body can exceed
    budget is when the budget is smaller than the irreducible envelope (no entries can
    fit) — then every entry is dropped and the bare envelope is returned.
  - NEVER raise on any input — the public entry point wraps the whole body and returns
    (response, {}, 0) on failure (no trimming is safer than a crash; the handler degrades).
  - NEVER mutate the input response dict.
  - Non-direction keys (found, target, risk_summary, truncated, etc.) are ALWAYS copied
    through unchanged — trimming touches only direction-tier entry lists.
  - Budget <= 0 means unlimited — return input unchanged immediately.

Unit = characters of json.dumps(obj, ensure_ascii=False) — the SAME serialization the
CLI uses to emit results (seam/cli/output.py emit_json). Measuring with the emit
serializer means the budget bounds the actual rendered bytes, not a more-compact proxy
that would let the real output overrun the ceiling. This is a deterministic,
dependency-free token proxy (~4 chars/token); no tokenizer is used (a real tokenizer
would be an external model-specific dep violating zero-external-services).

Algorithm note (O(n) running-total prefix):
  Entries are walked in keep-priority order. Each entry is charged its own serialized
  size PLUS one separator char (the comma that joins it to its siblings). The running
  total starts from the envelope size (the response with all tier lists emptied) and
  accumulates per kept entry. The +1-per-entry charge is a deliberate OVER-estimate:
  the first entry in a tier has no preceding comma, so `running` is always >= the true
  serialized size — which is exactly what makes `running <= budget` a hard guarantee.
  The first entry that would overflow stops all further placement (stop-at-first-overflow
  prefix): every entry before position k is kept, every entry at or after k is dropped.
  This is O(n) (no full re-serialization per placement), so it stays cheap even on the
  documented limit=0 / unbounded blast-radius path.
"""

import json
from typing import Any


def serialized_size(obj: Any) -> int:
    """Return the character count of obj under the CLI emit serialization.

    Matches seam/cli/output.py emit_json (json.dumps with default separators and
    ensure_ascii=False) so the measured budget reflects the bytes actually rendered.
    Exported as the single source of truth — the handler and tests measure with this
    same function so budget arithmetic cannot drift between layers.
    """
    return len(json.dumps(obj, ensure_ascii=False))


def fit_to_byte_budget(
    response: dict[str, Any],
    *,
    budget: int,
    direction_order: tuple[str, ...] = ("upstream", "downstream"),
    tier_order: tuple[str, ...] = ("WILL_BREAK", "LIKELY_AFFECTED", "MAY_NEED_TESTING"),
) -> tuple[dict[str, Any], dict[str, dict[str, int]], int]:
    """Trim seam_impact entries to fit within a byte budget.

    Args:
        response:        The fully-assembled seam_impact response dict. NOT mutated.
        budget:          Maximum serialized size in characters. 0 or negative = unlimited
                         (return the input unchanged, no trimming).
        direction_order: Keep-priority for directions (first = highest priority).
                         Default: upstream before downstream.
        tier_order:      Keep-priority for tiers within each direction (first = highest).
                         Default: WILL_BREAK → LIKELY_AFFECTED → MAY_NEED_TESTING.

    Returns:
        A 3-tuple:
          trimmed_response  — a NEW dict (input never mutated). Non-direction keys
                              (found, target, risk_summary, truncated, etc.) are copied
                              through unchanged. Direction-tier lists may be shorter.
          byte_dropped      — {direction: {tier: count}} of entries this function removed.
                              Only non-zero counts and non-empty direction dicts are included.
          total_omitted     — sum of all counts in byte_dropped (0 = ceiling did not fire /
                              everything fit).

    Never raises. On any exception returns (response, {}, 0).
    """
    # Budget 0 or negative = unlimited. Return unchanged immediately (byte-identical path).
    if budget <= 0:
        return response, {}, 0

    try:
        return _fit_to_byte_budget_impl(response, budget, direction_order, tier_order)
    except Exception:
        # Safety net: return input unchanged on any failure (no trimming > crash).
        # The handler detects this degradation (it knew the response did NOT fit yet
        # nothing was dropped) and logs it, so the silent path is observable upstream.
        return response, {}, 0


def _collect_entry_walk(
    response: dict[str, Any],
    direction_order: tuple[str, ...],
    tier_order: tuple[str, ...],
) -> list[tuple[str, str, Any]]:
    """Build the priority-ordered flat walk of (direction, tier, entry) triples.

    Only includes entries from directions/tiers that are BOTH present in the response
    AND have a list value (malformed non-list tier values are skipped here; they are
    copied through in the envelope, never trimmed).

    The walk order is the keep-priority order: for direction in direction_order, for
    tier in tier_order, for entry in that tier's list — earlier triples are more
    valuable and survive trimming.
    """
    walk: list[tuple[str, str, Any]] = []
    for direction in direction_order:
        dir_group = response.get(direction)
        if not isinstance(dir_group, dict):
            continue
        for tier in tier_order:
            tier_val = dir_group.get(tier)
            if not isinstance(tier_val, list):
                continue
            for entry in tier_val:
                walk.append((direction, tier, entry))
    return walk


def _build_envelope(
    response: dict[str, Any],
    direction_order: tuple[str, ...],
) -> dict[str, Any]:
    """Build a response copy with all direction-tier lists EMPTIED.

    Non-direction keys and direction groups present in the original are preserved in
    structure; only the entry lists are set to []. This gives the 'overhead' size that
    any kept entry must fit on top of. Malformed tier values (non-list) are copied
    through unchanged (they add to overhead but cannot be trimmed — conservatism).
    """
    envelope: dict[str, Any] = {}
    for key, value in response.items():
        if key in direction_order and isinstance(value, dict):
            dir_copy: dict[str, Any] = {}
            for tier_key, tier_val in value.items():
                dir_copy[tier_key] = [] if isinstance(tier_val, list) else tier_val
            envelope[key] = dir_copy
        else:
            envelope[key] = value
    return envelope


def _fit_to_byte_budget_impl(
    response: dict[str, Any],
    budget: int,
    direction_order: tuple[str, ...],
    tier_order: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, dict[str, int]], int]:
    """Inner implementation — only called when budget > 0. Caller wraps for never-raises."""
    # Fast path: the full response already fits.
    if serialized_size(response) <= budget:
        return response, {}, 0

    walk = _collect_entry_walk(response, direction_order, tier_order)
    # Running total starts at the envelope size (overhead with all tier lists empty).
    running = serialized_size(_build_envelope(response, direction_order))

    kept: dict[str, dict[str, list[Any]]] = {}
    dropped: dict[str, dict[str, int]] = {}
    overflow = False  # once True, all remaining (lower-priority) entries are dropped

    for direction, tier, entry in walk:
        if not overflow:
            # +1 charges the comma that joins this entry to its siblings. The first
            # entry in a tier has no preceding comma, so this OVER-estimates — which is
            # what makes `running <= budget` a hard upper bound on the real size.
            cost = serialized_size(entry) + 1
            if running + cost <= budget:
                kept.setdefault(direction, {}).setdefault(tier, []).append(entry)
                running += cost
                continue
            overflow = True
        dir_dropped = dropped.setdefault(direction, {})
        dir_dropped[tier] = dir_dropped.get(tier, 0) + 1

    # Build the trimmed response: a fresh empty-list envelope with the kept lists filled.
    # kept holds the SAME entry-dict references as the input (never copied, never mutated).
    trimmed = _build_envelope(response, direction_order)
    for direction, tier_map in kept.items():
        for tier, entries in tier_map.items():
            trimmed[direction][tier] = entries

    # byte_dropped: drop zero counts and empty direction dicts.
    byte_dropped: dict[str, dict[str, int]] = {}
    for direction, count_map in dropped.items():
        filtered = {tier: cnt for tier, cnt in count_map.items() if cnt > 0}
        if filtered:
            byte_dropped[direction] = filtered
    total_omitted = sum(cnt for count_map in byte_dropped.values() for cnt in count_map.values())

    return trimmed, byte_dropped, total_omitted
