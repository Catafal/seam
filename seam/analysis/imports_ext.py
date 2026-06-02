"""Phase 9 import mapping extraction and resolution — Java, C#, Ruby, C, C++, PHP.

LAYER: pure leaf module — imports only stdlib + tree_sitter types.
Must NOT import from seam.query, seam.server, confidence.py, or any other
seam module (same contract as imports.py).

LAYERING:
    imports_ext  (this file — leaf, no seam deps)
         ↑
    imports.py   (dispatch entry point — imports this at top level)

Entry points (called from imports.extract_import_mappings and
imports.resolve_import_source):
    _extract_<lang>(root, filepath)     -> list[ImportMapping]
    _resolve_<lang>(source, ref, root)  -> list[str]

All functions NEVER raise. On failure they return [] (empty list),
degrading cleanly to the name-count resolution rule in confidence.py.
"""

import logging
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# WHY this type alias: imports_ext is a leaf that must not import from imports.py
# (imports.py imports imports_ext at its top level). We use TypedDict locally to
# construct ImportMapping-compatible dicts without the circular import.
_ImportMappingList = list[Any]


class _ImportMapping(TypedDict):
    """Local copy of ImportMapping TypedDict to avoid circular import from imports.py."""

    local_name: str
    exported_name: str
    source_module: str
    is_default: bool
    is_namespace: bool
    is_wildcard: bool
    line: int


def _text_node(node: Any) -> str:
    """Safely decode a tree-sitter node's text bytes to str."""
    try:
        raw = node.text
        if raw is None:
            return ""
        return raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _make_mapping(
    local_name: str,
    exported_name: str,
    source_module: str,
    line: int,
    *,
    is_default: bool = False,
    is_namespace: bool = False,
    is_wildcard: bool = False,
) -> _ImportMapping:
    """Construct an ImportMapping dict with safe defaults."""
    return _ImportMapping(
        local_name=local_name,
        exported_name=exported_name,
        source_module=source_module,
        is_default=is_default,
        is_namespace=is_namespace,
        is_wildcard=is_wildcard,
        line=line,
    )


# ── Java ──────────────────────────────────────────────────────────────────────


def _java_scoped_identifier_last_segment(node: Any) -> str | None:
    """Extract the rightmost identifier from a Java scoped_identifier or identifier.

    scoped_identifier.name  → rightmost segment (e.g. 'List' from java.util.List)
    identifier              → the text itself
    """
    try:
        if node.type == "identifier":
            return _text_node(node)
        if node.type == "scoped_identifier":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                return _text_node(name_node)
    except Exception:  # noqa: BLE001
        pass
    return None


def _java_scoped_identifier_full(node: Any) -> str:
    """Return the full dotted name of a scoped_identifier or identifier.

    Used for the source_module field — e.g. 'java.util.List'.
    """
    try:
        return _text_node(node).strip()
    except Exception:  # noqa: BLE001
        return ""


def _extract_java(root: object, filepath: Path) -> _ImportMappingList:
    """Extract ImportMapping records from a Java AST root node.

    Handles:
        import java.util.List;         → local_name='List', source_module='java.util.List'
        import java.util.*;            → is_wildcard=True  (skipped — no single target)
        import static java.lang.Math.abs; → local_name='abs', source_module='java.lang.Math.abs'

    Resolution: Java package-to-directory resolution is out of scope per the spec;
    resolve always returns [] (degrades to name-count rule in confidence.py).
    Never raises. Returns [] on any failure.
    """
    result: _ImportMappingList = []
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return result

        def _walk(node: Any) -> None:
            if node.type == "import_declaration":
                line = node.start_point[0] + 1
                for child in node.named_children:
                    if child.type == "asterisk":
                        # Wildcard import — no single target, skip.
                        return
                    last = _java_scoped_identifier_last_segment(child)
                    full = _java_scoped_identifier_full(child)
                    if last:
                        result.append(
                            _make_mapping(
                                local_name=last,
                                exported_name=last,
                                source_module=full,
                                line=line,
                                is_default=True,
                            )
                        )
                return  # No need to recurse into import_declaration children.
            for child in node.named_children:
                _walk(child)

        _walk(root)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_java: extraction failed for %s: %r", filepath, exc)
    return result


