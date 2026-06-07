"""Unit tests for A3 Slice 2: field_access.py core — TypeScript/JavaScript.

TypeScript/JS coverage (A3 Slice 2):
  TS-READ:    this.x (not in call position) → reads edge
  TS-WRITE-ASSIGN: this.x = v (assignment_expression) → writes edge
  TS-WRITE-AUG: this.x += v (augmented_assignment_expression) → writes edge
  TS-WRITE-DEL: delete this.x (unary_expression) → writes edge
  TS-NO-CALL: this.foo() → NO field edge (call_expression; stays a call edge)
  TS-QUAL: this.x → qualified target Type.x when class context known
  TS-UNRESOLVABLE: obj.x when receiver type unknown → bare 'x' target
  TS-FIELD-SYMBOLS: field declarations + constructor this.x= + parameter properties
  TS-PARAM-PROP: constructor(private x: Foo) → field symbol Type.x
  TS-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no TS field symbols/edges
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.graph import extract_edges, extract_symbols

# ── TypeScript parse helper ────────────────────────────────────────────────────


def _parse_typescript(source: str):
    """Parse TypeScript source and return (symbols, edges)."""
    from seam.indexer.parser import parse_typescript

    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_typescript(path)
        assert root is not None
        syms = extract_symbols(root, "typescript", path)
        edges = extract_edges(root, "typescript", path)
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


# ── TS-READ: this.x in non-call position → reads edge ──────────────────────


class TestTSReadEdge:
    """this.x NOT in call position should emit a 'reads' edge."""

    def test_this_attr_read_in_return_emits_reads(self) -> None:
        """return this.balance produces reads edge for Account.balance."""
        src = """\
class Account {
    balance: number;
    getBalance(): number {
        return this.balance;
    }
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_this_attr_read_source_is_method(self) -> None:
        """Source of reads edge is the enclosing TS method."""
        src = """\
class Account {
    balance: number;
    getBalance(): number {
        return this.balance;
    }
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.getBalance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.getBalance to Account.balance; got reads={reads}"

    def test_this_attr_read_in_expression(self) -> None:
        """this.value used in an arithmetic expression is a read."""
        src = """\
class Calc {
    value: number;
    doubled(): number {
        return this.value * 2;
    }
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Calc.value" for e in reads), (
            f"Expected reads edge to Calc.value; got reads={reads}"
        )


# ── TS-WRITE-ASSIGN: this.x = v (assignment_expression) → writes edge ──────


class TestTSWriteAssignEdge:
    """this.x = value (assignment_expression) should emit a 'writes' edge."""

    def test_this_attr_assign_in_method_emits_writes(self) -> None:
        """this.balance = v inside a method produces writes edge."""
        src = """\
class Account {
    balance: number;
    setBalance(v: number): void {
        this.balance = v;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_constructor_this_assign_emits_writes(self) -> None:
        """this.balance = 0 in constructor produces writes edge."""
        src = """\
class Account {
    balance: number;
    constructor() {
        this.balance = 0;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge in constructor for Account.balance; got writes={writes}"
        )


# ── TS-WRITE-AUG: this.x += v (augmented_assignment_expression) → writes ───


class TestTSWriteAugmentedEdge:
    """Augmented assignment (+=, -=, etc.) to this.x → writes edge."""

    def test_this_attr_plus_eq_is_write(self) -> None:
        """this.balance += amount produces writes edge."""
        src = """\
class Account {
    balance: number;
    deposit(amount: number): void {
        this.balance += amount;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for += on Account.balance; got writes={writes}"
        )

    def test_this_attr_minus_eq_is_write(self) -> None:
        """this.balance -= amount produces writes edge."""
        src = """\
class Account {
    balance: number;
    withdraw(amount: number): void {
        this.balance -= amount;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for -= on Account.balance; got writes={writes}"
        )


# ── TS-WRITE-DEL: delete obj.x (unary_expression) → writes edge ─────────────


class TestTSWriteDeleteEdge:
    """delete this.x → writes edge (deletion is a mutation)."""

    def test_delete_this_attr_is_write(self) -> None:
        """delete (this as any).balance emits writes edge."""
        src = """\
class Cache {
    cache: object;
    clear(): void {
        delete (this as any).cache;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Cache.cache" for e in writes), (
            f"Expected writes edge for delete; got writes={writes}"
        )


# ── TS-NO-CALL: this.foo() → NO field edge ──────────────────────────────────


class TestTSNoCallFieldEdge:
    """this.foo() (member_expression in call_expression) must NOT produce a field edge."""

    def test_method_call_produces_no_field_edge(self) -> None:
        """this.doWork() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker {
    run(): void {
        this.doWork();
    }
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "doWork" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"

    def test_method_call_does_produce_call_edge(self) -> None:
        """this.doWork() should produce a 'call' edge."""
        src = """\
class Worker {
    run(): void {
        this.doWork();
    }
}
"""
        _, edges = _parse_typescript(src)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert any(
            "doWork" in e["target"] for e in call_edges
        ), f"Expected call edge for this.doWork(); got call_edges={call_edges}"


# ── TS-QUAL: this.x → qualified target Type.x ───────────────────────────────


class TestTSQualifiedTarget:
    """this.x in a method of class Foo → target is Foo.x."""

    def test_this_attr_read_has_qualified_target(self) -> None:
        """this.x read → target is 'ClassName.x'."""
        src = """\
class Foo {
    x: number;
    bar(): number {
        return this.x;
    }
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"

    def test_this_attr_write_has_qualified_target(self) -> None:
        """this.x = v write → target is 'ClassName.x'."""
        src = """\
class Foo {
    x: number;
    bar(v: number): void {
        this.x = v;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Foo.x" in targets, f"Expected Foo.x in write targets; got {targets}"


# ── TS-UNRESOLVABLE: unknown receiver → bare target ──────────────────────────


class TestTSUnresolvableReceiver:
    """When the receiver type is unknown, target should be the bare field name."""

    def test_unknown_receiver_uses_bare_name(self) -> None:
        """obj.value when type of obj is unknown → bare 'value' target."""
        src = """\
function process(obj: any): void {
    const v = obj.value;
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        # Should use bare 'value' since type is 'any'
        targets = {e["target"] for e in reads}
        # Ensure we don't emit a confidently wrong qualified name
        assert not any("Unknown.value" in t for t in targets), (
            f"Should not emit wrong qualified target; got {targets}"
        )


# ── TS-FIELD-SYMBOLS: field declarations ─────────────────────────────────────


class TestTSFieldSymbols:
    """TypeScript field declarations → kind='field' symbols."""

    def test_public_field_definition_becomes_field_symbol(self) -> None:
        """public_field_definition balance: number → field symbol Account.balance."""
        src = """\
class Account {
    balance: number;
    name: string;
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_field_symbol_kind_is_field(self) -> None:
        """TS field symbol kind must be 'field'."""
        src = """\
class Foo {
    x: number;
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_field_symbol_qualified_name(self) -> None:
        """TS field symbol qualified_name is 'Type.field'."""
        src = """\
class Account {
    balance: number;
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance field symbol; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_constructor_this_assignment_becomes_field_symbol(self) -> None:
        """this.count = 0 in constructor → field symbol Counter.count."""
        src = """\
class Counter {
    constructor() {
        this.count = 0;
    }
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        assert any(s["name"] == "Counter.count" for s in fields), (
            f"Expected Counter.count field symbol; got {[s['name'] for s in fields]}"
        )

    def test_field_symbol_dedup(self) -> None:
        """class field declaration + constructor assignment → ONE symbol (dedup)."""
        src = """\
class Account {
    balance: number;
    constructor() {
        this.balance = 0;
    }
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert len(bal) == 1, f"Expected exactly 1 Account.balance field symbol; got {bal}"


# ── TS-PARAM-PROP: constructor parameter properties → field symbols ──────────


class TestTSParamPropertySymbols:
    """constructor(private x: Foo) → field symbol Type.x."""

    def test_private_param_property_becomes_field_symbol(self) -> None:
        """constructor(private repo: Repository) → field symbol MyClass.repo."""
        src = """\
class MyService {
    constructor(private repo: Repository) {
    }
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        assert any(s["name"] == "MyService.repo" for s in fields), (
            f"Expected MyService.repo field symbol from param prop; got {[s['name'] for s in fields]}"
        )

    def test_public_param_property_becomes_field_symbol(self) -> None:
        """constructor(public name: string) → field symbol Type.name (non-builtin type only)."""
        src = """\
class Widget {
    constructor(public label: Label) {
    }
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        # Label is a non-builtin type, so should become a field symbol
        assert any(s["name"] == "Widget.label" for s in fields), (
            f"Expected Widget.label field symbol; got {[s['name'] for s in fields]}"
        )

    def test_param_property_dedup_with_class_field(self) -> None:
        """If both field declaration and param property exist for same name → ONE symbol."""
        src = """\
class Foo {
    x: Bar;
    constructor(private x: Bar) {
    }
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        foo_x = [s for s in fields if s["name"] == "Foo.x"]
        assert len(foo_x) == 1, f"Expected exactly 1 Foo.x field symbol; got {foo_x}"


# ── TS-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ──────────────────────────────


class TestTSFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero TS field symbols and zero reads/writes edges."""

    def test_off_no_ts_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from TS."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    balance: number;
    get(): number {
        return this.balance;
    }
}
"""
        _, edges = _parse_typescript(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_off_no_ts_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from TS."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    balance: number;
    set(v: number): void {
        this.balance = v;
    }
}
"""
        _, edges = _parse_typescript(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_off_no_ts_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from TS."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    balance: number;
    constructor() {
        this.balance = 0;
    }
}
"""
        syms, _ = _parse_typescript(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"
