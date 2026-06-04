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

import seam.config as config

# ── Phase 9 extractors — top-level imports, no cycle ─────────────────────────
# Each family module imports only graph_common (leaf), so the import chain is
# graph.py → graph_java_csharp/graph_c_cpp/graph_ruby/graph_php → graph_common (leaf).
# No circular dependencies.
from seam.indexer.graph_c_cpp import (
    _extract_comments_c,
    _extract_comments_cpp,
    _extract_edges_c,
    _extract_edges_cpp,
    _extract_symbols_c,
    _extract_symbols_cpp,
)

# ── Re-export shared primitives from the leaf module ──────────────────────────
# graph_common is the leaf (no seam deps); importing from it here does not create
# a cycle. All imports are at module top — no deferred/in-function imports.
from seam.indexer.graph_common import (
    SEMANTIC_MARKERS,
    Comment,
    Confidence,
    Edge,
    Symbol,
    _base_type_name,
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _node_name,
    _text,
)

# ── Go/Rust extractors — top-level import, no cycle ──────────────────────────
# graph_go_rust only imports from graph_common (the leaf). It does NOT import from
# this file, so the import here is one-directional: graph.py → graph_go_rust.py.
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
from seam.indexer.graph_ruby import (
    _extract_comments_ruby,
    _extract_edges_ruby,
    _extract_symbols_ruby,
)

# Phase 10 — Swift extractor (leaf imports graph_common only)
from seam.indexer.graph_swift import (
    _extract_comments_swift,
    _extract_edges_swift,
    _extract_symbols_swift,
)

# signatures.py is a leaf (no seam deps) so importing it here does not create a cycle.
from seam.indexer.signatures import extract_node_fields

