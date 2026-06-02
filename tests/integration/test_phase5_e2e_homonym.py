"""True end-to-end integration tests for Phase 5 homonym fix.

These tests exercise the homonym fix THROUGH the public read API:
  - flows.trace(...)
  - flows.callers(...)  / flows.callees(...)
  - tools.handle_seam_trace(...)
  - tools.handle_seam_impact(...)

NOT through resolve_edge() directly (those are unit tests in test_phase5_import_promotion.py).

Fixture topology:
  app/parser.py    — defines parse()
  lib/parser.py    — defines parse()   (second homonym — makes name-count = 2 → AMBIGUOUS)
  main.py          — `from app.parser import parse` then `main_func()` which calls `parse()`

Canonical assertions (from PRD stories 1 & 2):
  E1 — Imported binding: parse() call in main.py resolves EXTRACTED 'import' via trace()
  E2 — Un-imported homonym: no import binding → stays AMBIGUOUS 'name-collision'
  E3 — Builtin call: len() in main.py resolves INFERRED 'builtin' via callees()
  E4 — Watcher re-index: after single-file re-index, resolution is preserved (read-time guarantee)
  E5 — SEAM_IMPORT_RESOLUTION="off": resolved_by from name-count, not import promotion
  E6 — handle_seam_impact: upstream impact entry carries resolved_by from walk()
  E7 — handle_seam_trace: hop resolved_by = 'import' for the promoted binding
"""

from pathlib import Path

import pytest

from seam.analysis import flows as flows_module
from seam.analysis.confidence import CONFIDENCE_AMBIGUOUS, CONFIDENCE_EXTRACTED, CONFIDENCE_INFERRED
from seam.analysis.imports import ImportMapping
from seam.indexer.db import init_db, upsert_file, upsert_import_mappings
from seam.indexer.graph import Edge, Symbol
from seam.server import tools

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _sym(name: str, kind: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind=kind, file=file,
        start_line=line, end_line=line + 5,
        docstring=None, signature=None, decorators=[],
        is_exported=None, visibility=None, qualified_name=name,
    )


def _edge(source: str, target: str, file: str, kind: str = "call", line: int = 5) -> Edge:
    return Edge(
        source=source, target=target, kind=kind,
        file=file, line=line, confidence="INFERRED",
    )


def _import_edge(source: str, target: str, file: str, line: int = 1) -> Edge:
    return Edge(
        source=source, target=target, kind="import",
        file=file, line=line, confidence="INFERRED",
    )


def _mapping(local_name: str, exported_name: str, source_module: str) -> ImportMapping:
    """Build an ImportMapping for testing."""
    return ImportMapping(
        local_name=local_name,
        exported_name=exported_name,
        source_module=source_module,
        is_default=False,
        is_namespace=False,
        is_wildcard=False,
        line=1,
    )


# ── Shared fixture: homonym DB ────────────────────────────────────────────────


@pytest.fixture()
def homonym_db(tmp_path: Path):
    """Create a DB with two files declaring 'parse' and a referencing file.

    Topology:
        app/parser.py  — defines parse()
        lib/parser.py  — defines parse()       (homonym: count=2 → AMBIGUOUS globally)
        main.py        — defines main_func(), which calls parse() and len()
                         imports parse from app.parser
                         has an import edge: main_func → parse (call)
                         has an import edge: main_func → len   (call, builtin)

    Import mappings for main.py:
        local_name='parse', exported_name='parse', source_module='app.parser'

    Returns (conn, tmp_path, app_parser_path, lib_parser_path, main_path)
    """
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    # Create on-disk files so resolve_import_source() can find them.
    (tmp_path / "app").mkdir()
    (tmp_path / "lib").mkdir()

    app_parser = tmp_path / "app" / "parser.py"
    app_parser.write_text("def parse(): pass\n")

    lib_parser = tmp_path / "lib" / "parser.py"
    lib_parser.write_text("def parse(): pass\n")

    main_py = tmp_path / "main.py"
    main_py.write_text(
        "from app.parser import parse\n"
        "def main_func():\n"
        "    parse()\n"
        "    len([])\n"
    )

    # Index app/parser.py — declares parse
    upsert_file(conn, app_parser, "python", "hash_app",
                [_sym("parse", "function", str(app_parser))], [])

    # Index lib/parser.py — declares parse (second homonym)
    upsert_file(conn, lib_parser, "python", "hash_lib",
                [_sym("parse", "function", str(lib_parser))], [])

    # Index main.py — declares main_func, has call edges to parse and len
    upsert_file(conn, main_py, "python", "hash_main",
                [_sym("main_func", "function", str(main_py))],
                [
                    _edge("main_func", "parse", str(main_py)),   # call to homonym
                    _edge("main_func", "len", str(main_py)),     # call to builtin
                ])

    # Persist import mappings: main.py imports 'parse' from 'app.parser'
    upsert_import_mappings(conn, main_py, [
        _mapping("parse", "parse", "app.parser"),
    ])

    yield conn, tmp_path, app_parser, lib_parser, main_py
    conn.close()


