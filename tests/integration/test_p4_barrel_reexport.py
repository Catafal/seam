"""Integration tests for P4 — barrel re-export following.

A barrel is a TypeScript `index.ts` that re-exports symbols from sibling
files (`export { Foo } from './foo'`). When a consumer imports `Foo` from the
barrel, the barrel itself does NOT declare `Foo` — the real declarer is the
sibling file. P4 follows the re-export chain at read time (bounded + cached)
so the import promotes to EXTRACTED pointing at the ACTUAL declaring file.

TDD: written BEFORE the implementation (RED phase). The 1-hop / 2-hop / cycle
tests fail pre-P4 because the resolver falls through to the name-count rule
(AMBIGUOUS) instead of chasing the barrel.

Test groups:
    B1 — 1-hop barrel: index.ts re-exports Foo from ./foo → Foo resolves to foo.ts (EXTRACTED).
    B2 — 2-hop chain: index.ts → sub/index.ts → foo.ts resolves.
    B3 — depth cap: chain longer than SEAM_BARREL_DEPTH stops (stays AMBIGUOUS).
    B4 — SEAM_BARREL_DEPTH=0 reproduces pre-P4 behavior (AMBIGUOUS).
    B5 — cycle does not infinite-loop (a re-exports b, b re-exports a).
"""

from pathlib import Path

import seam.config as config
from seam.analysis.confidence import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    load_import_mappings,
    load_name_counts,
    resolve_edge,
)
from seam.analysis.imports import ImportMapping
from seam.indexer.db import init_db, upsert_file, upsert_import_mappings
from seam.indexer.graph import Symbol

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file,
        start_line=line, end_line=line + 2,
        docstring=None, signature=None, decorators=[],
        is_exported=True, visibility=None, qualified_name=name,
    )


def _reexport(local: str, source: str, line: int = 1) -> ImportMapping:
    """A barrel re-export binding: `export { local } from 'source'`.

    Modeled as a named non-default import mapping (same shape the TS extractor
    produces for `import { X } from './x'` — barrels reuse that binding form).
    """
    return ImportMapping(
        local_name=local, exported_name=local, source_module=source,
        is_default=False, is_namespace=False, is_wildcard=False, line=line,
    )


# ── B1: 1-hop barrel ───────────────────────────────────────────────────────────


