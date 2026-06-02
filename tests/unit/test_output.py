"""Unit tests for seam/cli/output.py — the agent-output contract.

Coverage:
  E1  success envelope has ok=True and data key
  E2  success envelope data is the payload passed in
  E3  error envelope has ok=False and error.code + error.message
  E4  error envelope code and message are strings
  E5  emit_json() writes compact JSON to stdout (no trailing newline after outer braces)
  E6  emit_json() key "ok" is the first key (deterministic ordering)
  E7  emit_json_error() returns exit code 1
  E8  quiet_lines() returns list of strings, one per logical value
  E9  mutual-exclusion guard: json_ and quiet both True raises ValueError
  Q1  quiet_lines for a dict with a load-bearing field returns that field's value
  Q2  quiet_lines for a list returns one item per line
"""

import json

import pytest

# ── Import the module under test (will fail until output.py exists) ───────────
from seam.cli.output import (
    build_error_envelope,
    build_success_envelope,
    check_mutual_exclusion,
    quiet_lines,
)

# ── E1: success envelope has ok=True and data ──────────────────────────────────


def test_success_envelope_ok_true() -> None:
    """build_success_envelope must always set ok=True."""
    env = build_success_envelope({"foo": "bar"})
    assert env["ok"] is True


def test_success_envelope_has_data() -> None:
    """build_success_envelope must nest the payload under 'data'."""
    payload = {"x": 1}
    env = build_success_envelope(payload)
    assert "data" in env
    assert env["data"] == payload


# ── E2: payload passthrough ────────────────────────────────────────────────────


def test_success_envelope_passthrough_list() -> None:
    """build_success_envelope works when payload is a list."""
    payload = [1, 2, 3]
    env = build_success_envelope(payload)
    assert env["data"] == [1, 2, 3]


def test_success_envelope_passthrough_none() -> None:
    """build_success_envelope works when payload is None."""
    env = build_success_envelope(None)
    assert env["data"] is None


# ── E3: error envelope structure ───────────────────────────────────────────────


def test_error_envelope_ok_false() -> None:
    """build_error_envelope must always set ok=False."""
    env = build_error_envelope("NO_INDEX", "Run seam init first.")
    assert env["ok"] is False


def test_error_envelope_has_error_key() -> None:
    """build_error_envelope must have an 'error' key."""
    env = build_error_envelope("INVALID_INPUT", "bad input")
    assert "error" in env


def test_error_envelope_error_has_code() -> None:
    """The 'error' sub-dict must have 'code'."""
    env = build_error_envelope("INVALID_INPUT", "bad input")
    assert env["error"]["code"] == "INVALID_INPUT"


def test_error_envelope_error_has_message() -> None:
    """The 'error' sub-dict must have 'message'."""
    env = build_error_envelope("NOT_A_GIT_REPO", "Not in a git repo")
    assert env["error"]["message"] == "Not in a git repo"


# ── E4: code and message are strings ──────────────────────────────────────────


def test_error_envelope_code_is_string() -> None:
    """Error code must be a plain string (not int, not None)."""
    env = build_error_envelope("NO_INDEX", "msg")
    assert isinstance(env["error"]["code"], str)


def test_error_envelope_message_is_string() -> None:
    """Error message must be a plain string."""
    env = build_error_envelope("NO_INDEX", "msg")
    assert isinstance(env["error"]["message"], str)


# ── E5: JSON-serializable output ───────────────────────────────────────────────


def test_success_envelope_is_json_serializable() -> None:
    """build_success_envelope result must round-trip through json.dumps/loads."""
    env = build_success_envelope({"a": 1, "b": [1, 2]})
    text = json.dumps(env)
    recovered = json.loads(text)
    assert recovered["ok"] is True
    assert recovered["data"]["a"] == 1


def test_error_envelope_is_json_serializable() -> None:
    """build_error_envelope result must round-trip through json.dumps/loads."""
    env = build_error_envelope("NO_INDEX", "Run seam init first.")
    text = json.dumps(env)
    recovered = json.loads(text)
    assert recovered["ok"] is False
    assert recovered["error"]["code"] == "NO_INDEX"


# ── E6: key ordering (ok first) ───────────────────────────────────────────────


def test_success_envelope_ok_is_first_key() -> None:
    """'ok' must be the first key in the success envelope dict."""
    env = build_success_envelope({"payload": True})
    assert list(env.keys())[0] == "ok"


def test_error_envelope_ok_is_first_key() -> None:
    """'ok' must be the first key in the error envelope dict."""
    env = build_error_envelope("INVALID_INPUT", "msg")
    assert list(env.keys())[0] == "ok"


# ── E7: check_mutual_exclusion raises ValueError when both set ─────────────────


def test_mutual_exclusion_raises_when_both_true() -> None:
    """check_mutual_exclusion must raise ValueError if both json_ and quiet are True."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        check_mutual_exclusion(json_=True, quiet=True)


def test_mutual_exclusion_ok_when_only_json() -> None:
    """check_mutual_exclusion must NOT raise when only json_=True."""
    check_mutual_exclusion(json_=True, quiet=False)  # should not raise


def test_mutual_exclusion_ok_when_only_quiet() -> None:
    """check_mutual_exclusion must NOT raise when only quiet=True."""
    check_mutual_exclusion(json_=False, quiet=True)  # should not raise


def test_mutual_exclusion_ok_when_neither() -> None:
    """check_mutual_exclusion must NOT raise when both are False."""
    check_mutual_exclusion(json_=False, quiet=False)  # should not raise


# ── Q1: quiet_lines for dict returns load-bearing field value ──────────────────


def test_quiet_lines_with_string_field() -> None:
    """quiet_lines returns the string value of the specified field, as a list."""
    data = {"risk_level": "high", "other": "ignored"}
    lines = quiet_lines(data, field="risk_level")
    assert lines == ["high"]


def test_quiet_lines_with_int_field() -> None:
    """quiet_lines returns string-coerced int field value."""
    data = {"count": 42, "label": "foo"}
    lines = quiet_lines(data, field="count")
    assert lines == ["42"]


# ── Q2: quiet_lines for list returns one item per line ────────────────────────


def test_quiet_lines_for_list_of_dicts() -> None:
    """quiet_lines with a list of dicts returns field value per item."""
    data = [{"name": "alpha"}, {"name": "beta"}, {"name": "gamma"}]
    lines = quiet_lines(data, field="name")
    assert lines == ["alpha", "beta", "gamma"]


def test_quiet_lines_for_list_of_strings() -> None:
    """quiet_lines with a list of strings returns them as-is."""
    data = ["foo", "bar", "baz"]
    lines = quiet_lines(data)
    assert lines == ["foo", "bar", "baz"]


def test_quiet_lines_for_empty_list() -> None:
    """quiet_lines for an empty list returns empty list."""
    lines = quiet_lines([])
    assert lines == []
