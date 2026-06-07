"""C# symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) and graph_scope_infer_ext[2] (leaf) — never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
         ↑
    graph_csharp       (this file)
         ↑
    graph_java_csharp  (thin re-exporter; graph.py imports from there)

WHY split from graph_java_csharp.py: graph_java_csharp.py exceeded 1000 lines after Tier B
additions. Java and C# are now each large enough to stand alone.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config
from seam.indexer.field_access_ext import (
    collect_field_symbols_csharp,
    extract_field_accesses_csharp,
)
from seam.indexer.graph_common import (
    Comment,
    Edge,
    Symbol,
    _base_type_name,
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _text,
)
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import (
    _CS_SELF_NAMES,
    collect_composition_types_cs,
    record_cs_local_types,
    record_cs_param_types,
    scan_class_fields_cs,
)
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)


# ── C# helpers ─────────────────────────────────────────────────────────────────


def _node_name_cs(node: Node) -> str | None:
    """Return text of the 'name' field child, or None if absent."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return _text(name_node)


def _csharp_doc_comment(decl_node: Node) -> str | None:
    """Capture C# XML doc-comment: the nearest contiguous block of /// lines above the declaration.

    In the C# grammar all comments (///, //, /* */) appear as 'comment' nodes.
    Walk prev_sibling backwards:
      - Skip non-/// 'comment' nodes (e.g. // HACK: ...) to find the nearest
        contiguous block of /// lines.
      - Collect contiguous /// lines into the docstring.
      - Stop at non-comment nodes or when a gap (non-/// comment) breaks the block.
    """
    try:
        lines: list[str] = []
        current = decl_node.prev_sibling

        # Phase 1: skip any leading non-/// comments to find the tail of the /// block.
        while current is not None and current.type == "comment":
            raw = _text(current)
            if raw.startswith("///"):
                break
            current = current.prev_sibling

        # Phase 2: collect contiguous /// lines.
        while current is not None and current.type == "comment":
            raw = _text(current)
            if not raw.startswith("///"):
                break
            body = raw[3:].strip()
            lines.append(body)
            current = current.prev_sibling

        if not lines:
            return None
        return "\n".join(reversed(lines))
    except Exception:  # noqa: BLE001
        return None


def _csharp_modifier(decl_node: Node, modifier_type: str = "modifier") -> str | None:
    """Find a modifier string ('public'/'private'/'protected'/'internal') from a C# declaration."""
    try:
        for child in decl_node.children:
            if child.type == modifier_type:
                text = _text(child).strip()
                if text in ("public", "private", "protected", "internal"):
                    return text
    except Exception:  # noqa: BLE001
        pass
    return None


def _csharp_attributes(decl_node: Node) -> list[str]:
    """Extract C# attribute list texts ([Serializable], [HttpGet], etc.) from a declaration."""
    result: list[str] = []
    try:
        for child in decl_node.children:
            if child.type == "attribute_list":
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


# ── C# extraction ──────────────────────────────────────────────────────────────


