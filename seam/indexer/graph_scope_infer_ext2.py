"""Scope-inference extension module — Java, C#, C++, Ruby, and PHP language families.

LAYER: leaf — imports from graph_common (leaf) and stdlib only. Never imports from
graph.py, graph_scope_infer, graph_scope_infer_ext, or any other seam module with effects.

LAYERING:
    graph_common             (leaf — no seam deps)
         ↑
    graph_scope_infer_ext2   (this file — Java/C#/C++/Ruby/PHP type-binding helpers)
         ↑
    graph_java_csharp.py     (_extract_edges_java / _extract_edges_csharp use these helpers)
    graph_c_cpp.py           (_extract_edges_cpp uses these helpers)
    graph_ruby.py            (_walk_ruby_edges uses these helpers)
    graph_php.py             (_walk_php_edges uses these helpers)

WHY a split from graph_scope_infer_ext.py:
  graph_scope_infer_ext.py would exceed 1000 lines if it contained all 7 language families
  inline. Following the Phase 9 split precedent (graph_java_csharp / graph_c_cpp / etc.),
  the Java/C#/C++/Ruby/PHP families live here while Go/Rust + shared helpers live in ext.

CONSERVATISM CONTRACT: identical to graph_scope_infer_ext.py — all functions never raise.
"""

import logging

from tree_sitter import Node

from seam.analysis.builtins import is_builtin
from seam.indexer.graph_common import _text

logger = logging.getLogger(__name__)

# ── Self/this aliases ─────────────────────────────────────────────────────────

# Java: 'this' — super is excluded (we don't know the superclass statically).
_JAVA_SELF_NAMES: frozenset[str] = frozenset({"this"})

# C#: 'this' — same reasoning as Java.
_CS_SELF_NAMES: frozenset[str] = frozenset({"this"})

# C++: 'this' is always a pointer to the current object.
_CPP_SELF_NAMES: frozenset[str] = frozenset({"this"})

# Ruby: 'self' is the conventional receiver.
_RUBY_SELF_NAMES: frozenset[str] = frozenset({"self"})

# PHP: '$this' is the conventional receiver (with the $ prefix from receiver text).
_PHP_SELF_NAMES: frozenset[str] = frozenset({"$this", "this"})


# ── Java: parameter and local-variable type binding ───────────────────────────


