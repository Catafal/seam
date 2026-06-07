"""Unit tests for A3 Slice 4: field_access.py core — C and C++.

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

# ── Parse helpers ──────────────────────────────────────────────────────────────


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
# C field-access tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


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
