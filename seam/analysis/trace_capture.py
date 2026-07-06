"""Opt-in local trace recorder for the agent-trace-derived eval goldens loop (WS6.1).

LEAF MODULE — stdlib-only imports. No new runtime dependency.
Never raises. All IO wrapped in try/except; errors are logged at WARNING and degrade
to a no-op for that call. Mirrors the leaf discipline of diagnostics.py exactly.

WHY this exists (the privacy reversal explained):
  SEAM_DIAGNOSTICS records ONLY numeric metrics — structural redaction means query text
  and result content can never reach the diagnostics file. That is intentional for a
  purely operational monitoring tool.
  WS6.1 deliberately REVERSES the redaction policy on ONE bounded surface: to derive a
  golden ("for this query, these symbols should surface") you must know BOTH the query
  AND the symbols the tool returned. The mitigation is tight bounding:
    - Symbols only: only the result symbol NAMES are stored, never full result bodies,
      never source text, never signature text.
    - Opt-in: default OFF; user must set SEAM_TRACE_CAPTURE=1.
    - Local-only: NDJSON files land in .seam/traces/ (gitignored); never networked.
  This policy is documented alongside SEAM_DIAGNOSTICS in config.py.

When SEAM_TRACE_CAPTURE != "1" (the default), TraceRecorder is a null recorder:
  - every method is a no-op
  - no file is opened or created
  - no atexit handler is registered
  - zero measurable overhead on the read path (byte-identical to pre-WS6.1)

When SEAM_TRACE_CAPTURE == "1", an active recorder:
  - record_tool_call(tool, args, symbol_names, result_count, elapsed_ms) — appends
    ONE NDJSON line per tool call. The line carries a session_id (UUID4, stable for
    the process lifetime), timestamp, tool name, args dict, symbol_names list, and
    result_count. Full result bodies and source text are NOT parameters — the interface
    prevents them from being written even by mistake (structural bound).
  - At process exit, an atexit handler writes a final flush (close) marker.

NDJSON writer: each file is per-session (keyed to the session UUID) inside the
configured trace directory. O_APPEND semantics ensure concurrent CLI processes
writing to DIFFERENT session files do not interfere. Python's open(path, "a")
resolves to O_APPEND at the OS level.

Allowed line keys (defense-in-depth — enforced at write time):
  event, session_id, ts, tool, args, symbol_names, result_count, elapsed_ms
"""

import atexit
import json
import logging
import os
import time
import uuid
from typing import Any

import seam.config as config

logger = logging.getLogger(__name__)

# ── Allowed tool_call line keys (symbols-only bound, enforced at write time) ──
# Adding any key here requires review: keys that could carry source text or full
# result bodies must never be added. args and symbol_names are the only content
# fields; all others are numeric/string metadata.
_TOOL_CALL_KEYS = frozenset({
    "event",
    "session_id",
    "ts",
    "tool",
    "args",
    "symbol_names",
    "result_count",
    "elapsed_ms",
})