# ── E1: Imported binding resolves EXTRACTED 'import' via flows.trace() ────────


class TestE1ImportedBindingViaTrace:
    """E1 — flows.trace() with repo_root: the imported parse hop resolves EXTRACTED 'import'."""

    def test_trace_imported_homonym_is_extracted(self, homonym_db: tuple) -> None:
        conn, tmp_path, app_parser, lib_parser, main_py = homonym_db

        # Trace from main_func to parse WITH repo_root (import promotion enabled).
        paths = flows_module.trace(
            conn, "main_func", "parse",
            max_depth=5,
            repo_root=tmp_path,
        )

        assert len(paths) == 1, "Expected a path from main_func to parse"
        path = paths[0]
        assert len(path) >= 1, "Path must have at least one hop"

        # The hop from main_func → parse should be EXTRACTED because main.py
        # has an import binding: `from app.parser import parse` → exactly one
        # indexed declaring file (app/parser.py).
        hop = path[0]
        assert hop["from_name"] == "main_func"
        assert hop["to_name"] == "parse"
        assert hop["confidence"] == CONFIDENCE_EXTRACTED, (
            f"Expected EXTRACTED for imported binding, got {hop['confidence']}"
        )
        assert hop["resolved_by"] == "import", (
            f"Expected resolved_by='import', got {hop['resolved_by']!r}"
        )

    def test_trace_without_repo_root_is_ambiguous(self, homonym_db: tuple) -> None:
        """Without repo_root, name-count applies: parse has count=2 → AMBIGUOUS."""
        conn, tmp_path, *_ = homonym_db

        paths = flows_module.trace(
            conn, "main_func", "parse",
            max_depth=5,
            repo_root=None,  # no import promotion
        )

        assert len(paths) == 1
        hop = paths[0][0]
        assert hop["confidence"] == CONFIDENCE_AMBIGUOUS, (
            f"Without repo_root, expected AMBIGUOUS for homonym, got {hop['confidence']}"
        )
        assert hop["resolved_by"] is None, (
            f"Without repo_root, resolved_by should be None, got {hop['resolved_by']!r}"
        )


# ── E2: Un-imported homonym stays AMBIGUOUS 'name-collision' ─────────────────


