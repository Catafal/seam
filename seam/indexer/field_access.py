"""Field-access edge helper — Python + TypeScript/JavaScript read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
family from graph_scope_infer. Never imports from graph.py, graph_python.py, db.py,
or any non-leaf seam module.

LAYERING:
    graph_common       (leaf — no seam deps)
    graph_scope_infer  (leaf — receiver-type inference)
         ↑
    field_access       (this file — field-access classification for Python + TS/JS)
         ↑
    graph_python       (calls extract_field_accesses_python for reads/writes edges)
    graph_typescript   (calls extract_field_accesses_typescript for reads/writes edges)

WHY a separate module:
  1. graph_python.py / graph_typescript.py would approach the 1000-line limit if this
     logic were inlined.
  2. The field-access cluster (call-position test + read/write classification +
     receiver resolution) is a coherent leaf unit — no Edge construction, no DB calls —
     so it belongs in a dedicated leaf, mirroring the graph_scope_infer.py pattern.
  3. Pure helper means it is straightforward to unit-test in isolation before wiring
     it into the extractor (TDD red/green cycle).

FIELD ACCESS DEFINITION (language-agnostic):
  A field access is a member/attribute expression <receiver>.<field> that is NOT in
  call position (i.e. it is NOT the function/callee of a call node). Examples:

  Python:
    self.x            → reads (used as a value)
    self.x = v        → writes (LHS of assignment)
    self.x += 1       → writes (augmented-assignment target)
    del self.x        → writes (delete is a mutation)
    self.foo()        → NOT a field access (call position — stays a 'call' edge)

  TypeScript/JavaScript:
    this.x            → reads (member_expression not in call position)
    this.x = v        → writes (assignment_expression LHS)
    this.x += v       → writes (augmented_assignment_expression LHS)
    delete this.x     → writes (unary_expression with 'delete')
    this.foo()        → NOT a field access (call_expression function child)

READ vs WRITE classification:
  Python WRITE when the attribute is:
    - The LHS 'left' child of an assignment node  (self.x = v)
    - Any child of an augmented_assignment node   (self.x += v, self.x -= v, etc.)
    - Any child of a delete_statement node        (del self.x)

  TypeScript WRITE when the member_expression is:
    - The 'left' child of an assignment_expression         (this.x = v)
    - The 'left' child of an augmented_assignment_expression (this.x += v)
    - A member_expression inside a delete unary_expression (delete this.x)

RECEIVER RESOLUTION / CONFIDENCE (conservatism contract):
  self/cls → enclosing class name (EXTRACTED). [Python]
  this → enclosing class name (EXTRACTED). [TypeScript]
  Typed local/param/field via resolve_receiver_type → qualified Type.field (EXTRACTED).
  Unresolvable receiver → bare field name as target (AMBIGUOUS).
  NEVER emit a wrong qualified target: if resolution is uncertain → bare name kept.

FIELD SYMBOLS:
  Python:  class-level annotations (x: Type) + first self.x = ... in __init__.
  TypeScript: public_field_definition nodes + this.x = ... in constructor body
              + constructor parameter properties (constructor(private x: Foo)).
  Dedup by (class_name, field_name) — multiple assignments → one symbol.

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer import (
    _PY_SELF_NAMES,
    _TS_SELF_NAMES,
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


# ══════════════════════════════════════════════════════════════════════════════
# TypeScript / JavaScript field-access classification (A3 Slice 2)
# ══════════════════════════════════════════════════════════════════════════════
#
# TS/JS AST patterns for field accesses:
#   member_expression   → the node type for both reads AND call-position references
#   assignment_expression LHS member_expression → write
#   augmented_assignment_expression LHS member_expression → write
#   unary_expression 'delete' → write (subtree contains the member_expression)
#   call_expression function=member_expression → NOT a field access (method call)
#
# Receiver resolution reuses _TS_SELF_NAMES ({'this'}) and resolve_receiver_type,
# matching exactly the approach used by the call-edge extractor in graph_typescript.py.


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
