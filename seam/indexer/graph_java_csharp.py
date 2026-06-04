"""Java and C# symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
         ↑
    graph_java_csharp  (this file)
         ↑
    graph.py           (imports this module's public extractors at top level)

WHY split from graph.py: graph.py would exceed 1000 lines if it contained
Java + C# extractors directly. Splitting here (one file per language pair)
keeps all files within limits and maintains the top-level-only import rule.

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default (whole-index resolution
    at read time handles EXTRACTED/AMBIGUOUS/INFERRED for cross-file edges).
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
    _base_type_name,
    _block_comment_lines,
    _find_enclosing_function,
    _make_symbol,
    _match_marker,
    _text,
)

# signatures.py is a leaf (no seam deps) so importing here does not create a cycle.
from seam.indexer.signatures import extract_node_fields

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _node_name(node: Node) -> str | None:
    """Return text of the 'name' field child, or None if absent."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return _text(name_node)


def _java_javadoc(decl_node: Node) -> str | None:
    """Capture the nearest Javadoc comment for a Java declaration.

    Walks prev_sibling backwards, skipping intervening line_comment nodes
    (e.g. // HACK: ... between /** */ and the class), to find the nearest
    block_comment that starts with '/**'.

    WHY skip line_comments: in the fixture pattern
        /** Javadoc */
        // HACK: ... (semantic comment — not the docstring)
        @SomeAnnotation
        public class Foo { }
    the line_comment separates the Javadoc from the declaration but should NOT
    prevent the Javadoc from being attached. Annotations are folded into modifiers
    inside the declaration, so they don't appear as prev_siblings.

    Stops at non-comment nodes other than line_comment (e.g. another declaration).
    """
    try:
        current = decl_node.prev_sibling
        while current is not None:
            if current.type == "block_comment":
                raw = _text(current)
                if raw.startswith("/**"):
                    return _clean_block_comment(raw)
                # Non-Javadoc block comment — stop searching.
                return None
            elif current.type == "line_comment":
                # Skip over line comments (e.g. // HACK: ...) to reach the /** block.
                current = current.prev_sibling
                continue
            else:
                # Hit another declaration or non-comment node — stop.
                return None
        return None
    except Exception:  # noqa: BLE001
        return None


def _clean_block_comment(raw: str) -> str | None:
    """Clean a /** ... */ block comment into a plain docstring.

    Removes /** and */ delimiters and leading ' * ' decoration from each line.
    Returns the joined text, or None if the result is empty.
    """
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        # Remove opening /**, closing */, and leading * decoration.
        if stripped.startswith("/**"):
            stripped = stripped[3:]
        elif stripped.startswith("*/"):
            stripped = stripped[2:]
        elif stripped.startswith("*"):
            stripped = stripped[1:]
        stripped = stripped.strip()
        if stripped:
            lines.append(stripped)
    result = "\n".join(lines)
    return result if result else None


def _csharp_doc_comment(decl_node: Node) -> str | None:
    """Capture C# XML doc-comment: the nearest contiguous block of /// lines above the declaration.

    In the C# grammar all comments (///, //, /* */) appear as 'comment' nodes.
    Walk prev_sibling backwards:
      - Skip non-/// 'comment' nodes (e.g. // HACK: ...) to find the nearest
        contiguous block of /// lines.
      - Collect contiguous /// lines into the docstring.
      - Stop at non-comment nodes or when a gap (non-/// comment) breaks the block.

    WHY: The fixture pattern is:
        /// <summary>...</summary>
        /// WHY: ...
        // HACK: ...           ← semantic comment, not docstring
        [Serializable]         ← folded into declaration node
        public class DataStore { }

    The // HACK line separates the /// block from the declaration but should not prevent
    the /// block from being captured as the docstring.

    Joins lines in source order, stripping '///' and whitespace from each.
    Returns None if no /// block is found.
    """
    try:
        lines: list[str] = []
        current = decl_node.prev_sibling

        # Phase 1: skip any leading non-/// comments (e.g. // HACK: ...) to find
        # the tail of the /// block.
        while current is not None and current.type == "comment":
            raw = _text(current)
            if raw.startswith("///"):
                break  # found the start of the /// block tail
            # Non-/// comment (// or /* */) — skip over it.
            current = current.prev_sibling

        # Phase 2: collect contiguous /// lines.
        while current is not None and current.type == "comment":
            raw = _text(current)
            if not raw.startswith("///"):
                break  # gap in /// block — stop
            body = raw[3:].strip()
            lines.append(body)
            current = current.prev_sibling

        if not lines:
            return None
        # Collected bottom-up; reverse to restore source order.
        return "\n".join(reversed(lines))
    except Exception:  # noqa: BLE001
        return None


