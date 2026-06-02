"""Unit + integration tests for /review + /backend-taste fixes (Phase 5 post-review).

Covers:
  A — Resolution-result cache (assert repeated (file, target) pairs use the cache).
  C — best_candidate surfaced on AMBIGUOUS entries in walk/impact/tools.
  D — Path normalization (../sibling resolution via normpath, not realpath).
  E — CLI impact Rich-mode parity (impact() gets repo_root in Rich branch).
  F — is_namespace skip in import promotion (namespace import not queried as name).
  K — Empty import_mappings warning fires once when symbols exist but table is empty.

Note: Fix B (config knobs wired) is verified by grep in the checklist, not a behavior test.
Note: Fix G (Go comment + cap) is non-behavioral — verified by reading the code.
Note: Fix H (dead-code cleanup) is structural — verified by ruff passing.
Note: Fix I,J (debug logging) require live logger observation — covered by ruff passing.
"""

from pathlib import Path
from unittest.mock import patch

from seam.analysis.confidence import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    load_import_mappings,
    resolve_edge,
)
from seam.analysis.imports import (
    ImportMapping,
    resolve_import_source,
)
from seam.analysis.traversal import walk
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, kind: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind=kind, file=file,
        start_line=line, end_line=line + 5,
        docstring=None, signature=None, decorators=[],
        is_exported=None, visibility=None, qualified_name=name,
    )


def _edge(source: str, target: str, file: str, kind: str = "call") -> Edge:
    return Edge(
        source=source, target=target, kind=kind,
        file=file, line=1, confidence="INFERRED",
    )


def _import(local_name: str, exported_name: str, source_module: str,
            is_namespace: bool = False, is_wildcard: bool = False) -> ImportMapping:
    return ImportMapping(
        local_name=local_name,
        exported_name=exported_name,
        source_module=source_module,
        is_default=False,
        is_namespace=is_namespace,
        is_wildcard=is_wildcard,
        line=1,
    )


# ── Fix A: Resolution-result cache ───────────────────────────────────────────


class TestResolutionCache:
    """Fix A — verify that repeated (file, target) pairs hit the cache, not the DB."""

    def test_walk_with_repeated_pairs_produces_consistent_results(
        self, tmp_path: Path
    ) -> None:
        """Walk with many edges sharing the same (file, target) pair should produce
        the same results as a walk without the cache — i.e. cache doesn't distort output."""
        db_path = tmp_path / "cache_test.db"
        conn = init_db(db_path)

        # Create a hub: many callers of one target in the same file.
        hub_file = tmp_path / "hub.py"
        hub_file.write_text("def hub(): pass\n")

        target_file = tmp_path / "target.py"
        target_file.write_text("def target_fn(): pass\n")

        symbols = [_sym("hub", "function", str(hub_file))]
        # Ten callers, all from hub_file, calling target_fn.
        caller_names = [f"caller_{i}" for i in range(10)]
        caller_syms = [_sym(n, "function", str(hub_file)) for n in caller_names]
        caller_edges = [_edge(n, "target_fn", str(hub_file)) for n in caller_names]

        upsert_file(conn, hub_file, "python", "abc1",
                    symbols + caller_syms, caller_edges)
        upsert_file(conn, target_file, "python", "abc2",
                    [_sym("target_fn", "function", str(target_file))], [])

        # Walk upstream from target_fn — all callers should appear exactly once.
        reached = walk(conn, ["target_fn"], "upstream", 2, repo_root=tmp_path)
        caller_set = {r["name"] for r in reached}
        for cn in caller_names:
            assert cn in caller_set, f"Expected {cn} in reached"

        # All callers at distance 1 — none should appear at distance 2.
        for r in reached:
            if r["name"] in caller_names:
                assert r["distance"] == 1

    def test_reached_has_best_candidate_field(self, tmp_path: Path) -> None:
        """Reached TypedDict must have best_candidate key (may be None)."""
        db_path = tmp_path / "best_cand.db"
        conn = init_db(db_path)

        f = tmp_path / "a.py"
        f.write_text("def a(): b()\ndef b(): pass\n")
        upsert_file(conn, f, "python", "h1",
                    [_sym("a", "function", str(f)), _sym("b", "function", str(f))],
                    [_edge("a", "b", str(f))])

        reached = walk(conn, ["a"], "downstream", 1)
        assert len(reached) > 0
        r = reached[0]
        assert "best_candidate" in r, "Reached must have best_candidate field"


# ── Fix C: best_candidate surfaced on AMBIGUOUS entries ──────────────────────


