"""Java symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) and graph_scope_infer_ext[2] (leaf) — never from graph.py.

LAYERING:
    graph_common       (leaf — no seam deps)
         ↑
    graph_java         (this file)
         ↑
    graph_java_csharp  (thin re-exporter; graph.py imports from there)

WHY split from graph_java_csharp.py: graph_java_csharp.py exceeded 1000 lines after Tier B
additions. Java and C# are now each large enough to stand alone. Same precedent as
graph_go.py / graph_rust.py (split from graph_go_rust.py).

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
from seam.indexer.graph_scope_infer_ext import resolve_receiver_type_ext
from seam.indexer.graph_scope_infer_ext2 import (
    _JAVA_SELF_NAMES,
    record_java_local_types,
    record_java_param_types,
    scan_class_fields_java,
)
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
                    # WHY recurse: enums can have methods in their body.
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for body_child in body.named_children:
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

    Call heuristic:
        method_invocation where 'object' field is absent → bare call → target = name text.
        method_invocation where 'object' field is set → obj.method() → capture receiver.

    Tier B B5: when SEAM_TYPE_INFERENCE is on, receiver calls are resolved to
    'Type.method' qualified targets using a per-function scope map (class fields +
    params + local variable declarations).
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem
    emit_inheritance = config.SEAM_INHERITANCE_EDGES == "on"
    infer = config.SEAM_TYPE_INFERENCE == "on"

    def _walk(
        node: Node,
        class_name: str | None,
        class_fields: dict[str, str],
        var_types: dict[str, str],
    ) -> None:
        try:
            ntype = node.type

            if emit_inheritance and ntype in (
                "class_declaration",
                "interface_declaration",
            ):
                _handle_java_inheritance(node, file_str, edges)

            if ntype == "import_declaration":
                _handle_java_import(node, file_str, file_stem, edges)
                return

            if ntype == "class_declaration":
                new_class_name: str | None = None
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    new_class_name = _text(name_node).strip() or None
                new_fields: dict[str, str] = scan_class_fields_java(node) if infer else {}
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.named_children:
                        _walk(child, new_class_name, new_fields, dict(new_fields))
                return

            if ntype in ("method_declaration", "constructor_declaration"):
                new_types: dict[str, str] = dict(class_fields)
                if infer:
                    record_java_param_types(node, new_types)
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.named_children:
                        _walk(child, class_name, class_fields, new_types)
                return

            if infer and ntype == "local_variable_declaration":
                record_java_local_types(node, var_types)

            # Tier B B6: object_creation_expression (new Foo()) → instantiates edge.
            elif ntype == "object_creation_expression":
                type_node = node.child_by_field_name("type")
                if type_node is None:
                    type_node = next(
                        (c for c in node.children if c.type == "type_identifier"), None
                    )
                if type_node is not None:
                    type_name = _text(type_node)
                    if type_name:
                        source = _find_enclosing_function(node, "java")
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
                for child in node.children:
                    _walk(child, class_name, class_fields, var_types)
                return

            elif ntype == "method_invocation":
                obj = node.child_by_field_name("object")
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    recv_text: str | None = _text(obj) if obj is not None else None
                    final_target = _text(name_node)
                    if infer and recv_text is not None:
                        resolved_type = resolve_receiver_type_ext(
                            recv_text, class_name, var_types, _JAVA_SELF_NAMES
                        )
                        if resolved_type:
                            final_target = f"{resolved_type}.{_text(name_node)}"
                    source = _find_enclosing_function(node, "java")
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

            for child in node.children:
                _walk(child, class_name, class_fields, var_types)

        except Exception:  # noqa: BLE001
            logger.debug(
                "_extract_edges_java: unhandled exception for node.type=%r file=%s",
                node.type,
                file_str,
            )

    for child in root.children:
        _walk(child, None, {}, {})

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
                    receiver=None,
                )
            )

    for child in decl_node.children:
        ctype = child.type
        if ctype == "superclass":
            for sub in child.named_children:
                _emit(sub, "extends")
        elif ctype == "super_interfaces":
            for tl in child.named_children:
                if tl.type == "type_list":
                    for t in tl.named_children:
                        _emit(t, "implements")
        elif ctype == "extends_interfaces":
            for tl in child.named_children:
                if tl.type == "type_list":
                    for t in tl.named_children:
                        _emit(t, "extends")


def _handle_java_import(decl_node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Emit an import edge from a Java import_declaration node.

    Extracts the last segment of the qualified name:
        import java.util.List  → 'List'  (scoped_identifier.name field)
        import java.util.*     → skipped (asterisk — no single target)
    """
    line = decl_node.start_point[0] + 1
    if any(child.type == "asterisk" for child in decl_node.children):
        return  # Wildcard import — no target to record.

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
                    receiver=None,
                )
            )


def _java_import_last_segment(node: Node) -> str | None:
    """Extract the rightmost segment from a Java scoped_identifier or identifier."""
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
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        try:
            if node.type == "line_comment":
                raw = _text(node)
                base_row = node.start_point[0] + 1
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
