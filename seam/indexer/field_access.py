"""Field-access edge helper — Python attribute read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
family from graph_scope_infer. Never imports from graph.py, graph_python.py, db.py,
or any non-leaf seam module.

LAYERING:
    graph_common       (leaf — no seam deps)
    graph_scope_infer  (leaf — receiver-type inference)
         ↑
    field_access       (this file — field-access classification for Python)
         ↑
    graph_python       (calls extract_field_accesses_python for reads/writes edges)

WHY a separate module:
  1. graph_python.py would approach the 1000-line limit if this logic were inlined.
  2. The field-access cluster (call-position test + read/write classification +
     receiver resolution) is a coherent leaf unit — no Edge construction, no DB calls —
     so it belongs in a dedicated leaf, mirroring the graph_scope_infer.py pattern.
  3. Pure helper means it is straightforward to unit-test in isolation before wiring
     it into the extractor (TDD red/green cycle).

FIELD ACCESS DEFINITION:
  A field access is an attribute expression <receiver>.<field> that is NOT in call
  position (i.e. it is NOT the function/callee of a call node). Examples:
    self.x            → reads (used as a value)
    self.x = v        → writes (LHS of assignment)
    self.x += 1       → writes (augmented-assignment target)
    del self.x        → writes (delete is a mutation)
    self.foo()        → NOT a field access (call position — stays a 'call' edge)

READ vs WRITE classification:
  WRITE when the attribute is:
    - The LHS 'left' child of an assignment node  (self.x = v)
    - Any child of an augmented_assignment node   (self.x += v, self.x -= v, etc.)
    - Any child of a delete_statement node        (del self.x)
  READ otherwise (any non-call attribute access that is not an assignment target).

RECEIVER RESOLUTION / CONFIDENCE (conservatism contract):
  self/cls → enclosing class name (EXTRACTED).
  Typed local/param/field via resolve_receiver_type → qualified Type.field (EXTRACTED).
  Unresolvable receiver → bare field name as target (AMBIGUOUS).
  NEVER emit a wrong qualified target: if resolution is uncertain → bare name kept.

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer import (
    _PY_SELF_NAMES,
    resolve_receiver_type,
)

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


def extract_field_accesses_python(
    func_body: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a Python function body.

    Args:
        func_body:   The 'body' block Node of the function/method (suite or block).
        source_fn:   Qualified name of the enclosing function, e.g. 'Account.get'.
        class_name:  Enclosing class name (None for module-level functions).
        var_types:   Scope map: param/local name → type name (Layer 2 per-function scope,
                     already merged with class-level field bindings by the caller).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'Type.field' when receiver resolves; bare 'field' when unknown.
        mode = 'reads' | 'writes'.
        line = 1-based source line of the attribute access.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _walk_body(func_body, source_fn, class_name, var_types, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_python: failed for source=%r: %r", source_fn, exc
        )
    return result


def _walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively walk a function body collecting field accesses.

    Handles nested if/for/while/try/with blocks by recursing into their children.
    Does NOT recurse into nested function/class definitions (those have their own scope).
    """
    for child in node.children:
        _walk_stmt(child, source_fn, class_name, var_types, result)


def _walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Walk a single statement node for field accesses.

    Handles:
      assignment / augmented_assignment / delete_statement at the top level.
      expression_statement (for plain reads like `_ = self.x`).
      Nested control-flow (if/for/while/try/with) by recursing into their bodies.
      Does NOT recurse into nested function_definition or class_definition nodes.
    """
    t = node.type

    # Skip nested function/class definitions — they have their own scope.
    if t in ("function_definition", "decorated_definition", "class_definition"):
        return

    # Assignment: LHS may be an attribute (write); RHS may contain reads.
    if t == "assignment":
        _handle_assignment(node, source_fn, class_name, var_types, result)
        return

    # Augmented assignment (+=, -=, *=, etc.): target is always a write.
    if t == "augmented_assignment":
        _handle_augmented_assignment(node, source_fn, class_name, var_types, result)
        return

    # Delete statement: each deleted expression is a write.
    if t == "delete_statement":
        _handle_delete_statement(node, source_fn, class_name, var_types, result)
        return

    # For all other nodes: recurse, collecting reads from attribute expressions.
    # This handles expression statements, return, if/elif/else, for, while, try, with.
    for child in node.children:
        _walk_stmt(child, source_fn, class_name, var_types, result)

    # Collect reads from attribute access directly on this node (if it's an attribute).
    # WHY here and not in a dedicated visitor: attribute nodes appear as children of
    # many different parent node types (return, expression_statement, binary_op, etc.).
    # Walking children then checking if this node itself is an attribute handles all cases
    # without a complex visitor pattern.
    # BUT: we must NOT collect from call-position attributes. That check is in _emit_read.
    if t == "attribute":
        _emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)


def _handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle an assignment node: LHS attribute → write; RHS attributes → reads."""
    # LHS: left field of the assignment. May be an attribute (write) or a subscript etc.
    left = node.child_by_field_name("left")
    if left is not None and left.type == "attribute":
        acc = _classify_attribute(left, class_name, var_types)
        if acc is not None:
            target, _recv = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    # RHS: right field may contain reads.
    right = node.child_by_field_name("right")
    if right is not None:
        _collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _handle_augmented_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle augmented assignment (+=, -=, etc.): target attribute → write.

    In Python tree-sitter, augmented_assignment has a 'left' field child
    and a 'right' field child. The left is the mutation target (write).
    """
    left = node.child_by_field_name("left")
    if left is not None and left.type == "attribute":
        acc = _classify_attribute(left, class_name, var_types)
        if acc is not None:
            target, _recv = acc
            result.append((source_fn, target, "writes", left.start_point[0] + 1))

    # Also collect reads from the RHS (the value expression).
    right = node.child_by_field_name("right")
    if right is not None:
        _collect_reads_recursive(right, source_fn, class_name, var_types, result)


def _handle_delete_statement(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Handle del statement: each deleted attribute is a write."""
    for child in node.children:
        if child.type == "attribute":
            acc = _classify_attribute(child, class_name, var_types)
            if acc is not None:
                target, _recv = acc
                result.append((source_fn, target, "writes", child.start_point[0] + 1))
        elif child.type not in ("del", ","):
            # delete_statement may contain expression_list or other wrappers
            for sub in child.children:
                if sub.type == "attribute":
                    acc = _classify_attribute(sub, class_name, var_types)
                    if acc is not None:
                        target, _recv = acc
                        result.append((source_fn, target, "writes", sub.start_point[0] + 1))


def _emit_read_if_not_in_call(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for an attribute node that is NOT in call position.

    An attribute is in call position when it is the 'function' field of a parent
    'call' node. We skip those (they are method calls, handled as 'call' edges).

    WHY start_point comparison instead of identity: tree-sitter creates fresh Python
    Node objects on each field access call, so `func_field is node` is always False
    even when both point to the same underlying C node. Comparing start_point (row,col)
    is the correct identity test for tree-sitter Python bindings.
    """
    parent = node.parent
    if parent is not None and parent.type == "call":
        # Check if THIS node is the 'function' field of the call — i.e., it is the callee.
        func_field = parent.child_by_field_name("function")
        if func_field is not None and func_field.start_point == node.start_point:
            return  # Call position — do not emit field read

    acc = _classify_attribute(node, class_name, var_types)
    if acc is not None:
        target, _recv = acc
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    var_types: dict[str, str],
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from an expression node.

    Walks the expression tree and emits reads edges for attribute nodes that are
    NOT in call position. Skips nested function/class definitions.
    """
    t = node.type
    if t in ("function_definition", "decorated_definition", "class_definition"):
        return

    if t == "attribute":
        _emit_read_if_not_in_call(node, source_fn, class_name, var_types, result)
        # Do NOT recurse into this attribute's children — the object part
        # (e.g. 'self' in 'self.x') is not itself an attribute access target.
        return

    for child in node.children:
        _collect_reads_recursive(child, source_fn, class_name, var_types, result)


def _classify_attribute(
    node: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> tuple[str, str | None] | None:
    """Resolve an attribute node to (qualified_target, receiver_text).

    Returns None when the node has no attribute field (unexpected shape).
    Returns (target, receiver_text) where:
      - target = 'Type.field' when receiver resolves (EXTRACTED confidence)
      - target = bare 'field' when unresolvable (AMBIGUOUS confidence)
      - receiver_text = raw receiver text (e.g. 'self', 'client')

    Conservatism contract (mirrors resolve_receiver_type):
      NEVER emit a wrong qualified target. If the receiver type cannot be
      confidently determined, return the bare field name.

    Never raises (returns None on any exception).
    """
    try:
        obj_node = node.child_by_field_name("object")
        attr_node = node.child_by_field_name("attribute")
        if obj_node is None or attr_node is None:
            return None

        field_name = _text(attr_node)
        if not field_name:
            return None

        receiver_text = _text(obj_node)

        # Resolve receiver to a type name using existing scope-inference.
        resolved_type = resolve_receiver_type(
            receiver_text, class_name, var_types, _PY_SELF_NAMES
        )

        if resolved_type is not None:
            # Qualified target: Type.field (EXTRACTED confidence at read time)
            return f"{resolved_type}.{field_name}", receiver_text
        else:
            # Unresolvable receiver → bare field name (AMBIGUOUS at read time)
            return field_name, receiver_text

    except Exception as exc:  # noqa: BLE001
        logger.debug("_classify_attribute: failed: %r", exc)
        return None


def collect_field_symbols_python(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a Python class_definition.

    Returns field symbols for:
      1. Class-level annotated fields: 'x: Type' or 'x: Type = value'
         (even when Type is a builtin — unlike holds, we index ALL fields)
      2. self.x = ... assignment sites in __init__ (first occurrence only)

    Dedup: by (class_name, field_name) — multiple assignments to same field → one entry.
    Class-level declaration wins over __init__ assignment (class body scanned first).

    Returns [] on any error. Never raises.
    """
    try:
        seen_fields: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Pass 1: class-level field annotations (x: Type or x: Type = value).
        for stmt in body.children:
            try:
                _try_collect_class_annotation(stmt, class_name, seen_fields, result)
            except Exception:  # noqa: BLE001
                pass

        # Pass 2: self.x = ... in __init__ (first occurrence by field name only).
        for stmt in body.children:
            try:
                init_node = _get_init_function(stmt)
                if init_node is None:
                    continue
                _collect_init_assignments(init_node, class_name, seen_fields, result)
            except Exception:  # noqa: BLE001
                pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_python: failed for class %r: %r", class_name, exc)
        return []


def _try_collect_class_annotation(
    stmt: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Try to extract a class-level field annotation from a statement.

    Handles:
      x: Type              (assignment with 'type' field, no right side)
      x: Type = value      (assignment with 'type' field + right side)
      x = value            (plain assignment — NOT a typed annotation, skip)

    NOTE: We accept ALL annotation types including builtins (unlike the holds
    collector) because we want 'balance: int' to become a field symbol.
    The field name is what matters, not the type.
    """
    if stmt.type != "expression_statement" or not stmt.children:
        return
    inner = stmt.children[0]
    if inner.type not in ("assignment", "annotated_assignment"):
        return

    # Must have a 'type' annotation field to count as a typed class field.
    ann = inner.child_by_field_name("type")
    if ann is None:
        return  # Plain assignment without annotation — skip

    left = inner.child_by_field_name("left")
    if left is None or left.type != "identifier":
        return  # Only simple name targets (not self.x: T at class level)

    field_name = _text(left)
    if not field_name:
        return

    qualified = f"{class_name}.{field_name}"
    if qualified not in seen:
        seen.add(qualified)
        result.append((qualified, stmt.start_point[0] + 1))


def _collect_init_assignments(
    init_node: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Scan __init__ body for self.x = ... assignments and collect as field symbols.

    Only the FIRST occurrence of each (class_name, field_name) pair is emitted.
    Uses a shallow walk — only direct statements in __init__ body, not nested scopes.
    """
    body = init_node.child_by_field_name("body")
    if body is None:
        return

    for stmt in body.children:
        _try_collect_init_stmt(stmt, class_name, seen, result)


def _try_collect_init_stmt(
    stmt: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Try to collect a self.x = ... field assignment from a single statement."""
    # Look for assignment statements in the __init__ body.
    # Could be directly assignment, or wrapped in expression_statement.
    node = stmt
    if stmt.type == "expression_statement" and stmt.children:
        node = stmt.children[0]

    if node.type not in ("assignment", "augmented_assignment"):
        # Also recurse one level for if/for blocks in __init__
        # (shallow — only one level deep for common patterns)
        return

    left = node.child_by_field_name("left")
    if left is None or left.type != "attribute":
        return

    obj_node = left.child_by_field_name("object")
    attr_node = left.child_by_field_name("attribute")
    if obj_node is None or attr_node is None:
        return

    receiver = _text(obj_node)
    if receiver not in _PY_SELF_NAMES:
        return  # Only self.x = ... patterns

    field_name = _text(attr_node)
    if not field_name:
        return

    qualified = f"{class_name}.{field_name}"
    if qualified not in seen:
        seen.add(qualified)
        result.append((qualified, stmt.start_point[0] + 1))


def _get_init_function(stmt: Node) -> Node | None:
    """Return the function_definition node if stmt is a Python __init__ method."""
    if stmt.type == "function_definition":
        name_node = stmt.child_by_field_name("name")
        if name_node is not None and _text(name_node) == "__init__":
            return stmt
    elif stmt.type == "decorated_definition":
        inner = stmt.child_by_field_name("definition")
        if inner is not None and inner.type == "function_definition":
            name_node = inner.child_by_field_name("name")
            if name_node is not None and _text(name_node) == "__init__":
                return inner
    return None