def _resolve_java(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a Java import path to source file paths.

    Package-to-directory resolution is out of scope for the MVP (see PRD §implementation).
    Returns [] — degrading cleanly to the name-count resolution rule.
    """
    return []  # Out of scope per spec — Java package resolution not implemented


# ── C# ────────────────────────────────────────────────────────────────────────


def _csharp_using_last_segment(node: Any) -> str | None:
    """Extract the rightmost identifier from a C# qualified_name or identifier.

    qualified_name.name → rightmost segment (e.g. 'Generic' from System.Collections.Generic)
    identifier          → the text itself
    """
    try:
        if node.type == "identifier":
            return _text_node(node)
        if node.type == "qualified_name":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                return _text_node(name_node)
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_csharp(root: object, filepath: Path) -> _ImportMappingList:
    """Extract ImportMapping records from a C# AST root node.

    Handles:
        using System;                       → local_name='System'
        using System.Collections.Generic;   → local_name='Generic'

    Resolution: C# namespace-to-directory resolution is out of scope per the spec.
    Never raises. Returns [] on any failure.
    """
    result: _ImportMappingList = []
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return result

        def _walk(node: Any) -> None:
            if node.type == "using_directive":
                line = node.start_point[0] + 1
                for child in node.named_children:
                    last = _csharp_using_last_segment(child)
                    full = _text_node(child).strip()
                    if last:
                        result.append(
                            _make_mapping(
                                local_name=last,
                                exported_name=last,
                                source_module=full,
                                line=line,
                                is_default=True,
                            )
                        )
                return  # No need to recurse into using_directive children.
            for child in node.named_children:
                _walk(child)

        _walk(root)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_csharp: extraction failed for %s: %r", filepath, exc)
    return result


def _resolve_csharp(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a C# using directive to source file paths.

    Namespace-to-directory resolution is out of scope for the MVP.
    Returns [] — degrading cleanly to the name-count resolution rule.
    """
    return []  # Out of scope per spec — C# namespace resolution not implemented


# ── Ruby ──────────────────────────────────────────────────────────────────────


def _ruby_require_string_content(string_node: Any) -> str | None:
    """Extract the content from a Ruby string node.

    Ruby string: string → [" string_content "]
    Falls back to stripping quotes from full text if string_content not found.
    """
    try:
        for child in string_node.named_children:
            if child.type == "string_content":
                return _text_node(child)
        # Fallback: strip surrounding single/double quotes.
        raw = _text_node(string_node)
        return raw.strip("'\"")
    except Exception:  # noqa: BLE001
        return None


def _extract_ruby(root: object, filepath: Path) -> _ImportMappingList:
    """Extract ImportMapping records from a Ruby AST root node.

    Handles:
        require 'json'           → local_name='json', source_module='json'
        require_relative './x'   → local_name='x', source_module='./x'
        require 'active_record'  → local_name='active_record', source_module='active_record'

    Resolution: require_relative './x' → relative .rb file probe (via _resolve_ruby).
    require 'x' → [] (gem/load path out of scope per spec).
    Never raises. Returns [] on any failure.
    """
    result: _ImportMappingList = []
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return result

        def _walk(node: Any) -> None:
            # Ruby require/require_relative appear as top-level 'call' nodes.
            if node.type == "call":
                method_node = node.child_by_field_name("method")
                if method_node is None or method_node.type != "identifier":
                    # Not a plain identifier call — skip, still recurse.
                    for child in node.named_children:
                        _walk(child)
                    return

                method_name = _text_node(method_node)
                line = node.start_point[0] + 1

                if method_name in ("require", "require_relative"):
                    arg_list = node.child_by_field_name("arguments")
                    if arg_list is not None:
                        for child in arg_list.named_children:
                            if child.type == "string":
                                content = _ruby_require_string_content(child)
                                if content:
                                    from pathlib import Path as _Path
                                    local = _Path(content).stem
                                    result.append(
                                        _make_mapping(
                                            local_name=local,
                                            exported_name=local,
                                            source_module=content,
                                            line=line,
                                            is_default=True,
                                        )
                                    )
                    return  # don't recurse into require call

                # Recurse into other calls' children
                for child in node.named_children:
                    _walk(child)
                return

            for child in node.named_children:
                _walk(child)

        _walk(root)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_ruby: extraction failed for %s: %r", filepath, exc)
    return result


def _resolve_ruby(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a Ruby require source to file paths.

    require_relative './x' → probe same directory as referencing_file for x.rb.
    require 'x'            → [] (gem / load path resolution out of scope per spec).

    Never raises.
    """
    try:
        # Only resolve relative paths (those starting with ./ or ../).
        if not (source_module.startswith("./") or source_module.startswith("../")):
            return []  # absolute gem name — out of scope

        ref_dir = referencing_file.parent
        # The source_module may or may not include .rb extension.
        if not source_module.endswith(".rb"):
            candidate = ref_dir / (source_module + ".rb")
        else:
            candidate = ref_dir / source_module

        if candidate.exists():
            return [str(candidate)]

        # Fallback: try without extension adjustment.
        candidate2 = ref_dir / source_module
        if candidate2.exists():
            return [str(candidate2)]
    except Exception:  # noqa: BLE001
        pass
    return []


# ── C ─────────────────────────────────────────────────────────────────────────


def _c_extract_include(node: Any, filepath: Path) -> _ImportMapping | None:
    """Extract an ImportMapping from a C/C++ preproc_include node.

    Handles:
        #include "utils.h"   → local_name='utils', source_module='utils.h'
        #include <stdio.h>   → local_name='stdio', source_module='<stdio.h>'

    Returns None if the include path cannot be resolved.
    """
    try:
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return None

        line = node.start_point[0] + 1

        if path_node.type == "string_literal":
            # Local include: #include "utils.h"
            content = None
            for child in path_node.children:
                if child.type == "string_content":
                    content = _text_node(child)
                    break
            if content is None:
                content = _text_node(path_node).strip('"')
            if not content:
                return None
            stem = Path(content).stem
            return _make_mapping(
                local_name=stem,
                exported_name=stem,
                source_module=content,
                line=line,
                is_default=True,
            )

        if path_node.type == "system_lib_string":
            # System include: #include <stdio.h>
            raw = _text_node(path_node).strip("<>")
            if not raw:
                return None
            stem = Path(raw).stem
            return _make_mapping(
                local_name=stem,
                exported_name=stem,
                source_module=f"<{raw}>",
                line=line,
                is_default=True,
            )
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_c(root: object, filepath: Path) -> _ImportMappingList:
    """Extract ImportMapping records from a C AST root node.

    Handles:
        #include "utils.h"   → local_name='utils', source_module='utils.h'
        #include <stdio.h>   → local_name='stdio', source_module='<stdio.h>'

    Resolution: relative #include "x.h" → best-effort file path probe
    (same dir, then repo_root). System #include <x> → [].
    Never raises. Returns [] on any failure.
    """
    result: _ImportMappingList = []
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return result

        def _walk(node: Any) -> None:
            if node.type == "preproc_include":
                mapping = _c_extract_include(node, filepath)
                if mapping is not None:
                    result.append(mapping)
                return  # no need to recurse into include node
            for child in node.named_children:
                _walk(child)

        _walk(root)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_c: extraction failed for %s: %r", filepath, exc)
    return result


def _resolve_c(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a C #include path to source file paths.

    #include "x.h"  → probe same directory as referencing_file, then repo_root.
    #include <x.h>  → [] (system header — out of scope per spec).

    Returns a list of matching file paths (as str), or [] if not found.
    Never raises.
    """
    try:
        # System includes start with '<' — out of scope per spec
        if source_module.startswith("<"):
            return []

        # Local include: probe relative to the referencing file's directory
        ref_dir = referencing_file.parent
        candidate = ref_dir / source_module
        if candidate.exists():
            return [str(candidate)]

        # Fallback: probe relative to repo root
        candidate2 = repo_root / source_module
        if candidate2.exists():
            return [str(candidate2)]
    except Exception:  # noqa: BLE001
        pass
    return []


# ── C++ ───────────────────────────────────────────────────────────────────────


def _extract_cpp(root: object, filepath: Path) -> _ImportMappingList:
    """Extract ImportMapping records from a C++ AST root node.

    C++ uses the same preproc_include mechanism as C. Both local and system
    includes are extracted; system includes resolve to [] at read time.
    Never raises. Returns [] on any failure.
    """
    # C++ and C share the same preproc_include grammar node — reuse _extract_c.
    return _extract_c(root, filepath)


def _resolve_cpp(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a C++ #include path to source file paths.

    #include "x.h"  → probe same dir, then repo_root (same as C).
    #include <x>    → [] (system/STL header — out of scope per spec).

    Never raises.
    """
    return _resolve_c(source_module, referencing_file, repo_root)


# ── PHP ───────────────────────────────────────────────────────────────────────


def _php_qualified_name_last_segment(node: Any) -> str | None:
    """Extract the last 'name' segment from a PHP qualified_name node.

    qualified_name: [namespace_name, "\\", name]
    Returns the final 'name' child text (e.g. 'User' from 'App\\Models\\User').
    Also handles bare 'name' nodes directly.
    """
    try:
        if node.type == "name":
            return _text_node(node)
        if node.type == "qualified_name":
            # Find the last 'name' child (after all backslashes).
            last_name = None
            for child in node.children:
                if child.type == "name":
                    last_name = _text_node(child)
            return last_name
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_php(root: object, filepath: Path) -> _ImportMappingList:
    """Extract ImportMapping records from a PHP AST root node.

    Handles:
        use App\\Models\\User;         → local_name='User', source_module='App\\Models\\User'
        use App\\Services\\Logger;     → local_name='Logger', source_module='App\\Services\\Logger'

    Resolution: PSR-4 autoload mapping is out of scope per the spec → returns [].
    Never raises. Returns [] on any failure.
    """
    result: _ImportMappingList = []
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return result

        def _walk(node: Any) -> None:
            if node.type == "namespace_use_declaration":
                line = node.start_point[0] + 1
                for child in node.named_children:
                    if child.type == "namespace_use_clause":
                        # namespace_use_clause → qualified_name or name
                        for qnode in child.named_children:
                            last = _php_qualified_name_last_segment(qnode)
                            full = _text_node(qnode).strip()
                            if last:
                                result.append(
                                    _make_mapping(
                                        local_name=last,
                                        exported_name=last,
                                        source_module=full,
                                        line=line,
                                        is_default=True,
                                    )
                                )
                return  # no recursion needed into use declaration

            for child in node.named_children:
                _walk(child)

        _walk(root)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_php: extraction failed for %s: %r", filepath, exc)
    return result


def _resolve_php(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a PHP use statement to source file paths.

    PSR-4 autoload mapping is out of scope per the spec.
    Returns [] — degrading cleanly to the name-count resolution rule.

    WHY: PHP PSR-4 requires a composer.json namespace→directory mapping which
    is not available at extraction time. Returning [] causes confidence.py to
    fall back to the name-count rule (AMBIGUOUS if multiple declarations exist).
    """
    return []  # Out of scope per spec — PHP PSR-4 autoload not implemented
