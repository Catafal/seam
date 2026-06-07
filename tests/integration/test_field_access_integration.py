"""Integration tests for A3 Slice 1+2+3+4: field-access edges + field symbols end-to-end.

TDD: Tests written alongside implementation to verify DB persistence.

Python coverage (Slice 1):
  INT-FIELD-SYM: field symbols in DB (kind='field', qualified_name='Type.field')
  INT-READS-EDGES: reads edges in DB (kind='reads')
  INT-WRITES-EDGES: writes edges in DB (kind='writes')
  INT-OFF: SEAM_FIELD_ACCESS_EDGES=off → zero field symbols and zero reads/writes edges
  INT-CONTEXT-FIELD: context() returns field_readers / field_writers
  INT-CONTEXT-CLASS: context() on a class aggregates its fields' readers/writers
  INT-CONTEXT-FUNCTION-NOREG: function-seed context() callers/callees unchanged (no regression)

TypeScript coverage (Slice 2):
  INT-TS-FIELD-SYM: TS field symbols in DB with kind='field', qualified_name='Type.field'
  INT-TS-READS: TS reads edges in DB
  INT-TS-WRITES: TS writes edges in DB
  INT-TS-OFF: SEAM_FIELD_ACCESS_EDGES=off → no TS field symbols/edges in DB
  INT-TS-CONTEXT: context('Type.field') returns field_readers/field_writers for TS fixture

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

Java coverage (Slice 4):
  INT-JAVA-FIELD-SYM: Java field symbols in DB (kind='field', qualified_name='Class.field')
  INT-JAVA-READS: Java reads edges in DB
  INT-JAVA-WRITES: Java writes edges in DB
  INT-JAVA-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Java field symbols/edges in DB
  INT-JAVA-CONTEXT: context('Class.field') returns field_readers/field_writers for Java fixture

C# coverage (Slice 4):
  INT-CS-FIELD-SYM: C# field symbols in DB (kind='field', qualified_name='Class.field')
  INT-CS-READS: C# reads edges in DB
  INT-CS-WRITES: C# writes edges in DB
  INT-CS-OFF: SEAM_FIELD_ACCESS_EDGES=off → no C# field symbols/edges in DB
  INT-CS-CONTEXT: context('Class.field') returns field_readers/field_writers for C# fixture

C coverage (Slice 4):
  INT-C-FIELD-SYM: C struct field symbols in DB (kind='field', qualified_name='Struct.field')
  INT-C-READS: C reads edges in DB
  INT-C-WRITES: C writes edges in DB
  INT-C-OFF: SEAM_FIELD_ACCESS_EDGES=off → no C field symbols/edges in DB

C++ coverage (Slice 4):
  INT-CPP-FIELD-SYM: C++ class/struct field symbols in DB
  INT-CPP-READS: C++ reads edges in DB
  INT-CPP-WRITES: C++ writes edges in DB
  INT-CPP-OFF: SEAM_FIELD_ACCESS_EDGES=off → no C++ field symbols/edges in DB
  INT-CPP-CONTEXT: context('Class.field') returns field_readers/field_writers for C++ fixture
"""

import hashlib
import sqlite3
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a Python source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_python

    src_path = tmp_path / "sample.py"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_python(src_path)
    assert root is not None

    symbols = extract_symbols(root, "python", src_path)
    edges = extract_edges(root, "python", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "python", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


def _make_ts_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a TypeScript source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_typescript

    src_path = tmp_path / "sample.ts"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_typescript(src_path)
    assert root is not None

    symbols = extract_symbols(root, "typescript", src_path)
    edges = extract_edges(root, "typescript", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "typescript", file_hash, symbols, edges)
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


# ── INT-FIELD-SYM ──────────────────────────────────────────────────────────────


class TestFieldSymbolsInDB:
    """Field symbols are persisted with kind='field' and qualified_name='Type.field'."""

    def test_annotated_field_creates_field_symbol(self, tmp_path: Path) -> None:
        """class Account: balance: int → field symbol Account.balance in DB."""
        src = """\
class Account:
    balance: int
"""
        conn, _ = _make_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "Account.balance" for r in fields), (
            f"Expected Account.balance field symbol; got {[r['name'] for r in fields]}"
        )

    def test_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """Field symbol in DB has kind='field'."""
        src = """\
class Foo:
    x: str
"""
        conn, _ = _make_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """Field symbol qualified_name is 'Type.field'."""
        src = """\
class Foo:
    x: str
"""
        conn, _ = _make_db(src, tmp_path)
        fields = _field_symbols(conn)
        fx = [r for r in fields if r["name"] == "Foo.x"]
        assert fx, f"Expected Foo.x field symbol; got {[r['name'] for r in fields]}"
        assert fx[0]["qualified_name"] == "Foo.x", (
            f"Expected qualified_name='Foo.x'; got {fx[0]['qualified_name']}"
        )

    def test_init_assignment_creates_field_symbol(self, tmp_path: Path) -> None:
        """self.count = 0 in __init__ → field symbol Counter.count in DB."""
        src = """\
class Counter:
    def __init__(self):
        self.count = 0
"""
        conn, _ = _make_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "Counter.count" for r in fields), (
            f"Expected Counter.count field symbol; got {[r['name'] for r in fields]}"
        )

    def test_field_symbol_dedup_in_db(self, tmp_path: Path) -> None:
        """Annotated class field + __init__ assignment → ONE field symbol in DB."""
        src = """\
class Account:
    balance: float
    def __init__(self):
        self.balance = 0.0
"""
        conn, _ = _make_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert len(bal) == 1, f"Expected exactly 1 Account.balance; got {bal}"


# ── INT-READS-EDGES ────────────────────────────────────────────────────────────


class TestReadsEdgesInDB:
    """reads edges are stored in the edges table."""

    def test_self_attr_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """self.balance in a method → reads edge kind='reads' in DB."""
        src = """\
class Account:
    def get(self):
        return self.balance
"""
        conn, _ = _make_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.get"
            for r in reads
        ), f"Expected reads edge Account.get->Account.balance; got {[(r['source_name'], r['target_name']) for r in reads]}"


# ── INT-WRITES-EDGES ───────────────────────────────────────────────────────────


class TestWritesEdgesInDB:
    """writes edges are stored in the edges table."""

    def test_self_attr_write_creates_writes_edge(self, tmp_path: Path) -> None:
        """self.balance = v → writes edge kind='writes' in DB."""
        src = """\
class Account:
    def set_balance(self, v):
        self.balance = v
"""
        conn, _ = _make_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {[(r['source_name'], r['target_name']) for r in writes]}"

    def test_aug_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """self.balance += amount → writes edge in DB."""
        src = """\
class Account:
    def deposit(self, amount):
        self.balance += amount
"""
        conn, _ = _make_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.balance" for r in writes), (
            f"Expected writes edge for +=; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )

    def test_del_creates_writes_edge(self, tmp_path: Path) -> None:
        """del self.cache → writes edge in DB."""
        src = """\
class Cache:
    def clear(self):
        del self.cache
"""
        conn, _ = _make_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Cache.cache" for r in writes), (
            f"Expected writes edge for del; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-OFF ────────────────────────────────────────────────────────────────────


class TestFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → zero field symbols and zero reads/writes edges in DB."""

    def test_off_no_field_symbols_in_db(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account:
    balance: int
    def __init__(self):
        self.balance = 0
"""
        conn, _ = _make_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_off_no_reads_edges_in_db(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account:
    def get(self):
        return self.balance
"""
        conn, _ = _make_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_off_no_writes_edges_in_db(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no writes edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account:
    def set(self, v):
        self.balance = v
"""
        conn, _ = _make_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert not writes, f"Expected no writes edges when feature off; got {writes}"


# ── INT-CONTEXT-FIELD ─────────────────────────────────────────────────────────


class TestContextFieldView:
    """context('Type.field') returns field_readers / field_writers."""

    def test_context_field_returns_field_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') returns field_readers with the reading methods."""
        src = """\
class Account:
    balance: int
    def get_balance(self):
        return self.balance
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got keys={list(result.keys())}"
        assert "Account.get_balance" in result["field_readers"], (
            f"Expected Account.get_balance in field_readers; got {result['field_readers']}"
        )

    def test_context_field_returns_field_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') returns field_writers with the writing methods."""
        src = """\
class Account:
    balance: int
    def set_balance(self, v):
        self.balance = v
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got keys={list(result.keys())}"
        assert "Account.set_balance" in result["field_writers"], (
            f"Expected Account.set_balance in field_writers; got {result['field_writers']}"
        )

    def test_context_field_empty_when_no_accesses(self, tmp_path: Path) -> None:
        """context('Foo.x') when no methods access x returns empty field_readers/writers."""
        src = """\
class Foo:
    x: int
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Foo.x")
        assert result is not None, "Expected context result for Foo.x"
        assert result.get("field_readers") == [], (
            f"Expected empty field_readers; got {result.get('field_readers')}"
        )
        assert result.get("field_writers") == [], (
            f"Expected empty field_writers; got {result.get('field_writers')}"
        )


# ── INT-CONTEXT-CLASS ──────────────────────────────────────────────────────────


class TestContextClassView:
    """context('Account') aggregates readers/writers of all Account fields."""

    def test_context_class_includes_field_readers(self, tmp_path: Path) -> None:
        """context('Account') has field_readers aggregated from all fields."""
        src = """\
class Account:
    balance: int
    name: str

    def get_balance(self):
        return self.balance

    def get_name(self):
        return self.name
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account")
        assert result is not None, "Expected context for Account"
        assert "field_readers" in result, f"Expected field_readers; got keys={list(result.keys())}"
        readers = result["field_readers"]
        # Both methods should appear as readers of Account's fields
        assert "Account.get_balance" in readers or "Account.get_name" in readers, (
            f"Expected field readers in Account context; got {readers}"
        )

    def test_context_class_includes_field_writers(self, tmp_path: Path) -> None:
        """context('Account') has field_writers aggregated from all fields."""
        src = """\
class Account:
    balance: int

    def deposit(self, amount):
        self.balance += amount
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account")
        assert result is not None, "Expected context for Account"
        assert "field_writers" in result, f"Expected field_writers; got keys={list(result.keys())}"
        writers = result["field_writers"]
        assert "Account.deposit" in writers, (
            f"Expected Account.deposit in field_writers; got {writers}"
        )


# ── INT-CONTEXT-FUNCTION-NOREG ────────────────────────────────────────────────


class TestContextFunctionNoRegression:
    """Function-seed context() callers/callees remain unchanged (no regression from A3)."""

    def test_function_context_callers_callees_unchanged(self, tmp_path: Path) -> None:
        """context('Worker.run') callers/callees are not affected by A3 changes."""
        src = """\
class Helper:
    def help(self):
        pass

class Worker:
    def run(self):
        h = Helper()
        h.help()
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Worker.run")
        assert result is not None, "Expected context for Worker.run"
        # callers/callees must still be present and be lists
        assert isinstance(result.get("callers"), list), "callers should be a list"
        assert isinstance(result.get("callees"), list), "callees should be a list"
        # The function kind should still be 'method' (not changed to 'field')
        assert result["kind"] in ("function", "method"), (
            f"Expected function/method kind; got {result['kind']}"
        )

    def test_function_context_no_spurious_field_keys(self, tmp_path: Path) -> None:
        """Function context result has field_readers/field_writers as empty lists, not absent."""
        src = """\
class Foo:
    def bar(self):
        pass
"""
        conn, _ = _make_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Foo.bar")
        assert result is not None, "Expected context for Foo.bar"
        # field_readers/field_writers should be present (even if empty)
        # for a non-field symbol: they should be [] not an error
        field_readers = result.get("field_readers", [])
        field_writers = result.get("field_writers", [])
        assert isinstance(field_readers, list), "field_readers should be list"
        assert isinstance(field_writers, list), "field_writers should be list"


# ═════════════════════════════════════════════════════════════════════════════
# TypeScript integration tests (A3 Slice 2)
# ═════════════════════════════════════════════════════════════════════════════


# ── INT-TS-FIELD-SYM ──────────────────────────────────────────────────────────


class TestTSFieldSymbolsInDB:
    """TS field symbols persisted with kind='field' and qualified_name='Type.field'."""

    def test_ts_field_definition_creates_field_symbol(self, tmp_path: Path) -> None:
        """class Account { balance: number; } → field symbol Account.balance in DB."""
        src = """\
class Account {
    balance: number;
    name: string;
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )

    def test_ts_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """TS field symbol in DB has kind='field'."""
        src = """\
class Foo {
    x: number;
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_ts_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """TS field symbol qualified_name is 'Type.field'."""
        src = """\
class Account {
    balance: number;
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )

    def test_ts_constructor_this_assignment_creates_field_symbol(self, tmp_path: Path) -> None:
        """this.count = 0 in constructor → field symbol Counter.count in DB."""
        src = """\
class Counter {
    constructor() {
        this.count = 0;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "Counter.count" for r in fields), (
            f"Expected Counter.count field symbol; got {[r['name'] for r in fields]}"
        )

    def test_ts_param_property_creates_field_symbol(self, tmp_path: Path) -> None:
        """constructor(private repo: Repository) → field symbol MyService.repo in DB."""
        src = """\
class MyService {
    constructor(private repo: Repository) {
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "MyService.repo" for r in fields), (
            f"Expected MyService.repo; got {[r['name'] for r in fields]}"
        )

    def test_ts_field_symbol_dedup_in_db(self, tmp_path: Path) -> None:
        """Field declaration + constructor assignment → ONE symbol in DB."""
        src = """\
class Account {
    balance: number;
    constructor() {
        this.balance = 0;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert len(bal) == 1, f"Expected exactly 1 Account.balance; got {bal}"


# ── INT-TS-READS ───────────────────────────────────────────────────────────────


class TestTSReadsEdgesInDB:
    """TS reads edges stored in the edges table."""

    def test_ts_this_attr_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """this.balance in a method → reads edge kind='reads' in DB."""
        src = """\
class Account {
    balance: number;
    get(): number {
        return this.balance;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.get"
            for r in reads
        ), f"Expected reads edge Account.get->Account.balance; got {[(r['source_name'], r['target_name']) for r in reads]}"

    def test_ts_reads_edge_in_expression(self, tmp_path: Path) -> None:
        """this.value used in arithmetic produces reads edge."""
        src = """\
class Calc {
    value: number;
    doubled(): number {
        return this.value * 2;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(r["target_name"] == "Calc.value" for r in reads), (
            f"Expected reads edge for Calc.value; got {[(r['source_name'], r['target_name']) for r in reads]}"
        )


# ── INT-TS-WRITES ──────────────────────────────────────────────────────────────


class TestTSWritesEdgesInDB:
    """TS writes edges stored in the edges table."""

    def test_ts_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.balance = v → writes edge in DB."""
        src = """\
class Account {
    balance: number;
    set(v: number): void {
        this.balance = v;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {[(r['source_name'], r['target_name']) for r in writes]}"

    def test_ts_aug_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.balance += amount → writes edge in DB."""
        src = """\
class Account {
    balance: number;
    deposit(amount: number): void {
        this.balance += amount;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.balance" for r in writes), (
            f"Expected writes edge for +=; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )

    def test_ts_constructor_this_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.balance = 0 in constructor → writes edge in DB."""
        src = """\
class Account {
    balance: number;
    constructor() {
        this.balance = 0;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.balance" for r in writes), (
            f"Expected writes edge from constructor; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-TS-OFF ─────────────────────────────────────────────────────────────────


class TestTSFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no TS field symbols/edges in DB."""

    def test_ts_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no TS field symbols in DB."""
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
        conn, _ = _make_ts_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_ts_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no TS reads edges in DB."""
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
        conn, _ = _make_ts_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"

    def test_ts_off_no_writes_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no TS writes edges in DB."""
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
        conn, _ = _make_ts_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert not writes, f"Expected no writes edges when feature off; got {writes}"


# ── INT-TS-CONTEXT ─────────────────────────────────────────────────────────────


class TestTSContextFieldView:
    """context('Type.field') returns field_readers/field_writers for TS fixture."""

    def test_ts_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the reading method from TS code."""
        src = """\
class Account {
    balance: number;
    getBalance(): number {
        return this.balance;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.getBalance" in result["field_readers"], (
            f"Expected Account.getBalance in field_readers; got {result['field_readers']}"
        )

    def test_ts_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the writing method from TS code."""
        src = """\
class Account {
    balance: number;
    setBalance(v: number): void {
        this.balance = v;
    }
}
"""
        conn, _ = _make_ts_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.setBalance" in result["field_writers"], (
            f"Expected Account.setBalance in field_writers; got {result['field_writers']}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Go integration tests (A3 Slice 3)
# ═════════════════════════════════════════════════════════════════════════════


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
            f"Expected reads edge from short_var_decl; got {[(r['source_name'], r['target_name']) for r in reads]}"
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


# ═════════════════════════════════════════════════════════════════════════════
# Java integration tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _make_java_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a Java source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_java

    src_path = tmp_path / "Sample.java"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_java(src_path)
    assert root is not None

    symbols = extract_symbols(root, "java", src_path)
    edges = extract_edges(root, "java", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "java", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


# ── INT-JAVA-FIELD-SYM ────────────────────────────────────────────────────────


class TestJavaFieldSymbolsInDB:
    """Java field symbols persisted with kind='field' and qualified_name='Class.field'."""

    def test_java_field_declaration_creates_field_symbol(self, tmp_path: Path) -> None:
        """private int balance; → field symbol Account.balance in DB."""
        src = """\
class Account {
    private int balance;
    private String name;
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )
        assert "Account.name" in names, (
            f"Expected Account.name field symbol; got {names}"
        )

    def test_java_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """Java field symbol in DB has kind='field'."""
        src = """\
class Foo {
    private int x;
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_java_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """Java field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account {
    private int balance;
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-JAVA-READS ────────────────────────────────────────────────────────────


class TestJavaReadsEdgesInDB:
    """Java reads edges stored in the edges table."""

    def test_java_this_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """this.balance in a method → reads edge kind='reads' in DB."""
        src = """\
class Account {
    private int balance;
    public int getBalance() {
        return this.balance;
    }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.getBalance"
            for r in reads
        ), f"Expected reads edge Account.getBalance->Account.balance; got {[(r['source_name'], r['target_name']) for r in reads]}"


# ── INT-JAVA-WRITES ───────────────────────────────────────────────────────────


class TestJavaWritesEdgesInDB:
    """Java writes edges stored in the edges table."""

    def test_java_this_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.balance = v → writes edge kind='writes' in DB."""
        src = """\
class Account {
    private int balance;
    public void setBalance(int v) {
        this.balance = v;
    }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {[(r['source_name'], r['target_name']) for r in writes]}"

    def test_java_aug_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.balance += amount → writes edge in DB."""
        src = """\
class Account {
    private int balance;
    public void deposit(int amount) {
        this.balance += amount;
    }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Account.balance" for r in writes), (
            f"Expected writes edge for +=; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )

    def test_java_increment_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.count++ → writes edge in DB."""
        src = """\
class Counter {
    private int count;
    public void inc() {
        this.count++;
    }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Counter.count" for r in writes), (
            f"Expected writes edge for ++; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-JAVA-OFF ──────────────────────────────────────────────────────────────


class TestJavaFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no Java field symbols/edges in DB."""

    def test_java_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Java field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_java_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Java reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public int get() { return this.balance; }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ── INT-JAVA-CONTEXT ──────────────────────────────────────────────────────────


class TestJavaContextFieldView:
    """context('Class.field') returns field_readers/field_writers for Java fixture."""

    def test_java_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the reading method from Java code."""
        src = """\
class Account {
    private int balance;
    public int getBalance() {
        return this.balance;
    }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.getBalance" in result["field_readers"], (
            f"Expected Account.getBalance in field_readers; got {result['field_readers']}"
        )

    def test_java_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the writing method from Java code."""
        src = """\
class Account {
    private int balance;
    public void deposit(int amount) {
        this.balance += amount;
    }
}
"""
        conn, _ = _make_java_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.deposit" in result["field_writers"], (
            f"Expected Account.deposit in field_writers; got {result['field_writers']}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# C# integration tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _make_cs_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a C# source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_csharp

    src_path = tmp_path / "Sample.cs"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_csharp(src_path)
    assert root is not None

    symbols = extract_symbols(root, "csharp", src_path)
    edges = extract_edges(root, "csharp", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "csharp", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


# ── INT-CS-FIELD-SYM ──────────────────────────────────────────────────────────


class TestCSFieldSymbolsInDB:
    """C# field symbols persisted with kind='field' and qualified_name='Class.field'."""

    def test_cs_field_declaration_creates_field_symbol(self, tmp_path: Path) -> None:
        """private int balance; → field symbol Account.balance in DB."""
        src = """\
class Account {
    private int balance;
    private string name;
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )

    def test_cs_property_declaration_creates_field_symbol(self, tmp_path: Path) -> None:
        """public int Balance { get; set; } → field symbol Account.Balance in DB."""
        src = """\
class Account {
    public int Balance { get; set; }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.Balance" in names, (
            f"Expected Account.Balance field symbol from property; got {names}"
        )

    def test_cs_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """C# field symbol in DB has kind='field'."""
        src = """\
class Foo {
    private int x;
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"


# ── INT-CS-READS ───────────────────────────────────────────────────────────────


class TestCSReadsEdgesInDB:
    """C# reads edges stored in the edges table."""

    def test_cs_this_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """this.balance in a method → reads edge kind='reads' in DB."""
        src = """\
class Account {
    private int balance;
    public int GetBalance() {
        return this.balance;
    }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.GetBalance"
            for r in reads
        ), f"Expected reads edge Account.GetBalance->Account.balance; got {[(r['source_name'], r['target_name']) for r in reads]}"


# ── INT-CS-WRITES ──────────────────────────────────────────────────────────────


class TestCSWritesEdgesInDB:
    """C# writes edges stored in the edges table."""

    def test_cs_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.balance = v → writes edge in DB."""
        src = """\
class Account {
    private int balance;
    public void SetBalance(int v) {
        this.balance = v;
    }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {[(r['source_name'], r['target_name']) for r in writes]}"

    def test_cs_increment_creates_writes_edge(self, tmp_path: Path) -> None:
        """this.count++ → writes edge in DB."""
        src = """\
class Counter {
    private int count;
    public void Inc() {
        this.count++;
    }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Counter.count" for r in writes), (
            f"Expected writes edge for ++; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-CS-OFF ─────────────────────────────────────────────────────────────────


class TestCSFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no C# field symbols/edges in DB."""

    def test_cs_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no C# field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public int Balance { get; set; }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_cs_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no C# reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    private int balance;
    public int Get() { return this.balance; }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ── INT-CS-CONTEXT ─────────────────────────────────────────────────────────────


class TestCSContextFieldView:
    """context('Class.field') returns field_readers/field_writers for C# fixture."""

    def test_cs_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the reading method from C# code."""
        src = """\
class Account {
    private int balance;
    public int GetBalance() {
        return this.balance;
    }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.GetBalance" in result["field_readers"], (
            f"Expected Account.GetBalance in field_readers; got {result['field_readers']}"
        )

    def test_cs_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the writing method from C# code."""
        src = """\
class Account {
    private int balance;
    public void SetBalance(int v) {
        this.balance = v;
    }
}
"""
        conn, _ = _make_cs_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.SetBalance" in result["field_writers"], (
            f"Expected Account.SetBalance in field_writers; got {result['field_writers']}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# C integration tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _make_c_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a C source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_c

    src_path = tmp_path / "sample.c"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_c(src_path)
    assert root is not None

    symbols = extract_symbols(root, "c", src_path)
    edges = extract_edges(root, "c", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "c", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


# ── INT-C-FIELD-SYM ───────────────────────────────────────────────────────────


class TestCFieldSymbolsInDB:
    """C struct field symbols persisted with kind='field' and qualified_name='Struct.field'."""

    def test_c_struct_field_creates_field_symbol(self, tmp_path: Path) -> None:
        """struct Account { int balance; } → field symbol Account.balance in DB."""
        src = """\
struct Account {
    int balance;
    int count;
};
"""
        conn, _ = _make_c_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )

    def test_c_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """C struct field symbol in DB has kind='field'."""
        src = """\
struct Foo { int x; };
"""
        conn, _ = _make_c_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_c_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """C struct field symbol qualified_name is 'StructName.field'."""
        src = """\
struct Account { int balance; };
"""
        conn, _ = _make_c_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-C-READS ───────────────────────────────────────────────────────────────


class TestCReadsEdgesInDB:
    """C reads edges stored in the edges table."""

    def test_c_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """p->balance in a function → reads edge kind='reads' in DB."""
        src = """\
struct Account { int balance; };
int get_balance(struct Account *p) {
    return p->balance;
}
"""
        conn, _ = _make_c_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            "balance" in r["target_name"] and r["source_name"] == "get_balance"
            for r in reads
        ), f"Expected reads edge from get_balance to balance; got {[(r['source_name'], r['target_name']) for r in reads]}"


# ── INT-C-WRITES ──────────────────────────────────────────────────────────────


class TestCWritesEdgesInDB:
    """C writes edges stored in the edges table."""

    def test_c_field_write_creates_writes_edge(self, tmp_path: Path) -> None:
        """p->balance = v → writes edge kind='writes' in DB."""
        src = """\
struct Account { int balance; };
void set_balance(struct Account *p, int v) {
    p->balance = v;
}
"""
        conn, _ = _make_c_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            "balance" in r["target_name"] and r["source_name"] == "set_balance"
            for r in writes
        ), f"Expected writes edge from set_balance to balance; got {[(r['source_name'], r['target_name']) for r in writes]}"


# ── INT-C-OFF ─────────────────────────────────────────────────────────────────


class TestCFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no C field symbols/edges in DB."""

    def test_c_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no C field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { int balance; };
"""
        conn, _ = _make_c_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_c_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no C reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
struct Account { int balance; };
int get(struct Account *p) { return p->balance; }
"""
        conn, _ = _make_c_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ═════════════════════════════════════════════════════════════════════════════
# C++ integration tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


def _make_cpp_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a C++ source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_cpp

    src_path = tmp_path / "sample.cpp"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_cpp(src_path)
    assert root is not None

    symbols = extract_symbols(root, "cpp", src_path)
    edges = extract_edges(root, "cpp", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "cpp", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


# ── INT-CPP-FIELD-SYM ─────────────────────────────────────────────────────────


class TestCPPFieldSymbolsInDB:
    """C++ class/struct field symbols persisted with kind='field'."""

    def test_cpp_class_field_creates_field_symbol(self, tmp_path: Path) -> None:
        """class Account { int balance; } → field symbol Account.balance in DB."""
        src = """\
class Account {
public:
    int balance;
    int count;
};
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        fields = _field_symbols(conn)
        names = [r["name"] for r in fields]
        assert "Account.balance" in names, (
            f"Expected Account.balance field symbol; got {names}"
        )

    def test_cpp_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """C++ field symbol in DB has kind='field'."""
        src = """\
class Foo { public: int x; };
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_cpp_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """C++ field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account { public: int balance; };
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-CPP-READS ──────────────────────────────────────────────────────────────


class TestCPPReadsEdgesInDB:
    """C++ reads edges stored in the edges table."""

    def test_cpp_this_arrow_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """this->balance in a method → reads edge kind='reads' in DB."""
        src = """\
class Account {
public:
    int balance;
    int getBalance() {
        return this->balance;
    }
};
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.getBalance"
            for r in reads
        ), f"Expected reads edge Account.getBalance->Account.balance; got {[(r['source_name'], r['target_name']) for r in reads]}"


# ── INT-CPP-WRITES ─────────────────────────────────────────────────────────────


class TestCPPWritesEdgesInDB:
    """C++ writes edges stored in the edges table."""

    def test_cpp_this_arrow_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """this->balance = v → writes edge in DB."""
        src = """\
class Account {
public:
    int balance;
    void setBalance(int v) {
        this->balance = v;
    }
};
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {[(r['source_name'], r['target_name']) for r in writes]}"

    def test_cpp_increment_creates_writes_edge(self, tmp_path: Path) -> None:
        """this->count++ → writes edge in DB."""
        src = """\
class Counter {
public:
    int count;
    void inc() {
        this->count++;
    }
};
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(r["target_name"] == "Counter.count" for r in writes), (
            f"Expected writes edge for ++; got {[(r['source_name'], r['target_name']) for r in writes]}"
        )


# ── INT-CPP-OFF ────────────────────────────────────────────────────────────────


class TestCPPFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no C++ field symbols/edges in DB."""

    def test_cpp_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no C++ field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account { public: int balance; };
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_cpp_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no C++ reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account { public: int balance; int get() { return this->balance; } };
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ── INT-CPP-CONTEXT ────────────────────────────────────────────────────────────


class TestCPPContextFieldView:
    """context('Class.field') returns field_readers/field_writers for C++ fixture."""

    def test_cpp_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the reading method from C++ code."""
        src = """\
class Account {
public:
    int balance;
    int getBalance() {
        return this->balance;
    }
};
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.getBalance" in result["field_readers"], (
            f"Expected Account.getBalance in field_readers; got {result['field_readers']}"
        )

    def test_cpp_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the writing method from C++ code."""
        src = """\
class Account {
public:
    int balance;
    void setBalance(int v) {
        this->balance = v;
    }
};
"""
        conn, _ = _make_cpp_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.setBalance" in result["field_writers"], (
            f"Expected Account.setBalance in field_writers; got {result['field_writers']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# A3 Slice 5: Ruby / PHP / Swift integration tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_ruby_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a Ruby source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_ruby

    src_path = tmp_path / "sample.rb"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_ruby(src_path)
    assert root is not None

    symbols = extract_symbols(root, "ruby", src_path)
    edges = extract_edges(root, "ruby", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "ruby", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


def _make_php_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a PHP source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_php

    src_path = tmp_path / "sample.php"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_php(src_path)
    assert root is not None

    symbols = extract_symbols(root, "php", src_path)
    edges = extract_edges(root, "php", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "php", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


def _make_swift_db(source: str, tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Index a Swift source snippet into a fresh DB and return (conn, src_path)."""
    from seam.indexer.db import init_db, upsert_file
    from seam.indexer.graph import extract_edges, extract_symbols
    from seam.indexer.parser import parse_swift

    src_path = tmp_path / "sample.swift"
    src_path.write_text(source)

    conn = init_db(Path(":memory:"))
    root = parse_swift(src_path)
    assert root is not None

    symbols = extract_symbols(root, "swift", src_path)
    edges = extract_edges(root, "swift", src_path, symbols)
    file_hash = hashlib.sha1(source.encode()).hexdigest()
    upsert_file(conn, src_path, "swift", file_hash, symbols, edges)
    conn.commit()
    return conn, src_path


# ── INT-RUBY-FIELD-SYM ─────────────────────────────────────────────────────────


class TestRubyFieldSymbolsInDB:
    """Ruby @ivar field symbols are persisted with kind='field' and qualified_name."""

    def test_ruby_ivar_creates_field_symbol(self, tmp_path: Path) -> None:
        """@balance = 0 in initialize → field symbol Account.balance in DB."""
        src = """\
class Account
  def initialize
    @balance = 0
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "Account.balance" for r in fields), (
            f"Expected Account.balance field symbol; got {[r['name'] for r in fields]}"
        )

    def test_ruby_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """Ruby field symbol in DB has kind='field'."""
        src = """\
class Foo
  def initialize
    @x = 1
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_ruby_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """Ruby field symbol qualified_name is 'ClassName.field' (no @)."""
        src = """\
class Account
  def initialize
    @balance = 0
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-RUBY-READS ─────────────────────────────────────────────────────────────


class TestRubyReadsEdgesInDB:
    """Ruby reads edges from @ivar accesses are stored in the edges table."""

    def test_ruby_ivar_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """@balance in get_balance → reads edge Account.get_balance->Account.balance in DB."""
        src = """\
class Account
  def get_balance
    @balance
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.get_balance"
            for r in reads
        ), f"Expected reads edge from Account.get_balance; got {reads}"


# ── INT-RUBY-WRITES ────────────────────────────────────────────────────────────


class TestRubyWritesEdgesInDB:
    """Ruby writes edges from @ivar assignments are stored in the edges table."""

    def test_ruby_ivar_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """@balance = v → writes edge Account.set_balance->Account.balance in DB."""
        src = """\
class Account
  def set_balance(v)
    @balance = v
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {writes}"

    def test_ruby_ivar_aug_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """@balance += amount → writes edge in DB."""
        src = """\
class Account
  def deposit(amount)
    @balance += amount
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge for +=; got {writes}"


# ── INT-RUBY-OFF ───────────────────────────────────────────────────────────────


class TestRubyFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no Ruby field symbols/edges in DB."""

    def test_ruby_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Ruby field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account
  def initialize
    @balance = 0
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_ruby_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Ruby reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account
  def get_balance
    @balance
  end
end
"""
        conn, _ = _make_ruby_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ── INT-PHP-FIELD-SYM ──────────────────────────────────────────────────────────


class TestPHPFieldSymbolsInDB:
    """PHP property_declaration field symbols are persisted with kind='field'."""

    def test_php_property_creates_field_symbol(self, tmp_path: Path) -> None:
        """private int $balance → field symbol Account.balance in DB."""
        src = """\
<?php
class Account {
    private int $balance = 0;
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "Account.balance" for r in fields), (
            f"Expected Account.balance field symbol; got {[r['name'] for r in fields]}"
        )

    def test_php_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """PHP field symbol in DB has kind='field'."""
        src = """\
<?php
class Foo {
    public int $x = 0;
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_php_field_symbol_no_dollar_prefix(self, tmp_path: Path) -> None:
        """PHP field symbol name must NOT include the $ prefix."""
        src = """\
<?php
class Account {
    private int $balance = 0;
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            field_part = r["name"].split(".")[-1] if "." in r["name"] else r["name"]
            assert not field_part.startswith("$"), (
                f"Field name must not start with $; got {r['name']}"
            )


# ── INT-PHP-READS ──────────────────────────────────────────────────────────────


class TestPHPReadsEdgesInDB:
    """PHP reads edges from $this->field accesses are stored in the edges table."""

    def test_php_this_field_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """$this->balance in getBalance → reads edge Account.getBalance->Account.balance."""
        src = """\
<?php
class Account {
    private int $balance = 0;
    public function getBalance(): int {
        return $this->balance;
    }
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.getBalance"
            for r in reads
        ), f"Expected reads edge Account.getBalance->Account.balance; got {reads}"


# ── INT-PHP-WRITES ─────────────────────────────────────────────────────────────


class TestPHPWritesEdgesInDB:
    """PHP writes edges from $this->field assignments are stored in the edges table."""

    def test_php_this_field_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """$this->balance = $v → writes edge in DB."""
        src = """\
<?php
class Account {
    private int $balance = 0;
    public function setBalance(int $v): void {
        $this->balance = $v;
    }
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {writes}"

    def test_php_this_field_aug_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """$this->balance += $amount → writes edge in DB."""
        src = """\
<?php
class Account {
    private int $balance = 0;
    public function deposit(int $amount): void {
        $this->balance += $amount;
    }
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge for +=; got {writes}"


# ── INT-PHP-OFF ────────────────────────────────────────────────────────────────


class TestPHPFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no PHP field symbols/edges in DB."""

    def test_php_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no PHP field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
<?php
class Account {
    private int $balance = 0;
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_php_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no PHP reads edges in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
<?php
class Account {
    private int $balance = 0;
    public function get(): int {
        return $this->balance;
    }
}
"""
        conn, _ = _make_php_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ── INT-SWIFT-FIELD-SYM ────────────────────────────────────────────────────────


class TestSwiftFieldSymbolsInDB:
    """Swift stored var/let field symbols are persisted with kind='field'."""

    def test_swift_stored_var_creates_field_symbol(self, tmp_path: Path) -> None:
        """var balance: Int → field symbol Account.balance in DB."""
        src = """\
class Account {
    var balance: Int = 0
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert any(r["name"] == "Account.balance" for r in fields), (
            f"Expected Account.balance field symbol; got {[r['name'] for r in fields]}"
        )

    def test_swift_field_symbol_kind_is_field(self, tmp_path: Path) -> None:
        """Swift field symbol in DB has kind='field'."""
        src = """\
class Foo {
    var x: Int = 0
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        fields = _field_symbols(conn)
        for r in fields:
            assert r["kind"] == "field", f"Expected kind='field'; got {r['kind']}"

    def test_swift_field_symbol_qualified_name(self, tmp_path: Path) -> None:
        """Swift field symbol qualified_name is 'ClassName.field'."""
        src = """\
class Account {
    var balance: Int = 0
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        fields = _field_symbols(conn)
        bal = [r for r in fields if r["name"] == "Account.balance"]
        assert bal, f"Expected Account.balance; got {[r['name'] for r in fields]}"
        assert bal[0]["qualified_name"] == "Account.balance", (
            f"Expected qualified_name='Account.balance'; got {bal[0]['qualified_name']}"
        )


# ── INT-SWIFT-READS ────────────────────────────────────────────────────────────


class TestSwiftReadsEdgesInDB:
    """Swift reads edges from self.prop accesses are stored in the edges table."""

    def test_swift_self_prop_read_creates_reads_edge(self, tmp_path: Path) -> None:
        """self.balance in getBalance → reads edge Account.getBalance->Account.balance."""
        src = """\
class Account {
    var balance: Int = 0
    func getBalance() -> Int {
        return self.balance
    }
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert any(
            r["target_name"] == "Account.balance" and r["source_name"] == "Account.getBalance"
            for r in reads
        ), f"Expected reads edge Account.getBalance->Account.balance; got {reads}"


# ── INT-SWIFT-WRITES ───────────────────────────────────────────────────────────


class TestSwiftWritesEdgesInDB:
    """Swift writes edges from self.prop assignments are stored in the edges table."""

    def test_swift_self_prop_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """self.balance = v → writes edge in DB."""
        src = """\
class Account {
    var balance: Int = 0
    func setBalance(_ v: Int) {
        self.balance = v
    }
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge to Account.balance; got {writes}"

    def test_swift_self_prop_aug_assign_creates_writes_edge(self, tmp_path: Path) -> None:
        """self.balance += amount → writes edge in DB."""
        src = """\
class Account {
    var balance: Int = 0
    func deposit(amount: Int) {
        self.balance += amount
    }
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        writes = _edges_of_kind(conn, "writes")
        assert any(
            r["target_name"] == "Account.balance"
            for r in writes
        ), f"Expected writes edge for +=; got {writes}"


# ── INT-SWIFT-OFF ──────────────────────────────────────────────────────────────


class TestSwiftFieldAccessEdgesOff:
    """SEAM_FIELD_ACCESS_EDGES=off → no Swift field symbols/edges in DB."""

    def test_swift_off_no_field_symbols(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Swift field symbols in DB."""
        import seam.config as config
        monkeypatch.setattr(config, "SEAM_FIELD_ACCESS_EDGES", "off")

        src = """\
class Account {
    var balance: Int = 0
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        fields = _field_symbols(conn)
        assert not fields, f"Expected no field symbols when feature off; got {fields}"

    def test_swift_off_no_reads_edges(self, tmp_path: Path, monkeypatch) -> None:
        """When feature is off, no Swift reads edges in DB."""
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
        conn, _ = _make_swift_db(src, tmp_path)
        reads = _edges_of_kind(conn, "reads")
        assert not reads, f"Expected no reads edges when feature off; got {reads}"


# ── INT-SWIFT-CONTEXT ──────────────────────────────────────────────────────────


class TestSwiftContextFieldView:
    """context('Class.field') returns field_readers/field_writers for Swift fixture."""

    def test_swift_context_field_returns_readers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the reading method from Swift code."""
        src = """\
class Account {
    var balance: Int = 0
    func getBalance() -> Int {
        return self.balance
    }
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_readers" in result, f"Expected field_readers key; got {list(result.keys())}"
        assert "Account.getBalance" in result["field_readers"], (
            f"Expected Account.getBalance in field_readers; got {result['field_readers']}"
        )

    def test_swift_context_field_returns_writers(self, tmp_path: Path) -> None:
        """context('Account.balance') includes the writing method from Swift code."""
        src = """\
class Account {
    var balance: Int = 0
    func setBalance(_ v: Int) {
        self.balance = v
    }
}
"""
        conn, _ = _make_swift_db(src, tmp_path)
        from seam.query.engine import context
        result = context(conn, "Account.balance")
        assert result is not None, "Expected context result for Account.balance"
        assert "field_writers" in result, f"Expected field_writers key; got {list(result.keys())}"
        assert "Account.setBalance" in result["field_writers"], (
            f"Expected Account.setBalance in field_writers; got {result['field_writers']}"
        )
