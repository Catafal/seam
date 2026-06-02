"""Phase 9 node-field extractors — Java, C#, Ruby, C, C++, PHP.

LAYER: pure leaf module — imports only stdlib + tree_sitter.
Must NOT import from any other seam.indexer module (same contract as signatures.py).

LAYERING:
    signatures_ext  (this file — leaf, no seam deps)
         ↑
    signatures.py   (dispatch entry point — imports this at top level)

Entry points (called from signatures.extract_node_fields):
    _extract_java / _extract_csharp / _extract_ruby
    _extract_c / _extract_cpp / _extract_php

Each function signature: (node, qualified_name, max_sig_len) -> NodeFields
NEVER raises. On any failure, returns _safe_defaults(qualified_name).
"""

import logging
from typing import TypedDict

from tree_sitter import Node

logger = logging.getLogger(__name__)


class NodeFields(TypedDict):
    """Five enrichment fields extracted per symbol node (all nullable).

    WHY re-declared here rather than imported from signatures.py:
    signatures_ext must stay a leaf (no seam deps). Importing NodeFields from
    signatures.py would create a cycle (signatures.py imports signatures_ext).
    The TypedDict shape is identical — this is intentional duplication to
    preserve the leaf property of both modules.
    """

    signature: str | None
    decorators: list[str]
    is_exported: bool | None
    visibility: str | None
    qualified_name: str | None


def _safe_defaults(qualified_name: str | None = None) -> NodeFields:
    """Return a safe NodeFields with all nulls/empty. Used on any extraction failure."""
    return NodeFields(
        signature=None,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=qualified_name,
    )


def _text(node: Node | None) -> str:
    """Safely decode a tree-sitter node's text bytes to str."""
    if node is None:
        return ""
    raw = node.text
    if raw is None:
        return ""
    return raw.decode("utf-8", errors="replace")


def _normalize_to_one_line(text: str) -> str:
    """Collapse embedded newlines and multiple spaces into a single space."""
    return " ".join(text.split())


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len characters, appending '...' if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ── Java ──────────────────────────────────────────────────────────────────────


_JAVA_VISIBILITY_KEYWORDS: frozenset[str] = frozenset({"public", "private", "protected"})


def _java_modifiers(node: Node) -> Node | None:
    """Find the 'modifiers' child of a Java declaration node, or None."""
    for child in node.children:
        if child.type == "modifiers":
            return child
    return None


def _java_visibility(node: Node) -> str | None:
    """Extract visibility from a Java declaration's modifiers child.

    Scans modifiers children for public/private/protected keyword nodes.
    Returns None for package-private (no access modifier present).
    """
    mods = _java_modifiers(node)
    if mods is None:
        return None
    try:
        for child in mods.children:
            if child.type in _JAVA_VISIBILITY_KEYWORDS:
                return child.type
    except Exception:  # noqa: BLE001
        pass
    return None


def _java_annotations(node: Node) -> list[str]:
    """Extract Java annotation texts (@Service, @Override, etc.) from a declaration node.

    Looks inside the modifiers child for marker_annotation and annotation nodes.
    Returns verbatim text (e.g. '@Service', '@SuppressWarnings("all")').
    """
    result: list[str] = []
    mods = _java_modifiers(node)
    if mods is None:
        return result
    try:
        for child in mods.children:
            if child.type in ("marker_annotation", "annotation"):
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


