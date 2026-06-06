"""C symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) — never from graph.py.

LAYERING:
    graph_common  (leaf — no seam deps)
         ↑
    graph_c       (this file)
         ↑
    graph_c_cpp   (thin re-exporter; graph.py imports from there)

WHY split from graph_c_cpp.py: graph_c_cpp.py exceeded 1000 lines after Tier B additions.
C and C++ are now each large enough to stand alone.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default.

GRAMMAR FACTS (verified by dumping AST from real fixtures):
  function_definition: fields 'type', 'declarator' (function_declarator whose
    'declarator' field is an identifier = function name), 'body'.
  storage_class_specifier "static" → file-local → visibility='private', is_exported=False.
  struct_specifier / union_specifier: field 'name' (type_identifier) → kind='class'.
  enum_specifier: field 'name' → kind='type'.
  type_definition: last type_identifier child is the typedef alias → kind='type'.
  preproc_include: field 'path' — string_literal (local) or system_lib_string (system).
  call_expression: field 'function' = identifier → bare call target.
  comment: covers both // and /* */ comments.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config
from seam.indexer.graph_common import (
    Comment,
    Edge,
    Symbol,
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _text,
)
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)


# ── Shared C/C++ helpers (also imported by graph_cpp.py) ──────────────────────


def _c_doc_comment(decl_node: Node) -> str | None:
    """Capture a C/C++ doc comment above a declaration.

    Strategy:
      1. If the immediately preceding sibling is a /* */ block comment that
         starts with '/**', return its cleaned content (Javadoc-style).
      2. Otherwise, collect contiguous // line comments immediately above the
         declaration (row-adjacent, no blank lines).
    """
    try:
        prev = decl_node.prev_sibling
        if prev is None:
            return None

        if prev.type == "comment":
            raw = _text(prev)
            if raw.startswith("/**"):
                block_lines: list[str] = []
                for line in raw.splitlines():
                    s = line.strip()
                    if s.startswith("/*"):
                        s = s[2:]
                    if s.endswith("*/"):
                        s = s[:-2]
                    s = s.strip().lstrip("*").strip()
                    if s:
                        block_lines.append(s)
                return "\n".join(block_lines) if block_lines else None

            if raw.startswith("//"):
                lines: list[str] = []
                current: Node | None = prev
                while current is not None and current.type == "comment":
                    c_raw = _text(current)
                    if not c_raw.startswith("//"):
                        break
                    next_node = current.next_sibling
                    if next_node is not None:
                        end_row = current.end_point[0]
                        next_start = next_node.start_point[0]
                        if end_row + 1 != next_start:
                            break
                    body = c_raw[2:].strip()
                    lines.append(body)
                    current = current.prev_sibling
                if not lines:
                    return None
                return "\n".join(reversed(lines))
    except Exception:  # noqa: BLE001
        pass
    return None


def _handle_c_include(node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Emit an import edge from a C/C++ preproc_include node.

    Both local (#include "x.h") and system (#include <x.h>) includes produce edges.
    The target is the header filename stem (e.g. "utils" from "utils.h" or <stdio.h>).
    """
    try:
        line = node.start_point[0] + 1
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return

        if path_node.type == "string_literal":
            content = None
            for child in path_node.children:
                if child.type == "string_content":
                    content = _text(child)
                    break
            if content is None:
                content = _text(path_node).strip('"')
            target = Path(content).stem if content else None

        elif path_node.type == "system_lib_string":
            raw = _text(path_node).strip("<>")
            target = Path(raw).stem if raw else None

        else:
            return

        if target:
            edges.append(
                Edge(
                    source=file_stem,
                    target=target,
                    kind="import",
                    file=file_str,
                    line=line,
                    confidence="INFERRED",
                    receiver=None,
                )
            )
    except Exception:  # noqa: BLE001
        logger.debug("_handle_c_include: failed for node at row %d", node.start_point[0])


# ── C extraction ───────────────────────────────────────────────────────────────


def _c_function_name(func_def: Node) -> str | None:
    """Extract the function name from a C function_definition node.

    The name lives inside: function_definition → function_declarator → identifier.
    """
    try:
        declarator = func_def.child_by_field_name("declarator")
        if declarator is None:
            return None
        name_node = declarator.child_by_field_name("declarator")
        if name_node is not None and name_node.type == "identifier":
            return _text(name_node)
    except Exception:  # noqa: BLE001
        pass
    return None


