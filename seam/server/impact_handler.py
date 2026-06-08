"""Impact-shaping machinery and handle_seam_impact handler.

Extracted from seam/server/tools.py (Slice 2, P2 #103) as a pure mechanical split.
No logic change — byte-identical output before and after the extraction.

Contains:
  - _serialize_tier_entry   (used by tests directly)
  - _prioritize_tier_entries (used by tests directly)
  - _compute_self_context
  - _shape_tier_group
  - _BYTE_CEILING_TRUNCATED_RESERVE, _count_direction_entries, _apply_byte_ceiling
  - handle_seam_impact
  - _STEER_RESERVE_MARGIN, _attach_steer

Import dependency: impact_handler → handler_common (one direction only, no cycle).
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

import seam.config as config
from seam.analysis import impact as impact_module
from seam.analysis.byte_budget import fit_to_byte_budget, serialized_size
from seam.analysis.relevance import order_by_relevance, owning_container, partition_self_refs
from seam.analysis.steer import generate_steer
from seam.query.names import get_member_names, is_container_symbol
from seam.server.handler_common import (
    _apply_verbosity,
    _invalid_input,
    _maybe_attach_staleness,
    _relativize,
    _resolve_uid,
)

logger = logging.getLogger(__name__)

# ── Tier entry serialization ──────────────────────────────────────────────────


def _serialize_tier_entry(
    entry: dict[str, Any],
    root: Path,
    verbose: bool,
    omit_null_candidate: bool = False,
) -> dict[str, Any]:
    """Serialize a single TieredEntry dict from the analysis layer.

    Relativizes file paths, includes Phase 5 provenance fields, and applies
    verbosity stripping. Extracted to keep the main handler readable.

    E1: when omit_null_candidate is True, the `best_candidate` key is DROPPED
    when its value is null. best_candidate is only meaningful for AMBIGUOUS
    entries; for EXTRACTED/INFERRED it is always null and carries no signal, so
    omitting it is lossless (null ≡ absent) and reclaims ~25 B/entry. In lean
    mode (_apply_verbosity already stripped it) this is a no-op.

    E4: when SEAM_EDGE_PROVENANCE=on, emits:
      - 'kind': the edge kind of the final hop (always present, NOT in _HEAVY_FIELDS
        because it is a core field kept in lean mode — like 'confidence').
      - 'synthesized_by': synthesis channel name when heuristic, null for static.
        In _HEAVY_FIELDS → stripped in lean mode (verbose=False), just like resolved_by.
        IMPORTANT: null is RETAINED in verbose mode (unlike best_candidate which is
        E1-omitted). For synthesized_by, null = "static edge", which is the common,
        informative case and must not be dropped.
    When SEAM_EDGE_PROVENANCE=off, neither 'kind' nor 'synthesized_by' is emitted →
    byte-identical pre-E4 output.
    """
    base: dict[str, Any] = {
        "name": entry["name"],
        "distance": entry["distance"],
        "confidence": entry["confidence"],
        # Phase 5: resolved_by carries import-promotion provenance.
        # null when name-count fast-path was used (repo_root absent or "off").
        "resolved_by": entry.get("resolved_by"),
        "tier": entry["tier"],
        "file": _relativize(entry["file"], root) if entry["file"] is not None else None,
        "is_test": entry["is_test"],
        # Phase 5: best_candidate surfaces the most-proximate declaring
        # file for AMBIGUOUS entries (PRD story 6). Null for non-AMBIGUOUS or
        # when proximity data was unavailable. Relativized like other file paths.
        "best_candidate": (
            _relativize(entry["best_candidate"], root)
            if entry.get("best_candidate") is not None
            else None
        ),
    }

    # E4: emit edge provenance fields when the knob is on.
    # 'kind' is always kept (not in _HEAVY_FIELDS); 'synthesized_by' is in
    # _HEAVY_FIELDS and gets stripped by _apply_verbosity when verbose=False.
    if config.SEAM_EDGE_PROVENANCE == "on":
        base["kind"] = entry.get("kind", "")  # defensive: empty string for pre-E4 entries
        base["synthesized_by"] = entry.get("synthesized_by")  # null = static, retained

    record = _apply_verbosity(base, verbose)
    if omit_null_candidate and record.get("best_candidate") is None:
        record.pop("best_candidate", None)
    return record


def _prioritize_tier_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order production (is_test=False) entries before test entries within a tier.

    Stable sort: preserves the analysis layer's BFS/distance order WITHIN the
    production and test groups (Python's sort is stable). Applied BEFORE the
    per-tier cap so that when the cap drops entries, production callers — what an
    agent assessing blast radius actually cares about — survive ahead of test
    dependents. WHY this matters: in a test-heavy repo a hub symbol's tier can be
    dominated by test callers (e.g. rescore had 52 test vs 9 production callers in
    LIKELY_AFFECTED), pushing the production callers past the cap of 25 and out of
    the default output entirely. Token budget is unchanged (still <= limit/tier).
    """
    return sorted(entries, key=lambda e: e.get("is_test", False))