def _java_signature(node: Node) -> str | None:
    """Build a one-line Java signature from a declaration node.

    Covers: class_declaration, interface_declaration, enum_declaration,
    record_declaration, method_declaration, constructor_declaration.

    Strategy: collect text from all children BEFORE the body (class_body, block,
    constructor_body, enum_body, interface_body), join, and normalize to one line.
    """
    try:
        body_types = frozenset(
            {
                "class_body",
                "block",
                "constructor_body",
                "enum_body",
                "interface_body",
            }
        )
        parts: list[str] = []
        for child in node.children:
            if child.type in body_types:
                break
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        return _normalize_to_one_line(" ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_java(
    node: Node,
    qualified_name: str | None,
    max_sig_len: int,
) -> NodeFields:
    """Extract all five enrichment fields for a Java symbol node.

    signature   : declaration header up to (but not including) the body, one line.
    decorators  : Java annotations verbatim (@Override, @Service, etc.).
    is_exported : True when 'public' modifier is present.
    visibility  : 'public' | 'private' | 'protected' | None (package-private).
    qualified_name : passed through from the caller.
    """
    try:
        vis = _java_visibility(node)
        is_exported = vis == "public"
        annotations = _java_annotations(node)

        sig = _java_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=annotations,
            is_exported=is_exported,
            visibility=vis,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── C# ────────────────────────────────────────────────────────────────────────


_CSHARP_VISIBILITY_KEYWORDS: frozenset[str] = frozenset(
    {
        "public",
        "private",
        "protected",
        "internal",
    }
)


def _csharp_modifier_text(node: Node) -> str | None:
    """Find the first access modifier from a C# declaration's 'modifier' children.

    Returns 'public', 'private', 'protected', or 'internal' if found, else None.
    """
    try:
        for child in node.children:
            if child.type == "modifier":
                text = _text(child).strip()
                if text in _CSHARP_VISIBILITY_KEYWORDS:
                    return text
    except Exception:  # noqa: BLE001
        pass
    return None


def _csharp_attributes_texts(node: Node) -> list[str]:
    """Extract C# attribute lists ([Serializable], [HttpGet], etc.) verbatim.

    Attribute lists appear as 'attribute_list' children before the modifier.
    Returns each attribute_list as a verbatim string.
    """
    result: list[str] = []
    try:
        for child in node.children:
            if child.type == "attribute_list":
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


def _csharp_signature(node: Node) -> str | None:
    """Build a one-line C# signature from a declaration node.

    Covers: class_declaration, struct_declaration, record_declaration,
    interface_declaration, enum_declaration, delegate_declaration,
    method_declaration, constructor_declaration.

    Strategy: collect text from all children BEFORE the body (block, declaration_list,
    enum_member_declaration_list), join, normalize. Skip attribute_list children
    (those are decorators, not part of the signature header).
    """
    try:
        body_types = frozenset(
            {
                "block",
                "declaration_list",
                "enum_member_declaration_list",
            }
        )
        skip_types = frozenset({"attribute_list"})
        parts: list[str] = []
        for child in node.children:
            if child.type in body_types:
                break
            if child.type in skip_types:
                continue
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        return _normalize_to_one_line(" ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_csharp(
    node: Node,
    qualified_name: str | None,
    max_sig_len: int,
) -> NodeFields:
    """Extract all five enrichment fields for a C# symbol node.

    signature   : declaration header up to (but not including) the body, one line.
    decorators  : C# attributes verbatim ([Serializable], [HttpGet], etc.).
    is_exported : True when 'public' modifier is present.
    visibility  : 'public' | 'private' | 'protected' | 'internal' | None.
    qualified_name : passed through from the caller.
    """
    try:
        vis = _csharp_modifier_text(node)
        is_exported = vis == "public"
        attributes = _csharp_attributes_texts(node)

        sig = _csharp_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=attributes,
            is_exported=is_exported,
            visibility=vis,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── Ruby ──────────────────────────────────────────────────────────────────────


