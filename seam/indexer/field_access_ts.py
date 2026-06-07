"""Field-access edge helper — TypeScript / JavaScript read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
helpers from graph_scope_infer. Never imports from graph.py, db.py, or any non-leaf
seam module.

LAYERING:
    graph_common         (leaf — no seam deps)
    graph_scope_infer    (leaf — Python/TS/JS receiver-type inference)
         ↑
    field_access_ts      (this file — field-access classification for TypeScript/JS)
         ↑
    field_access         (re-exports extract_field_accesses_typescript +
                          collect_field_symbols_typescript for backward compat)
    graph_typescript     (calls those functions via field_access import)

WHY a separate module from field_access.py:
  field_access.py would otherwise exceed the 1000-line limit when Python + TS/JS +
  Go + Rust are all in one file. This follows the graph_scope_infer precedent.

TS/JS AST patterns for field accesses:
  member_expression   → the node type for both reads AND call-position references
  assignment_expression LHS member_expression → write
  augmented_assignment_expression LHS member_expression → write
  unary_expression 'delete' → write (subtree contains the member_expression)
  call_expression function=member_expression → NOT a field access (method call)

Receiver resolution reuses _TS_SELF_NAMES ({'this'}) and resolve_receiver_type,
matching exactly the approach used by the call-edge extractor in graph_typescript.py.

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer import (
    _TS_SELF_NAMES,
    resolve_receiver_type,
)

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


def extract_field_accesses_typescript(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a TypeScript/JS function body.

    Args:
        func_body:   The statement_block Node of the method/function.
        source_fn:   Qualified name of the enclosing function, e.g. 'Account.get'.
        class_name:  Enclosing class name (None for module-level functions).
        var_types:   Scope map: param/local name → type name (class fields merged
                     with per-function param/local bindings by the caller).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'Type.field' when receiver resolves; bare 'field' when unknown.
        mode = 'reads' | 'writes'.
        line = 1-based source line of the member_expression.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _ts_walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_typescript: failed for source=%r: %r", source_fn, exc
        )
    return result


def _ts_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a TS/JS function body collecting field accesses.

    Does NOT recurse into nested function/class definitions (separate scope).
    """
    for child in node.children:
        _ts_walk_stmt(child, source_fn, class_name, var_types, result)


def _ts_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single TS/JS statement node for field accesses.

    Handles:
      expression_statement → assignment_expression / augmented_assignment_expression
                             / plain member_expression reads / unary delete
      return_statement, if_statement, for/while loops → recurse
      Skips nested function/class/method definitions (their own scope).
    """
    t = node.type

    # Skip nested scope-creating constructs — they have their own scope.
    if t in (
        "function_declaration", "function_expression", "arrow_function",
        "method_definition", "class_declaration",
    ):
        return

    # assignment_expression: this.x = v → write; RHS may contain reads.
    if t == "assignment_expression":
        _ts_handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # augmented_assignment_expression: this.x += v → write; RHS may contain reads.
    if t == "augmented_assignment_expression":
        _ts_handle_augmented_assignment(node, source_fn, class_name, var_types, result)
        return

    # unary_expression: 'delete this.x' → write.
    if t == "unary_expression":
        _ts_handle_delete(node, source_fn, class_name, var_types, result)
        # Also recurse in case there are sub-reads inside complex delete targets.
        return

    # call_expression: the function child may be a member_expression (method call).
    # We do NOT emit a field edge for it — it's a call. But arguments may contain reads.
    if t == "call_expression":
        args = node.child_by_field_name("arguments")
        if args is not None:
            for arg in args.children:
                _ts_walk_stmt(arg, source_fn, class_name, var_types, result)
        return

    # A bare member_expression (not in call position and not an assignment LHS)
    # e.g. `this.balance;` as an expression statement → read.
    if t == "member_expression":
        _ts_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        return

    # Recurse into all other node types (return_statement, if_statement, for_*,
    # while_statement, block, expression_statement, etc.).
    for child in node.children:
        _ts_walk_stmt(child, source_fn, class_name, var_types, result)


def _ts_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle TS assignment_expression: left=member_expression → write; right → reads."""
    left = node.child_by_field_name("left")
    if left is not None and left.type == "member_expression":
        acc = _ts_classify_member(left, class_name, var_types)
        if acc is not None:
            target, _recv = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _ts_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _ts_handle_augmented_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle TS augmented_assignment_expression: left → write; right → reads.

    TS tree-sitter grammar: augmented_assignment_expression has 'left' and 'right' fields.
    """
    left = node.child_by_field_name("left")
    if left is not None and left.type == "member_expression":
        acc = _ts_classify_member(left, class_name, var_types)
        if acc is not None:
            target, _recv = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    right = node.child_by_field_name("right")
    if right is not None:
        _ts_collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _ts_handle_delete(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle TS delete unary_expression: any member_expression child → write.

    Tree-sitter: unary_expression has operator ('delete') + argument. The argument
    may be a member_expression directly, or wrapped in a parenthesized_expression
    (e.g. `delete (this as any).balance`). We scan the first non-operator child.
    """
    for child in node.children:
        if child.type in ("delete",):
            continue  # Skip the operator keyword node
        # Walk into parenthesized_expression or directly handle member_expression.
        _ts_find_member_writes(child, source_fn, class_name, var_types, result)
        break  # Only one operand for 'delete'


