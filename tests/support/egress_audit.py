"""Strace egress parser: classifies connect() syscall lines as local or external.

WHY this exists:
  P5.4 proves Seam makes ZERO outbound network connections on its read path.
  This module (S1) is the pure, locally-testable parser. S2 (a separate slice)
  wires it into a GitHub Actions workflow that runs commands under
  ``strace -f -e trace=connect`` and fails on any external ``connect()``.

WHY stdlib-only (re, ipaddress, sys, dataclasses):
  Test-support code that imports Seam internals creates a circular validation
  problem — if the import itself is broken the test can't tell us why.  Staying
  stdlib-only also guarantees the module loads in a bare CI Python environment
  with no extras installed.

WHY fail-closed on unparseable AF_INET/AF_INET6 addresses:
  A security proof must err toward false positives.  A connect() line that
  clearly targets AF_INET or AF_INET6 but whose address cannot be extracted or
  parsed is reported as EXTERNAL rather than silently passed.  Letting an
  unparseable connect line slip through would defeat the purpose of the proof.

WHY failed connects (= -1 EINPROGRESS, = -1 ECONNREFUSED) still count:
  Intent to egress matters, not success.  A process that attempts an external
  connection but is refused is still violating the no-egress contract.  The
  return value comes after the closing ``)`` of the connect() call and does not
  affect our regex match.
"""

import ipaddress
import re
import sys

# ── regex patterns ───────────────────────────────────────────────────────────

# Match a connect() syscall line, optionally prefixed by ``[pid N]`` as emitted
# by ``strace -f``.  Captures the entire sockaddr struct body as group 1.
# The struct body is delimited by the outer ``{`` … ``}``; inner parentheses
# (e.g. htons(443), inet_pton(AF_INET6, ...)) are fine — they contain no ``}``.
_CONNECT_RE = re.compile(
    r"(?:\[pid\s+\d+\]\s+)?"   # optional [pid N] prefix (strace -f)
    r"connect\(\d+,\s*\{"      # connect(fd, {
    r"(.+?)"                    # group 1: sockaddr struct body (non-greedy)
    r"\},\s*\d+\)"              # }, addrlen)
)

# Extract the sa_family value (e.g. AF_INET, AF_UNIX, AF_NETLINK).
_FAMILY_RE = re.compile(r"sa_family=(\w+)")

# Extract the IPv4 address from ``sin_addr=inet_addr("x.x.x.x")``.
_IPV4_RE = re.compile(r'inet_addr\("([^"]+)"\)')

# Extract the IPv6 address from ``inet_pton(AF_INET6, "addr", ...)``.
_IPV6_RE = re.compile(r'inet_pton\(AF_INET6,\s*"([^"]+)"')

# Fallback: find any double-quoted string in the block (used for IPv6 when
# inet_pton(...) format is absent — strace sometimes writes just "::1").
_QUOTED_ADDR_RE = re.compile(r'"([^"]+)"')


# ── public API ───────────────────────────────────────────────────────────────


def classify_connect_line(line: str) -> str | None:
    """Classify a single strace output line.

    Args:
        line: One line of strace text (trailing newline is harmless).

    Returns:
        ``'local'``    — the line is a connect() to a local address
                         (loopback, AF_UNIX socket, or AF_NETLINK).
        ``'external'`` — the line is a connect() to an external address,
                         OR an AF_INET/AF_INET6 connect whose address cannot
                         be parsed (fail-closed contract — see module docstring).
        ``None``       — the line is not a connect() syscall at all.

    Never raises.  Garbage lines return ``None``.
    """
    m = _CONNECT_RE.search(line)
    if not m:
        return None

    block = m.group(1)
    family_m = _FAMILY_RE.search(block)
    if not family_m:
        # Cannot determine family — treat conservatively as None (not a
        # connect we recognise) rather than failing closed, because a corrupt
        # trace line with no sa_family may not be an internet connection at all.
        return None

    family = family_m.group(1)

    if family == "AF_UNIX":
        return "local"

    if family == "AF_NETLINK":
        return "local"

    if family in ("AF_INET", "AF_INET6"):
        return _classify_inet(family, block)

    # Unknown address family — not an internet socket, treat as local.
    return "local"


def _classify_inet(family: str, block: str) -> str:
    """Return ``'local'`` or ``'external'`` for an AF_INET/AF_INET6 block.

    FAIL-CLOSED: if the destination address cannot be extracted or parsed,
    returns ``'external'``.  We must never silently pass an internet connect
    with an unreadable address.

    Args:
        family: ``'AF_INET'`` or ``'AF_INET6'``.
        block:  The raw sockaddr struct body string extracted from the line.
    """
    addr_str: str | None = None

    if family == "AF_INET":
        m = _IPV4_RE.search(block)
        if m:
            addr_str = m.group(1)
    else:  # AF_INET6
        m = _IPV6_RE.search(block)
        if m:
            addr_str = m.group(1)
        else:
            # Fallback: scan all quoted strings in the block and take the first
            # one that parses as a valid IPv6 address.  strace sometimes omits
            # the inet_pton wrapper and writes the address directly, e.g. "::1".
            for q in _QUOTED_ADDR_RE.finditer(block):
                candidate = q.group(1)
                try:
                    parsed = ipaddress.ip_address(candidate)
                    if isinstance(parsed, ipaddress.IPv6Address):
                        addr_str = candidate
                        break
                except ValueError:
                    continue

    if addr_str is None:
        # Fail-closed: AF_INET/AF_INET6 connect with no extractable address.
        # Report as EXTERNAL — the safe side (see module docstring WHY).
        return "external"

    try:
        ip = ipaddress.ip_address(addr_str)
        # is_loopback covers 127.0.0.0/8 for IPv4 and ::1 for IPv6.
        # Private ranges (10/8, 172.16/12, 192.168/16) are NOT loopback and
        # are correctly reported as external — no "internal is fine" exception.
        return "local" if ip.is_loopback else "external"
    except ValueError:
        # Fail-closed: address string present but not parseable.
        return "external"


def scan_trace(text: str) -> list[str]:
    """Scan a full strace capture and return every EXTERNAL connect() line.

    Args:
        text: Full strace output text (may contain many lines).

    Returns:
        A list of raw lines (with trailing whitespace stripped) that were
        classified as EXTERNAL.  An empty list means no violations.

    Never raises.
    """
    offenders: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        try:
            result = classify_connect_line(line)
        except Exception:
            # classify_connect_line should never raise, but if it does,
            # fail-closed: treat the line as a potential external connection
            # rather than silently passing it.  A false positive is cheaper
            # than a missed violation in a security proof.
            offenders.append(line)
            continue
        if result == "external":
            offenders.append(line)
    return offenders


def main(argv: list[str]) -> int:
    """Scan strace trace files and report any external network connections.

    Args:
        argv: List of file paths to scan.  Each file is read as UTF-8 text
              (``errors='replace'`` so binary garbage does not crash the scan).

    Returns:
        ``0`` if no external connections were detected in any file.
        ``1`` if at least one external connection was detected.

    Output (stdout): each offender is printed as::

        external connection detected: <original strace line>

    Unreadable files are reported to stderr and skipped; they do not cause a
    non-zero exit by themselves (the violation is the network connect, not the
    missing file).
    """
    found_any = False
    for path in argv:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"egress_audit: cannot read {path!r}: {exc}", file=sys.stderr)
            continue
        for line in scan_trace(text):
            print(f"external connection detected: {line}")
            found_any = True
    return 1 if found_any else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
