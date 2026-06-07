"""C++ symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf), graph_scope_infer_ext[2] (leaf), graph_c (leaf) — never from graph.py.

LAYERING:
    graph_common  (leaf — no seam deps)
    graph_c       (leaf — C extractors + shared C/C++ helpers)
         ↑
    graph_cpp     (this file)
         ↑
    graph_c_cpp   (thin re-exporter; graph.py imports from there)

WHY split from graph_c_cpp.py: graph_c_cpp.py exceeded 1000 lines after Tier B additions.
C and C++ are now each large enough to stand alone. graph_cpp.py imports shared helpers
(_c_doc_comment, _handle_c_include) from graph_c to avoid duplication.

GRAMMAR FACTS (verified by dumping AST from real fixtures):
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
from seam.indexer.field_access_ext import (
    collect_field_symbols_cpp,
    extract_field_accesses_cpp,
)
from seam.indexer.graph_c import (
    _c_doc_comment,
    _extract_comments_c,
    _handle_c_include,
)
from seam.indexer.graph_common import (
    Comment,
    Edge,
    Symbol,
    _find_enclosing_function,
    _make_symbol,
    _text,
)
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import (
    _CPP_SELF_NAMES,
    collect_composition_types_cpp,
    record_cpp_local_types,
    record_cpp_param_types,
    scan_class_fields_cpp,
)
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)


# ── C++ helpers ────────────────────────────────────────────────────────────────


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
        if declarator.type != "function_declarator":
            return None
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            return None
        if inner.type == "identifier":
            return _text(inner)
        if inner.type == "field_identifier":
            return _text(inner)
        if inner.type == "qualified_identifier":
            scope = inner.child_by_field_name("scope")
            name_node = inner.child_by_field_name("name")
            if scope is not None and name_node is not None:
                return f"{_text(scope)}.{_text(name_node)}"
    except Exception:  # noqa: BLE001
        pass
    return None


def _cpp_enclosing_class_name(node: Node) -> str | None:
    """Walk up the parent chain to find the nearest enclosing C++ class/struct name."""
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
    symbol with the same qualified name and kind. The de-duplication rule: prefer
    the entry with the larger line span (out-of-line definitions typically have a full body).
    """
    seen: dict[tuple[str, str], Symbol] = {}
    for sym in symbols:
        key = (sym["name"], sym["kind"])
        if key not in seen:
            seen[key] = sym
        else:
            existing = seen[key]
            existing_span = existing["end_line"] - existing["start_line"]
            current_span = sym["end_line"] - sym["start_line"]
            if current_span > existing_span:
                seen[key] = sym
    result: list[Symbol] = []
    added: set[tuple[str, str]] = set()
    for sym in symbols:
        key = (sym["name"], sym["kind"])
        if seen[key] is sym and key not in added:
            result.append(sym)
            added.add(key)
    return result


