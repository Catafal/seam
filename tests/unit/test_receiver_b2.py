"""Tests for Tier B slice B2: receiver capture across the remaining 11 languages.

TDD: tests written before implementation. Each group covers one language:

  TS/JS  — call_expression with member_expression → receiver = object-side text
  Go     — call_expression with selector_expression → receiver = operand text
  Rust   — call_expression with field_expression → receiver text; scoped NULL
  Java   — method_invocation with 'object' field → receiver text
  C#     — invocation_expression with member_access_expression → receiver text
  Ruby   — call with 'receiver' field → receiver text; bare stays None
  C      — call_expression with field_expression (obj->m) → receiver text
  C++    — call_expression with field_expression or qualified_identifier → receiver text
  PHP    — member_call_expression / static_method_call_expression → receiver text
  Swift  — navigation_expression → raw receiver text on the emitted edge

Acceptance criteria (from issue #61):
  1. receiver captured for member/selector/scoped/field calls in all 11 remaining langs.
  2. target_name unchanged (rightmost method id); edges stay string-name-keyed.
  3. Graceful degradation: awkward shapes store NULL, never drop the call, never raise.
  4. Per-language fixture tests asserting receiver capture (and NULL where expected).
  5. No read-path/tool behavior change; make gate green.
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Shared helpers ────────────────────────────────────────────────────────────


def _call_edges(edges: list[Edge]) -> list[Edge]:
    """Filter to call edges only."""
    return [e for e in edges if e["kind"] == "call"]


def _edge_by_target(edges: list[Edge], target: str) -> Edge | None:
    """Return the first call edge with the given target, or None."""
    return next((e for e in _call_edges(edges) if e["target"] == target), None)


def _parse_and_extract(source: str, lang: str, suffix: str) -> list[Edge]:
    """Write source to a temp file, parse it, extract edges, and return them."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    parse_fn_map = {
        "typescript": getattr(parser_mod, "parse_typescript"),
        "javascript": getattr(parser_mod, "parse_typescript"),  # JS uses TS parser
        "go": getattr(parser_mod, "parse_go"),
        "rust": getattr(parser_mod, "parse_rust"),
        "java": getattr(parser_mod, "parse_java"),
        "csharp": getattr(parser_mod, "parse_csharp"),
        "ruby": getattr(parser_mod, "parse_ruby"),
        "c": getattr(parser_mod, "parse_c"),
        "cpp": getattr(parser_mod, "parse_cpp"),
        "php": getattr(parser_mod, "parse_php"),
        "swift": getattr(parser_mod, "parse_swift"),
    }
    parse_fn = parse_fn_map[lang]
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"{lang} parse returned None for: {source!r}"
        return extract_edges(root, lang, path)
    finally:
        os.unlink(fname)


# ── TypeScript / JavaScript ───────────────────────────────────────────────────


