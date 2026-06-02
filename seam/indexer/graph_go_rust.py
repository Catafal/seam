"""Go and Rust symbol, edge, and comment extraction from tree-sitter ASTs.

LAYER: imports from graph_common (leaf) only — never from graph.py.

LAYERING:
    graph_common  (leaf — no seam deps)
         ↑
    graph_go_rust  (this file)
         ↑
    graph.py       (imports this module's public extractors at top level)

WHY split from graph.py: graph.py would exceed 1000 lines if it contained
the Go + Rust extractors directly. Splitting here keeps both files within limits
and avoids the deferred-import workaround (all imports are at file top).

All extractor functions follow the same contract:
  - Accept a tree-sitter Node + filepath.
  - Return a list — never raise, never return None.
  - Edges carry confidence='INFERRED' by default (same-file confidence resolution
    is performed by the caller in graph.extract_edges).
"""

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
    _go_recv_type_name,
    _make_symbol,
    _match_marker,
    _node_name,
    _rust_impl_type_name,
    _text,
)

# Phase 4: node-field extractor (leaf module — no seam deps other than tree_sitter).
from seam.indexer.signatures import extract_node_fields

# ── Doc-comment adjacency helpers ──────────────────────────────────────────────


def _go_doc_comment(decl_node: Node) -> str | None:
    """Capture a Go doc-comment: contiguous // lines immediately above the decl.

    Walk prev_sibling collecting 'comment' nodes where each successive pair is
    row-adjacent (no blank line gap). Stops at first non-comment or gap.
    Joins lines in source order, stripping '//' prefix from each.
    """
    lines: list[str] = []
    current = decl_node.prev_sibling

    while current is not None and current.type == "comment":
        raw = _text(current)
        # Only // line comments count as Go doc comments.
        if not raw.startswith("//"):
            break
        # Go comment nodes do NOT include trailing newline, so adjacency rule:
        # comment's end_row + 1 == next_node's start_row.
        next_node = current.next_sibling
        if next_node is not None:
            end_row = current.end_point[0]
            next_start_row = next_node.start_point[0]
            if end_row + 1 != next_start_row:
                break
        body = raw[2:].strip()
        lines.append(body)
        current = current.prev_sibling

    if not lines:
        return None

    # Lines were collected bottom-up; reverse to restore source order.
    return "\n".join(reversed(lines))


def _rust_doc_comment(decl_node: Node) -> str | None:
    """Capture a Rust doc-comment: contiguous '///' line_comment nodes above item.

    Only outer doc-comments ('///') qualify — '//' (plain) and '//!' (inner/module-level)
    are excluded from docstrings (though //! may still match as a semantic marker).

    Adjacency quirk: Rust line_comment nodes include the trailing newline in their
    text, so end_point[0] reports the NEXT physical row (one row past the comment's
    visible last line). Go comments do NOT include the trailing newline.

    Correct adjacency rule for Rust:
        visible_end_row = end_point[0] - (1 if raw_text.endswith("\\n") else 0)
        adjacent        = next_node.start_point[0] == visible_end_row + 1

    Effect:
        "/// doc\\nfn foo() {}"    → docstring = 'doc'   (adjacent, attached)
        "/// orphan\\n\\nfn bar()" → docstring = None    (blank line = gap, not attached)
    """
    lines: list[str] = []
    current = decl_node.prev_sibling

    while current is not None and current.type == "line_comment":
        raw = _text(current)
        # Only /// (outer doc) qualifies; // and //! do not.
        if not raw.startswith("///"):
            break
        # Adjacency check: subtract the trailing-newline inflation before comparing.
        # Rust line_comment text includes '\n', making end_point[0] one row too high.
        next_node = current.next_sibling
        if next_node is not None:
            visible_end_row = current.end_point[0] - (1 if raw.endswith("\n") else 0)
            if next_node.start_point[0] != visible_end_row + 1:
                break  # blank line (or non-adjacent node) — stop collecting
        # Strip '///' prefix and normalize whitespace.
        body = raw[3:].strip()
        lines.append(body)
        current = current.prev_sibling

    if not lines:
        return None

    return "\n".join(reversed(lines))


# ── Go extraction ──────────────────────────────────────────────────────────────


