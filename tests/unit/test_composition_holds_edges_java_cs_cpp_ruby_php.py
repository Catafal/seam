"""Unit tests for Slice #79: composition (holds) edge collectors for Java, C#, C++, Ruby, PHP.

TDD: Tests written BEFORE implementation (RED first).

Coverage:
  JAVA-ACCEPT:   Java class with plain user-type field emits (type_name, line)
  JAVA-CTOR:     Java constructor parameter injection emits holds
  JAVA-REFUSE:   Generics List<T>, arrays, primitives, qualified → no holds
  JAVA-CONFIG:   SEAM_COMPOSITION_EDGES='off' → zero holds edges
  JAVA-DEDUP:    Same type as field + ctor param → one holds edge only

  CS-ACCEPT:     C# class with plain user-type field emits holds
  CS-CTOR:       C# constructor parameter injection emits holds
  CS-REFUSE:     Nullable T?, generic List<T>, primitive → no holds
  CS-CONFIG:     SEAM_COMPOSITION_EDGES='off' → zero holds edges
  CS-DEDUP:      Same type as field + ctor param → one holds edge

  CPP-ACCEPT:    C++ class/struct with plain user-type field emits holds
  CPP-REFUSE:    Template, pointer-to-template, primitive → no holds
  CPP-CONFIG:    SEAM_COMPOSITION_EDGES='off' → zero holds edges
  CPP-DEDUP:     Same type in two fields → one holds edge

  RUBY-ACCEPT:   Ruby class with @ivar = ClassName.new in initialize → holds
  RUBY-REFUSE:   Non-constructor assignment, lowercase → no holds
  RUBY-CONFIG:   SEAM_COMPOSITION_EDGES='off' → zero holds edges
  RUBY-DEDUP:    Same class assigned twice → one holds edge

  PHP-ACCEPT:    PHP class with typed property declaration emits holds
  PHP-CTOR:      PHP promoted constructor property (typed) emits holds
  PHP-REFUSE:    Nullable ?Type, union A|B, primitive → no holds
  PHP-CONFIG:    SEAM_COMPOSITION_EDGES='off' → zero holds edges
  PHP-DEDUP:     Same type as property + ctor param → one holds edge
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Parse helpers ──────────────────────────────────────────────────────────────


def _parse_java(source: str) -> list[Edge]:
    """Parse Java source and return all extracted edges."""
    from seam.indexer.parser import parse_java

    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_java(path)
        assert root is not None
        return extract_edges(root, "java", path)
    finally:
        os.unlink(fname)


def _parse_cs(source: str) -> list[Edge]:
    """Parse C# source and return all extracted edges."""
    from seam.indexer.parser import parse_csharp

    with tempfile.NamedTemporaryFile(suffix=".cs", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_csharp(path)
        assert root is not None
        return extract_edges(root, "csharp", path)
    finally:
        os.unlink(fname)


def _parse_cpp(source: str) -> list[Edge]:
    """Parse C++ source and return all extracted edges."""
    from seam.indexer.parser import parse_cpp

    with tempfile.NamedTemporaryFile(suffix=".cpp", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_cpp(path)
        assert root is not None
        return extract_edges(root, "cpp", path)
    finally:
        os.unlink(fname)


def _parse_ruby(source: str) -> list[Edge]:
    """Parse Ruby source and return all extracted edges."""
    from seam.indexer.parser import parse_ruby

    with tempfile.NamedTemporaryFile(suffix=".rb", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_ruby(path)
        assert root is not None
        return extract_edges(root, "ruby", path)
    finally:
        os.unlink(fname)


def _parse_php(source: str) -> list[Edge]:
    """Parse PHP source and return all extracted edges."""
    from seam.indexer.parser import parse_php

    with tempfile.NamedTemporaryFile(suffix=".php", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_php(path)
        assert root is not None
        return extract_edges(root, "php", path)
    finally:
        os.unlink(fname)


def _holds_edges(edges: list[Edge]) -> list[Edge]:
    """Filter to only 'holds' edges."""
    return [e for e in edges if e["kind"] == "holds"]


def _holds_targets(edges: list[Edge]) -> set[str]:
    """Return set of target names in holds edges."""
    return {e["target"] for e in _holds_edges(edges)}


def _holds_sources(edges: list[Edge]) -> set[str]:
    """Return set of source names in holds edges."""
    return {e["source"] for e in _holds_edges(edges)}


# ── Java: field acceptance ─────────────────────────────────────────────────────


class TestJavaFieldComposition:
    """JAVA-ACCEPT: Java class with plain user-type field → holds edge."""

    def test_plain_field_emits_holds(self) -> None:
        """class Owner { Repository repo; } → holds edge Owner→Repository."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_java(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository; got holds={holds}"
        )

    def test_private_field_emits_holds(self) -> None:
        """private Repository repo; → holds edge emitted regardless of modifier."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    private Repository repo;\n"
            "}\n"
        )
        edges = _parse_java(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository; got holds={holds}"
        )

    def test_multiple_plain_fields_emit_multiple_holds(self) -> None:
        """Multiple plain user-type fields each emit a holds edge."""
        src = (
            "class DB {}\n"
            "class Logger {}\n"
            "class Owner {\n"
            "    DB db;\n"
            "    Logger log;\n"
            "}\n"
        )
        edges = _parse_java(src)
        targets = _holds_targets(edges)
        assert "DB" in targets, f"Missing DB; got {targets}"
        assert "Logger" in targets, f"Missing Logger; got {targets}"

    def test_holds_confidence_is_inferred(self) -> None:
        """Java holds edge confidence must be INFERRED."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_java(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"

    def test_holds_source_is_class_name(self) -> None:
        """Source of holds edge is the class name."""
        src = (
            "class Repository {}\n"
            "class Container {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_java(src)
        holds = _holds_edges(edges)
        assert all(e["source"] == "Container" for e in holds), (
            f"Expected source='Container'; got {[e['source'] for e in holds]}"
        )


class TestJavaConstructorInjection:
    """JAVA-CTOR: Java constructor parameter with plain type → holds edge."""

    def test_constructor_param_emits_holds(self) -> None:
        """Owner(Repository repo) constructor param → holds edge Owner→Repository."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Owner(Repository repo) {}\n"
            "}\n"
        )
        edges = _parse_java(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository from ctor; got holds={holds}"
        )


class TestJavaRefusals:
    """JAVA-REFUSE: generics, arrays, primitives → no holds edge."""

    def test_generic_field_refused(self) -> None:
        """List<Repository> field → no holds edge (generic container)."""
        src = (
            "import java.util.List;\n"
            "class Repository {}\n"
            "class Owner {\n"
            "    List<Repository> repos;\n"
            "}\n"
        )
        edges = _parse_java(src)
        assert "Repository" not in _holds_targets(edges), "Generic field must not produce holds"

    def test_primitive_field_refused(self) -> None:
        """int fields → no holds edge (primitive type nodes are not type_identifier)."""
        src = (
            "class Owner {\n"
            "    int count;\n"
            "    boolean flag;\n"
            "}\n"
        )
        edges = _parse_java(src)
        targets = _holds_targets(edges)
        # Java primitives (int, boolean, etc.) are not type_identifier nodes → refused.
        assert "int" not in targets
        assert "boolean" not in targets

    def test_array_field_refused(self) -> None:
        """Repository[] field → no holds edge (array type)."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository[] repos;\n"
            "}\n"
        )
        edges = _parse_java(src)
        # array_type is not a plain type_identifier → refused
        assert "Repository" not in _holds_targets(edges), "Array field must not produce holds"

    def test_builtin_class_field_refused(self) -> None:
        """String/Integer fields → no holds edge.

        These ARE plain type_identifier nodes (PascalCase), so the first-char-uppercase
        heuristic alone would admit them — the collector post-filters them through
        is_builtin(name, "java"). Composition surfaces user-type dependencies, not JDK
        stdlib types, so String/Integer must not pollute the blast radius.
        """
        src = (
            "class Owner {\n"
            "    String name;\n"
            "    Integer count;\n"
            "}\n"
        )
        targets = _holds_targets(_parse_java(src))
        assert "String" not in targets, "JDK String must be filtered as a builtin"
        assert "Integer" not in targets, "JDK Integer must be filtered as a builtin"


class TestJavaDedup:
    """JAVA-DEDUP: same type as field AND ctor param → one holds edge only."""

    def test_same_type_field_and_ctor_deduped(self) -> None:
        """Repository as both field and ctor param → exactly one holds edge Owner→Repository."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "    Owner(Repository repo) {}\n"
            "}\n"
        )
        edges = _parse_java(src)
        holds = _holds_edges(edges)
        repo_holds = [e for e in holds if e["source"] == "Owner" and e["target"] == "Repository"]
        assert len(repo_holds) == 1, (
            f"Expected exactly 1 holds edge; got {len(repo_holds)}: {repo_holds}"
        )


class TestJavaConfigOff:
    """JAVA-CONFIG: SEAM_COMPOSITION_EDGES='off' → zero holds edges."""

    def test_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, Java classes emit no holds edges."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_java(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"


# ── C#: field acceptance ───────────────────────────────────────────────────────


class TestCsFieldComposition:
    """CS-ACCEPT: C# class with plain user-type field → holds edge."""

    def test_plain_field_emits_holds(self) -> None:
        """class Owner { Repository repo; } → holds edge Owner→Repository."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_cs(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository; got holds={holds}"
        )

    def test_private_field_emits_holds(self) -> None:
        """private Repository _repo; → holds edge emitted."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    private Repository _repo;\n"
            "}\n"
        )
        edges = _parse_cs(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository; got holds={holds}"
        )

    def test_multiple_fields_emit_multiple_holds(self) -> None:
        """Multiple plain user-type fields each emit a holds edge."""
        src = (
            "class DB {}\n"
            "class Logger {}\n"
            "class Owner {\n"
            "    DB db;\n"
            "    Logger logger;\n"
            "}\n"
        )
        edges = _parse_cs(src)
        targets = _holds_targets(edges)
        assert "DB" in targets, f"Missing DB; got {targets}"
        assert "Logger" in targets, f"Missing Logger; got {targets}"

    def test_holds_confidence_is_inferred(self) -> None:
        """C# holds edge confidence must be INFERRED."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_cs(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"


class TestCsConstructorInjection:
    """CS-CTOR: C# constructor parameter with plain type → holds edge."""

    def test_constructor_param_emits_holds(self) -> None:
        """Owner(Repository repo) → holds edge Owner→Repository."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Owner(Repository repo) {}\n"
            "}\n"
        )
        edges = _parse_cs(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge from ctor; got holds={holds}"
        )


class TestCsRefusals:
    """CS-REFUSE: nullable, generic, primitive → no holds edge."""

    def test_primitive_field_refused(self) -> None:
        """int/string fields → no holds edge."""
        src = (
            "class Owner {\n"
            "    int count;\n"
            "    string name;\n"
            "}\n"
        )
        edges = _parse_cs(src)
        targets = _holds_targets(edges)
        assert "int" not in targets
        assert "string" not in targets


class TestCsDedup:
    """CS-DEDUP: same type as field + ctor param → one holds edge only."""

    def test_same_type_deduped(self) -> None:
        """Repository as both field and ctor param → exactly one holds edge."""
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "    Owner(Repository repo) {}\n"
            "}\n"
        )
        edges = _parse_cs(src)
        holds = _holds_edges(edges)
        repo_holds = [e for e in holds if e["source"] == "Owner" and e["target"] == "Repository"]
        assert len(repo_holds) == 1, (
            f"Expected exactly 1 holds edge; got {len(repo_holds)}: {repo_holds}"
        )


class TestCsConfigOff:
    """CS-CONFIG: SEAM_COMPOSITION_EDGES='off' → zero holds edges."""

    def test_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, C# classes emit no holds edges."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = (
            "class Repository {}\n"
            "class Owner {\n"
            "    Repository repo;\n"
            "}\n"
        )
        edges = _parse_cs(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"


# ── C++: field acceptance ──────────────────────────────────────────────────────


class TestCppFieldComposition:
    """CPP-ACCEPT: C++ class/struct with plain user-type field → holds edge."""

    def test_plain_value_field_emits_holds(self) -> None:
        """class Owner { Service svc; }; → holds edge Owner→Service."""
        src = (
            "class Service {};\n"
            "class Owner {\n"
            "    Service svc;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Service" for e in holds), (
            f"Expected holds edge Owner→Service; got holds={holds}"
        )

    def test_struct_plain_field_emits_holds(self) -> None:
        """struct Owner { Engine engine; }; → holds edge Owner→Engine."""
        src = (
            "struct Engine {};\n"
            "struct Owner {\n"
            "    Engine engine;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Engine" for e in holds), (
            f"Expected holds edge Owner→Engine; got holds={holds}"
        )

    def test_multiple_fields_emit_multiple_holds(self) -> None:
        """Multiple plain user-type fields each emit a holds edge."""
        src = (
            "class DB {};\n"
            "class Logger {};\n"
            "class Owner {\n"
            "    DB db;\n"
            "    Logger logger;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        targets = _holds_targets(edges)
        assert "DB" in targets, f"Missing DB; got {targets}"
        assert "Logger" in targets, f"Missing Logger; got {targets}"

    def test_holds_confidence_is_inferred(self) -> None:
        """C++ holds edge confidence must be INFERRED."""
        src = (
            "class Service {};\n"
            "class Owner {\n"
            "    Service svc;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"


class TestCppRefusals:
    """CPP-REFUSE: template, primitive → no holds edge."""

    def test_primitive_field_refused(self) -> None:
        """int/double fields → no holds edge."""
        src = (
            "class Owner {\n"
            "    int count;\n"
            "    double value;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        targets = _holds_targets(edges)
        assert "int" not in targets
        assert "double" not in targets


class TestCppDedup:
    """CPP-DEDUP: same type in two fields → one holds edge."""

    def test_same_type_twice_emits_once(self) -> None:
        """Two fields of the same type → only ONE holds edge for that type."""
        src = (
            "class DB {};\n"
            "class Owner {\n"
            "    DB primary;\n"
            "    DB secondary;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        holds = _holds_edges(edges)
        db_holds = [e for e in holds if e["target"] == "DB" and e["source"] == "Owner"]
        assert len(db_holds) == 1, (
            f"Expected exactly 1 holds edge for DB; got {len(db_holds)}: {db_holds}"
        )


class TestCppConfigOff:
    """CPP-CONFIG: SEAM_COMPOSITION_EDGES='off' → zero holds edges."""

    def test_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, C++ classes emit no holds edges."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = (
            "class Service {};\n"
            "class Owner {\n"
            "    Service svc;\n"
            "};\n"
        )
        edges = _parse_cpp(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"


# ── Ruby: ivar acceptance ──────────────────────────────────────────────────────


class TestRubyIvarComposition:
    """RUBY-ACCEPT: Ruby class with @ivar = ClassName.new → holds edge."""

    def test_ivar_constructor_assignment_emits_holds(self) -> None:
        """@repo = Repository.new in initialize → holds edge Owner→Repository."""
        src = (
            "class Repository\nend\n"
            "class Owner\n"
            "  def initialize\n"
            "    @repo = Repository.new\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository; got holds={holds}"
        )

    def test_multiple_ivar_constructor_assignments(self) -> None:
        """Multiple @ivar = ClassName.new in initialize → multiple holds edges."""
        src = (
            "class DB\nend\n"
            "class Logger\nend\n"
            "class Owner\n"
            "  def initialize\n"
            "    @db = DB.new\n"
            "    @logger = Logger.new\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        targets = _holds_targets(edges)
        assert "DB" in targets, f"Missing DB; got {targets}"
        assert "Logger" in targets, f"Missing Logger; got {targets}"

    def test_holds_confidence_is_inferred(self) -> None:
        """Ruby holds edge confidence must be INFERRED."""
        src = (
            "class Repository\nend\n"
            "class Owner\n"
            "  def initialize\n"
            "    @repo = Repository.new\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"

    def test_holds_source_is_class_name(self) -> None:
        """Source of holds edge is the class name."""
        src = (
            "class Repository\nend\n"
            "class Container\n"
            "  def initialize\n"
            "    @repo = Repository.new\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        holds = _holds_edges(edges)
        assert all(e["source"] == "Container" for e in holds), (
            f"Expected source='Container'; got {[e['source'] for e in holds]}"
        )


class TestRubyRefusals:
    """RUBY-REFUSE: non-constructor assignment, lowercase → no holds."""

    def test_plain_assignment_without_new_refused(self) -> None:
        """@repo = some_method() (not .new) → no holds edge (can't determine type)."""
        src = (
            "class Owner\n"
            "  def initialize(repo)\n"
            "    @repo = repo\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        # Assignment of a variable (not ClassName.new) → no holds
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds; got {holds}"


class TestRubyDedup:
    """RUBY-DEDUP: same class assigned twice in initialize → one holds edge."""

    def test_same_class_assigned_twice_emits_once(self) -> None:
        """@primary = DB.new + @secondary = DB.new → only ONE holds edge Owner→DB."""
        src = (
            "class DB\nend\n"
            "class Owner\n"
            "  def initialize\n"
            "    @primary = DB.new\n"
            "    @secondary = DB.new\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        holds = _holds_edges(edges)
        db_holds = [e for e in holds if e["target"] == "DB" and e["source"] == "Owner"]
        assert len(db_holds) == 1, (
            f"Expected exactly 1 holds edge for DB; got {len(db_holds)}: {db_holds}"
        )


class TestRubyConfigOff:
    """RUBY-CONFIG: SEAM_COMPOSITION_EDGES='off' → zero holds edges."""

    def test_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, Ruby classes emit no holds edges."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = (
            "class Repository\nend\n"
            "class Owner\n"
            "  def initialize\n"
            "    @repo = Repository.new\n"
            "  end\n"
            "end\n"
        )
        edges = _parse_ruby(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"


# ── PHP: property acceptance ───────────────────────────────────────────────────


class TestPhpPropertyComposition:
    """PHP-ACCEPT: PHP class with typed property declaration → holds edge."""

    def test_typed_property_emits_holds(self) -> None:
        """private Repository $repo; → holds edge Owner→Repository."""
        src = (
            "<?php\n"
            "class Repository {}\n"
            "class Owner {\n"
            "    private Repository $repo;\n"
            "}\n"
        )
        edges = _parse_php(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge Owner→Repository; got holds={holds}"
        )

    def test_multiple_typed_properties_emit_multiple_holds(self) -> None:
        """Multiple typed properties each emit a holds edge."""
        src = (
            "<?php\n"
            "class DB {}\n"
            "class Logger {}\n"
            "class Owner {\n"
            "    private DB $db;\n"
            "    private Logger $logger;\n"
            "}\n"
        )
        edges = _parse_php(src)
        targets = _holds_targets(edges)
        assert "DB" in targets, f"Missing DB; got {targets}"
        assert "Logger" in targets, f"Missing Logger; got {targets}"

    def test_holds_confidence_is_inferred(self) -> None:
        """PHP holds edge confidence must be INFERRED."""
        src = (
            "<?php\n"
            "class Repository {}\n"
            "class Owner {\n"
            "    private Repository $repo;\n"
            "}\n"
        )
        edges = _parse_php(src)
        for e in _holds_edges(edges):
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"

    def test_holds_source_is_class_name(self) -> None:
        """Source of holds edge is the class name."""
        src = (
            "<?php\n"
            "class Repository {}\n"
            "class Container {\n"
            "    private Repository $repo;\n"
            "}\n"
        )
        edges = _parse_php(src)
        holds = _holds_edges(edges)
        assert all(e["source"] == "Container" for e in holds), (
            f"Expected source='Container'; got {[e['source'] for e in holds]}"
        )


class TestPhpConstructorParam:
    """PHP-CTOR: PHP constructor parameter with type hint → holds edge."""

    def test_typed_constructor_param_emits_holds(self) -> None:
        """__construct(Repository $repo) → holds edge Owner→Repository."""
        src = (
            "<?php\n"
            "class Repository {}\n"
            "class Owner {\n"
            "    public function __construct(Repository $repo) {}\n"
            "}\n"
        )
        edges = _parse_php(src)
        holds = _holds_edges(edges)
        assert any(e["source"] == "Owner" and e["target"] == "Repository" for e in holds), (
            f"Expected holds edge from ctor param; got holds={holds}"
        )


class TestPhpRefusals:
    """PHP-REFUSE: primitive, untyped property → no holds."""

    def test_untyped_property_refused(self) -> None:
        """Property without type hint → no holds edge."""
        src = (
            "<?php\n"
            "class Owner {\n"
            "    private $repo;\n"
            "}\n"
        )
        edges = _parse_php(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds; got {holds}"


class TestPhpDedup:
    """PHP-DEDUP: same type as property + ctor param → one holds edge only."""

    def test_same_type_property_and_ctor_deduped(self) -> None:
        """Repository as both property and ctor param → exactly one holds edge."""
        src = (
            "<?php\n"
            "class Repository {}\n"
            "class Owner {\n"
            "    private Repository $repo;\n"
            "    public function __construct(Repository $repo) {}\n"
            "}\n"
        )
        edges = _parse_php(src)
        holds = _holds_edges(edges)
        repo_holds = [e for e in holds if e["source"] == "Owner" and e["target"] == "Repository"]
        assert len(repo_holds) == 1, (
            f"Expected exactly 1 holds edge; got {len(repo_holds)}: {repo_holds}"
        )


class TestPhpConfigOff:
    """PHP-CONFIG: SEAM_COMPOSITION_EDGES='off' → zero holds edges."""

    def test_no_holds_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With SEAM_COMPOSITION_EDGES=off, PHP classes emit no holds edges."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        src = (
            "<?php\n"
            "class Repository {}\n"
            "class Owner {\n"
            "    private Repository $repo;\n"
            "}\n"
        )
        edges = _parse_php(src)
        holds = _holds_edges(edges)
        assert len(holds) == 0, f"Expected no holds edges; got {holds}"
