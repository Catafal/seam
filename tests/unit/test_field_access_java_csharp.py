"""Unit tests for A3 Slice 4: field_access.py core — Java and C#.

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
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.graph import extract_edges, extract_symbols

# ── Parse helpers ──────────────────────────────────────────────────────────────


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


def _reads_edges(edges):
    """Filter to only 'reads' edges."""
    return [e for e in edges if e["kind"] == "reads"]


def _writes_edges(edges):
    """Filter to only 'writes' edges."""
    return [e for e in edges if e["kind"] == "writes"]


def _field_symbols(symbols):
    """Filter to only 'field' symbols."""
    return [s for s in symbols if s["kind"] == "field"]


# ═════════════════════════════════════════════════════════════════════════════
# Java field-access tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


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
