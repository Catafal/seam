"""Index rebase leaf — re-homes a Seam index from one machine's filesystem
layout onto another's via a single-column prefix rewrite.

The index stores absolute filesystem paths in exactly ONE column: `files.path`.
Everything else (edges, symbols, comments) references files by `file_id` FK or
stores string symbol names. So re-homing an index built on machine A to run on
machine B is a pure prefix rewrite of `files.path`.

Public API
----------
rebase_index(conn, *, new_root, old_root=None) -> int

    Rewrites every real `files.path` row whose path starts with `old_root`,
    replacing the `old_root` prefix with `new_root`.

    When `old_root` is None it is AUTO-DETECTED as the longest common
    directory prefix of all real file rows (those whose path does NOT start
    with ':').

    Returns the number of rows rewritten. Returns 0 for no-op cases (already
    local, empty DB, old_root not found). NEVER raises — any DB error is
    caught, logged, and surfaced as 0.

Import contract (leaf discipline)
-----------------------------------
Only stdlib + sqlite3 (+ logging). This module MUST NOT import from
`seam.cli`, `seam.server`, or any module that imports from those layers.
Config is not needed here (no env-var-controlled knobs).
"""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# Synthetic file-row marker. The post-pass bridge (synthesis_index.py, test_edges.py)
# stores edges under a fake file whose path starts with ':' so that they survive
# per-file FK cascades but are not treated as real on-disk files.
# We must NEVER rewrite these rows.
_SYNTHETIC_PREFIX = ":"