def _compute_self_context(
    conn: sqlite3.Connection,
    target: str,
) -> tuple[str | None, set[str]]:
    """Resolve the target's container and own member-name set for self-ref ranking.

    Returns (container, self_names) where:
      - container  is the class/struct the target belongs to (the target itself when
        it IS a container, or its owning container when it's a method like "Foo.bar").
        None when the target is a free function / bare name with no container — such a
        target has no self-references and ordering falls back to production-before-test.
      - self_names is {container} ∪ {bare member names}. The owning_container() check in
        classify_self_ref handles qualified member entries ("Foo.bar"); self_names
        carries the container name and the BARE member entries ("bar") that
        owning_container() cannot resolve.

    WHY resolve the container even for a method target: querying impact on a single
    method "Foo.bar" should still surface EXTERNAL callers ahead of "Foo"'s other
    methods — those siblings live in the same file the developer is already editing,
    so they are low-signal self-references just like in the class-level case.

    Never raises (delegates to names.py helpers, which never raise).
    """
    if is_container_symbol(conn, target):
        container: str | None = target
    else:
        # Method ("Foo.bar") -> "Foo"; bare function ("run") -> None (no container).
        container = owning_container(target)

    if container is None:
        return None, set()

    members = get_member_names(conn, container)  # bare names, capped by config
    self_names = {container, *members}
    return container, self_names


def _shape_tier_group(
    tier_group: dict[str, list[dict[str, Any]]],
    root: Path,
    *,
    verbose: bool,
    effective_limit: int | None,
    relevance_on: bool,
    self_ref_mode: str,
    container: str | None,
    self_names: set[str],
    omit_null_candidate: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], int]:
    """Order, cap, and serialize one direction's tier group (E2/E3 output shaping).

    Returns (capped_tiers, dir_truncated, dir_hidden_self_refs):
      - capped_tiers        — {tier: [serialized entries]} after ordering + cap.
      - dir_truncated       — {tier: count omitted by the per-tier cap}.
      - dir_hidden_self_refs — count of self-refs dropped in "hide" mode (else 0).

    Ordering runs BEFORE the cap so the cap sheds the lowest-relevance entries first.
    The analysis layer's ascending-distance order is preserved within each relevance
    group by the stable sort, so entries[:N] keeps the closest, highest-signal dependents.
    """
    capped_tiers: dict[str, list[dict[str, Any]]] = {}
    dir_truncated: dict[str, int] = {}
    dir_hidden_self_refs = 0

    for tier, entries in tier_group.items():
        if not relevance_on:
            # Relevance off: byte-identical revert to production-before-test.
            entries = _prioritize_tier_entries(entries)
        elif self_ref_mode == "hide":
            # Drop the target's own members entirely; count them; order the remaining
            # externals production-before-test. risk_summary (counted by the caller
            # before this) still includes self-refs, so the blast radius stays honest —
            # the dropped members surface as hidden_self_refs.
            external, self_refs = partition_self_refs(entries, container, self_names)
            dir_hidden_self_refs += len(self_refs)
            entries = order_by_relevance(external, container, self_names)
        else:
            # "rank" (default) or "show": keep everything, externals/production first
            # and self-references last (so the cap sheds them first).
            entries = order_by_relevance(entries, container, self_names)

        if effective_limit is not None and len(entries) > effective_limit:
            kept = entries[:effective_limit]
            dir_truncated[tier] = len(entries) - effective_limit
        else:
            kept = entries
            dir_truncated[tier] = 0

        # Serialize each kept entry: relativize paths + apply verbose stripping +
        # E1 null-best_candidate omission.
        capped_tiers[tier] = [
            _serialize_tier_entry(entry, root, verbose, omit_null_candidate) for entry in kept
        ]

    return capped_tiers, dir_truncated, dir_hidden_self_refs


