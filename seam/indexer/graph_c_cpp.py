"""C and C++ symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common  (leaf — no seam deps)
         ↑
    graph_c_cpp   (this file)
         ↑
    graph.py      (imports this module's public extractors at top level)

WHY split from graph.py: graph.py would exceed 1000 lines if it contained
C + C++ extractors directly. Splitting here (one file per language pair)
keeps all files within limits and maintains the top-level-only import rule.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default (whole-index resolution
    at read time handles EXTRACTED/AMBIGUOUS/INFERRED for cross-file edges).

GRAMMAR FACTS (verified by dumping AST from real fixtures):
  C:
    function_definition: fields 'type', 'declarator' (function_declarator whose
      'declarator' field is an identifier = function name), 'body'.
    storage_class_specifier "static" → file-local → visibility='private', is_exported=False.
    struct_specifier / union_specifier: field 'name' (type_identifier) → kind='class'.
    enum_specifier: field 'name' → kind='type'.
    type_definition: last type_identifier child is the typedef alias → kind='type'.
    preproc_include: field 'path' — string_literal (local) or system_lib_string (system).
    call_expression: field 'function' = identifier → bare call target.
    comment: covers both // and /* */ comments.

  C++:
    namespace_definition: traversed, NOT emitted as a symbol.
    class_specifier / struct_specifier: field 'name' → kind='class'.
      field_declaration_list body may contain:
        access_specifier nodes (public/private/protected), field_declaration,
        function_definition (in-class method).
    In-class function_definition: declarator field is function_declarator whose
      'declarator' field is a field_identifier (method name, NOT identifier).
    Out-of-line method: function_declarator's 'declarator' field is a
      qualified_identifier with 'scope' (class name) and 'name' (method name).
    enum_specifier: field 'name' → kind='type'.
    template_declaration: the inner class_specifier or function_definition is
      extracted; the template itself is NOT emitted as a symbol.
    call_expression: field 'function' = identifier → bare call target.
    comment: same as C — covers // and /* */ comments.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config

# All shared types from the leaf module (no cycle — graph_common has no seam deps).
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

# signatures.py is a leaf (no seam deps) so importing it here does not create a cycle.
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)


# ── Doc-comment helpers ────────────────────────────────────────────────────────


def _c_doc_comment(decl_node: Node) -> str | None:
    """Capture a C/C++ doc comment above a declaration.

    Strategy:
      1. If the immediately preceding sibling is a /* */ block comment that
         starts with '/**', return its cleaned content (Javadoc-style).
      2. Otherwise, collect contiguous // line comments immediately above the
         declaration (row-adjacent, no blank lines).

    WHY /** */ only for block: plain /* */ comments are code comments, not docs.
    WHY // block: C/C++ codebases commonly use leading // lines as doc headers.
    """
    try:
        prev = decl_node.prev_sibling
        if prev is None:
            return None

        # Check for /** */ block comment immediately above
        if prev.type == "comment":
            raw = _text(prev)
            if raw.startswith("/**"):
                # Strip comment delimiters and leading * on each line
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

            # Check for contiguous // line comments
            if raw.startswith("//"):
                lines: list[str] = []
                current: Node | None = prev
                while current is not None and current.type == "comment":
                    c_raw = _text(current)
                    if not c_raw.startswith("//"):
                        break
                    # Check row-adjacency with next node
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


# ── C extraction ───────────────────────────────────────────────────────────────


def _c_function_name(func_def: Node) -> str | None:
    """Extract the function name from a C function_definition node.

    The name lives inside: function_definition → function_declarator → identifier.
    The 'declarator' field of function_definition is the function_declarator,
    and its 'declarator' field is the identifier.
    """
    try:
        declarator = func_def.child_by_field_name("declarator")
        if declarator is None:
            return None
        # function_declarator's 'declarator' field is the identifier (function name)
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

    C has no class/method concept; all functions are 'function' kind.
    Static functions are file-local (visibility='private', is_exported=False).
    """
    try:
        symbols: list[Symbol] = []
        file_str = str(filepath)

        def _walk(node: Node) -> None:
            if node.type == "function_definition":
                _handle_c_function(node, file_str, symbols)
                # Do NOT recurse into function body for top-level symbols

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
    """Emit a C struct/union/enum symbol from a named specifier node.

    Only named specifiers (those with a 'name' field) are emitted.
    Anonymous struct/union/enum inside a typedef are handled by _handle_c_typedef.
    """
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
    """Emit a C typedef symbol from a type_definition node.

    The typedef alias name is the last type_identifier child of the type_definition.
    Pattern: typedef <type> <alias>;
    """
    try:
        # The declarator field (or last type_identifier) is the alias name
        declarator = node.child_by_field_name("declarator")
        if declarator is not None and declarator.type == "type_identifier":
            name = _text(declarator)
        else:
            # Fallback: last type_identifier child
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
        Both local and system includes produce import edges; resolution at read time
        returns [] for system headers (no file found), degrading to name-count rule.

    Call heuristic (MVP — bare identifiers only):
        call_expression where 'function' field is an identifier → kind="call".
        source = enclosing function name (via _find_enclosing_function).
    """
    try:
        edges: list[Edge] = []
        file_str = str(filepath)
        file_stem = filepath.stem

        def _walk(node: Node) -> None:
            if node.type == "preproc_include":
                _handle_c_include(node, file_str, file_stem, edges)
                return  # no need to recurse into include node

            elif node.type == "call_expression":
                func_child = node.child_by_field_name("function")
                if func_child and func_child.type == "identifier":
                    source = _find_enclosing_function(node, "c")
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
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_edges_c: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []


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
            # Local include: #include "utils.h" → string_content is 'utils.h'
            content = None
            for child in path_node.children:
                if child.type == "string_content":
                    content = _text(child)
                    break
            if content is None:
                # Fallback: strip quotes from full text
                content = _text(path_node).strip('"')
            target = Path(content).stem if content else None

        elif path_node.type == "system_lib_string":
            # System include: #include <stdio.h> → text is '<stdio.h>'
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
                )
            )
    except Exception:  # noqa: BLE001
        logger.debug("_handle_c_include: failed for node at row %d", node.start_point[0])


def _extract_comments_c(root: Node, filepath: Path) -> list[Comment]:
    """Walk a C AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    C uses a single 'comment' node type for both // line and /* */ block comments.
    Block comments: every line is scanned via _block_comment_lines.
    Line comments: // prefix stripped before marker matching.
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
                    # Line comment: strip // prefix
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


