"""Unit tests for seam/analysis/relevance.py (E2/E3 ranking + self-ref classification).

Pure-function tests, no DB. Mirrors the unit-test style of test_names.py / test_builtins.py.
Tests assert externally-observable ordering/classification, not the internal key shape
beyond what the contract guarantees.

Coverage:
  owning_container       — qualified / bare / nested / empty / leading-dot / trailing-dot
  classify_self_ref      — None container, container name, bare member, qualified member, external
  relevance_key          — four (is_self, is_test) quadrants
  order_by_relevance     — externals first, self-refs last, stable within groups
  partition_self_refs    — correct split, input order preserved, no mutation
"""

from seam.analysis.relevance import (
    classify_self_ref,
    order_by_relevance,
    owning_container,
    partition_self_refs,
    relevance_key,
)

# ── owning_container ──────────────────────────────────────────────────────────


def test_owning_container_qualified() -> None:
    assert owning_container("Foo.bar") == "Foo"


def test_owning_container_nested_takes_everything_before_last_dot() -> None:
    # Member is "bar"; its container is the full "pkg.Foo" prefix (matches get_member_names).
    assert owning_container("pkg.Foo.bar") == "pkg.Foo"


def test_owning_container_bare_is_none() -> None:
    assert owning_container("bar") is None


def test_owning_container_empty_is_none() -> None:
    assert owning_container("") is None


def test_owning_container_leading_dot_is_none() -> None:
    # ".bar" has an empty prefix → not a real container.
    assert owning_container(".bar") is None


def test_owning_container_trailing_dot() -> None:
    assert owning_container("Foo.") == "Foo"


# ── classify_self_ref ─────────────────────────────────────────────────────────


def test_classify_none_container_always_external() -> None:
    # A target with no container (free function) can have no self-references.
    assert classify_self_ref("anything", None, set()) is False
    assert classify_self_ref("Foo.bar", None, {"Foo", "bar"}) is False


def test_classify_container_name_itself_is_self_ref() -> None:
    assert classify_self_ref("Foo", "Foo", {"Foo", "bar"}) is True


def test_classify_bare_member_is_self_ref() -> None:
    # owning_container("bar") is None, so the self_names set is what catches it.
    assert classify_self_ref("bar", "Foo", {"Foo", "bar"}) is True


def test_classify_qualified_member_is_self_ref_via_owning_container() -> None:
    # "Foo.baz" is NOT in self_names, but its owning container matches → self-ref.
    assert classify_self_ref("Foo.baz", "Foo", {"Foo", "bar"}) is True


def test_classify_external_qualified_is_not_self_ref() -> None:
    assert classify_self_ref("Other.bar", "Foo", {"Foo", "bar"}) is False


def test_classify_external_bare_not_in_self_names() -> None:
    assert classify_self_ref("unrelated", "Foo", {"Foo", "bar"}) is False


# ── relevance_key ─────────────────────────────────────────────────────────────


def test_relevance_key_quadrants() -> None:
    container = "Foo"
    self_names = {"Foo", "bar"}
    ext_prod = {"name": "Other.x", "is_test": False}
    ext_test = {"name": "Other.y", "is_test": True}
    self_prod = {"name": "Foo.bar", "is_test": False}
    self_test = {"name": "Foo.baz", "is_test": True}

    assert relevance_key(ext_prod, container, self_names) == (False, False)
    assert relevance_key(ext_test, container, self_names) == (False, True)
    assert relevance_key(self_prod, container, self_names) == (True, False)
    assert relevance_key(self_test, container, self_names) == (True, True)


def test_relevance_key_missing_fields_default_external_production() -> None:
    # Missing name / is_test must not raise and must default to the safe (external) side.
    assert relevance_key({}, "Foo", {"Foo"}) == (False, False)


# ── order_by_relevance ────────────────────────────────────────────────────────


def test_order_externals_before_self_refs() -> None:
    container = "Foo"
    self_names = {"Foo", "a", "b"}
    entries = [
        {"name": "Foo.a", "is_test": False},   # self-ref
        {"name": "Ext1.h", "is_test": False},  # external
        {"name": "Foo.b", "is_test": False},   # self-ref
        {"name": "Ext2.h", "is_test": False},  # external
    ]
    ordered = order_by_relevance(entries, container, self_names)
    names = [e["name"] for e in ordered]
    # Externals first (in original relative order), then self-refs (in original order).
    assert names == ["Ext1.h", "Ext2.h", "Foo.a", "Foo.b"]


def test_order_is_stable_within_groups() -> None:
    # Within the external group, input order is preserved (stable sort).
    container = "Foo"
    self_names = {"Foo"}
    entries = [
        {"name": "Zext.h", "is_test": False},
        {"name": "Aext.h", "is_test": False},
    ]
    ordered = order_by_relevance(entries, container, self_names)
    assert [e["name"] for e in ordered] == ["Zext.h", "Aext.h"]


def test_order_production_before_test_within_external_group() -> None:
    container = "Foo"
    self_names = {"Foo"}
    entries = [
        {"name": "Ext.test", "is_test": True},
        {"name": "Ext.prod", "is_test": False},
    ]
    ordered = order_by_relevance(entries, container, self_names)
    assert [e["name"] for e in ordered] == ["Ext.prod", "Ext.test"]


def test_order_does_not_mutate_input() -> None:
    entries = [{"name": "Foo.a", "is_test": False}, {"name": "Ext.h", "is_test": False}]
    before = list(entries)
    order_by_relevance(entries, "Foo", {"Foo", "a"})
    assert entries == before


# ── partition_self_refs ───────────────────────────────────────────────────────


def test_partition_splits_external_and_self_refs() -> None:
    container = "Foo"
    self_names = {"Foo", "a"}
    entries = [
        {"name": "Ext1.h"},
        {"name": "Foo.a"},
        {"name": "Ext2.h"},
        {"name": "Foo.b"},
    ]
    external, self_refs = partition_self_refs(entries, container, self_names)
    assert [e["name"] for e in external] == ["Ext1.h", "Ext2.h"]
    assert [e["name"] for e in self_refs] == ["Foo.a", "Foo.b"]


def test_partition_none_container_all_external() -> None:
    entries = [{"name": "Foo.a"}, {"name": "Bar.b"}]
    external, self_refs = partition_self_refs(entries, None, set())
    assert len(external) == 2
    assert self_refs == []