# ── C++ extraction ─────────────────────────────────────────────────────────────


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

    De-duplication is applied at the end: in-class + out-of-line definitions of the
    same method both produce the same (name, kind) — only one is retained.
    """
    try:
        symbols: list[Symbol] = []
        file_str = str(filepath)

        def _walk(node: Node, class_name: str | None = None) -> None:
            if node.type == "namespace_definition":
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
    walk_fn: object,
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
        # A3 Slice 4: emit field symbols for C++ class/struct fields.
        if config.SEAM_FIELD_ACCESS_EDGES == "on":
            for qual_name, field_line in collect_field_symbols_cpp(node, cls_name):
                symbols.append(Symbol(
                    name=qual_name,
                    kind="field",
                    file=file_str,
                    start_line=field_line,
                    end_line=field_line,
                    docstring=None,
                    signature=None,
                    decorators=[],
                    is_exported=None,
                    visibility=None,
                    qualified_name=qual_name,
                ))
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
      - In-class method (field_identifier declarator with enclosing class known):
        name = 'EnclosingClass.method', kind='method'.
      - Free function (identifier declarator, no enclosing class): name = plain name, kind='function'.
    """
    try:
        name = _cpp_function_name(node)
        if not name:
            return

        if "." in name:
            kind = "method"
            qualified = name
        elif enclosing_class:
            kind = "method"
            qualified = f"{enclosing_class}.{name}"
        else:
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

    Call heuristic:
        call_expression where 'function' is an identifier → bare call.
        call_expression where 'function' is field_expression → obj.m() or obj->m().

    Tier B B5: when SEAM_TYPE_INFERENCE is on, field_expression calls are resolved to
    'Type.method' qualified targets using per-function scope.
    """
    try:
        edges: list[Edge] = []
        file_str = str(filepath)
        file_stem = filepath.stem
        infer = config.SEAM_TYPE_INFERENCE == "on"
        composition_on = config.SEAM_COMPOSITION_EDGES == "on"
        field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

        def _walk(
            node: Node,
            class_name: str | None,
            class_fields: dict[str, str],
            var_types: dict[str, str],
        ) -> None:
            ntype = node.type
            if ntype == "preproc_include":
                _handle_c_include(node, file_str, file_stem, edges)
                return

            if ntype in ("class_specifier", "struct_specifier"):
                new_class: str | None = None
                cn = node.child_by_field_name("name")
                if cn is not None:
                    new_class = _text(cn).strip() or None
                # Slice #79: emit holds edges for C++ class/struct fields.
                if composition_on and new_class:
                    _handle_cpp_class_holds(node, new_class, file_str, edges)
                new_fields = scan_class_fields_cpp(node) if infer else {}
                for child in node.children:
                    _walk(child, new_class, new_fields, dict(new_fields))
                return

            if ntype == "function_definition":
                new_types: dict[str, str] = dict(class_fields)
                if infer:
                    record_cpp_param_types(node, new_types)
                body = node.child_by_field_name("body")
                if body is not None:
                    # A3 Slice 4: emit reads/writes field-access edges for C++ methods.
                    if field_access_on:
                        func_name = _cpp_function_name(node)
                        if func_name:
                            # For in-class methods (field_identifier declarator),
                            # qualify with enclosing class.
                            if "." not in func_name and class_name:
                                source_fn = f"{class_name}.{func_name}"
                            else:
                                source_fn = func_name
                            # Determine class context: use dotted prefix if out-of-line.
                            ctx_class = class_name
                            if "." in source_fn:
                                ctx_class = source_fn.split(".")[0]
                            for src, tgt, mode, ln in extract_field_accesses_cpp(
                                body, source_fn, ctx_class, new_types
                            ):
                                edges.append(Edge(
                                    source=src,
                                    target=tgt,
                                    kind=mode,
                                    file=file_str,
                                    line=ln,
                                    confidence="EXTRACTED",
                                    receiver=None,
                                ))
                    for child in body.children:
                        _walk(child, class_name, class_fields, new_types)
                    return

            if infer and ntype == "declaration":
                record_cpp_local_types(node, var_types)

            # Tier B B6: new_expression (new Foo(...)) → instantiates edge.
            elif ntype == "new_expression":
                type_node = next(
                    (c for c in node.children if c.type == "type_identifier"), None
                )
                if type_node is not None:
                    type_name = _text(type_node)
                    if type_name:
                        source = _find_enclosing_function(node, "cpp")
                        if source is not None:
                            edges.append(
                                Edge(
                                    source=source,
                                    target=type_name,
                                    kind="instantiates",
                                    file=file_str,
                                    line=node.start_point[0] + 1,
                                    confidence="INFERRED",
                                    receiver=None,
                                )
                            )
                for child in node.children:
                    _walk(child, class_name, class_fields, var_types)
                return

            elif ntype == "call_expression":
                func_child = node.child_by_field_name("function")
                cpp_callee: str | None = None
                cpp_recv: str | None = None

                if func_child and func_child.type == "identifier":
                    cpp_callee = _text(func_child)
                elif func_child and func_child.type == "field_expression":
                    arg_node = func_child.child_by_field_name("argument")
                    field_node = func_child.child_by_field_name("field")
                    if field_node is not None and field_node.type == "field_identifier":
                        cpp_callee = _text(field_node)
                        if arg_node is not None:
                            cpp_recv = _text(arg_node)

                if cpp_callee:
                    final_target = cpp_callee
                    if infer and cpp_recv is not None:
                        recv_lookup = cpp_recv.lstrip("*").strip()
                        resolved_type = resolve_receiver_type_ext(
                            recv_lookup, class_name, var_types, _CPP_SELF_NAMES
                        )
                        if resolved_type:
                            final_target = f"{resolved_type}.{cpp_callee}"

                    source = _find_enclosing_function(node, "cpp")
                    if source is not None:
                        edges.append(
                            Edge(
                                source=source,
                                target=final_target,
                                kind="call",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                                receiver=cpp_recv,
                            )
                        )

            for child in node.children:
                _walk(child, class_name, class_fields, var_types)

        for child in root.children:
            _walk(child, None, {}, {})

        return edges
    except Exception:  # noqa: BLE001
        logger.debug(
            "_extract_edges_cpp: unhandled exception for file=%s",
            filepath,
            exc_info=True,
        )
        return []


def _handle_cpp_class_holds(
    class_node: Node, class_name: str, file_str: str, edges: list[Edge]
) -> None:
    """Emit holds edges for each plain user-type field in a C++ class/struct.

    Delegates to collect_composition_types_cpp for (held_type, line) pairs and
    emits one Edge per unique pair. Never raises (backstop try/except).
    """
    try:
        for held_type, held_line in collect_composition_types_cpp(class_node):
            edges.append(
                Edge(
                    source=class_name,
                    target=held_type,
                    kind="holds",
                    file=file_str,
                    line=held_line,
                    confidence="INFERRED",
                    receiver=None,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_cpp_class_holds: failed: %r", exc)


def _extract_comments_cpp(root: Node, filepath: Path) -> list[Comment]:
    """Walk a C++ AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    C++ shares the same 'comment' node type as C — delegate to the C implementation.
    """
    return _extract_comments_c(root, filepath)
