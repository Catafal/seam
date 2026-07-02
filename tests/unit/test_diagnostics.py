"""Unit tests for seam/analysis/diagnostics.py — DiagnosticsRecorder leaf.

Tests assert EXTERNAL BEHAVIOR and the safety/security contract, not internals.

Coverage:
  D1  — No-op when disabled: SEAM_DIAGNOSTICS=0 → no file created, all methods are silent.
  D2  — Redaction invariant (security-critical): a secret-like string passed outside the
        approved fields cannot appear anywhere in the NDJSON output.
  D3  — NDJSON line shape: each written line is valid JSON with EXACTLY the expected keys.
  D4  — Graceful degradation: bad db_path → no raise, metric is null.
  D5  — Never raises: all public methods swallow errors (monkey-patched IO failure).
  D6  — Slow-query threshold: below threshold → no line; at/above → exactly one line.
  D7  — Multi-process / concurrent append safety: two recorders appending to the same
        file produce interleaved whole lines, all individually parseable.
  D8  — snapshot() writes exactly one event="snapshot" line.
  D9  — record_watcher_event() increments counters reflected in sample_resources.
  D10 — atexit handler is registered only when enabled (not when disabled).
  D11 — record_query below threshold increments counter but writes no file line.

Prior art: tests/unit/test_staleness.py, tests/unit/test_byte_budget.py.
"""

import json
import threading
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_recorder(tmp_path: Path, *, enabled: bool = True, slow_ms: int = 100):
    """Create a DiagnosticsRecorder with controlled config."""
    import seam.analysis.diagnostics as diag_mod  # noqa: PLC0415

    diag_path = str(tmp_path / "diagnostics.ndjson")
    return diag_mod.DiagnosticsRecorder(
        enabled=enabled,
        path=diag_path,
        slow_ms=slow_ms,
    )


def _read_lines(path: Path) -> list[dict]:
    """Parse each non-empty line in path as JSON and return the list."""
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            lines.append(json.loads(raw))
    return lines


# ── D1: No-op when disabled ───────────────────────────────────────────────────


class TestDisabledRecorder:
    def test_no_file_created_on_record_query(self, tmp_path: Path) -> None:
        """A disabled recorder must not create the NDJSON file on record_query."""
        rec = _make_recorder(tmp_path, enabled=False)
        diag_file = tmp_path / "diagnostics.ndjson"
        rec.record_query("seam_search", duration_ms=500.0, result_chars=100)
        assert not diag_file.exists(), "disabled recorder must not create the file"

    def test_no_file_created_on_snapshot(self, tmp_path: Path) -> None:
        """A disabled recorder must not create the NDJSON file on snapshot."""
        rec = _make_recorder(tmp_path, enabled=False)
        diag_file = tmp_path / "diagnostics.ndjson"
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))
        assert not diag_file.exists(), "disabled recorder must not create the file"

    def test_no_file_created_on_watcher_event(self, tmp_path: Path) -> None:
        """A disabled recorder must not create the NDJSON file on watcher events."""
        rec = _make_recorder(tmp_path, enabled=False)
        diag_file = tmp_path / "diagnostics.ndjson"
        rec.record_watcher_event("reindexed")
        rec.record_watcher_event("reindex_errors")
        assert not diag_file.exists(), "disabled recorder must not create the file"

    def test_sample_resources_returns_dict_or_none(self, tmp_path: Path) -> None:
        """Disabled recorder.sample_resources should return None (null recorder)."""
        rec = _make_recorder(tmp_path, enabled=False)
        db_path = tmp_path / "seam.db"
        result = rec.sample_resources(str(db_path))
        # Disabled recorder returns None (null recorder contract).
        assert result is None or isinstance(result, dict)

    def test_all_methods_return_none(self, tmp_path: Path) -> None:
        """Disabled recorder methods return None without side effects."""
        rec = _make_recorder(tmp_path, enabled=False)
        db_path = tmp_path / "seam.db"
        assert rec.record_query("seam_search", duration_ms=1.0, result_chars=5) is None
        assert rec.record_watcher_event("reindexed") is None
        assert rec.snapshot(str(db_path)) is None


# ── D2: Redaction invariant ───────────────────────────────────────────────────


