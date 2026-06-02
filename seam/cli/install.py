"""`seam install` / `seam uninstall` CLI commands.

Lives in its own module (not main.py) because main.py is already large; these
two Typer command functions are registered onto the app in main.py. The actual
config-writing logic lives in seam/installer/* — this layer only parses options,
orchestrates over the selected target(s), and renders Rich / JSON output.
"""

from pathlib import Path
from typing import NoReturn

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


def install_command(
    path: str = typer.Argument(".", help="Project root to index (default: current directory)."),
    target: str = typer.Option("claude", "--target", help="claude | cursor | codex | all"),
    location: str = typer.Option(
        "project", "--location", help="project | user (codex supports user only)."
    ),
    print_config: bool = typer.Option(
        False, "--print-config", help="Print the config that would be written; write nothing."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit a structured JSON envelope to stdout."),
) -> None:
    """Wire Seam into a coding agent's MCP config (Claude Code / Cursor / Codex).

    Writes an idempotent stdio MCP entry pointing at `seam start <project>`. Safe to
    re-run (deep-equal → unchanged) and reversible with `seam uninstall`.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        _fail(json_, "INVALID_INPUT", f"'{root}' is not a directory.")

    targets = _select_targets(target)
    if targets is None:
        _fail(json_, "INVALID_INPUT", f"unknown target '{target}' (expected: {', '.join(TARGETS)} or all)")

    command, found = resolve_seam_command()
    args = ["start", str(root)]
    index_present = config.get_db_path(root).exists()

    results: list[dict] = []
    for tgt in targets:
        if location not in tgt.supported_locations():
            # Explicit single target + bad location = user error; under `all`, just skip it.
            if target != _ALL:
                _fail(
                    json_,
                    "INVALID_INPUT",
                    f"target '{tgt.name}' does not support location '{location}' "
                    f"(supports: {', '.join(tgt.supported_locations())})",
                )
            results.append(
                {
                    "target": tgt.name,
                    "action": "skipped",
                    "reason": f"location '{location}' unsupported (supports {tgt.supported_locations()})",
                    "path": None,
                }
            )
            continue
        if print_config:
            results.append(
                {
                    "target": tgt.name,
                    "action": "preview",
                    "path": str(tgt.config_path(root, location)),
                    "config": tgt.render_entry(command, args),
                }
            )
            continue
        res = tgt.install(root, location, command, args)
        results.append(
            {"target": tgt.name, "action": res.action, "path": res.path, "backed_up": res.backed_up}
        )

    warnings: list[str] = []
    if not found:
        warnings.append(
            "Could not locate the 'seam' executable — wrote the bare command 'seam' "
            "(valid once Seam is on PATH / published)."
        )
    if not index_present:
        warnings.append(f"No index at {root}/.seam — run 'seam init' before the agent starts the server.")

    data = {
        "seam_command": command,
        "args": args,
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
    target: str = typer.Option("claude", "--target", help="claude | cursor | codex | all"),
    location: str = typer.Option("project", "--location", help="project | user (codex: user only)."),
    json_: bool = typer.Option(False, "--json", help="Emit a structured JSON envelope to stdout."),
) -> None:
    """Remove the Seam MCP entry from a coding agent's config (reverses `seam install`)."""
    root = Path(path).resolve()
    targets = _select_targets(target)
    if targets is None:
        _fail(json_, "INVALID_INPUT", f"unknown target '{target}' (expected: {', '.join(TARGETS)} or all)")

    results: list[dict] = []
    for tgt in targets:
        if location not in tgt.supported_locations():
            if target != _ALL:
                _fail(
                    json_,
                    "INVALID_INPUT",
                    f"target '{tgt.name}' does not support location '{location}' "
                    f"(supports: {', '.join(tgt.supported_locations())})",
                )
            results.append({"target": tgt.name, "action": "skipped", "path": None})
            continue
        res = tgt.uninstall(root, location)
        results.append({"target": tgt.name, "action": res.action, "path": res.path})

    data = {"results": results}
    if json_:
        emit_json(data)
        return
    for entry in results:
        console.print(f"  [bold]{entry['target']}[/bold]: {entry['action']}  [dim]{entry.get('path') or ''}[/dim]")


def _fail(json_: bool, code: str, message: str) -> NoReturn:
    """Emit an error in the requested format and exit non-zero (always raises)."""
    if json_:
        emit_json_error(code, message)
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


def _render_install(data: dict) -> None:
    """Human-readable Rich output for `seam install`."""
    if data["print_config"]:
        for entry in data["results"]:
            console.print(f"\n[bold]{entry['target']}[/bold] → [dim]{entry['path']}[/dim]")
            console.print(entry["config"])
        return

    console.print(f"[bold]seam install[/bold]  [dim]command: {data['seam_command']} {' '.join(data['args'])}[/dim]")
    for entry in data["results"]:
        if entry["action"] == "skipped":
            console.print(f"  [yellow]⊘ {entry['target']}[/yellow]: skipped [dim]({entry['reason']})[/dim]")
            continue
        backed = "  [yellow](backed up corrupt config)[/yellow]" if entry.get("backed_up") else ""
        console.print(f"  [green]✓ {entry['target']}[/green]: {entry['action']}  [dim]{entry['path']}[/dim]{backed}")

    for warning in data["warnings"]:
        console.print(f"  [yellow]![/yellow] {warning}")
    # Claude project-scoped servers need a one-time approval the first time the agent runs.
    if any(r.get("target") == "claude" and r.get("action") in ("created", "updated") for r in data["results"]):
        console.print("  [dim]Claude Code will prompt once to approve the project MCP server on next launch.[/dim]")
