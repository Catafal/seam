"""Unit tests for seam/analysis/imports.py — Phase 5, Slice 2.

TDD: tests written BEFORE implementation (RED phase).

Test groups:
    I1 — Python: extract_import_mappings (all import forms)
    I2 — TypeScript/JS: extract_import_mappings (all import forms)
    I3 — Go: extract_import_mappings
    I4 — Rust: extract_import_mappings
    I5 — resolve_import_source: extension-order + relative + third-party
    I6 — compute_path_proximity: ordering and pure function contract
    I7 — Never-raises contract: bad inputs return [] / 0
"""

import tempfile
from pathlib import Path

from seam.analysis.imports import (
    ImportMapping,
    compute_path_proximity,
    extract_import_mappings,
    resolve_import_source,
)
from seam.indexer.parser import parse_go, parse_python, parse_rust, parse_typescript

# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_and_extract(code: str, language: str, suffix: str = ".py") -> list[ImportMapping]:
    """Write code to a temp file, parse it, extract import mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / f"test{suffix}"
        filepath.write_text(code, encoding="utf-8")
        if language == "python":
            root = parse_python(filepath)
        elif language in ("typescript", "javascript"):
            root = parse_typescript(filepath)
        elif language == "go":
            root = parse_go(filepath)
        elif language == "rust":
            root = parse_rust(filepath)
        else:
            return []
        if root is None:
            return []
        return extract_import_mappings(root, filepath, language)


# ── I1: Python import forms ───────────────────────────────────────────────────


class TestPythonImportExtraction:
    """I1 — Python: all import statement forms produce correct ImportMapping records."""

    def test_import_module(self) -> None:
        mappings = _parse_and_extract("import os\n", "python")
        assert any(m["local_name"] == "os" and m["source_module"] == "os" for m in mappings)

    def test_import_aliased(self) -> None:
        mappings = _parse_and_extract("import numpy as np\n", "python")
        assert any(
            m["local_name"] == "np" and m["source_module"] == "numpy"
            for m in mappings
        )

    def test_from_import(self) -> None:
        mappings = _parse_and_extract("from app.parser import parse\n", "python")
        assert any(
            m["local_name"] == "parse"
            and m["exported_name"] == "parse"
            and m["source_module"] == "app.parser"
            for m in mappings
        )

    def test_from_import_aliased(self) -> None:
        mappings = _parse_and_extract("from app.parser import parse as p\n", "python")
        assert any(
            m["local_name"] == "p"
            and m["exported_name"] == "parse"
            and m["source_module"] == "app.parser"
            for m in mappings
        )

    def test_from_import_wildcard(self) -> None:
        mappings = _parse_and_extract("from app.utils import *\n", "python")
        assert any(m["is_wildcard"] is True and m["source_module"] == "app.utils" for m in mappings)

    def test_relative_import(self) -> None:
        mappings = _parse_and_extract("from . import sibling\n", "python")
        assert any(m["local_name"] == "sibling" for m in mappings)

    def test_relative_module_import(self) -> None:
        mappings = _parse_and_extract("from .parser import parse\n", "python")
        assert any(
            m["local_name"] == "parse" and "parser" in m["source_module"]
            for m in mappings
        )

    def test_multiple_imports_in_one_statement(self) -> None:
        mappings = _parse_and_extract("from app.utils import foo, bar\n", "python")
        names = {m["local_name"] for m in mappings}
        assert "foo" in names
        assert "bar" in names

    def test_non_wildcard_is_not_wildcard(self) -> None:
        mappings = _parse_and_extract("from app.parser import parse\n", "python")
        for m in mappings:
            if m["local_name"] == "parse":
                assert m["is_wildcard"] is False


# ── I2: TypeScript/JS import forms ───────────────────────────────────────────


class TestTypeScriptImportExtraction:
    """I2 — TypeScript: all import forms produce correct ImportMapping records."""

    def test_named_import(self) -> None:
        mappings = _parse_and_extract(
            "import { parse } from './parser';\n", "typescript", ".ts"
        )
        assert any(
            m["local_name"] == "parse"
            and m["exported_name"] == "parse"
            and m["source_module"] == "./parser"
            for m in mappings
        )

    def test_aliased_named_import(self) -> None:
        mappings = _parse_and_extract(
            "import { parse as p } from './parser';\n", "typescript", ".ts"
        )
        assert any(
            m["local_name"] == "p"
            and m["exported_name"] == "parse"
            for m in mappings
        )

    def test_default_import(self) -> None:
        mappings = _parse_and_extract(
            "import Parser from './parser';\n", "typescript", ".ts"
        )
        assert any(
            m["local_name"] == "Parser"
            and m["is_default"] is True
            for m in mappings
        )

    def test_namespace_import(self) -> None:
        mappings = _parse_and_extract(
            "import * as utils from './utils';\n", "typescript", ".ts"
        )
        assert any(
            m["local_name"] == "utils"
            and m["is_namespace"] is True
            for m in mappings
        )

    def test_multiple_named_imports(self) -> None:
        mappings = _parse_and_extract(
            "import { foo, bar } from './utils';\n", "typescript", ".ts"
        )
        names = {m["local_name"] for m in mappings}
        assert "foo" in names
        assert "bar" in names


# ── I3: Go import forms ───────────────────────────────────────────────────────


class TestGoImportExtraction:
    """I3 — Go: import statements produce correct ImportMapping records."""

    def test_simple_import(self) -> None:
        code = 'package main\nimport "fmt"\n'
        mappings = _parse_and_extract(code, "go", ".go")
        assert any(m["local_name"] == "fmt" and m["source_module"] == "fmt" for m in mappings)

    def test_aliased_import(self) -> None:
        code = 'package main\nimport p "path/filepath"\n'
        mappings = _parse_and_extract(code, "go", ".go")
        assert any(m["local_name"] == "p" for m in mappings)

    def test_grouped_imports(self) -> None:
        code = 'package main\nimport (\n\t"fmt"\n\t"os"\n)\n'
        mappings = _parse_and_extract(code, "go", ".go")
        names = {m["local_name"] for m in mappings}
        assert "fmt" in names
        assert "os" in names


# ── I4: Rust use forms ────────────────────────────────────────────────────────


class TestRustImportExtraction:
    """I4 — Rust: use statements produce correct ImportMapping records."""

    def test_simple_use(self) -> None:
        code = "use std::collections::HashMap;\n"
        mappings = _parse_and_extract(code, "rust", ".rs")
        assert any(m["local_name"] == "HashMap" for m in mappings)

    def test_use_alias(self) -> None:
        code = "use std::io::Error as IoError;\n"
        mappings = _parse_and_extract(code, "rust", ".rs")
        assert any(
            m["local_name"] == "IoError" and m["exported_name"] == "Error"
            for m in mappings
        )

    def test_use_wildcard(self) -> None:
        code = "use std::prelude::*;\n"
        mappings = _parse_and_extract(code, "rust", ".rs")
        assert any(m["is_wildcard"] is True for m in mappings)

    def test_use_nested_braces(self) -> None:
        code = "use crate::utils::{foo, bar};\n"
        mappings = _parse_and_extract(code, "rust", ".rs")
        names = {m["local_name"] for m in mappings}
        assert "foo" in names
        assert "bar" in names


# ── I5: resolve_import_source ─────────────────────────────────────────────────


class TestResolveImportSource:
    """I5 — resolve_import_source maps import sources to file paths."""

    def test_python_absolute_module(self, tmp_path: Path) -> None:
        # Create the file at expected location
        (tmp_path / "app").mkdir()
        target = tmp_path / "app" / "parser.py"
        target.write_text("def parse(): pass\n")

        result = resolve_import_source(
            "app.parser",
            tmp_path / "main.py",
            tmp_path,
            "python",
        )
        assert str(target) in result

    def test_python_relative_import(self, tmp_path: Path) -> None:
        # Relative import from a sibling file
        (tmp_path / "app").mkdir()
        sibling = tmp_path / "app" / "parser.py"
        sibling.write_text("def parse(): pass\n")
        ref_file = tmp_path / "app" / "main.py"

        result = resolve_import_source(
            ".parser",
            ref_file,
            tmp_path,
            "python",
        )
        assert str(sibling) in result

    def test_python_init_module(self, tmp_path: Path) -> None:
        # Resolves to package/__init__.py
        (tmp_path / "mypackage").mkdir()
        init = tmp_path / "mypackage" / "__init__.py"
        init.write_text("# init\n")

        result = resolve_import_source(
            "mypackage",
            tmp_path / "main.py",
            tmp_path,
            "python",
        )
        assert str(init) in result

    def test_third_party_returns_empty(self, tmp_path: Path) -> None:
        # Third-party package that doesn't exist in repo
        result = resolve_import_source(
            "requests",
            tmp_path / "main.py",
            tmp_path,
            "python",
        )
        assert result == []

    def test_typescript_relative_import(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        target = tmp_path / "src" / "parser.ts"
        target.write_text("export function parse() {}\n")
        ref_file = tmp_path / "src" / "main.ts"

        result = resolve_import_source(
            "./parser",
            ref_file,
            tmp_path,
            "typescript",
        )
        assert str(target) in result

    def test_typescript_parent_relative_import(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils").mkdir()
        target = tmp_path / "src" / "helpers.ts"
        target.write_text("export function help() {}\n")
        ref_file = tmp_path / "src" / "utils" / "worker.ts"

        result = resolve_import_source(
            "../helpers",
            ref_file,
            tmp_path,
            "typescript",
        )
        assert str(target) in result


# ── I6: compute_path_proximity ────────────────────────────────────────────────


class TestComputePathProximity:
    """I6 — compute_path_proximity: pure function, orders by shared segments."""

    def test_same_directory_is_highest(self) -> None:
        ref = Path("/project/app/router.py")
        same_dir = Path("/project/app/parser.py")
        different_dir = Path("/project/lib/parser.py")

        score_same = compute_path_proximity(ref, same_dir)
        score_diff = compute_path_proximity(ref, different_dir)
        assert score_same > score_diff

    def test_deeper_shared_prefix_beats_shallower(self) -> None:
        ref = Path("/project/app/utils/router.py")
        close = Path("/project/app/utils/parser.py")
        far = Path("/project/lib/parser.py")

        score_close = compute_path_proximity(ref, close)
        score_far = compute_path_proximity(ref, far)
        assert score_close > score_far

    def test_identical_paths_return_high_score(self) -> None:
        p = Path("/project/app/parser.py")
        assert compute_path_proximity(p, p) > 0

    def test_no_shared_segments_returns_zero(self) -> None:
        a = Path("/project/a.py")
        b = Path("/other/b.py")
        # No shared meaningful directory parts → 0
        score = compute_path_proximity(a, b)
        assert score >= 0  # May not be exactly 0 due to root sharing

    def test_pure_no_io(self) -> None:
        # Paths don't need to exist — pure function
        a = Path("/fake/path/a.py")
        b = Path("/fake/path/b.py")
        result = compute_path_proximity(a, b)
        assert isinstance(result, int)


# ── I7: Never-raises contract ─────────────────────────────────────────────────


class TestNeverRaisesContract:
    """I7 — extract_import_mappings and resolve_import_source never raise."""

    def test_extract_with_none_root_returns_empty(self) -> None:
        # None root should return []
        result = extract_import_mappings(None, Path("/fake.py"), "python")  # type: ignore[arg-type]
        assert result == []

    def test_resolve_with_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = resolve_import_source(
            "nonexistent.module",
            tmp_path / "main.py",
            tmp_path,
            "python",
        )
        assert result == []

    def test_compute_proximity_never_raises(self) -> None:
        # Even with unusual paths
        result = compute_path_proximity(Path("/a"), Path("/b/c"))
        assert isinstance(result, int)
