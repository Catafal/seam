"""Import mapping extraction and resolution for Phase 5 confidence promotion.

Leaf module — imports ONLY stdlib + tree_sitter types + seam.indexer TypedDicts.
Must NOT import seam.query, seam.server, traversal.py, flows.py, or confidence.py.

Provides:
    ImportMapping (TypedDict) — one import binding from a source file.
    extract_import_mappings(root, filepath, language) -> list[ImportMapping]
        Parse a file's AST and return all import bindings.
        Never raises — returns [] on any failure.
    resolve_import_source(source_module, referencing_file, repo_root, language) -> list[str]
        Map an import source string to absolute file paths that exist on disk.
        Returns [] for third-party / unresolvable sources.
    compute_path_proximity(referencing_file, candidate_file) -> int
        Pure path-distance score (shared directory segment count).
        Higher = closer. Used for AMBIGUOUS tie-break (step D).

Per-language extension resolution order:
    Python  : ['.py', '/__init__.py']
    Rust    : ['.rs', '/mod.rs']
    TS      : ['.ts', '.tsx', '.d.ts', '.js']
    JS      : ['.js', '.mjs', '.cjs', '/index.js']
    Go      : package directory (last segment of path)

Out of scope: tsconfig aliases, Go-module prefix stripping, barrel chasing.
"""

import logging
import os
from pathlib import Path
from typing import TypedDict

# Phase 9: import the new-language stub extractors/resolvers from the companion leaf module.
# imports_ext is a leaf (no seam deps) so importing it here does not create a cycle.
from seam.analysis.imports_ext import (
    _extract_c as _ext_extract_c,
)
from seam.analysis.imports_ext import (
    _extract_cpp as _ext_extract_cpp,
)
from seam.analysis.imports_ext import (
    _extract_csharp as _ext_extract_csharp,
)
from seam.analysis.imports_ext import (
    _extract_java as _ext_extract_java,
)
from seam.analysis.imports_ext import (
    _extract_php as _ext_extract_php,
)
from seam.analysis.imports_ext import (
    _extract_ruby as _ext_extract_ruby,
)
from seam.analysis.imports_ext import (
    _resolve_c as _ext_resolve_c,
)
from seam.analysis.imports_ext import (
    _resolve_cpp as _ext_resolve_cpp,
)
from seam.analysis.imports_ext import (
    _resolve_csharp as _ext_resolve_csharp,
)
from seam.analysis.imports_ext import (
    _resolve_java as _ext_resolve_java,
)
from seam.analysis.imports_ext import (
    _resolve_php as _ext_resolve_php,
)
from seam.analysis.imports_ext import (
    _resolve_ruby as _ext_resolve_ruby,
)

logger = logging.getLogger(__name__)


# ── Data types ────────────────────────────────────────────────────────────────


class ImportMapping(TypedDict):
    """One import binding extracted from a source file.

    Fields:
        local_name:    The name used in the referencing file (e.g. alias or original).
        exported_name: The original name in the source module (== local_name if no alias).
        source_module: The import source as written (e.g. 'app.parser', './utils').
        is_default:    True for Python 'import X' or TS 'import X from ...' (default).
        is_namespace:  True for TS 'import * as ns from ...' or similar namespace import.
        is_wildcard:   True for 'from x import *' or 'use x::*' — no specific binding.
        line:          1-based source line number of the import statement.
    """

    local_name: str
    exported_name: str
    source_module: str
    is_default: bool
    is_namespace: bool
    is_wildcard: bool
    line: int


# ── Per-language extension resolution orders ──────────────────────────────────
# Each entry is tried in order. Strings starting with '/' are appended as
# a directory/filename suffix (e.g. '/__init__.py') rather than replacing the ext.

_PY_EXTENSIONS = [".py", "/__init__.py"]
_TS_EXTENSIONS = [".ts", ".tsx", ".d.ts", ".js"]
_JS_EXTENSIONS = [".js", ".mjs", ".cjs", "/index.js"]
_RS_EXTENSIONS = [".rs", "/mod.rs"]


