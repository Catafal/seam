"""Symbol and edge extraction from tree-sitter AST nodes.

Pure functions: take AST node + metadata, return structured data.
No I/O, no DB, no side effects.

Contract (evolved from Phase-0 FROZEN — see docs/CONTRACT.md):
  Symbol fields: name, kind, file, start_line, end_line, docstring
  Edge fields:   source, target, kind, file, line, confidence (Phase 1 addition)

Confidence tagging (Phase 1 — issue #3):
  Confidence is assigned during edge extraction and stored in the edges.confidence column.
  Resolution scope at extraction time is the symbol list from the SAME FILE at the same call.
  This is a same-file lower-bound hint — useful for debugging — but NOT authoritative.

  READ-TIME WHOLE-INDEX RESOLUTION IS AUTHORITATIVE (Phase 1b — issue #9):
  When the analysis layer (seam/analysis/confidence.py) reads edges, it re-resolves
  confidence against the full index using a name→count map loaded once per query.
  This whole-index resolution overrides the stored column value.
  The stored column is kept as-is; no schema change is required.

  Stored column semantics (same-file scope):
    EXTRACTED  — target name resolves to exactly ONE symbol in the same-file symbol set.
    AMBIGUOUS  — target name matches MORE THAN ONE symbol in the same-file symbol set.
    INFERRED   — all other cases: heuristic best-guess (target not in same-file symbol set).

  Authoritative read-time semantics (whole-index scope, see seam/analysis/confidence.py):
    EXTRACTED  — target name is unique across the ENTIRE index.
    AMBIGUOUS  — target name is shared by more than one indexed symbol.
    INFERRED   — target name is not in the index at all (external, stdlib, dynamic).
"""

import logging
from pathlib import Path
from typing import Literal, TypedDict

from tree_sitter import Node

logger = logging.getLogger(__name__)

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
    confidence: Confidence  # Confidence value: EXTRACTED | INFERRED | AMBIGUOUS


# ── Internal helpers ───────────────────────────────────────────────────────────


