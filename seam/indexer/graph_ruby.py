"""Ruby symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common    (leaf — no seam deps)
         ↑
    graph_ruby      (this file)
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
  Ruby: module/class → constant child (not 'name' field); method → identifier child;
    singleton_method → [def, self, ".", identifier, ...]; bare call → 'method' field
    with no 'receiver' field; comment → '#' line comments only.
    class/module body: comment nodes can appear BETWEEN the name (constant child)
    and the body_statement — the signature builder must skip them.
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
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _text,
)

# signatures_ext is the leaf enrichment module for Phase 9 languages.
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
                                    receiver=None,
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
                                    receiver=None,
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
                            receiver=None,
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
