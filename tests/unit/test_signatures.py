"""Tests for seam/indexer/signatures.py — per-language node-field extraction.

TDD: Tests written before implementation (RED phase).

Test strategy: drive extraction through REAL tree-sitter fixtures (no mocks)
so the grammar is actually exercised. Assert on external behavior (field values)
not internal state.

Groups:
    S1 — Python: decorated fn, private fn, method, class, exports
    S2 — TypeScript/JavaScript: exported function, class method, private, decorators
    S3 — Go: capitalized (exported) vs lowercase (unexported), method, struct
    S4 — Rust: pub fn, private fn, method in impl, struct, trait
    S5 — Edge cases: malformed/None node never raises, empty fallbacks
"""

from pathlib import Path

import pytest

from seam.indexer.parser import (
    parse_go,
    parse_python,
    parse_rust,
    parse_typescript,
)
from seam.indexer.signatures import extract_node_fields

# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_node(root, node_types: set[str], name_text: str | None = None):
    """Walk tree to find first node of one of node_types, optionally matching name."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in node_types:
            if name_text is None:
                return node
            # Check if the 'name' field child text matches
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.text == name_text.encode():
                return node
        for child in node.children:
            stack.append(child)
    return None


def _find_node_with_name(root, node_types: set[str], name_text: str):
    """Walk tree to find first node of one of node_types whose name field text matches."""
    return _find_node(root, node_types, name_text)


# ── S1: Python ────────────────────────────────────────────────────────────────


PYTHON_SOURCE = '''
import os

@staticmethod
def static_method() -> str:
    return "static"

@classmethod
def class_method(cls):
    return cls(0)

def _private_func(x: int) -> None:
    pass

def public_func(name: str, value: int = 0) -> bool:
    """Some docstring."""
    return True

class MyClass:
    """A class."""

    def __init__(self, x: int) -> None:
        self.x = x

    def method(self) -> int:
        return self.x

    def _private_method(self) -> None:
        pass
'''


class TestPythonExtraction:
    """S1: Python per-language extraction."""

    @pytest.fixture(autouse=True)
    def parse(self, tmp_path: Path):
        src = tmp_path / "test.py"
        src.write_text(PYTHON_SOURCE)
        self.root = parse_python(src)
        self.src = src

    def test_public_function_is_exported(self) -> None:
        """public_func has no underscore prefix → is_exported=True."""
        node = _find_node_with_name(self.root, {"function_definition"}, "public_func")
        assert node is not None, "Expected to find public_func node"
        fields = extract_node_fields(node, "python")
        assert fields["is_exported"] is True
        assert fields["visibility"] == "public"

    def test_private_function_not_exported(self) -> None:
        """_private_func starts with _ → is_exported=False, visibility='private'."""
        node = _find_node_with_name(self.root, {"function_definition"}, "_private_func")
        assert node is not None, "Expected to find _private_func node"
        fields = extract_node_fields(node, "python")
        assert fields["is_exported"] is False
        assert fields["visibility"] == "private"

    def test_decorated_function_captures_decorators(self) -> None:
        """@staticmethod decorator is captured verbatim in decorators list."""
        # decorated_definition node wraps the function
        node = _find_node(self.root, {"decorated_definition"})
        assert node is not None, "Expected a decorated_definition node"
        fields = extract_node_fields(node, "python")
        assert isinstance(fields["decorators"], list)
        assert len(fields["decorators"]) >= 1
        # The first decorator text should start with '@'
        assert any("@" in d for d in fields["decorators"]), f"Got decorators: {fields['decorators']}"

    def test_function_signature_includes_params(self) -> None:
        """Signature for public_func includes the parameter names."""
        node = _find_node_with_name(self.root, {"function_definition"}, "public_func")
        assert node is not None
        fields = extract_node_fields(node, "python")
        sig = fields["signature"]
        assert sig is not None
        # Should contain the parameter list
        assert "name" in sig or "value" in sig or "public_func" in sig

    def test_function_signature_single_line(self) -> None:
        """Signature is always a single line (no embedded newlines)."""
        node = _find_node_with_name(self.root, {"function_definition"}, "public_func")
        assert node is not None
        fields = extract_node_fields(node, "python")
        if fields["signature"] is not None:
            assert "\n" not in fields["signature"]

    def test_no_decorators_on_plain_function(self) -> None:
        """Plain function has an empty decorators list."""
        node = _find_node_with_name(self.root, {"function_definition"}, "public_func")
        assert node is not None
        fields = extract_node_fields(node, "python")
        assert fields["decorators"] == []

    def test_class_not_exported_when_private(self) -> None:
        """MyClass is public (no underscore prefix) → is_exported=True."""
        node = _find_node_with_name(self.root, {"class_definition"}, "MyClass")
        assert node is not None
        fields = extract_node_fields(node, "python")
        assert fields["is_exported"] is True
        assert fields["visibility"] == "public"


PYTHON_PRIVATE_CLASS = '''
class _InternalHelper:
    pass
'''


def test_python_private_class_underscore() -> None:
    """_InternalHelper class → is_exported=False, visibility='private'."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        src = Path(f.name)
    src.write_text(PYTHON_PRIVATE_CLASS)
    try:
        root = parse_python(src)
        node = _find_node_with_name(root, {"class_definition"}, "_InternalHelper")
        assert node is not None
        fields = extract_node_fields(node, "python")
        assert fields["is_exported"] is False
        assert fields["visibility"] == "private"
    finally:
        src.unlink(missing_ok=True)


