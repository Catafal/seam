"""TypeScript/JavaScript symbol and edge extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf), graph_scope_infer (leaf), signatures (leaf) — never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
    graph_scope_infer  (leaf — Python+TS receiver-type inference)
         ↑
    graph_typescript   (this file — TS/JS symbol/edge extraction)
         ↑
    graph.py           (dispatcher; imports this module's public extractors)

WHY split from graph.py: graph.py exceeded 1000 lines. TypeScript/JS extraction is a
coherent leaf unit split following the Phase 9 precedent.

Contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default.
"""

from pathlib import Path

from tree_sitter import Node

import seam.config as config
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
from seam.indexer.graph_scope_infer import (
    _TS_SELF_NAMES,
    record_ts_local_types,
    record_ts_param_types,
    resolve_receiver_type,
    scan_class_fields_typescript,
)
from seam.indexer.signatures import extract_node_fields

# ── JSDoc helper ───────────────────────────────────────────────────────────────


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


# ── TypeScript/JavaScript extraction ──────────────────────────────────────────


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

    Call heuristic (Tier B B4 enhanced):
      - identifier call_expression → source/target edge (bare, no receiver)
      - member_expression call (obj.method()) → when SEAM_TYPE_INFERENCE=on and the
        receiver type is known in scope, target = 'Type.method'; else bare 'method'.
      - Scope: class field pre-scan (order-independent) + per-function param/local types.

    B6: new_expression → 'instantiates' edges.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"
    type_inference_on = config.SEAM_TYPE_INFERENCE == "on"

    def _emit_ts_inheritance(node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        src_name = _text(name_node)
        line = node.start_point[0] + 1

        def _emit_from_clause(clause: Node, kind: str) -> None:
            for c in clause.named_children:
                target = _base_type_name(c)
                if target:
                    edges.append(Edge(
                        source=src_name,
                        target=target,
                        kind=kind,
                        file=file_str,
                        line=line,
                        confidence="INFERRED",
                        receiver=None,
                    ))

        for child in node.children:
            if child.type == "class_heritage":
                for clause in child.children:
                    if clause.type == "extends_clause":
                        _emit_from_clause(clause, "extends")
                    elif clause.type == "implements_clause":
                        _emit_from_clause(clause, "implements")
            elif child.type == "extends_type_clause":
                _emit_from_clause(child, "extends")

    def _emit_import_edges_ts(node: Node) -> None:
        line = node.start_point[0] + 1
        clause = None
        for child in node.children:
            if child.type == "import_clause":
                clause = child
                break
        if not clause:
            return
        for clause_child in clause.children:
            if clause_child.type == "identifier":
                edges.append(Edge(
                    source=file_stem,
                    target=_text(clause_child),
                    kind="import",
                    file=file_str,
                    line=line,
                    confidence="INFERRED",
                    receiver=None,
                ))
            elif clause_child.type == "namespace_import":
                for ns_child in clause_child.children:
                    if ns_child.type == "identifier":
                        edges.append(Edge(
                            source=file_stem,
                            target=_text(ns_child),
                            kind="import",
                            file=file_str,
                            line=line,
                            confidence="INFERRED",
                            receiver=None,
                        ))
                        break
            elif clause_child.type == "named_imports":
                for spec in clause_child.children:
                    if spec.type == "import_specifier":
                        name_node = spec.child_by_field_name("name")
                        if name_node is None and spec.children:
                            name_node = spec.children[0]
                        if name_node:
                            edges.append(Edge(
                                source=file_stem,
                                target=_text(name_node),
                                kind="import",
                                file=file_str,
                                line=line,
                                confidence="INFERRED",
                                receiver=None,
                            ))

    def _emit_ts_instantiates(node: Node, class_name: str | None, language: str) -> None:
        """Emit an instantiates edge from a TS/JS new_expression node."""
        try:
            source = _find_enclosing_function(node, language)
            if source is None:
                return
            for child in node.children:
                if child.type == "identifier":
                    type_name = _text(child)
                    if type_name:
                        edges.append(Edge(
                            source=source,
                            target=type_name,
                            kind="instantiates",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=None,
                        ))
                    return
        except Exception:  # noqa: BLE001
            pass

    def _emit_call_edge_ts(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
        language: str,
    ) -> None:
        func_child = node.child_by_field_name("function")
        callee_node: Node | None = None
        receiver_text: str | None = None

        if func_child and func_child.type == "identifier":
            callee_node = func_child
        elif func_child and func_child.type == "member_expression":
            prop = func_child.child_by_field_name("property")
            obj = func_child.child_by_field_name("object")
            if prop is not None and prop.type == "property_identifier":
                callee_node = prop
                if obj is not None:
                    receiver_text = _text(obj)

        if callee_node is None:
            return
        source = _find_enclosing_function(node, language)
        if source is None:
            return

        method_name = _text(callee_node)
        target = method_name

        if type_inference_on and receiver_text is not None:
            resolved_type = resolve_receiver_type(
                receiver_text, class_name, var_types, _TS_SELF_NAMES
            )
            if resolved_type is not None:
                target = f"{resolved_type}.{method_name}"

        edges.append(Edge(
            source=source,
            target=target,
            kind="call",
            file=file_str,
            line=node.start_point[0] + 1,
            confidence="INFERRED",
            receiver=receiver_text,
        ))

    def _walk_ts_function_body(
        func_node: Node,
        class_name: str | None,
        class_fields: dict[str, str],
        language: str,
    ) -> None:
        var_types: dict[str, str] = dict(class_fields)
        record_ts_param_types(func_node, var_types)
        body = func_node.child_by_field_name("body")
        if body is None:
            return
        for stmt in body.children:
            record_ts_local_types(stmt, var_types)
            _walk_ts_stmt(stmt, class_name, var_types, class_fields, language)

    def _walk_ts_stmt(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
        class_fields: dict[str, str],
        language: str,
    ) -> None:
        if node.type == "import_statement":
            _emit_import_edges_ts(node)
            return

        # interface_declaration: emit inheritance here (not in _walk_ts_class).
        # class_declaration: inheritance is emitted inside _walk_ts_class — do NOT emit here
        # to avoid double-emitting when a class is defined inside a function body.
        if emit_inheritance and node.type == "interface_declaration":
            _emit_ts_inheritance(node)

        if node.type == "call_expression":
            _emit_call_edge_ts(node, class_name, var_types, language)

        if node.type == "new_expression":
            _emit_ts_instantiates(node, class_name, language)

        if node.type in ("function_declaration", "arrow_function", "function_expression"):
            _walk_ts_function_body(node, class_name, class_fields, language)
            return
        if node.type == "method_definition":
            _walk_ts_function_body(node, class_name, class_fields, language)
            return
        if node.type == "class_declaration":
            _walk_ts_class(node, language)
            return

        for child in node.children:
            _walk_ts_stmt(child, class_name, var_types, class_fields, language)

    def _walk_ts_class(class_node: Node, language: str) -> None:
        cls_name_node = class_node.child_by_field_name("name")
        cls_name = _text(cls_name_node) if cls_name_node else None

        if emit_inheritance and cls_name:
            _emit_ts_inheritance(class_node)

        class_fields: dict[str, str] = {}
        if type_inference_on and cls_name:
            class_fields = scan_class_fields_typescript(class_node)

        body = class_node.child_by_field_name("body")
        if body is None:
            return
        for child in body.children:
            if child.type == "method_definition":
                _walk_ts_function_body(child, cls_name, class_fields, language)
            elif child.type in ("public_field_definition", "field_definition"):
                pass
            elif child.type == "class_declaration":
                _walk_ts_class(child, language)
            else:
                _walk_ts_stmt(child, cls_name, class_fields, class_fields, language)

    def _walk_ts_toplevel(node: Node, language: str) -> None:
        if node.type == "import_statement":
            _emit_import_edges_ts(node)
        elif node.type == "class_declaration":
            _walk_ts_class(node, language)
        elif emit_inheritance and node.type == "interface_declaration":
            _emit_ts_inheritance(node)
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    _walk_ts_stmt(child, None, {}, {}, language)
        elif node.type in ("function_declaration", "function_expression"):
            _walk_ts_function_body(node, None, {}, language)
        elif node.type == "lexical_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    val = child.child_by_field_name("value")
                    if val and val.type in ("arrow_function", "function_expression"):
                        _walk_ts_function_body(val, None, {}, language)
        else:
            for child in node.children:
                _walk_ts_toplevel(child, language)

    lang = "typescript"
    for child in root.children:
        _walk_ts_toplevel(child, lang)

    return edges


def _extract_comments_typescript(root: Node, filepath: Path) -> list[Comment]:
    """Walk a TypeScript/JS AST and collect matched semantic comment nodes.

    Handles both // line comments and /* */ block comments.
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
                body = raw.lstrip("/").strip() if raw.startswith("//") else raw.strip()
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row))
        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments
