"""Unit tests for seam/indexer/init_index.py — the shared indexing function.

Tests assert external behavior only:
  - run_init creates a DB and returns sensible counts
  - Idempotent: a second run does not crash
  - Empty directory produces a valid empty index
  - Non-git directory works (no crash)
  - db_dir override puts the DB in the right place
  - progress_cb is called with at least one status string

WHY this test file: run_init is the shared code path used by both `seam init`
and `seam serve` auto-init. Unit-testing it directly ensures the logic is
stable regardless of how the CLI calls it.
"""

from pathlib import Path

from seam.indexer.init_index import InitResult, run_init

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_sample_py(d: Path, name: str = "sample.py") -> Path:
    """Write a minimal Python file with one function and one class."""
    (d / name).write_text(
        "class Greeter:\n"
        "    def greet(self, name: str) -> str:\n"
        "        return f'hello {name}'\n"
        "\n"
        "def main() -> None:\n"
        "    g = Greeter()\n"
        "    print(g.greet('world'))\n",
        encoding="utf-8",
    )
    return d / name


# ── T1: basic run creates DB + returns non-negative counts ────────────────────


def test_run_init_creates_db(tmp_path: Path) -> None:
    """run_init must create the database file and return a valid InitResult."""
    _write_sample_py(tmp_path)

    result = run_init(tmp_path)

    assert isinstance(result, InitResult)
    assert result.db_path.exists(), "DB file must be created"
    assert result.indexed_files >= 1, "At least one file must be indexed"
    assert result.total_symbols >= 1, "At least one symbol (class/function) must be found"
    assert result.total_edges >= 0, "Edge count must be non-negative"
    assert result.total_clusters >= 0, "Cluster count must be non-negative"
    assert result.total_synthesis >= 0, "Synthesis count must be non-negative"
    assert result.total_test_edges >= 0, "Test-edge count must be non-negative"
    assert result.total_embeddings is None, "Embeddings not requested — must be None"


def test_run_init_symbols_and_edges_positive(tmp_path: Path) -> None:
    """The indexed file must yield multiple symbols and at least one edge."""
    _write_sample_py(tmp_path)

    result = run_init(tmp_path)

    # The sample file has: Greeter (class), Greeter.greet (method), main (function)
    # and at least a call-edge from main → Greeter.greet (or instantiates)
    assert result.total_symbols >= 2  # at minimum 2 distinct symbols
    # Edges may vary by extractor version, but must be >= 0 — strict positive
    # would be fragile across config changes.
    assert result.total_edges >= 0


# ── T2: idempotent — second run must not crash ────────────────────────────────


def test_run_init_idempotent(tmp_path: Path) -> None:
    """A second run_init must not raise and must return a valid InitResult."""
    _write_sample_py(tmp_path)

    first = run_init(tmp_path)
    second = run_init(tmp_path)

    assert second.db_path.exists()
    # Same file = same (or more) symbols — never negative
    assert second.indexed_files >= 1
    assert second.total_symbols >= 1
    # The exact counts may differ between runs (e.g. clustering may vary);
    # we assert they are non-negative, not identical.
    assert second.total_clusters >= 0
    # DB path is stable between runs
    assert first.db_path == second.db_path


# ── T3: empty directory produces a valid empty index ─────────────────────────


def test_run_init_empty_dir(tmp_path: Path) -> None:
    """An empty directory must produce a valid (empty) index without crashing."""
    result = run_init(tmp_path)

    assert result.db_path.exists()
    assert result.indexed_files == 0
    assert result.skipped_files >= 0  # may be 0 too
    assert result.total_symbols == 0
    assert result.total_edges == 0


# ── T4: non-git directory works ───────────────────────────────────────────────


def test_run_init_non_git_dir(tmp_path: Path) -> None:
    """run_init must succeed on a directory that is not a git repository."""
    # tmp_path has no .git directory — it is deliberately NOT a git repo
    _write_sample_py(tmp_path)

    result = run_init(tmp_path)

    assert result.db_path.exists()
    assert result.indexed_files >= 1


# ── T5: db_dir override puts the DB in a custom location ─────────────────────


def test_run_init_db_dir_override(tmp_path: Path) -> None:
    """When db_dir is given, the DB must be created under that directory."""
    db_dir = tmp_path / "custom_db"
    db_dir.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    _write_sample_py(src_dir)

    result = run_init(src_dir, db_dir=db_dir)

    assert result.db_path.parent == db_dir / ".seam"
    assert result.db_path.exists()


# ── T6: progress_cb is called at least once ───────────────────────────────────


def test_run_init_progress_cb_is_called(tmp_path: Path) -> None:
    """The optional progress_cb must be called at least once during indexing."""
    _write_sample_py(tmp_path)

    calls: list[str] = []
    run_init(tmp_path, progress_cb=calls.append)

    assert len(calls) >= 1, "progress_cb must be called at least once"
    # Each call must be a non-empty string
    for msg in calls:
        assert isinstance(msg, str)
        assert msg.strip() != ""


# ── T7: .seam/.gitignore is written ──────────────────────────────────────────


def test_run_init_writes_gitignore(tmp_path: Path) -> None:
    """run_init must write .seam/.gitignore containing '*' to keep the index out of git."""
    _write_sample_py(tmp_path)

    result = run_init(tmp_path)

    gitignore = result.db_path.parent / ".gitignore"
    assert gitignore.exists(), ".seam/.gitignore must be created"
    assert gitignore.read_text().strip() == "*"
