# ADR-003: Heuristic Execution Flow Tracing (No LLM in Phase 0)

## Status
Accepted — 2026-06-01 (Phase 0 only — revisit for Phase 1)

## Context
Execution flow tracing (following multi-hop call chains end-to-end) can be implemented two ways:

- **Heuristic (static graph traversal):** Follow edges in the SQLite graph recursively. Fast, deterministic, zero cost, no API key.
- **LLM-assisted:** Use an LLM to name, summarize, and semantically cluster flows. Higher quality names, can infer implicit flows (dynamic dispatch). Adds latency + cost.

## Decision
**Heuristic-only for Phase 0. LLM layer optional for Phase 1.**

Specific reasons:
1. Phase 0 doesn't implement execution flows at all (they're in Phase 1). This ADR documents the Phase 1 approach decision.
2. Heuristic tracing is sufficient to demonstrate the core value proposition (token reduction benchmark).
3. Forcing an LLM API key requirement violates the "zero external dependencies" principle.
4. GitNexus implements heuristic flow tracing and it is the most-used feature in practice.

## Alternatives Rejected
- **LLM-only flows:** API key dependency; latency; cost; not "local-first."
- **Hybrid (heuristic + optional LLM naming):** Correct long-term direction, but adds complexity before Phase 0 benchmarks validate the approach.

## Consequences
- Phase 1 execution flows: recursive CTE on `edges` table from a set of entry points.
- Flow quality depends on edge extraction quality (import + call edges from tree-sitter).
- Dynamic dispatch (Python's `__getattr__`, TypeScript generics) will produce incomplete flows — this is acceptable and documented.
- Phase 2 can add an optional LLM naming layer as a plugin without changing the core graph.
