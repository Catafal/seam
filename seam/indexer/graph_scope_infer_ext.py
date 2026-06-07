"""Scope-inference extension module — shared resolver + Go and Rust language families.

LAYER: leaf — imports from graph_common (leaf) and stdlib only. Never imports from
graph.py, graph_scope_infer, or any other seam module with side effects.

LAYERING:
    graph_common            (leaf — no seam deps)
         ↑
    graph_scope_infer_ext   (this file — shared resolver + Go/Rust type-binding helpers)
         ↑
    graph_go_rust.py        (_extract_edges_go / _extract_edges_rust use these helpers)
    graph_scope_infer_ext2  (Java/C#/C++/Ruby/PHP type-binding helpers — same leaf layer)

WHY a split from graph_scope_infer.py (B4, Python/TS/JS) and from graph_scope_infer_ext2.py:
  graph_scope_infer.py covers Python + TypeScript/JS (B4). Adding 7 more language
  families inline would push it past 1000 lines. Go/Rust live here (with shared resolver);
  Java/C#/C++/Ruby/PHP live in graph_scope_infer_ext2.py — keeping both files under 1000
  lines and following the Phase 9 split precedent.

CONSERVATISM CONTRACT (identical to graph_swift_infer + graph_scope_infer):
  NEVER emit a wrong edge. resolve_receiver_type_ext returns None on ANY uncertainty.
  Self/this/Self normalization → enclosing class name (or None if unknown).

All public functions never raise (backstop try/except in each body).
"""

import logging

from tree_sitter import Node

from seam.analysis.builtins import is_builtin
from seam.indexer.graph_common import _text

logger = logging.getLogger(__name__)

# ── Self/this aliases per language ────────────────────────────────────────────

# Go: no universal self; method receivers vary by programmer convention. We do NOT infer
# via receiver parameter name — receiver typing comes from param annotation (param/local scope).
# (No _SELF set for Go.)

# Rust: 'self' and 'Self' both refer to the enclosing impl type.
_RUST_SELF_NAMES: frozenset[str] = frozenset({"self", "Self"})


# ── Shared: resolve receiver text to a declared type name ─────────────────────


def resolve_receiver_type_ext(
    receiver_text: str,
    class_name: str | None,
    var_types: dict[str, str],
    self_names: frozenset[str],
) -> str | None:
    """Resolve a receiver expression to its declared type name.

    Identical contract to resolve_receiver_type in graph_scope_infer — just called
    from different extractors. Extracted into this module to avoid importing
    graph_scope_infer from the family extractors (would create an indirect cycle
    through the caller chain).

    Args:
        receiver_text: Raw receiver text captured from the AST (e.g. 'self', 'client',
                       'this', '$this', 'self.field').
        class_name:    Name of the enclosing class (or None for module-level functions).
        var_types:     scope map — name → type for all in-scope bindings (class fields
                       merged with per-function params and locals).
        self_names:    Set of receiver strings that alias 'self' (e.g. {'self'} for
                       Ruby, {'this'} for Java/C#/C++, {'self','Self'} for Rust).

    Returns:
        The resolved type name string (e.g. 'Client'), or None when the type cannot
        be determined with confidence. None → caller keeps the bare target.
    """
    try:
        if not receiver_text:
            return None

        # Multi-part receiver: only handle 'self.field' / 'this.field' patterns.
        # a.b.method() where 'a' is not a self-alias → refuse.
        if "." in receiver_text:
            return _resolve_chained_ext(receiver_text, class_name, var_types, self_names)

        # Plain identifier — check self/this alias first.
        if receiver_text in self_names:
            return class_name  # May be None (top-level scope)

        # Look up in the scope map (class fields + params + locals).
        return var_types.get(receiver_text)  # None if not in scope

    except Exception as exc:  # noqa: BLE001
        logger.debug("resolve_receiver_type_ext: failed for %r: %r", receiver_text, exc)
        return None


