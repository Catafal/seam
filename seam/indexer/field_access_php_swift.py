"""Field-access edge helper — PHP and Swift read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + graph_common + the existing
resolve_receiver_type helpers. Never imports from graph.py, db.py, or any
non-leaf seam module.

LAYERING:
    graph_common                  (leaf — no seam deps)
    graph_scope_infer_ext         (leaf — Go/Rust receiver-type inference)
    graph_scope_infer_ext2        (leaf — Java/C#/C++/Ruby/PHP receiver-type inference)
         ↑
    field_access_php_swift  (this file — field-access classification for PHP + Swift)
         ↑
    field_access_ext2       (re-exports extract_field_accesses_php/swift +
                             collect_field_symbols_php/swift for backward compat)
    graph_php      (calls those functions via field_access_ext2 import)
    graph_swift    (calls those functions via field_access_ext2 import;
                   also calls emit_swift_field_access_edges and
                   emit_swift_field_symbols directly)

WHY a separate module from field_access_ext2.py:
  field_access_ext2.py (Ruby + PHP + Swift) exceeds 1000 lines. Splitting PHP + Swift
  into this module keeps both resulting files under the limit.
  This also hosts the Swift field-access emission helpers called by graph_swift.py,
  which are moved here to keep graph_swift.py under 1000 lines.

PHP member access uses member_access_expression: $this->field or $obj->field.
This is DISTINCT from member_call_expression ($obj->method()):
  member_access_expression  → field access (may be read or write)
  member_call_expression    → method call (NOT a field edge)

Swift stored-property access uses navigation_expression: self.prop or obj.prop.
Call position: call_expression whose first child is a navigation_expression.
  → the navigation_expression is the callee → NOT a field edge.

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import (
    Edge,
    Symbol,
    _text,
)
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import _PHP_SELF_NAMES

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


# ══════════════════════════════════════════════════════════════════════════════
# PHP field-access classification (A3 Slice 5)
# ══════════════════════════════════════════════════════════════════════════════
#
# PHP member access uses member_access_expression: $this->field or $obj->field.
# This is DISTINCT from member_call_expression ($obj->method()):
#   member_access_expression  → field access (may be read or write)
#   member_call_expression    → method call (NOT a field edge)
#
# PHP augmented assignment:
#   augmented_assignment_expression LHS member_access_expression → write
#
# Receiver resolution:
#   $this → enclosing class via _PHP_SELF_NAMES (EXTRACTED).
#   Other vars: resolved via resolve_receiver_type_ext from var_types.
#   Unresolvable: bare field name (AMBIGUOUS).
#
# Field symbols:
#   property_declaration nodes in the class body (declaration_list).
#   The property_element child contains variable_name ($field).
#   Strip the '$' prefix for the qualified target: '$balance' → 'balance'.


def extract_field_accesses_php(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a PHP method/function body.

    Args:
        func_body:    The 'compound_statement' body Node of the method/function.
        source_fn:    Qualified name of the enclosing function, e.g. 'Account.deposit'.
        class_name:   Enclosing class name (None for top-level functions).
        var_types:    Scope map: param/local variable name → type name (with '$' prefix).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'ClassName.field' when $this receiver; bare 'field' otherwise.
        mode = 'reads' | 'writes'.
        line = 1-based source line.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _php_walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_php: failed for source=%r: %r", source_fn, exc
        )
    return result


def _php_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a PHP function/method body collecting field accesses."""
    for child in node.children:
        _php_walk_stmt(child, source_fn, class_name, var_types, result)


