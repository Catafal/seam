"""Swift symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common  (leaf — no seam deps)
         ↑
    graph_swift   (this file)
         ↑
    graph.py       (imports this module's public extractors at top level)

WHY separate from graph.py: graph.py would exceed 1000 lines with Swift inside.
Keeping a per-family extractor module follows the Phase 9 precedent.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default.

Verified grammar facts (tree-sitter-swift 0.7.3, tree-sitter 0.25.2):
  - import_declaration → identifier → simple_identifier segments.
  - class_declaration represents class/struct/actor/extension/enum,
    distinguished by keyword child type.
  - protocol_declaration → kind=interface; protocol_function_declaration inside.
  - function_declaration: top-level → function; inside class_body → method.
  - call_expression: bare simple_identifier callee → emit call edge;
    navigation_expression callee → SKIP (obj.m, self.m).
  - Comments: 'comment' node for both // and ///; 'multiline_comment' for /* */.
"""

import logging
from pathlib import Path

from tree_sitter import Node

import seam.config as config

# All shared types, constants, and helpers from the leaf module.
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

# signatures.py is a leaf (no seam deps) so importing it here does not create a cycle.
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)

# ── Doc-comment adjacency helper ───────────────────────────────────────────────


def _swift_doc_comment(decl_node: Node) -> str | None:
    """Capture Swift doc-comment: contiguous /// or /** */ comment lines above a decl.

    Swift uses a single 'comment' node type for both // and ///. Only '///' prefix
    lines qualify as doc-comments. Walk prev_sibling collecting comment nodes
    where the text starts with '///' and rows are adjacent (no blank line gap).

    Adjacency rule: comment end_point[0] + 1 == next_node start_point[0].
    Swift comment nodes do NOT include trailing newline in text (same as Go),
    so end_point[0] is the SAME row as the comment text. Therefore a blank-line
    gap means: end_point[0] + 1 != next_start_point[0].
    """
    lines: list[str] = []
    current = decl_node.prev_sibling

    while current is not None and current.type == "comment":
        raw = _text(current)
        # Only /// (outer doc-comment) qualifies — // does not.
        if not raw.startswith("///"):
            break
        # Adjacency check: no blank line between comment and next declaration.
        next_node = current.next_sibling
        if next_node is not None:
            end_row = current.end_point[0]
            next_start_row = next_node.start_point[0]
            if end_row + 1 != next_start_row:
                break
        # Strip '///' prefix and normalize whitespace.
        body = raw[3:].strip()
        lines.append(body)
        current = current.prev_sibling

    if not lines:
        return None

    # Lines were collected bottom-up; reverse to restore source order.
    return "\n".join(reversed(lines))


# ── Symbol extraction helpers ─────────────────────────────────────────────────


def _swift_class_keyword(decl_node: Node) -> str | None:
    """Return the keyword child type for a class_declaration node.

    Returns 'class', 'struct', 'actor', 'extension', or 'enum' (or None if absent).
    This distinguishes the five forms that all use class_declaration in the grammar.
    """
    for child in decl_node.children:
        if child.type in ("class", "struct", "actor", "extension", "enum"):
            return child.type
    return None


def _swift_extension_type_name(decl_node: Node) -> str | None:
    """Extract the extended type name from an 'extension TypeName { ... }' node.

    The extended type name is the type_identifier inside the first user_type child
    that immediately follows the 'extension' keyword (NOT inside class_body).

    WHY not child_by_field_name('name'): extension nodes have no 'name' field —
    the extended type is stored in a user_type child, not a named field.
    """
    found_ext_kw = False
    for child in decl_node.children:
        if child.type == "extension":
            found_ext_kw = True
            continue
        # The user_type directly after 'extension' holds the extended name.
        if found_ext_kw and child.type == "user_type":
            for gc in child.children:
                if gc.type == "type_identifier":
                    return _text(gc)
        # Stop at class_body — the name was not found before the body.
        if child.type == "class_body":
            break
    return None


def _swift_modifiers(node: Node) -> Node | None:
    """Find the 'modifiers' child of a Swift declaration node, or None."""
    for child in node.children:
        if child.type == "modifiers":
            return child
    return None


