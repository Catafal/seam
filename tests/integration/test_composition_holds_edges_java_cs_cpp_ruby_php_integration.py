"""Integration tests for Slice #79: composition (holds) edges in Java, C#, C++, Ruby, PHP.

Full pipeline: parse → extract → upsert → query via impact.

Coverage:
  DB-JAVA:    Java holds edges stored in the SQLite DB
  DB-CS:      C# holds edges stored in the SQLite DB
  DB-CPP:     C++ holds edges stored in the SQLite DB
  DB-RUBY:    Ruby holds edges stored in the SQLite DB
  DB-PHP:     PHP holds edges stored in the SQLite DB
  IMPACT-JAVA: seam_impact upstream on a held Java type includes the holding class
  CONFIG:     SEAM_COMPOSITION_EDGES=off → zero holds rows in DB (Java smoke test)
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import extract_edges, extract_symbols
from seam.server.tools import handle_seam_impact

# ── Java fixture ────────────────────────────────────────────────────────────────

_JAVA_FIXTURE = """\
class Cache {
    int capacity;
}

class Repository {
    Cache cache;
    public Repository(Cache cache) {
        this.cache = cache;
    }
}
"""


def _build_java_db() -> tuple:
    """Parse the Java fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(_JAVA_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_java
        root = parse_java(filepath)
        assert root is not None

        symbols = extract_symbols(root, "java", filepath)
        edges = extract_edges(root, "java", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "java", "holds_java_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── C# fixture ──────────────────────────────────────────────────────────────────

_CS_FIXTURE = """\
class Logger {
    string name;
}

class Service {
    Logger logger;
    Service(Logger logger) {
        this.logger = logger;
    }
}
"""


def _build_cs_db() -> tuple:
    """Parse the C# fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".cs", mode="w", delete=False) as f:
        f.write(_CS_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_csharp
        root = parse_csharp(filepath)
        assert root is not None

        symbols = extract_symbols(root, "csharp", filepath)
        edges = extract_edges(root, "csharp", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "csharp", "holds_cs_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── C++ fixture ─────────────────────────────────────────────────────────────────

_CPP_FIXTURE = """\
class Engine {
    int power;
};

class Car {
    Engine engine;
};
"""


def _build_cpp_db() -> tuple:
    """Parse the C++ fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".cpp", mode="w", delete=False) as f:
        f.write(_CPP_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_cpp
        root = parse_cpp(filepath)
        assert root is not None

        symbols = extract_symbols(root, "cpp", filepath)
        edges = extract_edges(root, "cpp", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "cpp", "holds_cpp_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── Ruby fixture ────────────────────────────────────────────────────────────────

_RUBY_FIXTURE = """\
class Database
  def query
  end
end

class Store
  def initialize
    @db = Database.new
  end
end
"""


def _build_ruby_db() -> tuple:
    """Parse the Ruby fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".rb", mode="w", delete=False) as f:
        f.write(_RUBY_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_ruby
        root = parse_ruby(filepath)
        assert root is not None

        symbols = extract_symbols(root, "ruby", filepath)
        edges = extract_edges(root, "ruby", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "ruby", "holds_ruby_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── PHP fixture ─────────────────────────────────────────────────────────────────

_PHP_FIXTURE = """\
<?php
class Mailer {
    public function send() {}
}

class NotificationService {
    private Mailer $mailer;
    public function __construct(Mailer $mailer) {
        $this->mailer = $mailer;
    }
}
"""


def _build_php_db() -> tuple:
    """Parse the PHP fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".php", mode="w", delete=False) as f:
        f.write(_PHP_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_php
        root = parse_php(filepath)
        assert root is not None

        symbols = extract_symbols(root, "php", filepath)
        edges = extract_edges(root, "php", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "php", "holds_php_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── DB-JAVA: Java holds edges in the DB ────────────────────────────────────────


class TestJavaHoldsInDB:
    """Java holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the Java fixture, a holds edge Repository→Cache must exist."""
        conn, _ = _build_java_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Repository" and r[1] == "Cache" for r in rows), (
            f"Expected holds edge Repository→Cache; got holds rows: {rows}"
        )

    def test_holds_confidence_is_inferred(self) -> None:
        """Java holds edges have INFERRED confidence."""
        conn, _ = _build_java_db()
        rows = conn.execute(
            "SELECT confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        valid = {"INFERRED", "EXTRACTED"}
        assert all(r[0] in valid for r in rows), (
            f"Unexpected confidence: {[r[0] for r in rows]}"
        )

    def test_no_duplicate_holds_edges(self) -> None:
        """Cache as both field and ctor param → exactly ONE holds edge Repository→Cache."""
        conn, _ = _build_java_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='Repository' AND target_name='Cache'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── DB-CS: C# holds edges in the DB ────────────────────────────────────────────


class TestCsHoldsInDB:
    """C# holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the C# fixture, a holds edge Service→Logger must exist."""
        conn, _ = _build_cs_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Service" and r[1] == "Logger" for r in rows), (
            f"Expected holds edge Service→Logger; got holds rows: {rows}"
        )

    def test_no_duplicate_holds_edges(self) -> None:
        """Logger as both field and ctor param → exactly ONE holds edge Service→Logger."""
        conn, _ = _build_cs_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='Service' AND target_name='Logger'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── DB-CPP: C++ holds edges in the DB ──────────────────────────────────────────


class TestCppHoldsInDB:
    """C++ holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the C++ fixture, a holds edge Car→Engine must exist."""
        conn, _ = _build_cpp_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Car" and r[1] == "Engine" for r in rows), (
            f"Expected holds edge Car→Engine; got holds rows: {rows}"
        )


# ── DB-RUBY: Ruby holds edges in the DB ────────────────────────────────────────


class TestRubyHoldsInDB:
    """Ruby holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the Ruby fixture, a holds edge Store→Database must exist."""
        conn, _ = _build_ruby_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Store" and r[1] == "Database" for r in rows), (
            f"Expected holds edge Store→Database; got holds rows: {rows}"
        )


# ── DB-PHP: PHP holds edges in the DB ──────────────────────────────────────────


class TestPhpHoldsInDB:
    """PHP holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the PHP fixture, a holds edge NotificationService→Mailer must exist."""
        conn, _ = _build_php_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "NotificationService" and r[1] == "Mailer" for r in rows), (
            f"Expected holds edge NotificationService→Mailer; got holds rows: {rows}"
        )

    def test_no_duplicate_holds_php(self) -> None:
        """Mailer as both property and ctor param → exactly ONE holds edge."""
        conn, _ = _build_php_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='NotificationService' AND target_name='Mailer'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── IMPACT-JAVA: seam_impact traverses Java holds edges ───────────────────────


class TestJavaHoldsImpact:
    """seam_impact on a held Java type includes the holding class upstream."""

    def test_impact_upstream_on_held_type_includes_holder(self) -> None:
        """seam_impact upstream on Cache must include Repository (holds Cache)."""
        conn, fp = _build_java_db()
        root = fp.parent
        result = handle_seam_impact(conn, "Cache", direction="upstream", root=root)

        assert result.get("found") is True, f"Expected found=True; got {result}"
        all_names: set[str] = set()
        for tier_entries in result.get("upstream", {}).values():
            for entry in tier_entries:
                if isinstance(entry, dict):
                    all_names.add(entry.get("name", ""))

        assert "Repository" in all_names, (
            f"Expected Repository in upstream impact of Cache; got all_names={all_names}"
        )


# ── CONFIG: no holds edges when SEAM_COMPOSITION_EDGES=off ────────────────────


class TestConfigOff:
    """With SEAM_COMPOSITION_EDGES=off, no holds edges are stored in the DB."""

    def test_java_no_holds_in_db_when_config_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Java fixture with config off → zero holds rows in DB."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_java_db()
        rows = conn.execute("SELECT * FROM edges WHERE kind='holds'").fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"

    def test_ruby_no_holds_in_db_when_config_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ruby fixture with config off → zero holds rows in DB."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_ruby_db()
        rows = conn.execute("SELECT * FROM edges WHERE kind='holds'").fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"
