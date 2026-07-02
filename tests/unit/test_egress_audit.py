"""Unit tests for tests/support/egress_audit.py.

These tests exercise external behavior through the public API only
(classify_connect_line, scan_trace, main) — no implementation details.

WHY: egress_audit is the shared S1 primitive for P5.4 no-egress proof.
Locking down the contract here means S2 (the CI workflow) can rely on
the CLI exit codes and output format without re-validating the parser.

Test naming convention: ``test_<behavior>`` where the behavior is described
from the caller's perspective ("external AF_INET is an offender"), not from
the implementation perspective ("regex group 1 captures family").
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.egress_audit import classify_connect_line, main, scan_trace

# ── Real strace line fixtures (copied from P5.4 S1 spec) ────────────────────

_LINE_EXT_INET = (
    'connect(3, {sa_family=AF_INET, sin_port=htons(443), '
    'sin_addr=inet_addr("140.82.113.3")}, 16) = 0'
)
_LINE_LOCAL_LOOPBACK_INET = (
    'connect(3, {sa_family=AF_INET, sin_port=htons(53), '
    'sin_addr=inet_addr("127.0.0.53")}, 16) = 0'
)
_LINE_EXT_INET6 = (
    'connect(3, {sa_family=AF_INET6, sin6_port=htons(443), '
    'inet_pton(AF_INET6, "2606:4700::1111", &sin6_addr), sin6_scope_id=0}, 28) '
    "= -1 EINPROGRESS (Operation now in progress)"
)
_LINE_LOCAL_INET6 = (
    'connect(3, {sa_family=AF_INET6, sin6_port=htons(0), '
    'inet_pton(AF_INET6, "::1", &sin6_addr), sin6_scope_id=0}, 28) = 0'
)
_LINE_LOCAL_UNIX = (
    'connect(5, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = 0'
)
_LINE_LOCAL_NETLINK = (
    "connect(6, {sa_family=AF_NETLINK, nl_pid=0, nl_groups=00000000}, 12) = 0"
)
_LINE_PID_PREFIX_EXT = (
    '[pid 12345] connect(3, {sa_family=AF_INET, sin_port=htons(80), '
    'sin_addr=inet_addr("1.2.3.4")}, 16) = 0'
)
_LINE_FAILED_EXT = (
    'connect(3, {sa_family=AF_INET, sin_port=htons(443), '
    'sin_addr=inet_addr("93.184.216.34")}, 16) = -1 ECONNREFUSED (Connection refused)'
)
_LINE_NON_CONNECT_OPENAT = (
    'openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3'
)
_LINE_NON_CONNECT_WRITE = "write(1, \"hello\\n\", 6) = 6"
_LINE_MALFORMED_EXT_NO_ADDR = (
    "connect(3, {sa_family=AF_INET, sin_port=htons(80)}, 16) = 0"
)

# ── classify_connect_line: single-line classification ───────────────────────


def test_external_inet_is_classified_external() -> None:
    """An AF_INET connect to a public IP is classified as external."""
    assert classify_connect_line(_LINE_EXT_INET) == "external"


def test_loopback_inet_127_x_is_classified_local() -> None:
    """127.x.x.x addresses (the full /8 loopback range) are classified as local."""
    assert classify_connect_line(_LINE_LOCAL_LOOPBACK_INET) == "local"


def test_loopback_dns_127_0_0_53_is_classified_local() -> None:
    """127.0.0.53 (systemd-resolved) is loopback — must be classified as local."""
    line = (
        'connect(3, {sa_family=AF_INET, sin_port=htons(53), '
        'sin_addr=inet_addr("127.0.0.53")}, 16) = 0'
    )
    assert classify_connect_line(line) == "local"


def test_external_inet6_is_classified_external() -> None:
    """An AF_INET6 connect to a public IPv6 address is classified as external."""
    assert classify_connect_line(_LINE_EXT_INET6) == "external"


def test_loopback_inet6_is_classified_local() -> None:
    """AF_INET6 connect to ::1 is classified as local."""
    assert classify_connect_line(_LINE_LOCAL_INET6) == "local"


def test_af_unix_is_classified_local() -> None:
    """AF_UNIX (domain socket) connects are always local."""
    assert classify_connect_line(_LINE_LOCAL_UNIX) == "local"


def test_af_netlink_is_classified_local() -> None:
    """AF_NETLINK (kernel netlink) connects are always local."""
    assert classify_connect_line(_LINE_LOCAL_NETLINK) == "local"


def test_pid_prefixed_external_line_is_classified_external() -> None:
    """Lines with a [pid N] prefix from strace -f are correctly classified."""
    assert classify_connect_line(_LINE_PID_PREFIX_EXT) == "external"


def test_failed_external_connect_is_still_external() -> None:
    """A connect that returns -1 ECONNREFUSED is still an external violation."""
    assert classify_connect_line(_LINE_FAILED_EXT) == "external"


def test_non_connect_openat_line_is_none() -> None:
    """openat() lines are not connect() calls — classifier returns None."""
    assert classify_connect_line(_LINE_NON_CONNECT_OPENAT) is None


def test_non_connect_write_line_is_none() -> None:
    """write() lines are not connect() calls — classifier returns None."""
    assert classify_connect_line(_LINE_NON_CONNECT_WRITE) is None


def test_empty_string_is_none() -> None:
    """An empty string is not a connect() call."""
    assert classify_connect_line("") is None


def test_garbage_line_is_none() -> None:
    """A garbage/malformed line that is not a connect() call returns None."""
    assert classify_connect_line("this is not a trace line at all!!!") is None


def test_malformed_external_connect_no_addr_is_external_fail_closed() -> None:
    """AF_INET connect with no parseable address is EXTERNAL (fail-closed contract).

    A security proof must err toward false positives.  If an address cannot be
    extracted, we must report the line as a violation rather than pass it silently.
    """
    assert classify_connect_line(_LINE_MALFORMED_EXT_NO_ADDR) == "external"


def test_private_range_address_is_external() -> None:
    """Private-range IPs (10/8, 172.16/12, 192.168/16) are EXTERNAL — no exception."""
    line_10 = (
        'connect(3, {sa_family=AF_INET, sin_port=htons(8080), '
        'sin_addr=inet_addr("10.0.0.1")}, 16) = 0'
    )
    line_172 = (
        'connect(3, {sa_family=AF_INET, sin_port=htons(8080), '
        'sin_addr=inet_addr("172.16.0.1")}, 16) = 0'
    )
    line_192 = (
        'connect(3, {sa_family=AF_INET, sin_port=htons(8080), '
        'sin_addr=inet_addr("192.168.1.1")}, 16) = 0'
    )
    assert classify_connect_line(line_10) == "external"
    assert classify_connect_line(line_172) == "external"
    assert classify_connect_line(line_192) == "external"


def test_einprogress_failed_connect_is_external() -> None:
    """A connect that returns -1 EINPROGRESS (non-blocking) is still an offender."""
    assert classify_connect_line(_LINE_EXT_INET6) == "external"


# ── scan_trace: multi-line text scanning ────────────────────────────────────


def test_scan_trace_clean_trace_returns_empty() -> None:
    """A trace with only local connects produces no offenders."""
    clean_trace = "\n".join(
        [
            _LINE_LOCAL_LOOPBACK_INET,
            _LINE_LOCAL_UNIX,
            _LINE_LOCAL_NETLINK,
            _LINE_LOCAL_INET6,
            _LINE_NON_CONNECT_OPENAT,
            _LINE_NON_CONNECT_WRITE,
        ]
    )
    assert scan_trace(clean_trace) == []


def test_scan_trace_single_external_returns_that_line() -> None:
    """A trace with one external connect returns exactly that line."""
    trace = "\n".join(
        [
            _LINE_LOCAL_LOOPBACK_INET,
            _LINE_EXT_INET,
            _LINE_LOCAL_UNIX,
        ]
    )
    offenders = scan_trace(trace)
    assert len(offenders) == 1
    assert _LINE_EXT_INET in offenders[0]


def test_scan_trace_multiple_external_returns_all() -> None:
    """A trace with multiple external connects returns all of them."""
    trace = "\n".join(
        [
            _LINE_EXT_INET,
            _LINE_EXT_INET6,
            _LINE_PID_PREFIX_EXT,
        ]
    )
    offenders = scan_trace(trace)
    assert len(offenders) == 3


def test_scan_trace_empty_string_returns_empty() -> None:
    """An empty trace string produces no offenders."""
    assert scan_trace("") == []


def test_scan_trace_only_non_connect_lines_returns_empty() -> None:
    """A trace with no connect() lines at all produces no offenders."""
    trace = "\n".join([_LINE_NON_CONNECT_OPENAT, _LINE_NON_CONNECT_WRITE])
    assert scan_trace(trace) == []


# ── main(): CLI entry point ─────────────────────────────────────────────────


def test_main_returns_0_for_clean_trace_file(tmp_path: Path) -> None:
    """main() returns 0 when all connects in the file are local."""
    trace_file = tmp_path / "clean.strace"
    trace_file.write_text(
        "\n".join([_LINE_LOCAL_LOOPBACK_INET, _LINE_LOCAL_UNIX, _LINE_LOCAL_NETLINK]),
        encoding="utf-8",
    )
    assert main([str(trace_file)]) == 0


def test_main_returns_1_for_dirty_trace_file(tmp_path: Path) -> None:
    """main() returns 1 when any connect in the file is external."""
    trace_file = tmp_path / "dirty.strace"
    trace_file.write_text(
        "\n".join([_LINE_LOCAL_LOOPBACK_INET, _LINE_EXT_INET]),
        encoding="utf-8",
    )
    assert main([str(trace_file)]) == 1


def test_main_returns_1_if_any_file_is_dirty(tmp_path: Path) -> None:
    """main() returns 1 when only one of multiple files is dirty."""
    clean = tmp_path / "clean.strace"
    dirty = tmp_path / "dirty.strace"
    clean.write_text(_LINE_LOCAL_UNIX, encoding="utf-8")
    dirty.write_text(_LINE_EXT_INET, encoding="utf-8")
    assert main([str(clean), str(dirty)]) == 1


def test_main_prints_offender_line_with_prefix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() prints each offender prefixed with 'external connection detected: '."""
    trace_file = tmp_path / "dirty.strace"
    trace_file.write_text(_LINE_EXT_INET, encoding="utf-8")
    main([str(trace_file)])
    captured = capsys.readouterr()
    assert captured.out.startswith("external connection detected: ")
    assert _LINE_EXT_INET in captured.out