def _ts_find_member_writes(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Find any member_expression inside a delete operand and emit as writes.

    Handles both direct member_expression and member inside parenthesized_expression
    or type_assertion expressions (e.g. `(this as any).balance`).
    """
    if node.type == "member_expression":
        acc = _ts_classify_member(node, class_name, var_types)
        if acc is not None:
            target, _recv = acc
            result.append((source_fn, target, "writes", node.start_point[0] + 1))
        return
    # Recurse into wrappers (parenthesized_expression, type_assertion, as_expression).
    for child in node.children:
        _ts_find_member_writes(child, source_fn, class_name, var_types, result)


def _ts_emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a member_expression node that is NOT in call position.

    A member_expression is in call position when its parent is a call_expression AND
    it IS the 'function' field of that call. We skip those (method calls).

    WHY start_point comparison: same reasoning as Python — tree-sitter creates new
    Node objects on each field access call; start_point is the stable identity.
    """
    parent = node.parent
    if parent is not None and parent.type == "call_expression":
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _ts_classify_member(node, class_name, var_types)
    if acc is not None:
        target, _recv = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _ts_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a TS/JS expression node.

    Walks the expression tree and emits reads edges for member_expression nodes
    that are NOT in call position. Skips nested scope-creating constructs.
    """
    t = node.type
    if t in (
        "function_declaration", "function_expression", "arrow_function",
        "method_definition", "class_declaration",
    ):
        return

    if t == "member_expression":
        _ts_emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        # Do NOT recurse into member_expression children — the object part ('this')
        # is not itself a field access target we want to re-emit.
        return

    for child in node.children:
        _ts_collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _ts_classify_member(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve a TS/JS member_expression node to (qualified_target, receiver_text).

    Returns None when the node lacks the expected object/property shape.
    Returns (target, receiver_text) where:
      - target = 'Type.field' when receiver resolves (EXTRACTED confidence)
      - target = bare 'field' when unresolvable (AMBIGUOUS confidence)
      - receiver_text = raw receiver text (e.g. 'this', 'client')

    Conservatism contract (mirrors resolve_receiver_type):
      NEVER emit a wrong qualified target. Bare name kept on uncertain receiver.

    TS grammar: member_expression has 'object' field and 'property' field.
    The property is typically a property_identifier node.

    WHY unwrap as_expression/parenthesized_expression:
      `delete (this as any).cache` wraps `this` in a parenthesized as_expression.
      The object field is `parenthesized_expression → as_expression → this`.
      We unwrap one level to recover `this` and correctly classify the access.

    Never raises (returns None on any exception).
    """
    try:
        obj_node = node.child_by_field_name("object")
        prop_node = node.child_by_field_name("property")
        if obj_node is None or prop_node is None:
            return None

        # property_identifier is the standard field-name node in TS member expressions.
        # Other property types (computed, string literal) are skipped conservatively.
        if prop_node.type != "property_identifier":
            return None

        field_name = _text(prop_node)
        if not field_name:
            return None

        # Unwrap type-assertion wrappers to recover the underlying receiver.
        # Handles: (this as any).x → resolve as 'this'
        #          (this as SomeType).x → resolve as 'this'
        effective_obj = _ts_unwrap_type_assertion(obj_node)
        receiver_text = _text(effective_obj)

        # Resolve receiver to a type using existing scope-inference.
        resolved_type = resolve_receiver_type(
            receiver_text, class_name, var_types, _TS_SELF_NAMES
        )

        if resolved_type is not None:
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_ts_classify_member: failed: %r", exc)
        return None


def _ts_unwrap_type_assertion(node: Node) -> Node:
    """Unwrap parenthesized_expression and as_expression wrappers one level deep.

    Handles: (this as any) → as_expression → 'this' node
             (this) → parenthesized_expression → 'this' node

    WHY one level: deeper nesting is not common in practice, and the conservatism
    contract requires refusing uncertain receivers rather than over-unwrapping.
    Returns the original node if unwrapping does not yield a simpler node.
    Never raises.
    """
    try:
        if node.type == "parenthesized_expression":
            # Find the content inside the parentheses (skip '(' and ')' tokens).
            for child in node.children:
                if child.type not in ("(", ")"):
                    return _ts_unwrap_type_assertion(child)
        if node.type in ("as_expression", "type_assertion"):
            # as_expression: first named child is the value expression (before 'as').
            for child in node.named_children:
                # The first named child of an as_expression is the wrapped value.
                return child
    except Exception:  # noqa: BLE001
        pass
    return node


def collect_field_symbols_typescript(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a TypeScript class_declaration.

    Returns field symbols for:
      1. public_field_definition nodes in the class body (typed or untyped field decls).
      2. this.x = ... assignment sites in the constructor body (first occurrence only).
      3. Constructor parameter properties: required_parameter with accessibility_modifier
         (e.g. constructor(private x: Foo, public y: Bar)) — these become stored fields.

    Dedup: by (class_name, field_name) — multiple occurrences of the same field name
    produce ONE symbol. Class-body field declarations win over constructor assignments
    (body is scanned first), which win over parameter properties.

    Returns [] on any error. Never raises.
    """
    try:
        seen_fields: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Pass 1: class body field declarations (public_field_definition).
        for child in body.children:
            try:
                _ts_try_collect_field_definition(child, class_name, seen_fields, result)
            except Exception:  # noqa: BLE001
                pass

        # Pass 2: constructor body this.x = ... assignments.
        for child in body.children:
            try:
                ctor = _ts_get_constructor(child)
                if ctor is None:
                    continue
                _ts_collect_constructor_assignments(ctor, class_name, seen_fields, result)
            except Exception:  # noqa: BLE001
                pass

        # Pass 3: constructor parameter properties (private/public/protected params).
        for child in body.children:
            try:
                ctor = _ts_get_constructor(child)
                if ctor is None:
                    continue
                _ts_collect_param_properties(ctor, class_name, seen_fields, result)
            except Exception:  # noqa: BLE001
                pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_typescript: failed for class %r: %r", class_name, exc)
        return []


def _ts_try_collect_field_definition(
    node: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Try to extract a field symbol from a public_field_definition node.

    TS grammar: public_field_definition has a 'name' field (property_identifier)
    and an optional 'type' field (type_annotation). We index ALL field definitions
    regardless of type (unlike holds, which filters builtins) — 'balance: number'
    is a valid field even though 'number' is a builtin type.
    """
    if node.type not in ("public_field_definition", "field_definition"):
        return
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    field_name = _text(name_node)
    if not field_name:
        return
    qualified = f"{class_name}.{field_name}"
    if qualified not in seen:
        seen.add(qualified)
        result.append((qualified, node.start_point[0] + 1))


def _ts_collect_constructor_assignments(
    ctor_node: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Scan constructor body for this.x = ... assignments and collect as field symbols.

    Only the FIRST occurrence of each (class_name, field_name) pair is emitted.
    Uses a shallow walk of the constructor's statement_block.
    """
    body = ctor_node.child_by_field_name("body")
    if body is None:
        return
    for stmt in body.children:
        _ts_try_collect_ctor_stmt(stmt, class_name, seen, result)


def _ts_try_collect_ctor_stmt(
    stmt: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Try to extract a this.x = ... field assignment from a constructor statement.

    Handles expression_statement wrapping an assignment_expression:
      expression_statement → assignment_expression { left: member_expression, right: ... }
    where left member_expression has object='this' and property=field_name.
    """
    # Unwrap expression_statement wrapper.
    node = stmt
    if stmt.type == "expression_statement" and stmt.children:
        node = stmt.children[0]

    if node.type not in ("assignment_expression", "augmented_assignment_expression"):
        return

    left = node.child_by_field_name("left")
    if left is None or left.type != "member_expression":
        return

    obj_node = left.child_by_field_name("object")
    prop_node = left.child_by_field_name("property")
    if obj_node is None or prop_node is None:
        return

    receiver = _text(obj_node)
    if receiver not in _TS_SELF_NAMES:
        return  # Only this.x = ... patterns

    if prop_node.type != "property_identifier":
        return

    field_name = _text(prop_node)
    if not field_name:
        return

    qualified = f"{class_name}.{field_name}"
    if qualified not in seen:
        seen.add(qualified)
        result.append((qualified, stmt.start_point[0] + 1))


def _ts_collect_param_properties(
    ctor_node: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Collect field symbols from constructor parameter properties.

    A parameter property is a required_parameter with an accessibility_modifier
    (private, public, protected) — tree-sitter emits it as a required_parameter
    whose first child is an accessibility_modifier node, followed by an identifier
    and an optional type_annotation.

    These parameters implicitly define stored fields on the class, so they should
    become field symbols regardless of whether they have a type annotation.
    """
    params = ctor_node.child_by_field_name("parameters")
    if params is None:
        return
    for param in params.children:
        _ts_try_collect_param_property(param, class_name, seen, result)


def _ts_try_collect_param_property(
    param: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Try to collect a field symbol from a constructor parameter property node.

    A parameter property is identified by having an accessibility_modifier child
    (private/public/protected/readonly) as one of its children. The identifier
    child following the modifier is the field name.
    """
    if param.type not in ("required_parameter", "optional_parameter"):
        return

    # Check for accessibility_modifier child (marks this as a parameter property).
    has_modifier = any(c.type == "accessibility_modifier" for c in param.children)
    if not has_modifier:
        return

    # The identifier child is the field name.
    name_node = param.child_by_field_name("pattern")
    if name_node is None:
        # Fallback: find identifier child directly (grammar varies).
        for child in param.children:
            if child.type == "identifier":
                name_node = child
                break
    if name_node is None:
        return

    field_name = _text(name_node)
    if not field_name:
        return

    qualified = f"{class_name}.{field_name}"
    if qualified not in seen:
        seen.add(qualified)
        result.append((qualified, param.start_point[0] + 1))


def _ts_get_constructor(node: Node) -> Node | None:
    """Return the method_definition node if it is the TypeScript constructor.

    TS grammar: constructor is a method_definition whose 'name' property_identifier
    is 'constructor'.
    """
    if node.type != "method_definition":
        return None
    name_node = node.child_by_field_name("name")
    if name_node is not None and _text(name_node) == "constructor":
        return node
    return None
