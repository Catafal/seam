"""Affected-tests analysis — given a set of changed files, find which test files must run.

Algorithm (modeled on CodeGraph's `affected` command, §6.4 of codegraph-vs-seam.md):
  1. Resolve each input path to an absolute path (to match DB storage contract).
  2. For each changed file:
     (a) If the file itself is a test file (`is_test_file`), add it to affected_tests directly.
     (b) Look up all symbols defined in that file (via the `symbols` + `files` join).
     (c) For each such symbol, run `impact(direction="upstream", max_depth=depth)` to collect
         upstream dependents — the set of symbols that would break if the changed symbol changed.
     (d) Any dependent whose `file` resolves to a test file -> add that file to affected_tests.
  3. Dedup affected_tests (stable-sorted for determinism).
  4. Count total_dependents_traversed (all unique dependent entries across all symbols).

Why reuse impact() and is_test_file():
  - No duplicate blast-radius logic (PRD user story 21).
  - impact() already carries `file` and `is_test` per entry — we just filter is_test=True.
  - is_test_file() is the single source of truth for test classification.

Path resolution contract:
  The indexer (cli/main.py init) calls Path(path).resolve() before upsert_file().
  DB stores resolved absolute paths. We must resolve input paths identically so
  `(repo_root / relative_input).resolve()` matches stored DB paths.

Import hierarchy:
  This module lives in `analysis`. Imports from: analysis.impact, analysis.testpaths, config.
  NO imports from server or cli (mirrors changes.py pattern).
"""

import logging
import sqlite3
from pathlib import Path
from typing import TypedDict

import seam.config as config
from seam.analysis.impact import impact
from seam.analysis.testpaths import is_test_file

logger = logging.getLogger(__name__)

# ── Public types ───────────────────────────────────────────────────────────────


class AffectedResult(TypedDict):
    """Result shape returned by affected().

    Fields:
        changed_files          — resolved absolute paths of the input changed files
        affected_tests         — sorted unique absolute paths of affected test files
        total_dependents_traversed — total dependent entries traversed (may double-count
                                     across changed symbols from different files when the
                                     same dependent is reachable from multiple changed symbols)
        partial                — True when the per-file symbol cap (SEAM_MAX_AFFECTED_SYMBOLS)
                                 was hit for at least one file; the affected set may be incomplete
    """

    changed_files: list[str]
    affected_tests: list[str]
    total_dependents_traversed: int
    partial: bool


# ── Internal helpers ───────────────────────────────────────────────────────────


def _resolve_path(raw: str, repo_root: Path) -> str:
    """Resolve a raw path string to an absolute path matching the DB storage contract.

    WHY: The indexer stores resolved absolute paths. Relative inputs must be
    resolved against repo_root (not cwd) so DB lookups match.
    """
    p = Path(raw)
    if p.is_absolute():
        # Already absolute — still resolve to canonicalize symlinks (macOS /tmp vs /private/tmp)
        return str(p.resolve())
    # Relative input: resolve against repo_root
    return str((repo_root / p).resolve())


def _symbols_in_file(conn: sqlite3.Connection, abs_path: str) -> list[str]:
    """Return all symbol names defined in the given file (by absolute path).

    WHY: We need to know which symbols to run impact() on. The join on files.path
    is the same pattern used in the query engine and changes module.
    """
    rows = conn.execute(
        """
        SELECT s.name
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path = ?
        """,
        (abs_path,),
    ).fetchall()
    return [row[0] for row in rows]


def _collect_test_dependents(
    conn: sqlite3.Connection,
    symbol_name: str,
    depth: int,
) -> tuple[list[str], int]:
    """Run upstream impact for one symbol and collect test file paths from its dependents.

    Returns (test_file_paths, total_entries_count).

    WHY upstream: we want "who depends on this symbol" — the reverse-dependency set.
    An upstream dependent is something that would break if the symbol changed.
    impact() with direction="upstream" gives exactly this.

    Returns ([], 0) when the symbol is not in the index (impact returns found=False).
    All test files are collected from entries with is_test=True.
    """
    # depth=0 means no traversal — return empty immediately.
    if depth <= 0:
        return [], 0

    result = impact(conn, target=symbol_name, direction="upstream", max_depth=depth)

    if not result.get("found"):
        # Symbol not indexed (e.g. synthetic module-level names) — skip silently.
        logger.debug("affected: symbol %r not found in index, skipping", symbol_name)
        return [], 0

    tier_group = result.get("upstream", {})

    test_files: list[str] = []
    total_count = 0

    for entries in tier_group.values():
        for entry in entries:
            total_count += 1
            # is_test is pre-computed by impact() via is_test_file(entry["file"])
            if entry.get("is_test") and entry.get("file"):
                test_files.append(entry["file"])

    return test_files, total_count


