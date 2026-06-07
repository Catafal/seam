"""Unit tests for Slice #77: composition (holds) edge collectors in Python and TypeScript/JS.

TDD: Tests written BEFORE implementation (RED first).

Coverage:
  PY-ACCEPT:  Python field annotation of plain user type emits (type_name, line)
  PY-INIT:    Python __init__ parameter of plain user type emits (type_name, line)
  PY-DEDUP:   Injected-then-stored (init param + field of same type) emits ONE entry only
  PY-REFUSE:  Refusals: optional, union, container, generic, primitive, dotted, unknown
  TS-ACCEPT:  TS class field of plain type emits (type_name, line)
  TS-CTOR:    TS constructor param (plain type) emits (type_name, line)
  TS-PARAM-PROP: TS parameter property (constructor(private svc: Service)) emits entry
  TS-DEDUP:   Injected-then-stored (ctor param + field of same type) emits ONE entry
  TS-REFUSE:  Refusals: union, generic, array, predefined, undefined
  CONFIG-OFF: SEAM_COMPOSITION_EDGES='off' → zero holds edges emitted
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Parse helpers ─────────────────────────────────────────────────────────────


def _parse_python(source: str) -> list[Edge]:
    """Parse Python source and return all extracted edges."""
    from seam.indexer.parser import parse_python

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_python(path)
        assert root is not None
        return extract_edges(root, "python", path)
    finally:
        os.unlink(fname)


def _parse_ts(source: str) -> list[Edge]:
    """Parse TypeScript source and return all extracted edges."""
    from seam.indexer.parser import parse_typescript

    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_typescript(path)
        assert root is not None
        return extract_edges(root, "typescript", path)
    finally:
        os.unlink(fname)


def _holds_edges(edges: list[Edge]) -> list[Edge]:
    """Filter to only 'holds' edges."""
    return [e for e in edges if e["kind"] == "holds"]


def _holds_targets(edges: list[Edge]) -> set[str]:
    """Return set of target names in holds edges."""
    return {e["target"] for e in _holds_edges(edges)}


def _holds_sources(edges: list[Edge]) -> set[str]:
    """Return set of source names in holds edges."""
    return {e["source"] for e in _holds_edges(edges)}


# ── Python: field annotation acceptance ──────────────────────────────────────


class TestPythonFieldComposition:
    """PY-ACCEPT: plain user-type class field → holds edge."""

    def test_annotated_field_emits_holds_edge(self) -> None:
        """A class field `svc: Service` emits Edge(kind='holds', source='Owner', target='Service')."""
        src = "class Owner:\n    svc: Service\n"
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service; got holds={holds}"
        )

    def test_annotated_field_confidence_is_inferred(self) -> None:
        """Holds edge confidence must always be INFERRED (conservatism)."""
        src = "class Owner:\n    svc: Service\n"
        edges = _parse_python(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"

    def test_multiple_plain_fields_emit_multiple_holds(self) -> None:
        """Multiple plain-type fields each emit one holds edge."""
        src = (
            "class Owner:\n"
            "    repo: Repository\n"
            "    notifier: Notifier\n"
        )
        edges = _parse_python(src)
        targets = _holds_targets(edges)
        assert "Repository" in targets, f"Missing Repository; got {targets}"
        assert "Notifier" in targets, f"Missing Notifier; got {targets}"

    def test_source_is_class_name(self) -> None:
        """Source of holds edge must be the class name (not a method)."""
        src = "class MyClass:\n    dep: DepType\n"
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        assert all(e["source"] == "MyClass" for e in holds), (
            f"Expected source='MyClass'; got sources={[e['source'] for e in holds]}"
        )


# ── Python: __init__ parameter acceptance ─────────────────────────────────────


class TestPythonInitParamComposition:
    """PY-INIT: __init__ parameters with plain-type annotations → holds edge."""

    def test_init_param_emits_holds(self) -> None:
        """def __init__(self, svc: Service) → holds edge Owner->Service."""
        src = (
            "class Owner:\n"
            "    def __init__(self, svc: Service) -> None:\n"
            "        self.svc = svc\n"
        )
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service via __init__ param; got {holds}"
        )

    def test_multiple_init_params_emit_holds(self) -> None:
        """Multiple typed __init__ params each produce a holds edge."""
        src = (
            "class Owner:\n"
            "    def __init__(self, repo: Repository, notif: Notifier) -> None:\n"
            "        pass\n"
        )
        edges = _parse_python(src)
        targets = _holds_targets(edges)
        assert "Repository" in targets
        assert "Notifier" in targets

    def test_other_method_params_not_captured(self) -> None:
        """Only __init__ params are composition; other methods don't produce holds."""
        src = (
            "class Owner:\n"
            "    def process(self, svc: Service) -> None:\n"
            "        pass\n"
        )
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        # process() param should NOT produce a holds edge
        assert not any(e["target"] == "Service" for e in holds), (
            f"Non-__init__ param should not produce holds; got {holds}"
        )


# ── Python: deduplication ─────────────────────────────────────────────────────


class TestPythonDedup:
    """PY-DEDUP: init param + field of same type → only ONE holds edge."""

    def test_init_param_and_field_same_type_deduped(self) -> None:
        """class Owner: svc: Service; def __init__(self, svc: Service) → ONE holds edge."""
        src = (
            "class Owner:\n"
            "    svc: Service\n"
            "    def __init__(self, svc: Service) -> None:\n"
            "        self.svc = svc\n"
        )
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        service_holds = [e for e in holds if e["target"] == "Service" and e["source"] == "Owner"]
        assert len(service_holds) == 1, (
            f"Expected exactly 1 holds edge for Service; got {len(service_holds)}: {service_holds}"
        )


# ── Python: refusals (conservatism contract) ──────────────────────────────────


class TestPythonRefusals:
    """PY-REFUSE: Optional, Union, container, generic, primitive, dotted → no holds edge."""

    def test_optional_field_refused(self) -> None:
        """svc: Service | None → no holds edge (optional type, could be None)."""
        src = "class Owner:\n    svc: Service | None\n"
        edges = _parse_python(src)
        assert "Service" not in _holds_targets(edges), "Optional field must not produce holds"

    def test_optional_annotation_refused(self) -> None:
        """svc: Optional[Service] → no holds edge."""
        src = "from typing import Optional\nclass Owner:\n    svc: Optional[Service]\n"
        edges = _parse_python(src)
        assert "Service" not in _holds_targets(edges)

    def test_list_field_refused(self) -> None:
        """svcs: list[Service] → no holds edge (container type)."""
        src = "class Owner:\n    svcs: list[Service]\n"
        edges = _parse_python(src)
        assert "Service" not in _holds_targets(edges), "list[T] field must not produce holds"

    def test_dict_field_refused(self) -> None:
        """mapping: dict[str, Service] → no holds edge."""
        src = "class Owner:\n    mapping: dict[str, Service]\n"
        edges = _parse_python(src)
        assert "Service" not in _holds_targets(edges)

    def test_primitive_field_refused(self) -> None:
        """name: str → no holds edge (primitive type)."""
        src = "class Owner:\n    name: str\n"
        edges = _parse_python(src)
        assert "str" not in _holds_targets(edges)

    def test_int_field_refused(self) -> None:
        """count: int → no holds edge."""
        src = "class Owner:\n    count: int\n"
        edges = _parse_python(src)
        assert "int" not in _holds_targets(edges)

    def test_dotted_type_refused(self) -> None:
        """svc: module.Service → no holds edge (dotted/qualified type)."""
        src = "class Owner:\n    svc: module.Service\n"
        edges = _parse_python(src)
        # dotted annotations are attribute nodes — refused
        assert "Service" not in _holds_targets(edges)
        assert "module" not in _holds_targets(edges)

    def test_unannotated_field_no_holds(self) -> None:
        """Plain field without annotation produces no holds edge."""
        src = "class Owner:\n    svc = None\n"
        edges = _parse_python(src)
        # No type annotation → no holds
        assert len(_holds_edges(edges)) == 0

    def test_init_optional_param_refused(self) -> None:
        """def __init__(self, svc: Service | None) → no holds (optional param)."""
        src = (
            "class Owner:\n"
            "    def __init__(self, svc: Service | None) -> None: pass\n"
        )
        edges = _parse_python(src)
        assert "Service" not in _holds_targets(edges)

    def test_init_list_param_refused(self) -> None:
        """def __init__(self, svcs: list[Service]) → no holds (container param)."""
        src = (
            "class Owner:\n"
            "    def __init__(self, svcs: list[Service]) -> None: pass\n"
        )
        edges = _parse_python(src)
        assert "Service" not in _holds_targets(edges)

    def test_init_primitive_param_refused(self) -> None:
        """def __init__(self, name: str) → no holds (primitive param)."""
        src = (
            "class Owner:\n"
            "    def __init__(self, name: str) -> None: pass\n"
        )
        edges = _parse_python(src)
        assert "str" not in _holds_targets(edges)


# ── TypeScript: class field acceptance ────────────────────────────────────────


class TestTypeScriptFieldComposition:
    """TS-ACCEPT: plain user-type class field → holds edge."""

    def test_field_annotation_emits_holds(self) -> None:
        """class Owner { svc: Service } → Edge(kind='holds', source='Owner', target='Service')."""
        src = "class Owner {\n  svc: Service;\n}\n"
        edges = _parse_ts(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service; got {holds}"
        )

    def test_multiple_fields_emit_multiple_holds(self) -> None:
        """Multiple plain-type fields each produce a holds edge."""
        src = "class Owner {\n  repo: Repository;\n  notif: Notifier;\n}\n"
        edges = _parse_ts(src)
        targets = _holds_targets(edges)
        assert "Repository" in targets
        assert "Notifier" in targets

    def test_holds_confidence_is_inferred(self) -> None:
        """Holds edge confidence is always INFERRED."""
        src = "class Owner {\n  svc: Service;\n}\n"
        edges = _parse_ts(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED"


# ── TypeScript: constructor parameter acceptance ───────────────────────────────


class TestTypeScriptCtorParamComposition:
    """TS-CTOR: constructor(svc: Service) → holds edge."""

    def test_ctor_param_emits_holds(self) -> None:
        """constructor(private svc: Service) → holds edge."""
        src = (
            "class Owner {\n"
            "  constructor(private svc: Service) {}\n"
            "}\n"
        )
        edges = _parse_ts(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service via ctor; got {holds}"
        )

    def test_plain_ctor_param_emits_holds(self) -> None:
        """constructor(svc: Service) (no access modifier) → holds edge."""
        src = (
            "class Owner {\n"
            "  constructor(svc: Service) {}\n"
            "}\n"
        )
        edges = _parse_ts(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service; got {holds}"
        )

    def test_multiple_ctor_params_emit_holds(self) -> None:
        """Multiple typed ctor params produce multiple holds edges."""
        src = (
            "class Owner {\n"
            "  constructor(repo: Repository, notif: Notifier) {}\n"
            "}\n"
        )
        edges = _parse_ts(src)
        targets = _holds_targets(edges)
        assert "Repository" in targets
        assert "Notifier" in targets


# ── TypeScript: deduplication ─────────────────────────────────────────────────


class TestTypeScriptDedup:
    """TS-DEDUP: ctor param + field of same type → ONE holds edge."""

    def test_ctor_param_and_field_same_type_deduped(self) -> None:
        """class Owner { svc: Service; constructor(private svc: Service){} } → ONE holds."""
        src = (
            "class Owner {\n"
            "  svc: Service;\n"
            "  constructor(private svc: Service) {}\n"
            "}\n"
        )
        edges = _parse_ts(src)
        holds = _holds_edges(edges)
        service_holds = [e for e in holds if e["target"] == "Service" and e["source"] == "Owner"]
        assert len(service_holds) == 1, (
            f"Expected exactly 1 holds edge for Service; got {len(service_holds)}: {service_holds}"
        )


# ── TypeScript: refusals ───────────────────────────────────────────────────────


class TestTypeScriptRefusals:
    """TS-REFUSE: union, generic, array, predefined, null → no holds edge."""

    def test_union_type_refused(self) -> None:
        """svc: Service | null → no holds edge."""
        src = "class Owner {\n  svc: Service | null;\n}\n"
        edges = _parse_ts(src)
        assert "Service" not in _holds_targets(edges)

    def test_generic_field_refused(self) -> None:
        """svcs: Array<Service> → no holds edge."""
        src = "class Owner {\n  svcs: Array<Service>;\n}\n"
        edges = _parse_ts(src)
        assert "Service" not in _holds_targets(edges)

    def test_array_syntax_refused(self) -> None:
        """svcs: Service[] → no holds edge (array type)."""
        src = "class Owner {\n  svcs: Service[];\n}\n"
        edges = _parse_ts(src)
        assert "Service" not in _holds_targets(edges)

    def test_predefined_type_refused(self) -> None:
        """name: string → no holds edge (predefined/primitive)."""
        src = "class Owner {\n  name: string;\n}\n"
        edges = _parse_ts(src)
        assert "string" not in _holds_targets(edges)

    def test_number_field_refused(self) -> None:
        """count: number → no holds edge."""
        src = "class Owner {\n  count: number;\n}\n"
        edges = _parse_ts(src)
        assert "number" not in _holds_targets(edges)

    def test_ctor_union_param_refused(self) -> None:
        """constructor(svc: Service | null) → no holds edge."""
        src = "class Owner {\n  constructor(svc: Service | null) {}\n}\n"
        edges = _parse_ts(src)
        assert "Service" not in _holds_targets(edges)

    def test_ctor_generic_param_refused(self) -> None:
        """constructor(svcs: Array<Service>) → no holds edge."""
        src = "class Owner {\n  constructor(svcs: Array<Service>) {}\n}\n"
        edges = _parse_ts(src)
        assert "Service" not in _holds_targets(edges)

    def test_ctor_predefined_param_refused(self) -> None:
        """constructor(name: string) → no holds edge."""
        src = "class Owner {\n  constructor(name: string) {}\n}\n"
        edges = _parse_ts(src)
        assert "string" not in _holds_targets(edges)


# ── Config knob: SEAM_COMPOSITION_EDGES=off ───────────────────────────────────


class TestCompositionEdgesConfigOff:
    """CONFIG-OFF: SEAM_COMPOSITION_EDGES='off' suppresses ALL holds edges."""

    def test_python_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, holds edges are not emitted for Python."""
        import seam.config as cfg
        # Use setattr so monkeypatch automatically restores the original value on teardown.
        # WHY not importlib.reload: reload + monkeypatch interact badly because the
        # 'finally' block in the test runs BEFORE monkeypatch restores the env var,
        # so a final reload would leave the config in the 'off' state.
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = "class Owner:\n    svc: Service\n"
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"

    def test_ts_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, holds edges are not emitted for TypeScript."""
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = "class Owner {\n  svc: Service;\n}\n"
        edges = _parse_ts(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"

    def test_other_edges_unaffected_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, call/import edges still exist."""
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = "import os\nclass Owner:\n    def go(self) -> None:\n        foo()\n"
        edges = _parse_python(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0
        # import edge should still exist
        non_holds = [e for e in edges if e["kind"] != "holds"]
        assert len(non_holds) > 0, "Other edges should be unaffected"
