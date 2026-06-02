"""Agent-output contract for the Seam CLI.

This module is the single source of truth for structured output emitted
by CLI commands when --json or --quiet flags are passed.

Design goals (from PRD §slice-2):
- Consistent JSON envelope across ALL read commands: {"ok": true, "data": ...}
  on success, {"ok": false, "error": {"code": ..., "message": ...}} on failure.
- Errors in --json mode go to STDOUT as JSON + non-zero exit (not stderr text).
  This is where Seam leapfrogs CodeGraph: CodeGraph emits ANSI errors to stderr
  even in JSON mode; Seam always gives a machine-parseable envelope.
- --quiet emits bare, one-per-line values for piping into other tools.
- --json and --quiet are mutually exclusive.

WHY these as module-level functions rather than a class:
  Simplicity. There is no shared state between invocations. A module with
  plain functions is the minimum complexity that satisfies the spec.

Stable error codes (reuse vocabulary from existing MCP handlers):
  NO_INDEX       — index not found; run seam init first
  INVALID_INPUT  — blank/missing required argument (user-supplied bad input)
  INVALID_QUERY  — bad FTS5 syntax (rare after Phase 3 sanitization)
  NOT_A_GIT_REPO — changes command outside a git repository
  DB_ERROR       — database file exists but could not be opened (corrupted, locked, etc.)
                   Distinct from INVALID_INPUT (which implies the user sent bad data).
"""

import json
import sys
from typing import Any

import typer

# ── Envelope builders ──────────────────────────────────────────────────────────


def build_success_envelope(data: Any) -> dict[str, Any]:
    """Build a success envelope: {"ok": true, "data": <payload>}.

    'ok' is always the first key (deterministic ordering for diff-readability).
    The payload can be any JSON-serializable value (dict, list, None, …).
    """
    # Use insertion-ordered dict (Python 3.7+ guaranteed) so "ok" comes first.
    return {"ok": True, "data": data}


def build_error_envelope(code: str, message: str) -> dict[str, Any]:
    """Build an error envelope: {"ok": false, "error": {"code": ..., "message": ...}}.

    'ok' is always the first key. 'code' is a STABLE_UPPER_SNAKE identifier
    that callers can branch on without parsing human-readable text.
    """
    return {"ok": False, "error": {"code": code, "message": message}}


# ── Mutual-exclusion guard ─────────────────────────────────────────────────────


def check_mutual_exclusion(json_: bool, quiet: bool) -> None:
    """Raise ValueError when --json and --quiet are both set.

    WHY: Having two machine-output modes active simultaneously is undefined
    behaviour. The caller should emit the error envelope (since --json was
    requested) and exit 1 — handled by the CLI command, not here.
    """
    if json_ and quiet:
        raise ValueError("--json and --quiet are mutually exclusive; pick one")


# ── JSON emitter ───────────────────────────────────────────────────────────────


def emit_json(data: Any) -> None:
    """Write a compact JSON success envelope to stdout.

    WHY compact (no indent): agents parse structured output, not humans.
    Compact keeps tokens minimal. A trailing newline is added for shell
    compatibility (most tools expect it).
    """
    envelope = build_success_envelope(data)
    # ensure_ascii=False: preserve non-ASCII symbol names as-is
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")


def emit_json_error(code: str, message: str) -> None:
    """Write a compact JSON error envelope to stdout and exit with code 1.

    WHY stdout (not stderr): agents read stdout. The non-zero exit code
    signals failure to shell pipelines/CI; the JSON body gives the reason.
    This is the key leapfrog over CodeGraph, which writes ANSI text to stderr.
    """
    envelope = build_error_envelope(code, message)
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
    raise typer.Exit(code=1)


# ── Quiet renderer ─────────────────────────────────────────────────────────────


def quiet_lines(data: Any, field: str | None = None) -> list[str]:
    """Return bare line strings for --quiet mode rendering.

    Rules:
    - If data is a list of strings: return them as-is.
    - If data is a list of dicts: return str(item[field]) per item.
      field must be provided when data is a list of dicts.
    - If data is a dict: return [str(data[field])].
      field must be provided when data is a dict.
    - Empty list: return [].

    WHY return list instead of printing: callers decide how to format
    (newlines, prefixes, etc.) and can assert the list in tests without
    capturing stdout.
    """
    if isinstance(data, list):
        if not data:
            return []
        # Plain string list — return as-is
        if isinstance(data[0], str):
            return list(data)
        # List of dicts — extract the named field
        return [str(item[field]) for item in data if field is not None]

    if isinstance(data, dict) and field is not None:
        return [str(data[field])]

    return []


def print_quiet(data: Any, field: str | None = None) -> None:
    """Print quiet_lines output to stdout, one line each.

    Helper that CLI commands call after quiet_lines() to avoid
    duplicating the sys.stdout.write loop in every command.
    """
    for line in quiet_lines(data, field=field):
        sys.stdout.write(line + "\n")