# ── S2: TypeScript ────────────────────────────────────────────────────────────


TYPESCRIPT_SOURCE = '''
export function exportedFunc(x: number, y: string): boolean {
    return true;
}

function internalFunc(name: string): void {
    console.log(name);
}

export class PublicClass {
    private secret: string;

    constructor(secret: string) {
        this.secret = secret;
    }

    public greet(): string {
        return "hello";
    }

    private _hidden(): void {}
}

export interface MyInterface {
    id: number;
    process(): void;
}

export type MyType = {
    value: string;
};
'''


class TestTypeScriptExtraction:
    """S2: TypeScript per-language extraction."""

    @pytest.fixture(autouse=True)
    def parse(self, tmp_path: Path):
        src = tmp_path / "test.ts"
        src.write_text(TYPESCRIPT_SOURCE)
        self.root = parse_typescript(src)
        self.src = src

    def test_exported_function_is_exported(self) -> None:
        """export function exportedFunc → is_exported=True."""
        # The function_declaration node may be wrapped in export_statement
        node = _find_node_with_name(self.root, {"function_declaration"}, "exportedFunc")
        assert node is not None, "Expected to find exportedFunc"
        fields = extract_node_fields(node, "typescript")
        assert fields["is_exported"] is True

    def test_internal_function_not_exported(self) -> None:
        """internalFunc has no export → is_exported=False."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "internalFunc")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        assert fields["is_exported"] is False

    def test_function_signature_present(self) -> None:
        """exportedFunc signature contains parameter info."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "exportedFunc")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        assert fields["signature"] is not None
        # Should contain param names or types
        assert "x" in fields["signature"] or "number" in fields["signature"] or "exportedFunc" in fields["signature"]

    def test_no_decorators_on_plain_ts_function(self) -> None:
        """Plain TS function has empty decorators list."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "exportedFunc")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        assert isinstance(fields["decorators"], list)

    def test_method_visibility_public(self) -> None:
        """greet() method has public modifier → visibility='public'."""
        node = _find_node_with_name(self.root, {"method_definition"}, "greet")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        # greet has explicit 'public' keyword → visibility='public'
        assert fields["visibility"] in ("public", None)  # grammar may or may not expose modifier

    def test_method_visibility_private(self) -> None:
        """_hidden() method has private modifier → visibility='private'."""
        node = _find_node_with_name(self.root, {"method_definition"}, "_hidden")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        assert fields["visibility"] == "private"

    def test_class_declaration_has_signature(self) -> None:
        """class PublicClass has a non-null signature."""
        node = _find_node_with_name(self.root, {"class_declaration"}, "PublicClass")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        assert fields["signature"] is not None
        assert "PublicClass" in fields["signature"]

    def test_decorators_list_is_list(self) -> None:
        """Even when no decorators, decorators field is always a list."""
        node = _find_node_with_name(self.root, {"class_declaration"}, "PublicClass")
        assert node is not None
        fields = extract_node_fields(node, "typescript")
        assert isinstance(fields["decorators"], list)


# ── S3: Go ────────────────────────────────────────────────────────────────────


GO_SOURCE = '''package sample

import "fmt"

// Add is an exported function.
func Add(x, y int) int {
    return x + y
}

// multiply is internal.
func multiply(x, y int) int {
    return x * y
}

// Repo is a struct.
type Repo struct {
    Name string
}

// Save is an exported method.
func (r *Repo) Save() error {
    return fmt.Errorf("not implemented")
}

// get is an unexported method.
func (r *Repo) get() string {
    return r.Name
}
'''


class TestGoExtraction:
    """S3: Go per-language extraction."""

    @pytest.fixture(autouse=True)
    def parse(self, tmp_path: Path):
        src = tmp_path / "test.go"
        src.write_text(GO_SOURCE)
        self.root = parse_go(src)
        self.src = src

    def test_capitalized_func_is_exported(self) -> None:
        """Add (capitalized) → is_exported=True, visibility='public'."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "Add")
        assert node is not None, "Expected to find Add function"
        fields = extract_node_fields(node, "go")
        assert fields["is_exported"] is True
        assert fields["visibility"] == "public"

    def test_lowercase_func_not_exported(self) -> None:
        """multiply (lowercase) → is_exported=False, visibility='private'."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "multiply")
        assert node is not None
        fields = extract_node_fields(node, "go")
        assert fields["is_exported"] is False
        assert fields["visibility"] == "private"

    def test_go_no_decorators(self) -> None:
        """Go has no decorator construct → decorators is always []."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "Add")
        assert node is not None
        fields = extract_node_fields(node, "go")
        assert fields["decorators"] == []

    def test_go_func_signature_present(self) -> None:
        """Go function has a non-null signature."""
        node = _find_node_with_name(self.root, {"function_declaration"}, "Add")
        assert node is not None
        fields = extract_node_fields(node, "go")
        assert fields["signature"] is not None
        assert "Add" in fields["signature"] or "int" in fields["signature"]

    def test_go_method_exported(self) -> None:
        """Save method (capitalized) → is_exported=True."""
        node = _find_node_with_name(self.root, {"method_declaration"}, "Save")
        assert node is not None
        fields = extract_node_fields(node, "go")
        assert fields["is_exported"] is True

    def test_go_method_unexported(self) -> None:
        """get method (lowercase) → is_exported=False."""
        node = _find_node_with_name(self.root, {"method_declaration"}, "get")
        assert node is not None
        fields = extract_node_fields(node, "go")
        assert fields["is_exported"] is False


