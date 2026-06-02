"""Curated builtin/stdlib vocabulary for Phase 5 confidence resolution.

Leaf module — imports ONLY stdlib. No DB, no seam.query, no seam.server.

Provides:
    is_builtin(name, language) -> bool
        Returns True if `name` is a known builtin for `language`.
        Pure function: no I/O, no side effects, never raises.

Design constraints:
    - Conservative sets: common builtins/globals/prelude only — NOT exhaustive stdlib.
      An over-broad set risks shadowing real repo symbols.
    - The repo-declares-it guard in confidence.py (count==0 check) is the safety net.
      A tight list here is the first line of defense.
    - Language-scoped: a name that is a Go builtin doesn't suppress a Python edge.
      Each language's frozenset is fully independent.

Called only from confidence.py when name_counts.get(target) == 0.
The builtin check MUST NOT be called when count > 0 — that structural guarantee
is enforced by confidence.py's resolve_edge() control flow (user story 5).
"""

# ── Python built-in functions and types ──────────────────────────────────────
# Source: https://docs.python.org/3/library/functions.html (conservative subset)
_PYTHON_BUILTINS: frozenset[str] = frozenset(
    {
        # Built-in functions
        "abs",
        "aiter",
        "all",
        "anext",
        "any",
        "ascii",
        "bin",
        "bool",
        "breakpoint",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "help",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "vars",
        "zip",
        # Built-in constants
        "True",
        "False",
        "None",
        "Ellipsis",
        "NotImplemented",
        # Built-in exceptions (commonly used as call targets)
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "RuntimeError",
        "OSError",
        "IOError",
        "StopIteration",
        "GeneratorExit",
        "SystemExit",
        "KeyboardInterrupt",
        "NotImplementedError",
        "OverflowError",
        "ZeroDivisionError",
        "FileNotFoundError",
        "PermissionError",
        "TimeoutError",
        "AssertionError",
        "ImportError",
        "ModuleNotFoundError",
        "MemoryError",
        "RecursionError",
        "UnicodeError",
    }
)

# ── TypeScript / JavaScript globals and built-in objects ─────────────────────
# Conservative: well-known globals present in every JS/TS runtime environment.
# Deliberately excludes browser-only (fetch, document, window) — too likely to
# collide with user-defined wrappers.
_TYPESCRIPT_BUILTINS: frozenset[str] = frozenset(
    {
        # Core JS globals
        "undefined",
        "null",
        "NaN",
        "Infinity",
        # Functions
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "encodeURI",
        "decodeURI",
        "encodeURIComponent",
        "decodeURIComponent",
        "eval",
        # Built-in constructors / objects
        "Object",
        "Array",
        "Function",
        "Boolean",
        "Number",
        "String",
        "Symbol",
        "BigInt",
        "RegExp",
        "Date",
        "Error",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "WeakRef",
        "Promise",
        "Proxy",
        "Reflect",
        "ArrayBuffer",
        "DataView",
        "Int8Array",
        "Uint8Array",
        "Int16Array",
        "Uint16Array",
        "Int32Array",
        "Uint32Array",
        "Float32Array",
        "Float64Array",
        "Uint8ClampedArray",
        "BigInt64Array",
        "BigUint64Array",
        "SharedArrayBuffer",
        "Atomics",
        "JSON",
        "Math",
        "console",
        # Timers (Node.js + browser)
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "setImmediate",
        "clearImmediate",
        # Node.js process/global
        "process",
        "Buffer",
        "global",
        "globalThis",
        "require",
        "module",
        "exports",
        "__dirname",
        "__filename",
        # Generator/iterator
        "Iterator",
        "Generator",
    }
)

# JavaScript shares the same builtin vocabulary as TypeScript for our purposes.
_JAVASCRIPT_BUILTINS: frozenset[str] = _TYPESCRIPT_BUILTINS

# ── Go predeclared identifiers ────────────────────────────────────────────────
# Source: https://go.dev/ref/spec#Predeclared_identifiers
_GO_BUILTINS: frozenset[str] = frozenset(
    {
        # Built-in functions
        "append",
        "cap",
        "clear",
        "close",
        "copy",
        "delete",
        "imag",
        "len",
        "make",
        "max",
        "min",
        "new",
        "panic",
        "print",
        "println",
        "real",
        "recover",
        # Predeclared types
        "bool",
        "byte",
        "comparable",
        "complex64",
        "complex128",
        "error",
        "float32",
        "float64",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "rune",
        "string",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "any",
        # Predeclared constants
        "true",
        "false",
        "iota",
        # Predeclared zero value
        "nil",
        # Blank identifier
        "_",
    }
)

# ── Rust prelude (edition 2021) ───────────────────────────────────────────────
# Source: https://doc.rust-lang.org/std/prelude/index.html (2021 edition)
# The prelude is automatically imported into every Rust module.
# Also includes common macros used as call targets.
_RUST_BUILTINS: frozenset[str] = frozenset(
    {
        # Prelude types
        "Option",
        "Result",
        "String",
        "str",
        "Vec",
        "Box",
        "Copy",
        "Clone",
        "Send",
        "Sync",
        "Sized",
        "Unpin",
        "Drop",
        "Fn",
        "FnMut",
        "FnOnce",
        "Iterator",
        "IntoIterator",
        "DoubleEndedIterator",
        "ExactSizeIterator",
        "Extend",
        "FromIterator",
        "From",
        "Into",
        "AsRef",
        "AsMut",
        "ToOwned",
        "Default",
        "PartialEq",
        "Eq",
        "PartialOrd",
        "Ord",
        "Hash",
        "Debug",
        "Display",
        "ToString",
        # Prelude enum variants
        "Some",
        "None",
        "Ok",
        "Err",
        # Common macros (used as call targets in AST)
        "println",
        "print",
        "eprintln",
        "eprint",
        "panic",
        "assert",
        "assert_eq",
        "assert_ne",
        "unreachable",
        "unimplemented",
        "todo",
        "format",
        "write",
        "writeln",
        "vec",
        "dbg",
        # Built-in functions
        "drop",
        # Common stdlib traits used as calls
        "Default",
    }
)

# ── Language registry ─────────────────────────────────────────────────────────
# Maps language identifiers (matching SEAM_LANGUAGE_MAP values) to builtin sets.
_LANGUAGE_BUILTINS: dict[str, frozenset[str]] = {
    "python": _PYTHON_BUILTINS,
    "typescript": _TYPESCRIPT_BUILTINS,
    "javascript": _JAVASCRIPT_BUILTINS,
    "go": _GO_BUILTINS,
    "rust": _RUST_BUILTINS,
}


def is_builtin(name: str, language: str) -> bool:
    """Return True if `name` is a known builtin for `language`.

    Language-scoped: a Python builtin name does NOT affect Go edges and vice versa.
    Conservative: the builtin sets cover well-known globals/prelude only, not
    exhaustive stdlib mirrors. The caller (confidence.py) enforces that this is
    only called when name_counts.get(name, 0) == 0 (count==0 structural guard).

    Args:
        name:     The target symbol name to check.
        language: Language identifier (e.g. 'python', 'typescript', 'go', 'rust').
                  Unknown languages return False (never raises).

    Returns:
        True if the name is in the curated builtin vocabulary for the language.
        False if unknown language, empty name, or not in the set.
    """
    if not name or not language:
        return False
    builtin_set = _LANGUAGE_BUILTINS.get(language)
    if builtin_set is None:
        return False
    return name in builtin_set
