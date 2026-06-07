"""Scope-inference module for receiver-type resolution in Python and TypeScript/JS.

LAYER: leaf — imports from graph_common (leaf) and stdlib only. Never imports from
graph.py, graph_swift, or any other seam module with side effects.

LAYERING:
    graph_common       (leaf — no seam deps)
         ↑
    graph_scope_infer  (this file — pure receiver-type inference, no Edge construction)
         ↑
    graph.py           (_extract_edges_python / _extract_edges_typescript use these helpers)

WHY a separate module:
  1. graph.py would exceed the 1000-line limit if this logic were inlined.
  2. The scope-inference cluster (pre-scan + per-function lookup + resolve) is a coherent
     leaf unit — no Edge construction, no AST walker orchestration — so it belongs in a
     dedicated leaf, mirroring the graph_swift_infer.py precedent from Phase 10.

CONSERVATISM CONTRACT (identical to graph_swift_infer):
  NEVER emit a wrong edge. resolve_receiver_type returns None on ANY uncertainty:
    - Optionals: Foo | None, Foo?, Optional[Foo] → None
    - Containers: list[T], List[T], dict[K,V], Tuple[...] → None
    - Generics:   Array<T>, Set<T>, Map<K,V> → None
    - Unknown:    identifiers not found in the field/param/local scope → None
    - Chained:    a.b.c() where the 'b' field has no known type → None
  Callers that receive None keep the raw bare target (never fabricate a type).

Two-layer scope model (order-independent):
  Layer 1 — class-level pre-scan: field/property type bindings gathered for the whole
    class body BEFORE walking methods. This is essential for DI'd stored properties
    that are declared after the method that uses them.
  Layer 2 — per-function scope: parameter and local-variable type bindings accumulated
    during the function-body walk. Function scope is fresh for each function.

Self/this/cls normalization:
  self / cls  → enclosing class name (Python)
  this / super → enclosing class name (TypeScript/JS) for 'super': same class because
    we only know the type at declaration, not the base class; refuse if enclosing unknown.

All public functions never raise (backstop try/except in each body).
"""

import logging

from tree_sitter import Node

from seam.indexer.graph_common import _text

logger = logging.getLogger(__name__)

# ── Self/this normalized receivers ────────────────────────────────────────────

# Python: 'self' and 'cls' are conventional but not enforced — treat any of these
# as "enclosing type" without caring about exact identifier.
_PY_SELF_NAMES: frozenset[str] = frozenset({"self", "cls"})

# TypeScript/JS: 'this' always refers to the enclosing class instance.
# 'super' is deliberately excluded — we can't statically know the base class.
_TS_SELF_NAMES: frozenset[str] = frozenset({"this"})


# ── Shared: resolve a receiver expression to a type name ─────────────────────


def resolve_receiver_type(
    receiver_text: str,
    class_name: str | None,
    var_types: dict[str, str],
    self_names: frozenset[str],
) -> str | None:
    """Resolve a receiver expression to its declared type name.

    This is the core lookup that maps a receiver string (as captured by the edge
    extractor) to a type name using the current scope state (class fields + params +
    locals) — exactly mirroring _resolve_navigation_target in graph_swift_infer.

    Args:
        receiver_text: Raw receiver text captured from the AST (e.g. 'self', 'client',
                       'this', 'a.b'). Multi-part receivers (containing '.') resolve
                       only if they start with a self/this alias followed by a known
                       class field.
        class_name:    Name of the enclosing class (or None for module-level functions).
        var_types:     scope map — name → type for all in-scope bindings (class fields
                       merged with per-function params and locals).
        self_names:    Set of receiver strings that alias 'self' (e.g. {'self','cls'}
                       for Python, {'this'} for TypeScript).

    Returns:
        The resolved type name string (e.g. 'Client'), or None when the type cannot
        be determined with confidence. None → caller keeps the bare target.
    """
    try:
        if not receiver_text:
            return None

        # Multi-part receiver: only handle 'self.field' / 'this.field' patterns.
        # a.b.method() where 'a' is not a self-alias → refuse (cross-class chain).
        if "." in receiver_text:
            return _resolve_chained(receiver_text, class_name, var_types, self_names)

        # Plain identifier — check self/this alias first.
        if receiver_text in self_names:
            return class_name  # May be None (module-level scope)

        # Look up in the scope map (class fields + params + locals).
        return var_types.get(receiver_text)  # None if not in scope

    except Exception as exc:  # noqa: BLE001
        logger.debug("resolve_receiver_type: failed for %r: %r", receiver_text, exc)
        return None


