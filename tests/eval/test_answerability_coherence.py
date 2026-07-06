from __future__ import annotations

import json
from pathlib import Path

from tests.eval.answerability_coherence import (
    check_answerability_docs_coherence,
    check_capability_architecture_coherence,
    check_protocol_scenario_coherence,
    check_tracker_issue_coherence,
)
from tests.eval.answerability_report import run_answerability_coherence


def test_benchmark_docs_metadata_drift_is_reported(tmp_path: Path) -> None:
    catalog_path = tmp_path / "answerability_scenarios.json"
    docs_path = tmp_path / "agent-answerability-benchmark.md"
    catalog_path.write_text(
        json.dumps(
            {
                "version": "2026-07-06",
                "fixture_hash": "3207085f908caad8",
                "scenarios": [{"id": "one"}, {"id": "two"}],
            }
        ),
        encoding="utf-8",
    )
    docs_path.write_text(
        "\n".join(
            [
                "> Scenario set: `2026-07-05`.",
                "> Current fixture: `tests/eval/fixtures` with SHA `0f11955b3b5b277a`.",
                "The maintained scenario set contains 1 natural-language questions.",
                "Run `make eval-answerability`.",
                "Run `uv run python -m tests.eval.answerability_report --json`.",
                "Run `uv run python -m tests.eval.answerability_report --markdown-out out.md`.",
            ]
        ),
        encoding="utf-8",
    )

    findings = check_answerability_docs_coherence(catalog_path, docs_path)

    assert [finding.code for finding in findings] == [
        "answerability-docs-version-drift",
        "answerability-docs-fixture-hash-drift",
        "answerability-docs-scenario-count-drift",
    ]
    assert all(finding.severity == "error" for finding in findings)
    assert "2026-07-05" in findings[0].evidence
    assert "2026-07-06" in findings[0].suggested_fix
    assert "0f11955b3b5b277a" in findings[1].evidence
    assert "3207085f908caad8" in findings[1].suggested_fix
    assert "1 natural-language questions" in findings[2].evidence
    assert "catalog contains 2" in findings[2].suggested_fix


def test_benchmark_docs_metadata_accepts_current_values(tmp_path: Path) -> None:
    catalog_path = tmp_path / "answerability_scenarios.json"
    docs_path = tmp_path / "agent-answerability-benchmark.md"
    catalog_path.write_text(
        json.dumps(
            {
                "version": "2026-07-06",
                "fixture_hash": "3207085f908caad8",
                "scenarios": [{"id": "one"}, {"id": "two"}],
            }
        ),
        encoding="utf-8",
    )
    docs_path.write_text(
        "\n".join(
            [
                "> Scenario set: `2026-07-06`.",
                "> Current fixture: `tests/eval/fixtures` with SHA `3207085f908caad8`.",
                "The maintained scenario set contains 2 natural-language questions.",
                "Run `make eval-answerability`.",
                "Run `uv run python -m tests.eval.answerability_report --json`.",
                "Run `uv run python -m tests.eval.answerability_report --markdown-out out.md`.",
            ]
        ),
        encoding="utf-8",
    )

    assert check_answerability_docs_coherence(catalog_path, docs_path) == []


def test_capability_architecture_coherence_flags_missing_warning_for_populated_surface() -> None:
    schema = {"capabilities": {"has_test_edges": True}}
    architecture = {"warnings": [{"code": "NO_TEST_EDGES", "message": "No test edges found."}]}

    findings = check_capability_architecture_coherence(schema, architecture)

    assert findings[0].code == "capability-architecture-contradiction"
    assert findings[0].severity == "error"
    assert "has_test_edges" in findings[0].evidence
    assert "NO_TEST_EDGES" in findings[0].evidence


def test_tracker_issue_coherence_classifies_implemented_and_deferred_prds() -> None:
    issues = [
        {
            "number": 371,
            "title": "PRD: Docs and spec grounding for agent answerability",
            "state": "OPEN",
        },
        {
            "number": 316,
            "title": "PRD: Phase 11 RFC — Kubernetes and Kustomize infra graph",
            "state": "OPEN",
        },
    ]
    evidence = {
        "implemented_titles": ["docs and spec grounding"],
        "deferred_titles": ["kubernetes", "kustomize"],
    }

    findings = check_tracker_issue_coherence(issues, evidence)

    assert [finding.code for finding in findings] == [
        "tracker-implemented-needs-close",
        "tracker-deferred-roadmap-pressure",
    ]
    assert findings[0].suggested_fix == "Close or update #371; local implementation evidence exists."
    assert findings[1].suggested_fix == "Keep #316 open only as demand-gated roadmap pressure."


def test_protocol_scenario_coherence_flags_stale_negative_route_capability() -> None:
    catalog = {
        "scenarios": [
            {
                "id": "protocol-route-node-capability",
                "category": "protocol",
                "expected_facts": [{"kind": "capability", "value": "has_route_nodes:false"}],
                "required_evidence": [{"kind": "warning", "value": "UNSUPPORTED"}],
                "tool_plan": [{"tool": "schema_capability", "args": {"capability": "has_route_nodes"}}],
            }
        ]
    }
    schema = {"capabilities": {"has_route_nodes": True}, "counts": {"routes": 1}}

    findings = check_protocol_scenario_coherence(catalog, schema)

    assert [finding.code for finding in findings] == [
        "protocol-scenario-capability-contradiction"
    ]
    assert findings[0].severity == "error"
    assert "protocol-route-node-capability" in findings[0].evidence
    assert "has_route_nodes:false" in findings[0].evidence
    assert "has_route_nodes:true" in findings[0].suggested_fix


def test_answerability_coherence_uses_the_requested_fixture_dir(tmp_path: Path) -> None:
    scenario_path = tmp_path / "answerability_scenarios.json"
    docs_path = tmp_path / "agent-answerability-benchmark.md"
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "plain.py").write_text("def plain() -> None:\n    pass\n", encoding="utf-8")
    scenario_path.write_text(
        json.dumps(
            {
                "version": "2026-07-06",
                "fixture_hash": "custom",
                "scenarios": [
                    {
                        "id": "protocol-route-node-capability",
                        "category": "protocol",
                        "expected_facts": [
                            {"kind": "capability", "value": "has_route_nodes:false"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    docs_path.write_text(
        "\n".join(
            [
                "> Scenario set: `2026-07-06`.",
                "> Current fixture: `tests/eval/fixtures` with SHA `custom`.",
                "The maintained scenario set contains 1 natural-language questions.",
                "Run `make eval-answerability`.",
                "Run `uv run python -m tests.eval.answerability_report --json`.",
                "Run `uv run python -m tests.eval.answerability_report --markdown-out out.md`.",
            ]
        ),
        encoding="utf-8",
    )

    assert (
        run_answerability_coherence(
            scenario_path=scenario_path,
            docs_path=docs_path,
            fixture_dir=fixture_dir,
        )
        == []
    )
