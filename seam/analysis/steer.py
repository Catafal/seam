"""Truncation-steer generator for seam_impact output (E4).

LEAF MODULE — pure function over plain dicts. Imports only stdlib (typing).
No database access, no config import, no IO, never raises.
Mirrors the leaf discipline of seam/analysis/byte_budget.py and
seam/analysis/relevance.py.

WHY this module exists (the usability gap it closes):
  When seam_impact drops entries — via the per-tier count cap or the E1-FULL
  byte ceiling — the response carries 'truncated' and 'byte_capped' counts that
  are honest but inert. An agent knows entries were dropped but must guess the
  remedy (raise the limit? bump max_bytes? narrow the query?). The information
  needed to act is known at trim time but discarded.

  generate_steer() receives the already-computed trim metadata and returns a
  flat list of ready-to-act prose hints — e.g.:
    "Raise limit to 17 to see 12 more WILL_BREAK upstream dependents."
    "Pass max_bytes=0 for the full untrimmed blast radius."
  Each hint is a complete, actionable sentence. The list is ABSENT (not empty-
  and-present) when nothing was trimmed — so presence is the "there is more"
  signal and absence is unambiguous.

  When the byte ceiling drops ALL entries (the anti-false-safe case), the steer
  always includes an explicit warning that the blast radius was trimmed to nothing
  and this is NOT "no dependents" — it must not let an agent conclude the symbol
  is safe to delete when its dependents were merely dropped.

Steer content rules (from E4 spec):
  (a) Count-cap drops → suggest the smallest `limit` that would reveal them:
      risk_summary[dir][tier] is the minimum (current kept count + omitted count).
      Emits one hint per direction+tier that has count-cap omissions.
  (b) Byte-ceiling drops → suggest raising or zeroing `max_bytes`.
      Emits one hint covering the total byte-ceiling drop.
  (c) All-trimmed (entries lists all empty, risk_summary non-empty) → explicit
      anti-false-safe line. Emitted BEFORE the byte-ceiling remedy hint.
  (d) Both caps fired → both hints without double-counting. Count-cap portion =
      truncated[dir][tier] - byte_capped_portion_per_tier. Since byte_capped.omitted
      is a total (not per-direction/tier), we output the two remedies separately:
      count-cap hints first, then the byte-ceiling hint.

Null-contract:
  `byte_capped=None` means the byte ceiling did not fire (same null-contract as
  the handler's _apply_byte_ceiling usage). An empty {} byte_capped is handled
  defensively (treated as not-fired).

Never raises. On any exception returns [].
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default tier/direction priority. These are DEFAULTS only — the handler injects the
# canonical impact.py TIER_* constants via the tier_order/direction_order params so the
# names have a single source of truth (the leaf stays standalone-testable without
# importing impact.py, preserving leaf discipline). Order matters: highest priority
# first (WILL_BREAK before MAY_NEED_TESTING) for DISPLAY; the byte-portion deduction
# walks this reversed (least-valuable first) to mirror fit_to_byte_budget's trim order.
_DEFAULT_TIER_ORDER = ("WILL_BREAK", "LIKELY_AFFECTED", "MAY_NEED_TESTING")
_DEFAULT_DIRECTION_ORDER = ("upstream", "downstream")


def generate_steer(
    *,
    truncated: dict[str, dict[str, int]],
    byte_capped: dict[str, int] | None,
    risk_summary: dict[str, dict[str, int]],
    limit: int,
    max_bytes: int,
    tier_order: tuple[str, ...] = _DEFAULT_TIER_ORDER,
    direction_order: tuple[str, ...] = _DEFAULT_DIRECTION_ORDER,
) -> list[str]:
    """Generate actionable truncation-steer hints from seam_impact trim metadata.

    Called by handle_seam_impact AFTER per-tier capping and byte-ceiling trimming
    have both run. Returns a flat list of ready-to-act prose strings. Returns []
    (empty, never None) when nothing was trimmed or on any internal error.

    Args:
        truncated:   The merged per-direction/tier omitted count map.
                     {direction: {tier: count}}. Includes BOTH count-cap and
                     byte-ceiling drops (additive, as in the handler). May be {}.
        byte_capped: The byte_capped metadata dict {"limit": int, "omitted": int}
                     when the byte ceiling fired and dropped ≥1 entry, or None
                     when the ceiling did not fire (unlimited or nothing trimmed).
        risk_summary: The honest pre-cap total per direction+tier.
                      {direction: {tier: count}}. Used to compute the suggested
                      limit value (minimum that reveals all omitted entries).
        limit:        The per-tier count cap that was applied (SEAM_IMPACT_MAX_RESULTS
                      or the per-call override). Used in hint phrasing.
        max_bytes:    The byte budget that was applied (SEAM_IMPACT_MAX_BYTES or the
                      per-call override). Used in hint phrasing. 0 = unlimited.
        tier_order:   Risk tiers in DISPLAY (highest-priority-first) order. Injected by
                      the handler from impact.py's TIER_* constants (single source of truth).
        direction_order: Directions in display order. Injected by the handler.

    Returns:
        list[str]: Prose hint lines. Empty list when nothing to say.
                   Never raises — any exception returns [].
    """
    try:
        return _generate_steer_impl(
            truncated=truncated,
            byte_capped=byte_capped,
            risk_summary=risk_summary,
            limit=limit,
            max_bytes=max_bytes,
            tier_order=tier_order,
            direction_order=direction_order,
        )
    except Exception:
        # Safety net: never break the read path. Empty steer is always safe.
        # Logged (matching _apply_byte_ceiling's failure precedent) so a malformed-input
        # bug that silently drops the steer — including the all-trimmed anti-false-safe
        # warning generated inside this path — is observable rather than invisible.
        logger.warning("seam_impact steer generation failed; omitting next_actions", exc_info=True)
        return []


def _total_risk(risk_summary: Any) -> int:
    """Sum all counts in risk_summary. Returns 0 on malformed input."""
    if not isinstance(risk_summary, dict):
        return 0
    total = 0
    for dir_map in risk_summary.values():
        if not isinstance(dir_map, dict):
            continue
        for count in dir_map.values():
            if isinstance(count, int):
                total += count
    return total


def _total_truncated(truncated: Any) -> int:
    """Sum all counts in truncated. Returns 0 on malformed input."""
    if not isinstance(truncated, dict):
        return 0
    total = 0
    for dir_map in truncated.values():
        if not isinstance(dir_map, dict):
            continue
        for count in dir_map.values():
            if isinstance(count, int):
                total += count
    return total


def _is_all_trimmed(
    truncated: dict[str, dict[str, int]],
    byte_capped: dict[str, int] | None,
    risk_summary: dict[str, dict[str, int]],
) -> bool:
    """Return True when the byte ceiling dropped ALL entries (entries shown == 0).

    All-trimmed means: risk_summary is non-empty (there ARE real dependents)
    AND total truncated == total risk_summary (every entry was dropped).
    We only flag this when byte_capped is present (the ceiling fired), because
    all-entries-count-capped is a normal use case (the count cap is opt-in at
    limit=0), while all-entries-byte-capped is the dangerous false-safe case.
    """
    if not byte_capped or not isinstance(byte_capped, dict):
        return False
    byte_omitted = byte_capped.get("omitted", 0)
    if not isinstance(byte_omitted, int) or byte_omitted <= 0:
        return False
    total_risk = _total_risk(risk_summary)
    total_trunc = _total_truncated(truncated)
    # All-trimmed: every entry that exists in risk_summary was dropped.
    return total_risk > 0 and total_trunc >= total_risk


def _generate_steer_impl(
    *,
    truncated: dict[str, dict[str, int]],
    byte_capped: dict[str, int] | None,
    risk_summary: dict[str, dict[str, int]],
    limit: int,
    max_bytes: int,
    tier_order: tuple[str, ...],
    direction_order: tuple[str, ...],
) -> list[str]:
    """Inner implementation — only called when input types are validated by caller.

    Separated from the public function so the try/except wraps the whole body.
    """
    # Validate core inputs are dicts to avoid TypeErrors below.
    if not isinstance(truncated, dict):
        return []
    if not isinstance(risk_summary, dict):
        return []

    # Early exit: nothing was trimmed at all.
    byte_capped_clean = byte_capped if isinstance(byte_capped, dict) else None
    byte_omitted = 0
    if byte_capped_clean:
        raw_omitted = byte_capped_clean.get("omitted", 0)
        byte_omitted = raw_omitted if isinstance(raw_omitted, int) else 0

    total_trunc = _total_truncated(truncated)
    if total_trunc == 0 and byte_omitted == 0:
        return []

    hints: list[str] = []

    # ── (c) All-trimmed-to-nothing warning (must come first — most critical) ──
    if _is_all_trimmed(truncated, byte_capped_clean, risk_summary):
        hints.append(
            "WARNING: The blast radius was trimmed to nothing by the byte ceiling. "
            "This is NOT 'no dependents' — dependents exist but were omitted. "
            "Pass max_bytes=0 for the full untrimmed blast radius before concluding "
            "this symbol is safe to delete."
        )
        # After the all-trimmed warning, still emit the max_bytes remedy below
        # (it is the actionable fix). Skip count-cap hints since the byte ceiling
        # dominated and the count cap is irrelevant here.
        if byte_capped_clean:
            byte_limit = byte_capped_clean.get("limit", max_bytes)
            if not isinstance(byte_limit, int):
                byte_limit = max_bytes
            hints.append(
                f"Pass max_bytes=0 (currently {byte_limit}) for the full untrimmed blast radius."
            )
        return hints

    # ── (a) Count-cap hints — one per direction+tier with count-cap omissions ──
    # truncated[dir][tier] is the MERGED omitted count (count-cap drops + byte-ceiling
    # drops, additive). To attribute it correctly we must isolate the count-cap-only
    # portion, because the two have DIFFERENT remedies: count-cap drops are revealed by
    # raising `limit`, byte-ceiling drops only by raising `max_bytes`. Misattributing a
    # byte-dropped entry to the count cap yields a hint ("Raise limit to N") that, when
    # followed, does NOT reveal the entry — the agent re-queries, sees the same truncation,
    # and may wrongly conclude the dependent is phantom.
    #
    # The byte ceiling (fit_to_byte_budget) trims from the LEAST-valuable end first:
    # downstream before upstream, MAY_NEED_TESTING before WILL_BREAK. So we deduct the
    # byte-omitted total from the merged counts in that SAME reverse order; whatever
    # remains in a (dir, tier) cell is its genuine count-cap portion.
    count_cap_portion: dict[tuple[str, str], int] = {}
    remaining_byte_omitted = byte_omitted
    for direction in reversed(direction_order):
        dir_trunc = truncated.get(direction)
        if not isinstance(dir_trunc, dict):
            continue
        for tier in reversed(tier_order):
            merged_omitted = dir_trunc.get(tier, 0)
            if not isinstance(merged_omitted, int) or merged_omitted <= 0:
                continue
            byte_portion = min(remaining_byte_omitted, merged_omitted)
            remaining_byte_omitted -= byte_portion
            count_cap_portion[(direction, tier)] = merged_omitted - byte_portion

    # Emit count-cap hints in DISPLAY order (highest-priority tier first).
    for direction in direction_order:
        dir_risk = risk_summary.get(direction, {})
        if not isinstance(dir_risk, dict):
            dir_risk = {}
        for tier in tier_order:
            portion = count_cap_portion.get((direction, tier), 0)
            if portion <= 0:
                continue
            # A count-cap drop is only possible when the count cap is active (limit > 0);
            # with limit=0 (unlimited) every count-cap drop is really a byte-ceiling drop
            # and the deduction above should have zeroed `portion`. Guard defensively.
            if limit <= 0:
                continue

            # Suggest the minimum limit that reveals all entries in this tier:
            # risk_summary[dir][tier] is the honest pre-cap total. Clamp to never suggest
            # a value <= the current limit (which would tell the agent to LOWER the cap
            # and HIDE entries) — the suggestion must always be enough to reveal `portion`
            # more entries on top of what is shown.
            total_in_tier = dir_risk.get(tier, 0)
            if not isinstance(total_in_tier, int):
                total_in_tier = 0
            suggested_limit = max(total_in_tier, limit + portion)
            hints.append(
                f"Raise limit to {suggested_limit} to see {portion} more "
                f"{tier} {direction} dependents "
                f"(currently capped at {limit})."
            )

    # ── (b) Byte-ceiling hint ─────────────────────────────────────────────────
    if byte_omitted > 0 and byte_capped_clean:
        byte_limit = byte_capped_clean.get("limit", max_bytes)
        if not isinstance(byte_limit, int):
            byte_limit = max_bytes
        hints.append(
            f"Pass max_bytes=0 (currently {byte_limit}) for the full "
            f"untrimmed blast radius ({byte_omitted} "
            f"{'entry' if byte_omitted == 1 else 'entries'} trimmed by byte ceiling)."
        )

    return hints