def _swift_visibility_and_exported(node: Node) -> tuple[str, bool]:
    """Extract (visibility, is_exported) from a Swift declaration node.

    Scans the modifiers child for visibility_modifier keyword text:
      public / open  → ('public', True)
      private / fileprivate → ('private', False)
      internal (or absent) → ('internal', False)

    WHY 'internal' for absent: Swift's default access level is 'internal'.
    Only public/open actually crosses the module boundary (is_exported=True).
    """
    mods = _swift_modifiers(node)
    if mods is None:
        return ("internal", False)
    for child in mods.children:
        if child.type == "visibility_modifier":
            vis_text = _text(child).strip()
            if vis_text in ("public", "open"):
                return ("public", True)
            if vis_text in ("private", "fileprivate"):
                return ("private", False)
            # 'internal' explicitly written
            return ("internal", False)
    return ("internal", False)


def _swift_attributes(node: Node) -> list[str]:
    """Extract Swift @attribute decorator texts from a declaration node.

    Looks inside the modifiers child for 'attribute' nodes.
    Each attribute is returned as its verbatim text (e.g. '@objc', '@available(iOS 13.0, *)').
    """
    result: list[str] = []
    mods = _swift_modifiers(node)
    if mods is None:
        return result
    try:
        for child in mods.children:
            if child.type == "attribute":
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


def _swift_signature(node: Node) -> str | None:
    """Build a one-line Swift signature from a declaration node.

    Strategy: collect text from all children BEFORE the body (function_body,
    class_body, enum_class_body, protocol_body), join, and normalize to one line.
    Skip modifiers child (those are decorators / visibility, not sig header text)
    except for the visibility_modifier which IS part of the sig (e.g. 'public').

    Covers: function_declaration, class_declaration, protocol_declaration,
    protocol_function_declaration.
    """
    try:
        body_stop_types = frozenset({
            "function_body",
            "class_body",
            "enum_class_body",
            "protocol_body",
        })
        parts: list[str] = []
        for child in node.children:
            if child.type in body_stop_types:
                break
            if child.type == "modifiers":
                # Include only visibility_modifier in signature, skip @attributes.
                for mc in child.children:
                    if mc.type == "visibility_modifier":
                        vis = _text(mc).strip()
                        if vis:
                            parts.append(vis)
                continue
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        raw = " ".join(parts)
        # Collapse whitespace.
        return " ".join(raw.split())
    except Exception:  # noqa: BLE001
        pass
    return None


def _truncate_sig(sig: str | None, max_len: int) -> str | None:
    """Truncate signature to max_len chars, appending '...' if needed."""
    if sig is None or len(sig) <= max_len:
        return sig
    return sig[: max_len - 3] + "..."


# ── Swift symbol extraction ────────────────────────────────────────────────────


def _extract_symbols_swift(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Swift AST and extract function, method, class, struct, enum, protocol symbols.

    Kind mapping (closed vocabulary):
        class_declaration keyword 'class'/'struct'/'actor' → class
        class_declaration keyword 'extension' → class (extended type name as symbol name)
        class_declaration keyword 'enum' (body=enum_class_body) → type
        protocol_declaration → interface
        function_declaration top-level → function
        function_declaration inside class_body → method (qualified as 'Type.method')
        protocol_function_declaration → method (qualified as 'Proto.method')

    Never raises: the outer try/except + logger.debug is the final backstop.
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    try:
        _walk_top_level(root, file_str, symbols)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_extract_symbols_swift: unhandled exception for %s: %r", filepath, exc
        )

    return symbols


