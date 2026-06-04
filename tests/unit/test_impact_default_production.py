"""P1 — seam_impact defaults to the PRODUCTION blast radius.

Contract under test (the deliberate behavior flip):
  - handle_seam_impact with NO include_tests arg → test-file dependents are EXCLUDED
    (production-only), and the filtered count surfaces as hidden_tests.
  - Passing include_tests=True explicitly restores the old behavior (tests kept).
  - The seam_impact MCP tool advertises include_tests=False as its default (so the
    schema agents see matches the handler).
  - The CLI exposes --include-tests (default off = production-only) and --no-include-tests.

WHY this matters: "what breaks if I change X?" should answer with the production
blast radius. Test callers crowded the risk tiers and tripped the per-tier cap,
burying the real production chain (the seam-repo impact-A/B/context-B benchmark loss).
Tests are derivable separately (seam_affected) — they are opt-in here, not default.

These tests verify EXTERNAL behavior through the public handler / tool / CLI surfaces.
The analysis-layer impact() default stays include_tests=True (seam_changes depends on
it) — that contract is covered by test_impact_is_test.py and is intentionally untouched.
"""

import inspect
import sqlite3
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.server.tools import handle_seam_impact

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str) -> Symbol:
    return Symbol(name=name, kind="function", file=file, start_line=1, end_line=2, docstring=None)


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call", file=file, line=1, confidence="EXTRACTED"
    )


@pytest.fixture()
def mixed_db() -> tuple[sqlite3.Connection, Path]:
    """Graph where target 'A' has one production caller and one test caller (both d=1).

        prod_caller (prod.py)        -> A
        test_caller (tests/t.py)     -> A

    Yields (conn, project_root).
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir()

        prod_file = root / "prod.py"
        prod_file.write_text("# prod\n")
        tests_dir = root / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_thing.py"
        test_file.write_text("# test\n")

        conn = init_db(db_path)
        upsert_file(
            conn,
            prod_file,
            "python",
            "hash_prod",
            [_sym("A", str(prod_file)), _sym("prod_caller", str(prod_file))],
            [_edge("prod_caller", "A", str(prod_file))],
        )
        upsert_file(
            conn,
            test_file,
            "python",
            "hash_test",
            [_sym("test_caller", str(test_file))],
            [_edge("test_caller", "A", str(test_file))],
        )
        yield conn, root  # type: ignore[misc]
        conn.close()


def _names(result: dict, direction: str = "upstream") -> list[str]:
    return [e["name"] for entry_list in result[direction].values() for e in entry_list]


# ── Handler default ─────────────────────────────────────────────────────────


def test_handler_default_excludes_test_dependents(
    mixed_db: tuple[sqlite3.Connection, Path],
) -> None:
    """handle_seam_impact with NO include_tests arg must return production-only."""
    conn, root = mixed_db

    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=1)

    names = _names(result)
    assert "prod_caller" in names, f"production caller must remain by default; got {names}"
    assert "test_caller" not in names, (
        f"test caller must be excluded by default (production-only); got {names}"
    )


def test_handler_default_reports_hidden_tests(
    mixed_db: tuple[sqlite3.Connection, Path],
) -> None:
    """The default (production-only) must report how many test dependents were hidden."""
    conn, root = mixed_db

    result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=1)

    assert result.get("hidden_tests") == 1, (
        f"one hidden test dependent expected by default, got {result.get('hidden_tests')!r}"
    )


def test_handler_explicit_include_tests_true_keeps_tests(
    mixed_db: tuple[sqlite3.Connection, Path],
) -> None:
    """Passing include_tests=True explicitly restores the old behavior (tests kept)."""
    conn, root = mixed_db

    result = handle_seam_impact(
        conn, "A", root, direction="upstream", max_depth=1, include_tests=True
    )

    names = _names(result)
    assert "prod_caller" in names
    assert "test_caller" in names, (
        f"explicit include_tests=True must keep test callers; got {names}"
    )


# ── Test-file-stem import sources are hidden by the production-only default ────


def test_default_hides_test_file_stem_import_sources() -> None:
    """A test file that imports the target directly (import edge sourced at the test
    file's STEM, file=None) must be hidden by the production-only default.

    Production code imports the MODULE (e.g. `fts.rescore()`), so the only import edges
    that target a bare symbol come from test files. Those arrive as file=None entries
    named after the test file's stem and would leak into the "production-only" result
    unless the stem is recognised as test-only.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir()

        prod_file = root / "prod.py"
        prod_file.write_text("# prod\n")
        tests_dir = root / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_thing.py"
        test_file.write_text("# test\n")

        conn = init_db(db_path)
        # target 'A' defined in prod; one production caller.
        upsert_file(
            conn,
            prod_file,
            "python",
            "h_prod",
            [_sym("A", str(prod_file)), _sym("prod_caller", str(prod_file))],
            [_edge("prod_caller", "A", str(prod_file))],
        )
        # test file imports A directly → import edge sourced at the file stem 'test_thing'
        # (no indexed symbol of that name → file=None at read time).
        upsert_file(
            conn,
            test_file,
            "python",
            "h_test",
            [],
            [
                Edge(
                    source="test_thing",
                    target="A",
                    kind="import",
                    file=str(test_file),
                    line=1,
                    confidence="INFERRED",
                )
            ],
        )

        result = handle_seam_impact(conn, "A", root, direction="upstream", max_depth=1)
        names = _names(result)
        assert "prod_caller" in names, f"production caller must remain; got {names}"
        assert "test_thing" not in names, (
            f"test-file-stem import source must be hidden by default; got {names}"
        )
        assert result.get("hidden_tests", 0) >= 1, "the hidden test-stem must be counted"
        conn.close()


# ── MCP tool default (advertised schema) ──────────────────────────────────────


def test_mcp_seam_impact_default_is_production_only() -> None:
    """The registered seam_impact tool must advertise include_tests=False as its default.

    Agents read the tool schema's defaults; the MCP default must match the handler so
    a bare seam_impact call returns the production blast radius.
    """
    from mcp.server.fastmcp import FastMCP

    import seam.server.mcp as mcp_module

    # create_server registers the closures; introspect the registered tool's signature.
    conn = init_db(Path(tempfile.mkdtemp()) / ".seam.db")
    server: FastMCP = mcp_module.create_server(conn, Path("."))
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    conn.close()

    assert "seam_impact" in tools, "seam_impact tool must be registered"
    params = tools["seam_impact"].parameters  # JSON schema dict
    include_tests = params.get("properties", {}).get("include_tests", {})
    assert include_tests.get("default") is False, (
        f"seam_impact must advertise include_tests default=False; got {include_tests!r}"
    )


# ── CLI flag (--include-tests, default off = production-only) ──────────────────


def test_cli_impact_has_include_tests_flag_defaulting_off() -> None:
    """The CLI impact command must expose --include-tests defaulting to off (production-only).

    Guards the CLI↔MCP parity: the flag name mirrors the MCP param and defaults to the
    same production-only behavior.
    """
    from seam.cli.main import impact_cmd

    sig = inspect.signature(impact_cmd)
    assert "include_tests" in sig.parameters, (
        f"impact_cmd must take an include_tests option; params: {list(sig.parameters)}"
    )
    # Typer OptionInfo default lives on the parameter's .default.default
    opt = sig.parameters["include_tests"].default
    assert getattr(opt, "default", None) is False, (
        f"--include-tests must default to False (production-only); got {opt!r}"
    )
    # The old flag must be gone (single mental model, no drift).
    assert "production_only" not in sig.parameters, (
        "the old --production-only flag should be replaced by --include-tests"
    )
