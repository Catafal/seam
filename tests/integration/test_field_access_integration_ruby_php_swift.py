"""Integration tests for A3 Slice 5: field-access edges + field symbols — Ruby, PHP, Swift.

Ruby coverage:
  INT-RUBY-FIELD-SYM: Ruby @ivar field symbols in DB
  INT-RUBY-READS: Ruby reads edges in DB
  INT-RUBY-WRITES: Ruby writes edges in DB
  INT-RUBY-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Ruby field symbols/edges in DB

PHP coverage:
  INT-PHP-FIELD-SYM: PHP property_declaration field symbols in DB
  INT-PHP-READS: PHP reads edges in DB
  INT-PHP-WRITES: PHP writes edges in DB
  INT-PHP-OFF: SEAM_FIELD_ACCESS_EDGES=off → no PHP field symbols/edges in DB

Swift coverage:
  INT-SWIFT-FIELD-SYM: Swift stored var/let field symbols in DB
  INT-SWIFT-READS: Swift reads edges in DB
  INT-SWIFT-WRITES: Swift writes edges in DB
  INT-SWIFT-OFF: SEAM_FIELD_ACCESS_EDGES=off → no Swift field symbols/edges in DB
  INT-SWIFT-CONTEXT: context('Class.field') returns field_readers/field_writers for Swift fixture
"""

import hashlib
import sqlite3
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────


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
# Ruby integration tests
# ═════════════════════════════════════════════════════════════════════════════


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


# ═════════════════════════════════════════════════════════════════════════════
# PHP integration tests
# ═════════════════════════════════════════════════════════════════════════════


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


# ═════════════════════════════════════════════════════════════════════════════
# Swift integration tests
# ═════════════════════════════════════════════════════════════════════════════


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
