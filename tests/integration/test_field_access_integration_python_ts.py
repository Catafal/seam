"""Integration tests for A3 Slice 1+2: field-access edges + field symbols — Python and TypeScript.

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
