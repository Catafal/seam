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
  - NEVER exceed the budget (hard ceiling).
  - NEVER raise on any input — wrap the whole body and return (response, {}, 0) on failure
    (no trimming is safer than a crash; the handler degrades gracefully).
  - NEVER mutate the input response dict.
  - Non-direction keys (found, target, risk_summary, truncated, etc.) are ALWAYS copied
    through unchanged — trimming touches only direction-tier entry lists.
  - Budget <= 0 means unlimited — return input unchanged immediately.

Unit = characters in the compact JSON serialization (json.dumps with separators=(",", ":")).
This is a deterministic, dependency-free token proxy (~4 chars/token). No tokenizer is used
— a real tokenizer would be an external model-specific dep violating zero-external-services.

Note on algorithm (stop-at-first-overflow prefix):
  Entries are walked in keep-priority order. Each entry is placed tentatively and the
  FULL response is re-serialized. If the result fits the budget the entry is kept;
  if not, ALL remaining lower-priority entries are also dropped (stop-at-first-overflow).
  This guarantees a clean prefix: every entry before position k is kept, every entry at or
  after k is dropped. With n <= ~150 entries per response (25 per tier × 3 tiers × 2
  directions), re-measuring per placement is negligible in practice.
"""

import json
from typing import Any


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
        return response, {}, 0


def _serialized_size(obj: Any) -> int:
    """Return the compact JSON character count of obj."""
    return len(json.dumps(obj, separators=(",", ":")))


def _collect_entry_walk(
    response: dict[str, Any],
    direction_order: tuple[str, ...],
    tier_order: tuple[str, ...],
) -> list[tuple[str, str, Any]]:
    """Build the priority-ordered flat walk of (direction, tier, entry) triples.

    Only includes entries from directions/tiers that are BOTH present in the response
    AND have a list value (malformed non-list tier values are silently skipped here;
    they are copied through in the envelope).

    The walk order is: for direction in direction_order, for tier in tier_order,
    for entry in that tier's list. This is the keep-priority order — earlier triples
    are more valuable and survive trimming.
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
    tier_order: tuple[str, ...],
) -> dict[str, Any]:
    """Build a response copy with all direction-tier lists EMPTIED.

    Non-direction keys and direction groups that are present in the original are
    preserved in structure; only the entry lists are set to []. This gives the
    'overhead' size that any kept entry must fit on top of.

    Malformed tier values (non-list) are copied through unchanged (they add to
    overhead but cannot be trimmed — safety/conservatism).
    """
    envelope: dict[str, Any] = {}
    for key, value in response.items():
        if key in direction_order and isinstance(value, dict):
            # Empty all list-valued tier slots; copy non-list slots unchanged.
            dir_copy: dict[str, Any] = {}
            for tier_key, tier_val in value.items():
                if isinstance(tier_val, list):
                    dir_copy[tier_key] = []
                else:
                    dir_copy[tier_key] = tier_val
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
    """Inner implementation — only called when budget > 0. May not raise (caller wraps it)."""
    # Check if the full response already fits — fast path.
    if _serialized_size(response) <= budget:
        return response, {}, 0

    # Build the envelope (response with all tier lists emptied).
    working = _build_envelope(response, direction_order, tier_order)

    # If even the envelope doesn't fit, all entries must be dropped.
    if _serialized_size(working) > budget:
        # Count all original entries as dropped.
        byte_dropped: dict[str, dict[str, int]] = {}
        total = 0
        for direction in direction_order:
            dir_group = response.get(direction)
            if not isinstance(dir_group, dict):
                continue
            for tier in tier_order:
                tier_val = dir_group.get(tier)
                if isinstance(tier_val, list) and tier_val:
                    count = len(tier_val)
                    byte_dropped.setdefault(direction, {})[tier] = count
                    total += count
        return working, byte_dropped, total

    # Walk entries in keep-priority order; greedily place each one.
    walk = _collect_entry_walk(response, direction_order, tier_order)

    # Track which entries we KEEP per (direction, tier).
    kept: dict[str, dict[str, list[Any]]] = {}
    # Track counts of dropped entries per (direction, tier).
    dropped_counts: dict[str, dict[str, int]] = {}

    overflow = False  # once True, all remaining entries are dropped

    for direction, tier, entry in walk:
        if overflow:
            # Drop all remaining lower-priority entries.
            dropped_counts.setdefault(direction, {})[tier] = (
                dropped_counts.get(direction, {}).get(tier, 0) + 1
            )
            continue

        # Tentatively add this entry and re-measure.
        kept.setdefault(direction, {}).setdefault(tier, []).append(entry)
        _apply_kept_to_working(working, kept)
        if _serialized_size(working) <= budget:
            # Fits — keep it.
            pass
        else:
            # Doesn't fit — remove it and stop placing any more entries.
            kept[direction][tier].pop()
            _apply_kept_to_working(working, kept)
            overflow = True
            dropped_counts.setdefault(direction, {})[tier] = (
                dropped_counts.get(direction, {}).get(tier, 0) + 1
            )

    # Also count entries that were never reached because overflow=True was set earlier,
    # but which weren't in the walk yet. Actually the loop above handles all entries
    # (they all appear in walk), so we just need to finalize dropped from the totals.
    # Compute byte_dropped (only non-zero direction/tier counts).
    byte_dropped_final: dict[str, dict[str, int]] = {}
    for direction, tier_map in dropped_counts.items():
        filtered = {tier: cnt for tier, cnt in tier_map.items() if cnt > 0}
        if filtered:
            byte_dropped_final[direction] = filtered

    total_omitted = sum(
        cnt for tier_map in byte_dropped_final.values() for cnt in tier_map.values()
    )

    return working, byte_dropped_final, total_omitted


def _apply_kept_to_working(
    working: dict[str, Any],
    kept: dict[str, dict[str, list[Any]]],
) -> None:
    """Apply the current kept entry lists into the working response dict (in-place).

    Only updates direction groups + tier lists that are represented in kept.
    """
    for direction, tier_map in kept.items():
        for tier, entries in tier_map.items():
            working[direction][tier] = entries
