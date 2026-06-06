"""Unit tests for Tier B slice B4: scope-inference module + Python/TS/JS receiver-type inference.

TDD: Tests written BEFORE implementation. Each group covers one behavioral slice:

B4-M: Scope module unit tests (pure function; no AST needed)
B4-P: Python receiver-type inference (extractor integration)
B4-T: TypeScript/JS receiver-type inference (extractor integration)
B4-N: Negative / conservatism contract tests (wrong-edge guards)
B4-C: Config knob SEAM_TYPE_INFERENCE (on/off behavior)

CONSERVATISM CONTRACT (enforced by negative tests):
  - Plain user types ONLY. Refuse: Optional/Union (Foo | None), List[Foo], list[Foo],
    Dict[K, V], Tuple[...], Any, generic shapes — bind NONE, keep bare target.
  - Only resolve identifiers known in scope (class field, param, or local).
  - self/this/cls/super → enclosing class (only when class is known).
  - Chained receivers (a.b.method()) → refuse (would require cross-class field typing).
  - Unknown variables → refuse (never guess).
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_python(source: str) -> list[Edge]:
    """Parse Python source, extract all edges, return them."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    parse_fn = getattr(parser_mod, "parse_python")
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"parse_python returned None for: {source!r}"
        return extract_edges(root, "python", path)
    finally:
        os.unlink(fname)


def _parse_ts(source: str) -> list[Edge]:
    """Parse TypeScript source, extract all edges, return them."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    parse_fn = getattr(parser_mod, "parse_typescript")
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"parse_typescript returned None for: {source!r}"
        return extract_edges(root, "typescript", path)
    finally:
        os.unlink(fname)


def _parse_js(source: str) -> list[Edge]:
    """Parse JavaScript source, extract all edges."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    parse_fn = getattr(parser_mod, "parse_typescript")
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"parse_typescript returned None for: {source!r}"
        return extract_edges(root, "javascript", path)
    finally:
        os.unlink(fname)


def _call_edges(edges: list[Edge]) -> list[Edge]:
    """Return only call-kind edges."""
    return [e for e in edges if e["kind"] == "call"]


def _edge_by_target(edges: list[Edge], target: str) -> Edge | None:
    """Return first call edge with given target, or None."""
    return next((e for e in _call_edges(edges) if e["target"] == target), None)


# ── B4-M: Scope module unit tests ────────────────────────────────────────────


class TestScopeInferModule:
    """B4-M: The scope-inference module is importable and exposes a known interface."""

    def test_module_importable(self) -> None:
        """graph_scope_infer is importable without raising."""
        import importlib

        mod = importlib.import_module("seam.indexer.graph_scope_infer")
        assert mod is not None

    def test_resolve_receiver_type_exists(self) -> None:
        """resolve_receiver_type function exists in the module."""
        import importlib

        mod = importlib.import_module("seam.indexer.graph_scope_infer")
        assert hasattr(mod, "resolve_receiver_type"), (
            "graph_scope_infer must expose resolve_receiver_type()"
        )

    def test_scan_class_fields_python_exists(self) -> None:
        """scan_class_fields_python exists in the module."""
        import importlib

        mod = importlib.import_module("seam.indexer.graph_scope_infer")
        assert hasattr(mod, "scan_class_fields_python"), (
            "graph_scope_infer must expose scan_class_fields_python()"
        )

    def test_scan_class_fields_typescript_exists(self) -> None:
        """scan_class_fields_typescript exists in the module."""
        import importlib

        mod = importlib.import_module("seam.indexer.graph_scope_infer")
        assert hasattr(mod, "scan_class_fields_typescript"), (
            "graph_scope_infer must expose scan_class_fields_typescript()"
        )


# ── B4-P: Python receiver-type inference ─────────────────────────────────────


