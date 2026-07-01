"""Symbol and edge extraction from tree-sitter AST nodes.

Pure functions: take AST node + metadata, return structured data.
No I/O, no DB, no side effects.

LAYER: sits between graph_common/graph_go_rust (below) and pipeline.py/db.py (above).
  - Imports shared types and helpers from graph_common (leaf — no seam deps).
  - Imports Go/Rust extractors from graph_go_rust (which also imports graph_common only).
  - Re-exports all public TypedDicts so callers can continue using:
        from seam.indexer.graph import Symbol, Edge, Comment, Confidence

Contract (evolved from Phase-0 FROZEN — see docs/CONTRACT.md):
  Symbol fields: name, kind, file, start_line, end_line, docstring
  Edge fields:   source, target, kind, file, line, confidence (Phase 1 addition)

Confidence — two-layer model:
  Layer 1 — stored column (same-file scope, index time):
    Computed by _resolve_confidence_multi against the symbol list from the SAME FILE.
    EXTRACTED  — target resolves to exactly one symbol in the same-file set.
    AMBIGUOUS  — target matches more than one symbol in the same-file set.
    INFERRED   — target not in the same-file set (heuristic / external).
    This is a cheap debugging hint only — NOT authoritative for cross-file edges.

  Layer 2 — read-time whole-index resolution (authoritative, see seam/analysis/confidence.py):
    At query time, confidence is re-resolved against the full symbol index.
    EXTRACTED  — target name is unique across the ENTIRE index.
    AMBIGUOUS  — target name is shared by more than one indexed symbol.
    INFERRED   — target name is not in the index at all (external, stdlib, dynamic).
    This overrides the stored column value; no schema change is needed.
"""

import logging
from pathlib import Path

from tree_sitter import Node

# All seam imports in one block (alphabetically ordered, as required by ruff/isort).
# Layer structure:
#   graph_common      (leaf — no seam deps)
#   graph_scope_infer (leaf — imports graph_common only; Tier B B4 receiver-type inference)
#   graph_c / graph_cpp / graph_go / graph_rust / graph_java / graph_csharp /
#   graph_python / graph_typescript (language leaves — import graph_common only; no cycle)
#   graph_c_cpp / graph_go_rust / graph_java_csharp (thin re-exporters)
#   graph_php / graph_ruby / graph_swift (language families)
#   graph.py          (this file — dispatcher only; imports all of the above)
from seam.indexer.graph_c_cpp import (
    _extract_comments_c,
    _extract_comments_cpp,
    _extract_edges_c,
    _extract_edges_cpp,
    _extract_symbols_c,
    _extract_symbols_cpp,
)
from seam.indexer.graph_common import (
    SEMANTIC_MARKERS,
    Comment,
    Confidence,
    ConfigMetadata,
    Edge,
    ResourceMetadata,
    RouteMetadata,
    Symbol,
)
from seam.indexer.graph_go_rust import (
    _extract_comments_go,
    _extract_comments_rust,
    _extract_edges_go,
    _extract_edges_rust,
    _extract_symbols_go,
    _extract_symbols_rust,
)
from seam.indexer.graph_java_csharp import (
    _extract_comments_csharp,
    _extract_comments_java,
    _extract_edges_csharp,
    _extract_edges_java,
    _extract_symbols_csharp,
    _extract_symbols_java,
)
from seam.indexer.graph_php import (
    _extract_comments_php,
    _extract_edges_php,
    _extract_symbols_php,
)
from seam.indexer.graph_python import (
    _extract_comments_python,
    _extract_edges_python,
    _extract_symbols_python,
)
from seam.indexer.graph_ruby import (
    _extract_comments_ruby,
    _extract_edges_ruby,
    _extract_symbols_ruby,
)
from seam.indexer.graph_swift import (
    _extract_comments_swift,
    _extract_edges_swift,
    _extract_symbols_swift,
)
from seam.indexer.graph_typescript import (
    _extract_comments_typescript,
    _extract_edges_typescript,
    _extract_symbols_typescript,
)

# Keep these names visible for `from seam.indexer.graph import ...` callers.
__all__ = [
    "Comment",
    "ConfigMetadata",
    "Confidence",
    "Edge",
    "ResourceMetadata",
    "RouteMetadata",
    "Symbol",
    "SEMANTIC_MARKERS",
    "extract_comments",
    "extract_edges",
    "extract_symbols",
]

logger = logging.getLogger(__name__)


# ── Internal confidence helper ─────────────────────────────────────────────────


def _resolve_confidence_multi(target_name: str, symbol_name_counts: dict[str, int]) -> Confidence:
    """Resolve confidence using a same-file name->count mapping.

    SCOPE: same-file only — this is a lower-bound hint stored on the edge.
    The authoritative whole-index resolution lives in seam/analysis/confidence.py.

    Args:
        target_name:        The edge target name to resolve.
        symbol_name_counts: Mapping of symbol_name -> occurrence count in THIS file only.
    """
    count = symbol_name_counts.get(target_name, 0)
    if count == 1:
        return "EXTRACTED"
    if count > 1:
        return "AMBIGUOUS"
    return "INFERRED"


# NOTE: Python extraction (_extract_symbols_python, _extract_edges_python) lives in
# graph_python.py. TypeScript/JS extraction lives in graph_typescript.py.
# Both are imported at the top of this file. graph.py is now a pure dispatcher.

# ── Public API ─────────────────────────────────────────────────────────────────


