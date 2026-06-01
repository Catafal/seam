"""Symbol and edge extraction from tree-sitter AST nodes.

Pure functions: take AST node + metadata, return structured data.
No I/O, no DB, no side effects.

Contract (FROZEN — see docs/CONTRACT.md):
  Symbol fields: name, kind, file, start_line, end_line, docstring
  Edge fields:   source, target, kind, file, line
"""

from pathlib import Path
from typing import TypedDict

from tree_sitter import Node


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


# ── Internal helpers ───────────────────────────────────────────────────────────


def _text(node: Node) -> str:
    """Safely decode a tree-sitter node's text bytes to str.

    node.text is typed as bytes | None in the stubs; guard against None
    to satisfy mypy even though concrete nodes always have bytes.
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


def _py_docstring(func_or_class_node: Node) -> str | None:
    """Extract Python docstring: first expression_statement(string) in body.

    Returns the stripped string value (without surrounding quotes), or None.
    """
    body = func_or_class_node.child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    # Must be an expression_statement whose first child is a string literal
    if first.type != "expression_statement" or not first.children:
        return None
    expr = first.children[0]
    if expr.type != "string":
        return None
    return _text(expr).strip("\"'").strip()


def _ts_jsdoc(symbol_node: Node) -> str | None:
    """Extract leading JSDoc comment from the previous sibling node.

    tree-sitter emits /** ... */ blocks as 'comment' nodes immediately
    before the declaration. Only /** blocks qualify as JSDoc.
    """
    prev = symbol_node.prev_sibling
    if prev is None or prev.type != "comment":
        return None
    comment_text = _text(prev)
    # Only /** ... */ blocks, not // line comments
    if not comment_text.startswith("/**"):
        return None
    return comment_text


def _find_enclosing_function(node: Node, language: str) -> str | None:
    """Walk up the parent chain to find the nearest enclosing function/method name.

    Returns 'ClassName.methodName' for methods, plain name for functions, or None
    when no enclosing function exists (e.g. top-level module code).
    """
    func_types_py = {"function_definition"}
    func_types_ts = {"function_declaration", "method_definition", "arrow_function"}
    func_types = func_types_py if language == "python" else func_types_ts

    current = node.parent
    while current is not None:
        if current.type in func_types:
            name_node = current.child_by_field_name("name")
            if name_node is None:
                current = current.parent
                continue
            func_name = _text(name_node)
            # Check if the function is inside a class to produce qualified name
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
    return None


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
                symbols.append(_make_symbol(qualified, kind, file_str, node, doc))
                # Recurse into function body (handles nested defs, if any)
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, class_name)

        elif node.type == "decorated_definition":
            # @staticmethod / @classmethod wrap a function_definition
            definition = node.child_by_field_name("definition")
            if definition and definition.type == "function_definition":
                name = _node_name(definition)
                if name:
                    kind = "method" if class_name else "function"
                    qualified = f"{class_name}.{name}" if class_name else name
                    doc = _py_docstring(definition)
                    # Use decorated_definition start line (includes decorator row)
                    symbols.append(_make_symbol(qualified, kind, file_str, node, doc))

        elif node.type == "class_definition":
            name = _node_name(node)
            if name:
                doc = _py_docstring(node)
                symbols.append(_make_symbol(name, "class", file_str, node, doc))
                # Walk class body with class_name context for method qualification
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, name)
        else:
            # Recurse into other nodes without changing class context
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

    Call heuristic (MVP — precision not a goal):
      - call node where function is a bare identifier → target = identifier
      - source = nearest enclosing function/method (skip if none)
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem  # import edges use the file stem as source

    def _walk(node: Node) -> None:
        if node.type == "import_statement":
            # import X [, Y]
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    # For aliased imports use the 'name' field (original name)
                    target_node = child.child_by_field_name("name") or child
                    edges.append(
                        Edge(
                            source=file_stem,
                            target=_text(target_node),
                            kind="import",
                            file=file_str,
                            line=node.start_point[0] + 1,
                        )
                    )

        elif node.type == "import_from_statement":
            # from X import Y [, Z]
            # Collect all imported names that appear after the 'import' keyword
            found_import_kw = False
            for child in node.children:
                if child.type == "import":
                    found_import_kw = True
                    continue
                if found_import_kw:
                    if child.type in ("dotted_name", "identifier"):
                        edges.append(
                            Edge(
                                source=file_stem,
                                target=_text(child),
                                kind="import",
                                file=file_str,
                                line=node.start_point[0] + 1,
                            )
                        )
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            edges.append(
                                Edge(
                                    source=file_stem,
                                    target=_text(name_node),
                                    kind="import",
                                    file=file_str,
                                    line=node.start_point[0] + 1,
                                )
                            )

        elif node.type == "call":
            # Only track bare-identifier callees (not attribute calls like obj.method())
            func_child = node.child_by_field_name("function")
            if func_child and func_child.type == "identifier":
                source = _find_enclosing_function(node, "python")
                if source is not None:
                    edges.append(
                        Edge(
                            source=source,
                            target=_text(func_child),
                            kind="call",
                            file=file_str,
                            line=node.start_point[0] + 1,
                        )
                    )

        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return edges


