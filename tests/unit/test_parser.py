"""Tests for seam/indexer/parser.py.

TDD: These tests are written before the implementation. They verify:
- Valid .py file returns a non-None root node
- Valid .ts file returns a non-None root node
- Binary file returns None (null byte in first 1KB)
- Oversized file returns None (via monkeypatching config.SEAM_MAX_FILE_BYTES)
- Missing file returns None
"""

from pathlib import Path

import pytest

from seam.indexer.parser import parse_javascript, parse_python, parse_typescript

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_PY = FIXTURES_DIR / "sample.py"
SAMPLE_TS = FIXTURES_DIR / "sample.ts"


class TestParsePython:
    def test_valid_python_file_returns_node(self) -> None:
        """Valid .py file must return a non-None AST root node."""
        node = parse_python(SAMPLE_PY)
        assert node is not None

    def test_valid_python_node_has_children(self) -> None:
        """Root node from a real file should have at least one child."""
        node = parse_python(SAMPLE_PY)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_missing_file_returns_none(self) -> None:
        """Non-existent file must return None (not raise)."""
        node = parse_python(Path("/nonexistent/path/file.py"))
        assert node is None

    def test_binary_file_returns_none(self, tmp_path: Path) -> None:
        """File with null byte in first 1KB must return None (binary guard)."""
        binary_file = tmp_path / "binary.py"
        # Write a file that starts with valid Python but has a null byte early
        binary_file.write_bytes(b"x = 1\x00rest of file")
        node = parse_python(binary_file)
        assert node is None

    def test_oversized_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File larger than SEAM_MAX_FILE_BYTES must return None."""
        import seam.config as config

        # Limit to 10 bytes so our test file exceeds it
        monkeypatch.setattr(config, "SEAM_MAX_FILE_BYTES", 10)
        big_file = tmp_path / "big.py"
        big_file.write_text("x = 1  # this is more than 10 bytes for sure")
        node = parse_python(big_file)
        assert node is None


class TestParseTypeScript:
    def test_valid_typescript_file_returns_node(self) -> None:
        """Valid .ts file must return a non-None AST root node."""
        node = parse_typescript(SAMPLE_TS)
        assert node is not None

    def test_valid_typescript_node_has_children(self) -> None:
        """Root node from a real .ts file should have at least one child."""
        node = parse_typescript(SAMPLE_TS)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_missing_file_returns_none(self) -> None:
        """Non-existent .ts file must return None (not raise)."""
        node = parse_typescript(Path("/nonexistent/path/file.ts"))
        assert node is None

    def test_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .ts file must return None."""
        binary_file = tmp_path / "binary.ts"
        binary_file.write_bytes(b"const x = 1;\x00rest")
        node = parse_typescript(binary_file)
        assert node is None

    def test_oversized_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Oversized .ts file must return None."""
        import seam.config as config

        monkeypatch.setattr(config, "SEAM_MAX_FILE_BYTES", 10)
        big_file = tmp_path / "big.ts"
        big_file.write_text("const x = 1;  // more than 10 bytes")
        node = parse_typescript(big_file)
        assert node is None


class TestParseJavaScript:
    def test_valid_javascript_returns_node(self, tmp_path: Path) -> None:
        """Valid .js file must return a non-None root node (uses TSX grammar)."""
        js_file = tmp_path / "sample.js"
        js_file.write_text("function foo() { return 1; }")
        node = parse_javascript(js_file)
        assert node is not None

    def test_missing_file_returns_none(self) -> None:
        """Non-existent .js file must return None."""
        node = parse_javascript(Path("/nonexistent/path/file.js"))
        assert node is None
