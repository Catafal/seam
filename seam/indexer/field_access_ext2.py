"""Field-access edge helper — Ruby, PHP, and Swift read/write classification.

LAYER: leaf — imports only stdlib + tree_sitter + the existing resolve_receiver_type
family. Never imports from graph.py, graph_ruby.py, db.py, or any non-leaf seam module.

LAYERING:
    graph_common                  (leaf — no seam deps)
    graph_scope_infer_ext         (leaf — Go/Rust receiver-type inference)
    graph_scope_infer_ext2        (leaf — Java/C#/C++/Ruby/PHP receiver-type inference)
    graph_swift_infer             (leaf — Swift receiver-type inference)
         ↑
    field_access_ext2       (this file — field-access classification for Ruby/PHP/Swift)
         ↑
    graph_ruby       (calls extract_field_accesses_ruby + collect_field_symbols_ruby)
    graph_php        (calls extract_field_accesses_php + collect_field_symbols_php)
    graph_swift      (calls extract_field_accesses_swift + collect_field_symbols_swift)

WHY a separate module from field_access_ext.py:
  field_access_ext.py already has 1292+ lines (Java/C#/C/C++). Adding Ruby/PHP/Swift
  would push it beyond the 1000-line limit. Same precedent as splitting graph_java_csharp,
  graph_c_cpp, graph_ruby, graph_php, graph_swift into separate modules.

FIELD ACCESS DEFINITION per language:

Ruby:
  instance_variable node (@balance) — standalone, belongs to enclosing class.
  assignment with instance_variable LHS → write.
  operator_assignment (+=, -=, etc.) with instance_variable LHS → write.
  Every other instance_variable occurrence → read.
  Call nodes (call) → NOT a field edge (stays a 'call' edge).
  NOTE: Ruby @ivars have no explicit receiver — they always belong to the enclosing
  class. The field strip the '@' prefix for the qualified target: 'ClassName.field'.

PHP:
  member_access_expression: $this->field or $obj->field → field access edge.
  assignment_expression LHS member_access_expression → write.
  augmented_assignment_expression LHS member_access_expression → write.
  member_call_expression ($this->method()) → NOT a field edge (call position).
  Receiver resolution: $this → enclosing class (_PHP_SELF_NAMES).
  Other vars: resolved via resolve_receiver_type_ext from var_types.
  Unresolvable: bare field name (AMBIGUOUS).

Swift:
  navigation_expression: self.prop or obj.prop → field access edge.
  Call position: call_expression whose first child is a navigation_expression → NOT field.
  assignment LHS directly_assignable_expression wrapping navigation_expression → write.
  Augmented assignment (assignment with operator +=, -=, etc.) → write.
  Receiver resolution: self_expression → enclosing class ('Account').
  Other: resolved via _resolve_navigation_target (graph_swift_infer).
  Unresolvable: bare field name (AMBIGUOUS).

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import _PHP_SELF_NAMES

logger = logging.getLogger(__name__)

# Return type for the public API: (source_fn, target_field, mode, line)
# mode is "reads" or "writes"
FieldAccess = tuple[str, str, str, int]


# ══════════════════════════════════════════════════════════════════════════════
# Ruby field-access classification (A3 Slice 5)
# ══════════════════════════════════════════════════════════════════════════════
#
# Ruby instance variables (@ivar) are bespoke:
#   - They appear as 'instance_variable' AST nodes (no separate receiver/object).
#   - They always belong to the enclosing class (no receiver to resolve).
#   - The field symbol name strips the '@' prefix: '@balance' → 'balance'.
#   - qualified target = 'ClassName.balance' (always EXTRACTED when class_name known).
#
# Assignment forms:
#   assignment: LHS is instance_variable → write
#   operator_assignment (+=, -=, etc.): LHS is instance_variable → write
#   All other instance_variable occurrences → read
#
# NOT a field access:
#   call node (Ruby call AST for foo() or obj.foo()) → these stay 'call' edges.
#   Ruby instance methods use 'call' nodes — no confusion with instance_variable nodes.


def extract_field_accesses_ruby(
    method_body: Node,
    source_fn: str,
    class_name: str | None,
) -> list[FieldAccess]:
    """Extract field accesses (reads and writes) from a Ruby method body.

    Args:
        method_body:  The 'body_statement' Node of the method body.
        source_fn:    Qualified name of the enclosing method, e.g. 'Account.deposit'.
        class_name:   Enclosing class/module name (None for top-level methods).

    Returns:
        List of (source_fn, target_field, mode, line) tuples.
        target_field = 'ClassName.field' when class_name known; bare 'field' otherwise.
        mode = 'reads' | 'writes'.
        line = 1-based source line.
        Returns [] on any error.

    Never raises.
    """
    result: list[FieldAccess] = []
    try:
        _ruby_walk_body(method_body, source_fn, class_name, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "extract_field_accesses_ruby: failed for source=%r: %r", source_fn, exc
        )
    return result


def _ruby_walk_body(
    node: Node,
    source_fn: str,
    class_name: str | None,
    result: list[FieldAccess],
) -> None:
    """Recursively walk a Ruby method body collecting @ivar field accesses.

    Does NOT recurse into nested method/singleton_method nodes (their own scope).
    Does NOT recurse into nested class/module nodes (their own scope).
    """
    for child in node.children:
        _ruby_walk_stmt(child, source_fn, class_name, result)


def _ruby_walk_stmt(
    node: Node,
    source_fn: str,
    class_name: str | None,
    result: list[FieldAccess],
) -> None:
    """Walk a single Ruby statement node for @ivar field accesses.

    Handles:
      assignment with instance_variable LHS → write; RHS may contain reads.
      operator_assignment (+=, -=, etc.) with instance_variable LHS → write.
      instance_variable as a standalone expression → read.
      All other nodes: recurse collecting reads from instance_variable nodes.
      Skips nested method/singleton_method/class/module definitions (own scope).
    """
    t = node.type

    # Skip nested scope-creating constructs.
    if t in ("method", "singleton_method", "class", "module"):
        return

    # assignment: LHS instance_variable → write; RHS may contain reads.
    if t == "assignment":
        _ruby_handle_assignment(node, source_fn, class_name, result)
        return

    # operator_assignment (+=, -=, etc.): LHS instance_variable → write.
    if t == "operator_assignment":
        _ruby_handle_operator_assignment(node, source_fn, class_name, result)
        return

    # A bare instance_variable in non-assignment context → read.
    if t == "instance_variable":
        _ruby_emit_read(node, source_fn, class_name, result)
        return

    # Recurse into all other node types (return, if, while, etc.).
    for child in node.children:
        _ruby_walk_stmt(child, source_fn, class_name, result)


def _ruby_handle_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    result: list[FieldAccess],
) -> None:
    """Handle Ruby assignment: LHS instance_variable → write; RHS → reads.

    Ruby assignment node children: [LHS, '=', RHS]
    The LHS is the first child; the RHS is the last child.
    """
    children = list(node.children)
    if not children:
        return

    # LHS: first child (may be instance_variable)
    lhs = children[0]
    if lhs.type == "instance_variable":
        field_name = _ruby_ivar_field_name(lhs)
        if field_name:
            target = _ruby_qualify(field_name, class_name)
            result.append((source_fn, target, "writes", lhs.start_point[0] + 1))

    # RHS: last non-'=' child — collect reads.
    if len(children) >= 3:
        rhs = children[-1]
        _ruby_collect_reads_recursive(rhs, source_fn, class_name, result)


def _ruby_handle_operator_assignment(
    node: Node,
    source_fn: str,
    class_name: str | None,
    result: list[FieldAccess],
) -> None:
    """Handle Ruby operator_assignment (+=, -=, etc.): LHS instance_variable → write.

    operator_assignment node children: [LHS, operator, RHS]
    The LHS is the first child; the RHS is the last child.
    Both reads (implicit in +=) and writes are captured:
    - The LHS instance_variable is a WRITE (mutation takes place).
    - The RHS may contain additional reads.
    WHY only write: story 13 requires augmented assignment to be classified as write.
    We do NOT also emit a read for the LHS (the pre-mutation read is implicit and
    capturing it would produce a duplicate reads edge for the same ivar in the same
    operation, which misleads blast-radius analysis).
    """
    children = list(node.children)
    if not children:
        return

    # LHS: first child
    lhs = children[0]
    if lhs.type == "instance_variable":
        field_name = _ruby_ivar_field_name(lhs)
        if field_name:
            target = _ruby_qualify(field_name, class_name)
            result.append((source_fn, target, "writes", lhs.start_point[0] + 1))

    # RHS: last child — collect reads.
    if len(children) >= 3:
        rhs = children[-1]
        _ruby_collect_reads_recursive(rhs, source_fn, class_name, result)


def _ruby_emit_read(
    node: Node,
    source_fn: str,
    class_name: str | None,
    result: list[FieldAccess],
) -> None:
    """Emit a reads edge for a Ruby instance_variable node.

    Ruby instance variables are NEVER in call position (calls use a separate 'call'
    node type — this is different from Python/TS where the attribute node itself
    may appear as the function of a call expression).
    So any bare instance_variable node that reaches this function is a read.
    """
    field_name = _ruby_ivar_field_name(node)
    if field_name:
        target = _ruby_qualify(field_name, class_name)
        result.append((source_fn, target, "reads", node.start_point[0] + 1))


def _ruby_collect_reads_recursive(
    node: Node,
    source_fn: str,
    class_name: str | None,
    result: list[FieldAccess],
) -> None:
    """Recursively collect reads from a Ruby expression node.

    Skips nested scope-creating constructs (method/class/module).
    Emits reads for instance_variable nodes that are not in write position.
    """
    t = node.type
    if t in ("method", "singleton_method", "class", "module"):
        return

    if t == "instance_variable":
        _ruby_emit_read(node, source_fn, class_name, result)
        return

    for child in node.children:
        _ruby_collect_reads_recursive(child, source_fn, class_name, result)


def _ruby_ivar_field_name(ivar_node: Node) -> str | None:
    """Extract the field name from a Ruby instance_variable node, stripping '@'.

    Ruby instance_variable text is '@field_name'. We strip the '@' prefix to produce
    the field name used in qualified targets ('ClassName.field_name').

    Returns None if the text is empty or only '@'.
    """
    text = _text(ivar_node)
    if not text or not text.startswith("@"):
        return None
    field_name = text[1:]  # Strip leading '@'
    return field_name if field_name else None


def _ruby_qualify(field_name: str, class_name: str | None) -> str:
    """Qualify a Ruby field name with the enclosing class name.

    Returns 'ClassName.field_name' when class_name is known (EXTRACTED),
    or bare 'field_name' when at module level (AMBIGUOUS at read time).
    """
    if class_name:
        return f"{class_name}.{field_name}"
    return field_name


def collect_field_symbols_ruby(
    class_node: Node,
    class_name: str,
) -> list[tuple[str, int]]:
    """Collect (qualified_field_name, line) pairs from a Ruby class node.

    Returns field symbols for:
      @ivar first-assignment sites in ANY method body (not just initialize).
      The first occurrence of each @ivar (by field name) is used as the symbol
      location. Dedup by (class_name, field_name) — multiple assignments → one symbol.

    NOTE: Ruby has no static field declarations (unlike Python's class-level annotations).
    The only way to discover @ivar fields is from assignment sites in method bodies.
    We scan ALL methods (not just initialize) to catch ivars assigned in setters etc.
    First-seen wins for the line number.

    Returns [] on any error. Never raises.
    """
    try:
        seen_fields: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            # Ruby class body is in a body_statement child
            for child in class_node.children:
                if child.type == "body_statement":
                    body = child
                    break
        if body is None:
            return result

        # Scan all method/singleton_method children for @ivar first-assignments.
        for child in body.children:
            try:
                if child.type in ("method", "singleton_method"):
                    _ruby_scan_method_for_ivar_assignments(
                        child, class_name, seen_fields, result
                    )
            except Exception:  # noqa: BLE001
                pass

        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_field_symbols_ruby: failed for class %r: %r", class_name, exc)
        return []


def _ruby_scan_method_for_ivar_assignments(
    method_node: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Scan a Ruby method body for @ivar = ... assignments, collecting field symbols.

    Scans the method's body_statement for any assignment or operator_assignment
    whose LHS is an instance_variable. Only the FIRST occurrence of each @ivar
    (by field name) is recorded.
    """
    body = method_node.child_by_field_name("body")
    if body is None:
        return

    for stmt in body.children:
        _ruby_try_collect_ivar_stmt(stmt, class_name, seen, result)


def _ruby_try_collect_ivar_stmt(
    stmt: Node,
    class_name: str,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Try to collect an @ivar field symbol from a single Ruby statement.

    Handles:
      assignment: @ivar = ...
      operator_assignment: @ivar += ...
    Both forms indicate the @ivar is a stored field of the class.
    """
    t = stmt.type
    if t not in ("assignment", "operator_assignment"):
        return

    children = list(stmt.children)
    if not children:
        return

    lhs = children[0]
    if lhs.type != "instance_variable":
        return

    field_name = _ruby_ivar_field_name(lhs)
    if not field_name:
        return

    qualified = f"{class_name}.{field_name}"
    if qualified not in seen:
        seen.add(qualified)
        result.append((qualified, stmt.start_point[0] + 1))


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
#   The PHP grammar uses 'augmented_assignment_expression' (not 'operator_assignment').
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

    WHY: PHP distinguishes member_access_expression ($obj->field) from
    member_call_expression ($obj->method()), so there's no ambiguity from
    node type alone. We still guard against being inside a call just in case.
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

    PHP field access: member_access_expression has 'object' (receiver) and 'name'
    (field identifier). The 'name' node type is 'name' (plain identifier in PHP grammar).

    Conservatism contract: NEVER emit a wrong qualified target.
    Never raises (returns None on any exception).
    """
    try:
        obj_node = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if obj_node is None or name_node is None:
            return None

        # name_node should be a 'name' node (plain identifier).
        # Skip if it's something dynamic.
        if name_node.type != "name":
            return None

        field_name = _text(name_node)
        if not field_name:
            return None

        receiver_text = _text(obj_node)

        # Resolve receiver using PHP self-names ($this) + var_types.
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
    children (e.g. 'public int $x = 0, $y = 1;' — though rare in practice).

    The field name has the '$' prefix stripped: '$balance' → 'Account.balance'.
    Unlike the holds collector, we do NOT filter by user-type constraints —
    ALL properties are indexed (including primitive types like int, string).

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

    WHY no type filter: unlike holds (which only cares about user-type composition),
    field symbols index ALL properties regardless of type — 'balance: int' is
    just as important as 'client: Client' for field-access queries.
    """
    for child in prop_decl.children:
        if child.type == "property_element":
            for pc in child.children:
                if pc.type == "variable_name":
                    # variable_name text includes $ prefix: '$balance'
                    raw_name = _text(pc).strip()
                    # Strip '$' prefix for the field symbol name.
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
#   In Swift tree-sitter, both plain and augmented assignments use 'assignment' node.
#   Augmented: the operator is '+=' etc.; plain: operator is '='.
#
# AST shapes:
#   assignment → directly_assignable_expression → navigation_expression → write
#   statements (non-assignment) → navigation_expression → read
#   call_expression → navigation_expression (callee, first child) → NOT field edge
#
# Receiver resolution:
#   self_expression → enclosing class (EXTRACTED).
#   Other vars: resolved via _resolve_navigation_target (graph_swift_infer).
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

    WHY both plain and augmented as write: story 13 requires augmented assignment
    (balance += x) to be classified as write. Swift tree-sitter uses the same
    'assignment' node for both, distinguished by the operator child.
    """
    # Find LHS (directly_assignable_expression) and RHS (the value).
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

    directly_assignable_expression contains a navigation_expression when the
    assignment target is a property access (self.balance = v).
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

    # The callee is the first child. If it's a navigation_expression, it's a call.
    # Skip it (no field edge). Recurse into call_suffix (arguments).
    for child in node.children:
        if child.type == "call_suffix":
            for sub in child.children:
                _swift_walk_stmt(sub, source_fn, class_name, var_types, result)
        # Also recurse into value_arguments to handle argument reads.
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

    WHY start_point comparison: same reasoning as other languages — tree-sitter
    creates new Node objects on each access; start_point is the stable identity.
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

    Swift navigation_expression structure:
      <receiver_expr>  <navigation_suffix>
    where navigation_suffix contains '.' + simple_identifier (the field/property name).

    Returns None when the node lacks the expected shape (e.g. subscript access).
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

        # The first child is the receiver; the last child is the navigation_suffix.
        receiver_node = children[0]
        # Find navigation_suffix.
        nav_suffix = None
        for child in children:
            if child.type == "navigation_suffix":
                nav_suffix = child
                break
        if nav_suffix is None:
            return None

        # Extract the field/property name from navigation_suffix.
        # navigation_suffix: '.' simple_identifier
        field_name = None
        for child in nav_suffix.children:
            if child.type == "simple_identifier":
                field_name = _text(child)
                break
        if not field_name:
            return None

        receiver_text = _text(receiver_node)

        # Receiver type resolution:
        # 1. self_expression → enclosing class (EXTRACTED).
        # 2. simple_identifier in var_types → qualified (EXTRACTED).
        # 3. Unresolvable → bare field name (AMBIGUOUS).
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
    A stored property is a property_declaration that has a 'pattern' child (the name),
    but is NOT a computed property (no computed property body with { ... }).

    Unlike the holds collector (collect_composition_types_swift), we do NOT filter
    by user-type constraints — ALL stored properties are indexed regardless of type
    (Int, String, custom types — all become field symbols).

    Returns [] on any error. Never raises.
    """
    try:
        result: list[tuple[str, int]] = []

        # Find class_body child.
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

    Swift property_declaration structure:
      modifiers? value_binding_pattern pattern type_annotation? ('=' value)? (computed_body)?
    The 'pattern' child contains a simple_identifier — the property name.
    Computed properties have a computed_property or computed_value_body child.

    WHY skip computed properties: a computed property like 'var doubled: Int { balance * 2 }'
    is a function, not a stored field. We only index stored properties that hold actual state.
    A stored property has no computed body (no '{' block directly in the declaration).

    WHY no type filter: same reasoning as other languages — ALL stored properties
    are indexed so that 'who writes balance' works for primitive-typed properties too.
    """
    # Check for computed property (has computed_property, computed_value_body, or
    # a direct code_block child) — skip those.
    for child in prop_decl.children:
        if child.type in ("computed_property", "computed_value_body", "code_block"):
            return  # It's a computed property, not a stored field

    # Extract the property name from the 'pattern' child.
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
