"""Read-only federation over explicitly registered Seam indexes."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

from seam.indexer.artifact import compute_root_fingerprint, read_repository_identity
from seam.indexer.readonly import open_readonly_connection
from seam.query.graph_search import graph_search
from seam.query.schema import describe_schema
from seam.query.snippet import snippet
from seam.server.tools import handle_seam_impact
from seam.workspace.registry import RegisteredRepo

CURRENT_SCHEMA_VERSION = 15


def workspace_status(repos: list[RegisteredRepo]) -> dict[str, Any]:
    return {"repos": [validate_repo(repo) for repo in repos]}


def workspace_graph_search(
    repos: list[RegisteredRepo],
    *,
    kind: str | None = None,
    name_pattern: str | None = None,
    edge_kind: str | None = None,
    recipe: str | None = None,
    limit: int = 20,
    include_preview: bool = False,
    preview_limit: int = 3,
) -> dict[str, Any]:
    repo_results: list[dict[str, Any]] = []
    flat_items: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    per_repo_limit = max(1, limit)
    for repo in repos:
        status = validate_repo(repo)
        if status["state"] not in {"ready", "stale"}:
            warnings.extend(_repo_warnings(repo, status))
            repo_results.append(_skipped_repo(repo, status))
            continue
        conn = open_readonly_connection(repo.index_path)
        try:
            result = graph_search(
                conn,
                root=repo.root,
                kind=kind,
                name_pattern=name_pattern,
                edge_kind=edge_kind,
                recipe=recipe,
                limit=per_repo_limit,
                include_preview=include_preview,
                preview_limit=preview_limit,
            )
        finally:
            conn.close()
        result_payload = cast(dict[str, Any], result)
        if "error" in result_payload:
            warnings.append({
                "code": str(result_payload["error"]),
                "message": str(result_payload.get("message", "workspace graph search failed")),
                "repo": repo.alias,
            })
            repo_results.append(_skipped_repo(repo, status))
            continue
        # Workspace callers must never see raw local UIDs without repo identity.
        items = [_qualify_item(repo, item) for item in cast(list[dict[str, Any]], result_payload.get("items", []))]
        flat_items.extend(items)
        repo_results.append({
            "alias": repo.alias,
            "state": status["state"],
            "total": result_payload.get("total", len(items)),
            "limit": result_payload.get("limit", per_repo_limit),
            "truncated": bool(result_payload.get("has_more")),
            "items": items,
            "warnings": status.get("warnings", []) + result_payload.get("warnings", []),
            "recipe": result_payload.get("recipe"),
        })
    total_flat_items = len(flat_items)
    flat_items = flat_items[:limit]
    return {
        "repos": repo_results,
        "items": flat_items,
        "total": sum(int(repo.get("total", 0)) for repo in repo_results),
        "limit": limit,
        "truncated": any(bool(repo.get("truncated")) for repo in repo_results) or total_flat_items > limit,
        "warnings": warnings,
        "recipe": next((repo.get("recipe") for repo in repo_results if repo.get("recipe")), None),
    }


def workspace_snippet(
    repo: RegisteredRepo,
    *,
    uid: str | None,
    symbol: str | None = None,
    file: str | None = None,
    line: int | None = None,
    context_lines: int = 0,
    max_lines: int = 200,
    max_bytes: int = 20_000,
) -> dict[str, Any]:
    status = validate_repo(repo)
    if status["state"] not in {"ready", "stale"}:
        return {"error": "REPO_UNAVAILABLE", "message": f"Repo {repo.alias!r} is {status['state']}."}
    conn = open_readonly_connection(repo.index_path)
    try:
        result = snippet(
            conn,
            root=repo.root,
            uid=uid,
            symbol=symbol,
            file=file,
            line=line,
            context_lines=context_lines,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    finally:
        conn.close()
    if result.get("error"):
        return result
    result["repo"] = _repo_payload(repo)
    if result.get("uid"):
        result["local_uid"] = result["uid"]
        result["uid"] = f"{repo.alias}:{result['uid']}"
    return result


def workspace_route_callers(
    repos: list[RegisteredRepo],
    *,
    method: str | None = None,
    path: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    statuses = {repo.alias: validate_repo(repo) for repo in repos}
    ready = [repo for repo in repos if statuses[repo.alias]["state"] in {"ready", "stale"}]
    warnings = [
        warning
        for repo in repos
        if statuses[repo.alias]["state"] not in {"ready", "stale"}
        for warning in _repo_warnings(repo, statuses[repo.alias])
    ]
    routes = [
        route
        for repo in ready
        for route in _fetch_routes(repo, method=method, path=path)
    ]
    callers = [
        caller
        for repo in ready
        for caller in _fetch_http_callers(repo)
    ]
    routes_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        routes_by_symbol.setdefault(str(route["symbol"]), []).append(route)
    links: list[dict[str, Any]] = []
    for caller in callers:
        for route in routes_by_symbol.get(str(caller["target"]), []):
            links.append({
                "route": route,
                "caller": caller,
                "edge_kind": "http_calls",
                "confidence": caller["confidence"],
                "provenance": caller["provenance"],
                "derived": caller["repo"]["alias"] != route["repo"]["alias"],
            })
    total_links = len(links)
    links = links[:limit]
    return {
        "routes": routes,
        "links": links,
        "limit": limit,
        "truncated": total_links > limit,
        "warnings": warnings,
    }


def workspace_matches(
    repos: list[RegisteredRepo],
    *,
    config_key: str | None = None,
    resource_category: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    statuses = {repo.alias: validate_repo(repo) for repo in repos}
    ready = [repo for repo in repos if statuses[repo.alias]["state"] in {"ready", "stale"}]
    warnings = [
        warning
        for repo in repos
        if statuses[repo.alias]["state"] not in {"ready", "stale"}
        for warning in _repo_warnings(repo, statuses[repo.alias])
    ]
    configs_all = [
        config
        for repo in ready
        for config in _fetch_config_matches(repo, config_key=config_key)
    ]
    resources_all = [
        resource
        for repo in ready
        for resource in _fetch_resource_matches(repo, resource_category=resource_category)
    ]
    configs = configs_all[:limit]
    resources = resources_all[:limit]
    return {
        "configs": configs,
        "resources": resources,
        "limit": limit,
        "truncated": len(configs_all) > limit or len(resources_all) > limit,
        "warnings": warnings,
    }


def workspace_impact(
    repos: list[RegisteredRepo],
    target: str,
    *,
    direction: str = "upstream",
    max_depth: int = 3,
    limit: int = 25,
) -> dict[str, Any]:
    repo_results: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    cross_repo_evidence = _cross_repo_evidence(repos, target, limit=limit)
    for repo in repos:
        status = validate_repo(repo)
        if status["state"] not in {"ready", "stale"}:
            warnings.extend(_repo_warnings(repo, status))
            repo_results.append({
                "alias": repo.alias,
                "state": status["state"],
                "local_impact": None,
                "cross_repo_evidence": [],
                "warnings": status.get("warnings", []),
            })
            continue
        conn = open_readonly_connection(repo.index_path)
        try:
            local_impact = handle_seam_impact(
                conn,
                target=target,
                root=repo.root,
                direction=direction,
                max_depth=max_depth,
                include_tests=False,
                verbose=True,
                limit=limit,
            )
        finally:
            conn.close()
        repo_results.append({
            "alias": repo.alias,
            "state": status["state"],
            "local_impact": local_impact,
            "cross_repo_evidence": [
                evidence
                for evidence in cross_repo_evidence
                if repo.alias in evidence.get("repos", [])
            ],
            "warnings": status.get("warnings", []),
        })
    return {"target": target, "repos": repo_results, "warnings": warnings}


def _cross_repo_evidence(
    repos: list[RegisteredRepo],
    target: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    cap = limit if limit > 0 else 100
    evidence: list[dict[str, Any]] = []

    for link in workspace_route_callers(repos, limit=max(cap, 100)).get("links", []):
        if not link.get("derived"):
            continue
        route = link["route"]
        caller = link["caller"]
        if not (_matches_route_target(route, target) or _matches_caller_target(caller, target)):
            continue
        evidence.append({
            "kind": "http_calls",
            "repos": [caller["repo"]["alias"], route["repo"]["alias"]],
            "source_repo": caller["repo"]["alias"],
            "target_repo": route["repo"]["alias"],
            "caller": caller,
            "route": route,
            "confidence": link.get("confidence"),
            "provenance": link.get("provenance"),
        })

    configs = workspace_matches(repos, config_key=target, limit=max(cap, 100)).get("configs", [])
    configs_by_key: dict[str, list[dict[str, Any]]] = {}
    for config in configs:
        configs_by_key.setdefault(str(config["normalized_key"]), []).append(config)
    for normalized_key, matches in configs_by_key.items():
        aliases = sorted({match["repo"]["alias"] for match in matches})
        if len(aliases) < 2:
            continue
        evidence.append({
            "kind": "shared_config_key",
            "repos": aliases,
            "normalized_key": normalized_key,
            "matches": matches,
        })

    return evidence[:cap]


def _matches_route_target(route: dict[str, Any], target: str) -> bool:
    return target in {
        str(route.get("symbol", "")),
        str(route.get("handler", "")),
        str(route.get("path", "")),
        str(route.get("normalized_path", "")),
    }


def _matches_caller_target(caller: dict[str, Any], target: str) -> bool:
    return target in {
        str(caller.get("symbol", "")),
        str(caller.get("target", "")),
    }


def validate_repo(repo: RegisteredRepo) -> dict[str, Any]:
    base: dict[str, Any] = {
        "alias": repo.alias,
        "identity": {
            "git_remote": repo.git_remote,
            "git_head": repo.git_head,
            "root_fingerprint": repo.root_fingerprint,
        },
    }
    if not repo.root.exists():
        return base | {"state": "path_moved", "warnings": [_warning("PATH_MOVED", "Registered repo path no longer exists.")]}
    actual = read_repository_identity(repo.root)
    actual_fingerprint = compute_root_fingerprint(repo.root)
    if _identity_changed(repo, actual, actual_fingerprint):
        return base | {"state": "identity_changed", "warnings": [_warning("IDENTITY_CHANGED", "Registered repo identity no longer matches this path.")]}
    if not repo.index_path.exists():
        return base | {"state": "missing_index", "warnings": [_warning("NO_INDEX", "No Seam index found for this repo.")]}
    try:
        conn = open_readonly_connection(repo.index_path)
    except sqlite3.Error as exc:
        return base | {"state": "unreadable_index", "warnings": [_warning("DB_ERROR", f"Failed to open index read-only: {exc}")]}
    try:
        schema = describe_schema(conn, root=repo.root)
        schema_version = schema.get("schema_version")
        if isinstance(schema_version, int) and schema_version > CURRENT_SCHEMA_VERSION:
            return base | {
                "state": "schema_too_new",
                "schema_version": schema_version,
                "warnings": [_warning("SCHEMA_TOO_NEW", "Repo index was produced by a newer Seam schema.")],
            }
        freshness = schema["freshness"]
        state = "stale" if freshness["stale"] else "ready"
        warnings = []
        if freshness["stale"]:
            warnings.append(_warning("INDEX_STALE", freshness["reason"] or "Repo index is stale."))
        return base | {
            "state": state,
            "schema_version": schema_version,
            "counts": schema.get("counts", {}),
            "capabilities": schema.get("capabilities", {}),
            "freshness": freshness,
            "warnings": warnings,
        }
    finally:
        conn.close()


def _identity_changed(
    repo: RegisteredRepo,
    actual: dict[str, str | None],
    actual_fingerprint: str,
) -> bool:
    if repo.git_remote and actual.get("remote") and repo.git_remote != actual.get("remote"):
        return True
    if repo.git_head and actual.get("head") and repo.git_head != actual.get("head"):
        return True
    if not repo.git_remote and not repo.git_head and repo.root_fingerprint != actual_fingerprint:
        return True
    return False


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _repo_payload(repo: RegisteredRepo) -> dict[str, str]:
    return {"alias": repo.alias}


def _qualify_item(repo: RegisteredRepo, item: dict[str, Any]) -> dict[str, Any]:
    qualified = dict(item)
    local_uid = str(qualified.get("uid", ""))
    qualified["repo"] = _repo_payload(repo)
    qualified["local_uid"] = local_uid
    qualified["uid"] = f"{repo.alias}:{local_uid}"
    if qualified.get("preview"):
        qualified["preview"] = [_qualify_preview(repo, preview) for preview in qualified["preview"]]
    return qualified


def _qualify_preview(repo: RegisteredRepo, preview: dict[str, Any]) -> dict[str, Any]:
    qualified = dict(preview)
    local_uid = str(qualified.get("uid", ""))
    qualified["repo"] = _repo_payload(repo)
    qualified["local_uid"] = local_uid
    qualified["uid"] = f"{repo.alias}:{local_uid}"
    return qualified


def _skipped_repo(repo: RegisteredRepo, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "alias": repo.alias,
        "state": status["state"],
        "total": 0,
        "limit": 0,
        "truncated": False,
        "items": [],
        "warnings": status.get("warnings", []),
    }


def _repo_warnings(repo: RegisteredRepo, status: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "code": warning.get("code", status["state"]),
            "message": warning.get("message", f"Repo {repo.alias!r} skipped."),
            "repo": repo.alias,
        }
        for warning in status.get("warnings", [])
    ]


def _fetch_routes(
    repo: RegisteredRepo,
    *,
    method: str | None,
    path: str | None,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[str] = []
    if method:
        clauses.append("r.method = ?")
        params.append(method.upper())
    if path:
        normalized = path if path.startswith("/") else f"/{path}"
        clauses.append("r.normalized_path = ?")
        params.append(normalized)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = open_readonly_connection(repo.index_path)
    try:
        rows = conn.execute(
            f"""
            SELECT r.symbol_name, r.method, r.path, r.normalized_path, r.framework,
                   r.handler, r.line, r.confidence, r.provenance, f.path AS file
            FROM routes r
            JOIN files f ON f.id = r.file_id
            {where}
            ORDER BY r.symbol_name
            """,
            params,
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return [
        {
            "repo": _repo_payload(repo),
            "symbol": row["symbol_name"],
            "method": row["method"],
            "path": row["path"],
            "normalized_path": row["normalized_path"],
            "framework": row["framework"],
            "handler": row["handler"],
            "file": _rel(row["file"], repo.root),
            "line": row["line"],
            "uid": f"{repo.alias}:{_uid(row['file'], row['line'])}",
            "local_uid": _uid(row["file"], row["line"]),
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows
    ]


def _fetch_http_callers(repo: RegisteredRepo) -> list[dict[str, Any]]:
    conn = open_readonly_connection(repo.index_path)
    try:
        rows = conn.execute(
            """
            SELECT e.source_name, e.target_name, e.line, e.confidence, e.provenance,
                   f.path AS file,
                   s.start_line AS start_line
            FROM edges e
            JOIN files f ON f.id = e.file_id
            LEFT JOIN symbols s ON s.file_id = e.file_id AND s.name = e.source_name
            WHERE e.kind = 'http_calls'
            ORDER BY e.source_name
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return [
        {
            "repo": _repo_payload(repo),
            "symbol": row["source_name"],
            "target": row["target_name"],
            "file": _rel(row["file"], repo.root),
            "line": row["line"],
            "uid": f"{repo.alias}:{_uid(row['file'], row['start_line'] or row['line'])}",
            "local_uid": _uid(row["file"], row["start_line"] or row["line"]),
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows
    ]


def _uid(file_path: str, start_line: int) -> str:
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{start_line}"


def _rel(file_path: str, root: Path) -> str:
    try:
        return str(Path(file_path).resolve(strict=False).relative_to(root))
    except ValueError:
        return Path(file_path).name


def _fetch_config_matches(repo: RegisteredRepo, *, config_key: str | None) -> list[dict[str, Any]]:
    clauses = []
    params: list[str] = []
    if config_key:
        clauses.append("c.normalized_key = ?")
        params.append(_normalize_config_key(config_key))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = open_readonly_connection(repo.index_path)
    try:
        rows = conn.execute(
            f"""
            SELECT c.symbol_name, c.key, c.normalized_key, c.source_family, c.role,
                   c.value_state, c.value_category, c.line, c.confidence, c.provenance,
                   f.path AS file
            FROM config_keys c
            JOIN files f ON f.id = c.file_id
            {where}
            ORDER BY c.normalized_key
            """,
            params,
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return [
        {
            "repo": _repo_payload(repo),
            "symbol": row["symbol_name"],
            "key": row["key"],
            "normalized_key": row["normalized_key"],
            "source_family": row["source_family"],
            "role": row["role"],
            "value_state": row["value_state"],
            "value_category": row["value_category"],
            "file": _rel(row["file"], repo.root),
            "line": row["line"],
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows
    ]


def _fetch_resource_matches(
    repo: RegisteredRepo,
    *,
    resource_category: str | None,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[str] = []
    if resource_category:
        clauses.append("r.category = ?")
        params.append(resource_category)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = open_readonly_connection(repo.index_path)
    try:
        rows = conn.execute(
            f"""
            SELECT r.symbol_name, r.name, r.normalized_name, r.category, r.source_family,
                   r.line, r.confidence, r.provenance, f.path AS file
            FROM resources r
            JOIN files f ON f.id = r.file_id
            {where}
            ORDER BY r.category, r.normalized_name
            """,
            params,
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return [
        {
            "repo": _repo_payload(repo),
            "symbol": row["symbol_name"],
            "name": row["name"],
            "normalized_name": row["normalized_name"],
            "category": row["category"],
            "source_family": row["source_family"],
            "file": _rel(row["file"], repo.root),
            "line": row["line"],
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows
    ]


def _normalize_config_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.]", "_", key.strip()).upper()
