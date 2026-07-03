"""`seam install` / `seam uninstall` CLI commands.

Lives in its own module (not main.py) because main.py is already large; these
two Typer command functions are registered onto the app in main.py.

CLI-first by default: `seam install` writes the token-lean CLI **guidance** (a
Claude Code skill, a Cursor rule, an AGENTS.md block) into the repo. `--with-mcp`
ALSO writes the MCP server config (the heavier, ~6k-token-standing path). Guidance
is project-scoped (it lives in the repo); only the MCP config respects `--location`.
The config-writing logic lives in seam/installer/* — this layer parses options,
orchestrates over the selected target(s), and renders Rich / JSON output.
"""

from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console

import seam.config as config
from seam.cli.output import emit_json, emit_json_error
from seam.installer import TARGETS, AgentTarget, get_target, resolve_seam_command

console = Console()

_ALL = "all"


def _select_targets(target: str) -> list[AgentTarget] | None:
    """Resolve the --target value to a list of AgentTargets, or None if unknown."""
    if target == _ALL:
        return list(TARGETS.values())
    one = get_target(target)
    return [one] if one is not None else None


def _install_target(
    tgt: AgentTarget, root: Path, location: str, command: str, args: list[str], with_mcp: bool
) -> dict[str, Any]:
    """Write guidance (always) and, if requested, the MCP config for one target."""
    entry: dict[str, Any] = {
        "target": tgt.name,
        "guidance": [{"action": r.action, "path": r.path} for r in tgt.install_guidance(root)],
        "mcp": None,
    }
    if not with_mcp:
        return entry
    if location in tgt.supported_locations():
        res = tgt.install(root, location, command, args)
        entry["mcp"] = {"action": res.action, "path": res.path, "backed_up": res.backed_up}
    else:
        # Only reachable under `all` — an explicit single target is validated upfront.
        entry["mcp"] = {
            "action": "skipped",
            "reason": f"MCP location '{location}' unsupported (supports {tgt.supported_locations()})",
            "path": None,
        }
    return entry


def _preview_target(
    tgt: AgentTarget, root: Path, location: str, command: str, args: list[str], with_mcp: bool
) -> dict[str, Any]:
    """Build the --print-config preview for one target (writes nothing)."""
    entry: dict[str, Any] = {
        "target": tgt.name,
        "guidance_preview": [{"path": p, "content": c} for p, c in tgt.guidance_previews(root)],
        "mcp_preview": None,
    }
    if with_mcp and location in tgt.supported_locations():
        entry["mcp_preview"] = {
            "path": str(tgt.config_path(root, location)),
            "config": tgt.render_entry(command, args),
        }
    return entry