# Keep these names visible for `from seam.indexer.graph import ...` callers.
__all__ = [
    "Comment",
    "Confidence",
    "Edge",
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


# ── Python docstring extractor ─────────────────────────────────────────────────


def _py_docstring(func_or_class_node: Node) -> str | None:
    """Extract Python docstring: first expression_statement(string) in body.

    Returns the docstring CONTENT (without surrounding quotes), or None.
    Uses tree-sitter `string_content` child node — avoids stripping legitimate
    leading/trailing quote characters from the docstring text.
    """
    body = func_or_class_node.child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    # Must be an expression_statement whose first child is a string literal.
    if first.type != "expression_statement" or not first.children:
        return None
    expr = first.children[0]
    if expr.type != "string":
        return None
    # tree-sitter Python string nodes: [string_start, string_content, string_end].
    for child in expr.children:
        if child.type == "string_content":
            return _text(child).strip()
    return None  # empty string literal has no string_content


# ── TypeScript/JS JSDoc extractor ──────────────────────────────────────────────


def _ts_jsdoc(symbol_node: Node) -> str | None:
    """Extract leading JSDoc comment from the previous sibling node.

    tree-sitter emits /** ... */ blocks as 'comment' nodes immediately
    before the declaration. Only /** blocks qualify as JSDoc.
    """
    prev = symbol_node.prev_sibling
    if prev is None or prev.type != "comment":
        return None
    comment_text = _text(prev)
    if not comment_text.startswith("/**"):
        return None
    return comment_text


# ── Python extraction ──────────────────────────────────────────────────────────


def _extract_symbols_python(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Python AST and extract function, class, and method symbols."""
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, class_name: str | None = None) -> None:
        """Recursively walk AST, tracking class context for method qualification."""
        if node.type == "function_definition":
            name = _node_name(node)
            if name:
                kind = "method" if class_name else "function"
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _py_docstring(node)
                # Phase 4: pass qualified name from our scope-walker so signatures.py doesn't
                # need to re-resolve it independently — single source of qualified-name truth.
                fields = extract_node_fields(
                    node,
                    "python",
                    qualified_name=qualified,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        qualified,
                        kind,
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=qualified,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, None)  # nested def is local, not a method

        elif node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition and definition.type == "function_definition":
                name = _node_name(definition)
                if name:
                    kind = "method" if class_name else "function"
                    qualified = f"{class_name}.{name}" if class_name else name
                    doc = _py_docstring(definition)
                    # Pass the decorated_definition node (not the inner definition) so
                    # signatures.py can traverse its children for @decorator nodes.
                    fields = extract_node_fields(
                        node,
                        "python",
                        qualified_name=qualified,
                        max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                    )
                    symbols.append(
                        _make_symbol(
                            qualified,
                            kind,
                            file_str,
                            node,
                            doc,
                            signature=fields["signature"],
                            decorators=fields["decorators"],
                            is_exported=fields["is_exported"],
                            visibility=fields["visibility"],
                            qualified_name=qualified,
                        )
                    )
                    body = definition.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            _walk(child, None)
            elif definition and definition.type == "class_definition":
                name = _node_name(definition)
                if name:
                    doc = _py_docstring(definition)
                    # Pass decorated_definition node so class decorators are captured.
                    fields = extract_node_fields(
                        node,
                        "python",
                        qualified_name=name,
                        max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                    )
                    symbols.append(
                        _make_symbol(
                            name,
                            "class",
                            file_str,
                            node,
                            doc,
                            signature=fields["signature"],
                            decorators=fields["decorators"],
                            is_exported=fields["is_exported"],
                            visibility=fields["visibility"],
                            qualified_name=name,
                        )
                    )
                    body = definition.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            _walk(child, name)

        elif node.type == "class_definition":
            name = _node_name(node)
            if name:
                doc = _py_docstring(node)
                fields = extract_node_fields(
                    node,
                    "python",
                    qualified_name=name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        name,
                        "class",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=name,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, name)
        else:
            for child in node.children:
                _walk(child, class_name)

    for child in root.children:
        _walk(child, None)

    return symbols


def _extract_edges_python(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Python AST.

    Import heuristic:
      - import X     → target = 'X' (dotted_name as-is)
      - from X import Y → target = 'Y' for each name after 'import' keyword

    Call heuristic:
      - call node where function is a bare identifier (`foo()`) → target = identifier
      - call node where function is an attribute (`mod.fn()`, `self.m()`, `a.b.c()`)
        → target = the RIGHTMOST identifier (the bare name the declared symbol is
        stored under — the edge graph is name-keyed / homonym-collapsed). This
        captures import-alias and method calls that bare-identifier-only matching
        dropped (e.g. `fts.rescore(...)`).
      - source = nearest enclosing function/method (skip if none)
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"

    def _walk(node: Node) -> None:
        if emit_inheritance and node.type == "class_definition":
            # Python has no syntactic class/interface split — every base in the
            # `superclasses` argument_list is an 'extends' edge (subclass → base).
            cls_name = _node_name(node)
            bases = node.child_by_field_name("superclasses")
            if cls_name and bases is not None:
                for base_child in bases.named_children:
                    base_target = _base_type_name(base_child)
                    if base_target:
                        edges.append(
                            Edge(
                                source=cls_name,
                                target=base_target,
                                kind="extends",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                            )
                        )
            # Fall through to recurse into the class body for nested calls/classes.

        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    target_node = child.child_by_field_name("name") or child
                    edges.append(
                        Edge(
                            source=file_stem,
                            target=_text(target_node),
                            kind="import",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                        )
                    )

        elif node.type == "import_from_statement":
            found_import_kw = False
            for child in node.children:
                if child.type == "import":
                    found_import_kw = True
                    continue
                if found_import_kw:
                    if child.type in ("dotted_name", "identifier"):
                        edges.append(
                            Edge(
                                source=file_stem,
                                target=_text(child),
                                kind="import",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                            )
                        )
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            edges.append(
                                Edge(
                                    source=file_stem,
                                    target=_text(name_node),
                                    kind="import",
                                    file=file_str,
                                    line=node.start_point[0] + 1,
                                    confidence="INFERRED",
                                )
                            )

        elif node.type == "call":
            func_child = node.child_by_field_name("function")
            # Resolve the callee NAME node. Two shapes:
            #   identifier  → bare call `foo()`            → the identifier itself
            #   attribute   → `mod.fn()` / `self.m()` / `a.b.c()`
            #                 → the 'attribute' field = the rightmost identifier.
            # Attribute calls were previously dropped, hiding import-alias and
            # method call edges (e.g. `fts.rescore(...)`) from impact/callers.
            callee_node: Node | None = None
            if func_child and func_child.type == "identifier":
                callee_node = func_child
            elif func_child and func_child.type == "attribute":
                callee_node = func_child.child_by_field_name("attribute")

            if callee_node is not None and callee_node.type == "identifier":
                source = _find_enclosing_function(node, "python")
                if source is not None:
                    edges.append(
                        Edge(
                            source=source,
                            target=_text(callee_node),
                            kind="call",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                        )
                    )

        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return edges


# ── TypeScript / JavaScript extraction ────────────────────────────────────────


def _extract_symbols_typescript(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a TypeScript/TSX AST and extract all symbol types."""
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, class_name: str | None = None) -> None:
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                kind = "method" if class_name else "function"
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=qualified,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        qualified,
                        kind,
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=qualified,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, None)

        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=qualified,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        qualified,
                        "method",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=qualified,
                    )
                )

        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = _text(name_node)
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=cls_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        cls_name,
                        "class",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=cls_name,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, cls_name)

        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        name,
                        "interface",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=name,
                    )
                )

        elif node.type == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        name,
                        "type",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=name,
                    )
                )

        else:
            for child in node.children:
                _walk(child, class_name)

    for child in root.children:
        _walk(child, None)

    return symbols


