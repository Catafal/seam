"""Unit tests for A3 Slice 1+2: field_access.py core (Python + TypeScript/JS).

TDD: Tests written BEFORE implementation (RED first).

Python coverage:
  PY-READ:    self.x (not in call position) → reads edge
  PY-WRITE-ASSIGN: self.x = v → writes edge
  PY-WRITE-AUG: self.x += v → writes edge
  PY-WRITE-DEL: del self.x → writes edge
  PY-NO-CALL: self.foo() → NO field edge (stays a call edge)
  PY-QUAL: self.x → qualified target Type.x when class context known
  PY-UNRESOLVABLE: obj.x when receiver type unknown → bare 'x' target
  PY-MULTI: multiple attribute accesses in function body → all extracted
  PY-NESTED: attribute access in nested if/for/while → extracted
  PY-LINE: line numbers are correct (1-based)

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

# ── Parse helpers ──────────────────────────────────────────────────────────────


def _parse_python(source: str):
    """Parse Python source and return (symbols, edges)."""
    from seam.indexer.parser import parse_python

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_python(path)
        assert root is not None
        syms = extract_symbols(root, "python", path)
        edges = extract_edges(root, "python", path)
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


# ── PY-READ: self.x in non-call position → reads edge ────────────────────────


class TestReadEdge:
    """self.x NOT in call position should emit a 'reads' edge."""

    def test_self_attr_read_emits_reads_edge(self) -> None:
        """self.x = self.balance produces reads edge for balance."""
        src = """\
class Account:
    def get(self):
        return self.balance
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_reads_edge_source_is_method(self) -> None:
        """Source of reads edge is the enclosing method name."""
        src = """\
class Account:
    def get(self):
        return self.balance
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.get" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.get to Account.balance; got reads={reads}"

    def test_reads_edge_confidence(self) -> None:
        """Reads edge for self-qualified attr is EXTRACTED or INFERRED (not AMBIGUOUS)."""
        src = """\
class Account:
    def get(self):
        return self.balance
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        for e in reads:
            if e["target"] == "Account.balance":
                assert e["confidence"] in ("EXTRACTED", "INFERRED"), (
                    f"Expected EXTRACTED/INFERRED; got {e['confidence']}"
                )

    def test_reads_edge_used_in_expression(self) -> None:
        """self.x used in an expression (not assignment LHS) is a read."""
        src = """\
class Calc:
    def result(self):
        return self.value + 1
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Calc.value" for e in reads), (
            f"Expected reads edge to Calc.value; got reads={reads}"
        )


# ── PY-WRITE-ASSIGN: self.x = v → writes edge ────────────────────────────────


class TestWriteAssignEdge:
    """self.x = value should emit a 'writes' edge."""

    def test_self_attr_write_emits_writes_edge(self) -> None:
        """self.balance = v inside a method produces writes edge."""
        src = """\
class Account:
    def set_balance(self, v):
        self.balance = v
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_write_source_is_method(self) -> None:
        """Source of writes edge is the enclosing method."""
        src = """\
class Account:
    def set_balance(self, v):
        self.balance = v
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(
            e["source"] == "Account.set_balance" and e["target"] == "Account.balance"
            for e in writes
        ), f"Expected writes from set_balance; got {writes}"

    def test_init_self_write_emits_writes_edge(self) -> None:
        """self.x = v inside __init__ produces a writes edge."""
        src = """\
class Account:
    def __init__(self, amount):
        self.balance = amount
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )


# ── PY-WRITE-AUG: self.x += v → writes edge ──────────────────────────────────


class TestWriteAugmentedEdge:
    """Augmented assignments (+=, -=, *=, etc.) to self.x → writes edge."""

    def test_self_attr_plus_eq_is_write(self) -> None:
        """self.balance += 10 produces writes edge."""
        src = """\
class Account:
    def deposit(self, amount):
        self.balance += amount
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_self_attr_minus_eq_is_write(self) -> None:
        """self.balance -= 10 produces writes edge."""
        src = """\
class Account:
    def withdraw(self, amount):
        self.balance -= amount
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance for -=; got writes={writes}"
        )


# ── PY-WRITE-DEL: del self.x → writes edge ────────────────────────────────────


