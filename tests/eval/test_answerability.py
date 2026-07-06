from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.eval.answerability_harness import (
    AnswerabilityRunner,
    ScenarioValidationError,
    ToolStepResult,
    load_scenarios,
    load_scenarios_from_dict,
    normalize_evidence,
    render_markdown_report,
    score_scenario,
    summarize_results,
)
from tests.eval.answerability_report import run_answerability_benchmark


class FakeAdapter:
    def execute(self, tool: str, args: dict[str, Any], fixture_dir: Path) -> ToolStepResult:
        payload = {
            "symbol": args.get("symbol", "validate_data"),
            "file": "pipeline.py",
            "line": 12,
            "confidence": "EXTRACTED",
            "provenance": "fake-fixture",
        }
        return ToolStepResult.from_payload(tool, args, payload, elapsed_ms=2.5)


def _scenario(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "discovery-validate-data",
        "category": "discovery",
        "question": "Where is validate data implemented?",
        "target": {"kind": "fixture", "name": "eval-fixture"},
        "expected_facts": [{"kind": "symbol", "value": "validate_data"}],
        "required_evidence": [{"kind": "file", "value": "pipeline.py"}],
        "acceptable_caveats": ["No runtime behavior is inspected."],
        "tool_plan": [{"tool": "fake_symbol", "args": {"symbol": "validate_data"}}],
        "fallback_plan": [{"tool": "grep", "args": {"query": "validate_data"}}],
        "capability_tags": ["search"],
        "product_gap_tags": ["graph-quality coherence"],
    }
    base.update(overrides)
    return base


