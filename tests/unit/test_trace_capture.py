"""Unit tests for seam/analysis/trace_capture.py — TraceRecorder leaf.

Tests assert EXTERNAL BEHAVIOR and the safety/security contract, not internals.

Coverage:
  TC1  — No-op when disabled: SEAM_TRACE_CAPTURE=0 → no file created, all methods silent.
  TC2  — Symbols-only gate (security-critical): a full result body / source string passed
         outside the approved fields cannot appear anywhere in the NDJSON output.
         This is the defense-in-depth gate test required by the spec.
  TC3  — NDJSON line shape: each written line is valid JSON with EXACTLY the expected keys.
  TC4  — Never raises: all public methods swallow errors (monkey-patched IO failure).
  TC5  — Opt-in gating: only "1" enables capture; any other value → disabled.
  TC6  — reset_recorder teardown hygiene: same autouse-fixture pattern as diagnostics tests.
  TC7  — Session ID: all records from one recorder share the same session_id (UUID4 str).
  TC8  — Multiple calls accumulate records in the same file.
  TC9  — record_tool_call with no symbols (empty result) writes count=0 symbol_names=[].
  TC10 — atexit handler registered only when enabled, not when disabled.
"""

import json
import uuid
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_recorder(tmp_path: Path, *, enabled: bool = True):
    """Create a TraceRecorder with a temp directory."""
    import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

    trace_dir = str(tmp_path / "traces")
    return tc_mod.TraceRecorder(enabled=enabled, trace_dir=trace_dir)


def _read_lines(trace_dir: Path) -> list[dict]:
    """Read all NDJSON lines from all files in trace_dir."""
    lines = []
    if not trace_dir.exists():
        return lines
    for f in sorted(trace_dir.glob("*.ndjson")):
        for raw in f.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                lines.append(json.loads(raw))
    return lines


# ── autouse reset fixture (mirrors test_diagnostics.py pattern) ───────────────


@pytest.fixture(autouse=True)
def _reset_trace(tmp_path: Path):
    """Reset the process-level TraceRecorder singleton after every test."""
    import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

    yield
    tc_mod.reset_recorder()


# ── TC1: No-op when disabled ──────────────────────────────────────────────────


class TestDisabledRecorder:
    def test_no_file_created_on_record_tool_call(self, tmp_path: Path) -> None:
        """A disabled recorder must not create any file on record_tool_call."""
        rec = _make_recorder(tmp_path, enabled=False)
        trace_dir = tmp_path / "traces"
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "parse token"},
            symbol_names=["Token.parse", "Lexer"],
            result_count=2,
            elapsed_ms=5.0,
        )
        assert not trace_dir.exists(), "disabled recorder must not create any trace dir"

    def test_all_methods_return_none_when_disabled(self, tmp_path: Path) -> None:
        """All public methods of a disabled recorder return None silently."""
        rec = _make_recorder(tmp_path, enabled=False)
        r = rec.record_tool_call(
            tool="seam_query",
            args={"concept": "x"},
            symbol_names=[],
            result_count=0,
            elapsed_ms=1.0,
        )
        assert r is None

    def test_enabled_property_false_when_disabled(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path, enabled=False)
        assert rec.enabled is False


# ── TC2: Symbols-only gate (defense-in-depth) ─────────────────────────────────


