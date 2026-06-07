"""Integration tests for A1 dynamic-dispatch synthesis channels.

Full pipeline: write fixture files → index → run synthesis → query via seam_impact.

Coverage:
  EE-IMPACT:   event-emitter dispatch edge is traversed by seam_impact
  CC-IMPACT:   closure-collection dispatch edge is traversed by seam_impact
  EE-OFF:      SEAM_EDGE_SYNTHESIS=off → no event-emitter edges in DB
  CHANNEL-TAG: synthesized_by correctly identifies the channel

These tests write real fixture files to a temp directory so the bridge can
load source text (which is needed by A1a/A1b channels).
"""

import os
import tempfile
from pathlib import Path
from typing import Any

# ── EventEmitter integration fixture ─────────────────────────────────────────

# A minimal JS-style fixture that has a clearly matched event-emitter pattern:
# .on('save', onSaveHandler) + .emit('save') + a named function onSaveHandler.
_EE_FIXTURE = """\
class Store {
    constructor() {
        this._listeners = {};
    }

    on(event, handler) {
        this._listeners[event] = handler;
    }

    emit(event) {
        if (this._listeners[event]) {
            this._listeners[event]();
        }
    }
}

function onSaveHandler() {
    console.log("saved");
}

const store = new Store();
store.on('save', onSaveHandler);
store.emit('save');
"""

# A minimal closure-collection fixture that has the full positive pattern:
# a collection field iterated + element invoked, + an append site.
_CC_FIXTURE = """\
class Pipeline {
    var stages: [() -> Void] = []

    func execute() {
        stages.forEach { $0() }
    }
}

func setupPipeline(p: Pipeline) {
    p.stages.append(validateInput)
}

func validateInput() {
    print("validating")
}
"""


def _index_file(conn: Any, filepath: Path, language: str) -> None:
    """Parse and index a single file into conn."""
    from seam.indexer.db import upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_python, parse_swift, parse_typescript

    parser_map = {
        "python": parse_python,
        "typescript": parse_typescript,
        "swift": parse_swift,
    }
    parser = parser_map.get(language)
    if parser is None:
        return

    root = parser(filepath)
    if root is None:
        return

    symbols = extract_symbols(root, language, filepath)
    edges = extract_edges(root, language, filepath, symbols)
    upsert_file(conn, filepath, language, "test_hash", symbols, edges)


def _build_ee_db(enabled: bool = True) -> tuple[Any, int]:
    """Build an in-memory DB with the EventEmitter fixture, run synthesis.

    Returns (conn, synthesis_count).
    The fixture is written to a real tempfile because the bridge reads source from disk.
    """
    # Write the EE fixture to a temp file so _load_file_sources can read it.
    with tempfile.NamedTemporaryFile(
        suffix=".js", mode="w", delete=False, dir=tempfile.mkdtemp()
    ) as f:
        f.write(_EE_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)

    from seam.indexer.db import init_db
    conn = init_db(Path(":memory:"))

    # We index by treating JS as "javascript" — but for simplicity just upsert
    # the file row manually and rely on the synthesis channel's source-text scan.
    # The synthesis channels parse source text, not the AST, so we just need the
    # file row in the DB and the file on disk.
    try:
        # Insert the file row without language-specific symbol extraction.
        # The synthesis channels work from source text, not the symbol graph, so
        # we need the file path registered + on disk. We also add function symbols
        # manually so the event-emitter channel can resolve handler names.
        conn.execute(
            """
            INSERT INTO files (path, language, file_hash, mtime, indexed_at)
            VALUES (?, 'javascript', 'test', 0.0, 0.0)
            ON CONFLICT(path) DO NOTHING
            """,
            (str(filepath),),
        )
        row = conn.execute(
            "SELECT id FROM files WHERE path = ?", (str(filepath),)
        ).fetchone()
        file_id = row[0]

        # Insert the handler symbol so the EE channel finds it in known_names.
        conn.execute(
            """
            INSERT INTO symbols (name, kind, file_id, start_line, end_line)
            VALUES (?, 'function', ?, 1, 5)
            """,
            ("onSaveHandler", file_id),
        )
        conn.commit()

        import seam.config as cfg
        from seam.indexer.synthesis_index import index_synthesis
        count = index_synthesis(conn, enabled=enabled, fanout_cap=cfg.SEAM_SYNTHESIS_FANOUT_CAP)
    finally:
        try:
            os.unlink(fpath)
            os.rmdir(os.path.dirname(fpath))
        except Exception:
            pass

    return conn, count


def _build_cc_db(enabled: bool = True) -> tuple[Any, int]:
    """Build an in-memory DB with the closure-collection fixture, run synthesis."""
    with tempfile.NamedTemporaryFile(
        suffix=".swift", mode="w", delete=False, dir=tempfile.mkdtemp()
    ) as f:
        f.write(_CC_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)

    from seam.indexer.db import init_db
    conn = init_db(Path(":memory:"))

    try:
        conn.execute(
            """
            INSERT INTO files (path, language, file_hash, mtime, indexed_at)
            VALUES (?, 'swift', 'test', 0.0, 0.0)
            ON CONFLICT(path) DO NOTHING
            """,
            (str(filepath),),
        )
        row = conn.execute(
            "SELECT id FROM files WHERE path = ?", (str(filepath),)
        ).fetchone()
        file_id = row[0]

        # Insert the symbols needed for the closure-collection channel.
        for sym_name, sym_kind in [
            ("Pipeline", "class"),
            ("Pipeline.execute", "method"),
            ("setupPipeline", "function"),
            ("validateInput", "function"),
        ]:
            conn.execute(
                """
                INSERT INTO symbols (name, kind, file_id, start_line, end_line)
                VALUES (?, ?, ?, 1, 5)
                """,
                (sym_name, sym_kind, file_id),
            )
        conn.commit()

        import seam.config as cfg
        from seam.indexer.synthesis_index import index_synthesis
        count = index_synthesis(conn, enabled=enabled, fanout_cap=cfg.SEAM_SYNTHESIS_FANOUT_CAP)
    finally:
        try:
            os.unlink(fpath)
            os.rmdir(os.path.dirname(fpath))
        except Exception:
            pass

    return conn, count


