"""Symbol and edge extraction from tree-sitter AST nodes.

Pure functions: take AST node + metadata, return structured data.
No I/O, no DB, no side effects.
"""

from pathlib import Path
from typing import TypedDict


class Symbol(TypedDict):
    name: str
    kind: str          # 'function' | 'class' | 'method' | 'interface' | 'type'
    file: str          # str(path) — resolved at call time
    start_line: int
    end_line: int
    docstring: str | None


class Edge(TypedDict):
    source: str        # Symbol name of caller / importer
    target: str        # Symbol name of callee / importee
    kind: str          # 'import' | 'call'
    file: str
    line: int


# Implementations: see IMPLEMENTATION_PLAN.md steps 4.1 and 4.2


def extract_symbols(node: object, language: str, filepath: Path) -> list[Symbol]:
    """Extract all symbol definitions from an AST node."""
    raise NotImplementedError("Implement in step 4.1")


def extract_edges(node: object, language: str, filepath: Path) -> list[Edge]:
    """Extract import and call edges from an AST node."""
    raise NotImplementedError("Implement in step 4.2")
