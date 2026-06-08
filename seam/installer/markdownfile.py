"""LEAF: pure text/Markdown file operations for the installer (stdlib only).

Two kinds of write the guidance installer needs, neither expressible through the
JSON/TOML leaves:

  * **owned files** — files Seam fully owns (a Claude Code skill `SKILL.md`, a
    Cursor `seam.mdc`). `write_file` / `remove_file` treat the whole file as ours.
  * **shared files** — files that hold the user's own content too (`AGENTS.md`,
    `CLAUDE.md`). `upsert_block` / `remove_block` edit ONLY a marker-delimited
    region (`<!-- seam:start -->` … `<!-- seam:end -->`) and never touch the rest.

Every write is atomic (temp + os.replace): an agent reads these files at startup,
so a crash mid-write must never leave a half-written instruction file behind.

No Seam dependencies → targets compose these without import cycles.
"""

import os
import re
import tempfile
from pathlib import Path


def read_text(path: Path) -> str | None:
    """Return the file's text, or None if it is absent or unreadable.

    None is the "treat as empty / nothing to remove" signal for callers — a
    missing or undecodable file is not an error here (we degrade, never raise).
    """
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically; create parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        # Never leave the temp file behind on failure (disk-full, signal, etc.).
        Path(tmp).unlink(missing_ok=True)
        raise


# ── owned files (SKILL.md, seam.mdc) ──────────────────────────────────────────


def write_file(path: Path, content: str) -> str:
    """Write a Seam-owned file. Returns created | updated | unchanged.

    Idempotent: identical content (after trailing-newline normalisation) is a
    no-op so re-running `seam install` does not churn the file's mtime.
    """
    normalized = content if content.endswith("\n") else content + "\n"
    if read_text(path) == normalized:
        return "unchanged"
    action = "updated" if path.exists() else "created"
    atomic_write_text(path, normalized)
    return action


def remove_file(path: Path) -> str:
    """Delete a Seam-owned file if present. Returns removed | not_present."""
    if path.exists():
        path.unlink()
        return "removed"
    return "not_present"


# ── shared files (AGENTS.md, CLAUDE.md) — marker-delimited block ──────────────


def _markers(marker: str) -> tuple[str, str]:
    """The start/end HTML-comment sentinels for a marker name.

    HTML comments render invisibly in Markdown across every agent, so the block
    is unobtrusive in a file the user also reads.
    """
    return f"<!-- {marker}:start -->", f"<!-- {marker}:end -->"


def _block_re(marker: str) -> re.Pattern[str]:
    """Regex matching the whole start…end region (inclusive), non-greedy."""
    start, end = _markers(marker)
    return re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)


def wrap_block(content: str, marker: str) -> str:
    """Wrap `content` in the marker sentinels — the canonical block form."""
    start, end = _markers(marker)
    return f"{start}\n{content.strip()}\n{end}"


def upsert_block(path: Path, content: str, *, marker: str) -> str:
    """Insert or replace the marked block in a shared file.

    Returns created | updated | unchanged. Replaces an existing block IN PLACE
    (never duplicates) and appends a new one without disturbing foreign content.
    """
    block = wrap_block(content, marker)
    existing = read_text(path)

    if existing is None:
        atomic_write_text(path, block + "\n")
        return "created"

    pattern = _block_re(marker)
    if pattern.search(existing):
        # Replace via a function so backslashes/group-refs in `block` stay literal.
        new_text = pattern.sub(lambda _m: block, existing, count=1)
        if new_text == existing:
            return "unchanged"
        atomic_write_text(path, new_text)
        return "updated"

    base = existing.rstrip("\n")
    new_text = f"{base}\n\n{block}\n" if base else f"{block}\n"
    atomic_write_text(path, new_text)
    return "updated"


def remove_block(path: Path, *, marker: str) -> str:
    """Remove the marked block from a shared file. Returns removed | not_present.

    Preserves all foreign content and collapses the blank lines left behind so
    the file does not accrete empty space across install/uninstall cycles.
    """
    existing = read_text(path)
    if existing is None:
        return "not_present"

    pattern = _block_re(marker)
    if not pattern.search(existing):
        return "not_present"

    new_text = pattern.sub("", existing, count=1)
    new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip("\n")
    atomic_write_text(path, (new_text + "\n") if new_text else "")
    return "removed"