# Worst-case size (chars) of the trailing `truncated` structure the byte pass can add
# on top of what the count cap already wrote: 6 (direction × tier) slots in the CLI emit
# serialization. Reserved (with the exact byte_capped size) so the FINAL response —
# entries PLUS the trailing byte_capped/truncated metadata — still fits the budget,
# making the ceiling a hard guarantee rather than entries-only.
_BYTE_CEILING_TRUNCATED_RESERVE = 200


def _count_direction_entries(response: dict[str, Any]) -> int:
    """Total entries across all direction-tier lists — the upper bound on `omitted`."""
    total = 0
    for direction in ("upstream", "downstream"):
        dir_group = response.get(direction)
        if isinstance(dir_group, dict):
            for tier_val in dir_group.values():
                if isinstance(tier_val, list):
                    total += len(tier_val)
    return total


def _apply_byte_ceiling(
    response: dict[str, Any], budget: int, *, extra_reserve: int = 0
) -> dict[str, Any]:
    """Apply the E1-FULL byte ceiling to a fully-assembled seam_impact response.

    Runs AFTER the per-tier count cap and E2/E3 relevance ordering. When budget > 0 and
    the response does not already fit, trims entries (via fit_to_byte_budget) from the
    least-valuable end, merges the byte-dropped counts into response["truncated"]
    additively (so risk_summary - shown == truncated holds end-to-end), and sets
    response["byte_capped"] = {"limit", "omitted"}.

    Hard-ceiling guarantee: the trim runs against `budget - reserve`, where `reserve`
    is the exact byte_capped size plus a worst-case allowance for the `truncated` growth
    this function appends afterwards — so the FINAL serialized response (entries + that
    trailing metadata) stays within `budget`. The only exception is a `budget` smaller
    than the irreducible envelope, where no entries fit at all.

    When budget <= 0: returns the response unchanged (byte-identical revert).
    When the response already fits: returns it unchanged, byte_capped NOT added (so a
    generous budget is a true no-op).

    Never raises — the whole body is guarded; on any failure the untrimmed response is
    returned. If the trim degrades to a no-op despite the response NOT fitting (the leaf's
    never-raises safety net fired), that silent path is logged so it is observable.

    Args:
        response: Fully-assembled seam_impact response dict (non-mutating).
        budget:   SEAM_IMPACT_MAX_BYTES (from param). 0 or negative = unlimited.
        extra_reserve: Additional bytes to hold back from the trim budget (E4). The
                  handler passes the serialized size of the `next_actions` steer here so
                  the FINAL response — entries + byte_capped/truncated + next_actions —
                  still fits `budget`. byte_capped["limit"] keeps reporting the true
                  `budget` (not the reduced trim budget), so the reported ceiling is honest.

    Returns:
        The (possibly trimmed) response dict with merged truncated + byte_capped.
    """
    if budget <= 0:
        return response

    try:
        # Already within budget (including the steer the handler will append) → no trim.
        if serialized_size(response) + extra_reserve <= budget:
            return response

        # Reserve room for the trailing metadata the merge appends, so the final
        # response still fits. byte_capped is sized exactly (omitted <= entry count);
        # truncated growth uses a worst-case 6-slot allowance. extra_reserve (E4) holds
        # back room for the next_actions steer the handler appends after this returns.
        total_entries = _count_direction_entries(response)
        reserve = serialized_size({"byte_capped": {"limit": budget, "omitted": total_entries}})
        reserve += _BYTE_CEILING_TRUNCATED_RESERVE + max(extra_reserve, 0)
        effective = max(budget - reserve, 1)

        trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=effective)

        if total_omitted == 0:
            # The response did NOT fit (checked above) yet nothing was trimmed — the
            # leaf's never-raises safety net fired. Surface it and return untrimmed
            # rather than attach a misleading byte_capped that claims a trim happened.
            logger.warning(
                "seam_impact byte ceiling could not trim output (budget=%d); returning untrimmed",
                budget,
            )
            return response

        # Merge byte_dropped into truncated ADDITIVELY so the invariant holds:
        #   risk_summary[dir][tier] - shown[dir][tier] == truncated[dir][tier]
        existing_truncated: dict[str, dict[str, int]] = dict(trimmed.get("truncated", {}))
        for direction, tier_map in byte_dropped.items():
            dir_trunc = dict(existing_truncated.get(direction, {}))
            for tier, count in tier_map.items():
                dir_trunc[tier] = dir_trunc.get(tier, 0) + count
            existing_truncated[direction] = dir_trunc

        # trimmed is a new dict from fit_to_byte_budget (never mutates input).
        result = dict(trimmed)
        result["truncated"] = existing_truncated
        result["byte_capped"] = {"limit": budget, "omitted": total_omitted}
        return result
    except Exception:
        # The handler claims "never raises" in its own right (not only via the leaf).
        logger.warning("seam_impact byte ceiling failed; returning untrimmed output", exc_info=True)
        return response


