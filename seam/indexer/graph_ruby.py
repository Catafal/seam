"""Ruby symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) and graph_scope_infer_ext[2] (leaf) — never from graph.py.

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

# A3 Slice 5: Ruby field-access edges + field symbols (sorts before graph_* imports).
from seam.indexer.field_access_ext2 import (
    collect_field_symbols_ruby,
    extract_field_accesses_ruby,
)

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

# Scope-inference: shared resolver from ext, Ruby helpers from ext2.
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import (
    _RUBY_SELF_NAMES,
    collect_composition_types_ruby,
    record_ruby_local_types,
    scan_class_fields_ruby,
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

    # A3 Slice 5: emit field symbols for Ruby @ivar first-assignment sites.
    if config.SEAM_FIELD_ACCESS_EDGES == "on":
        for qual_name, field_line in collect_field_symbols_ruby(node, name):
            symbols.append(Symbol(
                name=qual_name,
                kind="field",
                file=file_str,
                start_line=field_line,
                end_line=field_line,
                docstring=None,
                signature=None,
                decorators=[],
                is_exported=None,
                visibility=None,
                qualified_name=qual_name,
            ))

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


def _ruby_method_qualified_name(method_node: Node, class_name: str) -> str | None:
    """Return the qualified method name ('ClassName.method') from a Ruby method/singleton_method.

    For 'method' nodes: name is the first 'identifier' child.
    For 'singleton_method' nodes: name is the identifier after the '.' separator.
    Used by the field-access extractor to set source_fn on emitted edges.
    Returns None if the name cannot be determined (graceful skip).
    """
    ntype = method_node.type
    if ntype == "method":
        for child in method_node.children:
            if child.type == "identifier":
                method_name = _text(child)
                if method_name:
                    return f"{class_name}.{method_name}"
    elif ntype == "singleton_method":
        singleton_name = _ruby_singleton_method_name(method_node)
        if singleton_name:
            return f"{class_name}.{singleton_name}"
    return None


def _extract_edges_ruby(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Ruby AST.

    Import heuristic:
        require('x')             → target = 'x'
        require_relative('./x')  → target = basename stem of 'x' (strip dir + .rb)

    Call heuristic:
        call node where 'method' field is identifier → call edge.
        require/require_relative calls are handled as imports — excluded from calls.

    Tier B B5: when SEAM_TYPE_INFERENCE is on, receiver calls are resolved to
    'Type.method' qualified targets using per-function scope (class ivar bindings +
    local variable constructor calls). Ruby has no static type annotations — we infer
    from `var = ClassName.new` patterns.

    NEVER raises. Returns [] on any failure.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    infer = config.SEAM_TYPE_INFERENCE == "on"
    composition_on = config.SEAM_COMPOSITION_EDGES == "on"
    field_access_on = config.SEAM_FIELD_ACCESS_EDGES == "on"

    try:
        _walk_ruby_edges(
            root, file_str, file_stem, edges, infer, composition_on, field_access_on,
            None, {}, {}
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_edges_ruby: unexpected error for %s: %r", filepath, exc)

    return edges


def _walk_ruby_edges(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
    infer: bool,
    composition_on: bool,
    field_access_on: bool,
    class_name: str | None,
    class_fields: dict[str, str],
    var_types: dict[str, str],
) -> None:
    """Recursive walker for Ruby edge extraction.

    Threads class_name + class_fields (ivar types from initialize) + var_types
    (local var types from constructor calls in the current method body).
    composition_on gates holds edge emission (Slice #79).
    field_access_on gates A3 Slice 5 reads/writes edge emission.
    """
    ntype = node.type

    if ntype == "class":
        # Update class context and pre-scan ivar types from initialize.
        new_class: str | None = None
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            new_class = _text(name_node).strip() or None
        # Slice #79: emit holds edges for Ruby class ivar assignments in initialize.
        if composition_on and new_class:
            _handle_ruby_class_holds(node, new_class, file_str, edges)
        new_fields = scan_class_fields_ruby(node) if infer else {}
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk_ruby_edges(
                    child, file_str, file_stem, edges, infer, composition_on, field_access_on,
                    new_class, new_fields, dict(new_fields)
                )
        return

    if ntype in ("method", "singleton_method"):
        # New method scope: inherit class fields (ivars), empty local vars.
        new_types: dict[str, str] = dict(class_fields)
        body = node.child_by_field_name("body")
        if body is not None:
            # A3 Slice 5: emit reads/writes field-access edges for @ivar accesses.
            if field_access_on and class_name is not None:
                source_fn = _ruby_method_qualified_name(node, class_name)
                if source_fn is not None:
                    for src, tgt, mode, line in extract_field_accesses_ruby(
                        body, source_fn, class_name
                    ):
                        edges.append(Edge(
                            source=src,
                            target=tgt,
                            kind=mode,
                            file=file_str,
                            line=line,
                            confidence="EXTRACTED",
                            receiver=None,
                        ))
            for child in body.children:
                _walk_ruby_edges(
                    child, file_str, file_stem, edges, infer, composition_on, field_access_on,
                    class_name, class_fields, new_types
                )
        return

    if infer and ntype == "assignment":
        record_ruby_local_types(node, var_types)
        # Still recurse to catch nested calls.

    if ntype == "call":
        _handle_ruby_call(node, file_str, file_stem, edges, infer, class_name, var_types)
        # Still recurse — calls can be nested

    for child in node.children:
        _walk_ruby_edges(
            child, file_str, file_stem, edges, infer, composition_on, field_access_on,
            class_name, class_fields, var_types
        )


def _handle_ruby_call(
    node: Node,
    file_str: str,
    file_stem: str,
    edges: list[Edge],
    infer: bool,
    class_name: str | None,
    var_types: dict[str, str],
) -> None:
    """Emit import or call edges from a Ruby call node.

    A 'call' node can be either a bare function call (no receiver) or a method
    call on an object (with receiver). We emit edges for both — bare calls get
    receiver=None; receiver calls get the raw receiver text.

    Tier B B5: when infer=True, resolves receiver text to type name → qualified target.
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

    # Tier B B6: Foo.new → instantiates edge.
    # In Ruby, class names are constants (PascalCase) — tree-sitter represents them
    # as 'constant' nodes. A call where method='new' and receiver is a 'constant'
    # node is a constructor call. Lowercase receiver.new (identifier) is NOT a class
    # constructor (e.g. obj.new is unusual Ruby — no instantiates edge emitted).
    if method_name == "new" and receiver_node is not None and receiver_node.type == "constant":
        type_name = _text(receiver_node)
        if type_name:
            source = _find_enclosing_function(node, "ruby")
            if source is not None:
                edges.append(
                    Edge(
                        source=source,
                        target=type_name,
                        kind="instantiates",
                        file=file_str,
                        line=node.start_point[0] + 1,
                        confidence="INFERRED",
                        receiver=None,
                    )
                )
        return  # Do not also emit a call edge for Foo.new

    # Tier B B2 + B5: emit call edges for bare and receiver calls.
    # Receiver calls are resolved to qualified 'Type.method' when type is known.
    recv_text: str | None = None
    if receiver_node is not None:
        recv_text = _text(receiver_node)

    # B5: resolve receiver to type → qualify the target.
    final_target = method_name
    if infer and recv_text is not None:
        resolved_type = resolve_receiver_type_ext(
            recv_text, class_name, var_types, _RUBY_SELF_NAMES
        )
        if resolved_type:
            final_target = f"{resolved_type}.{method_name}"

    source = _find_enclosing_function(node, "ruby")
    if source is not None:
        edges.append(
            Edge(
                source=source,
                target=final_target,
                kind="call",
                file=file_str,
                line=node.start_point[0] + 1,
                confidence="INFERRED",
                receiver=recv_text,
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


def _handle_ruby_class_holds(
    class_node: Node, class_name: str, file_str: str, edges: list[Edge]
) -> None:
    """Emit holds edges for a Ruby class based on initialize ivar assignments.

    Delegates to collect_composition_types_ruby for (held_type, line) pairs and
    emits one Edge per unique pair. Never raises (backstop try/except).
    """
    try:
        for held_type, held_line in collect_composition_types_ruby(class_node):
            edges.append(
                Edge(
                    source=class_name,
                    target=held_type,
                    kind="holds",
                    file=file_str,
                    line=held_line,
                    confidence="INFERRED",
                    receiver=None,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_ruby_class_holds: failed: %r", exc)


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