# ── C++ extraction ─────────────────────────────────────────────────────────────


def _cpp_function_name(func_def: Node) -> str | None:
    """Extract the function name from a C++ function_definition node.

    Handles three patterns observed in the real grammar:
      1. Free function:  declarator → function_declarator → declarator=identifier
      2. In-class method: declarator → function_declarator → declarator=field_identifier
      3. Out-of-line method: declarator → function_declarator → declarator=qualified_identifier

    Returns a plain name (for free functions and in-class methods) or a
    'Class.method' qualified name (for out-of-line Class::method definitions).
    """
    try:
        declarator = func_def.child_by_field_name("declarator")
        if declarator is None:
            return None
        # declarator field of function_definition is function_declarator
        if declarator.type != "function_declarator":
            return None
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            return None
        if inner.type == "identifier":
            # Free function: int add(...)
            return _text(inner)
        if inner.type == "field_identifier":
            # In-class method: double area() inside class body
            return _text(inner)
        if inner.type == "qualified_identifier":
            # Out-of-line method: double Circle::area()
            scope = inner.child_by_field_name("scope")
            name_node = inner.child_by_field_name("name")
            if scope and name_node:
                cls_name = _text(scope)
                method_name = _text(name_node)
                return f"{cls_name}.{method_name}"
    except Exception:  # noqa: BLE001
        pass
    return None


def _cpp_enclosing_class_name(node: Node) -> str | None:
    """Walk up the parent chain to find the nearest enclosing C++ class/struct name.

    Used to qualify in-class method definitions as 'ClassName.method'.
    """
    try:
        current = node.parent
        while current is not None:
            if current.type in ("class_specifier", "struct_specifier"):
                name_node = current.child_by_field_name("name")
                if name_node is not None:
                    return _text(name_node)
            current = current.parent
    except Exception:  # noqa: BLE001
        pass
    return None


def _dedup_cpp_symbols(symbols: list[Symbol]) -> list[Symbol]:
    """De-duplicate C++ symbols by (name, kind), keeping one per (name, kind) pair.

    WHY: a class method declared in-class AND defined out-of-line both produce a
    symbol with the same qualified name and kind (e.g. 'Circle.area' / 'method').
    The de-duplication rule: prefer the entry with the larger line span (out-of-line
    definitions typically have a full body). On tie, keep the first occurrence.

    This does NOT merge different (name, kind) combinations — those remain distinct.
    """
    seen: dict[tuple[str, str], Symbol] = {}
    for sym in symbols:
        key = (sym["name"], sym["kind"])
        if key not in seen:
            seen[key] = sym
        else:
            # Prefer the symbol with a larger line span (end_line - start_line).
            existing = seen[key]
            existing_span = existing["end_line"] - existing["start_line"]
            current_span = sym["end_line"] - sym["start_line"]
            if current_span > existing_span:
                seen[key] = sym
    # Preserve original order (first occurrence wins for equal spans).
    result: list[Symbol] = []
    added: set[tuple[str, str]] = set()
    for sym in symbols:
        key = (sym["name"], sym["kind"])
        if seen[key] is sym and key not in added:
            result.append(sym)
            added.add(key)
    return result