def _java_visibility_from_modifiers(mods_node: Node | None) -> str | None:
    """Extract visibility from a Java modifiers node.

    Scans children for 'public', 'private', 'protected' keyword nodes.
    Returns None if no access modifier is present (package-private).
    """
    if mods_node is None:
        return None
    try:
        for child in mods_node.children:
            if child.type in ("public", "private", "protected"):
                return child.type
    except Exception:  # noqa: BLE001
        pass
    return None


def _java_annotations_from_modifiers(mods_node: Node | None) -> list[str]:
    """Extract Java annotations (@Service, @Override, etc.) from a modifiers node.

    Returns verbatim annotation text for each marker_annotation and annotation child.
    """
    result: list[str] = []
    if mods_node is None:
        return result
    try:
        for child in mods_node.children:
            if child.type in ("marker_annotation", "annotation"):
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


def _csharp_modifier(decl_node: Node, modifier_type: str = "modifier") -> str | None:
    """Find a modifier string ('public'/'private'/'protected'/'internal') from a C# declaration.

    In the C# grammar, modifiers are sibling 'modifier' nodes inside the declaration.
    Returns the first matched modifier text, or None.
    """
    try:
        for child in decl_node.children:
            if child.type == modifier_type:
                text = _text(child).strip()
                if text in ("public", "private", "protected", "internal"):
                    return text
    except Exception:  # noqa: BLE001
        pass
    return None


def _csharp_attributes(decl_node: Node) -> list[str]:
    """Extract C# attribute list texts ([Serializable], [HttpGet], etc.) from a declaration.

    Attribute lists appear as 'attribute_list' children in the declaration node.
    Returns verbatim '[Attr]' strings for each attribute_list found.
    """
    result: list[str] = []
    try:
        for child in decl_node.children:
            if child.type == "attribute_list":
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


# ── Java extraction ────────────────────────────────────────────────────────────


