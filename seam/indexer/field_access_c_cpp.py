"""Field-access edge helper — C and C++ read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
helpers from graph_scope_infer_ext2. Never imports from graph.py, db.py, or any
non-leaf seam module.

LAYERING:
    graph_common                  (leaf — no seam deps)
    graph_scope_infer_ext2        (leaf — Java/C#/C++/Ruby/PHP receiver-type inference)
         ↑
    field_access_c_cpp      (this file — field-access classification for C and C++)
         ↑
    field_access_ext        (re-exports extract_field_accesses_c/cpp +
                             collect_field_symbols_c/cpp for backward compat)
    graph_c          (calls those functions via field_access_ext import)
    graph_cpp        (calls those functions via field_access_ext import)

WHY a separate module from field_access_ext.py:
  field_access_ext.py (Java + C#) would otherwise exceed the 1000-line limit with
  C and C++ added. This follows the graph_c_cpp.py split precedent.

C AST patterns for field accesses:
  field_expression  → <argument>.<field_identifier> or <argument>-><field_identifier>
  Both dot (.) and arrow (->) accesses use field_expression.
  call_expression function=field_expression → function pointer call — NOT field access
  assignment_expression left=field_expression → WRITE
  update_expression argument=field_expression → WRITE (++ / --)

C++ AST patterns for field accesses:
  field_expression  → same node type as C for BOTH dot and arrow access
  this->field: argument='this', operator='->'
  call_expression function=field_expression → method/function-pointer call — NOT field
  assignment_expression left=field_expression → WRITE
  update_expression argument=field_expression → WRITE (++ / --)

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import _CPP_SELF_NAMES

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


# ══════════════════════════════════════════════════════════════════════════════
# C field-access classification (A3 Slice 4)
# ══════════════════════════════════════════════════════════════════════════════
#
# C AST patterns for field accesses:
#   field_expression  → <argument>.<field_identifier> or <argument>-><field_identifier>
#   Both dot (.) and arrow (->) accesses use field_expression.
#   call_expression function=field_expression → function pointer call — NOT field access
#   assignment_expression left=field_expression → WRITE
#   update_expression argument=field_expression → WRITE (++ / --)
#
# Receiver resolution for C:
#   C has no class concept in free functions. We look up var_types for typed struct
#   pointers. If var_types has the receiver → qualified StructName.field.
#   Otherwise → bare field name (AMBIGUOUS confidence).
#   There are no self-names in C (frozenset()).


def extract_field_accesses_c(
    func_body: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a C function body.

    Args:
        func_body:   The 'compound_statement' (body) Node of the function.
        source_fn:   Name of the enclosing function, e.g. 'get_balance'.
        struct_name: None (C functions are not inside a struct; kept for API parity).
        var_types:   Scope map: param/local name → struct type name (for pointer params).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _c_walk_body(func_body, source_fn, struct_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_c: failed for source=%r: %r", source_fn, exc
        )
    return result


def _c_walk_body(
    node: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a C function body collecting field accesses."""
    for child in node.children:
        _c_walk_stmt(child, source_fn, struct_name, var_types, result)


