"""Field-access edge helper — Java, C#, C, and C++ read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
family. Never imports from graph.py, graph_java.py, db.py, or any non-leaf seam module.

LAYERING:
    graph_common                  (leaf — no seam deps)
    graph_scope_infer_ext         (leaf — Go/Rust receiver-type inference)
    graph_scope_infer_ext2        (leaf — Java/C#/C++/Ruby/PHP receiver-type inference)
         ↑
    field_access_ext        (this file — field-access classification for Java/C#/C/C++)
         ↑
    graph_java       (calls extract_field_accesses_java + collect_field_symbols_java)
    graph_csharp     (calls extract_field_accesses_csharp + collect_field_symbols_csharp)
    graph_c          (calls extract_field_accesses_c + collect_field_symbols_c)
    graph_cpp        (calls extract_field_accesses_cpp + collect_field_symbols_cpp)

WHY a separate module from field_access.py:
  field_access.py already exceeds 1000 lines (Python + TS/JS + Go + Rust). Adding
  Java/C#/C/C++ would push it beyond the 1000-line limit. The same precedent as
  graph_java_csharp.py → graph_java.py / graph_csharp.py split.

FIELD ACCESS DEFINITION (language-agnostic):
  A field access is a member expression <receiver>.<field> that is NOT in call position.
  Call position means: the expression is the callee of a call node.

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

C:
  field_expression node: argument + field → field access edge
  Both '.' (dot) and '->' (arrow) are field_expression nodes
  assignment_expression left=field_expression → write; else read
  update_expression argument=field_expression → write (++ / --)
  call_expression function=field_expression → NOT a field edge

C++:
  Same node types as C (field_expression)
  this->field resolves to the enclosing class name
  resolve_receiver_type_ext for non-this receivers

RECEIVER RESOLUTION:
  Java/C#: 'this' → enclosing class (EXTRACTED). Others via resolve_receiver_type_ext.
  C: No self convention. All accesses use var_types (pass {} for C unless resolved).
     Bare field name used when receiver type unknown (AMBIGUOUS).
  C++: 'this' → enclosing class (EXTRACTED). Others via resolve_receiver_type_ext.

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import (
    _CPP_SELF_NAMES,
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
        # Strip leading '*' for pointer dereference (e.g. (*p) → p).
        clean_recv = receiver_text.lstrip("*").strip()

        # C has no self convention — pass empty frozenset().
        # Resolution comes purely from var_types.
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
            if field_name and field_name not in ("public", "private", "protected",
                                                  "static", "const", "virtual",
                                                  "inline", "explicit", "override"):
                qualified = f"{class_name}.{field_name}"
                result.append((qualified, field_decl.start_point[0] + 1))
