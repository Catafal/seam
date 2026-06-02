"""Unit tests for seam/analysis/builtins.py — Phase 5, Slice 1.

TDD: these tests are written BEFORE the implementation (RED phase).

Test groups:
    B1 — Python builtins are detected as builtins.
    B2 — TypeScript/JavaScript builtins are detected.
    B3 — Go builtins are detected.
    B4 — Rust prelude/builtin names are detected.
    B5 — Non-builtins are correctly excluded.
    B6 — Cross-language isolation: a Python builtin name is NOT a Go builtin.
    B7 — Never-raises contract: unknown languages return False.
"""


from seam.analysis.builtins import is_builtin


class TestPythonBuiltins:
    """B1 — Python builtin names are recognized."""

    def test_len_is_python_builtin(self) -> None:
        assert is_builtin("len", "python") is True

    def test_print_is_python_builtin(self) -> None:
        assert is_builtin("print", "python") is True

    def test_range_is_python_builtin(self) -> None:
        assert is_builtin("range", "python") is True

    def test_type_is_python_builtin(self) -> None:
        assert is_builtin("type", "python") is True

    def test_isinstance_is_python_builtin(self) -> None:
        assert is_builtin("isinstance", "python") is True

    def test_int_is_python_builtin(self) -> None:
        assert is_builtin("int", "python") is True

    def test_str_is_python_builtin(self) -> None:
        assert is_builtin("str", "python") is True

    def test_list_is_python_builtin(self) -> None:
        assert is_builtin("list", "python") is True

    def test_dict_is_python_builtin(self) -> None:
        assert is_builtin("dict", "python") is True

    def test_open_is_python_builtin(self) -> None:
        assert is_builtin("open", "python") is True

    def test_enumerate_is_python_builtin(self) -> None:
        assert is_builtin("enumerate", "python") is True

    def test_zip_is_python_builtin(self) -> None:
        assert is_builtin("zip", "python") is True

    def test_map_is_python_builtin(self) -> None:
        assert is_builtin("map", "python") is True

    def test_filter_is_python_builtin(self) -> None:
        assert is_builtin("filter", "python") is True

    def test_super_is_python_builtin(self) -> None:
        assert is_builtin("super", "python") is True

    def test_getattr_is_python_builtin(self) -> None:
        assert is_builtin("getattr", "python") is True

    def test_setattr_is_python_builtin(self) -> None:
        assert is_builtin("setattr", "python") is True

    def test_hasattr_is_python_builtin(self) -> None:
        assert is_builtin("hasattr", "python") is True


class TestTypescriptBuiltins:
    """B2 — TypeScript/JavaScript global and builtin names."""

    def test_console_is_ts_builtin(self) -> None:
        assert is_builtin("console", "typescript") is True

    def test_console_is_js_builtin(self) -> None:
        assert is_builtin("console", "javascript") is True

    def test_set_timeout_is_ts_builtin(self) -> None:
        assert is_builtin("setTimeout", "typescript") is True

    def test_set_interval_is_ts_builtin(self) -> None:
        assert is_builtin("setInterval", "typescript") is True

    def test_clear_timeout_is_ts_builtin(self) -> None:
        assert is_builtin("clearTimeout", "typescript") is True

    def test_promise_is_ts_builtin(self) -> None:
        assert is_builtin("Promise", "typescript") is True

    def test_array_is_ts_builtin(self) -> None:
        assert is_builtin("Array", "typescript") is True

    def test_object_is_ts_builtin(self) -> None:
        assert is_builtin("Object", "typescript") is True

    def test_json_is_ts_builtin(self) -> None:
        assert is_builtin("JSON", "typescript") is True

    def test_math_is_ts_builtin(self) -> None:
        assert is_builtin("Math", "typescript") is True

    def test_parse_int_is_ts_builtin(self) -> None:
        assert is_builtin("parseInt", "typescript") is True

    def test_parse_float_is_ts_builtin(self) -> None:
        assert is_builtin("parseFloat", "typescript") is True

    def test_undefined_is_ts_builtin(self) -> None:
        assert is_builtin("undefined", "typescript") is True