def _ruby_signature(node: Node) -> str | None:
    """Build a one-line Ruby signature from a method, class, or module node.

    For method nodes: capture everything from 'def' up to (but not including)
    the body_statement. For class/module nodes: capture 'class Name' / 'module Name'.

    Covers: method, singleton_method, class, module nodes.
    Strategy: collect text from all children BEFORE body_statement, join, normalize.
    SKIP 'comment' child nodes — inline comments like `module Utils # NOTE: ...`
    can appear between the name (constant) and body_statement and must not be
    included in the signature text.
    """
    try:
        body_stop_types = frozenset({"body_statement", "end"})
        # WHY skip comment: class/module nodes may have inline comment children
        # between the class/module name and the body_statement. Including them
        # would corrupt the signature (e.g. 'module Utils # NOTE: ...').
        skip_types = frozenset({"comment"})
        parts: list[str] = []
        for child in node.children:
            if child.type in body_stop_types:
                break
            if child.type in skip_types:
                continue  # skip inline comments
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        return _normalize_to_one_line(" ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_ruby(
    node: Node,
    qualified_name: str | None,
    max_sig_len: int,
) -> NodeFields:
    """Extract enrichment fields for a Ruby symbol node.

    signature   : method/class declaration header (best-effort, one line).
    decorators  : [] (Ruby has no decorator/annotation syntax).
    is_exported : None (Ruby visibility is dynamic — private/protected not tracked at
                  extraction time; tracking would require stateful AST traversal).
    visibility  : None (same reason — deferred to a future enhancement).
    qualified_name : passed through from the caller.

    WHY None for visibility: Ruby's visibility model (private/protected/public) is
    set by method calls (private :foo) rather than keywords on the declaration itself.
    Statically extracting it correctly would require tracking call context — out of
    scope for this MVP (same limitation noted in the spec).
    """
    try:
        sig = _ruby_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=[],  # Ruby has no annotation/decorator syntax
            is_exported=None,  # Dynamic visibility — not tracked statically
            visibility=None,  # Dynamic visibility — not tracked statically
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── C ─────────────────────────────────────────────────────────────────────────


def _c_is_static(node: Node) -> bool:
    """Return True if a C function_definition has a 'static' storage_class_specifier."""
    try:
        for child in node.children:
            if child.type == "storage_class_specifier":
                if _text(child).strip() == "static":
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _c_signature(node: Node) -> str | None:
    """Build a one-line C signature from a declaration node.

    Strategy: collect text from all children BEFORE the body (compound_statement,
    field_declaration_list, enumerator_list), join, and normalize to one line.

    Covers: function_definition, struct_specifier, union_specifier, enum_specifier,
    type_definition.
    """
    try:
        body_types = frozenset(
            {
                "compound_statement",
                "field_declaration_list",
                "enumerator_list",
            }
        )
        parts: list[str] = []
        for child in node.children:
            if child.type in body_types:
                break
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        return _normalize_to_one_line(" ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_c(
    node: Node,
    qualified_name: str | None,
    max_sig_len: int,
) -> NodeFields:
    """Extract all five enrichment fields for a C symbol node.

    signature   : declaration header up to (but not including) the body, one line.
    decorators  : [] (C has no decorator syntax).
    is_exported : False when 'static' storage class is present (file-local); True otherwise.
    visibility  : 'private' for static functions (file-local); 'public' otherwise.
    qualified_name : passed through from the caller.

    WHY static→private: C 'static' at file scope means the function/variable is not
    visible outside the translation unit — the closest equivalent to 'private'.
    """
    try:
        is_static = _c_is_static(node)
        vis = "private" if is_static else "public"
        is_exported = not is_static

        sig = _c_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=[],
            is_exported=is_exported,
            visibility=vis,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── C++ ───────────────────────────────────────────────────────────────────────