def _extract_edges_typescript(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a TypeScript/TSX AST.

    Import heuristic:
      - default import X          → target = 'X'
      - named import { X, Y }     → one edge per import_specifier (real name)
      - aliased import { a as b } → target = 'a' (real name, not alias)
      - namespace import * as ns  → target = 'ns'
    Call heuristic: bare identifier call_expression → source/target edge.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"

    def _emit_ts_inheritance(node: Node) -> None:
        """Emit extends/implements edges for a TS class_declaration or interface_declaration.

        class_declaration → class_heritage → extends_clause (extends) + implements_clause.
        interface_declaration → extends_type_clause (interface inheritance → 'extends').
        Each base name is normalized to a bare type name (generic args stripped).
        """
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        src_name = _text(name_node)
        line = node.start_point[0] + 1

        def _emit_from_clause(clause: Node, kind: str) -> None:
            for c in clause.named_children:
                target = _base_type_name(c)
                if target:
                    edges.append(
                        Edge(
                            source=src_name,
                            target=target,
                            kind=kind,
                            file=file_str,
                            line=line,
                            confidence="INFERRED",
                        )
                    )

        for child in node.children:
            if child.type == "class_heritage":
                for clause in child.children:
                    if clause.type == "extends_clause":
                        _emit_from_clause(clause, "extends")
                    elif clause.type == "implements_clause":
                        _emit_from_clause(clause, "implements")
            elif child.type == "extends_type_clause":
                # interface A extends B, C — interface inheritance is 'extends'.
                _emit_from_clause(child, "extends")

    def _walk(node: Node) -> None:
        if emit_inheritance and node.type in (
            "class_declaration",
            "interface_declaration",
        ):
            _emit_ts_inheritance(node)
            # Fall through to recurse for nested calls.

        if node.type == "import_statement":
            line = node.start_point[0] + 1
            clause = None
            for child in node.children:
                if child.type == "import_clause":
                    clause = child
                    break
            if clause:
                for clause_child in clause.children:
                    if clause_child.type == "identifier":
                        edges.append(
                            Edge(
                                source=file_stem,
                                target=_text(clause_child),
                                kind="import",
                                file=file_str,
                                line=line,
                                confidence="INFERRED",
                            )
                        )
                    elif clause_child.type == "namespace_import":
                        for ns_child in clause_child.children:
                            if ns_child.type == "identifier":
                                edges.append(
                                    Edge(
                                        source=file_stem,
                                        target=_text(ns_child),
                                        kind="import",
                                        file=file_str,
                                        line=line,
                                        confidence="INFERRED",
                                    )
                                )
                                break
                    elif clause_child.type == "named_imports":
                        for spec in clause_child.children:
                            if spec.type == "import_specifier":
                                name_node = spec.child_by_field_name("name")
                                if name_node is None and spec.children:
                                    name_node = spec.children[0]
                                if name_node:
                                    edges.append(
                                        Edge(
                                            source=file_stem,
                                            target=_text(name_node),
                                            kind="import",
                                            file=file_str,
                                            line=line,
                                            confidence="INFERRED",
                                        )
                                    )

        elif node.type == "call_expression":
            func_child = node.child_by_field_name("function")
            if func_child and func_child.type == "identifier":
                source = _find_enclosing_function(node, "typescript")
                if source is not None:
                    edges.append(
                        Edge(
                            source=source,
                            target=_text(func_child),
                            kind="call",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                        )
                    )

        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return edges


# ── Comment extraction ────────────────────────────────────────────────────────


def _strip_py_comment(raw: str) -> str:
    """Strip the leading '#' delimiter and whitespace from a Python comment."""
    return raw.lstrip("#").strip()


def _strip_ts_line_comment(raw: str) -> str:
    """Strip the leading '//' delimiter and whitespace from a TS/JS line comment."""
    return raw.lstrip("/").strip()


def _extract_comments_python(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Python AST and collect matched semantic comment nodes."""
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "comment":
            raw = _text(node)
            body = _strip_py_comment(raw)
            result = _match_marker(body)
            if result is not None:
                marker, text = result
                comments.append(
                    Comment(
                        marker=marker,
                        text=text,
                        line=node.start_point[0] + 1,
                    )
                )
        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments


def _extract_comments_typescript(root: Node, filepath: Path) -> list[Comment]:
    """Walk a TypeScript/JS AST and collect matched semantic comment nodes.

    Handles both // line comments and /* */ block comments. For block comments,
    EVERY line is scanned so a marker on line 2+ of a JSDoc-style block is
    detected, with the stored line number pointing at the marker's real line.
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "comment":
            raw = _text(node)
            base_row = node.start_point[0] + 1
            if raw.startswith("/*"):
                for offset, body in _block_comment_lines(raw):
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row + offset))
            else:
                body = _strip_ts_line_comment(raw) if raw.startswith("//") else raw.strip()
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row))
        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments


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
