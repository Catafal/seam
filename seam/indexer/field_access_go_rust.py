"""Field-access edge helper — Go and Rust read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
helpers from graph_scope_infer_ext. Never imports from graph.py, db.py, or any
non-leaf seam module.

LAYERING:
    graph_common              (leaf — no seam deps)
    graph_scope_infer_ext     (leaf — Go/Rust receiver-type inference)
         ↑
    field_access_go_rust      (this file — field-access classification for Go + Rust)
         ↑
    field_access              (re-exports extract_field_accesses_go/rust +
                               collect_field_symbols_go/rust for backward compat)
    graph_go                  (calls those functions via field_access import)
    graph_rust                (calls those functions via field_access import)

WHY a separate module from field_access.py:
  field_access.py would otherwise exceed the 1000-line limit when Python + TS/JS +
  Go + Rust are all in one file. This follows the graph_scope_infer precedent.

Go AST patterns for field accesses:
  selector_expression  → <operand>.<field_identifier> — the node for ALL member access
  call_expression function=selector_expression → method call — NOT a field access
  inc_statement  → selector_expression ++ → WRITE
  dec_statement  → selector_expression -- → WRITE
  assignment_statement → expression_list op expression_list:
      LHS expression_list contains selector_expression → WRITE (any operator: = += -= ...)
      RHS expression_list contains selector_expression → READ

Rust AST patterns for field accesses:
  field_expression  → <value>.<field_identifier> — the node for ALL member access
  call_expression function=field_expression → method call — NOT a field access
  assignment_expression → LHS field_expression → WRITE; RHS → reads
  compound_assignment_expr → first child field_expression → WRITE

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer_ext import (
    _RUST_SELF_NAMES,
    resolve_receiver_type_ext,
)

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


# ══════════════════════════════════════════════════════════════════════════════
# Go field-access classification (A3 Slice 3)
# ══════════════════════════════════════════════════════════════════════════════
#
# Go AST patterns for field accesses:
#   selector_expression  → <operand>.<field_identifier> — the node for ALL member access
#   call_expression function=selector_expression → method call — NOT a field access
#   inc_statement  → selector_expression ++ → WRITE
#   dec_statement  → selector_expression -- → WRITE
#   assignment_statement → expression_list op expression_list:
#       LHS expression_list contains selector_expression → WRITE (any operator: = += -= ...)
#       RHS expression_list contains selector_expression → READ
#
# Receiver resolution:
#   Go has no universal self keyword. The receiver variable is arbitrary (r, a, s, etc.).
#   We use resolve_receiver_type_ext with empty frozenset() as self_names, so ALL type
#   resolution comes from param/local scope bindings (record_go_param_types pre-populates
#   var_types for the receiver variable like 'r' → 'Account').
#   Unresolvable receivers produce bare field names (AMBIGUOUS at read time).


def extract_field_accesses_go(
    func_body: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a Go function/method body block.

    Args:
        func_body:  The 'block' Node of the function/method body.
        source_fn:  Qualified name of the enclosing function, e.g. 'Account.Get'.
        impl_type:  Enclosing struct name for methods (e.g. 'Account'), or None for
                    top-level functions.
        var_types:  Scope map: param/local name → type name (populated before this call
                    by record_go_param_types + record_go_local_types).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'Type.Field' when receiver resolves; bare 'Field' when unknown.
        mode = 'reads' | 'writes'.
        line = 1-based source line.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _go_walk_block(func_body, source_fn, impl_type, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_go: failed for source=%r: %r", source_fn, exc
        )
    return result


def _go_walk_block(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a Go block/statement collecting field accesses.

    Does NOT recurse into nested function literals (they have their own scope).
    """
    for child in node.children:
        _go_walk_stmt(child, source_fn, impl_type, var_types, result)