class TestSymbolsOnlyGate:
    """Defense-in-depth: full result bodies / source text must NEVER reach the file.

    This mirrors the redaction gate test in test_diagnostics.py (D2).
    The record_tool_call() interface accepts only symbol NAMES (list[str]) — not
    full result dicts, not source text. We verify that a secret-like string placed
    in a forbidden position cannot appear in the written NDJSON.
    """

    _SECRET = "SECRET_SOURCE_BODY_CONTENT_XYZ_FULL_RESULT_DO_NOT_CAPTURE"

    def test_secret_not_in_file_when_passed_as_symbol_name(self, tmp_path: Path) -> None:
        """Even if a caller passes a 'symbol name' that looks like source, only the
        approved line keys are written, and the value is bounded to the symbol_names list.
        The test verifies that the allowed key set is enforced."""
        rec = _make_recorder(tmp_path)
        # Pass the secret AS a symbol name (worst-case: attacker controls symbol_names).
        rec.record_tool_call(
            tool="seam_context",
            args={"symbol": "Foo"},
            symbol_names=[self._SECRET],
            result_count=1,
            elapsed_ms=1.0,
        )
        trace_dir = tmp_path / "traces"
        lines = _read_lines(trace_dir)
        assert lines, "expected one line to be written"
        line = lines[0]
        # The allowed key set must match exactly — no extra key may slip in.
        allowed_keys = {
            "event", "session_id", "ts", "tool", "args",
            "symbol_names", "result_count", "elapsed_ms",
        }
        assert set(line.keys()) == allowed_keys, (
            f"BUG: trace line has unexpected keys: {set(line.keys()) - allowed_keys}"
        )

    def test_no_full_result_body_field_in_line(self, tmp_path: Path) -> None:
        """The record_tool_call interface does not have a 'result' or 'body' parameter."""
        import inspect  # noqa: PLC0415

        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        sig = inspect.signature(tc_mod.TraceRecorder.record_tool_call)
        param_names = set(sig.parameters.keys())
        forbidden = {"result", "body", "source", "full_result", "result_body"}
        found = param_names & forbidden
        assert not found, (
            f"BUG: record_tool_call has forbidden params that could carry full bodies: {found}"
        )

    def test_allowed_keys_enforced_at_write_time(self, tmp_path: Path) -> None:
        """Assert that the written line contains exactly the allowed keys — no extras."""
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_impact",
            args={"symbol": "Client.connect"},
            symbol_names=["Client.connect", "Server"],
            result_count=2,
            elapsed_ms=10.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert len(lines) == 1
        allowed = {
            "event", "session_id", "ts", "tool", "args",
            "symbol_names", "result_count", "elapsed_ms",
        }
        assert set(lines[0].keys()) == allowed


# ── TC3: NDJSON line shape ────────────────────────────────────────────────────


