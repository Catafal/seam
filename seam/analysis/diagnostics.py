"""Opt-in local diagnostics recorder for Seam (P5.5).

LEAF MODULE — stdlib-only imports. No new runtime dependency.
Never raises. All IO / sampling wrapped in try/except; errors are logged at WARNING
and degrade to a no-op for that call. Mirrors the leaf discipline of staleness.py /
byte_budget.py / steer.py.

When SEAM_DIAGNOSTICS != "1" (the default), DiagnosticsRecorder is a null recorder:
  - every method is a no-op
  - no file is opened
  - no sampling runs
  - no atexit handler is registered
  - zero measurable overhead on the read path (byte-identical to pre-P5.5)

When SEAM_DIAGNOSTICS == "1", an active recorder:
  - record_query(tool, duration_ms, result_chars) — increments an in-memory query
    counter; appends ONE slow_query NDJSON line when duration_ms >= SEAM_DIAGNOSTICS_SLOW_MS.
    SECURITY: the interface deliberately does NOT accept argument text or result bodies —
    they are not parameters so they cannot be written even by mistake (structural redaction).
  - record_watcher_event(kind) — increments reindexed / reindex_errors counters.
  - sample_resources(db_path) → dict — RSS, open FDs, DB size, counters; None for
    any metric that cannot be obtained on the current platform.
  - snapshot(db_path) — calls sample_resources and appends one event="snapshot" line.
  - At process exit, an atexit handler calls snapshot() for a final flush.

NDJSON writer: append mode (Python open(path, "a")) with one json.dumps + newline
per call, so concurrent CLI processes interleave whole lines without corruption.

RSS note: resource.getrusage(RUSAGE_SELF).ru_maxrss is PEAK RSS, not current RSS.
  - Linux: ru_maxrss is in KiB → multiply by 1024 to normalize to bytes.
  - macOS: ru_maxrss is already in bytes.
  - Other platforms: use sys.platform to detect; fall back to None.
"""

import atexit
import json
import logging
import os
import sys
import time
from typing import Any

import seam.config as config

# `resource` is stdlib but does not exist on Windows — guard the import at module
# level (imports-at-top rule) and degrade RSS sampling to None when it is absent.
try:
    import resource as _resource
except ImportError:  # pragma: no cover — Windows only
    _resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── Public type aliases ───────────────────────────────────────────────────────

ResourceMetrics = dict[str, int | None]

# ── Allowed slow_query line keys (redaction invariant) ───────────────────────
# Any change to this set must be reflected in the test_diagnostics.py allowed_keys check.
_SLOW_QUERY_KEYS = frozenset({"event", "tool", "duration_ms", "result_chars", "seq", "ts"})

# ── Snapshot line keys ────────────────────────────────────────────────────────
_SNAPSHOT_REQUIRED_KEYS = frozenset({
    "event", "ts", "rss_bytes", "open_fds", "db_size_bytes",
    "query_count", "watcher_reindexed", "watcher_errors",
})


