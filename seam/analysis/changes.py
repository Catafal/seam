"""detect_changes — map a git diff to affected symbols and produce a pre-commit risk report.

This module owns the git boundary for Seam. It shells out to git, parses the
unified diff, maps changed line ranges to symbols in the index, runs impact
analysis on the changed symbols, and rolls up an overall risk level.

Public interface
----------------
``detect_changes(conn, base_ref, scope, repo_root) -> ChangeReport``

    scope values:
        "working"  -> ``git diff`` (unstaged working tree vs index)
        "staged"   -> ``git diff --cached``
        "branch"   -> ``git diff <base_ref>...HEAD``

``ChangeReport`` (TypedDict):
    changed_symbols  — list of ChangedSymbol (symbol + file + changed_lines)
    new_files        — list of str (added/untracked absolute paths)
    affected         — list of AffectedSymbol (symbol + tier + confidence + file)
    risk_level       — "low" | "medium" | "high" | "critical"
    ambiguous_warning — bool (True when dominant edges are AMBIGUOUS, see rollup rule)
    scope            — the scope used ("working" | "staged" | "branch")
    base_ref         — the base ref used (only relevant for scope="branch")

Risk rollup rule (documented exactly):
    1. Collect every Reached symbol from impact() across all changed symbols.
    2. Compute the highest tier reached: d=1 -> WILL_BREAK, d=2 -> LIKELY_AFFECTED, d=3+ -> MAY_NEED_TESTING.
    3. Map to risk_level:
         WILL_BREAK       -> "critical"
         LIKELY_AFFECTED  -> "high"
         MAY_NEED_TESTING -> "medium"
         no dependents    -> "low"
    4. AMBIGUOUS attenuation: if ANY reached symbol has AMBIGUOUS confidence,
       set ambiguous_warning=True. When ALL reached symbols are AMBIGUOUS
       (no EXTRACTED or INFERRED), cap the risk_level at "medium" (uncertain
       inputs cap the verdict's confidence). When some are AMBIGUOUS and some
       are not, keep the raw risk_level but still set ambiguous_warning=True.

Non-git directory / git command failure:
    Raises ``NotAGitRepoError`` (a subclass of ValueError) with a clear message.
    The handler and CLI catch this and render a user-friendly message, NOT a raw traceback.

Path storage contract (VERIFIED):
    The indexer (cli/main.py init_cmd) resolves the project root with Path(path).resolve()
    before calling index_one_file, which passes the already-resolved Path to upsert_file.
    upsert_file stores str(filepath) — the resolved absolute path.
    Therefore detect_changes MUST resolve repo_root so that
    ``str(repo_root / fd.path)`` matches what was indexed.
    Both detect_changes and _get_untracked_files use the same resolved root.

Import hierarchy:
    analysis.changes imports from analysis.impact, analysis.traversal, config.
    NO imports from server or cli.
"""

import logging
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import TypedDict

import seam.config as config
from seam.analysis.impact import impact
from seam.analysis.traversal import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_INFERRED,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Valid scope values.
VALID_SCOPES = {"working", "staged", "branch"}

# Default base ref for branch scope.
DEFAULT_BASE_REF = "main"

# Max depth for impact analysis from changed symbols.
_IMPACT_MAX_DEPTH = 3

# Timeout (seconds) for all git subprocess calls. Prevents the MCP server from
# hanging indefinitely if git blocks (e.g. lock contention, slow NFS mount).
_GIT_TIMEOUT_SECONDS = 30

# Risk level strings (ordered from least to most severe).
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"

# Maps highest tier name to risk_level (before attenuation).
_TIER_TO_RISK: dict[str, str] = {
    "WILL_BREAK": RISK_CRITICAL,
    "LIKELY_AFFECTED": RISK_HIGH,
    "MAY_NEED_TESTING": RISK_MEDIUM,
}

# Risk level ordering for capping logic.
_RISK_ORDER: list[str] = [RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL]


# ── Custom exceptions ─────────────────────────────────────────────────────────


