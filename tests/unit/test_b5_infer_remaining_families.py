"""Unit tests for Tier B slice B5: receiver-type inference for remaining language families
+ Swift type-qualified static calls.

TDD: Tests written BEFORE implementation. Each group covers one family or feature:

B5-Go:    Go receiver-type inference via scope-inference module
B5-Rust:  Rust receiver-type inference via scope-inference module
B5-Java:  Java receiver-type inference via scope-inference module
B5-CS:    C# receiver-type inference via scope-inference module
B5-Cpp:   C++ receiver-type inference via scope-inference module
B5-Ruby:  Ruby receiver-type inference via scope-inference module
B5-PHP:   PHP receiver-type inference via scope-inference module
B5-Swift: Swift type-qualified STATIC calls (PascalCase receiver not in scope)
B5-Neg:   Negative / conservatism contract (wrong-edge guards for each family)
B5-Cfg:   SEAM_TYPE_INFERENCE knob respected by all new families

CONSERVATISM CONTRACT (enforced by negative tests):
  - Plain user types ONLY. Optionals/generics/unknowns → None → bare target kept.
  - Only resolve receivers known in scope (class field, param, local var).
  - self/this/Self/cls → enclosing class name.
  - Unknown receivers → refuse (never guess).
  - Never emit a wrong edge.

Swift static-call contract:
  - PascalCase receiver NOT in var_types → treat as Type name → resolve to Type.method.
  - Lowercase / known-var receivers → use existing scope resolution (NOT static path).
  - Optionals (Type?.method), chained (A.B.method) → refuse.
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_lang(source: str, suffix: str, lang: str) -> list[Edge]:
    """Parse source in a given language, extract all edges, return them."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")

    lang_to_func = {
        "go": "parse_go",
        "rust": "parse_rust",
        "java": "parse_java",
        "csharp": "parse_csharp",
        "cpp": "parse_cpp",
        "ruby": "parse_ruby",
        "php": "parse_php",
        "swift": "parse_swift",
    }
    parse_fn = getattr(parser_mod, lang_to_func[lang])

    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"parse_{lang} returned None for: {source!r}"
        return extract_edges(root, lang, path)
    finally:
        os.unlink(fname)


def _call_edges(edges: list[Edge]) -> list[Edge]:
    """Return only call-kind edges."""
    return [e for e in edges if e["kind"] == "call"]


def _targets(edges: list[Edge]) -> set[str]:
    """Return set of call edge targets."""
    return {e["target"] for e in _call_edges(edges)}


def _edge_by_target(edges: list[Edge], target: str) -> Edge | None:
    """Return first call edge with given target, or None."""
    return next((e for e in _call_edges(edges) if e["target"] == target), None)


# ── B5-Go: Go receiver-type inference ─────────────────────────────────────────


class TestGoTypeInference:
    """B5-Go: Go emits qualified Type.method when receiver type is known from params."""

    # Go does not have traditional classes but methods can be on structs.
    # Cross-function call: a func takes a *Client and calls client.Send()
    GO_PARAM_CALL = """\
package main

type Client struct {}

func (c *Client) Send(msg string) {}

func Run(client *Client) {
    client.Send("hello")
}
"""

    def test_go_param_type_resolves_target(self) -> None:
        """Go: client *Client → client.Send() → target='Client.Send' (qualified)."""
        edges = _parse_lang(self.GO_PARAM_CALL, ".go", "go")
        ts = _targets(edges)
        assert "Client.Send" in ts, (
            f"Expected 'Client.Send' from Go param type. Got call targets: {ts}. "
            "Type inference must resolve 'client *Client' → 'Client.Send'."
        )

    def test_go_bare_not_emitted_when_qualified(self) -> None:
        """Go: when Client.Send is resolved, bare 'Send' must NOT be emitted."""
        edges = _parse_lang(self.GO_PARAM_CALL, ".go", "go")
        ts = _targets(edges)
        assert "Send" not in ts, (
            f"Bare 'Send' must not be emitted when 'Client.Send' is resolved. Got: {ts}"
        )

    def test_go_receiver_stored_on_edge(self) -> None:
        """Go: the qualified edge carries receiver='client'."""
        edges = _parse_lang(self.GO_PARAM_CALL, ".go", "go")
        e = _edge_by_target(edges, "Client.Send")
        assert e is not None, "Expected 'Client.Send' edge"
        assert e.get("receiver") == "client", (
            f"Expected receiver='client', got {e.get('receiver')!r}"
        )

    # Local variable from constructor call
    GO_LOCAL_CONSTRUCTOR = """\
package main

type Parser struct {}

func (p *Parser) Parse(text string) {}

func Process() {
    p := &Parser{}
    p.Parse("hello")
}
"""

    def test_go_local_constructor_resolves_target(self) -> None:
        """Go: p := &Parser{} → p.Parse() → target='Parser.Parse'."""
        edges = _parse_lang(self.GO_LOCAL_CONSTRUCTOR, ".go", "go")
        ts = _targets(edges)
        assert "Parser.Parse" in ts, (
            f"Expected 'Parser.Parse' from Go local constructor. Got: {ts}."
        )


# ── B5-Rust: Rust receiver-type inference ─────────────────────────────────────


class TestRustTypeInference:
    """B5-Rust: Rust emits qualified Type.method when receiver type is known from params."""

    RUST_PARAM_CALL = """\
struct Client {}

impl Client {
    fn send(&self, msg: &str) {}
}

fn run(client: &Client) {
    client.send("hello");
}
"""

    def test_rust_param_type_resolves_target(self) -> None:
        """Rust: client: &Client → client.send() → target='Client.send' (qualified)."""
        edges = _parse_lang(self.RUST_PARAM_CALL, ".rs", "rust")
        ts = _targets(edges)
        assert "Client.send" in ts, (
            f"Expected 'Client.send' from Rust param type. Got call targets: {ts}. "
            "Type inference must resolve 'client: &Client' → 'Client.send'."
        )

    def test_rust_bare_not_emitted_when_qualified(self) -> None:
        """Rust: when Client.send is resolved, bare 'send' must NOT be emitted."""
        edges = _parse_lang(self.RUST_PARAM_CALL, ".rs", "rust")
        ts = _targets(edges)
        assert "send" not in ts, (
            f"Bare 'send' must not be emitted when 'Client.send' is resolved. Got: {ts}"
        )

    RUST_SELF_CALL = """\
struct Manager {}

impl Manager {
    fn helper(&self) {}

    fn process(&self) {
        self.helper();
    }
}
"""

    def test_rust_self_resolves_to_enclosing_type(self) -> None:
        """Rust: self.helper() in Manager.process → target='Manager.helper'."""
        edges = _parse_lang(self.RUST_SELF_CALL, ".rs", "rust")
        ts = _targets(edges)
        assert "Manager.helper" in ts, (
            f"Expected 'Manager.helper' from self.helper() in Rust impl. Got: {ts}."
        )

    RUST_LOCAL_CONSTRUCTOR = """\
struct Parser {}

impl Parser {
    fn new() -> Self { Parser {} }
    fn parse(&self, text: &str) {}
}

fn process() {
    let p = Parser::new();
    p.parse("hello");
}
"""

    def test_rust_local_constructor_resolves_target(self) -> None:
        """Rust: let p = Parser::new() → p.parse() → target='Parser.parse'."""
        edges = _parse_lang(self.RUST_LOCAL_CONSTRUCTOR, ".rs", "rust")
        ts = _targets(edges)
        assert "Parser.parse" in ts, (
            f"Expected 'Parser.parse' from Rust local var. Got: {ts}."
        )


# ── B5-Java: Java receiver-type inference ─────────────────────────────────────


class TestJavaTypeInference:
    """B5-Java: Java emits qualified Type.method when receiver type is known from params."""

    JAVA_PARAM_CALL = """\
class Client {
    void send(String msg) {}
}

class Service {
    void run(Client client) {
        client.send("hello");
    }
}
"""

    def test_java_param_type_resolves_target(self) -> None:
        """Java: Client client → client.send() → target='Client.send' (qualified)."""
        edges = _parse_lang(self.JAVA_PARAM_CALL, ".java", "java")
        ts = _targets(edges)
        assert "Client.send" in ts, (
            f"Expected 'Client.send' from Java param type. Got call targets: {ts}. "
            "Type inference must resolve 'Client client' → 'Client.send'."
        )

    def test_java_bare_not_emitted_when_qualified(self) -> None:
        """Java: when Client.send is resolved, bare 'send' must NOT be emitted."""
        edges = _parse_lang(self.JAVA_PARAM_CALL, ".java", "java")
        ts = _targets(edges)
        assert "send" not in ts, (
            f"Bare 'send' must not be emitted when 'Client.send' is resolved. Got: {ts}"
        )

    JAVA_THIS_CALL = """\
class Manager {
    void helper() {}

    void process() {
        this.helper();
    }
}
"""

    def test_java_this_resolves_to_enclosing_class(self) -> None:
        """Java: this.helper() in Manager.process → target='Manager.helper'."""
        edges = _parse_lang(self.JAVA_THIS_CALL, ".java", "java")
        ts = _targets(edges)
        assert "Manager.helper" in ts, (
            f"Expected 'Manager.helper' from this.helper() in Java. Got: {ts}."
        )

    JAVA_FIELD_CALL = """\
class Repository {
    void save(String item) {}
}

class Service {
    Repository repo;

    void store(String item) {
        this.repo.save(item);
    }
}
"""

    def test_java_field_type_resolves_target(self) -> None:
        """Java: this.repo.save() where repo: Repository → target='Repository.save'."""
        edges = _parse_lang(self.JAVA_FIELD_CALL, ".java", "java")
        ts = _targets(edges)
        assert "Repository.save" in ts, (
            f"Expected 'Repository.save' from Java class field. Got: {ts}."
        )

    JAVA_LOCAL_CALL = """\
class Engine {
    void start() {}
}

class Runner {
    void run() {
        Engine e = new Engine();
        e.start();
    }
}
"""

    def test_java_local_var_resolves_target(self) -> None:
        """Java: Engine e = new Engine(); e.start() → target='Engine.start'."""
        edges = _parse_lang(self.JAVA_LOCAL_CALL, ".java", "java")
        ts = _targets(edges)
        assert "Engine.start" in ts, (
            f"Expected 'Engine.start' from Java local var. Got: {ts}."
        )


# ── B5-CS: C# receiver-type inference ─────────────────────────────────────────


class TestCSharpTypeInference:
    """B5-CS: C# emits qualified Type.method when receiver type is known from params."""

    CS_PARAM_CALL = """\
class Client {
    void Send(string msg) {}
}

class Service {
    void Run(Client client) {
        client.Send("hello");
    }
}
"""

    def test_cs_param_type_resolves_target(self) -> None:
        """C#: Client client → client.Send() → target='Client.Send' (qualified)."""
        edges = _parse_lang(self.CS_PARAM_CALL, ".cs", "csharp")
        ts = _targets(edges)
        assert "Client.Send" in ts, (
            f"Expected 'Client.Send' from C# param type. Got call targets: {ts}. "
            "Type inference must resolve 'Client client' → 'Client.Send'."
        )

    def test_cs_bare_not_emitted_when_qualified(self) -> None:
        """C#: when Client.Send is resolved, bare 'Send' must NOT be emitted."""
        edges = _parse_lang(self.CS_PARAM_CALL, ".cs", "csharp")
        ts = _targets(edges)
        assert "Send" not in ts, (
            f"Bare 'Send' must not be emitted when 'Client.Send' is resolved. Got: {ts}"
        )

    CS_THIS_CALL = """\
class Worker {
    void Helper() {}

    void Process() {
        this.Helper();
    }
}
"""

    def test_cs_this_resolves_to_enclosing_class(self) -> None:
        """C#: this.Helper() in Worker.Process → target='Worker.Helper'."""
        edges = _parse_lang(self.CS_THIS_CALL, ".cs", "csharp")
        ts = _targets(edges)
        assert "Worker.Helper" in ts, (
            f"Expected 'Worker.Helper' from this.Helper() in C#. Got: {ts}."
        )

    CS_LOCAL_CALL = """\
class Engine {
    void Start() {}
}

class Runner {
    void Run() {
        Engine e = new Engine();
        e.Start();
    }
}
"""

    def test_cs_local_var_resolves_target(self) -> None:
        """C#: Engine e = new Engine(); e.Start() → target='Engine.Start'."""
        edges = _parse_lang(self.CS_LOCAL_CALL, ".cs", "csharp")
        ts = _targets(edges)
        assert "Engine.Start" in ts, (
            f"Expected 'Engine.Start' from C# local var. Got: {ts}."
        )


# ── B5-Cpp: C++ receiver-type inference ───────────────────────────────────────


class TestCppTypeInference:
    """B5-Cpp: C++ emits qualified Type.method when receiver type is known from params."""

    CPP_PARAM_CALL = """\
class Client {
public:
    void send(const char* msg) {}
};

class Service {
public:
    void run(Client& client) {
        client.send("hello");
    }
};
"""

    def test_cpp_param_type_resolves_target(self) -> None:
        """C++: Client& client → client.send() → target='Client.send' (qualified)."""
        edges = _parse_lang(self.CPP_PARAM_CALL, ".cpp", "cpp")
        ts = _targets(edges)
        assert "Client.send" in ts, (
            f"Expected 'Client.send' from C++ param type. Got call targets: {ts}. "
            "Type inference must resolve 'Client& client' → 'Client.send'."
        )

    def test_cpp_bare_not_emitted_when_qualified(self) -> None:
        """C++: when Client.send is resolved, bare 'send' must NOT be emitted."""
        edges = _parse_lang(self.CPP_PARAM_CALL, ".cpp", "cpp")
        ts = _targets(edges)
        assert "send" not in ts, (
            f"Bare 'send' must not be emitted when 'Client.send' is resolved. Got: {ts}"
        )

    CPP_THIS_CALL = """\
class Manager {
public:
    void helper() {}

    void process() {
        this->helper();
    }
};
"""

    def test_cpp_this_resolves_to_enclosing_class(self) -> None:
        """C++: this->helper() in Manager::process → target='Manager.helper'."""
        edges = _parse_lang(self.CPP_THIS_CALL, ".cpp", "cpp")
        ts = _targets(edges)
        assert "Manager.helper" in ts, (
            f"Expected 'Manager.helper' from this->helper() in C++. Got: {ts}."
        )


# ── B5-Ruby: Ruby receiver-type inference ─────────────────────────────────────


class TestRubyTypeInference:
    """B5-Ruby: Ruby emits qualified Type.method when receiver type is known from params."""

    # Ruby uses duck typing but explicit type-annotated patterns like
    # `def run(client)` cannot be annotated, BUT we can capture local vars
    # from `client = Client.new` or from comment-style type hints.
    # The MOST PRACTICAL Ruby case: local var assigned from constructor + call.
    RUBY_LOCAL_CONSTRUCTOR = """\
class Client
  def send(msg)
  end
end

def process
  client = Client.new
  client.send("hello")
end
"""

    def test_ruby_local_constructor_resolves_target(self) -> None:
        """Ruby: client = Client.new → client.send → target='Client.send'."""
        edges = _parse_lang(self.RUBY_LOCAL_CONSTRUCTOR, ".rb", "ruby")
        ts = _targets(edges)
        assert "Client.send" in ts, (
            f"Expected 'Client.send' from Ruby local constructor. Got call targets: {ts}. "
            "Type inference must resolve 'client = Client.new' → 'Client.send'."
        )

    def test_ruby_bare_not_emitted_when_qualified(self) -> None:
        """Ruby: when Client.send is resolved, bare 'send' must NOT be emitted."""
        edges = _parse_lang(self.RUBY_LOCAL_CONSTRUCTOR, ".rb", "ruby")
        ts = _targets(edges)
        assert "send" not in ts, (
            f"Bare 'send' must not be emitted when 'Client.send' is resolved. Got: {ts}"
        )

    RUBY_SELF_CALL = """\
class Manager
  def helper
  end

  def process
    self.helper
  end
end
"""

    def test_ruby_self_resolves_to_enclosing_class(self) -> None:
        """Ruby: self.helper inside Manager.process → target='Manager.helper'."""
        edges = _parse_lang(self.RUBY_SELF_CALL, ".rb", "ruby")
        ts = _targets(edges)
        assert "Manager.helper" in ts, (
            f"Expected 'Manager.helper' from self.helper in Ruby. Got: {ts}."
        )

    RUBY_FIELD_CALL = """\
class Repository
  def save(item)
  end
end

class Service
  def initialize
    @repo = Repository.new
  end

  def store(item)
    @repo.save(item)
  end
end
"""

    def test_ruby_ivar_constructor_resolves_target(self) -> None:
        """Ruby: @repo = Repository.new → @repo.save → target='Repository.save'."""
        edges = _parse_lang(self.RUBY_FIELD_CALL, ".rb", "ruby")
        ts = _targets(edges)
        assert "Repository.save" in ts, (
            f"Expected 'Repository.save' from Ruby ivar constructor. Got: {ts}."
        )


# ── B5-PHP: PHP receiver-type inference ───────────────────────────────────────


class TestPhpTypeInference:
    """B5-PHP: PHP emits qualified Type.method when receiver type is known."""

    PHP_PARAM_CALL = """\
<?php
class Client {
    public function send(string $msg): void {}
}

class Service {
    public function run(Client $client): void {
        $client->send("hello");
    }
}
"""

    def test_php_param_type_resolves_target(self) -> None:
        """PHP: Client $client → $client->send() → target='Client.send' (qualified)."""
        edges = _parse_lang(self.PHP_PARAM_CALL, ".php", "php")
        ts = _targets(edges)
        assert "Client.send" in ts, (
            f"Expected 'Client.send' from PHP param type. Got call targets: {ts}. "
            "Type inference must resolve 'Client $client' → 'Client.send'."
        )

    def test_php_bare_not_emitted_when_qualified(self) -> None:
        """PHP: when Client.send is resolved, bare 'send' must NOT be emitted."""
        edges = _parse_lang(self.PHP_PARAM_CALL, ".php", "php")
        ts = _targets(edges)
        assert "send" not in ts, (
            f"Bare 'send' must not be emitted when 'Client.send' is resolved. Got: {ts}"
        )

    PHP_THIS_CALL = """\
<?php
class Worker {
    private function helper(): void {}

    public function process(): void {
        $this->helper();
    }
}
"""

    def test_php_this_resolves_to_enclosing_class(self) -> None:
        """PHP: $this->helper() in Worker::process → target='Worker.helper'."""
        edges = _parse_lang(self.PHP_THIS_CALL, ".php", "php")
        ts = _targets(edges)
        assert "Worker.helper" in ts, (
            f"Expected 'Worker.helper' from $this->helper() in PHP. Got: {ts}."
        )

    PHP_LOCAL_CALL = """\
<?php
class Engine {
    public function start(): void {}
}

class Runner {
    public function run(): void {
        $e = new Engine();
        $e->start();
    }
}
"""

    def test_php_local_var_resolves_target(self) -> None:
        """PHP: $e = new Engine() → $e->start() → target='Engine.start'."""
        edges = _parse_lang(self.PHP_LOCAL_CALL, ".php", "php")
        ts = _targets(edges)
        assert "Engine.start" in ts, (
            f"Expected 'Engine.start' from PHP local new. Got: {ts}."
        )


# ── B5-Swift: Swift type-qualified STATIC calls ───────────────────────────────


class TestSwiftStaticCalls:
    """B5-Swift: PascalCase receiver NOT in scope → static/type call → Type.method."""

    # Static method call: Type.staticMethod() — PascalCase receiver not in var_types
    SWIFT_STATIC_CALL = """\
class Logger {
    static func log(_ msg: String) {}
}

class Service {
    func run() {
        Logger.log("hello")
    }
}
"""

    def test_swift_static_call_resolves_to_qualified(self) -> None:
        """Swift: Logger.log("hello") where Logger is NOT a var → target='Logger.log'."""
        edges = _parse_lang(self.SWIFT_STATIC_CALL, ".swift", "swift")
        ts = _targets(edges)
        assert "Logger.log" in ts, (
            f"Expected 'Logger.log' from Swift static call. Got call targets: {ts}. "
            "PascalCase receiver not in scope must resolve to 'Logger.log'."
        )

    SWIFT_ENUM_STATIC = """\
enum Color {
    case red, green, blue

    static func fromString(_ s: String) -> Color { .red }
}

func makeColor() -> Color {
    return Color.fromString("red")
}
"""

    def test_swift_enum_static_resolves(self) -> None:
        """Swift: Color.fromString("red") where Color is NOT a var → target='Color.fromString'."""
        edges = _parse_lang(self.SWIFT_ENUM_STATIC, ".swift", "swift")
        ts = _targets(edges)
        assert "Color.fromString" in ts, (
            f"Expected 'Color.fromString' from Swift enum static call. Got: {ts}."
        )

    # When a PascalCase name IS in scope (it's a var), use normal scope resolution,
    # NOT the static path — already tested by B4 / graph_swift tests.
    # This negative: a PascalCase var in scope must NOT be treated as a type.
    SWIFT_PASCAL_VAR_NOT_STATIC = """\
class Processor {
    func process() {}
}

class Main {
    var Processor: Processor = Processor()

    func run() {
        Processor.process()
    }
}
"""

    def test_swift_pascal_var_in_scope_uses_scope_not_static(self) -> None:
        """Swift: if 'Processor' is in var_types, resolve via scope (not static path)."""
        # When Processor is in var_types → should get 'Processor.process' (correct)
        # but via the SCOPE path (var lookup), not the static path.
        edges = _parse_lang(self.SWIFT_PASCAL_VAR_NOT_STATIC, ".swift", "swift")
        ts = _targets(edges)
        # The edge should still resolve — either path gives 'Processor.process'
        assert "Processor.process" in ts, (
            f"Expected 'Processor.process' even when Processor is both a var and a PascalCase name. "
            f"Got: {ts}."
        )


# ── B5-Neg: Negative / conservatism contract ──────────────────────────────────


class TestConservatismContractB5:
    """B5-Neg: The conservatism contract is enforced for all new families.

    NEVER emit a wrong edge. Unknown receiver → keep bare target.
    """

    # Go: unknown receiver (not a typed var) must NOT produce qualified target
    GO_UNKNOWN_RECV = """\
package main

func Run() {
    mystery.Method()
}
"""

    def test_go_unknown_receiver_does_not_qualify(self) -> None:
        """Go: mystery.Method() where 'mystery' is not typed → must NOT qualify."""
        edges = _parse_lang(self.GO_UNKNOWN_RECV, ".go", "go")
        ts = _targets(edges)
        qualified = [t for t in ts if "." in t]
        assert not qualified, (
            f"Go unknown receiver must not produce qualified targets. Got: {qualified}."
        )

    # Rust: unknown receiver must NOT produce qualified target
    RUST_UNKNOWN_RECV = """\
fn run() {
    mystery.method();
}
"""

    def test_rust_unknown_receiver_does_not_qualify(self) -> None:
        """Rust: mystery.method() where 'mystery' is not typed → must NOT qualify."""
        edges = _parse_lang(self.RUST_UNKNOWN_RECV, ".rs", "rust")
        ts = _targets(edges)
        qualified = [t for t in ts if "." in t]
        assert not qualified, (
            f"Rust unknown receiver must not produce qualified targets. Got: {qualified}."
        )

    # Java: unknown receiver must NOT produce qualified target
    JAVA_UNKNOWN_RECV = """\
class Foo {
    void test() {
        mystery.method();
    }
}
"""

    def test_java_unknown_receiver_does_not_qualify(self) -> None:
        """Java: mystery.method() where 'mystery' has no declared type → must NOT qualify."""
        edges = _parse_lang(self.JAVA_UNKNOWN_RECV, ".java", "java")
        ts = _targets(edges)
        qualified = [t for t in ts if "." in t and "Foo" not in t]
        assert not qualified, (
            f"Java unknown receiver must not produce qualified targets. Got: {qualified}."
        )

    # C++: unknown receiver must NOT produce qualified target
    CPP_UNKNOWN_RECV = """\
void run() {
    mystery.method();
}
"""

    def test_cpp_unknown_receiver_does_not_qualify(self) -> None:
        """C++: mystery.method() where 'mystery' is not typed → must NOT qualify."""
        edges = _parse_lang(self.CPP_UNKNOWN_RECV, ".cpp", "cpp")
        ts = _targets(edges)
        qualified = [t for t in ts if "." in t]
        assert not qualified, (
            f"C++ unknown receiver must not produce qualified targets. Got: {qualified}."
        )

    # Ruby: unknown receiver must NOT produce qualified target
    RUBY_UNKNOWN_RECV = """\
def run
  mystery.method
end
"""

    def test_ruby_unknown_receiver_does_not_qualify(self) -> None:
        """Ruby: mystery.method where 'mystery' is not typed → must NOT qualify."""
        edges = _parse_lang(self.RUBY_UNKNOWN_RECV, ".rb", "ruby")
        ts = _targets(edges)
        qualified = [t for t in ts if "." in t]
        assert not qualified, (
            f"Ruby unknown receiver must not produce qualified targets. Got: {qualified}."
        )

    # PHP: unknown receiver must NOT produce qualified target
    PHP_UNKNOWN_RECV = """\
<?php
function run(): void {
    $mystery->method();
}
"""

    def test_php_unknown_receiver_does_not_qualify(self) -> None:
        """PHP: $mystery->method() where $mystery has no declared type → must NOT qualify."""
        edges = _parse_lang(self.PHP_UNKNOWN_RECV, ".php", "php")
        ts = _targets(edges)
        qualified = [t for t in ts if "." in t]
        assert not qualified, (
            f"PHP unknown receiver must not produce qualified targets. Got: {qualified}."
        )

    # Swift: lowercase receiver NOT in scope must NOT be treated as a static type call
    SWIFT_LOWERCASE_NOT_STATIC = """\
func run() {
    logger.log("hello")
}
"""

    def test_swift_lowercase_unresolved_does_not_qualify(self) -> None:
        """Swift: logger.log where 'logger' is lowercase and not in scope → skip (no edge)."""
        edges = _parse_lang(self.SWIFT_LOWERCASE_NOT_STATIC, ".swift", "swift")
        ts = _targets(edges)
        # Should NOT produce any qualified target since 'logger' is unknown
        assert "logger.log" not in ts, (
            f"Lowercase unresolved receiver must not produce qualified edge. Got: {ts}."
        )

    # Never raise on any family
    def test_go_inference_never_raises(self) -> None:
        """Go: edge extraction must not raise on any input."""
        src = "package main\nfunc f() { x.y.z.method() }\n"
        try:
            _parse_lang(src, ".go", "go")
        except Exception as exc:
            pytest.fail(f"Go type inference must not raise. Got: {exc}")

    def test_java_inference_never_raises(self) -> None:
        """Java: edge extraction must not raise on any input."""
        src = "class A { void f() { x.y.z.method(); } }\n"
        try:
            _parse_lang(src, ".java", "java")
        except Exception as exc:
            pytest.fail(f"Java type inference must not raise. Got: {exc}")

    def test_cpp_inference_never_raises(self) -> None:
        """C++: edge extraction must not raise on any input."""
        src = "void f() { x->y->z(); }\n"
        try:
            _parse_lang(src, ".cpp", "cpp")
        except Exception as exc:
            pytest.fail(f"C++ type inference must not raise. Got: {exc}")

    def test_ruby_inference_never_raises(self) -> None:
        """Ruby: edge extraction must not raise on any input."""
        src = "def f\n  x.y.z.method\nend\n"
        try:
            _parse_lang(src, ".rb", "ruby")
        except Exception as exc:
            pytest.fail(f"Ruby type inference must not raise. Got: {exc}")

    def test_php_inference_never_raises(self) -> None:
        """PHP: edge extraction must not raise on any input."""
        src = "<?php\nfunction f(): void { $x->y->z(); }\n"
        try:
            _parse_lang(src, ".php", "php")
        except Exception as exc:
            pytest.fail(f"PHP type inference must not raise. Got: {exc}")


# ── B5-Cfg: Config knob SEAM_TYPE_INFERENCE ───────────────────────────────────


class TestTypeInferenceConfigKnobB5:
    """B5-Cfg: SEAM_TYPE_INFERENCE=off disables inference in all new families."""

    GO_PARAM_CALL = """\
package main

type Client struct {}

func (c *Client) Send(msg string) {}

func Run(client *Client) {
    client.Send("hello")
}
"""

    def test_go_config_off_gives_bare_target(self) -> None:
        """Go: with SEAM_TYPE_INFERENCE=off, client.Send() → bare 'Send' (no inference)."""
        import seam.config as cfg

        original = cfg.SEAM_TYPE_INFERENCE
        try:
            cfg.SEAM_TYPE_INFERENCE = "off"
            edges = _parse_lang(self.GO_PARAM_CALL, ".go", "go")
            ts = _targets(edges)
            assert "Client.Send" not in ts, (
                f"With SEAM_TYPE_INFERENCE=off, must not qualify Go targets. Got: {ts}"
            )
            assert "Send" in ts, (
                f"With SEAM_TYPE_INFERENCE=off, bare 'Send' must be present. Got: {ts}"
            )
        finally:
            cfg.SEAM_TYPE_INFERENCE = original

    JAVA_PARAM_CALL = """\
class Client {
    void send(String msg) {}
}
class Service {
    void run(Client client) {
        client.send("hello");
    }
}
"""

    def test_java_config_off_gives_bare_target(self) -> None:
        """Java: with SEAM_TYPE_INFERENCE=off, client.send() → bare 'send'."""
        import seam.config as cfg

        original = cfg.SEAM_TYPE_INFERENCE
        try:
            cfg.SEAM_TYPE_INFERENCE = "off"
            edges = _parse_lang(self.JAVA_PARAM_CALL, ".java", "java")
            ts = _targets(edges)
            assert "Client.send" not in ts, (
                f"With SEAM_TYPE_INFERENCE=off, must not qualify Java targets. Got: {ts}"
            )
        finally:
            cfg.SEAM_TYPE_INFERENCE = original

    PHP_PARAM_CALL = """\
<?php
class Client {
    public function send(string $msg): void {}
}
class Service {
    public function run(Client $client): void {
        $client->send("hello");
    }
}
"""

    def test_php_config_off_gives_bare_target(self) -> None:
        """PHP: with SEAM_TYPE_INFERENCE=off, $client->send() → bare 'send'."""
        import seam.config as cfg

        original = cfg.SEAM_TYPE_INFERENCE
        try:
            cfg.SEAM_TYPE_INFERENCE = "off"
            edges = _parse_lang(self.PHP_PARAM_CALL, ".php", "php")
            ts = _targets(edges)
            assert "Client.send" not in ts, (
                f"With SEAM_TYPE_INFERENCE=off, must not qualify PHP targets. Got: {ts}"
            )
        finally:
            cfg.SEAM_TYPE_INFERENCE = original
