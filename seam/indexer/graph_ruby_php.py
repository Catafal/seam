"""Ruby and PHP symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common    (leaf — no seam deps)
         ↑
    graph_ruby_php  (this file)
         ↑
    graph.py        (imports this module's public extractors at top level)

WHY split from graph.py: graph.py would exceed 1000 lines if it contained
Ruby + PHP extractors directly. Splitting here (one file per language pair)
keeps all files within limits and maintains the top-level-only import rule.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default (whole-index resolution
    at read time handles EXTRACTED/AMBIGUOUS/INFERRED for cross-file edges).

VERIFIED GRAMMAR FACTS (AST-dumped before coding):
  Ruby: module/class → constant child (not 'name' field); method → identifier child;
    singleton_method → [def, self, ".", identifier, ...]; bare call → 'method' field
    with no 'receiver' field; comment → '#' line comments only.
  PHP: class/function/method 'name' field (name node) + declaration_list/compound_statement
    body; namespace_use_declaration → namespace_use_clause → qualified_name (last 'name');
    function_call_expression 'function' field = 'name' node (bare); comment node covers
    //, #, and /* */ blocks.
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
from seam.indexer.signatures_ext import _extract_ruby as _sig_ruby

logger = logging.getLogger(__name__)

# ── Ruby doc-comment helpers ──────────────────────────────────────────────────


def _ruby_doc_comment(decl_node: Node) -> str | None:
    """Capture a Ruby doc-comment: contiguous # lines immediately above the node.

    WHY: Ruby has no dedicated doc-comment syntax — leading # blocks serve as docs.
    """
    lines: list[str] = []
    current = decl_node.prev_sibling

    while current is not None and current.type == "comment":
        raw = _text(current)
        # Adjacency: comment end_row + 1 == decl start_row (or next sibling start).
        next_node = current.next_sibling
        if next_node is not None:
            end_row = current.end_point[0]
            next_start_row = next_node.start_point[0]
            if end_row + 1 != next_start_row:
                break  # blank line gap — stop collecting

        # Strip '#' prefix and leading whitespace.
        body = raw.lstrip("#").strip()
        lines.append(body)
        current = current.prev_sibling

    if not lines:
        return None
    # Lines collected bottom-up; reverse to source order.
    return "\n".join(reversed(lines))


# ── Ruby symbol extraction ────────────────────────────────────────────────────


def _extract_symbols_ruby(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Ruby AST and extract class, module, method, and singleton method symbols.

    Kind mapping (Phase 9 spec):
        module  → class  (named container — closest fit in the closed vocabulary; per ADR)
        class   → class
        method  inside a class/module → method, qualified 'Container.method'
        singleton_method (def self.X) inside class/module → method 'Container.X'
        method  at top-level (no class/module ancestor) → function

    Traversal: DFS from root, tracking current class/module name as we descend.
    body_statement holds the child nodes inside a class/module body.

    NEVER raises — all exceptions caught and logged at DEBUG level.
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    try:
        _walk_ruby(root, filepath, file_str, symbols, class_name=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_symbols_ruby: unexpected error for %s: %r", filepath, exc)

    return symbols


def _ruby_class_or_module_name(node: Node) -> str | None:
    """Extract the name from a Ruby class or module node (first 'constant' child)."""
    # The class/module name is the first 'constant' child.
    for child in node.children:
        if child.type == "constant":
            return _text(child)
    return None


def _ruby_singleton_method_name(node: Node) -> str | None:
    """Extract the method name from a Ruby singleton_method node.

    Structure: [def, self, ".", identifier, ...]. Name is the identifier after the dot.
    """
    # Children: [def, self, ".", identifier, method_parameters?, body_statement, end]
    found_dot = False
    for child in node.children:
        if child.type == ".":
            found_dot = True
            continue
        if found_dot and child.type == "identifier":
            return _text(child)
    return None


def _walk_ruby(
    node: Node,
    filepath: Path,
    file_str: str,
    symbols: list[Symbol],
    class_name: str | None,
) -> None:
    """Recursive DFS walker for Ruby AST nodes.

    class_name: enclosing class/module name (for method qualification).
    """
    if node.type == "class":
        _handle_ruby_class(node, filepath, file_str, symbols)
        return  # handled recursively inside

    if node.type == "module":
        _handle_ruby_module(node, filepath, file_str, symbols)
        return  # handled recursively inside

    if node.type == "method":
        _handle_ruby_method(node, filepath, file_str, symbols, class_name)
        # No return — recurse into method body for nested defs (unusual but safe)

    if node.type == "singleton_method":
        _handle_ruby_singleton_method(node, filepath, file_str, symbols, class_name)
        # No return — recurse for nested content

    for child in node.children:
        _walk_ruby(child, filepath, file_str, symbols, class_name)


def _handle_ruby_class(
    node: Node,
    filepath: Path,
    file_str: str,
    symbols: list[Symbol],
) -> None:
    """Emit a class symbol and recurse into its body with the class name set."""
    name = _ruby_class_or_module_name(node)
    if not name:
        return

    doc = _ruby_doc_comment(node)
    fields = _sig_ruby(node, name, config.SEAM_MAX_SIGNATURE_LEN)
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

    # Recurse into body with this class name as context.
    for child in node.children:
        _walk_ruby(child, filepath, file_str, symbols, class_name=name)


def _handle_ruby_module(
    node: Node,
    filepath: Path,
    file_str: str,
    symbols: list[Symbol],
) -> None:
    """Emit a module symbol (kind='class') and recurse into body with module name set."""
    name = _ruby_class_or_module_name(node)
    if not name:
        return

    doc = _ruby_doc_comment(node)
    fields = _sig_ruby(node, name, config.SEAM_MAX_SIGNATURE_LEN)
    symbols.append(
        _make_symbol(
            name,
            "class",  # Per spec: module → class (closest fit in closed vocabulary)
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

    # Recurse into module body with module name as context.
    for child in node.children:
        _walk_ruby(child, filepath, file_str, symbols, class_name=name)


def _handle_ruby_method(
    node: Node,
    filepath: Path,
    file_str: str,
    symbols: list[Symbol],
    class_name: str | None,
) -> None:
    """Emit a method or function symbol from a Ruby 'method' (def) node.

    If inside a class/module (class_name set) → kind='method', name='Class.method'.
    At top level (class_name=None) → kind='function', name=method_name.
    """
    # The name is an 'identifier' child (first identifier after 'def').
    name: str | None = None
    for child in node.children:
        if child.type == "identifier":
            name = _text(child)
            break
    if not name:
        return

    doc = _ruby_doc_comment(node)

    if class_name is not None:
        qualified = f"{class_name}.{name}"
        fields = _sig_ruby(node, qualified, config.SEAM_MAX_SIGNATURE_LEN)
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
    else:
        fields = _sig_ruby(node, name, config.SEAM_MAX_SIGNATURE_LEN)
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


def _handle_ruby_singleton_method(
    node: Node,
    filepath: Path,
    file_str: str,
    symbols: list[Symbol],
    class_name: str | None,
) -> None:
    """Emit a singleton_method (def self.X) symbol.

    singleton_method inside a class/module: name='Class.X', kind='method'.
    singleton_method at top level (unusual): name='X', kind='function'.
    """
    name = _ruby_singleton_method_name(node)
    if not name:
        return

    doc = _ruby_doc_comment(node)

    if class_name is not None:
        qualified = f"{class_name}.{name}"
        fields = _sig_ruby(node, qualified, config.SEAM_MAX_SIGNATURE_LEN)
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
    else:
        fields = _sig_ruby(node, name, config.SEAM_MAX_SIGNATURE_LEN)
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


# ── Ruby edge extraction ───────────────────────────────────────────────────────


def _extract_edges_ruby(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Ruby AST.

    Import heuristic:
        require('x')             → target = 'x'
        require_relative('./x')  → target = basename stem of 'x' (strip dir + .rb)

    Call heuristic (MVP — bare identifiers only):
        call node where 'method' field is identifier AND no 'receiver' field → call edge.
        require/require_relative calls are already handled as imports — excluded from calls.

    NEVER raises. Returns [] on any failure.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    try:
        _walk_ruby_edges(root, file_str, file_stem, edges)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_edges_ruby: unexpected error for %s: %r", filepath, exc)

    return edges


def _walk_ruby_edges(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
) -> None:
    """Recursive walker for Ruby edge extraction."""
    if node.type == "call":
        _handle_ruby_call(node, file_str, file_stem, edges)
        # Still recurse — calls can be nested

    for child in node.children:
        _walk_ruby_edges(child, file_str, file_stem, edges)


def _handle_ruby_call(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
) -> None:
    """Emit import or call edges from a Ruby call node.

    A 'call' node can be either a bare function call (no receiver) or a method
    call on an object (with receiver). We only emit edges for bare calls — those
    without a 'receiver' field. The 'method' field holds the identifier name.

    require/require_relative are handled as import edges (not call edges).
    """
    # 'method' field = the function/method identifier being called
    method_node = node.child_by_field_name("method")
    if method_node is None or method_node.type != "identifier":
        return

    method_name = _text(method_node)

    # 'receiver' field = the object receiving the call (obj.foo → receiver='obj')
    receiver_node = node.child_by_field_name("receiver")

    if method_name == "require":
        # Import edge for require 'x' — extract the string argument.
        if receiver_node is not None:
            return  # skip receiver calls like obj.require
        target = _ruby_require_target(node)
        if target:
            edges.append(
                Edge(
                    source=file_stem,
                    target=target,
                    kind="import",
                    file=file_str,
                    line=node.start_point[0] + 1,
                    confidence="INFERRED",
                )
            )
        return

    if method_name == "require_relative":
        # Import edge for require_relative './x' → target = basename stem.
        if receiver_node is not None:
            return
        target = _ruby_require_relative_target(node)
        if target:
            edges.append(
                Edge(
                    source=file_stem,
                    target=target,
                    kind="import",
                    file=file_str,
                    line=node.start_point[0] + 1,
                    confidence="INFERRED",
                )
            )
        return

    # Bare call: only emit when no receiver (bare identifier call).
    if receiver_node is not None:
        return  # receiver call like obj.foo — skip

    source = _find_enclosing_function(node, "ruby")
    if source is not None:
        edges.append(
            Edge(
                source=source,
                target=method_name,
                kind="call",
                file=file_str,
                line=node.start_point[0] + 1,
                confidence="INFERRED",
            )
        )


def _ruby_require_target(call_node: Node) -> str | None:
    """Extract the require target from a Ruby require('x') call node.

    Returns the string content 'x', or None if the argument cannot be parsed.
    """
    arg_list = call_node.child_by_field_name("arguments")
    if arg_list is None:
        return None
    for child in arg_list.named_children:
        if child.type == "string":
            content = _ruby_string_content(child)
            if content:
                return Path(content).stem  # strip directory prefix and extension
    return None


def _ruby_require_relative_target(call_node: Node) -> str | None:
    """Extract the stem from a require_relative './path/x' call node.

    Returns 'x' (basename without extension), or None if not parseable.
    """
    arg_list = call_node.child_by_field_name("arguments")
    if arg_list is None:
        return None
    for child in arg_list.named_children:
        if child.type == "string":
            content = _ruby_string_content(child)
            if content:
                # Strip leading ./ or ../ and return stem.
                return Path(content).stem
    return None


def _ruby_string_content(string_node: Node) -> str | None:
    """Extract the text content from a Ruby string node.

    Ruby string nodes contain a 'string_content' child with the actual text.
    Falls back to stripping quotes from the full text if no string_content found.
    """
    for child in string_node.named_children:
        if child.type == "string_content":
            return _text(child)
    # Fallback: strip surrounding quotes from full text
    raw = _text(string_node)
    return raw.strip("'\"")


# ── Ruby comment extraction ────────────────────────────────────────────────────


def _extract_comments_ruby(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Ruby AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    Ruby has only one comment node type: 'comment' (# line comment).
    Each comment's text is scanned after stripping the '#' prefix.

    NEVER raises. Returns [] on any failure.
    """
    comments: list[Comment] = []

    try:
        _walk_ruby_comments(root, comments)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_comments_ruby: unexpected error: %r", exc)

    return comments