class NotAGitRepoError(ValueError):
    """Raised when the repo_root is not a git repository or git is unavailable.

    This is intentionally a ValueError subclass so callers can catch it
    without importing this module's exception class. The handler and CLI
    must catch this to render a user-friendly message instead of a traceback.
    """


# ── Public TypedDicts ─────────────────────────────────────────────────────────


class ChangedSymbol(TypedDict):
    """A symbol whose definition spans one or more changed lines."""
    name: str
    file: str         # absolute path
    kind: str
    start_line: int
    end_line: int
    changed_lines: list[int]  # changed line numbers that overlap this symbol's range


class AffectedSymbol(TypedDict):
    """A symbol downstream/upstream of a changed symbol, from impact analysis."""
    name: str
    file: str | None  # absolute path if indexed, else None
    tier: str         # WILL_BREAK | LIKELY_AFFECTED | MAY_NEED_TESTING
    confidence: str   # EXTRACTED | INFERRED | AMBIGUOUS
    distance: int


class ChangeReport(TypedDict):
    """Full result of detect_changes()."""
    changed_symbols: list[ChangedSymbol]
    new_files: list[str]         # absolute paths of added/untracked files
    affected: list[AffectedSymbol]
    risk_level: str               # low | medium | high | critical
    ambiguous_warning: bool       # True when AMBIGUOUS edges dominate (see rollup rule)
    scope: str
    base_ref: str
    partial: bool                 # True when changed symbols exceeded the cap (SEAM_MAX_IMPACT_SYMBOLS)