def test_load_scenarios_validates_required_fields_and_orders_by_id(tmp_path: Path) -> None:
    scenario_file = tmp_path / "scenarios.json"
    scenario_file.write_text(
        json.dumps(
            {
                "version": "2026-07-05",
                "scenarios": [
                    _scenario(id="navigation-context"),
                    _scenario(id="discovery-validate-data"),
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_scenarios(scenario_file)

    assert [scenario.id for scenario in loaded] == [
        "discovery-validate-data",
        "navigation-context",
    ]
    assert loaded[0].question == "Where is validate data implemented?"


def test_load_scenarios_rejects_duplicate_ids(tmp_path: Path) -> None:
    scenario_file = tmp_path / "scenarios.json"
    scenario_file.write_text(
        json.dumps(
            {
                "version": "2026-07-05",
                "scenarios": [_scenario(), _scenario()],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ScenarioValidationError, match="duplicate scenario id"):
        load_scenarios(scenario_file)


def test_score_distinguishes_missing_evidence_from_answer_hit() -> None:
    scenario = load_scenarios_from_dict({"version": "test", "scenarios": [_scenario()]})[0]
    step = ToolStepResult.from_payload(
        "fake_symbol",
        {},
        {"symbol": "validate_data", "file": "other.py", "line": 10},
        elapsed_ms=1.0,
    )

    result = score_scenario(scenario, [step])

    assert result.scores["answer"] == 2
    assert result.scores["evidence"] == 0
    assert result.missing_evidence == ["file:pipeline.py"]
    assert "graph-quality coherence" in result.product_gaps


def test_score_penalizes_confident_unsupported_answers_more_than_honest_caveats() -> None:
    scenario = load_scenarios_from_dict({"version": "test", "scenarios": [_scenario()]})[0]
    unsupported = ToolStepResult.from_payload(
        "fake_symbol",
        {},
        {"symbol": "unknown", "warnings": [{"code": "UNSUPPORTED"}]},
        elapsed_ms=1.0,
    )
    honest = ToolStepResult.from_payload(
        "fake_symbol",
        {},
        {
            "symbol": "unknown",
            "warnings": [{"code": "UNSUPPORTED"}],
            "caveats": ["No runtime behavior is inspected."],
        },
        elapsed_ms=1.0,
    )

    unsupported_score = score_scenario(scenario, [unsupported])
    honest_score = score_scenario(scenario, [honest])

    assert unsupported_score.scores["false_confidence"] == 0
    assert honest_score.scores["false_confidence"] == 2
    assert honest_score.scores["caveats"] == 2


def test_status_requires_caveats_and_false_confidence_to_pass() -> None:
    scenario = load_scenarios_from_dict(
        {
            "version": "test",
            "scenarios": [
                _scenario(
                    expected_facts=[{"kind": "warning", "value": "UNSUPPORTED"}],
                    required_evidence=[{"kind": "warning", "value": "UNSUPPORTED"}],
                )
            ],
        }
    )[0]
    unsupported = ToolStepResult.from_payload(
        "fake_symbol",
        {},
        {"warnings": [{"code": "UNSUPPORTED"}]},
        elapsed_ms=1.0,
    )

    result = score_scenario(scenario, [unsupported])

    assert result.scores["answer"] == 2
    assert result.scores["evidence"] == 2
    assert result.scores["false_confidence"] == 0
    assert result.status == "partial"


def test_normalize_evidence_includes_semantic_retrieval_contract() -> None:
    evidence = normalize_evidence(
        {
            "symbol": "parse_config",
            "retrieval_mode": "keyword-fallback",
            "retrieval": {"sources": ["lexical"]},
            "caveats": ["Semantic similarity is a discovery lead."],
            "recommended_next_calls": ["seam_context"],
        }
    )

    keys = {item.key for item in evidence}
    assert "retrieval_mode:keyword-fallback" in keys
    assert "caveat:Semantic similarity is a discovery lead." in keys
    assert "next_call:seam_context" in keys


def test_runner_executes_tool_plan_and_records_costs(tmp_path: Path) -> None:
    scenario = load_scenarios_from_dict({"version": "test", "scenarios": [_scenario()]})[0]
    runner = AnswerabilityRunner(FakeAdapter(), tmp_path)

    result = runner.run(scenario)

    assert result.scenario_id == "discovery-validate-data"
    assert result.status == "passed"
    assert result.scores["answer"] == 2
    assert result.round_trips == 1
    assert result.byte_count > 0
    assert result.estimated_tokens > 0


def test_report_summarizes_categories_gaps_and_roadmap_signal(tmp_path: Path) -> None:
    scenario = load_scenarios_from_dict({"version": "test", "scenarios": [_scenario()]})[0]
    result = AnswerabilityRunner(FakeAdapter(), tmp_path).run(scenario)

    summary = summarize_results([result], scenario_set_version="test")
    markdown = render_markdown_report(summary, [result])

    assert summary["scenario_set_version"] == "test"
    assert summary["totals"]["scenarios"] == 1
    assert summary["categories"]["discovery"]["scenarios"] == 1
    assert "graph-quality coherence" not in summary["top_failure_gaps"]
    assert "graph-quality coherence" in summary["roadmap_pressure"]
    assert "Roadmap Signal" in markdown
    assert "discovery-validate-data" in markdown


def test_report_prefers_partial_failure_gaps_over_passing_roadmap_pressure() -> None:
    passing = score_scenario(
        load_scenarios_from_dict(
            {
                "version": "test",
                "scenarios": [
                    _scenario(
                        id="infra-pressure",
                        expected_facts=[{"kind": "warning", "value": "UNSUPPORTED"}],
                        required_evidence=[{"kind": "warning", "value": "UNSUPPORTED"}],
                        product_gap_tags=[],
                        roadmap_pressure_tags=["infra graph"],
                    )
                ],
            }
        )[0],
        [
            ToolStepResult.from_payload(
                "fake_symbol",
                {},
                {
                    "warnings": [{"code": "UNSUPPORTED"}],
                    "caveats": ["No runtime behavior is inspected."],
                },
                elapsed_ms=1.0,
            )
        ],
    )
    partial = score_scenario(
        load_scenarios_from_dict(
            {
                "version": "test",
                "scenarios": [
                    _scenario(
                        id="protocol-failure",
                        expected_facts=[{"kind": "symbol", "value": "missing"}],
                        required_evidence=[{"kind": "edge_kind", "value": "http_calls"}],
                        product_gap_tags=[],
                        failure_gap_tags=["protocol-edge quality"],
                    )
                ],
            }
        )[0],
        [ToolStepResult.from_payload("fake_symbol", {}, {"symbol": "other"}, elapsed_ms=1.0)],
    )

    summary = summarize_results([passing, partial], scenario_set_version="test")

    assert summary["top_failure_gaps"] == ["protocol-edge quality"]
    assert summary["roadmap_pressure"] == ["infra graph"]
    assert summary["recommendation"]["kind"] == "failure_gap"
    assert summary["recommendation"]["tag"] == "protocol-edge quality"


def test_report_treats_failing_regression_coverage_as_failure_gap() -> None:
    partial = score_scenario(
        load_scenarios_from_dict(
            {
                "version": "test",
                "scenarios": [
                    _scenario(
                        id="regression-now-failing",
                        expected_facts=[{"kind": "symbol", "value": "missing"}],
                        required_evidence=[{"kind": "file", "value": "missing.py"}],
                        product_gap_tags=[],
                        regression_tags=["graph-quality coherence"],
                    )
                ],
            }
        )[0],
        [ToolStepResult.from_payload("fake_symbol", {}, {"symbol": "other"}, elapsed_ms=1.0)],
    )

    summary = summarize_results([partial], scenario_set_version="test")

    assert summary["top_failure_gaps"] == ["graph-quality coherence"]
    assert summary["regression_coverage"] == []
    assert summary["recommendation"]["kind"] == "failure_gap"


def test_maintained_scenario_suite_runs_against_fixture() -> None:
    summary, markdown = run_answerability_benchmark()

    assert summary["scenario_set_version"] == "2026-07-06"
    assert summary["schema_version"] >= 1
    assert summary["seam_version"]
    assert summary["fixture_hash"] == "3207085f908caad8"
    assert summary["totals"]["scenarios"] == 26
    assert summary["totals"]["average_score"] >= 1.8
    assert "protocol-edge quality" in summary["top_failure_gaps"]
    assert "infra-kubernetes-capability-honesty" in markdown
