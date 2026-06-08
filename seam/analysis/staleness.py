"""Index staleness detector — single source of truth for "is this index stale".

Given an open DB connection + the project root, returns a StalenessVerdict.
Encapsulates the "is this index stale" logic behind one clean interface and is
the single source of truth — `seam status` and the MCP read tools both use it.

Algorithm (bounded-scan + per-process TTL cache):
  1. Query the N most recently indexed REAL files (path NOT LIKE ':%'), ordered
     by indexed_at DESC, LIMIT SEAM_STALENESS_SCAN_CAP.
  2. For each file in that set, compare on-disk st_mtime vs stored mtime.
     - Newer on-disk mtime → file was modified since last index → stale.
     - OSError (file missing / permission denied) on a real file path → deleted → stale.
  3. If the watcher is alive (watcher_alive=True), file drift is NOT reported as
     stale — the watcher self-heals file changes. HOWEVER, if synthesized edges
     exist in the DB (SELECT COUNT(*) > 0 WHERE synthesized_by IS NOT NULL), still
     report stale because the watcher never recomputes synthesized edges or clusters.
  4. Cache the verdict in a module-level dict keyed by (resolved db_path, resolved root)
     for SEAM_STALENESS_TTL_SECONDS to avoid re-stat on every MCP read call.

Conservatism direction: on any IO/DB error, return stale=False (do NOT cry wolf).
WHY: a false-positive stale banner erodes agent trust; a missed staleness is a
pre-existing condition (agents already lived without the banner).

Never raises. All IO wrapped in try/except. Degrades gracefully on pre-v12 indexes
(the synthesized_by guard handles the missing-column case).

Mirrors the leaf discipline of seam/analysis/affected.py:
  - imports only stdlib + seam/config
  - takes a conn + root (not pure-leaf, but bounded/safe IO only)
  - never raises, never mutates the DB
"""

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import TypedDict

import seam.config as config

logger = logging.getLogger(__name__)

# ── Public types ───────────────────────────────────────────────────────────────


class StalenessVerdict(TypedDict):
    """Result shape returned by check_staleness().

    Fields:
        stale   — True when the index is believed to be stale; False otherwise.
                  When False, callers omit the banner entirely (absence = fresh).
        reason  — Human/agent-readable cause of staleness. Empty string when fresh.
        hint    — The specific remedy. Empty string when fresh.
                  File drift → "Run 'seam sync' to reconcile the index."
                  Synthesized-edge drift → "Run 'seam init' or 'seam sync' ..."
    """

    stale: bool
    reason: str
    hint: str


# ── Hints (single source of truth for hint text) ───────────────────────────────

_HINT_FILE_DRIFT = "Run 'seam sync' to reconcile the index."
_HINT_SYNTH_DRIFT = (
    "Run 'seam init' or 'seam sync' to refresh synthesized edges and clusters."
)

# ── Per-process TTL verdict cache ──────────────────────────────────────────────

# Keyed by (db_path_str, root_str) → (cached_at: float, verdict: StalenessVerdict).
# Module-level dict is safe: one dict per process, bounded by number of unique DB paths
# (typically 1–2 in any real server process). No eviction needed; TTL guards freshness.
_cache: dict[str, tuple[float, StalenessVerdict]] = {}


def _cache_key(conn: sqlite3.Connection, root: Path) -> str:
    """Build a string cache key from the DB path and root path."""
    try:
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:  # noqa: BLE001 — closed/invalid conn → no cache
        db_path = "<unknown>"
    return f"{db_path}||{root.resolve()}"


def _cache_get(key: str) -> StalenessVerdict | None:
    """Return a cached verdict if still within TTL, else None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    cached_at, verdict = entry
    ttl = config.SEAM_STALENESS_TTL_SECONDS
    if ttl > 0 and (time.time() - cached_at) < ttl:
        return verdict
    # Expired — evict.
    _cache.pop(key, None)
    return None


def _cache_put(key: str, verdict: StalenessVerdict) -> None:
    """Store a verdict in the cache."""
    _cache[key] = (time.time(), verdict)


# ── Watcher liveness probe ─────────────────────────────────────────────────────


def _watcher_is_alive(pid_file: Path) -> int | None:
    """Return the PID if a live watcher process is recorded, else None.

    Reads the PID file and probes the process with os.kill(pid, 0). A stale
    PID file (process gone) returns None so callers can safely overwrite it.

    Extracted here from cli/main.py to be importable by the handler layer
    without creating a seam.server → seam.cli import cycle.
    """
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, doesn't actually signal
    except OSError:
        return None  # no such process (or not ours) — treat as dead
    return pid


# ── Synthesized-edge check ─────────────────────────────────────────────────────


def _has_synthesized_edges(conn: sqlite3.Connection) -> bool:
    """Return True if the DB has any synthesized edges (synthesized_by IS NOT NULL).

    WHY: the watcher never recomputes synthesized edges or clusters. Even with a
    live watcher, a synthesis-enabled index becomes stale for synthesized data.

    Guard for pre-v12 indexes: if the synthesized_by column doesn't exist, return
    False (conservatism: treat as no synthesized edges, don't report staleness).
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE synthesized_by IS NOT NULL"
        ).fetchone()
        return bool(row and row[0] > 0)
    except Exception:  # noqa: BLE001 — pre-v12 DB (no synthesized_by column), or closed conn
        return False


# ── Core detection ─────────────────────────────────────────────────────────────


