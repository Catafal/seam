"""Unit tests for A3 Slice 1+2+3+4: field_access.py core (Python + TypeScript/JS + Go + Rust + Java + C# + C + C++).

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

Go coverage (A3 Slice 3):
  GO-READ:    r.Field (not in call position) → reads edge
  GO-WRITE-ASSIGN: r.Field = v → writes edge
  GO-WRITE-AUG: r.Field += v (assignment_statement with +=) → writes edge
  GO-WRITE-INC: r.Field++ → writes edge
  GO-WRITE-DEC: r.Field-- → writes edge
  GO-NO-CALL: r.Method() → NO field edge (selector in call position)
  GO-QUAL: r.Field → qualified target Type.Field when receiver type known
  GO-UNRESOLVABLE: x.Field when receiver type unknown → bare 'Field' target
  GO-FIELD-SYMBOLS: struct field_declaration → kind='field' symbols
  GO-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Go field symbols/edges

Rust coverage (A3 Slice 3):
  RUST-READ:    self.field (not in call position) → reads edge
  RUST-WRITE-ASSIGN: self.field = v (assignment_expression) → writes edge
  RUST-WRITE-AUG: self.field += v (compound_assignment_expr) → writes edge
  RUST-NO-CALL: self.method() → NO field edge (field_expression in call position)
  RUST-QUAL: self.field → qualified target Type.field when inside impl block
  RUST-UNRESOLVABLE: other.field when receiver type unknown → bare 'field' target
  RUST-FIELD-SYMBOLS: struct field_declaration → kind='field' symbols
  RUST-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Rust field symbols/edges

Java coverage (A3 Slice 4):
  JAVA-READ:    this.f / obj.f (not in call position) → reads edge
  JAVA-WRITE-ASSIGN: this.f = v (assignment_expression) → writes edge
  JAVA-WRITE-AUG: this.f += v (augmented assignment) → writes edge
  JAVA-WRITE-INC: this.f++ / ++this.f (update_expression) → writes edge
  JAVA-NO-CALL: this.foo() → NO field edge (method_invocation; stays a call edge)
  JAVA-QUAL: this.f → qualified target Type.f when class context known
  JAVA-FIELD-SYMBOLS: field_declaration → kind='field' symbols
  JAVA-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Java field symbols/edges

C# coverage (A3 Slice 4):
  CS-READ:    this.f / obj.f (member_access_expression not in call position) → reads edge
  CS-WRITE-ASSIGN: this.f = v → writes edge
  CS-WRITE-AUG: this.f += v → writes edge
  CS-WRITE-INC: this.f++ / ++this.f (prefix/postfix_unary_expression) → writes edge
  CS-NO-CALL: this.Foo() → NO field edge (invocation_expression; stays a call edge)
  CS-QUAL: this.f → qualified target Type.f when class context known
  CS-FIELD-SYMBOLS: field_declaration + property_declaration → kind='field' symbols
  CS-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no C# field symbols/edges

C coverage (A3 Slice 4):
  C-READ:    s.f and p->f (field_expression not in call position) → reads edge
  C-WRITE-ASSIGN: s.f = v → writes edge
  C-WRITE-INC: s.f++ / s.f-- (update_expression) → writes edge
  C-NO-CALL: s.f() → NO field edge (call_expression function child)
  C-FIELD-SYMBOLS: struct field_declaration → kind='field' symbols (StructName.field)
  C-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no C field symbols/edges

C++ coverage (A3 Slice 4):
  CPP-READ:    this->f / s.f → reads edge
  CPP-WRITE-ASSIGN: this->f = v → writes edge
  CPP-WRITE-INC: this->f++ (update_expression) → writes edge
  CPP-NO-CALL: this->foo() → NO field edge (call_expression function child)
  CPP-QUAL: this->f → qualified target ClassName.f when class context known
  CPP-FIELD-SYMBOLS: class/struct field_declaration → kind='field' symbols
  CPP-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no C++ field symbols/edges
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


# ═════════════════════════════════════════════════════════════════════════════
# Go field-access tests (A3 Slice 3)
# ═════════════════════════════════════════════════════════════════════════════


def _parse_go(source: str):
    """Parse Go source and return (symbols, edges)."""
    from seam.indexer.parser import parse_go

    with tempfile.NamedTemporaryFile(suffix=".go", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_go(path)
        assert root is not None
        syms = extract_symbols(root, "go", path)
        edges = extract_edges(root, "go", path)
        return syms, edges
    finally:
        os.unlink(fname)


def _parse_rust(source: str):
    """Parse Rust source and return (symbols, edges)."""
    from seam.indexer.parser import parse_rust

    with tempfile.NamedTemporaryFile(suffix=".rs", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_rust(path)
        assert root is not None
        syms = extract_symbols(root, "rust", path)
        edges = extract_edges(root, "rust", path)
        return syms, edges
    finally:
        os.unlink(fname)


# ── GO-READ: r.Field in non-call position → reads edge ──────────────────────


class TestGoReadEdge:
    """r.Field NOT in call position should emit a 'reads' edge."""

    def test_go_field_read_in_return_emits_reads(self) -> None:
        """return r.Balance produces reads edge for Account.Balance."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.Balance" for e in reads), (
            f"Expected reads edge to Account.Balance; got reads={reads}"
        )

    def test_go_reads_edge_source_is_method(self) -> None:
        """Source of reads edge is the enclosing method."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.Get" and e["target"] == "Account.Balance"
            for e in reads
        ), f"Expected reads from Account.Get to Account.Balance; got reads={reads}"

    def test_go_field_read_in_short_var_decl(self) -> None:
        """x := r.Balance in a function produces reads edge."""
        src = """\
