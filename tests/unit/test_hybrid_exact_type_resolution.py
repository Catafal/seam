"""Hybrid exact type-resolution tests for Phase 11.

These fixtures pin the cases that matter for agent answerability: when code
stores a typed dependency and later calls through it, Seam should produce the
class-qualified call target instead of leaving agents to disambiguate a bare
method name.
"""

from pathlib import Path

from seam.indexer.graph import Edge, extract_edges
from seam.indexer.parser import parse_python, parse_typescript


def _write(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


def _extract_python(path: Path, source: str) -> list[Edge]:
    root = parse_python(_write(path, source))
    assert root is not None
    return extract_edges(root, "python", path)


def _extract_typescript(path: Path, source: str) -> list[Edge]:
    root = parse_typescript(_write(path, source))
    assert root is not None
    return extract_edges(root, "typescript", path)


def _call_targets(edges: list[Edge]) -> set[str]:
    return {edge["target"] for edge in edges if edge["kind"] == "call"}


def _call_edge(edges: list[Edge], target: str) -> Edge:
    edge = next(
        (edge for edge in edges if edge["kind"] == "call" and edge["target"] == target), None
    )
    assert edge is not None, f"Expected call target {target!r}; got {_call_targets(edges)}"
    return edge


def test_python_import_alias_and_init_field_resolve_exact_receiver(tmp_path: Path) -> None:
    """`from x import Client as C` plus `self.client: C` resolves to `Client.method`."""
    source = """\
from client import Client as C

class Client:
    def send(self):
        pass

class Other:
    def send(self):
        pass

class Service:
    def __init__(self):
        self.client: C = C()

    def run(self):
        self.client.send()
"""

    edges = _extract_python(tmp_path / "service.py", source)

    edge = _call_edge(edges, "Client.send")
    assert edge["source"] == "Service.run"
    assert edge["receiver"] == "self.client"
    assert edge["provenance"] == "python-receiver-type"
    assert "C.send" not in _call_targets(edges)
    assert "Other.send" not in _call_targets(edges)


def test_typescript_import_alias_and_parameter_property_resolve_exact_receiver(
    tmp_path: Path,
) -> None:
    """TS parameter properties should seed `this.field` receiver resolution."""
    source = """\
import { Client as C } from "./client";

class Client {
  send(): void {}
}

class Other {
  send(): void {}
}

class Service {
  constructor(private client: C) {}

  run(): void {
    this.client.send();
  }
}
"""

    edges = _extract_typescript(tmp_path / "service.ts", source)

    edge = _call_edge(edges, "Client.send")
    assert edge["source"] == "Service.run"
    assert edge["receiver"] == "this.client"
    assert edge["provenance"] == "typescript-receiver-type"
    assert "C.send" not in _call_targets(edges)
    assert "Other.send" not in _call_targets(edges)


def test_typescript_optional_parameter_does_not_promote_receiver(tmp_path: Path) -> None:
    """Optional params can be undefined, so receiver exactness must refuse them."""
    source = """\
class Client {
  send(): void {}
}

function maybe(skip?: Client): void {
  skip.send();
}
"""

    edges = _extract_typescript(tmp_path / "optional.ts", source)

    edge = _call_edge(edges, "send")
    assert edge["source"] == "maybe"
    assert edge["receiver"] == "skip"
    assert "Client.send" not in _call_targets(edges)
