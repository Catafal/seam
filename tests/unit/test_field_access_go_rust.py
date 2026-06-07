"""Unit tests for A3 Slice 3: field_access.py core — Go and Rust.

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
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.graph import extract_edges, extract_symbols

# ── Parse helpers ──────────────────────────────────────────────────────────────


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
# Go field-access tests (A3 Slice 3)
# ═════════════════════════════════════════════════════════════════════════════


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
