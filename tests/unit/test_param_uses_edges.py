"""Unit tests for method-param composition edges (kind='uses') across all 12 languages.

A `uses` edge links a function/method to a plain user type it references as a PARAMETER
(e.g. f(x: T) → f uses T). Conservatism matches `holds`: only plain user types bind;
builtins/optionals/generics/containers are refused; the feature is gated by
SEAM_PARAM_EDGES. Ruby is dynamically typed (no param type annotations) → naturally
produces no `uses` edges.

Coverage:
  - one representative `uses` edge per typed language (source qualification + target type)
  - builtins refused (int/string/number/etc.)
  - optionals/generics/containers refused
  - SEAM_PARAM_EDGES='off' → zero `uses` edges (byte-identical revert)
  - Ruby → no `uses` edges (untyped)
"""

import tempfile
from pathlib import Path

import pytest

import seam.config as config
from seam.indexer.graph import extract_edges
from seam.indexer.parser import (
    parse_c,
    parse_cpp,
    parse_csharp,
    parse_go,
    parse_java,
    parse_php,
    parse_python,
    parse_ruby,
    parse_rust,
    parse_swift,
    parse_typescript,
)


def _uses(src: str, parse, language: str, suffix: str) -> list[tuple[str, str]]:
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as f:
        f.write(src)
        fname = f.name
    root = parse(Path(fname))
    assert root is not None, f"{language} parse returned None"
    edges = extract_edges(root, language, Path(fname))
    return sorted((e["source"], e["target"]) for e in edges if e["kind"] == "uses")


# (language, parse_fn, suffix, source) — each fixture has Car.drive(Engine, <builtin>)
# plus a refused shape (optional/generic/container), so a correct extractor yields
# exactly [("...drive", "Engine")] (+ a top-level/free fn where the language has one).
_CASES = [
    (
        "python", parse_python, ".py",
        "class Engine: pass\n"
        "class Car:\n"
        "    def drive(self, e: Engine, speed: int): ...\n"
        "    def bad(self, xs: list[Engine]): ...\n",
        [("Car.drive", "Engine")],
    ),
    (
        "typescript", parse_typescript, ".ts",
        "class Engine {}\n"
        "class Car {\n"
        "  drive(e: Engine, speed: number) {}\n"
        "  bad(xs: Engine[]) {}\n"
        "}\n",
        [("Car.drive", "Engine")],
    ),
    (
        "go", parse_go, ".go",
        "package main\n"
        "type Engine struct {}\n"
        "type Car struct {}\n"
        "func (c *Car) Drive(e *Engine, speed int) {}\n",
        [("Car.Drive", "Engine")],
    ),
    (
        "rust", parse_rust, ".rs",
        "struct Engine {}\n"
        "struct Car {}\n"
        "impl Car { fn drive(&self, e: Engine, n: i32) {} }\n"
        "fn bad(o: Option<Engine>) {}\n",
        [("Car.drive", "Engine")],
    ),
    (
        "java", parse_java, ".java",
        "class Engine {}\n"
        "class Car { void drive(Engine e, int speed) {} }\n",
        [("Car.drive", "Engine")],
    ),
    (
        "csharp", parse_csharp, ".cs",
        "class Engine {}\n"
        "class Car { void Drive(Engine e, int speed) {} }\n",
        [("Car.Drive", "Engine")],
    ),
    (
        "cpp", parse_cpp, ".cpp",
        "class Engine {};\n"
        "class Car { public: void drive(Engine e, int speed) {} };\n",
        [("Car.drive", "Engine")],
    ),
    (
        "c", parse_c, ".c",
        "typedef struct { int x; } Engine;\n"
        "void drive(Engine e, int speed) {}\n",
        [("drive", "Engine")],
    ),
    (
        "php", parse_php, ".php",
        "<?php\nclass Engine {}\n"
        "class Car { public function drive(Engine $e, int $speed) {} }\n",
        [("Car.drive", "Engine")],
    ),
    (
        "swift", parse_swift, ".swift",
        "class Engine {}\n"
        "class Car {\n"
        "  func drive(e: Engine, speed: Int) {}\n"
        "  func bad(e: Engine?) {}\n"
        "}\n",
        [("Car.drive", "Engine")],
    ),
]


@pytest.mark.parametrize("language,parse,suffix,src,expected", _CASES)
def test_uses_edges_per_language(language, parse, suffix, src, expected) -> None:
    """Each typed language emits the expected `uses` edge; builtins/optionals/containers
    are refused so the result is exactly the plain-user-type param edge(s)."""
    assert _uses(src, parse, language, suffix) == expected


def test_ruby_untyped_emits_no_uses_edges() -> None:
    """Ruby params have no static type annotations → no `uses` edges (natural no-op)."""
    src = "class Car\n  def drive(engine, speed)\n  end\nend\n"
    assert _uses(src, parse_ruby, "ruby", ".rb") == []


def test_param_edges_off_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEAM_PARAM_EDGES='off' → zero `uses` edges (byte-identical revert)."""
    monkeypatch.setattr(config, "SEAM_PARAM_EDGES", "off")
    src = "class Engine: pass\nclass Car:\n    def drive(self, e: Engine): ...\n"
    assert _uses(src, parse_python, "python", ".py") == []


def test_free_function_uses_edge_python() -> None:
    """A top-level function's param type binds with a BARE source (no container)."""
    src = "class Engine: pass\ndef standalone(e: Engine): ...\n"
    assert ("standalone", "Engine") in _uses(src, parse_python, "python", ".py")
