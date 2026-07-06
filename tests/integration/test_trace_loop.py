"""Integration tests for the WS6.1 trace-capture loop curation glue.

Tests verify the end-to-end round-trip:
  captured trace file → derive → review file → approve → live golden set.

All tests are offline — no DB query needed (we build synthetic trace files).
No network, no model download.

Coverage:
  TL1 — Fixture golden.json is BYTE-UNCHANGED after running the derive step.
  TL2 — derive step: reading a trace NDJSON + outcome → writes review file.
  TL3 — review file has expected shape (list of GoldenCandidate-like dicts).
  TL4 — promote step: approved candidates land in live golden set with commit SHA.
  TL5 — promote step: unapproved candidates do NOT appear in live golden set.
  TL6 — live golden set is keyed to repo slug + commit SHA (not fixture hash).
  TL7 — promote is idempotent: running twice with the same candidates → no duplicate.
  TL8 — live golden set does NOT have "fixture_hash" key (it's a SEPARATE set).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make repo root importable so `from benchmarks.trace_loop import ...` works
# even when pytest is run from a subdirectory. Mirrors the pattern in
# tests/unit/test_semantic_ann_bench_smoke.py.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Path to the fixture golden.json (must never be modified by the loop).
_FIXTURE_GOLDEN = Path(__file__).parent.parent / "eval" / "golden.json"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_trace_ndjson(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    """Write a synthetic NDJSON trace file and return its path."""
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_file = trace_dir / "session-test-abc.ndjson"
    with open(trace_file, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return trace_file


def _make_trace_record(
    *,
    tool: str = "seam_search",
    query: str = "auth token",
    symbol_names: list[str] | None = None,
    session_id: str = "session-test-abc",
) -> dict[str, Any]:
    names = symbol_names or []
    return {
        "event": "tool_call",
        "session_id": session_id,
        "ts": 1700000000.0,
        "tool": tool,
        "args": {"query": query},
        "symbol_names": names,
        "result_count": len(names),
        "elapsed_ms": 5.0,
    }


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_fixture_golden_bytes() -> bytes:
    with open(_FIXTURE_GOLDEN, "rb") as f:
        return f.read()


# ── TL1: Fixture golden.json byte-unchanged ───────────────────────────────────


class TestFixtureGoldenUnchanged:
    """CRITICAL: The fixture golden.json must NEVER be modified by the trace loop."""

    def test_fixture_golden_exists(self) -> None:
        assert _FIXTURE_GOLDEN.exists(), (
            f"Fixture golden.json not found at {_FIXTURE_GOLDEN}"
        )

    def test_fixture_golden_byte_unchanged_after_derive(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        before = _read_fixture_golden_bytes()
        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Auth.validate"]),
        ])
        outcome = {"Auth.validate"}
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome=outcome,
            review_path=review_path,
        )
        after = _read_fixture_golden_bytes()
        assert before == after, (
            "BUG: fixture golden.json was modified by derive_from_trace! "
            "The trace loop MUST use a SEPARATE live golden set."
        )

    def test_fixture_golden_byte_unchanged_after_promote(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        before = _read_fixture_golden_bytes()
        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Auth.validate"]),
        ])
        outcome = {"Auth.validate"}
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome=outcome, review_path=review_path)

        live_path = tmp_path / "live_goldens.json"
        review = _read_json(review_path)
        # Approve the first candidate
        if review.get("candidates"):
            review["candidates"][0]["approved"] = True
        promote_candidates(
            review_path=review_path,
            live_golden_path=live_path,
            repo_sha="abc123",
        )
        after = _read_fixture_golden_bytes()
        assert before == after, (
            "BUG: fixture golden.json was modified by promote_candidates!"
        )


# ── TL2: derive step writes review file ──────────────────────────────────────


class TestDeriveWritesReviewFile:
    def test_review_file_created(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Auth.validate"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome={"Auth.validate"},
            review_path=review_path,
        )
        assert review_path.exists()

    def test_review_file_is_valid_json(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome={"Foo"},
            review_path=review_path,
        )
        data = _read_json(review_path)
        assert isinstance(data, dict)

    def test_empty_outcome_writes_empty_candidates(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome=set(),
            review_path=review_path,
        )
        data = _read_json(review_path)
        assert data.get("candidates") == []


# ── TL3: Review file shape ────────────────────────────────────────────────────


class TestReviewFileShape:
    def test_review_has_candidates_key(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome={"Foo"}, review_path=review_path)
        data = _read_json(review_path)
        assert "candidates" in data

    def test_candidate_has_required_fields(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome={"Foo"}, review_path=review_path)
        data = _read_json(review_path)
        c = data["candidates"][0]
        assert "tool" in c
        assert "query" in c
        assert "k" in c
        assert "expected_symbols" in c
        assert "gap" in c
        assert "provenance" in c
        # Review file has an 'approved' field defaulting to False
        assert "approved" in c
        assert c["approved"] is False


# ── TL4: promote approved candidates to live golden set ───────────────────────


class TestPromoteApproved:
    def test_approved_candidate_lands_in_live_set(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(query="auth token", symbol_names=["Auth.validate"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome={"Auth.validate"},
            review_path=review_path,
        )
        # Approve the first candidate
        review = _read_json(review_path)
        review["candidates"][0]["approved"] = True
        with open(review_path, "w") as f:
            json.dump(review, f)

        live_path = tmp_path / "live_goldens.json"
        promote_candidates(
            review_path=review_path,
            live_golden_path=live_path,
            repo_sha="abc123def456",
        )
        live = _read_json(live_path)
        queries = [q["query"] for q in live.get("queries", [])]
        assert "auth token" in queries


# ── TL5: unapproved candidates do NOT appear ─────────────────────────────────


class TestUnapprovedNotPromoted:
    def test_unapproved_not_in_live_set(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(query="secret query", symbol_names=["SecretSym"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome={"SecretSym"},
            review_path=review_path,
        )
        # Do NOT approve (leave approved=False)

        live_path = tmp_path / "live_goldens.json"
        promote_candidates(
            review_path=review_path,
            live_golden_path=live_path,
            repo_sha="abc123",
        )
        # When no candidates are approved, the live golden file may not be created.
        # File not existing == definitely no unapproved content in live set.
        if not live_path.exists():
            return
        live = _read_json(live_path)
        queries = [q.get("query", "") for q in live.get("queries", [])]
        assert "secret query" not in queries


# ── TL6: Live golden set keyed to commit SHA ──────────────────────────────────


class TestLiveGoldenSetShape:
    def test_live_golden_has_repo_sha(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome={"Foo"}, review_path=review_path)

        review = _read_json(review_path)
        review["candidates"][0]["approved"] = True
        with open(review_path, "w") as f:
            json.dump(review, f)

        live_path = tmp_path / "live_goldens.json"
        promote_candidates(
            review_path=review_path,
            live_golden_path=live_path,
            repo_sha="deadbeef1234",
        )
        live = _read_json(live_path)
        assert "repo_sha" in live
        assert live["repo_sha"] == "deadbeef1234"

    def test_live_golden_has_no_fixture_hash(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome={"Foo"}, review_path=review_path)

        review = _read_json(review_path)
        review["candidates"][0]["approved"] = True
        with open(review_path, "w") as f:
            json.dump(review, f)

        live_path = tmp_path / "live_goldens.json"
        promote_candidates(
            review_path=review_path,
            live_golden_path=live_path,
            repo_sha="deadbeef1234",
        )
        live = _read_json(live_path)
        assert "fixture_hash" not in live


# ── TL7: Promote is idempotent ────────────────────────────────────────────────


class TestPromoteIdempotent:
    def test_promote_twice_no_duplicate(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(query="auth", symbol_names=["Auth"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome={"Auth"}, review_path=review_path)
        review = _read_json(review_path)
        review["candidates"][0]["approved"] = True
        with open(review_path, "w") as f:
            json.dump(review, f)

        live_path = tmp_path / "live_goldens.json"
        promote_candidates(review_path=review_path, live_golden_path=live_path, repo_sha="sha1")
        promote_candidates(review_path=review_path, live_golden_path=live_path, repo_sha="sha1")
        live = _read_json(live_path)
        queries = [q["query"] for q in live.get("queries", [])]
        # Should appear exactly once
        assert queries.count("auth") == 1


# ── TL8: No fixture_hash in live set ─────────────────────────────────────────


# ── M1: _assert_not_fixture guard fires when fixture path is used ─────────────


class TestAssertNotFixtureGuard:
    """CRITICAL guard: the trace loop must NEVER write to the fixture golden.json.

    M1 review finding: no test proved the guard fires. These tests directly exercise
    the failure mode — passing the fixture path as the output target — and assert that
    (a) ValueError is raised, and (b) the fixture is byte-unchanged after the attempt.
    """

    def test_derive_raises_value_error_for_fixture_review_path(
        self, tmp_path: Path
    ) -> None:
        """derive_from_trace must raise ValueError when review_path IS the fixture golden."""
        from benchmarks.trace_loop import derive_from_trace  # noqa: PLC0415

        before = _read_fixture_golden_bytes()
        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Auth.validate"]),
        ])
        with pytest.raises(ValueError, match="fixture golden"):
            derive_from_trace(
                trace_path=trace_file,
                outcome={"Auth.validate"},
                review_path=_FIXTURE_GOLDEN,  # <-- the forbidden path
            )
        # Guard must have fired BEFORE any write — fixture is byte-unchanged.
        after = _read_fixture_golden_bytes()
        assert before == after, (
            "BUG: _assert_not_fixture guard fired but fixture was still modified!"
        )

    def test_promote_raises_value_error_for_fixture_live_path(
        self, tmp_path: Path
    ) -> None:
        """promote_candidates must raise ValueError when live_golden_path IS the fixture."""
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        # First derive into a valid review file (not the fixture).
        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Auth.validate"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(
            trace_path=trace_file,
            outcome={"Auth.validate"},
            review_path=review_path,
        )
        # Approve the candidate.
        review = _read_json(review_path)
        review["candidates"][0]["approved"] = True
        with open(review_path, "w") as f:
            json.dump(review, f)

        before = _read_fixture_golden_bytes()
        with pytest.raises(ValueError, match="fixture golden"):
            promote_candidates(
                review_path=review_path,
                live_golden_path=_FIXTURE_GOLDEN,  # <-- the forbidden path
                repo_sha="test-sha",
            )
        # Guard must have fired BEFORE any write — fixture is byte-unchanged.
        after = _read_fixture_golden_bytes()
        assert before == after, (
            "BUG: _assert_not_fixture guard fired but fixture was still modified!"
        )


# ── TL8: No fixture_hash in live set ─────────────────────────────────────────


class TestNoFixtureHashInLiveSet:
    def test_live_golden_queries_have_no_fixture_hash(self, tmp_path: Path) -> None:
        from benchmarks.trace_loop import derive_from_trace, promote_candidates  # noqa: PLC0415

        trace_file = _make_trace_ndjson(tmp_path, [
            _make_trace_record(symbol_names=["Foo"]),
        ])
        review_path = tmp_path / "review.json"
        derive_from_trace(trace_path=trace_file, outcome={"Foo"}, review_path=review_path)
        review = _read_json(review_path)
        review["candidates"][0]["approved"] = True
        with open(review_path, "w") as f:
            json.dump(review, f)

        live_path = tmp_path / "live_goldens.json"
        promote_candidates(review_path=review_path, live_golden_path=live_path, repo_sha="x")
        live = _read_json(live_path)
        # The live set must be SEPARATE from the fixture golden.json
        assert "fixture_hash" not in live
        # And must have the live-golden marker key
        assert "repo_sha" in live