class TraceRecorder:
    """Per-process trace recorder with null-recorder support when disabled.

    Construct once per process via TraceRecorder(enabled=..., trace_dir=...).
    When not enabled, all methods are no-ops and no file is opened.
    When enabled, records each read-path tool call to a session-keyed NDJSON file
    inside trace_dir.

    Thread-safe: Python's GIL protects the in-memory state; file writes use
    O_APPEND (one json.dumps per call → whole lines, no interleaving within a
    file; different sessions write to different files).

    Never raises: every method wraps its body in try/except → log + degrade.
    """

    def __init__(self, *, enabled: bool, trace_dir: str) -> None:
        self._enabled = enabled
        self._trace_dir = trace_dir
        # UUID4 session identifier — stable for the lifetime of this process.
        self._session_id: str = str(uuid.uuid4())
        # Per-session NDJSON file path (set on first write when enabled).
        self._trace_path: str | None = None

        if enabled:
            # Ensure the trace directory exists before the first write.
            try:
                os.makedirs(trace_dir, exist_ok=True)
                # Set the session file path now so it is predictable.
                self._trace_path = os.path.join(
                    trace_dir, f"session-{self._session_id}.ndjson"
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "trace_capture: could not create trace directory %s", trace_dir,
                    exc_info=True,
                )

            # Register the atexit handler so the recorder can flush on exit.
            atexit.register(self._atexit_close)

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when SEAM_TRACE_CAPTURE=1 and this recorder writes records."""
        return self._enabled

    @property
    def session_id(self) -> str:
        """UUID4 session identifier — stable for the lifetime of this recorder."""
        return self._session_id

    def record_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        symbol_names: list[str],
        result_count: int,
        elapsed_ms: float,
    ) -> None:
        """Append one tool_call NDJSON line for a read-path Seam tool invocation.

        SYMBOLS-ONLY BOUND: this method accepts only tool name, query args (the dict
        the caller passed to the tool), result symbol NAMES, and numeric counts.
        Full result bodies, source text, signatures, docstrings — NONE of these are
        parameters and therefore CANNOT be written even by a buggy caller.

        Args:
            tool:         MCP tool name or CLI command (e.g. "seam_search").
            args:         Query args dict passed to the tool (e.g. {"query": "auth"}).
            symbol_names: Ordered list of qualified symbol names returned by the tool.
            result_count: Total number of results returned (may exceed len(symbol_names)
                          if the caller extracted a subset).
            elapsed_ms:   Wall-clock duration of the tool call in milliseconds.
        """
        if not self._enabled:
            return
        try:
            line: dict[str, Any] = {
                "event": "tool_call",
                "session_id": self._session_id,
                "ts": time.time(),
                "tool": tool,
                "args": args,
                "symbol_names": symbol_names,
                "result_count": result_count,
                "elapsed_ms": elapsed_ms,
            }
            # Enforce the symbols-only bound at write time as a defense-in-depth check.
            assert set(line.keys()) == _TOOL_CALL_KEYS, (
                f"BUG: tool_call line has unexpected keys: {set(line.keys())}"
            )
            self._write_line(line)
        except Exception:  # noqa: BLE001
            logger.warning("trace_capture: record_tool_call failed", exc_info=True)

    def close(self) -> None:
        """Unregister the atexit handler and stop recording.

        For tests: prevents a dangling atexit handler after the temp directory is
        cleaned up. Idempotent; never raises.
        """
        if not self._enabled:
            return
        try:
            atexit.unregister(self._atexit_close)
        except Exception:  # noqa: BLE001
            pass
        self._enabled = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _write_line(self, obj: dict[str, Any]) -> None:
        """Append one JSON line to the session NDJSON file.

        Uses Python's open(path, "a") → O_APPEND. Each json.dumps call produces a
        complete self-contained JSON object on one line. Never raises (callers catch).
        """
        if self._trace_path is None:
            return
        text = json.dumps(obj, ensure_ascii=False) + "\n"
        with open(self._trace_path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def _atexit_close(self) -> None:
        """atexit handler: silent no-op close on process exit.

        Never raises — atexit handlers must not propagate exceptions to the runtime.
        """
        try:
            # Disable further writes; atexit runs once.
            self._enabled = False
        except Exception:  # noqa: BLE001
            pass


# ── Module-level factory (matches the config-driven pattern in diagnostics.py) ─


def make_recorder() -> TraceRecorder:
    """Create a TraceRecorder driven by the current seam.config values.

    Returns a null recorder (all methods no-op) when SEAM_TRACE_CAPTURE != "1".
    Returns an active recorder when SEAM_TRACE_CAPTURE == "1".

    Intended for use by mcp.py, cli/read.py, and cli/main.py to obtain the
    per-process recorder without importing config directly in those modules.
    """
    enabled = config.SEAM_TRACE_CAPTURE == "1"
    return TraceRecorder(
        enabled=enabled,
        trace_dir=config.SEAM_TRACE_CAPTURE_PATH,
    )


# ── Process-level singleton + passthrough helper (mirrors diagnostics.py) ──────

_process_recorder: TraceRecorder | None = None


def get_recorder() -> TraceRecorder:
    """Return the ONE trace recorder for this process, creating it on first use.

    A single recorder per process means a single session_id and a single trace file
    — the correct model for both the long-lived MCP server and a short-lived CLI
    invocation (which opens one file per invocation via the session UUID).
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


def extract_symbol_names(result: Any) -> list[str]:
    """Extract a list of qualified symbol names from a Seam tool result.

    This is the ONLY place where a tool result is inspected — we pull out ONLY
    symbol names (strings) and discard everything else. The returned list is what
    gets stored in the trace file.

    Handles the common Seam result shapes:
    - list of dicts with a "name" key (seam_search, seam_query results)
    - dict with a "data" key containing such a list
    - dict with "callers"/"callees" lists (seam_context)
    - None or unexpected shape → empty list (safe degradation)

    Never raises.
    """
    if result is None:
        return []
    try:
        # Unwrap {"ok": true, "data": [...]} envelope if present.
        if isinstance(result, dict):
            if "data" in result and isinstance(result["data"], list):
                result = result["data"]
            elif "name" in result:
                return [result["name"]] if isinstance(result["name"], str) else []
            else:
                # For context-style results, pull callers + callees + the symbol itself.
                names: list[str] = []
                if "symbol" in result and isinstance(result["symbol"], str):
                    names.append(result["symbol"])
                for key in ("callers", "callees"):
                    entries = result.get(key, [])
                    if isinstance(entries, list):
                        for e in entries:
                            if isinstance(e, dict) and isinstance(e.get("name"), str):
                                names.append(e["name"])
                            elif isinstance(e, str):
                                names.append(e)
                return names

        if isinstance(result, list):
            names = []
            for item in result:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.append(item["name"])
                elif isinstance(item, str):
                    names.append(item)
            return names

        return []
    except Exception:  # noqa: BLE001
        return []


def trace_run_query(tool: str, args: dict[str, Any], thunk: Any) -> Any:
    """Time a read-query call, record a trace line, and return the result unchanged.

    Used by the CLI read commands (seam query/search/context/impact/trace) as a
    companion to diagnostics.run_query(). When trace capture is off this is a
    transparent passthrough (one attribute check). When on, it times the thunk,
    extracts symbol names from the result, and records the trace line. Never alters
    the result. Never raises.
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
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        symbols = extract_symbol_names(result)
        rec.record_tool_call(
            tool=tool,
            args=args,
            symbol_names=symbols,
            result_count=len(symbols),
            elapsed_ms=elapsed_ms,
        )
