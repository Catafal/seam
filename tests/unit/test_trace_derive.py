"""Unit tests for seam/eval/trace_derive.py — pure GoldenCandidate derivation.

Tests assert EXTERNAL BEHAVIOR through the public interface, not internals.
All tests are offline — no DB, no network, no model download.

Coverage:
  D1  — Basic derive: one trace query + matching outcome → one candidate.
  D2  — expected_symbols = outcome symbols (the hindsight set).
  D3  — Dedup by (tool, query): same (tool, query) in two sessions → one candidate.
  D4  — Gap flag: outcome symbol absent from query's result → gap=True.
  D5  — No-gap: outcome symbol IS in query's result → gap=False.
  D6  — Empty outcome → zero candidates (not an error).
  D7  — Provenance fields: session_id, source_query, derived_at all present.
  D8  — Multiple tools, multiple queries → multiple candidates (no cross-tool dedup).
  D9  — Candidate shape: tool, query, k, expected_symbols, gap, provenance keys.
  D10 — Pure: no IO, no config — function accepts only lists/sets, returns list.
  D11 — Dedup keeps the candidate with the most gap symbols when merging duplicates.
"""

from __future__ import annotations

import datetime
from typing import Any

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_trace_record(
    *,
    tool: str = "seam_search",
    query: str = "auth token",
    symbol_names: list[str] | None = None,
    result_count: int | None = None,
    session_id: str = "session-abc",
    ts: float = 1700000000.0,
    elapsed_ms: float = 5.0,
) -> dict[str, Any]:
    """Build a synthetic trace record matching the trace_capture NDJSON shape."""
    names = symbol_names or []
    return {
        "event": "tool_call",
        "session_id": session_id,
        "ts": ts,
        "tool": tool,
        "args": _query_args(tool, query),
        "symbol_names": names,
        "result_count": result_count if result_count is not None else len(names),
        "elapsed_ms": elapsed_ms,
    }


def _query_args(tool: str, query: str) -> dict[str, Any]:
    if tool in ("seam_search",):
        return {"query": query}
    if tool in ("seam_query",):
        return {"concept": query}
    if tool in ("seam_context",):
        return {"symbol": query}
    if tool in ("seam_impact",):
        return {"symbol": query}
    return {"query": query}


# ── D1: Basic derive ──────────────────────────────────────────────────────────


class TestBasicDerive:
    def test_one_trace_one_outcome_one_candidate(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth.validate", "Token"])]
        outcome = {"Auth.validate"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 1

    def test_returns_list(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        candidates = derive_goldens([], set())
        assert isinstance(candidates, list)


# ── D2: expected_symbols = outcome symbols ────────────────────────────────────


class TestExpectedSymbols:
    def test_expected_symbols_equals_outcome(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["A", "B"])]
        outcome = {"X", "Y"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 1
        assert set(candidates[0].expected_symbols) == {"X", "Y"}

    def test_expected_symbols_is_list(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["A"])]
        outcome = {"X"}
        candidates = derive_goldens(records, outcome)
        assert isinstance(candidates[0].expected_symbols, list)


# ── D3: Dedup by (tool, query) ────────────────────────────────────────────────


class TestDedup:
    def test_same_tool_query_deduped(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(
                tool="seam_search", query="auth", symbol_names=["Auth"],
                session_id="s1",
            ),
            _make_trace_record(
                tool="seam_search", query="auth", symbol_names=["Auth", "Token"],
                session_id="s2",
            ),
        ]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 1

    def test_different_queries_not_deduped(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(tool="seam_search", query="auth", symbol_names=["Auth"]),
            _make_trace_record(tool="seam_search", query="token", symbol_names=["Token"]),
        ]
        outcome = {"Auth", "Token"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 2

    def test_different_tools_not_deduped(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(tool="seam_search", query="auth", symbol_names=["Auth"]),
            _make_trace_record(tool="seam_query", query="auth", symbol_names=["Auth"]),
        ]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 2


# ── D4: Gap flag (outcome symbol absent from result) ──────────────────────────


class TestGapFlag:
    def test_gap_true_when_outcome_symbol_absent(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        # The result did NOT contain "MissingSymbol" but the agent edited it
        records = [_make_trace_record(symbol_names=["Auth"])]
        outcome = {"MissingSymbol"}
        candidates = derive_goldens(records, outcome)
        assert candidates[0].gap is True

    def test_gap_false_when_all_outcome_symbols_present(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth", "Token"])]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        assert candidates[0].gap is False


# ── D5: No-gap ────────────────────────────────────────────────────────────────


class TestNoGap:
    def test_gap_false_when_outcome_subset_of_result(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["A", "B", "C"])]
        outcome = {"A", "B"}
        candidates = derive_goldens(records, outcome)
        assert candidates[0].gap is False

    def test_gap_true_when_not_all_outcome_symbols_present(self) -> None:
        """When SOME but not ALL outcome symbols appear in the result, gap=True.
        gap requires ALL outcome symbols to be in the result — any miss = gap."""
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["A"])]
        # Outcome has A (found) and B (not found) — gap=True because B is missing
        outcome = {"A", "B"}
        candidates = derive_goldens(records, outcome)
        # gap=True because NOT ALL outcome symbols were found (B is absent)
        assert candidates[0].gap is True


# ── D6: Empty outcome → zero candidates ───────────────────────────────────────


class TestEmptyOutcome:
    def test_empty_outcome_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth"])]
        candidates = derive_goldens(records, set())
        assert candidates == []

    def test_empty_records_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        candidates = derive_goldens([], {"Auth"})
        assert candidates == []

    def test_both_empty_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        candidates = derive_goldens([], set())
        assert candidates == []


# ── D7: Provenance fields ─────────────────────────────────────────────────────


class TestProvenance:
    def test_provenance_has_session_id(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(session_id="session-xyz", symbol_names=["Auth"])]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        assert "session-xyz" in candidates[0].provenance.session_ids

    def test_provenance_has_source_query(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(query="auth token", symbol_names=["Auth"])]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        assert candidates[0].provenance.source_query == "auth token"

    def test_provenance_has_derived_at(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth"])]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        # derived_at should be a string (ISO timestamp)
        assert isinstance(candidates[0].provenance.derived_at, str)
        # Should parse as a datetime
        datetime.datetime.fromisoformat(candidates[0].provenance.derived_at)

    def test_provenance_merges_session_ids_on_dedup(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(session_id="s1", query="auth", symbol_names=["Auth"]),
            _make_trace_record(session_id="s2", query="auth", symbol_names=["Auth"]),
        ]
        outcome = {"Auth"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 1
        assert "s1" in candidates[0].provenance.session_ids
        assert "s2" in candidates[0].provenance.session_ids


# ── D8: Multiple tools, multiple queries ─────────────────────────────────────


class TestMultipleQueries:
    def test_multiple_tools_produce_multiple_candidates(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(tool="seam_search", query="q1", symbol_names=["A"]),
            _make_trace_record(tool="seam_query", query="q2", symbol_names=["B"]),
            _make_trace_record(tool="seam_context", query="q3", symbol_names=["C"]),
        ]
        outcome = {"A", "B", "C"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 3


# ── D9: Candidate shape ───────────────────────────────────────────────────────


class TestCandidateShape:
    def test_candidate_has_required_fields(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth"])]
        outcome = {"Auth"}
        c = derive_goldens(records, outcome)[0]
        # Must have all required fields
        assert hasattr(c, "tool")
        assert hasattr(c, "query")
        assert hasattr(c, "k")
        assert hasattr(c, "expected_symbols")
        assert hasattr(c, "gap")
        assert hasattr(c, "provenance")

    def test_tool_field_matches_record(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(tool="seam_impact", query="Foo.bar", symbol_names=[])]
        outcome = {"Foo.bar"}
        c = derive_goldens(records, outcome)[0]
        assert c.tool == "seam_impact"

    def test_query_field_matches_record(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(query="my query", symbol_names=[])]
        outcome = {"X"}
        c = derive_goldens(records, outcome)[0]
        assert c.query == "my query"

    def test_k_is_int(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["A", "B", "C"])]
        outcome = {"A"}
        c = derive_goldens(records, outcome)[0]
        assert isinstance(c.k, int)
        assert c.k > 0

    def test_candidate_to_dict(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth"])]
        outcome = {"Auth"}
        c = derive_goldens(records, outcome)[0]
        d = c.to_dict()
        assert isinstance(d, dict)
        assert "tool" in d
        assert "query" in d
        assert "k" in d
        assert "expected_symbols" in d
        assert "gap" in d
        assert "provenance" in d


# ── D10: Pure function ────────────────────────────────────────────────────────


class TestPureFunction:
    def test_does_not_mutate_records(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [_make_trace_record(symbol_names=["Auth"])]
        original_len = len(records)
        original_record = dict(records[0])
        derive_goldens(records, {"Auth"})
        assert len(records) == original_len
        assert records[0] == original_record

    def test_does_not_mutate_outcome(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        outcome = {"Auth", "Token"}
        original_len = len(outcome)
        derive_goldens([], outcome)
        assert len(outcome) == original_len

    def test_deterministic_output(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(tool="seam_search", query="auth", symbol_names=["Auth"]),
        ]
        outcome = {"Auth"}
        c1 = derive_goldens(records, outcome)
        c2 = derive_goldens(records, outcome)
        assert c1[0].tool == c2[0].tool
        assert c1[0].query == c2[0].query
        assert c1[0].expected_symbols == c2[0].expected_symbols
        assert c1[0].gap == c2[0].gap


# ── D11: Dedup keeps best candidate ──────────────────────────────────────────


class TestDedupMerge:
    def test_dedup_merges_session_ids_from_both(self) -> None:
        from seam.eval.trace_derive import derive_goldens  # noqa: PLC0415

        records = [
            _make_trace_record(session_id="s1", query="auth", symbol_names=["Auth"]),
            _make_trace_record(session_id="s2", query="auth", symbol_names=["Auth", "Token"]),
        ]
        outcome = {"Token"}
        candidates = derive_goldens(records, outcome)
        assert len(candidates) == 1
        assert "s1" in candidates[0].provenance.session_ids
        assert "s2" in candidates[0].provenance.session_ids