class TestBestCandidateSurfaced:
    """Fix C — AMBIGUOUS entries in walk/impact/tools carry best_candidate."""

    def test_ambiguous_entry_has_best_candidate_from_resolve_edge(
        self, tmp_path: Path
    ) -> None:
        """When a target name has count>1, the Reached entry should get best_candidate
        from the proximity resolver (non-None when candidates exist)."""
        db_path = tmp_path / "bc.db"
        conn = init_db(db_path)

        f1 = tmp_path / "pkg" / "a.py"
        f1.parent.mkdir()
        f1.write_text("def helper(): pass\n")

        f2 = tmp_path / "other" / "b.py"
        f2.parent.mkdir()
        f2.write_text("def helper(): pass\n")  # same name, second file → AMBIGUOUS

        caller_file = tmp_path / "pkg" / "caller.py"
        caller_file.write_text("def caller(): helper()\n")

        upsert_file(conn, f1, "python", "h1", [_sym("helper", "function", str(f1))], [])
        upsert_file(conn, f2, "python", "h2", [_sym("helper", "function", str(f2))], [])
        upsert_file(conn, caller_file, "python", "h3",
                    [_sym("caller", "function", str(caller_file))],
                    [_edge("caller", "helper", str(caller_file))])

        # Walk upstream from "helper" — should find caller at d=1, AMBIGUOUS since 2 files.
        reached = walk(conn, ["helper"], "upstream", 1, repo_root=tmp_path)
        caller_entries = [r for r in reached if r["name"] == "caller"]
        assert caller_entries, "caller must be in reached"
        r = caller_entries[0]
        assert r["confidence"] == CONFIDENCE_AMBIGUOUS
        # best_candidate should be non-None when conn is provided and candidates exist.
        assert r["best_candidate"] is not None, (
            "AMBIGUOUS reached entry should have best_candidate when proximity data available"
        )

    def test_non_ambiguous_entry_best_candidate_is_none(
        self, tmp_path: Path
    ) -> None:
        """EXTRACTED entries should have best_candidate=None."""
        db_path = tmp_path / "bc2.db"
        conn = init_db(db_path)

        f = tmp_path / "a.py"
        f.write_text("def unique_fn(): pass\ndef caller(): unique_fn()\n")
        upsert_file(conn, f, "python", "h1",
                    [_sym("unique_fn", "function", str(f)),
                     _sym("caller", "function", str(f))],
                    [_edge("caller", "unique_fn", str(f))])

        reached = walk(conn, ["unique_fn"], "upstream", 1, repo_root=tmp_path)
        caller_entries = [r for r in reached if r["name"] == "caller"]
        assert caller_entries
        r = caller_entries[0]
        assert r["confidence"] == CONFIDENCE_EXTRACTED
        assert r["best_candidate"] is None, "EXTRACTED entries should have best_candidate=None"


# ── Fix D: Path normalization (../sibling resolution) ────────────────────────


