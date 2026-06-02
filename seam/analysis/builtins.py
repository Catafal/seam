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

# ── Phase 9: new-language builtin sets ───────────────────────────────────────
# Conservative vocabulary: common builtins/globals/stdlib only — NOT exhaustive.
# An over-broad set risks shadowing real repo symbols.
# The count==0 structural guard in confidence.py is the primary safety net.

# Java common builtins — core language types, java.lang (auto-imported), common exceptions.
# WHY conservative: avoiding java.util.*, java.io.* etc. which users commonly name their own
# classes after (e.g. "List", "Map" — too likely to shadow real repo symbols).
_JAVA_BUILTINS: frozenset[str] = frozenset(
    {
        # java.lang (auto-imported in every Java file)
        "Object",
        "String",
        "Integer",
        "Long",
        "Double",
        "Float",
        "Boolean",
        "Byte",
        "Short",
        "Character",
        "Number",
        "Math",
        "System",
        "Runtime",
        "Thread",
        "Class",
        "StringBuilder",
        "StringBuffer",
        "Comparable",
        "Iterable",
        "Runnable",
        "Enum",
        "Record",
        "Void",
        # Common exceptions (java.lang)
        "Exception",
        "RuntimeException",
        "Error",
        "Throwable",
        "NullPointerException",
        "IllegalArgumentException",
        "IllegalStateException",
        "IndexOutOfBoundsException",
        "UnsupportedOperationException",
        "ArithmeticException",
        "ClassCastException",
        "ArrayIndexOutOfBoundsException",
        "StackOverflowError",
        "OutOfMemoryError",
        # Primitive wrappers (common as call targets)
        "int",
        "long",
        "double",
        "float",
        "boolean",
        "byte",
        "char",
        "short",
        "void",
        # Special values
        "null",
        "true",
        "false",
        "this",
        "super",
        # Common annotation types
        "Override",
        "Deprecated",
        "SuppressWarnings",
        "FunctionalInterface",
    }
)

# C# common builtins — System namespace types and C# keyword types.
# WHY conservative: avoiding System.Collections.*, System.IO.* etc. which risk shadowing.
_CSHARP_BUILTINS: frozenset[str] = frozenset(
    {
        # C# keyword types (aliases for BCL types)
        "string",
        "int",
        "long",
        "double",
        "float",
        "decimal",
        "bool",
        "byte",
        "short",
        "char",
        "object",
        "void",
        "uint",
        "ulong",
        "ushort",
        "sbyte",
        "nint",
        "nuint",
        # System namespace commonly used without qualifier
        "Console",
        "Math",
        "String",
        "Object",
        "Int32",
        "Int64",
        "Double",
        "Boolean",
        "Byte",
        "Char",
        "Type",
        "Enum",
        "Array",
        "Convert",
        "DateTime",
        "TimeSpan",
        "Guid",
        "Environment",
        "GC",
        "Exception",
        # Common exceptions
        "Exception",
        "SystemException",
        "ArgumentException",
        "ArgumentNullException",
        "ArgumentOutOfRangeException",
        "InvalidOperationException",
        "NotImplementedException",
        "NotSupportedException",
        "NullReferenceException",
        "IndexOutOfRangeException",
        "OverflowException",
        "DivideByZeroException",
        "OutOfMemoryException",
        "StackOverflowException",
        # Special keywords that appear as call targets
        "null",
        "true",
        "false",
        "this",
        "base",
        "nameof",
        "typeof",
        "sizeof",
        "default",
        "new",
    }
)

# Ruby common builtins — core Kernel methods, IO primitives, and common globals.
# WHY conservative: Ruby's open classes mean many common names (e.g. "format",
# "Array", "Hash") are likely real repo symbols too. Only truly global, unambiguous
# Kernel methods and constants are included.
_RUBY_BUILTINS: frozenset[str] = frozenset(
    {
        # Kernel methods (available everywhere without require)
        "puts",
        "print",
        "p",
        "pp",
        "sprintf",
        "format",
        "gets",
        "require",
        "require_relative",
        "load",
        "raise",
        "fail",
        "exit",
        "abort",
        "sleep",
        "rand",
        "srand",
        "lambda",
        "proc",
        "block_given?",
        "caller",
        "binding",
        "loop",
        "at_exit",
        # Common type constructors (capitalized Kernel conversion methods)
        "Array",
        "Integer",
        "Float",
        "String",
        "Rational",
        "Complex",
        # Core constants
        "nil",
        "true",
        "false",
        "self",
        "RUBY_VERSION",
        "RUBY_PLATFORM",
        "ARGV",
        "STDIN",
        "STDOUT",
        "STDERR",
        # Common exceptions (Kernel raise targets)
        "RuntimeError",
        "ArgumentError",
        "TypeError",
        "NameError",
        "NoMethodError",
        "StandardError",
        "Exception",
        "NotImplementedError",
        "StopIteration",
        "IndexError",
        "KeyError",
        "IOError",
        "Errno",
        "Interrupt",
    }
)

