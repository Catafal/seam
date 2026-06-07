"""E1 — seam_impact omits `best_candidate` from entries when it is null.

best_candidate is only meaningful for AMBIGUOUS entries (it is the proximity pick); for
EXTRACTED/INFERRED entries it is always null and carries no signal. Under the default
SEAM_IMPACT_OMIT_NULL_CANDIDATE="on", a null best_candidate key is DROPPED (lossless;
null ≡ absent), keeping the default output lean so more high-signal dependents survive
the per-tier cap. resolved_by is ALWAYS kept (genuine provenance).

Coverage:
  - non-AMBIGUOUS entry (best_candidate null) → key ABSENT; resolved_by present.
  - AMBIGUOUS entry (best_candidate non-null) → key PRESENT with its value.
  - knob "off" → null best_candidate key RESTORED (byte-identical revert).
"""

from pathlib import Path

import pytest

import seam.config as config
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_impact


def _sym(name: str, file: str) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file,
        start_line=1, end_line=5,
        docstring=None, signature=None, decorators=[],
        is_exported=None, visibility=None, qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call",
        file=file, line=1, confidence="INFERRED",
    )


def _unique_db(tmp_path: Path):
    """One target with a unique name → caller resolves EXTRACTED, best_candidate null."""
    conn = init_db(tmp_path / "uniq.db")
    f = tmp_path / "a.py"
    f.write_text("def unique_target(): pass\ndef caller(): unique_target()\n")
    upsert_file(conn, f, "python", "h1",
                [_sym("unique_target", str(f)), _sym("caller", str(f))],
                [_edge("caller", "unique_target", str(f))])
    return conn


def _ambiguous_db(tmp_path: Path):
    """Target name declared in TWO files → caller is AMBIGUOUS, best_candidate non-null."""
    conn = init_db(tmp_path / "amb.db")
    f1 = tmp_path / "pkg" / "a.py"
    f1.parent.mkdir()
    f1.write_text("def dup_target(): pass\n")
    f2 = tmp_path / "other" / "b.py"
    f2.parent.mkdir()
    f2.write_text("def dup_target(): pass\n")
    caller = tmp_path / "pkg" / "caller.py"
    caller.write_text("def caller(): dup_target()\n")
    upsert_file(conn, f1, "python", "h1", [_sym("dup_target", str(f1))], [])
    upsert_file(conn, f2, "python", "h2", [_sym("dup_target", str(f2))], [])
    upsert_file(conn, caller, "python", "h3",
                [_sym("caller", str(caller))],
                [_edge("caller", "dup_target", str(caller))])
    return conn


def _will_break(result: dict) -> list[dict]:
    return result["upstream"]["WILL_BREAK"]


def test_null_best_candidate_omitted_by_default(tmp_path: Path) -> None:
    """Non-AMBIGUOUS entry: best_candidate is null → key omitted; resolved_by kept."""
    conn = _unique_db(tmp_path)
    try:
        result = handle_seam_impact(conn, "unique_target", tmp_path, direction="upstream")
    finally:
        conn.close()

    entries = _will_break(result)
    assert entries, "caller should be in WILL_BREAK"
    for entry in entries:
        assert "best_candidate" not in entry, (
            f"null best_candidate must be omitted by default; got {entry}"
        )
        assert "resolved_by" in entry, "resolved_by must always be present"


def test_non_null_best_candidate_is_kept(tmp_path: Path) -> None:
    """AMBIGUOUS entry: best_candidate is non-null → key PRESENT with its value."""
    conn = _ambiguous_db(tmp_path)
    try:
        result = handle_seam_impact(conn, "dup_target", tmp_path, direction="upstream")
    finally:
        conn.close()

    entries = [e for e in _will_break(result) if e["name"] == "caller"]
    assert entries, "caller should be in WILL_BREAK"
    entry = entries[0]
    assert "best_candidate" in entry, "non-null best_candidate must be kept"
    assert entry["best_candidate"] is not None


def test_knob_off_restores_null_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SEAM_IMPACT_OMIT_NULL_CANDIDATE='off' → null best_candidate key restored."""
    monkeypatch.setattr(config, "SEAM_IMPACT_OMIT_NULL_CANDIDATE", "off")
    conn = _unique_db(tmp_path)
    try:
        result = handle_seam_impact(conn, "unique_target", tmp_path, direction="upstream")
    finally:
        conn.close()

    entries = _will_break(result)
    assert entries
    for entry in entries:
        assert "best_candidate" in entry, "knob off must keep best_candidate (null)"
        assert entry["best_candidate"] is None


def test_lean_mode_unaffected(tmp_path: Path) -> None:
    """Lean mode already strips best_candidate → E1 omission is a no-op there."""
    conn = _unique_db(tmp_path)
    try:
        result = handle_seam_impact(
            conn, "unique_target", tmp_path, direction="upstream", verbose=False
        )
    finally:
        conn.close()

    for entry in _will_break(result):
        assert "best_candidate" not in entry
        # Lean strips resolved_by too (it is a heavy field).
        assert "resolved_by" not in entry
