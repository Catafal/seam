"""Symbol and edge extraction from tree-sitter AST nodes.

Pure functions: take AST node + metadata, return structured data.
No I/O, no DB, no side effects.

LAYER: sits between graph_common/graph_go_rust (below) and pipeline.py/db.py (above).
  - Imports shared types and helpers from graph_common (leaf — no seam deps).
  - Imports Go/Rust extractors from graph_go_rust (which also imports graph_common only).
  - Re-exports all public TypedDicts so callers can continue using:
        from seam.indexer.graph import Symbol, Edge, Comment, Confidence

Contract (evolved from Phase-0 FROZEN — see docs/CONTRACT.md):
  Symbol fields: name, kind, file, start_line, end_line, docstring
  Edge fields:   source, target, kind, file, line, confidence (Phase 1 addition)

Confidence — two-layer model:
  Layer 1 — stored column (same-file scope, index time):
    Computed by _resolve_confidence_multi against the symbol list from the SAME FILE.
    EXTRACTED  — target resolves to exactly one symbol in the same-file set.
    AMBIGUOUS  — target matches more than one symbol in the same-file set.
    INFERRED   — target not in the same-file set (heuristic / external).
    This is a cheap debugging hint only — NOT authoritative for cross-file edges.

  Layer 2 — read-time whole-index resolution (authoritative, see seam/analysis/confidence.py):
    At query time, confidence is re-resolved against the full symbol index.
    EXTRACTED  — target name is unique across the ENTIRE index.
    AMBIGUOUS  — target name is shared by more than one indexed symbol.
    INFERRED   — target name is not in the index at all (external, stdlib, dynamic).
    This overrides the stored column value; no schema change is needed.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config

# All seam imports in one block (alphabetically ordered, as required by ruff/isort).
# Layer structure:
#   graph_common      (leaf — no seam deps)
#   graph_scope_infer (leaf — imports graph_common only; Tier B B4 receiver-type inference)
#   graph_c_cpp / graph_go_rust / graph_java_csharp / graph_php / graph_ruby / graph_swift
#                     (family extractors — import graph_common only; no cycle)
#   signatures        (leaf — imports graph_common only)
#   graph.py          (this file — orchestrator; imports all of the above)
from seam.indexer.graph_c_cpp import (
    _extract_comments_c,
    _extract_comments_cpp,
    _extract_edges_c,
    _extract_edges_cpp,
    _extract_symbols_c,
    _extract_symbols_cpp,
)
from seam.indexer.graph_common import (
    SEMANTIC_MARKERS,
    Comment,
    Confidence,
    Edge,
    Symbol,
    _base_type_name,
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _node_name,
    _text,
)
from seam.indexer.graph_go_rust import (
    _extract_comments_go,
    _extract_comments_rust,
    _extract_edges_go,
    _extract_edges_rust,
    _extract_symbols_go,
    _extract_symbols_rust,
)
from seam.indexer.graph_java_csharp import (
    _extract_comments_csharp,
    _extract_comments_java,
    _extract_edges_csharp,
    _extract_edges_java,
    _extract_symbols_csharp,
    _extract_symbols_java,
)
from seam.indexer.graph_php import (
    _extract_comments_php,
    _extract_edges_php,
    _extract_symbols_php,
)
from seam.indexer.graph_ruby import (
    _extract_comments_ruby,
    _extract_edges_ruby,
    _extract_symbols_ruby,
)

# Tier B B4 — scope-inference leaf (Python + TS/JS receiver-type resolution).
# graph_scope_infer imports only graph_common (leaf) — no circular dependency.
from seam.indexer.graph_scope_infer import (
    _PY_SELF_NAMES,
    _TS_SELF_NAMES,
    record_py_local_types,
    record_py_param_types,
    record_ts_local_types,
    record_ts_param_types,
    resolve_receiver_type,
    scan_class_fields_python,
    scan_class_fields_typescript,
)
from seam.indexer.graph_swift import (
    _extract_comments_swift,
    _extract_edges_swift,
    _extract_symbols_swift,
)

# signatures.py is a leaf (no seam deps) so importing it here does not create a cycle.
from seam.indexer.signatures import extract_node_fields

# Keep these names visible for `from seam.indexer.graph import ...` callers.
__all__ = [
    "Comment",
    "Confidence",
    "Edge",
    "Symbol",
    "SEMANTIC_MARKERS",
    "extract_comments",
    "extract_edges",
    "extract_symbols",
]

logger = logging.getLogger(__name__)


# ── Internal confidence helper ─────────────────────────────────────────────────


def _resolve_confidence_multi(target_name: str, symbol_name_counts: dict[str, int]) -> Confidence:
    """Resolve confidence using a same-file name->count mapping.

    SCOPE: same-file only — this is a lower-bound hint stored on the edge.
    The authoritative whole-index resolution lives in seam/analysis/confidence.py.

    Args:
        target_name:        The edge target name to resolve.
        symbol_name_counts: Mapping of symbol_name -> occurrence count in THIS file only.
    """
    count = symbol_name_counts.get(target_name, 0)
    if count == 1:
        return "EXTRACTED"
    if count > 1:
        return "AMBIGUOUS"
    return "INFERRED"


# ── Python docstring extractor ─────────────────────────────────────────────────


def _py_docstring(func_or_class_node: Node) -> str | None:
    """Extract Python docstring: first expression_statement(string) in body.

    Returns the docstring CONTENT (without surrounding quotes), or None.
    Uses tree-sitter `string_content` child node — avoids stripping legitimate
    leading/trailing quote characters from the docstring text.
    """
    body = func_or_class_node.child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    # Must be an expression_statement whose first child is a string literal.
    if first.type != "expression_statement" or not first.children:
        return None
    expr = first.children[0]
    if expr.type != "string":
        return None
    # tree-sitter Python string nodes: [string_start, string_content, string_end].
    for child in expr.children:
        if child.type == "string_content":
            return _text(child).strip()
    return None  # empty string literal has no string_content


# ── TypeScript/JS JSDoc extractor ──────────────────────────────────────────────


def _ts_jsdoc(symbol_node: Node) -> str | None:
    """Extract leading JSDoc comment from the previous sibling node.

    tree-sitter emits /** ... */ blocks as 'comment' nodes immediately
    before the declaration. Only /** blocks qualify as JSDoc.
    """
    prev = symbol_node.prev_sibling
    if prev is None or prev.type != "comment":
        return None
    comment_text = _text(prev)
    if not comment_text.startswith("/**"):
        return None
    return comment_text


# ── Python extraction ──────────────────────────────────────────────────────────


def _extract_symbols_python(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Python AST and extract function, class, and method symbols."""
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, class_name: str | None = None) -> None:
        """Recursively walk AST, tracking class context for method qualification."""
        if node.type == "function_definition":
            name = _node_name(node)
            if name:
                kind = "method" if class_name else "function"
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _py_docstring(node)
                # Phase 4: pass qualified name from our scope-walker so signatures.py doesn't
                # need to re-resolve it independently — single source of qualified-name truth.
                fields = extract_node_fields(
                    node,
                    "python",
                    qualified_name=qualified,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        qualified,
                        kind,
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=qualified,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, None)  # nested def is local, not a method

        elif node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition and definition.type == "function_definition":
                name = _node_name(definition)
                if name:
                    kind = "method" if class_name else "function"
                    qualified = f"{class_name}.{name}" if class_name else name
                    doc = _py_docstring(definition)
                    # Pass the decorated_definition node (not the inner definition) so
                    # signatures.py can traverse its children for @decorator nodes.
                    fields = extract_node_fields(
                        node,
                        "python",
                        qualified_name=qualified,
                        max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                    )
                    symbols.append(
                        _make_symbol(
                            qualified,
                            kind,
                            file_str,
                            node,
                            doc,
                            signature=fields["signature"],
                            decorators=fields["decorators"],
                            is_exported=fields["is_exported"],
                            visibility=fields["visibility"],
                            qualified_name=qualified,
                        )
                    )
                    body = definition.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            _walk(child, None)
            elif definition and definition.type == "class_definition":
                name = _node_name(definition)
                if name:
                    doc = _py_docstring(definition)
                    # Pass decorated_definition node so class decorators are captured.
                    fields = extract_node_fields(
                        node,
                        "python",
                        qualified_name=name,
                        max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                    )
                    symbols.append(
                        _make_symbol(
                            name,
                            "class",
                            file_str,
                            node,
                            doc,
                            signature=fields["signature"],
                            decorators=fields["decorators"],
                            is_exported=fields["is_exported"],
                            visibility=fields["visibility"],
                            qualified_name=name,
                        )
                    )
                    body = definition.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            _walk(child, name)

        elif node.type == "class_definition":
            name = _node_name(node)
            if name:
                doc = _py_docstring(node)
                fields = extract_node_fields(
                    node,
                    "python",
                    qualified_name=name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        name,
                        "class",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=name,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, name)
        else:
            for child in node.children:
                _walk(child, class_name)

    for child in root.children:
        _walk(child, None)

    return symbols


def _extract_edges_python(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Python AST.

    Import heuristic:
      - import X     → target = 'X' (dotted_name as-is)
      - from X import Y → target = 'Y' for each name after 'import' keyword

    Call heuristic (Tier B B4 enhanced):
      - call node where function is a bare identifier (`foo()`) → target = identifier
      - call node where function is an attribute (`mod.fn()`, `self.m()`, `a.b.c()`)
        → when SEAM_TYPE_INFERENCE=on and receiver type is known in scope, target =
          'Type.method' (qualified); otherwise target = the rightmost identifier (bare).
      - Scope is two-layer: class field pre-scan (order-independent) + per-function
        param/local types accumulated during the walk.
      - source = nearest enclosing function/method (skip if none)
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"
    type_inference_on = config.SEAM_TYPE_INFERENCE == "on"

    def _emit_import_edges(node: Node) -> None:
        """Emit import edges for import_statement and import_from_statement nodes."""
        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    target_node = child.child_by_field_name("name") or child
                    edges.append(Edge(
                        source=file_stem,
                        target=_text(target_node),
                        kind="import",
                        file=file_str,
                        line=node.start_point[0] + 1,
                        confidence="INFERRED",
                        receiver=None,
                    ))
        elif node.type == "import_from_statement":
            found_import_kw = False
            for child in node.children:
                if child.type == "import":
                    found_import_kw = True
                    continue
                if found_import_kw:
                    if child.type in ("dotted_name", "identifier"):
                        edges.append(Edge(
                            source=file_stem,
                            target=_text(child),
                            kind="import",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=None,
                        ))
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            edges.append(Edge(
                                source=file_stem,
                                target=_text(name_node),
                                kind="import",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                                receiver=None,
                            ))

    def _emit_call_edge(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
    ) -> None:
        """Emit a call edge for a Python 'call' node using scope inference.

        With type_inference_on=True: when the receiver's type is in scope,
        target = 'Type.method'; else target = bare method name.
        Receiver field always carries the raw receiver text regardless.
        """
        func_child = node.child_by_field_name("function")
        callee_node: Node | None = None
        receiver_text: str | None = None

        if func_child and func_child.type == "identifier":
            callee_node = func_child
        elif func_child and func_child.type == "attribute":
            callee_node = func_child.child_by_field_name("attribute")
            object_node = func_child.child_by_field_name("object")
            if object_node is not None:
                receiver_text = _text(object_node)

        if callee_node is None or callee_node.type != "identifier":
            return
        source = _find_enclosing_function(node, "python")
        if source is None:
            return

        method_name = _text(callee_node)
        target = method_name

        # Tier B B6: PascalCase bare call (no receiver) → instantiates edge.
        # A bare call like Foo() where the callee starts with an uppercase letter
        # is a constructor call in Python. receiver_text=None means bare call
        # (not obj.Foo()), so we emit kind='instantiates' instead of 'call'.
        # Attribute calls (self.Foo(), obj.Foo()) are still call edges — only
        # bare-identifier PascalCase calls trigger this.
        if receiver_text is None and method_name and method_name[0].isupper():
            edges.append(Edge(
                source=source,
                target=method_name,
                kind="instantiates",
                file=file_str,
                line=node.start_point[0] + 1,
                confidence="INFERRED",
                receiver=None,
            ))
            return

        # Tier B B4: attempt receiver-type resolution when inference is enabled
        # and we have a receiver expression (attribute call, not bare call).
        if type_inference_on and receiver_text is not None:
            resolved_type = resolve_receiver_type(
                receiver_text, class_name, var_types, _PY_SELF_NAMES
            )
            if resolved_type is not None:
                target = f"{resolved_type}.{method_name}"

        edges.append(Edge(
            source=source,
            target=target,
            kind="call",
            file=file_str,
            line=node.start_point[0] + 1,
            confidence="INFERRED",
            receiver=receiver_text,
        ))

    def _walk_function_body(func_node: Node, class_name: str | None, class_fields: dict[str, str]) -> None:
        """Walk a function body with accumulated per-function scope.

        Builds a fresh var_types dict (class fields + params + locals) for each
        function, then walks the body emitting call edges with full scope context.
        """
        # Start with a copy of class fields as base scope; params/locals extend it.
        var_types: dict[str, str] = dict(class_fields)
        record_py_param_types(func_node, var_types)

        body = func_node.child_by_field_name("body")
        if body is None:
            return
        for stmt in body.children:
            # Record local bindings BEFORE emitting edges so variables defined
            # earlier in the function are in scope for later calls.
            record_py_local_types(stmt, var_types)
            _walk_stmt(stmt, class_name, var_types, class_fields)

    def _walk_stmt(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
        class_fields: dict[str, str],
    ) -> None:
        """Recursively walk a statement, emitting edges with current scope."""
        if node.type in ("import_statement", "import_from_statement"):
            _emit_import_edges(node)
            return

        if node.type == "call":
            _emit_call_edge(node, class_name, var_types)
            # Fall through to recurse — a call can contain nested calls.

        # Nested function/class definitions: enter new scope.
        if node.type in ("function_definition", "decorated_definition"):
            # A nested function inside another function — recurse with empty class scope.
            inner_fn = node
            if node.type == "decorated_definition":
                inner_fn = node.child_by_field_name("definition") or node
            if inner_fn.type == "function_definition":
                _walk_function_body(inner_fn, class_name, class_fields)
                return  # Already recursed into body; don't double-recurse below.

        if node.type == "class_definition":
            _walk_class(node)
            return  # Nested class is handled recursively.

        # Recurse into all children (expressions, comprehensions, lambdas, etc.)
        for child in node.children:
            _walk_stmt(child, class_name, var_types, class_fields)

    def _walk_class(class_node: Node) -> None:
        """Walk a class definition: emit inheritance edges + recurse methods."""
        cls_name = _node_name(class_node)

        if emit_inheritance and cls_name:
            bases = class_node.child_by_field_name("superclasses")
            if bases is not None:
                for base_child in bases.named_children:
                    base_target = _base_type_name(base_child)
                    if base_target:
                        edges.append(Edge(
                            source=cls_name,
                            target=base_target,
                            kind="extends",
                            file=file_str,
                            line=class_node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=None,
                        ))

        # Pre-scan class fields for the two-layer scope model.
        class_fields: dict[str, str] = {}
        if type_inference_on and cls_name:
            class_fields = scan_class_fields_python(class_node)

        body = class_node.child_by_field_name("body")
        if body is None:
            return
        for child in body.children:
            # Each method/function in the class body gets its own per-function scope
            # that starts from the class-level field map.
            if child.type == "function_definition":
                _walk_function_body(child, cls_name, class_fields)
            elif child.type == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner and inner.type == "function_definition":
                    _walk_function_body(inner, cls_name, class_fields)
                elif inner and inner.type == "class_definition":
                    _walk_class(inner)
                else:
                    # Decorated non-function (e.g. decorated class var) — still recurse.
                    _walk_stmt(child, cls_name, {}, class_fields)
            elif child.type == "class_definition":
                _walk_class(child)
            else:
                # Class body statements (class-level calls, annotations, etc.)
                _walk_stmt(child, cls_name, class_fields, class_fields)

    def _walk_toplevel(node: Node) -> None:
        """Walk top-level (module-level) nodes."""
        if node.type in ("import_statement", "import_from_statement"):
            _emit_import_edges(node)
        elif node.type == "class_definition":
            _walk_class(node)
        elif node.type == "function_definition":
            _walk_function_body(node, None, {})
        elif node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner and inner.type == "function_definition":
                _walk_function_body(inner, None, {})
            elif inner and inner.type == "class_definition":
                _walk_class(inner)
            else:
                for child in node.children:
                    _walk_toplevel(child)
        else:
            # Module-level calls, expressions, etc.
            for child in node.children:
                _walk_toplevel(child)

    for child in root.children:
        _walk_toplevel(child)

    return edges


# ── TypeScript / JavaScript extraction ────────────────────────────────────────


def _extract_symbols_typescript(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a TypeScript/TSX AST and extract all symbol types."""
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, class_name: str | None = None) -> None:
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                kind = "method" if class_name else "function"
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=qualified,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        qualified,
                        kind,
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=qualified,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, None)

        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=qualified,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        qualified,
                        "method",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=qualified,
                    )
                )

        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = _text(name_node)
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=cls_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        cls_name,
                        "class",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=cls_name,
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, cls_name)

        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        name,
                        "interface",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=name,
                    )
                )

        elif node.type == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                doc = _ts_jsdoc(node)
                fields = extract_node_fields(
                    node,
                    "typescript",
                    qualified_name=name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        name,
                        "type",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=name,
                    )
                )

        else:
            for child in node.children:
                _walk(child, class_name)

    for child in root.children:
        _walk(child, None)

    return symbols


def _extract_edges_typescript(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a TypeScript/TSX AST.

    Import heuristic:
      - default import X          → target = 'X'
      - named import { X, Y }     → one edge per import_specifier (real name)
      - aliased import { a as b } → target = 'a' (real name, not alias)
      - namespace import * as ns  → target = 'ns'

    Call heuristic (Tier B B4 enhanced):
      - identifier call_expression → source/target edge (bare, no receiver)
      - member_expression call (obj.method()) → when SEAM_TYPE_INFERENCE=on and the
        receiver type is known in scope, target = 'Type.method'; else bare 'method'.
      - Scope: class field pre-scan (order-independent) + per-function param/local types.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"
    type_inference_on = config.SEAM_TYPE_INFERENCE == "on"

    def _emit_ts_inheritance(node: Node) -> None:
        """Emit extends/implements edges for a TS class_declaration or interface_declaration."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        src_name = _text(name_node)
        line = node.start_point[0] + 1

        def _emit_from_clause(clause: Node, kind: str) -> None:
            for c in clause.named_children:
                target = _base_type_name(c)
                if target:
                    edges.append(Edge(
                        source=src_name,
                        target=target,
                        kind=kind,
                        file=file_str,
                        line=line,
                        confidence="INFERRED",
                        receiver=None,
                    ))

        for child in node.children:
            if child.type == "class_heritage":
                for clause in child.children:
                    if clause.type == "extends_clause":
                        _emit_from_clause(clause, "extends")
                    elif clause.type == "implements_clause":
                        _emit_from_clause(clause, "implements")
            elif child.type == "extends_type_clause":
                _emit_from_clause(child, "extends")

    def _emit_import_edges_ts(node: Node) -> None:
        """Emit import edges for a TS import_statement."""
        line = node.start_point[0] + 1
        clause = None
        for child in node.children:
            if child.type == "import_clause":
                clause = child
                break
        if not clause:
            return
        for clause_child in clause.children:
            if clause_child.type == "identifier":
                edges.append(Edge(
                    source=file_stem,
                    target=_text(clause_child),
                    kind="import",
                    file=file_str,
                    line=line,
                    confidence="INFERRED",
                    receiver=None,
                ))
            elif clause_child.type == "namespace_import":
                for ns_child in clause_child.children:
                    if ns_child.type == "identifier":
                        edges.append(Edge(
                            source=file_stem,
                            target=_text(ns_child),
                            kind="import",
                            file=file_str,
                            line=line,
                            confidence="INFERRED",
                            receiver=None,
                        ))
                        break
            elif clause_child.type == "named_imports":
                for spec in clause_child.children:
                    if spec.type == "import_specifier":
                        name_node = spec.child_by_field_name("name")
                        if name_node is None and spec.children:
                            name_node = spec.children[0]
                        if name_node:
                            edges.append(Edge(
                                source=file_stem,
                                target=_text(name_node),
                                kind="import",
                                file=file_str,
                                line=line,
                                confidence="INFERRED",
                                receiver=None,
                            ))

    def _emit_ts_instantiates(
        node: Node,
        class_name: str | None,
        language: str,
    ) -> None:
        """Emit an instantiates edge from a TS/JS new_expression node.

        Tier B B6: new Foo(...) → kind='instantiates' edge with target='Foo'.
        The constructor name is the first identifier child of new_expression.
        Dynamic news (new (expr)()) have a non-identifier child — skipped gracefully.
        Never raises.
        """
        try:
            source = _find_enclosing_function(node, language)
            if source is None:
                return
            # Children of new_expression: [new, constructor, arguments]
            # constructor is identifier for `new Foo()`, or a complex expr for dynamic.
            for child in node.children:
                if child.type == "identifier":
                    type_name = _text(child)
                    if type_name:
                        edges.append(Edge(
                            source=source,
                            target=type_name,
                            kind="instantiates",
                            file=file_str,
                            line=node.start_point[0] + 1,
                            confidence="INFERRED",
                            receiver=None,
                        ))
                    return  # Only the first identifier is the type name.
        except Exception:  # noqa: BLE001
            pass

    def _emit_call_edge_ts(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
        language: str,
    ) -> None:
        """Emit a call edge for a TS/JS call_expression node using scope inference."""
        func_child = node.child_by_field_name("function")
        callee_node: Node | None = None
        receiver_text: str | None = None

        if func_child and func_child.type == "identifier":
            callee_node = func_child
        elif func_child and func_child.type == "member_expression":
            prop = func_child.child_by_field_name("property")
            obj = func_child.child_by_field_name("object")
            if prop is not None and prop.type == "property_identifier":
                callee_node = prop
                if obj is not None:
                    receiver_text = _text(obj)

        if callee_node is None:
            return
        source = _find_enclosing_function(node, language)
        if source is None:
            return

        method_name = _text(callee_node)
        target = method_name

        # Tier B B4: receiver-type inference for member_expression calls.
        if type_inference_on and receiver_text is not None:
            resolved_type = resolve_receiver_type(
                receiver_text, class_name, var_types, _TS_SELF_NAMES
            )
            if resolved_type is not None:
                target = f"{resolved_type}.{method_name}"

        edges.append(Edge(
            source=source,
            target=target,
            kind="call",
            file=file_str,
            line=node.start_point[0] + 1,
            confidence="INFERRED",
            receiver=receiver_text,
        ))

    def _walk_ts_function_body(
        func_node: Node,
        class_name: str | None,
        class_fields: dict[str, str],
        language: str,
    ) -> None:
        """Walk a TS/JS function body with accumulated per-function scope."""
        var_types: dict[str, str] = dict(class_fields)
        record_ts_param_types(func_node, var_types)

        body = func_node.child_by_field_name("body")
        if body is None:
            return
        for stmt in body.children:
            record_ts_local_types(stmt, var_types)
            _walk_ts_stmt(stmt, class_name, var_types, class_fields, language)

    def _walk_ts_stmt(
        node: Node,
        class_name: str | None,
        var_types: dict[str, str],
        class_fields: dict[str, str],
        language: str,
    ) -> None:
        """Recursively walk a TS/JS statement, emitting edges with current scope."""
        if node.type == "import_statement":
            _emit_import_edges_ts(node)
            return

        if emit_inheritance and node.type in ("class_declaration", "interface_declaration"):
            _emit_ts_inheritance(node)

        if node.type == "call_expression":
            _emit_call_edge_ts(node, class_name, var_types, language)
            # Fall through to recurse — nested calls inside arguments, etc.

        # Tier B B6: new_expression (new Foo(...)) → instantiates edge.
        # The identifier child of new_expression is the constructed type name.
        # Dynamic new (new (expr)()) has a non-identifier constructor — skip gracefully.
        if node.type == "new_expression":
            _emit_ts_instantiates(node, class_name, language)
            # Fall through to recurse — constructor args may contain nested news.

        # Nested function/method: enter a new function scope.
        if node.type in ("function_declaration", "arrow_function", "function_expression"):
            _walk_ts_function_body(node, class_name, class_fields, language)
            return
        if node.type == "method_definition":
            _walk_ts_function_body(node, class_name, class_fields, language)
            return
        if node.type == "class_declaration":
            _walk_ts_class(node, language)
            return

        for child in node.children:
            _walk_ts_stmt(child, class_name, var_types, class_fields, language)

    def _walk_ts_class(class_node: Node, language: str) -> None:
        """Walk a TS class_declaration: emit inheritance + recurse into methods."""
        cls_name_node = class_node.child_by_field_name("name")
        cls_name = _text(cls_name_node) if cls_name_node else None

        if emit_inheritance and cls_name:
            _emit_ts_inheritance(class_node)

        class_fields: dict[str, str] = {}
        if type_inference_on and cls_name:
            class_fields = scan_class_fields_typescript(class_node)

        body = class_node.child_by_field_name("body")
        if body is None:
            return
        for child in body.children:
            if child.type == "method_definition":
                _walk_ts_function_body(child, cls_name, class_fields, language)
            elif child.type in ("public_field_definition", "field_definition"):
                # Already captured in pre-scan; skip to avoid re-emitting.
                pass
            elif child.type == "class_declaration":
                _walk_ts_class(child, language)
            else:
                _walk_ts_stmt(child, cls_name, class_fields, class_fields, language)

    def _walk_ts_toplevel(node: Node, language: str) -> None:
        """Walk top-level TS/JS nodes."""
        if node.type == "import_statement":
            _emit_import_edges_ts(node)
        elif node.type == "class_declaration":
            _walk_ts_class(node, language)
        elif emit_inheritance and node.type == "interface_declaration":
            _emit_ts_inheritance(node)
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    _walk_ts_stmt(child, None, {}, {}, language)
        elif node.type in ("function_declaration", "function_expression"):
            _walk_ts_function_body(node, None, {}, language)
        elif node.type == "lexical_declaration":
            # const f = () => {...} — arrow function at top level
            for child in node.children:
                if child.type == "variable_declarator":
                    val = child.child_by_field_name("value")
                    if val and val.type in ("arrow_function", "function_expression"):
                        _walk_ts_function_body(val, None, {}, language)
        else:
            for child in node.children:
                _walk_ts_toplevel(child, language)

    # Determine language for enclosing-function resolution.
    # filepath suffix determines language — both "typescript" and "javascript" go here.
    lang = "typescript"

    for child in root.children:
        _walk_ts_toplevel(child, lang)

    return edges


# ── Comment extraction ────────────────────────────────────────────────────────


def _strip_py_comment(raw: str) -> str:
    """Strip the leading '#' delimiter and whitespace from a Python comment."""
    return raw.lstrip("#").strip()


def _strip_ts_line_comment(raw: str) -> str:
    """Strip the leading '//' delimiter and whitespace from a TS/JS line comment."""
    return raw.lstrip("/").strip()


def _extract_comments_python(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Python AST and collect matched semantic comment nodes."""
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "comment":
            raw = _text(node)
            body = _strip_py_comment(raw)
            result = _match_marker(body)
            if result is not None:
                marker, text = result
                comments.append(
                    Comment(
                        marker=marker,
                        text=text,
                        line=node.start_point[0] + 1,
                    )
                )
        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments


def _extract_comments_typescript(root: Node, filepath: Path) -> list[Comment]:
    """Walk a TypeScript/JS AST and collect matched semantic comment nodes.

    Handles both // line comments and /* */ block comments. For block comments,
    EVERY line is scanned so a marker on line 2+ of a JSDoc-style block is
    detected, with the stored line number pointing at the marker's real line.
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "comment":
            raw = _text(node)
            base_row = node.start_point[0] + 1
            if raw.startswith("/*"):
                for offset, body in _block_comment_lines(raw):
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row + offset))
            else:
                body = _strip_ts_line_comment(raw) if raw.startswith("//") else raw.strip()
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row))
        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments


# ── Public API ─────────────────────────────────────────────────────────────────


def extract_symbols(node: object, language: str, filepath: Path) -> list[Symbol]:
    """Extract all symbol definitions from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript' | 'go' | 'rust' |
                  'java' | 'csharp' | 'ruby' | 'c' | 'cpp' | 'php'
        filepath: resolved absolute Path to the source file

    Returns list of Symbol TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            return _extract_symbols_python(node, filepath)
        elif language in ("typescript", "javascript"):
            return _extract_symbols_typescript(node, filepath)
        elif language == "go":
            return _extract_symbols_go(node, filepath)
        elif language == "rust":
            return _extract_symbols_rust(node, filepath)
        # Phase 9 — new languages (stubs return []; family agents fill logic)
        elif language == "java":
            return _extract_symbols_java(node, filepath)
        elif language == "csharp":
            return _extract_symbols_csharp(node, filepath)
        elif language == "ruby":
            return _extract_symbols_ruby(node, filepath)
        elif language == "c":
            return _extract_symbols_c(node, filepath)
        elif language == "cpp":
            return _extract_symbols_cpp(node, filepath)
        elif language == "php":
            return _extract_symbols_php(node, filepath)
        # Phase 10 — Swift
        elif language == "swift":
            return _extract_symbols_swift(node, filepath)
    except Exception:  # noqa: BLE001
        # WHY log: a silent except here would make a grammar-version break
        # or a bad language string completely invisible. Logging at debug
        # preserves the never-raise contract while surfacing the root cause.
        logger.debug(
            "extract_symbols: unhandled exception for language=%r file=%s",
            language,
            filepath,
            exc_info=True,
        )
        return []
    return []


def extract_comments(node: object, language: str, filepath: Path) -> list[Comment]:
    """Extract semantic comments from an AST root node.

    Only WHY/HACK/NOTE/TODO/FIXME-tagged comments are returned; plain comments
    are silently ignored. The marker is normalized to UPPERCASE.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path).
        language: 'python' | 'typescript' | 'javascript' | 'go' | 'rust' |
                  'java' | 'csharp' | 'ruby' | 'c' | 'cpp' | 'php'
        filepath: resolved absolute Path to the source file.

    Returns list of Comment TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            return _extract_comments_python(node, filepath)
        elif language in ("typescript", "javascript"):
            return _extract_comments_typescript(node, filepath)
        elif language == "go":
            return _extract_comments_go(node, filepath)
        elif language == "rust":
            return _extract_comments_rust(node, filepath)
        # Phase 9 — new languages (stubs return []; family agents fill logic)
        elif language == "java":
            return _extract_comments_java(node, filepath)
        elif language == "csharp":
            return _extract_comments_csharp(node, filepath)
        elif language == "ruby":
            return _extract_comments_ruby(node, filepath)
        elif language == "c":
            return _extract_comments_c(node, filepath)
        elif language == "cpp":
            return _extract_comments_cpp(node, filepath)
        elif language == "php":
            return _extract_comments_php(node, filepath)
        # Phase 10 — Swift
        elif language == "swift":
            return _extract_comments_swift(node, filepath)
    except Exception:  # noqa: BLE001
        logger.debug(
            "extract_comments: unhandled exception for language=%r file=%s",
            language,
            filepath,
            exc_info=True,
        )
        return []
    return []