# Impact limit defaults (mirrored here for the handler's default parameter).
_IMPACT_DEPTH_DEFAULT = 3
_IMPACT_DIRECTION_DEFAULT = "upstream"


def handle_seam_impact(
    conn: sqlite3.Connection,
    target: str,
    root: Path,
    direction: str = _IMPACT_DIRECTION_DEFAULT,
    max_depth: int = _IMPACT_DEPTH_DEFAULT,
    include_tests: bool = False,
    verbose: bool = True,
    limit: int = config.SEAM_IMPACT_MAX_RESULTS,
    max_bytes: int = config.SEAM_IMPACT_MAX_BYTES,
    *,
    uid: str | None = None,
) -> dict[str, Any]:
    """Handler for the seam_impact MCP tool.

    Computes blast radius for a target symbol: which symbols are affected if the
    target changes, grouped into risk tiers by distance.

    Args:
        conn:          Open SQLite connection.
        target:        Symbol name to analyze (must not be blank/whitespace).
        root:          Project root for path relativization. Each TieredEntry includes a
                       `file` field (absolute path from the analysis layer) which is
                       relativized to root before returning.
        direction:     "upstream" | "downstream" | "both". Default: "upstream".
        max_depth:     Max hops. Clamped to [1, 10]. Default: 3.
        include_tests: When False (default), test-file dependents are filtered out from
                       all tiers — "what breaks?" answers with the PRODUCTION blast radius,
                       and the count of hidden test dependents surfaces as `hidden_tests`.
                       When True, test-file dependents are included and tagged is_test=True.
                       (Test dependents are derivable separately via seam_affected.)
        verbose:       When True (default), output includes all Phase 4/5 enrichment fields.
                       When False, heavy fields (resolved_by, best_candidate, etc.) are
                       stripped from each entry — lean mode.
        limit:         Per-tier entry cap. Default: SEAM_IMPACT_MAX_RESULTS (25).
                       Entries arrive distance-ordered from the analysis layer (tiers group
                       by distance), so the kept slice is always the closest/highest-risk.
                       limit <= 0 means unlimited (all entries returned).
        max_bytes:     Optional byte ceiling for the serialized output (characters of compact
                       JSON). Default: SEAM_IMPACT_MAX_BYTES (0 = unlimited). When > 0, the
                       ceiling runs AFTER the per-tier count cap and E2/E3 ordering, trimming
                       entries from the least-valuable end (downstream before upstream,
                       MAY_NEED_TESTING before WILL_BREAK, tail before front) until the
                       serialized output fits. The dropped counts are merged into `truncated`
                       additively and a `byte_capped` key is added when the ceiling fired
                       (byte_capped is ABSENT when max_bytes=0 or nothing was trimmed). 0 or
                       negative means unlimited — byte-identical to the pre-feature output.

    Returns:
        A JSON-able dict with the impact result, or an error dict on bad input.
        Top-level keys always include `found`, `target`, and `risk_summary`.
        risk_summary is {direction: {tier: count}} computed from the FULL pre-cap
        result — it is always trustworthy even when entry lists are capped.
        NOTE: "full" means before the `limit` cap, but AFTER the include_tests filter —
        when include_tests=False, risk_summary counts the production-only blast radius
        (test dependents are already excluded), matching the entries actually returned.
        When any tier was capped, `truncated` is included: {direction: {tier: omitted}}.
        When the byte ceiling fires, `byte_capped` is added: {"limit": int, "omitted": int}.

        Shape for direction="upstream":
            {"found": bool, "target": str, "risk_summary": {...},
             "upstream": {"WILL_BREAK": [...], "LIKELY_AFFECTED": [...], "MAY_NEED_TESTING": [...]}}
        Shape for direction="both":
            {"found": bool, "target": str, "risk_summary": {...},
             "upstream": {...tiers...}, "downstream": {...tiers...}}

        Each entry in a tier list includes:
            file    (str | None) — relative path from project root; None for unindexed.
            is_test (bool)       — True if the entry's file is a test file.

    Error shapes:
        {"error": "INVALID_INPUT", "message": "..."} — blank target or invalid direction.
    """
    # uid (P6c): a stable handle pins the exact symbol. The impact graph is
    # name-keyed (edges store names), so we resolve the uid to its symbol NAME and
    # analyze that — the handle just removes the homonym disambiguation round-trip.
    # An unknown uid returns the standard found=False result (not an error).
    if uid is not None:
        resolved = _resolve_uid(conn, uid)
        if resolved is None:
            return {"found": False, "target": uid, "risk_summary": {}}
        target = resolved[0]

    # Validate: target must not be empty or whitespace-only.
    if not target or not target.strip():
        return _invalid_input("target must not be empty or whitespace-only")

    # Validate direction before passing to impact (impact raises ValueError on bad direction,
    # but we want the standard INVALID_INPUT shape here in the handler).
    valid_directions = {"upstream", "downstream", "both"}
    if direction not in valid_directions:
        return _invalid_input(
            f"direction must be one of: {sorted(valid_directions)}; got {direction!r}"
        )

    # Clamp max_depth via impact module's own clamp helper (single source of truth).
    safe_depth = impact_module.clamp_depth(max_depth)

    raw = impact_module.impact(
        conn,
        target=target.strip(),
        direction=direction,
        max_depth=safe_depth,
        include_tests=include_tests,
        # Thread repo_root for Phase 5 import-promotion (root is already the project root).
        repo_root=root,
    )

    # Build the response: pass found/target through, relativize file paths in entries.
    response: dict[str, Any] = {
        "found": raw["found"],
        "target": raw["target"],
    }

    # Determine whether capping is active (limit <= 0 means unlimited).
    effective_limit = limit if limit > 0 else None

    # E2/E3 output shaping (handler-layer only — seam_changes/seam_affected bypass this).
    # relevance_on ranks EXTERNAL dependents ahead of the target's own members so the
    # per-tier cap drops self-references first. self_ref_mode "hide" additionally drops
    # self-refs entirely and surfaces hidden_self_refs (mirrors hidden_tests).
    relevance_on = config.SEAM_IMPACT_RELEVANCE_SORT == "on"
    self_ref_mode = config.SEAM_IMPACT_SELF_REF
    # E1: drop null best_candidate per entry (lossless; null ≡ absent) to keep the
    # default output lean so more high-signal dependents survive the per-tier cap.
    omit_null_candidate = config.SEAM_IMPACT_OMIT_NULL_CANDIDATE == "on"
    # Resolve the self-ref context only when it can actually change ordering — i.e.
    # relevance is on and the mode treats self-refs specially ("rank"/"hide"). "show"
    # and relevance-off skip the lookup (container=None → no entry is a self-ref).
    if relevance_on and self_ref_mode in ("rank", "hide"):
        container, self_names = _compute_self_context(conn, target.strip())
    else:
        container, self_names = None, set()
    hidden_self_refs = 0

    # Build risk_summary and capped tiers for each direction key present in raw.
    # WHY compute summary first: risk_summary must reflect the FULL pre-cap result
    # (story 15) — we count before slicing so truncation cannot hide the true total.
    # In "hide" mode the summary still counts self-refs (the honest total); the dropped
    # self-refs surface separately as hidden_self_refs.
    risk_summary: dict[str, dict[str, int]] = {}
    truncated: dict[str, dict[str, int]] = {}

    for dir_key in ("upstream", "downstream"):
        if dir_key not in raw:
            continue
        tier_group = raw[dir_key]

        # ── 1. Count BEFORE capping (risk_summary denominator) ────────────────
        # Counts the FULL pre-cap tier group including self-refs (the honest total).
        dir_summary = {tier: len(entries) for tier, entries in tier_group.items()}
        risk_summary[dir_key] = dir_summary

        # ── 2. Order (E2/E3) + per-tier cap + serialize ───────────────────────
        capped_tiers, dir_truncated, dir_hidden = _shape_tier_group(
            tier_group,
            root,
            verbose=verbose,
            effective_limit=effective_limit,
            relevance_on=relevance_on,
            self_ref_mode=self_ref_mode,
            container=container,
            self_names=self_names,
            omit_null_candidate=omit_null_candidate,
        )
        hidden_self_refs += dir_hidden
        response[dir_key] = capped_tiers

        # Only include truncated for directions where something was actually dropped.
        if any(count > 0 for count in dir_truncated.values()):
            truncated[dir_key] = dir_truncated

    # risk_summary is always present — it is the honest summary of the full blast radius.
    response["risk_summary"] = risk_summary

    # truncated is only present when at least one tier was capped in any direction.
    # Absence signals "nothing was dropped" (omitted vs all-zero to reduce token cost).
    if truncated:
        response["truncated"] = truncated

    # Surface hidden_tests when present (include_tests=False filtered test dependents).
    # Lets MCP callers distinguish "no dependents" from "all dependents were tests and
    # were hidden" — without it, the production-only default could read as a false-safe.
    if "hidden_tests" in raw:
        response["hidden_tests"] = raw["hidden_tests"]

    # Surface hidden_self_refs whenever hide mode is active (even when 0), so agents
    # can rely on its presence to reconcile risk_summary against the shown entries.
    if relevance_on and self_ref_mode == "hide":
        response["hidden_self_refs"] = hidden_self_refs

    # E1-FULL: byte ceiling — runs LAST (before steer), after count cap + E2/E3 ordering.
    # When max_bytes > 0, trims entries from the least-valuable end until the
    # serialized output fits the budget. byte_capped is set only when the ceiling
    # actually fired (i.e. at least one entry was dropped). When max_bytes <= 0
    # this is a no-op (byte-identical revert). seam_changes/seam_affected bypass
    # this entirely because they call the analysis layer directly.
    final_response = _apply_byte_ceiling(response, max_bytes)

    # E4: truncation steer — runs AFTER byte ceiling so it reads the merged truncated
    # totals (count-cap drops + byte-ceiling drops) and the byte_capped metadata.
    # Generates ready-to-act prose hints when ≥1 entry was trimmed. ABSENT when
    # nothing was trimmed (so presence is an unambiguous "there is more" signal).
    # Gated by SEAM_IMPACT_STEER; "off" = byte-identical pre-E4 (no next_actions key).
    # `response` (pre-ceiling) is passed so the steer-aware re-trim starts clean rather
    # than re-trimming an already-trimmed response (which would double-count truncated).
    if config.SEAM_IMPACT_STEER == "on":
        final_response = _attach_steer(
            final_response, response, limit=limit, max_bytes=max_bytes
        )

    # P2: attach staleness banner LAST — purely additive, byte-identical when fresh.
    return _maybe_attach_staleness(final_response, conn, root)