def _extract_symbols_go(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Go AST and extract function, method, struct, interface, and type symbols.

    Kind mapping (per spec, existing 5 kinds only):
        function_declaration  → function
        method_declaration    → method  (qualified as 'Recv.Name', *T normalized to T,
                                         generic receivers Repo[T] → Repo)
        type_spec struct_type → class
        type_spec interface_type → interface
        type_spec other       → type
        type_alias            → type
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node) -> None:
        if node.type == "function_declaration":
            name = _node_name(node)
            if name:
                doc = _go_doc_comment(node)
                # Phase 4: extract enrichment fields.
                fields = extract_node_fields(
                    node, "go", qualified_name=name, max_signature_len=config.SEAM_MAX_SIGNATURE_LEN
                )
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

        elif node.type == "method_declaration":
            method_name = _node_name(node)
            recv_name = _go_recv_type_name(node)
            if method_name and recv_name:
                qualified = f"{recv_name}.{method_name}"
                doc = _go_doc_comment(node)
                # Phase 4: pass node — Go method export is based on method name capitalization.
                fields = extract_node_fields(
                    node,
                    "go",
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
            elif method_name:
                # Receiver parse failed — emit as plain function to avoid silent drop.
                doc = _go_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "go",
                    qualified_name=method_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        method_name,
                        "function",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=method_name,
                    )
                )

        elif node.type == "type_declaration":
            for child in node.named_children:
                if child.type == "type_spec":
                    _handle_go_type_spec(child, node, file_str, symbols)
                elif child.type == "type_alias":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        type_name = _text(name_node)
                        doc = _go_doc_comment(node)
                        # Phase 4: pass the type_declaration node.
                        fields = extract_node_fields(
                            node,
                            "go",
                            qualified_name=type_name,
                            max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                        )
                        symbols.append(
                            _make_symbol(
                                type_name,
                                "type",
                                file_str,
                                node,
                                doc,
                                signature=fields["signature"],
                                decorators=fields["decorators"],
                                is_exported=fields["is_exported"],
                                visibility=fields["visibility"],
                                qualified_name=type_name,
                            )
                        )

        else:
            for child in node.children:
                _walk(child)

    for child in root.children:
        _walk(child)

    return symbols


def _handle_go_type_spec(
    type_spec: Node, decl_node: Node, file_str: str, symbols: list[Symbol]
) -> None:
    """Classify a Go type_spec node and append the appropriate symbol.

    The doc-comment lives above the parent type_declaration (decl_node).
    Phase 4: extract enrichment fields from the parent type_declaration node.
    """
    name_node = type_spec.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node)

    type_node = type_spec.child_by_field_name("type")
    doc = _go_doc_comment(decl_node)

    # Phase 4: extract enrichment fields from the decl_node (type_declaration).
    fields = extract_node_fields(
        decl_node, "go", qualified_name=name, max_signature_len=config.SEAM_MAX_SIGNATURE_LEN
    )

    if type_node is None:
        kind = "type"
    elif type_node.type == "struct_type":
        kind = "class"
    elif type_node.type == "interface_type":
        kind = "interface"
    else:
        kind = "type"

    symbols.append(
        _make_symbol(
            name,
            kind,
            file_str,
            decl_node,
            doc,
            signature=fields["signature"],
            decorators=fields["decorators"],
            is_exported=fields["is_exported"],
            visibility=fields["visibility"],
            qualified_name=name,
        )
    )