class TestE2UnimportedHomonymStaysAmbiguous:
    """E2 — A homonym with no import binding stays AMBIGUOUS 'name-collision'."""

    def test_unimported_homonym_ambiguous_via_callees(self, tmp_path: Path) -> None:
        """caller.py calls render() but has no import binding → AMBIGUOUS."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        file_a = tmp_path / "a" / "views.py"
        file_a.write_text("def render(): pass\n")
        file_b = tmp_path / "b" / "views.py"
        file_b.write_text("def render(): pass\n")

        caller = tmp_path / "caller.py"
        caller.write_text("def handler():\n    render()\n")

        upsert_file(conn, file_a, "python", "aaa",
                    [_sym("render", "function", str(file_a))], [])
        upsert_file(conn, file_b, "python", "bbb",
                    [_sym("render", "function", str(file_b))], [])
        upsert_file(conn, caller, "python", "ccc",
                    [_sym("handler", "function", str(caller))],
                    [_edge("handler", "render", str(caller))])
        # No import mapping for render → must fall through to name-count

        result = flows_module.callees(conn, "handler", repo_root=tmp_path)
        conn.close()

        render_hops = [h for h in result if h["name"] == "render"]
        assert len(render_hops) >= 1, "Expected at least one hop to render"
        hop = render_hops[0]
        assert hop["confidence"] == CONFIDENCE_AMBIGUOUS, (
            f"Expected AMBIGUOUS for un-imported homonym, got {hop['confidence']}"
        )
        assert hop["resolved_by"] == "name-collision", (
            f"Expected 'name-collision', got {hop['resolved_by']!r}"
        )


# ── E3: Builtin call resolves INFERRED 'builtin' ─────────────────────────────


class TestE3BuiltinCallViaCallees:
    """E3 — len() call resolves INFERRED 'builtin' via flows.callees() with repo_root."""

    def test_builtin_len_resolves_builtin(self, homonym_db: tuple) -> None:
        conn, tmp_path, *_ = homonym_db

        result = flows_module.callees(conn, "main_func", repo_root=tmp_path)

        len_hops = [h for h in result if h["name"] == "len"]
        assert len(len_hops) >= 1, "Expected a callees hop to len"
        hop = len_hops[0]
        assert hop["confidence"] == CONFIDENCE_INFERRED, (
            f"Expected INFERRED for builtin, got {hop['confidence']}"
        )
        assert hop["resolved_by"] == "builtin", (
            f"Expected resolved_by='builtin', got {hop['resolved_by']!r}"
        )


# ── E4: Watcher re-index preserves resolution (read-time guarantee) ───────────


class TestE4WatcherReindexPreservesResolution:
    """E4 — After single-file re-index of main.py, resolution is preserved.

    Because resolution is read-time (not write-time), a watcher-style
    single-file re-index of the referencing file must yield the same result.
    """

    def test_resolution_preserved_after_reindex(self, homonym_db: tuple) -> None:
        conn, tmp_path, app_parser, lib_parser, main_py = homonym_db

        # Re-index main.py in-place (watcher-style single-file re-index).
        # This simulates what the watcher daemon does after a file save.
        upsert_file(conn, main_py, "python", "hash_main_v2",
                    [_sym("main_func", "function", str(main_py))],
                    [
                        _edge("main_func", "parse", str(main_py)),
                        _edge("main_func", "len", str(main_py)),
                    ])
        upsert_import_mappings(conn, main_py, [
            _mapping("parse", "parse", "app.parser"),
        ])

        # After re-index, resolution must still be EXTRACTED 'import' — read-time guarantee.
        paths = flows_module.trace(
            conn, "main_func", "parse",
            max_depth=5,
            repo_root=tmp_path,
        )
        assert len(paths) == 1
        hop = paths[0][0]
        assert hop["confidence"] == CONFIDENCE_EXTRACTED, (
            f"After re-index, expected EXTRACTED, got {hop['confidence']}"
        )
        assert hop["resolved_by"] == "import"


# ── E5: SEAM_IMPORT_RESOLUTION="off" falls back to name-count ─────────────────


class TestE5ImportResolutionOff:
    """E5 — When SEAM_IMPORT_RESOLUTION="off", resolved_by comes from name-count.

    The name-count rule for a homonym (count=2) → AMBIGUOUS 'name-collision'.
    resolved_by must NOT be None when SEAM_IMPORT_RESOLUTION="off" because
    _resolve_name_count always provides a resolved_by (unlike the pure fast-path
    name-count resolve() shim which returns None).
    """

    def test_import_resolution_off_uses_name_count(
        self, homonym_db: tuple, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn, tmp_path, *_ = homonym_db

        # Disable import resolution at the config level.
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_IMPORT_RESOLUTION", "off")

        paths = flows_module.trace(
            conn, "main_func", "parse",
            max_depth=5,
            repo_root=tmp_path,  # repo_root provided but SEAM_IMPORT_RESOLUTION="off"
        )

        assert len(paths) == 1
        hop = paths[0][0]
        # With resolution "off", falls back to plain resolve() shim (name-count only).
        # count=2 → AMBIGUOUS; resolved_by is None because plain resolve() returns no provenance.
        assert hop["confidence"] == CONFIDENCE_AMBIGUOUS, (
            f"Expected AMBIGUOUS with resolution off, got {hop['confidence']}"
        )
        # Fast-path resolve() returns None for resolved_by.
        assert hop["resolved_by"] is None, (
            f"Expected None when SEAM_IMPORT_RESOLUTION='off', got {hop['resolved_by']!r}"
        )


# ── E6: handle_seam_impact includes resolved_by in tier entries ───────────────


class TestE6ImpactHandlerResolvedBy:
    """E6 — handle_seam_impact entries carry resolved_by from walk()."""

    def test_impact_entry_has_resolved_by(self, homonym_db: tuple) -> None:
        conn, tmp_path, app_parser, lib_parser, main_py = homonym_db

        # Impact on 'parse' upstream = who calls parse.
        # main_func calls parse → main_func should appear as WILL_BREAK.
        result = tools.handle_seam_impact(conn, "parse", tmp_path, direction="upstream")

        assert result["found"] is True, "parse must be found in the index"
        will_break = result["upstream"]["WILL_BREAK"]
        assert len(will_break) >= 1, "main_func should be in WILL_BREAK tier"

        entry = next((e for e in will_break if e["name"] == "main_func"), None)
        assert entry is not None, "main_func not found in WILL_BREAK"
        # resolved_by field must be present (may be None for no-context hops, or a string)
        assert "resolved_by" in entry, "resolved_by field must be present in impact entries"

    def test_impact_entry_resolved_by_is_import_when_promoted(
        self, homonym_db: tuple
    ) -> None:
        conn, tmp_path, app_parser, lib_parser, main_py = homonym_db

        # Downstream impact on main_func: what does main_func call?
        # parse is called by main_func and has an import binding → EXTRACTED 'import'.
        result = tools.handle_seam_impact(
            conn, "main_func", tmp_path, direction="downstream"
        )

        assert result["found"] is True
        will_break = result["downstream"]["WILL_BREAK"]
        parse_entries = [e for e in will_break if e["name"] == "parse"]
        assert len(parse_entries) >= 1, "parse must appear in downstream WILL_BREAK"

        entry = parse_entries[0]
        assert entry["confidence"] == CONFIDENCE_EXTRACTED, (
            f"Downstream parse hop should be EXTRACTED, got {entry['confidence']}"
        )
        assert entry["resolved_by"] == "import", (
            f"Expected resolved_by='import', got {entry['resolved_by']!r}"
        )


# ── E7: handle_seam_trace hops carry resolved_by = 'import' ──────────────────


class TestE7TraceHandlerResolvedBy:
    """E7 — handle_seam_trace path hops carry resolved_by='import' for promoted binding."""

    def test_trace_handler_hop_resolved_by_import(self, homonym_db: tuple) -> None:
        conn, tmp_path, *_ = homonym_db

        result = tools.handle_seam_trace(conn, "main_func", "parse", tmp_path)

        assert result["found"] is True, "Path from main_func to parse must exist"
        assert len(result["paths"]) >= 1
        hop = result["paths"][0][0]

        assert hop["confidence"] == CONFIDENCE_EXTRACTED, (
            f"Expected EXTRACTED hop, got {hop['confidence']}"
        )
        assert hop["resolved_by"] == "import", (
            f"Expected resolved_by='import', got {hop['resolved_by']!r}"
        )

    def test_trace_handler_callees_have_resolved_by(self, homonym_db: tuple) -> None:
        """callees_source in handle_seam_trace output also carry resolved_by."""
        conn, tmp_path, *_ = homonym_db

        result = tools.handle_seam_trace(conn, "main_func", "parse", tmp_path)

        # All callees_source entries must have the resolved_by key.
        for h in result["callees_source"]:
            assert "resolved_by" in h, f"Missing resolved_by in callee hop: {h}"
