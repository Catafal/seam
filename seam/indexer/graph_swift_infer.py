"""Swift call-edge type inference — receiver-type resolution helpers.

LAYER: imports from graph_common (leaf) only — never from graph_swift or graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
         ↑
    graph_swift_infer  (this file — pure receiver-type inference)
         ↑
    graph_swift        (imports these helpers for edge extraction)
         ↑
    graph.py

WHY a separate module: graph_swift would exceed the 1000-line limit with the
dependency-injection inference added inline. This file holds the cohesive, leaf-pure
type-inference cluster (no Edge construction, no AST walking) so graph_swift keeps the
orchestration (walk + Edge emit). Follows the Phase 9 per-family split precedent.

CONTRACT: every function is pure and never raises (the recording helpers wrap their
body in a backstop). The Swift module NEVER emits a wrong edge — resolution returns
None on any uncertainty so the caller drops the edge rather than guess.
"""

import logging

from tree_sitter import Node

from seam.analysis.builtins import is_builtin
from seam.indexer.graph_common import _text

logger = logging.getLogger(__name__)


def _swift_instantiated_class(value_node: Node) -> str | None:
    """If value_node is a 'ClassName(...)' instantiation, return 'ClassName', else None.

    A Swift constructor call is a call_expression whose first child is a bare
    simple_identifier (the type name). Used both for `let x = Foo()` binding capture
    and for inline `Foo().method()` resolution. Returns None for any other shape.
    """
    if value_node.type != "call_expression" or not value_node.children:
        return None
    head = value_node.children[0]
    if head.type == "simple_identifier":
        text = _text(head)
        return text or None
    return None


def _plain_user_type_name(holder: Node) -> str | None:
    """Return the type name from a node ONLY if it wraps a plain `user_type`.

    Conservative by design (the Swift module never emits a wrong edge): the type binds
    only for a bare `: TypeName`. Optional (`: Foo?`), array (`: [Foo]`), dictionary, and
    generic (`Array<Foo>`) annotations resolve to a different child node type — or carry a
    type_arguments clause — so they return None rather than mis-bind a receiver to the
    wrong type (e.g. an array's `.append` to its element type).

    `holder` is a `type_annotation`, a `parameter`, or any node carrying a declared
    type child. A `parameter` carries the type as a direct `user_type` child; a
    `type_annotation` does too — but a parameter whose grammar wraps the type in a
    nested `type_annotation` is handled by the recursive case below, so the function
    works for both shapes without the caller needing to know which it is.
    """
    for child in holder.children:
        if child.type == "user_type":
            ids = [gc for gc in child.children if gc.type == "type_identifier"]
            has_generic = any(gc.type == "type_arguments" for gc in child.children)
            if len(ids) == 1 and not has_generic:
                return _text(ids[0])
            return None
        # Defensive: some node shapes wrap the type one level deeper in a
        # type_annotation (e.g. a parameter in a future grammar revision). Recurse
        # so a direct-vs-wrapped grammar difference cannot silently drop the binding.
        if child.type == "type_annotation":
            return _plain_user_type_name(child)
        # A non-plain type wrapper (optional/array/dictionary) — refuse to bind.
        if child.type in ("optional_type", "array_type", "dictionary_type"):
            return None
    return None


def _property_var_name(node: Node) -> str | None:
    """Return the bound variable name from a property_declaration's `pattern` child."""
    for child in node.children:
        if child.type == "pattern":
            for gc in child.children:
                if gc.type == "simple_identifier":
                    return _text(gc)
    return None