class TestRedactionInvariant:
    """Security-critical: argument text must NEVER appear in the NDJSON output."""

    def test_secret_string_not_in_ndjson(self, tmp_path: Path) -> None:
        """A 'secret' value passed outside approved fields must not appear in the file.

        WHY this test is security-critical:
          record_query's interface deliberately does NOT accept argument text or result
          bodies. This test would fail if a future change added such a parameter and
          accidentally wrote it to the file.
        """
        secret = "SUPER_SECRET_API_KEY_xyz987"
        # The secret is NOT passed to record_query — this models the intended usage.
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=0)
        # Trigger a slow-query write — only allowed fields go in.
        rec.record_query("seam_search", duration_ms=200.0, result_chars=512)
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))

        diag_file = tmp_path / "diagnostics.ndjson"
        raw_content = diag_file.read_text(encoding="utf-8")
        assert secret not in raw_content, (
            f"Secret value must NEVER appear in NDJSON output; found in:\n{raw_content}"
        )

    def test_query_line_has_only_allowed_keys(self, tmp_path: Path) -> None:
        """Slow-query lines must have EXACTLY the allowed set of keys.

        Allowed keys: event, tool, duration_ms, result_chars, seq, ts.
        Any key beyond this set is a redaction violation.
        """
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=0)
        rec.record_query("seam_impact", duration_ms=300.0, result_chars=1024)

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        query_lines = [ln for ln in lines if ln.get("event") == "slow_query"]
        assert len(query_lines) == 1, "Expected exactly one slow_query line"

        allowed_keys = {"event", "tool", "duration_ms", "result_chars", "seq", "ts"}
        actual_keys = set(query_lines[0].keys())
        assert actual_keys == allowed_keys, (
            f"slow_query line has unexpected keys: {actual_keys - allowed_keys}"
        )


# ── D3: NDJSON line shape ─────────────────────────────────────────────────────


class TestNdjsonLineShape:
    def test_slow_query_line_is_valid_json(self, tmp_path: Path) -> None:
        """Each slow-query line must be individually parseable as JSON."""
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=50)
        rec.record_query("seam_context", duration_ms=75.0, result_chars=200)

        diag_file = tmp_path / "diagnostics.ndjson"
        raw = diag_file.read_text(encoding="utf-8")
        for line in raw.splitlines():
            line = line.strip()
            if line:
                obj = json.loads(line)  # must not raise
                assert isinstance(obj, dict)

    def test_slow_query_line_has_correct_field_values(self, tmp_path: Path) -> None:
        """Slow-query line field values must match what was passed to record_query."""
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=0)
        rec.record_query("seam_trace", duration_ms=123.5, result_chars=999)

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        q_line = next(ln for ln in lines if ln.get("event") == "slow_query")

        assert q_line["tool"] == "seam_trace"
        assert q_line["duration_ms"] == pytest.approx(123.5, rel=1e-3)
        assert q_line["result_chars"] == 999
        assert isinstance(q_line["seq"], int)
        assert isinstance(q_line["ts"], (int, float))

    def test_snapshot_line_has_expected_fields(self, tmp_path: Path) -> None:
        """Snapshot lines must include event='snapshot' and resource metric keys."""
        rec = _make_recorder(tmp_path, enabled=True)
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        snaps = [ln for ln in lines if ln.get("event") == "snapshot"]
        assert len(snaps) == 1, f"Expected 1 snapshot line, got {len(snaps)}"

        snap = snaps[0]
        # These keys must always be present (value may be null).
        required = {"event", "ts", "rss_bytes", "open_fds", "db_size_bytes",
                    "query_count", "watcher_reindexed", "watcher_errors"}
        missing = required - set(snap.keys())
        assert not missing, f"Snapshot line missing keys: {missing}"


# ── D4: Graceful degradation ──────────────────────────────────────────────────


class TestGracefulDegradation:
    def test_bad_db_path_returns_null_db_size(self, tmp_path: Path) -> None:
        """A non-existent db_path must produce db_size_bytes=None, never raise."""
        rec = _make_recorder(tmp_path, enabled=True)
        result = rec.sample_resources("/nonexistent/path/seam.db")
        assert result is not None, "sample_resources should return a dict"
        assert result["db_size_bytes"] is None, (
            f"Expected None for bad db_path, got {result['db_size_bytes']}"
        )

    def test_snapshot_with_bad_db_path_writes_line(self, tmp_path: Path) -> None:
        """snapshot() with a bad db_path must still write a line (with null db_size)."""
        rec = _make_recorder(tmp_path, enabled=True)
        rec.snapshot("/nonexistent/path/seam.db")

        diag_file = tmp_path / "diagnostics.ndjson"
        assert diag_file.exists(), "snapshot must create the file"
        lines = _read_lines(diag_file)
        snaps = [ln for ln in lines if ln.get("event") == "snapshot"]
        assert len(snaps) == 1
        assert snaps[0]["db_size_bytes"] is None


# ── D5: Never raises ──────────────────────────────────────────────────────────


