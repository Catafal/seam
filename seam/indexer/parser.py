"""Tree-sitter parsing layer — one function per supported language.

Returns raw tree-sitter root Nodes for graph.py to interpret.
Never raises on parse errors; returns None instead.

Guards applied before parsing:
  1. File size > SEAM_MAX_FILE_BYTES  → None
  2. Binary file (null byte in first 1KB) → None
  3. Any read / OS error               → None
  4. broad Exception backstop          → None
"""

from pathlib import Path

import tree_sitter_c as tsc
import tree_sitter_c_sharp as tscsharp
import tree_sitter_cpp as tscpp
import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_php as tsphp
import tree_sitter_python as tspython
import tree_sitter_ruby as tsruby
import tree_sitter_rust as tsrust
import tree_sitter_swift as tsswift
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

import seam.config as config

# Build Language objects once at module level (cheap singletons).
# Phase 9: six new grammars added alongside the original five.
_PY_LANG = Language(tspython.language())
_TS_LANG = Language(tstypescript.language_typescript())
# TSX grammar is a superset of JS+JSX — used for .js/.mjs/.cjs (no separate JS dep)
_TSX_LANG = Language(tstypescript.language_tsx())
_GO_LANG = Language(tsgo.language())
_RUST_LANG = Language(tsrust.language())
# Phase 9 grammars
_JAVA_LANG = Language(tsjava.language())
_CSHARP_LANG = Language(tscsharp.language())
_RUBY_LANG = Language(tsruby.language())
_C_LANG = Language(tsc.language())
_CPP_LANG = Language(tscpp.language())
# PHP: language_php() (not language()) to handle the <?php open tag correctly.
_PHP_LANG = Language(tsphp.language_php())
# Phase 10 — Swift
_SWIFT_LANG = Language(tsswift.language())


def _parse(path: Path, language: Language) -> Node | None:
    """Internal helper: guard checks then parse with tree-sitter.

    Returns the root_node of the parsed tree, or None if the file should
    be skipped.  Never raises — the outer except is the final backstop.
    """
    try:
        # Guard 1: file size
        try:
            file_size = path.stat().st_size
        except OSError:
            return None  # file does not exist or unreadable

        if file_size > config.SEAM_MAX_FILE_BYTES:
            return None

        # Guard 2: binary check — read first 1 KB and look for null byte
        try:
            with path.open("rb") as fh:
                header = fh.read(1024)
        except OSError:
            return None

        if b"\x00" in header:
            return None  # binary file, skip gracefully

        # Guard 3: read full content
        try:
            source_bytes = path.read_bytes()
        except OSError:
            return None

        # Parse — tree-sitter never raises on syntax errors, returns ERROR nodes
        parser = Parser(language)
        tree = parser.parse(source_bytes)
        return tree.root_node

    except Exception:  # noqa: BLE001 — broad backstop so parsers never raise
        return None


def parse_python(path: Path) -> Node | None:
    """Parse a Python source file.

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed Python still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _PY_LANG)


def parse_typescript(path: Path) -> Node | None:
    """Parse a TypeScript (.ts / .tsx) source file.

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    """
    return _parse(path, _TS_LANG)


def parse_javascript(path: Path) -> Node | None:
    """Parse a JavaScript (.js / .mjs / .cjs) source file using the TSX grammar.

    tree-sitter-typescript's TSX grammar is a superset that covers JS+JSX.
    No separate JS grammar is needed; this is a deliberate Phase 0 decision.
    See lessons.md: '2026-06-01 — JavaScript parsed via the TSX grammar'.
    """
    return _parse(path, _TSX_LANG)


def parse_go(path: Path) -> Node | None:
    """Parse a Go source file (.go).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed Go still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _GO_LANG)


def parse_rust(path: Path) -> Node | None:
    """Parse a Rust source file (.rs).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed Rust still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _RUST_LANG)


# ── Phase 9 parsers ────────────────────────────────────────────────────────────


def parse_java(path: Path) -> Node | None:
    """Parse a Java source file (.java).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed Java still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _JAVA_LANG)


def parse_csharp(path: Path) -> Node | None:
    """Parse a C# source file (.cs).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed C# still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _CSHARP_LANG)


def parse_ruby(path: Path) -> Node | None:
    """Parse a Ruby source file (.rb).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed Ruby still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _RUBY_LANG)


def parse_c(path: Path) -> Node | None:
    """Parse a C source file (.c / .h).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Both .c and .h files use the same C grammar — .h→C is a deliberate MVP decision.
    Malformed C still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _C_LANG)


def parse_cpp(path: Path) -> Node | None:
    """Parse a C++ source file (.cpp / .cc / .cxx / .hpp / .hh / .hxx / .c++).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed C++ still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _CPP_LANG)


def parse_php(path: Path) -> Node | None:
    """Parse a PHP source file (.php).

    Uses language_php() (not language()) which handles the <?php open tag.
    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed PHP still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _PHP_LANG)


# ── Phase 10 parser ────────────────────────────────────────────────────────────


def parse_swift(path: Path) -> Node | None:
    """Parse a Swift source file (.swift).

    Returns tree-sitter root Node, or None for binary/oversized/unreadable files.
    Malformed Swift still returns a (possibly partial) tree with ERROR nodes.
    """
    return _parse(path, _SWIFT_LANG)