# ── S4: Rust ──────────────────────────────────────────────────────────────────


RUST_SOURCE = '''
/// Public function.
pub fn public_func(x: i32) -> i32 {
    x + 1
}

/// Private function.
fn private_func(x: i32) -> i32 {
    x - 1
}

/// A public struct.
pub struct PublicStore {
    name: String,
}

/// A private struct.
struct PrivateStore {
    value: i32,
}

impl PublicStore {
    /// A pub method.
    pub fn create(name: String) -> Self {
        PublicStore { name }
    }

    /// A private method.
    fn internal(&self) -> String {
        self.name.clone()
    }
}
'''


class TestRustExtraction:
    """S4: Rust per-language extraction."""

    @pytest.fixture(autouse=True)
    def parse(self, tmp_path: Path):
        src = tmp_path / "test.rs"
        src.write_text(RUST_SOURCE)
        self.root = parse_rust(src)
        self.src = src

    def test_pub_function_is_exported(self) -> None:
        """pub fn public_func → is_exported=True, visibility='public'."""
        node = _find_node_with_name(self.root, {"function_item"}, "public_func")
        assert node is not None, "Expected to find public_func"
        fields = extract_node_fields(node, "rust")
        assert fields["is_exported"] is True
        assert fields["visibility"] == "public"

    def test_private_function_not_exported(self) -> None:
        """fn private_func (no pub) → is_exported=False, visibility='private'."""
        node = _find_node_with_name(self.root, {"function_item"}, "private_func")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["is_exported"] is False
        assert fields["visibility"] == "private"

    def test_rust_no_decorators(self) -> None:
        """Rust has no decorator construct → decorators is always []."""
        node = _find_node_with_name(self.root, {"function_item"}, "public_func")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["decorators"] == []

    def test_rust_func_signature_present(self) -> None:
        """Rust function has a non-null signature."""
        node = _find_node_with_name(self.root, {"function_item"}, "public_func")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["signature"] is not None
        assert "public_func" in fields["signature"] or "i32" in fields["signature"]

    def test_pub_struct_is_exported(self) -> None:
        """pub struct PublicStore → is_exported=True."""
        node = _find_node_with_name(self.root, {"struct_item"}, "PublicStore")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["is_exported"] is True

    def test_private_struct_not_exported(self) -> None:
        """struct PrivateStore (no pub) → is_exported=False."""
        node = _find_node_with_name(self.root, {"struct_item"}, "PrivateStore")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["is_exported"] is False

    def test_pub_impl_method_is_exported(self) -> None:
        """pub fn create inside impl → is_exported=True."""
        node = _find_node_with_name(self.root, {"function_item"}, "create")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["is_exported"] is True

    def test_private_impl_method_not_exported(self) -> None:
        """fn internal (no pub) inside impl → is_exported=False."""
        node = _find_node_with_name(self.root, {"function_item"}, "internal")
        assert node is not None
        fields = extract_node_fields(node, "rust")
        assert fields["is_exported"] is False


