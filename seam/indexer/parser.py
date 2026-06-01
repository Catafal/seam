"""Tree-sitter parsing layer — one function per supported language.

Returns raw tree-sitter Nodes for graph.py to interpret.
Never raises on parse errors; returns None instead.
"""

from pathlib import Path

# Implementations: see IMPLEMENTATION_PLAN.md steps 3.1 and 3.2


def parse_python(path: Path) -> object | None:
    """Parse a Python source file. Returns tree-sitter Node or None on error."""
    raise NotImplementedError("Implement in step 3.1")


def parse_typescript(path: Path) -> object | None:
    """Parse a TypeScript/TSX source file. Returns tree-sitter Node or None on error."""
    raise NotImplementedError("Implement in step 3.2")


def parse_javascript(path: Path) -> object | None:
    """Parse a JavaScript/MJS/CJS source file. Returns tree-sitter Node or None on error."""
    raise NotImplementedError("Implement in step 3.2")
