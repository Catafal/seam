"""Node-field extractor for Phase 4 — signature, decorators, is_exported, visibility, qualified_name.

LAYER: pure leaf module — imports only stdlib + tree_sitter.
Must NOT import from any other seam.indexer module (same contract as graph_common.py).

Entry point:
    extract_node_fields(node, language, qualified_name=None) -> NodeFields

NEVER raises. On any extraction failure, returns the field as None / empty list
so the caller (graph.py / graph_go_rust.py) can still emit the symbol.

Per-language rules (PRD §Implementation Decisions):
  signature       : declaration header normalized to one line, truncated to max len.
  decorators      : Python/TS verbatim decorator nodes; Go/Rust → always [].
  is_exported     : TS/JS: export keyword; Go: uppercase first letter; Rust: pub/pub(crate);
                    Python: no single-underscore prefix (heuristic; __all__ is out of scope).
  visibility      : Rust: pub/pub(crate)/none → public/crate/private;
                    TS/JS: public/private/protected modifiers;
                    Python: private if underscore-prefix, else public;
                    Go: derived from capitalization.
  qualified_name  : passed in from the caller (already resolved by graph.py scope-walking).
"""

import logging
from typing import Any, TypedDict

from tree_sitter import Node

logger = logging.getLogger(__name__)

# Default maximum signature length in characters.
# The actual limit is threaded in as a parameter from the caller (graph.py / graph_go_rust.py),
# which reads it from seam.config.SEAM_MAX_SIGNATURE_LEN. This default is only used when
# the caller does not explicitly pass max_signature_len (e.g. in tests or ad-hoc calls).
# WHY a constant here instead of reading the env var directly:
#   CLAUDE.md requires config access through seam.config only. signatures.py is a pure
#   leaf module that must not import from any other seam module (including seam.config).
#   The solution is parameter threading: callers pass the configured value, and this
#   module stays dependency-free. The constant 300 matches seam.config's default.
_DEFAULT_MAX_SIG_LEN = 300


class NodeFields(TypedDict):
    """Five enrichment fields extracted per symbol node (all nullable)."""

    signature: str | None  # declaration header, single line, truncated
    decorators: list[str]  # verbatim decorator strings (Python/TS); [] otherwise
    is_exported: bool | None  # True = public API; None = unknown (unsupported language)
    visibility: str | None  # "public" | "private" | "protected" | "crate" | None
    qualified_name: str | None  # "ClassName.method" or plain name; None for top-level unknown


# ── Safe text helper (mirrors graph_common._text without importing it) ────────


def _text(node: Node | None) -> str:
    """Safely decode a tree-sitter node's text bytes to str."""
    if node is None:
        return ""
    raw = node.text
    if raw is None:
        return ""
    return raw.decode("utf-8", errors="replace")


def _normalize_to_one_line(text: str) -> str:
    """Collapse any embedded newlines and multiple spaces into a single space."""
    return " ".join(text.split())


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len characters, appending '...' if truncated."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ── Python extraction ─────────────────────────────────────────────────────────


def _py_is_exported(name: str) -> bool:
    """Python export heuristic: name NOT starting with underscore → exported."""
    return not name.startswith("_")


def _py_visibility(name: str) -> str:
    """Python visibility: underscore prefix → private; else public."""
    return "private" if name.startswith("_") else "public"


def _py_signature(node: Node) -> str | None:
    """Extract Python function/class declaration header as a one-line string.

    For function_definition: "def name(params) -> return_type"
    For class_definition: "class Name(Bases)"
    """
    try:
        node_type = node.type

        # Unwrap decorated_definition to get the inner definition
        if node_type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                return _py_signature(inner)
            return None

        if node_type == "function_definition":
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            return_node = node.child_by_field_name("return_type")

            name = _text(name_node)
            params = _text(params_node)
            parts = [f"def {name}{params}"]
            if return_node is not None:
                # WHY removeprefix not the strip-char-set method:
                # The strip-charset variant treats the argument as a SET of characters to
                # strip, not a prefix string. removeprefix("->") strips only the exact
                # two-char prefix once — correct for return-type nodes whose text starts
                # with "->" (the arrow is part of the node's text in tree-sitter Python).
                ret = _text(return_node).removeprefix("->").strip()
                parts.append(f"-> {ret}")
            return _normalize_to_one_line(" ".join(parts))

        if node_type == "class_definition":
            name_node = node.child_by_field_name("name")
            # Superclasses are in the argument_list node (if present)
            superclasses_node = node.child_by_field_name("superclasses")
            name = _text(name_node)
            if superclasses_node:
                bases = _text(superclasses_node)
                return _normalize_to_one_line(f"class {name}{bases}")
            return _normalize_to_one_line(f"class {name}")

    except Exception as exc:  # noqa: BLE001
        # Log so extraction-quality degradation is traceable (Finding 8).
        logger.debug("_py_signature extraction failed for node.type=%r: %s", node.type, exc)
    return None