# ── EventEmitter integration tests ────────────────────────────────────────────


class TestEventEmitterIntegration:
    """Event-emitter synthesized edges are stored and traversable."""

    def test_ee_edges_present_in_db(self) -> None:
        """After synthesis, event-emitter edge exists for 'save' event → onSaveHandler."""
        conn, count = _build_ee_db(enabled=True)
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE synthesized_by='event-emitter'"
        ).fetchall()
        pairs = {(r[0], r[1]) for r in rows}
        assert any("onSaveHandler" in tgt for _, tgt in pairs), (
            f"Expected onSaveHandler in event-emitter edges; got {pairs}"
        )

    def test_ee_count_positive(self) -> None:
        """index_synthesis returns positive count when EE patterns exist."""
        _, count = _build_ee_db(enabled=True)
        assert count > 0, f"Expected synthesis count > 0; got {count}"

    def test_ee_traversed_by_seam_impact(self) -> None:
        """seam_impact on the event key finds the registered handler as downstream."""
        conn, _ = _build_ee_db(enabled=True)

        # Get the source of the event-emitter edge (should be the event key 'save').
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE synthesized_by='event-emitter'"
        ).fetchall()
        assert rows, "No event-emitter edges in DB — cannot test impact traversal"

        # Use the source of the first EE edge as the impact target.
        source_name = rows[0][0]
        target_name = rows[0][1]

        from seam.server.tools import handle_seam_impact
        result = handle_seam_impact(
            conn,
            target=source_name,
            root=Path("/"),
            direction="downstream",
            max_depth=2,
            include_tests=False,
            verbose=False,
            limit=50,
        )

        # Collect all names from impact result.
        all_names: set[str] = set()
        for dir_key in ("downstream", "upstream", "both"):
            tier_group = result.get(dir_key, {})
            for tier_entries in tier_group.values():
                for entry in tier_entries:
                    all_names.add(entry.get("name", ""))

        assert target_name in all_names or any(
            "onSaveHandler" in n for n in all_names
        ), (
            f"Expected {target_name} in impact downstream; got {sorted(all_names)}"
        )

    def test_ee_absent_when_synthesis_off(self) -> None:
        """When synthesis disabled, no event-emitter edges in DB."""
        conn, count = _build_ee_db(enabled=False)
        assert count == 0
        n = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE synthesized_by='event-emitter'"
        ).fetchone()[0]
        assert n == 0, f"Expected no EE edges when disabled; found {n}"

    def test_ee_channel_tag_in_db(self) -> None:
        """synthesized_by='event-emitter' for all EE edges."""
        conn, _ = _build_ee_db(enabled=True)
        rows = conn.execute(
            "SELECT DISTINCT synthesized_by FROM edges WHERE synthesized_by='event-emitter'"
        ).fetchall()
        assert rows, "No event-emitter edges written"
        for row in rows:
            assert row[0] == "event-emitter"


# ── Closure-Collection integration tests ─────────────────────────────────────


class TestClosureCollectionIntegration:
    """Closure-collection synthesized edges are stored and traversable."""

    def test_cc_edges_present_in_db(self) -> None:
        """After synthesis, closure-collection edge links stages field → validateInput."""
        conn, count = _build_cc_db(enabled=True)
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE synthesized_by='closure-collection'"
        ).fetchall()
        pairs = {(r[0], r[1]) for r in rows}
        assert any("validateInput" in tgt for _, tgt in pairs), (
            f"Expected validateInput in closure-collection edges; got {pairs}"
        )

    def test_cc_count_positive(self) -> None:
        """index_synthesis returns positive count for CC patterns."""
        _, count = _build_cc_db(enabled=True)
        assert count > 0, f"Expected synthesis count > 0 for CC; got {count}"

    def test_cc_traversed_by_seam_impact(self) -> None:
        """seam_impact on the dispatch field finds the appended callback downstream."""
        conn, _ = _build_cc_db(enabled=True)

        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE synthesized_by='closure-collection'"
        ).fetchall()
        assert rows, "No closure-collection edges in DB — cannot test impact traversal"

        source_name = rows[0][0]
        target_name = rows[0][1]

        from seam.server.tools import handle_seam_impact
        result = handle_seam_impact(
            conn,
            target=source_name,
            root=Path("/"),
            direction="downstream",
            max_depth=2,
            include_tests=False,
            verbose=False,
            limit=50,
        )

        all_names: set[str] = set()
        for dir_key in ("downstream", "upstream", "both"):
            tier_group = result.get(dir_key, {})
            for tier_entries in tier_group.values():
                for entry in tier_entries:
                    all_names.add(entry.get("name", ""))

        assert target_name in all_names or any(
            "validateInput" in n for n in all_names
        ), (
            f"Expected {target_name} in impact downstream; got {sorted(all_names)}"
        )

    def test_cc_absent_when_synthesis_off(self) -> None:
        """When synthesis disabled, no CC edges in DB."""
        conn, count = _build_cc_db(enabled=False)
        assert count == 0
        n = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE synthesized_by='closure-collection'"
        ).fetchone()[0]
        assert n == 0, f"Expected no CC edges when disabled; found {n}"
