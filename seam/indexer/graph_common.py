"""Shared primitive types, constants, and helpers for the Seam indexer graph layer.

LAYER: leaf — imports only stdlib and tree_sitter. This module must never import
from any other seam.indexer module.

LAYERING (import direction):
    graph_common  (this file — leaf, no seam deps)
         ↑           ↑
    graph.py      graph_go_rust.py

Both graph.py and graph_go_rust.py import from here at their top levels.
Neither imports from the other, so no circular dependency exists.

WHY this split was necessary: before this module existed, graph_go_rust.py imported
helpers (Symbol, Edge, _text, etc.) from graph.py at its top level, and graph.py in
turn tried to import the Go/Rust extractors from graph_go_rust.py — a circular init.
The only escape at the time was deferred in-function imports, which violated the
project rule that all imports must be at the top of every file. Moving the shared
primitives here (a leaf with no seam deps) cuts the cycle completely, restores
top-level-only imports in all three files, and is the authoritative home for the
public TypedDicts so callers can do:
    from seam.indexer.graph import Symbol, Edge  # still works — graph re-exports these

Contents:
  - TypedDicts:  Confidence, Symbol, Edge, Comment
  - Constants:   SEMANTIC_MARKERS, _MARKER_RE
  - Helpers:     _text, _node_name, _make_symbol, _match_marker,
                 _block_comment_lines, _arrow_function_name, _find_enclosing_function
  - Go/Rust receiver helpers: _go_recv_type_name, _rust_impl_type_name
    (kept here so _find_enclosing_function can call them without importing
    from graph_go_rust — this is what keeps the leaf property intact)
"""

import logging
import re
from typing import Literal, NotRequired, TypedDict

from tree_sitter import Node

logger = logging.getLogger(__name__)

# ── Semantic comment markers (WHY-extraction feature, Phase 1b) ───────────────

# Fixed set of marker keywords matched case-insensitively at the START of the
# comment body (after stripping the delimiter and leading whitespace).
SEMANTIC_MARKERS: frozenset[str] = frozenset({"WHY", "HACK", "NOTE", "TODO", "FIXME"})

# Pre-compiled regex: group 1=marker keyword, group 3=remainder text.
# Marker must be followed by ':', whitespace, or end-of-string — blocks prefix
# matches like 'whyever'.
_MARKER_RE = re.compile(
    r"^(WHY|HACK|NOTE|TODO|FIXME)(?::|((?=\s)|$))(.*)",
    re.IGNORECASE | re.DOTALL,
)

# Confidence level for an edge — persisted in the DB, exposed via MCP.
Confidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]


class Symbol(TypedDict):
    name: str
    kind: str  # 'function' | 'class' | 'method' | 'interface' | 'type'
    file: str  # str(path) — resolved at call time
    start_line: int
    end_line: int
    docstring: str | None
    # Phase 4 — Node-field enrichment. All nullable: None means "not yet extracted"
    # (e.g. pre-v5 rows) rather than "has no value". Callers must not treat None as
    # equivalent to empty string or False.
    signature: str | None       # declaration header, single line, truncated at SEAM_MAX_SIGNATURE_LEN
    decorators: list[str]       # verbatim decorator text (Python/TS only); [] for Go/Rust
    is_exported: bool | None    # export status; None when language has no uniform export concept
    visibility: str | None      # "public"|"private"|"protected"|"crate"; Python uses heuristic
    qualified_name: str | None  # "ClassName.method" for methods; None for anonymous/top-level unknown