class TestWriteDeleteEdge:
    """del self.x → writes edge (deletion is a mutation)."""

    def test_del_self_attr_is_write(self) -> None:
        """del self.cache produces writes edge."""
        src = """\
class Cache:
    def clear(self):
        del self.cache
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Cache.cache" for e in writes), (
            f"Expected writes edge for del self.cache; got writes={writes}"
        )


# ── PY-NO-CALL: self.foo() → NO field edge ────────────────────────────────────


class TestNoCallFieldEdge:
    """self.foo() (attribute in call position) must NOT produce a field edge."""

    def test_method_call_produces_no_field_edge(self) -> None:
        """self.foo() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker:
    def run(self):
        self.do_work()
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        # No reads or writes edges for do_work
        assert not any(
            "do_work" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"

    def test_chained_method_call_no_field_edge(self) -> None:
        """self.formatter.format() — intermediate attribute access is NOT indexed as reads."""
        src = """\
class Processor:
    def process(self):
        self.formatter.format()
"""
        # The call to format() is the top-level call; intermediate accesses are not field reads.
        # This is conservative: we do not try to chase chained attribute reads.
        _, edges = _parse_python(src)
        # At minimum, no spurious writes for the call expression
        writes = _writes_edges(edges)
        assert not any(
            "format" in e["target"] for e in writes
        ), f"method call should not produce writes edge; writes={writes}"


# ── PY-QUAL: qualified target via self → Type.field ──────────────────────────


class TestQualifiedTarget:
    """self.x in a method of class Foo → target is Foo.x."""

    def test_self_attr_read_has_qualified_target(self) -> None:
        """self.x read → target is 'ClassName.x' not just 'x'."""
        src = """\
class Foo:
    def bar(self):
        return self.x
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"

    def test_self_attr_write_has_qualified_target(self) -> None:
        """self.x = v write → target is 'ClassName.x'."""
        src = """\
class Foo:
    def bar(self, v):
        self.x = v
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Foo.x" in targets, f"Expected Foo.x in write targets; got {targets}"


# ── PY-UNRESOLVABLE: unknown receiver → bare target ──────────────────────────


class TestUnresolvableReceiver:
    """When the receiver type is unknown, target should be the bare field name."""

    def test_unknown_receiver_uses_bare_name(self) -> None:
        """obj.x when type of obj is unknown → target is bare 'x' (not 'Unknown.x')."""
        src = """\
def process(obj):
    return obj.value
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        # Target should be bare 'value' since obj's type is unknown
        targets = {e["target"] for e in reads}
        # No qualified target should appear since we don't know the type
        assert "value" in targets or any("." not in t for t in targets if "value" in t), (
            f"Expected bare 'value' target for unknown receiver; got targets={targets}"
        )
        # Also check that no wrong qualified name is emitted
        assert not any("Unknown.value" in t for t in targets), (
            f"Should not emit wrong qualified target; got {targets}"
        )


# ── PY-MULTI: multiple accesses in one function ───────────────────────────────


class TestMultipleAccesses:
    """Multiple attribute accesses in one function body should all be extracted."""

    def test_multiple_reads_all_extracted(self) -> None:
        """Two reads: self.a + self.b → two separate reads edges."""
        src = """\
class Pair:
    def total(self):
        return self.a + self.b
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Pair.a" in targets, f"Expected Pair.a in reads; got {targets}"
        assert "Pair.b" in targets, f"Expected Pair.b in reads; got {targets}"

    def test_mixed_read_and_write_in_same_method(self) -> None:
        """A method that both reads and writes: both edges emitted."""
        src = """\
class Counter:
    def increment(self, step):
        old = self.count
        self.count = old + step
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.count" for e in reads), (
            f"Expected reads edge for Counter.count; reads={reads}"
        )
        assert any(e["target"] == "Counter.count" for e in writes), (
            f"Expected writes edge for Counter.count; writes={writes}"
        )


# ── PY-NESTED: accesses in nested control flow ───────────────────────────────


class TestNestedControlFlow:
    """Attribute accesses inside if/for/while should still be extracted."""

    def test_read_inside_if(self) -> None:
        """self.x read inside an if block → reads edge."""
        src = """\
class Validator:
    def is_valid(self):
        if self.enabled:
            return True
        return False
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Validator.enabled" for e in reads), (
            f"Expected reads edge for Validator.enabled inside if; got {reads}"
        )

    def test_write_inside_for(self) -> None:
        """self.count += 1 inside a for loop → writes edge."""
        src = """\
class Accumulator:
    def run(self, items):
        for item in items:
            self.total += item
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Accumulator.total" for e in writes), (
            f"Expected writes edge for Accumulator.total inside for loop; got {writes}"
        )


# ── PY-LINE: line numbers ──────────────────────────────────────────────────────


class TestLineNumbers:
    """Line numbers in reads/writes edges are 1-based and point to the access site."""

    def test_reads_edge_line_is_correct(self) -> None:
        """reads edge line matches the line where self.x is accessed."""
        src = """\
class Foo:
    def bar(self):
        return self.x
"""
        # 'return self.x' is line 3
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        matching = [e for e in reads if e["target"] == "Foo.x"]
        assert matching, f"Expected reads edge to Foo.x; got {reads}"
        assert matching[0]["line"] == 3, (
            f"Expected line 3 for reads of Foo.x; got {matching[0]['line']}"
        )


# ── PY-FIELD-SYMBOLS: field symbols extracted ────────────────────────────────


class TestFieldSymbols:
    """Field declarations and self.x = ... assignments → kind='field' symbols."""

    def test_annotated_class_field_is_field_symbol(self) -> None:
        """class Foo:\n    x: int  → symbol kind='field', name='Foo.x'."""
        src = """\
class Account:
    balance: int
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        assert any(s["name"] == "Account.balance" for s in fields), (
            f"Expected field symbol Account.balance; got fields={fields}"
        )

    def test_field_symbol_qualified_name(self) -> None:
        """Field symbol's qualified_name is 'Type.field'."""
        src = """\
class Account:
    balance: int
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        matching = [s for s in fields if s["name"] == "Account.balance"]
        assert matching, f"Expected Account.balance field symbol; got fields={fields}"
        assert matching[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {matching[0]['qualified_name']}"
        )

    def test_init_self_assignment_becomes_field_symbol(self) -> None:
        """self.x = value in __init__ without class-level annotation → field symbol."""
        src = """\
class Counter:
    def __init__(self):
        self.count = 0
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        assert any(s["name"] == "Counter.count" for s in fields), (
            f"Expected field symbol Counter.count from __init__; got fields={fields}"
        )

    def test_field_symbol_dedup(self) -> None:
        """Multiple self.x = ... assignments → only ONE field symbol (dedup by Type.field)."""
        src = """\
class Foo:
    def __init__(self):
        self.x = 1
        self.x = 2
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        foo_x = [s for s in fields if s["name"] == "Foo.x"]
        assert len(foo_x) == 1, f"Expected exactly 1 Foo.x field symbol; got {foo_x}"

    def test_field_symbol_kind_is_field(self) -> None:
        """Field symbol kind must be 'field'."""
        src = """\
class Foo:
    x: str
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_class_and_init_field_dedup(self) -> None:
        """class-level annotation + __init__ assignment → ONE symbol (class-level wins)."""
        src = """\
class Account:
    balance: float
    def __init__(self):
        self.balance = 0.0
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert len(bal) == 1, f"Expected exactly 1 Account.balance symbol; got {bal}"

    def test_property_method_not_field_symbol(self) -> None:
        """A @property method should NOT be indexed as kind='field'."""
        src = """\
class Foo:
    @property
    def x(self):
        return self._x
"""
        syms, _ = _parse_python(src)
        # @property method becomes kind='method', not 'field'
        fields = _field_symbols(syms)
        # The @property decorated method 'x' should be 'method', not 'field'
        # (we only make assignment/annotation sites 'field')
        prop_fields = [s for s in fields if s["name"] == "Foo.x"]
        assert not prop_fields, (
            f"@property should not be a 'field' symbol; got {prop_fields}"
        )


# ── CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ───────────────────────────────────


class TestFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero field symbols and zero reads/writes edges."""

    def test_off_produces_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges are emitted."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account:
    def get(self):
        return self.balance
"""
        _, edges = _parse_python(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature is off; got {reads}"

    def test_off_produces_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges are emitted."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account:
    def set(self, v):
        self.balance = v
"""
        _, edges = _parse_python(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature is off; got {writes}"

    def test_off_produces_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols are emitted."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account:
    balance: int
    def __init__(self):
        self.balance = 0
"""
        syms, _ = _parse_python(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature is off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# TypeScript / JavaScript field-access tests (A3 Slice 2)
# ═════════════════════════════════════════════════════════════════════════════


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