def record_java_param_types(method_node: Node, var_types: dict[str, str]) -> None:
    """Bind Java method parameter names → declared type into var_types.

    Java parameter shape (inside formal_parameters):
      formal_parameter → type + name  (e.g. `Client client`)

    Conservative: only plain type_identifier binds. generic_type (List<T>) → refused.
    Never raises.
    """
    try:
        params = method_node.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.named_children:
            if child.type == "formal_parameter":
                _record_java_single_param(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_java_param_types: failed: %r", exc)


def _record_java_single_param(param_node: Node, var_types: dict[str, str]) -> None:
    """Record a single Java formal_parameter's name → type binding."""
    try:
        type_node = param_node.child_by_field_name("type")
        name_node = param_node.child_by_field_name("name")
        if type_node is None or name_node is None:
            return
        # Only plain type_identifier (no generics, no arrays)
        if type_node.type != "type_identifier":
            return
        type_name = _text(type_node).strip()
        if not type_name or not type_name[0].isupper():
            return
        name = _text(name_node).strip()
        if name and name not in _JAVA_SELF_NAMES:
            var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_java_single_param: failed: %r", exc)


def record_java_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a Java local statement.

    Handles:
      local_variable_declaration: `Client c = new Client()` → c → Client

    Conservative: only plain type_identifier (no generics). Never raises.
    """
    try:
        if stmt_node.type != "local_variable_declaration":
            return
        type_node = stmt_node.child_by_field_name("type")
        if type_node is None or type_node.type != "type_identifier":
            return
        type_name = _text(type_node).strip()
        if not type_name or not type_name[0].isupper():
            return
        for child in stmt_node.named_children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    name = _text(name_node).strip()
                    if name:
                        var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_java_local_types: failed: %r", exc)


def scan_class_fields_java(class_node: Node) -> dict[str, str]:
    """Pre-scan a Java class_declaration body for field-level type bindings.

    Captures:
      Repository repo;                     → repo → Repository
      private Repository repo;             → repo → Repository

    Returns name → type for direct class body field declarations. Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for child in body.named_children:
            if child.type == "field_declaration":
                _record_java_field(child, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_java: failed: %r", exc)
    return out


def _record_java_field(field_node: Node, out: dict[str, str]) -> None:
    """Record a single Java field_declaration's name → type binding."""
    try:
        type_node = field_node.child_by_field_name("type")
        if type_node is None or type_node.type != "type_identifier":
            return
        type_name = _text(type_node).strip()
        if not type_name or not type_name[0].isupper():
            return
        for child in field_node.named_children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    name = _text(name_node).strip()
                    if name:
                        out[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_java_field: failed: %r", exc)


# ── C#: parameter and local-variable type binding ─────────────────────────────


def record_cs_param_types(method_node: Node, var_types: dict[str, str]) -> None:
    """Bind C# method parameter names → declared type into var_types.

    C# parameter shape (inside parameter_list):
      parameter → type + name  (e.g. `Client client`)

    Conservative: only plain identifier type nodes bind. Never raises.
    """
    try:
        params = method_node.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.named_children:
            if child.type == "parameter":
                _record_cs_single_param(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_cs_param_types: failed: %r", exc)


def _record_cs_single_param(param_node: Node, var_types: dict[str, str]) -> None:
    """Record a single C# parameter's name → type binding."""
    try:
        type_node = param_node.child_by_field_name("type")
        name_node = param_node.child_by_field_name("name")
        if type_node is None or name_node is None:
            return
        # Only plain identifier (no nullable T?, no generic List<T>, no array T[])
        if type_node.type != "identifier":
            return
        type_name = _text(type_node).strip()
        if not type_name or not type_name[0].isupper():
            return
        name = _text(name_node).strip()
        if name and name not in _CS_SELF_NAMES:
            var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_cs_single_param: failed: %r", exc)


def record_cs_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a C# local statement.

    Handles:
      local_declaration_statement → variable_declaration → type(identifier) + variable_declarator
      e.g. `Client c = new Client();` → c → Client

    In tree-sitter-c-sharp, local_declaration_statement contains a variable_declaration
    child (not a direct 'type' field). The first identifier child of variable_declaration
    is the type name; subsequent children are variable_declarators.

    Conservative: only plain identifier type annotations bind (no generics, no nullable).
    Never raises.
    """
    try:
        if stmt_node.type != "local_declaration_statement":
            return
        var_decl = None
        for child in stmt_node.children:
            if child.type == "variable_declaration":
                var_decl = child
                break
        if var_decl is None:
            return
        type_name: str | None = None
        for child in var_decl.children:
            if child.type == "identifier":
                type_name = _text(child).strip()
                break
            if child.type not in ("variable_declarator", "=", ","):
                break  # Non-identifier type → refuse
        if not type_name or not type_name[0].isupper():
            return
        for child in var_decl.named_children:
            if child.type == "variable_declarator":
                for dc in child.children:
                    if dc.type == "identifier":
                        name = _text(dc).strip()
                        if name:
                            var_types[name] = type_name
                        break
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_cs_local_types: failed: %r", exc)


def scan_class_fields_cs(class_node: Node) -> dict[str, str]:
    """Pre-scan a C# class_declaration body for field-level type bindings.

    Captures:
      private Client client;               → client → Client
      public Repository repo = new ...();  → repo → Repository

    Returns name → type for direct class body field declarations. Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for child in body.named_children:
            if child.type == "field_declaration":
                _record_cs_field(child, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_cs: failed: %r", exc)
    return out


def _record_cs_field(field_node: Node, out: dict[str, str]) -> None:
    """Record a single C# field_declaration's name → type binding.

    C# field_declaration structure (tree-sitter-c-sharp):
      field_declaration → modifier* + variable_declaration
      variable_declaration → identifier(type) + variable_declarator*(name)
    There is NO direct 'type' field on field_declaration.
    """
    try:
        var_decl = None
        for child in field_node.children:
            if child.type == "variable_declaration":
                var_decl = child
                break
        if var_decl is None:
            return
        type_name: str | None = None
        for child in var_decl.children:
            if child.type == "identifier":
                type_name = _text(child).strip()
                break
        if not type_name or not type_name[0].isupper():
            return
        for child in var_decl.named_children:
            if child.type == "variable_declarator":
                for dc in child.children:
                    if dc.type == "identifier":
                        name = _text(dc).strip()
                        if name:
                            out[name] = type_name
                        break
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_cs_field: failed: %r", exc)


# ── C++: parameter and local-variable type binding ────────────────────────────


def record_cpp_param_types(func_node: Node, var_types: dict[str, str]) -> None:
    """Bind C++ function parameter names → declared type into var_types.

    C++ function_definition has a 'declarator' (function_declarator) with
    'parameters' (parameter_list). Each parameter_declaration has type + name.

    Conservative: only plain type_identifier binds. Pointers/refs (&T, *T) → strip.
    Never raises.
    """
    try:
        declarator = func_node.child_by_field_name("declarator")
        if declarator is None or declarator.type != "function_declarator":
            return
        params = declarator.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.named_children:
            if child.type == "parameter_declaration":
                _record_cpp_single_param(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_cpp_param_types: failed: %r", exc)


def _record_cpp_single_param(param_node: Node, var_types: dict[str, str]) -> None:
    """Record a single C++ parameter_declaration's name → type binding."""
    try:
        type_name = _cpp_extract_type_name(param_node)
        if not type_name or not type_name[0].isupper():
            return
        name = _cpp_extract_param_name(param_node)
        if name and name not in _CPP_SELF_NAMES:
            var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_cpp_single_param: failed: %r", exc)


def _cpp_extract_type_name(param_node: Node) -> str | None:
    """Extract the plain base type name from a C++ parameter_declaration.

    Handles:
      Client client         → type specifier is identifier 'Client'
      Client& client        → reference_declarator; type specifier is 'Client'
      Client* client        → pointer_declarator; type specifier is 'Client'
      const Client& client  → type specifier (const Client); name = 'Client'

    Refuses: templates (generic_type / template_type), qualified names with ::, arrays.
    """
    for child in param_node.children:
        t = child.type
        if t == "type_identifier":
            name = _text(child).strip()
            return name if (name and name[0].isupper()) else None
        if t in ("type_qualifier", "storage_class_specifier"):
            continue  # skip 'const', 'volatile', 'static'
    return None


def _cpp_extract_param_name(param_node: Node) -> str | None:
    """Extract the parameter name from a C++ parameter_declaration."""
    for child in param_node.children:
        t = child.type
        if t == "identifier":
            return _text(child).strip()
        if t in ("reference_declarator", "pointer_declarator"):
            for gc in child.children:
                if gc.type == "identifier":
                    return _text(gc).strip()
    return None


def record_cpp_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a C++ local statement.

    Handles:
      declaration: `Client c;` or `Client c = ...;` or `Client* c = new Client();`

    Conservative: only plain type_identifier (no templates). Never raises.
    """
    try:
        if stmt_node.type != "declaration":
            return
        type_name = _cpp_extract_type_name(stmt_node)
        if not type_name:
            return
        for child in stmt_node.children:
            if child.type in ("identifier", "init_declarator"):
                if child.type == "identifier":
                    bare_name: str = _text(child).strip()
                    if bare_name and bare_name not in _CPP_SELF_NAMES:
                        var_types[bare_name] = type_name
                elif child.type == "init_declarator":
                    decl = child.child_by_field_name("declarator")
                    if decl is None:
                        decl = child.children[0] if child.children else None
                    if decl is not None:
                        decl_name: str | None = _cpp_extract_param_name_from_decl(decl)
                        if decl_name and decl_name not in _CPP_SELF_NAMES:
                            var_types[decl_name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_cpp_local_types: failed: %r", exc)


def _cpp_extract_param_name_from_decl(decl_node: Node) -> str | None:
    """Extract identifier name from a C++ declarator node."""
    if decl_node.type == "identifier":
        return _text(decl_node).strip()
    if decl_node.type in ("reference_declarator", "pointer_declarator"):
        for child in decl_node.children:
            if child.type == "identifier":
                return _text(child).strip()
    return None


def scan_class_fields_cpp(class_node: Node) -> dict[str, str]:
    """Pre-scan a C++ class_specifier or struct_specifier body for field type bindings.

    Captures:
      Client client;   → client → Client (field declaration in class body)

    Returns name → type for direct class body declarations. Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for child in body.named_children:
            if child.type == "field_declaration":
                type_name = _cpp_extract_type_name(child)
                if not type_name:
                    continue
                for decl_child in child.children:
                    if decl_child.type == "identifier":
                        name = _text(decl_child).strip()
                        if name and name not in _CPP_SELF_NAMES:
                            out[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_cpp: failed: %r", exc)
    return out


# ── Ruby: local-variable and ivar type binding ────────────────────────────────


def record_ruby_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a Ruby statement.

    Ruby has no static type annotations. We infer from constructor calls:
      client = Client.new      → client → Client
      @repo   = Repository.new → @repo → Repository   (ivar)

    Conservative: only `.new` calls with a plain constant (PascalCase) receiver bind.
    Never raises.
    """
    try:
        if stmt_node.type == "assignment":
            left = stmt_node.child_by_field_name("left")
            right = stmt_node.child_by_field_name("right")
            if left is None or right is None:
                return
            cls = _ruby_new_call_class(right)
            if cls:
                name = _text(left).strip()
                if name:
                    var_types[name] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_ruby_local_types: failed: %r", exc)


def _ruby_new_call_class(value_node: Node) -> str | None:
    """If value_node is a `ClassName.new` call, return 'ClassName', else None."""
    try:
        if value_node.type != "call":
            return None
        method_node = value_node.child_by_field_name("method")
        if method_node is None or _text(method_node).strip() != "new":
            return None
        receiver_node = value_node.child_by_field_name("receiver")
        if receiver_node is None or receiver_node.type != "constant":
            return None
        name = _text(receiver_node).strip()
        return name if (name and name[0].isupper()) else None
    except Exception:  # noqa: BLE001
        return None


def scan_class_fields_ruby(class_node: Node) -> dict[str, str]:
    """Pre-scan a Ruby class body for ivar type bindings from initialize.

    Looks for `@name = ClassName.new` in the initialize method.
    Returns name → type for @ivar bindings found in initialize. Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for child in body.named_children:
            if child.type == "method":
                method_name_node = child.child_by_field_name("name")
                if method_name_node and _text(method_name_node).strip() == "initialize":
                    _scan_ruby_method_body(child, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_ruby: failed: %r", exc)
    return out


def _scan_ruby_method_body(method_node: Node, out: dict[str, str]) -> None:
    """Walk a Ruby method body for ivar assignment patterns."""
    try:
        body = method_node.child_by_field_name("body")
        if body is None:
            return
        for child in body.named_children:
            if child.type == "assignment":
                record_ruby_local_types(child, out)
    except Exception:  # noqa: BLE001
        pass


# ── PHP: parameter and local-variable type binding ────────────────────────────


def record_php_param_types(method_node: Node, var_types: dict[str, str]) -> None:
    """Bind PHP method parameter names → declared type into var_types.

    PHP parameter shape (inside formal_parameters):
      simple_parameter → type? + name  (e.g. `Client $client`)

    Conservative: only plain named_type (plain class name) binds. Union types,
    nullable (?Client), generics → refused. Never raises.
    """
    try:
        params = method_node.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.named_children:
            if child.type == "simple_parameter":
                _record_php_single_param(child, var_types)
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_php_param_types: failed: %r", exc)


def _record_php_single_param(param_node: Node, var_types: dict[str, str]) -> None:
    """Record a single PHP simple_parameter's name → type binding."""
    try:
        type_node = None
        name_node = None
        for child in param_node.children:
            if child.type == "named_type":
                type_node = child
            elif child.type == "variable_name":
                name_node = child
        if type_node is None or name_node is None:
            return
        type_name = None
        for child in type_node.children:
            if child.type == "name":
                type_name = _text(child).strip()
                break
        if not type_name or not type_name[0].isupper():
            return
        name = _text(name_node).strip()  # includes $ prefix: '$client'
        if name:
            var_types[name] = type_name
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_php_single_param: failed: %r", exc)


def record_php_local_types(stmt_node: Node, var_types: dict[str, str]) -> None:
    """Record type bindings from a PHP local statement.

    Handles:
      expression_statement with assignment: $e = new Engine() → $e → Engine

    Conservative: only `new ClassName` (plain name, no generic) binds. Never raises.
    """
    try:
        if stmt_node.type == "expression_statement" and stmt_node.children:
            inner = stmt_node.children[0]
            if inner.type == "assignment_expression":
                left = inner.child_by_field_name("left")
                right = inner.child_by_field_name("right")
                if left is not None and right is not None:
                    cls = _php_new_class(right)
                    if cls:
                        name = _text(left).strip()  # includes $ prefix
                        if name:
                            var_types[name] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_php_local_types: failed: %r", exc)


def _php_new_class(value_node: Node) -> str | None:
    """If value_node is `new ClassName(...)` or `new ClassName`, return 'ClassName', else None."""
    try:
        if value_node.type != "object_creation_expression":
            return None
        for child in value_node.children:
            if child.type in ("qualified_name", "name"):
                if child.type == "name":
                    name = _text(child).strip()
                    return name if (name and name[0].isupper()) else None
                if child.type == "qualified_name":
                    last = None
                    for qchild in child.children:
                        if qchild.type == "name":
                            last = _text(qchild).strip()
                    return last if (last and last[0].isupper()) else None
    except Exception:  # noqa: BLE001
        pass
    return None


def scan_class_fields_php(class_node: Node) -> dict[str, str]:
    """Pre-scan a PHP class_declaration body for property type bindings.

    Captures:
      private Client $client;       → $client → Client
      public Repository $repo;      → $repo → Repository

    Returns name → type for declared class properties. Never raises.
    """
    out: dict[str, str] = {}
    try:
        body = class_node.child_by_field_name("body")
        if body is None:
            return out
        for child in body.named_children:
            if child.type == "property_declaration":
                _record_php_property(child, out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_class_fields_php: failed: %r", exc)
    return out


def _record_php_property(prop_node: Node, out: dict[str, str]) -> None:
    """Record a single PHP property_declaration's name → type binding."""
    try:
        type_name = None
        prop_name = None
        for child in prop_node.children:
            if child.type == "named_type":
                for tc in child.children:
                    if tc.type == "name":
                        type_name = _text(tc).strip()
                        break
            elif child.type == "property_element":
                for pc in child.children:
                    if pc.type == "variable_name":
                        prop_name = _text(pc).strip()
                        break
        if type_name and prop_name and type_name[0].isupper():
            out[prop_name] = type_name
    except Exception:  # noqa: BLE001
        pass


# ── Slice #79: Composition (holds) collectors for Java, C#, C++, Ruby, PHP ───
# Reuse scan_class_fields_* helpers; add ctor/init param pass where applicable.
# Conservatism: plain user-type identifiers only. Generics/nullable/primitives refused.
# Dedupe: same type from field + ctor → one entry. Never raises; returns [] on error.


def collect_composition_types_java(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a Java class_declaration node.

    Pass 1: field_declaration with plain type_identifier. Pass 2: constructor_declaration
    formal_parameter with plain type_identifier. Deduped by type name.
    Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Pass 1: class field declarations (plain type_identifier only).
        for child in body.named_children:
            if child.type != "field_declaration":
                continue
            try:
                type_node = child.child_by_field_name("type")
                if type_node is None or type_node.type != "type_identifier":
                    continue
                type_name = _text(type_node).strip()
                if not type_name or not type_name[0].isupper():
                    continue
                if type_name not in seen:
                    seen.add(type_name)
                    result.append((type_name, child.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Pass 2: constructor parameter types (constructor_declaration formal_parameters).
        for child in body.named_children:
            if child.type != "constructor_declaration":
                continue
            try:
                params = child.child_by_field_name("parameters")
                if params is None:
                    continue
                for param in params.named_children:
                    if param.type != "formal_parameter":
                        continue
                    type_node = param.child_by_field_name("type")
                    if type_node is None or type_node.type != "type_identifier":
                        continue
                    type_name = _text(type_node).strip()
                    if not type_name or not type_name[0].isupper():
                        continue
                    if type_name not in seen:
                        seen.add(type_name)
                        result.append((type_name, param.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Post-filter builtins (String/Integer/List/Map/etc.) — the PascalCase guard
        # admits them; is_builtin is the authoritative cross-language source.
        return [(t, ln) for (t, ln) in result if not is_builtin(t, "java")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_java: failed: %r", exc)
        return []


def collect_composition_types_cs(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a C# class/struct/record_declaration node.

    Pass 1: field_declaration → variable_declaration first identifier (type name).
    Pass 2: constructor_declaration parameter → plain identifier type (no nullable/generic).
    Deduped by type name. Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Pass 1: field declarations.
        for child in body.named_children:
            if child.type != "field_declaration":
                continue
            try:
                # C# field_declaration → variable_declaration → first identifier = type name.
                var_decl = None
                for fc in child.children:
                    if fc.type == "variable_declaration":
                        var_decl = fc
                        break
                if var_decl is None:
                    continue
                type_name: str | None = None
                for vc in var_decl.children:
                    if vc.type == "identifier":
                        type_name = _text(vc).strip()
                        break
                if not type_name or not type_name[0].isupper():
                    continue
                if type_name not in seen:
                    seen.add(type_name)
                    result.append((type_name, child.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Pass 2: constructor parameter types.
        for child in body.named_children:
            if child.type != "constructor_declaration":
                continue
            try:
                params = child.child_by_field_name("parameters")
                if params is None:
                    continue
                for param in params.named_children:
                    if param.type != "parameter":
                        continue
                    type_node = param.child_by_field_name("type")
                    name_node = param.child_by_field_name("name")
                    if type_node is None or name_node is None:
                        continue
                    # Only plain identifier (no nullable, no generic).
                    if type_node.type != "identifier":
                        continue
                    type_name = _text(type_node).strip()
                    if not type_name or not type_name[0].isupper():
                        continue
                    if type_name not in seen:
                        seen.add(type_name)
                        result.append((type_name, param.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Post-filter builtins (String/Int32/List/etc.) via the authoritative source.
        return [(t, ln) for (t, ln) in result if not is_builtin(t, "csharp")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_cs: failed: %r", exc)
        return []


def collect_composition_types_cpp(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a C++ class_specifier/struct_specifier node.

    Single pass: field_declaration nodes in the body with a plain type_identifier.
    Uses _cpp_extract_type_name (same as scan_class_fields_cpp). No ctor-param pass
    (C++ constructors may be out-of-line). Deduped. Returns [] on error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        for child in body.named_children:
            if child.type != "field_declaration":
                continue
            try:
                # _cpp_extract_type_name searches for type_identifier child.
                type_name = _cpp_extract_type_name(child)
                if not type_name or not type_name[0].isupper():
                    continue
                if type_name not in seen:
                    seen.add(type_name)
                    result.append((type_name, child.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Post-filter builtins (std::string surfaces as 'string'; PascalCase stdlib
        # types) via the authoritative source.
        return [(t, ln) for (t, ln) in result if not is_builtin(t, "cpp")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_cpp: failed: %r", exc)
        return []


def collect_composition_types_ruby(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a Ruby class node.

    Scans initialize method body for @ivar = ClassName.new patterns only.
    Ruby has no static type annotations; .new calls on PascalCase constants are the
    only reliable composition signal. No ctor-param pass (params are untyped).
    Deduped by type name. Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Find the initialize method and scan its body for @ivar = ClassName.new.
        for child in body.named_children:
            if child.type != "method":
                continue
            try:
                name_node = child.child_by_field_name("name")
                if name_node is None or _text(name_node).strip() != "initialize":
                    continue
                # Found initialize — scan its body for ivar assignments.
                method_body = child.child_by_field_name("body")
                if method_body is None:
                    continue
                for stmt in method_body.named_children:
                    if stmt.type != "assignment":
                        continue
                    try:
                        right = stmt.child_by_field_name("right")
                        if right is None:
                            continue
                        type_name = _ruby_new_call_class(right)
                        if type_name and type_name not in seen:
                            seen.add(type_name)
                            result.append((type_name, stmt.start_point[0] + 1))
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

        # Post-filter builtins (Array.new/Hash.new/String.new etc. are PascalCase
        # constants but stdlib, not user composition) via the authoritative source.
        return [(t, ln) for (t, ln) in result if not is_builtin(t, "ruby")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_ruby: failed: %r", exc)
        return []


def collect_composition_types_php(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a PHP class_declaration node.

    Pass 1: property_declaration with named_type → plain name child.
    Pass 2: __construct simple_parameter nodes with named_type type hint.
    PHP 8+ promoted constructor properties appear in pass 2.
    Deduped by type name. Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        body = class_node.child_by_field_name("body")
        if body is None:
            return result

        # Pass 1: typed property declarations.
        for child in body.named_children:
            if child.type != "property_declaration":
                continue
            try:
                type_name: str | None = None
                for pc in child.children:
                    if pc.type == "named_type":
                        for tc in pc.children:
                            if tc.type == "name":
                                type_name = _text(tc).strip()
                                break
                        break
                if not type_name or not type_name[0].isupper():
                    continue
                if type_name not in seen:
                    seen.add(type_name)
                    result.append((type_name, child.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Pass 2: constructor parameter type hints (__construct).
        for child in body.named_children:
            if child.type != "method_declaration":
                continue
            try:
                name_node = child.child_by_field_name("name")
                if name_node is None or _text(name_node).strip() != "__construct":
                    continue
                params = child.child_by_field_name("parameters")
                if params is None:
                    continue
                for param in params.named_children:
                    if param.type != "simple_parameter":
                        continue
                    type_node = None
                    for pc in param.children:
                        if pc.type == "named_type":
                            type_node = pc
                            break
                    if type_node is None:
                        continue
                    type_name = None
                    for tc in type_node.children:
                        if tc.type == "name":
                            type_name = _text(tc).strip()
                            break
                    if not type_name or not type_name[0].isupper():
                        continue
                    if type_name not in seen:
                        seen.add(type_name)
                        result.append((type_name, param.start_point[0] + 1))
            except Exception:  # noqa: BLE001
                pass

        # Post-filter builtins via the authoritative source (PHP type hints can name
        # built-in classes like 'DateTime'/'ArrayObject').
        return [(t, ln) for (t, ln) in result if not is_builtin(t, "php")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_php: failed: %r", exc)
        return []