# ── Public interface ───────────────────────────────────────────────────────────


def affected(
    conn: sqlite3.Connection,
    changed_files: list[str],
    *,
    depth: int = config.SEAM_AFFECTED_DEPTH,
    repo_root: Path,
) -> AffectedResult:
    """Compute the set of test files affected by changes to the given files.

    Args:
        conn:          Open SQLite connection (read-only; no writes).
        changed_files: List of file paths (absolute or relative to repo_root).
                       May be empty — returns an empty result (not an error).
        depth:         Max traversal depth for upstream impact. Default from config.
                       depth=0 returns empty affected_tests (no traversal).
        repo_root:     Absolute path to the project root. Used to resolve relative
                       input paths to match DB storage.

    Returns:
        AffectedResult TypedDict with:
            changed_files              — resolved absolute paths of input files
            affected_tests             — sorted unique absolute paths of affected test files
            total_dependents_traversed — total entries traversed (may double-count when the
                                         same dependent is reachable from multiple changed symbols)
            partial                    — True when a file had more symbols than
                                         SEAM_MAX_AFFECTED_SYMBOLS; result may be incomplete

    Never raises. Files not in the index are silently skipped (no error).
    A changed file with no dependents yields an empty contribution (not an error).
    """
    sym_cap = config.SEAM_MAX_AFFECTED_SYMBOLS

    # Resolve all input paths to absolute (DB storage contract).
    resolved_inputs = [_resolve_path(p, repo_root) for p in changed_files]

    # Collect test files and dependent counts across all changed files.
    all_test_files: set[str] = set()
    total_dependents = 0
    hit_cap = False

    # Memoize impact() results within this call so a dependent reachable from multiple
    # changed symbols is not re-traversed. Key: symbol name (string).
    impact_cache: dict[str, tuple[list[str], int]] = {}

    for abs_path in resolved_inputs:
        # (a) The changed file itself is a test file -> include directly.
        if is_test_file(abs_path):
            all_test_files.add(abs_path)
            logger.debug("affected: %r is itself a test file — including directly", abs_path)

        # (b) Look up all symbols defined in this file.
        symbol_names = _symbols_in_file(conn, abs_path)

        if not symbol_names and not is_test_file(abs_path):
            # A non-test file with no indexed symbols is likely a stale index or path mismatch.
            # Log at INFO so this silent case is visible to operators — an empty affected set
            # from a stale index is a false-negative, not a safe "nothing to test".
            logger.info(
                "affected: %r has no indexed symbols and is not a test file — "
                "possible stale index or path mismatch; contributing 0 test dependents",
                abs_path,
            )

        # (c) Apply per-file symbol cap to prevent unbounded O(n) impact traversal.
        if len(symbol_names) > sym_cap:
            logger.info(
                "affected: %r has %d symbols, exceeds cap %d — truncating to first %d; "
                "result.partial will be True",
                abs_path,
                len(symbol_names),
                sym_cap,
                sym_cap,
            )
            symbol_names = symbol_names[:sym_cap]
            hit_cap = True

        # (d) For each symbol, find its upstream dependents and collect test files.
        # Memoize to avoid re-traversing the same symbol from multiple changed files.
        for sym_name in symbol_names:
            if sym_name in impact_cache:
                test_files, count = impact_cache[sym_name]
            else:
                test_files, count = _collect_test_dependents(conn, sym_name, depth)
                impact_cache[sym_name] = (test_files, count)

            all_test_files.update(test_files)
            total_dependents += count

    # Stable-sorted dedup: deterministic ordering for agent consumers.
    sorted_test_files = sorted(all_test_files)

    logger.debug(
        "affected(%d files, depth=%d) -> %d affected tests, %d dependents traversed, partial=%s",
        len(changed_files),
        depth,
        len(sorted_test_files),
        total_dependents,
        hit_cap,
    )

    return AffectedResult(
        changed_files=resolved_inputs,
        affected_tests=sorted_test_files,
        total_dependents_traversed=total_dependents,
        partial=hit_cap,
    )