# Small margin (chars) added to the steer-byte reserve when re-trimming so the
# regenerated steer's digit growth (byte-drop counts grow as more entries are trimmed)
# cannot nudge the response back over the budget.
_STEER_RESERVE_MARGIN = 64


def _attach_steer(
    final_response: dict[str, Any],
    pre_ceiling_response: dict[str, Any],
    *,
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Generate the E4 next_actions steer and attach it WITHIN the byte ceiling (E4 fix).

    The steer is generated from the post-ceiling trim metadata. Naively appending it would
    push the response past max_bytes — defeating the E1-FULL hard ceiling exactly when the
    ceiling fired (the steer fires iff something was trimmed). So when max_bytes is active
    and attaching the steer would breach the budget, we re-run the ceiling from the
    PRE-CEILING response (clean — not the already-trimmed one, which would double-count
    `truncated`), reserving room for the steer, then regenerate it for the smaller set.

    WHY a single re-trim converges: the steer's count-cap hints depend only on the
    count-cap portion of `truncated` (applied before the ceiling), which is INVARIANT
    under further byte trimming. So the regenerated steer differs from the first only in
    the byte-hint's trailing count — a few digits — absorbed by _STEER_RESERVE_MARGIN.
    No iteration loop needed.

    All-trimmed (budget-below-envelope) is the documented exception: entries are already
    empty, re-trimming changes nothing, and the anti-false-safe WARNING is the point — it
    is attached even if it exceeds a sub-envelope budget (the same carve-out the
    irreducible envelope already has).

    tier_order / direction_order are injected from impact.py's canonical TIER_* constants
    so the steer has a single source of truth for the tier names (no hardcoded copy).
    """
    tier_order = (
        impact_module.TIER_WILL_BREAK,
        impact_module.TIER_LIKELY_AFFECTED,
        impact_module.TIER_MAY_NEED_TESTING,
    )

    def _make_steer(resp: dict[str, Any]) -> list[str]:
        return generate_steer(
            truncated=resp.get("truncated", {}),
            byte_capped=resp.get("byte_capped"),
            risk_summary=resp.get("risk_summary", {}),
            limit=limit,
            max_bytes=max_bytes,
            tier_order=tier_order,
            direction_order=("upstream", "downstream"),
        )

    steer = _make_steer(final_response)
    if not steer:
        return final_response

    # Keep the steer inside the byte budget (E4 STOP fix). Only re-trim when the budget
    # is active AND attaching the steer would actually breach it.
    if max_bytes > 0:
        steer_bytes = serialized_size({"next_actions": steer})
        if serialized_size(final_response) + steer_bytes > max_bytes:
            # Re-trim from the PRE-ceiling response (clean single pass) reserving room
            # for the steer, then regenerate the steer for the now-smaller entry set.
            final_response = _apply_byte_ceiling(
                pre_ceiling_response,
                max_bytes,
                extra_reserve=steer_bytes + _STEER_RESERVE_MARGIN,
            )
            steer = _make_steer(final_response)
            if not steer:
                return final_response

    final_response["next_actions"] = steer
    return final_response