class TestOneHopBarrel:
    def test_barrel_resolves_to_declaring_file(self, tmp_path: Path) -> None:
        """main.ts imports Foo from ./barrel; barrel/index.ts re-exports Foo
        from ./foo; foo.ts declares Foo. Two Foo symbols globally (collision),
        but the barrel chain should promote to EXTRACTED pointing at foo.ts."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        # Real declarer
        foo_file = tmp_path / "barrel" / "foo.ts"
        foo_file.parent.mkdir(parents=True)
        foo_file.write_text("export function Foo() {}\n")

        # A second, unrelated declarer of Foo elsewhere → global collision
        other_file = tmp_path / "other" / "foo.ts"
        other_file.parent.mkdir(parents=True)
        other_file.write_text("export function Foo() {}\n")

        # The barrel: re-exports Foo from ./foo but does NOT declare Foo itself
        barrel_file = tmp_path / "barrel" / "index.ts"
        barrel_file.write_text("export { Foo } from './foo';\n")

        # Consumer importing from the barrel
        main_file = tmp_path / "main.ts"
        main_file.write_text("import { Foo } from './barrel';\nFoo();\n")

        upsert_file(conn, foo_file, "typescript", "f", [_sym("Foo", str(foo_file))], [])
        upsert_file(conn, other_file, "typescript", "o", [_sym("Foo", str(other_file))], [])
        upsert_file(conn, barrel_file, "typescript", "b", [], [])
        upsert_file(conn, main_file, "typescript", "m", [], [])

        # barrel re-exports Foo from ./foo
        upsert_import_mappings(conn, barrel_file, [_reexport("Foo", "./foo")])
        # main imports Foo from ./barrel
        upsert_import_mappings(conn, main_file, [_reexport("Foo", "./barrel")])

        name_counts = load_name_counts(conn)
        assert name_counts.get("Foo", 0) == 2, "Setup: Foo must collide globally"

        import_mappings = load_import_mappings(conn, str(main_file))
        result = resolve_edge(
            target_name="Foo", name_counts=name_counts, language="typescript",
            import_mappings=import_mappings, referencing_file=main_file,
            repo_root=tmp_path, conn=conn,
        )
        conn.close()

        assert result["confidence"] == CONFIDENCE_EXTRACTED, (
            f"Expected EXTRACTED via barrel chase, got {result['confidence']}"
        )
        assert result["resolved_by"] == "import"
        assert result["best_candidate"] is not None
        assert str(foo_file) == result["best_candidate"], (
            f"best_candidate should be the real declarer foo.ts, got {result['best_candidate']}"
        )


# ── B2: 2-hop chain ─────────────────────────────────────────────────────────────


class TestTwoHopChain:
    def test_two_hop_chain_resolves(self, tmp_path: Path) -> None:
        """main → top/index.ts → top/sub/index.ts → top/sub/foo.ts."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        foo_file = tmp_path / "top" / "sub" / "foo.ts"
        foo_file.parent.mkdir(parents=True)
        foo_file.write_text("export function Bar() {}\n")

        other_file = tmp_path / "elsewhere" / "bar.ts"
        other_file.parent.mkdir(parents=True)
        other_file.write_text("export function Bar() {}\n")

        sub_barrel = tmp_path / "top" / "sub" / "index.ts"
        sub_barrel.write_text("export { Bar } from './foo';\n")

        top_barrel = tmp_path / "top" / "index.ts"
        top_barrel.write_text("export { Bar } from './sub';\n")

        main_file = tmp_path / "main.ts"
        main_file.write_text("import { Bar } from './top';\nBar();\n")

        upsert_file(conn, foo_file, "typescript", "f", [_sym("Bar", str(foo_file))], [])
        upsert_file(conn, other_file, "typescript", "o", [_sym("Bar", str(other_file))], [])
        upsert_file(conn, sub_barrel, "typescript", "s", [], [])
        upsert_file(conn, top_barrel, "typescript", "t", [], [])
        upsert_file(conn, main_file, "typescript", "m", [], [])

        upsert_import_mappings(conn, sub_barrel, [_reexport("Bar", "./foo")])
        upsert_import_mappings(conn, top_barrel, [_reexport("Bar", "./sub")])
        upsert_import_mappings(conn, main_file, [_reexport("Bar", "./top")])

        name_counts = load_name_counts(conn)
        import_mappings = load_import_mappings(conn, str(main_file))
        result = resolve_edge(
            target_name="Bar", name_counts=name_counts, language="typescript",
            import_mappings=import_mappings, referencing_file=main_file,
            repo_root=tmp_path, conn=conn,
        )
        conn.close()

        assert result["confidence"] == CONFIDENCE_EXTRACTED
        assert result["best_candidate"] == str(foo_file)


# ── B3: depth cap ───────────────────────────────────────────────────────────────


