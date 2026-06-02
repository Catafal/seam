"""LEAF: pure JSON config-file operations for the installer (stdlib only).

Used by the JSON-format targets (Claude `.mcp.json`, Cursor `.cursor/mcp.json`).
No Seam dependencies, so targets compose these without import cycles. Every
write is atomic (temp + os.replace) because a half-written agent config is
silently skipped at the agent's startup — a crash mid-write must never corrupt it.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any] | None:
    """Parse the JSON object at `path`, or None if absent OR unparseable.

    None is intentionally ambiguous between "absent" and "corrupt" — the caller
    disambiguates with `path.exists()` so it can back up a corrupt file before
    overwriting it (see core._backup_if_corrupt).
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    # A top-level non-object (list/scalar) is not a config we can merge into.
    return data if isinstance(data, dict) else None


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write `data` as pretty JSON to `path` atomically; create parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        # Never leave the temp file behind on failure (disk-full, signal, etc.).
        Path(tmp).unlink(missing_ok=True)
        raise


def get_in(data: dict[str, Any], key_path: list[str]) -> Any:
    """Return the value at the nested `key_path`, or None if any segment is missing."""
    cur: Any = data
    for key in key_path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def set_in(data: dict[str, Any], key_path: list[str], value: Any) -> None:
    """Set `value` at the nested `key_path`, creating intermediate dicts as needed.

    A non-dict found at an intermediate segment is replaced with a dict — the
    target key paths (mcpServers / projects.<root>.mcpServers) own their subtree.
    """
    cur = data
    for key in key_path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[key_path[-1]] = value


def delete_in(data: dict[str, Any], key_path: list[str]) -> bool:
    """Delete the leaf at `key_path`. Return True iff something was removed."""
    cur = data
    for key in key_path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            return False
        cur = nxt
    if key_path[-1] in cur:
        del cur[key_path[-1]]
        return True
    return False
