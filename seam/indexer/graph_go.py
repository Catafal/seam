"""Go symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf), graph_scope_infer_ext (leaf), field_access (leaf) —
never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
    field_access       (leaf — field-access read/write classification + field symbols)
         ↑
    graph_go           (this file)
         ↑
    graph_go_rust      (thin re-exporter; graph.py imports from there)

WHY split from graph_go_rust.py: graph_go_rust.py exceeded 1000 lines after Tier B additions.
Go and Rust are now each large enough to stand alone.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config
from seam.indexer.field_access import (
    collect_field_symbols_go,
    extract_field_accesses_go,
)
from seam.indexer.graph_common import (
    Comment,
    Edge,
    Symbol,
    _block_comment_lines,
    _find_enclosing_function,
    _go_recv_type_name,
    _make_symbol,
    _match_marker,
    _node_name,
    _text,
)
from seam.indexer.graph_scope_infer_ext import (
    collect_composition_types_go,
    record_go_local_types,
    record_go_param_types,
    resolve_receiver_type_ext,
)
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)

# ── Doc-comment helper ─────────────────────────────────────────────────────────


def _go_doc_comment(decl_node: Node) -> str | None:
    """Capture a Go doc-comment: contiguous // lines immediately above the decl.

    Walk prev_sibling collecting 'comment' nodes where each successive pair is
    row-adjacent (no blank line gap). Stops at first non-comment or gap.
    Joins lines in source order, stripping '//' prefix from each.
    """
    lines: list[str] = []
    current = decl_node.prev_sibling

    while current is not None and current.type == "comment":
        raw = _text(current)
        if not raw.startswith("//"):
            break
        # Go comment nodes do NOT include trailing newline, so adjacency rule:
        # comment's end_row + 1 == next_node's start_row.
        next_node = current.next_sibling
        if next_node is not None:
            end_row = current.end_point[0]
            next_start_row = next_node.start_point[0]
            if end_row + 1 != next_start_row:
                break
        body = raw[2:].strip()
        lines.append(body)
        current = current.prev_sibling

    if not lines:
        return None

    return "\n".join(reversed(lines))


# ── Go extraction ──────────────────────────────────────────────────────────────


def _extract_symbols_go(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Go AST and extract function, method, struct, interface, type, and field symbols.

    Kind mapping (per spec, existing 5 kinds only for non-field symbols):
        function_declaration  → function
        method_declaration    → method  (qualified as 'Recv.Name', *T normalized to T,
                                         generic receivers Repo[T] → Repo)
        type_spec struct_type → class
        type_spec interface_type → interface
        type_spec other       → type
        type_alias            → type

    A3 Slice 3: when SEAM_FIELD_ACCESS_EDGES='on', also emits kind='field' symbols
    for each struct_type field_declaration, using collect_field_symbols_go.
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

    def _walk(node: Node) -> None:
        if node.type == "function_declaration":
            name = _node_name(node)
            if name:
                doc = _go_doc_comment(node)
                fields = extract_node_fields(
                    node, "go", qualified_name=name, max_signature_len=config.SEAM_MAX_SIGNATURE_LEN
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

        elif node.type == "method_declaration":
            method_name = _node_name(node)
            recv_name = _go_recv_type_name(node)
            if method_name and recv_name:
                qualified = f"{recv_name}.{method_name}"
                doc = _go_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "go",
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
            elif method_name:
                # Receiver parse failed — emit as plain function to avoid silent drop.
                doc = _go_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "go",
                    qualified_name=method_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        method_name,
                        "function",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=method_name,
                    )
                )

        elif node.type == "type_declaration":
            for child in node.named_children:
                if child.type == "type_spec":
                    _handle_go_type_spec(child, node, file_str, symbols)
                    # A3: emit field symbols for struct types when feature is on.
                    # WHY here: type_spec holds both the name and the struct_type node.
                    if field_access_on:
                        _go_emit_field_symbols(child, file_str, symbols)
                elif child.type == "type_alias":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        type_name = _text(name_node)
                        doc = _go_doc_comment(node)
                        fields = extract_node_fields(
                            node,
                            "go",
                            qualified_name=type_name,
                            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                        )
                        symbols.append(
                            _make_symbol(
                                type_name,
                                "type",
                                file_str,
                                node,
                                doc,
                                signature=fields["signature"],
                                decorators=fields["decorators"],
                                is_exported=fields["is_exported"],
                                visibility=fields["visibility"],
                                qualified_name=type_name,
                            )
                        )

        else:
            for child in node.children:
                _walk(child)

    for child in root.children:
        _walk(child)

    return symbols


def _go_emit_field_symbols(
    type_spec: Node,
    file_str: str,
    symbols: list[Symbol],
) -> None:
    """Emit kind='field' symbols for all fields in a Go struct type_spec.

    Called when SEAM_FIELD_ACCESS_EDGES='on'. Walks the type_spec's 'type' child
    (which must be a struct_type) and calls collect_field_symbols_go to get the
    (qualified_field, line) pairs, then appends Symbol entries.

    Never raises (backstop try/except).
    """
    try:
        name_node = type_spec.child_by_field_name("name")
        type_node = type_spec.child_by_field_name("type")
        if name_node is None or type_node is None:
            return
        if type_node.type != "struct_type":
            # WHY skip non-struct type_specs: interface_type and other named types
            # have no stored fields — only struct_type has a field_declaration_list.
            # Interfaces define method signatures, not data, so no field symbols.
            return

        struct_name = _text(name_node).strip()
        if not struct_name:
            return

        for qualified_field, field_line in collect_field_symbols_go(type_node, struct_name):
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
    except Exception as exc:  # noqa: BLE001
        logger.debug("_go_emit_field_symbols: failed: %r", exc)


def _handle_go_type_spec(
    type_spec: Node, decl_node: Node, file_str: str, symbols: list[Symbol]
) -> None:
    """Classify a Go type_spec node and append the appropriate symbol.

    The doc-comment lives above the parent type_declaration (decl_node).
    Phase 4: extract enrichment fields from the parent type_declaration node.
    """
    name_node = type_spec.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node)

    type_node = type_spec.child_by_field_name("type")
    doc = _go_doc_comment(decl_node)

    fields = extract_node_fields(
        decl_node, "go", qualified_name=name, max_signature_len=config.SEAM_MAX_SIGNATURE_LEN
    )

    if type_node is None:
        kind = "type"
    elif type_node.type == "struct_type":
        kind = "class"
    elif type_node.type == "interface_type":
        kind = "interface"
    else:
        kind = "type"

    symbols.append(
        _make_symbol(
            name,
            kind,
            file_str,
            decl_node,
            doc,
            signature=fields["signature"],
            decorators=fields["decorators"],
            is_exported=fields["is_exported"],
            visibility=fields["visibility"],
            qualified_name=name,
        )
    )


def _extract_edges_go(root: Node, filepath: Path) -> list[Edge]:
    """Extract import, call, holds, reads, and writes edges from a Go AST.

    Import heuristic:
        import "pkg/path"         → target = last path segment ('path')
        import ( "pkg/path" ... ) → one edge per import_spec

    Call heuristic:
        call_expression where 'function' field is an identifier → bare call → target = identifier
        call_expression where 'function' field is a selector_expression → recv.Method() →
          receiver = operand text, target = method name.

    Tier B B5: when SEAM_TYPE_INFERENCE is on, selector-expression calls are resolved to
    'Type.method' qualified targets by looking up the receiver identifier in the per-function
    scope map (params + locals). The scope map is rebuilt at each function/method entry.

    Slice #78: when SEAM_COMPOSITION_EDGES is on, struct_type declarations emit
    holds edges for each plain user-type field (pointer fields have * stripped).

    A3 Slice 3: when SEAM_FIELD_ACCESS_EDGES is on, selector_expression nodes that are
    NOT in call position emit reads/writes edges for struct field accesses.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    infer = config.SEAM_TYPE_INFERENCE == "on"
    composition_on = config.SEAM_COMPOSITION_EDGES == "on"
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

    def _walk(node: Node, var_types: dict[str, str]) -> None:
        ntype = node.type

        # Slice #78: emit composition (holds) edges for Go structs.
        # WHY here: type_declaration wraps type_spec which contains the struct_type.
        # We handle it at type_declaration level to get the struct name from the
        # type_spec.name field, then pass the struct_type node to the collector.
        if ntype == "type_declaration" and composition_on:
            _handle_go_struct_holds(node, file_str, edges)
            # Still recurse into children so nested composites are also visited.
            for child in node.children:
                _walk(child, var_types)
            return

        if ntype == "import_declaration":
            _handle_go_import(node, file_str, file_stem, edges)
            return

        if ntype in ("function_declaration", "method_declaration"):
            # Start a fresh scope for each function/method.
            # WHY new dict: Go has no class-level field pre-scan equivalent (fields live
            # on the struct_item, not the method/function). Each function gets only its
            # parameter bindings (and locals accumulated below). A fresh dict prevents
            # bindings from leaking between sibling functions at the same nesting level.
            new_types: dict[str, str] = {}
            if infer:
                record_go_param_types(node, new_types)
            # A3: Also record param types for field-access resolution even when
            # SEAM_TYPE_INFERENCE is off. WHY: field-access receiver resolution
            # uses the same var_types dict; we need the receiver param to be bound
            # (e.g. 'r' → 'Account') for qualified target emission.
            if field_access_on and not infer:
                record_go_param_types(node, new_types)
            for child in node.children:
                _walk(child, new_types)
            # A3: emit field-access (reads/writes) edges for this function body.
            # Done AFTER the body walk so that record_go_local_types has accumulated
            # all local bindings into new_types (incremental during the walk above).
            # WHY separate pass: same reasoning as Python/TS — fully-populated
            # var_types gives better receiver resolution.
            if field_access_on:
                _go_emit_field_access_edges(node, file_str, new_types, edges)
            return

        if infer and ntype in (
            "short_var_declaration",
            "var_declaration",
            "var_spec",
            "assignment_statement",
        ):
            record_go_local_types(node, var_types)

        # Tier B B6: composite_literal (Foo{...}) → instantiates edge.
        if ntype == "composite_literal":
            type_child = node.child_by_field_name("type")
            if type_child is None:
                type_child = next(
                    (c for c in node.children if c.type == "type_identifier"), None
                )
            if type_child is not None and type_child.type == "type_identifier":
                type_name = _text(type_child)
                if type_name:
                    source = _find_enclosing_function(node, "go")
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
                _walk(child, var_types)
            return

        if ntype == "call_expression":
            func_child = node.child_by_field_name("function")
            callee_name: str | None = None
            recv_text: str | None = None

            if func_child and func_child.type == "identifier":
                callee_name = _text(func_child)
            elif func_child and func_child.type == "selector_expression":
                operand = func_child.child_by_field_name("operand")
                field = func_child.child_by_field_name("field")
                if field is not None and field.type == "field_identifier":
                    callee_name = _text(field)
                    if operand is not None:
                        recv_text = _text(operand)

            if callee_name:
                final_target = callee_name
                if infer and recv_text is not None:
                    # Go: pass frozenset() (empty) as self_names because Go has no
                    # universal 'self' keyword. The receiver variable name is set by
                    # the programmer (e.g. 'r', 's', 'c'). Receiver type comes purely
                    # from param type bindings (record_go_param_types), not from a
                    # conventional self-alias. Passing empty frozenset means no
                    # receiver text is treated as "this class instance" — each variable
                    # must be explicitly bound. Conservatism contract: no binding → None.
                    resolved_type = resolve_receiver_type_ext(
                        recv_text, None, var_types, frozenset()
                    )
                    if resolved_type:
                        final_target = f"{resolved_type}.{callee_name}"

                source = _find_enclosing_function(node, "go")
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
            _walk(child, var_types)

    for child in root.children:
        _walk(child, {})

    return edges


