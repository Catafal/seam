"""Deterministic answerability benchmark primitives.

LAYER: eval support. The scoring core is DB-free and LLM-free; concrete adapters
are isolated here so reports can exercise real Seam read handlers when needed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

VALID_CATEGORIES = {
    "architecture",
    "change-safety",
    "cleanup-risk",
    "discovery",
    "docs",
    "infra",
    "navigation",
    "protocol",
}


class ScenarioValidationError(ValueError):
    """Raised when a scenario catalog cannot be trusted as benchmark input."""


@dataclass(frozen=True)
class ExpectedItem:
    kind: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"


@dataclass(frozen=True)
class ToolStep:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    question: str
    target: dict[str, Any]
    expected_facts: list[ExpectedItem]
    required_evidence: list[ExpectedItem]
    acceptable_caveats: list[str]
    tool_plan: list[ToolStep]
    fallback_plan: list[ToolStep]
    capability_tags: list[str]
    product_gap_tags: list[str]
    failure_gap_tags: list[str]
    roadmap_pressure_tags: list[str]
    regression_tags: list[str]
    max_estimated_tokens: int


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"


@dataclass(frozen=True)
class ToolStepResult:
    tool: str
    args: dict[str, Any]
    ok: bool
    payload: Any
    elapsed_ms: float
    byte_count: int
    estimated_tokens: int
    warnings: list[dict[str, Any]]
    caveats: list[str]
    error: str | None = None

    @classmethod
    def from_payload(
        cls,
        tool: str,
        args: dict[str, Any],
        payload: Any,
        *,
        elapsed_ms: float,
        ok: bool = True,
        error: str | None = None,
    ) -> ToolStepResult:
        raw = json.dumps(payload, sort_keys=True, default=str)
        warnings = _extract_named_dicts(payload, "warnings")
        caveats = _extract_strings(payload, "caveats")
        return cls(
            tool=tool,
            args=dict(args),
            ok=ok,
            payload=payload,
            elapsed_ms=elapsed_ms,
            byte_count=len(raw.encode("utf-8")),
            estimated_tokens=max(1, math.ceil(len(raw) / 4)),
            warnings=warnings,
            caveats=caveats,
            error=error,
        )


@dataclass(frozen=True)
class ScenarioScore:
    scenario_id: str
    category: str
    status: str
    scores: dict[str, int]
    missing_facts: list[str]
    missing_evidence: list[str]
    product_gaps: list[str]
    failure_gap_tags: list[str]
    roadmap_pressure_tags: list[str]
    regression_tags: list[str]
    notes: list[str]
    round_trips: int
    byte_count: int
    estimated_tokens: int
    elapsed_ms: float


class ToolAdapter(Protocol):
    def execute(self, tool: str, args: dict[str, Any], fixture_dir: Path) -> ToolStepResult: ...


class SeamFixtureAdapter:
    """Executes answerability tool plans against the deterministic eval fixture."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, tool: str, args: dict[str, Any], fixture_dir: Path) -> ToolStepResult:
        started = time.perf_counter()
        try:
            payload = self._execute_payload(tool, args, fixture_dir)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - started) * 1000
            return ToolStepResult.from_payload(
                tool,
                args,
                {"error": str(exc)},
                elapsed_ms=elapsed_ms,
                ok=False,
                error=str(exc),
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return ToolStepResult.from_payload(tool, args, payload, elapsed_ms=elapsed_ms)

    def _execute_payload(self, tool: str, args: dict[str, Any], fixture_dir: Path) -> Any:
        from seam.server.impact_handler import handle_seam_impact
        from seam.server.tools import (
            handle_seam_context,
            handle_seam_context_pack,
            handle_seam_graph_search,
            handle_seam_grounding,
            handle_seam_plan,
            handle_seam_query,
            handle_seam_schema,
            handle_seam_search,
            handle_seam_suspects,
        )
        from seam.server.trace_handler import handle_seam_trace

        if tool == "schema":
            return handle_seam_schema(self._conn, fixture_dir, verbose=False)
        if tool == "schema_capability":
            schema = handle_seam_schema(self._conn, fixture_dir, verbose=False)
            capability = str(args["capability"])
            enabled = bool(schema.get("capabilities", {}).get(capability))
            payload: dict[str, Any] = {
                "capability": f"{capability}:{str(enabled).lower()}",
                "caveats": [f"{capability} is unsupported or empty in this fixture."]
                if not enabled
                else [],
                "warnings": [{"code": "UNSUPPORTED", "capability": capability}]
                if not enabled
                else [],
            }
            return payload
        if tool == "search":
            return handle_seam_search(
                self._conn,
                str(args["query"]),
                fixture_dir,
                limit=int(args.get("limit", 10)),
                semantic=False,
            )
        if tool == "query":
            return handle_seam_query(
                self._conn,
                str(args["query"]),
                fixture_dir,
                limit=int(args.get("limit", 10)),
                semantic=False,
            )
        if tool in {
            "context_callers",
            "context_callees",
            "context_field_readers",
            "context_field_writers",
        }:
            ctx = handle_seam_context(self._conn, str(args["symbol"]), fixture_dir)
            field = {
                "context_callers": "callers",
                "context_callees": "callees",
                "context_field_readers": "field_readers",
                "context_field_writers": "field_writers",
            }[tool]
            return {"symbols": [{"symbol": item} for item in (ctx or {}).get(field, [])]}
        if tool == "context_pack":
            return handle_seam_context_pack(
                self._conn,
                str(args["symbol"]),
                fixture_dir,
                verbose=bool(args.get("verbose", True)),
            )
        if tool == "plan":
            return handle_seam_plan(
                self._conn,
                fixture_dir,
                symbol=args.get("symbol"),
                mode=str(args.get("mode", "target")),
                max_depth=int(args.get("max_depth", 3)),
                scope=str(args.get("scope", "working")),
                base_ref=str(args.get("base_ref", "main")),
            )
        if tool == "suspects":
            return handle_seam_suspects(self._conn, fixture_dir, **args)
        if tool == "grounding":
            return handle_seam_grounding(self._conn, fixture_dir, **args)
        if tool == "impact":
            return handle_seam_impact(
                self._conn,
                str(args["symbol"]),
                fixture_dir,
                direction=str(args.get("direction", "upstream")),
                max_depth=int(args.get("max_depth", 3)),
                include_tests=bool(args.get("include_tests", True)),
                limit=int(args.get("limit", 0)),
            )
        if tool == "trace":
            return handle_seam_trace(
                self._conn,
                str(args["source"]),
                str(args["target"]),
                fixture_dir,
                max_depth=int(args.get("max_depth", 10)),
            )
        if tool == "graph_search":
            return handle_seam_graph_search(self._conn, fixture_dir, **args)
        if tool == "grep":
            query = str(args["query"])
            matches = []
            for path in sorted(fixture_dir.glob("*.py")):
                text = path.read_text(encoding="utf-8")
                if query in text:
                    matches.append({"file": path.name, "query": query, "bytes": len(text)})
            return {"matches": matches}
        raise ValueError(f"unsupported answerability tool: {tool}")


def load_scenarios(path: Path) -> list[Scenario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return load_scenarios_from_dict(data)


def load_scenarios_from_dict(data: dict[str, Any]) -> list[Scenario]:
    scenarios = [_scenario_from_dict(item) for item in data.get("scenarios", [])]
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.id in seen:
            raise ScenarioValidationError(f"duplicate scenario id: {scenario.id}")
        seen.add(scenario.id)
    return sorted(scenarios, key=lambda item: item.id)


@dataclass
class FixtureIndex:
    conn: sqlite3.Connection
    temp_dir: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()


@contextmanager
def build_fixture_index_context(fixture_dir: Path) -> Iterator[FixtureIndex]:
    import seam.config as cfg
    from seam.indexer.cluster_index import index_clusters
    from seam.indexer.db import init_db
    from seam.indexer.pipeline import index_one_file, walk_project
    from seam.indexer.synthesis_index import index_synthesis

    temp_dir = tempfile.TemporaryDirectory(prefix="seam_answerability_")
    conn = init_db(Path(temp_dir.name) / "seam.db")
    try:
        for fpath in walk_project(fixture_dir):
            index_one_file(conn, fpath, root=fixture_dir)
        index_clusters(
            conn,
            naming_mode="deterministic",
            llm_api_key=None,
            llm_model=None,
            min_size=2,
        )
        index_synthesis(conn, enabled=True, fanout_cap=cfg.SEAM_SYNTHESIS_FANOUT_CAP)
        fixture_index = FixtureIndex(conn=conn, temp_dir=temp_dir)
        yield fixture_index
    finally:
        conn.close()
        temp_dir.cleanup()


class AnswerabilityRunner:
    def __init__(self, adapter: ToolAdapter, fixture_dir: Path) -> None:
        self._adapter = adapter
        self._fixture_dir = fixture_dir

    def run(self, scenario: Scenario) -> ScenarioScore:
        results: list[ToolStepResult] = []
        for step in scenario.tool_plan:
            started = time.perf_counter()
            try:
                result = self._adapter.execute(step.tool, step.args, self._fixture_dir)
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = (time.perf_counter() - started) * 1000
                result = ToolStepResult.from_payload(
                    step.tool,
                    step.args,
                    {"error": str(exc)},
                    elapsed_ms=elapsed_ms,
                    ok=False,
                    error=str(exc),
                )
            results.append(result)
        return score_scenario(scenario, results)


def score_scenario(scenario: Scenario, tool_results: list[ToolStepResult]) -> ScenarioScore:
    evidence = {item.key for result in tool_results for item in normalize_evidence(result.payload)}
    expected = {item.key for item in scenario.expected_facts}
    required = {item.key for item in scenario.required_evidence}
    missing_facts = sorted(expected - evidence)
    missing_evidence = sorted(required - evidence)

    answer_score = _score_presence(expected, evidence)
    evidence_score = _score_presence(required, evidence)
    caveat_score = _score_caveats(scenario, tool_results)
    freshness_score = 0 if _has_warning(tool_results, "STALE") else 2
    false_confidence_score = _score_false_confidence(tool_results)

    byte_count = sum(result.byte_count for result in tool_results)
    estimated_tokens = sum(result.estimated_tokens for result in tool_results)
    elapsed_ms = round(sum(result.elapsed_ms for result in tool_results), 3)
    output_budget = scenario.max_estimated_tokens
    output_cost_score = (
        2 if estimated_tokens <= output_budget else 1 if estimated_tokens <= 2000 else 0
    )
    round_trip_score = 2 if len(tool_results) <= 2 else 1 if len(tool_results) <= 5 else 0
    latency_score = 2 if elapsed_ms <= 500 else 1 if elapsed_ms <= 2000 else 0
    scores = {
        "answer": answer_score,
        "evidence": evidence_score,
        "caveats": caveat_score,
        "output_cost": output_cost_score,
        "round_trips": round_trip_score,
        "latency": latency_score,
        "freshness": freshness_score,
        "false_confidence": false_confidence_score,
    }
    status = "passed" if all(score == 2 for score in scores.values()) else "partial"

    notes = []
    if missing_facts:
        notes.append(f"Missing expected facts: {', '.join(missing_facts)}")
    if missing_evidence:
        notes.append(f"Missing required evidence: {', '.join(missing_evidence)}")
    if _has_unsupported_warning(tool_results) and false_confidence_score < 2:
        notes.append("Unsupported evidence was not paired with an acceptable caveat.")

    return ScenarioScore(
        scenario_id=scenario.id,
        category=scenario.category,
        status=status,
        scores=scores,
        missing_facts=missing_facts,
        missing_evidence=missing_evidence,
        product_gaps=list(scenario.product_gap_tags),
        failure_gap_tags=list(scenario.failure_gap_tags),
        roadmap_pressure_tags=list(scenario.roadmap_pressure_tags),
        regression_tags=list(scenario.regression_tags),
        notes=notes,
        round_trips=len(tool_results),
        byte_count=byte_count,
        estimated_tokens=estimated_tokens,
        elapsed_ms=elapsed_ms,
    )


def normalize_evidence(payload: Any) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    _walk_evidence(payload, items)
    return _unique_evidence(items)


def summarize_results(
    results: list[ScenarioScore],
    *,
    scenario_set_version: str,
    seam_version: str | None = None,
    schema_version: int | None = None,
    fixture_hash: str | None = None,
) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    failure_counts: Counter[str] = Counter()
    pressure_counts: Counter[str] = Counter()
    regression_counts: Counter[str] = Counter()
    axis_totals: dict[str, float] = {}
    for result in results:
        category = categories.setdefault(
            result.category,
            {"scenarios": 0, "average_score": 0.0, "passed": 0, "partial": 0},
        )
        category["scenarios"] += 1
        category["passed" if result.status == "passed" else "partial"] += 1
        category["average_score"] += _mean_score(result)
        for axis, score in result.scores.items():
            axis_totals[axis] = axis_totals.get(axis, 0.0) + score
        failure_counts.update(_failure_gap_tags(result))
        pressure_counts.update(_roadmap_pressure_tags(result))
        if result.status == "passed":
            regression_counts.update(result.regression_tags)

    for category in categories.values():
        category["average_score"] = round(category["average_score"] / category["scenarios"], 3)

    axis_averages = {
        axis: round(total / len(results), 3) for axis, total in sorted(axis_totals.items())
    } if results else {}
    lowest_scoring = sorted(
        (
            {
                "scenario_id": result.scenario_id,
                "category": result.category,
                "status": result.status,
                "average_score": round(_mean_score(result), 3),
                "low_axes": sorted(axis for axis, score in result.scores.items() if score < 2),
            }
            for result in results
            if result.status != "passed"
        ),
        key=lambda item: (item["average_score"], item["scenario_id"]),
    )[:5]
    top_failure_gaps = _rank_counts(failure_counts)
    roadmap_pressure = _rank_counts(pressure_counts)
    regression_coverage = _rank_counts(regression_counts)
    recommendation = _recommend_next_prd(top_failure_gaps, roadmap_pressure)

    return {
        "scenario_set_version": scenario_set_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "seam_version": seam_version,
        "schema_version": schema_version,
        "fixture_hash": fixture_hash,
        "totals": {
            "scenarios": len(results),
            "passed": sum(1 for result in results if result.status == "passed"),
            "partial": sum(1 for result in results if result.status != "passed"),
            "average_score": round(_average(_mean_score(result) for result in results), 3),
            "byte_count": sum(result.byte_count for result in results),
            "estimated_tokens": sum(result.estimated_tokens for result in results),
            "round_trips": sum(result.round_trips for result in results),
        },
        "categories": categories,
        "axis_averages": axis_averages,
        "lowest_scoring_scenarios": lowest_scoring,
        "top_failure_gaps": top_failure_gaps,
        "roadmap_pressure": roadmap_pressure,
        "regression_coverage": regression_coverage,
        "top_product_gaps": top_failure_gaps,
        "recommendation": recommendation,
        "roadmap_signal": {
            "top_failure_gaps": top_failure_gaps,
            "top_roadmap_pressure": roadmap_pressure,
            "regression_coverage": regression_coverage,
            "recommended_next_prd": recommendation,
        },
    }


def render_markdown_report(summary: dict[str, Any], results: list[ScenarioScore]) -> str:
    lines = [
        "# Agent Answerability Benchmark",
        "",
        f"- Scenario set: `{summary['scenario_set_version']}`",
        f"- Seam version: `{summary.get('seam_version') or 'unknown'}`",
        f"- Schema version: `{summary.get('schema_version') or 'unknown'}`",
        f"- Fixture hash: `{summary.get('fixture_hash') or 'unknown'}`",
        f"- Scenarios: {summary['totals']['scenarios']}",
        f"- Average score: {summary['totals']['average_score']}",
        f"- Estimated tokens: {summary['totals']['estimated_tokens']}",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Category | Status | Answer | Evidence | Tokens | Gaps |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for result in results:
        gaps = ", ".join(result.product_gaps) if result.product_gaps else "none"
        lines.append(
            "| "
            f"{result.scenario_id} | {result.category} | {result.status} | "
            f"{result.scores['answer']} | {result.scores['evidence']} | "
            f"{result.estimated_tokens} | {gaps} |"
        )

    recommendation = summary.get("recommendation", {})
    lines.extend(
        [
            "",
            "## Category Summary",
            "",
        ]
    )
    for category, data in sorted(summary["categories"].items()):
        lines.append(
            f"- `{category}`: {data['scenarios']} scenarios, average score {data['average_score']}"
        )
    lines.extend(
        [
            "",
            "## Roadmap Signal",
            "",
            f"Top failing answerability gaps: {_format_tags(summary.get('top_failure_gaps', []))}.",
            f"Top roadmap pressure: {_format_tags(summary.get('roadmap_pressure', []))}.",
            f"Regression coverage: {_format_tags(summary.get('regression_coverage', []))}.",
            f"Recommended next PRD: `{recommendation.get('tag', 'none')}` ({recommendation.get('kind', 'none')}).",
            recommendation.get("reason", "No recommendation available."),
            "",
            "## Low-Score Axes",
            "",
        ]
    )
    for axis, score in summary.get("axis_averages", {}).items():
        lines.append(f"- `{axis}`: average score {score}")
    lines.extend(["", "## Lowest Scoring Scenarios", ""])
    for item in summary.get("lowest_scoring_scenarios", []):
        axes = ", ".join(item["low_axes"]) if item["low_axes"] else "none"
        lines.append(
            f"- `{item['scenario_id']}`: average {item['average_score']} ({axes})"
        )
    return "\n".join(lines)


def _scenario_from_dict(raw: dict[str, Any]) -> Scenario:
    required_fields = {
        "acceptable_caveats",
        "capability_tags",
        "category",
        "expected_facts",
        "fallback_plan",
        "id",
        "product_gap_tags",
        "question",
        "required_evidence",
        "target",
        "tool_plan",
    }
    missing = sorted(required_fields - raw.keys())
    if missing:
        raise ScenarioValidationError(f"{raw.get('id', '<unknown>')} missing fields: {missing}")
    if raw["category"] not in VALID_CATEGORIES:
        raise ScenarioValidationError(f"{raw['id']} unknown category: {raw['category']}")

    return Scenario(
        id=str(raw["id"]),
        category=str(raw["category"]),
        question=str(raw["question"]),
        target=dict(raw["target"]),
        expected_facts=[_expected(item) for item in raw["expected_facts"]],
        required_evidence=[_expected(item) for item in raw["required_evidence"]],
        acceptable_caveats=[str(item) for item in raw["acceptable_caveats"]],
        tool_plan=[_tool_step(item) for item in raw["tool_plan"]],
        fallback_plan=[_tool_step(item) for item in raw["fallback_plan"]],
        capability_tags=[str(item) for item in raw["capability_tags"]],
        product_gap_tags=[str(item) for item in raw.get("product_gap_tags", [])],
        failure_gap_tags=[str(item) for item in raw.get("failure_gap_tags", [])],
        roadmap_pressure_tags=[str(item) for item in raw.get("roadmap_pressure_tags", [])],
        regression_tags=[str(item) for item in raw.get("regression_tags", [])],
        max_estimated_tokens=int(raw.get("max_estimated_tokens", 500)),
    )


def _expected(raw: dict[str, Any]) -> ExpectedItem:
    return ExpectedItem(kind=str(raw["kind"]), value=str(raw["value"]))


def _tool_step(raw: dict[str, Any]) -> ToolStep:
    return ToolStep(tool=str(raw["tool"]), args=dict(raw.get("args", {})))


def _failure_gap_tags(result: ScenarioScore) -> list[str]:
    if result.status == "passed":
        return []
    return result.failure_gap_tags or result.product_gaps or result.regression_tags


def _roadmap_pressure_tags(result: ScenarioScore) -> list[str]:
    tags = list(result.roadmap_pressure_tags)
    if result.status == "passed":
        tags.extend(result.product_gaps)
    return tags


def _rank_counts(counts: Counter[str]) -> list[str]:
    return [tag for tag, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _recommend_next_prd(
    top_failure_gaps: list[str], roadmap_pressure: list[str]
) -> dict[str, str | None]:
    if top_failure_gaps:
        tag = top_failure_gaps[0]
        return {
            "kind": "failure_gap",
            "tag": tag,
            "reason": f"`{tag}` is backed by non-passing answerability scenarios.",
        }
    if roadmap_pressure:
        tag = roadmap_pressure[0]
        return {
            "kind": "roadmap_pressure",
            "tag": tag,
            "reason": f"`{tag}` is demand-gated pressure from passing scenarios.",
        }
    return {
        "kind": "none",
        "tag": None,
        "reason": "No failing answerability gap or demand-gated roadmap pressure was found.",
    }


def _format_tags(tags: list[str]) -> str:
    return ", ".join(f"`{tag}`" for tag in tags) if tags else "`none`"


def _walk_evidence(value: Any, items: list[EvidenceItem]) -> None:
    if isinstance(value, dict):
        if "capability" in value and value["capability"] is not None:
            items.append(EvidenceItem("capability", str(value["capability"])))
        if "code" in value and value["code"] is not None:
            items.append(EvidenceItem("warning", str(value["code"])))
        for key in ("symbol", "name", "handler", "source", "target", "from_name", "to_name"):
            if key in value and value[key] is not None:
                items.append(EvidenceItem("symbol", str(value[key])))
        for key in ("file", "path", "doc_path"):
            if key in value and value[key] is not None:
                items.append(EvidenceItem("file", str(value[key])))
        for key in ("doc_kind", "status", "relation_type", "retrieval_mode", "reason"):
            if key in value and value[key] is not None:
                items.append(EvidenceItem(key, str(value[key])))
        if "value" in value and value["value"] is not None:
            items.append(EvidenceItem("value", str(value["value"])))
        if "line" in value and value["line"] is not None:
            items.append(EvidenceItem("line", str(value["line"])))
        if "edge_kind" in value and value["edge_kind"] is not None:
            items.append(EvidenceItem("edge_kind", str(value["edge_kind"])))
        if "kind" in value and value["kind"] is not None:
            items.append(EvidenceItem("edge_kind", str(value["kind"])))
        if "route_resolved" in value and value["route_resolved"] is not None:
            items.append(EvidenceItem("route_resolved", str(value["route_resolved"]).lower()))
        if "confidence" in value and value["confidence"] is not None:
            items.append(EvidenceItem("confidence", str(value["confidence"])))
        if "provenance" in value and value["provenance"] is not None:
            items.append(EvidenceItem("provenance", str(value["provenance"])))
        if "suspect_strength" in value and value["suspect_strength"] is not None:
            items.append(EvidenceItem("suspect_strength", str(value["suspect_strength"])))
        if "removal_risk" in value and value["removal_risk"] is not None:
            items.append(EvidenceItem("removal_risk", str(value["removal_risk"])))
        for key in ("reasons", "blockers"):
            values = value.get(key)
            if isinstance(values, list):
                item_kind = "reason" if key == "reasons" else "blocker"
                items.extend(EvidenceItem(item_kind, str(item)) for item in values)
        for key in ("caveats", "recommended_next_calls"):
            values = value.get(key)
            if isinstance(values, list):
                item_kind = "caveat" if key == "caveats" else "next_call"
                items.extend(EvidenceItem(item_kind, str(item)) for item in values)
        for child in value.values():
            _walk_evidence(child, items)
    elif isinstance(value, list):
        for child in value:
            _walk_evidence(child, items)


def _unique_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    unique: list[EvidenceItem] = []
    for item in items:
        if item.key not in seen:
            seen.add(item.key)
            unique.append(item)
    return unique


def _score_presence(expected: set[str], actual: set[str]) -> int:
    if not expected:
        return 2
    found = len(expected & actual)
    if found == len(expected):
        return 2
    if found > 0:
        return 1
    return 0


def _score_caveats(scenario: Scenario, tool_results: list[ToolStepResult]) -> int:
    if not _has_unsupported_warning(tool_results):
        return 2
    caveats = {caveat for result in tool_results for caveat in result.caveats}
    accepted = set(scenario.acceptable_caveats)
    return 2 if caveats & accepted else 0


def _score_false_confidence(tool_results: list[ToolStepResult]) -> int:
    if not _has_unsupported_warning(tool_results):
        return 2
    return 2 if any(result.caveats for result in tool_results) else 0


def _has_warning(tool_results: list[ToolStepResult], code_fragment: str) -> bool:
    return any(
        code_fragment in str(warning.get("code", ""))
        for result in tool_results
        for warning in result.warnings
    )


def _has_unsupported_warning(tool_results: list[ToolStepResult]) -> bool:
    return _has_warning(tool_results, "UNSUPPORTED")


def _extract_named_dicts(payload: Any, key: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, dict))
        for child in payload.values():
            found.extend(_extract_named_dicts(child, key))
    elif isinstance(payload, list):
        for child in payload:
            found.extend(_extract_named_dicts(child, key))
    return found


def _extract_strings(payload: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            found.extend(str(item) for item in value)
        for child in payload.values():
            found.extend(_extract_strings(child, key))
    elif isinstance(payload, list):
        for child in payload:
            found.extend(_extract_strings(child, key))
    return found


def _mean_score(result: ScenarioScore) -> float:
    return _average(result.scores.values())


def _average(values: Any) -> float:
    numbers = list(values)
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)