class Edge(TypedDict):
    source: str  # Symbol name of caller / importer
    target: str  # Symbol name of callee / importee
    # Edge kind vocabulary:
    #   'import'       — module/symbol import statement
    #   'call'         — function/method call edge
    #   'extends'      — class inheritance (base class)
    #   'implements'   — interface implementation
    #   'instantiates' — object construction (new Foo(), Foo(), Foo::new(), Foo{}) [Tier B B6]
    #   'holds'        — composition: a class stores a plain user type as a typed field/property
    #                    OR receives one as a typed constructor/init parameter [Slice #77]
    #   'uses'         — a function/method references a plain user type as a PARAMETER in its
    #                    signature (e.g. f(x: T) → f uses T). Complements 'holds' (stored
    #                    composition) with signature-level coupling. Gated by SEAM_PARAM_EDGES.
    kind: str
    file: str
    line: int
    confidence: Confidence  # EXTRACTED | INFERRED | AMBIGUOUS
    # Tier B B1 (v10): raw receiver expression text for attribute calls (e.g. 'self', 'obj').
    # None for import edges, bare-identifier call edges, and pre-v10 rows.
    #
    # WHY NotRequired (not a plain required field): the Edge TypedDict is constructed in
    # many call sites across 12 language extractors. Making receiver Required would force
    # all existing Edge() instantiations to be updated simultaneously. NotRequired lets
    # us add the field incrementally — old callers remain valid, new callers opt in.
    # upsert_file uses edge.get("receiver") which defaults to None, so absent ≡ None
    # at the DB layer. This is the same null-contract as Phase 4/5 enrichment fields.
    #
    # WHY stored even when target is already qualified (e.g. 'Client.method'):
    # Preserves the raw receiver text for debugging, future re-inference passes, and
    # any tooling that needs to re-derive qualification without a full re-index.
    # Edges remain string-name-keyed (source/target are names, not node IDs) as required
    # for independent re-indexing.
    receiver: NotRequired[str | None]
    # v12: synthesis channel that produced this edge. None for parser-extracted edges;
    # a channel name string (e.g. 'interface-override') for edges emitted by the
    # post-pass synthesis engine (seam/analysis/synthesis.py).
    #
    # WHY NotRequired: same rationale as receiver — all 12 extractors construct Edge()
    # dicts without this field, and synthesis adds it only on its own output.
    # upsert_file uses edge.get("synthesized_by") defaulting to None, so absent ≡ NULL.
    # Provenance is DERIVED: synthesized_by IS NOT NULL ⟹ heuristic edge. This avoids
    # a separate boolean column and keeps the schema additive.
    synthesized_by: NotRequired[str | None]


class Comment(TypedDict):
    """A semantic comment extracted from source code during indexing.

    Only WHY/HACK/NOTE/TODO/FIXME-tagged comments are stored — plain
    comments are ignored. Marker is normalized to UPPERCASE.
    """
    marker: str  # Normalized: WHY | HACK | NOTE | TODO | FIXME
    text: str    # Body after the marker (and optional colon), stripped
    line: int    # 1-based line number in the source file


# ── Low-level AST helpers ──────────────────────────────────────────────────────


def _text(node: Node) -> str:
    """Safely decode a tree-sitter node's text bytes to str.

    node.text is typed as bytes | None in the stubs; guard against None.
    """
    raw = node.text
    if raw is None:
        return ""
    return raw.decode("utf-8", errors="replace")