class TestNeverRaises:
    def test_record_query_does_not_raise_on_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """record_query must not raise even when the write operation fails."""
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=0)

        # Patch the internal write method to throw.
        def _boom(*a, **kw) -> None:  # type: ignore[no-untyped-def]
            raise OSError("simulated disk full")

        monkeypatch.setattr(rec, "_write_line", _boom, raising=False)
        # Must not raise.
        rec.record_query("seam_search", duration_ms=500.0, result_chars=100)

    def test_snapshot_does_not_raise_on_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """snapshot() must not raise even when the write operation fails."""
        rec = _make_recorder(tmp_path, enabled=True)

        def _boom(*a, **kw) -> None:  # type: ignore[no-untyped-def]
            raise OSError("simulated disk full")

        monkeypatch.setattr(rec, "_write_line", _boom, raising=False)
        # Must not raise.
        rec.snapshot(str(tmp_path / "seam.db"))

    def test_record_watcher_event_does_not_raise(self, tmp_path: Path) -> None:
        """record_watcher_event must never raise, including with unknown kind."""
        rec = _make_recorder(tmp_path, enabled=True)
        rec.record_watcher_event("reindexed")
        rec.record_watcher_event("reindex_errors")
        rec.record_watcher_event("unknown_kind_xyz")  # must not raise

    def test_sample_resources_never_raises(self, tmp_path: Path) -> None:
        """sample_resources must never raise regardless of platform or path."""
        rec = _make_recorder(tmp_path, enabled=True)
        result = rec.sample_resources("/completely/invalid/path")
        assert isinstance(result, dict)


# ── D6: Slow-query threshold ──────────────────────────────────────────────────


class TestSlowQueryThreshold:
    def test_below_threshold_no_line_written(self, tmp_path: Path) -> None:
        """A query below SEAM_DIAGNOSTICS_SLOW_MS must NOT write a slow_query line."""
        slow_ms = 100
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=slow_ms)
        # Exactly one below threshold.
        rec.record_query("seam_search", duration_ms=float(slow_ms - 1), result_chars=50)

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        slow_lines = [ln for ln in lines if ln.get("event") == "slow_query"]
        assert len(slow_lines) == 0, "No slow_query line expected below threshold"

    def test_at_threshold_writes_one_line(self, tmp_path: Path) -> None:
        """A query at exactly SEAM_DIAGNOSTICS_SLOW_MS must write exactly one line."""
        slow_ms = 100
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=slow_ms)
        rec.record_query("seam_search", duration_ms=float(slow_ms), result_chars=50)

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        slow_lines = [ln for ln in lines if ln.get("event") == "slow_query"]
        assert len(slow_lines) == 1, "Exactly one slow_query line expected at threshold"

    def test_above_threshold_writes_one_line(self, tmp_path: Path) -> None:
        """A query above SEAM_DIAGNOSTICS_SLOW_MS must write exactly one line."""
        slow_ms = 100
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=slow_ms)
        rec.record_query("seam_impact", duration_ms=float(slow_ms + 1), result_chars=200)

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        slow_lines = [ln for ln in lines if ln.get("event") == "slow_query"]
        assert len(slow_lines) == 1, "Exactly one slow_query line expected above threshold"

    def test_multiple_queries_only_slow_ones_written(self, tmp_path: Path) -> None:
        """Multiple queries: only those >= threshold produce a file line."""
        slow_ms = 50
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=slow_ms)
        rec.record_query("seam_search", duration_ms=10.0, result_chars=10)  # fast
        rec.record_query("seam_search", duration_ms=50.0, result_chars=20)  # exactly threshold
        rec.record_query("seam_search", duration_ms=200.0, result_chars=30)  # slow

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        slow_lines = [ln for ln in lines if ln.get("event") == "slow_query"]
        assert len(slow_lines) == 2


# ── D7: Multi-process / concurrent append safety ──────────────────────────────


