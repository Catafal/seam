"""Unit tests for seam/query/pack.py — context_pack() orchestration.

Slice 2 of Phase 6. Tests exercise the BEHAVIOR of the bundle through the
public interface, not the internal SQL queries.

Test groups:
    P1 — missing symbol → None (matches context() contract)
    P2 — basic bundle shape: target/callers/callees/why/cluster_peers/truncated
    P3 — callers/callees are enriched with {name,file,line,kind,signature}
    P4 — WHY comments included and capped at SEAM_PACK_MAX_COMMENTS
    P5 — truncated counts correct when lists exceed caps
    P6 — graceful degradation: neighbor name with no symbols row is skipped
    P7 — per-file cap (SEAM_PACK_PER_FILE_CAP) holds before global limit
    P8 — chunked IN() works for name lists larger than _SQLITE_MAX_IN_PARAMS
    P9 — truncated counts ONLY cap drops; unindexed names do NOT bump truncated
    P10 — direct relationship evidence preserves edge metadata
    P11 — caveats and recommended next calls explain evidence limits
"""

from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.query.pack import _enrich_neighbors, context_pack

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(
    name: str,
    file: str,
    kind: str = "function",
    line: int = 1,
    signature: str | None = None,
) -> Symbol:
    """Build a Symbol TypedDict for test seeding."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring=None,
        signature=signature,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=name,
    )


def _edge(source: str, target: str, file: str, kind: str = "call") -> Edge:
    """Build an Edge TypedDict for test seeding."""
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=file,
        line=1,
        confidence="INFERRED",
    )


def _make_db(tmp_path: Path):
    """Create an initialized DB and return an open connection."""
    db_path = tmp_path / "test.db"
    return init_db(db_path)


# ── P1: Missing symbol → None ─────────────────────────────────────────────────


class TestMissingSymbol:
    """context_pack() returns None when the symbol is not in the index."""

    def test_missing_symbol_returns_none(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = context_pack(conn, "nonexistent_symbol")
        conn.close()
        assert result is None

    def test_empty_index_returns_none(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = context_pack(conn, "anything")
        conn.close()
        assert result is None


# ── P2: Basic bundle shape ─────────────────────────────────────────────────────


class TestBundleShape:
    """context_pack() returns a ContextPack with the required top-level keys."""

    def test_bundle_has_required_keys(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): bar()\ndef bar(): pass\n")
        upsert_file(
            conn, f, "python", "h1",
            [_sym("foo", str(f)), _sym("bar", str(f))],
            [_edge("foo", "bar", str(f))],
        )

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        # Required top-level keys from PRD
        assert "target" in result
        assert "callers" in result
        assert "callees" in result
        assert "why" in result
        assert "cluster_peers" in result
        assert "truncated" in result
        assert "relationship_evidence" in result
        assert "caveats" in result
        assert "recommended_next_calls" in result

    def test_target_is_full_context_result(self, tmp_path: Path) -> None:
        """target carries the full ContextResult fields (same as engine.context())."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): pass\n")
        upsert_file(conn, f, "python", "h1", [_sym("foo", str(f))], [])

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        target = result["target"]
        # engine.context() returns ContextResult; we verify key fields
        assert target["symbol"] == "foo"
        assert target["kind"] == "function"
        assert "callers" in target  # bare name lists from context()
        assert "callees" in target
        assert "ambiguous" in target

    def test_truncated_has_three_counters(self, tmp_path: Path) -> None:
        """truncated dict has callers, callees, comments keys."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): pass\n")
        upsert_file(conn, f, "python", "h1", [_sym("foo", str(f))], [])

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        trunc = result["truncated"]
        assert "callers" in trunc
        assert "callees" in trunc
        assert "comments" in trunc
        assert isinstance(trunc["callers"], int)
        assert isinstance(trunc["callees"], int)
        assert isinstance(trunc["comments"], int)

    def test_truncated_zero_when_no_caps_hit(self, tmp_path: Path) -> None:
        """truncated counts are 0 when lists are below the caps."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): bar()\ndef bar(): pass\n")
        upsert_file(
            conn, f, "python", "h1",
            [_sym("foo", str(f)), _sym("bar", str(f))],
            [_edge("foo", "bar", str(f))],
        )

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        trunc = result["truncated"]
        assert trunc["callers"] == 0
        assert trunc["callees"] == 0
        assert trunc["comments"] == 0


# ── P3: Callers/callees enrichment ────────────────────────────────────────────