def _node_name(node: Node) -> str | None:
    """Return the text of the 'name' field child, or None if absent."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return _text(name_node)


def _make_symbol(
    name: str,
    kind: str,
    file: str,
    node: Node,
    docstring: str | None,
    signature: str | None = None,
    decorators: list[str] | None = None,
    is_exported: bool | None = None,
    visibility: str | None = None,
    qualified_name: str | None = None,
) -> Symbol:
    """Construct a Symbol TypedDict from a tree-sitter node.

    Phase 4 enrichment fields default to None/[] so callers that don't yet call
    extract_node_fields continue to work without changes — backward-compatible extension
    rather than requiring every call site to be updated simultaneously.
    """
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=node.start_point[0] + 1,  # tree-sitter rows are 0-based
        end_line=node.end_point[0] + 1,
        docstring=docstring,
        signature=signature,
        decorators=decorators if decorators is not None else [],
        is_exported=is_exported,
        visibility=visibility,
        qualified_name=qualified_name,
    )


def _match_marker(body: str) -> tuple[str, str] | None:
    """Try to match a semantic marker at the start of a stripped comment body.

    Returns (marker_upper, text) if matched, or None.
    The regex requires the marker to be followed by ':', whitespace, or end-of-string.
    """
    m = _MARKER_RE.match(body)
    if not m:
        return None
    marker = m.group(1).upper()
    text = (m.group(3) or "").strip()
    return marker, text


def _block_comment_lines(raw: str) -> list[tuple[int, str]]:
    """Return (line_offset, cleaned_body) for each non-empty line of a /* */ block.

    line_offset is the 0-based line index from the block's first line.
    Each line has /*, */ and leading '*' decorations stripped. Empty lines omitted.
    """
    out: list[tuple[int, str]] = []
    for i, line in enumerate(raw.splitlines()):
        s = line.strip()
        if s.startswith("/*"):
            s = s[2:]
        if s.endswith("*/"):
            s = s[:-2]
        s = s.strip().lstrip("*").strip()
        if s:
            out.append((i, s))
    return out


# ── Go/Rust receiver helpers (leaf — no circular dep) ─────────────────────────


def _go_recv_type_name(method_node: Node) -> str | None:
    """Extract the receiver type name from a Go method_declaration.

    Go method receivers come in four forms; all are normalized to a plain type name:
      - value receiver:          func (r Foo) M()       → 'Foo'       (type_identifier)
      - pointer receiver:        func (r *Foo) M()      → 'Foo'       (pointer_type → type_identifier)
      - generic value receiver:  func (r Foo[T]) M()    → 'Foo'       (generic_type → type_identifier)
      - generic pointer recv:    func (r *Repo[T]) M()  → 'Repo'      (pointer_type → generic_type → type_identifier)

    Normalization rule: always extract the base type_identifier, discarding `*` and `[T]`.
    Returns None if the receiver list is absent or the type node has an unexpected shape.
    """
    recv = method_node.child_by_field_name("receiver")
    if recv is None:
        return None
    for pd in recv.named_children:
        if pd.type == "parameter_declaration":
            typ = pd.child_by_field_name("type")
            if typ is None:
                continue
            if typ.type == "pointer_type":
                # *T or *T[K]: inspect the single named child of pointer_type.
                inner = next(iter(typ.named_children), None)
                if inner is None:
                    continue
                if inner.type == "type_identifier":
                    # Simple pointer receiver: *Foo → 'Foo'
                    return _text(inner)
                if inner.type == "generic_type":
                    # Generic pointer receiver: *Repo[T] → extract 'Repo' from generic_type
                    base = next(
                        (c for c in inner.named_children if c.type == "type_identifier"),
                        None,
                    )
                    if base is not None:
                        return _text(base)
            elif typ.type == "type_identifier":
                # Plain value receiver: Foo → 'Foo'
                return _text(typ)
            elif typ.type == "generic_type":
                # Generic value receiver: Repo[T] → extract 'Repo' from generic_type
                base = next(
                    (c for c in typ.named_children if c.type == "type_identifier"),
                    None,
                )
                if base is not None:
                    return _text(base)
    return None


def _rust_impl_type_name(impl_node: Node) -> str | None:
    """Extract the base type name from a Rust impl_item node.

    Handles:
      - Plain impl:   impl Foo { ... }           → 'Foo'  (type field is type_identifier)
      - Generic impl: impl<T> Foo<T> { ... }     → 'Foo'  (type field is generic_type;
                                                            extract the first type_identifier child)

    Returns the base type_identifier string (e.g. 'Foo', not 'Foo<T>'), or None if
    the type field is absent or has an unexpected shape.
    """
    type_node = impl_node.child_by_field_name("type")
    if type_node is None:
        return None
    if type_node.type == "type_identifier":
        return _text(type_node)
    if type_node.type == "generic_type":
        base = next(
            (c for c in type_node.named_children if c.type == "type_identifier"),
            None,
        )
        if base is not None:
            return _text(base)
    return None


# ── Inheritance base-name normalizer (P6a) ───────────────────────────────────


def _base_type_name(node: Node) -> str | None:
    """Normalize a base-class / interface type node to its bare type NAME.

    The edge graph is name-keyed and homonym-collapsed, so generic arguments and
    namespace/package qualifiers are stripped to the rightmost simple type name —
    matching how call/import targets are stored. Handles the shapes that appear in
    base-class / interface clauses across Python, TypeScript, Java, and C#:

      - identifier / type_identifier          → the text directly  (Base)
      - generic_type   (Java/TS: Base<T>)     → first type_identifier child  (Base)
      - generic_name   (C#:  IFace<T>)        → first identifier child       (IFace)
      - qualified_name (C#:  Ns.Base)         → 'name' field, else last identifier child
      - scoped_type_identifier (Java: a.B)    → 'name' field, else last type_identifier
      - member_expression (TS: ns.Base)       → 'property' field, else last child

    Returns the bare name string, or None when the node shape is unrecognized
    (the caller then skips emitting an edge — never raises).
    """
    t = node.type
    if t in ("identifier", "type_identifier"):
        return _text(node)
    if t in ("generic_type", "generic_name"):
        for c in node.named_children:
            if c.type in ("type_identifier", "identifier"):
                return _text(c)
        return None
    if t in ("qualified_name", "scoped_type_identifier"):
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _text(name_node)
        # Fallback: rightmost identifier-like child.
        for c in reversed(node.named_children):
            if c.type in ("identifier", "type_identifier"):
                return _text(c)
        return None
    if t == "member_expression":
        prop = node.child_by_field_name("property")
        if prop is not None:
            return _text(prop)
    return None


# ── Arrow-function name resolver (TypeScript/JS only) ─────────────────────────


def _arrow_function_name(arrow_node: Node) -> str | None:
    """Resolve the name of an arrow function from its assignment context.

    Returns the variable name if `const X = () => {...}`, else None.
    """
    parent = arrow_node.parent
    if parent is None:
        return None
    if parent.type == "variable_declarator":
        name_node = parent.child_by_field_name("name")
        if name_node is not None and name_node.type == "identifier":
            return _text(name_node)
    return None


# ── Enclosing-scope resolver ───────────────────────────────────────────────────


def _c_cpp_function_name_from_def(func_def: Node) -> str | None:
    """Extract the function name from a C or C++ function_definition node.

    C/C++ function_definition has NO 'name' field directly — the name is nested:
        function_definition → declarator (function_declarator) → declarator (identifier
        | field_identifier | qualified_identifier).

    Returns:
      - Plain name (str) for free functions and in-class method declarations.
      - 'Class.method' qualified name for out-of-line definitions (Class::method).
      - None if the name cannot be resolved.

    WHY this helper is in graph_common (leaf): _find_enclosing_function needs it to
    resolve enclosing scope names for C/C++ call edges. Putting it in graph_c_cpp
    would create a cycle (graph_c_cpp → graph_common → graph_c_cpp). Keeping it here
    (alongside the other language helpers like _go_recv_type_name) preserves the leaf.
    """
    try:
        declarator = func_def.child_by_field_name("declarator")
        if declarator is None or declarator.type != "function_declarator":
            return None
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            return None
        if inner.type in ("identifier", "field_identifier"):
            return _text(inner)
        if inner.type == "qualified_identifier":
            # Out-of-line C++ method: Class::method → 'Class.method'
            scope = inner.child_by_field_name("scope")
            name_node = inner.child_by_field_name("name")
            if scope and name_node:
                return f"{_text(scope)}.{_text(name_node)}"
    except Exception:  # noqa: BLE001
        pass
    return None


def _find_enclosing_function(node: Node, language: str) -> str | None:
    """Walk up the parent chain to find the nearest enclosing function/method name.

    Returns 'ClassName.methodName' for methods, plain name for functions, or None
    when no enclosing function exists (e.g. top-level module code). None causes the
    caller to drop the edge — only named scopes produce edge sources.

    Supports all 12 Seam languages: python, typescript, javascript, go, rust,
    java, csharp, ruby, c, cpp, php, swift. Per-language function/method node types
    and class-context types are defined in the body below.

    WHY in this leaf module: both graph.py and the family modules (graph_go_rust,
    graph_java_csharp, graph_c_cpp, graph_ruby_php) call this for call-edge source
    resolution. Keeping it here avoids circular imports between family modules.
    """
    func_types_py = {"function_definition"}
    func_types_ts = {"function_declaration", "method_definition", "arrow_function"}
    func_types_go = {"function_declaration", "method_declaration"}
    func_types_rust = {"function_item"}
    # Phase 9 function/method node types per language
    func_types_java = {"method_declaration", "constructor_declaration"}
    func_types_csharp = {
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
    }
    func_types_ruby = {"method", "singleton_method"}
    func_types_c = {"function_definition"}
    func_types_cpp = {"function_definition"}
    func_types_php = {"method_declaration", "function_definition"}
    # Phase 10 Swift: function_declaration is the sole function/method node type.
    func_types_swift = {"function_declaration", "protocol_function_declaration"}

    # Phase 9 class-context node types for qualified 'Class.method' names
    class_types_java = {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    }
    class_types_csharp = {
        "class_declaration",
        "struct_declaration",
        "record_declaration",
        "interface_declaration",
    }
    class_types_ruby = {"class", "module"}
    class_types_cpp = {"class_specifier", "struct_specifier"}
    class_types_php = {
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
    }
    # Phase 10 Swift: class_declaration covers class/struct/actor/extension;
    # protocol_declaration covers protocol methods.
    class_types_swift = {"class_declaration", "protocol_declaration"}

    if language == "go":
        func_types = func_types_go
    elif language == "rust":
        func_types = func_types_rust
    elif language == "java":
        func_types = func_types_java
    elif language == "csharp":
        func_types = func_types_csharp
    elif language == "ruby":
        func_types = func_types_ruby
    elif language == "c":
        func_types = func_types_c
    elif language == "cpp":
        func_types = func_types_cpp
    elif language == "php":
        func_types = func_types_php
    elif language == "swift":
        func_types = func_types_swift
    else:
        func_types = func_types_py if language == "python" else func_types_ts

    # Fallback: name of the innermost const-assigned arrow found while walking up.
    fallback_arrow_name: str | None = None

    current = node.parent
    while current is not None:
        if current.type in func_types:
            if current.type == "arrow_function":
                if fallback_arrow_name is None:
                    fallback_arrow_name = _arrow_function_name(current)
                current = current.parent
                continue

            # Go method_declaration: build qualified 'Recv.Name' using shared helper.
            if language == "go" and current.type == "method_declaration":
                # FIX 3: if the method has no name, skip — do NOT fall back to full node text.
                func_name_node = current.child_by_field_name("name")
                if func_name_node is None:
                    current = current.parent
                    continue
                func_name = _text(func_name_node)
                recv_name = _go_recv_type_name(current)
                if recv_name:
                    return f"{recv_name}.{func_name}"
                return func_name

            # Rust function_item inside impl_item: build 'Type.fn' qualified name.
            if language == "rust" and current.type == "function_item":
                func_name_node = current.child_by_field_name("name")
                if func_name_node is None:
                    current = current.parent
                    continue
                func_name = _text(func_name_node)
                parent = current.parent
                if parent is not None and parent.type == "declaration_list":
                    grandparent = parent.parent
                    if grandparent is not None and grandparent.type == "impl_item":
                        type_name = _rust_impl_type_name(grandparent)
                        if type_name is not None:
                            return f"{type_name}.{func_name}"
                return func_name

            # C/C++ function_definition: name lives in declarator chain, not a 'name' field.
            # function_definition → function_declarator ('declarator' field) →
            #   identifier or field_identifier or qualified_identifier ('declarator' field).
            if language in ("c", "cpp") and current.type == "function_definition":
                c_func_name = _c_cpp_function_name_from_def(current)
                if c_func_name is None:
                    current = current.parent
                    continue
                # C++ out-of-line method (e.g. 'Circle.area') is already qualified
                if "." in c_func_name:
                    return c_func_name
                # C++ in-class: check for enclosing class_specifier/struct_specifier
                if language == "cpp":
                    parent = current.parent
                    while parent is not None:
                        if parent.type in class_types_cpp:
                            cls_name_node = parent.child_by_field_name("name")
                            if cls_name_node is not None:
                                return f"{_text(cls_name_node)}.{c_func_name}"
                        parent = parent.parent
                return c_func_name

            # Swift function_declaration has no 'name' field — name is a simple_identifier child.
            # Also handle protocol_function_declaration the same way.
            if language == "swift" and current.type in (
                "function_declaration",
                "protocol_function_declaration",
            ):
                swift_func_name: str | None = None
                for sc in current.children:
                    if sc.type == "simple_identifier":
                        swift_func_name = _text(sc)
                        break
                if swift_func_name is None:
                    current = current.parent
                    continue
                func_name = swift_func_name
                # Walk up for enclosing class_declaration or protocol_declaration
                parent = current.parent
                while parent is not None:
                    if parent.type in class_types_swift:
                        # Swift class_declaration has type_identifier child (not 'name' field)
                        # extension also uses type_identifier inside user_type
                        cls_name: str | None = None
                        kw_seen = False
                        for pc in parent.children:
                            if pc.type in ("class", "struct", "actor", "extension", "enum"):
                                kw_seen = True
                                continue
                            if kw_seen and pc.type == "type_identifier":
                                cls_name = _text(pc)
                                break
                            if kw_seen and pc.type == "user_type":
                                # extension node: name in user_type → type_identifier
                                for uc in pc.children:
                                    if uc.type == "type_identifier":
                                        cls_name = _text(uc)
                                        break
                                if cls_name:
                                    break
                            if kw_seen and pc.type in ("class_body", "protocol_body"):
                                break
                        if cls_name:
                            return f"{cls_name}.{func_name}"
                        # Protocol declaration: name is type_identifier directly
                        if parent.type == "protocol_declaration":
                            for pc in parent.children:
                                if pc.type == "type_identifier":
                                    cls_name = _text(pc)
                                    break
                            if cls_name:
                                return f"{cls_name}.{func_name}"
                    parent = parent.parent
                return func_name

            # Named scope (function_declaration, method_definition, function_definition).
            name_node = current.child_by_field_name("name")
            if name_node is None:
                current = current.parent
                continue
            func_name = _text(name_node)

            # Determine the set of class-context types for this language.
            if language == "python":
                class_types: set[str] = {"class_definition"}
            elif language == "java":
                class_types = class_types_java
            elif language == "csharp":
                class_types = class_types_csharp
            elif language == "ruby":
                class_types = class_types_ruby
            elif language == "cpp":
                class_types = class_types_cpp
            elif language == "php":
                class_types = class_types_php
            elif language == "swift":
                class_types = class_types_swift
            elif language == "c":
                # C has no classes — never qualify
                class_types = set()
            else:
                # TypeScript/JavaScript
                class_types = {"class_declaration", "class_body"}

            parent = current.parent
            while parent is not None:
                if parent.type in class_types:
                    class_name_node = parent.child_by_field_name("name")
                    if class_name_node is not None:
                        cls_name = _text(class_name_node)
                        return f"{cls_name}.{func_name}"
                parent = parent.parent
            return func_name
        current = current.parent

    # No named function/method found; return arrow fallback or None.
    if fallback_arrow_name is None:
        logger.debug(
            "_find_enclosing_function: no named scope found — edge source "
            "cannot be resolved; edge will be dropped"
        )
    return fallback_arrow_name