def _php_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single PHP statement node for field accesses.

    Handles:
      expression_statement containing assignment/augmented_assignment → extract
      assignment_expression: LHS member_access_expression → write.
      augmented_assignment_expression: LHS member_access_expression → write.
      member_call_expression: NOT a field edge (method call). Recurse into arguments.
      member_access_expression (standalone, not in call position) → read.
      All other nodes: recurse collecting reads.
      Skips nested class/function/method definitions.
    """
    t = node.type

    # Skip nested scope-creating constructs.
    if t in ("class_declaration", "function_definition", "method_declaration",
             "arrow_function", "anonymous_function_creation_expression"):
        return

    # assignment_expression: $this->field = v → write; RHS → reads.
    if t == "assignment_expression":
        _php_handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # augmented_assignment_expression: $this->field += v → write; RHS → reads.
    if t == "augmented_assignment_expression":
        _php_handle_augmented_assignment(node, source_fn, class_name, var_types, result)
        return

    # member_call_expression: $this->method() → NOT a field edge. Recurse into args.
    if t in ("member_call_expression", "nullsafe_member_call_expression"):
        # Only recurse into the arguments — not the object/name fields.
        args = node.child_by_field_name("arguments")
        if args is not None:
            for arg in args.children:
                _php_walk_stmt(arg, source_fn, class_name, var_types, result)
        return

    # A bare member_access_expression (not in call position) → read.
    if t == "member_access_expression":
        _php_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    # Recurse into all other node types.
    for child in node.children:
        _php_walk_stmt(child, source_fn, class_name, var_types, result)


def _php_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle PHP assignment_expression: LHS member_access_expression → write."""
    left = node.child_by_field_name("left")
    if left is not None and left.type == "member_access_expression":
        acc = _php_classify_member_access(left, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _php_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _php_handle_augmented_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle PHP augmented_assignment_expression: LHS member_access_expression → write.

    PHP grammar: augmented_assignment_expression has 'left' and 'right' fields.
    """
    left = node.child_by_field_name("left")
    if left is not None and left.type == "member_access_expression":
        acc = _php_classify_member_access(left, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _php_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _php_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a PHP member_access_expression NOT in call position.

    A member_access_expression is in call position when its parent is a
    member_call_expression/nullsafe_member_call_expression AND it is the
    'object' part of that call. We handle this by not reaching here for such cases
    (the _php_walk_stmt handles member_call_expression separately and recurses only
    into arguments, not the object).
    """
    parent = node.parent
    if parent is not None and parent.type in (
        "member_call_expression", "nullsafe_member_call_expression"
    ):
        # If this member_access_expression is the 'object' field of a call → skip.
        obj_field = parent.child_by_field_name("object")
        if obj_field is not None and obj_field.start_point == node.start_point:
            return  # It's the receiver of a method call — skip

    acc = _php_classify_member_access(node, class_name, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _php_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a PHP expression node."""
    t = node.type
    if t in ("class_declaration", "function_definition", "method_declaration",
             "arrow_function", "anonymous_function_creation_expression"):
        return

    if t == "member_access_expression":
        _php_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    for child in node.children:
        _php_collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _php_classify_member_access(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a PHP member_access_expression to (qualified_target, receiver_text).

    PHP member_access_expression:
      object: the receiver ($this, $obj, variable_name)
      '->': the access operator
      name: the field name ('name' node)

    Returns None when the node lacks the expected shape.
    Returns (target, receiver_text) where:
      - target = 'ClassName.field' when $this / $self resolves (EXTRACTED confidence)
      - target = 'Type.field' when receiver type is known via var_types (EXTRACTED)
      - target = bare 'field' when unresolvable (AMBIGUOUS confidence)
      receiver_text = raw receiver text (e.g. '$this', '$obj')

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        obj_node = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if obj_node is None or name_node is None:
            return None

        # name_node should be a 'name' node (plain identifier).
        if name_node.type != "name":
            return None

        field_name = _text(name_node)
        if not field_name:
            return None

        receiver_text = _text(obj_node)

        resolved_type = resolve_receiver_type_ext(
            receiver_text, class_name, var_types, _PHP_SELF_NAMES
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_php_classify_member_access: failed: %r", exc)
        return None


def collect_field_symbols_php(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a PHP class_declaration.

    Returns field symbols for ALL property_declaration nodes in the class body
    (declaration_list). Each property_declaration may have one or more property_element
    children. The field name has the '$' prefix stripped: '$balance' → 'Account.balance'.

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []
        body = class_node.child_by_field_name("body")
        if body is None:
            # PHP class body is in a 'declaration_list' child
            for child in class_node.children:
                if child.type == "declaration_list":
                    body = child
                    break
        if body is None:
            return result

        for child in body.named_children:
            if child.type == "property_declaration":
                try:
                    _php_collect_property_decl(child, class_name, result)
                except Exception:  # noqa: BLE001
                    pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_php: failed for class %r: %r", class_name, exc)
        return []


def _php_collect_property_decl(
    prop_decl: Node,
    class_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract field symbols from a single PHP property_declaration node.

    PHP property_declaration structure:
      visibility_modifier? type? property_element+ ';'
    Each property_element contains a variable_name node ($field_name).
    Strip the '$' prefix for the field symbol name.
    """
    for child in prop_decl.children:
        if child.type == "property_element":
            for pc in child.children:
                if pc.type == "variable_name":
                    raw_name = _text(pc).strip()
                    field_name = raw_name.lstrip("$")
                    if field_name:
                        qualified = f"{class_name}.{field_name}"
                        result.append((qualified, prop_decl.start_point[0] + 1))


# ══════════════════════════════════════════════════════════════════════════════
# Swift field-access classification (A3 Slice 5)
# ══════════════════════════════════════════════════════════════════════════════
#
# Swift stored-property access uses navigation_expression: self.prop or obj.prop.
# Call position: call_expression whose first child is a navigation_expression.
#   → the navigation_expression is the callee → NOT a field edge.
# Assignment: Swift uses a dedicated 'assignment' node wrapping
#   directly_assignable_expression (LHS) and the value (RHS).
# Augmented assignment: assignment node with an operator child (+=, -=, etc.).
#
# Receiver resolution:
#   self_expression → enclosing class (EXTRACTED).
#   Other vars: resolved via var_types lookup.
#   Unresolvable: bare field name (AMBIGUOUS).


def extract_field_accesses_swift(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a Swift function body.

    Args:
        func_body:    The 'function_body' or 'statements' Node of the function.
        source_fn:    Qualified name of the enclosing function, e.g. 'Account.deposit'.
        class_name:   Enclosing class/struct/actor name (None for top-level functions).
        var_types:    Scope map: param/local name → type name (pre-populated with class
                      properties via _scan_class_properties + _record_param_types).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'ClassName.field' when self receiver; bare 'field' otherwise.
        mode = 'reads' | 'writes'.
        line = 1-based source line.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _swift_walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_swift: failed for source=%r: %r", source_fn, exc
        )
    return result


def _swift_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a Swift function body collecting field accesses."""
    # function_body contains a 'statements' child; handle both shapes.
    for child in node.children:
        _swift_walk_stmt(child, source_fn, class_name, var_types, result)


def _swift_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single Swift statement/expression node for field accesses.

    Handles:
      assignment: LHS is directly_assignable_expression → extract write/read.
      call_expression: first child is navigation_expression → NOT a field edge.
      navigation_expression (standalone, not in call position) → read.
      All other nodes: recurse collecting reads.
      Skips nested function_declaration and class_declaration (own scope).
    """
    t = node.type

    # Skip nested scope-creating constructs.
    if t in ("function_declaration", "class_declaration"):
        return

    # assignment: LHS directly_assignable_expression → write; RHS → reads.
    if t == "assignment":
        _swift_handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # call_expression: first child is the callee. If callee is navigation_expression,
    # it's a method call — NOT a field edge. Recurse into arguments only.
    if t == "call_expression":
        _swift_handle_call_expression(node, source_fn, class_name, var_types, result)
        return

    # A bare navigation_expression (not in call position) → read.
    if t == "navigation_expression":
        _swift_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    # Recurse into all other nodes (return, if, for, while, switch, etc.).
    for child in node.children:
        _swift_walk_stmt(child, source_fn, class_name, var_types, result)


def _swift_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Swift assignment node: LHS navigation → write; RHS → reads.

    Swift assignment node structure:
      directly_assignable_expression  operator  value
    where:
      directly_assignable_expression wraps a navigation_expression for property access.
      operator is '=' (plain) or '+=' etc. (augmented) — both are classified as WRITE.
    """
    lhs = None
    rhs = None
    children = list(node.children)
    for i, child in enumerate(children):
        if child.type == "directly_assignable_expression":
            lhs = child
        elif lhs is not None and child.type not in (
            "=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="
        ):
            # The value is after the operator — any non-operator child after LHS.
            if child.type not in ("directly_assignable_expression",):
                rhs = child
                break

    # LHS: look for navigation_expression inside directly_assignable_expression.
    if lhs is not None:
        nav = _swift_find_navigation_in_assignable(lhs)
        if nav is not None:
            acc = _swift_classify_navigation(nav, class_name, var_types)
            if acc is not None:
                target, _ = acc
                result.append((source_fn, target, "writes", nav.start_point[0] + 1))

    # RHS: collect reads.
    if rhs is not None:
        _swift_collect_reads_recursive(rhs, source_fn, class_name, var_types, result)


def _swift_find_navigation_in_assignable(
    node: Node,
) -> Node | None:
    """Find the navigation_expression inside a directly_assignable_expression.

    Returns None if no navigation_expression is found (e.g. plain variable).
    """
    if node.type == "navigation_expression":
        return node
    for child in node.children:
        if child.type == "navigation_expression":
            return child
    return None


def _swift_handle_call_expression(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle call_expression: skip navigation_expression callee; recurse into arguments.

    A call_expression whose first child is a navigation_expression is a method call
    (e.g. self.doWork()). We must NOT emit a field edge for the callee navigation.
    However, arguments may contain field reads — recurse into the call_suffix.
    """
    if not node.children:
        return

    for child in node.children:
        if child.type == "call_suffix":
            for sub in child.children:
                _swift_walk_stmt(sub, source_fn, class_name, var_types, result)
        elif child.type == "value_arguments":
            for sub in child.children:
                _swift_walk_stmt(sub, source_fn, class_name, var_types, result)
        # Skip the callee (first child) — it's either a simple_identifier or
        # navigation_expression (method call); either way, not a field read.


def _swift_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a Swift navigation_expression NOT in call position.

    A navigation_expression is in call position when its parent is a call_expression
    AND it is the first child (callee). We skip those (method calls).
    """
    parent = node.parent
    if parent is not None and parent.type == "call_expression":
        if parent.children and parent.children[0].start_point == node.start_point:
            return  # Callee of a call expression — not a field read

    acc = _swift_classify_navigation(node, class_name, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _swift_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a Swift expression node."""
    t = node.type
    if t in ("function_declaration", "class_declaration"):
        return

    if t == "navigation_expression":
        _swift_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        # Do NOT recurse into navigation_expression children — the receiver part
        # ('self' in 'self.balance') is not itself a field access target to re-emit.
        return

    if t == "call_expression":
        # In RHS expressions, call arguments may contain field reads.
        _swift_handle_call_expression(node, source_fn, class_name, var_types, result)
        return

    for child in node.children:
        _swift_collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _swift_classify_navigation(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a Swift navigation_expression to (qualified_target, receiver_text).

    Returns None when the node lacks the expected shape.
    Returns (target, receiver_text) where:
      - target = 'ClassName.field' when self_expression receiver (EXTRACTED)
      - target = 'Type.field' when var_types resolves the receiver (EXTRACTED)
      - target = bare 'field' when unresolvable (AMBIGUOUS)
      receiver_text = raw receiver text (e.g. 'self', 'account')

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        children = list(node.children)
        if not children:
            return None

        receiver_node = children[0]
        nav_suffix = None
        for child in children:
            if child.type == "navigation_suffix":
                nav_suffix = child
                break
        if nav_suffix is None:
            return None

        field_name = None
        for child in nav_suffix.children:
            if child.type == "simple_identifier":
                field_name = _text(child)
                break
        if not field_name:
            return None

        receiver_text = _text(receiver_node)

        # self_expression → enclosing class (EXTRACTED).
        if receiver_node.type == "self_expression":
            if class_name:
                return f"{class_name}.{field_name}", receiver_text
            else:
                return field_name, receiver_text

        # Try var_types lookup for other receivers.
        resolved_type: str | None = None
        if receiver_text and receiver_text in var_types:
            resolved_type = var_types[receiver_text]

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_swift_classify_navigation: failed: %r", exc)
        return None


def collect_field_symbols_swift(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a Swift class/struct/actor node.

    Returns field symbols for ALL stored property_declaration nodes in the class_body.
    A stored property has no computed body (no '{' block directly in the declaration).

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []

        body = None
        for child in class_node.children:
            if child.type == "class_body":
                body = child
                break
        if body is None:
            return result

        for child in body.children:
            if child.type == "property_declaration":
                try:
                    _swift_collect_property_decl(child, class_name, result)
                except Exception:  # noqa: BLE001
                    pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "collect_field_symbols_swift: failed for class %r: %r", class_name, exc
        )
        return []


def _swift_collect_property_decl(
    prop_decl: Node,
    class_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract a field symbol from a Swift property_declaration node.

    Skip computed properties (they have a computed_property, computed_value_body,
    or code_block child). Only stored properties (with no computed body) are indexed.
    """
    # Check for computed property — skip those.
    for child in prop_decl.children:
        if child.type in ("computed_property", "computed_value_body", "code_block"):
            return

    name_node = None
    for child in prop_decl.children:
        if child.type == "pattern":
            for gc in child.children:
                if gc.type == "simple_identifier":
                    name_node = gc
                    break
            break

    if name_node is None:
        return

    field_name = _text(name_node).strip()
    if not field_name:
        return

    qualified = f"{class_name}.{field_name}"
    result.append((qualified, prop_decl.start_point[0] + 1))


# ══════════════════════════════════════════════════════════════════════════════
# Swift field-access emission helpers for graph_swift.py
# ══════════════════════════════════════════════════════════════════════════════
#
# These helpers encapsulate the field-access edge + field-symbol emission wiring
# that was previously inline in graph_swift.py. Moving them here reduces
# graph_swift.py below the 1000-line limit.
#
# Both functions are PURE CONSTRUCTORS: they return new lists and never mutate
# their inputs. graph_swift.py extends its own edge/symbol lists with the results.


def emit_swift_field_access_edges(
    func_node: Node,
    file_str: str,
    class_name: str,
    var_types: dict[str, str],
) -> list[Edge]:
    """Build reads/writes field-access edges for a Swift function_declaration.

    Finds the function_body child and runs extract_field_accesses_swift on it.
    The source_fn is derived from the function's simple_identifier child.
    Returns a (possibly empty) list of Edge TypedDicts.

    Never raises.
    """
    try:
        func_name = None
        for child in func_node.children:
            if child.type == "simple_identifier":
                func_name = _text(child)
                break
        if not func_name:
            return []

        source_fn = f"{class_name}.{func_name}"

        func_body = None
        for child in func_node.children:
            if child.type == "function_body":
                func_body = child
                break
        if func_body is None:
            return []

        edges: list[Edge] = []
        for src, tgt, mode, line in extract_field_accesses_swift(
            func_body, source_fn, class_name, var_types
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
        return edges

    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "emit_swift_field_access_edges: failed for class=%r: %r", class_name, exc
        )
        return []


def emit_swift_field_symbols(
    class_node: Node,
    class_name: str,
    file_str: str,
) -> list[Symbol]:
    """Build field-kind Symbol TypedDicts for a Swift class/struct/actor node.

    Calls collect_field_symbols_swift and wraps each result as a Symbol.
    Returns a (possibly empty) list of Symbol TypedDicts.

    Never raises.
    """
    try:
        symbols: list[Symbol] = []
        for qual_name, field_line in collect_field_symbols_swift(class_node, class_name):
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
        return symbols
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "emit_swift_field_symbols: failed for class=%r: %r", class_name, exc
        )
        return []