def _cpp_signature(node: Node) -> str | None:
    """Build a one-line C++ signature from a declaration node.

    Strategy: collect text from all children BEFORE the body (compound_statement,
    field_declaration_list, enumerator_list, declaration_list), join, normalize.

    Covers: class_specifier, struct_specifier, union_specifier, enum_specifier,
    function_definition.
    """
    try:
        body_types = frozenset(
            {
                "compound_statement",
                "field_declaration_list",
                "enumerator_list",
                "declaration_list",
            }
        )
        parts: list[str] = []
        for child in node.children:
            if child.type in body_types:
                break
            # Skip field_initializer_list (constructor initializer : x(v)) from signature
            if child.type == "field_initializer_list":
                break
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        return _normalize_to_one_line(" ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_cpp(
    node: Node,
    qualified_name: str | None,
    max_sig_len: int,
) -> NodeFields:
    """Extract all five enrichment fields for a C++ symbol node.

    signature   : declaration header up to (but not including) the body, one line.
    decorators  : [] (C++ [[...]] attributes are out of scope per PRD).
    is_exported : True for public class members; True for free functions; None for
                  non-class context where visibility is undecidable at extraction time.
    visibility  : Not tracked per-member in this MVP (would require tracking
                  access_specifier state across the class body); returns None.
    qualified_name : passed through from the caller.

    WHY None for visibility: C++ visibility depends on the current access_specifier
    (public/private/protected) within the class body, which would require stateful
    tracking during AST traversal. This is deferred to a future enhancement.
    """
    try:
        sig = _cpp_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=[],
            is_exported=True,  # Conservative: assume exportable at extraction time
            visibility=None,  # Stateful tracking required; out of scope for MVP
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── PHP ───────────────────────────────────────────────────────────────────────


_PHP_VISIBILITY_KEYWORDS: frozenset[str] = frozenset({"public", "private", "protected"})


def _php_visibility(node: Node) -> str | None:
    """Extract visibility from a PHP method_declaration or property_declaration node.

    Looks for a visibility_modifier child and reads its keyword child.
    Returns 'public' | 'private' | 'protected' | None.
    """
    try:
        for child in node.children:
            if child.type == "visibility_modifier":
                for kw in child.children:
                    if kw.type in _PHP_VISIBILITY_KEYWORDS:
                        return kw.type
    except Exception:  # noqa: BLE001
        pass
    return None


def _php_attributes(node: Node) -> list[str]:
    """Extract PHP 8 attribute lists (#[...]) verbatim from a declaration node.

    PHP attributes appear as attribute_list children before visibility_modifier.
    Each attribute_list (which may contain one or more attribute_group entries)
    is returned verbatim (e.g. "#[Route('/users')]").
    """
    result: list[str] = []
    try:
        for child in node.children:
            if child.type == "attribute_list":
                text = _text(child).strip()
                if text:
                    result.append(text)
    except Exception:  # noqa: BLE001
        pass
    return result


def _php_signature(node: Node) -> str | None:
    """Build a one-line PHP signature from a declaration node.

    Covers: class_declaration, interface_declaration, trait_declaration,
    enum_declaration, function_definition, method_declaration.

    Strategy: collect text from all children BEFORE the body (compound_statement,
    declaration_list, enum_declaration_list), join, normalize. Skip attribute_list
    children (those are decorators, not signature header).
    """
    try:
        body_stop_types = frozenset(
            {
                "compound_statement",
                "declaration_list",
                "enum_declaration_list",
            }
        )
        skip_types = frozenset({"attribute_list"})
        parts: list[str] = []
        for child in node.children:
            if child.type in body_stop_types:
                break
            if child.type in skip_types:
                continue  # skip attributes — those are decorators
            text = _text(child).strip()
            if text:
                parts.append(text)
        if not parts:
            return None
        return _normalize_to_one_line(" ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_php(
    node: Node,
    qualified_name: str | None,
    max_sig_len: int,
) -> NodeFields:
    """Extract all five enrichment fields for a PHP symbol node.

    signature   : declaration header up to (but not including) the body, one line.
    decorators  : PHP 8 attributes (#[Route(...)]) verbatim from attribute_list children.
    is_exported : True when 'public' modifier is present.
    visibility  : 'public' | 'private' | 'protected' | None (no modifier).
    qualified_name : passed through from the caller.
    """
    try:
        vis = _php_visibility(node)
        is_exported = vis == "public"
        attributes = _php_attributes(node)

        sig = _php_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=attributes,
            is_exported=is_exported,
            visibility=vis,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)