def _walk_ruby_comments(node: Node, comments: list[Comment]) -> None:
    """Recursive walker for Ruby comment nodes."""
    if node.type == "comment":
        raw = _text(node)
        base_row = node.start_point[0] + 1
        # Strip '#' prefix (one or more) and any leading spaces.
        body = raw.lstrip("#").strip()
        result = _match_marker(body)
        if result is not None:
            marker, text = result
            comments.append(Comment(marker=marker, text=text, line=base_row))

    for child in node.children:
        _walk_ruby_comments(child, comments)


# ── PHP doc-comment helpers ───────────────────────────────────────────────────


def _php_phpdoc_comment(decl_node: Node) -> str | None:
    """Capture a PHP phpdoc comment (/** ... */) immediately above the declaration.

    Walks prev_sibling collecting comment nodes. Only block comments (/** */)
    directly adjacent (no blank line) qualify as phpdoc. Line comments (// , #)
    are also captured as leading doc if they're adjacent.

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

        elif current.type == "attribute_list":
            # Attribute (#[...]) before the declaration — skip and keep looking up.
            current = current.prev_sibling
            continue
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
        method_declaration (inside class/interface/trait) → method ('Class.method')

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

    class_name: enclosing class/interface/trait name for method qualification.
    """
    ntype = node.type

    if ntype in ("class_declaration", "interface_declaration", "trait_declaration"):
        _handle_php_class_like(node, file_str, symbols, ntype)
        return  # handled recursively inside

    if ntype == "enum_declaration":
        _handle_php_enum(node, file_str, symbols)
        return

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
    """Emit a PHP enum symbol (kind='type')."""
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

    use App\\Models\\User; → target = 'User' (last segment of the qualified name)
    Handles multiple clauses in the same use statement.
    """
    line = node.start_point[0] + 1
    for child in node.named_children:
        if child.type == "namespace_use_clause":
            target = _php_use_clause_last_segment(child)
            if target:
                edges.append(
                    Edge(
                        source=file_stem,
                        target=target,
                        kind="import",
                        file=file_str,
                        line=line,
                        confidence="INFERRED",
                    )
                )


def _php_use_clause_last_segment(clause_node: Node) -> str | None:
    """Extract the last name segment from a PHP namespace_use_clause.

    namespace_use_clause → qualified_name → [namespace_name, "\\", name]
    The final 'name' child of qualified_name is the local binding.
    Also handles simple identifier clauses.
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
