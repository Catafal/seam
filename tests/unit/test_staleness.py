"""Unit tests for seam/analysis/staleness.py — index staleness detector.

Tests verify EXTERNAL BEHAVIOR only — given a synthetic DB state and on-disk
state, the staleness verdict is correct. No coupling to implementation internals.

Coverage:
  S1  — fresh index (no drift) → stale=False
  S2  — modified file (stored mtime older than disk mtime) → stale=True, file-drift reason
  S3  — deleted tracked file → stale=True
  S4  — scan-cap boundary: stale file just OUTSIDE newest-N cap is NOT detected (documented limitation)
  S5  — watcher_alive=True, no synthesized edges → stale=False (watcher self-heals)
  S6  — watcher_alive=True, WITH synthesized edges → stale=True (synth-edge staleness)
  S7  — IO error path → stale=False, never raises (conservative default)
  S8  — SEAM_STALENESS_CHECK=off → function still returns cheaply (handlers skip calling it)

Prior art: tests/unit/test_steer.py, tests/unit/test_byte_budget.py — pure-leaf unit tests.
"""

import sqlite3
import time
from pathlib import Path

import pytest

from seam.analysis.staleness import check_staleness
from seam.indexer.db import init_db

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_db(db_path: Path) -> sqlite3.Connection:
    """Create a minimal indexed DB."""
    conn = init_db(db_path)
    return conn


def _insert_file_row(
    conn: sqlite3.Connection,
    path: str,
    mtime: float,
    indexed_at: float | None = None,
) -> None:
    """Insert a synthetic files row for testing.

    WHY direct SQL: we need fine-grained control over stored mtime and indexed_at
    values without running the full indexer pipeline.
    """
    if indexed_at is None:
        indexed_at = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO files (path, language, file_hash, mtime, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (path, "python", "abc123deadbeef", mtime, indexed_at),
    )
    conn.commit()


def _insert_synth_edge(conn: sqlite3.Connection) -> None:
    """Insert a synthetic synthesized edge to simulate a synthesis-enabled index."""
    # First ensure the synthesized_by column exists (v12+ schema).
    # The test DB is created by init_db which runs migrations, so it should be present.
    # Insert a files row for the synthetic source first.
    conn.execute(
        "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (":synthesis:", "python", "synth_hash", time.time(), time.time()),
    )
    conn.commit()
    # Get that file's id.
    file_id = conn.execute(
        "SELECT id FROM files WHERE path = ':synthesis:'"
    ).fetchone()[0]

    # Insert a synthesized edge (no symbol needed — edges are name-keyed).
    conn.execute(
        "INSERT INTO edges (source_name, target_name, kind, confidence, file_id, line, synthesized_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "synth_source",
            "synth_target",
            "call",
            "INFERRED",
            file_id,
            1,
            "interface-override",  # non-null = synthesized
        ),
    )
    conn.commit()


# ── S1: fresh index → stale=False ─────────────────────────────────────────────


def test_fresh_index_returns_not_stale(tmp_path: Path) -> None:
    """A fresh index where stored mtime matches on-disk mtime is not stale."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    # Create a real file on disk.
    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    # Store the SAME mtime as on disk → fresh.
    _insert_file_row(conn, str(real_file.resolve()), disk_mtime)

    verdict = check_staleness(conn, root=tmp_path)
    assert verdict["stale"] is False


# ── S2: modified file → stale=True ────────────────────────────────────────────


def test_modified_file_returns_stale(tmp_path: Path) -> None:
    """A file with newer on-disk mtime than stored mtime flips stale=True."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    # Store a PAST mtime — the file appears modified on disk.
    stored_mtime = disk_mtime - 10.0  # 10 seconds older
    _insert_file_row(conn, str(real_file.resolve()), stored_mtime)

    verdict = check_staleness(conn, root=tmp_path)
    assert verdict["stale"] is True
    # Reason should mention file drift.
    assert "modified" in verdict["reason"].lower() or "changed" in verdict["reason"].lower()
    # Hint should mention seam sync.
    assert "sync" in verdict["hint"].lower()


# ── S3: deleted tracked file → stale=True ─────────────────────────────────────