# ── Git helpers ────────────────────────────────────────────────────────────────


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout as a string.

    Raises NotAGitRepoError on:
      - 'not a git repository' in stderr
      - FileNotFoundError (git not installed)
      - CalledProcessError with returncode != 0 (e.g. invalid ref)

    Other OSError variants bubble as-is (unexpected system error).
    """
    try:
        result = subprocess.run(  # noqa: S603 — controlled git invocation, no shell=True
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise NotAGitRepoError(
            f"git command timed out after {_GIT_TIMEOUT_SECONDS}s"
        ) from exc
    except FileNotFoundError as exc:
        raise NotAGitRepoError(
            "git command not found. Make sure git is installed and in PATH."
        ) from exc

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if "not a git repository" in stderr_lower:
            raise NotAGitRepoError(
                f"'{cwd}' is not a git repository (or its parents)."
            )
        raise NotAGitRepoError(
            f"git {args[0]!r} failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    return result.stdout


def _get_diff(scope: str, base_ref: str, repo_root: Path) -> str:
    """Return the raw unified diff text for the given scope.

    scope=working  -> git diff (unstaged)
    scope=staged   -> git diff --cached
    scope=branch   -> git diff <base_ref>...HEAD
    """
    if scope == "working":
        return _run_git(["diff"], repo_root)
    if scope == "staged":
        return _run_git(["diff", "--cached"], repo_root)
    # scope == "branch"
    return _run_git(["diff", f"{base_ref}...HEAD"], repo_root)


def _get_untracked_files(repo_root: Path) -> list[str]:
    """Return absolute paths of untracked files (scope=working only).

    Uses 'git ls-files --others --exclude-standard' to list untracked files.
    Returns empty list on any git error (non-fatal for the main report).
    """
    try:
        output = _run_git(
            ["ls-files", "--others", "--exclude-standard"],
            repo_root,
        )
    except NotAGitRepoError:
        return []

    paths: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line:
            # repo_root is already resolved (detect_changes resolves it at the top),
            # so (repo_root / line) produces a resolved absolute path consistent with DB storage.
            paths.append(str(repo_root / line))
    return paths


# ── Unified diff parser ────────────────────────────────────────────────────────

# Regex patterns for diff parsing.
_HUNK_HEADER_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER_RE = re.compile(r"^\+\+\+ b/(.+)$")
_DEV_NULL_RE = re.compile(r"^\+\+\+ /dev/null$")
_ADDED_FILE_RE = re.compile(r"^new file mode")


class _FileDiff:
    """Parsed result for one file in a unified diff."""
    __slots__ = ("path", "is_new_file", "changed_lines")

    def __init__(self, path: str, is_new_file: bool, changed_lines: list[int]) -> None:
        self.path = path                # relative path from repo root
        self.is_new_file = is_new_file
        self.changed_lines = changed_lines  # new-file line numbers of added/changed lines


def _parse_unified_diff(diff_text: str) -> list[_FileDiff]:
    """Parse a unified diff into per-file changed line ranges.

    Parses @@ hunk headers to extract new-file line numbers for all added
    lines ('+' prefix). Deleted-only lines do not contribute new-file
    line numbers. Modified hunks contribute the new-file side.

    Returns a list of _FileDiff — one entry per file that appears in the diff.
    Files deleted entirely ('+++ /dev/null') are skipped (no new-file lines).
    """
    results: list[_FileDiff] = []
    current_path: str | None = None
    current_lines: list[int] = []
    current_is_new: bool = False
    current_line_no: int = 0
    # git emits 'new file mode' BEFORE the '+++ b/path' header, so we cannot
    # set current_is_new directly in the '+++ b/' handler (it arrives later).
    # Use a pending flag that the '+++ b/' handler reads and clears on parse.
    pending_new_file: bool = False

    for line in diff_text.splitlines():
        # New file path header
        m = _FILE_HEADER_RE.match(line)
        if m:
            # Flush the previous file if any.
            if current_path is not None:
                results.append(_FileDiff(current_path, current_is_new, current_lines))
            current_path = m.group(1)
            current_lines = []
            # Apply the pending new-file flag set by 'new file mode' above this line.
            current_is_new = pending_new_file
            pending_new_file = False
            current_line_no = 0
            continue

        # Deleted file (no new lines to map)
        if _DEV_NULL_RE.match(line):
            if current_path is not None:
                results.append(_FileDiff(current_path, current_is_new, current_lines))
            current_path = None
            current_lines = []
            current_is_new = False
            pending_new_file = False
            current_line_no = 0
            continue

        # Detect new file marker (appears between --- and +++ headers).
        # Set pending_new_file so the subsequent '+++ b/path' handler picks it up
        # without being reset (the '+++ b/' handler reads and clears it).
        if _ADDED_FILE_RE.match(line):
            pending_new_file = True
            continue

        # Hunk header: @@ -old +new[,count] @@
        hm = _HUNK_HEADER_RE.match(line)
        if hm:
            start = int(hm.group(1))
            current_line_no = start
            continue

        # Content lines — only track new-file side.
        if current_path is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            # Added or modified line in the new file — record it.
            current_lines.append(current_line_no)
            current_line_no += 1
        elif line.startswith("-") and not line.startswith("---"):
            # Deleted line — does NOT advance new-file line counter.
            pass
        elif line.startswith(" "):
            # Context line — present in both old and new file (always has leading space).
            # Empty strings are inter-hunk artifacts, not context lines; skip them.
            current_line_no += 1

    # Flush the last file.
    if current_path is not None:
        results.append(_FileDiff(current_path, current_is_new, current_lines))

    return results


# ── Symbol lookup ──────────────────────────────────────────────────────────────


def _lookup_symbols_for_file_lines(
    conn: sqlite3.Connection,
    abs_path: str,
    changed_lines: list[int],
) -> list[ChangedSymbol]:
    """Find all symbols in a file whose range overlaps any changed line.

    A symbol overlaps if any changed line satisfies:
        symbol.start_line <= line <= symbol.end_line

    Uses a join on files.path to find the file_id, then queries symbols.
    Returns a list of ChangedSymbol dicts (one per matching symbol).

    Lines not inside ANY symbol (module-level code) result in a synthetic
    ChangedSymbol entry attributed to the file path itself (not dropped).
    """
    if not changed_lines:
        return []

    # Resolve the file_id for this absolute path.
    row = conn.execute("SELECT id FROM files WHERE path = ?", (abs_path,)).fetchone()
    if row is None:
        # File not in the index — return a file-level attribution.
        return _make_file_level_entry(abs_path, changed_lines)

    file_id = row["id"]

    # Find all symbols in this file with their line ranges.
    symbols = conn.execute(
        "SELECT name, kind, start_line, end_line FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    if not symbols:
        # File is indexed but no symbols — return file-level attribution.
        return _make_file_level_entry(abs_path, changed_lines)

    changed_set = set(changed_lines)
    results: list[ChangedSymbol] = []
    covered_lines: set[int] = set()

    for sym in symbols:
        # Find which changed lines fall within this symbol's range.
        sym_lines = [
            ln for ln in changed_lines
            if sym["start_line"] <= ln <= sym["end_line"]
        ]
        if sym_lines:
            results.append(
                ChangedSymbol(
                    name=sym["name"],
                    file=abs_path,
                    kind=sym["kind"],
                    start_line=sym["start_line"],
                    end_line=sym["end_line"],
                    changed_lines=sym_lines,
                )
            )
            covered_lines.update(sym_lines)

    # Lines not covered by any symbol → file-level attribution.
    uncovered = sorted(changed_set - covered_lines)
    if uncovered:
        results.extend(_make_file_level_entry(abs_path, uncovered))

    return results


def _make_file_level_entry(abs_path: str, lines: list[int]) -> list[ChangedSymbol]:
    """Create a module-level ChangedSymbol attribution for lines outside any symbol."""
    return [
        ChangedSymbol(
            name=f"<module:{Path(abs_path).name}>",
            file=abs_path,
            kind="module",
            start_line=0,
            end_line=0,
            changed_lines=lines,
        )
    ]


def _lookup_symbols_for_new_file(
    conn: sqlite3.Connection,
    abs_path: str,
) -> list[ChangedSymbol]:
    """Return all symbols from an added/new file that has been indexed.

    If the file is not yet in the index (brand-new, untracked), returns a
    single file-level ChangedSymbol to mark the file as new (not invisible).
    """
    row = conn.execute("SELECT id FROM files WHERE path = ?", (abs_path,)).fetchone()
    if row is None:
        # New file not yet indexed — surface as file-level entry so it's not invisible.
        return [
            ChangedSymbol(
                name=f"<new:{Path(abs_path).name}>",
                file=abs_path,
                kind="module",
                start_line=0,
                end_line=0,
                changed_lines=[],
            )
        ]

    file_id = row["id"]
    symbols = conn.execute(
        "SELECT name, kind, start_line, end_line FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    results: list[ChangedSymbol] = []
    for sym in symbols:
        results.append(
            ChangedSymbol(
                name=sym["name"],
                file=abs_path,
                kind=sym["kind"],
                start_line=sym["start_line"],
                end_line=sym["end_line"],
                changed_lines=[],  # new file — all lines are "changed"
            )
        )

    if not results:
        # File indexed but no symbols extracted.
        results = _make_file_level_entry(abs_path, [])

    return results


# ── Impact rollup ─────────────────────────────────────────────────────────────


def _collect_impact(
    conn: sqlite3.Connection,
    changed_symbol_names: list[str],
    cap: int | None = None,
) -> tuple[list[AffectedSymbol], bool]:
    """Run impact(upstream) for each changed symbol name, collect unique affected symbols.

    Returns a tuple (affected_symbols, was_truncated) where was_truncated is True
    when the number of real changed symbols exceeded the cap — the caller uses this
    to set ChangeReport.partial so agents know the risk verdict covers only a subset.

    The cap defaults to config.SEAM_MAX_IMPACT_SYMBOLS (env-configurable). Passing
    an explicit cap is supported for testing without needing to monkeypatch config.

    Uses a deduplication dict keyed by symbol name. When the same symbol
    appears via multiple changed sources, we keep the highest-tier (most severe)
    entry (lowest distance = most severe).

    Only runs impact on indexable symbol names (skips module-level <module:...> entries
    that are not real indexed symbols — the impact engine gracefully handles unknown
    symbols, but it's wasteful to call for every <module:file.py> entry).
    """
    # Resolve cap: prefer explicit arg (test injection), fall back to config.
    max_symbols = cap if cap is not None else config.SEAM_MAX_IMPACT_SYMBOLS

    # Filter out synthetic module-level names.
    real_names = [n for n in changed_symbol_names if not n.startswith("<")]
    if not real_names:
        return [], False

    # Cap changed symbols to avoid unbounded impact() calls on very large diffs.
    # Processes the first max_symbols in list order (deterministic).
    # A warning is logged when the cap is hit — visible via SEAM_LOG_LEVEL=WARNING.
    was_truncated = len(real_names) > max_symbols
    if was_truncated:
        logger.warning(
            "detect_changes: %d changed symbols exceeds cap %d; impact computed on first %d",
            len(real_names),
            max_symbols,
            max_symbols,
        )
        real_names = real_names[:max_symbols]

    # Deduplicate by (name, lowest distance) across all seeds.
    # key=name -> AffectedSymbol (keep the one with lowest distance / worst tier).
    best: dict[str, AffectedSymbol] = {}

    for name in real_names:
        result = impact(conn, target=name, direction="upstream", max_depth=_IMPACT_MAX_DEPTH)
        if not result.get("found"):
            continue
        tier_group = result.get("upstream", {})
        for tier, entries in tier_group.items():
            for entry in entries:
                aname = entry["name"]
                candidate = AffectedSymbol(
                    name=aname,
                    file=entry.get("file"),
                    tier=tier,
                    confidence=entry.get("confidence", CONFIDENCE_INFERRED),
                    distance=entry.get("distance", 1),
                )
                existing = best.get(aname)
                if existing is None or candidate["distance"] < existing["distance"]:
                    best[aname] = candidate

    return list(best.values()), was_truncated


def _compute_risk_level(
    affected: list[AffectedSymbol],
) -> tuple[str, bool]:
    """Compute overall risk_level and ambiguous_warning from affected symbols.

    Returns (risk_level, ambiguous_warning).

    Risk rollup rule (exact):
    1. Find the highest tier across all affected symbols:
         WILL_BREAK > LIKELY_AFFECTED > MAY_NEED_TESTING > (none)
    2. Map that tier to risk_level:
         WILL_BREAK       -> critical
         LIKELY_AFFECTED  -> high
         MAY_NEED_TESTING -> medium
         (none)           -> low
    3. AMBIGUOUS attenuation:
         a. If ALL affected symbols have AMBIGUOUS confidence → cap risk_level at "medium".
         b. If ANY symbol has AMBIGUOUS confidence (but not all) → keep raw risk, set warning.
         c. Set ambiguous_warning=True in cases (a) and (b).
    """
    if not affected:
        return RISK_LOW, False

    # Tier severity order (highest first) for finding the worst tier.
    tier_severity = {"WILL_BREAK": 3, "LIKELY_AFFECTED": 2, "MAY_NEED_TESTING": 1}

    highest_severity = 0
    highest_tier: str | None = None
    all_ambiguous = True
    any_ambiguous = False

    for sym in affected:
        sev = tier_severity.get(sym["tier"], 0)
        if sev > highest_severity:
            highest_severity = sev
            highest_tier = sym["tier"]

        conf = sym["confidence"]
        if conf == CONFIDENCE_AMBIGUOUS:
            any_ambiguous = True
        else:
            all_ambiguous = False

    # Base risk from highest tier.
    if highest_tier is None:
        base_risk = RISK_LOW
    else:
        base_risk = _TIER_TO_RISK.get(highest_tier, RISK_LOW)

    ambiguous_warning = any_ambiguous

    # Attenuation: if ALL edges are AMBIGUOUS, cap at "medium".
    if all_ambiguous and any_ambiguous:
        # Cap risk_level at medium (uncertain inputs limit verdict confidence).
        risk_idx = _RISK_ORDER.index(base_risk)
        medium_idx = _RISK_ORDER.index(RISK_MEDIUM)
        final_risk = _RISK_ORDER[min(risk_idx, medium_idx)]
    else:
        final_risk = base_risk

    return final_risk, ambiguous_warning


# ── Public interface ───────────────────────────────────────────────────────────


def detect_changes(
    conn: sqlite3.Connection,
    base_ref: str = DEFAULT_BASE_REF,
    scope: str = "working",
    repo_root: Path | None = None,
) -> ChangeReport:
    """Map git diff to affected symbols and compute an overall pre-commit risk level.

    Args:
        conn:       Open SQLite connection (read-only; no writes).
        base_ref:   Git ref to compare against for scope="branch" (e.g. "main").
                    Ignored for scope="working" and scope="staged".
        scope:      One of "working", "staged", "branch". Default: "working".
        repo_root:  Absolute path to the git repository root. If None, uses the
                    current working directory (not recommended in production;
                    pass an explicit path from the CLI or handler).

    Returns:
        ChangeReport TypedDict (see module docstring for field descriptions).

    Raises:
        NotAGitRepoError: if repo_root is not a git repo or git fails.
        ValueError: if scope is not one of the valid values.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {sorted(VALID_SCOPES)}; got {scope!r}")

    # Always resolve repo_root so that `str(repo_root / fd.path)` produces the
    # same resolved absolute path the indexer stored in the DB.
    # The indexer (cli/main.py init) calls Path(path).resolve() before indexing, so
    # DB paths are fully resolved. On macOS, /tmp is a symlink to /private/tmp —
    # an unresolved root would silently miss every DB lookup for files under /tmp.
    repo_root = (repo_root or Path(os.getcwd())).resolve()

    # Get the unified diff for the requested scope.
    diff_text = _get_diff(scope, base_ref, repo_root)

    # Parse diff into per-file changed line ranges.
    file_diffs: list[_FileDiff] = _parse_unified_diff(diff_text)

    changed_symbols: list[ChangedSymbol] = []
    new_files: list[str] = []

    for fd in file_diffs:
        # repo_root is already resolved above; joining with the relative diff path
        # produces a resolved absolute path that matches what the indexer stored.
        abs_path = str(repo_root / fd.path)

        if fd.is_new_file:
            # New file: surface its symbols (or a file-level entry if not indexed yet).
            new_files.append(abs_path)
            syms = _lookup_symbols_for_new_file(conn, abs_path)
            changed_symbols.extend(syms)
        else:
            # Modified file: map changed lines to owning symbols.
            syms = _lookup_symbols_for_file_lines(conn, abs_path, fd.changed_lines)
            changed_symbols.extend(syms)

    # For working scope, also include untracked files.
    if scope == "working":
        untracked = _get_untracked_files(repo_root)
        for abs_path in untracked:
            if abs_path not in new_files:
                new_files.append(abs_path)
                syms = _lookup_symbols_for_new_file(conn, abs_path)
                changed_symbols.extend(syms)

    # Run impact on all changed symbol names (deduplication inside _collect_impact).
    # The function returns (affected_list, was_truncated) — partial=True when cap was hit.
    changed_names = [s["name"] for s in changed_symbols]
    affected, partial = _collect_impact(conn, changed_names)

    # Compute overall risk level.
    risk_level, ambiguous_warning = _compute_risk_level(affected)

    logger.debug(
        "detect_changes(scope=%r, base_ref=%r, repo_root=%s) -> "
        "%d changed_symbols, %d affected, risk=%s, partial=%s, ambiguous_warning=%s",
        scope,
        base_ref,
        repo_root,
        len(changed_symbols),
        len(affected),
        risk_level,
        partial,
        ambiguous_warning,
    )

    return ChangeReport(
        changed_symbols=changed_symbols,
        new_files=new_files,
        affected=affected,
        risk_level=risk_level,
        ambiguous_warning=ambiguous_warning,
        scope=scope,
        base_ref=base_ref,
        partial=partial,
    )
