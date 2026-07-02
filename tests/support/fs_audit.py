"""Filesystem snapshot + diff helper for installer write-scope audit tests.

WHY this exists:
  The P5.3 installer write-scope tests (S2/S3) need to verify that `seam install`
  writes exactly the files it should and nothing else.  Rather than hard-coding
  expected paths, they take a filesystem snapshot before and after each install
  call and inspect the diff.  This module provides that shared primitive.

WHY stdlib-only (hashlib, pathlib, os, dataclasses):
  Test-support code that imports Seam internals creates a circular validation
  problem — if the import itself is broken the test can't tell us why.  Using
  only the stdlib keeps this module unconditionally importable.

WHY absolute-path string keys:
  S2/S3 need to answer "is this path under the project root or under HOME?"
  Absolute strings make those comparisons trivial with startswith() or
  Path(key).is_relative_to(root) without round-tripping through Path objects.

WHY sha256 and not mtime:
  mtime resolution on macOS/Linux is 1 second; an install that runs in <1 s
  would appear unchanged.  Content digest is always correct regardless of clock.

WHY we never raise on read failure:
  A file that can't be read in the 'after' snapshot would silently disappear
  from the diff, making the test trivially pass.  Representing it with a fixed
  sentinel digest means it compares as 'different from the real content' in
  'before', which surfaces the anomaly in 'modified' rather than hiding it.
  A file that was never readable in 'before' either stays consistent (sentinel
  == sentinel) or causes the test to notice the mismatch.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# Sentinel used when a file exists but cannot be read (permission error,
# OS error, binary encoding mismatch, etc.).  It is a fixed string that is
# NOT a valid sha256 hex digest (it contains spaces), so it will always
# differ from a real digest and never silently mark a file as unchanged.
_UNREADABLE_SENTINEL = "UNREADABLE FILE (permission or OS error)"


def snapshot(roots: list[Path] | tuple[Path, ...]) -> dict[str, str]:
    """Walk *roots* and return a mapping {absolute_path_str: sha256_hex}.

    Args:
        roots: An iterable of directories to walk.  Non-existent directories
               are silently skipped.  Empty directories contribute no keys.

    Returns:
        A dict whose keys are absolute resolved path strings for every *file*
        found under any root.  Directories and symlinks-to-directories are
        not included.  The value is the sha256 hex digest of the file's
        binary content, or ``_UNREADABLE_SENTINEL`` if the file cannot be read.
    """
    result: dict[str, str] = {}
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            # Non-existent roots are silently skipped — caller may snapshot a
            # directory before it is created (e.g. during install tests).
            continue
        for dirpath, _dirnames, filenames in os.walk(root_path):
            for name in filenames:
                abs_path = Path(dirpath, name).resolve()
                result[str(abs_path)] = _digest(abs_path)
    return result


def _digest(path: Path) -> str:
    """Return the sha256 hex digest of *path*'s binary content.

    Never raises: returns ``_UNREADABLE_SENTINEL`` on any OS/IO error.
    """
    try:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return _UNREADABLE_SENTINEL


@dataclass(frozen=True)
class FsChanges:
    """The result of comparing two filesystem snapshots.

    All three sets contain absolute path strings.  They are always disjoint:
    a path can be created, deleted, or modified, but never two of those at once.

    Attributes:
        created:  Paths present in *after* but not in *before*.
        modified: Paths present in both snapshots whose digest changed.
        deleted:  Paths present in *before* but not in *after*.
    """

    created: set[str]
    modified: set[str]
    deleted: set[str]


def diff(before: dict[str, str], after: dict[str, str]) -> FsChanges:
    """Compute the difference between two snapshots produced by :func:`snapshot`.

    Args:
        before: Snapshot taken *before* the operation under test.
        after:  Snapshot taken *after* the operation under test.

    Returns:
        An :class:`FsChanges` with three disjoint sets of absolute path strings.
    """
    before_keys = set(before)
    after_keys = set(after)

    created = after_keys - before_keys
    deleted = before_keys - after_keys
    # 'modified' = paths in both whose digest changed
    common = before_keys & after_keys
    modified = {k for k in common if before[k] != after[k]}

    return FsChanges(created=created, modified=modified, deleted=deleted)
