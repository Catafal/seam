"""PHP symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common    (leaf — no seam deps)
         ↑
    graph_php       (this file)
         ↑
    graph.py        (imports this module's public extractors at top level)

WHY split from graph_ruby_php.py: graph_ruby_php.py reached the 1000-line
limit; splitting each language into its own module keeps all files within
limits and maintains the top-level-only import rule.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default (whole-index resolution
    at read time handles EXTRACTED/AMBIGUOUS/INFERRED for cross-file edges).

VERIFIED GRAMMAR FACTS (AST-dumped before coding):
  PHP: class/function/method 'name' field (name node) + declaration_list/compound_statement
    body; namespace_use_declaration → namespace_use_clause → qualified_name (last 'name');
    namespace_use_group: the group { ... } is a 'namespace_use_group' child of the
    namespace_use_declaration — must descend into it to find clause children;
    aliased use clause: namespace_use_clause → qualified_name + 'as' + name (alias);
    function_call_expression 'function' field = 'name' node (bare); comment node covers
    //, #, and /* */ blocks.
    PHP attributes: attribute_list appears as FIRST CHILD of the declaration node (not
    prev_sibling) — the prev_sibling branch for attribute_list in phpdoc lookup is dead code.
    enum_declaration body is 'enum_declaration_list' which may contain method_declaration nodes.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config

# All shared types from the leaf module (no cycle — graph_common has no seam deps).
from seam.indexer.graph_common import (
    Comment,
    Edge,
    Symbol,
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _text,
)

# signatures_ext is the leaf enrichment module for Phase 9 languages.
from seam.indexer.signatures_ext import _extract_php as _sig_php

logger = logging.getLogger(__name__)

# ── PHP doc-comment helpers ───────────────────────────────────────────────────


def _php_phpdoc_comment(decl_node: Node) -> str | None:
    """Capture a PHP phpdoc comment (/** ... */) immediately above the declaration.

    Walks prev_sibling collecting comment nodes. Only block comments (/** */)
    directly adjacent (no blank line) qualify as phpdoc. Line comments (// , #)
    are also captured as leading doc if they're adjacent.

    WHY no attribute_list prev_sibling branch: PHP attributes (#[...]) are the
    FIRST CHILD of the declaration node itself, not a separate prev_sibling.
    A prev_sibling attribute_list never appears in practice — the branch was dead
    code and has been removed.

    Returns the combined text, stripped of /* */ and * decorations, or None.
    """
    current = decl_node.prev_sibling

    while current is not None:
        if current.type == "comment":
            raw = _text(current)
            # Check adjacency: comment must end on the row just before decl start.
            decl_start_row = decl_node.start_point[0]
            comment_end_row = current.end_point[0]
            if comment_end_row + 1 != decl_start_row:
                break  # gap — not adjacent

            if raw.startswith("/**"):
                # phpdoc block: strip /** */ decorations and return.
                return _php_strip_phpdoc(raw)
            if raw.startswith("//") or raw.startswith("#"):
                # Single line comment — use as doc if it's immediately above.
                body = raw.lstrip("/").lstrip("#").strip()
                return body if body else None
            break  # unknown comment type
        else:
            break

    return None


def _php_strip_phpdoc(raw: str) -> str | None:
    """Strip /** ... */ decorations from a PHP phpdoc block.

    Returns clean text (lines joined with newline), or None if empty.
    """
    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("/**"):
            s = s[3:].strip()
        elif s.startswith("*/"):
            s = s[2:].strip()
        elif s.startswith("*"):
            s = s[1:].strip()
        if s:
            lines.append(s)
    return "\n".join(lines) if lines else None


# ── PHP symbol extraction ──────────────────────────────────────────────────────


def _extract_symbols_php(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a PHP AST and extract class, interface, trait, enum, function, and method symbols.

    Kind mapping (Phase 9 spec):
        class_declaration       → class
        interface_declaration   → interface
        trait_declaration       → interface (named mixin — closest fit in closed vocabulary)
        enum_declaration        → type
        function_definition     → function  (top-level)
        method_declaration (inside class/interface/trait/enum) → method ('Class.method')

    PHP AST root is 'program' which contains php_tag, namespace_definition,
    namespace_use_declaration, class_declaration, function_definition, etc.

    NEVER raises — all exceptions caught and logged at DEBUG level.
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    try:
        _walk_php_symbols(root, file_str, symbols, class_name=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_symbols_php: unexpected error for %s: %r", filepath, exc)

    return symbols


def _walk_php_symbols(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    class_name: str | None,
) -> None:
    """Recursive DFS walker for PHP symbol extraction.

    class_name: enclosing class/interface/trait/enum name for method qualification.
    """
    ntype = node.type

    if ntype in ("class_declaration", "interface_declaration", "trait_declaration"):
        _handle_php_class_like(node, file_str, symbols, ntype)
        return  # handled recursively inside

    if ntype == "enum_declaration":
        _handle_php_enum(node, file_str, symbols)
        return  # handled recursively inside (recurses into enum body for methods)

    if ntype == "function_definition" and class_name is None:
        # Top-level function (not inside a class body).
        _handle_php_function(node, file_str, symbols)
        return

    if ntype == "method_declaration" and class_name is not None:
        _handle_php_method(node, file_str, symbols, class_name)
        return

    for child in node.children:
        _walk_php_symbols(child, file_str, symbols, class_name)


def _handle_php_class_like(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    ntype: str,
) -> None:
    """Emit a class/interface/trait symbol and recurse into its body."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        # Fallback: find first 'name' child
        for child in node.children:
            if child.type == "name":
                name_node = child
                break
    if name_node is None:
        return

    name = _text(name_node)
    doc = _php_phpdoc_comment(node)

    if ntype == "class_declaration":
        kind = "class"
    elif ntype == "interface_declaration":
        kind = "interface"
    else:  # trait_declaration → interface per spec
        kind = "interface"

    fields = _sig_php(node, name, config.SEAM_MAX_SIGNATURE_LEN)
    symbols.append(
        _make_symbol(
            name,
            kind,
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

    # Recurse into declaration_list body with this class name set.
    for child in node.children:
        _walk_php_symbols(child, file_str, symbols, class_name=name)


def _handle_php_enum(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a PHP enum symbol (kind='type') and recurse into its body for methods.

    WHY recurse: PHP enums can contain method_declaration nodes inside their
    enum_declaration_list body. These methods must be qualified 'EnumName.method'.
    """
    name_node = node.child_by_field_name("name")
    if name_node is None:
        for child in node.children:
            if child.type == "name":
                name_node = child
                break
    if name_node is None:
        return

    name = _text(name_node)
    doc = _php_phpdoc_comment(node)
    fields = _sig_php(node, name, config.SEAM_MAX_SIGNATURE_LEN)
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

    # Recurse into enum body with enum name as class context so methods are qualified.
    for child in node.children:
        _walk_php_symbols(child, file_str, symbols, class_name=name)


def _handle_php_function(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a top-level PHP function symbol (kind='function')."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        for child in node.children:
            if child.type == "name":
                name_node = child
                break
    if name_node is None:
        return

    name = _text(name_node)
    doc = _php_phpdoc_comment(node)
    fields = _sig_php(node, name, config.SEAM_MAX_SIGNATURE_LEN)
    symbols.append(
        _make_symbol(
            name,
            "function",
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


def _handle_php_method(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    class_name: str,
) -> None:
    """Emit a PHP method_declaration symbol (kind='method', qualified 'Class.method')."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        for child in node.children:
            if child.type == "name":
                name_node = child
                break
    if name_node is None:
        return

    name = _text(name_node)
    qualified = f"{class_name}.{name}"
    doc = _php_phpdoc_comment(node)
    fields = _sig_php(node, qualified, config.SEAM_MAX_SIGNATURE_LEN)
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


# ── PHP edge extraction ────────────────────────────────────────────────────────


def _extract_edges_php(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a PHP AST.

    Import heuristic:
        namespace_use_declaration → namespace_use_clause → qualified_name
        → last 'name' segment (e.g. 'use App\\Models\\User' → 'User')
        namespace_use_group: descend into group to find clauses
        Aliased use: 'use App\\Models\\User as U' → target = 'User' (real name)

    Call heuristic (MVP — bare identifiers only):
        function_call_expression where 'function' field is a 'name' node → call edge.
        member_call_expression ($this->m) → SKIP (not bare).

    NEVER raises. Returns [] on any failure.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    try:
        _walk_php_edges(root, file_str, file_stem, edges)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_edges_php: unexpected error for %s: %r", filepath, exc)

    return edges


def _walk_php_edges(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
) -> None:
    """Recursive walker for PHP edge extraction."""
    if node.type == "namespace_use_declaration":
        _handle_php_use_declaration(node, file_str, file_stem, edges)
        return  # no need to recurse into use declaration

    if node.type == "function_call_expression":
        _handle_php_call(node, file_str, file_stem, edges)
        # Still recurse — calls can be nested inside argument lists

    for child in node.children:
        _walk_php_edges(child, file_str, file_stem, edges)


def _handle_php_use_declaration(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
) -> None:
    """Extract import edges from a PHP namespace_use_declaration node.

    Handles three forms:
      use App\\Models\\User;          → target = 'User'
      use App\\Models\\User as U;     → target = 'User' (real exported name, not alias)
      use App\\{Foo, Bar};            → targets = ['Foo', 'Bar'] (grouped use)

    WHY grouped use: namespace_use_group is a direct child of namespace_use_declaration.
    The group contains namespace_use_clause children — must descend into it.
    """
    line = node.start_point[0] + 1
    for child in node.named_children:
        if child.type == "namespace_use_clause":
            target = _php_use_clause_exported_name(child)
            if target:
                edges.append(
                    Edge(
                        source=file_stem,
                        target=target,
                        kind="import",
                        file=file_str,
                        line=line,
                        confidence="INFERRED",
                                            receiver=None,
                                        )
                )
        elif child.type == "namespace_use_group":
            # Grouped use: use App\{Foo, Bar} — descend into the group's clauses.
            for clause in child.named_children:
                if clause.type == "namespace_use_clause":
                    target = _php_use_clause_exported_name(clause)
                    if target:
                        edges.append(
                            Edge(
                                source=file_stem,
                                target=target,
                                kind="import",
                                file=file_str,
                                line=line,
                                confidence="INFERRED",
                                                            receiver=None,
                                                        )
                        )


def _php_use_clause_exported_name(clause_node: Node) -> str | None:
    """Extract the real exported name from a PHP namespace_use_clause.

    For a plain clause (use App\\Models\\User): returns 'User' (last name segment).
    For an aliased clause (use App\\Models\\User as U): returns 'User' (the real
    exported name — consistent with TS/Rust alias convention where the import edge
    targets the real symbol, not the local alias).

    namespace_use_clause → qualified_name → last 'name' child
                         → (optional) 'as' + name (alias — ignored for edge target)
    """
    for child in clause_node.named_children:
        if child.type == "qualified_name":
            # Walk the qualified_name to find the last 'name' child.
            last_name = None
            for qchild in child.children:
                if qchild.type == "name":
                    last_name = _text(qchild)
            return last_name
        if child.type == "name":
            # Bare name (e.g. from grouped use: `use App\{Foo, Bar}`)
            return _text(child)
    return None


def _handle_php_call(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
) -> None:
    """Emit a call edge from a PHP function_call_expression node.

    function_call_expression: 'function' field is a 'name' node → bare call.
    Skip if function field is a variable ($fn()) or complex expression.
    """
    func_node = node.child_by_field_name("function")
    if func_node is None or func_node.type != "name":
        return  # variable call or complex expression — skip

    target = _text(func_node)
    source = _find_enclosing_function(node, "php")
    if source is not None:
        edges.append(
            Edge(
                source=source,
                target=target,
                kind="call",
                file=file_str,
                line=node.start_point[0] + 1,
                confidence="INFERRED",
                            receiver=None,
                        )
        )


# ── PHP comment extraction ─────────────────────────────────────────────────────


def _extract_comments_php(root: Node, filepath: Path) -> list[Comment]:
    """Walk a PHP AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    PHP comment node types:
        comment — covers // line, # line, and /* */ /* */ /** */ block comments.
    All variants are scanned; phpdoc blocks use _block_comment_lines for multi-line.

    NEVER raises. Returns [] on any failure.
    """
    comments: list[Comment] = []

    try:
        _walk_php_comments(root, comments)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_comments_php: unexpected error: %r", exc)

    return comments


def _walk_php_comments(node: Node, comments: list[Comment]) -> None:
    """Recursive walker for PHP comment nodes."""
    if node.type == "comment":
        raw = _text(node)
        base_row = node.start_point[0] + 1

        if raw.startswith("/*"):
            # Block comment (/** */ or /* */) — scan each line.
            for offset, body in _block_comment_lines(raw):
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row + offset))
        elif raw.startswith("//"):
            body = raw[2:].strip()
            result = _match_marker(body)
            if result is not None:
                marker, text = result
                comments.append(Comment(marker=marker, text=text, line=base_row))
        elif raw.startswith("#"):
            body = raw[1:].strip()
            result = _match_marker(body)
            if result is not None:
                marker, text = result
                comments.append(Comment(marker=marker, text=text, line=base_row))

    for child in node.children:
        _walk_php_comments(child, comments)