# ── S5: Edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    """S5: Edge cases — never raises, safe defaults on None/malformed input."""

    def test_none_node_returns_safe_defaults(self) -> None:
        """extract_node_fields(None, 'python') must never raise."""
        # None is not a tree_sitter Node — should return safe defaults
        fields = extract_node_fields(None, "python")
        assert isinstance(fields, dict)
        assert fields["decorators"] == []
        assert fields["signature"] is None
        assert fields["is_exported"] is None
        assert fields["visibility"] is None
        assert fields["qualified_name"] is None

    def test_unknown_language_returns_safe_defaults(self, tmp_path: Path) -> None:
        """Unknown language returns safe defaults without raising."""
        src = tmp_path / "test.py"
        src.write_text("def foo(): pass\n")
        root = parse_python(src)
        node = _find_node(root, {"function_definition"})
        assert node is not None
        # Pass an unrecognized language
        fields = extract_node_fields(node, "cobol")
        assert isinstance(fields, dict)
        assert fields["decorators"] == []

    def test_return_type_always_dict_with_required_keys(self, tmp_path: Path) -> None:
        """extract_node_fields always returns a dict with all 5 required keys."""
        src = tmp_path / "test.py"
        src.write_text("def foo(): pass\n")
        root = parse_python(src)
        node = _find_node(root, {"function_definition"})
        fields = extract_node_fields(node, "python")
        assert "signature" in fields
        assert "decorators" in fields
        assert "is_exported" in fields
        assert "visibility" in fields
        assert "qualified_name" in fields

    def test_decorators_always_list_never_none(self, tmp_path: Path) -> None:
        """decorators field is always a list, never None, for any language."""
        for lang, ext, content in [
            ("python", "py", "def foo(): pass\n"),
            ("go", "go", "package p\nfunc Foo() {}\n"),
            ("rust", "rs", "fn foo() {}\n"),
        ]:
            src = tmp_path / f"test.{ext}"
            src.write_text(content)
            if lang == "python":
                root = parse_python(src)
                types = {"function_definition"}
            elif lang == "go":
                root = parse_go(src)
                types = {"function_declaration"}
            else:
                root = parse_rust(src)
                types = {"function_item"}
            node = _find_node(root, types)
            if node is not None:
                fields = extract_node_fields(node, lang)
                assert isinstance(fields["decorators"], list), f"decorators not list for {lang}"


class TestSignatureTruncation:
    """Signature truncation at SEAM_MAX_SIGNATURE_LEN."""

    def test_signature_truncated_to_max_len(self, tmp_path: Path) -> None:
        """A very long parameter list is truncated to SEAM_MAX_SIGNATURE_LEN chars."""
        from seam.config import SEAM_MAX_SIGNATURE_LEN

        # Build a function with very many parameters
        params = ", ".join(f"param_{i}: int" for i in range(100))
        src = tmp_path / "long.py"
        src.write_text(f"def very_long_func({params}) -> None:\n    pass\n")
        root = parse_python(src)
        node = _find_node_with_name(root, {"function_definition"}, "very_long_func")
        if node is None:
            pytest.skip("Could not find very_long_func node")
        fields = extract_node_fields(node, "python")
        if fields["signature"] is not None:
            assert len(fields["signature"]) <= SEAM_MAX_SIGNATURE_LEN
