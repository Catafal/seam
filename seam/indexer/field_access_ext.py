"""Field-access edge helper — Java and C# classification (façade for C/C++ too).

This file contains the Java and C# field-access implementations and re-exports the
C and C++ implementations from field_access_c_cpp so callers can continue using
`from seam.indexer.field_access_ext import <name>` without changes.

LAYER: leaf — imports only stdlib + tree_sitter + resolve_receiver_type helpers.
Never imports from graph.py, db.py, or any non-leaf seam module.

LAYERING:
    graph_common                  (leaf — no seam deps)
    graph_scope_infer_ext         (leaf — Go/Rust receiver-type inference)
    graph_scope_infer_ext2        (leaf — Java/C#/C++/Ruby/PHP receiver-type inference)
         ↑
    field_access_c_cpp      (leaf — C and C++ field-access implementation)
    field_access_ext        (this file — Java/C# implementation + façade re-exports)
         ↑
    graph_java       (calls extract_field_accesses_java + collect_field_symbols_java)
    graph_csharp     (calls extract_field_accesses_csharp + collect_field_symbols_csharp)
    graph_c          (calls extract_field_accesses_c + collect_field_symbols_c)
    graph_cpp        (calls extract_field_accesses_cpp + collect_field_symbols_cpp)

WHY split: the combined Java/C#/C/C++ implementation exceeded the 1000-line limit.
C and C++ are in field_access_c_cpp.py; all public names re-exported here unchanged.

Java:
  field_access node: object + field → field_access edge
  assignment_expression left=field_access → write; else read
  update_expression (++/--) containing field_access → write
  method_invocation → NOT a field edge (call position)

C#:
  member_access_expression node: expression + name → field access edge
  assignment_expression left=member_access_expression → write; else read
  postfix_unary_expression / prefix_unary_expression (++/--) → write
  invocation_expression function=member_access_expression → NOT a field edge

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

# Re-export C and C++ implementations so callers need no import changes.
# noqa: F401 — intentional re-exports for the façade pattern.
from seam.indexer.field_access_c_cpp import (  # noqa: F401
    collect_field_symbols_c,
    collect_field_symbols_cpp,
    extract_field_accesses_c,
    extract_field_accesses_cpp,
)
from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import (
    _CS_SELF_NAMES,
    _JAVA_SELF_NAMES,
)

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


# ══════════════════════════════════════════════════════════════════════════════
# Java field-access classification (A3 Slice 4)
# ══════════════════════════════════════════════════════════════════════════════
#
# Java AST patterns for field accesses:
#   field_access   → <object>.<field> — the node for ALL member field access
#   method_invocation object=... name=... → method call — NOT a field access
#   assignment_expression left=field_access → WRITE
#   assignment_expression (operator +=, -=, etc.) left=field_access → WRITE
#   update_expression (++/--) operand=field_access → WRITE
#
# Receiver resolution:
#   'this' → enclosing class (EXTRACTED). Others via resolve_receiver_type_ext.


def extract_field_accesses_java(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a Java method/constructor body.

    Args:
        func_body:   The 'body' block Node of the method/constructor.
        source_fn:   Qualified name of the enclosing function, e.g. 'Account.getBalance'.
        class_name:  Enclosing class name (None for static contexts).
        var_types:   Scope map: param/local name → type name.

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'Type.field' when receiver resolves; bare 'field' when unknown.
        mode = 'reads' | 'writes'.
        line = 1-based source line of the field access.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _java_walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_java: failed for source=%r: %r", source_fn, exc
        )
    return result


def _java_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a Java method body collecting field accesses."""
    for child in node.children:
        _java_walk_stmt(child, source_fn, class_name, var_types, result)