# ── TypeScript / JavaScript extraction ────────────────────────────────────────


def _extract_symbols_typescript(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a TypeScript/TSX AST and extract all symbol types."""
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, class_name: str | None = None) -> None:
        """Recursively walk AST, tracking class context for method qualification."""
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                kind = "method" if class_name else "function"
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _ts_jsdoc(node)
                symbols.append(_make_symbol(qualified, kind, file_str, node, doc))
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, class_name)

        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                qualified = f"{class_name}.{name}" if class_name else name
                doc = _ts_jsdoc(node)
                symbols.append(_make_symbol(qualified, "method", file_str, node, doc))

        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = _text(name_node)
                doc = _ts_jsdoc(node)
                symbols.append(_make_symbol(cls_name, "class", file_str, node, doc))
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, cls_name)

        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                doc = _ts_jsdoc(node)
                symbols.append(_make_symbol(name, "interface", file_str, node, doc))

        elif node.type == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(name_node)
                doc = _ts_jsdoc(node)
                symbols.append(_make_symbol(name, "type", file_str, node, doc))

        else:
            for child in node.children:
                _walk(child, class_name)

    for child in root.children:
        _walk(child, None)

    return symbols


def _extract_edges_typescript(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a TypeScript/TSX AST.

    Import heuristic:
      - named import { X, Y } → one edge per import_specifier
      - default import X       → edge with target = 'X'

    Call heuristic (MVP):
      - call_expression where function is a bare identifier → target = identifier
      - source = nearest enclosing function/method (skip if none)
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    def _walk(node: Node) -> None:
        if node.type == "import_statement":
            line = node.start_point[0] + 1
            clause = None
            for child in node.children:
                if child.type == "import_clause":
                    clause = child
                    break
            if clause:
                for clause_child in clause.children:
                    if clause_child.type == "identifier":
                        # Default import: import X from 'mod'
                        edges.append(
                            Edge(
                                source=file_stem,
                                target=_text(clause_child),
                                kind="import",
                                file=file_str,
                                line=line,
                            )
                        )
                    elif clause_child.type == "named_imports":
                        # Named imports: { X, Y }
                        for spec in clause_child.children:
                            if spec.type == "import_specifier":
                                # Prefer the 'name' field; fall back to first child
                                name_node = spec.child_by_field_name("name")
                                if name_node is None and spec.children:
                                    name_node = spec.children[0]
                                if name_node:
                                    edges.append(
                                        Edge(
                                            source=file_stem,
                                            target=_text(name_node),
                                            kind="import",
                                            file=file_str,
                                            line=line,
                                        )
                                    )

        elif node.type == "call_expression":
            func_child = node.child_by_field_name("function")
            if func_child and func_child.type == "identifier":
                source = _find_enclosing_function(node, "typescript")
                if source is not None:
                    edges.append(
                        Edge(
                            source=source,
                            target=_text(func_child),
                            kind="call",
                            file=file_str,
                            line=node.start_point[0] + 1,
                        )
                    )

        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return edges


# ── Public API ─────────────────────────────────────────────────────────────────


def extract_symbols(node: object, language: str, filepath: Path) -> list[Symbol]:
    """Extract all symbol definitions from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript'
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
    except Exception:  # noqa: BLE001
        return []
    return []


def extract_edges(node: object, language: str, filepath: Path) -> list[Edge]:
    """Extract import and call edges from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript'
        filepath: resolved absolute Path to the source file

    Returns list of Edge TypedDicts (may be empty, never raises).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            return _extract_edges_python(node, filepath)
        elif language in ("typescript", "javascript"):
            return _extract_edges_typescript(node, filepath)
    except Exception:  # noqa: BLE001
        return []
    return []
