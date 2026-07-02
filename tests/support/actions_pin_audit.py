"""GitHub Actions uses: ref auditor — flags mutable (non-SHA-pinned) action refs.

WHY this exists:
  P5.2 hardens Seam's PyPI release workflow.  A mutable ``uses: owner/repo@v1``
  ref means the action code can change silently between runs — a supply-chain
  risk.  Pinning to a full 40-hex commit SHA makes every CI run bit-for-bit
  reproducible and immune to tag-squatting or branch-force-push attacks.

WHY stdlib-only (re, sys, pathlib):
  Test-support code must load in a bare environment with no extras installed.
  Staying stdlib-only avoids pulling yaml or any other dep that might not be
  present.  We parse only the ``uses:`` line — a simple regex suffices.

WHY fail-closed on ambiguous refs:
  A supply-chain proof must err toward false positives.  Any ``uses:`` ref we
  cannot confidently classify as PINNED (local action OR 40-hex SHA) is
  reported as MUTABLE.  Letting an unparseable ref slip through would defeat
  the purpose of the audit.

WHY local ``./…`` refs are allowed:
  Local composite actions (defined inside the same repo) are versioned together
  with the workflow file.  They cannot be tampered with independently and do not
  require SHA pinning.

Classification rules:
  PINNED   — ``./anything``  (local action)
             OR  ``owner/repo@<40-hex-sha>``  (optional trailing ``# vX`` comment)
             OR  ``owner/repo/subdir@<40-hex-sha>``  (subdir action, same rule)
  MUTABLE  — everything else (@vN tag, @branch, @release/v1, 39/41-char hex, …)
"""

import re
import sys
from collections.abc import Sequence
from pathlib import Path

# ── regex patterns ────────────────────────────────────────────────────────────

# Match a ``uses:`` YAML key in two forms:
#   ``      - uses: ref``   (inline list-item: first key is ``uses``)
#   ``        uses: ref``   (subsequent key under a list item)
# Capture the ref value.  The value may be followed by a ``#`` comment.
_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(.+?)(?:\s*#.*)?$")

# A valid 40-hex SHA (exactly 40 lowercase or uppercase hex digits).
_SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


# ── public API ────────────────────────────────────────────────────────────────


def classify_uses_ref(ref: str) -> str:
    """Classify a single ``uses:`` ref string.

    Args:
        ref: The raw ``uses:`` value, e.g. ``"actions/checkout@v4"``,
             ``"actions/checkout@abc123…40hexchars"``, or ``"./local"``.
             Any trailing ``# comment`` must already be stripped by the caller.

    Returns:
        ``'pinned'``  — local action (``./…``) or SHA-pinned (40-hex after ``@``).
        ``'mutable'`` — tag, branch, partial SHA, or anything else.

    Never raises.  An unparseable ref is treated as ``'mutable'`` (fail-closed).
    """
    ref = ref.strip()

    # Local composite actions are always safe — they live in the same repo.
    if ref.startswith("./"):
        return "pinned"

    # Must contain exactly one ``@`` to separate owner/repo from the ref.
    # Fail-closed on refs without ``@``.
    at_idx = ref.find("@")
    if at_idx < 0:
        return "mutable"

    after_at = ref[at_idx + 1:]

    # A valid pin is exactly 40 hex characters — no more, no less.
    return "pinned" if _SHA40_RE.match(after_at) else "mutable"


def scan_workflow_text(text: str, source: str = "<text>") -> list[tuple[str, str]]:
    """Scan workflow file text and return all MUTABLE ``uses:`` refs.

    Args:
        text:   Full text of a GitHub Actions workflow YAML file.
        source: A label attached to each offender (typically the file path).

    Returns:
        A list of ``(source, ref)`` tuples — one per mutable ``uses:`` line.
        Empty list means no violations.

    Never raises.
    """
    offenders: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        m = _USES_RE.match(raw_line)
        if not m:
            continue
        ref_value = m.group(1).strip()
        try:
            verdict = classify_uses_ref(ref_value)
        except Exception:
            # classify_uses_ref must never raise, but if it does, fail-closed.
            offenders.append((source, ref_value))
            continue
        if verdict == "mutable":
            offenders.append((source, ref_value))
    return offenders


def scan_workflow_files(paths: Sequence[str | Path]) -> list[tuple[str, str]]:
    """Scan a list of workflow file paths and collect MUTABLE refs.

    Args:
        paths: Paths to YAML workflow files to audit.

    Returns:
        All ``(file_path, ref)`` offender tuples across all files.
        Unreadable files are skipped with a stderr warning.

    Never raises.
    """
    offenders: list[tuple[str, str]] = []
    for path in paths:
        path_str = str(path)
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(
                f"actions_pin_audit: cannot read {path_str!r}: {exc}",
                file=sys.stderr,
            )
            continue
        offenders.extend(scan_workflow_text(text, source=path_str))
    return offenders


def main(argv: Sequence[str]) -> int:
    """Scan workflow files and report mutable action refs.

    Args:
        argv: List of workflow file paths to scan.

    Returns:
        ``0`` — all refs are SHA-pinned (or local).
        ``1`` — at least one mutable ref was found.

    Output (stdout): each offender is printed as::

        mutable action ref: <file>: <ref>

    Never raises.
    """
    offenders = scan_workflow_files(argv)
    for source, ref in offenders:
        print(f"mutable action ref: {source}: {ref}")
    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
