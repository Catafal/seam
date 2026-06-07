"""Unit tests for A3 Slice 5: field_access.py core — Swift.

Swift coverage:
  SWIFT-READ:    self.prop in non-call position → reads edge
  SWIFT-WRITE-ASSIGN: self.prop = v → writes edge
  SWIFT-WRITE-AUG: self.prop += v → writes edge
  SWIFT-NO-CALL: self.method() → NO field edge (navigation_expression in call_expression)
  SWIFT-QUAL: self.prop → qualified ClassName.prop
  SWIFT-FIELD-SYMBOLS: stored var/let in class/struct/actor bodies → kind='field' symbols
  SWIFT-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Swift field symbols/edges

Swift stored-property access uses navigation_expression: self.prop or obj.prop.
  - Call position: call_expression whose first child is a navigation_expression → NOT field.
  - Assignment: directly_assignable_expression wraps the navigation_expression → write.
  - Augmented assignment (+=, -=) LHS with navigation_expression → write.
  - Field symbols: stored var/let property_declaration in class/struct/actor bodies.
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.graph import extract_edges, extract_symbols

# ── Parse helper ──────────────────────────────────────────────────────────────


def _parse_swift(source: str):
    """Parse Swift source and return (symbols, edges)."""
    from seam.indexer.parser import parse_swift

    with tempfile.NamedTemporaryFile(suffix=".swift", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_swift(path)
        assert root is not None
        syms = extract_symbols(root, "swift", path)
        edges = extract_edges(root, "swift", path)
        return syms, edges
    finally:
        os.unlink(fname)


def _reads_edges(edges):
    """Filter to only 'reads' edges."""
    return [e for e in edges if e["kind"] == "reads"]


def _writes_edges(edges):
    """Filter to only 'writes' edges."""
    return [e for e in edges if e["kind"] == "writes"]


def _field_symbols(symbols):
    """Filter to only 'field' symbols."""
    return [s for s in symbols if s["kind"] == "field"]


# ── SWIFT-READ: self.prop in non-call position → reads edge ──────────────────


class TestSwiftReadEdge:
    """self.balance NOT in call position should emit a 'reads' edge."""

    def test_swift_self_prop_read_emits_reads_edge(self) -> None:
        """self.balance in a method body produces reads edge for Account.balance."""
        src = """\
class Account {
    var balance: Int = 0
    func getBalance() -> Int {
        return self.balance
    }
}
"""
        _, edges = _parse_swift(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_swift_self_prop_read_source_is_method(self) -> None:
        """Source of reads edge is the enclosing Swift method."""
        src = """\
class Account {
    var balance: Int = 0
    func getBalance() -> Int {
        return self.balance
    }
}
"""
        _, edges = _parse_swift(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.getBalance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.getBalance to Account.balance; got reads={reads}"


# ── SWIFT-WRITE-ASSIGN: self.prop = v → writes edge ─────────────────────────


class TestSwiftWriteAssignEdge:
    """self.balance = v (assignment) should emit a 'writes' edge."""

    def test_swift_self_prop_assign_emits_writes(self) -> None:
        """self.balance = v in a method produces writes edge."""
        src = """\
class Account {
    var balance: Int = 0
    func setBalance(_ v: Int) {
        self.balance = v
    }
}
"""
        _, edges = _parse_swift(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_swift_write_source_is_method(self) -> None:
        """Source of writes edge is the enclosing Swift method."""
        src = """\
class Account {
    var balance: Int = 0
    func setBalance(_ v: Int) {
        self.balance = v
    }
}
"""
        _, edges = _parse_swift(src)
        writes = _writes_edges(edges)
        assert any(
            e["source"] == "Account.setBalance" and e["target"] == "Account.balance"
            for e in writes
        ), f"Expected writes from Account.setBalance to Account.balance; got {writes}"


# ── SWIFT-WRITE-AUG: self.prop += v → writes edge ────────────────────────────


class TestSwiftWriteAugmentedEdge:
    """Augmented assignment (self.balance += amount) should emit a 'writes' edge."""

    def test_swift_self_prop_plus_eq_is_write(self) -> None:
        """self.balance += amount produces writes edge."""
        src = """\
class Account {
    var balance: Int = 0
    func deposit(amount: Int) {
        self.balance += amount
    }
}
"""
        _, edges = _parse_swift(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for +=; got writes={writes}"
        )

    def test_swift_self_prop_minus_eq_is_write(self) -> None:
        """self.balance -= amount produces writes edge."""
        src = """\
class Account {
    var balance: Int = 0
    func withdraw(amount: Int) {
        self.balance -= amount
    }
}
"""
        _, edges = _parse_swift(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for -=; got writes={writes}"
        )


# ── SWIFT-NO-CALL: self.method() → NO field edge ─────────────────────────────


class TestSwiftNoCallFieldEdge:
    """self.doWork() (navigation_expression in call_expression) must NOT produce a field edge."""

    def test_swift_method_call_produces_no_field_edge(self) -> None:
        """self.doWork() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker {
    func run() {
        self.doWork()
    }
}
"""
        _, edges = _parse_swift(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "doWork" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"


# ── SWIFT-QUAL: self.prop → qualified ClassName.prop ─────────────────────────


class TestSwiftQualifiedTarget:
    """self.prop in a method of class Foo → target is 'Foo.prop'."""

    def test_swift_self_prop_read_qualified_target(self) -> None:
        """self.x read in class Foo → target is 'Foo.x'."""
        src = """\
class Foo {
    var x: Int = 0
    func bar() -> Int {
        return self.x
    }
}
"""
        _, edges = _parse_swift(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"

    def test_swift_self_prop_write_qualified_target(self) -> None:
        """self.x = v write in class Foo → target is 'Foo.x'."""
        src = """\
class Foo {
    var x: Int = 0
    func bar(_ v: Int) {
        self.x = v
    }
}
"""
        _, edges = _parse_swift(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Foo.x" in targets, f"Expected Foo.x in write targets; got {targets}"


# ── SWIFT-FIELD-SYMBOLS: stored var/let in class body → kind='field' symbols ──


class TestSwiftFieldSymbols:
    """Swift stored var/let property_declaration → kind='field' symbols."""

    def test_swift_stored_var_becomes_field_symbol(self) -> None:
        """var balance: Int → field symbol Account.balance."""
        src = """\
class Account {
    var balance: Int = 0
    var name: String = ""
}
"""
        syms, _ = _parse_swift(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_swift_struct_stored_var_becomes_field_symbol(self) -> None:
        """struct Point { var x: Int; var y: Int } → field symbols Point.x, Point.y."""
        src = """\
struct Point {
    var x: Int = 0
    var y: Int = 0
}
"""
        syms, _ = _parse_swift(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Point.x" in names, f"Expected Point.x; got {names}"
        assert "Point.y" in names, f"Expected Point.y; got {names}"

    def test_swift_field_symbol_kind_is_field(self) -> None:
        """Swift field symbol kind must be 'field'."""
        src = """\
class Foo {
    var x: Int = 0
}
"""
        syms, _ = _parse_swift(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_swift_field_symbol_qualified_name(self) -> None:
        """Swift field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account {
    var balance: Int = 0
}
"""
        syms, _ = _parse_swift(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance field symbol; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_swift_all_field_types_indexed(self) -> None:
        """ALL Swift stored properties are indexed regardless of type."""
        src = """\
class Foo {
    var count: Int = 0
    var label: String = ""
}
"""
        syms, _ = _parse_swift(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Foo.count" in names, f"Expected Foo.count even with Int type; got {names}"
        assert "Foo.label" in names, f"Expected Foo.label even with String type; got {names}"


# ── SWIFT-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ────────────────────────────


class TestSwiftFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero Swift field symbols and zero reads/writes edges."""

    def test_swift_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from Swift."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    var balance: Int = 0
    func get() -> Int {
        return self.balance
    }
}
"""
        _, edges = _parse_swift(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_swift_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from Swift."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    var balance: Int = 0
    func set(_ v: Int) {
        self.balance = v
    }
}
"""
        _, edges = _parse_swift(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_swift_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from Swift."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    var balance: Int = 0
}
"""
        syms, _ = _parse_swift(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"
