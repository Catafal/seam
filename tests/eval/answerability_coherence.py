"""Deterministic coherence checks for answerability roadmap signals.

These checks stay in eval support because they validate product-decision
artifacts, not runtime code intelligence. They intentionally avoid network and
LLM calls so future agents can trust the result during local PR work.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CoherenceFinding:
    severity: str
    code: str
    evidence: str
    suggested_fix: str


def check_answerability_docs_coherence(
    catalog_path: Path, docs_path: Path
) -> list[CoherenceFinding]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    docs = docs_path.read_text(encoding="utf-8")
    findings: list[CoherenceFinding] = []

    version = str(catalog.get("version", ""))
    documented_version = _first_match(r"Scenario set:\s*`([^`]+)`", docs)
    if documented_version and documented_version != version:
        findings.append(
            CoherenceFinding(
                severity="error",
                code="answerability-docs-version-drift",
                evidence=f"docs scenario set {documented_version}",
                suggested_fix=f"Update docs to catalog version {version}.",
            )
        )

    fixture_hash = str(catalog.get("fixture_hash", ""))
    documented_hash = _first_match(r"SHA\s*`([^`]+)`", docs)
    if documented_hash and documented_hash != fixture_hash:
        findings.append(
            CoherenceFinding(
                severity="error",
                code="answerability-docs-fixture-hash-drift",
                evidence=f"docs fixture hash {documented_hash}",
                suggested_fix=f"Update docs to fixture hash {fixture_hash}.",
            )
        )

    scenario_count = len(catalog.get("scenarios", []))
    documented_count = _first_match(
        r"maintained scenario set contains\s+(\d+)\s+natural-language questions", docs
    )
    if documented_count and int(documented_count) != scenario_count:
        findings.append(
            CoherenceFinding(
                severity="error",
                code="answerability-docs-scenario-count-drift",
                evidence=f"{documented_count} natural-language questions",
                suggested_fix=f"Update docs; catalog contains {scenario_count}.",
            )
        )

    required_commands = [
        "make eval-answerability",
        "uv run python -m tests.eval.answerability_report --json",
        "uv run python -m tests.eval.answerability_report --markdown-out",
    ]
    for command in required_commands:
        if not _contains_command(docs, command):
            findings.append(
                CoherenceFinding(
                    severity="warning",
                    code="answerability-docs-command-missing",
                    evidence=f"missing `{command}`",
                    suggested_fix=f"Document `{command}` in the reproduce section.",
                )
            )

    return findings


def check_capability_architecture_coherence(
    schema: dict[str, Any], architecture: dict[str, Any]
) -> list[CoherenceFinding]:
    capabilities = schema.get("capabilities", {})
    warnings = architecture.get("warnings", [])
    warning_codes = {
        str(warning.get("code", "")) for warning in warnings if isinstance(warning, dict)
    }

    checks = {
        "has_test_edges": "NO_TEST_EDGES",
        "has_http_calls": "NO_HTTP_CALLS",
        "has_doc_grounding": "NO_DOC_GROUNDING",
        "has_infra_graph": "NO_INFRA_GRAPH",
    }
    findings: list[CoherenceFinding] = []
    for capability, missing_code in checks.items():
        if capabilities.get(capability) and missing_code in warning_codes:
            findings.append(
                CoherenceFinding(
                    severity="error",
                    code="capability-architecture-contradiction",
                    evidence=f"{capability}=true but architecture warning {missing_code} is present",
                    suggested_fix=(
                        f"Remove or narrow {missing_code}; populated evidence must not be warned "
                        "as missing."
                    ),
                )
            )
    return findings


def check_tracker_issue_coherence(
    issues: list[dict[str, Any]], evidence: dict[str, list[str]]
) -> list[CoherenceFinding]:
    implemented = [item.lower() for item in evidence.get("implemented_titles", [])]
    deferred = [item.lower() for item in evidence.get("deferred_titles", [])]
    findings: list[CoherenceFinding] = []
    for issue in issues:
        title = str(issue.get("title", "")).lower()
        if str(issue.get("state", "")).upper() != "OPEN":
            continue
        number = int(issue["number"])
        if any(marker in title for marker in implemented):
            findings.append(
                CoherenceFinding(
                    severity="warning",
                    code="tracker-implemented-needs-close",
                    evidence=f"open issue #{number}: {issue.get('title')}",
                    suggested_fix=(
                        f"Close or update #{number}; local implementation evidence exists."
                    ),
                )
            )
        elif any(marker in title for marker in deferred):
            findings.append(
                CoherenceFinding(
                    severity="info",
                    code="tracker-deferred-roadmap-pressure",
                    evidence=f"open issue #{number}: {issue.get('title')}",
                    suggested_fix=f"Keep #{number} open only as demand-gated roadmap pressure.",
                )
            )
    return findings


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _contains_command(text: str, command: str) -> bool:
    collapsed_text = re.sub(r"\\\s*", " ", text)
    collapsed_text = re.sub(r"\s+", " ", collapsed_text)
    collapsed_command = re.sub(r"\s+", " ", command)
    return collapsed_command in collapsed_text
