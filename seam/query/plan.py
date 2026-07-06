"""Agent change-planning composition.

Owns: turning existing Seam evidence into a bounded inspect-and-test plan.
Does not own: extracting new graph evidence, running tests, mutating git, or
transport-specific path shaping.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, TypedDict

import seam.config as config
from seam.analysis.affected import affected
from seam.analysis.changes import (
    DEFAULT_BASE_REF,
    ChangeReport,
    _get_diff,
    _get_untracked_files,
    _parse_unified_diff,
    detect_changes,
)
from seam.analysis.impact import (
    TIER_LIKELY_AFFECTED,
    TIER_MAY_NEED_TESTING,
    TIER_WILL_BREAK,
    impact,
)
from seam.analysis.testpaths import is_test_file
from seam.query.pack import ContextPack, NeighborRef, context_pack

_TIER_TO_REASON = {
    TIER_WILL_BREAK: "will_break",
    TIER_LIKELY_AFFECTED: "likely_affected",
    TIER_MAY_NEED_TESTING: "may_need_testing",
}
_REASON_RANK = {
    "target": 0,
    "changed_symbol": 1,
    "will_break": 2,
    "direct_caller": 3,
    "direct_callee": 4,
    "likely_affected": 5,
    "may_need_testing": 6,
    "affected_test": 7,
}
_CONFIDENCE_RANK = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}


class EvidenceRef(TypedDict, total=False):
    tool: str
    symbol: str
    file: str | None
    line: int | None
    edge_kind: str | None
    confidence: str | None
    provenance: str | None
    note: str


class InspectionItem(TypedDict):
    symbol: str
    file: str | None
    line: int | None
    kind: str | None
    reasons: list[str]
    tier: str | None
    confidence: str | None
    evidence: list[EvidenceRef]


class TestPlan(TypedDict):
    test_files: list[str]
    commands: list[str]
    partial: bool
    omitted: int


class PlanResult(TypedDict, total=False):
    mode: str
    found: bool
    target: dict[str, Any]
    diff: dict[str, Any]
    risk: dict[str, Any]
    inspection_plan: list[InspectionItem]
    test_plan: TestPlan
    caveats: list[str]
    recommended_next_calls: list[dict[str, Any]]
    omitted: dict[str, int]


def _item_key(
    symbol: str, file: str | None, line: int | None
) -> tuple[str, str | None, int | None]:
    # Impact evidence usually knows symbol+file but not declaration line; context
    # evidence does. Keying by symbol+file lets the two merge instead of creating
    # duplicate rows for the same inspection target.
    return (symbol, file, None)


def _add_item(
    items: dict[tuple[str, str | None, int | None], InspectionItem],
    *,
    symbol: str,
    reason: str,
    file: str | None,
    line: int | None,
    kind: str | None,
    tier: str | None = None,
    confidence: str | None = None,
    evidence: EvidenceRef | None = None,
) -> None:
    key = _item_key(symbol, file, line)
    existing = items.get(key)
    if existing is None:
        existing = {
            "symbol": symbol,
            "file": file,
            "line": line,
            "kind": kind,
            "reasons": [],
            "tier": tier,
            "confidence": confidence,
            "evidence": [],
        }
        items[key] = existing
    elif existing["line"] is None and line is not None:
        existing["line"] = line
    elif existing["kind"] is None and kind is not None:
        existing["kind"] = kind

    if reason not in existing["reasons"]:
        existing["reasons"].append(reason)
        existing["reasons"].sort(key=lambda r: _REASON_RANK.get(r, 99))

    if existing["tier"] is None or _tier_rank(tier) < _tier_rank(existing["tier"]):
        existing["tier"] = tier
    if existing["confidence"] is None or _confidence_rank(confidence) < _confidence_rank(
        existing["confidence"]
    ):
        existing["confidence"] = confidence
    if evidence is not None:
        existing["evidence"].append(evidence)


def _tier_rank(tier: str | None) -> int:
    order = {TIER_WILL_BREAK: 0, TIER_LIKELY_AFFECTED: 1, TIER_MAY_NEED_TESTING: 2}
    return order.get(tier or "", 99)


def _confidence_rank(confidence: str | None) -> int:
    return _CONFIDENCE_RANK.get(confidence or "", 99)


def _sort_items(items: list[InspectionItem]) -> list[InspectionItem]:
    return sorted(
        items,
        key=lambda item: (
            min(_REASON_RANK.get(reason, 99) for reason in item["reasons"]),
            _tier_rank(item["tier"]),
            _confidence_rank(item["confidence"]),
            item["file"] or "",
            item["line"] or 0,
            item["symbol"],
        ),
    )


def _risk_summary(tier_group: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {
        TIER_WILL_BREAK: len(tier_group.get(TIER_WILL_BREAK, [])),
        TIER_LIKELY_AFFECTED: len(tier_group.get(TIER_LIKELY_AFFECTED, [])),
        TIER_MAY_NEED_TESTING: len(tier_group.get(TIER_MAY_NEED_TESTING, [])),
    }


def _risk_level(summary: dict[str, int]) -> str:
    if summary.get(TIER_WILL_BREAK, 0):
        return "critical"
    if summary.get(TIER_LIKELY_AFFECTED, 0):
        return "high"
    if summary.get(TIER_MAY_NEED_TESTING, 0):
        return "medium"
    return "low"


def _test_files_for_symbols(conn: sqlite3.Connection, symbol_names: list[str]) -> list[str]:
    if not symbol_names:
        return []
    placeholders = ",".join("?" for _ in symbol_names)
    rows = conn.execute(
        f"""
        SELECT DISTINCT f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name IN ({placeholders})
        ORDER BY f.path
        """,
        symbol_names,
    ).fetchall()
    return [str(row["path"]) for row in rows if is_test_file(str(row["path"]))]


def _test_plan(test_files: list[str], *, partial: bool = False) -> TestPlan:
    unique = sorted(dict.fromkeys(test_files))
    limit = max(config.SEAM_PLAN_MAX_TEST_FILES, 0)
    if limit and len(unique) > limit:
        shown = unique[:limit]
        omitted = len(unique) - limit
    else:
        shown = unique
        omitted = 0
    return {
        "test_files": shown,
        "commands": [f"pytest {' '.join(shown)}"] if shown else [],
        "partial": partial,
        "omitted": omitted,
    }


def _cap_enriched_neighbors(pack: ContextPack) -> tuple[list[NeighborRef], list[NeighborRef], int]:
    limit = max(config.SEAM_PLAN_MAX_ENRICHED_TARGETS, 0)
    callers = list(pack["callers"])
    callees = list(pack["callees"])
    if not limit:
        return callers, callees, 0

    shown_callers = callers[:limit]
    shown_callees = callees[:limit]
    omitted = max(len(callers) - len(shown_callers), 0) + max(len(callees) - len(shown_callees), 0)
    return shown_callers, shown_callees, omitted


def plan_target(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    max_depth: int = 3,
) -> PlanResult:
    """Build an edit/test plan for a single symbol from current static evidence."""
    raw_pack: ContextPack | None = context_pack(conn, symbol)
    if raw_pack is None:
        return {
            "mode": "target",
            "found": False,
            "target": {"symbol": symbol},
            "risk": {"level": "unknown", "upstream": {}},
            "inspection_plan": [],
            "test_plan": _test_plan([]),
            "caveats": [f"Target symbol {symbol!r} was not found in the index."],
            "recommended_next_calls": [
                {
                    "tool": "seam_search",
                    "reason": "Find the closest indexed symbol name before planning.",
                    "params": {"text": symbol},
                },
                {
                    "tool": "seam_query",
                    "reason": "Search conceptually if the target name is approximate.",
                    "params": {"concept": symbol},
                },
            ],
            "omitted": {"inspection_items": 0, "test_files": 0},
        }

    target = raw_pack["target"]
    items: dict[tuple[str, str | None, int | None], InspectionItem] = {}
    _add_item(
        items,
        symbol=target["symbol"],
        reason="target",
        file=target["file"],
        line=target["line"],
        kind=target["kind"],
        evidence={
            "tool": "seam_context_pack",
            "symbol": target["symbol"],
            "file": target["file"],
            "line": target["line"],
            "note": "Planner target.",
        },
    )

    enriched_callers, enriched_callees, omitted_enriched = _cap_enriched_neighbors(raw_pack)

    for neighbor in enriched_callers:
        _add_item(
            items,
            symbol=neighbor["name"],
            reason="direct_caller",
            file=neighbor["file"],
            line=neighbor["line"],
            kind=neighbor["kind"],
            evidence={
                "tool": "seam_context_pack",
                "symbol": neighbor["name"],
                "file": neighbor["file"],
                "line": neighbor["line"],
                "note": "Direct caller in the context pack.",
            },
        )

    for neighbor in enriched_callees:
        _add_item(
            items,
            symbol=neighbor["name"],
            reason="direct_callee",
            file=neighbor["file"],
            line=neighbor["line"],
            kind=neighbor["kind"],
            evidence={
                "tool": "seam_context_pack",
                "symbol": neighbor["name"],
                "file": neighbor["file"],
                "line": neighbor["line"],
                "note": "Direct callee in the context pack.",
            },
        )

    raw_impact = impact(
        conn,
        target=symbol,
        direction="upstream",
        max_depth=max_depth,
        include_tests=False,
    )
    upstream = raw_impact.get("upstream", {})
    risk_summary = _risk_summary(upstream)
    for tier, entries in upstream.items():
        reason = _TIER_TO_REASON.get(tier, "may_need_testing")
        for entry in entries:
            _add_item(
                items,
                symbol=entry["name"],
                reason=reason,
                file=entry.get("file"),
                line=None,
                kind=None,
                tier=tier,
                confidence=entry.get("confidence"),
                evidence={
                    "tool": "seam_impact",
                    "symbol": entry["name"],
                    "file": entry.get("file"),
                    "line": None,
                    "edge_kind": entry.get("kind"),
                    "confidence": entry.get("confidence"),
                    "provenance": entry.get("synthesized_by"),
                    "note": f"Upstream {tier} dependent.",
                },
            )

    sorted_items = _sort_items(list(items.values()))
    limit = max(config.SEAM_PLAN_MAX_INSPECTION_ITEMS, 0)
    omitted_items = max(len(sorted_items) - limit, 0) if limit else 0
    total_omitted_items = omitted_items + omitted_enriched
    if limit:
        sorted_items = sorted_items[:limit]

    test_files = _test_files_for_symbols(conn, target.get("test_callers", []))
    caveats = [
        "Static analysis only: this plan is not runtime proof and does not run tests.",
        *raw_pack["caveats"],
    ]
    if raw_pack["truncated"]["callers"] or raw_pack["truncated"]["callees"]:
        caveats.append("Context-pack neighbor caps fired; inspect impact for the full graph.")
    if raw_impact.get("hidden_tests", 0):
        caveats.append(
            f"{raw_impact['hidden_tests']} test dependent(s) were hidden from production impact."
        )
    test_plan = _test_plan(test_files)
    if omitted_enriched:
        caveats.append(
            f"{omitted_enriched} enriched context item(s) were omitted by "
            "SEAM_PLAN_MAX_ENRICHED_TARGETS."
        )
    if omitted_items:
        caveats.append(
            f"{omitted_items} inspection item(s) were omitted by SEAM_PLAN_MAX_INSPECTION_ITEMS."
        )
    if test_plan["omitted"]:
        caveats.append(f"{test_plan['omitted']} test file(s) were omitted by SEAM_PLAN_MAX_TEST_FILES.")

    return {
        "mode": "target",
        "found": bool(raw_impact.get("found", True)),
        "target": {
            "symbol": target["symbol"],
            "file": target["file"],
            "line": target["line"],
            "kind": target["kind"],
            "ambiguous": target["ambiguous"],
        },
        "risk": {
            "level": _risk_level(risk_summary),
            "upstream": risk_summary,
            "hidden_tests": raw_impact.get("hidden_tests", 0),
        },
        "inspection_plan": sorted_items,
        "test_plan": test_plan,
        "caveats": caveats,
        "recommended_next_calls": [
            {
                "tool": "seam_impact",
                "reason": "Expand the full upstream blast radius if the plan is capped.",
                "params": {"target": symbol, "direction": "upstream", "include_tests": True},
            },
            {
                "tool": "seam_snippet",
                "reason": "Read exact source for any inspection item before editing.",
                "params": {"symbol": "<inspection_plan.symbol>"},
            },
        ],
        "omitted": {
            "inspection_items": total_omitted_items,
            "test_files": test_plan["omitted"],
        },
    }


def plan_diff(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    scope: str = "working",
    base_ref: str = DEFAULT_BASE_REF,
    affected_depth: int = config.SEAM_AFFECTED_DEPTH,
) -> PlanResult:
    """Build an inspect/test plan for the current git diff."""
    report: ChangeReport = detect_changes(
        conn,
        base_ref=base_ref,
        scope=scope,
        repo_root=repo_root,
    )
    changed_files = _all_diff_files(repo_root.resolve(), scope=scope, base_ref=base_ref)
    if not changed_files:
        changed_files = _changed_files_from_report(report)
    affected_result = affected(
        conn,
        changed_files,
        depth=affected_depth,
        repo_root=repo_root,
    )

    items: dict[tuple[str, str | None, int | None], InspectionItem] = {}
    for changed in report["changed_symbols"]:
        _add_item(
            items,
            symbol=changed["name"],
            reason="changed_symbol",
            file=changed["file"],
            line=changed["start_line"] or None,
            kind=changed["kind"],
            evidence={
                "tool": "seam_changes",
                "symbol": changed["name"],
                "file": changed["file"],
                "line": changed["start_line"] or None,
                "note": "Changed line range overlaps this symbol.",
            },
        )

    affected_summary = {TIER_WILL_BREAK: 0, TIER_LIKELY_AFFECTED: 0, TIER_MAY_NEED_TESTING: 0}
    for entry in report["affected"]:
        tier = entry["tier"]
        if tier in affected_summary:
            affected_summary[tier] += 1
        _add_item(
            items,
            symbol=entry["name"],
            reason=_TIER_TO_REASON.get(tier, "may_need_testing"),
            file=entry.get("file"),
            line=None,
            kind=None,
            tier=tier,
            confidence=entry.get("confidence"),
            evidence={
                "tool": "seam_changes",
                "symbol": entry["name"],
                "file": entry.get("file"),
                "line": None,
                "confidence": entry.get("confidence"),
                "note": f"Dependent reached from changed symbol at distance {entry['distance']}.",
            },
        )

    sorted_items = _sort_items(list(items.values()))
    limit = max(config.SEAM_PLAN_MAX_INSPECTION_ITEMS, 0)
    omitted_items = max(len(sorted_items) - limit, 0) if limit else 0
    if limit:
        sorted_items = sorted_items[:limit]

    caveats = ["Static analysis only: this plan is not runtime proof and does not run tests."]
    if report["partial"]:
        caveats.append(
            "Change risk is partial; changed symbol cap was hit, so risk is a lower bound."
        )
    if affected_result["partial"]:
        caveats.append("Affected tests are partial; per-file symbol cap was hit.")
    if report["ambiguous_warning"]:
        caveats.append("Some affected symbols were reached through ambiguous evidence.")
    if not report["changed_symbols"] and not report["new_files"]:
        caveats.append("No git changes were detected for this scope.")
    test_plan = _test_plan(
        affected_result["affected_tests"],
        partial=affected_result["partial"],
    )
    if omitted_items:
        caveats.append(
            f"{omitted_items} inspection item(s) were omitted by SEAM_PLAN_MAX_INSPECTION_ITEMS."
        )
    if test_plan["omitted"]:
        caveats.append(f"{test_plan['omitted']} test file(s) were omitted by SEAM_PLAN_MAX_TEST_FILES.")

    return {
        "mode": "diff",
        "found": True,
        "diff": {
            "scope": report["scope"],
            "base_ref": report["base_ref"],
            "changed_symbols": report["changed_symbols"],
            "new_files": report["new_files"],
            "partial": report["partial"],
            "ambiguous_warning": report["ambiguous_warning"],
        },
        "risk": {
            "level": report["risk_level"],
            "upstream": affected_summary,
        },
        "inspection_plan": sorted_items,
        "test_plan": test_plan,
        "caveats": caveats,
        "recommended_next_calls": [
            {
                "tool": "seam_changes",
                "reason": "Inspect the raw change-risk report for all changed symbols.",
                "params": {"scope": scope, "base_ref": base_ref},
            },
            {
                "tool": "seam_affected",
                "reason": "Recompute affected tests if you want a different traversal depth.",
                "params": {"changed_files": "<diff.changed_files>"},
            },
        ],
        "omitted": {
            "inspection_items": omitted_items,
            "test_files": test_plan["omitted"],
        },
    }


def _changed_files_from_report(report: ChangeReport) -> list[str]:
    files: list[str] = []
    for changed in report["changed_symbols"]:
        file = changed["file"]
        if file not in files:
            files.append(file)
    for file in report["new_files"]:
        if file not in files:
            files.append(file)
    return files


def _all_diff_files(repo_root: Path, *, scope: str, base_ref: str) -> list[str]:
    diff_text = _get_diff(scope, base_ref, repo_root)
    files = {str(repo_root / fd.path) for fd in _parse_unified_diff(diff_text)}
    if scope == "working":
        files.update(_get_untracked_files(repo_root))
    return sorted(files)