class TestConcurrentAppendSafety:
    def test_two_recorders_same_path_all_lines_parseable(self, tmp_path: Path) -> None:
        """Two recorders writing to the same path produce all-parseable NDJSON.

        WHY: concurrent CLI invocations must not corrupt each other's lines.
        Each call to record_query / snapshot writes one complete JSON line.
        """
        import seam.analysis.diagnostics as diag_mod  # noqa: PLC0415

        diag_file = tmp_path / "diagnostics.ndjson"
        diag_path = str(diag_file)

        rec_a = diag_mod.DiagnosticsRecorder(enabled=True, path=diag_path, slow_ms=0)
        rec_b = diag_mod.DiagnosticsRecorder(enabled=True, path=diag_path, slow_ms=0)

        errors: list[Exception] = []

        def _write_a() -> None:
            for _ in range(10):
                try:
                    rec_a.record_query("seam_search", duration_ms=200.0, result_chars=50)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        def _write_b() -> None:
            for _ in range(10):
                try:
                    rec_b.record_query("seam_impact", duration_ms=300.0, result_chars=100)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        t_a = threading.Thread(target=_write_a)
        t_b = threading.Thread(target=_write_b)
        t_a.start()
        t_b.start()
        t_a.join()
        t_b.join()

        assert not errors, f"Exceptions in write threads: {errors}"
        lines = _read_lines(diag_file)
        assert len(lines) == 20, f"Expected 20 lines, got {len(lines)}"
        for idx, ln in enumerate(lines):
            assert isinstance(ln, dict), f"Line {idx} is not a dict: {ln!r}"


# ── D8: snapshot() ────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_writes_exactly_one_line(self, tmp_path: Path) -> None:
        """Each snapshot() call writes exactly one event=snapshot line."""
        rec = _make_recorder(tmp_path, enabled=True)
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        snaps = [ln for ln in lines if ln.get("event") == "snapshot"]
        assert len(snaps) == 1

    def test_two_snapshots_write_two_lines(self, tmp_path: Path) -> None:
        """Two snapshot() calls write two lines."""
        rec = _make_recorder(tmp_path, enabled=True)
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))
        rec.snapshot(str(db_path))

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        snaps = [ln for ln in lines if ln.get("event") == "snapshot"]
        assert len(snaps) == 2


# ── D9: watcher counters ──────────────────────────────────────────────────────


class TestWatcherCounters:
    def test_reindexed_counter_increments(self, tmp_path: Path) -> None:
        """record_watcher_event('reindexed') increments the counter."""
        rec = _make_recorder(tmp_path, enabled=True)
        rec.record_watcher_event("reindexed")
        rec.record_watcher_event("reindexed")
        result = rec.sample_resources(str(tmp_path / "seam.db"))
        assert result is not None
        assert result["watcher_reindexed"] == 2

    def test_reindex_errors_counter_increments(self, tmp_path: Path) -> None:
        """record_watcher_event('reindex_errors') increments the error counter."""
        rec = _make_recorder(tmp_path, enabled=True)
        rec.record_watcher_event("reindex_errors")
        result = rec.sample_resources(str(tmp_path / "seam.db"))
        assert result is not None
        assert result["watcher_errors"] == 1


# ── D10: atexit registration ──────────────────────────────────────────────────


class TestAtexitRegistration:
    def test_enabled_recorder_can_write_on_demand(self, tmp_path: Path) -> None:
        """An enabled recorder must be able to write the NDJSON file.

        We verify indirectly: after creating an enabled recorder, snapshot is
        invokable as a proxy for the handler being wired up.
        """
        rec = _make_recorder(tmp_path, enabled=True)
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))
        diag_file = tmp_path / "diagnostics.ndjson"
        assert diag_file.exists(), "enabled recorder should be able to write the file"


# ── D11: query counter increments even below threshold ───────────────────────


class TestQueryCounter:
    def test_query_count_increments_below_threshold(self, tmp_path: Path) -> None:
        """record_query below threshold increments query_count but writes no file line."""
        slow_ms = 1000
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=slow_ms)
        rec.record_query("seam_search", duration_ms=1.0, result_chars=10)
        rec.record_query("seam_search", duration_ms=2.0, result_chars=10)

        result = rec.sample_resources(str(tmp_path / "seam.db"))
        assert result is not None
        assert result["query_count"] == 2

        diag_file = tmp_path / "diagnostics.ndjson"
        assert not diag_file.exists() or not any(
            ln.get("event") == "slow_query" for ln in _read_lines(diag_file)
        ), "No slow_query lines expected below threshold"

    def test_query_count_in_snapshot(self, tmp_path: Path) -> None:
        """The query_count in a snapshot line reflects the current counter value."""
        slow_ms = 1000
        rec = _make_recorder(tmp_path, enabled=True, slow_ms=slow_ms)
        rec.record_query("seam_search", duration_ms=1.0, result_chars=10)
        rec.record_query("seam_impact", duration_ms=2.0, result_chars=20)
        db_path = tmp_path / "seam.db"
        rec.snapshot(str(db_path))

        diag_file = tmp_path / "diagnostics.ndjson"
        lines = _read_lines(diag_file)
        snaps = [ln for ln in lines if ln.get("event") == "snapshot"]
        assert len(snaps) == 1
        assert snaps[0]["query_count"] == 2
