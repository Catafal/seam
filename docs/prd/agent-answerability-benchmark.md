# PRD - Agent Answerability Benchmark

> Status: implemented by issues #318-#322.
> Created: 2026-07-05.
> Tracker source: <https://github.com/Catafal/seam/issues/317>.

## Problem Statement

Seam already exposes many static code-intelligence primitives, but the roadmap needs
a repeatable way to decide what to build next. The product question is not whether a
new graph surface sounds useful; it is whether an AI coding agent can answer daily
codebase questions faster, with less context, and with trustworthy evidence.

The existing recall/MRR eval harness protects retrieval quality, but it does not model
complete agent questions, evidence sufficiency, stale or unsupported capability
behavior, output cost, round trips, latency, caveats, or false confidence.

## Solution

Build a deterministic Agent Answerability Benchmark. The benchmark defines
natural-language scenarios, runs explicit Seam tool plans, compares output against
authored expected facts and required evidence, and reports the roadmap buckets exposed
by the scenario set.

The first implementation is intentionally local-only and deterministic:

- no LLM judge;
- no network calls;
- no runtime probing;
- no new graph extraction;
- no product CLI changes;
- no default gate dependency on long-running agent sessions.

## User Stories

1. As a Seam maintainer, I want natural-language agent questions in a repeatable
   benchmark, so roadmap decisions are based on observed answerability gaps.
2. As a Seam maintainer, I want authored expected facts and required evidence, so
   scoring is grounded in source truth rather than live Seam output.
3. As a Seam maintainer, I want caveat and false-confidence scoring, so unsupported
   or static-only evidence is handled honestly.
4. As a Seam maintainer, I want token/byte, round-trip, and latency accounting, so
   output quality includes cost, not only correctness.
5. As a future implementation agent, I want a machine-readable and Markdown report,
   so the next PRD can be chosen from measured results.

## Implementation Decisions

- Implement as eval tooling under the existing deterministic benchmark conventions.
- Keep the scenario catalog as structured JSON.
- Score on a 0-2 scale for answer facts, evidence, caveats, output cost, round trips,
  latency, freshness, and false confidence.
- Use the existing eval fixture as the first required scenario target.
- Treat unsupported protocol/infra capability as a valid passing answer only when it is
  paired with explicit unsupported evidence and an accepted caveat.
- Add an optional `make eval-answerability` target; keep the full benchmark out of the
  default gate while the scenario set evolves.

## Testing Decisions

- Unit-test scenario loading, duplicate rejection, scoring, false-confidence handling,
  runner metadata, and report shape.
- Regression-test the maintained scenario suite against the deterministic fixture.
- Reuse the existing fixture hash discipline so source changes cannot silently stale
  the scenario ground truth.

## Out of Scope

- Kubernetes/Kustomize, Helm, Terraform, cloud resources, or other new infra extraction.
- New protocol families or broader HTTP-call extraction.
- Context-pack upgrade, change-planning surface, dead-code suspects, or docs/spec
  grounding implementation.
- LLM judging, live agent sessions, external services, runtime probing, telemetry, or
  production log ingestion.

## Further Notes

The first scenario set is fixture-sized on purpose. A perfect fixture score means the
measurement loop exists; it does not prove Seam is complete. The next useful step is to
add larger optional repos, then use low scores for failing answerability and recurring
product-gap labels for roadmap pressure when the current benchmark remains green.
