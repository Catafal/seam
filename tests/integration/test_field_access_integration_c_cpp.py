"""Integration tests for A3 Slice 4: field-access edges + field symbols — C and C++.

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
# C integration tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


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
