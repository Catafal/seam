"""Regression test: the CLI impact renderers must not crash on the index_status banner.

Bug: the P2 staleness banner adds a top-level `index_status` dict to the seam_impact
result. The CLI quiet/Rich renderers iterate every top-level dict value as if it were a
direction group (upstream/downstream → tier → entries). `index_status` is a dict but NOT
a direction group, so the walker reached its bool `stale` value and crashed with
`TypeError: 'bool' object is not iterable`. The fix adds `index_status` to
`_IMPACT_META_KEYS` so all three render sites skip it.

We inject a crafted result (the banner is otherwise only produced by a stale on-disk
index, which is fiddly to force deterministically) and assert each render mode exits 0.
"""

from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import seam.cli.main as cli_main
from seam.cli.main import app
from seam.indexer.db import init_db

runner = CliRunner()


def _result_with_banner() -> dict[str, Any]:
    """A minimal seam_impact result that carries the P2 index_status banner."""
    return {
        "found": True,
        "target": "hub",
        "upstream": {
            "WILL_BREAK": [
                {"name": "caller_a", "confidence": "EXTRACTED", "distance": 1, "kind": "call"},
                {"name": "caller_b", "confidence": "EXTRACTED", "distance": 1, "kind": "call"},
            ]
        },
        "risk_summary": {"upstream": {"WILL_BREAK": 2}},
        "truncated": {"upstream": {"WILL_BREAK": 0}},
        "index_status": {
            "stale": True,
            "reason": "2 files changed since last index",
            "hint": "run `seam sync` to refresh",
        },
    }


def _db(tmp_path: Path) -> Path:
    """A valid empty index so impact_cmd can open a connection before our patch runs."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path).close()
    return tmp_path


def _invoke(root: Path, *flags: str) -> Any:
    return runner.invoke(
        app,
        ["impact", "hub", *flags, "--db-dir", str(root), "--path", str(root)],
    )


def test_quiet_mode_does_not_crash_on_index_status(tmp_path: Path, monkeypatch: Any) -> None:
    root = _db(tmp_path)
    monkeypatch.setattr(cli_main, "handle_seam_impact", lambda *a, **k: _result_with_banner())

    result = _invoke(root, "--quiet")

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    # The two real dependents print as bare names; banner keys never leak as names.
    assert "caller_a" in result.output
    assert "caller_b" in result.output
    for leaked in ("stale", "2 files changed", "run `seam sync`"):
        assert leaked not in result.output, f"banner field leaked into quiet names: {leaked!r}"


def test_rich_mode_does_not_crash_on_index_status(tmp_path: Path, monkeypatch: Any) -> None:
    root = _db(tmp_path)
    monkeypatch.setattr(cli_main, "handle_seam_impact", lambda *a, **k: _result_with_banner())

    result = _invoke(root)

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    assert "caller_a" in result.output


def test_json_mode_passes_banner_through(tmp_path: Path, monkeypatch: Any) -> None:
    root = _db(tmp_path)
    monkeypatch.setattr(cli_main, "handle_seam_impact", lambda *a, **k: _result_with_banner())

    result = _invoke(root, "--json")

    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    assert "index_status" in result.output