class TestNeighborEnrichment:
    """Callers and callees are enriched with {name,file,line,kind,signature}."""

    def test_callee_is_enriched(self, tmp_path: Path) -> None:
        """callees list contains NeighborRef dicts, not bare strings."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): bar()\ndef bar(): pass\n")
        upsert_file(
            conn, f, "python", "h1",
            [_sym("foo", str(f), line=1), _sym("bar", str(f), line=2)],
            [_edge("foo", "bar", str(f))],
        )

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        callees = result["callees"]
        assert len(callees) == 1
        nb = callees[0]
        # NeighborRef shape
        assert nb["name"] == "bar"
        assert nb["file"] == str(f)
        assert isinstance(nb["line"], int)
        assert nb["kind"] == "function"
        # signature is nullable (None for pre-v5 rows) — field must be present
        assert "signature" in nb

    def test_caller_is_enriched(self, tmp_path: Path) -> None:
        """callers list contains NeighborRef dicts."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def caller(): target()\ndef target(): pass\n")
        upsert_file(
            conn, f, "python", "h1",
            [_sym("caller", str(f), line=1), _sym("target", str(f), line=2)],
            [_edge("caller", "target", str(f))],
        )

        result = context_pack(conn, "target")
        conn.close()

        assert result is not None
        callers = result["callers"]
        assert len(callers) == 1
        nb = callers[0]
        assert nb["name"] == "caller"
        assert "file" in nb
        assert "line" in nb
        assert "kind" in nb
        assert "signature" in nb

    def test_neighbor_with_no_symbol_row_is_skipped(self, tmp_path: Path) -> None:
        """An edge to a name not in the symbols table is skipped gracefully."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): unknown_extern()\n")
        # Only 'foo' is indexed; 'unknown_extern' is referenced but not declared
        upsert_file(
            conn, f, "python", "h1",
            [_sym("foo", str(f))],
            [_edge("foo", "unknown_extern", str(f))],
        )

        # Should not raise — graceful degradation
        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        # unknown_extern has no symbols row — it is silently skipped
        callee_names = [nb["name"] for nb in result["callees"]]
        assert "unknown_extern" not in callee_names

    def test_signature_in_neighbor_when_present(self, tmp_path: Path) -> None:
        """NeighborRef carries the signature when the index has it."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): bar()\ndef bar(x: int) -> str: pass\n")
        upsert_file(
            conn, f, "python", "h1",
            [
                _sym("foo", str(f), line=1),
                _sym("bar", str(f), line=2, signature="def bar(x: int) -> str"),
            ],
            [_edge("foo", "bar", str(f))],
        )

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        callee = result["callees"][0]
        assert callee["signature"] == "def bar(x: int) -> str"


# ── P4: WHY comments included ─────────────────────────────────────────────────


class TestWhyComments:
    """WHY comments from comments.why() are included in the bundle."""

    def test_why_is_list(self, tmp_path: Path) -> None:
        """why is always a list (empty when no comments)."""
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): pass\n")
        upsert_file(conn, f, "python", "h1", [_sym("foo", str(f))], [])

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        assert isinstance(result["why"], list)


# ── P5: Truncated counts ──────────────────────────────────────────────────────


class TestTruncatedCounts:
    """truncated counts correctly report entries dropped by caps."""

    def test_callees_truncated_when_exceed_limit(self, tmp_path: Path) -> None:
        """When callees exceed caps, truncated.callees equals total dropped entries.

        Place all callees in different files to isolate the global-limit cap
        (per-file cap would not fire since each file contributes only 1 entry).
        """
        import seam.config as config

        conn = _make_db(tmp_path)
        # n = limit + 3, each callee in its own file so PER_FILE_CAP does not fire.
        n = config.SEAM_PACK_NEIGHBOR_LIMIT + 3
        main_file = tmp_path / "main.py"
        main_file.write_text("# main\n")
        main_syms = [_sym("foo", str(main_file), line=1)]
        main_edges = []
        for i in range(n):
            callee_file = tmp_path / f"callee_{i}.py"
            callee_file.write_text("# callee\n")
            callee_name = f"helper_{i}"
            upsert_file(
                conn, callee_file, "python", f"h_{i}",
                [_sym(callee_name, str(callee_file), line=1)], [],
            )
            main_edges.append(_edge("foo", callee_name, str(main_file)))
        upsert_file(conn, main_file, "python", "h_main", main_syms, main_edges)

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        assert len(result["callees"]) == config.SEAM_PACK_NEIGHBOR_LIMIT
        # exactly 3 dropped by the global limit
        assert result["truncated"]["callees"] == 3

    def test_callers_truncated_when_exceed_limit(self, tmp_path: Path) -> None:
        """When callers exceed global limit, truncated.callers == dropped count.

        Place each caller in its own file so only the global cap fires.
        """
        import seam.config as config

        conn = _make_db(tmp_path)
        n = config.SEAM_PACK_NEIGHBOR_LIMIT + 2
        target_file = tmp_path / "target.py"
        target_file.write_text("# target\n")
        upsert_file(conn, target_file, "python", "h_tgt",
                    [_sym("target", str(target_file), line=1)], [])
        for i in range(n):
            caller_file = tmp_path / f"caller_{i}.py"
            caller_file.write_text("# caller\n")
            caller_name = f"user_{i}"
            upsert_file(
                conn, caller_file, "python", f"h_{i}",
                [_sym(caller_name, str(caller_file), line=1)],
                [_edge(caller_name, "target", str(caller_file))],
            )

        result = context_pack(conn, "target")
        conn.close()

        assert result is not None
        assert len(result["callers"]) == config.SEAM_PACK_NEIGHBOR_LIMIT
        assert result["truncated"]["callers"] == 2