# C standard library functions — conservative subset of <stdio.h>, <stdlib.h>,
# <string.h>, <math.h>, <stddef.h>, and language keywords used as call targets.
# WHY conservative: avoiding less common stdlib names that risk shadowing real repo
# symbols. The count==0 guard in confidence.py is the primary safety net.
_C_BUILTINS: frozenset[str] = frozenset(
    {
        # <stdio.h> — the most commonly called C stdlib functions
        "printf",
        "fprintf",
        "sprintf",
        "snprintf",
        "scanf",
        "fscanf",
        "sscanf",
        "fopen",
        "fclose",
        "fread",
        "fwrite",
        "fgets",
        "fputs",
        "puts",
        "gets",
        "fflush",
        "feof",
        "ferror",
        "perror",
        "putchar",
        "getchar",
        # <stdlib.h>
        "malloc",
        "calloc",
        "realloc",
        "free",
        "exit",
        "abort",
        "atexit",
        "atoi",
        "atol",
        "atof",
        "strtol",
        "strtod",
        "rand",
        "srand",
        "abs",
        "labs",
        "qsort",
        "bsearch",
        "getenv",
        "system",
        # <string.h>
        "memcpy",
        "memmove",
        "memset",
        "memcmp",
        "strcpy",
        "strncpy",
        "strcat",
        "strncat",
        "strcmp",
        "strncmp",
        "strlen",
        "strchr",
        "strrchr",
        "strstr",
        "strtok",
        # <math.h>
        "sqrt",
        "pow",
        "fabs",
        "floor",
        "ceil",
        "sin",
        "cos",
        "tan",
        "log",
        "exp",
        # <stddef.h> / common keywords that appear as call-like expressions
        "sizeof",
        "offsetof",
        # C99 / C11 additions
        "assert",
    }
)

# C++ standard library / STL names — conservative subset covering the most common
# global names and STL entry points that appear as bare-identifier call targets.
# WHY conservative: C++ STL is vast; broad sets risk shadowing repo symbols.
_CPP_BUILTINS: frozenset[str] = frozenset(
    {
        # Namespace name itself (appears in using directives and bare calls)
        "std",
        # Common STL stream objects used without std:: qualifier
        "cout",
        "cin",
        "cerr",
        "clog",
        "endl",
        # Common STL types used as constructors / call targets
        "string",
        "vector",
        "map",
        "set",
        "list",
        "deque",
        "queue",
        "stack",
        "pair",
        "tuple",
        "array",
        "unordered_map",
        "unordered_set",
        "unique_ptr",
        "shared_ptr",
        "weak_ptr",
        "make_unique",
        "make_shared",
        "make_pair",
        "move",
        "forward",
        "swap",
        "sort",
        "find",
        "copy",
        "fill",
        "begin",
        "end",
        "size",
        "empty",
        # C stdlib (inherited in C++; all C builtins are valid C++ too)
        "printf",
        "fprintf",
        "sprintf",
        "malloc",
        "calloc",
        "realloc",
        "free",
        "exit",
        "abort",
        "strlen",
        "strcpy",
        "strcmp",
        "memcpy",
        "memset",
        "assert",
        # C++ exceptions
        "exception",
        "runtime_error",
        "logic_error",
        "invalid_argument",
        "out_of_range",
        "overflow_error",
        "bad_alloc",
        "throw",
        # C++ keywords that appear as call targets (e.g. delete, new)
        "new",
        "delete",
        "sizeof",
        "typeid",
        "static_cast",
        "dynamic_cast",
        "const_cast",
        "reinterpret_cast",
    }
)