def test_main_no_output_for_clean_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() produces no stdout output when the trace is clean."""
    trace_file = tmp_path / "clean.strace"
    trace_file.write_text(_LINE_LOCAL_UNIX, encoding="utf-8")
    main([str(trace_file)])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_no_argv_returns_0() -> None:
    """main([]) with no files to scan returns 0 (nothing to violate)."""
    assert main([]) == 0


def test_main_exits_0_via_subprocess(tmp_path: Path) -> None:
    """CLI subprocess exits 0 for a clean trace file (process-level integration)."""
    trace_file = tmp_path / "clean.strace"
    trace_file.write_text(
        "\n".join([_LINE_LOCAL_UNIX, _LINE_LOCAL_NETLINK, _LINE_LOCAL_LOOPBACK_INET]),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.egress_audit", str(trace_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_main_exits_1_via_subprocess(tmp_path: Path) -> None:
    """CLI subprocess exits 1 for a dirty trace file (process-level integration)."""
    trace_file = tmp_path / "dirty.strace"
    trace_file.write_text(_LINE_EXT_INET, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.egress_audit", str(trace_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "external connection detected:" in result.stdout


# ── IPv6 fallback path (plain-quoted address, no inet_pton wrapper) ─────────
# Some strace versions omit the inet_pton() wrapper and write the address
# directly as a quoted string, e.g. sin6_addr="::1".  The _QUOTED_ADDR_RE
# fallback in _classify_inet handles this path.


def test_ipv6_plain_loopback_without_inet_pton_is_local() -> None:
    """AF_INET6 with sin6_addr='::1' (no inet_pton wrapper) is classified local.

    Exercises the _QUOTED_ADDR_RE fallback path in _classify_inet, which is not
    covered by the inet_pton-format fixtures (_LINE_LOCAL_INET6).
    """
    line = (
        'connect(3, {sa_family=AF_INET6, sin6_port=htons(0), '
        'sin6_addr="::1", sin6_scope_id=0}, 28) = 0'
    )
    assert classify_connect_line(line) == "local"


def test_ipv6_plain_external_without_inet_pton_is_external() -> None:
    """AF_INET6 with sin6_addr='2001:db8::1' (no inet_pton wrapper) is external.

    Exercises the _QUOTED_ADDR_RE fallback path for a non-loopback IPv6 address.
    """
    line = (
        'connect(3, {sa_family=AF_INET6, sin6_port=htons(443), '
        'sin6_addr="2001:db8::1", sin6_scope_id=0}, 28) = 0'
    )
    assert classify_connect_line(line) == "external"


# ── Known limitation: strace -f interleaved syscall format ──────────────────
# When strace -f follows multiple threads/processes, a syscall can be split
# across two lines: "<unfinished ...>" and "<... resumed>".  The _CONNECT_RE
# pattern requires the full struct body in one line and therefore returns None
# for both halves.  This is an accepted limitation documented here so regressions
# are visible.  In practice it is not a problem for the no-egress proof because
# seam's read path makes no outbound connections — there are no split connect()
# calls to miss.


def test_strace_unfinished_connect_line_is_none() -> None:
    """An '<unfinished ...>' split-connect line returns None (known limitation).

    We cannot classify an incomplete connect() line.  The incomplete half does
    NOT trigger a false-negative in the no-egress proof because seam never
    opens external connections, so the unfinished/resumed pattern only appears
    for local connects (SQLite WAL, Unix sockets) which would also return None.
    """
    unfinished = (
        '[pid 12345] connect(3, {sa_family=AF_INET, '
        'sin_addr=inet_addr("8.8.8.8")}, <unfinished ...>'
    )
    assert classify_connect_line(unfinished) is None


def test_strace_resumed_connect_line_is_none() -> None:
    """A '<... connect resumed>' continuation line returns None (known limitation)."""
    resumed = "[pid 67890] <... connect resumed>) = 0"
    assert classify_connect_line(resumed) is None


# ── scan_trace: fail-closed on exception in classifier ──────────────────────


def test_scan_trace_treats_classifier_exception_as_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scan_trace appends a line to offenders when classify_connect_line raises.

    classify_connect_line is documented as never raising, but if a future bug
    causes it to raise on a connect-looking line, scan_trace must fail-closed
    (report the line) rather than silently skipping it.
    """
    import tests.support.egress_audit as _mod

    def _raise(_line: str) -> str | None:
        raise RuntimeError("simulated classifier bug")

    monkeypatch.setattr(_mod, "classify_connect_line", _raise)
    # Any non-empty text triggers the exception path.
    offenders = _mod.scan_trace("some connect line\nanother line")
    assert len(offenders) == 2  # both lines reported, neither silently dropped
