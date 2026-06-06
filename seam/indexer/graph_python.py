"""Python symbol and edge extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf), graph_scope_infer (leaf), signatures (leaf) — never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
    graph_scope_infer  (leaf — Python+TS receiver-type inference)
         ↑
    graph_python       (this file — Python-only symbol/edge extraction)
         ↑
    graph.py           (dispatcher; imports this module's public extractors)

WHY split from graph.py: graph.py exceeded 1000 lines. Python extraction is a coherent
leaf unit and is split following the Phase 9 precedent (graph_go_rust.py, etc.).

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
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _node_name,
    _text,
)
from seam.indexer.graph_scope_infer import (
    _PY_BUILTIN_TYPES,
    _PY_SELF_NAMES,
    record_py_local_types,
    record_py_param_types,
    resolve_receiver_type,
    scan_class_fields_python,
)
from seam.indexer.signatures import extract_node_fields

# ── Python docstring helper ────────────────────────────────────────────────────


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
    if first.type != "expression_statement" or not first.children:
        return None
    expr = first.children[0]
    if expr.type != "string":
        return None
    for child in expr.children:
        if child.type == "string_content":
            return _text(child).strip()
    return None


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
                        _walk(child, None)

        elif node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition and definition.type == "function_definition":
                name = _node_name(definition)
                if name:
                    kind = "method" if class_name else "function"
                    qualified = f"{class_name}.{name}" if class_name else name
                    doc = _py_docstring(definition)
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

    Call heuristic (Tier B B4 enhanced):
      - call node where function is a bare identifier (`foo()`) → target = identifier
      - call node where function is an attribute (`mod.fn()`, `self.m()`, `a.b.c()`)
        → when SEAM_TYPE_INFERENCE=on and receiver type is known in scope, target =
          'Type.method' (qualified); otherwise target = the rightmost identifier (bare).
      - source = nearest enclosing function/method (skip if none)

    B6: PascalCase bare calls (no receiver) → 'instantiates' edges.
    Guard: stdlib/typing builtins excluded via _PY_BUILTIN_TYPES to avoid false positives.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"
    type_inference_on = config.SEAM_TYPE_INFERENCE == "on"

    def _emit_import_edges(node: Node) -> None:
        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    target_node = child.child_by_field_name("name") or child
                    edges.append(Edge(
                        source=file_stem,
                        target=_text(target_node),
                        kind="import",
                        file=file_str,
                        line=node.start_point[0] + 1,
                        confidence="INFERRED",
                        receiver=None,
                    ))
        elif node.type == "import_from_statement":
            found_import_kw = False
            for child in node.children:
                if child.type == "import":
                    found_import_kw = True
                    continue
                if found_import_kw:
                    if child.type in ("dotted_name", "identifier"):
                        edges.append(Edge(
                            source=file_stem,
                            target=_text(child),
                            kind="import",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=None,
                        ))
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            edges.append(Edge(
                                source=file_stem,
                                target=_text(name_node),
                                kind="import",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                                receiver=None,
                            ))

    def _emit_call_edge(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
    ) -> None:
        """Emit a call edge for a Python 'call' node using scope inference."""
        func_child = node.child_by_field_name("function")
        callee_node: Node | None = None
        receiver_text: str | None = None

        if func_child and func_child.type == "identifier":
            callee_node = func_child
        elif func_child and func_child.type == "attribute":
            callee_node = func_child.child_by_field_name("attribute")
            object_node = func_child.child_by_field_name("object")
            if object_node is not None:
                receiver_text = _text(object_node)

        if callee_node is None or callee_node.type != "identifier":
            return
        source = _find_enclosing_function(node, "python")
        if source is None:
            return

        method_name = _text(callee_node)
        target = method_name

        # Tier B B6: PascalCase bare call (no receiver) → instantiates edge.
        # Guard: skip stdlib/typing builtins (Exception, TypeError, NamedTuple, etc.)
        if (
            receiver_text is None
            and method_name
            and method_name[0].isupper()
            and method_name not in _PY_BUILTIN_TYPES
        ):
            edges.append(Edge(
                source=source,
                target=method_name,
                kind="instantiates",
                file=file_str,
                line=node.start_point[0] + 1,
                confidence="INFERRED",
                receiver=None,
            ))
            return

        # Tier B B4: receiver-type inference for attribute calls.
        if type_inference_on and receiver_text is not None:
            resolved_type = resolve_receiver_type(
                receiver_text, class_name, var_types, _PY_SELF_NAMES
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

    def _walk_function_body(func_node: Node, class_name: str | None, class_fields: dict[str, str]) -> None:
        var_types: dict[str, str] = dict(class_fields)
        record_py_param_types(func_node, var_types)
        body = func_node.child_by_field_name("body")
        if body is None:
            return
        for stmt in body.children:
            record_py_local_types(stmt, var_types)
            _walk_stmt(stmt, class_name, var_types, class_fields)

    def _walk_stmt(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
        class_fields: dict[str, str],
    ) -> None:
        if node.type in ("import_statement", "import_from_statement"):
            _emit_import_edges(node)
            return

        if node.type == "call":
            _emit_call_edge(node, class_name, var_types)

        if node.type in ("function_definition", "decorated_definition"):
            inner_fn = node
            if node.type == "decorated_definition":
                inner_fn = node.child_by_field_name("definition") or node
            if inner_fn.type == "function_definition":
                _walk_function_body(inner_fn, class_name, class_fields)
                return

        if node.type == "class_definition":
            _walk_class(node)
            return

        for child in node.children:
            _walk_stmt(child, class_name, var_types, class_fields)

    def _walk_class(class_node: Node) -> None:
        cls_name = _node_name(class_node)

        if emit_inheritance and cls_name:
            bases = class_node.child_by_field_name("superclasses")
            if bases is not None:
                for base_child in bases.named_children:
                    base_target = _base_type_name(base_child)
                    if base_target:
                        edges.append(Edge(
                            source=cls_name,
                            target=base_target,
                            kind="extends",
                            file=file_str,
                            line=class_node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=None,
                        ))

        class_fields: dict[str, str] = {}
        if type_inference_on and cls_name:
            class_fields = scan_class_fields_python(class_node)

        body = class_node.child_by_field_name("body")
        if body is None:
            return
        for child in body.children:
            if child.type == "function_definition":
                _walk_function_body(child, cls_name, class_fields)
            elif child.type == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner and inner.type == "function_definition":
                    _walk_function_body(inner, cls_name, class_fields)
                elif inner and inner.type == "class_definition":
                    _walk_class(inner)
                else:
                    _walk_stmt(child, cls_name, {}, class_fields)
            elif child.type == "class_definition":
                _walk_class(child)
            else:
                _walk_stmt(child, cls_name, class_fields, class_fields)

    def _walk_toplevel(node: Node) -> None:
        if node.type in ("import_statement", "import_from_statement"):
            _emit_import_edges(node)
        elif node.type == "class_definition":
            _walk_class(node)
        elif node.type == "function_definition":
            _walk_function_body(node, None, {})
        elif node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner and inner.type == "function_definition":
                _walk_function_body(inner, None, {})
            elif inner and inner.type == "class_definition":
                _walk_class(inner)
            else:
                for child in node.children:
                    _walk_toplevel(child)
        else:
            for child in node.children:
                _walk_toplevel(child)

    for child in root.children:
        _walk_toplevel(child)

    return edges


def _extract_comments_python(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Python AST and collect matched semantic comment nodes (WHY/HACK/NOTE/TODO/FIXME).

    Python has a single 'comment' node type (lines starting with '#').
    Strips the leading '#' prefix before marker matching.
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "comment":
            raw = _text(node)
            body = raw.lstrip("#").strip()
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
