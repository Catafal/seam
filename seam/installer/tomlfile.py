"""LEAF: TOML config-file operations for the Codex target.

Codex stores MCP servers in ~/.codex/config.toml as [mcp_servers.<name>] tables.
Stdlib `tomllib` is read-only, so we use `tomlkit` — crucially, it round-trips a
user's existing comments and formatting, so installing a Seam entry never mangles
a hand-tuned config. No Seam dependencies (leaf).
"""

import os
import tempfile
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument


def load_toml(path: Path) -> TOMLDocument | None:
    """Parse the TOML document at `path`, or None if absent OR unparseable."""
    if not path.exists():
        return None
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except (tomlkit.exceptions.TOMLKitError, OSError, UnicodeDecodeError):
        return None


def atomic_write_toml(path: Path, doc: TOMLDocument) -> None:
    """Write `doc` to `path` atomically (temp + os.replace); create parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(tomlkit.dumps(doc))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def get_server_table(doc: TOMLDocument, server: str) -> dict[str, Any] | None:
    """Return [mcp_servers.<server>] as a plain dict for comparison, or None if absent."""
    servers = doc.get("mcp_servers")
    if not isinstance(servers, dict) or server not in servers:
        return None
    # Unwrap tomlkit items to plain Python so equality checks are value-based.
    return {k: _plain(v) for k, v in servers[server].items()}


def set_server_table(doc: TOMLDocument, server: str, table: dict[str, Any]) -> None:
    """Set [mcp_servers.<server>] = table, creating the parent table if needed."""
    if "mcp_servers" not in doc:
        doc["mcp_servers"] = tomlkit.table()
    doc["mcp_servers"][server] = table


def delete_server_table(doc: TOMLDocument, server: str) -> bool:
    """Delete [mcp_servers.<server>]. Return True iff it existed."""
    servers = doc.get("mcp_servers")
    if isinstance(servers, dict) and server in servers:
        del servers[server]
        return True
    return False


def _plain(value: Any) -> Any:
    """Recursively unwrap tomlkit containers to plain dict/list for equality checks."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value
