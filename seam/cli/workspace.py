"""CLI commands for explicit local cross-repo workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

from seam.cli.output import check_mutual_exclusion, emit_json, emit_json_error
from seam.workspace.federation import (
    workspace_graph_search,
    workspace_impact,
    workspace_matches,
    workspace_route_callers,
    workspace_snippet,
    workspace_status,
)
from seam.workspace.registry import (
    WorkspaceError,
    add_repo,
    create_workspace,
    list_repos,
    load_repos,
    remove_repo,
    repo_by_alias,
)

workspace_app = typer.Typer(
    name="workspace",
    help="Explicit local cross-repo workspace registry and read-only federation.",
    add_completion=False,
)
console = Console()


def _handle_workspace_error(exc: WorkspaceError, json_: bool) -> NoReturn:
    if json_:
        emit_json_error(exc.code, exc.message)
    console.print(f"[red]{exc.code}:[/red] {exc.message}")
    raise typer.Exit(code=1)


def _guard_output(json_: bool, quiet: bool) -> None:
    try:
        check_mutual_exclusion(json_=json_, quiet=quiet)
    except ValueError as exc:
        emit_json_error("INVALID_INPUT", str(exc))


def _render_status(data: dict) -> None:
    table = Table(title="workspace status")
    table.add_column("repo", style="bold")
    table.add_column("state")
    table.add_column("schema", justify="right")
    table.add_column("stale")
    for repo in data["repos"]:
        freshness = repo.get("freshness", {})
        table.add_row(
            repo["alias"],
            repo["state"],
            str(repo.get("schema_version", "")),
            str(freshness.get("stale", "")),
        )
    console.print(table)


def _emit_error_dict(result: dict, json_: bool) -> NoReturn:
    if json_:
        emit_json_error(result["error"], result.get("message", ""))
    console.print(f"[red]Error:[/red] {result.get('message', result['error'])}")
    raise typer.Exit(code=1)


@workspace_app.command(name="init")
def workspace_init_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print registry path only."),
) -> None:
    """Create an explicit workspace registry under the selected root."""
    _guard_output(json_, quiet)
    try:
        data = create_workspace(Path(path))
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        console.print(data["workspace"]["registry"])
    else:
        console.print(f"[green]Workspace registry ready:[/green] {data['workspace']['registry']}")


@workspace_app.command(name="add")
def workspace_add_cmd(
    alias: str = typer.Argument(..., help="Human-readable repo alias."),
    repo_path: str = typer.Argument(..., help="Repo root to register."),
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print added alias only."),
) -> None:
    """Add one explicitly selected repo to the workspace trust set."""
    _guard_output(json_, quiet)
    try:
        data = add_repo(Path(path), alias, Path(repo_path))
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        console.print(data["repo"]["alias"])
    else:
        console.print(f"[green]Added repo:[/green] {data['repo']['alias']}")


@workspace_app.command(name="list")
def workspace_list_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    absolute_paths: bool = typer.Option(False, "--absolute-paths", help="Include absolute repo paths."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print aliases only."),
) -> None:
    """List repos explicitly registered in a workspace."""
    _guard_output(json_, quiet)
    try:
        data = list_repos(Path(path), include_absolute_paths=absolute_paths)
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for repo in data["repos"]:
            console.print(repo["alias"])
    else:
        for repo in data["repos"]:
            console.print(repo["alias"])


@workspace_app.command(name="remove")
def workspace_remove_cmd(
    alias: str = typer.Argument(..., help="Repo alias to remove."),
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print remaining aliases only."),
) -> None:
    """Remove one repo from the workspace trust set."""
    _guard_output(json_, quiet)
    try:
        data = remove_repo(Path(path), alias)
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for repo in data.get("repos", []):
            console.print(repo["alias"])
    else:
        console.print(f"[green]Removed repo:[/green] {alias}")


@workspace_app.command(name="status")
def workspace_status_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print alias:state lines."),
) -> None:
    """Validate registered repos without mutating their indexes."""
    _guard_output(json_, quiet)
    try:
        repos = load_repos(Path(path))
        data = workspace_status(repos)
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for repo in data["repos"]:
            console.print(f"{repo['alias']}:{repo['state']}")
    else:
        _render_status(data)


@workspace_app.command(name="graph-search")
def workspace_graph_search_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    kind: str | None = typer.Option(None, "--kind", help="Symbol kind filter."),
    name_pattern: str | None = typer.Option(None, "--name", help="Symbol name glob."),
    edge_kind: str | None = typer.Option(None, "--edge-kind", help="Edge kind filter."),
    limit: int = typer.Option(20, "--limit", help="Maximum flattened results."),
    include_preview: bool = typer.Option(False, "--preview", help="Include one-hop previews."),
    preview_limit: int = typer.Option(3, "--preview-limit", help="Preview edges per result."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print repo:file:line:symbol lines."),
) -> None:
    """Search registered indexes in an explicitly selected workspace."""
    _guard_output(json_, quiet)
    try:
        data = workspace_graph_search(
            load_repos(Path(path)),
            kind=kind,
            name_pattern=name_pattern,
            edge_kind=edge_kind,
            limit=limit,
            include_preview=include_preview,
            preview_limit=preview_limit,
        )
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for item in data["items"]:
            console.print(f"{item['repo']['alias']}:{item['file']}:{item['line']}:{item['symbol']}")
    else:
        for item in data["items"]:
            console.print(f"{item['repo']['alias']}  {item['file']}:{item['line']}  {item['symbol']}")


@workspace_app.command(name="snippet")
def workspace_snippet_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    uid: str | None = typer.Option(None, "--uid", help="Workspace uid formatted as repo:local_uid."),
    repo: str | None = typer.Option(None, "--repo", help="Repo alias when uid is local."),
    symbol: str | None = typer.Option(None, "--symbol", help="Symbol name to retrieve."),
    file: str | None = typer.Option(None, "--file", help="Root-relative file path."),
    line: int | None = typer.Option(None, "--line", help="1-based source line."),
    context_lines: int = typer.Option(0, "--context-lines", help="Context lines around symbol."),
    max_lines: int = typer.Option(200, "--max-lines", help="Maximum source lines."),
    max_bytes: int = typer.Option(20_000, "--max-bytes", help="Maximum UTF-8 bytes."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print source text only."),
) -> None:
    """Retrieve source from one registered repo using a repo-qualified selector."""
    _guard_output(json_, quiet)
    try:
        local_uid = uid
        repo_alias = repo
        if uid and ":" in uid and repo is None:
            repo_alias, local_uid = uid.split(":", 1)
        if repo_alias is None:
            raise WorkspaceError("INVALID_INPUT", "Pass --repo or a workspace uid formatted as repo:local_uid.")
        target_repo = repo_by_alias(Path(path), repo_alias)
        data = workspace_snippet(
            target_repo,
            uid=local_uid,
            symbol=symbol,
            file=file,
            line=line,
            context_lines=context_lines,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if isinstance(data, dict) and data.get("error"):
        _emit_error_dict(data, json_)
    if json_:
        emit_json(data)
    elif quiet:
        if data.get("found"):
            console.print(data["source"], end="")
        else:
            raise typer.Exit(code=1)
    else:
        console.print(f"[bold]{data.get('symbol')}[/bold]  [dim]{data.get('repo', {}).get('alias')}:{data.get('file')}[/dim]")
        if data.get("source"):
            console.print(data["source"], end="")


@workspace_app.command(name="route-callers")
def workspace_route_callers_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    method: str | None = typer.Option(None, "--method", help="HTTP method filter."),
    route_path: str | None = typer.Option(None, "--path", help="Normalized route path filter."),
    limit: int = typer.Option(50, "--limit", help="Maximum matched links."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print caller -> route lines."),
) -> None:
    """Find HTTP callers in registered repos for registered route nodes."""
    _guard_output(json_, quiet)
    try:
        data = workspace_route_callers(
            load_repos(Path(path)),
            method=method,
            path=route_path,
            limit=limit,
        )
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for link in data["links"]:
            caller = link["caller"]
            route = link["route"]
            console.print(
                f"{caller['repo']['alias']}:{caller['file']}:{caller['line']}:{caller['symbol']} -> "
                f"{route['repo']['alias']}:{route['symbol']}"
            )
    else:
        for link in data["links"]:
            caller = link["caller"]
            route = link["route"]
            console.print(f"{caller['repo']['alias']} {caller['symbol']} -> {route['repo']['alias']} {route['symbol']}")


@workspace_app.command(name="matches")
def workspace_matches_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    config_key: str | None = typer.Option(None, "--config-key", help="Config key to match."),
    resource_category: str | None = typer.Option(None, "--resource-category", help="Resource category to match."),
    limit: int = typer.Option(100, "--limit", help="Maximum config/resource rows per section."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print repo:key/resource lines."),
) -> None:
    """Find no-secret config/resource evidence across registered repos."""
    _guard_output(json_, quiet)
    try:
        data = workspace_matches(
            load_repos(Path(path)),
            config_key=config_key,
            resource_category=resource_category,
            limit=limit,
        )
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for item in data["configs"]:
            console.print(f"{item['repo']['alias']}:config:{item['normalized_key']}")
        for item in data["resources"]:
            console.print(f"{item['repo']['alias']}:resource:{item['category']}:{item['normalized_name']}")
    else:
        for item in data["configs"]:
            console.print(f"{item['repo']['alias']} config {item['normalized_key']}")
        for item in data["resources"]:
            console.print(f"{item['repo']['alias']} resource {item['category']} {item['normalized_name']}")


@workspace_app.command(name="impact")
def workspace_impact_cmd(
    path: str = typer.Argument(".", help="Workspace root that owns the registry."),
    target: str = typer.Argument(..., help="Symbol to analyze in each registered repo."),
    direction: str = typer.Option("upstream", "--direction", help="upstream | downstream | both."),
    max_depth: int = typer.Option(3, "--max-depth", help="Max graph traversal depth."),
    limit: int = typer.Option(25, "--limit", help="Per-tier impact entry cap."),
    json_: bool = typer.Option(False, "--json", help="Emit structured JSON envelope."),
    quiet: bool = typer.Option(False, "--quiet", help="Print repo:found lines."),
) -> None:
    """Run opt-in per-repo impact while keeping cross-repo evidence separate."""
    _guard_output(json_, quiet)
    try:
        data = workspace_impact(
            load_repos(Path(path)),
            target,
            direction=direction,
            max_depth=max_depth,
            limit=limit,
        )
    except WorkspaceError as exc:
        _handle_workspace_error(exc, json_)
    if json_:
        emit_json(data)
    elif quiet:
        for repo in data["repos"]:
            found = bool((repo.get("local_impact") or {}).get("found"))
            console.print(f"{repo['alias']}:{found}")
    else:
        for repo in data["repos"]:
            found = bool((repo.get("local_impact") or {}).get("found"))
            console.print(f"{repo['alias']} found={found}")