# ── P7: Per-file cap ──────────────────────────────────────────────────────────


class TestPerFileCap:
    """SEAM_PACK_PER_FILE_CAP limits how many neighbors come from any single file."""

    def test_per_file_cap_applied_before_global_limit(self, tmp_path: Path) -> None:
        """Neighbors from a single file are capped at SEAM_PACK_PER_FILE_CAP."""
        import seam.config as config

        conn = _make_db(tmp_path)
        hot_file = tmp_path / "hot.py"
        cold_file = tmp_path / "cold.py"
        main_file = tmp_path / "main.py"

        # hot.py defines more symbols than PER_FILE_CAP
        n_hot = config.SEAM_PACK_PER_FILE_CAP + 2
        hot_syms = [_sym(f"hot_{i}", str(hot_file), line=1 + i) for i in range(n_hot)]
        hot_file.write_text("# hot\n")
        upsert_file(conn, hot_file, "python", "h_hot", hot_syms, [])

        # cold.py defines 1 symbol
        cold_syms = [_sym("cold_1", str(cold_file), line=1)]
        cold_file.write_text("# cold\n")
        upsert_file(conn, cold_file, "python", "h_cold", cold_syms, [])

        # main.py defines foo + edges to all hot + cold symbols
        main_syms = [_sym("foo", str(main_file), line=1)]
        main_edges = [_edge("foo", f"hot_{i}", str(main_file)) for i in range(n_hot)]
        main_edges.append(_edge("foo", "cold_1", str(main_file)))
        main_file.write_text("# main\n")
        upsert_file(conn, main_file, "python", "h_main", main_syms, main_edges)

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        # Count how many callees come from hot_file
        hot_callees = [nb for nb in result["callees"] if nb["file"] == str(hot_file)]
        assert len(hot_callees) <= config.SEAM_PACK_PER_FILE_CAP

        # cold_1 should still be present (different file)
        callee_names = [nb["name"] for nb in result["callees"]]
        assert "cold_1" in callee_names


# ── P8: Chunked IN() works for large name lists ───────────────────────────────


