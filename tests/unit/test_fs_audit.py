"""Unit tests for tests/support/fs_audit.py.

These tests exercise the external behavior of snapshot() and diff() through
the public API only — no implementation details.

WHY: fs_audit is the shared test-support helper for P5.3 installer write-scope
audit (S2/S3). These tests lock down the contract so S2/S3 can import and rely
on it without re-validating the helper itself.
"""

from pathlib import Path

from tests.support.fs_audit import (
    FsChanges,
    diff,
    snapshot,
)

# ── snapshot ────────────────────────────────────────────────────────────────


def test_snapshot_single_root_single_file(tmp_path: Path) -> None:
    """snapshot() captures a file in a single root."""
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    result = snapshot([tmp_path])
    assert str(f.resolve()) in result


def test_snapshot_multiple_roots(tmp_path: Path) -> None:
    """snapshot() merges files from all supplied roots."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    fa = root_a / "file_a.txt"
    fb = root_b / "file_b.txt"
    fa.write_bytes(b"aaa")
    fb.write_bytes(b"bbb")

    result = snapshot([root_a, root_b])

    assert str(fa.resolve()) in result
    assert str(fb.resolve()) in result


def test_snapshot_nested_paths(tmp_path: Path) -> None:
    """snapshot() recurses into nested subdirectories."""
    deep = tmp_path / "x" / "y" / "z"
    deep.mkdir(parents=True)
    f = deep / "nested.txt"
    f.write_bytes(b"deep")

    result = snapshot([tmp_path])

    assert str(f.resolve()) in result


def test_snapshot_empty_dirs_contribute_nothing(tmp_path: Path) -> None:
    """Empty directories do not appear as keys in the snapshot."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = snapshot([tmp_path])

    # Only files are keys — the empty dir must not appear
    assert str(empty_dir.resolve()) not in result
    assert result == {}


def test_snapshot_keys_are_absolute_strings(tmp_path: Path) -> None:
    """Snapshot keys are absolute resolved path strings, not Path objects."""
    f = tmp_path / "abs.txt"
    f.write_bytes(b"x")

    result = snapshot([tmp_path])

    keys = list(result.keys())
    assert len(keys) == 1
    key = keys[0]
    assert isinstance(key, str)
    # An absolute path starts with '/' on POSIX
    assert Path(key).is_absolute()


def test_snapshot_nonexistent_root_degrades_without_raising(tmp_path: Path) -> None:
    """A root that does not exist is silently ignored — no exception."""
    ghost = tmp_path / "does_not_exist"
    result = snapshot([ghost])
    assert result == {}


def test_snapshot_empty_roots_iterable_returns_empty(tmp_path: Path) -> None:
    """An empty roots list returns an empty mapping without raising."""
    result = snapshot([])
    assert result == {}


def test_snapshot_digest_is_stable_across_calls(tmp_path: Path) -> None:
    """The same file produces the same digest on repeated snapshot() calls."""
    f = tmp_path / "stable.txt"
    f.write_bytes(b"deterministic content")

    r1 = snapshot([tmp_path])
    r2 = snapshot([tmp_path])

    key = str(f.resolve())
    assert r1[key] == r2[key]


# ── diff ────────────────────────────────────────────────────────────────────


def test_diff_created_file(tmp_path: Path) -> None:
    """A file present only in after is in created."""
    before = snapshot([tmp_path])

    new_file = tmp_path / "new.txt"
    new_file.write_bytes(b"created")
    after = snapshot([tmp_path])

    changes = diff(before, after)

    assert str(new_file.resolve()) in changes.created
    assert changes.deleted == set()
    assert changes.modified == set()


def test_diff_deleted_file(tmp_path: Path) -> None:
    """A file present only in before is in deleted."""
    gone = tmp_path / "gone.txt"
    gone.write_bytes(b"soon gone")
    before = snapshot([tmp_path])

    gone.unlink()
    after = snapshot([tmp_path])

    changes = diff(before, after)

    assert str(gone.resolve()) in changes.deleted
    assert changes.created == set()
    assert changes.modified == set()


def test_diff_modified_file(tmp_path: Path) -> None:
    """A file whose content changed between snapshots is in modified."""
    f = tmp_path / "change_me.txt"
    f.write_bytes(b"original")
    before = snapshot([tmp_path])

    f.write_bytes(b"something completely different")
    after = snapshot([tmp_path])

    changes = diff(before, after)

    assert str(f.resolve()) in changes.modified
    assert changes.created == set()
    assert changes.deleted == set()


def test_diff_unchanged_file_not_flagged(tmp_path: Path) -> None:
    """A file with the same content in before and after does not appear in any set."""
    f = tmp_path / "same.txt"
    f.write_bytes(b"unchanged")
    before = snapshot([tmp_path])
    after = snapshot([tmp_path])

    changes = diff(before, after)

    key = str(f.resolve())
    assert key not in changes.created
    assert key not in changes.deleted
    assert key not in changes.modified


def test_diff_sets_are_disjoint(tmp_path: Path) -> None:
    """created, modified, deleted are always mutually exclusive."""
    existing = tmp_path / "exist.txt"
    existing.write_bytes(b"v1")
    before = snapshot([tmp_path])

    # modify existing, create new, delete existing-then-create another
    existing.write_bytes(b"v2")
    new = tmp_path / "new.txt"
    new.write_bytes(b"fresh")
    after = snapshot([tmp_path])

    changes = diff(before, after)

    assert changes.created.isdisjoint(changes.modified)
    assert changes.created.isdisjoint(changes.deleted)
    assert changes.modified.isdisjoint(changes.deleted)


def test_diff_returns_fschanges(tmp_path: Path) -> None:
    """diff() always returns an FsChanges instance."""
    before = snapshot([tmp_path])
    after = snapshot([tmp_path])
    changes = diff(before, after)
    assert isinstance(changes, FsChanges)


def test_diff_empty_snapshots_no_changes(tmp_path: Path) -> None:
    """Two empty snapshots produce an FsChanges with all-empty sets."""
    changes = diff({}, {})
    assert changes.created == set()
    assert changes.modified == set()
    assert changes.deleted == set()