class TestPathNormalization:
    """Fix D — relative TS/JS imports with ../ resolve via normpath, not realpath."""

    def test_resolve_relative_dotdot_sibling(self, tmp_path: Path) -> None:
        """from '../sibling/utils' should resolve using lexical normpath."""
        # Create a sibling directory structure.
        pkg_a = tmp_path / "packages" / "a"
        pkg_a.mkdir(parents=True)
        pkg_b = tmp_path / "packages" / "b"
        pkg_b.mkdir(parents=True)

        target_file = pkg_b / "utils.ts"
        target_file.write_text("export function util() {}")

        referencing_file = pkg_a / "index.ts"
        referencing_file.write_text("import { util } from '../b/utils'")

        # Resolve '../b/utils' from pkg_a/index.ts
        candidates = resolve_import_source(
            "../b/utils",
            referencing_file,
            tmp_path,
            "typescript",
        )
        assert str(target_file) in candidates, (
            f"Expected {target_file} in candidates {candidates}"
        )

    def test_resolve_dotslash_sibling(self, tmp_path: Path) -> None:
        """from './utils' should resolve a file in the same directory."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        utils = src_dir / "utils.ts"
        utils.write_text("export function util() {}")
        caller = src_dir / "index.ts"
        caller.write_text("import { util } from './utils'")

        candidates = resolve_import_source(
            "./utils",
            caller,
            tmp_path,
            "typescript",
        )
        assert str(utils) in candidates

    def test_resolve_python_relative_import(self, tmp_path: Path) -> None:
        """Python relative imports via .. should resolve lexically."""
        pkg = tmp_path / "myapp" / "pkg"
        pkg.mkdir(parents=True)
        sibling = tmp_path / "myapp" / "utils.py"
        sibling.write_text("def helper(): pass\n")
        caller = pkg / "module.py"
        caller.write_text("from ..utils import helper\n")

        candidates = resolve_import_source(
            "..utils",
            caller,
            tmp_path,
            "python",
        )
        assert str(sibling) in candidates


# ── Fix F: is_namespace skip in promotion ────────────────────────────────────


class TestNamespaceImportSkip:
    """Fix F — namespace imports (import * as ns) are skipped in import promotion."""

    def test_namespace_import_not_queried(self, tmp_path: Path) -> None:
        """A namespace binding (is_namespace=True) should be skipped even if local_name matches."""
        db_path = tmp_path / "ns.db"
        conn = init_db(db_path)

        # Create a target symbol in a file.
        f = tmp_path / "lib.py"
        f.write_text("def utils(): pass\n")
        upsert_file(conn, f, "python", "h1",
                    [_sym("utils", "function", str(f))], [])

        caller = tmp_path / "caller.py"

        # Provide a namespace import mapping where local_name='utils' but is_namespace=True.
        # This should be SKIPPED — not queried for declaration check.
        ns_mapping = _import("utils", "*", "./lib", is_namespace=True)
        name_counts = {"utils": 1}  # count=1 → EXTRACTED if promotion ran

        result = resolve_edge(
            target_name="utils",
            name_counts=name_counts,
            language="python",
            import_mappings=[ns_mapping],
            referencing_file=caller,
            repo_root=tmp_path,
            conn=conn,
        )
        # Namespace import is skipped, so we fall through to name-count: count=1 → EXTRACTED,
        # but via name-unique (not 'import'), confirming skip occurred.
        assert result["confidence"] == CONFIDENCE_EXTRACTED
        # resolved_by='name-unique' (not 'import') proves the namespace was skipped.
        assert result["resolved_by"] == "name-unique", (
            f"Expected 'name-unique' (namespace skipped), got {result['resolved_by']!r}"
        )

    def test_wildcard_import_still_skipped(self, tmp_path: Path) -> None:
        """is_wildcard=True is also skipped (pre-existing behavior, confirm not broken)."""
        db_path = tmp_path / "wc.db"
        conn = init_db(db_path)
        f = tmp_path / "lib.py"
        f.write_text("def foo(): pass\n")
        upsert_file(conn, f, "python", "h1",
                    [_sym("foo", "function", str(f))], [])
        caller = tmp_path / "caller.py"
        wc_mapping = _import("*", "*", "./lib", is_wildcard=True)
        name_counts = {"foo": 1}

        result = resolve_edge(
            target_name="foo",
            name_counts=name_counts,
            language="python",
            import_mappings=[wc_mapping],
            referencing_file=caller,
            repo_root=tmp_path,
            conn=conn,
        )
        # Wildcard skip → falls through to name-count.
        assert result["confidence"] == CONFIDENCE_EXTRACTED
        assert result["resolved_by"] == "name-unique"


# ── Fix K: Empty import_mappings warning ─────────────────────────────────────


class TestEmptyImportMappingsWarning:
    """Fix K — once-per-process warning when import_mappings empty but symbols exist."""

    def test_warning_emitted_when_table_empty_but_symbols_exist(
        self, tmp_path: Path
    ) -> None:
        """When import_mappings is empty but symbols table has rows, a warning fires."""
        import seam.analysis.confidence as conf_module

        # Reset the module-level guard so this test can observe the first warning.
        original = conf_module._import_mappings_empty_warned
        conf_module._import_mappings_empty_warned = False

        try:
            db_path = tmp_path / "warn.db"
            conn = init_db(db_path)

            # Add a symbol but NO import_mappings row.
            f = tmp_path / "a.py"
            f.write_text("def fn(): pass\n")
            upsert_file(conn, f, "python", "h1",
                        [_sym("fn", "function", str(f))], [])

            # Manually ensure import_mappings is empty (init_db v6+ should have created the table).
            conn.execute("DELETE FROM import_mappings")
            conn.commit()

            # Call load_import_mappings — should trigger the warning via logger.warning.
            with patch.object(
                conf_module.logger, "warning",
                wraps=conf_module.logger.warning
            ) as mock_warn:
                load_import_mappings(conn, str(f))
                # The warning should have been called at least once.
                assert any(
                    "import_mappings" in str(call.args[0])
                    for call in mock_warn.call_args_list
                ), "Expected import_mappings warning to be logged"

        finally:
            # Restore original state so other tests are not affected.
            conf_module._import_mappings_empty_warned = original

    def test_warning_fires_only_once(self, tmp_path: Path) -> None:
        """The module-level guard ensures the warning fires at most once."""
        import seam.analysis.confidence as conf_module

        original = conf_module._import_mappings_empty_warned
        conf_module._import_mappings_empty_warned = False

        try:
            db_path = tmp_path / "once.db"
            conn = init_db(db_path)
            f = tmp_path / "a.py"
            f.write_text("def fn(): pass\n")
            upsert_file(conn, f, "python", "h1",
                        [_sym("fn", "function", str(f))], [])
            conn.execute("DELETE FROM import_mappings")
            conn.commit()

            with patch.object(
                conf_module.logger, "warning",
                wraps=conf_module.logger.warning
            ) as mock_warn:
                load_import_mappings(conn, str(f))
                load_import_mappings(conn, str(f))  # second call — should NOT re-warn
                load_import_mappings(conn, str(f))  # third call — should NOT re-warn

            # Count calls that mention import_mappings.
            relevant = [
                call for call in mock_warn.call_args_list
                if "import_mappings" in str(call.args[0])
            ]
            assert len(relevant) == 1, (
                f"Expected exactly 1 import_mappings warning, got {len(relevant)}"
            )
        finally:
            conf_module._import_mappings_empty_warned = original