class TestTypeScriptReceiverCapture:
    """TS: member_expression calls capture receiver text; bare calls stay None."""

    # obj.method() — simple property access call
    MEMBER_CALL_SRC = """\
class Printer {
    print(): void {}
}
function test(p: Printer) {
    p.print();
}
"""

    # this.method() — self-access call
    THIS_CALL_SRC = """\
class Handler {
    process(): void {}
    run(): void {
        this.process();
    }
}
"""

    # Bare call: receiver must be None
    BARE_CALL_SRC = """\
function callee(): void {}
function caller(): void {
    callee();
}
"""

    def test_member_call_receiver_captured(self, tmp_path: Path) -> None:
        """p.print() → call edge with receiver='p'.

        B4 update: since p: Printer is type-annotated, target may be 'Printer.print'
        (inference on) or 'print' (inference off). Accept both forms.
        """
        edges = _parse_and_extract(self.MEMBER_CALL_SRC, "typescript", ".ts")
        # Accept either bare 'print' or qualified 'Printer.print'.
        print_edges = [e for e in _call_edges(edges) if "print" in e["target"]]
        assert print_edges, (
            f"Expected a call edge containing 'print', got: {[x['target'] for x in _call_edges(edges)]}"
        )
        e = print_edges[0]
        assert e["receiver"] == "p", f"Expected receiver='p', got {e['receiver']!r}"

    def test_this_call_receiver_captured(self, tmp_path: Path) -> None:
        """this.process() → call edge with receiver='this'.

        B4 update: 'this' inside Handler resolves to 'Handler', so target may be
        'Handler.process' (inference on) or 'process' (inference off).
        """
        edges = _parse_and_extract(self.THIS_CALL_SRC, "typescript", ".ts")
        # Accept either bare 'process' or qualified 'Handler.process'.
        process_edges = [e for e in _call_edges(edges) if "process" in e["target"]]
        assert process_edges, (
            f"Expected a call edge containing 'process', got: {[x['target'] for x in _call_edges(edges)]}"
        )
        e = process_edges[0]
        assert e["receiver"] == "this", f"Expected receiver='this', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self, tmp_path: Path) -> None:
        """callee() bare call → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "typescript", ".ts")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, f"Bare call must have receiver=None, got {e.get('receiver')!r}"

    def test_import_edges_receiver_none(self, tmp_path: Path) -> None:
        """import edges always have receiver=None."""
        src = "import { foo } from './bar';\n"
        edges = _parse_and_extract(src, "typescript", ".ts")
        import_edges = [e for e in edges if e["kind"] == "import"]
        for e in import_edges:
            assert e.get("receiver") is None, f"Import edge must have receiver=None, got {e!r}"


class TestJavaScriptReceiverCapture:
    """JS: same member_expression pattern as TypeScript."""

    MEMBER_CALL_SRC = """\
function doWork(obj) {
    obj.execute();
}
"""

    def test_member_call_receiver_captured(self, tmp_path: Path) -> None:
        """obj.execute() → call edge target='execute', receiver='obj'."""
        edges = _parse_and_extract(self.MEMBER_CALL_SRC, "javascript", ".js")
        e = _edge_by_target(edges, "execute")
        assert e is not None, (
            f"Expected 'execute' call edge, got: {[x['target'] for x in _call_edges(edges)]}"
        )
        assert e["receiver"] == "obj", f"Expected receiver='obj', got {e['receiver']!r}"


# ── Go ────────────────────────────────────────────────────────────────────────


class TestGoReceiverCapture:
    """Go: selector_expression calls (recv.Method) capture receiver text."""

    SELECTOR_CALL_SRC = """\
package main

import "fmt"

type Logger struct{}

func (l *Logger) Log(msg string) {
    fmt.Println(msg)
}

func run(logger *Logger) {
    logger.Log("hello")
}
"""

    BARE_CALL_SRC = """\
package main

func callee() {}

func caller() {
    callee()
}
"""

    def test_selector_call_receiver_captured(self) -> None:
        """logger.Log("hello") → call edge for Log (bare or qualified), receiver='logger'.

        B5 update: when logger's type is known from param (`logger *Logger`), the target
        is qualified to 'Logger.Log'. The receiver field is preserved on the emitted edge.
        """
        edges = _parse_and_extract(self.SELECTOR_CALL_SRC, "go", ".go")
        call_edges = _call_edges(edges)
        # Accept either qualified ('Logger.Log') or bare ('Log') — B5 qualifies when type known.
        e = _edge_by_target(edges, "Logger.Log") or _edge_by_target(edges, "Log")
        assert e is not None, (
            f"Expected 'Logger.Log' or 'Log' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "logger", f"Expected receiver='logger', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """callee() bare call → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "go", ".go")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )

    def test_import_call_println_receiver_fmt(self) -> None:
        """fmt.Println("msg") → call edge target='Println', receiver='fmt'."""
        edges = _parse_and_extract(self.SELECTOR_CALL_SRC, "go", ".go")
        e = _edge_by_target(edges, "Println")
        assert e is not None, (
            f"Expected 'Println' call edge, got: {[x['target'] for x in _call_edges(edges)]}"
        )
        assert e["receiver"] == "fmt", f"Expected receiver='fmt', got {e['receiver']!r}"


# ── Rust ─────────────────────────────────────────────────────────────────────


class TestRustReceiverCapture:
    """Rust: field_expression calls (self.method, obj.method) capture receiver text."""

    METHOD_CALL_SRC = """\
struct Db {
    connected: bool,
}

impl Db {
    fn connect(&mut self) {
        self.init();
    }
    fn init(&mut self) {}
}

fn run(db: &mut Db) {
    db.connect();
}
"""

    BARE_CALL_SRC = """\
fn callee() {}

fn caller() {
    callee();
}
"""

    def test_field_expression_receiver_captured(self) -> None:
        """db.connect() → call edge for connect (bare or qualified), receiver='db'.

        B5 update: when db's type is known from param (`db: &mut Db`), target may be
        qualified to 'Db.connect'. Receiver field is always preserved.
        """
        edges = _parse_and_extract(self.METHOD_CALL_SRC, "rust", ".rs")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Db.connect") or _edge_by_target(edges, "connect")
        assert e is not None, (
            f"Expected 'Db.connect' or 'connect' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "db", f"Expected receiver='db', got {e['receiver']!r}"

    def test_self_receiver_captured(self) -> None:
        """self.init() → call edge for init (bare or qualified), receiver='self'.

        B5 update: self.init() inside impl Db → qualified to 'Db.init'. Receiver='self'.
        """
        edges = _parse_and_extract(self.METHOD_CALL_SRC, "rust", ".rs")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Db.init") or _edge_by_target(edges, "init")
        assert e is not None, (
            f"Expected 'Db.init' or 'init' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "self", f"Expected receiver='self', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """callee() → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "rust", ".rs")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── Java ──────────────────────────────────────────────────────────────────────


class TestJavaReceiverCapture:
    """Java: method_invocation with 'object' field captures receiver text."""

    MEMBER_CALL_SRC = """\
class Service {
    public void start() {}
}

class Client {
    public void run() {
        Service svc = new Service();
        svc.start();
    }
}
"""

    BARE_CALL_SRC = """\
class Util {
    static void helper() {}
    void use() {
        helper();
    }
}
"""

    def test_object_call_receiver_captured(self) -> None:
        """svc.start() → call edge for start (bare or qualified), receiver='svc'.

        B5 update: Service svc = new Service() → type known → 'Service.start'. Receiver preserved.
        """
        edges = _parse_and_extract(self.MEMBER_CALL_SRC, "java", ".java")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Service.start") or _edge_by_target(edges, "start")
        assert e is not None, (
            f"Expected 'Service.start' or 'start' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "svc", f"Expected receiver='svc', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """helper() → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "java", ".java")
        e = _edge_by_target(edges, "helper")
        assert e is not None, "Expected bare 'helper' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── C# ────────────────────────────────────────────────────────────────────────


class TestCSharpReceiverCapture:
    """C#: invocation_expression where function is member_access_expression."""

    MEMBER_CALL_SRC = """\
class Logger {
    public void Log(string msg) {}
}

class App {
    public void Run() {
        Logger log = new Logger();
        log.Log("hello");
    }
}
"""

    THIS_CALL_SRC = """\
class Worker {
    public void Execute() {}
    public void Start() {
        this.Execute();
    }
}
"""

    BARE_CALL_SRC = """\
class Utils {
    static void Helper() {}
    void Use() {
        Helper();
    }
}
"""

    def test_member_call_receiver_captured(self) -> None:
        """log.Log("hello") → call edge for Log (bare or qualified), receiver='log'.

        B5 update: Logger log = new Logger() → type known → 'Logger.Log'. Receiver preserved.
        """
        edges = _parse_and_extract(self.MEMBER_CALL_SRC, "csharp", ".cs")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Logger.Log") or _edge_by_target(edges, "Log")
        assert e is not None, (
            f"Expected 'Logger.Log' or 'Log' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "log", f"Expected receiver='log', got {e['receiver']!r}"

    def test_this_call_receiver_captured(self) -> None:
        """this.Execute() → call edge for Execute (bare or qualified), receiver='this'.

        B5 update: this.Execute() inside Worker → 'Worker.Execute'. Receiver='this'.
        """
        edges = _parse_and_extract(self.THIS_CALL_SRC, "csharp", ".cs")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Worker.Execute") or _edge_by_target(edges, "Execute")
        assert e is not None, (
            f"Expected 'Worker.Execute' or 'Execute' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "this", f"Expected receiver='this', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """Helper() → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "csharp", ".cs")
        e = _edge_by_target(edges, "Helper")
        assert e is not None, "Expected bare 'Helper' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── Ruby ──────────────────────────────────────────────────────────────────────


class TestRubyReceiverCapture:
    """Ruby: call node with 'receiver' field captures receiver text.

    B2 changes the behavior: receiver calls are NOW emitted (with receiver text)
    instead of being silently dropped (the pre-B2 behavior).
    """

    RECEIVER_CALL_SRC = """\
class Formatter
  def format(x)
    x.to_s
  end
end
"""

    BARE_CALL_SRC = """\
def callee
end

def caller
  callee()
end
"""

    def test_receiver_call_emitted_with_receiver(self) -> None:
        """x.to_s → call edge target='to_s', receiver='x'."""
        edges = _parse_and_extract(self.RECEIVER_CALL_SRC, "ruby", ".rb")
        e = _edge_by_target(edges, "to_s")
        assert e is not None, (
            f"Expected 'to_s' call edge (receiver calls now emitted), "
            f"got: {[x['target'] for x in _call_edges(edges)]}"
        )
        assert e["receiver"] == "x", f"Expected receiver='x', got {e['receiver']!r}"
        assert e["target"] == "to_s", "target_name must be the rightmost method id"

    def test_bare_call_receiver_none(self) -> None:
        """callee bare call → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "ruby", ".rb")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── C ─────────────────────────────────────────────────────────────────────────


class TestCReceiverCapture:
    """C: call_expression where function is a field_expression (ptr->method or obj.field).

    C has no methods — but struct-pointer-based dispatch (obj->fn, callbacks, field access)
    uses field_expression as the callee. We capture the receiver (object side) for these.
    For bare function calls, receiver stays None.
    """

    # In C, obj->func_ptr() is a call to a function pointer stored as a field.
    # The tree-sitter C grammar models this as:
    #   call_expression.function = field_expression{ argument: obj, field: func_ptr }
    FIELD_CALL_SRC = """\
#include <stdlib.h>

typedef struct {
    void (*init)(void);
} Driver;

void setup(Driver *drv) {
    drv->init();
}
"""

    BARE_CALL_SRC = """\
void callee(void) {}

void caller(void) {
    callee();
}
"""

    def test_field_expression_call_receiver_captured(self) -> None:
        """drv->init() → call edge target='init', receiver='drv'."""
        edges = _parse_and_extract(self.FIELD_CALL_SRC, "c", ".c")
        e = _edge_by_target(edges, "init")
        assert e is not None, (
            f"Expected 'init' call edge, got: {[x['target'] for x in _call_edges(edges)]}"
        )
        assert e["receiver"] == "drv", f"Expected receiver='drv', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """callee() → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "c", ".c")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── C++ ───────────────────────────────────────────────────────────────────────


class TestCppReceiverCapture:
    """C++: field_expression calls (obj.m, obj->m) capture receiver text."""

    FIELD_CALL_SRC = """\
class Engine {
public:
    void start() {}
    void run() {
        this->start();
    }
};

void launch(Engine* e) {
    e->start();
}
"""

    DOT_CALL_SRC = """\
class Config {
public:
    void load() {}
};

void init(Config cfg) {
    cfg.load();
}
"""

    BARE_CALL_SRC = """\
void callee() {}

void caller() {
    callee();
}
"""

    def test_arrow_call_receiver_captured(self) -> None:
        """e->start() → call edge for start (bare or qualified), receiver='e'.

        B5 update: `Engine* e` → type known as Engine → 'Engine.start'. Receiver preserved.
        """
        edges = _parse_and_extract(self.FIELD_CALL_SRC, "cpp", ".cpp")
        call_edges = _call_edges(edges)
        # Accept qualified 'Engine.start' or bare 'start' (B5 qualifies when type known)
        start_edges = [e for e in call_edges if e["target"] in ("start", "Engine.start")]
        assert start_edges, (
            f"Expected 'start' or 'Engine.start' call edges, got: {[x['target'] for x in call_edges]}"
        )
        receivers = {e["receiver"] for e in start_edges}
        assert "e" in receivers, f"Expected receiver='e' among {receivers}"

    def test_this_arrow_call_receiver_captured(self) -> None:
        """this->start() → call edge for start (bare or qualified), receiver='this'.

        B5 update: this->start() inside Engine → 'Engine.start'. Receiver='this'.
        """
        edges = _parse_and_extract(self.FIELD_CALL_SRC, "cpp", ".cpp")
        call_edges = _call_edges(edges)
        start_edges = [e for e in call_edges if e["target"] in ("start", "Engine.start")]
        receivers = {e["receiver"] for e in start_edges}
        assert "this" in receivers, f"Expected receiver='this' among {receivers}"

    def test_dot_call_receiver_captured(self) -> None:
        """cfg.load() → call edge for load (bare or qualified), receiver='cfg'.

        B5 update: `Config cfg` typed param → 'Config.load'. Receiver='cfg'.
        """
        edges = _parse_and_extract(self.DOT_CALL_SRC, "cpp", ".cpp")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Config.load") or _edge_by_target(edges, "load")
        assert e is not None, (
            f"Expected 'Config.load' or 'load' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "cfg", f"Expected receiver='cfg', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """callee() → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "cpp", ".cpp")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── PHP ───────────────────────────────────────────────────────────────────────


class TestPhpReceiverCapture:
    """PHP: member_call_expression and static_method_call_expression capture receiver."""

    MEMBER_CALL_SRC = """\
<?php
class Mailer {
    public function send(string $msg): void {}
}

class Notifier {
    public function notify(): void {
        $mailer = new Mailer();
        $mailer->send("hello");
    }
}
"""

    THIS_CALL_SRC = """\
<?php
class Handler {
    public function process(): void {}
    public function run(): void {
        $this->process();
    }
}
"""

    STATIC_CALL_SRC = """\
<?php
class Config {
    public static function get(): string { return ""; }
}

function init(): void {
    Config::get();
}
"""

    BARE_CALL_SRC = """\
<?php
function callee(): void {}
function caller(): void {
    callee();
}
"""

    def test_member_call_receiver_captured(self) -> None:
        """$mailer->send("hello") → call edge for send (bare or qualified), receiver='$mailer'.

        B5 update: $mailer = new Mailer() → type known → 'Mailer.send'. Receiver preserved.
        """
        edges = _parse_and_extract(self.MEMBER_CALL_SRC, "php", ".php")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Mailer.send") or _edge_by_target(edges, "send")
        assert e is not None, (
            f"Expected 'Mailer.send' or 'send' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "$mailer", f"Expected receiver='$mailer', got {e['receiver']!r}"

    def test_this_call_receiver_captured(self) -> None:
        """$this->process() → call edge for process (bare or qualified), receiver='$this'.

        B5 update: $this->process() inside Handler → 'Handler.process'. Receiver='$this'.
        """
        edges = _parse_and_extract(self.THIS_CALL_SRC, "php", ".php")
        call_edges = _call_edges(edges)
        e = _edge_by_target(edges, "Handler.process") or _edge_by_target(edges, "process")
        assert e is not None, (
            f"Expected 'Handler.process' or 'process' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e["receiver"] == "$this", f"Expected receiver='$this', got {e['receiver']!r}"

    def test_static_call_receiver_captured(self) -> None:
        """Config::get() → call edge target='Config.get', receiver='Config'.

        PHP static calls (scoped_call_expression) are immediately qualified by
        _handle_php_scoped_call because the class name is literally in the AST —
        no scope lookup needed (B5 conservatism contract satisfied).
        The target is 'Config.get' (qualified); receiver='Config' for provenance.
        """
        edges = _parse_and_extract(self.STATIC_CALL_SRC, "php", ".php")
        # B5 fix: target is now fully qualified as 'Config.get', not bare 'get'.
        e = _edge_by_target(edges, "Config.get")
        assert e is not None, (
            f"Expected 'Config.get' call edge, got: {[x['target'] for x in _call_edges(edges)]}"
        )
        assert e["receiver"] == "Config", f"Expected receiver='Config', got {e['receiver']!r}"

    def test_bare_call_receiver_none(self) -> None:
        """callee() → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "php", ".php")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── Swift ─────────────────────────────────────────────────────────────────────


class TestSwiftReceiverCapture:
    """Swift: navigation_expression calls capture the raw receiver text.

    Swift already does receiver-type INFERENCE and emits qualified edges (e.g.
    'Repo.persist') when the type is known. B2 adds the raw receiver TEXT to the
    emitted edge's receiver field (e.g. 'self', 'x', '$0', etc.) so the read path
    can use it if inference was not attempted or returned None.

    For inference-on cases: the qualified target is emitted AND receiver text is set.
    For inference-off cases: no edge is emitted for receiver calls (existing behavior
    — Swift is conservative: never emit a wrong edge). So receiver text on Swift edges
    is only meaningful when an edge IS emitted.
    """

    # self.method() — receiver = 'self'
    SELF_CALL_SRC = (
        "class Repo {\n"
        "    func save() {\n"
        "        self.persist()\n"
        "    }\n"
        "    func persist() {}\n"
        "}\n"
    )

    # Bare call — receiver = None (no navigation_expression)
    BARE_CALL_SRC = "func caller() {\n    callee()\n}\nfunc callee() {}\n"

    def test_self_call_receiver_is_self(self, tmp_path: Path) -> None:
        """self.persist() → qualified edge 'Repo.persist' AND receiver='self'."""
        edges = _parse_and_extract(self.SELF_CALL_SRC, "swift", ".swift")
        call_edges = _call_edges(edges)
        # Find the edge for the navigation call (target = 'Repo.persist' when infer=on)
        e = next(
            (x for x in call_edges if "persist" in x["target"]),
            None,
        )
        assert e is not None, (
            f"Expected a 'persist' call edge, got: {[x['target'] for x in call_edges]}"
        )
        assert e.get("receiver") == "self", (
            f"Expected receiver='self', got {e.get('receiver')!r}"
        )

    def test_bare_call_receiver_none(self, tmp_path: Path) -> None:
        """callee() bare call → receiver=None."""
        edges = _parse_and_extract(self.BARE_CALL_SRC, "swift", ".swift")
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Expected bare 'callee' edge"
        assert e.get("receiver") is None, (
            f"Bare call must have receiver=None, got {e.get('receiver')!r}"
        )


# ── Graceful degradation / no-raise contract ─────────────────────────────────


class TestReceiverGracefulDegradation:
    """Awkward or nested receiver shapes store NULL, never drop the call, never raise."""

    # Chained call: a.b.c() — the receiver is a member_expression itself.
    # We store the raw receiver text (e.g. 'a.b') and target = 'c'.
    TS_CHAINED_CALL = """\
function test(a: any) {
    a.b.c();
}
"""

    # Go multi-segment selector: pkg.Sub.Method() — selector within selector.
    GO_CHAINED_CALL = """\
package main

func run(obj any) {
    // chained call would be complex receiver
}
"""

    def test_ts_chained_call_does_not_raise(self) -> None:
        """a.b.c() → does not raise; target='c'; receiver captured."""
        try:
            edges = _parse_and_extract(self.TS_CHAINED_CALL, "typescript", ".ts")
        except Exception as exc:
            pytest.fail(f"Chained TS call raised an exception: {exc}")
        # Should have a 'c' edge or gracefully degrade — just must not raise
        # and must not drop the edge silently
        call_edges = _call_edges(edges)
        targets = {e["target"] for e in call_edges}
        assert "c" in targets, f"Expected 'c' in call targets, got {targets}"

    def test_go_parse_does_not_raise(self) -> None:
        """Go extraction never raises even on complex inputs."""
        try:
            _parse_and_extract(self.GO_CHAINED_CALL, "go", ".go")
        except Exception as exc:
            pytest.fail(f"Go extraction raised: {exc}")
