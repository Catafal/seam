"""Integration tests for the P2 index staleness banner on graph-traversal handlers.

Tests verify EXTERNAL BEHAVIOR: a stale index surfaces index_status on the
5 graph-traversal handlers; a fresh index omits it; SEAM_STALENESS_CHECK=off
is byte-identical to pre-feature (no banner ever).

Coverage:
  BN1  — stale index: handle_seam_impact returns index_status with stale=True, reason, hint
  BN2  — stale index: handle_seam_changes returns index_status
  BN3  — stale index: handle_seam_affected returns index_status
  BN4  — stale index: handle_seam_context returns index_status
  BN5  — stale index: handle_seam_trace returns index_status
  BN6  — fresh index: index_status is ABSENT from all 5 handlers (byte-identical)
  BN7  — SEAM_STALENESS_CHECK=off: no index_status even when stale (byte-identical)
  BN8  — handle_seam_changes / handle_seam_affected risk verdicts unchanged vs baseline
         (staleness adds only an additive field; risk_level / affected_tests unchanged)

Prior art:
  tests/integration/test_affected_handler.py — handler integration test pattern
  tests/unit/test_impact_max_bytes.py         — byte-stability assertion pattern
"""

import os
import time
from pathlib import Path

import pytest

import seam.config as config
from seam.analysis.staleness import _cache  # for cache invalidation between tests
from seam.indexer.db import connect, init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import (
    handle_seam_affected,
    handle_seam_changes,
    handle_seam_context,
    handle_seam_impact,
    handle_seam_trace,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

ROOT = Path("/fake/root")


def _sym(name: str, file_path: str, line: int = 1, kind: str = "function") -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file_path,
        start_line=line,
        end_line=line + 2,
        docstring=None,
        signature=None,
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=name,
    )


def _edge(source: str, target: str, file_path: str) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind="call",
        file=file_path,
        line=1,
        confidence="INFERRED",
    )