def _resolve_chained(
    receiver_text: str,
    class_name: str | None,
    var_types: dict[str, str],
    self_names: frozenset[str],
) -> str | None:
    """Resolve a dotted receiver like 'self.field' or 'this.repo'.

    Only ONE level of chaining is supported:
      self.field / this.field  → look up field's type from var_types
    Everything else (self.a.b, unknown.field, chained member expressions) → None.

    This mirrors _resolve_navigation_target's self.prop.method handling in Swift:
    only the self.<prop> form is resolvable since we only track class-level field types.
    """
    parts = receiver_text.split(".", 1)
    if len(parts) != 2:
        return None
    head, field = parts
    if head not in self_names:
        # Foreign chain (a.b, not self.b) → refuse — would need cross-class field typing.
        return None
    return var_types.get(field)  # None if the field has no known type


# ── Python: plain-type extraction ────────────────────────────────────────────


def _py_plain_type_from_annotation(ann_node: Node) -> str | None:
    """Extract a plain type name from a Python type annotation node.

    Conservative: returns the type name ONLY for bare identifiers. Refuses:
      - Union types (X | Y, Optional[X])  → node type contains '|' or subscript
      - Generic subscripts (list[T], List[T], dict[K,V])
      - String-quoted annotations (deferred — we'd need to eval the string)
      - Any complex expression

    WHY refuse optionals and generics: `foo: Optional[Client]` means `foo` could be
    None; emitting `Client.method()` on `foo.method()` would be wrong when foo=None.
    `foo: list[Client]` means `foo` is a container — `foo.method()` resolves on list,
    not Client. The conservatism contract requires NEVER emitting a wrong edge, so
    any type that could be None or that wraps another type is refused entirely.

    The node passed here is whatever tree-sitter calls the 'type' field, or
    an 'annotation' node child. We inspect its grammar type:
      identifier      → plain bare name → accept
      type            → nested type node → recurse one level
      subscript       → generic subscript (list[Foo]) → refuse
      binary_operator → X | Y union → refuse
      attribute       → dotted type (pkg.Foo) → refuse
      string          → deferred annotation → refuse (conservative)
    """
    if ann_node is None:
        return None
    t = ann_node.type
    if t == "identifier":
        name = _text(ann_node)
        # Reject built-in type names that are not user classes.
        if name in _PY_BUILTIN_TYPES:
            return None
        return name or None
    if t == "type":
        # Nested type wrapper — recurse into its first child.
        for child in ann_node.children:
            if child.type not in (":", "->"):
                return _py_plain_type_from_annotation(child)
        return None
    # All other shapes (subscript, binary_operator, attribute, string, etc.) → refuse.
    return None


# Python built-in type names that are NOT user classes. Seeing one of these as
# the annotation means we must NOT try to resolve .method() calls on it.
_PY_BUILTIN_TYPES: frozenset[str] = frozenset({
    "int", "float", "str", "bytes", "bool", "None", "object",
    "list", "dict", "tuple", "set", "frozenset",
    "List", "Dict", "Tuple", "Set", "FrozenSet", "Optional", "Union",
    "Any", "Type", "Callable", "Generator", "Iterator", "Iterable",
    "Sequence", "Mapping", "MutableMapping", "MutableSequence",
    "ClassVar", "Final", "Literal", "TypeVar",
})


def _py_constructor_class(value_node: Node) -> str | None:
    """If value_node is a 'ClassName(...)' constructor call, return 'ClassName', else None.

    A Python constructor call is a call node whose function child is a bare identifier.
    Conservative: refuses dotted constructors (pkg.Foo()), subscripts (Foo[T]()), etc.
    """
    if value_node.type != "call":
        return None
    func = value_node.child_by_field_name("function")
    if func is None:
        return None
    if func.type == "identifier":
        name = _text(func)
        if name and name not in _PY_BUILTIN_TYPES:
            return name
    return None