def _py_decorators(node: Node) -> list[str]:
    """Extract Python decorator texts from a decorated_definition node."""
    if node.type != "decorated_definition":
        return []
    decorators: list[str] = []
    try:
        for child in node.children:
            if child.type == "decorator":
                text = _text(child).strip()
                if text:
                    decorators.append(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_py_decorators extraction failed: %s", exc)
    return decorators


def _extract_python(node: Node, qualified_name: str | None, max_sig_len: int) -> NodeFields:
    """Extract all five fields for a Python node."""
    try:
        # Determine the effective name for export/visibility check
        effective_node = node
        if node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                effective_node = inner

        name_node = effective_node.child_by_field_name("name")
        name = _text(name_node) if name_node else ""

        sig = _py_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=_py_decorators(node),
            is_exported=_py_is_exported(name) if name else None,
            visibility=_py_visibility(name) if name else None,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── TypeScript / JavaScript extraction ───────────────────────────────────────


_TS_VISIBILITY_KEYWORDS: frozenset[str] = frozenset({"public", "private", "protected"})


def _ts_is_exported(node: Node) -> bool:
    """TypeScript/JS export detection: check if parent is export_statement.

    Covers:
      export function foo() {}
      export class Bar {}
      export interface X {}
      export default function foo() {}
      export default class Foo {}

    All of these produce an export_statement parent in the tree-sitter grammar.
    The previous code had a duplicate dead branch (two identical checks) — removed.
    """
    try:
        parent = node.parent
        if parent is None:
            return False
        # export function/class/interface/default: parent is export_statement.
        if parent.type == "export_statement":
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _ts_visibility(node: Node) -> str | None:
    """TypeScript visibility from access modifier keywords on a method/property."""
    try:
        for child in node.children:
            if child.type in ("public", "private", "protected"):
                return child.type
            # Some tree-sitter grammars store access modifiers as accessibility_modifier
            if child.type == "accessibility_modifier":
                text = _text(child).strip()
                if text in _TS_VISIBILITY_KEYWORDS:
                    return text
    except Exception:  # noqa: BLE001
        pass
    return None


def _ts_decorators(node: Node) -> list[str]:
    """Extract TypeScript/JS decorator nodes from the node's parent context.

    In the tree-sitter TypeScript grammar, decorators appear as sibling nodes
    before the class/method declaration, or as decorator nodes attached directly.
    This walks prev_sibling for decorator nodes.
    """
    decorators: list[str] = []
    try:
        current = node.prev_sibling
        while current is not None and current.type == "decorator":
            text = _text(current).strip()
            if text:
                decorators.insert(0, text)  # prepend to preserve order
            current = current.prev_sibling
    except Exception:  # noqa: BLE001
        pass
    return decorators


def _ts_signature(node: Node) -> str | None:
    """Extract TypeScript/JS declaration header as one-line string."""
    try:
        ntype = node.type

        if ntype == "function_declaration":
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            ret_node = node.child_by_field_name("return_type")
            name = _text(name_node)
            params = _text(params_node)
            parts = [f"function {name}{params}"]
            if ret_node:
                parts.append(f": {_text(ret_node).lstrip(':').strip()}")
            return _normalize_to_one_line("".join(parts))

        if ntype == "method_definition":
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            ret_node = node.child_by_field_name("return_type")
            name = _text(name_node)
            params = _text(params_node)
            parts = [f"{name}{params}"]
            if ret_node:
                parts.append(f": {_text(ret_node).lstrip(':').strip()}")
            return _normalize_to_one_line("".join(parts))

        if ntype == "class_declaration":
            name_node = node.child_by_field_name("name")
            # type_parameters and class_heritage (implements/extends)
            type_params = node.child_by_field_name("type_parameters")
            heritage = None
            for child in node.children:
                if child.type == "class_heritage":
                    heritage = child
                    break
            name = _text(name_node)
            sig = f"class {name}"
            if type_params:
                sig += _text(type_params)
            if heritage:
                sig += f" {_text(heritage)}"
            return _normalize_to_one_line(sig)

        if ntype == "interface_declaration":
            name_node = node.child_by_field_name("name")
            type_params = node.child_by_field_name("type_parameters")
            name = _text(name_node)
            sig = f"interface {name}"
            if type_params:
                sig += _text(type_params)
            return _normalize_to_one_line(sig)

        if ntype == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node)
            return _normalize_to_one_line(f"type {name}")

    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_typescript(node: Node, qualified_name: str | None, max_sig_len: int) -> NodeFields:
    """Extract all five fields for a TypeScript/JavaScript node."""
    try:
        sig = _ts_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        is_exported = _ts_is_exported(node)
        visibility = _ts_visibility(node)

        return NodeFields(
            signature=sig,
            decorators=_ts_decorators(node),
            is_exported=is_exported,
            visibility=visibility,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── Go extraction ─────────────────────────────────────────────────────────────


def _go_is_exported(name: str) -> bool:
    """Go export rule: capitalized first letter → exported."""
    return bool(name) and name[0].isupper()


def _go_visibility(name: str) -> str:
    """Go visibility from capitalization."""
    return "public" if _go_is_exported(name) else "private"


def _go_signature(node: Node) -> str | None:
    """Extract Go declaration header as one-line string."""
    try:
        ntype = node.type

        if ntype == "function_declaration":
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            result_node = node.child_by_field_name("result")
            name = _text(name_node)
            params = _text(params_node)
            sig = f"func {name}{params}"
            if result_node:
                sig += f" {_text(result_node)}"
            return _normalize_to_one_line(sig)

        if ntype == "method_declaration":
            # func (recv RecvType) MethodName(params) result
            recv_node = node.child_by_field_name("receiver")
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            result_node = node.child_by_field_name("result")
            recv = _text(recv_node) if recv_node else ""
            name = _text(name_node)
            params = _text(params_node)
            sig = f"func {recv} {name}{params}"
            if result_node:
                sig += f" {_text(result_node)}"
            return _normalize_to_one_line(sig)

        if ntype == "type_declaration":
            # For type declarations we extract the first type_spec or type_alias
            for child in node.named_children:
                if child.type in ("type_spec", "type_alias"):
                    name_node = child.child_by_field_name("name")
                    type_node = child.child_by_field_name("type")
                    if name_node:
                        name = _text(name_node)
                        if type_node:
                            type_kind = type_node.type.replace("_type", "").replace("_", " ")
                            return _normalize_to_one_line(f"type {name} {type_kind}")
                        return _normalize_to_one_line(f"type {name}")

    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_go(node: Node, qualified_name: str | None, max_sig_len: int) -> NodeFields:
    """Extract all five fields for a Go node."""
    try:
        # Determine name for export check
        name_node = node.child_by_field_name("name")
        name = _text(name_node) if name_node else ""

        # For type_declaration, name is inside the type_spec
        if node.type == "type_declaration":
            for child in node.named_children:
                if child.type in ("type_spec", "type_alias"):
                    inner_name = child.child_by_field_name("name")
                    name = _text(inner_name) if inner_name else ""
                    break

        sig = _go_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=[],  # Go has no decorators
            is_exported=_go_is_exported(name) if name else None,
            visibility=_go_visibility(name) if name else None,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── Rust extraction ───────────────────────────────────────────────────────────


def _rust_has_pub(node: Node) -> bool:
    """Check if a Rust node has a `pub` or `pub(...)` visibility modifier."""
    try:
        for child in node.children:
            if child.type in ("visibility_modifier",):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _rust_visibility(node: Node) -> str:
    """Rust visibility from pub modifier.

    pub         → 'public'
    pub(crate)  → 'crate'
    (none)      → 'private'
    """
    try:
        for child in node.children:
            if child.type == "visibility_modifier":
                text = _text(child).strip()
                if "crate" in text:
                    return "crate"
                return "public"
    except Exception:  # noqa: BLE001
        pass
    return "private"


def _rust_signature(node: Node) -> str | None:
    """Extract Rust declaration header as one-line string."""
    try:
        ntype = node.type

        if ntype in ("function_item", "function_signature_item"):
            # Build: [pub] fn name(params) -> return_type
            parts: list[str] = []
            for child in node.children:
                # Stop at the body block (declaration_list or block)
                if child.type in ("block", "declaration_list"):
                    break
                text = _text(child).strip()
                if text:
                    parts.append(text)
            return _normalize_to_one_line(" ".join(parts))

        if ntype == "struct_item":
            parts = []
            for child in node.children:
                if child.type in ("field_declaration_list", "ordered_field_declaration_list"):
                    break
                text = _text(child).strip()
                if text:
                    parts.append(text)
            return _normalize_to_one_line(" ".join(parts))

        if ntype == "enum_item":
            parts = []
            for child in node.children:
                if child.type == "enum_variant_list":
                    break
                text = _text(child).strip()
                if text:
                    parts.append(text)
            return _normalize_to_one_line(" ".join(parts))

        if ntype == "trait_item":
            parts = []
            for child in node.children:
                if child.type == "declaration_list":
                    break
                text = _text(child).strip()
                if text:
                    parts.append(text)
            return _normalize_to_one_line(" ".join(parts))

    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_rust(node: Node, qualified_name: str | None, max_sig_len: int) -> NodeFields:
    """Extract all five fields for a Rust node."""
    try:
        vis = _rust_visibility(node)
        is_exported = vis in ("public", "crate")

        sig = _rust_signature(node)
        if sig is not None:
            sig = _truncate(sig, max_sig_len)

        return NodeFields(
            signature=sig,
            decorators=[],  # Rust attributes (#[...]) are out of scope per PRD
            is_exported=is_exported,
            visibility=vis,
            qualified_name=qualified_name,
        )
    except Exception:  # noqa: BLE001
        return _safe_defaults(qualified_name)


# ── Safe defaults ─────────────────────────────────────────────────────────────


def _safe_defaults(qualified_name: str | None = None) -> NodeFields:
    """Return a safe NodeFields with all nulls/empty. Used on any extraction failure."""
    return NodeFields(
        signature=None,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=qualified_name,
    )


# ── Public entry point ────────────────────────────────────────────────────────


def extract_node_fields(
    node: Any,
    language: str,
    qualified_name: str | None = None,
    max_signature_len: int = _DEFAULT_MAX_SIG_LEN,
) -> NodeFields:
    """Extract the five enrichment fields from a tree-sitter symbol node.

    Args:
        node:               A tree-sitter Node (or None/invalid — never raises).
        language:           'python' | 'typescript' | 'javascript' | 'go' | 'rust'.
        qualified_name:     Already-resolved qualified name from the caller's scope-walker.
                            Passed through as-is into NodeFields.qualified_name.
        max_signature_len:  Maximum signature length in characters. Signatures longer
                            than this are truncated with '...'. Callers (graph.py /
                            graph_go_rust.py) read this from seam.config.SEAM_MAX_SIGNATURE_LEN
                            so there is a single config source of truth. The default here
                            (300) matches the seam.config default.

    Returns:
        NodeFields TypedDict. ALL fields are safe: signature may be None,
        decorators is always a list (possibly empty), is_exported/visibility may be None.

    NEVER raises: any extraction error returns _safe_defaults().
    """
    # Guard: None or non-Node input → safe defaults
    if node is None:
        return _safe_defaults(qualified_name)

    # Import Node class here for isinstance check — this is still leaf-compliant
    # because tree_sitter is in the stdlib-or-build-time-available category.
    if not isinstance(node, Node):
        return _safe_defaults(qualified_name)

    try:
        if language == "python":
            return _extract_python(node, qualified_name, max_signature_len)
        elif language in ("typescript", "javascript"):
            return _extract_typescript(node, qualified_name, max_signature_len)
        elif language == "go":
            return _extract_go(node, qualified_name, max_signature_len)
        elif language == "rust":
            return _extract_rust(node, qualified_name, max_signature_len)
        else:
            # Unknown language — return safe defaults (not an error)
            return _safe_defaults(qualified_name)
    except Exception:  # noqa: BLE001
        # Belt-and-suspenders: any unhandled exception → safe defaults
        logger.debug(
            "extract_node_fields: unhandled exception for language=%r node.type=%r",
            language,
            getattr(node, "type", "?"),
        )
        return _safe_defaults(qualified_name)