# ── Python import extraction ──────────────────────────────────────────────────


def _extract_python(root: object, filepath: Path) -> list[ImportMapping]:
    """Extract ImportMapping records from a Python AST root node.

    Handles:
      - import X
      - import X as Y
      - from X import Y
      - from X import Y as Z
      - from X import *
      - from . import x  (relative)
      - from .mod import x  (relative)
    Never raises.
    """
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return []
        mappings: list[ImportMapping] = []
        _walk_python(root, filepath, mappings)
        return mappings
    except Exception as exc:  # noqa: BLE001
        # Log at debug so failures are traceable without spamming production output.
        logger.debug("_extract_python: extraction failed for %s: %r", filepath, exc)
        return []


def _text(node: object) -> str:
    """Extract UTF-8 text from a tree-sitter Node."""
    try:
        from tree_sitter import Node as TSNode

        if isinstance(node, TSNode) and node.text is not None:
            return node.text.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _walk_python(node: object, filepath: Path, mappings: list[ImportMapping]) -> None:
    """Recursive AST walker for Python import extraction."""
    from tree_sitter import Node

    if not isinstance(node, Node):
        return
    line = node.start_point[0] + 1

    if node.type == "import_statement":
        # import X  /  import X as Y  /  import X, Y
        for child in node.children:
            if child.type == "dotted_name":
                name = _text(child)
                mappings.append(
                    ImportMapping(
                        local_name=name,
                        exported_name=name,
                        source_module=name,
                        is_default=True,
                        is_namespace=False,
                        is_wildcard=False,
                        line=line,
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    name = _text(name_node)
                    alias = _text(alias_node) if alias_node else name
                    mappings.append(
                        ImportMapping(
                            local_name=alias,
                            exported_name=name,
                            source_module=name,
                            is_default=True,
                            is_namespace=False,
                            is_wildcard=False,
                            line=line,
                        )
                    )

    elif node.type == "import_from_statement":
        # from X import Y  /  from X import Y as Z  /  from X import *
        # Also handles relative: from . import x, from .mod import x
        source = _python_from_source(node)
        found_import_kw = False
        for child in node.children:
            if child.type == "import":
                found_import_kw = True
                continue
            if not found_import_kw:
                continue
            if child.type == "wildcard_import":
                # Wildcard import: extracted so the module source is recorded,
                # but is_wildcard=True causes confidence.py to skip promotion
                # because no specific exported name is bound.
                mappings.append(
                    ImportMapping(
                        local_name="*",
                        exported_name="*",
                        source_module=source,
                        is_default=False,
                        is_namespace=False,
                        is_wildcard=True,
                        line=line,
                    )
                )
            elif child.type in ("dotted_name", "identifier"):
                name = _text(child)
                mappings.append(
                    ImportMapping(
                        local_name=name,
                        exported_name=name,
                        source_module=source,
                        is_default=False,
                        is_namespace=False,
                        is_wildcard=False,
                        line=line,
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    name = _text(name_node)
                    alias = _text(alias_node) if alias_node else name
                    mappings.append(
                        ImportMapping(
                            local_name=alias,
                            exported_name=name,
                            source_module=source,
                            is_default=False,
                            is_namespace=False,
                            is_wildcard=False,
                            line=line,
                        )
                    )

    else:
        for child in node.children:
            _walk_python(child, filepath, mappings)


def _python_from_source(node: object) -> str:
    """Extract the source module from a Python import_from_statement node.

    Handles: 'from app.mod import ...' → 'app.mod'
    And relative: 'from . import ...' → '.' | 'from .mod import ...' → '.mod'

    tree-sitter Python AST structure:
      import_from_statement:
        from
        relative_import:           ← present for relative imports
          import_prefix: '.' | '..'
          dotted_name: 'parser'    ← optional
        dotted_name: 'app.mod'     ← present for absolute imports
        import
        ...
    """
    try:
        from tree_sitter import Node

        if not isinstance(node, Node):
            return ""
        dots = 0
        module_part = ""
        seen_from = False
        for child in node.children:
            if child.type == "from":
                seen_from = True
                continue
            if child.type == "import":
                break
            if not seen_from:
                continue
            if child.type == "relative_import":
                # Relative import: contains import_prefix (dots) + optional dotted_name
                for sub in child.children:
                    if sub.type == "import_prefix":
                        dots = len(_text(sub))
                    elif sub.type == "dotted_name":
                        module_part = _text(sub)
            elif child.type == "dotted_name":
                # Absolute import: 'from app.parser import ...'
                module_part = _text(child)
        prefix = "." * dots
        return f"{prefix}{module_part}" if module_part else prefix or ""
    except Exception:  # noqa: BLE001
        return ""


# ── TypeScript / JavaScript import extraction ─────────────────────────────────


def _extract_typescript(root: object, filepath: Path) -> list[ImportMapping]:
    """Extract ImportMapping records from a TypeScript/JS AST root node.

    Handles:
      - import { foo } from './bar'
      - import { x as y } from './bar'
      - import Default from './bar'   (default import)
      - import * as ns from './bar'   (namespace import)
      - import './side'               (side-effect only — no binding, skip)
    Never raises.
    """
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return []
        mappings: list[ImportMapping] = []
        _walk_typescript(root, filepath, mappings)
        return mappings
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_typescript: extraction failed for %s: %r", filepath, exc)
        return []


def _walk_typescript(node: object, filepath: Path, mappings: list[ImportMapping]) -> None:
    """Recursive AST walker for TypeScript/JS import extraction."""
    from tree_sitter import Node

    if not isinstance(node, Node):
        return
    line = node.start_point[0] + 1

    if node.type == "import_statement":
        # Extract source module from the 'string' child
        source = ""
        for child in node.children:
            if child.type == "string":
                raw = _text(child)
                source = raw.strip("'\"")
                break

        if not source:
            # Side-effect-only import or unparseable → no bindings
            for child in node.children:
                _walk_typescript(child, filepath, mappings)
            return

        # Find the import_clause
        for child in node.children:
            if child.type == "import_clause":
                _extract_ts_clause(child, source, line, mappings)
                break
        return

    for child in node.children:
        _walk_typescript(child, filepath, mappings)


def _extract_ts_clause(
    clause: object,
    source: str,
    line: int,
    mappings: list[ImportMapping],
) -> None:
    """Extract bindings from a TypeScript import_clause node."""
    from tree_sitter import Node

    if not isinstance(clause, Node):
        return

    for child in clause.children:
        if child.type == "identifier":
            # Default import: import Foo from '...'
            name = _text(child)
            mappings.append(
                ImportMapping(
                    local_name=name,
                    exported_name=name,
                    source_module=source,
                    is_default=True,
                    is_namespace=False,
                    is_wildcard=False,
                    line=line,
                )
            )

        elif child.type == "namespace_import":
            # import * as ns from '...'
            for sub in child.children:
                if sub.type == "identifier":
                    alias = _text(sub)
                    mappings.append(
                        ImportMapping(
                            local_name=alias,
                            exported_name="*",
                            source_module=source,
                            is_default=False,
                            is_namespace=True,
                            is_wildcard=False,
                            line=line,
                        )
                    )
                    break

        elif child.type == "named_imports":
            # import { foo, bar as b } from '...'
            for spec in child.children:
                if spec.type == "import_specifier":
                    # local_name is the alias used inside this file (call-site match);
                    # exported_name is the original name from the declaring module
                    # (used by confidence.py to look up the symbol in the index).
                    name_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    if name_node is None and spec.children:
                        name_node = spec.children[0]
                    if name_node:
                        orig_name = _text(name_node)
                        local = _text(alias_node) if alias_node else orig_name
                        mappings.append(
                            ImportMapping(
                                local_name=local,
                                exported_name=orig_name,
                                source_module=source,
                                is_default=False,
                                is_namespace=False,
                                is_wildcard=False,
                                line=line,
                            )
                        )


# ── Go import extraction ──────────────────────────────────────────────────────


def _extract_go(root: object, filepath: Path) -> list[ImportMapping]:
    """Extract ImportMapping records from a Go AST root node.

    Handles:
      - import "module/pkg"      → local_name = last segment of path
      - import p "module/pkg"    → local_name = p
      - Grouped import blocks
    Never raises.
    """
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return []
        mappings: list[ImportMapping] = []
        _walk_go(root, filepath, mappings)
        return mappings
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_go: extraction failed for %s: %r", filepath, exc)
        return []


def _walk_go(node: object, filepath: Path, mappings: list[ImportMapping]) -> None:
    """Recursive AST walker for Go import extraction."""
    from tree_sitter import Node

    if not isinstance(node, Node):
        return
    line = node.start_point[0] + 1

    if node.type == "import_declaration":
        for child in node.children:
            if child.type == "import_spec_list":
                for spec in child.children:
                    if spec.type == "import_spec":
                        _extract_go_spec(spec, line, mappings)
            elif child.type == "import_spec":
                _extract_go_spec(child, line, mappings)
        return

    for child in node.children:
        _walk_go(child, filepath, mappings)


def _extract_go_spec(spec: object, line: int, mappings: list[ImportMapping]) -> None:
    """Extract one Go import_spec into an ImportMapping."""
    from tree_sitter import Node

    if not isinstance(spec, Node):
        return

    path_node = spec.child_by_field_name("path")
    name_node = spec.child_by_field_name("name")

    if path_node is None:
        return

    raw_path = _text(path_node).strip('"')  # e.g. "fmt" or "github.com/user/pkg"
    # Go convention: local name is the last path segment (package name)
    last_segment = raw_path.rsplit("/", 1)[-1]

    if name_node:
        # Explicit alias: import p "path"
        alias = _text(name_node)
        if alias == "_":
            return  # blank import — no binding
        local = alias
    else:
        local = last_segment

    mappings.append(
        ImportMapping(
            local_name=local,
            exported_name=last_segment,
            source_module=raw_path,
            is_default=False,
            is_namespace=False,
            is_wildcard=False,
            line=line,
        )
    )


# ── Rust use statement extraction ─────────────────────────────────────────────


def _extract_rust(root: object, filepath: Path) -> list[ImportMapping]:
    """Extract ImportMapping records from a Rust AST root node.

    Handles:
      - use crate::mod::Thing;
      - use super::x;
      - use a::b::{c, d};
      - use x::* (wildcard)
      - use a::b as c (alias)
    Never raises.
    """
    try:
        from tree_sitter import Node

        if not isinstance(root, Node):
            return []
        mappings: list[ImportMapping] = []
        _walk_rust(root, filepath, mappings)
        return mappings
    except Exception as exc:  # noqa: BLE001
        logger.debug("_extract_rust: extraction failed for %s: %r", filepath, exc)
        return []


def _walk_rust(node: object, filepath: Path, mappings: list[ImportMapping]) -> None:
    """Recursive AST walker for Rust use-declaration extraction."""
    from tree_sitter import Node

    if not isinstance(node, Node):
        return
    line = node.start_point[0] + 1

    if node.type == "use_declaration":
        # Recurse into the use tree
        for child in node.children:
            if child.type != "use":
                _extract_rust_use_tree(child, "", line, mappings)
        return

    for child in node.children:
        _walk_rust(child, filepath, mappings)


def _extract_rust_use_tree(
    node: object,
    prefix: str,
    line: int,
    mappings: list[ImportMapping],
) -> None:
    """Recursively expand a Rust use_tree into ImportMapping records."""
    from tree_sitter import Node

    if not isinstance(node, Node):
        return

    if node.type == "use_wildcard":
        # use path::*
        source = prefix.rstrip("::")
        mappings.append(
            ImportMapping(
                local_name="*",
                exported_name="*",
                source_module=source,
                is_default=False,
                is_namespace=False,
                is_wildcard=True,
                line=line,
            )
        )
        return

    if node.type == "use_as_clause":
        # use a::b as c
        path_node = node.child_by_field_name("path")
        alias_node = node.child_by_field_name("alias")
        if path_node and alias_node:
            path = _text(path_node)
            alias = _text(alias_node)
            source = f"{prefix.rstrip('::')}::{path}" if prefix else path
            # Last segment of path is the exported name
            exported = path.rsplit("::", 1)[-1]
            mappings.append(
                ImportMapping(
                    local_name=alias,
                    exported_name=exported,
                    source_module=source.replace(f"::{exported}", "") or source,
                    is_default=False,
                    is_namespace=False,
                    is_wildcard=False,
                    line=line,
                )
            )
        return

    if node.type == "use_list":
        # use a::{b, c, d}
        for child in node.children:
            _extract_rust_use_tree(child, prefix, line, mappings)
        return

    if node.type == "scoped_use_list":
        # use a::b::{c, d}  — has a 'path' child and a 'list' child
        path_node = node.child_by_field_name("path")
        list_node = node.child_by_field_name("list")
        new_prefix = ""
        if path_node:
            path_text = _text(path_node)
            new_prefix = f"{prefix}{path_text}::" if prefix else f"{path_text}::"
        if list_node:
            _extract_rust_use_tree(list_node, new_prefix, line, mappings)
        return

    if node.type in ("use_tree", "identifier", "scoped_identifier"):
        # Simple use item or fully qualified path
        text = _text(node)
        if not text or text in (";", "{", "}", ",", "use"):
            return
        # Last segment is the local binding name
        local = text.rsplit("::", 1)[-1]
        source = f"{prefix.rstrip('::')}" if prefix else text
        if prefix:
            full = f"{prefix}{text}"
            source = full.rsplit("::", 1)[0] if "::" in full else prefix
        mappings.append(
            ImportMapping(
                local_name=local,
                exported_name=local,
                source_module=source,
                is_default=False,
                is_namespace=False,
                is_wildcard=False,
                line=line,
            )
        )
        return

    # Recurse into child nodes for composite structures
    for child in node.children:
        _extract_rust_use_tree(child, prefix, line, mappings)


# ── Public: extract_import_mappings ──────────────────────────────────────────


def extract_import_mappings(
    root: object,
    filepath: Path,
    language: str,
) -> list[ImportMapping]:
    """Extract all import/use mappings from an AST root node.

    Per-language dispatch: Python, TypeScript, JavaScript, Go, Rust.
    Never raises — returns [] on any failure.

    Args:
        root:     tree-sitter root Node from parser.parse_*(path).
        filepath: Absolute path to the source file being parsed.
        language: Language identifier (must match SEAM_LANGUAGE_MAP values).

    Returns:
        List of ImportMapping TypedDicts. Empty list on parse error or no imports.
    """
    if root is None:
        return []
    try:
        if language == "python":
            return _extract_python(root, filepath)
        if language in ("typescript", "javascript"):
            return _extract_typescript(root, filepath)
        if language == "go":
            return _extract_go(root, filepath)
        if language == "rust":
            return _extract_rust(root, filepath)
        # Phase 9 — new languages routed to imports_ext (stubs return [])
        if language == "java":
            return _ext_extract_java(root, filepath)
        if language == "csharp":
            return _ext_extract_csharp(root, filepath)
        if language == "ruby":
            return _ext_extract_ruby(root, filepath)
        if language == "c":
            return _ext_extract_c(root, filepath)
        if language == "cpp":
            return _ext_extract_cpp(root, filepath)
        if language == "php":
            return _ext_extract_php(root, filepath)
    except Exception:  # noqa: BLE001
        pass
    return []


# ── Public: resolve_import_source ─────────────────────────────────────────────


def resolve_import_source(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
    language: str,
) -> list[str]:
    """Map an import source string to candidate absolute file paths.

    Tries per-language extension resolution order against the file system.
    Relative sources (starting with '.') resolve from the referencing file's dir.
    Absolute/dotted module paths resolve from repo_root using dot→slash conversion.
    Third-party sources that don't map to an indexed file return [].

    Out of scope: tsconfig aliases, Go module prefix stripping, barrel chasing.

    Args:
        source_module:    Import source as written (e.g. './parser', 'app.parser').
        referencing_file: Absolute path of the importing file (for relative resolution).
        repo_root:        Repository root (for absolute module resolution).
        language:         Language identifier.

    Returns:
        List of existing absolute file path strings (may be empty).
        Never raises.
    """
    if not source_module:
        return []
    try:
        if language == "python":
            return _resolve_python(source_module, referencing_file, repo_root)
        if language in ("typescript", "javascript"):
            exts = _TS_EXTENSIONS if language == "typescript" else _JS_EXTENSIONS
            return _resolve_relative_or_dotted(source_module, referencing_file, repo_root, exts)
        if language == "go":
            return _resolve_go(source_module, referencing_file, repo_root)
        if language == "rust":
            return _resolve_rust(source_module, referencing_file, repo_root)
        # Phase 9 — new languages routed to imports_ext (stubs/out-of-scope return [])
        if language == "java":
            return _ext_resolve_java(source_module, referencing_file, repo_root)
        if language == "csharp":
            return _ext_resolve_csharp(source_module, referencing_file, repo_root)
        if language == "ruby":
            return _ext_resolve_ruby(source_module, referencing_file, repo_root)
        if language == "c":
            return _ext_resolve_c(source_module, referencing_file, repo_root)
        if language == "cpp":
            return _ext_resolve_cpp(source_module, referencing_file, repo_root)
        if language == "php":
            return _ext_resolve_php(source_module, referencing_file, repo_root)
    except Exception:  # noqa: BLE001
        pass
    return []


def _probe_extensions(base: Path, extensions: list[str]) -> list[str]:
    """Return existing file paths by appending each extension to base.

    Shared probe loop used by Python, TS/JS, and Rust resolvers so that the
    per-language extension lists (_PY_EXTENSIONS etc.) are the single source of
    resolution order — each resolver calls this instead of duplicating the loop.

    Args:
        base:       Candidate path stem (no extension) as a Path object.
        extensions: Ordered list of extension strings to try. Strings starting
                    with '/' are directory suffixes (e.g. '/__init__.py' for packages);
                    strings starting with '.' are regular extensions.
    """
    results: list[str] = []
    for ext in extensions:
        # String concatenation (not Path /) because '/__init__.py' must append
        # as-is; Path would normalise the leading slash away.
        candidate = Path(str(base) + ext)
        if candidate.exists():
            results.append(str(candidate))
    return results


def _resolve_python(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve Python module source to file paths using _PY_EXTENSIONS."""
    is_relative = source_module.startswith(".")

    if is_relative:
        # Count leading dots for relative level
        level = len(source_module) - len(source_module.lstrip("."))
        module_path = source_module.lstrip(".")
        base = referencing_file.parent
        for _ in range(level - 1):
            base = base.parent
        if module_path:
            candidate_base = base / module_path.replace(".", "/")
        else:
            candidate_base = base
    else:
        # Absolute: convert dots to path separators, resolve from repo_root
        candidate_base = repo_root / source_module.replace(".", "/")

    return _probe_extensions(candidate_base, _PY_EXTENSIONS)


def _resolve_relative_or_dotted(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
    extensions: list[str],
) -> list[str]:
    """Resolve TypeScript/JavaScript import source to file paths.

    Handles:
    - Relative paths starting with ./ or ../
    - Absolute-ish paths starting without . (treated as third-party → [])
    """
    if not (source_module.startswith("./") or source_module.startswith("../")):
        # Not a relative import → treat as third-party/bare specifier → []
        return []

    base = referencing_file.parent / source_module
    # os.path.normpath resolves ./ and ../ lexically without following symlinks,
    # so candidate paths match exactly how the indexer stores them (str(filepath)
    # after repo_root is resolved but individual files are NOT symlink-expanded).
    # Path.resolve() would expand /tmp → /private/tmp on macOS, breaking the
    # declaring-file lookup for any repo checked out under a symlinked prefix.
    try:
        base = Path(os.path.normpath(base))
    except Exception:  # noqa: BLE001
        return []

    return _probe_extensions(base, extensions)


def _resolve_go(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
    max_candidates: int = 25,
) -> list[str]:
    """Resolve a Go import path to a directory of .go files.

    Go imports use directory-based packages. We look for the import path as a
    subdirectory under repo_root (same-repo relative reference).

    Module-qualified Go imports (e.g. 'github.com/org/repo/pkg') are out of scope:
    they will never be_dir() under repo_root, so they correctly return [] without
    any special-casing. This resolver only handles same-repo-relative package paths
    whose directory exists directly under repo_root.
    """
    # Treat the import path as a directory relative to repo_root.
    # Module-qualified paths silently return [] because is_dir() will be False — intended.
    candidate_dir = repo_root / source_module
    if candidate_dir.is_dir():
        go_files = list(candidate_dir.glob("*.go"))
        return [str(f) for f in go_files[:max_candidates]]
    return []


def _resolve_rust(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
) -> list[str]:
    """Resolve a Rust use path to source file paths.

    Handles:
    - crate:: → relative to src/ directory
    - super:: → relative to parent directory
    - Other paths → try repo_root-relative resolution

    Extension order: ['.rs', '/mod.rs']
    """
    if source_module.startswith("crate::"):
        # Resolve relative to repo_root/src/
        parts = source_module[len("crate::") :].replace("::", "/")
        base = repo_root / "src" / parts
    elif source_module.startswith("super::"):
        parts = source_module[len("super::") :].replace("::", "/")
        base = referencing_file.parent.parent / parts
    elif source_module.startswith("self::"):
        parts = source_module[len("self::") :].replace("::", "/")
        base = referencing_file.parent / parts
    else:
        # Try as a path from repo_root
        parts = source_module.replace("::", "/")
        base = repo_root / parts

    return _probe_extensions(base, _RS_EXTENSIONS)


# ── Public: compute_path_proximity ────────────────────────────────────────────


def compute_path_proximity(referencing_file: Path, candidate_file: Path) -> int:
    """Return shared path segment count between two files' parent directories.

    Higher score = more shared directory ancestry = closer proximity.
    Pure function: no I/O, no side effects, never raises.

    Used for AMBIGUOUS edge tie-break (step D): when multiple files declare
    the same symbol name, the one that shares the most directory ancestry
    with the referencing file is the most likely intended target.

    Args:
        referencing_file: The file containing the reference (e.g. import/call).
        candidate_file:   A candidate declaring file for the referenced symbol.

    Returns:
        Number of shared parent directory path parts (>= 0).
        Identical parent directories return the full parent depth.
    """
    try:
        ref_parts = referencing_file.parent.parts
        cand_parts = candidate_file.parent.parts
        # Count shared prefix segments
        shared = 0
        for a, b in zip(ref_parts, cand_parts):
            if a == b:
                shared += 1
            else:
                break
        return shared
    except Exception:  # noqa: BLE001
        return 0