package p
type Account struct { Balance int }
func process(r *Account) { x := r.Balance; _ = x }
"""
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.Balance" for e in reads), (
            f"Expected reads edge to Account.Balance from short_var_decl; got {reads}"
        )


# ── GO-WRITE-ASSIGN: r.Field = v → writes edge ──────────────────────────────


class TestGoWriteAssignEdge:
    """r.Field = v should emit a 'writes' edge."""

    def test_go_plain_assignment_is_write(self) -> None:
        """r.Name = name inside a method produces writes edge."""
        src = """\
package p
type Account struct { Name string }
func (r *Account) SetName(name string) { r.Name = name }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.Name" for e in writes), (
            f"Expected writes edge to Account.Name; got writes={writes}"
        )

    def test_go_write_source_is_method(self) -> None:
        """Source of writes edge is the enclosing method."""
        src = """\
package p
type Account struct { Name string }
func (r *Account) SetName(name string) { r.Name = name }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert any(
            e["source"] == "Account.SetName" and e["target"] == "Account.Name"
            for e in writes
        ), f"Expected writes from Account.SetName; got {writes}"


# ── GO-WRITE-AUG: r.Field += v → writes edge ────────────────────────────────


class TestGoWriteAugmentedEdge:
    """Augmented assignment (+=, -=, etc.) to r.Field → writes edge."""

    def test_go_plus_eq_is_write(self) -> None:
        """r.Balance += amount produces writes edge."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Deposit(amount int) { r.Balance += amount }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.Balance" for e in writes), (
            f"Expected writes edge for +=; got writes={writes}"
        )

    def test_go_minus_eq_is_write(self) -> None:
        """r.Balance -= amount produces writes edge."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Withdraw(amount int) { r.Balance -= amount }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.Balance" for e in writes), (
            f"Expected writes edge for -=; got writes={writes}"
        )


# ── GO-WRITE-INC: r.Field++ → writes edge ────────────────────────────────────


class TestGoWriteIncrement:
    """r.Field++ → writes edge (increment is a mutation)."""

    def test_go_inc_statement_is_write(self) -> None:
        """r.Count++ produces writes edge."""
        src = """\
package p
type Counter struct { Count int }
func (r *Counter) Inc() { r.Count++ }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.Count" for e in writes), (
            f"Expected writes edge for ++; got writes={writes}"
        )


# ── GO-WRITE-DEC: r.Field-- → writes edge ────────────────────────────────────


class TestGoWriteDecrement:
    """r.Field-- → writes edge (decrement is a mutation)."""

    def test_go_dec_statement_is_write(self) -> None:
        """r.Count-- produces writes edge."""
        src = """\
package p
type Counter struct { Count int }
func (r *Counter) Dec() { r.Count-- }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.Count" for e in writes), (
            f"Expected writes edge for --; got writes={writes}"
        )


# ── GO-NO-CALL: r.Method() → NO field edge ──────────────────────────────────


class TestGoNoCallFieldEdge:
    """r.Method() (selector_expression in call position) must NOT produce a field edge."""

    def test_go_method_call_produces_no_field_edge(self) -> None:
        """r.Process() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
package p
type Worker struct {}
func (r *Worker) Run() { r.Process() }
"""
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "Process" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"

    def test_go_method_call_does_produce_call_edge(self) -> None:
        """r.Process() should produce a 'call' edge."""
        src = """\
package p
type Worker struct {}
func (r *Worker) Run() { r.Process() }
"""
        _, edges = _parse_go(src)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert any(
            "Process" in e["target"] for e in call_edges
        ), f"Expected call edge for r.Process(); got call_edges={call_edges}"


# ── GO-QUAL: r.Field → qualified target Type.Field ───────────────────────────


class TestGoQualifiedTarget:
    """r.Field in a method with known receiver type → target is Type.Field."""

    def test_go_receiver_resolves_to_qualified_target(self) -> None:
        """r.Balance read → target is 'Account.Balance' not just 'Balance'."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Account.Balance" in targets, (
            f"Expected Account.Balance in read targets; got {targets}"
        )

    def test_go_write_has_qualified_target(self) -> None:
        """r.Balance = v write → target is 'Account.Balance'."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Set(v int) { r.Balance = v }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Account.Balance" in targets, (
            f"Expected Account.Balance in write targets; got {targets}"
        )


# ── GO-UNRESOLVABLE: unknown receiver → bare target ───────────────────────────


class TestGoUnresolvableReceiver:
    """When the receiver type is unknown, target should be bare field name."""

    def test_go_unknown_receiver_bare_name(self) -> None:
        """x.Value when type of x is unknown → bare 'Value' target."""
        src = """\