def _declared_type_name(decl_node: Node) -> str | None:
    """Return the bound type for a property_declaration, or None.

    Two shapes bind (both conservative — see _plain_user_type_name):
        let x: TypeName   → 'TypeName'   (typed declaration; the DI'd-property case)
        let x = Foo()     → 'Foo'        (inline instantiation; the original P5 case)
    A typed annotation wins over a value when both somehow appear. Anything else
    (optional/array/generic/closure/computed property) → None.
    """
    type_name: str | None = None
    value: Node | None = None
    seen_eq = False
    for child in decl_node.children:
        if child.type == "type_annotation":
            type_name = _plain_user_type_name(child)
        elif child.type == "=":
            seen_eq = True
        elif seen_eq and value is None:
            value = child
    if type_name:
        return type_name
    if value is not None:
        return _swift_instantiated_class(value)
    return None


def _record_var_binding(node: Node, var_types: dict[str, str]) -> None:
    """Record a property_declaration's `name → type` binding into a var_types map.

    Captures BOTH `let x: Type` (typed declaration) and `let x = Type()` (inline
    instantiation) — the typed form is what dependency-injected stored properties use,
    and missing it was the dominant cause of dropped inter-class Swift edges.
    Compound/tuple patterns and non-plain types are ignored. Never raises.
    """
    try:
        var_name = _property_var_name(node)
        if not var_name:
            return
        cls = _declared_type_name(node)
        if cls:
            var_types[var_name] = cls
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_var_binding: failed at %r: %r", node.start_point, exc)


def _scan_class_properties(class_node: Node) -> dict[str, str]:
    """Pre-scan a class/struct/actor/extension body for stored-property type bindings.

    WHY a pre-scan instead of recording during the walk: Swift allows a property to be
    declared AFTER the method that uses it, and every method needs the full property
    type map regardless of source order. Returns name → type for the direct class_body
    property declarations only (not nested types). Never raises.
    """
    out: dict[str, str] = {}
    try:
        for child in class_node.children:
            if child.type == "class_body":
                for gc in child.children:
                    if gc.type == "property_declaration":
                        _record_var_binding(gc, out)
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("_scan_class_properties: failed at %r: %r", class_node.start_point, exc)
    return out


def _record_param_types(func_node: Node, var_types: dict[str, str]) -> None:
    """Bind each parameter's in-body name → declared type into var_types.

    func f(p: P) { p.use() } → binds p→P so `p.use()` resolves to 'P.use'. The in-body
    name is the LAST simple_identifier before the type (handles `_ name:` and
    `label name:` external-name forms). Same conservative plain-user_type rule as
    properties. Never raises.
    """
    try:
        for child in func_node.children:
            if child.type != "parameter":
                continue
            names = [gc for gc in child.children if gc.type == "simple_identifier"]
            ptype = _plain_user_type_name(child)
            if names and ptype:
                # Last identifier = the name actually used inside the body.
                var_types[_text(names[-1])] = ptype
    except Exception as exc:  # noqa: BLE001
        logger.debug("_record_param_types: failed at %r: %r", func_node.start_point, exc)


def _navigation_method_name(nav: Node) -> str | None:
    """Return the trailing method name from a navigation_expression's navigation_suffix."""
    for child in nav.children:
        if child.type == "navigation_suffix":
            for gc in child.children:
                if gc.type == "simple_identifier":
                    return _text(gc)
    return None


# ── Slice #80: Composition (holds) collector for Swift ───────────────────────
#
# collect_composition_types_swift returns deduped (held_type_name, line) pairs
# for a class/struct/actor declaration node. Two passes:
#   1. Stored properties (property_declaration with a plain type_annotation)
#      — including @ObservedObject/@StateObject/@EnvironmentObject wrappers,
#        which only affect the 'modifiers' child and do not change the type_annotation.
#   2. init_declaration parameters with a plain user_type annotation.
#
# CONSERVATISM CONTRACT (same as receiver-type inference):
#   ONLY emit for a bare TypeName (plain user_type with no type_arguments).
#   REFUSE: Foo? (optional_type), [Foo] (array_type), [K:V] (dictionary_type),
#            Foo<T> (user_type with type_arguments), and Swift builtins.
#
# Reuses _plain_user_type_name which already handles all refusals.
# Dedup: a type appearing in BOTH a property and an init param → ONE entry.
# NEVER RAISES: backstop try/except returns [] on any error.