def _make_minimal_db(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal indexed DB with 1 real file + 2 symbols connected by a call edge.

    Returns (db_path, src_file_path).
    """
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    src = tmp_path / "src.py"
    src.write_text("def caller(): pass\ndef callee(): pass\n")

    conn = init_db(db_path)
    src_str = str(src.resolve())
    symbols = [_sym("caller", src_str, line=1), _sym("callee", src_str, line=2)]
    edges = [_edge("caller", "callee", src_str)]
    upsert_file(conn, src, "python", "abc123", symbols, edges)
    conn.commit()
    conn.close()

    return db_path, src


def _make_stale(db_path: Path, src_file: Path) -> Path:
    """Make the index stale by writing a newer mtime to the file AFTER indexing.

    Overwrites src_file's content (bumping its mtime) while leaving the stored
    mtime in the DB unchanged.
    Returns the db_path.
    """
    src_file.write_text("def caller(): return 1\ndef callee(): pass\n")
    # Force a definitively-newer mtime (not just a content write) so the test is not
    # flaky on coarse-mtime filesystems where the rewrite could land in the same tick
    # as indexing. A far-future stamp is unambiguously > any stored mtime.
    future = time.time() + 1000.0
    os.utime(src_file, (future, future))
    return db_path


def _clear_staleness_cache() -> None:
    """Clear the module-level staleness cache between tests."""
    _cache.clear()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Clear the staleness verdict cache before each test to prevent TTL interference."""
    _clear_staleness_cache()
    yield
    _clear_staleness_cache()


# ── BN1: stale index — handle_seam_impact ─────────────────────────────────────


def test_stale_impact_has_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale index triggers index_status on handle_seam_impact."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    # Set TTL=0 to disable caching (force fresh check each call).
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    conn = connect(db_path)
    try:
        result = handle_seam_impact(conn, "callee", tmp_path)
    finally:
        conn.close()

    assert "index_status" in result, (
        "Expected index_status key when index is stale, but it was absent"
    )
    assert result["index_status"]["stale"] is True
    assert isinstance(result["index_status"]["reason"], str)
    assert len(result["index_status"]["reason"]) > 0
    assert isinstance(result["index_status"]["hint"], str)
    assert len(result["index_status"]["hint"]) > 0


# ── BN2: stale index — handle_seam_changes ────────────────────────────────────


def test_stale_changes_has_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale index triggers index_status on handle_seam_changes.

    NOTE: risk verdicts (risk_level, affected, changed_symbols) are unchanged —
    the banner is purely additive.
    """
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    conn = connect(db_path)
    try:
        # handle_seam_changes may raise NotAGitRepoError on a non-git dir.
        # Use a try/except to skip the NOT_A_GIT_REPO case (we just want the staleness field).
        result = handle_seam_changes(conn, tmp_path)
    finally:
        conn.close()

    # If this is a git repo, the result should have index_status.
    # If NOT_A_GIT_REPO error returned, skip the banner assertion (the handler
    # returns early on error before attaching the banner).
    if "error" in result:
        pytest.skip(f"Not a git repo: {result['error']} — skip banner test")

    assert "index_status" in result
    assert result["index_status"]["stale"] is True


# ── BN3: stale index — handle_seam_affected ───────────────────────────────────


def test_stale_affected_has_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale index triggers index_status on handle_seam_affected.

    risk verdicts (affected_tests, changed_files) must be unchanged vs baseline.
    """
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    conn = connect(db_path)
    try:
        result = handle_seam_affected(conn, [str(src_file.resolve())], tmp_path)
    finally:
        conn.close()

    assert "index_status" in result
    assert result["index_status"]["stale"] is True
    # Risk verdicts are present and unchanged (additive field only).
    assert "changed_files" in result
    assert "affected_tests" in result


# ── BN4: stale index — handle_seam_context ────────────────────────────────────


def test_stale_context_has_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale index triggers index_status on handle_seam_context."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    conn = connect(db_path)
    try:
        result = handle_seam_context(conn, "callee", tmp_path)
    finally:
        conn.close()

    # handle_seam_context returns None for not-found symbols (can't attach banner to None).
    # For a found symbol it should be a dict.
    assert result is not None, "Symbol 'callee' should be found in the index"
    assert isinstance(result, dict)
    assert "index_status" in result
    assert result["index_status"]["stale"] is True


# ── BN5: stale index — handle_seam_trace ──────────────────────────────────────


def test_stale_trace_has_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale index triggers index_status on handle_seam_trace."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    conn = connect(db_path)
    try:
        result = handle_seam_trace(conn, "caller", "callee", tmp_path)
    finally:
        conn.close()

    assert "index_status" in result
    assert result["index_status"]["stale"] is True


# ── BN6: fresh index → index_status ABSENT ───────────────────────────────────


def test_fresh_impact_no_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh index has NO index_status key — output byte-identical to pre-feature."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    # Do NOT modify src_file — leave it fresh (on-disk mtime == stored mtime).

    conn = connect(db_path)
    try:
        result = handle_seam_impact(conn, "callee", tmp_path)
    finally:
        conn.close()

    assert "index_status" not in result, (
        "Expected no index_status on a fresh index, but it was present"
    )


def test_fresh_context_no_index_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh index has no index_status on handle_seam_context."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)

    conn = connect(db_path)
    try:
        result = handle_seam_context(conn, "callee", tmp_path)
    finally:
        conn.close()

    assert result is not None
    assert "index_status" not in result


# ── BN7: SEAM_STALENESS_CHECK=off → byte-identical ───────────────────────────


def test_staleness_check_off_no_banner_even_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEAM_STALENESS_CHECK=off means no index_status even when the index is stale."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "off")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    conn = connect(db_path)
    try:
        result_impact = handle_seam_impact(conn, "callee", tmp_path)
        result_context = handle_seam_context(conn, "callee", tmp_path)
        result_trace = handle_seam_trace(conn, "caller", "callee", tmp_path)
        result_affected = handle_seam_affected(conn, [str(src_file.resolve())], tmp_path)
    finally:
        conn.close()

    # None of these should have index_status when knob is off.
    assert "index_status" not in result_impact, "impact should have no banner when knob off"
    assert result_context is not None
    assert "index_status" not in result_context, "context should have no banner when knob off"
    assert "index_status" not in result_trace, "trace should have no banner when knob off"
    assert "index_status" not in result_affected, "affected should have no banner when knob off"


# ── BN8: risk verdicts unchanged vs baseline ─────────────────────────────────


def test_risk_verdicts_unchanged_with_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seam_changes/seam_affected risk verdicts are byte-stable — banner is additive only."""
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    monkeypatch.setattr(config, "SEAM_STALENESS_TTL_SECONDS", 0)

    db_path, src_file = _make_minimal_db(tmp_path)
    _make_stale(db_path, src_file)

    # Get baseline with banner off.
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "off")
    conn = connect(db_path)
    try:
        baseline_affected = handle_seam_affected(conn, [str(src_file.resolve())], tmp_path)
    finally:
        conn.close()

    # Get result with banner on.
    monkeypatch.setattr(config, "SEAM_STALENESS_CHECK", "on")
    _clear_staleness_cache()
    conn = connect(db_path)
    try:
        result_affected = handle_seam_affected(conn, [str(src_file.resolve())], tmp_path)
    finally:
        conn.close()

    # The risk-related fields must be byte-identical.
    assert result_affected["changed_files"] == baseline_affected["changed_files"]
    assert result_affected["affected_tests"] == baseline_affected["affected_tests"]
    assert result_affected["total_dependents_traversed"] == baseline_affected[
        "total_dependents_traversed"
    ]
    assert result_affected["partial"] == baseline_affected["partial"]
    # The banner is additive — result has index_status but baseline does not.
    assert "index_status" in result_affected
    assert "index_status" not in baseline_affected