def extract_symbols(node: object, language: str, filepath: Path) -> list[Symbol]:
    """Extract all symbol definitions from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript' | 'go' | 'rust' |
                  'java' | 'csharp' | 'ruby' | 'c' | 'cpp' | 'php'
        filepath: resolved absolute Path to the source file

    Returns list of Symbol TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            return _extract_symbols_python(node, filepath)
        elif language in ("typescript", "javascript"):
            return _extract_symbols_typescript(node, filepath)
        elif language == "go":
            return _extract_symbols_go(node, filepath)
        elif language == "rust":
            return _extract_symbols_rust(node, filepath)
        # Phase 9 — new languages (stubs return []; family agents fill logic)
        elif language == "java":
            return _extract_symbols_java(node, filepath)
        elif language == "csharp":
            return _extract_symbols_csharp(node, filepath)
        elif language == "ruby":
            return _extract_symbols_ruby(node, filepath)
        elif language == "c":
            return _extract_symbols_c(node, filepath)
        elif language == "cpp":
            return _extract_symbols_cpp(node, filepath)
        elif language == "php":
            return _extract_symbols_php(node, filepath)
        # Phase 10 — Swift
        elif language == "swift":
            return _extract_symbols_swift(node, filepath)
    except Exception:  # noqa: BLE001
        # WHY log: a silent except here would make a grammar-version break
        # or a bad language string completely invisible. Logging at debug
        # preserves the never-raise contract while surfacing the root cause.
        logger.debug(
            "extract_symbols: unhandled exception for language=%r file=%s",
            language,
            filepath,
            exc_info=True,
        )
        return []
    return []


def extract_comments(node: object, language: str, filepath: Path) -> list[Comment]:
    """Extract semantic comments from an AST root node.

    Only WHY/HACK/NOTE/TODO/FIXME-tagged comments are returned; plain comments
    are silently ignored. The marker is normalized to UPPERCASE.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path).
        language: 'python' | 'typescript' | 'javascript' | 'go' | 'rust' |
                  'java' | 'csharp' | 'ruby' | 'c' | 'cpp' | 'php'
        filepath: resolved absolute Path to the source file.

    Returns list of Comment TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            return _extract_comments_python(node, filepath)
        elif language in ("typescript", "javascript"):
            return _extract_comments_typescript(node, filepath)
        elif language == "go":
            return _extract_comments_go(node, filepath)
        elif language == "rust":
            return _extract_comments_rust(node, filepath)
        # Phase 9 — new languages (stubs return []; family agents fill logic)
        elif language == "java":
            return _extract_comments_java(node, filepath)
        elif language == "csharp":
            return _extract_comments_csharp(node, filepath)
        elif language == "ruby":
            return _extract_comments_ruby(node, filepath)
        elif language == "c":
            return _extract_comments_c(node, filepath)
        elif language == "cpp":
            return _extract_comments_cpp(node, filepath)
        elif language == "php":
            return _extract_comments_php(node, filepath)
        # Phase 10 — Swift
        elif language == "swift":
            return _extract_comments_swift(node, filepath)
    except Exception:  # noqa: BLE001
        logger.debug(
            "extract_comments: unhandled exception for language=%r file=%s",
            language,
            filepath,
            exc_info=True,
        )
        return []
    return []


def extract_edges(
    node: object,
    language: str,
    filepath: Path,
    symbols: list[Symbol] | None = None,
) -> list[Edge]:
    """Extract import and call edges from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript' | 'go' | 'rust'
        filepath: resolved absolute Path to the source file
        symbols:  Optional list of symbols extracted from the same file.
                  When provided, each edge's confidence is resolved:
                    EXTRACTED  — target name matches exactly one symbol in the list
                    AMBIGUOUS  — target name matches more than one symbol
                    INFERRED   — target not in the symbol list (default/heuristic)
                  When omitted, all edges carry confidence='INFERRED'.

    Returns list of Edge TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            raw_edges = _extract_edges_python(node, filepath)
        elif language in ("typescript", "javascript"):
            raw_edges = _extract_edges_typescript(node, filepath)
        elif language == "go":
            raw_edges = _extract_edges_go(node, filepath)
        elif language == "rust":
            raw_edges = _extract_edges_rust(node, filepath)
        # Phase 9 — new languages (stubs return []; family agents fill logic)
        elif language == "java":
            raw_edges = _extract_edges_java(node, filepath)
        elif language == "csharp":
            raw_edges = _extract_edges_csharp(node, filepath)
        elif language == "ruby":
            raw_edges = _extract_edges_ruby(node, filepath)
        elif language == "c":
            raw_edges = _extract_edges_c(node, filepath)
        elif language == "cpp":
            raw_edges = _extract_edges_cpp(node, filepath)
        elif language == "php":
            raw_edges = _extract_edges_php(node, filepath)
        # Phase 10 — Swift
        elif language == "swift":
            raw_edges = _extract_edges_swift(node, filepath)
        else:
            return []

        if symbols is None:
            return raw_edges

        # Build a name-count map from the symbol list to detect same-file duplicates.
        name_counts: dict[str, int] = {}
        for sym in symbols:
            name_counts[sym["name"]] = name_counts.get(sym["name"], 0) + 1

        # Annotate each edge's confidence based on resolution against the symbol set.
        for edge in raw_edges:
            edge["confidence"] = _resolve_confidence_multi(edge["target"], name_counts)
        return raw_edges

    except Exception:  # noqa: BLE001
        logger.debug(
            "extract_edges: unhandled exception for language=%r file=%s",
            language,
            filepath,
            exc_info=True,
        )
        return []