def _walk_top_level(root: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Walk top-level children of the source_file node."""
    for child in root.children:
        _visit_top(child, file_str, symbols)


def _visit_top(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Visit a top-level AST node and emit symbols."""
    if node.type == "function_declaration":
        _handle_function(node, file_str, symbols, class_name=None)

    elif node.type == "class_declaration":
        keyword = _swift_class_keyword(node)
        if keyword in ("class", "struct", "actor"):
            _handle_class_like(node, file_str, symbols, keyword)
        elif keyword == "extension":
            _handle_extension(node, file_str, symbols)
        elif keyword == "enum":
            _handle_enum(node, file_str, symbols)

    elif node.type == "protocol_declaration":
        _handle_protocol(node, file_str, symbols)


def _handle_function(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    class_name: str | None,
) -> None:
    """Emit a function or method symbol from a function_declaration node.

    Top-level → kind='function'; inside a class body → kind='method' (qualified).
    Never raises.
    """
    try:
        # Simple name from simple_identifier child (function_declaration has no 'name' field)
        name = None
        for child in node.children:
            if child.type == "simple_identifier":
                name = _text(child)
                break
        if not name:
            return

        if class_name:
            kind = "method"
            qualified = f"{class_name}.{name}"
        else:
            kind = "function"
            qualified = name

        doc = _swift_doc_comment(node)
        fields = extract_node_fields(
            node,
            "swift",
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
    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_function: failed for node at %r: %r", node.start_point, exc)


def _handle_class_like(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    keyword: str,
) -> None:
    """Emit a class/struct/actor symbol and recurse into its body for methods.

    Always kind='class'. The name comes from the type_identifier child.
    Recurses into class_body to find method function_declaration nodes.
    Never raises.
    """
    try:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            # Fallback: find type_identifier directly among children
            for child in node.children:
                if child.type == "type_identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        class_name = _text(name_node)
        doc = _swift_doc_comment(node)
        fields = extract_node_fields(
            node,
            "swift",
            qualified_name=class_name,
            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
        )
        symbols.append(
            _make_symbol(
                class_name,
                "class",
                file_str,
                node,
                doc,
                signature=fields["signature"],
                decorators=fields["decorators"],
                is_exported=fields["is_exported"],
                visibility=fields["visibility"],
                qualified_name=class_name,
            )
        )

        # Recurse into class_body for methods
        body = node.child_by_field_name("body")
        if body is None:
            for child in node.children:
                if child.type == "class_body":
                    body = child
                    break
        if body is not None:
            for child in body.children:
                if child.type == "function_declaration":
                    _handle_function(child, file_str, symbols, class_name=class_name)

    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_class_like: failed at %r: %r", node.start_point, exc)


def _handle_extension(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit an extension symbol (kind='class') and its method symbols.

    The extended type name is the type_identifier inside the user_type child
    that follows the 'extension' keyword. Methods are qualified 'Type.method'.
    Never raises.
    """
    try:
        ext_name = _swift_extension_type_name(node)
        if ext_name is None:
            return

        doc = _swift_doc_comment(node)
        fields = extract_node_fields(
            node,
            "swift",
            qualified_name=ext_name,
            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
        )
        symbols.append(
            _make_symbol(
                ext_name,
                "class",
                file_str,
                node,
                doc,
                signature=fields["signature"],
                decorators=fields["decorators"],
                is_exported=fields["is_exported"],
                visibility=fields["visibility"],
                qualified_name=ext_name,
            )
        )

        # Recurse into class_body for methods
        for child in node.children:
            if child.type == "class_body":
                for gc in child.children:
                    if gc.type == "function_declaration":
                        _handle_function(gc, file_str, symbols, class_name=ext_name)
                break

    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_extension: failed at %r: %r", node.start_point, exc)


def _handle_enum(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit an enum symbol as kind='type'.

    enum Status { ... } → kind='type'. enum cases are NOT emitted (no matching kind).
    Never raises.
    """
    try:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "type_identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        enum_name = _text(name_node)
        doc = _swift_doc_comment(node)
        fields = extract_node_fields(
            node,
            "swift",
            qualified_name=enum_name,
            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
        )
        symbols.append(
            _make_symbol(
                enum_name,
                "type",
                file_str,
                node,
                doc,
                signature=fields["signature"],
                decorators=fields["decorators"],
                is_exported=fields["is_exported"],
                visibility=fields["visibility"],
                qualified_name=enum_name,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_enum: failed at %r: %r", node.start_point, exc)


def _handle_protocol(node: Node, file_str: str, symbols: list[Symbol]) -> None:
    """Emit a protocol symbol as kind='interface' and its method symbols.

    protocol Describable { func describe() -> String } →
        - 'Describable' (kind='interface')
        - 'Describable.describe' (kind='method')

    Protocol methods are 'protocol_function_declaration' nodes in the grammar,
    not 'function_declaration'. Their name comes from a simple_identifier child.
    Never raises.
    """
    try:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "type_identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        proto_name = _text(name_node)
        doc = _swift_doc_comment(node)
        fields = extract_node_fields(
            node,
            "swift",
            qualified_name=proto_name,
            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
        )
        symbols.append(
            _make_symbol(
                proto_name,
                "interface",
                file_str,
                node,
                doc,
                signature=fields["signature"],
                decorators=fields["decorators"],
                is_exported=fields["is_exported"],
                visibility=fields["visibility"],
                qualified_name=proto_name,
            )
        )

        # Recurse into protocol_body for protocol_function_declaration methods
        for child in node.children:
            if child.type == "protocol_body":
                for gc in child.children:
                    if gc.type == "protocol_function_declaration":
                        _handle_protocol_method(gc, file_str, symbols, proto_name)
                break

    except Exception as exc:  # noqa: BLE001
        logger.debug("_handle_protocol: failed at %r: %r", node.start_point, exc)


def _handle_protocol_method(
    node: Node,
    file_str: str,
    symbols: list[Symbol],
    proto_name: str,
) -> None:
    """Emit a protocol method symbol (protocol_function_declaration node).

    Name comes from a simple_identifier child (no 'name' field in this node type).
    Qualified as 'ProtoName.methodName'.
    Never raises.
    """
    try:
        name = None
        for child in node.children:
            if child.type == "simple_identifier":
                name = _text(child)
                break
        if not name:
            return

        qualified = f"{proto_name}.{name}"
        doc = _swift_doc_comment(node)
        fields = extract_node_fields(
            node,
            "swift",
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
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_handle_protocol_method: failed at %r: %r", node.start_point, exc
        )


# ── Swift edge extraction ──────────────────────────────────────────────────────


def _extract_edges_swift(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Swift AST.

    Import heuristic:
        import Foundation         → target = 'Foundation' (sole simple_identifier)
        import UIKit.UIView       → target = 'UIView' (LAST simple_identifier in identifier)

    Call heuristic (MVP — bare identifiers only):
        call_expression where callee is a bare simple_identifier → kind='call'
        call_expression where callee is a navigation_expression (obj.m, self.m) → SKIP

    Never raises — outer try/except wraps the walk.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    try:
        _walk_edges(root, file_str, file_stem, edges)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_extract_edges_swift: unhandled exception for %s: %r", filepath, exc
        )

    return edges


def _walk_edges(node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Recursively walk the AST to collect import and call edges."""
    if node.type == "import_declaration":
        _handle_import(node, file_str, file_stem, edges)
        return  # No need to recurse into import node

    if node.type == "call_expression":
        _handle_call(node, file_str, edges)
        # Still recurse — calls can be nested

    for child in node.children:
        _walk_edges(child, file_str, file_stem, edges)


def _handle_import(
    node: Node, file_str: str, file_stem: str, edges: list[Edge]
) -> None:
    """Extract import edge from a Swift import_declaration node.

    import Foundation       → target = 'Foundation'
    import UIKit.UIView     → target = 'UIView' (last simple_identifier segment)
    """
    line = node.start_point[0] + 1
    # Find the 'identifier' child which contains simple_identifier segments.
    for child in node.children:
        if child.type == "identifier":
            # Collect all simple_identifier children; take the LAST one.
            segments = [
                _text(gc) for gc in child.children if gc.type == "simple_identifier"
            ]
            if segments:
                target = segments[-1]
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
            break


def _handle_call(node: Node, file_str: str, edges: list[Edge]) -> None:
    """Extract a call edge from a call_expression node.

    Only bare simple_identifier callees produce edges. Navigation expressions
    (obj.m, self.m) are skipped — these are member calls handled by the object.
    """
    if not node.children:
        return
    callee = node.children[0]
    if callee.type != "simple_identifier":
        # navigation_expression (obj.m) or other complex callee → skip
        return

    target = _text(callee)
    if not target:
        return

    source = _find_enclosing_function(node, "swift")
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


# ── Swift comment extraction ───────────────────────────────────────────────────


def _extract_comments_swift(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Swift AST and extract semantic comment markers.

    Swift comment node types (verified against tree-sitter-swift 0.7.3):
        'comment'           — // and /// lines (both kinds are the same node type)
        'multiline_comment' — /* */ blocks

    For // and /// nodes: strip the '//' prefix (and any additional slashes),
    then match the marker. For multiline_comment, scan every line with
    _block_comment_lines.

    Never raises — outer try/except wraps the walk.
    """
    comments: list[Comment] = []

    try:
        _walk_comments(root, comments)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_extract_comments_swift: unhandled exception for %s: %r", filepath, exc
        )

    return comments


def _walk_comments(node: Node, comments: list[Comment]) -> None:
    """Recursively collect semantic comment markers from the AST."""
    if node.type == "comment":
        raw = _text(node)
        base_row = node.start_point[0] + 1
        # Strip '//' prefix and any additional slashes (covers // and ///)
        body = raw.lstrip("/").strip()
        result = _match_marker(body)
        if result is not None:
            marker, text = result
            comments.append(Comment(marker=marker, text=text, line=base_row))

    elif node.type == "multiline_comment":
        raw = _text(node)
        base_row = node.start_point[0] + 1
        for offset, body in _block_comment_lines(raw):
            result = _match_marker(body)
            if result is not None:
                marker, text = result
                comments.append(Comment(marker=marker, text=text, line=base_row + offset))

    for child in node.children:
        _walk_comments(child, comments)