def _resolve_confidence_multi(target_name: str, symbol_name_counts: dict[str, int]) -> Confidence:
    """Resolve confidence using a same-file name->count mapping.

    SCOPE: same-file only — this is a lower-bound hint stored on the edge.
    The authoritative whole-index resolution lives in seam/analysis/confidence.py
    and is applied at read time by traversal.walk / flows.trace / callers / callees.

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

    Returns the docstring's CONTENT (without the surrounding quote delimiters),
    or None. Uses the tree-sitter `string_content` child node rather than
    char-class stripping — `.strip("\"'")` would also eat legitimate leading/
    trailing quote characters that are part of the docstring text.
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
    # tree-sitter Python string nodes are [string_start, string_content, string_end].
    # Extract the content node directly so quotes in the text survive.
    for child in expr.children:
        if child.type == "string_content":
            return _text(child).strip()
    return None  # empty string literal ("" / '') has no string_content


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


def _arrow_function_name(arrow_node: Node) -> str | None:
    """Resolve the name of an arrow function from its assignment context.

    Arrow functions have no 'name' field in the AST. If the arrow function
    is directly assigned to a variable in a variable_declarator
    (e.g. `const handler = () => { ... }`), we use that variable's name.

    Rule (documented):
      - arrow in `const/let/var X = () => {...}` → source name is `X`
      - arrow as a property value, callback argument, or truly anonymous → None
        (caller continues walking up to find an enclosing named function)

    We intentionally do NOT recurse into object literal properties
    (`{ method: () => { ... } }`) because those identifiers are property keys,
    not function declarations, and naming them would produce misleading edges.
    """
    parent = arrow_node.parent
    if parent is None:
        return None
    # Direct assignment: variable_declarator is the immediate parent
    if parent.type == "variable_declarator":
        name_node = parent.child_by_field_name("name")
        if name_node is not None and name_node.type == "identifier":
            return _text(name_node)
    return None


def _find_enclosing_function(node: Node, language: str) -> str | None:
    """Walk up the parent chain to find the nearest enclosing function/method name.

    Returns 'ClassName.methodName' for methods, plain name for functions, or None
    when no enclosing function exists (e.g. top-level module code).

    For TypeScript/JavaScript arrow functions (which have no 'name' AST field):
      - Named function_declaration / method_definition ALWAYS wins: if one is found
        while walking up, its (qualified) name is returned immediately, regardless
        of any inner arrow const names.
      - The FIRST (innermost) arrow_function assigned to a variable sets a fallback
        name. This fallback is only returned if no named function/method is found
        higher in the chain.
      - If neither a named scope nor a named arrow is found, returns None (edge dropped).

    Attribution priority (highest to lowest):
      1. Nearest named function_declaration or method_definition (with class qualification)
      2. Innermost const-assigned arrow_function (fallback_arrow_name)
      3. None — edge is skipped
    """
    func_types_py = {"function_definition"}
    func_types_ts = {"function_declaration", "method_definition", "arrow_function"}
    func_types = func_types_py if language == "python" else func_types_ts

    # Fallback: name of the innermost const-assigned arrow found while walking up.
    # Only used when NO named function/method exists higher in the chain.
    fallback_arrow_name: str | None = None

    current = node.parent
    while current is not None:
        if current.type in func_types:
            if current.type == "arrow_function":
                # Record the first (innermost) arrow name as a fallback only.
                # Named scopes higher up still take priority.
                if fallback_arrow_name is None:
                    fallback_arrow_name = _arrow_function_name(current)
                # Always continue walking up — a named function/method wins.
                current = current.parent
                continue

            # Named scope (function_declaration, method_definition, function_definition).
            # This ALWAYS overrides any arrow fallback collected below.
            name_node = current.child_by_field_name("name")
            if name_node is None:
                current = current.parent
                continue
            func_name = _text(name_node)
            # Check if the function is inside a class to produce qualified name.
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

    # No named function/method found anywhere in the chain.
    # Return the innermost arrow const name if one was recorded; otherwise None.
    if fallback_arrow_name is None:
        logger.debug(
            "_find_enclosing_function: no named scope found — call edge source "
            "cannot be resolved; edge will be dropped"
        )
    return fallback_arrow_name


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
                # Recurse into the body, but a nested def is a LOCAL function,
                # NOT a method of the enclosing class — drop the class context.
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        _walk(child, None)

        elif node.type == "decorated_definition":
            # A decorator wraps either a function (@staticmethod, @property, ...)
            # OR a class (@dataclass, @attr.s, ...). Handle BOTH; the inner
            # definition node is processed with the decorated node's line range
            # (so the decorator rows are included), then its body is walked.
            definition = node.child_by_field_name("definition")
            if definition and definition.type == "function_definition":
                name = _node_name(definition)
                if name:
                    kind = "method" if class_name else "function"
                    qualified = f"{class_name}.{name}" if class_name else name
                    doc = _py_docstring(definition)
                    symbols.append(_make_symbol(qualified, kind, file_str, node, doc))
                    body = definition.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            _walk(child, None)
            elif definition and definition.type == "class_definition":
                name = _node_name(definition)
                if name:
                    doc = _py_docstring(definition)
                    symbols.append(_make_symbol(name, "class", file_str, node, doc))
                    body = definition.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            _walk(child, name)

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

    All edges are emitted with confidence='INFERRED' by default.
    The caller (extract_edges) upgrades confidence using the symbol set.
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
                            confidence="INFERRED",  # upgraded by extract_edges if resolvable
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
                                confidence="INFERRED",
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
                                    confidence="INFERRED",
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
                            confidence="INFERRED",
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
                        _walk(child, None)  # nested fn is local, not a method

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
      - default import X          → edge with target = 'X'
      - named import { X, Y }     → one edge per import_specifier, target = real name
      - aliased import { a as b } → target = 'a' (real name), NOT 'b' (alias)
      - namespace import * as ns  → target = 'ns' (the local binding; a namespace has
                                    no single exported name, so we use the binding)
      - call_expression inside arrow_function body → source is the arrow's variable
        name (if assigned), else the nearest named enclosing function, else skipped.

    Call heuristic (MVP):
      - call_expression where function is a bare identifier → target = identifier
      - source = nearest enclosing function/method (skip if none)

    All edges are emitted with confidence='INFERRED' by default.
    The caller (extract_edges) upgrades confidence using the symbol set.
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
                        # Default import: import X from 'mod' → target = 'X'
                        edges.append(
                            Edge(
                                source=file_stem,
                                target=_text(clause_child),
                                kind="import",
                                file=file_str,
                                line=line,
                                confidence="INFERRED",
                            )
                        )
                    elif clause_child.type == "namespace_import":
                        # Namespace import: import * as ns from 'mod' → target = 'ns'
                        # A namespace has no single exported name; we use the local
                        # binding (ns) as the target so the edge points to a usable name.
                        for ns_child in clause_child.children:
                            if ns_child.type == "identifier":
                                edges.append(
                                    Edge(
                                        source=file_stem,
                                        target=_text(ns_child),
                                        kind="import",
                                        file=file_str,
                                        line=line,
                                        confidence="INFERRED",
                                    )
                                )
                                break  # only the identifier (alias) needed
                    elif clause_child.type == "named_imports":
                        # Named imports: { X, Y } or { a as b }
                        for spec in clause_child.children:
                            if spec.type == "import_specifier":
                                # 'name' field = real exported name (e.g. 'a' in 'a as b')
                                # 'alias' field = local binding (e.g. 'b' in 'a as b')
                                # We use the real name so the edge points to the actual export.
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
                                            confidence="INFERRED",
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
                            confidence="INFERRED",
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


def extract_edges(
    node: object,
    language: str,
    filepath: Path,
    symbols: list[Symbol] | None = None,
) -> list[Edge]:
    """Extract import and call edges from an AST root node.

    Args:
        node:     tree-sitter root node returned by parser.parse_*(path)
        language: 'python' | 'typescript' | 'javascript'
        filepath: resolved absolute Path to the source file
        symbols:  Optional list of symbols extracted from the same file.
                  When provided, each edge's confidence is resolved:
                    EXTRACTED  — target name matches exactly one symbol in the list
                    AMBIGUOUS  — target name matches more than one symbol
                    INFERRED   — target not in the symbol list (default/heuristic)
                  When omitted, all edges carry confidence='INFERRED'.

    Returns list of Edge TypedDicts (may be empty, never raises).

    Resolution scope note: confidence is resolved ONLY against the same-file symbol
    list passed here, not the full DB. Cross-file ambiguity is handled at the query
    layer (engine.context() sets ambiguous=True when multiple DB rows share a name).
    """
    if not isinstance(node, Node):
        return []
    try:
        if language == "python":
            raw_edges = _extract_edges_python(node, filepath)
        elif language in ("typescript", "javascript"):
            raw_edges = _extract_edges_typescript(node, filepath)
        else:
            return []

        if symbols is None:
            return raw_edges

        # Build a name-count map from the symbol list to detect same-file duplicates.
        name_counts: dict[str, int] = {}
        for sym in symbols:
            name_counts[sym["name"]] = name_counts.get(sym["name"], 0) + 1

        # Annotate each edge's confidence based on resolution against the symbol set.
        # Mutate in place: TypedDicts are mutable dicts; no need to rebuild a new Edge.
        for edge in raw_edges:
            edge["confidence"] = _resolve_confidence_multi(edge["target"], name_counts)
        return raw_edges

    except Exception:  # noqa: BLE001
        return []
