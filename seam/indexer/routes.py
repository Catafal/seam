"""Conservative HTTP route extraction for first-class route graph nodes.

Owns: turning visible framework route declarations and literal client calls into
route symbols, route metadata, and graph edges.
Does not own: normal symbol/call extraction, route prefix inference, runtime
server discovery, OpenAPI fetching, or arbitrary URL solving.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tree_sitter import Node

from seam.indexer.graph_common import Edge, RouteMetadata, Symbol, _find_enclosing_function, _text

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
_PY_DECORATOR_RE = re.compile(
    r"^@(?P<receiver>[A-Za-z_][\w.]*)\.(?P<action>get|post|put|patch|delete|options|head|route|api_route)\s*\((?P<args>.*)\)$",
    re.IGNORECASE | re.DOTALL,
)
_PY_METHODS_RE = re.compile(r"\bmethods\s*=\s*(?P<value>\[[^\]]*\]|\([^\)]*\)|\{[^\}]*\})")
_PARAM_RE = re.compile(r"(:[A-Za-z_][\w]*|\{[^}/]+\}|<[^>/]+>)")
_PY_ASSIGNMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<factory>(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _route_name(method: str, path: str) -> str:
    return f"ROUTE {method.upper()} {normalize_route_path(path)}"


def normalize_route_path(path: str) -> str:
    """Normalize framework path parameters without solving arbitrary URL syntax."""
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = _PARAM_RE.sub("{param}", path)
    return re.sub(r"/+", "/", normalized)


def _literal_string(value: str) -> str | None:
    try:
        parsed = ast.literal_eval(value.strip())
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, str) else None


def _first_literal_arg(args: str) -> str | None:
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for char in args:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            break
        current.append(char)
    return _literal_string("".join(current))


def _methods_from_decorator(action: str, args: str) -> list[str]:
    action_lower = action.lower()
    if action_lower in {"get", "post", "put", "patch", "delete", "options", "head"}:
        return [action_lower.upper()]
    match = _PY_METHODS_RE.search(args)
    if not match:
        return ["GET"]
    try:
        parsed = ast.literal_eval(match.group("value"))
    except (SyntaxError, ValueError):
        return ["GET"]
    methods = [str(method).upper() for method in parsed if str(method).upper() in _HTTP_METHODS]
    return methods or ["GET"]


def _python_framework_receivers(root: Node) -> dict[str, str]:
    """Return framework receivers proved by local constructor/import evidence."""
    source = _text(root)
    fastapi_factories = {"FastAPI", "APIRouter"}
    flask_factories = {"Flask", "Blueprint"}
    if "import fastapi" in source:
        fastapi_factories.update({"fastapi.FastAPI", "fastapi.APIRouter"})
    if "import flask" in source:
        flask_factories.update({"flask.Flask", "flask.Blueprint"})

    receivers: dict[str, str] = {}
    for match in _PY_ASSIGNMENT_RE.finditer(source):
        factory = match.group("factory")
        if factory in fastapi_factories:
            receivers[match.group("name")] = "fastapi"
        elif factory in flask_factories:
            receivers[match.group("name")] = "flask"
    return receivers


def _route_symbol(
    *,
    method: str,
    path: str,
    file: str,
    line: int,
    handler: str | None,
) -> Symbol:
    name = _route_name(method, path)
    signature = f"{method.upper()} {path}"
    if handler:
        signature = f"{signature} -> {handler}"
    return Symbol(
        name=name,
        kind="route",
        file=file,
        start_line=line,
        end_line=line,
        docstring=None,
        signature=signature,
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=name,
    )


def _python_decorator_routes(
    root: Node,
    symbols: list[Symbol],
    filepath: Path,
) -> tuple[list[Symbol], list[Edge], list[RouteMetadata]]:
    route_symbols: list[Symbol] = []
    route_edges: list[Edge] = []
    route_metadata: list[RouteMetadata] = []
    file_str = str(filepath)
    receivers = _python_framework_receivers(root)
    for symbol in symbols:
        decorators = symbol.get("decorators") or []
        for decorator in decorators:
            match = _PY_DECORATOR_RE.match(decorator.strip())
            if not match:
                continue
            receiver = match.group("receiver").split(".")[0]
            framework = receivers.get(receiver)
            if framework is None:
                continue
            action = match.group("action").lower()
            if framework == "fastapi" and action == "route":
                continue
            if framework == "flask" and action != "route":
                continue
            path = _first_literal_arg(match.group("args"))
            if not path:
                continue
            provenance = f"python-{framework}-decorator"
            for method in _methods_from_decorator(action, match.group("args")):
                route_name = _route_name(method, path)
                route_symbols.append(_route_symbol(
                    method=method,
                    path=path,
                    file=file_str,
                    line=symbol["start_line"],
                    handler=symbol["name"],
                ))
                route_metadata.append(RouteMetadata(
                    symbol_name=route_name,
                    method=method,
                    path=path,
                    normalized_path=normalize_route_path(path),
                    framework=framework,
                    handler=symbol["name"],
                    line=symbol["start_line"],
                    confidence="EXTRACTED",
                    provenance=provenance,
                ))
                route_edges.append(Edge(
                    source=route_name,
                    target=symbol["name"],
                    kind="call",
                    file=file_str,
                    line=symbol["start_line"],
                    confidence="EXTRACTED",
                ))
    return route_symbols, route_edges, route_metadata


def _walk(node: Node) -> list[Node]:
    out = [node]
    for child in node.children:
        out.extend(_walk(child))
    return out


def _ts_literal_string(node: Node | None) -> str | None:
    if node is None:
        return None
    text = _text(node).strip()
    if node.type == "string":
        return _literal_string(text)
    if node.type == "template_string" and "${" not in text:
        return text[1:-1]
    return None


def _first_arg(arguments: Node | None) -> Node | None:
    if arguments is None:
        return None
    return next(iter(arguments.named_children), None)


def _second_arg(arguments: Node | None) -> Node | None:
    if arguments is None:
        return None
    children = list(arguments.named_children)
    return children[1] if len(children) > 1 else None


def _member_call_parts(call: Node) -> tuple[str, str, Node | None] | None:
    callee = call.child_by_field_name("function")
    if callee is None or callee.type != "member_expression":
        return None
    obj = callee.child_by_field_name("object")
    prop = callee.child_by_field_name("property")
    if obj is None or prop is None:
        return None
    return _text(obj), _text(prop), call.child_by_field_name("arguments")


def _identifier_call_name(call: Node) -> str | None:
    callee = call.child_by_field_name("function")
    if callee is not None and callee.type == "identifier":
        return _text(callee)
    return None


def _ts_object_literal_value(obj: Node | None, key: str) -> str | None:
    if obj is None or obj.type != "object":
        return None
    for pair in obj.named_children:
        if pair.type != "pair":
            continue
        key_node = pair.child_by_field_name("key")
        value_node = pair.child_by_field_name("value")
        if key_node is None or value_node is None:
            continue
        if _text(key_node).strip("'\"") == key:
            return _ts_literal_string(value_node)
    return None


def _ts_express_receivers(root: Node) -> set[str]:
    receivers: set[str] = set()
    for node in _walk(root):
        if node.type != "variable_declarator":
            continue
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or value is None or value.type != "call_expression":
            continue
        call_name = _identifier_call_name(value)
        member = _member_call_parts(value)
        if call_name == "express":
            receivers.add(_text(name))
        elif call_name == "Router":
            receivers.add(_text(name))
        elif member and member[0] == "express" and member[1] == "Router":
            receivers.add(_text(name))
    return receivers


def _handler_arg(arguments: Node | None) -> str | None:
    node = _second_arg(arguments)
    if node is not None and node.type == "identifier":
        return _text(node)
    return None


def _typescript_routes(
    root: Node,
    language: str,
    filepath: Path,
) -> tuple[list[Symbol], list[Edge], list[RouteMetadata]]:
    route_symbols: list[Symbol] = []
    route_edges: list[Edge] = []
    route_metadata: list[RouteMetadata] = []
    file_str = str(filepath)
    receivers = _ts_express_receivers(root)
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        member = _member_call_parts(node)
        if member is not None:
            receiver, action, arguments = member
            action_lower = action.lower()
            path = _ts_literal_string(_first_arg(arguments))
            if (
                receiver in receivers
                and path
                and action_lower in {"get", "post", "put", "patch", "delete", "options", "head"}
            ):
                method = action_lower.upper()
                handler = _handler_arg(arguments)
                route_name = _route_name(method, path)
                line = node.start_point[0] + 1
                route_symbols.append(_route_symbol(
                    method=method,
                    path=path,
                    file=file_str,
                    line=line,
                    handler=handler,
                ))
                route_metadata.append(RouteMetadata(
                    symbol_name=route_name,
                    method=method,
                    path=path,
                    normalized_path=normalize_route_path(path),
                    framework="express",
                    handler=handler,
                    line=line,
                    confidence="EXTRACTED",
                    provenance="typescript-express-registration"
                    if language == "typescript"
                    else "javascript-express-registration",
                ))
                if handler:
                    route_edges.append(Edge(
                        source=route_name,
                        target=handler,
                        kind="call",
                        file=file_str,
                        line=line,
                        confidence="EXTRACTED",
                    ))
                continue

            if receiver == "axios" and action_lower in _HTTP_METHODS_LOWER and path:
                source = _find_enclosing_function(node, language)
                if source:
                    route_edges.append(Edge(
                        source=source,
                        target=_route_name(action_lower.upper(), path),
                        kind="http_calls",
                        file=file_str,
                        line=node.start_point[0] + 1,
                        confidence="EXTRACTED",
                    ))
                continue

        call_name = _identifier_call_name(node)
        arguments = node.child_by_field_name("arguments")
        if call_name == "fetch":
            path = _ts_literal_string(_first_arg(arguments))
            if not path:
                continue
            method = (_ts_object_literal_value(_second_arg(arguments), "method") or "GET").upper()
            if method not in _HTTP_METHODS:
                continue
            source = _find_enclosing_function(node, language)
            if source:
                route_edges.append(Edge(
                    source=source,
                    target=_route_name(method, path),
                    kind="http_calls",
                    file=file_str,
                    line=node.start_point[0] + 1,
                    confidence="EXTRACTED",
                ))
        elif call_name == "axios":
            config = _first_arg(arguments)
            path = _ts_object_literal_value(config, "url")
            method = (_ts_object_literal_value(config, "method") or "").upper()
            source = _find_enclosing_function(node, language)
            if source and path and method in _HTTP_METHODS:
                route_edges.append(Edge(
                    source=source,
                    target=_route_name(method, path),
                    kind="http_calls",
                    file=file_str,
                    line=node.start_point[0] + 1,
                    confidence="EXTRACTED",
                ))
    return route_symbols, route_edges, route_metadata


_HTTP_METHODS_LOWER = {method.lower() for method in _HTTP_METHODS}


def extract_routes(
    root: Node,
    language: str,
    filepath: Path,
    symbols: list[Symbol],
) -> tuple[list[Symbol], list[Edge], list[RouteMetadata]]:
    """Extract route graph additions for one parsed source file.

    The interface intentionally mirrors normal symbol/edge extraction so the
    pipeline can append route evidence before one atomic `upsert_file` call.
    """
    if language == "python":
        return _python_decorator_routes(root, symbols, filepath)
    if language in {"typescript", "javascript"}:
        return _typescript_routes(root, language, filepath)
    return [], [], []