def _detect_old_root(conn: sqlite3.Connection) -> str | None:
    """Auto-detect the common directory prefix of all real file rows.

    WHY exclude synthetic rows: they start with ':' (not a real path), so
    including them would corrupt the os.path.commonpath() result.

    Returns the detected prefix string, or None when the DB has no real file rows.
    Never raises — errors are logged and return None.
    """
    try:
        rows = conn.execute(
            "SELECT path FROM files WHERE path NOT LIKE ?",
            (_SYNTHETIC_PREFIX + "%",),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug("rebase: failed to read files table for auto-detect: %s", exc)
        return None

    if not rows:
        return None

    paths = [row[0] for row in rows]

    if len(paths) == 1:
        # With a single path, commonpath returns the path itself if it is a file.
        # We want the parent directory as the "prefix" so that the relative portion
        # is "filename" and not "". Using os.path.dirname is correct here.
        return os.path.dirname(paths[0])

    try:
        # os.path.commonpath returns the longest common sub-path.
        # e.g. ["/ci/ws/src/a.py", "/ci/ws/tests/b.py"] → "/ci/ws"
        return os.path.commonpath(paths)
    except ValueError as exc:
        # commonpath raises ValueError on an empty list or mixed drive roots (Windows).
        logger.debug("rebase: commonpath failed during auto-detect: %s", exc)
        return None


def rebase_index(
    conn: sqlite3.Connection,
    *,
    new_root: str,
    old_root: str | None = None,
) -> int:
    """Rewrite `files.path` rows from old_root prefix to new_root.

    Args:
        conn:     Open SQLite connection to the Seam index.
        new_root: The local root to rewrite paths to.
        old_root: The source machine's root to rewrite FROM. When None,
                  auto-detected as the common directory prefix of all real rows.

    Returns:
        Number of rows rewritten. 0 for no-op, error, or empty DB.

    Contract:
        - NEVER raises — all exceptions are caught and logged.
        - Synthetic rows (path LIKE ':%') are NEVER touched.
        - Idempotent: calling again after a successful rebase returns 0.
    """
    try:
        return _rebase_index_impl(conn, new_root=new_root, old_root=old_root)
    except Exception as exc:  # noqa: BLE001
        # Catch-all so the caller (CLI or library) never gets an unhandled exception.
        # The leaf contract: NEVER raises.
        logger.warning("rebase: unexpected error (returning 0): %s", exc)
        return 0


def _rebase_index_impl(
    conn: sqlite3.Connection,
    *,
    new_root: str,
    old_root: str | None,
) -> int:
    """Inner (raising) implementation — wrapped by rebase_index for safety.

    Algorithm:
      1. Resolve new_root to a normalized, separator-consistent string.
      2. If old_root is None, auto-detect from DB.
      3. Early-exit when old_root is None (empty DB) or old_root == new_root.
      4. Ensure old_root ends with the OS separator so we match whole directory
         components and not a path like '/workspace/app' matching '/workspace/app2'.
      5. Run a single UPDATE … WHERE path LIKE '<old_root>%' AND path NOT LIKE ':%'.
         SQLite's LIKE with a trailing '%' is a prefix scan and avoids a full table
         scan on large indexes when the `path` column has an index.
      6. Return conn.execute().rowcount.
    """
    # ── Step 1: Normalize roots ────────────────────────────────────────────────
    # Use os.path.normpath to collapse redundant separators and '.' components.
    # We do NOT call os.path.abspath because the caller may pass a foreign-OS path
    # (e.g. a Linux path on a macOS machine). We normalize only what we can safely.
    new_root_norm = os.path.normpath(new_root)

    # ── Step 2: Resolve old_root ──────────────────────────────────────────────
    if old_root is None:
        detected = _detect_old_root(conn)
        if detected is None:
            # Empty DB or all-synthetic DB — nothing to rewrite.
            logger.debug("rebase: auto-detect found no real file rows; returning 0")
            return 0
        old_root_norm = os.path.normpath(detected)
    else:
        old_root_norm = os.path.normpath(old_root)

    # ── Step 3: Early-exit on identity rebase ─────────────────────────────────
    # WHY: if old and new are identical after normalization, the UPDATE would
    # be a no-op anyway, but we can skip the DB round trip entirely.
    if old_root_norm == new_root_norm:
        logger.debug("rebase: old_root == new_root ('%s'); nothing to do", new_root_norm)
        return 0

    # ── Step 4: Build the prefix string with trailing separator ───────────────
    # Adding sep ensures we match whole directory components.
    # E.g. old_root = "/workspace/app" → prefix = "/workspace/app/"
    # This prevents "/workspace/app2/foo.py" from being rewritten when only
    # "/workspace/app" was specified.
    #
    # Special case: if old_root IS the filesystem root ("/"), do not add
    # another "/" (that would make "//"). Check with os.path.dirname identity.
    old_sep = os.sep  # local OS separator (/ on macOS/Linux, \ on Windows)
    if old_root_norm.endswith(old_sep):
        old_prefix = old_root_norm
    else:
        old_prefix = old_root_norm + old_sep

    # Similarly, new_root must end with sep so the replacement produces
    # "/new/root/src/a.py" not "/new/rootsrc/a.py".
    if new_root_norm.endswith(old_sep):
        new_prefix = new_root_norm
    else:
        new_prefix = new_root_norm + old_sep

    # ── Step 5: Single-pass SQL UPDATE ────────────────────────────────────────
    # WHY SQL (not Python loop): one DB round trip rewriting N rows atomically.
    # SQLite LIKE with trailing '%' uses the prefix index when available.
    # REPLACE(path, old, new) is not LIKE-anchored, but since we only run it on
    # rows WHERE path LIKE old_prefix||'%', it is safe.
    #
    # LIKE pattern for the WHERE clause: the prefix + '%' wildcard.
    # We also exclude synthetic rows (path NOT LIKE ':%').
    like_pattern = old_prefix + "%"

    cursor = conn.execute(
        """
        UPDATE files
           SET path = ? || substr(path, ?)
         WHERE path LIKE ?
           AND path NOT LIKE ?
        """,
        (
            new_prefix,
            len(old_prefix) + 1,  # SQLite substr is 1-indexed
            like_pattern,
            _SYNTHETIC_PREFIX + "%",
        ),
    )
    conn.commit()

    n = cursor.rowcount
    logger.info(
        "rebase: rewrote %d file row(s): '%s' → '%s'",
        n,
        old_prefix,
        new_prefix,
    )
    return n
