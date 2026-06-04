"""Unit tests for P6c — stable symbol UID handle.

The UID is a pure computed string: sha1(file_path)[:8] + ':' + str(start_line).
It is surfaced in seam_search / seam_query results and accepted as an optional
`uid=` alternative to `name` on context / impact / trace handlers — killing the
homonym disambiguation round-trip.

Test groups:
    UID1 — compute_uid produces the documented shape
    UID2 — search/query results carry a `uid` field of the right shape
    UID3 — context/impact/trace accept uid= and resolve to the exact symbol
    UID4 — omitting uid behaves exactly as before (backward compat)
    UID5 — an unknown uid returns the same not-found contract
"""

import hashlib
import re
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import (
    compute_uid,
    handle_seam_context,
    handle_seam_impact,
    handle_seam_query,
    handle_seam_search,
    handle_seam_trace,
)

# UID shape: 8 lowercase hex chars, a colon, then 1+ digits.
_UID_RE = re.compile(r"^[0-9a-f]{8}:\d+$")


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring="A docstring.",
        signature=f"def {name}() -> None",
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=f"module.{name}",
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call",
        file=file, line=1, confidence="INFERRED",
    )


def _make_db(tmp_path: Path):
    """foo calls bar; bar calls baz — single file."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    src = tmp_path / "src.py"
    src.write_text("def foo(): bar()\ndef bar(): baz()\ndef baz(): pass\n")
    upsert_file(
        conn, src, "python", "h1",
        [
            _sym("foo", str(src), line=1),
            _sym("bar", str(src), line=2),
            _sym("baz", str(src), line=3),
        ],
        [
            _edge("foo", "bar", str(src)),
            _edge("bar", "baz", str(src)),
        ],
    )
    return conn, tmp_path, src


def _make_homonym_db(tmp_path: Path):
    """Two files each define a symbol named `helper` at different lines."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def helper(): pass\n")
    b.write_text("\n\ndef helper(): pass\n")
    upsert_file(conn, a, "python", "ha", [_sym("helper", str(a), line=1)], [])
    upsert_file(conn, b, "python", "hb", [_sym("helper", str(b), line=3)], [])
    return conn, tmp_path, a, b


# ── UID1: compute_uid shape ───────────────────────────────────────────────────


class TestComputeUid:
    def test_shape(self) -> None:
        uid = compute_uid("/abs/path/src.py", 42)
        assert _UID_RE.match(uid), f"uid {uid!r} does not match the documented shape"

    def test_matches_sha1_contract(self) -> None:
        path = "/abs/path/src.py"
        line = 42
        expected = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8] + ":" + str(line)
        assert compute_uid(path, line) == expected

    def test_distinct_per_file_for_same_line(self) -> None:
        assert compute_uid("/a.py", 1) != compute_uid("/b.py", 1)


# ── UID2: search/query results carry uid ──────────────────────────────────────


class TestSearchQueryUid:
    def test_search_results_include_uid(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        results = handle_seam_search(conn, "foo", root)
        assert isinstance(results, list) and results
        for r in results:
            assert "uid" in r, "search result missing uid field"
            assert _UID_RE.match(r["uid"]), f"bad uid shape: {r['uid']!r}"

    def test_query_results_include_uid(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        results = handle_seam_query(conn, "foo", root)
        assert isinstance(results, list) and results
        for r in results:
            assert "uid" in r
            assert _UID_RE.match(r["uid"])

    def test_uid_matches_absolute_file_line(self, tmp_path: Path) -> None:
        """The uid must be derivable from the symbol's ABSOLUTE file + start_line,
        even though the returned `file` field is relativized."""
        conn, root, src = _make_db(tmp_path)
        results = handle_seam_search(conn, "bar", root)
        bar = next(r for r in results if r["symbol"] == "bar")
        assert bar["uid"] == compute_uid(str(src), 2)


# ── UID3: uid= resolves to the exact symbol ────────────────────────────────────


class TestUidResolution:
    def test_context_by_uid_resolves_exact_homonym(self, tmp_path: Path) -> None:
        conn, root, a, b = _make_homonym_db(tmp_path)
        # The uid for the b.py `helper` at line 3.
        uid_b = compute_uid(str(b), 3)
        result = handle_seam_context(conn, symbol="", root=root, uid=uid_b)
        assert result is not None and not result.get("error")
        assert result["symbol"] == "helper"
        assert result["line"] == 3
        assert result["file"] == "b.py"

    def test_impact_by_uid(self, tmp_path: Path) -> None:
        conn, root, src = _make_db(tmp_path)
        uid_bar = compute_uid(str(src), 2)
        by_uid = handle_seam_impact(conn, target="", root=root, uid=uid_bar)
        by_name = handle_seam_impact(conn, target="bar", root=root)
        assert by_uid["found"] is True
        assert by_uid["target"] == by_name["target"] == "bar"
        assert by_uid["risk_summary"] == by_name["risk_summary"]

    def test_trace_by_uid(self, tmp_path: Path) -> None:
        conn, root, src = _make_db(tmp_path)
        uid_foo = compute_uid(str(src), 1)
        uid_baz = compute_uid(str(src), 3)
        by_uid = handle_seam_trace(conn, source="", target="", root=root,
                                   uid=uid_foo, target_uid=uid_baz)
        by_name = handle_seam_trace(conn, source="foo", target="baz", root=root)
        assert by_uid["found"] == by_name["found"] is True
        assert by_uid["source"] == "foo"
        assert by_uid["target"] == "baz"


# ── UID4: omitting uid is byte-identical to before ─────────────────────────────


class TestBackwardCompat:
    def test_context_by_name_unchanged(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        result = handle_seam_context(conn, "foo", root)
        assert result is not None
        assert result["symbol"] == "foo"

    def test_impact_by_name_unchanged(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        result = handle_seam_impact(conn, "bar", root)
        assert result["found"] is True
        assert result["target"] == "bar"

    def test_trace_by_name_unchanged(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        result = handle_seam_trace(conn, "foo", "baz", root)
        assert result["found"] is True


# ── UID5: unknown uid → same not-found contract ────────────────────────────────


class TestUnknownUid:
    def test_context_unknown_uid_returns_none(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        # A well-formed but non-matching uid (no symbol at that file/line).
        result = handle_seam_context(conn, symbol="", root=root, uid="deadbeef:999")
        assert result is None  # same not-found contract as an unknown symbol name

    def test_impact_unknown_uid_not_found(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        result = handle_seam_impact(conn, target="", root=root, uid="deadbeef:999")
        assert result["found"] is False

    def test_trace_unknown_uid_not_found(self, tmp_path: Path) -> None:
        conn, root, _src = _make_db(tmp_path)
        result = handle_seam_trace(conn, source="", target="", root=root,
                                   uid="deadbeef:999", target_uid="deadbeef:998")
        assert result["found"] is False