# ── Python: field pre-scan ────────────────────────────────────────────────────


def scan_class_fields_python(class_node: Node) -> dict[str, str]:
    """Pre-scan a Python class_definition body for field-level type bindings.

    WHY a pre-scan: methods may reference a field before its declaration in source
    order. Pre-scanning the whole class body gives an order-independent type map.

    Captures:
      field: Type          (annotated class variable, no value)
      field: Type = ...    (annotated class variable with value)
      field = ClassName()  (plain assignment with constructor; conservative)

    Returns name → type dict for direct class body members only (not nested classes).
    Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for stmt in body.children:
            _py_scan_field_stmt(stmt, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_python: failed: %r", exc)
    return out


def _py_scan_field_stmt(stmt: Node, out: dict[str, str]) -> None:
    """Record a single class body statement's field binding if it has a plain type.

    Handles:
      expression_statement → assignment with 'type' field (field: Type or field: Type = val)
      expression_statement → assignment without 'type' field (field = ClassName())

    NOTE: Python tree-sitter represents `x: Type` and `x: Type = val` as `assignment`
    nodes (not `annotated_assignment`), with the annotation stored in the 'type' field.
    The `annotated_assignment` node type exists in older grammars but in practice the
    current Python grammar uses `assignment` with an optional `type` field.
    We handle both for robustness.
    """
    try:
        if stmt.type == "expression_statement" and stmt.children:
            inner = stmt.children[0]
            if inner.type in ("assignment", "annotated_assignment"):
                # Check for type annotation field first (covers x: Type and x: Type = val)
                ann = inner.child_by_field_name("type")
                if ann is not None:
                    _py_record_annotated_assignment(inner, out)
                else:
                    # Plain assignment — check if RHS is a constructor call.
                    _py_record_constructor_assignment(inner, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_py_scan_field_stmt: failed: %r", exc)


def _py_record_annotated_assignment(node: Node, out: dict[str, str]) -> None:
    """Record binding from `name: Type` or `name: Type = value`.

    Handles both `annotated_assignment` and `assignment` nodes with a `type` field.
    tree-sitter Python uses `assignment` nodes (with optional `type` field) for:
      field: Type        → assignment{left=identifier, type=type}
      field: Type = val  → assignment{left=identifier, type=type, right=...}
    """
    try:
        left = node.child_by_field_name("left")
        ann = node.child_by_field_name("type")
        if left is None or ann is None:
            return
        # Only bare identifier targets (no 'self.x: T' — that's an attribute)
        if left.type != "identifier":
            return
        name = _text(left)
        if not name:
            return
        type_name = _py_plain_type_from_annotation(ann)
        if type_name:
            out[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_py_record_annotated_assignment: failed: %r", exc)


def _py_record_constructor_assignment(node: Node, out: dict[str, str]) -> None:
    """Record binding from `name = ClassName()`.

    tree-sitter Python: assignment has left + right children.
    """
    try:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return
        if left.type != "identifier":
            return
        name = _text(left)
        if not name:
            return
        cls = _py_constructor_class(right)
        if cls:
            out[name] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("_py_record_constructor_assignment: failed: %r", exc)


# ── Python: per-function scope ────────────────────────────────────────────────


def record_py_param_types(func_node: Node, var_types: dict[str, str]) -> None:
    """Bind each function parameter's name → declared type into var_types.

    Handles:
      def f(self, x: Foo, y: Bar) → binds x→Foo, y→Bar
      def f(cls, x: Foo) → binds x→Foo (cls itself is handled by _PY_SELF_NAMES)

    Conservative: only plain type annotations bind. Optional / generic / union → skip.
    Never raises.
    """
    try:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return
        for param in params_node.children:
            _py_record_single_param(param, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_py_param_types: failed: %r", exc)


def _py_record_single_param(param: Node, var_types: dict[str, str]) -> None:
    """Record name → type for a single parameter node.

    tree-sitter Python parameter shapes:
      identifier                     (positional, no annotation)
      typed_parameter                 (positional with annotation: x: Foo)
      default_parameter               (positional with default: x=val — no annotation)
      typed_default_parameter         (positional with annotation+default: x: Foo = val)
      list_splat_pattern / dict_splat_pattern  (*args/**kwargs — skip)
    """
    try:
        t = param.type
        if t == "typed_parameter":
            # children: identifier, ':', type
            names = [c for c in param.children if c.type == "identifier"]
            ann = param.child_by_field_name("type")
            if names and ann:
                pname = _text(names[0])
                if pname and pname not in _PY_SELF_NAMES:
                    type_name = _py_plain_type_from_annotation(ann)
                    if type_name:
                        var_types[pname] = type_name
        elif t == "typed_default_parameter":
            name_node = param.child_by_field_name("name")
            ann = param.child_by_field_name("type")
            if name_node and ann:
                pname = _text(name_node)
                if pname and pname not in _PY_SELF_NAMES:
                    type_name = _py_plain_type_from_annotation(ann)
                    if type_name:
                        var_types[pname] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_py_record_single_param: failed: %r", exc)


def record_py_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a local statement (annotated assignment or constructor).

    Handles two patterns inside function bodies:
      x: Foo = ...   (assignment with type field — Python grammar for annotated vars)
      x = Foo()      (assignment whose RHS is a constructor call)

    NOTE: Python tree-sitter uses `assignment` nodes with a `type` field for `x: Foo`
    patterns (not the `annotated_assignment` node type). We check for `type` field first.

    Called incrementally during the function-body walk so later code in the same
    function can resolve vars defined earlier. Never raises.
    """
    try:
        if stmt_node.type == "expression_statement" and stmt_node.children:
            inner = stmt_node.children[0]
            if inner.type in ("assignment", "annotated_assignment"):
                ann = inner.child_by_field_name("type")
                if ann is not None:
                    _py_record_annotated_assignment(inner, var_types)
                else:
                    _py_record_constructor_assignment(inner, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_py_local_types: failed: %r", exc)


# ── TypeScript/JS: plain-type extraction ──────────────────────────────────────


def _ts_plain_type_from_annotation(type_node: Node) -> str | None:
    """Extract a plain user type name from a TypeScript type annotation.

    Conservative: only accepts a bare type_identifier. Refuses:
      - Union types (string | null, Foo | Bar)  → union_type node
      - Generic types (Array<T>, Map<K,V>)      → generic_type node
      - Array types (T[])                       → array_type node (or predefined_type)
      - Intersection, tuple, function types     → other nodes
      - Predefined/primitive types              → predefined_type (string, number, etc.)

    WHY refuse union types: `foo: Client | null` means `foo` may be null; binding
    Client and emitting `Client.method()` would be incorrect half the time at runtime.
    WHY refuse generics: `Array<Client>` or `Map<K,V>` are container types — calling
    `.method()` on them resolves on the container, not the element type.
    Both refusals are required by the conservatism contract: prefer no edge over a
    wrong edge.

    The node passed here is a type_annotation node or a direct type child.
    """
    if type_node is None:
        return None
    t = type_node.type

    # type_annotation wraps the actual type — skip the ':' and recurse.
    if t == "type_annotation":
        for child in type_node.children:
            if child.type not in (":", "=>"):
                result = _ts_plain_type_from_annotation(child)
                if result:
                    return result
        return None

    # A bare type identifier (e.g. `Client`, `Parser`, `Engine`)
    if t == "type_identifier":
        name = _text(type_node)
        if name and name not in _TS_BUILTIN_TYPES:
            return name
        return None

    # All other node types (union_type, generic_type, array_type, predefined_type,
    # intersection_type, tuple_type, function_type, undefined_type, literal_type) → refuse.
    return None


# TypeScript built-in / primitive type names that are not user classes.
_TS_BUILTIN_TYPES: frozenset[str] = frozenset({
    "string", "number", "boolean", "void", "any", "unknown", "never", "object",
    "symbol", "bigint", "null", "undefined",
    "Array", "Map", "Set", "WeakMap", "WeakSet", "WeakRef",
    "Promise", "Function", "Object", "Error",
    "String", "Number", "Boolean", "Symbol", "BigInt",
    "Readonly", "Record", "Partial", "Required", "Pick", "Omit",
    "Exclude", "Extract", "NonNullable", "ReturnType", "InstanceType",
    "Parameters", "ConstructorParameters",
    "ReadonlyArray", "ReadonlyMap", "ReadonlySet",
})


def _ts_constructor_class(value_node: Node) -> str | None:
    """If value_node is `new ClassName(...)`, return 'ClassName', else None.

    TypeScript new_expression: `new Foo(...)` has a 'constructor' field that is a
    type_identifier (plain class name) or a member_expression (namespace.Foo — refuse).
    Conservative: only accepts plain type_identifier.
    """
    if value_node.type != "new_expression":
        return None
    constructor = value_node.child_by_field_name("constructor")
    if constructor is None:
        return None
    if constructor.type == "identifier":
        name = _text(constructor)
        if name and name not in _TS_BUILTIN_TYPES:
            return name
    return None


# ── TypeScript: class field pre-scan ─────────────────────────────────────────


def scan_class_fields_typescript(class_node: Node) -> dict[str, str]:
    """Pre-scan a TS class_declaration body for field-level type bindings.

    WHY pre-scan: same reason as Python — methods may reference a field declared
    below them. Pre-scanning the class body gives an order-independent field map.

    Captures:
      field: Type;               (public_field_definition or field_definition)
      field: Type = new Foo();   (field with initializer — take annotation type)

    Returns name → type dict for direct class members only (not nested classes).
    Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for child in body.children:
            _ts_scan_field_member(child, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_typescript: failed: %r", exc)
    return out


def _ts_scan_field_member(member: Node, out: dict[str, str]) -> None:
    """Record a single class body member if it is a field with a plain type annotation.

    TypeScript grammar field shapes:
      public_field_definition  → name field (property_identifier) + type (type_annotation)
      field_definition         → same structure (used in some grammars)
    """
    try:
        if member.type not in ("public_field_definition", "field_definition"):
            return
        name_node = member.child_by_field_name("name")
        type_node = member.child_by_field_name("type")
        if name_node is None or type_node is None:
            return
        field_name = _text(name_node)
        if not field_name:
            return
        type_name = _ts_plain_type_from_annotation(type_node)
        if type_name:
            out[field_name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_ts_scan_field_member: failed: %r", exc)


# ── TypeScript: per-function scope ────────────────────────────────────────────


def record_ts_param_types(func_node: Node, var_types: dict[str, str]) -> None:
    """Bind each TS/JS function parameter's name → declared type into var_types.

    Handles:
      required_parameter  (x: Foo)
      optional_parameter  (x?: Foo)

    Conservative: only plain type_identifier annotations bind. Never raises.
    """
    try:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return
        for param in params_node.children:
            _ts_record_single_param(param, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_ts_param_types: failed: %r", exc)


def _ts_record_single_param(param: Node, var_types: dict[str, str]) -> None:
    """Record name → type for a single TS/JS parameter node."""
    try:
        if param.type not in ("required_parameter", "optional_parameter"):
            return
        name_node = param.child_by_field_name("pattern")
        type_node = param.child_by_field_name("type")
        if name_node is None or type_node is None:
            return
        if name_node.type not in ("identifier", "shorthand_property_identifier_pattern"):
            return
        pname = _text(name_node)
        if not pname or pname in _TS_SELF_NAMES:
            return
        type_name = _ts_plain_type_from_annotation(type_node)
        if type_name:
            var_types[pname] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_ts_record_single_param: failed: %r", exc)


def record_ts_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a local TS/JS statement.

    Handles:
      const x: Foo = ...    (lexical_declaration with type_annotation)
      const x = new Foo()   (lexical_declaration with new_expression initializer)
      let x: Foo = ...      (same — let)

    Called incrementally during the function-body walk. Never raises.
    """
    try:
        if stmt_node.type not in ("lexical_declaration", "variable_declaration"):
            return
        for child in stmt_node.children:
            if child.type == "variable_declarator":
                _ts_record_single_declarator(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_ts_local_types: failed: %r", exc)


def _ts_record_single_declarator(decl: Node, var_types: dict[str, str]) -> None:
    """Record one variable_declarator's name → type binding.

    Two forms:
      const x: Type = ...   → 'type' field is type_annotation → use annotation
      const x = new Foo()   → 'value' field is new_expression → use constructor name
    Annotation wins over constructor when both present.
    """
    try:
        name_node = decl.child_by_field_name("name")
        if name_node is None or name_node.type not in ("identifier",):
            return
        var_name = _text(name_node)
        if not var_name:
            return

        # Prefer explicit type annotation.
        type_node = decl.child_by_field_name("type")
        if type_node is not None:
            type_name = _ts_plain_type_from_annotation(type_node)
            if type_name:
                var_types[var_name] = type_name
                return

        # Fall back to constructor-call inference.
        value_node = decl.child_by_field_name("value")
        if value_node is not None:
            cls = _ts_constructor_class(value_node)
            if cls:
                var_types[var_name] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("_ts_record_single_declarator: failed: %r", exc)


# ── Slice #77: Composition (holds) collectors ────────────────────────────────
#
# These functions return deduped (held_type_name, line) pairs for a class node.
# They REUSE the existing scan_class_fields_* helpers (Layer 1 field pre-scan) for
# the stored-field half, and add a constructor/init-parameter pass using the SAME
# plain-type extractors (_py_plain_type_from_annotation, _ts_plain_type_from_annotation).
#
# CONSERVATISM CONTRACT (same as receiver-type inference):
#   NEVER emit a wrong type. Only plain user-type identifiers are accepted.
#   Optionals, containers, generics, primitives, and dotted types are all refused.
#   The existing scan_class_fields_* helpers already apply this filter for fields.
#   Constructor/init params add the same filter on top.
#
#   WHY no is_builtin() post-filter here (unlike graph_scope_infer_ext*.py):
#     This file is a LEAF module — it may only import from graph_common and stdlib.
#     Importing seam.analysis.builtins would break the layering constraint. The
#     _PY_BUILTIN_TYPES and _TS_BUILTIN_TYPES frozensets (already used by the
#     plain-type helpers) serve the same authoritative-filter role in a leaf-safe way.
#
# WHY constructor/__init__ params count as composition but other method params do not:
#   A constructor/init parameter that is stored as a field represents an OWNED
#   dependency — the object holds a reference for its lifetime. Parameters of
#   other methods are transient (passed per-call) and express USE, not composition.
#   Emitting holds edges for non-init params would conflate dependency injection with
#   ordinary function arguments, producing false composition claims.
#
# DEDUPE: a type name that appears as BOTH a field and a ctor/init param is emitted
# only ONCE. Dedupe is per (source, target) — the set returned here is deduplicated.
#
# NEVER RAISES: all public functions have a backstop try/except and return [] on error.


def collect_composition_types_python(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a Python class_definition node.

    Two passes:
      1. Field types: reuses scan_class_fields_python (annotated class attrs).
      2. __init__ parameter types: scans parameters of __init__ with plain-type annotations.

    Deduped: if the same type appears in both field and __init__ param, only one entry
    is returned (first source wins — typically the field declaration wins since the class
    body is scanned first, but dedup uses a set so order doesn't matter for correctness).

    Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        # Pass 1: class-level field annotations.
        # scan_class_fields_python returns name→type, but we need the LINE too.
        # Walk the body directly to capture line numbers alongside types.
        body = class_node.child_by_field_name("body")
        if body is not None:
            for stmt in body.children:
                try:
                    if stmt.type == "expression_statement" and stmt.children:
                        inner = stmt.children[0]
                        if inner.type in ("assignment", "annotated_assignment"):
                            ann = inner.child_by_field_name("type")
                            left = inner.child_by_field_name("left")
                            if ann is not None and left is not None and left.type == "identifier":
                                type_name = _py_plain_type_from_annotation(ann)
                                if type_name and type_name not in seen:
                                    seen.add(type_name)
                                    result.append((type_name, stmt.start_point[0] + 1))
                except Exception:  # noqa: BLE001
                    pass

        # Pass 2: __init__ parameter types.
        if body is not None:
            for stmt in body.children:
                try:
                    fn = _py_get_init_function(stmt)
                    if fn is None:
                        continue
                    params = fn.child_by_field_name("parameters")
                    if params is None:
                        continue
                    for param in params.children:
                        if param.type not in ("typed_parameter", "typed_default_parameter"):
                            continue
                        ann = param.child_by_field_name("type")
                        if ann is None:
                            continue
                        type_name = _py_plain_type_from_annotation(ann)
                        if type_name and type_name not in seen:
                            seen.add(type_name)
                            result.append((type_name, param.start_point[0] + 1))
                except Exception:  # noqa: BLE001
                    pass

        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_python: failed: %r", exc)
        return []


def _py_get_init_function(stmt: Node) -> Node | None:
    """Return the function_definition node if stmt is a Python __init__ method.

    Handles plain `def __init__(...)` and `@decorator def __init__(...)`.
    Returns None for all other nodes.
    """
    if stmt.type == "function_definition":
        name = _text(stmt.child_by_field_name("name") or stmt)
        if name == "__init__":
            return stmt
    elif stmt.type == "decorated_definition":
        inner = stmt.child_by_field_name("definition")
        if inner is not None and inner.type == "function_definition":
            name = _text(inner.child_by_field_name("name") or inner)
            if name == "__init__":
                return inner
    return None


def collect_composition_types_typescript(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a TypeScript class_declaration node.

    Two passes:
      1. Field types: scan public_field_definition/field_definition nodes with a plain
         type_annotation — reuses _ts_plain_type_from_annotation (same as scan_class_fields_ts).
      2. Constructor parameter types: scan required_parameter and optional_parameter nodes
         in the constructor method, including parameter-property forms
         (e.g. constructor(private svc: Service)).

    Deduped: same type from both field and ctor param → one entry.
    Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Pass 1: field declarations.
        for child in body.children:
            try:
                if child.type not in ("public_field_definition", "field_definition"):
                    continue
                type_node = child.child_by_field_name("type")
                if type_node is None:
                    continue
                type_name = _ts_plain_type_from_annotation(type_node)
                if type_name and type_name not in seen:
                    seen.add(type_name)
                    result.append((type_name, child.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Pass 2: constructor parameters (including parameter-property DI).
        for child in body.children:
            try:
                if child.type != "method_definition":
                    continue
                name_node = child.child_by_field_name("name")
                if name_node is None or _text(name_node) != "constructor":
                    continue
                # Found the constructor — scan its parameters.
                params = child.child_by_field_name("parameters")
                if params is None:
                    continue
                for param in params.children:
                    type_name = _ts_param_plain_type(param)
                    if type_name and type_name not in seen:
                        seen.add(type_name)
                        result.append((type_name, param.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_typescript: failed: %r", exc)
        return []


def _ts_param_plain_type(param: Node) -> str | None:
    """Extract a plain user type from a TS constructor parameter node.

    Handles:
      required_parameter(x: Type)              — normal param
      optional_parameter(x?: Type)             — optional param name (still has a type)
      required_parameter with access_modifier   — parameter property (private/public/protected)
        e.g. constructor(private svc: Service)
      public_field_definition used as param     — some grammars model param-props as fields

    Conservative: only plain type_identifier annotations bind. Never raises.
    """
    try:
        # Standard required/optional parameter.
        if param.type in ("required_parameter", "optional_parameter"):
            type_node = param.child_by_field_name("type")
            if type_node is not None:
                return _ts_plain_type_from_annotation(type_node)

        # Parameter property: tree-sitter models `constructor(private svc: Service)` as
        # a required_parameter whose FIRST child is an accessibility_modifier ("private",
        # "public", "protected"), followed by the identifier and type_annotation.
        # Some grammar versions emit `public_field_definition` inside the parameter list.
        if param.type == "public_field_definition":
            type_node = param.child_by_field_name("type")
            if type_node is not None:
                return _ts_plain_type_from_annotation(type_node)

        return None
    except Exception:  # noqa: BLE001
        return None