def _c_walk_stmt(
    node: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single C statement node for field accesses."""
    t = node.type

    # assignment_expression: left=field_expression → write.
    if t == "assignment_expression":
        _c_handle_assignment(node, source_fn, struct_name, var_types, result)
        return

    # update_expression (++/--): argument=field_expression → write.
    if t == "update_expression":
        _c_handle_update(node, source_fn, struct_name, var_types, result)
        return

    # call_expression: function child may be field_expression (function pointer call).
    # Do NOT emit a field edge for the function child. Recurse into arguments.
    if t == "call_expression":
        args = node.child_by_field_name("arguments")
        if args is not None:
            for arg in args.children:
                _c_walk_stmt(arg, source_fn, struct_name, var_types, result)
        return

    # A bare field_expression (not in call position) → read.
    if t == "field_expression":
        _c_emit_read_if_not_in_call(node, source_fn, struct_name, var_types, result)
        return

    # Recurse into all other nodes.
    for child in node.children:
        _c_walk_stmt(child, source_fn, struct_name, var_types, result)


def _c_handle_assignment(
    node: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle C assignment_expression: left field_expression → write; right → reads."""
    left = node.child_by_field_name("left")
    if left is not None and left.type == "field_expression":
        acc = _c_classify_field_expr(left, struct_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _c_collect_reads_recursive(right, source_fn, struct_name, var_types, result)


def _c_handle_update(
    node: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle C update_expression (++/--): argument field_expression → write."""
    operand = node.child_by_field_name("argument")
    if operand is None:
        for child in node.children:
            if child.type == "field_expression":
                operand = child
                break
    if operand is not None and operand.type == "field_expression":
        acc = _c_classify_field_expr(operand, struct_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", operand.start_point[0] + 1))


def _c_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a C field_expression NOT in call position.

    A field_expression is in call position when its parent is call_expression AND
    it IS the 'function' field of that call.
    """
    parent = node.parent
    if parent is not None and parent.type == "call_expression":
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _c_classify_field_expr(node, struct_name, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _c_collect_reads_recursive(
    node: Node,
    source_fn: str,
    struct_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a C expression node."""
    t = node.type
    if t == "field_expression":
        _c_emit_read_if_not_in_call(node, source_fn, struct_name, var_types, result)
        return

    for child in node.children:
        _c_collect_reads_recursive(child, source_fn, struct_name, var_types, result)


def _c_classify_field_expr(
    node: Node,
    struct_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a C field_expression node to (qualified_target, receiver_text).

    C field_expression:
      argument: the receiver expression (e.g. 'p', 's')
      field: field_identifier — the accessed field name
      operator: '.' (dot) or '->' (arrow)

    Returns None when the node lacks the expected shape.
    Returns (target, receiver_text) where:
      - target = 'StructName.field' when receiver type is known in var_types
      - target = bare 'field' when unresolvable (AMBIGUOUS confidence)

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        arg_node = node.child_by_field_name("argument")
        field_node = node.child_by_field_name("field")
        if arg_node is None or field_node is None:
            return None

        # Only field_identifier fields are real struct field accesses.
        if field_node.type != "field_identifier":
            return None

        field_name = _text(field_node)
        if not field_name:
            return None

        receiver_text = _text(arg_node)
        # Strip leading '*' for pointer dereference (e.g. (*p).field or *p → p).
        # WHY strip: C struct access via pointer uses `->` (tree-sitter handles both
        # dot and arrow as field_expression), but explicit dereference `(*p).x` also
        # occurs. The receiver text would be `*p`; after stripping, `p` can be looked
        # up in var_types to resolve the struct type.
        clean_recv = receiver_text.lstrip("*").strip()

        # C has no self convention — pass empty frozenset().
        # WHY: Unlike C++/Java/Python, C free functions have no implicit receiver.
        # All struct type resolution must come from explicit var_types bindings
        # (e.g. `Account *a` → var_types['a'] = 'Account'). If the receiver is not
        # in var_types, we emit a bare field name rather than guessing.
        resolved_type = resolve_receiver_type_ext(
            clean_recv, struct_name, var_types, frozenset()
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_c_classify_field_expr: failed: %r", exc)
        return None


def collect_field_symbols_c(
    struct_node: Node,
    struct_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a C struct_specifier node.

    Returns field symbols for ALL field_declaration nodes in the struct body.
    Each field_declaration contains one or more field_identifier children (field names).

    Unlike the holds collector (collect_composition_types_*), we do NOT filter by type —
    ALL fields are indexed regardless of their type.

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []

        # struct_specifier contains a field_declaration_list child.
        for child in struct_node.children:
            if child.type != "field_declaration_list":
                continue
            for field_decl in child.named_children:
                if field_decl.type != "field_declaration":
                    continue
                try:
                    _c_collect_field_decl(field_decl, struct_name, result)
                except Exception:  # noqa: BLE001
                    pass
            break  # Only one field_declaration_list per struct

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_c: failed for struct %r: %r", struct_name, exc)
        return []


def _c_collect_field_decl(
    field_decl: Node,
    struct_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract field symbols from a single C field_declaration node.

    C field_declaration:
      type_specifier field_identifier (',' field_identifier)* ';'
      (Each field_identifier is a field name)
    """
    for child in field_decl.children:
        if child.type == "field_identifier":
            field_name = _text(child).strip()
            if field_name:
                qualified = f"{struct_name}.{field_name}"
                result.append((qualified, field_decl.start_point[0] + 1))


# ══════════════════════════════════════════════════════════════════════════════
# C++ field-access classification (A3 Slice 4)
# ══════════════════════════════════════════════════════════════════════════════
#
# C++ AST patterns for field accesses:
#   field_expression  → same node type as C for BOTH dot and arrow access
#   this->field: argument='this', operator='->'
#   call_expression function=field_expression → method/function-pointer call — NOT field
#   assignment_expression left=field_expression → WRITE
#   update_expression argument=field_expression → WRITE (++ / --)
#
# Receiver resolution for C++:
#   'this' → enclosing class via _CPP_SELF_NAMES (EXTRACTED confidence).
#   Others via resolve_receiver_type_ext using var_types.


def extract_field_accesses_cpp(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a C++ method/function body.

    Args:
        func_body:   The 'compound_statement' (body) Node of the function.
        source_fn:   Qualified name of the enclosing function, e.g. 'Account.getBalance'.
        class_name:  Enclosing class/struct name (None for free functions).
        var_types:   Scope map: param/local name → type name.

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _cpp_walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_cpp: failed for source=%r: %r", source_fn, exc
        )
    return result


def _cpp_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a C++ function body collecting field accesses."""
    for child in node.children:
        _cpp_walk_stmt(child, source_fn, class_name, var_types, result)


def _cpp_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single C++ statement node for field accesses."""
    t = node.type

    # Skip nested scope-creating constructs.
    if t in ("class_specifier", "struct_specifier", "function_definition",
             "lambda_expression"):
        return

    # assignment_expression: left=field_expression → write.
    if t == "assignment_expression":
        _cpp_handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # update_expression (++/--): → write.
    if t == "update_expression":
        _cpp_handle_update(node, source_fn, class_name, var_types, result)
        return

    # call_expression: function child may be field_expression (method/function-ptr call).
    # Do NOT emit a field edge for the function child. Recurse into arguments.
    if t == "call_expression":
        args = node.child_by_field_name("arguments")
        if args is not None:
            for arg in args.children:
                _cpp_walk_stmt(arg, source_fn, class_name, var_types, result)
        return

    # A bare field_expression (not in call position) → read.
    if t == "field_expression":
        _cpp_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    # Recurse into all other nodes.
    for child in node.children:
        _cpp_walk_stmt(child, source_fn, class_name, var_types, result)


def _cpp_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle C++ assignment_expression: left field_expression → write; right → reads."""
    left = node.child_by_field_name("left")
    if left is not None and left.type == "field_expression":
        acc = _cpp_classify_field_expr(left, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _cpp_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _cpp_handle_update(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle C++ update_expression (++/--): argument field_expression → write."""
    operand = node.child_by_field_name("argument")
    if operand is None:
        for child in node.children:
            if child.type == "field_expression":
                operand = child
                break
    if operand is not None and operand.type == "field_expression":
        acc = _cpp_classify_field_expr(operand, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", operand.start_point[0] + 1))


def _cpp_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a C++ field_expression NOT in call position."""
    parent = node.parent
    if parent is not None and parent.type == "call_expression":
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _cpp_classify_field_expr(node, class_name, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _cpp_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a C++ expression node."""
    t = node.type
    if t in ("class_specifier", "struct_specifier", "function_definition",
             "lambda_expression"):
        return

    if t == "field_expression":
        _cpp_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    for child in node.children:
        _cpp_collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _cpp_classify_field_expr(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a C++ field_expression node to (qualified_target, receiver_text).

    C++ field_expression (same structure as C):
      argument: the receiver expression (e.g. 'this', 'p', 's')
      field: field_identifier — the accessed field name
      operator: '.' or '->'

    'this' resolves to the enclosing class via _CPP_SELF_NAMES.
    Other receivers resolved via var_types (resolve_receiver_type_ext).

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        arg_node = node.child_by_field_name("argument")
        field_node = node.child_by_field_name("field")
        if arg_node is None or field_node is None:
            return None

        if field_node.type != "field_identifier":
            return None

        field_name = _text(field_node)
        if not field_name:
            return None

        receiver_text = _text(arg_node)
        clean_recv = receiver_text.lstrip("*").strip()

        resolved_type = resolve_receiver_type_ext(
            clean_recv, class_name, var_types, _CPP_SELF_NAMES
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_cpp_classify_field_expr: failed: %r", exc)
        return None


def collect_field_symbols_cpp(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a C++ class_specifier or struct_specifier.

    Returns field symbols for ALL field_declaration nodes in the class/struct body.
    C++ field_declaration_list body → field_declaration nodes.

    Unlike the holds collector, we do NOT filter by user-type constraints —
    ALL fields are indexed regardless of their type.

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []
        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        for child in body.named_children:
            if child.type == "field_declaration":
                try:
                    _cpp_collect_field_decl(child, class_name, result)
                except Exception:  # noqa: BLE001
                    pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_cpp: failed for class %r: %r", class_name, exc)
        return []


def _cpp_collect_field_decl(
    field_decl: Node,
    class_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract field symbols from a single C++ field_declaration node.

    C++ field_declaration in a class body:
      type_specifier declarator+ ';'
      The declarator(s) can be identifier or field_identifier nodes.
    """
    for child in field_decl.children:
        # In C++ class body, field names appear as field_identifier nodes.
        # Simple cases also have identifier nodes.
        if child.type in ("field_identifier", "identifier"):
            field_name = _text(child).strip()
            # WHY keyword filter: C++ tree-sitter sometimes surfaces access specifiers
            # and storage/qualifier keywords as bare identifier tokens inside a
            # field_declaration (e.g. `static int x` may produce an 'identifier' child
            # with text 'static'). These are not field names and must be excluded to
            # avoid creating phantom field symbols with names like 'static' or 'const'.
            if field_name and field_name not in ("public", "private", "protected",
                                                  "static", "const", "virtual",
                                                  "inline", "explicit", "override"):
                qualified = f"{class_name}.{field_name}"
                result.append((qualified, field_decl.start_point[0] + 1))