def _extract_symbols_cpp(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a C++ AST and extract class, struct, union, enum, and function symbols.

    Kind mapping (per Phase 9 spec):
        namespace_definition                  → traversed, NOT emitted
        class_specifier / struct_specifier    → class
        union_specifier                       → class
        enum_specifier                        → type
        function_definition (free)            → function
        function_definition (in-class)        → method  (Class.method)
        function_definition (out-of-line)     → method  (Class.method via qualified_identifier)
        template_declaration                  → inner class/function extracted

    C++ '::' qualified names are normalized to 'Class.method' (dot notation)
    for cross-language uniformity with Python/TS/Go/Rust.

    De-duplication is applied at the end: in-class + out-of-line definitions of the
    same method both produce the same (name, kind) — only one is retained.
    """
    try:
        symbols: list[Symbol] = []
        file_str = str(filepath)

        def _walk(node: Node, class_name: str | None = None) -> None:
            """Recursively walk AST, tracking enclosing class for method qualification."""
            if node.type == "namespace_definition":
                # Traverse namespace body but do NOT emit the namespace as a symbol.
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        _walk(child, class_name)

            elif node.type in ("class_specifier", "struct_specifier"):
                _handle_cpp_class(node, file_str, symbols, _walk)

            elif node.type == "union_specifier":
                _handle_cpp_union(node, file_str, symbols, _walk)

            elif node.type == "enum_specifier":
                _handle_cpp_enum(node, file_str, symbols)

            elif node.type == "function_definition":
                _handle_cpp_function(node, file_str, symbols, class_name)

            elif node.type == "template_declaration":
                # Look inside the template for the actual declaration
                for child in node.children:
                    if child.type in (
                        "class_specifier",
                        "struct_specifier",
                        "function_definition",
                    ):
                        _walk(child, class_name)

            else:
                for child in node.children:
                    _walk(child, class_name)

        for child in root.children:
            _walk(child, class_name=None)

        # De-duplicate: in-class + out-of-line definitions share the same (name, kind).
        return _dedup_cpp_symbols(symbols)
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_symbols_cpp: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []


def _handle_cpp_class(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    walk_fn: object,  # the recursive _walk closure
) -> None:
    """Emit a C++ class/struct symbol and recurse into its body for methods."""
    try:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        cls_name = _text(name_node)
        if not cls_name:
            return
        doc = _c_doc_comment(node)
        fields = extract_node_fields(
            node,
            "cpp",
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
        # Recurse into body to find in-class methods
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                if child.type == "function_definition":
                    _handle_cpp_function(child, file_str, symbols, cls_name)
    except Exception:  # noqa: BLE001
        logger.debug("_handle_cpp_class: failed for node at row %d", node.start_point[0])


def _handle_cpp_union(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    walk_fn: object,
) -> None:
    """Emit a C++ union symbol from a union_specifier node."""
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
            "cpp",
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
    except Exception:  # noqa: BLE001
        logger.debug("_handle_cpp_union: failed for node at row %d", node.start_point[0])


def _handle_cpp_enum(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a C++ enum symbol from an enum_specifier node."""
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
            "cpp",
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
        logger.debug("_handle_cpp_enum: failed for node at row %d", node.start_point[0])


def _handle_cpp_function(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    enclosing_class: str | None,
) -> None:
    """Emit a C++ function or method symbol from a function_definition node.

    Qualification rules:
      - Out-of-line method (qualified_identifier declarator): name = 'Class.method'
        (the '::' is normalized to '.').
      - In-class method (field_identifier declarator with enclosing class known):
        name = 'EnclosingClass.method', kind='method'.
      - Free function (identifier declarator, no enclosing class): name = plain name,
        kind='function'.
    """
    try:
        name = _cpp_function_name(node)
        if not name:
            return

        # Determine kind and final qualified name
        if "." in name:
            # Already qualified (out-of-line Class::method → 'Class.method')
            kind = "method"
            qualified = name
        elif enclosing_class:
            # In-class method: qualify with the enclosing class name
            kind = "method"
            qualified = f"{enclosing_class}.{name}"
        else:
            # Free function at top level or inside namespace (namespace is not emitted)
            kind = "function"
            qualified = name

        doc = _c_doc_comment(node)
        fields = extract_node_fields(
            node,
            "cpp",
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
    except Exception:  # noqa: BLE001
        logger.debug("_handle_cpp_function: failed for node at row %d", node.start_point[0])


def _extract_edges_cpp(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a C++ AST.

    Import heuristic:
        #include "x.h"  → target = stem ('x')
        #include <x>    → target = stem ('x')  [system/STL header]

    Call heuristic (MVP — bare identifiers only):
        call_expression where 'function' is an identifier → kind="call".
        Selector/member calls (obj.m(), Class::f()) are NOT tracked in this MVP.
    """
    try:
        edges: list[Edge] = []
        file_str = str(filepath)
        file_stem = filepath.stem

        def _walk(node: Node) -> None:
            if node.type == "preproc_include":
                # Reuse the shared C/C++ include handler
                _handle_c_include(node, file_str, file_stem, edges)
                return

            elif node.type == "call_expression":
                func_child = node.child_by_field_name("function")
                if func_child and func_child.type == "identifier":
                    source = _find_enclosing_function(node, "cpp")
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
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_edges_cpp: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []


def _extract_comments_cpp(root: Node, filepath: Path) -> list[Comment]:
    """Walk a C++ AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    C++ shares the same 'comment' node type as C (covers both // and /* */),
    so this function mirrors _extract_comments_c exactly.
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
            "_extract_comments_cpp: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []
