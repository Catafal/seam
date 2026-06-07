"""Unit tests for Slice #80: composition (holds) edge collector for Swift.

TDD: Tests written BEFORE implementation (RED first).

Coverage:
  SWIFT-ACCEPT:   Plain user-type stored property emits (type_name, line)
  SWIFT-WRAPPER:  @ObservedObject/@StateObject/@EnvironmentObject-wrapped property
                  emits the DECLARED type (not the wrapper), e.g. var vm: ViewModel
  SWIFT-INIT:     init(...) parameter of a plain user type emits (type_name, line)
  SWIFT-STRUCT:   struct composition also emits holds edges
  SWIFT-ACTOR:    actor composition also emits holds edges
  SWIFT-DEDUP:    Property + init param of the same type → ONE entry
  SWIFT-REFUSE:   Optional (Foo?), array ([Foo]), dict ([K:V]), generic (Foo<T>),
                  primitive (String, Int, Bool, etc.) → no holds edge
  CONFIG-OFF:     SEAM_COMPOSITION_EDGES='off' → zero holds edges emitted
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Parse helper ──────────────────────────────────────────────────────────────


def _parse_swift(source: str) -> list[Edge]:
    """Parse Swift source string and return all extracted edges."""
    from seam.indexer.parser import parse_swift

    with tempfile.NamedTemporaryFile(suffix=".swift", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_swift(path)
        assert root is not None, "Swift parse returned None"
        return extract_edges(root, "swift", path)
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


# ── SWIFT-ACCEPT: plain stored property acceptance ────────────────────────────


class TestSwiftStoredPropertyComposition:
    """SWIFT-ACCEPT: plain user-type stored property → holds edge."""

    def test_var_typed_property_emits_holds(self) -> None:
        """class Owner { var svc: Service } → Edge(kind='holds', source='Owner', target='Service')."""
        src = "class Owner {\n    var svc: Service\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service; got holds={holds}"
        )

    def test_let_typed_property_emits_holds(self) -> None:
        """class Owner { let repo: Repository } → holds edge."""
        src = "class Owner {\n    let repo: Repository\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner->Repository; got {holds}"
        )

    def test_holds_confidence_is_inferred(self) -> None:
        """Holds edges always have confidence=INFERRED (conservatism contract)."""
        src = "class Owner {\n    var svc: Service\n}\n"
        edges = _parse_swift(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"

    def test_source_is_class_name(self) -> None:
        """Source of holds edge is the class name, not a method."""
        src = "class MyClass {\n    var dep: DepType\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert all(e["source"] == "MyClass" for e in holds), (
            f"Expected source='MyClass'; got {[e['source'] for e in holds]}"
        )

    def test_multiple_properties_emit_multiple_holds(self) -> None:
        """Multiple plain-type properties each emit a holds edge."""
        src = (
            "class Owner {\n"
            "    var repo: Repository\n"
            "    var notifier: Notifier\n"
            "}\n"
        )
        edges = _parse_swift(src)
        targets = _holds_targets(edges)
        assert "Repository" in targets, f"Missing Repository; got {targets}"
        assert "Notifier" in targets, f"Missing Notifier; got {targets}"


# ── SWIFT-WRAPPER: property wrapper acceptance ────────────────────────────────


class TestSwiftPropertyWrapperComposition:
    """SWIFT-WRAPPER: @ObservedObject/@StateObject/@EnvironmentObject-wrapped properties
    still emit holds for the DECLARED type (the wrapper itself is not the composition)."""

    def test_observed_object_wrapper_emits_holds(self) -> None:
        """@ObservedObject var viewModel: ViewModel → holds edge to ViewModel (not ObservedObject)."""
        src = "class View {\n    @ObservedObject var viewModel: ViewModel\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "View" and e["target"] == "ViewModel" for e in holds), (
            f"Expected holds edge View->ViewModel (not the wrapper); got {holds}"
        )
        # The wrapper name itself must NOT be a target
        assert "ObservedObject" not in _holds_targets(edges), (
            "The wrapper name @ObservedObject must not be the holds target"
        )

    def test_state_object_wrapper_emits_holds(self) -> None:
        """@StateObject var store: Store → holds edge to Store."""
        src = "class View {\n    @StateObject var store: Store\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "View" and e["target"] == "Store" for e in holds), (
            f"Expected holds edge View->Store; got {holds}"
        )

    def test_environment_object_wrapper_emits_holds(self) -> None:
        """@EnvironmentObject var settings: Settings → holds edge to Settings."""
        src = "class View {\n    @EnvironmentObject var settings: Settings\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "View" and e["target"] == "Settings" for e in holds), (
            f"Expected holds edge View->Settings; got {holds}"
        )

    def test_all_three_wrappers_in_one_class(self) -> None:
        """Three differently-wrapped properties all produce holds edges to their declared types."""
        src = (
            "class ContentView {\n"
            "    @ObservedObject var viewModel: ViewModel\n"
            "    @StateObject var store: Store\n"
            "    @EnvironmentObject var settings: Settings\n"
            "}\n"
        )
        edges = _parse_swift(src)
        targets = _holds_targets(edges)
        assert "ViewModel" in targets, f"Missing ViewModel; got {targets}"
        assert "Store" in targets, f"Missing Store; got {targets}"
        assert "Settings" in targets, f"Missing Settings; got {targets}"


# ── SWIFT-INIT: init parameter acceptance ────────────────────────────────────


class TestSwiftInitParamComposition:
    """SWIFT-INIT: init(...) parameters with plain-type annotations → holds edge."""

    def test_init_param_emits_holds(self) -> None:
        """init(svc: Service) → holds edge Owner->Service."""
        src = (
            "class Owner {\n"
            "    init(svc: Service) {\n"
            "        self.svc = svc\n"
            "    }\n"
            "}\n"
        )
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service via init param; got {holds}"
        )

    def test_multiple_init_params_emit_holds(self) -> None:
        """Multiple typed init params each produce a holds edge."""
        src = (
            "class Owner {\n"
            "    init(repo: Repository, notif: Notifier) {\n"
            "    }\n"
            "}\n"
        )
        edges = _parse_swift(src)
        targets = _holds_targets(edges)
        assert "Repository" in targets, f"Missing Repository; got {targets}"
        assert "Notifier" in targets, f"Missing Notifier; got {targets}"

    def test_init_param_with_external_label(self) -> None:
        """init(_ svc: Service) or init(with svc: Service) → captures internal param name."""
        src = (
            "class Owner {\n"
            "    init(with svc: Service) {\n"
            "    }\n"
            "}\n"
        )
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["target"] == "Service" for e in holds), (
            f"Expected holds edge for Service param with external label; got {holds}"
        )

    def test_other_func_params_not_captured(self) -> None:
        """Only init params are composition; regular function params don't produce holds."""
        src = (
            "class Owner {\n"
            "    func process(svc: Service) {\n"
            "    }\n"
            "}\n"
        )
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert not any(e["target"] == "Service" for e in holds), (
            f"Non-init function param should not produce holds; got {holds}"
        )


# ── SWIFT-STRUCT: struct composition ─────────────────────────────────────────


class TestSwiftStructComposition:
    """SWIFT-STRUCT: struct declarations also emit holds edges."""

    def test_struct_property_emits_holds(self) -> None:
        """struct Owner { var svc: Service } → holds edge."""
        src = "struct Owner {\n    var svc: Service\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service from struct; got {holds}"
        )

    def test_struct_init_param_emits_holds(self) -> None:
        """struct with init(svc: Service) → holds edge."""
        src = (
            "struct Owner {\n"
            "    init(svc: Service) {}\n"
            "}\n"
        )
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service from struct init param; got {holds}"
        )


# ── SWIFT-ACTOR: actor composition ────────────────────────────────────────────


class TestSwiftActorComposition:
    """SWIFT-ACTOR: actor declarations also emit holds edges."""

    def test_actor_property_emits_holds(self) -> None:
        """actor Owner { var svc: Service } → holds edge."""
        src = "actor Owner {\n    var svc: Service\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner->Service from actor; got {holds}"
        )


# ── SWIFT-DEDUP: deduplication ────────────────────────────────────────────────


class TestSwiftDedup:
    """SWIFT-DEDUP: property + init param of the same type → ONE holds edge."""

    def test_property_and_init_param_same_type_deduped(self) -> None:
        """class Owner { var svc: Service; init(svc: Service) } → ONE holds edge."""
        src = (
            "class Owner {\n"
            "    var svc: Service\n"
            "    init(svc: Service) {\n"
            "        self.svc = svc\n"
            "    }\n"
            "}\n"
        )
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        service_holds = [
            e for e in holds if e["target"] == "Service" and e["source"] == "Owner"
        ]
        assert len(service_holds) == 1, (
            f"Expected exactly 1 holds edge for Service; got {len(service_holds)}: {service_holds}"
        )


# ── SWIFT-REFUSE: conservatism refusals ──────────────────────────────────────


class TestSwiftRefusals:
    """SWIFT-REFUSE: optional, array, dict, generic, primitive → no holds edge."""

    def test_optional_type_refused(self) -> None:
        """var svc: Service? → no holds edge (optional)."""
        src = "class Owner {\n    var svc: Service?\n}\n"
        edges = _parse_swift(src)
        assert "Service" not in _holds_targets(edges), "Optional type must not produce holds"

    def test_array_type_refused(self) -> None:
        """var items: [Service] → no holds edge (array)."""
        src = "class Owner {\n    var items: [Service]\n}\n"
        edges = _parse_swift(src)
        assert "Service" not in _holds_targets(edges), "Array type must not produce holds"

    def test_dictionary_type_refused(self) -> None:
        """var map: [String: Service] → no holds edge (dictionary)."""
        src = "class Owner {\n    var map: [String: Service]\n}\n"
        edges = _parse_swift(src)
        assert "Service" not in _holds_targets(edges), "Dictionary type must not produce holds"

    def test_generic_type_refused(self) -> None:
        """var items: Array<Service> → no holds edge (generic)."""
        src = "class Owner {\n    var items: Array<Service>\n}\n"
        edges = _parse_swift(src)
        assert "Service" not in _holds_targets(edges), "Generic type must not produce holds"

    def test_string_primitive_refused(self) -> None:
        """var name: String → no holds edge (builtin)."""
        src = "class Owner {\n    var name: String\n}\n"
        edges = _parse_swift(src)
        assert "String" not in _holds_targets(edges), "String primitive must not produce holds"

    def test_int_primitive_refused(self) -> None:
        """var count: Int → no holds edge (builtin)."""
        src = "class Owner {\n    var count: Int\n}\n"
        edges = _parse_swift(src)
        assert "Int" not in _holds_targets(edges), "Int primitive must not produce holds"

    def test_bool_primitive_refused(self) -> None:
        """var flag: Bool → no holds edge (builtin)."""
        src = "class Owner {\n    var flag: Bool\n}\n"
        edges = _parse_swift(src)
        assert "Bool" not in _holds_targets(edges), "Bool primitive must not produce holds"

    def test_init_optional_param_refused(self) -> None:
        """init(svc: Service?) → no holds edge (optional param)."""
        src = "class Owner {\n    init(svc: Service?) {}\n}\n"
        edges = _parse_swift(src)
        assert "Service" not in _holds_targets(edges), "Optional init param must not produce holds"

    def test_init_array_param_refused(self) -> None:
        """init(items: [Service]) → no holds edge (array param)."""
        src = "class Owner {\n    init(items: [Service]) {}\n}\n"
        edges = _parse_swift(src)
        assert "Service" not in _holds_targets(edges), "Array init param must not produce holds"

    def test_init_string_param_refused(self) -> None:
        """init(name: String) → no holds edge (primitive param)."""
        src = "class Owner {\n    init(name: String) {}\n}\n"
        edges = _parse_swift(src)
        assert "String" not in _holds_targets(edges), "String init param must not produce holds"


# ── CONFIG-OFF: SEAM_COMPOSITION_EDGES=off ────────────────────────────────────


class TestCompositionEdgesConfigOffSwift:
    """CONFIG-OFF: SEAM_COMPOSITION_EDGES='off' suppresses ALL holds edges for Swift."""

    def test_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, holds edges are not emitted for Swift."""
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = "class Owner {\n    var svc: Service\n}\n"
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges with config off; got {holds}"

    def test_call_edges_unaffected_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With config off, other edge types (import, call) still exist."""
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = (
            "import Foundation\n"
            "class Owner {\n"
            "    var svc: Service\n"
            "}\n"
        )
        edges = _parse_swift(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0
        # import edge must still be present
        non_holds = [e for e in edges if e["kind"] != "holds"]
        assert len(non_holds) > 0, "Import/call edges must remain unaffected"