class TestPythonTypeInference:
    """B4-P: Python extractor emits qualified Type.method when receiver type is known."""

    # The canonical cross-class call: client: Client -> client.send() -> Client.send
    CROSS_CLASS_PARAM = """\
class Client:
    def send(self, msg: str) -> None:
        pass

class Service:
    def run(self, client: Client) -> None:
        client.send("hello")
"""

    def test_param_type_annotation_resolves_target(self) -> None:
        """client: Client → client.send() → target='Client.send' (qualified)."""
        edges = _parse_python(self.CROSS_CLASS_PARAM)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Client.send" in call_targets, (
            f"Expected 'Client.send' (qualified) call edge for cross-class call. "
            f"Got call targets: {call_targets}. "
            "Type inference must resolve 'client: Client' -> 'Client.send'."
        )

    def test_qualified_target_preserves_receiver(self) -> None:
        """The qualified-target edge still carries receiver='client'."""
        edges = _parse_python(self.CROSS_CLASS_PARAM)
        e = _edge_by_target(edges, "Client.send")
        assert e is not None, "Expected 'Client.send' edge"
        assert e.get("receiver") == "client", (
            f"Expected receiver='client', got {e.get('receiver')!r}"
        )

    def test_bare_target_not_emitted_when_qualified(self) -> None:
        """When type is resolved to Client.send, bare 'send' edge must NOT be emitted."""
        edges = _parse_python(self.CROSS_CLASS_PARAM)
        call_targets = {e["target"] for e in _call_edges(edges)}
        # When inference succeeds, the bare 'send' must not also appear
        assert "send" not in call_targets, (
            f"Bare 'send' must not be emitted when qualified 'Client.send' is resolved. "
            f"Got: {call_targets}"
        )

    # self.method() → EnclosingClass.method
    SELF_CALL_PY = """\
class Manager:
    def _helper(self) -> None:
        pass

    def process(self) -> None:
        self._helper()
"""

    def test_self_resolves_to_enclosing_class(self) -> None:
        """self._helper() inside Manager.process → target='Manager._helper'."""
        edges = _parse_python(self.SELF_CALL_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Manager._helper" in call_targets, (
            f"Expected 'Manager._helper' from self._helper() in Manager.process. "
            f"Got: {call_targets}."
        )

    # cls.method() — classmethod receiver
    CLS_CALL_PY = """\
class Factory:
    def _create(cls) -> "Factory":
        return cls()

    @classmethod
    def make(cls) -> "Factory":
        return cls._create()
"""

    def test_cls_resolves_to_enclosing_class(self) -> None:
        """cls._create() inside Factory.make → target='Factory._create'."""
        edges = _parse_python(self.CLS_CALL_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Factory._create" in call_targets, (
            f"Expected 'Factory._create' from cls._create(). Got: {call_targets}."
        )

    # Local variable with type annotation
    LOCAL_ANNOTATED_PY = """\
class Engine:
    def start(self) -> None:
        pass

def run() -> None:
    e: Engine = Engine()
    e.start()
"""

    def test_local_annotated_var_resolves_target(self) -> None:
        """e: Engine = Engine(); e.start() → target='Engine.start'."""
        edges = _parse_python(self.LOCAL_ANNOTATED_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Engine.start" in call_targets, (
            f"Expected 'Engine.start' from local annotated var. Got: {call_targets}."
        )

    # Local constructor call (no annotation, just assignment from constructor)
    LOCAL_CONSTRUCTOR_PY = """\
class Parser:
    def parse(self, text: str) -> None:
        pass

def process() -> None:
    p = Parser()
    p.parse("hello")
"""

    def test_local_constructor_call_resolves_target(self) -> None:
        """p = Parser(); p.parse('hello') → target='Parser.parse'."""
        edges = _parse_python(self.LOCAL_CONSTRUCTOR_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Parser.parse" in call_targets, (
            f"Expected 'Parser.parse' from constructor-call local. Got: {call_targets}."
        )

    # Class field with type annotation (DI'd property)
    CLASS_FIELD_PY = """\
class Repository:
    def save(self, item: str) -> None:
        pass

class Service:
    repo: Repository

    def store(self, item: str) -> None:
        self.repo.save(item)
"""

    def test_class_field_annotation_resolves_target(self) -> None:
        """self.repo.save() where repo: Repository → target='Repository.save'."""
        edges = _parse_python(self.CLASS_FIELD_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Repository.save" in call_targets, (
            f"Expected 'Repository.save' from class-field type annotation. "
            f"Got: {call_targets}."
        )


# ── B4-T: TypeScript/JS receiver-type inference ───────────────────────────────


class TestTypeScriptTypeInference:
    """B4-T: TS/JS extractor emits qualified Type.method when receiver type is known."""

    # The canonical cross-class call: client: Client → client.send() → Client.send
    CROSS_CLASS_PARAM_TS = """\
class Client {
    send(msg: string): void {}
}
class Service {
    run(client: Client): void {
        client.send("hello");
    }
}
"""

    def test_ts_param_type_annotation_resolves_target(self) -> None:
        """TS: client: Client → client.send() → target='Client.send'."""
        edges = _parse_ts(self.CROSS_CLASS_PARAM_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Client.send" in call_targets, (
            f"Expected 'Client.send' (qualified) for TS cross-class call. "
            f"Got call targets: {call_targets}."
        )

    def test_ts_bare_target_not_emitted_when_qualified(self) -> None:
        """TS: When Client.send is resolved, bare 'send' must not also be emitted."""
        edges = _parse_ts(self.CROSS_CLASS_PARAM_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "send" not in call_targets, (
            f"Bare 'send' must not be emitted when 'Client.send' is resolved. "
            f"Got: {call_targets}"
        )

    # this.method() → EnclosingClass.method
    THIS_CALL_TS = """\
class Worker {
    _process(): void {}
    run(): void {
        this._process();
    }
}
"""

    def test_ts_this_resolves_to_enclosing_class(self) -> None:
        """TS: this._process() in Worker.run → target='Worker._process'."""
        edges = _parse_ts(self.THIS_CALL_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Worker._process" in call_targets, (
            f"Expected 'Worker._process' from this._process() in Worker. "
            f"Got: {call_targets}."
        )

    def test_ts_this_target_not_emitted_bare(self) -> None:
        """TS: bare '_process' must not be emitted when 'Worker._process' is resolved."""
        edges = _parse_ts(self.THIS_CALL_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "_process" not in call_targets, (
            f"Bare '_process' must not be emitted when 'Worker._process' resolved. "
            f"Got: {call_targets}"
        )

    # Local variable with type annotation
    LOCAL_ANNOTATED_TS = """\
class Engine {
    start(): void {}
}
function run(): void {
    const e: Engine = new Engine();
    e.start();
}
"""

    def test_ts_local_annotated_var_resolves_target(self) -> None:
        """TS: const e: Engine = new Engine(); e.start() → target='Engine.start'."""
        edges = _parse_ts(self.LOCAL_ANNOTATED_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Engine.start" in call_targets, (
            f"Expected 'Engine.start' from TS local annotated var. Got: {call_targets}."
        )

    # Constructor call inference (no type annotation)
    LOCAL_CONSTRUCTOR_TS = """\
class Parser {
    parse(text: string): void {}
}
function process(): void {
    const p = new Parser();
    p.parse("hello");
}
"""

    def test_ts_local_constructor_resolves_target(self) -> None:
        """TS: const p = new Parser(); p.parse() → target='Parser.parse'."""
        edges = _parse_ts(self.LOCAL_CONSTRUCTOR_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Parser.parse" in call_targets, (
            f"Expected 'Parser.parse' from TS constructor-call local. Got: {call_targets}."
        )

    # Class field (property) with type annotation
    CLASS_FIELD_TS = """\
class Repository {
    save(item: string): void {}
}
class Service {
    repo: Repository;
    store(item: string): void {
        this.repo.save(item);
    }
}
"""

    def test_ts_class_field_annotation_resolves_target(self) -> None:
        """TS: this.repo.save() where repo: Repository → target='Repository.save'."""
        edges = _parse_ts(self.CLASS_FIELD_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Repository.save" in call_targets, (
            f"Expected 'Repository.save' from TS class-field annotation. "
            f"Got: {call_targets}."
        )

    # JS: constructor-call inference only (no type annotations)
    JS_CONSTRUCTOR_INFERENCE = """\
function run() {
    const p = new Parser();
    p.parse("hello");
}
"""

    def test_js_constructor_resolves_target(self) -> None:
        """JS: const p = new Parser(); p.parse() → target='Parser.parse'."""
        edges = _parse_js(self.JS_CONSTRUCTOR_INFERENCE)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Parser.parse" in call_targets, (
            f"Expected 'Parser.parse' from JS constructor local. Got: {call_targets}."
        )


# ── B4-N: Negative / conservatism contract ───────────────────────────────────


class TestConservatismContract:
    """B4-N: The conservatism contract is enforced. NEVER emit a wrong edge.

    These tests verify that complex/uncertain receiver types produce NO qualified
    edge (the extractor falls back to the bare target or keeps the raw receiver).
    """

    # Optional type (Foo | None) — must NOT resolve
    OPTIONAL_PY = """\
class Foo:
    def bar(self) -> None:
        pass

def test(x: Foo | None) -> None:
    x.bar()
"""

    def test_python_optional_does_not_qualify(self) -> None:
        """x: Foo | None → x.bar() must NOT resolve to 'Foo.bar' (optional = refuse)."""
        edges = _parse_python(self.OPTIONAL_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Foo.bar" not in call_targets, (
            f"Optional type 'Foo | None' must NOT produce qualified target. "
            f"Got: {call_targets}. Conservatism contract violated."
        )

    # list[Foo] type — must NOT resolve
    LIST_PY = """\
class Item:
    def process(self) -> None:
        pass

def test(items: list["Item"]) -> None:
    items.process()
"""

    def test_python_list_type_does_not_qualify(self) -> None:
        """items: list[Item] → items.process() must NOT resolve to 'Item.process'."""
        edges = _parse_python(self.LIST_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Item.process" not in call_targets, (
            f"list[Item] must NOT produce qualified target. "
            f"Got: {call_targets}. Conservatism contract violated."
        )

    # Dict type — must NOT resolve
    DICT_PY = """\
class Val:
    def get(self) -> None:
        pass

def test(d: dict[str, "Val"]) -> None:
    d.get("key")
"""

    def test_python_dict_type_does_not_qualify(self) -> None:
        """d: dict[str, Val] → must NOT resolve to qualified target."""
        edges = _parse_python(self.DICT_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Val.get" not in call_targets, (
            f"dict type must NOT produce qualified target. Got: {call_targets}."
        )

    # Unknown variable — must NOT resolve
    UNKNOWN_VAR_PY = """\
def test() -> None:
    mystery.method()
"""

    def test_python_unknown_variable_does_not_qualify(self) -> None:
        """mystery.method() where 'mystery' is not in scope → must NOT qualify."""
        edges = _parse_python(self.UNKNOWN_VAR_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        # The edge target should be bare 'method', not any qualified form
        qualified = [t for t in call_targets if "." in t]
        assert not qualified, (
            f"Unknown variable must not produce qualified targets. "
            f"Got qualified: {qualified}. Conservatism contract violated."
        )

    # Chained receiver (self.field.method()) — must NOT resolve unless field type is known
    CHAINED_UNKNOWN_PY = """\
class Outer:
    def run(self) -> None:
        self.inner.execute()
"""

    def test_python_chained_unknown_field_does_not_qualify(self) -> None:
        """self.inner.execute() where 'inner' has no type annotation → must NOT qualify."""
        edges = _parse_python(self.CHAINED_UNKNOWN_PY)
        call_targets = {e["target"] for e in _call_edges(edges)}
        # 'inner' has no declared type → cannot know its type → must not qualify
        qualified = [t for t in call_targets if "." in t and "Outer" not in t]
        assert not qualified, (
            f"Chained access 'self.inner.execute()' with unknown field type must not qualify. "
            f"Got: {qualified}."
        )

    # TS Optional (T | null) — must NOT resolve
    OPTIONAL_TS = """\
class Foo { bar(): void {} }
function test(x: Foo | null): void {
    x.bar();
}
"""

    def test_ts_optional_does_not_qualify(self) -> None:
        """TS: x: Foo | null → x.bar() must NOT resolve to 'Foo.bar'."""
        edges = _parse_ts(self.OPTIONAL_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Foo.bar" not in call_targets, (
            f"TS optional type must NOT produce qualified target. Got: {call_targets}."
        )

    # TS Generic (Array<Foo>) — must NOT resolve
    GENERIC_TS = """\
class Foo { doIt(): void {} }
function test(items: Array<Foo>): void {
    items.doIt();
}
"""

    def test_ts_generic_does_not_qualify(self) -> None:
        """TS: items: Array<Foo> → items.doIt() must NOT qualify to 'Foo.doIt'."""
        edges = _parse_ts(self.GENERIC_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "Foo.doIt" not in call_targets, (
            f"TS generic type must NOT produce qualified target. Got: {call_targets}."
        )

    # TS: unknown variable — must NOT resolve
    UNKNOWN_TS = """\
function test(): void {
    mystery.method();
}
"""

    def test_ts_unknown_variable_does_not_qualify(self) -> None:
        """TS: mystery.method() where 'mystery' is not in scope → must not qualify."""
        edges = _parse_ts(self.UNKNOWN_TS)
        call_targets = {e["target"] for e in _call_edges(edges)}
        qualified = [t for t in call_targets if "." in t]
        assert not qualified, (
            f"Unknown TS variable must not produce qualified targets. Got: {qualified}."
        )

    # Never raise — even on malformed input
    def test_python_inference_never_raises(self) -> None:
        """Any Python source must not raise during edge extraction."""
        malformed = "class A:\n    def f(self, x: int | str | None | list[int]) -> None:\n        x.method()\n"
        try:
            _parse_python(malformed)
        except Exception as exc:
            pytest.fail(f"Python type inference must not raise. Got: {exc}")

    def test_ts_inference_never_raises(self) -> None:
        """Any TS source must not raise during edge extraction."""
        malformed = "function test(x: string | number | null | Array<string>): void { x.method(); }\n"
        try:
            _parse_ts(malformed)
        except Exception as exc:
            pytest.fail(f"TS type inference must not raise. Got: {exc}")


# ── B4-C: Config knob SEAM_TYPE_INFERENCE ────────────────────────────────────


class TestTypeInferenceConfigKnob:
    """B4-C: SEAM_TYPE_INFERENCE=off reproduces pre-B4 bare targets byte-for-byte."""

    CROSS_CLASS_PY = """\
class Client:
    def send(self, msg: str) -> None:
        pass
class Service:
    def run(self, client: Client) -> None:
        client.send("hello")
"""

    def test_config_knob_off_gives_bare_target(self) -> None:
        """With SEAM_TYPE_INFERENCE=off, client.send() → bare 'send' (no inference)."""
        import seam.config as cfg

        original = cfg.SEAM_TYPE_INFERENCE
        try:
            cfg.SEAM_TYPE_INFERENCE = "off"
            # Re-import to pick up new value (config is read at call time)
            edges = _parse_python(self.CROSS_CLASS_PY)
            call_targets = {e["target"] for e in _call_edges(edges)}
            # With inference off → must get bare 'send', NOT 'Client.send'
            assert "Client.send" not in call_targets, (
                f"With SEAM_TYPE_INFERENCE=off, must not qualify targets. Got: {call_targets}"
            )
            assert "send" in call_targets, (
                f"With SEAM_TYPE_INFERENCE=off, bare 'send' must be present. Got: {call_targets}"
            )
        finally:
            cfg.SEAM_TYPE_INFERENCE = original

    def test_config_knob_on_gives_qualified_target(self) -> None:
        """With SEAM_TYPE_INFERENCE=on (default), client.send() → 'Client.send'."""
        import seam.config as cfg

        original = cfg.SEAM_TYPE_INFERENCE
        try:
            cfg.SEAM_TYPE_INFERENCE = "on"
            edges = _parse_python(self.CROSS_CLASS_PY)
            call_targets = {e["target"] for e in _call_edges(edges)}
            assert "Client.send" in call_targets, (
                f"With SEAM_TYPE_INFERENCE=on, must produce 'Client.send'. Got: {call_targets}"
            )
        finally:
            cfg.SEAM_TYPE_INFERENCE = original

    def test_config_knob_in_config_module(self) -> None:
        """SEAM_TYPE_INFERENCE is defined in seam/config.py."""
        import seam.config as cfg

        assert hasattr(cfg, "SEAM_TYPE_INFERENCE"), (
            "SEAM_TYPE_INFERENCE must be defined in seam/config.py"
        )
        # Default must be "on"
        assert cfg.SEAM_TYPE_INFERENCE in ("on", "off"), (
            f"SEAM_TYPE_INFERENCE must be 'on' or 'off', got {cfg.SEAM_TYPE_INFERENCE!r}"
        )