def test_deleted_tracked_file_returns_stale(tmp_path: Path) -> None:
    """A tracked file that no longer exists on disk flips stale=True."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    # Insert a file path that does NOT exist on disk.
    ghost_path = str(tmp_path / "ghost.py")
    conn = _make_db(db_path)
    _insert_file_row(conn, ghost_path, time.time())

    verdict = check_staleness(conn, root=tmp_path)
    assert verdict["stale"] is True
    # Reason should mention deletion.
    assert (
        "deleted" in verdict["reason"].lower()
        or "missing" in verdict["reason"].lower()
        or "removed" in verdict["reason"].lower()
    )
    assert "sync" in verdict["hint"].lower()


# ── S4: scan-cap boundary ─────────────────────────────────────────────────────


def test_scan_cap_misses_stale_file_outside_window(tmp_path: Path) -> None:
    """A stale file that falls OUTSIDE the scan-cap window is NOT detected.

    This is the documented limitation: only the N most-recently-indexed files
    are checked. A stale file that is older than the Nth most recent is missed.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    # Create scan_cap+1 fresh files on disk.
    scan_cap = 5  # use a small cap for testing
    files = []
    for i in range(scan_cap + 1):
        f = tmp_path / f"file_{i}.py"
        f.write_text(f"# file {i}\n")
        files.append(f)

    conn = _make_db(db_path)

    # Index all files with matching mtimes (all fresh).
    # Use indexed_at times spread over 0..scan_cap+1 seconds ago, oldest FIRST.
    now = time.time()
    for i, f in enumerate(files):
        disk_mtime = f.stat().st_mtime
        # The FIRST file gets the OLDEST indexed_at (so it falls OUTSIDE the top-N window).
        indexed_at = now - (scan_cap + 1 - i)
        _insert_file_row(conn, str(f.resolve()), disk_mtime, indexed_at=indexed_at)

    # Now simulate a modification to the OLDEST file (the one outside the cap).
    oldest_file = files[0]
    stored_mtime_for_oldest = oldest_file.stat().st_mtime - 100.0  # stored mtime = older than disk
    # Update its stored mtime to simulate staleness WITHOUT changing indexed_at.
    conn.execute(
        "UPDATE files SET mtime = ? WHERE path = ?",
        (stored_mtime_for_oldest, str(oldest_file.resolve())),
    )
    conn.commit()

    # With our small scan_cap, only the top scan_cap files are checked.
    # The stale file is outside the window → should NOT be detected.
    # The other files are fresh → verdict should be fresh.
    verdict = check_staleness(conn, root=tmp_path, scan_cap=scan_cap)
    # The stale file is OUTSIDE the cap window → not detected.
    assert verdict["stale"] is False


# ── S5: watcher_alive=True, no synthesized edges → stale=False ───────────────


def test_watcher_alive_no_synth_edges_returns_fresh(tmp_path: Path) -> None:
    """When the watcher is alive and there are no synthesized edges, stale=False.

    The watcher self-heals file drift, so file modifications are not reported
    as staleness. Without synthesized edges, nothing else is stale.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    # Store an OLDER mtime — would normally be stale.
    _insert_file_row(conn, str(real_file.resolve()), disk_mtime - 10.0)

    # watcher_alive=True → file drift is NOT reported as stale.
    verdict = check_staleness(conn, root=tmp_path, watcher_alive=True)
    assert verdict["stale"] is False


# ── S6: watcher_alive=True, WITH synthesized edges → stale=True ──────────────


def test_watcher_alive_with_synth_edges_returns_stale(tmp_path: Path) -> None:
    """When the watcher is alive but synthesized edges exist, stale=True.

    Synthesized edges / clusters are NOT recomputed by the watcher — this is the
    higher-stakes staleness case. Even a live watcher leaves synthesis stale.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    _insert_file_row(conn, str(real_file.resolve()), disk_mtime)  # fresh file
    _insert_synth_edge(conn)  # synthesized edges exist

    verdict = check_staleness(conn, root=tmp_path, watcher_alive=True)
    assert verdict["stale"] is True
    # Reason should mention synthesized edges / clusters / watcher.
    reason_lower = verdict["reason"].lower()
    assert (
        "synth" in reason_lower
        or "cluster" in reason_lower
        or "watcher" in reason_lower
    )
    # Hint should mention seam init or seam sync.
    hint_lower = verdict["hint"].lower()
    assert "init" in hint_lower or "sync" in hint_lower


# ── S7: IO error path → stale=False, never raises ────────────────────────────