def _resolve_chained_ext(
    receiver_text: str,
    class_name: str | None,
    var_types: dict[str, str],
    self_names: frozenset[str],
) -> str | None:
    """Resolve a dotted receiver like 'self.field' or 'this.repo'.

    Only ONE level of chaining is supported:
      self.field / this.field  → look up field's type from var_types
    Everything else → None (refuse — would need cross-class field typing).
    """
    parts = receiver_text.split(".", 1)
    if len(parts) != 2:
        return None
    head, field = parts
    if head not in self_names:
        # Foreign chain (a.b, not self.b) → refuse.
        return None
    return var_types.get(field)


# ── Type-name extraction helpers ───────────────────────────────────────────────


def _strip_ref_wrapper(type_text: str) -> str | None:
    """Strip Go/Rust/C++ reference/pointer wrappers to extract the plain type name.

    Conservative: only strips a SINGLE leading pointer/reference indicator:
      *Client    → 'Client'   (Go pointer receiver, C++ pointer)
      &Client    → 'Client'   (Rust reference, C++ reference)
      **Client   → None       (double pointer — refuse; too complex)
      *[]Client  → None       (pointer to slice — refuse)
      Vec<Client>→ None       (generic — refuse; may carry multiple types)

    WHY refuse generics and double-pointers: the conservatism contract requires that we
    only bind types we can be CERTAIN about. A double-pointer `**Foo` is an unusual
    pattern where the variable holds a pointer-to-pointer — the actual type is unclear.
    A generic like `Vec<Client>` has multiple possible element types; binding the outer
    container to 'Vec' would produce wrong edges. Refusing preserves correctness over
    coverage.

    Returns the stripped name, or None if the shape is not a plain single-ref.
    """
    if not type_text:
        return None
    # Refuse generics (contain '<' or '[')
    if "<" in type_text or "[" in type_text:
        return None
    # Strip single leading * or &
    stripped = type_text.lstrip("*&").strip()
    # After stripping, must not contain further * or &
    if "*" in stripped or "&" in stripped:
        return None
    return stripped if stripped else None


# ── Go: parameter and local-variable type binding ─────────────────────────────