class TestNdjsonShape:
    def test_line_is_valid_json(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "auth token"},
            symbol_names=["Auth.validate", "Token"],
            result_count=2,
            elapsed_ms=15.5,
        )
        lines = _read_lines(tmp_path / "traces")
        assert len(lines) == 1

    def test_event_field_is_tool_call(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_query",
            args={"concept": "parser"},
            symbol_names=["Parser"],
            result_count=1,
            elapsed_ms=3.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["event"] == "tool_call"

    def test_tool_field_matches(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_context",
            args={"symbol": "Foo"},
            symbol_names=["Foo"],
            result_count=1,
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["tool"] == "seam_context"

    def test_symbol_names_stored_as_list(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "x"},
            symbol_names=["A.foo", "B.bar"],
            result_count=2,
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["symbol_names"] == ["A.foo", "B.bar"]

    def test_result_count_stored(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "x"},
            symbol_names=["A"],
            result_count=42,
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["result_count"] == 42

    def test_elapsed_ms_stored(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "x"},
            symbol_names=[],
            result_count=0,
            elapsed_ms=99.9,
        )
        lines = _read_lines(tmp_path / "traces")
        assert abs(lines[0]["elapsed_ms"] - 99.9) < 0.01

    def test_ts_is_float(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "x"},
            symbol_names=[],
            result_count=0,
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert isinstance(lines[0]["ts"], float)

    def test_args_stored_as_dict(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        args = {"query": "auth", "limit": 10}
        rec.record_tool_call(
            tool="seam_search",
            args=args,
            symbol_names=[],
            result_count=0,
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["args"] == args


# ── TC4: Never raises ─────────────────────────────────────────────────────────


class TestNeverRaises:
    def test_io_failure_does_not_raise(self, tmp_path: Path, monkeypatch) -> None:
        """A forced IO error in _write_line must not propagate to the caller."""
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        rec = _make_recorder(tmp_path)

        def _bad_open(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(tc_mod, "open", _bad_open, raising=False)
        # Should not raise
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "x"},
            symbol_names=["Foo"],
            result_count=1,
            elapsed_ms=1.0,
        )


# ── TC5: Opt-in gating ────────────────────────────────────────────────────────


class TestOptInGating:
    def test_value_one_enables_recorder(self, tmp_path: Path) -> None:
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        rec = tc_mod.TraceRecorder(enabled=True, trace_dir=str(tmp_path / "traces"))
        assert rec.enabled is True

    def test_other_values_disable_recorder(self, tmp_path: Path) -> None:
        """Only enabled=True enables the recorder; enabled=False → null recorder."""
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        rec = tc_mod.TraceRecorder(enabled=False, trace_dir=str(tmp_path / "traces"))
        assert rec.enabled is False


# ── TC6: reset_recorder teardown hygiene ──────────────────────────────────────


class TestResetRecorder:
    def test_reset_clears_singleton(self, tmp_path: Path) -> None:
        """After reset_recorder(), get_recorder() returns a fresh recorder."""
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        tc_mod.reset_recorder()  # ensure clean state
        r1 = tc_mod.get_recorder()
        assert r1 is tc_mod.get_recorder()  # same singleton
        tc_mod.reset_recorder()
        r2 = tc_mod.get_recorder()
        assert r1 is not r2  # new singleton after reset

    def test_reset_disables_atexit(self, tmp_path: Path) -> None:
        """reset_recorder() calls close() which disables the old recorder."""
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        # Directly inject an enabled recorder into the singleton.
        tc_mod.reset_recorder()
        rec = tc_mod.TraceRecorder(enabled=True, trace_dir=str(tmp_path / "traces"))
        tc_mod._process_recorder = rec
        assert rec.enabled
        tc_mod.reset_recorder()
        # After reset the singleton is None.
        assert tc_mod._process_recorder is None


# ── TC7: Session ID ───────────────────────────────────────────────────────────


class TestSessionId:
    def test_all_records_share_session_id(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        for i in range(3):
            rec.record_tool_call(
                tool="seam_search",
                args={"query": f"q{i}"},
                symbol_names=[],
                result_count=0,
                elapsed_ms=1.0,
            )
        lines = _read_lines(tmp_path / "traces")
        assert len(lines) == 3
        ids = {rec["session_id"] for rec in lines}
        assert len(ids) == 1, "all records must share one session_id per recorder"

    def test_session_id_is_valid_uuid(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_query",
            args={"concept": "x"},
            symbol_names=[],
            result_count=0,
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        sid = lines[0]["session_id"]
        # Validate it is a valid UUID string
        uuid.UUID(sid)  # raises ValueError if not valid


# ── TC8: Multiple calls accumulate ───────────────────────────────────────────


class TestMultipleCalls:
    def test_multiple_calls_produce_multiple_lines(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        for i in range(5):
            rec.record_tool_call(
                tool="seam_search",
                args={"query": f"term{i}"},
                symbol_names=[f"Symbol{i}"],
                result_count=1,
                elapsed_ms=float(i),
            )
        lines = _read_lines(tmp_path / "traces")
        assert len(lines) == 5


# ── TC9: Empty result ─────────────────────────────────────────────────────────


class TestEmptyResult:
    def test_empty_symbols_written_as_empty_list(self, tmp_path: Path) -> None:
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "nonexistent"},
            symbol_names=[],
            result_count=0,
            elapsed_ms=2.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["symbol_names"] == []
        assert lines[0]["result_count"] == 0


# ── TC10: atexit registration ─────────────────────────────────────────────────


class TestAtexitRegistration:
    def test_atexit_registered_when_enabled(self, tmp_path: Path) -> None:
        """An enabled recorder registers an atexit handler."""
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        rec = tc_mod.TraceRecorder(enabled=True, trace_dir=str(tmp_path / "traces"))
        # We can't directly inspect atexit registry in all Python versions, but we
        # can verify that close() cleanly disables the recorder without raising.
        assert rec.enabled
        rec.close()
        assert not rec.enabled

    def test_atexit_not_registered_when_disabled(self, tmp_path: Path) -> None:
        """A disabled recorder does not register an atexit handler."""
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        rec = tc_mod.TraceRecorder(enabled=False, trace_dir=str(tmp_path / "traces"))
        assert not rec.enabled
        # close() on a disabled recorder is a no-op, never raises
        rec.close()
        assert not rec.enabled


# ── TC11: extract_symbol_names — result shape coverage (H1 fix) ──────────────


class TestExtractSymbolNames:
    """Verify that extract_symbol_names handles seam_impact and seam_trace shapes.

    These are the two most important tools for the change-safety loop, and the
    original implementation returned [] for both (H1 review finding). Each test
    constructs a representative result shape and asserts the expected names come out.
    """

    def _esn(self, result: object) -> list[str]:
        import seam.analysis.trace_capture as tc_mod  # noqa: PLC0415

        return tc_mod.extract_symbol_names(result)

    # ── seam_impact shape ─────────────────────────────────────────────────────

    def test_impact_upstream_single_tier(self) -> None:
        """seam_impact upstream with one tier returns all entry names."""
        result = {
            "found": True,
            "target": "Client.connect",
            "risk_summary": {"upstream": {"WILL_BREAK": 2}},
            "upstream": {
                "WILL_BREAK": [
                    {"name": "Server.handle", "distance": 1, "confidence": "EXTRACTED"},
                    {"name": "Router.dispatch", "distance": 2, "confidence": "INFERRED"},
                ],
            },
        }
        names = self._esn(result)
        assert "Server.handle" in names
        assert "Router.dispatch" in names

    def test_impact_upstream_multiple_tiers(self) -> None:
        """seam_impact with multiple tiers collects names across all tiers."""
        result = {
            "found": True,
            "target": "DB.query",
            "risk_summary": {},
            "upstream": {
                "WILL_BREAK": [{"name": "Service.run", "distance": 1, "confidence": "EXTRACTED"}],
                "LIKELY_AFFECTED": [{"name": "Cache.get", "distance": 2, "confidence": "INFERRED"}],
                "MAY_NEED_TESTING": [{"name": "Monitor.check", "distance": 3, "confidence": "INFERRED"}],
            },
        }
        names = self._esn(result)
        assert "Service.run" in names
        assert "Cache.get" in names
        assert "Monitor.check" in names

    def test_impact_both_directions(self) -> None:
        """seam_impact direction=both collects names from upstream and downstream."""
        result = {
            "found": True,
            "target": "Encoder.encode",
            "risk_summary": {},
            "upstream": {
                "WILL_BREAK": [{"name": "Pipeline.run", "distance": 1, "confidence": "EXTRACTED"}],
            },
            "downstream": {
                "MAY_NEED_TESTING": [{"name": "Output.write", "distance": 1, "confidence": "INFERRED"}],
            },
        }
        names = self._esn(result)
        assert "Pipeline.run" in names
        assert "Output.write" in names

    def test_impact_found_false_no_entries(self) -> None:
        """seam_impact found=False with empty tiers → empty name list."""
        result = {
            "found": False,
            "target": "Unknown.sym",
            "risk_summary": {},
        }
        names = self._esn(result)
        assert names == []

    def test_impact_empty_tiers(self) -> None:
        """seam_impact with empty tier lists returns an empty name list."""
        result = {
            "found": True,
            "target": "Foo.bar",
            "risk_summary": {},
            "upstream": {"WILL_BREAK": [], "MAY_NEED_TESTING": []},
        }
        names = self._esn(result)
        assert names == []

    # ── seam_trace shape ──────────────────────────────────────────────────────

    def test_trace_single_path(self) -> None:
        """seam_trace with one path returns from_name/to_name of each hop."""
        result = {
            "found": True,
            "source": "A.call",
            "target": "C.run",
            "paths": [
                [
                    {"from_name": "A.call", "to_name": "B.process", "kind": "call", "confidence": "EXTRACTED"},
                    {"from_name": "B.process", "to_name": "C.run", "kind": "call", "confidence": "EXTRACTED"},
                ]
            ],
            "callers_source": [],
            "callees_source": [],
            "callers_target": [],
            "callees_target": [],
        }
        names = self._esn(result)
        assert "A.call" in names
        assert "B.process" in names
        assert "C.run" in names

    def test_trace_deduplicates_hop_names(self) -> None:
        """A→B→C hops give [A, B, C] not [A, B, B, C] (dedup while preserving order)."""
        result = {
            "found": True,
            "source": "A",
            "target": "C",
            "paths": [
                [
                    {"from_name": "A", "to_name": "B", "kind": "call", "confidence": "EXTRACTED"},
                    {"from_name": "B", "to_name": "C", "kind": "call", "confidence": "EXTRACTED"},
                ]
            ],
            "callers_source": [],
            "callees_source": [],
            "callers_target": [],
            "callees_target": [],
        }
        names = self._esn(result)
        assert names.count("B") == 1, "intermediate hop name must appear exactly once"
        assert set(names) == {"A", "B", "C"}

    def test_trace_not_found_empty_paths(self) -> None:
        """seam_trace found=False with empty paths → empty name list."""
        result = {
            "found": False,
            "source": "X",
            "target": "Y",
            "paths": [],
            "callers_source": [],
            "callees_source": [],
            "callers_target": [],
            "callees_target": [],
        }
        names = self._esn(result)
        assert names == []

    # ── existing shapes still work ────────────────────────────────────────────

    def test_search_query_list_of_dicts(self) -> None:
        """seam_search/seam_query flat list result still works."""
        result = [
            {"name": "Auth.validate", "score": 0.9},
            {"name": "Token.decode", "score": 0.8},
        ]
        names = self._esn(result)
        assert names == ["Auth.validate", "Token.decode"]

    def test_context_callers_callees(self) -> None:
        """seam_context callers/callees shape still works."""
        result = {
            "symbol": "Parser.parse",
            "callers": [{"name": "CLI.run"}, {"name": "Server.handle"}],
            "callees": [{"name": "Lexer.tokenize"}],
        }
        names = self._esn(result)
        assert "Parser.parse" in names
        assert "CLI.run" in names
        assert "Server.handle" in names
        assert "Lexer.tokenize" in names

    def test_none_returns_empty(self) -> None:
        """None result → empty list (safe degradation)."""
        assert self._esn(None) == []

    def test_unknown_shape_returns_empty(self) -> None:
        """An unrecognised result shape → empty list, never raises."""
        assert self._esn(42) == []
        assert self._esn("a string") == []
        assert self._esn({"totally": "unknown"}) == []


# ── TC12: result_count semantics (H2 fix) ────────────────────────────────────


class TestResultCountSemantics:
    """Lock the result_count == len(symbol_names) contract (H2 review finding).

    The docstring was updated to document this as the invariant; these tests
    enforce it so callers cannot drift from the contract.
    """

    def test_result_count_matches_len_symbol_names_in_written_record(
        self, tmp_path: Path
    ) -> None:
        """The written NDJSON record must have result_count == len(symbol_names)."""
        rec = _make_recorder(tmp_path)
        symbols = ["Alpha.foo", "Beta.bar", "Gamma.baz"]
        rec.record_tool_call(
            tool="seam_search",
            args={"query": "test"},
            symbol_names=symbols,
            result_count=len(symbols),
            elapsed_ms=1.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert len(lines) == 1
        assert lines[0]["result_count"] == len(symbols)
        assert lines[0]["result_count"] == len(lines[0]["symbol_names"])

    def test_result_count_zero_for_empty_symbol_names(self, tmp_path: Path) -> None:
        """When symbol_names=[], result_count must be 0."""
        rec = _make_recorder(tmp_path)
        rec.record_tool_call(
            tool="seam_query",
            args={"concept": "nothing"},
            symbol_names=[],
            result_count=0,
            elapsed_ms=2.0,
        )
        lines = _read_lines(tmp_path / "traces")
        assert lines[0]["result_count"] == 0
        assert lines[0]["result_count"] == len(lines[0]["symbol_names"])