def extract_edges(
    node: object,
    language: str,
    filepath: Path,
    symbols: list[Symbol] | None = None,
) -> list[Edge]:
    """Extract import and call edges from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript' | 'go' | 'rust'
        filepath: resolved absolute Path to the source file
        symbols:  Optional list of symbols extracted from the same file.
                  When provided, each edge's confidence is resolved:
                    EXTRACTED  — target name matches exactly one symbol in the list
                    AMBIGUOUS  — target name matches more than one symbol
                    INFERRED   — target not in the symbol list (default/heuristic)
                  When omitted, all edges carry confidence='INFERRED'.

    Returns list of Edge TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            raw_edges = _extract_edges_python(node, filepath)
        elif language in ("typescript", "javascript"):
            raw_edges = _extract_edges_typescript(node, filepath)
        elif language == "go":
            raw_edges = _extract_edges_go(node, filepath)
        elif language == "rust":
            raw_edges = _extract_edges_rust(node, filepath)
        # Phase 9 — new languages (stubs return []; family agents fill logic)
        elif language == "java":
            raw_edges = _extract_edges_java(node, filepath)
        elif language == "csharp":
            raw_edges = _extract_edges_csharp(node, filepath)
        elif language == "ruby":
            raw_edges = _extract_edges_ruby(node, filepath)
        elif language == "c":
            raw_edges = _extract_edges_c(node, filepath)
        elif language == "cpp":
            raw_edges = _extract_edges_cpp(node, filepath)
        elif language == "php":
            raw_edges = _extract_edges_php(node, filepath)
        # Phase 10 — Swift
        elif language == "swift":
            raw_edges = _extract_edges_swift(node, filepath)
        else:
            return []

        if symbols is None:
            return raw_edges

        # Build a name-count map from the symbol list to detect same-file duplicates.
        name_counts: dict[str, int] = {}
        for sym in symbols:
            name_counts[sym["name"]] = name_counts.get(sym["name"], 0) + 1

        # Annotate each edge's confidence based on resolution against the symbol set.
        for edge in raw_edges:
            edge["confidence"] = _resolve_confidence_multi(edge["target"], name_counts)
        return raw_edges

    except Exception:  # noqa: BLE001
        logger.debug(
            "extract_edges: unhandled exception for language=%r file=%s",
            language,
            filepath,
            exc_info=True,
        )
        return []