class TestDepthCap:
    def test_chain_longer_than_cap_stays_ambiguous(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A 3-hop chain with SEAM_BARREL_DEPTH=2 cannot reach the declarer →
        stays AMBIGUOUS (falls through to name-count)."""
        monkeypatch.setattr(config, "SEAM_BARREL_DEPTH", 2)

        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        # declarer at the END of a 3-hop barrel chain
        foo_file = tmp_path / "a" / "b" / "c" / "foo.ts"
        foo_file.parent.mkdir(parents=True)
        foo_file.write_text("export function Baz() {}\n")
        other = tmp_path / "z" / "foo.ts"
        other.parent.mkdir(parents=True)
        other.write_text("export function Baz() {}\n")

        b3 = tmp_path / "a" / "b" / "c" / "index.ts"
        b3.write_text("export { Baz } from './foo';\n")
        b2 = tmp_path / "a" / "b" / "index.ts"
        b2.write_text("export { Baz } from './c';\n")
        b1 = tmp_path / "a" / "index.ts"
        b1.write_text("export { Baz } from './b';\n")
        main_file = tmp_path / "main.ts"
        main_file.write_text("import { Baz } from './a';\nBaz();\n")

        upsert_file(conn, foo_file, "typescript", "f", [_sym("Baz", str(foo_file))], [])
        upsert_file(conn, other, "typescript", "o", [_sym("Baz", str(other))], [])
        for bf, tag in ((b3, "3"), (b2, "2"), (b1, "1"), (main_file, "m")):
            upsert_file(conn, bf, "typescript", tag, [], [])

        upsert_import_mappings(conn, b3, [_reexport("Baz", "./foo")])
        upsert_import_mappings(conn, b2, [_reexport("Baz", "./c")])
        upsert_import_mappings(conn, b1, [_reexport("Baz", "./b")])
        upsert_import_mappings(conn, main_file, [_reexport("Baz", "./a")])

        name_counts = load_name_counts(conn)
        import_mappings = load_import_mappings(conn, str(main_file))
        result = resolve_edge(
            target_name="Baz", name_counts=name_counts, language="typescript",
            import_mappings=import_mappings, referencing_file=main_file,
            repo_root=tmp_path, conn=conn,
        )
        conn.close()

        # depth 2 cannot reach the declarer 3 hops away → ambiguous fallthrough
        assert result["confidence"] == CONFIDENCE_AMBIGUOUS


# ── B4: SEAM_BARREL_DEPTH=0 → pre-P4 behavior ───────────────────────────────────


class TestDisabled:
    def test_depth_zero_reproduces_pre_p4(self, tmp_path: Path, monkeypatch) -> None:
        """With SEAM_BARREL_DEPTH=0, a barrel import that would otherwise chase
        stays AMBIGUOUS — byte-identical to pre-P4."""
        monkeypatch.setattr(config, "SEAM_BARREL_DEPTH", 0)

        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        foo_file = tmp_path / "barrel" / "foo.ts"
        foo_file.parent.mkdir(parents=True)
        foo_file.write_text("export function Foo() {}\n")
        other = tmp_path / "other" / "foo.ts"
        other.parent.mkdir(parents=True)
        other.write_text("export function Foo() {}\n")
        barrel = tmp_path / "barrel" / "index.ts"
        barrel.write_text("export { Foo } from './foo';\n")
        main_file = tmp_path / "main.ts"
        main_file.write_text("import { Foo } from './barrel';\nFoo();\n")

        upsert_file(conn, foo_file, "typescript", "f", [_sym("Foo", str(foo_file))], [])
        upsert_file(conn, other, "typescript", "o", [_sym("Foo", str(other))], [])
        upsert_file(conn, barrel, "typescript", "b", [], [])
        upsert_file(conn, main_file, "typescript", "m", [], [])
        upsert_import_mappings(conn, barrel, [_reexport("Foo", "./foo")])
        upsert_import_mappings(conn, main_file, [_reexport("Foo", "./barrel")])

        name_counts = load_name_counts(conn)
        import_mappings = load_import_mappings(conn, str(main_file))
        result = resolve_edge(
            target_name="Foo", name_counts=name_counts, language="typescript",
            import_mappings=import_mappings, referencing_file=main_file,
            repo_root=tmp_path, conn=conn,
        )
        conn.close()

        assert result["confidence"] == CONFIDENCE_AMBIGUOUS


# ── B5: cycle safety ────────────────────────────────────────────────────────────


class TestCycleSafety:
    def test_cycle_does_not_infinite_loop(self, tmp_path: Path) -> None:
        """index_a re-exports from ./b, index_b re-exports from ./a, and no file
        declares the name. The chase must terminate (no infinite loop) and fall
        through to a non-EXTRACTED resolution."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        a_barrel = tmp_path / "a" / "index.ts"
        a_barrel.parent.mkdir(parents=True)
        a_barrel.write_text("export { Loop } from '../b';\n")
        b_barrel = tmp_path / "b" / "index.ts"
        b_barrel.parent.mkdir(parents=True)
        b_barrel.write_text("export { Loop } from '../a';\n")

        # Two declarers so the name is a global collision (count==2) — without the
        # chase this is AMBIGUOUS; the point is termination, not the final tier.
        d1 = tmp_path / "x" / "loop.ts"
        d1.parent.mkdir(parents=True)
        d1.write_text("export function Loop() {}\n")
        d2 = tmp_path / "y" / "loop.ts"
        d2.parent.mkdir(parents=True)
        d2.write_text("export function Loop() {}\n")

        main_file = tmp_path / "main.ts"
        main_file.write_text("import { Loop } from './a';\nLoop();\n")

        upsert_file(conn, d1, "typescript", "d1", [_sym("Loop", str(d1))], [])
        upsert_file(conn, d2, "typescript", "d2", [_sym("Loop", str(d2))], [])
        upsert_file(conn, a_barrel, "typescript", "a", [], [])
        upsert_file(conn, b_barrel, "typescript", "b", [], [])
        upsert_file(conn, main_file, "typescript", "m", [], [])
        upsert_import_mappings(conn, a_barrel, [_reexport("Loop", "../b")])
        upsert_import_mappings(conn, b_barrel, [_reexport("Loop", "../a")])
        upsert_import_mappings(conn, main_file, [_reexport("Loop", "./a")])

        name_counts = load_name_counts(conn)
        import_mappings = load_import_mappings(conn, str(main_file))
        # The assertion is simply: this call RETURNS (no infinite loop / stack blow).
        result = resolve_edge(
            target_name="Loop", name_counts=name_counts, language="typescript",
            import_mappings=import_mappings, referencing_file=main_file,
            repo_root=tmp_path, conn=conn,
        )
        conn.close()

        assert result["confidence"] in (CONFIDENCE_AMBIGUOUS,)