# PHP built-in functions and language constructs — conservative subset of the most
# commonly called PHP global functions and language constructs.
# WHY conservative: PHP's function namespace is large; over-broad sets risk
# shadowing user-defined functions with the same names. The count==0 guard in
# confidence.py is the primary safety net.
_PHP_BUILTINS: frozenset[str] = frozenset(
    {
        # Language constructs (behave like functions but are keywords)
        "echo",
        "print",
        "die",
        "exit",
        "isset",
        "unset",
        "empty",
        "list",
        "array",
        "include",
        "require",
        "include_once",
        "require_once",
        # String functions
        "strlen",
        "str_len",
        "substr",
        "strpos",
        "strrpos",
        "str_replace",
        "str_contains",
        "str_starts_with",
        "str_ends_with",
        "trim",
        "ltrim",
        "rtrim",
        "strtolower",
        "strtoupper",
        "ucfirst",
        "lcfirst",
        "ucwords",
        "explode",
        "implode",
        "join",
        "split",
        "sprintf",
        "printf",
        "number_format",
        "htmlspecialchars",
        "htmlentities",
        "nl2br",
        "strip_tags",
        "md5",
        "sha1",
        "base64_encode",
        "base64_decode",
        "json_encode",
        "json_decode",
        "serialize",
        "unserialize",
        # Array functions
        "count",
        "sizeof",
        "array_map",
        "array_filter",
        "array_reduce",
        "array_merge",
        "array_push",
        "array_pop",
        "array_shift",
        "array_unshift",
        "array_slice",
        "array_splice",
        "array_keys",
        "array_values",
        "array_flip",
        "array_reverse",
        "array_unique",
        "array_search",
        "in_array",
        "sort",
        "rsort",
        "asort",
        "arsort",
        "ksort",
        "krsort",
        "usort",
        "uasort",
        "uksort",
        "range",
        "compact",
        "extract",
        # Math functions
        "abs",
        "ceil",
        "floor",
        "round",
        "max",
        "min",
        "pow",
        "sqrt",
        "rand",
        "mt_rand",
        "intval",
        "floatval",
        "strval",
        "intdiv",
        "fmod",
        # Type checking
        "is_array",
        "is_string",
        "is_int",
        "is_integer",
        "is_float",
        "is_bool",
        "is_null",
        "is_numeric",
        "is_callable",
        "is_object",
        "gettype",
        "settype",
        "var_dump",
        "var_export",
        "print_r",
        # Date/time
        "time",
        "date",
        "mktime",
        "strtotime",
        "microtime",
        # File/IO
        "file_get_contents",
        "file_put_contents",
        "file_exists",
        "is_file",
        "is_dir",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "fopen",
        "fclose",
        "fgets",
        "fread",
        "fwrite",
        "feof",
        # Error handling
        "trigger_error",
        "set_error_handler",
        "error_reporting",
        # Other common globals
        "header",
        "headers_sent",
        "session_start",
        "session_destroy",
        "ob_start",
        "ob_get_clean",
        "ob_end_clean",
        "class_exists",
        "method_exists",
        "function_exists",
        "defined",
        "define",
        "constant",
        "get_class",
        "get_parent_class",
        "is_a",
        "instanceof",
        "call_user_func",
        "call_user_func_array",
        "func_get_args",
    }
)

# ── Swift standard library types and global functions ─────────────────────────
# Conservative vocabulary: common Swift stdlib and language built-ins only.
# NOT exhaustive (no SwiftUI, no Foundation classes beyond primitives) — an
# over-broad set risks shadowing real repo symbols.
# The count==0 structural guard in confidence.py is the primary safety net.
_SWIFT_BUILTINS: frozenset[str] = frozenset(
    {
        # Global functions (commonly called as bare identifiers)
        "print", "debugPrint", "fatalError", "precondition", "preconditionFailure",
        "assert", "assertionFailure", "min", "max", "abs", "zip", "swap",
        # Primitive types
        "Int", "Int8", "Int16", "Int32", "Int64",
        "UInt", "UInt8", "UInt16", "UInt32", "UInt64",
        "Float", "Double", "Bool", "Character", "String", "Substring",
        # Collection types
        "Array", "Dictionary", "Set", "Optional", "Result",
        "Range", "ClosedRange",
        # Special values / literals
        "nil", "true", "false",
        # Type-system keywords that appear as call targets
        "Error", "AnyObject", "Any", "Void", "Never", "Self", "self", "super",
        # Common protocols used as identifiers
        "Comparable", "Equatable", "Hashable", "Codable", "Encodable", "Decodable",
        "CustomStringConvertible",
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
    # Phase 9 — new languages (empty sets; family agents populate)
    "java": _JAVA_BUILTINS,
    "csharp": _CSHARP_BUILTINS,
    "ruby": _RUBY_BUILTINS,
    "c": _C_BUILTINS,
    "cpp": _CPP_BUILTINS,
    "php": _PHP_BUILTINS,
    # Phase 10 — Swift
    "swift": _SWIFT_BUILTINS,
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