def _extract_symbols_csharp(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a C# AST and extract class, struct, record, interface, enum, delegate, and method symbols.

    Kind mapping (Phase 9 spec, closed vocabulary):
        class_declaration / struct_declaration / record_declaration → class
        interface_declaration                                       → interface
        enum_declaration / delegate_declaration                     → type
        method_declaration / constructor_declaration (inside type) → method (qualified)

    namespace_declaration and file_scoped_namespace_declaration are TRAVERSED but
    NOT emitted as symbols (per spec — namespace is a container, not a symbol).
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

    def _walk_body(body_node: Node, class_name: str | None = None) -> None:
        """Walk declaration_list children, tracking class context."""
        for child in body_node.named_children:
            _walk(child, class_name)

    def _walk(node: Node, class_name: str | None = None) -> None:
        """Recursively walk the AST; class_name tracks the enclosing type."""
        try:
            ntype = node.type

            if ntype in ("namespace_declaration", "file_scoped_namespace_declaration"):
                body = node.child_by_field_name("body")
                if body is not None:
                    _walk_body(body)

            elif ntype in ("class_declaration", "struct_declaration", "record_declaration"):
                name = _node_name_cs(node)
                if name:
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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
                    # A3 Slice 4: emit field symbols for C# class/struct fields and properties.
                    if field_access_on:
                        for qual_name, field_line in collect_field_symbols_csharp(node, name):
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
                        _walk_body(body, class_name=name)

            elif ntype == "interface_declaration":
                name = _node_name_cs(node)
                if name:
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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
                    body = node.child_by_field_name("body")
                    if body is not None:
                        _walk_body(body, class_name=name)

            elif ntype in ("enum_declaration", "delegate_declaration"):
                name = _node_name_cs(node)
                if name:
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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

            elif (
                ntype in ("method_declaration", "constructor_declaration")
                and class_name is not None
            ):
                name = _node_name_cs(node)
                if name:
                    qualified = f"{class_name}.{name}"
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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

            else:
                for child in node.named_children:
                    _walk(child, class_name)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_symbols_csharp: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.named_children:
        _walk(child)

    return symbols


def _extract_edges_csharp(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a C# AST.

    Import heuristic:
        using System;                    → target = 'System'   (identifier)
        using System.Collections.Generic → target = 'Generic'  (qualified_name.name)

    Call heuristic:
        invocation_expression where 'function' is an identifier → bare call.
        invocation_expression where 'function' is member_access_expression → obj.Method().

    Tier B B5: when SEAM_TYPE_INFERENCE is on, member-access calls are resolved to
    'Type.method' qualified targets using per-function scope (class fields + params +
    local declaration statements).
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"
    infer = config.SEAM_TYPE_INFERENCE == "on"
    composition_on = config.SEAM_COMPOSITION_EDGES == "on"
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

    def _walk(
        node: Node,
        class_name: str | None,
        class_fields: dict[str, str],
        var_types: dict[str, str],
    ) -> None:
        try:
            ntype = node.type

            if emit_inheritance and ntype in (
                "class_declaration",
                "struct_declaration",
                "interface_declaration",
                "record_declaration",
            ):
                _handle_csharp_inheritance(node, file_str, edges)

            if ntype == "using_directive":
                _handle_csharp_using(node, file_str, file_stem, edges)
                return

            if ntype in (
                "class_declaration",
                "struct_declaration",
                "record_declaration",
            ):
                new_class_name: str | None = None
                cn = node.child_by_field_name("name")
                if cn is not None:
                    new_class_name = _text(cn).strip() or None
                # Slice #79: emit holds edges for C# class fields + ctor params.
                if composition_on and new_class_name:
                    _handle_cs_class_holds(node, new_class_name, file_str, edges)
                new_fields: dict[str, str] = scan_class_fields_cs(node) if infer else {}
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.named_children:
                        _walk(child, new_class_name, new_fields, dict(new_fields))
                return

            if ntype in ("method_declaration", "constructor_declaration"):
                new_types: dict[str, str] = dict(class_fields)
                if infer:
                    record_cs_param_types(node, new_types)
                body = node.child_by_field_name("body")
                if body is not None:
                    # A3 Slice 4: emit reads/writes field-access edges.
                    if field_access_on and class_name is not None:
                        method_name = _node_name_cs(node)
                        if method_name:
                            source_fn = f"{class_name}.{method_name}"
                            for src, tgt, mode, line in extract_field_accesses_csharp(
                                body, source_fn, class_name, new_types
                            ):
                                edges.append(Edge(
                                    source=src,
                                    target=tgt,
                                    kind=mode,
                                    file=file_str,
                                    line=line,
                                    confidence="INFERRED",
                                    receiver=None,
                                ))
                    for child in body.named_children:
                        _walk(child, class_name, class_fields, new_types)
                return

            if infer and ntype == "local_declaration_statement":
                record_cs_local_types(node, var_types)

            # Tier B B6: object_creation_expression (new Foo()) → instantiates edge.
            elif ntype == "object_creation_expression":
                type_node = next(
                    (c for c in node.children if c.type == "identifier"), None
                )
                if type_node is not None:
                    type_name = _text(type_node)
                    if type_name:
                        source = _find_enclosing_function(node, "csharp")
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

            elif ntype == "invocation_expression":
                func_node = node.child_by_field_name("function")
                cs_callee: str | None = None
                cs_recv: str | None = None

                if func_node is not None and func_node.type == "identifier":
                    cs_callee = _text(func_node)
                elif func_node is not None and func_node.type == "member_access_expression":
                    expr_node = func_node.child_by_field_name("expression")
                    name_node = func_node.child_by_field_name("name")
                    if name_node is not None and name_node.type == "identifier":
                        cs_callee = _text(name_node)
                        if expr_node is not None:
                            cs_recv = _text(expr_node)

                if cs_callee is not None:
                    final_target = cs_callee
                    if infer and cs_recv is not None:
                        resolved_type = resolve_receiver_type_ext(
                            cs_recv, class_name, var_types, _CS_SELF_NAMES
                        )
                        if resolved_type:
                            final_target = f"{resolved_type}.{cs_callee}"
                    source = _find_enclosing_function(node, "csharp")
                    if source is not None:
                        edges.append(
                            Edge(
                                source=source,
                                target=final_target,
                                kind="call",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                                receiver=cs_recv,
                            )
                        )

            for child in node.children:
                _walk(child, class_name, class_fields, var_types)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_edges_csharp: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.children:
        _walk(child, None, {}, {})

    return edges


def _handle_cs_class_holds(
    class_node: Node, class_name: str, file_str: str, edges: list[Edge]
) -> None:
    """Emit holds edges for each plain user-type field/ctor-param in a C# class.

    Delegates to collect_composition_types_cs for (held_type, line) pairs and
    emits one Edge per unique pair. Never raises (backstop try/except).
    """
    try:
        for held_type, held_line in collect_composition_types_cs(class_node):
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
        logger.debug("_handle_cs_class_holds: failed: %r", exc)


def _handle_csharp_inheritance(decl_node: Node, file_str: str, edges: list[Edge]) -> None:
    """Emit extends edges from a C# type declaration's base_list.

    C# does NOT syntactically distinguish a base class from implemented interfaces
    (both live in `base_list` after the colon), so all base entries are emitted as
    'extends' edges. Never raises.
    """
    name_node = decl_node.child_by_field_name("name")
    if name_node is None:
        return
    src_name = _text(name_node)
    line = decl_node.start_point[0] + 1

    for child in decl_node.children:
        if child.type == "base_list":
            for base in child.named_children:
                target = _base_type_name(base)
                if target:
                    edges.append(
                        Edge(
                            source=src_name,
                            target=target,
                            kind="extends",
                            file=file_str,
                            line=line,
                            confidence="INFERRED",
                            receiver=None,
                        )
                    )


def _handle_csharp_using(
    using_node: Node, file_str: str, file_stem: str, edges: list[Edge]
) -> None:
    """Emit an import edge from a C# using_directive node.

    Extracts the last segment of the namespace:
        using System                     → 'System'   (identifier)
        using System.Collections.Generic → 'Generic'  (qualified_name.name)
        using Foo = System.Collections.Generic → 'Generic' (alias form, skip alias identifier)
    """
    line = using_node.start_point[0] + 1
    named = using_node.named_children
    has_identifier = any(c.type == "identifier" for c in named)
    has_qualified = any(c.type == "qualified_name" for c in named)
    if has_identifier and has_qualified:
        # Alias form: emit only the qualified_name segment, not the alias.
        for child in named:
            if child.type == "qualified_name":
                target = _csharp_using_last_segment(child)
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
        return

    for child in named:
        target = _csharp_using_last_segment(child)
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


def _csharp_using_last_segment(node: Node) -> str | None:
    """Extract the rightmost segment from a C# using_directive's name node."""
    if node.type == "identifier":
        return _text(node)
    if node.type == "qualified_name":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _text(name_node)
    return None


def _extract_comments_csharp(root: Node, filepath: Path) -> list[Comment]:
    """Walk a C# AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    C# comment node types:
        'comment' — covers //, ///, and /* */ comments (all under the same node type).
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        try:
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
                elif raw.startswith("///"):
                    body = raw[3:].strip()
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row))
                elif raw.startswith("//"):
                    body = raw[2:].strip()
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row))

            for child in node.children:
                _walk(child)

        except Exception:  # noqa: BLE001
            pass

    for child in root.children:
        _walk(child)

    return comments