class TestChunkedInQuery:
    """_enrich_neighbors works when unique_names > _SQLITE_MAX_IN_PARAMS.

    We monkeypatch the constant to a tiny value (3) so the test is fast and
    deterministic without needing 900+ real symbols in the DB.
    """

    def test_enrichment_over_chunk_boundary_returns_all_symbols(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All indexed symbols are found even when the name list spans two chunks.

        Monkeypatches _SQLITE_MAX_IN_PARAMS = 3 so 5 names cross the boundary.
        """
        import seam.query.pack as pack_module

        # Force a tiny chunk size so 5 names require 2 rounds of IN(...)
        monkeypatch.setattr(pack_module, "_SQLITE_MAX_IN_PARAMS", 3)

        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("# symbols\n")
        names = [f"sym_{i}" for i in range(5)]
        syms = [_sym(n, str(f), line=1 + i) for i, n in enumerate(names)]
        upsert_file(conn, f, "python", "h1", syms, [])

        # All 5 names are indexed — enrichment must return 5 entries
        refs, truncated = _enrich_neighbors(
            conn,
            names,
            neighbor_limit=100,
            per_file_cap=100,
        )
        conn.close()

        returned_names = {r["name"] for r in refs}
        assert returned_names == set(names), (
            f"Expected all 5 names; got {returned_names}"
        )
        assert truncated == 0


# ── P9: Unindexed neighbors do NOT bump truncated ────────────────────────────


class TestTruncatedUnindexedNotCounted:
    """truncated counts ONLY cap drops; unindexed names are silently skipped."""

    def test_unindexed_callee_does_not_increment_truncated(
        self, tmp_path: Path
    ) -> None:
        """An edge to an unindexed name must NOT inflate truncated.callees.

        If it did, an agent would run seam_impact on a phantom symbol. A higher
        cap never retrieves the unindexed name, so it must not be counted.
        """
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): indexed_callee(); extern_callee()\n")
        # Only foo and indexed_callee are in the index; extern_callee is NOT.
        upsert_file(
            conn, f, "python", "h1",
            [_sym("foo", str(f), line=1), _sym("indexed_callee", str(f), line=2)],
            [
                _edge("foo", "indexed_callee", str(f)),
                _edge("foo", "extern_callee", str(f)),
            ],
        )

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        # indexed_callee is present; extern_callee is skipped (not indexed)
        callee_names = [nb["name"] for nb in result["callees"]]
        assert "indexed_callee" in callee_names
        assert "extern_callee" not in callee_names

        # The unindexed skip must NOT count as truncated
        assert result["truncated"]["callees"] == 0, (
            "truncated.callees must be 0 because no cap was hit; "
            "the unindexed skip is not a cap drop"
        )


# ── P10: Direct relationship evidence ────────────────────────────────────────


class TestRelationshipEvidence:
    """context_pack() surfaces direct edge evidence for caller/callee claims."""

    def test_relationship_evidence_includes_direct_call_metadata(
        self, tmp_path: Path
    ) -> None:
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def caller(): target()\ndef target(): helper()\ndef helper(): pass\n")
        upsert_file(
            conn, f, "python", "h1",
            [
                _sym("caller", str(f), line=1),
                _sym("target", str(f), line=2),
                _sym("helper", str(f), line=3),
            ],
            [
                Edge(
                    source="caller",
                    target="target",
                    kind="call",
                    file=str(f),
                    line=1,
                    confidence="EXTRACTED",
                    receiver=None,
                    provenance="python-call",
                ),
                Edge(
                    source="target",
                    target="helper",
                    kind="call",
                    file=str(f),
                    line=2,
                    confidence="EXTRACTED",
                    receiver=None,
                    provenance="python-call",
                ),
            ],
        )

        result = context_pack(conn, "target")
        conn.close()

        assert result is not None
        evidence = result["relationship_evidence"]
        assert evidence["truncated"] == {"callers": 0, "callees": 0}

        caller_edge = evidence["callers"][0]
        assert caller_edge == {
            "source": "caller",
            "target": "target",
            "direction": "incoming",
            "kind": "call",
            "file": str(f),
            "line": 1,
            "confidence": "EXTRACTED",
            "receiver": None,
            "synthesized_by": None,
            "provenance": "python-call",
        }

        callee_edge = evidence["callees"][0]
        assert callee_edge["source"] == "target"
        assert callee_edge["target"] == "helper"
        assert callee_edge["direction"] == "outgoing"
        assert callee_edge["kind"] == "call"


# ── P11: Caveats and recommended next calls ──────────────────────────────────


class TestEvidenceGuidance:
    """context_pack() explains static evidence limits and next useful calls."""

    def test_static_caveat_and_snippet_recommendation_are_always_present(
        self, tmp_path: Path
    ) -> None:
        conn = _make_db(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo(): pass\n")
        upsert_file(conn, f, "python", "h1", [_sym("foo", str(f))], [])

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        assert any("static" in caveat.lower() for caveat in result["caveats"])
        assert result["recommended_next_calls"][0] == {
            "tool": "seam_snippet",
            "reason": "Read bounded source for the selected symbol before editing.",
            "params": {"symbol": "foo"},
        }

    def test_truncated_evidence_recommends_impact(self, tmp_path: Path) -> None:
        import seam.config as config

        conn = _make_db(tmp_path)
        target_file = tmp_path / "target.py"
        target_file.write_text("# target\n")
        upsert_file(conn, target_file, "python", "h_tgt",
                    [_sym("target", str(target_file), line=1)], [])
        for i in range(config.SEAM_PACK_NEIGHBOR_LIMIT + 1):
            caller_file = tmp_path / f"caller_{i}.py"
            caller_file.write_text("# caller\n")
            caller_name = f"user_{i}"
            upsert_file(
                conn, caller_file, "python", f"h_{i}",
                [_sym(caller_name, str(caller_file), line=1)],
                [_edge(caller_name, "target", str(caller_file))],
            )

        result = context_pack(conn, "target")
        conn.close()

        assert result is not None
        assert result["relationship_evidence"]["truncated"]["callers"] > 0
        assert any("truncated" in caveat.lower() for caveat in result["caveats"])
        assert any(
            call["tool"] == "seam_impact" and call["params"] == {"symbol": "target"}
            for call in result["recommended_next_calls"]
        )
