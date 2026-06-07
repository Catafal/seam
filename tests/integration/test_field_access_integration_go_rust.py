"""Integration tests for A3 Slice 3: field-access edges + field symbols — Go and Rust.

Go coverage (Slice 3):
  INT-GO-FIELD-SYM: Go field symbols in DB (kind='field', qualified_name='Type.Field')
  INT-GO-READS: Go reads edges in DB
  INT-GO-WRITES: Go writes edges in DB
  INT-GO-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Go field symbols/edges in DB
  INT-GO-CONTEXT: context('Type.Field') returns field_readers/field_writers for Go fixture

Rust coverage (Slice 3):
  INT-RUST-FIELD-SYM: Rust field symbols in DB (kind='field', qualified_name='Struct.field')
  INT-RUST-READS: Rust reads edges in DB
  INT-RUST-WRITES: Rust writes edges in DB
  INT-RUST-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Rust field symbols/edges in DB
  INT-RUST-CONTEXT: context('Type.field') returns field_readers/field_writers for Rust fixture
"""

import hashlib
import sqlite3
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_go_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a Go source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_go

    src_path = tmp_path / "sample.go"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_go(src_path)
    assert root is not None

    symbols = extract_symbols(root, "go", src_path)
    edges = extract_edges(root, "go", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "go", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


def _make_rust_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a Rust source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_rust

    src_path = tmp_path / "sample.rs"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_rust(src_path)
    assert root is not None

    symbols = extract_symbols(root, "rust", src_path)
    edges = extract_edges(root, "rust", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "rust", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


def _field_symbols(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all field symbols from the DB."""
    return conn.execute(
        "SELECT name, kind, qualified_name, start_line FROM symbols WHERE kind='field'"
    ).fetchall()


def _edges_of_kind(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    """Return all edges of a given kind."""
    return conn.execute(
        "SELECT source_name, target_name, kind, line FROM edges WHERE kind=?",
        (kind,),
    ).fetchall()


# ═════════════════════════════════════════════════════════════════════════════
# Go integration tests (A3 Slice 3)
# ═════════════════════════════════════════════════════════════════════════════


# ── INT-GO-FIELD-SYM ─────────────────────────────────────────────────────────


class TestGoFieldSymbolsInDB:
    """Go field symbols are persisted with kind='field' and qualified_name='Type.Field'."""

    def test_go_struct_field_creates_field_symbol(self, tmp_path: Path) -> None:
        """type Account struct { Balance int } → field symbol Account.Balance in DB."""
        src = """\
package p
type Account struct {
    Balance int
    Name string
}
"""
        conn, _ = _make_go_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.Balance" in names, (
            f"Expected Account.Balance field symbol; got {names}"
        )
        assert "Account.Name" in names, (
            f"Expected Account.Name field symbol; got {names}"
        )

    def test_go_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """Go field symbol in DB has kind='field'."""
        src = """\
package p
type Foo struct { X int }
"""
        conn, _ = _make_go_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_go_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """Go field symbol qualified_name is 'StructName.FieldName'."""
        src = """\
package p
type Account struct { Balance int }
"""
        conn, _ = _make_go_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.Balance"]
        assert bal, f"Expected Account.Balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.Balance", (
            f"Expected qualified_name='Account.Balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-GO-READS ──────────────────────────────────────────────────────────────


class TestGoReadsEdgesInDB:
    """Go reads edges stored in the edges table."""

    def test_go_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """r.Balance in a method → reads edge kind='reads' in DB."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        conn, _ = _make_go_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.Balance" and r["source_name"] == "Account.Get"
            for r in reads
        ), f"Expected reads edge Account.Get->Account.Balance; got {[(r['source_name'], r['target_name']) for r in reads]}"

    def test_go_field_read_in_short_var(self, tmp_path: Path) -> None:
        """x := r.Balance → reads edge in DB."""
        src = """\
package p
type Account struct { Balance int }
func process(r *Account) { x := r.Balance; _ = x }
"""
        conn, _ = _make_go_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(r["target_name"] == "Account.Balance" for r in reads), (
            f"Expected reads edge to Account.Balance; got {[(r['source_name'], r['target_name']) for r in reads]}"
        )


# ── INT-GO-WRITES ─────────────────────────────────────────────────────────────


class TestGoWritesEdgesInDB:
    """Go writes edges stored in the edges table."""

    def test_go_plain_assignment_creates_writes_edge(self, tmp_path: Path) -> None:
        """r.Balance = v → writes edge kind='writes' in DB."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Set(v int) { r.Balance = v }
"""
        conn, _ = _make_go_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.Balance" for r in writes), (
            f"Expected writes edge to Account.Balance; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )

    def test_go_aug_assignment_creates_writes_edge(self, tmp_path: Path) -> None:
        """r.Balance += amount → writes edge in DB."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Deposit(amount int) { r.Balance += amount }
"""
        conn, _ = _make_go_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.Balance" for r in writes), (
            f"Expected writes edge for +=; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )

    def test_go_inc_creates_writes_edge(self, tmp_path: Path) -> None:
        """r.Count++ → writes edge in DB."""
        src = """\
package p
type Counter struct { Count int }
func (r *Counter) Inc() { r.Count++ }
"""
        conn, _ = _make_go_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Counter.Count" for r in writes), (
            f"Expected writes edge for ++; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-GO-OFF ────────────────────────────────────────────────────────────────


class TestGoFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no Go field symbols/edges in DB."""

    def test_go_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Go field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
package p
type Account struct { Balance int }
"""
        conn, _ = _make_go_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_go_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Go reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        conn, _ = _make_go_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_go_off_no_writes_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Go writes edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
package p
type Account struct { Balance int }
func (r *Account) Set(v int) { r.Balance = v }
"""
        conn, _ = _make_go_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert not writes, f"Expected no writes edges when feature off; got {writes}"


# ── INT-GO-CONTEXT ────────────────────────────────────────────────────────────


class TestGoContextFieldView:
    """context('Type.Field') returns field_readers/field_writers for Go fixture."""

    def test_go_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.Balance') includes the reading method from Go code."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Get() int { return r.Balance }
"""
        conn, _ = _make_go_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.Balance")
        assert result is not None, "Expected context result for Account.Balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.Get" in result["field_readers"], (
            f"Expected Account.Get in field_readers; got {result['field_readers']}"
        )

    def test_go_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.Balance') includes the writing method from Go code."""
        src = """\
package p
type Account struct { Balance int }
func (r *Account) Set(v int) { r.Balance = v }
"""
        conn, _ = _make_go_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.Balance")
        assert result is not None, "Expected context result for Account.Balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.Set" in result["field_writers"], (
            f"Expected Account.Set in field_writers; got {result['field_writers']}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Rust integration tests (A3 Slice 3)
# ═════════════════════════════════════════════════════════════════════════════


# ── INT-RUST-FIELD-SYM ────────────────────────────────────────────────────────


class TestRustFieldSymbolsInDB:
    """Rust field symbols persisted with kind='field' and qualified_name='Struct.field'."""

    def test_rust_struct_field_creates_field_symbol(self, tmp_path: Path) -> None:
        """struct Account { balance: i64 } → field symbol Account.balance in DB."""
        src = """\
struct Account {
    balance: i64,
    name: String,
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_rust_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """Rust field symbol in DB has kind='field'."""
        src = """\
struct Foo { x: i32 }
"""
        conn, _ = _make_rust_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_rust_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """Rust field symbol qualified_name is 'StructName.field_name'."""
        src = """\
struct Account { balance: i64 }
"""
        conn, _ = _make_rust_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-RUST-READS ────────────────────────────────────────────────────────────


class TestRustReadsEdgesInDB:
    """Rust reads edges stored in the edges table."""

    def test_rust_self_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """self.balance in a method → reads edge kind='reads' in DB."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn get(&self) -> i64 { self.balance }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.get"
            for r in reads
        ), f"Expected reads edge Account.get->Account.balance; got {[(r['source_name'], r['target_name']) for r in reads]}"

    def test_rust_field_read_in_let(self, tmp_path: Path) -> None:
        """let x = self.balance → reads edge in DB."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn show(&self) { let x = self.balance; let _ = x; }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(r["target_name"] == "Account.balance" for r in reads), (
            f"Expected reads edge in let; got {[(r['source_name'], r['target_name']) for r in reads]}"
        )


# ── INT-RUST-WRITES ───────────────────────────────────────────────────────────


class TestRustWritesEdgesInDB:
    """Rust writes edges stored in the edges table."""

    def test_rust_plain_assignment_creates_writes_edge(self, tmp_path: Path) -> None:
        """self.name = name → writes edge in DB."""
        src = """\
struct Account { name: String }
impl Account {
    fn set_name(&mut self, name: String) { self.name = name; }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.name" for r in writes), (
            f"Expected writes edge to Account.name; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )

    def test_rust_compound_assignment_creates_writes_edge(self, tmp_path: Path) -> None:
        """self.balance += amount → writes edge in DB."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn deposit(&mut self, amount: i64) { self.balance += amount; }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.balance" for r in writes), (
            f"Expected writes edge for +=; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-RUST-OFF ──────────────────────────────────────────────────────────────


class TestRustFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no Rust field symbols/edges in DB."""

    def test_rust_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Rust field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { balance: i64 }
"""
        conn, _ = _make_rust_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_rust_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Rust reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { balance: i64 }
impl Account {
    fn get(&self) -> i64 { self.balance }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_rust_off_no_writes_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Rust writes edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { balance: i64 }
impl Account {
    fn set(&mut self, v: i64) { self.balance = v; }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert not writes, f"Expected no writes edges when feature off; got {writes}"


# ── INT-RUST-CONTEXT ──────────────────────────────────────────────────────────


class TestRustContextFieldView:
    """context('Type.field') returns field_readers/field_writers for Rust fixture."""

    def test_rust_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the reading method from Rust code."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn get(&self) -> i64 { self.balance }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.get" in result["field_readers"], (
            f"Expected Account.get in field_readers; got {result['field_readers']}"
        )

    def test_rust_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the writing method from Rust code."""
        src = """\
struct Account { balance: i64 }
impl Account {
    fn deposit(&mut self, amount: i64) { self.balance += amount; }
}
"""
        conn, _ = _make_rust_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.deposit" in result["field_writers"], (
            f"Expected Account.deposit in field_writers; got {result['field_writers']}"
        )