def _handle_go_struct_holds(
    type_decl_node: Node, file_str: str, edges: list[Edge]
) -> None:
    """Emit holds edges for each plain user-type field in a Go struct_type.

    Walks the type_declaration's type_spec children. For each type_spec whose
    'type' field is a struct_type, collects (held_type, line) pairs from
    collect_composition_types_go and emits one holds edge per unique held type.

    WHY a separate helper (not inline in _walk):
      Keeps _walk lean and mirrors the Python/TS pattern of a dedicated _handle_*
      function for each edge kind. Also called for nested type declarations inside
      function bodies if they arise (defensive programming).

    Never raises (backstop try/except).
    """
    try:
        for child in type_decl_node.children:
            if child.type != "type_spec":
                continue
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if name_node is None or type_node is None:
                continue
            if type_node.type != "struct_type":
                continue
            struct_name = _text(name_node).strip()
            if not struct_name:
                continue
            for held_type, held_line in collect_composition_types_go(type_node):
                edges.append(
                    Edge(
                        source=struct_name,
                        target=held_type,
                        kind="holds",
                        file=file_str,
                        line=held_line,
                        confidence="INFERRED",
                        receiver=None,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_go_struct_holds: failed: %r", exc)


def _handle_go_import(decl_node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Extract import edges from a Go import_declaration node.

    Handles both single imports (import "pkg") and grouped imports.
    Target is the last path segment (e.g. "path/filepath" → "filepath").
    """
    line = decl_node.start_point[0] + 1

    def _emit_from_spec(spec: Node) -> None:
        path_node = spec.child_by_field_name("path")
        if path_node is None:
            return
        content_node = next(
            (c for c in path_node.named_children if c.type == "interpreted_string_literal_content"),
            None,
        )
        if content_node is None:
            return
        path_str = _text(content_node)
        target = path_str.split("/")[-1] if path_str else ""
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

    for child in decl_node.children:
        if child.type == "import_spec":
            _emit_from_spec(child)
        elif child.type == "import_spec_list":
            for spec in child.children:
                if spec.type == "import_spec":
                    _emit_from_spec(spec)


def _go_extract_receiver_binding(method_node: Node) -> tuple[str | None, str | None]:
    """Extract (receiver_var_name, receiver_type_name) from a Go method_declaration.

    Returns (None, None) when the receiver list is absent or unexpected shape.
    The receiver is in the 'receiver' field of the method_declaration node
    (NOT the 'parameters' field, which holds regular function parameters).

    Example: func (r *Account) M() → ('r', 'Account')
    """
    try:
        recv = method_node.child_by_field_name("receiver")
        if recv is None:
            return None, None
        for pd in recv.named_children:
            if pd.type == "parameter_declaration":
                # Find the variable name (identifier child)
                for child in pd.children:
                    if child.type == "identifier":
                        var_name = _text(child).strip()
                        # Get the type from _go_recv_type_name logic
                        recv_type = _go_recv_type_name(method_node)
                        return var_name if var_name else None, recv_type
        return None, None
    except Exception:  # noqa: BLE001
        return None, None


def _go_emit_field_access_edges(
    func_node: Node,
    file_str: str,
    var_types: dict[str, str],
    edges: list[Edge],
) -> None:
    """Emit reads/writes edges for field accesses in a Go function/method body.

    Called after the main body walk so that var_types is fully populated.
    Determines the qualified source name and impl_type from the function/method node,
    then delegates to extract_field_accesses_go.

    For method_declaration:
      - source_fn = 'RecvType.MethodName'
      - impl_type = 'RecvType' (for receiver resolution of 'r.Field' patterns)
      - Also binds the receiver var (e.g. 'r') → 'Account' in var_types so that
        _go_classify_selector can resolve 'r.Balance' → 'Account.Balance'.
    For function_declaration:
      - source_fn = 'FunctionName'
      - impl_type = None

    WHY bind receiver var: Go has no universal self/this keyword. The receiver
    parameter (e.g. 'r' in 'func (r *Account) M()') is a regular variable binding.
    We must add r → Account to var_types so that resolve_receiver_type_ext can
    resolve it. record_go_param_types only handles the regular function parameters
    (the second parameter_list), NOT the receiver (in the 'receiver' field).

    Never raises (extract_field_accesses_go is backstopped).
    """
    ntype = func_node.type
    if ntype == "method_declaration":
        method_name = _node_name(func_node)
        recv_name = _go_recv_type_name(func_node)
        if not method_name:
            return
        source_fn = f"{recv_name}.{method_name}" if recv_name else method_name
        impl_type = recv_name

        # Bind the receiver variable to the receiver type.
        # WHY: var_types is populated by record_go_param_types for REGULAR params,
        # but the Go receiver is in a separate 'receiver' field and is NOT covered
        # by record_go_param_types. Without this binding, 'r.Balance' where r is
        # the method receiver cannot be resolved to 'Account.Balance'.
        if recv_name:
            recv_var, recv_type = _go_extract_receiver_binding(func_node)
            if recv_var and recv_type and recv_var not in var_types:
                var_types[recv_var] = recv_type

    elif ntype == "function_declaration":
        func_name = _node_name(func_node)
        if not func_name:
            return
        source_fn = func_name
        impl_type = None
    else:
        return

    # Find the body block node.
    body = None
    for child in func_node.children:
        if child.type == "block":
            body = child
            break
    if body is None:
        return

    for _src, target_field, mode, fa_line in extract_field_accesses_go(
        body, source_fn, impl_type, var_types
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


def _extract_comments_go(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Go AST and extract semantic comment markers.

    Go has a single 'comment' node type for both // and /* */ comments.
    For block comments, every line is scanned using _block_comment_lines.
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
