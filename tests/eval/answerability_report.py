"""Standalone Agent Answerability Benchmark report.

Usage:
    make eval-answerability
    uv run python -m tests.eval.answerability_report
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tests.eval.answerability_coherence import (
    check_answerability_docs_coherence,
    check_protocol_scenario_coherence,
)
from tests.eval.answerability_harness import (
    AnswerabilityRunner,
    SeamFixtureAdapter,
    build_fixture_index_context,
    load_scenarios,
    render_markdown_report,
    summarize_results,
)
from tests.eval.recall_harness import compute_fixture_hash

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCENARIO_PATH = Path(__file__).parent / "answerability_scenarios.json"
DOCS_PATH = Path(__file__).parents[2] / "docs" / "agent-answerability-benchmark.md"


def run_answerability_benchmark(
    *,
    scenario_path: Path = SCENARIO_PATH,
    fixture_dir: Path = FIXTURE_DIR,
) -> tuple[dict[str, Any], str]:
    catalog = json.loads(scenario_path.read_text(encoding="utf-8"))
    stored_hash = str(catalog.get("fixture_hash", ""))
    current_hash = compute_fixture_hash(fixture_dir)
    if stored_hash and stored_hash != current_hash:
        raise RuntimeError(
            "answerability fixture hash mismatch: "
            f"stored={stored_hash} current={current_hash}; update scenario ground truth"
        )

    scenarios = load_scenarios(scenario_path)
    with build_fixture_index_context(fixture_dir) as fixture_index:
        adapter = SeamFixtureAdapter(fixture_index.conn)
        runner = AnswerabilityRunner(adapter, fixture_dir)
        results = [runner.run(scenario) for scenario in scenarios]
        schema = adapter.execute("schema", {}, fixture_dir).payload
    summary = summarize_results(
        results,
        scenario_set_version=str(catalog["version"]),
        seam_version=str(schema.get("seam_version", "unknown")),
        schema_version=int(schema.get("schema_version", 0)),
        fixture_hash=current_hash,
    )
    return summary, render_markdown_report(summary, results)


def run_answerability_coherence(
    *,
    scenario_path: Path = SCENARIO_PATH,
    docs_path: Path = DOCS_PATH,
    fixture_dir: Path = FIXTURE_DIR,
) -> list[dict[str, str]]:
    catalog = json.loads(scenario_path.read_text(encoding="utf-8"))
    findings = check_answerability_docs_coherence(scenario_path, docs_path)
    with build_fixture_index_context(fixture_dir) as fixture_index:
        adapter = SeamFixtureAdapter(fixture_index.conn)
        schema = adapter.execute("schema", {}, fixture_dir).payload
    findings.extend(check_protocol_scenario_coherence(catalog, schema))
    return [
        {
            "severity": finding.severity,
            "code": finding.code,
            "evidence": finding.evidence,
            "suggested_fix": finding.suggested_fix,
        }
        for finding in findings
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--markdown-out",
        type=Path,
        help="write the Markdown report to this path while still printing a summary",
    )
    parser.add_argument(
        "--coherence",
        action="store_true",
        help="include local answerability docs/scenario coherence findings",
    )
    args = parser.parse_args()

    summary, markdown = run_answerability_benchmark()
    if args.coherence:
        findings = run_answerability_coherence()
        summary["coherence_findings"] = findings
        if findings:
            markdown += "\n## Coherence Findings\n\n"
            for finding in findings:
                markdown += (
                    f"- `{finding['severity']}` `{finding['code']}`: "
                    f"{finding['evidence']} -> {finding['suggested_fix']}\n"
                )
        else:
            markdown += "\n## Coherence Findings\n\n- No local coherence findings.\n"
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(markdown)


if __name__ == "__main__":
    main()
