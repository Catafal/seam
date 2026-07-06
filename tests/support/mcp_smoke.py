"""MCP stdio smoke driver for the no-egress proof (P5.4 S2).

WHY this exists:
  The no-egress CI workflow must prove that the SERVER path — not only the CLI
  commands — makes zero outbound connections.  ``seam start`` is the MCP stdio
  server; this script performs a real MCP handshake with it so the workflow can
  wrap the whole interaction in ``strace -f -e trace=connect`` and feed the trace
  to ``egress_audit``.

WHY asyncio with wait_for timeout:
  ``seam start`` runs in the foreground over stdio.  If the handshake stalls
  (broken pipe, index missing, import error) this script would hang CI
  indefinitely.  ``asyncio.wait_for`` with an outer 30-second guard ensures we
  always exit — exit 1 on timeout so the workflow fails rather than hangs.

WHY resolve the ``seam`` command via SEAM_CMD env / venv path / PATH fallback:
  In CI the venv ``seam`` script is on PATH under ``uv run``.
  Locally, testers may run outside ``uv run`` — the venv bin path works there.
  A SEAM_CMD override lets callers inject any path (useful in tests).

WHY not ``sys.executable -m seam.cli.main``:
  Using the console-script path tests the exact same entrypoint that end users
  invoke.  The ``-m`` form would bypass the entrypoint machinery and is slightly
  different from what ``seam install`` wires into Claude / Cursor.

USAGE:
  uv run --extra server python -m tests.support.mcp_smoke <root>

  Optional second argument overrides the seam command:
  python -m tests.support.mcp_smoke <root> /path/to/seam
"""

import asyncio
import os
import sys
from pathlib import Path

# mcp is in the [server] optional extra; not available in the base install.
# This script is intended to run under ``uv run --extra server`` (or in CI
# where the extra was synced).  A missing import is a clear error.
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# ── constants ────────────────────────────────────────────────────────────────

# Hard timeout for the entire handshake (initialize + list_tools).
# Must be generous enough for a cold import of the full seam package on slow CI
# runners, but short enough that a hung server fails the job quickly.
_HANDSHAKE_TIMEOUT_SEC = 30


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_seam_command(override: str | None) -> str:
    """Return the path to the ``seam`` executable to use as the server command.

    Resolution order (first wins):
    1. ``override`` argument (from argv[1] or SEAM_CMD env var).
    2. Venv sibling: ``<this-script's-venv>/bin/seam``.
    3. Plain ``"seam"`` — relies on PATH (works under ``uv run`` in CI).

    Args:
        override: Explicit path or ``None`` to auto-detect.

    Returns:
        A string command suitable for ``StdioServerParameters(command=...)``.
    """
    if override:
        return override

    env_cmd = os.environ.get("SEAM_CMD")
    if env_cmd:
        return env_cmd

    # Detect venv: if the current interpreter is inside a ``.venv``, the
    # ``seam`` console script lives alongside it.
    interpreter = Path(sys.executable)
    venv_seam = interpreter.parent / "seam"
    if venv_seam.is_file():
        return str(venv_seam)

    # Fall back to bare name — must be on PATH.
    return "seam"


# ── core handshake ───────────────────────────────────────────────────────────


async def _run_handshake(root: str, seam_cmd: str) -> int:
    """Perform one full MCP handshake: spawn server, initialize, list tools.

    This is the inner coroutine wrapped by ``asyncio.wait_for`` in ``main()``.

    Args:
        root:     Absolute path to a project root; `seam start` may auto-init it.
        seam_cmd: Command used to spawn the ``seam start`` stdio server.

    Returns:
        The number of tools reported by the server (printed to stdout).

    Raises:
        Anything from the MCP client on failure — propagated to ``main()`` which
        translates it to exit code 1.
    """
    params = StdioServerParameters(
        command=seam_cmd,
        args=["start", root],
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # initialize() performs the MCP protocol handshake.
            await session.initialize()

            result = await session.list_tools()
            tool_count = len(result.tools)
            print(f"mcp_smoke: {tool_count} tools available via seam start")
            return tool_count


# ── entry point ──────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    """Drive one MCP stdio handshake against ``seam start``.

    Args:
        argv: ``[<root>]`` or ``[<root>, <seam-command>]``.
              root          — path to a project directory.
              seam-command  — optional override for the seam executable.

    Returns:
        ``0`` on success (handshake completed; tool count printed).
        ``1`` on any failure: timeout, non-zero exit from server, MCP error, etc.
    """
    if not argv:
        print(
            "usage: python -m tests.support.mcp_smoke <root> [seam-cmd]",
            file=sys.stderr,
        )
        return 1

    root = str(Path(argv[0]).resolve())
    seam_cmd_override = argv[1] if len(argv) > 1 else None
    seam_cmd = _resolve_seam_command(seam_cmd_override)

    print(f"mcp_smoke: connecting to seam start at root={root!r}, cmd={seam_cmd!r}")

    try:
        asyncio.run(
            asyncio.wait_for(
                _run_handshake(root, seam_cmd),
                timeout=_HANDSHAKE_TIMEOUT_SEC,
            )
        )
    except TimeoutError:
        print(
            f"mcp_smoke: TIMEOUT — handshake did not complete in {_HANDSHAKE_TIMEOUT_SEC}s",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — intentional: any failure = exit 1
        print(f"mcp_smoke: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