def install_command(
    path: str = typer.Argument(".", help="Project root to index (default: current directory)."),
    target: str = typer.Option("claude", "--target", help="claude | cursor | codex | vscode | gemini | zed | all"),
    location: str = typer.Option(
        "project", "--location", help="MCP config scope: project | user (codex: user only)."
    ),
    with_mcp: bool = typer.Option(
        False, "--with-mcp", help="Also write the MCP server config (CLI guidance is the default)."
    ),
    print_config: bool = typer.Option(
        False, "--print-config", help="Print what would be written; write nothing."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit a structured JSON envelope to stdout."),
) -> None:
    """Set up Seam for a coding agent (Claude Code / Cursor / Codex / VS Code / Gemini CLI / Zed).

    Default: writes the token-lean CLI guidance into the repo so the agent queries
    via the `seam` CLI. Add `--with-mcp` to also wire the MCP server. Idempotent and
    reversible with `seam uninstall`.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        _fail(json_, "INVALID_INPUT", f"'{root}' is not a directory.")

    targets = _select_targets(target)
    if targets is None:
        _fail(json_, "INVALID_INPUT", f"unknown target '{target}' (expected: {', '.join(TARGETS)} or all)")

    # Validate an explicit single target's MCP location BEFORE any write, so we
    # never leave guidance written and then fail. (Under `all`, a bad location is
    # skipped per-target instead.)
    if with_mcp and target != _ALL and location not in targets[0].supported_locations():
        _fail(
            json_,
            "INVALID_INPUT",
            f"target '{targets[0].name}' MCP config does not support location '{location}' "
            f"(supports: {', '.join(targets[0].supported_locations())})",
        )

    command, found = resolve_seam_command()
    args = ["start", str(root)]
    index_present = config.get_db_path(root).exists()

    results: list[dict[str, Any]] = []
    for tgt in targets:
        if print_config:
            results.append(_preview_target(tgt, root, location, command, args, with_mcp))
        else:
            results.append(_install_target(tgt, root, location, command, args, with_mcp))

    warnings: list[str] = []
    if with_mcp and not found:
        warnings.append(
            "Could not locate the 'seam' executable — wrote the bare command 'seam' "
            "(valid once Seam is on PATH / published)."
        )
    if not index_present:
        warnings.append(f"No index at {root}/.seam — run 'seam init' so the agent has something to query.")

    data = {
        "with_mcp": with_mcp,
        "seam_command": command if with_mcp else None,
        "args": args if with_mcp else None,
        "index_present": index_present,
        "print_config": print_config,
        "results": results,
        "warnings": warnings,
    }
    if json_:
        emit_json(data)
        return
    _render_install(data)


def uninstall_command(
    path: str = typer.Argument(".", help="Project root the entry points at (default: current directory)."),
    target: str = typer.Option("claude", "--target", help="claude | cursor | codex | vscode | gemini | zed | all"),
    location: str = typer.Option("project", "--location", help="MCP config scope: project | user."),
    json_: bool = typer.Option(False, "--json", help="Emit a structured JSON envelope to stdout."),
) -> None:
    """Remove Seam's guidance AND MCP config from a coding agent (reverses `seam install`).

    Supports: Claude Code / Cursor / Codex / VS Code / Gemini CLI / Zed / all.
    """
    root = Path(path).resolve()
    targets = _select_targets(target)
    if targets is None:
        _fail(json_, "INVALID_INPUT", f"unknown target '{target}' (expected: {', '.join(TARGETS)} or all)")

    results: list[dict[str, Any]] = []
    for tgt in targets:
        entry: dict[str, Any] = {
            "target": tgt.name,
            "guidance": [{"action": r.action, "path": r.path} for r in tgt.uninstall_guidance(root)],
        }
        if location in tgt.supported_locations():
            res = tgt.uninstall(root, location)
            entry["mcp"] = {"action": res.action, "path": res.path}
        else:
            entry["mcp"] = {"action": "skipped", "path": None}
        results.append(entry)

    data = {"results": results}
    if json_:
        emit_json(data)
        return
    for entry in results:
        guide_str = ", ".join(f"{Path(g['path']).name}:{g['action']}" for g in entry["guidance"])
        console.print(
            f"  [bold]{entry['target']}[/bold] guidance: [dim]{guide_str}[/dim]  "
            f"MCP: [dim]{entry['mcp']['action']}[/dim]"
        )


def _fail(json_: bool, code: str, message: str) -> NoReturn:
    """Emit an error in the requested format and exit non-zero (always raises)."""
    if json_:
        emit_json_error(code, message)
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


def _render_install(data: dict[str, Any]) -> None:
    """Human-readable Rich output for `seam install`."""
    if data["print_config"]:
        for entry in data["results"]:
            console.print(f"\n[bold]{entry['target']}[/bold]")
            for prev in entry["guidance_preview"]:
                console.print(f"  [dim]{prev['path']}[/dim]")
                console.print(prev["content"])
            if entry["mcp_preview"]:
                console.print(f"  [dim]{entry['mcp_preview']['path']}  (MCP)[/dim]")
                console.print(entry["mcp_preview"]["config"])
        return

    mode = "guidance + MCP" if data["with_mcp"] else "guidance"
    console.print(f"[bold]seam install[/bold]  [dim]({mode})[/dim]")
    for entry in data["results"]:
        guide_str = ", ".join(f"{Path(g['path']).name}:{g['action']}" for g in entry["guidance"])
        console.print(f"  [green]✓ {entry['target']}[/green] guidance: [dim]{guide_str}[/dim]")
        mcp = entry["mcp"]
        if mcp is None:
            continue
        if mcp["action"] == "skipped":
            console.print(f"    [yellow]⊘ MCP skipped[/yellow] [dim]({mcp.get('reason', '')})[/dim]")
        else:
            backed = "  [yellow](backed up corrupt config)[/yellow]" if mcp.get("backed_up") else ""
            console.print(f"    [green]MCP: {mcp['action']}[/green]  [dim]{mcp['path']}[/dim]{backed}")

    for warning in data["warnings"]:
        console.print(f"  [yellow]![/yellow] {warning}")
    # Claude project-scoped MCP servers need a one-time approval the first time the agent runs.
    if data["with_mcp"] and any(
        e["target"] == "claude" and e["mcp"] and e["mcp"].get("action") in ("created", "updated")
        for e in data["results"]
    ):
        console.print("  [dim]Claude Code will prompt once to approve the project MCP server on next launch.[/dim]")