def test_io_error_returns_conservative_default_never_raises(tmp_path: Path) -> None:
    """On any IO error, check_staleness returns stale=False and never raises.

    WHY prefer stale=False on error: a false-positive stale banner erodes agent
    trust more than a missed stale detection. The function degrades gracefully.
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    conn = _make_db(db_path)
    # Insert a file path that will cause an OSError on stat() — but NOT a missing file
    # (that is the S3 "deleted" case). We pass a closed connection to trigger a DB error.
    conn.close()

    # Calling with a closed connection should NOT raise; should return stale=False.
    # WHY stale=False: do not cry wolf when we cannot determine freshness.
    verdict = check_staleness(conn, root=tmp_path)
    assert verdict["stale"] is False
    # Never raises — the assert above proves it didn't.


# ── S8: SEAM_STALENESS_CHECK=off makes function cheap/safe ───────────────────


def test_check_staleness_is_cheap_when_knob_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When SEAM_STALENESS_CHECK=off, the function returns quickly (handlers skip it).

    This test verifies the module function still returns a valid StalenessVerdict
    even when called with the knob off (for safety; handlers will skip calling it).
    """
    import seam.config as config
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "off")

    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    conn = _make_db(db_path)

    # Even with a modified file, knob off means no IO and no detection.
    # Function should return something reasonable and not raise.
    verdict = check_staleness(conn, root=tmp_path)
    # With knob off, the function should skip stat IO (fast path).
    # The exact stale value is implementation-defined when off — just verify it doesn't raise
    # and returns the right shape.
    assert isinstance(verdict["stale"], bool)
    assert isinstance(verdict["reason"], str)
    assert isinstance(verdict["hint"], str)


# ── S9: respect_knob=False computes freshness even when the knob is off ───────


def test_respect_knob_false_computes_despite_knob_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """respect_knob=False (used by `seam status`) detects staleness even when the
    banner knob is off — the knob gates the MCP banner, NOT the CLI freshness field.

    Regression guard: a prior version short-circuited to stale=False whenever the
    knob was off, which silently disabled `seam status` freshness detection.
    """
    import seam.config as config

    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "off")

    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    _insert_file_row(conn, str(real_file.resolve()), disk_mtime - 10.0)  # modified

    # Banner gate (respect_knob=True, the default) → fresh because knob is off.
    assert check_staleness(conn, root=tmp_path)["stale"] is False
    # CLI path (respect_knob=False) → still detects the drift despite knob off.
    assert check_staleness(conn, root=tmp_path, respect_knob=False)["stale"] is True


# ── S10: cache asymmetry — fresh is NOT cached, stale IS cached ──────────────


def test_fresh_verdict_is_not_cached_so_new_drift_is_not_masked(tmp_path: Path) -> None:
    """A fresh verdict must NOT be cached: an edit within the TTL window must still
    be detected on the next call (the false-safe this feature exists to prevent).
    """
    from seam.analysis.staleness import _cache

    _cache.clear()
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    _insert_file_row(conn, str(real_file.resolve()), disk_mtime)  # fresh

    assert check_staleness(conn, root=tmp_path)["stale"] is False
    # Simulate an edit AFTER the fresh verdict (stored mtime now older than disk).
    conn.execute(
        "UPDATE files SET mtime = ? WHERE path = ?",
        (disk_mtime - 10.0, str(real_file.resolve())),
    )
    conn.commit()
    # The fresh verdict must NOT have been cached → drift detected immediately.
    assert check_staleness(conn, root=tmp_path)["stale"] is True


def test_stale_verdict_is_cached(tmp_path: Path) -> None:
    """A stale verdict IS cached (the safe direction): a known-stale repo is not
    re-scanned on every call within the TTL.
    """
    from seam.analysis.staleness import _cache

    _cache.clear()
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    real_file = tmp_path / "src.py"
    real_file.write_text("def foo(): pass\n")
    disk_mtime = real_file.stat().st_mtime

    conn = _make_db(db_path)
    _insert_file_row(conn, str(real_file.resolve()), disk_mtime - 10.0)  # stale

    assert check_staleness(conn, root=tmp_path)["stale"] is True
    # "Fix" the index (stored mtime now matches disk) but stay within the TTL.
    conn.execute(
        "UPDATE files SET mtime = ? WHERE path = ?",
        (disk_mtime, str(real_file.resolve())),
    )
    conn.commit()
    # The stale verdict was cached → still served as stale within the TTL window.
    assert check_staleness(conn, root=tmp_path)["stale"] is True