def _c_is_static(func_def: Node) -> bool:
    """Return True if a C function_definition has a storage_class_specifier 'static'."""
    try:
        for child in func_def.children:
            if child.type == "storage_class_specifier":
                if _text(child).strip() == "static":
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _extract_symbols_c(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a C AST and extract function, struct, union, enum, and typedef symbols.

    Kind mapping (per Phase 9 spec):
        function_definition            → function
        struct_specifier (named)       → class
        union_specifier (named)        → class
        enum_specifier (named)         → type
        type_definition (typedef)      → type
    """
    try:
        symbols: list[Symbol] = []
        file_str = str(filepath)

        def _walk(node: Node) -> None:
            if node.type == "function_definition":
                _handle_c_function(node, file_str, symbols)

            elif node.type == "struct_specifier":
                _handle_c_aggregate(node, "class", file_str, symbols)

            elif node.type == "union_specifier":
                _handle_c_aggregate(node, "class", file_str, symbols)

            elif node.type == "enum_specifier":
                _handle_c_aggregate(node, "type", file_str, symbols)

            elif node.type == "type_definition":
                _handle_c_typedef(node, file_str, symbols)

            else:
                for child in node.children:
                    _walk(child)

        for child in root.children:
            _walk(child)

        return symbols
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_symbols_c: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []


def _handle_c_function(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a C function symbol from a function_definition node."""
    try:
        name = _c_function_name(node)
        if not name:
            return
        doc = _c_doc_comment(node)
        fields = extract_node_fields(
            node,
            "c",
            qualified_name=name,
            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
        )
        symbols.append(
            _make_symbol(
                name,
                "function",
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
    except Exception:  # noqa: BLE001
        logger.debug("_handle_c_function: failed for node at row %d", node.start_point[0])


def _handle_c_aggregate(node: Node, kind: str, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a C struct/union/enum symbol from a named specifier node."""
    try:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node)
        if not name:
            return
        doc = _c_doc_comment(node)
        fields = extract_node_fields(
            node,
            "c",
            qualified_name=name,
            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
        )
        symbols.append(
            _make_symbol(
                name,
                kind,
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
    except Exception:  # noqa: BLE001
        logger.debug("_handle_c_aggregate: failed for node at row %d", node.start_point[0])


def _handle_c_typedef(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a C typedef symbol from a type_definition node."""
    try:
        declarator = node.child_by_field_name("declarator")
        if declarator is not None and declarator.type == "type_identifier":
            name = _text(declarator)
        else:
            name = None
            for child in reversed(node.children):
                if child.type == "type_identifier":
                    name = _text(child)
                    break
        if not name:
            return
        doc = _c_doc_comment(node)
        fields = extract_node_fields(
            node,
            "c",
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
    except Exception:  # noqa: BLE001
        logger.debug("_handle_c_typedef: failed for node at row %d", node.start_point[0])


def _extract_edges_c(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a C AST.

    Import heuristic:
        #include "x.h"   → target = stem of "x.h" (e.g. 'x')
        #include <x.h>   → target = stem of 'x.h' (e.g. 'x') [system header]

    Call heuristic (MVP — bare identifiers and field_expression):
        call_expression where 'function' field is an identifier → kind="call".
        call_expression where 'function' field is field_expression → obj->func_ptr() → capture receiver.
    """
    try:
        edges: list[Edge] = []
        file_str = str(filepath)
        file_stem = filepath.stem

        def _walk(node: Node) -> None:
            if node.type == "preproc_include":
                _handle_c_include(node, file_str, file_stem, edges)
                return

            elif node.type == "call_expression":
                func_child = node.child_by_field_name("function")
                c_callee: str | None = None
                c_recv: str | None = None

                if func_child and func_child.type == "identifier":
                    c_callee = _text(func_child)
                elif func_child and func_child.type == "field_expression":
                    arg_node = func_child.child_by_field_name("argument")
                    field_node = func_child.child_by_field_name("field")
                    if field_node is not None and field_node.type == "field_identifier":
                        c_callee = _text(field_node)
                        if arg_node is not None:
                            c_recv = _text(arg_node)

                if c_callee:
                    source = _find_enclosing_function(node, "c")
                    if source is not None:
                        edges.append(
                            Edge(
                                source=source,
                                target=c_callee,
                                kind="call",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                                receiver=c_recv,
                            )
                        )

            for child in node.children:
                _walk(child)

        for child in root.children:
            _walk(child)

        return edges
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_edges_c: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []


def _extract_comments_c(root: Node, filepath: Path) -> list[Comment]:
    """Walk a C AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    C uses a single 'comment' node type for both // line and /* */ block comments.
    """
    try:
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
                            comments.append(
                                Comment(marker=marker, text=text, line=base_row + offset)
                            )
                else:
                    body = raw.lstrip("/").strip()
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row))

            for child in node.children:
                _walk(child)

        for child in root.children:
            _walk(child)

        return comments
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_comments_c: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []
