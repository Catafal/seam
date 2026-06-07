"""Integration tests for A3 Slice 4: field-access edges + field symbols — Java and C#.

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
"""

import hashlib
import sqlite3
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────


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
# Java integration tests (A3 Slice 4)
# ═════════════════════════════════════════════════════════════════════════════


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