def _scan_for_drift(
    conn: sqlite3.Connection,
    scan_cap: int,
) -> tuple[int, int]:
    """Scan the N most recently indexed real files for mtime drift and deletions.

    Returns (changed_count, deleted_count).

    WHY bounded scan: only the newest `scan_cap` files are stat'd. A stale file
    outside the cap window is NOT detected — this is the documented limitation.
    On a repo with 10k files, checking all of them on every MCP read call would
    add ~50-100ms per call on spinning disk.
    """
    rows = conn.execute(
        """
        SELECT path, mtime
        FROM files
        WHERE path NOT LIKE ':%'
        ORDER BY indexed_at DESC
        LIMIT ?
        """,
        (scan_cap,),
    ).fetchall()

    changed = 0
    deleted = 0
    for row in rows:
        stored_path, stored_mtime = row[0], row[1]
        p = Path(stored_path)
        try:
            disk_mtime = p.stat().st_mtime
            if disk_mtime > stored_mtime:
                changed += 1
        except OSError:
            # File is gone (deleted) or permission denied.
            # Both count as "stale" — the index references something that changed.
            deleted += 1
    return changed, deleted


def check_staleness(
    conn: sqlite3.Connection,
    *,
    root: Path,
    watcher_alive: bool = False,
    scan_cap: int | None = None,
) -> StalenessVerdict:
    """Determine whether the index is stale relative to the current on-disk state.

    Args:
        conn:          Open SQLite connection (read-only; never mutated).
        root:          Absolute project root path. Used for cache key and watcher
                       PID-file location derivation.
        watcher_alive: When True, the watcher is running and self-heals file drift.
                       File mtime drift is then NOT reported as stale (but
                       synthesized-edge drift still is).
        scan_cap:      Override for SEAM_STALENESS_SCAN_CAP (used by tests to
                       exercise the boundary without setting the config).

    Returns:
        StalenessVerdict with stale, reason, hint.

    Never raises. On any error, returns stale=False (conservative default; do NOT
    cry wolf when freshness cannot be determined).
    """
    # Fast path: knob off → no IO, no banner, byte-identical to pre-feature.
    # The handlers skip calling this when the knob is off, but this guard is a
    # safety net in case this function is called directly with the knob off.
    if config.SEAM_STALENESS_CHECK != "on":
        return StalenessVerdict(stale=False, reason="", hint="")

    # Resolve scan_cap: test override takes precedence; otherwise use config.
    effective_cap = scan_cap if scan_cap is not None else config.SEAM_STALENESS_SCAN_CAP

    # Per-process TTL cache: skip re-stat if verdict is fresh.
    cache_key = _cache_key(conn, root)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("check_staleness: returning cached verdict (stale=%s)", cached["stale"])
        return cached

    try:
        verdict = _check_staleness_impl(conn, watcher_alive=watcher_alive, scan_cap=effective_cap)
    except Exception:  # noqa: BLE001 — never propagate to the read-tool caller
        # Conservative default: stale=False means we don't cry wolf on unexpected errors.
        # Logged so the unexpected error is observable (not silently swallowed).
        logger.debug("check_staleness: unexpected error; returning stale=False", exc_info=True)
        verdict = StalenessVerdict(stale=False, reason="", hint="")

    _cache_put(cache_key, verdict)
    return verdict


def _check_staleness_impl(
    conn: sqlite3.Connection,
    *,
    watcher_alive: bool,
    scan_cap: int,
) -> StalenessVerdict:
    """Inner implementation — called from check_staleness under a try/except."""
    # ── Case 1: watcher is alive ──────────────────────────────────────────────
    if watcher_alive:
        # The watcher self-heals file drift in real time, so file-mtime drift is
        # NOT reported as stale. But synthesized edges / clusters are NEVER
        # recomputed by the watcher — check for those separately.
        if _has_synthesized_edges(conn):
            return StalenessVerdict(
                stale=True,
                reason=(
                    "Synthesized edges and clusters may be stale — "
                    "the file watcher does not recompute them."
                ),
                hint=_HINT_SYNTH_DRIFT,
            )
        # File drift is expected (watcher handles it); nothing else to check.
        return StalenessVerdict(stale=False, reason="", hint="")

    # ── Case 2: no watcher — scan for file drift ──────────────────────────────
    try:
        changed, deleted = _scan_for_drift(conn, scan_cap=scan_cap)
    except Exception:  # noqa: BLE001 — e.g. closed conn; be conservative
        logger.debug("check_staleness: _scan_for_drift failed; defaulting to stale=False", exc_info=True)
        return StalenessVerdict(stale=False, reason="", hint="")

    if deleted > 0 and changed > 0:
        reason = (
            f"{changed} indexed file(s) changed on disk and "
            f"{deleted} tracked file(s) were deleted since last index."
        )
        return StalenessVerdict(stale=True, reason=reason, hint=_HINT_FILE_DRIFT)

    if deleted > 0:
        reason = f"{deleted} tracked file(s) were deleted since last index."
        return StalenessVerdict(stale=True, reason=reason, hint=_HINT_FILE_DRIFT)

    if changed > 0:
        reason = f"{changed} indexed file(s) changed on disk since last index."
        return StalenessVerdict(stale=True, reason=reason, hint=_HINT_FILE_DRIFT)

    # No drift found in the scanned window.
    return StalenessVerdict(stale=False, reason="", hint="")