def _java_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single Java statement node for field accesses.

    Handles:
      expression_statement → assignment_expression / update_expression / bare field_access
      return_statement, if_statement, for/while loops → recurse
      Skips nested class/method definitions.
    """
    t = node.type

    # Skip nested scope-creating constructs.
    if t in ("class_declaration", "method_declaration", "constructor_declaration",
             "lambda_expression"):
        return

    # assignment_expression: left=field_access → write; right may contain reads.
    if t == "assignment_expression":
        _java_handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # update_expression (++ / --): operand field_access → write.
    if t == "update_expression":
        _java_handle_update(node, source_fn, class_name, var_types, result)
        return

    # method_invocation: NOT a field edge (it's a call). Recurse into arguments.
    if t == "method_invocation":
        args = node.child_by_field_name("arguments")
        if args is not None:
            for arg in args.children:
                _java_walk_stmt(arg, source_fn, class_name, var_types, result)
        return

    # A bare field_access that is not in call position → read.
    if t == "field_access":
        _java_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    # Recurse into all other nodes.
    for child in node.children:
        _java_walk_stmt(child, source_fn, class_name, var_types, result)


def _java_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Java assignment_expression: left field_access → write; right → reads."""
    left = node.child_by_field_name("left")
    if left is not None and left.type == "field_access":
        acc = _java_classify_field_access(left, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _java_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _java_handle_update(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle Java update_expression (++/--): operand field_access → write.

    Java update_expression has 'expression' field OR the field_access is a direct child.
    Both prefix (++this.f) and postfix (this.f++) forms produce update_expression.
    """
    # Try 'expression' field first (tree-sitter-java may use either form).
    operand = node.child_by_field_name("expression")
    if operand is None:
        # Fallback: find first field_access child directly.
        for child in node.children:
            if child.type == "field_access":
                operand = child
                break
    if operand is not None and operand.type == "field_access":
        acc = _java_classify_field_access(operand, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", operand.start_point[0] + 1))


def _java_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a Java field_access node that is NOT in call position.

    In Java, a field_access becomes a method_invocation when it is the 'object' field
    of the method_invocation AND the method is called on it. However, Java tree-sitter
    does NOT emit a field_access for the callee — instead the 'object' field of
    method_invocation is the receiver expression. So any bare field_access that we
    encounter at the statement level is already NOT in call position.

    WHY: Java method calls like this.foo() use method_invocation, not field_access +
    call_expression. So there is no field_access-in-call-position case to worry about.
    We still guard against being inside a method_invocation argument list.

    WHY guard against being the 'object' of method_invocation: chained calls like
    `this.obj.foo()` produce a field_access for `this.obj` as the 'object' of the
    method_invocation. In that case `this.obj` IS genuinely being read (to get the
    receiver), but we skip it conservatively — the call edge on `obj.foo` already
    captures the dependency. Emitting both a 'reads' edge AND a 'call' edge for the
    same chained expression would double-count the relationship.
    """
    parent = node.parent
    # Guard: if parent is a method_invocation and this is the 'object' field,
    # it means we are the receiver of a method call (e.g. this.obj.foo() where
    # this.obj is a field_access). In that case, it's actually a field read
    # (reading obj to call a method on it) — but we conservatively skip it
    # to avoid confusion with call edges.
    # NOTE: For simplicity, we only skip if parent is method_invocation AND
    # this field_access is exactly the 'object' field (receiver, not an arg).
    if parent is not None and parent.type == "method_invocation":
        obj_field = parent.child_by_field_name("object")
        if obj_field is not None and obj_field.start_point == node.start_point:
            return  # This is the method receiver — skip (the call edge handles it)

    acc = _java_classify_field_access(node, class_name, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _java_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a Java expression node."""
    t = node.type
    if t in ("class_declaration", "method_declaration", "constructor_declaration",
             "lambda_expression"):
        return

    if t == "field_access":
        _java_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    for child in node.children:
        _java_collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _java_classify_field_access(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a Java field_access node to (qualified_target, receiver_text).

    Java field_access: object field contains receiver (e.g. 'this', identifier),
    field field contains the identifier (field name).

    Returns None when the node lacks the expected shape.
    Returns (target, receiver_text) where:
      - target = 'Type.field' when receiver resolves (EXTRACTED confidence)
      - target = bare 'field' when unresolvable (AMBIGUOUS confidence)

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        obj_node = node.child_by_field_name("object")
        field_node = node.child_by_field_name("field")
        if obj_node is None or field_node is None:
            return None

        field_name = _text(field_node)
        if not field_name:
            return None

        receiver_text = _text(obj_node)

        resolved_type = resolve_receiver_type_ext(
            receiver_text, class_name, var_types, _JAVA_SELF_NAMES
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_java_classify_field_access: failed: %r", exc)
        return None


def collect_field_symbols_java(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a Java class_declaration.

    Returns field symbols for ALL field_declaration nodes in the class body.
    Each field_declaration may declare multiple names (e.g. 'int x, y;').
    Unlike the holds collector, we do NOT filter by user-type constraints —
    ALL fields are indexed (including primitive types like int, String).

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
                    _java_collect_field_decl(child, class_name, result)
                except Exception:  # noqa: BLE001
                    pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_java: failed for class %r: %r", class_name, exc)
        return []


def _java_collect_field_decl(
    field_decl: Node,
    class_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract field symbols from a single Java field_declaration node.

    Java field_declaration:
      modifiers? type variable_declarator+ ';'
      Each variable_declarator has a 'name' field (identifier).

    Multi-name declarations (int x, y;) produce one symbol per name.
    """
    for child in field_decl.named_children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                field_name = _text(name_node).strip()
                if field_name:
                    qualified = f"{class_name}.{field_name}"
                    result.append((qualified, field_decl.start_point[0] + 1))


# ══════════════════════════════════════════════════════════════════════════════
# C# field-access classification (A3 Slice 4)
# ══════════════════════════════════════════════════════════════════════════════
#
# C# AST patterns for field accesses:
#   member_access_expression  → <expression>.<name> — the node for ALL member access
#   invocation_expression function=member_access_expression → method call — NOT field
#   assignment_expression left=member_access_expression → WRITE (any operator)
#   postfix_unary_expression / prefix_unary_expression (++/--) → WRITE
#
# Receiver resolution:
#   'this' → enclosing class (EXTRACTED). Others via resolve_receiver_type_ext.


def extract_field_accesses_csharp(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a C# method/constructor body.

    Args:
        func_body:   The 'body' block Node of the method/constructor.
        source_fn:   Qualified name of the enclosing function, e.g. 'Account.GetBalance'.
        class_name:  Enclosing class name (None for static contexts).
        var_types:   Scope map: param/local name → type name.

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _cs_walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_csharp: failed for source=%r: %r", source_fn, exc
        )
    return result


def _cs_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a C# method body collecting field accesses."""
    for child in node.children:
        _cs_walk_stmt(child, source_fn, class_name, var_types, result)


def _cs_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single C# statement node for field accesses."""
    t = node.type

    # Skip nested scope-creating constructs.
    if t in ("class_declaration", "method_declaration", "constructor_declaration",
             "lambda_expression", "anonymous_method_expression"):
        return

    # assignment_expression: left=member_access_expression → write.
    if t == "assignment_expression":
        _cs_handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # postfix_unary_expression / prefix_unary_expression (++ / --): → write.
    if t in ("postfix_unary_expression", "prefix_unary_expression"):
        _cs_handle_unary_update(node, source_fn, class_name, var_types, result)
        return

    # invocation_expression: NOT a field edge. Recurse into arguments.
    if t == "invocation_expression":
        args = node.child_by_field_name("argument_list")
        if args is not None:
            for arg in args.children:
                _cs_walk_stmt(arg, source_fn, class_name, var_types, result)
        return

    # A bare member_access_expression (not in call position) → read.
    if t == "member_access_expression":
        _cs_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    # Recurse into all other nodes.
    for child in node.children:
        _cs_walk_stmt(child, source_fn, class_name, var_types, result)


def _cs_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle C# assignment_expression: left member_access_expression → write."""
    left = node.child_by_field_name("left")
    if left is not None and left.type == "member_access_expression":
        acc = _cs_classify_member_access(left, class_name, var_types)
        if acc is not None:
            target, _ = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _cs_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _cs_handle_unary_update(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle C# postfix/prefix ++ / -- on a member_access_expression → write.

    postfix_unary_expression: the member_access_expression is the first named child.
    prefix_unary_expression: the member_access_expression follows the operator.
    """
    for child in node.children:
        if child.type == "member_access_expression":
            acc = _cs_classify_member_access(child, class_name, var_types)
            if acc is not None:
                target, _ = acc
                result.append((source_fn, target, "writes", child.start_point[0] + 1))
            break  # Only one operand in unary ++/--


def _cs_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a C# member_access_expression NOT in call position.

    A member_access_expression is in call position when its parent is an
    invocation_expression AND it is the 'function' field of that call.
    """
    parent = node.parent
    if parent is not None and parent.type == "invocation_expression":
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _cs_classify_member_access(node, class_name, var_types)
    if acc is not None:
        target, _ = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _cs_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a C# expression node."""
    t = node.type
    if t in ("class_declaration", "method_declaration", "constructor_declaration",
             "lambda_expression", "anonymous_method_expression"):
        return

    if t == "member_access_expression":
        _cs_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    for child in node.children:
        _cs_collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _cs_classify_member_access(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a C# member_access_expression node to (qualified_target, receiver_text).

    C# member_access_expression:
      expression: the receiver expression (e.g. 'this', 'obj')
      name: identifier (the field/property name)

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        expr_node = node.child_by_field_name("expression")
        name_node = node.child_by_field_name("name")
        if expr_node is None or name_node is None:
            return None

        # name should be an identifier node.
        if name_node.type != "identifier":
            return None

        field_name = _text(name_node)
        if not field_name:
            return None

        receiver_text = _text(expr_node)

        resolved_type = resolve_receiver_type_ext(
            receiver_text, class_name, var_types, _CS_SELF_NAMES
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_cs_classify_member_access: failed: %r", exc)
        return None


def collect_field_symbols_csharp(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a C# class/struct/record declaration.

    Returns field symbols for:
      1. field_declaration nodes in the class body.
      2. property_declaration nodes (auto-properties) in the class body.

    C# field_declaration structure:
      modifier* variable_declaration
      variable_declaration → identifier(type) + variable_declarator*(name=identifier)

    C# property_declaration:
      modifier* type name=identifier { get; set; }

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []
        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        for child in body.named_children:
            try:
                if child.type == "field_declaration":
                    _cs_collect_field_decl(child, class_name, result)
                elif child.type == "property_declaration":
                    _cs_collect_property_decl(child, class_name, result)
            except Exception:  # noqa: BLE001
                pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_csharp: failed for class %r: %r", class_name, exc)
        return []


def _cs_collect_field_decl(
    field_decl: Node,
    class_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract field symbols from a single C# field_declaration node.

    C# field_declaration has a variable_declaration child:
      variable_declaration → type identifier + variable_declarator*(name=identifier)
    """
    var_decl = None
    for child in field_decl.children:
        if child.type == "variable_declaration":
            var_decl = child
            break
    if var_decl is None:
        return

    # Each variable_declarator in the variable_declaration is a field name.
    for child in var_decl.named_children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                field_name = _text(name_node).strip()
                if field_name:
                    qualified = f"{class_name}.{field_name}"
                    result.append((qualified, field_decl.start_point[0] + 1))


def _cs_collect_property_decl(
    prop_decl: Node,
    class_name: str,
    result: list[tuple[str, int]],
) -> None:
    """Extract a field symbol from a C# property_declaration node.

    C# property_declaration:
      modifier* type name=identifier { ... }
    The 'name' field is the property name (identifier).
    We index auto-properties as field symbols (they are effectively stored state).
    """
    name_node = prop_decl.child_by_field_name("name")
    if name_node is None:
        return
    field_name = _text(name_node).strip()
    if not field_name:
        return
    qualified = f"{class_name}.{field_name}"
    result.append((qualified, prop_decl.start_point[0] + 1))