def _extract_symbols_java(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Java AST and extract class, interface, enum, record, and method symbols.

    Kind mapping (Phase 9 spec, closed vocabulary):
        class_declaration       → class
        interface_declaration   → interface
        enum_declaration        → type
        record_declaration      → class
        method_declaration      (inside class) → method (qualified as 'Class.method')
        constructor_declaration (inside class) → method (qualified as 'Class.ClassName')

    WHY top-level-only walk for type declarations: Java allows multiple top-level types
    but methods/constructors are always inside a type body.
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, class_name: str | None = None) -> None:
        """Recursively walk the AST, tracking class context for method qualification."""
        try:
            ntype = node.type

            if ntype == "class_declaration":
                name = _node_name(node)
                if name:
                    doc = _java_javadoc(node)
                    fields = extract_node_fields(
                        node,
                        "java",
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
                    # Recurse into class body with class context set.
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for child in body.named_children:
                            _walk(child, class_name=name)

            elif ntype == "interface_declaration":
                name = _node_name(node)
                if name:
                    doc = _java_javadoc(node)
                    fields = extract_node_fields(
                        node,
                        "java",
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
                    # Recurse into interface body (to capture interface methods too).
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for child in body.named_children:
                            _walk(child, class_name=name)

            elif ntype == "enum_declaration":
                name = _node_name(node)
                if name:
                    doc = _java_javadoc(node)
                    fields = extract_node_fields(
                        node,
                        "java",
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
                    # WHY recurse: enums can have methods in their body (enum_body_declarations).
                    # Before this fix the branch returned here, silently dropping enum methods
                    # like 'EntityStatus.label'. Mirror the interface/class recursion pattern.
                    # The enum body node is 'enum_body'; methods live in 'enum_body_declarations'.
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for body_child in body.named_children:
                            # enum_body_declarations holds the method nodes
                            if body_child.type == "enum_body_declarations":
                                for decl in body_child.named_children:
                                    _walk(decl, class_name=name)

            elif ntype == "record_declaration":
                name = _node_name(node)
                if name:
                    doc = _java_javadoc(node)
                    fields = extract_node_fields(
                        node,
                        "java",
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

            elif (
                ntype in ("method_declaration", "constructor_declaration")
                and class_name is not None
            ):
                name = _node_name(node)
                if name:
                    qualified = f"{class_name}.{name}"
                    doc = _java_javadoc(node)
                    fields = extract_node_fields(
                        node,
                        "java",
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

            else:
                # Traverse into other node types — picks up nested classes etc.
                for child in node.named_children:
                    _walk(child, class_name)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_symbols_java: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.named_children:
        _walk(child)

    return symbols


def _extract_edges_java(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Java AST.

    Import heuristic:
        import java.util.List;  → target = 'List' (last segment of scoped_identifier)
        import static java.lang.Math.abs; → target = 'abs' (same rule)

    Call heuristic (MVP — bare identifiers only):
        method_invocation where there is no 'object' field → bare call → target = name text.
        Selector/member calls (this.foo(), obj.bar()) have an 'object' field → skipped.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"

    def _walk(node: Node) -> None:
        try:
            ntype = node.type

            if emit_inheritance and ntype in (
                "class_declaration",
                "interface_declaration",
            ):
                _handle_java_inheritance(node, file_str, edges)
                # Fall through to recurse into the body for call edges.

            if ntype == "import_declaration":
                _handle_java_import(node, file_str, file_stem, edges)
                return  # No need to recurse into import declarations.

            elif ntype == "method_invocation":
                # Bare-identifier call: no 'object' field (not a member call).
                obj = node.child_by_field_name("object")
                name_node = node.child_by_field_name("name")
                if obj is None and name_node is not None:
                    source = _find_enclosing_function(node, "java")
                    if source is not None:
                        edges.append(
                            Edge(
                                source=source,
                                target=_text(name_node),
                                kind="call",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                            )
                        )

            for child in node.children:
                _walk(child)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_edges_java: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.children:
        _walk(child)

    return edges


def _handle_java_inheritance(decl_node: Node, file_str: str, edges: list[Edge]) -> None:
    """Emit extends/implements edges from a Java class or interface declaration.

    class_declaration:
        superclass        (extends Base)          → 'extends' edge subclass→Base
        super_interfaces  (implements I, J)       → 'implements' edges per type
    interface_declaration:
        extends_interfaces (extends I, J)         → 'extends' edges (interface inheritance)

    Each base name is normalized to a bare type name (generic args stripped).
    String-name-keyed: source=this type's name, target=base name. Never raises.
    """
    name_node = decl_node.child_by_field_name("name")
    if name_node is None:
        return
    src_name = _text(name_node)
    line = decl_node.start_point[0] + 1

    def _emit(type_node: Node, kind: str) -> None:
        target = _base_type_name(type_node)
        if target:
            edges.append(
                Edge(
                    source=src_name,
                    target=target,
                    kind=kind,
                    file=file_str,
                    line=line,
                    confidence="INFERRED",
                )
            )

    for child in decl_node.children:
        ctype = child.type
        if ctype == "superclass":
            # superclass → [extends, type_identifier|generic_type]
            for sub in child.named_children:
                _emit(sub, "extends")
        elif ctype == "super_interfaces":
            # super_interfaces → type_list → type_identifier+
            for tl in child.named_children:
                if tl.type == "type_list":
                    for t in tl.named_children:
                        _emit(t, "implements")
        elif ctype == "extends_interfaces":
            # interface inheritance → type_list → type_identifier+ ('extends')
            for tl in child.named_children:
                if tl.type == "type_list":
                    for t in tl.named_children:
                        _emit(t, "extends")


def _handle_java_import(decl_node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Emit an import edge from a Java import_declaration node.

    Extracts the last segment of the qualified name:
        import java.util.List  → 'List'  (scoped_identifier.name field)
        import java.util.*     → skipped (asterisk — no single target)

    WHY pre-scan: in `import java.util.*;` the tree is:
        import_declaration → [scoped_identifier('java.util'), '.', asterisk, ';']
    Iterating named_children left-to-right processes 'java.util' BEFORE the
    asterisk, which would emit 'util' as a spurious target. Pre-scan prevents this.
    """
    line = decl_node.start_point[0] + 1
    # Pre-scan all children (not just named) for an asterisk node.
    # If found, this is a wildcard import — skip it entirely.
    if any(child.type == "asterisk" for child in decl_node.children):
        return  # Wildcard import — no target to record.

    # Non-wildcard: extract the last segment of the qualified name.
    for child in decl_node.named_children:
        target = _java_import_last_segment(child)
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


def _java_import_last_segment(node: Node) -> str | None:
    """Extract the rightmost segment from a Java scoped_identifier or identifier.

    scoped_identifier has a 'name' field pointing to the rightmost identifier.
    identifier is already the last segment.
    """
    if node.type == "identifier":
        return _text(node)
    if node.type == "scoped_identifier":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _text(name_node)
    return None


def _extract_comments_java(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Java AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    Java comment node types:
        line_comment   — //  single-line comments
        block_comment  — /* */ and /** */ (Javadoc)

    All comment nodes are scanned. Block comments use _block_comment_lines to split
    into per-line bodies. Line comments strip the '//' prefix before matching.
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        try:
            if node.type == "line_comment":
                raw = _text(node)
                base_row = node.start_point[0] + 1
                # Strip // prefix and any leading whitespace.
                body = raw.lstrip("/").strip()
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row))

            elif node.type == "block_comment":
                raw = _text(node)
                base_row = node.start_point[0] + 1
                for offset, body in _block_comment_lines(raw):
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row + offset))

            for child in node.children:
                _walk(child)

        except Exception:  # noqa: BLE001
            pass

    for child in root.children:
        _walk(child)

    return comments


# ── C# extraction ──────────────────────────────────────────────────────────────


def _extract_symbols_csharp(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a C# AST and extract class, struct, record, interface, enum, delegate, and method symbols.

    Kind mapping (Phase 9 spec, closed vocabulary):
        class_declaration / struct_declaration / record_declaration → class
        interface_declaration                                       → interface
        enum_declaration / delegate_declaration                     → type
        method_declaration / constructor_declaration (inside type) → method (qualified)

    namespace_declaration and file_scoped_namespace_declaration are TRAVERSED but
    NOT emitted as symbols (per spec — namespace is a container, not a symbol).
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk_body(body_node: Node, class_name: str | None = None) -> None:
        """Walk declaration_list children, tracking class context."""
        for child in body_node.named_children:
            _walk(child, class_name)

    def _walk(node: Node, class_name: str | None = None) -> None:
        """Recursively walk the AST; class_name tracks the enclosing type."""
        try:
            ntype = node.type

            if ntype in ("namespace_declaration", "file_scoped_namespace_declaration"):
                # Traverse namespace body without emitting a symbol for the namespace.
                body = node.child_by_field_name("body")
                if body is not None:
                    _walk_body(body)
                else:
                    # file-scoped namespace: remaining siblings in the compilation unit
                    # are the namespace members; they'll be hit by the top-level walk.
                    pass

            elif ntype in ("class_declaration", "struct_declaration", "record_declaration"):
                name = _node_name(node)
                if name:
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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
                    # Recurse into body with class context.
                    body = node.child_by_field_name("body")
                    if body is not None:
                        _walk_body(body, class_name=name)

            elif ntype == "interface_declaration":
                name = _node_name(node)
                if name:
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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
                    body = node.child_by_field_name("body")
                    if body is not None:
                        _walk_body(body, class_name=name)

            elif ntype in ("enum_declaration", "delegate_declaration"):
                name = _node_name(node)
                if name:
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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

            elif (
                ntype in ("method_declaration", "constructor_declaration")
                and class_name is not None
            ):
                name = _node_name(node)
                if name:
                    qualified = f"{class_name}.{name}"
                    doc = _csharp_doc_comment(node)
                    fields = extract_node_fields(
                        node,
                        "csharp",
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

            else:
                # Traverse into other nodes (e.g. compilation_unit top-level).
                for child in node.named_children:
                    _walk(child, class_name)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_symbols_csharp: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.named_children:
        _walk(child)

    return symbols


def _extract_edges_csharp(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a C# AST.

    Import heuristic:
        using System;                    → target = 'System'   (identifier)
        using System.Collections.Generic → target = 'Generic'  (qualified_name.name)

    Call heuristic (MVP — bare identifiers only):
        invocation_expression where 'function' field is an identifier → bare call.
        If 'function' is a member_access_expression → selector call → skipped.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"

    def _walk(node: Node) -> None:
        try:
            ntype = node.type

            if emit_inheritance and ntype in (
                "class_declaration",
                "struct_declaration",
                "interface_declaration",
                "record_declaration",
            ):
                _handle_csharp_inheritance(node, file_str, edges)
                # Fall through to recurse into the body for call edges.

            if ntype == "using_directive":
                _handle_csharp_using(node, file_str, file_stem, edges)
                return  # No need to recurse into using directives.

            elif ntype == "invocation_expression":
                func_node = node.child_by_field_name("function")
                if func_node is not None and func_node.type == "identifier":
                    # Bare identifier call (not a member access).
                    source = _find_enclosing_function(node, "csharp")
                    if source is not None:
                        edges.append(
                            Edge(
                                source=source,
                                target=_text(func_node),
                                kind="call",
                                file=file_str,
                                line=node.start_point[0] + 1,
                                confidence="INFERRED",
                            )
                        )

            for child in node.children:
                _walk(child)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_edges_csharp: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.children:
        _walk(child)

    return edges


def _handle_csharp_inheritance(decl_node: Node, file_str: str, edges: list[Edge]) -> None:
    """Emit extends edges from a C# type declaration's base_list.

    C# does NOT syntactically distinguish a base class from implemented interfaces
    (both live in `base_list` after the colon), so all base entries are emitted as
    'extends' edges — the same string-name-keyed upstream traversal surfaces both
    subclasses and interface implementers, which is the P6a goal.

    Each base name is normalized to a bare type name (qualified Ns.Base → Base;
    generic IFace<T> → IFace). String-name-keyed: source=type name, target=base.
    Never raises.
    """
    name_node = decl_node.child_by_field_name("name")
    if name_node is None:
        return
    src_name = _text(name_node)
    line = decl_node.start_point[0] + 1

    for child in decl_node.children:
        if child.type == "base_list":
            for base in child.named_children:
                target = _base_type_name(base)
                if target:
                    edges.append(
                        Edge(
                            source=src_name,
                            target=target,
                            kind="extends",
                            file=file_str,
                            line=line,
                            confidence="INFERRED",
                        )
                    )


def _handle_csharp_using(
    using_node: Node, file_str: str, file_stem: str, edges: list[Edge]
) -> None:
    """Emit an import edge from a C# using_directive node.

    Extracts the last segment of the namespace:
        using System                     → 'System'   (identifier)
        using System.Collections.Generic → 'Generic'  (qualified_name, name field)
        using Foo = System.Collections.Generic → 'Generic' (alias form, skip alias identifier)

    WHY alias detection: in `using Foo = System.Collections.Generic;` the grammar produces:
        using_directive → [using, identifier('Foo'), '=', qualified_name('System...Generic'), ';']
    Without alias detection, named_children iteration hits identifier('Foo') first and emits
    'Foo' as the target — a spurious alias edge.
    Fix: if both an identifier AND a qualified_name are present → alias form.
    Emit only the qualified_name's last segment; skip the alias identifier.
    """
    line = using_node.start_point[0] + 1
    named = using_node.named_children
    # Detect alias form: identifier + qualified_name siblings → `using Alias = Namespace`.
    # Only the qualified_name carries the actual namespace; the identifier is the alias.
    has_identifier = any(c.type == "identifier" for c in named)
    has_qualified = any(c.type == "qualified_name" for c in named)
    if has_identifier and has_qualified:
        # Alias form: emit only the qualified_name segment, not the alias.
        for child in named:
            if child.type == "qualified_name":
                target = _csharp_using_last_segment(child)
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
        return

    # Non-alias form: emit the last segment of whichever name node is present.
    for child in named:
        target = _csharp_using_last_segment(child)
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


def _csharp_using_last_segment(node: Node) -> str | None:
    """Extract the rightmost segment from a C# using_directive's name node.

    identifier         → the text directly
    qualified_name     → the 'name' field (rightmost segment)
    """
    if node.type == "identifier":
        return _text(node)
    if node.type == "qualified_name":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _text(name_node)
    return None


def _extract_comments_csharp(root: Node, filepath: Path) -> list[Comment]:
    """Walk a C# AST and extract semantic comment markers (WHY/HACK/NOTE/TODO/FIXME).

    C# comment node types:
        'comment' — covers //, ///, and /* */ comments (all under the same node type).

    For each comment node, the raw text is inspected to determine the kind:
      - Starts with '///' → XML doc comment; strip '///' prefix.
      - Starts with '//' → plain line comment; strip '//' prefix.
      - Starts with '/*' → block comment; use _block_comment_lines.
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        try:
            if node.type == "comment":
                raw = _text(node)
                base_row = node.start_point[0] + 1

                if raw.startswith("/*"):
                    # Block comment: scan line-by-line.
                    for offset, body in _block_comment_lines(raw):
                        result = _match_marker(body)
                        if result is not None:
                            marker, text = result
                            comments.append(
                                Comment(marker=marker, text=text, line=base_row + offset)
                            )
                elif raw.startswith("///"):
                    # XML doc comment: strip '///' and check for marker.
                    body = raw[3:].strip()
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row))
                elif raw.startswith("//"):
                    # Plain line comment: strip '//' and check for marker.
                    body = raw[2:].strip()
                    result = _match_marker(body)
                    if result is not None:
                        marker, text = result
                        comments.append(Comment(marker=marker, text=text, line=base_row))

            for child in node.children:
                _walk(child)

        except Exception:  # noqa: BLE001
            pass

    for child in root.children:
        _walk(child)

    return comments