def _extract_edges_go(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Go AST.

    Import heuristic:
        import "pkg/path"         → target = last path segment ('path')
        import ( "pkg/path" ... ) → one edge per import_spec

    Call heuristic (MVP — bare identifiers only):
        call_expression where 'function' field is an identifier → target = identifier
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    def _walk(node: Node) -> None:
        if node.type == "import_declaration":
            _handle_go_import(node, file_str, file_stem, edges)
            return  # handled inside; no need to recurse

        elif node.type == "call_expression":
            func_child = node.child_by_field_name("function")
            if func_child and func_child.type == "identifier":
                source = _find_enclosing_function(node, "go")
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


def _handle_go_import(decl_node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Extract import edges from a Go import_declaration node.

    Handles both single imports (import "pkg") and grouped imports.
    Target is the last path segment (e.g. "path/filepath" → "filepath").
    """
    line = decl_node.start_point[0] + 1

    def _emit_from_spec(spec: Node) -> None:
        path_node = spec.child_by_field_name("path")
        if path_node is None:
            return
        content_node = next(
            (c for c in path_node.named_children if c.type == "interpreted_string_literal_content"),
            None,
        )
        if content_node is None:
            return
        path_str = _text(content_node)
        target = path_str.split("/")[-1] if path_str else ""
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

    for child in decl_node.children:
        if child.type == "import_spec":
            _emit_from_spec(child)
        elif child.type == "import_spec_list":
            for spec in child.children:
                if spec.type == "import_spec":
                    _emit_from_spec(spec)


def _extract_comments_go(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Go AST and extract semantic comment markers.

    Go has a single 'comment' node type for both // and /* */ comments.
    For block comments, every line is scanned using _block_comment_lines.
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
                body = raw.lstrip("/").strip()
                result = _match_marker(body)
                if result is not None:
                    marker, text = result
                    comments.append(Comment(marker=marker, text=text, line=base_row))

        for child in node.children:
            _walk(child)

    for child in root.children:
        _walk(child)

    return comments


# ── Rust extraction ────────────────────────────────────────────────────────────


def _extract_symbols_rust(root: Node, filepath: Path) -> list[Symbol]:
    """Walk a Rust AST and extract fn, struct, enum, trait, and impl-method symbols.

    Kind mapping (per spec, existing 5 kinds only):
        function_item (top-level or in mod)  → function
        function_item inside impl_item       → method  (qualified as 'Type.fn')
        struct_item                          → class
        enum_item                            → type
        trait_item                           → interface
        function_item inside trait_item body → method  (qualified as 'Trait.fn')
        mod_item                             → traversed (NOT emitted as symbol)

    FIX 5: trait_item body (declaration_list) is now recursed; each function_item
    inside is emitted as a method qualified by the trait name (Trait.fn).
    """
    symbols: list[Symbol] = []
    file_str = str(filepath)

    def _walk(node: Node, impl_type: str | None = None) -> None:
        """Recursively walk AST.

        impl_type: when set, we are inside an impl or trait block of this type name.
        """
        if node.type == "function_item":
            name = _node_name(node)
            if name:
                doc = _rust_doc_comment(node)
                # Phase 4: extract enrichment fields.
                if impl_type is not None:
                    qualified = f"{impl_type}.{name}"
                    fields = extract_node_fields(
                        node,
                        "rust",
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
                    fields = extract_node_fields(
                        node,
                        "rust",
                        qualified_name=name,
                        max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                    )
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

        elif node.type == "function_signature_item":
            # Signature-only trait method (no body): fn foo(&self);
            # Only emit when inside a trait/impl context (impl_type set).
            if impl_type is not None:
                name = _node_name(node)
                if name:
                    doc = _rust_doc_comment(node)
                    qualified = f"{impl_type}.{name}"
                    fields = extract_node_fields(
                        node,
                        "rust",
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

        elif node.type == "impl_item":
            type_name = _rust_impl_type_name(node)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    _walk(child, impl_type=type_name)

        elif node.type == "struct_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                struct_name = _text(name_node)
                doc = _rust_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "rust",
                    qualified_name=struct_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        struct_name,
                        "class",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=struct_name,
                    )
                )

        elif node.type == "enum_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                enum_name = _text(name_node)
                doc = _rust_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "rust",
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

        elif node.type == "trait_item":
            # Emit the trait itself as an interface.
            name_node = node.child_by_field_name("name")
            if name_node:
                trait_name = _text(name_node)
                doc = _rust_doc_comment(node)
                fields = extract_node_fields(
                    node,
                    "rust",
                    qualified_name=trait_name,
                    max_signature_len=config.SEAM_MAX_SIGNATURE_LEN,
                )
                symbols.append(
                    _make_symbol(
                        trait_name,
                        "interface",
                        file_str,
                        node,
                        doc,
                        signature=fields["signature"],
                        decorators=fields["decorators"],
                        is_exported=fields["is_exported"],
                        visibility=fields["visibility"],
                        qualified_name=trait_name,
                    )
                )
                # FIX 5: recurse into the trait body and emit each function_item
                # as a method qualified by the trait name (handles both signature-only
                # and default-with-body methods).
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        _walk(child, impl_type=trait_name)

        elif node.type == "mod_item":
            # Traverse into mod bodies but do NOT emit the mod itself as a symbol.
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    _walk(child, impl_type=None)

        else:
            for child in node.children:
                _walk(child, impl_type)

    for child in root.children:
        _walk(child, impl_type=None)

    return symbols


def _extract_edges_rust(root: Node, filepath: Path) -> list[Edge]:
    """Extract import and call edges from a Rust AST.

    Import heuristic:
        use std::io::Write        → target = 'Write'
        use std::io::{Read, Write} → targets 'Read', 'Write'
        use name                  → target = 'name'
        use foo as bar            → target = 'foo' (real name, not alias)

    Call heuristic (MVP — bare identifiers only):
        call_expression where 'function' is identifier → target = identifier.
    """
    edges: list[Edge] = []
    file_str = str(filepath)
    file_stem = filepath.stem

    def _walk(node: Node) -> None:
        if node.type == "use_declaration":
            _handle_rust_use(node, file_str, file_stem, edges)
            return  # handled recursively inside

        elif node.type == "call_expression":
            func_child = node.child_by_field_name("function")
            if func_child and func_child.type == "identifier":
                source = _find_enclosing_function(node, "rust")
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


def _handle_rust_use(use_node: Node, file_str: str, file_stem: str, edges: list[Edge]) -> None:
    """Extract import edges from a Rust use_declaration node.

    Emits one edge per imported name (the REAL exported name, never the alias):
        use ident               → 'ident'
        use path::ident         → 'ident'   (rightmost segment of scoped_identifier)
        use path::{A, B}        → 'A', 'B'  (each member of the use_list)
        use path::*             → skipped   (glob — no single resolvable target)
        use foo as bar          → 'foo'     (use_as_clause: real name, not alias 'bar')
        use path::ident as X    → 'ident'   (scoped_identifier inside use_as_clause)

    WHY real name not alias: consistent with the Python/TS convention where
    `from x import y as z` records 'y'. The alias is local scope sugar; the
    imported symbol that other call-graph edges reference is always the real name.
    """
    line = use_node.start_point[0] + 1
    arg = use_node.child_by_field_name("argument")
    if arg is None:
        return

    def _emit(target: str) -> None:
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

    def _collect(node: Node) -> None:
        """Recursively collect final-segment identifiers from use tree nodes."""
        if node.type == "identifier":
            _emit(_text(node))
        elif node.type == "scoped_identifier":
            # a::b::c — the 'name' field is the rightmost segment.
            name_node = node.child_by_field_name("name")
            if name_node and name_node.type == "identifier":
                _emit(_text(name_node))
        elif node.type == "use_as_clause":
            # FIX 6: use foo as bar / use a::b as c
            # We want the ORIGINAL name (the real export), not the alias.
            # 'path' field = the original path node (identifier or scoped_identifier).
            path_node = node.child_by_field_name("path")
            if path_node is not None:
                if path_node.type == "identifier":
                    _emit(_text(path_node))
                elif path_node.type == "scoped_identifier":
                    # a::b as c → emit 'b' (rightmost segment of the real path)
                    name_node = path_node.child_by_field_name("name")
                    if name_node and name_node.type == "identifier":
                        _emit(_text(name_node))
        elif node.type in ("use_list", "use_wildcard"):
            if node.type == "use_wildcard":
                return  # glob import — skip
            for child in node.named_children:
                _collect(child)
        elif node.type == "scoped_use_list":
            # path::{A, B} — the 'list' field holds the use_list.
            lst = node.child_by_field_name("list")
            if lst is not None:
                for child in lst.named_children:
                    _collect(child)

    _collect(arg)


def _extract_comments_rust(root: Node, filepath: Path) -> list[Comment]:
    """Walk a Rust AST and extract semantic comment markers.

    Rust comment node types:
        line_comment  — covers // (plain), /// (outer doc), //! (inner doc)
        block_comment — /* */ blocks

    All line_comment variants are scanned; the // prefix (and any subsequent
    / or !) is stripped before matching. Block comments use _block_comment_lines.
    """
    comments: list[Comment] = []

    def _walk(node: Node) -> None:
        if node.type == "line_comment":
            raw = _text(node)
            base_row = node.start_point[0] + 1
            # Strip //, ///, or //! prefix.
            body = raw.lstrip("/").lstrip("!").strip()
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

    for child in root.children:
        _walk(child)

    return comments