package p
func process(x interface{}) { _ = x.(struct{ Value int }).Value }
"""
        # With an unresolvable receiver, we expect bare 'Value' or nothing (no wrong qualified)
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        for e in reads:
            assert "." not in e["target"] or e["target"].split(".")[0] != "Unknown", (
                f"Should not emit wrong qualified target; got {e}"
            )


# ── GO-FIELD-SYMBOLS: struct fields → kind='field' symbols ───────────────────


class TestGoFieldSymbols:
    """Go struct field declarations → kind='field' symbols."""

    def test_go_struct_fields_become_field_symbols(self) -> None:
        """type Account struct { Balance int } → field symbol Account.Balance."""
        src = """\
package p
type Account struct {
    Balance int
    Name    string
}
"""
        syms, _ = _parse_go(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.Balance" in names, (
            f"Expected Account.Balance field symbol; got {names}"
        )
        assert "Account.Name" in names, (
            f"Expected Account.Name field symbol; got {names}"
        )

    def test_go_field_symbol_kind_is_field(self) -> None:
        """Go struct field symbol kind must be 'field'."""
        src = """\
package p
type Foo struct { X int }
"""
        syms, _ = _parse_go(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_go_field_symbol_qualified_name(self) -> None:
        """Go field symbol qualified_name is 'StructName.FieldName'."""
        src = """\
package p
type Account struct { Balance int }
"""
        syms, _ = _parse_go(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.Balance"]
        assert bal, f"Expected Account.Balance field symbol; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.Balance", (
            f"qualified_name should be 'Account.Balance'; got {bal[0]['qualified_name']}"
        )

    def test_go_all_field_types_indexed(self) -> None:
        """ALL struct fields are indexed (including primitive types like int, string)."""
        src = """\
package p
type Foo struct {
    Count int
    Label string
}
"""
        syms, _ = _parse_go(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        # Both fields should be indexed (unlike holds, which filters builtins)
        assert "Foo.Count" in names, (
            f"Expected Foo.Count even with builtin type; got {names}"
        )
        assert "Foo.Label" in names, (
            f"Expected Foo.Label even with builtin type; got {names}"
        )


# ── GO-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ────────────────────────────────


class TestGoFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero Go field symbols and zero reads/writes edges."""

    def test_go_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from Go."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        _, edges = _parse_go(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_go_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from Go."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
package p
type Account struct { Balance int }
func (r *Account) Set(v int) { r.Balance = v }
"""
        _, edges = _parse_go(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_go_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from Go."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
package p
type Account struct { Balance int }
"""
        syms, _ = _parse_go(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# Rust field-access tests (A3 Slice 3)
# ═════════════════════════════════════════════════════════════════════════════


# ── RUST-READ: self.field in non-call position → reads edge ──────────────────


class TestRustReadEdge:
    """self.field NOT in call position should emit a 'reads' edge."""

    def test_rust_field_read_in_return_emits_reads(self) -> None:
        """self.balance produces reads edge for Account.balance."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn get_balance(&self) -> i64 { self.balance }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_rust_reads_edge_source_is_method(self) -> None:
        """Source of reads edge is the enclosing method."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn get_balance(&self) -> i64 { self.balance }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.get_balance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.get_balance to Account.balance; got reads={reads}"

    def test_rust_field_read_in_let(self) -> None:
        """let x = self.balance produces reads edge."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn show(&self) { let x = self.balance; let _ = x; }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge in let binding; got {reads}"
        )


# ── RUST-WRITE-ASSIGN: self.field = v → writes edge ─────────────────────────


class TestRustWriteAssignEdge:
    """self.field = v (assignment_expression) should emit a 'writes' edge."""

    def test_rust_plain_assignment_is_write(self) -> None:
        """self.name = name inside a method produces writes edge."""
        src = """\
struct Account { name: String }
impl Account {
    fn set_name(&mut self, name: String) { self.name = name; }
}
"""
        _, edges = _parse_rust(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.name" for e in writes), (
            f"Expected writes edge to Account.name; got writes={writes}"
        )

    def test_rust_write_source_is_method(self) -> None:
        """Source of writes edge is the enclosing method."""
        src = """\
struct Account { name: String }
impl Account {
    fn set_name(&mut self, name: String) { self.name = name; }
}
"""
        _, edges = _parse_rust(src)
        writes = _writes_edges(edges)
        assert any(
            e["source"] == "Account.set_name" and e["target"] == "Account.name"
            for e in writes
        ), f"Expected writes from Account.set_name; got {writes}"


# ── RUST-WRITE-AUG: self.field += v → writes edge ────────────────────────────


class TestRustWriteAugmentedEdge:
    """compound_assignment_expr (+=, -=, etc.) to self.field → writes edge."""

    def test_rust_plus_eq_is_write(self) -> None:
        """self.balance += amount produces writes edge."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn deposit(&mut self, amount: i64) { self.balance += amount; }
}
"""
        _, edges = _parse_rust(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for +=; got writes={writes}"
        )

    def test_rust_minus_eq_is_write(self) -> None:
        """self.balance -= amount produces writes edge."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn withdraw(&mut self, amount: i64) { self.balance -= amount; }
}
"""
        _, edges = _parse_rust(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for -=; got writes={writes}"
        )


# ── RUST-NO-CALL: self.method() → NO field edge ──────────────────────────────


class TestRustNoCallFieldEdge:
    """self.method() (field_expression in call position) must NOT produce a field edge."""

    def test_rust_method_call_produces_no_field_edge(self) -> None:
        """self.process() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
struct Worker {}
impl Worker {
    fn run(&self) { self.process(); }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "process" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"

    def test_rust_method_call_does_produce_call_edge(self) -> None:
        """self.process() should produce a 'call' edge."""
        src = """\
struct Worker {}
impl Worker {
    fn run(&self) { self.process(); }
}
"""
        _, edges = _parse_rust(src)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert any(
            "process" in e["target"] for e in call_edges
        ), f"Expected call edge for self.process(); got call_edges={call_edges}"


# ── RUST-QUAL: self.field → qualified target Type.field ──────────────────────


class TestRustQualifiedTarget:
    """self.field in an impl block → target is Type.field."""

    def test_rust_self_field_read_has_qualified_target(self) -> None:
        """self.balance read → target is 'Account.balance'."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn get(&self) -> i64 { self.balance }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Account.balance" in targets, (
            f"Expected Account.balance in read targets; got {targets}"
        )

    def test_rust_self_field_write_has_qualified_target(self) -> None:
        """self.balance = v write → target is 'Account.balance'."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn set(&mut self, v: i64) { self.balance = v; }
}
"""
        _, edges = _parse_rust(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Account.balance" in targets, (
            f"Expected Account.balance in write targets; got {targets}"
        )


# ── RUST-UNRESOLVABLE: unknown receiver → bare target ────────────────────────


class TestRustUnresolvableReceiver:
    """When the receiver type is unknown, target should be bare field name."""

    def test_rust_unknown_receiver_bare_name(self) -> None:
        """other.balance when type of other is unknown → bare 'balance' or no edge."""
        src = """\
struct Transfer { amount: i64 }
impl Transfer {
    fn apply(&self, other: &mut dyn std::any::Any) {
        let _ = self.amount;
    }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        for e in reads:
            # Ensure no wrong qualified target is emitted for unknown receivers
            if "balance" in e["target"]:
                assert "." not in e["target"], (
                    f"Should not emit wrong qualified target for unknown receiver; got {e}"
                )


# ── RUST-FIELD-SYMBOLS: struct fields → kind='field' symbols ─────────────────


class TestRustFieldSymbols:
    """Rust struct field declarations → kind='field' symbols."""

    def test_rust_struct_fields_become_field_symbols(self) -> None:
        """struct Account { balance: i64 } → field symbol Account.balance."""
        src = """\
struct Account {
    balance: i64,
    name: String,
}
"""
        syms, _ = _parse_rust(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_rust_field_symbol_kind_is_field(self) -> None:
        """Rust struct field symbol kind must be 'field'."""
        src = """\
struct Foo { x: i32 }
"""
        syms, _ = _parse_rust(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_rust_field_symbol_qualified_name(self) -> None:
        """Rust field symbol qualified_name is 'StructName.field_name'."""
        src = """\
struct Account { balance: i64 }
"""
        syms, _ = _parse_rust(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance field symbol; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_rust_all_field_types_indexed(self) -> None:
        """ALL struct fields are indexed (including primitive types)."""
        src = """\
struct Foo {
    count: i32,
    label: String,
}
"""
        syms, _ = _parse_rust(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Foo.count" in names, (
            f"Expected Foo.count even with primitive type; got {names}"
        )
        assert "Foo.label" in names, (
            f"Expected Foo.label even with String type; got {names}"
        )


# ── RUST-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ─────────────────────────────


class TestRustFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero Rust field symbols and zero reads/writes edges."""

    def test_rust_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from Rust."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { balance: i64 }
impl Account {
    fn get(&self) -> i64 { self.balance }
}
"""
        _, edges = _parse_rust(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_rust_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from Rust."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { balance: i64 }
impl Account {
    fn set(&mut self, v: i64) { self.balance = v; }
}
"""
        _, edges = _parse_rust(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_rust_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from Rust."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { balance: i64 }
"""
        syms, _ = _parse_rust(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# Java field-access tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _parse_java(source: str):
    """Parse Java source and return (symbols, edges)."""
    from seam.indexer.parser import parse_java

    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_java(path)
        assert root is not None
        syms = extract_symbols(root, "java", path)
        edges = extract_edges(root, "java", path)
        return syms, edges
    finally:
        os.unlink(fname)


# ── JAVA-READ: this.f in non-call position → reads edge ─────────────────────


class TestJavaReadEdge:
    """this.f NOT in call position should emit a 'reads' edge."""

    def test_java_this_field_read_in_return(self) -> None:
        """return this.balance produces reads edge for Account.balance."""
        src = """\
class Account {
    private int balance;
    public int getBalance() {
        return this.balance;
    }
}
"""
        _, edges = _parse_java(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_java_reads_edge_source_is_method(self) -> None:
        """Source of reads edge is the enclosing Java method."""
        src = """\
class Account {
    private int balance;
    public int getBalance() {
        return this.balance;
    }
}
"""
        _, edges = _parse_java(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.getBalance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.getBalance to Account.balance; got reads={reads}"

    def test_java_this_field_read_in_expression(self) -> None:
        """this.balance used in an expression is a read."""
        src = """\
class Account {
    private int balance;
    public int doubled() {
        return this.balance * 2;
    }
}
"""
        _, edges = _parse_java(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )


# ── JAVA-WRITE-ASSIGN: this.f = v → writes edge ─────────────────────────────


class TestJavaWriteAssignEdge:
    """this.f = v should emit a 'writes' edge."""

    def test_java_this_field_assign_in_method(self) -> None:
        """this.balance = v inside a method produces writes edge."""
        src = """\
class Account {
    private int balance;
    public void setBalance(int v) {
        this.balance = v;
    }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_java_constructor_this_assign_emits_writes(self) -> None:
        """this.balance = v in constructor produces writes edge."""
        src = """\
class Account {
    private int balance;
    public Account(int b) {
        this.balance = b;
    }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge in constructor for Account.balance; got writes={writes}"
        )


# ── JAVA-WRITE-AUG: this.f += v → writes edge ───────────────────────────────


class TestJavaWriteAugmentedEdge:
    """Augmented assignment (+=, -=, etc.) to this.f → writes edge."""

    def test_java_this_field_plus_eq_is_write(self) -> None:
        """this.balance += amount produces writes edge."""
        src = """\
class Account {
    private int balance;
    public void deposit(int amount) {
        this.balance += amount;
    }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for += on Account.balance; got writes={writes}"
        )


# ── JAVA-WRITE-INC: this.f++ → writes edge ──────────────────────────────────


class TestJavaWriteIncrementEdge:
    """this.f++ / ++this.f (update_expression) → writes edge."""

    def test_java_postfix_increment_is_write(self) -> None:
        """this.count++ produces writes edge."""
        src = """\
class Counter {
    private int count;
    public void inc() {
        this.count++;
    }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.count" for e in writes), (
            f"Expected writes edge for this.count++; got writes={writes}"
        )

    def test_java_prefix_decrement_is_write(self) -> None:
        """--this.count produces writes edge."""
        src = """\
class Counter {
    private int count;
    public void dec() {
        --this.count;
    }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.count" for e in writes), (
            f"Expected writes edge for --this.count; got writes={writes}"
        )


# ── JAVA-NO-CALL: this.foo() → NO field edge ─────────────────────────────────


class TestJavaNoCallFieldEdge:
    """this.foo() (method_invocation) must NOT produce a field edge."""

    def test_java_method_call_produces_no_field_edge(self) -> None:
        """this.doWork() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker {
    public void run() {
        this.doWork();
    }
}
"""
        _, edges = _parse_java(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "doWork" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"


# ── JAVA-QUAL: this.f → qualified target Type.f ──────────────────────────────


class TestJavaQualifiedTarget:
    """this.f in a method of class Foo → target is Foo.f."""

    def test_java_this_field_read_has_qualified_target(self) -> None:
        """this.x read → target is 'ClassName.x'."""
        src = """\
class Foo {
    private int x;
    public int getX() {
        return this.x;
    }
}
"""
        _, edges = _parse_java(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"

    def test_java_this_field_write_has_qualified_target(self) -> None:
        """this.x = v write → target is 'ClassName.x'."""
        src = """\
class Foo {
    private int x;
    public void setX(int v) {
        this.x = v;
    }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Foo.x" in targets, f"Expected Foo.x in write targets; got {targets}"


# ── JAVA-FIELD-SYMBOLS: field_declaration → kind='field' symbols ─────────────


class TestJavaFieldSymbols:
    """Java field declarations → kind='field' symbols."""

    def test_java_field_declaration_becomes_field_symbol(self) -> None:
        """private int balance; → field symbol Account.balance."""
        src = """\
class Account {
    private int balance;
    private String name;
}
"""
        syms, _ = _parse_java(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_java_field_symbol_kind_is_field(self) -> None:
        """Java field symbol kind must be 'field'."""
        src = """\
class Foo {
    private int x;
}
"""
        syms, _ = _parse_java(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_java_field_symbol_qualified_name(self) -> None:
        """Java field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account {
    private int balance;
}
"""
        syms, _ = _parse_java(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_java_all_field_types_indexed(self) -> None:
        """ALL Java fields are indexed (including primitive types like int, String)."""
        src = """\
class Foo {
    private int count;
    private String label;
}
"""
        syms, _ = _parse_java(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Foo.count" in names, f"Expected Foo.count; got {names}"
        assert "Foo.label" in names, f"Expected Foo.label; got {names}"


# ── JAVA-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ─────────────────────────────


class TestJavaFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero Java field symbols and zero reads/writes edges."""

    def test_java_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from Java."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public int get() { return this.balance; }
}
"""
        _, edges = _parse_java(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_java_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from Java."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public void set(int v) { this.balance = v; }
}
"""
        _, edges = _parse_java(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_java_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from Java."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
}
"""
        syms, _ = _parse_java(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# C# field-access tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _parse_csharp(source: str):
    """Parse C# source and return (symbols, edges)."""
    from seam.indexer.parser import parse_csharp

    with tempfile.NamedTemporaryFile(suffix=".cs", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_csharp(path)
        assert root is not None
        syms = extract_symbols(root, "csharp", path)
        edges = extract_edges(root, "csharp", path)
        return syms, edges
    finally:
        os.unlink(fname)


# ── CS-READ: this.f in non-call position → reads edge ───────────────────────


class TestCSReadEdge:
    """this.f NOT in call position should emit a 'reads' edge."""

    def test_cs_this_field_read_in_return(self) -> None:
        """return this.balance produces reads edge for Account.balance."""
        src = """\
class Account {
    private int balance;
    public int GetBalance() {
        return this.balance;
    }
}
"""
        _, edges = _parse_csharp(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_cs_reads_edge_source_is_method(self) -> None:
        """Source of reads edge is the enclosing C# method."""
        src = """\
class Account {
    private int balance;
    public int GetBalance() {
        return this.balance;
    }
}
"""
        _, edges = _parse_csharp(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.GetBalance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.GetBalance to Account.balance; got reads={reads}"


# ── CS-WRITE-ASSIGN: this.f = v → writes edge ───────────────────────────────


class TestCSWriteAssignEdge:
    """this.f = v should emit a 'writes' edge."""

    def test_cs_this_field_assign_in_method(self) -> None:
        """this.balance = v inside a method produces writes edge."""
        src = """\
class Account {
    private int balance;
    public void SetBalance(int v) {
        this.balance = v;
    }
}
"""
        _, edges = _parse_csharp(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_cs_constructor_this_assign_emits_writes(self) -> None:
        """this.balance = v in constructor produces writes edge."""
        src = """\
class Account {
    private int balance;
    public Account(int b) {
        this.balance = b;
    }
}
"""
        _, edges = _parse_csharp(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge in constructor for Account.balance; got writes={writes}"
        )


# ── CS-WRITE-AUG: this.f += v → writes edge ─────────────────────────────────


class TestCSWriteAugmentedEdge:
    """Augmented assignment (+=, -=, etc.) to this.f → writes edge."""

    def test_cs_this_field_plus_eq_is_write(self) -> None:
        """this.balance += amount produces writes edge."""
        src = """\
class Account {
    private int balance;
    public void Deposit(int amount) {
        this.balance += amount;
    }
}
"""
        _, edges = _parse_csharp(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for += on Account.balance; got writes={writes}"
        )


# ── CS-WRITE-INC: this.f++ → writes edge ─────────────────────────────────────


class TestCSWriteIncrementEdge:
    """this.f++ / ++this.f → writes edge."""

    def test_cs_postfix_increment_is_write(self) -> None:
        """this.count++ produces writes edge."""
        src = """\
class Counter {
    private int count;
    public void Inc() {
        this.count++;
    }
}
"""
        _, edges = _parse_csharp(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.count" for e in writes), (
            f"Expected writes edge for this.count++; got writes={writes}"
        )


# ── CS-NO-CALL: this.Foo() → NO field edge ───────────────────────────────────


class TestCSNoCallFieldEdge:
    """this.Foo() (invocation_expression) must NOT produce a field edge."""

    def test_cs_method_call_produces_no_field_edge(self) -> None:
        """this.DoWork() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker {
    public void Run() {
        this.DoWork();
    }
}
"""
        _, edges = _parse_csharp(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "DoWork" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"


# ── CS-QUAL: this.f → qualified target Type.f ────────────────────────────────


class TestCSQualifiedTarget:
    """this.f in a method of class Foo → target is Foo.f."""

    def test_cs_this_field_read_has_qualified_target(self) -> None:
        """this.x read → target is 'ClassName.x'."""
        src = """\
class Foo {
    private int x;
    public int GetX() {
        return this.x;
    }
}
"""
        _, edges = _parse_csharp(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"


# ── CS-FIELD-SYMBOLS: field_declaration + property_declaration ───────────────


class TestCSFieldSymbols:
    """C# field declarations and properties → kind='field' symbols."""

    def test_cs_field_declaration_becomes_field_symbol(self) -> None:
        """private int balance; → field symbol Account.balance."""
        src = """\
class Account {
    private int balance;
    private string name;
}
"""
        syms, _ = _parse_csharp(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_cs_property_declaration_becomes_field_symbol(self) -> None:
        """public int Balance { get; set; } → field symbol Account.Balance."""
        src = """\
class Account {
    public int Balance { get; set; }
}
"""
        syms, _ = _parse_csharp(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.Balance" in names, (
            f"Expected Account.Balance field symbol from property; got {names}"
        )

    def test_cs_field_symbol_kind_is_field(self) -> None:
        """C# field symbol kind must be 'field'."""
        src = """\
class Foo {
    private int x;
}
"""
        syms, _ = _parse_csharp(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_cs_field_symbol_qualified_name(self) -> None:
        """C# field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account {
    private int balance;
}
"""
        syms, _ = _parse_csharp(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── CS-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ───────────────────────────────


class TestCSFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero C# field symbols and zero reads/writes edges."""

    def test_cs_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from C#."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public int Get() { return this.balance; }
}
"""
        _, edges = _parse_csharp(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_cs_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from C#."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public void Set(int v) { this.balance = v; }
}
"""
        _, edges = _parse_csharp(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_cs_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from C#."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
}
"""
        syms, _ = _parse_csharp(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# C field-access tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _parse_c(source: str):
    """Parse C source and return (symbols, edges)."""
    from seam.indexer.parser import parse_c

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_c(path)
        assert root is not None
        syms = extract_symbols(root, "c", path)
        edges = extract_edges(root, "c", path)
        return syms, edges
    finally:
        os.unlink(fname)


# ── C-READ: s.f and p->f in non-call position → reads edge ──────────────────


class TestCReadEdge:
    """s.f and p->f NOT in call position should emit 'reads' edges."""

    def test_c_dot_field_read_emits_reads(self) -> None:
        """s.balance (dot access) in a function produces reads edge."""
        src = """\
typedef struct { int balance; } Account;
int get_balance(Account s) {
    return s.balance;
}
"""
        _, edges = _parse_c(src)
        reads = _reads_edges(edges)
        # Bare field name (C has no class context in functions)
        assert any("balance" in e["target"] for e in reads), (
            f"Expected reads edge containing 'balance'; got reads={reads}"
        )

    def test_c_arrow_field_read_emits_reads(self) -> None:
        """p->balance (arrow access) in a function produces reads edge."""
        src = """\
typedef struct { int balance; } Account;
int get_balance(Account *p) {
    return p->balance;
}
"""
        _, edges = _parse_c(src)
        reads = _reads_edges(edges)
        assert any("balance" in e["target"] for e in reads), (
            f"Expected reads edge for p->balance; got reads={reads}"
        )


# ── C-WRITE-ASSIGN: s.f = v → writes edge ───────────────────────────────────


class TestCWriteAssignEdge:
    """s.f = v / p->f = v should emit 'writes' edges."""

    def test_c_dot_assign_is_write(self) -> None:
        """s.balance = v in a function produces writes edge."""
        src = """\
typedef struct { int balance; } Account;
void set_balance(Account s, int v) {
    s.balance = v;
}
"""
        _, edges = _parse_c(src)
        writes = _writes_edges(edges)
        assert any("balance" in e["target"] for e in writes), (
            f"Expected writes edge for s.balance = v; got writes={writes}"
        )

    def test_c_arrow_assign_is_write(self) -> None:
        """p->balance = v in a function produces writes edge."""
        src = """\
typedef struct { int balance; } Account;
void set_balance(Account *p, int v) {
    p->balance = v;
}
"""
        _, edges = _parse_c(src)
        writes = _writes_edges(edges)
        assert any("balance" in e["target"] for e in writes), (
            f"Expected writes edge for p->balance = v; got writes={writes}"
        )


# ── C-WRITE-INC: s.f++ → writes edge ────────────────────────────────────────


class TestCWriteIncrementEdge:
    """s.f++ and s.f-- → writes edge."""

    def test_c_field_postfix_inc_is_write(self) -> None:
        """s.count++ produces writes edge."""
        src = """\
typedef struct { int count; } Counter;
void inc(Counter *p) {
    p->count++;
}
"""
        _, edges = _parse_c(src)
        writes = _writes_edges(edges)
        assert any("count" in e["target"] for e in writes), (
            f"Expected writes edge for p->count++; got writes={writes}"
        )


# ── C-NO-CALL: s.f() → NO field edge ─────────────────────────────────────────


class TestCNoCallFieldEdge:
    """s.f() (call_expression function child) must NOT produce a field edge."""

    def test_c_function_pointer_call_no_field_edge(self) -> None:
        """s.handler() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
typedef struct { void (*handler)(void); } Worker;
void run(Worker *w) {
    w->handler();
}
"""
        _, edges = _parse_c(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        # The handler call is in call position — no field edge for it
        assert not any(
            "handler" in e["target"] for e in reads + writes
        ), f"function pointer call should not produce field edge; reads={reads}, writes={writes}"


# ── C-FIELD-SYMBOLS: struct field_declaration → kind='field' symbols ─────────


class TestCFieldSymbols:
    """C struct field declarations → kind='field' symbols with 'StructName.field'."""

    def test_c_struct_fields_become_field_symbols(self) -> None:
        """struct Account { int balance; } → field symbol Account.balance."""
        src = """\
struct Account {
    int balance;
    char name[64];
};
"""
        syms, _ = _parse_c(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )

    def test_c_field_symbol_kind_is_field(self) -> None:
        """C struct field symbol kind must be 'field'."""
        src = """\
struct Foo { int x; };
"""
        syms, _ = _parse_c(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_c_field_symbol_qualified_name(self) -> None:
        """C struct field symbol qualified_name is 'StructName.field'."""
        src = """\
struct Account { int balance; };
"""
        syms, _ = _parse_c(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── C-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ────────────────────────────────


class TestCFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero C field symbols and zero reads/writes edges."""

    def test_c_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from C."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { int balance; };
int get(struct Account *p) { return p->balance; }
"""
        _, edges = _parse_c(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_c_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from C."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { int balance; };
void set(struct Account *p, int v) { p->balance = v; }
"""
        _, edges = _parse_c(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_c_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from C."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { int balance; };
"""
        syms, _ = _parse_c(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# C++ field-access tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _parse_cpp(source: str):
    """Parse C++ source and return (symbols, edges)."""
    from seam.indexer.parser import parse_cpp

    with tempfile.NamedTemporaryFile(suffix=".cpp", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_cpp(path)
        assert root is not None
        syms = extract_symbols(root, "cpp", path)
        edges = extract_edges(root, "cpp", path)
        return syms, edges
    finally:
        os.unlink(fname)


# ── CPP-READ: this->f / s.f → reads edge ─────────────────────────────────────


class TestCPPReadEdge:
    """this->f NOT in call position should emit a 'reads' edge."""

    def test_cpp_this_arrow_field_read_emits_reads(self) -> None:
        """return this->balance produces reads edge for Account.balance."""
        src = """\
class Account {
public:
    int balance;
    int getBalance() {
        return this->balance;
    }
};
"""
        _, edges = _parse_cpp(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_cpp_reads_edge_source_is_method(self) -> None:
        """Source of reads edge is the enclosing C++ method."""
        src = """\
class Account {
public:
    int balance;
    int getBalance() {
        return this->balance;
    }
};
"""
        _, edges = _parse_cpp(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.getBalance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.getBalance to Account.balance; got reads={reads}"


# ── CPP-WRITE-ASSIGN: this->f = v → writes edge ──────────────────────────────


class TestCPPWriteAssignEdge:
    """this->f = v should emit a 'writes' edge."""

    def test_cpp_this_arrow_assign_is_write(self) -> None:
        """this->balance = v inside a method produces writes edge."""
        src = """\
class Account {
public:
    int balance;
    void setBalance(int v) {
        this->balance = v;
    }
};
"""
        _, edges = _parse_cpp(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )


# ── CPP-WRITE-INC: this->f++ → writes edge ───────────────────────────────────


class TestCPPWriteIncrementEdge:
    """this->f++ → writes edge."""

    def test_cpp_postfix_increment_is_write(self) -> None:
        """this->count++ produces writes edge."""
        src = """\
class Counter {
public:
    int count;
    void inc() {
        this->count++;
    }
};
"""
        _, edges = _parse_cpp(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Counter.count" for e in writes), (
            f"Expected writes edge for this->count++; got writes={writes}"
        )


# ── CPP-NO-CALL: this->foo() → NO field edge ─────────────────────────────────


class TestCPPNoCallFieldEdge:
    """this->foo() (call_expression function child) must NOT produce a field edge."""

    def test_cpp_method_call_produces_no_field_edge(self) -> None:
        """this->doWork() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker {
public:
    void run() {
        this->doWork();
    }
};
"""
        _, edges = _parse_cpp(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "doWork" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"


# ── CPP-QUAL: this->f → qualified target ClassName.f ─────────────────────────


class TestCPPQualifiedTarget:
    """this->f in a method of class Foo → target is Foo.f."""

    def test_cpp_this_arrow_read_has_qualified_target(self) -> None:
        """this->x read → target is 'ClassName.x'."""
        src = """\
class Foo {
public:
    int x;
    int getX() {
        return this->x;
    }
};
"""
        _, edges = _parse_cpp(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"


# ── CPP-FIELD-SYMBOLS: class/struct field_declaration → kind='field' symbols ──


class TestCPPFieldSymbols:
    """C++ class/struct field declarations → kind='field' symbols."""

    def test_cpp_class_fields_become_field_symbols(self) -> None:
        """class Account { int balance; } → field symbol Account.balance."""
        src = """\
class Account {
public:
    int balance;
    std::string name;
};
"""
        syms, _ = _parse_cpp(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )

    def test_cpp_struct_fields_become_field_symbols(self) -> None:
        """struct Point { int x; int y; } → field symbols Point.x, Point.y."""
        src = """\
struct Point {
    int x;
    int y;
};
"""
        syms, _ = _parse_cpp(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Point.x" in names, f"Expected Point.x; got {names}"
        assert "Point.y" in names, f"Expected Point.y; got {names}"

    def test_cpp_field_symbol_kind_is_field(self) -> None:
        """C++ field symbol kind must be 'field'."""
        src = """\
class Foo { public: int x; };
"""
        syms, _ = _parse_cpp(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_cpp_field_symbol_qualified_name(self) -> None:
        """C++ field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account { public: int balance; };
"""
        syms, _ = _parse_cpp(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── CPP-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ───────────────────────────────


class TestCPPFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero C++ field symbols and zero reads/writes edges."""

    def test_cpp_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from C++."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account { public: int balance; int get() { return this->balance; } };
"""
        _, edges = _parse_cpp(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_cpp_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from C++."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account { public: int balance; void set(int v) { this->balance = v; } };
"""
        _, edges = _parse_cpp(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_cpp_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from C++."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account { public: int balance; };
"""
        syms, _ = _parse_cpp(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"