class TestGoBuiltins:
    """B3 — Go builtin function and type names."""

    def test_make_is_go_builtin(self) -> None:
        assert is_builtin("make", "go") is True

    def test_new_is_go_builtin(self) -> None:
        assert is_builtin("new", "go") is True

    def test_len_is_go_builtin(self) -> None:
        assert is_builtin("len", "go") is True

    def test_cap_is_go_builtin(self) -> None:
        assert is_builtin("cap", "go") is True

    def test_append_is_go_builtin(self) -> None:
        assert is_builtin("append", "go") is True

    def test_copy_is_go_builtin(self) -> None:
        assert is_builtin("copy", "go") is True

    def test_close_is_go_builtin(self) -> None:
        assert is_builtin("close", "go") is True

    def test_delete_is_go_builtin(self) -> None:
        assert is_builtin("delete", "go") is True

    def test_panic_is_go_builtin(self) -> None:
        assert is_builtin("panic", "go") is True

    def test_recover_is_go_builtin(self) -> None:
        assert is_builtin("recover", "go") is True

    def test_print_is_go_builtin(self) -> None:
        assert is_builtin("print", "go") is True

    def test_println_is_go_builtin(self) -> None:
        assert is_builtin("println", "go") is True

    def test_error_is_go_builtin(self) -> None:
        # error is a predeclared interface type in Go
        assert is_builtin("error", "go") is True


class TestRustBuiltins:
    """B4 — Rust prelude and common builtin names."""

    def test_vec_is_rust_builtin(self) -> None:
        assert is_builtin("Vec", "rust") is True

    def test_string_type_is_rust_builtin(self) -> None:
        assert is_builtin("String", "rust") is True

    def test_option_is_rust_builtin(self) -> None:
        assert is_builtin("Option", "rust") is True

    def test_result_is_rust_builtin(self) -> None:
        assert is_builtin("Result", "rust") is True

    def test_some_is_rust_builtin(self) -> None:
        assert is_builtin("Some", "rust") is True

    def test_none_variant_is_rust_builtin(self) -> None:
        assert is_builtin("None", "rust") is True

    def test_ok_is_rust_builtin(self) -> None:
        assert is_builtin("Ok", "rust") is True

    def test_err_is_rust_builtin(self) -> None:
        assert is_builtin("Err", "rust") is True

    def test_println_macro_is_rust_builtin(self) -> None:
        assert is_builtin("println", "rust") is True

    def test_panic_is_rust_builtin(self) -> None:
        assert is_builtin("panic", "rust") is True

    def test_box_is_rust_builtin(self) -> None:
        assert is_builtin("Box", "rust") is True

    def test_drop_is_rust_builtin(self) -> None:
        assert is_builtin("drop", "rust") is True


class TestNonBuiltins:
    """B5 — User-defined names are NOT treated as builtins."""

    def test_parse_not_python_builtin(self) -> None:
        assert is_builtin("parse", "python") is False

    def test_get_not_python_builtin(self) -> None:
        # User story 5: a user who writes def get() must keep normal resolution
        assert is_builtin("get", "python") is False

    def test_my_function_not_builtin(self) -> None:
        assert is_builtin("my_function", "python") is False

    def test_fetch_not_ts_builtin(self) -> None:
        # fetch is a browser global but not in our conservative set
        assert is_builtin("fetch", "typescript") is False

    def test_handler_not_go_builtin(self) -> None:
        assert is_builtin("handler", "go") is False

    def test_process_not_rust_builtin(self) -> None:
        assert is_builtin("process", "rust") is False

    def test_empty_name_not_builtin(self) -> None:
        assert is_builtin("", "python") is False


class TestCrossLanguageIsolation:
    """B6 — Builtins are language-scoped; cross-language homonyms are NOT false-positives."""

    def test_make_not_python_builtin(self) -> None:
        # 'make' is a Go builtin but NOT a Python builtin
        assert is_builtin("make", "python") is False

    def test_len_python_but_also_go(self) -> None:
        # 'len' is BOTH a Python and Go builtin — each independently
        assert is_builtin("len", "python") is True
        assert is_builtin("len", "go") is True

    def test_println_not_ts_builtin(self) -> None:
        # 'println' is Go/Rust; not TS
        assert is_builtin("println", "typescript") is False

    def test_vec_not_python_builtin(self) -> None:
        # 'Vec' is Rust-only
        assert is_builtin("Vec", "python") is False

    def test_promise_not_go_builtin(self) -> None:
        # 'Promise' is TS/JS-only
        assert is_builtin("Promise", "go") is False


class TestNeverRaises:
    """B7 — is_builtin never raises even on unknown inputs."""

    def test_unknown_language_returns_false(self) -> None:
        # Should return False, not raise
        result = is_builtin("len", "cobol")
        assert result is False

    def test_none_like_language_returns_false(self) -> None:
        result = is_builtin("len", "")
        assert result is False