class DiagnosticsRecorder:
    """Per-process diagnostics recorder with null-recorder support when disabled.

    Construct once per process via DiagnosticsRecorder(enabled=..., path=..., slow_ms=...).
    When not enabled, all methods are no-ops and no file handle is opened.
    When enabled, records slow queries and resource snapshots to a local NDJSON file.

    Thread-safe: counters use simple integer operations (GIL-protected on CPython).
    File writes are serialized by the OS O_APPEND guarantee — concurrent processes
    appending whole lines don't corrupt each other's records.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        path: str,
        slow_ms: int,
    ) -> None:
        self._enabled = enabled
        self._path = path
        self._slow_ms = slow_ms
        self._query_count: int = 0
        self._watcher_reindexed: int = 0
        self._watcher_errors: int = 0
        self._seq: int = 0  # monotonic sequence number for slow_query lines
        # Resolved DB path for the atexit snapshot's db_size measurement. Callers
        # that know the real path (which may differ from config.SEAM_DB_PATH under
        # --db-dir or a non-root CWD) set it via set_db_path(); until then the
        # atexit flush falls back to the CWD-relative config default.
        self._db_path: str | None = None

        if enabled:
            # Ensure the parent directory exists before we need to write.
            try:
                parent = os.path.dirname(os.path.abspath(path))
                if parent:
                    os.makedirs(parent, exist_ok=True)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "diagnostics: could not create parent directory for %s", path, exc_info=True
                )

            # Register the atexit handler so short-lived CLI invocations leave a record.
            atexit.register(self._atexit_snapshot)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_db_path(self, db_path: str | None) -> None:
        """Record the resolved DB path so the atexit snapshot measures the right file.

        The atexit flush otherwise falls back to config.SEAM_DB_PATH, which is
        relative to the process CWD — wrong when the caller used --db-dir or ran from
        a directory other than the project root. Consumers that resolve the real path
        (CLI _open_index, MCP create_server, the watcher) call this once. No-op-safe
        on a null recorder; never raises.
        """
        self._db_path = db_path

    @property
    def enabled(self) -> bool:
        """True when SEAM_DIAGNOSTICS=1 and this recorder writes records.

        Consumers (mcp.py / cli/read.py) read this ONCE to decide whether to install
        their timing wrapper at all — so when diagnostics is off there is zero
        per-call overhead (the wrapper is never installed), not merely a no-op call.
        """
        return self._enabled

    def record_query(
        self,
        tool: str,
        duration_ms: float,
        result_chars: int,
    ) -> None:
        """Increment the query counter and, if slow, append a slow_query NDJSON line.

        SECURITY INVARIANT: this method accepts only a tool name and numeric metrics.
        Argument text and result bodies are NOT parameters — they cannot be written.

        Args:
            tool:         MCP tool name or CLI command name (e.g. "seam_search").
            duration_ms:  Wall-clock duration of the query in milliseconds.
            result_chars: Character count of the serialized result (a size proxy).
        """
        if not self._enabled:
            return
        try:
            self._query_count += 1
            if duration_ms >= self._slow_ms:
                self._seq += 1
                line: dict[str, Any] = {
                    "event": "slow_query",
                    "tool": tool,
                    "duration_ms": duration_ms,
                    "result_chars": result_chars,
                    "seq": self._seq,
                    "ts": time.time(),
                }
                # Enforce the redaction invariant at write time as a defense-in-depth check.
                assert set(line.keys()) == _SLOW_QUERY_KEYS, (
                    f"BUG: slow_query line has unexpected keys: {set(line.keys())}"
                )
                self._write_line(line)
        except Exception:  # noqa: BLE001
            logger.warning("diagnostics: record_query failed", exc_info=True)

    def record_watcher_event(self, kind: str) -> None:
        """Increment a watcher activity counter.

        Args:
            kind: "reindexed" increments the reindexed counter;
                  "reindex_errors" increments the error counter;
                  any other value is accepted silently (forward-compat).
        """
        if not self._enabled:
            return
        try:
            if kind == "reindexed":
                self._watcher_reindexed += 1
            elif kind == "reindex_errors":
                self._watcher_errors += 1
            # Unknown kinds are silently ignored for forward-compatibility.
        except Exception:  # noqa: BLE001
            logger.warning("diagnostics: record_watcher_event failed", exc_info=True)

    def sample_resources(self, db_path: str) -> ResourceMetrics | None:
        """Return a dict of current resource metrics. None for any unavailable metric.

        The method is available on both enabled and disabled recorders so callers
        (e.g. soak.py) can call it without enabling file writes. A disabled recorder
        returns None (null recorder contract).

        Args:
            db_path: Path to the SQLite database file (for size measurement).

        Returns:
            dict with keys: rss_bytes, open_fds, db_size_bytes, query_count,
            watcher_reindexed, watcher_errors. Any metric that cannot be sampled
            on the current platform is None (→ JSON null). Never raises.
        """
        if not self._enabled:
            return None
        try:
            return {
                "rss_bytes": self._sample_rss(),
                "open_fds": self._sample_open_fds(),
                "db_size_bytes": self._sample_db_size(db_path),
                "query_count": self._query_count,
                "watcher_reindexed": self._watcher_reindexed,
                "watcher_errors": self._watcher_errors,
            }
        except Exception:  # noqa: BLE001
            logger.warning("diagnostics: sample_resources failed", exc_info=True)
            return {
                "rss_bytes": None,
                "open_fds": None,
                "db_size_bytes": None,
                "query_count": self._query_count,
                "watcher_reindexed": self._watcher_reindexed,
                "watcher_errors": self._watcher_errors,
            }

    def snapshot(self, db_path: str) -> None:
        """Sample resources and append one event="snapshot" NDJSON line.

        Args:
            db_path: Path to the SQLite database file (for size measurement).

        Never raises. On any error, logs at WARNING and returns silently.
        """
        if not self._enabled:
            return
        try:
            metrics = self.sample_resources(db_path) or {}
            line: dict[str, Any] = {
                "event": "snapshot",
                "ts": time.time(),
                **metrics,
            }
            # Defense-in-depth redaction guard (mirrors record_query): a snapshot line
            # must contain ONLY the known numeric/None metric keys — no unexpected key
            # (e.g. a future string field) may leak in. Subset check tolerates the
            # degenerate {event, ts} line if sample_resources ever returns empty.
            assert set(line.keys()) <= _SNAPSHOT_REQUIRED_KEYS, (
                f"BUG: snapshot line has unexpected keys: "
                f"{set(line.keys()) - _SNAPSHOT_REQUIRED_KEYS}"
            )
            self._write_line(line)
        except Exception:  # noqa: BLE001
            logger.warning("diagnostics: snapshot failed", exc_info=True)

    def close(self) -> None:
        """Unregister the atexit snapshot handler and stop recording.

        Primarily for tests: enabling a recorder registers a process-lifetime atexit
        handler, so a test that points it at a temp directory would otherwise leave a
        dangling handler that fires (and harmlessly logs a failure) at interpreter exit
        once the temp dir is gone. Calling close() in teardown prevents that. Harmless
        in production — the process is exiting anyway. Idempotent; never raises.
        """
        if not self._enabled:
            return
        try:
            atexit.unregister(self._atexit_snapshot)
        except Exception:  # noqa: BLE001
            pass
        self._enabled = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _write_line(self, obj: dict[str, Any]) -> None:
        """Append one JSON line to the NDJSON file.

        Uses Python's open(path, "a") which resolves to O_APPEND at the OS level.
        Each json.dumps call produces a complete, self-contained JSON object on one
        line so concurrent processes interleave whole lines without corruption.

        Never raises — errors are caught and logged by the caller.
        """
        text = json.dumps(obj, ensure_ascii=False) + "\n"
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def _atexit_snapshot(self) -> None:
        """atexit handler: write a final snapshot line on process exit.

        Never raises — atexit handlers must not propagate exceptions to the runtime.
        """
        try:
            # Prefer the resolved DB path a caller set via set_db_path(); fall back to
            # the CWD-relative config default (correct when CWD == project root).
            self.snapshot(self._db_path or config.SEAM_DB_PATH)
        except Exception:  # noqa: BLE001
            pass  # atexit handlers must be silent

    # ── Platform-specific resource sampling ───────────────────────────────────

    @staticmethod
    def _sample_rss() -> int | None:
        """Return peak RSS in bytes, or None if unavailable on this platform.

        ru_maxrss semantics differ by OS:
          - Linux:  ru_maxrss is in KiB (kilobytes) → multiply by 1024 for bytes.
          - macOS:  ru_maxrss is already in bytes.
          - Other:  conservatively return None (unknown unit → don't guess).

        Note: ru_maxrss is PEAK RSS (high-water mark), not the current resident set.
        Current RSS is not available from stdlib without /proc or psutil.
        """
        if _resource is None:
            return None  # Windows — no resource module
        try:
            usage = _resource.getrusage(_resource.RUSAGE_SELF)
            raw = usage.ru_maxrss
            if sys.platform == "linux":
                # Linux: value is in KiB.
                return raw * 1024
            elif sys.platform == "darwin":
                # macOS: value is already in bytes.
                return raw
            else:
                # Unknown platform — don't silently return a wrong unit.
                return None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _sample_open_fds() -> int | None:
        """Count open file descriptors via /proc/self/fd (Linux only).

        Returns None on platforms without /proc (macOS, Windows, BSD).
        WHY /proc: it is the only portable stdlib-compatible way to count open FDs
        without shelling out or importing psutil. macOS has no equivalent.
        """
        try:
            fd_dir = "/proc/self/fd"
            if not os.path.isdir(fd_dir):
                return None
            # Count entries; subtract 1 for the opendir FD itself (best-effort).
            entries = os.listdir(fd_dir)
            # The listdir call itself opens an FD that appears in the listing.
            # Subtract 1 for that transient entry (conservative, not exact).
            return max(0, len(entries) - 1)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _sample_db_size(db_path: str) -> int | None:
        """Return SQLite DB file size in bytes, or None if it cannot be measured."""
        try:
            return os.path.getsize(db_path)
        except Exception:  # noqa: BLE001
            return None


# ── Module-level factory (matches the config-driven pattern) ─────────────────


def make_recorder() -> DiagnosticsRecorder:
    """Create a DiagnosticsRecorder driven by the current seam.config values.

    Returns a null recorder (all methods no-op) when SEAM_DIAGNOSTICS != "1".
    Returns an active recorder when SEAM_DIAGNOSTICS == "1".

    Intended for use by mcp.py, cli/read.py, and watcher/daemon.py to obtain
    a process-level recorder without importing config directly.
    """
    enabled = config.SEAM_DIAGNOSTICS == "1"
    return DiagnosticsRecorder(
        enabled=enabled,
        path=config.SEAM_DIAGNOSTICS_PATH,
        slow_ms=config.SEAM_DIAGNOSTICS_SLOW_MS,
    )


# ── Process-level singleton + query helpers (shared by MCP + CLI + watcher) ────

_process_recorder: DiagnosticsRecorder | None = None


def get_recorder() -> DiagnosticsRecorder:
    """Return the ONE diagnostics recorder for this process, creating it on first use.

    A single recorder per process means a single atexit snapshot flush and a single
    accumulating query counter — the correct model for both the long-lived MCP server
    and a short-lived CLI invocation (which reconstructs cross-invocation trends from
    the append-only NDJSON file, not from this per-process counter).
    """
    global _process_recorder
    if _process_recorder is None:
        _process_recorder = make_recorder()
    return _process_recorder


def reset_recorder() -> None:
    """Close and discard the process recorder singleton (test hygiene helper).

    Closes the current recorder (unregistering its atexit handler) and clears the
    singleton so the next get_recorder() rebuilds from current config. Intended for
    test teardown; a no-op in normal single-shot process use. Never raises.
    """
    global _process_recorder
    if _process_recorder is not None:
        _process_recorder.close()
        _process_recorder = None


def result_chars(result: Any) -> int:
    """Character count of a serialized tool result — a SIZE PROXY, never content.

    The serialized string is measured and immediately discarded; it is NEVER stored,
    so no source text can leak into diagnostics. Returns 0 for None (error/not-found)
    and degrades to 0 on any serialization failure. Shared by the MCP and CLI paths.
    """
    if result is None:
        return 0
    try:
        return len(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 — size proxy must never break a caller
        return 0


def run_query(tool: str, thunk: Any) -> Any:
    """Time and record a read-query call, returning the thunk's result unchanged.

    Used by the CLI read commands (seam query/search/context/impact/trace). When
    diagnostics is off this is a transparent passthrough (one attribute check). When
    on, it times the thunk and records (tool, duration_ms, result_chars) — a slow_query
    line is written only when the call exceeds SEAM_DIAGNOSTICS_SLOW_MS; the query count
    (surfaced in the atexit snapshot) is always incremented. Never alters the result.
    """
    rec = get_recorder()
    if not rec.enabled:
        return thunk()
    start = time.perf_counter()
    result: Any = None
    try:
        result = thunk()
        return result
    finally:
        rec.record_query(tool, (time.perf_counter() - start) * 1000.0, result_chars(result))