def _go_walk_stmt(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single Go statement node for field accesses.

    Handles:
      assignment_statement: LHS selector_expression → write; RHS → reads.
      inc_statement / dec_statement: selector_expression → write.
      All other statements: recurse, collecting reads from selector_expressions.
      Skips nested function_literal nodes (their own scope).
    """
    t = node.type

    # Skip nested function literals — they have their own scope.
    if t == "func_literal":
        return

    if t == "assignment_statement":
        _go_handle_assignment(node, source_fn, impl_type, var_types, result)
        return

    if t in ("inc_statement", "dec_statement"):
        _go_handle_inc_dec(node, source_fn, impl_type, var_types, result)
        return

    # A bare selector_expression (not in call position and not in assignment LHS)
    # is a read. The parent/context check is handled in _go_emit_read_if_not_in_call.
    if t == "selector_expression":
        _go_emit_read_if_not_in_call(node, source_fn, impl_type, var_types, result)
        return

    # Recurse into all other node types.
    for child in node.children:
        _go_walk_stmt(child, source_fn, impl_type, var_types, result)


def _go_handle_assignment(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Go assignment_statement: LHS selector_expression → write; RHS → reads.

    Go assignment_statement structure:
        expression_list  op  expression_list
    where op is '=', '+=', '-=', '*=', '/=', '%=', etc.

    The first child is the LHS expression_list (write targets).
    The last child is the RHS expression_list (read values).

    WHY iterate expression_list children: multi-assignment (a.X, a.Y = 1, 2) has
    multiple selector_expressions in the LHS expression_list.
    """
    children = node.children
    if not children:
        return

    # Find the operator token position to split LHS / RHS.
    # Go grammar: assignment_statement has the operator as a named token
    # (type '=', '+=', '-=', etc.) between two expression_list nodes.
    lhs_node = None
    rhs_node = None
    # The first named child tends to be the LHS expression_list.
    for i, child in enumerate(children):
        if child.type == "expression_list":
            if lhs_node is None:
                lhs_node = child
            else:
                rhs_node = child
                break

    # LHS: each selector_expression in the expression_list is a write target.
    if lhs_node is not None:
        for child in lhs_node.children:
            if child.type == "selector_expression":
                acc = _go_classify_selector(child, impl_type, var_types)
                if acc is not None:
                    target, _ = acc
                    result.append((source_fn, target, "writes", child.start_point[0] + 1))

    # RHS: collect reads from expression_list.
    if rhs_node is not None:
        _go_collect_reads_recursive(rhs_node, source_fn, impl_type, var_types, result)


def _go_handle_inc_dec(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Go inc_statement (r.Field++) and dec_statement (r.Field--).

    Both ++ and -- are writes (they mutate the field value).
    Grammar: inc_statement = selector_expression '++' (or '--')
    The selector_expression is the first child.
    """
    for child in node.children:
        if child.type == "selector_expression":
            acc = _go_classify_selector(child, impl_type, var_types)
            if acc is not None:
                target, _ = acc
                result.append((source_fn, target, "writes", child.start_point[0] + 1))
            break  # Only one operand in inc/dec


def _go_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a Go selector_expression NOT in call position.

    A selector_expression is in call position when its parent is call_expression
    AND it IS the 'function' field of that call. We skip those (method calls).

    WHY start_point comparison: same reasoning as Python/TS — tree-sitter creates
    new Node objects on each field access; start_point is the stable identity.
    """
    parent = node.parent
    if parent is not None and parent.type == "call_expression":
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _go_classify_selector(node, impl_type, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _go_collect_reads_recursive(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a Go expression node.

    Walks the expression tree and emits reads edges for selector_expression nodes
    that are NOT in call position. Skips nested func_literal nodes.
    """
    t = node.type
    if t == "func_literal":
        return

    if t == "selector_expression":
        _go_emit_read_if_not_in_call(node, source_fn, impl_type, var_types, result)
        # Do NOT recurse into selector's children — the operand part ('r' in 'r.X')
        # is not itself a field access target.
        return

    for child in node.children:
        _go_collect_reads_recursive(child, source_fn, impl_type, var_types, result)


def _go_classify_selector(
    node: Node,
    impl_type: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a Go selector_expression node to (qualified_target, receiver_text).

    Go selector_expression:
      operand: the receiver expression (identifier or more complex)
      field: field_identifier — the accessed field/method name

    Returns None when the node lacks the expected shape.
    Returns (target, receiver_text) where:
      - target = 'Type.Field' when receiver resolves (EXTRACTED confidence)
      - target = bare 'Field' when unresolvable (AMBIGUOUS confidence)

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        operand = node.child_by_field_name("operand")
        field = node.child_by_field_name("field")
        if operand is None or field is None:
            return None

        # Only field_identifier fields are real field accesses.
        # (type_identifier would be a package.Type reference — skip)
        if field.type != "field_identifier":
            return None

        field_name = _text(field)
        if not field_name:
            return None

        receiver_text = _text(operand)

        # Go has no universal self — pass empty frozenset() for self_names.
        # The receiver type comes purely from var_types (record_go_param_types),
        # not from any self-alias convention.
        resolved_type = resolve_receiver_type_ext(
            receiver_text, impl_type, var_types, frozenset()
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_go_classify_selector: failed: %r", exc)
        return None


def collect_field_symbols_go(
    struct_node: Node,
    struct_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a Go struct_type node.

    Returns field symbols for ALL field declarations in the struct body.
    Unlike the holds collector (collect_composition_types_go), we do NOT
    filter by PascalCase or user-type constraints — ALL fields are indexed
    because 'who writes balance' must work for primitive-typed fields too.

    Args:
        struct_node:  The struct_type node (the 'type' field of a type_spec).
        struct_name:  Name of the enclosing struct (e.g. 'Account').

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []

        # struct_type contains a field_declaration_list child.
        for child in struct_node.children:
            if child.type != "field_declaration_list":
                continue
            for field_decl in child.named_children:
                if field_decl.type != "field_declaration":
                    continue
                try:
                    _go_collect_field_decl(field_decl, struct_name, result)
                except Exception:  # noqa: BLE001
                    pass
            break  # Only one field_declaration_list per struct

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_go: failed for struct %r: %r", struct_name, exc)
        return []


def _go_collect_field_decl(
    field_decl: Node,
    struct_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract a field symbol from a single Go field_declaration node.

    Go field_declaration has:
      - One or more 'name' field_identifiers (the field names)
      - A 'type' field (the declared type — any type, not filtered)

    Multi-name declarations ('X, Y int') produce one symbol per name.

    WHY no type filter: unlike holds (which only cares about composition with
    user-defined types), field symbols index ALL fields regardless of type.
    A field 'balance: int' is just as important as 'client: Client'.
    """
    # Collect all field_identifier children as field names.
    # The 'name' field in Go field_declaration is either a single field_identifier
    # or we find all field_identifier children for multi-name decls.
    for child in field_decl.children:
        if child.type == "field_identifier":
            field_name = _text(child).strip()
            if field_name:
                qualified = f"{struct_name}.{field_name}"
                result.append((qualified, field_decl.start_point[0] + 1))


# ══════════════════════════════════════════════════════════════════════════════
# Rust field-access classification (A3 Slice 3)
# ══════════════════════════════════════════════════════════════════════════════
#
# Rust AST patterns for field accesses:
#   field_expression  → <value>.<field_identifier> — the node for ALL member access
#   call_expression function=field_expression → method call — NOT a field access
#   assignment_expression → LHS field_expression → WRITE; RHS → reads
#   compound_assignment_expr → first child field_expression → WRITE
#
# Receiver resolution:
#   Rust uses self/Self as conventional receiver aliases (_RUST_SELF_NAMES).
#   Other variables are resolved from var_types (seeded from struct_fields + params).


def extract_field_accesses_rust(
    func_body: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a Rust function body block.

    Args:
        func_body:  The 'block' Node of the function body.
        source_fn:  Qualified name of the enclosing function, e.g. 'Account.deposit'.
        impl_type:  Enclosing struct/impl type name (e.g. 'Account'), or None for
                    top-level functions.
        var_types:  Scope map: param/local name → type name (seeded from struct_fields
                    + record_rust_param_types + record_rust_local_types).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _rust_walk_block(func_body, source_fn, impl_type, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_rust: failed for source=%r: %r", source_fn, exc
        )
    return result


def _rust_walk_block(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a Rust block collecting field accesses.

    Does NOT recurse into nested closure_expression nodes (their own scope).
    """
    for child in node.children:
        _rust_walk_stmt(child, source_fn, impl_type, var_types, result)


def _rust_walk_stmt(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single Rust statement or expression for field accesses.

    Handles:
      expression_statement wrapping assignment_expression / compound_assignment_expr.
      Direct assignment_expression or compound_assignment_expr.
      All other statements: recurse collecting reads from field_expression nodes.
      Skips closure_expression nodes (their own scope).
    """
    t = node.type

    # Skip closures — they have their own scope.
    if t == "closure_expression":
        return

    if t == "assignment_expression":
        _rust_handle_assignment(node, source_fn, impl_type, var_types, result)
        return

    if t == "compound_assignment_expr":
        _rust_handle_compound_assignment(node, source_fn, impl_type, var_types, result)
        return

    # A bare field_expression (not in call position) is a read.
    if t == "field_expression":
        _rust_emit_read_if_not_in_call(node, source_fn, impl_type, var_types, result)
        return

    # Recurse into all other nodes.
    for child in node.children:
        _rust_walk_stmt(child, source_fn, impl_type, var_types, result)


def _rust_handle_assignment(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Rust assignment_expression: left field_expression → write; right → reads.

    Rust grammar:
        assignment_expression = left '=' right
    where left may be a field_expression (self.field = ...).
    """
    left = node.child_by_field_name("left")
    if left is not None and left.type == "field_expression":
        acc = _rust_classify_field_expr(left, impl_type, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _rust_collect_reads_recursive(right, source_fn, impl_type, var_types, result)


def _rust_handle_compound_assignment(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Rust compound_assignment_expr (+=, -=, etc.): left → write; right → reads.

    Rust grammar:
        compound_assignment_expr = left op right
    where 'left' is the field child (tree-sitter uses 'left' field name).
    """
    left = node.child_by_field_name("left")
    if left is not None and left.type == "field_expression":
        acc = _rust_classify_field_expr(left, impl_type, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _rust_collect_reads_recursive(right, source_fn, impl_type, var_types, result)


def _rust_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a Rust field_expression NOT in call position.

    A field_expression is in call position when its parent is call_expression AND
    it IS the 'function' field of that call. We skip those (method calls).
    """
    parent = node.parent
    if parent is not None and parent.type == "call_expression":
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _rust_classify_field_expr(node, impl_type, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _rust_collect_reads_recursive(
    node: Node,
    source_fn: str,
    impl_type: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a Rust expression node.

    Skips closure_expression nodes (their own scope).
    """
    t = node.type
    if t == "closure_expression":
        return

    if t == "field_expression":
        _rust_emit_read_if_not_in_call(node, source_fn, impl_type, var_types, result)
        # Do NOT recurse — the value part ('self' in 'self.field') is not a separate
        # field access target we want to re-emit.
        return

    for child in node.children:
        _rust_collect_reads_recursive(child, source_fn, impl_type, var_types, result)


def _rust_classify_field_expr(
    node: Node,
    impl_type: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a Rust field_expression node to (qualified_target, receiver_text).

    Rust field_expression:
      value: the receiver (e.g. 'self', 'other')
      field: field_identifier — the accessed field name

    Returns None when the node lacks the expected shape.
    Returns (target, receiver_text) where:
      - target = 'Type.field' when receiver resolves (EXTRACTED confidence)
      - target = bare 'field' when unresolvable (AMBIGUOUS confidence)

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        value_node = node.child_by_field_name("value")
        field_node = node.child_by_field_name("field")
        if value_node is None or field_node is None:
            return None

        if field_node.type != "field_identifier":
            return None

        field_name = _text(field_node)
        if not field_name:
            return None

        receiver_text = _text(value_node)

        resolved_type = resolve_receiver_type_ext(
            receiver_text, impl_type, var_types, _RUST_SELF_NAMES
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_rust_classify_field_expr: failed: %r", exc)
        return None


def collect_field_symbols_rust(
    struct_node: Node,
    struct_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a Rust struct_item node.

    Returns field symbols for ALL field declarations in the struct body.
    Unlike the holds collector, we do NOT filter by user-type constraints —
    ALL fields are indexed regardless of their type ('balance: i64', 'name: String').

    Args:
        struct_node:  The struct_item node.
        struct_name:  Name of the struct (e.g. 'Account').

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []

        # struct_item contains a field_declaration_list child.
        for child in struct_node.children:
            if child.type != "field_declaration_list":
                continue
            for field_decl in child.named_children:
                if field_decl.type != "field_declaration":
                    continue
                try:
                    _rust_collect_field_decl(field_decl, struct_name, result)
                except Exception:  # noqa: BLE001
                    pass
            break  # Only one field_declaration_list per struct

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_rust: failed for struct %r: %r", struct_name, exc)
        return []


def _rust_collect_field_decl(
    field_decl: Node,
    struct_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract a field symbol from a single Rust field_declaration node.

    Rust field_declaration:
      name: field_identifier  (the field name)
      type: <type node>       (any type)

    WHY no type filter: same reasoning as Go — ALL fields are indexed so that
    queries like 'who writes balance' work for primitive-typed fields too.
    """
    name_node = field_decl.child_by_field_name("name")
    if name_node is None:
        # Fallback: find first field_identifier child
        for child in field_decl.children:
            if child.type == "field_identifier":
                name_node = child
                break
    if name_node is None:
        return

    field_name = _text(name_node).strip()
    if not field_name:
        return

    qualified = f"{struct_name}.{field_name}"
    result.append((qualified, field_decl.start_point[0] + 1))