def collect_composition_types_swift(class_node: Node) -> list[tuple[str, int]]:
    """Collect (held_type_name, line) pairs from a Swift class/struct/actor node.

    Two passes over the class_body children:
      1. property_declaration: captures the type from the type_annotation child.
         @ObservedObject, @StateObject, @EnvironmentObject modifiers are transparent —
         the type_annotation is still a direct child regardless of the wrapper attribute.
      2. init_declaration: scans parameter children of the init, capturing each
         parameter's plain user_type via _plain_user_type_name (same helper used for
         receiver-type inference).

    Returns [] on any error. Never raises.
    """
    try:
        seen: set[str] = set()
        result: list[tuple[str, int]] = []

        # Find the class_body (first child of type 'class_body').
        body: Node | None = None
        for child in class_node.children:
            if child.type == "class_body":
                body = child
                break
        if body is None:
            return result

        # Pass 1: stored properties.
        for child in body.children:
            _swift_collect_property_holds(child, seen, result)

        # Pass 2: init declaration parameters.
        for child in body.children:
            _swift_collect_init_holds(child, seen, result)

        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("collect_composition_types_swift: failed: %r", exc)
        return []


def _swift_collect_property_holds(
    node: Node,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Capture a stored-property declaration's held type if it is a plain user type.

    property_declaration structure (relevant path):
        property_declaration
          [modifiers]   ← optional; @ObservedObject/@StateObject/etc. live here
          value_binding_pattern  ← 'var' or 'let' keyword
          pattern         ← the variable name
          type_annotation ← ': TypeName'  ← THIS is what we care about

    The modifiers (wrapper attributes) are transparent: they only affect the
    modifiers child, not the type_annotation. _plain_user_type_name rejects
    optional_type / array_type / dictionary_type / generics automatically.

    Never raises.
    """
    try:
        if node.type != "property_declaration":
            return
        # Find the type_annotation child.
        for child in node.children:
            if child.type == "type_annotation":
                type_name = _plain_user_type_name(child)
                if type_name and not is_builtin(type_name, "swift") and type_name not in seen:
                    seen.add(type_name)
                    result.append((type_name, node.start_point[0] + 1))
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("_swift_collect_property_holds: failed at %r: %r", node.start_point, exc)


def _swift_collect_init_holds(
    node: Node,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Capture an init_declaration's parameter types that are plain user types.

    init_declaration structure (relevant path):
        init_declaration
          'init'
          '('
          parameter  ← one per init param
            [simple_identifier]  ← external label (optional, e.g. 'with' or '_')
            simple_identifier    ← internal name (always present)
            ':'
            [user_type | optional_type | array_type | dictionary_type | ...]
          ')'
          function_body

    _plain_user_type_name applied to the parameter node extracts the type if plain,
    refusing optional/array/dictionary/generic shapes. Dedup: a type already in
    'seen' (added during the property pass) is skipped.

    Never raises.
    """
    try:
        if node.type != "init_declaration":
            return
        for child in node.children:
            if child.type == "parameter":
                _swift_collect_single_param_holds(child, seen, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_swift_collect_init_holds: failed at %r: %r", node.start_point, exc)


def _swift_collect_single_param_holds(
    param: Node,
    seen: set[str],
    result: list[tuple[str, int]],
) -> None:
    """Record one init parameter's plain type into the result if accepted.

    Applies _plain_user_type_name to the parameter node, which handles:
        svc: Service          → 'Service'
        _ svc: Service        → 'Service'  (external '_' label)
        with svc: Service     → 'Service'  (external label)
        svc: Service?         → None       (optional — refused)
        items: [Service]      → None       (array — refused)
        map: [String: Svc]    → None       (dictionary — refused)
        items: Array<Service> → None       (generic — refused)

    Dedup: skip if the type was already seen from a property declaration.
    Never raises.
    """
    try:
        type_name = _plain_user_type_name(param)
        if type_name and not is_builtin(type_name, "swift") and type_name not in seen:
            seen.add(type_name)
            result.append((type_name, param.start_point[0] + 1))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_swift_collect_single_param_holds: failed at %r: %r", param.start_point, exc
        )


def _resolve_navigation_target(
    nav: Node,
    class_name: str | None,
    var_types: dict[str, str],
) -> str | None:
    """Resolve a navigation_expression callee to a qualified 'Type.method' target.

    Handles these receiver shapes:
      self.method              → '<class_name>.method'   (needs enclosing class)
      ClassName().method       → 'ClassName.method'      (inline instantiation)
      x.method (x: Type)       → 'Type.method'           (scope binding: prop/param/local)
      self.prop.method         → '<type of prop>.method' (one-level chain via class prop)
      TypeName.method          → 'TypeName.method'       (B5: static/enum call — PascalCase
                                                           receiver NOT found in var_types)

    The static-call path (B5, #6): when the receiver is a simple_identifier NOT in
    var_types and its first character is uppercase (PascalCase), treat it as a type-qualified
    static or enum call: `Logger.log()` → 'Logger.log'. This is gate-safe because:
      (a) only plain simple_identifier receivers trigger it (no chains, no expressions),
      (b) lowercase-first receivers that are NOT in scope still return None (the caller
          drops the edge rather than guessing), preserving the conservatism contract.
      (c) if the PascalCase name IS in var_types, the existing scope-lookup path fires
          first (scope wins over static heuristic).

    Returns None for any unknown receiver so the caller drops the edge rather than guess.
    """
    receiver = nav.children[0] if nav.children else None
    if receiver is None:
        return None

    # The method name lives in the navigation_suffix → simple_identifier child.
    method = _navigation_method_name(nav)
    if not method:
        return None

    # self.method → enclosing class.
    if receiver.type == "self_expression":
        return f"{class_name}.{method}" if class_name else None

    # x.method — try scope lookup first, then fall through to static-call heuristic.
    if receiver.type == "simple_identifier":
        recv_name = _text(receiver)
        # `Self` (capital S) is Swift's metatype keyword: it names the ENCLOSING type,
        # exactly like lowercase `self`. The tree-sitter grammar parses it as a plain
        # simple_identifier (not self_expression), so without this it would fall into the
        # PascalCase static-call heuristic below and emit a bogus target `Self.method`
        # (a type literally named "Self") — which joins no symbol. Normalize to the class.
        if recv_name == "Self":
            return f"{class_name}.{method}" if class_name else None
        # Scope lookup (class property / parameter / local — set during AST walk).
        cls = var_types.get(recv_name)
        if cls:
            return f"{cls}.{method}"
        # B5 (#6): PascalCase receiver not in scope → static / enum / type call.
        # Only fires for upper-first identifiers — lowercase unknown → refuse (None).
        if recv_name and recv_name[0].isupper():
            return f"{recv_name}.{method}"
        return None

    # self.prop.method → resolve prop's type from the (inherited) scope map. Only the
    # self.<prop> form is resolvable: a property's type is in var_types, whereas a
    # foreign field access (x.prop.method) would need cross-class field typing we
    # deliberately don't track — so it stays unresolved.
    if receiver.type == "navigation_expression":
        inner = receiver.children[0] if receiver.children else None
        prop = _navigation_method_name(receiver)
        if inner is not None and inner.type == "self_expression" and prop:
            cls = var_types.get(prop)
            return f"{cls}.{method}" if cls else None
        return None

    # ClassName().method — inline instantiation as the receiver.
    inline_cls = _swift_instantiated_class(receiver)
    if inline_cls:
        return f"{inline_cls}.{method}"

    return None