def record_go_param_types(func_node: Node, var_types: dict[str, str]) -> None:
    """Bind Go function/method parameter names → declared type into var_types.

    Go parameter shapes (inside parameter_list):
      parameter_declaration — one or more 'name' identifiers + 'type' field
        func f(client *Client) → binds client → Client
        func f(a, b string)    → binds both a → string (builtin → skip)

    Conservative: strips single * or & pointer/reference prefix from type name.
    Refuses generics (Vec<T>, map[K]V) — these are container types, not plain user types.
    Never raises.
    """
    try:
        params = func_node.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.named_children:
            if child.type == "parameter_declaration":
                _record_go_single_param(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_go_param_types: failed: %r", exc)


def _record_go_single_param(param_node: Node, var_types: dict[str, str]) -> None:
    """Record a single Go parameter_declaration's name → type binding.

    A parameter_declaration has:
      - One or more 'name' identifiers (parameter names)
      - A 'type' field (the declared type node)
    """
    try:
        type_node = param_node.child_by_field_name("type")
        if type_node is None:
            return
        type_text = _text(type_node).strip()
        type_name = _strip_ref_wrapper(type_text)
        if not type_name or not type_name[0].isupper():
            return  # conservative: only bind PascalCase types (user types)
        # Collect all identifier children as parameter names.
        for child in param_node.named_children:
            if child.type == "identifier":
                name = _text(child).strip()
                if name:
                    var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_go_single_param: failed at %r: %r", param_node.start_point, exc)


def record_go_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a Go local statement.

    Handles two Go declaration patterns:
      short_var_declaration:  p := &Parser{}   → p → Parser  (composite literal)
      var_declaration:        var p *Parser     → p → Parser
      assignment_statement:   p = &Parser{}     → p → Parser (less conservative)

    Conservative: only composite literals `&Type{}` or `Type{}` (Go struct literals)
    bind without an explicit type annotation. Pointer receivers are stripped.
    Never raises.
    """
    try:
        ntype = stmt_node.type
        if ntype == "short_var_declaration":
            _record_go_short_var(stmt_node, var_types)
        elif ntype in ("var_declaration", "var_spec"):
            _record_go_var_decl(stmt_node, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_go_local_types: failed: %r", exc)


def _record_go_short_var(node: Node, var_types: dict[str, str]) -> None:
    """Record binding from Go short_var_declaration: name := value.

    Handles `p := &Parser{}` → p → Parser (composite literal).
    Conservative: only composite_literal_value (Type{}) or unary_expression (&Type{}).
    """
    try:
        # short_var_declaration: left, ':=', right
        left_node = node.child_by_field_name("left")
        right_node = node.child_by_field_name("right")
        if left_node is None or right_node is None:
            return
        # Left side: expression_list containing identifiers
        names: list[str] = []
        for child in left_node.children:
            if child.type == "identifier":
                names.append(_text(child).strip())
        if not names:
            return
        # Right side: expression_list; take first expression
        values = [c for c in right_node.children if c.type not in (",",)]
        if not values:
            return
        value_node = values[0]
        cls = _go_constructor_class(value_node)
        if cls and names:
            var_types[names[0]] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_go_short_var: failed: %r", exc)


def _record_go_var_decl(node: Node, var_types: dict[str, str]) -> None:
    """Record binding from Go var_declaration or var_spec: var name Type.

    Handles:
      var client *Client → client → Client
      var client Client  → client → Client
    """
    try:
        # var_declaration has var_spec children; var_spec has name + type fields
        if node.type == "var_declaration":
            for child in node.named_children:
                if child.type == "var_spec":
                    _record_go_var_spec(child, var_types)
        elif node.type == "var_spec":
            _record_go_var_spec(node, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_go_var_decl: failed: %r", exc)


def _record_go_var_spec(node: Node, var_types: dict[str, str]) -> None:
    """Record one Go var_spec (name + type) binding."""
    try:
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        type_text = _text(type_node).strip()
        type_name = _strip_ref_wrapper(type_text)
        if not type_name or not type_name[0].isupper():
            return
        # Name field: identifier or expression_list
        name_node = node.child_by_field_name("name")
        if name_node is not None and name_node.type == "identifier":
            name = _text(name_node).strip()
            if name:
                var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_go_var_spec: failed: %r", exc)


def _go_constructor_class(value_node: Node) -> str | None:
    """If value_node is a Go `Type{}` or `&Type{}` literal, return 'Type', else None.

    Handles:
      composite_literal (Parser{...})           → 'Parser'
      unary_expression  (&Parser{...})          → 'Parser'  (pointer to composite literal)
      call_expression   (Parser.New())          → None       (function call, not literal)
    """
    try:
        if value_node.type == "composite_literal":
            # composite_literal → type field (or first child)
            type_node = value_node.child_by_field_name("type")
            if type_node is None and value_node.children:
                type_node = value_node.children[0]
            if type_node is not None:
                type_text = _text(type_node).strip()
                name = _strip_ref_wrapper(type_text) or type_text
                if name and name[0].isupper():
                    return name
        elif value_node.type == "unary_expression":
            # &Type{} → operand is composite_literal
            operand = value_node.child_by_field_name("operand")
            if operand is not None:
                return _go_constructor_class(operand)
        elif value_node.type == "call_expression":
            # Type.New() or Type{} as a call — extract function (may be selector)
            func = value_node.child_by_field_name("function")
            if func is not None and func.type == "identifier":
                name = _text(func).strip()
                if name and name[0].isupper():
                    return name
    except Exception:  # noqa: BLE001
        pass
    return None


def scan_class_fields_go(struct_node: Node) -> dict[str, str]:
    """Pre-scan a Go struct_type body for field-level type bindings.

    WHY pre-scan: methods may reference a struct field before its declaration in source
    order. Go structs appear as type_spec with a struct_type child.

    Captures:
      FieldName *Client → field → Client (pointer field)
      FieldName  Client → field → Client (value field)

    Returns name → type for direct struct fields. Never raises.
    """
    out: dict[str, str] = {}
    try:
        # struct_type has a field_declaration_list child
        for child in struct_node.children:
            if child.type == "field_declaration_list":
                for field in child.named_children:
                    if field.type == "field_declaration":
                        _record_go_field(field, out)
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_go: failed: %r", exc)
    return out


def _record_go_field(field_node: Node, out: dict[str, str]) -> None:
    """Record a single Go field_declaration's name → type binding."""
    try:
        type_node = field_node.child_by_field_name("type")
        if type_node is None:
            return
        type_text = _text(type_node).strip()
        type_name = _strip_ref_wrapper(type_text) or type_text
        if not type_name or not type_name[0].isupper():
            return
        # 'name' field may be an identifier or a field_identifier_list
        name_node = field_node.child_by_field_name("name")
        if name_node is not None:
            if name_node.type == "identifier":
                name = _text(name_node).strip()
                if name:
                    out[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_go_field: failed: %r", exc)


# ── Rust: parameter and local-variable type binding ───────────────────────────


def record_rust_param_types(func_node: Node, var_types: dict[str, str]) -> None:
    """Bind Rust function parameter names → declared type into var_types.

    Rust parameter shapes (inside parameters):
      &self, &mut self, self  → handled by _RUST_SELF_NAMES (caller uses them directly)
      name: &Client           → binds name → Client (reference stripped)
      name: Client            → binds name → Client (plain type)
      name: Option<Client>    → refused (generic)

    Conservative: only plain type_identifier and reference-wrapped type_identifier bind.
    Never raises.
    """
    try:
        params = func_node.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.named_children:
            if child.type == "parameter":
                _record_rust_single_param(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_rust_param_types: failed: %r", exc)


def _record_rust_single_param(param_node: Node, var_types: dict[str, str]) -> None:
    """Record a single Rust parameter's name → type binding.

    Rust parameter shapes:
      pattern: type  (pattern is 'identifier', type is 'type_identifier' or 'reference_type')
    """
    try:
        pattern = param_node.child_by_field_name("pattern")
        type_node = param_node.child_by_field_name("type")
        if pattern is None or type_node is None:
            return
        if pattern.type not in ("identifier", "mutable_specifier"):
            return
        # Get the parameter name
        if pattern.type == "mutable_specifier":
            # mut name: &Type — name is the next sibling
            name_node = next(
                (c for c in param_node.named_children if c.type == "identifier"), None
            )
            if name_node is None:
                return
            name = _text(name_node).strip()
        else:
            name = _text(pattern).strip()
        if not name or name in _RUST_SELF_NAMES:
            return
        type_name = _rust_plain_type(type_node)
        if type_name:
            var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_rust_single_param: failed: %r", exc)


def _rust_plain_type(type_node: Node) -> str | None:
    """Extract a plain user type name from a Rust type node.

    Conservative: only accepts:
      type_identifier        → bare name (e.g. Client)
      reference_type         → &T or &mut T (strip ref, take T if type_identifier)
      mutable_specifier + type_identifier (inside reference_type)

    Refuses generics (generic_type: Vec<T>), arrays (array_type), tuples, etc.
    """
    if type_node is None:
        return None
    t = type_node.type
    if t == "type_identifier":
        name = _text(type_node).strip()
        return name if (name and name[0].isupper()) else None
    if t == "reference_type":
        # &T or &mut T — find the inner type_identifier
        for child in type_node.named_children:
            if child.type == "type_identifier":
                name = _text(child).strip()
                return name if (name and name[0].isupper()) else None
            if child.type == "mutable_specifier":
                continue  # skip 'mut'
    return None


def record_rust_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a Rust local statement.

    Handles:
      let_declaration: let name: &Type = ...  → name → Type (annotation)
                       let name = Type::new()  → name → Type (constructor call)
                       let name = Type { ... } → name → Type (struct literal)

    Never raises.
    """
    try:
        if stmt_node.type != "let_declaration":
            return
        pattern = stmt_node.child_by_field_name("pattern")
        type_node = stmt_node.child_by_field_name("type")
        value_node = stmt_node.child_by_field_name("value")

        if pattern is None or pattern.type != "identifier":
            return
        name = _text(pattern).strip()
        if not name:
            return

        # Annotation wins (more reliable than inference from value)
        if type_node is not None:
            type_name = _rust_plain_type(type_node)
            if type_name:
                var_types[name] = type_name
                return

        # Fall back to constructor/struct-literal inference
        if value_node is not None:
            cls = _rust_constructor_class(value_node)
            if cls:
                var_types[name] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_rust_local_types: failed: %r", exc)


def _rust_constructor_class(value_node: Node) -> str | None:
    """If value_node is Type::new() or Type { ... }, return 'Type', else None.

    Handles:
      call_expression  with scoped_identifier Type::new → 'Type'
        (path field of scoped_identifier is 'identifier' in tree-sitter-rust 0.24.x)
      struct_expression  (Type { ... })                → 'Type'
    """
    try:
        if value_node.type == "call_expression":
            func = value_node.child_by_field_name("function")
            if func is not None and func.type == "scoped_identifier":
                # Type::new — the 'path' field is an identifier (the type name),
                # and 'name' field is the method (e.g. 'new').
                # In tree-sitter-rust the path can be 'identifier' or 'type_identifier'.
                path = func.child_by_field_name("path")
                if path is not None and path.type in ("identifier", "type_identifier"):
                    name = _text(path).strip()
                    return name if (name and name[0].isupper()) else None
        elif value_node.type == "struct_expression":
            # Type { field: val, ... } — name is the first type_identifier child
            for child in value_node.named_children:
                if child.type in ("type_identifier", "identifier"):
                    name = _text(child).strip()
                    return name if (name and name[0].isupper()) else None
    except Exception:  # noqa: BLE001
        pass
    return None


def scan_class_fields_rust(impl_node: Node) -> dict[str, str]:
    """Pre-scan a Rust struct_item body for field-level type bindings.

    In Rust, struct fields live in the struct_item, not the impl block. This
    function scans a struct_item's field_declaration_list.

    Returns name → type for plain field declarations. Never raises.
    """
    out: dict[str, str] = {}
    try:
        for child in impl_node.children:
            if child.type == "field_declaration_list":
                for field in child.named_children:
                    if field.type == "field_declaration":
                        name_node = field.child_by_field_name("name")
                        type_node = field.child_by_field_name("type")
                        if name_node and type_node:
                            name = _text(name_node).strip()
                            type_name = _rust_plain_type(type_node)
                            if name and type_name:
                                out[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_rust: failed: %r", exc)
    return out


# ── Slice #78: Composition (holds) collectors for Go and Rust ─────────────────
#
# These functions return deduped (held_type_name, line) pairs for a struct node.
# They REUSE scan_class_fields_go and scan_class_fields_rust for the stored-field
# half (no constructor/init-parameter pass in Go/Rust — struct fields ARE the
# composition declaration for these languages).
#
# CONSERVATISM CONTRACT (same as Python/TS collectors and receiver-type inference):
#   NEVER emit a wrong type. Only plain user-type identifiers are accepted.
#   Slices, maps, generics, primitives, and lowercase names are all refused by
#   the existing _strip_ref_wrapper + PascalCase guard in scan_class_fields_go,
#   and by _rust_plain_type in scan_class_fields_rust.
#
# DEDUPE: a type name that appears in multiple fields is emitted only ONCE.
#   Dedupe is per target type name — the set returned here is deduplicated.
#
# NEVER RAISES: all public functions have a backstop try/except, return [] on error.


def collect_composition_types_go(struct_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a Go struct_type node.

    Single pass: iterates the field_declaration_list, accepting only PascalCase
    plain-type fields (pointer fields have the pointer stripped). Deduped by type name.

    Go does not have constructors in the same sense as Python/__init__ — the struct
    field declarations ARE the composition declaration. No constructor param pass needed.

    WHY struct_node is the struct_type node (not the type_spec or type_declaration):
      scan_class_fields_go takes the struct_type node directly. The emission site in
      graph_go.py has the type_spec; it passes type_spec.child 'type' (the struct_type)
      to this collector.

    Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        # struct_type has a field_declaration_list child
        for child in struct_node.children:
            if child.type != "field_declaration_list":
                continue
            for field in child.named_children:
                if field.type != "field_declaration":
                    continue
                try:
                    type_node = field.child_by_field_name("type")
                    if type_node is None:
                        continue
                    type_text = _text(type_node).strip()
                    # _strip_ref_wrapper handles *Type → Type (and refuses slices/generics).
                    type_name = _strip_ref_wrapper(type_text) or type_text
                    # Require PascalCase (user-defined type) and non-empty.
                    if not type_name or not type_name[0].isupper():
                        continue
                    # Refuse types that still contain pointer/ref chars after strip
                    # (double-pointer or slice after strip → residual * or [).
                    if "*" in type_name or "[" in type_name or "<" in type_name:
                        continue
                    if type_name not in seen:
                        seen.add(type_name)
                        result.append((type_name, field.start_point[0] + 1))
                except Exception:  # noqa: BLE001
                    pass
            break  # Only one field_declaration_list per struct

        # Post-filter: drop dotted qualified targets (e.g. 'pkg.Client' — would be a
        # wrong/dangling edge since edges are bare-name-keyed) and language builtins
        # (the PascalCase heuristic alone admits stdlib types). is_builtin is the
        # authoritative source, mirroring graph_swift_infer.
        return [(t, ln) for (t, ln) in result if "." not in t and not is_builtin(t, "go")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_go: failed: %r", exc)
        return []


def collect_composition_types_rust(struct_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a Rust struct_item node.

    Single pass: iterates the field_declaration_list, accepting only PascalCase
    plain-type fields (reference types &T have the ref stripped via _rust_plain_type).
    Deduped by type name.

    Rust has no constructor-as-composition pattern that is statically visible in the
    struct AST (::new() is an associated function, not a struct declaration), so
    only struct field declarations are scanned. This mirrors how scan_class_fields_rust
    works, but also captures the line number for the Edge.

    WHY struct_node is the struct_item node:
      The emission site in graph_rust.py has the struct_item node directly available
      at the point where we index the struct symbol.

    Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        for child in struct_node.children:
            if child.type != "field_declaration_list":
                continue
            for field in child.named_children:
                if field.type != "field_declaration":
                    continue
                try:
                    type_node = field.child_by_field_name("type")
                    if type_node is None:
                        continue
                    type_name = _rust_plain_type(type_node)
                    if not type_name:
                        continue
                    if type_name not in seen:
                        seen.add(type_name)
                        result.append((type_name, field.start_point[0] + 1))
                except Exception:  # noqa: BLE001
                    pass
            break  # Only one field_declaration_list per struct

        # Post-filter builtins (e.g. Rust 'String', 'Box' used bare) via the
        # authoritative is_builtin source — the PascalCase heuristic alone admits them.
        return [(t, ln) for (t, ln) in result if not is_builtin(t, "rust")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_rust: failed: %r", exc)
        return []


def param_types_via_recorder(func_node: Node, recorder, language: str) -> list[tuple[str, int]]:
    """Shared 'uses'-collector: run a record_<lang>_param_types recorder and return its
    bound (plain-user-type, function-line) pairs, deduped and builtin-filtered.

    The receiver-inference recorders already bind only plain user types (refusing
    builtins/optionals/generics/containers/pointers-stripped) — exactly the conservatism
    `uses` needs — so reusing them keeps `uses` and receiver inference byte-aligned. The
    recorder maps param-name → type; we discard names and keep distinct types. The edge
    line is the function's start line (the recorder does not track per-param lines).

    Never raises (recorders never raise; this wraps defensively anyway).
    """
    try:
        tmp: dict[str, str] = {}
        recorder(func_node, tmp)
        line = func_node.start_point[0] + 1
        seen: set[str] = set()
        out: list[tuple[str, int]] = []
        for t in tmp.values():
            if t and t not in seen and not is_builtin(t, language):
                seen.add(t)
                out.append((t, line))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("param_types_via_recorder(%s): failed: %r", language, exc)
        return []


def collect_param_types_go(func_node: Node) -> list[tuple[str, int]]:
    """(param_type, line) pairs for a Go function/method — reuses record_go_param_types."""
    return param_types_via_recorder(func_node, record_go_param_types, "go")


def collect_param_types_rust(func_node: Node) -> list[tuple[str, int]]:
    """(param_type, line) pairs for a Rust function — reuses record_rust_param_types."""
    return param_types_via_recorder(func_node, record_rust_param_types, "rust")

