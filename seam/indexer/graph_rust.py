"""Rust symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) and graph_scope_infer_ext (leaf) — never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
         ↑
    graph_rust         (this file)
         ↑
    graph_go_rust      (thin re-exporter; graph.py imports from there)

WHY split from graph_go_rust.py: graph_go_rust.py exceeded 1000 lines after Tier B additions.
Go and Rust are now each large enough to stand alone.

All extractor functions follow the same contract:
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
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _node_name,
    _rust_impl_type_name,
    _text,
)
from seam.indexer.graph_scope_infer_ext import (
    _RUST_SELF_NAMES,
    collect_composition_types_rust,
    record_rust_local_types,
    record_rust_param_types,
    resolve_receiver_type_ext,
    scan_class_fields_rust,
)
from seam.indexer.signatures import extract_node_fields

# ── Doc-comment helper ─────────────────────────────────────────────────────────


def _rust_doc_comment(decl_node: Node) -> str | None:
    """Capture a Rust doc-comment: contiguous '///' line_comment nodes above item.

    Only outer doc-comments ('///') qualify — '//' (plain) and '//!' (inner/module-level)
    are excluded from docstrings (though //! may still match as a semantic marker).

    Adjacency quirk: Rust line_comment nodes include the trailing newline in their
    text, so end_point[0] reports the NEXT physical row (one row past the comment's
    visible last line). Go comments do NOT include the trailing newline.

    Correct adjacency rule for Rust:
        visible_end_row = end_point[0] - (1 if raw_text.endswith("\\n") else 0)
        adjacent        = next_node.start_point[0] == visible_end_row + 1
    """
    lines: list[str] = []
    current = decl_node.prev_sibling

    while current is not None and current.type == "line_comment":
        raw = _text(current)
        if not raw.startswith("///"):
            break
        next_node = current.next_sibling
        if next_node is not None:
            visible_end_row = current.end_point[0] - (1 if raw.endswith("\n") else 0)
            if next_node.start_point[0] != visible_end_row + 1:
                break
        body = raw[3:].strip()
        lines.append(body)
        current = current.prev_sibling

    if not lines:
        return None

    return "\n".join(reversed(lines))


# ── Rust extraction ────────────────────────────────────────────────────────────


def _extract_symbols_rust(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Rust AST and extract fn, struct, enum, trait, and impl-method symbols.

    Kind mapping (per spec, existing 5 kinds only):
        function_item (top-level or in mod)  → function
        function_item inside impl_item       → method  (qualified as 'Type.fn')
        struct_item                          → class
        enum_item                            → type
        trait_item                           → interface
        function_item inside trait_item body → method  (qualified as 'Trait.fn')
        mod_item                             → traversed (NOT emitted as symbol)
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, impl_type: str | None = None) -> None:
        if node.type == "function_item":
            name = _node_name(node)
            if name:
                doc = _rust_doc_comment(node)
                if impl_type is not None:
                    qualified = f"{impl_type}.{name}"
                    fields = extract_node_fields(
                        node,
                        "rust",
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
                    fields = extract_node_fields(
                        node,
                        "rust",
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

        elif node.type == "function_signature_item":
            # Signature-only trait method (no body): fn foo(&self);
            if impl_type is not None:
                name = _node_name(node)
                if name:
                    doc = _rust_doc_comment(node)
                    qualified = f"{impl_type}.{name}"
                    fields = extract_node_fields(
                        node,
                        "rust",
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

        elif node.type == "impl_item":
            type_name = _rust_impl_type_name(node)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    _walk(child, impl_type=type_name)

        elif node.type == "struct_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                struct_name = _text(name_node)
                doc = _rust_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "rust",
                    qualified_name=struct_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        struct_name,
                        "class",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=struct_name,
                    )
                )

        elif node.type == "enum_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                enum_name = _text(name_node)
                doc = _rust_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "rust",
                    qualified_name=enum_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        enum_name,
                        "type",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=enum_name,
                    )
                )

        elif node.type == "trait_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                trait_name = _text(name_node)
                doc = _rust_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "rust",
                    qualified_name=trait_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        trait_name,
                        "interface",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=trait_name,
                    )
                )
                # Recurse into trait body to emit each function_item as a method.
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        _walk(child, impl_type=trait_name)

        elif node.type == "mod_item":
            # Traverse into mod bodies but do NOT emit the mod itself as a symbol.
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    _walk(child, impl_type=None)

        else:
            for child in node.children:
                _walk(child, impl_type)

    for child in root.children:
        _walk(child, impl_type=None)

    return symbols


def _extract_edges_rust(root: Node, filepath: Path) -> list[Edge]:
    """Extract import, call, and holds edges from a Rust AST.

    Import heuristic:
        use std::io::Write        → target = 'Write'
        use std::io::{Read, Write} → targets 'Read', 'Write'
        use name                  → target = 'name'
        use foo as bar            → target = 'foo' (real name, not alias)

    Call heuristic:
        call_expression where 'function' is identifier → bare call → target = identifier.
        call_expression where 'function' is field_expression → recv.method() → capture receiver.

    Tier B B5: when SEAM_TYPE_INFERENCE is on, field_expression calls are resolved to
    'Type.method' qualified targets by looking up the receiver in the per-function scope
    (params + let_declarations). Also handles self.method() → 'Type.method'.

    Slice #78: when SEAM_COMPOSITION_EDGES is on, struct_item nodes emit holds edges
    for each plain user-type field. Happens alongside the Tier B field pre-scan.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    infer = config.SEAM_TYPE_INFERENCE == "on"
    composition_on = config.SEAM_COMPOSITION_EDGES == "on"

    # struct_fields: struct name → field type map; pre-scanned so impl methods can see them.
    struct_fields: dict[str, dict[str, str]] = {}

    def _walk(
        node: Node,
        var_types: dict[str, str],
        impl_type: str | None,
    ) -> None:
        ntype = node.type

        if ntype == "use_declaration":
            _handle_rust_use(node, file_str, file_stem, edges)
            return

        if ntype == "struct_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                sname = _text(name_node).strip()
                if sname:
                    if infer:
                        struct_fields[sname] = scan_class_fields_rust(node)
                    # Slice #78: emit holds edges for each plain user-type field.
                    # WHY here: struct_item is where field declarations live in Rust
                    # (unlike Python/TS where fields are in the class body that is also
                    # walked for method edges). struct_item is a top-level item — safe
                    # to emit here without worrying about nested scopes.
                    if composition_on:
                        for held_type, held_line in collect_composition_types_rust(node):
                            edges.append(
                                Edge(
                                    source=sname,
                                    target=held_type,
                                    kind="holds",
                                    file=file_str,
                                    line=held_line,
                                    confidence="INFERRED",
                                    receiver=None,
                                )
                            )

        if ntype == "impl_item":
            # _rust_impl_type_name returns None when the type field is absent — no guard needed.
            new_impl_type = _rust_impl_type_name(node)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    _walk(child, {}, new_impl_type)
            return

        if ntype == "function_item":
            # Build a fresh per-function scope seeded with struct fields (Layer 1).
            # WHY seed from struct_fields: in Rust, struct fields and impl methods are
            # in separate AST nodes (struct_item vs impl_item). The pre-scan at struct_item
            # populates struct_fields[TypeName]. Here, when inside an impl block for that
            # type, we seed var_types from it so `self.field` lookups can resolve the
            # field's declared type — equivalent to the Python class-body pre-scan.
            # WHY fresh dict: same reason as Go/Python — parameter bindings must not leak
            # between methods sharing the same impl block.
            new_types: dict[str, str] = {}
            if infer:
                if impl_type and impl_type in struct_fields:
                    new_types.update(struct_fields[impl_type])
                record_rust_param_types(node, new_types)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    _walk(child, new_types, impl_type)
            return

        if infer and ntype == "let_declaration":
            record_rust_local_types(node, var_types)

        # Tier B B6: struct_expression (Foo { ... }) → instantiates edge.
        if ntype == "struct_expression":
            type_id = node.child_by_field_name("name")
            if type_id is not None:
                type_name = _text(type_id)
                if type_name:
                    source = _find_enclosing_function(node, "rust")
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
                _walk(child, var_types, impl_type)
            return

        if ntype == "call_expression":
            func_child = node.child_by_field_name("function")
            callee_name: str | None = None
            recv_text: str | None = None

            if func_child and func_child.type == "identifier":
                callee_name = _text(func_child)
            elif func_child and func_child.type == "field_expression":
                value_node = func_child.child_by_field_name("value")
                field_node = func_child.child_by_field_name("field")
                if field_node is not None and field_node.type == "field_identifier":
                    callee_name = _text(field_node)
                    if value_node is not None:
                        recv_text = _text(value_node)
            elif func_child and func_child.type == "scoped_identifier":
                # Tier B B6: Type::new() → instantiates edge.
                name_node = func_child.child_by_field_name("name")
                if name_node is not None and _text(name_node) == "new":
                    path_node = func_child.child_by_field_name("path")
                    if path_node is not None:
                        type_name = _text(path_node)
                        if type_name:
                            source = _find_enclosing_function(node, "rust")
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
                    _walk(child, var_types, impl_type)
                return

            if callee_name:
                final_target = callee_name
                if infer and recv_text is not None:
                    resolved_type = resolve_receiver_type_ext(
                        recv_text, impl_type, var_types, _RUST_SELF_NAMES
                    )
                    if resolved_type:
                        final_target = f"{resolved_type}.{callee_name}"

                source = _find_enclosing_function(node, "rust")
                if source is not None:
                    edges.append(
                        Edge(
                            source=source,
                            target=final_target,
                            kind="call",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=recv_text,
                        )
                    )

        for child in node.children:
            _walk(child, var_types, impl_type)

    for child in root.children:
        _walk(child, {}, None)

    return edges


def _handle_rust_use(use_node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Extract import edges from a Rust use_declaration node.

    Emits one edge per imported name (the REAL exported name, never the alias):
        use ident               → 'ident'
        use path::ident         → 'ident'   (rightmost segment of scoped_identifier)
        use path::{A, B}        → 'A', 'B'  (each member of the use_list)
        use path::*             → skipped   (glob — no single resolvable target)
        use foo as bar          → 'foo'     (use_as_clause: real name, not alias 'bar')
    """
    line = use_node.start_point[0] + 1
    arg = use_node.child_by_field_name("argument")
    if arg is None:
        return

    def _emit(target: str) -> None:
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

    def _collect(node: Node) -> None:
        if node.type == "identifier":
            _emit(_text(node))
        elif node.type == "scoped_identifier":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.type == "identifier":
                _emit(_text(name_node))
        elif node.type == "use_as_clause":
            path_node = node.child_by_field_name("path")
            if path_node is not None:
                if path_node.type == "identifier":
                    _emit(_text(path_node))
                elif path_node.type == "scoped_identifier":
                    name_node = path_node.child_by_field_name("name")
                    if name_node and name_node.type == "identifier":
                        _emit(_text(name_node))
        elif node.type in ("use_list", "use_wildcard"):
            if node.type == "use_wildcard":
                return
            for child in node.named_children:
                _collect(child)
        elif node.type == "scoped_use_list":
            lst = node.child_by_field_name("list")
            if lst is not None:
                for child in lst.named_children:
                    _collect(child)

    _collect(arg)


def _extract_comments_rust(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Rust AST and extract semantic comment markers.

    Rust comment node types:
        line_comment  — covers // (plain), /// (outer doc), //! (inner doc)
        block_comment — /* */ blocks
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "line_comment":
            raw = _text(node)
            base_row = node.start_point[0] + 1
            body = raw.lstrip("/").lstrip("!").strip()
            result = _match_marker(body)
            if result is not None:
                marker, text = result
                comments.append(Comment(marker=marker, text=text, line=base_row))

        elif node.type == "block_comment":
            raw = _text(node)
            base_row = node.start_point[0] + 1
            for offset, body in _block_comment_lines(raw):
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row + offset))

        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments
