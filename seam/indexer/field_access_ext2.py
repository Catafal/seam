"""Field-access edge helper — Ruby classification (façade for PHP + Swift too).

This file contains the Ruby field-access implementation and re-exports the PHP and
Swift implementations from field_access_php_swift so callers can continue using
`from seam.indexer.field_access_ext2 import <name>` without changes.

LAYER: leaf — imports only stdlib + tree_sitter + resolve_receiver_type helpers.
Never imports from graph.py, db.py, or any non-leaf seam module.

LAYERING:
    graph_common                  (leaf — no seam deps)
    graph_scope_infer_ext2        (leaf — Java/C#/C++/Ruby/PHP receiver-type inference)
         ↑
    field_access_php_swift  (leaf — PHP and Swift field-access implementation)
    field_access_ext2       (this file — Ruby implementation + façade re-exports)
         ↑
    graph_ruby       (calls extract_field_accesses_ruby + collect_field_symbols_ruby)
    graph_php        (calls extract_field_accesses_php + collect_field_symbols_php)
    graph_swift      (calls extract_field_accesses_swift + collect_field_symbols_swift)

WHY split: the combined Ruby/PHP/Swift implementation exceeded the 1000-line limit.
PHP and Swift are in field_access_php_swift.py; all public names re-exported here.

Ruby:
  instance_variable node (@balance) — standalone, belongs to enclosing class.
  assignment with instance_variable LHS → write.
  operator_assignment (+=, -=, etc.) with instance_variable LHS → write.
  Every other instance_variable occurrence → read.
  Call nodes (call) → NOT a field edge (stays a 'call' edge).

NEVER RAISES: all public functions have a backstop try/except and return [] on error.
"""

import logging

from tree_sitter import Node

# Re-export PHP and Swift implementations so callers need no import changes.
# noqa: F401 — intentional re-exports for the façade pattern.
from seam.indexer.field_access_php_swift import (  # noqa: F401
    collect_field_symbols_php,
    collect_field_symbols_swift,
    extract_field_accesses_php,
    extract_field_accesses_swift,
)
from seam.indexer.graph_common import _text

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

    WHY no receiver resolution step: Ruby @ivars are implicitly bound to the enclosing
    class — there is no receiver expression to resolve. `@balance` always belongs to
    `self` (the current instance), so the qualified target is always `ClassName.balance`
    when inside a class method. No var_types lookup is needed or possible.
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

    WHY scan all methods and not just initialize: Ruby setters like `def balance=(v)`
    assign `@balance = v` — these are the canonical assignment sites for that field.
    Restricting to initialize would miss such setter-defined fields entirely.
    The first-seen line number gives a reasonable canonical location even when an ivar
    is first assigned in a setter rather than the constructor.

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

