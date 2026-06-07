"""Unit tests for A3 Slice 5: field_access.py core — Ruby and PHP.

Ruby coverage:
  RUBY-READ:    @field in non-call position → reads edge
  RUBY-WRITE-ASSIGN: @field = v → writes edge
  RUBY-WRITE-AUG: @field += v → writes edge
  RUBY-NO-CALL: bar() → NO field edge (call node)
  RUBY-QUAL: @field → qualified target ClassName.field (@ prefix stripped)
  RUBY-FIELD-SYMBOLS: @ivar first-assignment → kind='field' symbols
  RUBY-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Ruby field symbols/edges

PHP coverage:
  PHP-READ:    $this->field in non-call position → reads edge
  PHP-WRITE-ASSIGN: $this->field = v → writes edge
  PHP-WRITE-AUG: $this->field += v → writes edge
  PHP-NO-CALL: $this->method() → NO field edge (member_call_expression)
  PHP-QUAL: $this->field → qualified ClassName.field
  PHP-FIELD-SYMBOLS: property_declaration → kind='field' symbols
  PHP-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off → no PHP field symbols/edges

Ruby instance variables (@ivar):
  - No explicit receiver; always belong to the enclosing class
  - AST: standalone 'instance_variable' nodes
  - Field symbols: @ivar first-assignment sites (deduped per class)
  - qualified_name='ClassName.balance' (@ prefix stripped)

PHP member access:
  - member_access_expression ($obj->field or $this->field)
  - Call position: member_call_expression → NOT a field edge
  - Field symbols: property_declaration nodes in class body
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.graph import extract_edges, extract_symbols

# ── Parse helpers ──────────────────────────────────────────────────────────────


def _parse_ruby(source: str):
    """Parse Ruby source and return (symbols, edges)."""
    from seam.indexer.parser import parse_ruby

    with tempfile.NamedTemporaryFile(suffix=".rb", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_ruby(path)
        assert root is not None
        syms = extract_symbols(root, "ruby", path)
        edges = extract_edges(root, "ruby", path)
        return syms, edges
    finally:
        os.unlink(fname)


def _parse_php(source: str):
    """Parse PHP source and return (symbols, edges)."""
    from seam.indexer.parser import parse_php

    with tempfile.NamedTemporaryFile(suffix=".php", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_php(path)
        assert root is not None
        syms = extract_symbols(root, "php", path)
        edges = extract_edges(root, "php", path)
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
# Ruby field-access tests
# ═════════════════════════════════════════════════════════════════════════════


# ── RUBY-READ: @field in non-call position → reads edge ──────────────────────


class TestRubyReadEdge:
    """@balance NOT in call position should emit a 'reads' edge."""

    def test_ruby_ivar_read_emits_reads_edge(self) -> None:
        """@balance in a method body produces reads edge for Account.balance."""
        src = """\
class Account
  def get_balance
    @balance
  end
end
"""
        _, edges = _parse_ruby(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_ruby_ivar_read_source_is_method(self) -> None:
        """Source of reads edge is the enclosing method."""
        src = """\
class Account
  def get_balance
    @balance
  end
end
"""
        _, edges = _parse_ruby(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.get_balance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.get_balance to Account.balance; got reads={reads}"

    def test_ruby_ivar_read_in_return(self) -> None:
        """return @balance produces reads edge."""
        src = """\
class Account
  def balance
    return @balance
  end
end
"""
        _, edges = _parse_ruby(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance in return; got reads={reads}"
        )


# ── RUBY-WRITE-ASSIGN: @field = v → writes edge ──────────────────────────────


class TestRubyWriteAssignEdge:
    """@balance = v should emit a 'writes' edge."""

    def test_ruby_ivar_write_emits_writes_edge(self) -> None:
        """@balance = v inside a method produces writes edge."""
        src = """\
class Account
  def set_balance(v)
    @balance = v
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_ruby_initialize_write_emits_writes_edge(self) -> None:
        """@balance = 0 in initialize produces writes edge."""
        src = """\
class Account
  def initialize
    @balance = 0
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge in initialize for Account.balance; got writes={writes}"
        )

    def test_ruby_ivar_write_source_is_method(self) -> None:
        """Source of writes edge is the enclosing method."""
        src = """\
class Account
  def set_balance(v)
    @balance = v
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        assert any(
            e["source"] == "Account.set_balance" and e["target"] == "Account.balance"
            for e in writes
        ), f"Expected writes from Account.set_balance to Account.balance; got {writes}"


# ── RUBY-WRITE-AUG: @field += v → writes edge ────────────────────────────────


class TestRubyWriteAugmentedEdge:
    """Augmented assignment (@balance += v) should emit a 'writes' edge."""

    def test_ruby_ivar_plus_eq_is_write(self) -> None:
        """@balance += amount produces writes edge."""
        src = """\
class Account
  def deposit(amount)
    @balance += amount
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for +=; got writes={writes}"
        )

    def test_ruby_ivar_minus_eq_is_write(self) -> None:
        """@balance -= amount produces writes edge."""
        src = """\
class Account
  def withdraw(amount)
    @balance -= amount
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for -=; got writes={writes}"
        )


# ── RUBY-NO-CALL: bar() → NO field edge ──────────────────────────────────────


class TestRubyNoCallFieldEdge:
    """Method calls (call node) must NOT produce a field edge."""

    def test_ruby_call_produces_no_field_edge(self) -> None:
        """bar() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
class Worker
  def run
    bar()
  end
end
"""
        _, edges = _parse_ruby(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "bar" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"


# ── RUBY-QUAL: @field → qualified target ClassName.field ─────────────────────


class TestRubyQualifiedTarget:
    """@field inside class Foo → target is 'Foo.field' (without @ prefix)."""

    def test_ruby_ivar_read_qualified_target(self) -> None:
        """@x read in class Foo → target is 'Foo.x' (no @ in target)."""
        src = """\
class Foo
  def bar
    @x
  end
end
"""
        _, edges = _parse_ruby(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"
        # Ensure the @ prefix is stripped
        assert not any(e["target"].startswith("@") for e in reads), (
            f"Target must NOT have @ prefix; got {targets}"
        )

    def test_ruby_ivar_write_qualified_target(self) -> None:
        """@x = v in class Foo → target is 'Foo.x'."""
        src = """\
class Foo
  def bar(v)
    @x = v
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Foo.x" in targets, f"Expected Foo.x in write targets; got {targets}"


# ── RUBY-FIELD-SYMBOLS: @ivar first-assignment → kind='field' symbols ─────────


class TestRubyFieldSymbols:
    """@ivar first-assignment sites → kind='field' symbols (qualified ClassName.field)."""

    def test_ruby_ivar_in_initialize_becomes_field_symbol(self) -> None:
        """@balance = 0 in initialize → field symbol Account.balance."""
        src = """\
class Account
  def initialize
    @balance = 0
    @name = ''
  end
end
"""
        syms, _ = _parse_ruby(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_ruby_field_symbol_kind_is_field(self) -> None:
        """Ruby field symbol kind must be 'field'."""
        src = """\
class Foo
  def initialize
    @x = 1
  end
end
"""
        syms, _ = _parse_ruby(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_ruby_field_symbol_qualified_name(self) -> None:
        """Ruby field symbol qualified_name is 'ClassName.field' (no @ prefix)."""
        src = """\
class Account
  def initialize
    @balance = 0
  end
end
"""
        syms, _ = _parse_ruby(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance field symbol; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_ruby_field_symbol_dedup(self) -> None:
        """Multiple @x = ... assignments in different methods → ONE field symbol."""
        src = """\
class Foo
  def initialize
    @x = 1
  end
  def reset
    @x = 0
  end
end
"""
        syms, _ = _parse_ruby(src)
        fields = _field_symbols(syms)
        foo_x = [s for s in fields if s["name"] == "Foo.x"]
        assert len(foo_x) == 1, f"Expected exactly 1 Foo.x field symbol; got {foo_x}"

    def test_ruby_field_symbol_has_no_at_prefix(self) -> None:
        """Field symbol name must NOT include the @ prefix."""
        src = """\
class Foo
  def initialize
    @value = 0
  end
end
"""
        syms, _ = _parse_ruby(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert not s["name"].startswith("@"), (
                f"Field symbol name must not start with @; got {s['name']}"
            )


# ── RUBY-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ─────────────────────────────


class TestRubyFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero Ruby field symbols and zero reads/writes edges."""

    def test_ruby_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from Ruby."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account
  def get_balance
    @balance
  end
end
"""
        _, edges = _parse_ruby(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_ruby_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from Ruby."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account
  def set_balance(v)
    @balance = v
  end
end
"""
        _, edges = _parse_ruby(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_ruby_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from Ruby."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account
  def initialize
    @balance = 0
  end
end
"""
        syms, _ = _parse_ruby(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"


# ═════════════════════════════════════════════════════════════════════════════
# PHP field-access tests
# ═════════════════════════════════════════════════════════════════════════════


# ── PHP-READ: $this->field in non-call position → reads edge ─────────────────


class TestPHPReadEdge:
    """$this->balance NOT in call position should emit a 'reads' edge."""

    def test_php_this_field_read_emits_reads_edge(self) -> None:
        """$this->balance in a method body produces reads edge for Account.balance."""
        src = """\
<?php
class Account {
    public int $balance = 0;
    public function getBalance(): int {
        return $this->balance;
    }
}
"""
        _, edges = _parse_php(src)
        reads = _reads_edges(edges)
        assert any(e["target"] == "Account.balance" for e in reads), (
            f"Expected reads edge to Account.balance; got reads={reads}"
        )

    def test_php_this_field_read_source_is_method(self) -> None:
        """Source of reads edge is the enclosing PHP method."""
        src = """\
<?php
class Account {
    public int $balance = 0;
    public function getBalance(): int {
        return $this->balance;
    }
}
"""
        _, edges = _parse_php(src)
        reads = _reads_edges(edges)
        assert any(
            e["source"] == "Account.getBalance" and e["target"] == "Account.balance"
            for e in reads
        ), f"Expected reads from Account.getBalance to Account.balance; got reads={reads}"


# ── PHP-WRITE-ASSIGN: $this->field = v → writes edge ─────────────────────────


class TestPHPWriteAssignEdge:
    """$this->balance = v (assignment_expression) should emit a 'writes' edge."""

    def test_php_this_field_assign_emits_writes(self) -> None:
        """$this->balance = $v in a method produces writes edge."""
        src = """\
<?php
class Account {
    public int $balance = 0;
    public function setBalance(int $v): void {
        $this->balance = $v;
    }
}
"""
        _, edges = _parse_php(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge to Account.balance; got writes={writes}"
        )

    def test_php_write_source_is_method(self) -> None:
        """Source of writes edge is the enclosing PHP method."""
        src = """\
<?php
class Account {
    public int $balance = 0;
    public function setBalance(int $v): void {
        $this->balance = $v;
    }
}
"""
        _, edges = _parse_php(src)
        writes = _writes_edges(edges)
        assert any(
            e["source"] == "Account.setBalance" and e["target"] == "Account.balance"
            for e in writes
        ), f"Expected writes from Account.setBalance to Account.balance; got {writes}"


# ── PHP-WRITE-AUG: $this->field += v → writes edge ───────────────────────────


class TestPHPWriteAugmentedEdge:
    """Augmented assignment ($this->balance += $amount) should emit a 'writes' edge."""

    def test_php_this_field_plus_eq_is_write(self) -> None:
        """$this->balance += $amount produces writes edge."""
        src = """\
<?php
class Account {
    public int $balance = 0;
    public function deposit(int $amount): void {
        $this->balance += $amount;
    }
}
"""
        _, edges = _parse_php(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for +=; got writes={writes}"
        )

    def test_php_this_field_minus_eq_is_write(self) -> None:
        """$this->balance -= $amount produces writes edge."""
        src = """\
<?php
class Account {
    public int $balance = 0;
    public function withdraw(int $amount): void {
        $this->balance -= $amount;
    }
}
"""
        _, edges = _parse_php(src)
        writes = _writes_edges(edges)
        assert any(e["target"] == "Account.balance" for e in writes), (
            f"Expected writes edge for -=; got writes={writes}"
        )


# ── PHP-NO-CALL: $this->method() → NO field edge ─────────────────────────────


class TestPHPNoCallFieldEdge:
    """$this->method() (member_call_expression) must NOT produce a field edge."""

    def test_php_method_call_produces_no_field_edge(self) -> None:
        """$this->bar() should produce a 'call' edge, NOT a reads/writes edge."""
        src = """\
<?php
class Worker {
    public function run(): void {
        $this->bar();
    }
}
"""
        _, edges = _parse_php(src)
        reads = _reads_edges(edges)
        writes = _writes_edges(edges)
        assert not any(
            "bar" in e["target"] for e in reads + writes
        ), f"method call should not produce field edge; reads={reads}, writes={writes}"


# ── PHP-QUAL: $this->field → qualified ClassName.field ───────────────────────


class TestPHPQualifiedTarget:
    """$this->field in a method of class Foo → target is 'Foo.field'."""

    def test_php_this_field_read_qualified_target(self) -> None:
        """$this->x read → target is 'Foo.x'."""
        src = """\
<?php
class Foo {
    public int $x = 0;
    public function bar(): int {
        return $this->x;
    }
}
"""
        _, edges = _parse_php(src)
        reads = _reads_edges(edges)
        targets = {e["target"] for e in reads}
        assert "Foo.x" in targets, f"Expected Foo.x in read targets; got {targets}"

    def test_php_this_field_write_qualified_target(self) -> None:
        """$this->x = $v write → target is 'Foo.x'."""
        src = """\
<?php
class Foo {
    public int $x = 0;
    public function bar(int $v): void {
        $this->x = $v;
    }
}
"""
        _, edges = _parse_php(src)
        writes = _writes_edges(edges)
        targets = {e["target"] for e in writes}
        assert "Foo.x" in targets, f"Expected Foo.x in write targets; got {targets}"


# ── PHP-FIELD-SYMBOLS: property_declaration → kind='field' symbols ────────────


class TestPHPFieldSymbols:
    """PHP property_declaration → kind='field' symbols."""

    def test_php_property_declaration_becomes_field_symbol(self) -> None:
        """private int $balance → field symbol Account.balance."""
        src = """\
<?php
class Account {
    private int $balance = 0;
    private string $name;
}
"""
        syms, _ = _parse_php(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_php_field_symbol_kind_is_field(self) -> None:
        """PHP field symbol kind must be 'field'."""
        src = """\
<?php
class Foo {
    public int $x = 0;
}
"""
        syms, _ = _parse_php(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert s["kind"] == "field", f"Expected kind='field'; got {s['kind']}"

    def test_php_field_symbol_qualified_name(self) -> None:
        """PHP field symbol qualified_name is 'ClassName.field' (no $ prefix)."""
        src = """\
<?php
class Account {
    public int $balance = 0;
}
"""
        syms, _ = _parse_php(src)
        fields = _field_symbols(syms)
        bal = [s for s in fields if s["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance field symbol; got {[s['name'] for s in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"qualified_name should be 'Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_php_field_symbol_no_dollar_prefix(self) -> None:
        """Field symbol name must NOT include the $ prefix."""
        src = """\
<?php
class Foo {
    public int $value = 0;
}
"""
        syms, _ = _parse_php(src)
        fields = _field_symbols(syms)
        for s in fields:
            assert not s["name"].split(".")[-1].startswith("$"), (
                f"Field name must not start with $; got {s['name']}"
            )

    def test_php_all_field_types_indexed(self) -> None:
        """ALL PHP properties are indexed regardless of type (including primitive int)."""
        src = """\
<?php
class Foo {
    public int $count = 0;
    public string $label = '';
}
"""
        syms, _ = _parse_php(src)
        fields = _field_symbols(syms)
        names = {s["name"] for s in fields}
        assert "Foo.count" in names, f"Expected Foo.count even with primitive type; got {names}"
        assert "Foo.label" in names, f"Expected Foo.label even with primitive type; got {names}"


# ── PHP-CONFIG-OFF: SEAM_FIELD_ACCESS_EDGES=off ───────────────────────────────


class TestPHPFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES='off' → zero PHP field symbols and zero reads/writes edges."""

    def test_php_off_no_reads_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no reads edges from PHP."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
<?php
class Account {
    public int $balance = 0;
    public function get(): int {
        return $this->balance;
    }
}
"""
        _, edges = _parse_php(src)
        reads = _reads_edges(edges)
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_php_off_no_writes_edges(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no writes edges from PHP."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
<?php
class Account {
    public int $balance = 0;
    public function set(int $v): void {
        $this->balance = $v;
    }
}
"""
        _, edges = _parse_php(src)
        writes = _writes_edges(edges)
        assert not writes, f"Expected no writes edges when feature off; got {writes}"

    def test_php_off_no_field_symbols(self, monkeypatch) -> None:
        """When SEAM_FIELD_ACCESS_EDGES='off', no field symbols from PHP."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
<?php
class Account {
    public int $balance = 0;
}
"""
        syms, _ = _parse_php(src)
        fields = _field_symbols(syms)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"
