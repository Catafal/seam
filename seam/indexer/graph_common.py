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
from typing import Literal, TypedDict

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


class Edge(TypedDict):
    source: str  # Symbol name of caller / importer
    target: str  # Symbol name of callee / importee
    kind: str  # 'import' | 'call'
    file: str
    line: int
    confidence: Confidence  # EXTRACTED | INFERRED | AMBIGUOUS


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
) -> Symbol:
    """Construct a Symbol TypedDict from a tree-sitter node."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=node.start_point[0] + 1,  # tree-sitter rows are 0-based
        end_line=node.end_point[0] + 1,
        docstring=docstring,
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


def _find_enclosing_function(node: Node, language: str) -> str | None:
    """Walk up the parent chain to find the nearest enclosing function/method name.

    Returns 'ClassName.methodName' for methods, plain name for functions, or None
    when no enclosing function exists (e.g. top-level module code). None causes the
    caller to drop the edge — only named scopes produce edge sources.

    Language-specific behaviour:
      Python:
        - function_definition nodes; class qualification via class_definition parent.
      TypeScript/JavaScript:
        - Named function_declaration / method_definition ALWAYS wins over arrow functions.
        - The innermost const-assigned arrow_function sets a fallback name only.
        - If neither is found, returns None (edge dropped).
      Go:
        - function_declaration and method_declaration nodes.
        - method_declaration builds a 'Recv.Name' qualified name using _go_recv_type_name.
        - If the method node has no 'name' field (malformed AST), the node is skipped
          rather than falling back to the full source text — which would produce a
          nonsensical multi-line edge source.
      Rust:
        - function_item nodes.
        - function_item inside impl_item builds a 'Type.fn' qualified name using
          _rust_impl_type_name.
    """
    func_types_py = {"function_definition"}
    func_types_ts = {"function_declaration", "method_definition", "arrow_function"}
    func_types_go = {"function_declaration", "method_declaration"}
    func_types_rust = {"function_item"}

    if language == "go":
        func_types = func_types_go
    elif language == "rust":
        func_types = func_types_rust
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

            # Named scope (function_declaration, method_definition, function_definition).
            name_node = current.child_by_field_name("name")
            if name_node is None:
                current = current.parent
                continue
            func_name = _text(name_node)
            class_types = (
                {"class_definition"}
                if language == "python"
                else {"class_declaration", "class_body"}
            )
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
