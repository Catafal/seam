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
from seam.indexer.field_access import (
    collect_field_symbols_python,
    extract_field_accesses_python,
)
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
    collect_composition_types_python,
    collect_param_types_python,
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
    """Walk a Python AST and extract function, class, and method symbols.

    A3: Also emits kind='field' symbols for class-level annotated fields and
    first self.x = ... assignments in __init__, when SEAM_FIELD_ACCESS_EDGES='on'.
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

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

                # A3: Emit field symbols for this class when feature is on.
                # Collects annotated class-level fields (x: Type) and first
                # self.x = ... assignments in __init__. Deduped by (class, field).
                # WHY emit field symbols here alongside the class symbol: we need the
                # class name context ('name') which is only available at the class_definition
                # node. Doing it in a separate post-pass over symbols would require
                # re-parsing or re-walking.
                if field_access_on:
                    for qualified_field, field_line in collect_field_symbols_python(node, name):
                        # WHY build Symbol manually instead of using _make_symbol:
                        # _make_symbol expects a tree-sitter Node for start/end lines.
                        # collect_field_symbols_python returns explicit (name, line) pairs
                        # because the field may come from __init__ (a child node of the
                        # class body, not the class node itself). Building Symbol directly
                        # with the exact line avoids needing a dummy or sentinel node.
                        symbols.append(Symbol(
                            name=qualified_field,
                            kind="field",
                            file=file_str,
                            start_line=field_line,
                            end_line=field_line,
                            docstring=None,
                            signature=None,
                            decorators=[],
                            is_exported=None,
                            visibility=None,
                            qualified_name=qualified_field,
                        ))

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
    composition_on = config.SEAM_COMPOSITION_EDGES == "on"
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"
    param_edges_on = config.SEAM_PARAM_EDGES == "on"

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
        """Emit a call edge for a Python 'call' node using scope inference.

        Two-stage decision (Tier B B4 + B6):
          1. Classify the call shape (bare identifier vs attribute):
             - `foo()`          → bare call (receiver_text=None)
             - `obj.method()`   → attribute call (receiver_text='obj')
          2. Emit the right edge kind:
             - Bare PascalCase  → 'instantiates' (B6); not 'call', because
               seam_query needs to distinguish object-construction from
               method calls, and callee_node is NOT stored at callee level.
             - Attribute call   → 'call' with target qualified to Type.method
               when the receiver type is known; bare method name when unknown.
               receiver_text is ALWAYS stored in the edge so the Tier A read
               path (names.py) and future passes have the raw text even when
               type resolution succeeded (prevents information loss).
        """
        func_child = node.child_by_field_name("function")
        callee_node: Node | None = None
        receiver_text: str | None = None

        if func_child and func_child.type == "identifier":
            callee_node = func_child
        elif func_child and func_child.type == "attribute":
            # attribute node: object='self'/'obj'/… + attribute='method_name'
            callee_node = func_child.child_by_field_name("attribute")
            object_node = func_child.child_by_field_name("object")
            if object_node is not None:
                receiver_text = _text(object_node)

        if callee_node is None or callee_node.type != "identifier":
            return
        source = _find_enclosing_function(node, "python")
        if source is None:
            # Top-level code with no enclosing named scope — drop the edge;
            # source would be the file stem which conflates all module-level calls.
            return

        method_name = _text(callee_node)
        target = method_name

        # B6: PascalCase bare call (no receiver) → 'instantiates' edge.
        # WHY 'instantiates' not 'call': a bare PascalCase name in Python is
        # overwhelmingly a constructor call (Foo(), MyClass()), not a function named
        # with an acronym. 'instantiates' allows seam_query to distinguish
        # construction from regular calls. Early-return: no receiver to store.
        # Guard: _PY_BUILTIN_TYPES excludes Exception, list, dict, etc. which would
        # otherwise produce thousands of false instantiates edges to stdlib types.
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

        # B4: receiver-type inference for attribute calls.
        # WHY only when resolved: the conservatism contract forbids emitting a wrong
        # qualified edge. When resolve_receiver_type returns None (unknown/optional/
        # generic/chained), we keep the bare method name — Tier A can still elevate
        # unambiguous bare names at read time, and the raw receiver_text is stored.
        if type_inference_on and receiver_text is not None:
            resolved_type = resolve_receiver_type(
                receiver_text, class_name, var_types, _PY_SELF_NAMES
            )
            if resolved_type is not None:
                target = f"{resolved_type}.{method_name}"

        # Always store receiver_text even when the target was already qualified.
        # WHY: the raw receiver is useful for debugging mis-resolutions and for
        # future inference passes that re-process edges without a full re-index.
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
        # Start with a COPY of class_fields, not a reference. WHY: param/local bindings
        # must not leak back into class_fields across methods (each function has an
        # independent scope). class_fields is the order-independent Layer 1 pre-scan;
        # var_types is the per-function Layer 2 scope that adds params and locals on top.
        var_types: dict[str, str] = dict(class_fields)
        record_py_param_types(func_node, var_types)

        # 'uses' edges: this function references plain user types as parameters.
        # Hooked here (not in the body walk) because the function node carries the
        # full signature; source is qualified Class.method (or bare top-level fn).
        if param_edges_on:
            name_node = func_node.child_by_field_name("name")
            if name_node is not None:
                fn_name = _text(name_node)
                source_fn = f"{class_name}.{fn_name}" if class_name else fn_name
                for ptype, pline in collect_param_types_python(func_node):
                    edges.append(Edge(
                        source=source_fn,
                        target=ptype,
                        kind="uses",
                        file=file_str,
                        line=pline,
                        confidence="INFERRED",
                        receiver=None,
                    ))

        body = func_node.child_by_field_name("body")
        if body is None:
            return
        for stmt in body.children:
            record_py_local_types(stmt, var_types)
            _walk_stmt(stmt, class_name, var_types, class_fields)

        # A3: Emit reads/writes edges for field accesses in this function body.
        # Done AFTER the main stmt walk so that var_types is fully populated
        # (record_py_local_types runs incrementally during the stmt walk above).
        # WHY separate pass: fully-populated var_types gives higher-quality receiver
        # resolution than an interleaved walk.
        if field_access_on:
            # Build the qualified source name from the function's own name field.
            # WHY not _find_enclosing_function: that walks UP the parent chain from a
            # child node, which would land on the function itself — identical result
            # but needs a child node as input. Computing directly from the name field
            # is simpler and avoids passing an arbitrary child node.
            name_node = func_node.child_by_field_name("name")
            if name_node is not None:
                fn_name = _text(name_node)
                source_fn = f"{class_name}.{fn_name}" if class_name else fn_name
                for _src, target_field, mode, fa_line in extract_field_accesses_python(
                    body, source_fn, class_name, var_types
                ):
                    edges.append(Edge(
                        source=source_fn,
                        target=target_field,
                        kind=mode,
                        file=file_str,
                        line=fa_line,
                        confidence="INFERRED",
                        receiver=None,
                    ))

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

        # Pre-scan class body for field type bindings BEFORE walking methods.
        # WHY: a method defined above a field declaration should still be able to
        # resolve that field's type (e.g. DI patterns store injected objects as
        # annotated class attributes). This is Layer 1 of the two-layer scope model.
        # When SEAM_TYPE_INFERENCE=off the dict stays empty — inference is skipped
        # entirely, producing the same bare-target edges as pre-Tier-B.
        class_fields: dict[str, str] = {}
        if type_inference_on and cls_name:
            class_fields = scan_class_fields_python(class_node)

        # Slice #77: emit composition (holds) edges.
        # WHY here: this is the single natural place where we have both the class name
        # (cls_name) and the full class AST node. The collector handles dedup internally
        # so we don't risk double-emitting even if the same type appears as both a class
        # field and an __init__ parameter.
        if composition_on and cls_name:
            for held_type, held_line in collect_composition_types_python(class_node):
                edges.append(Edge(
                    source=cls_name,
                    target=held_type,
                    kind="holds",
                    file=file_str,
                    line=held_line,
                    confidence="INFERRED",
                    receiver=None,
                ))

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
