"""No-secret Docker Compose and Dockerfile infrastructure extraction."""

from __future__ import annotations

import re
from pathlib import Path

from seam.indexer.graph_common import ConfigMetadata, Edge, ResourceMetadata, Symbol

# These markers usually mean "resolved at build/run time"; storing them as
# resource names would turn runtime expressions into false static infra nodes.
_INFRA_DYNAMIC_MARKERS = ("${", "$(", "`")
_DOCKER_FROM_RE = re.compile(r"^FROM\s+(?P<body>.+)$", re.IGNORECASE)
_DOCKER_EXPOSE_RE = re.compile(r"^EXPOSE\s+(?P<ports>.+)$", re.IGNORECASE)
_DOCKER_ARG_RE = re.compile(r"^ARG\s+(?P<body>.+)$", re.IGNORECASE)
_DOCKER_ENV_RE = re.compile(r"^ENV\s+(?P<body>.+)$", re.IGNORECASE)
_DOCKER_COPY_FROM_RE = re.compile(
    r"^COPY\s+.*--from=(?P<stage>[A-Za-z_][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)


def is_dockerfile_name(name: str) -> bool:
    lower_name = name.lower()
    return (
        lower_name == "dockerfile"
        or lower_name.startswith("dockerfile.")
        or lower_name.endswith(".dockerfile")
    )


def normalize_config_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.]", "_", key.strip()).upper()


def normalize_resource_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@/-]", "_", name.strip()).upper()


def config_symbol_name(key: str) -> str:
    return f"CONFIG {normalize_config_key(key)}"


def resource_symbol_name(category: str, name: str) -> str:
    return f"RESOURCE {category} {normalize_resource_name(name)}"


def _symbol(name: str, kind: str, file: str, line: int, signature: str) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
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


def _resource_metadata(
    *,
    name: str,
    category: str,
    source_family: str,
    line: int,
    provenance: str,
    confidence: str = "INFERRED",
) -> ResourceMetadata:
    normalized = normalize_resource_name(name)
    return ResourceMetadata(
        symbol_name=resource_symbol_name(category, name),
        name=name,
        normalized_name=normalized,
        category=category,
        source_family=source_family,
        line=line,
        confidence=confidence,  # type: ignore[typeddict-item]
        provenance=provenance,
    )


def _config_metadata(
    *,
    key: str,
    source_family: str,
    role: str,
    line: int,
    provenance: str,
) -> ConfigMetadata:
    normalized = normalize_config_key(key)
    return ConfigMetadata(
        symbol_name=config_symbol_name(key),
        key=key,
        normalized_key=normalized,
        source_family=source_family,
        role=role,
        value_state="key-only",
        value_category=None,
        line=line,
        confidence="EXTRACTED",
        provenance=provenance,
    )


def _resource_for_key(key: str) -> tuple[str, str] | None:
    normalized = normalize_config_key(key)
    if "DATABASE" in normalized or normalized in {"DB_URL", "DB_DSN"}:
        return "database", "DATABASE"
    if "REDIS" in normalized or "CACHE" in normalized:
        return "cache", "REDIS" if "REDIS" in normalized else normalized
    if "QUEUE" in normalized:
        return "queue", normalized
    if "BUCKET" in normalized or "S3" in normalized or "STORAGE" in normalized:
        return "storage", normalized
    if normalized.endswith("_URL") and any(token in normalized for token in ("API", "SERVICE")):
        return "external", normalized.removesuffix("_URL")
    return None


def _is_dynamic_infra_value(value: str) -> bool:
    return any(marker in value for marker in _INFRA_DYNAMIC_MARKERS)


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        if char == "#" and quote is None:
            return value[:index].rstrip()
    return value.strip()


def _strip_quotes(value: str) -> str:
    value = _strip_inline_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


def _split_yaml_key_value(stripped: str) -> tuple[str, str] | None:
    if ":" not in stripped or stripped.startswith("-"):
        return None
    key, value = stripped.split(":", 1)
    key = key.strip().strip("\"'")
    if not key:
        return None
    return key, _strip_quotes(value)


def _add_edge(
    edges: list[Edge],
    *,
    source: str,
    target: str,
    kind: str,
    filepath: Path,
    line: int,
    confidence: str = "EXTRACTED",
    provenance: str | None = None,
) -> None:
    edge = Edge(
        source=source,
        target=target,
        kind=kind,
        file=str(filepath),
        line=line,
        confidence=confidence,  # type: ignore[typeddict-item]
    )
    if provenance is not None:
        edge["provenance"] = provenance
    edges.append(edge)


def _add_resource(
    *,
    name: str,
    category: str,
    source_family: str,
    line: int,
    provenance: str,
    filepath: Path,
    symbols: dict[str, Symbol],
    resources: dict[str, ResourceMetadata],
    confidence: str = "EXTRACTED",
) -> str:
    metadata = _resource_metadata(
        name=name,
        category=category,
        source_family=source_family,
        line=line,
        provenance=provenance,
        confidence=confidence,
    )
    resources.setdefault(metadata["symbol_name"], metadata)
    symbols.setdefault(
        metadata["symbol_name"],
        _symbol(
            metadata["symbol_name"],
            "resource",
            str(filepath),
            line,
            f"{category} resource {metadata['normalized_name']}",
        ),
    )
    return metadata["symbol_name"]


def _add_config(
    *,
    key: str,
    source_family: str,
    role: str,
    line: int,
    provenance: str,
    filepath: Path,
    symbols: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
    source_symbol: str | None = None,
    source_edge_provenance: str | None = None,
) -> None:
    symbol_name = config_symbol_name(key)
    symbols.setdefault(
        symbol_name,
        _symbol(symbol_name, "config", str(filepath), line, f"config {normalize_config_key(key)}"),
    )
    configs.append(
        _config_metadata(
            key=key,
            source_family=source_family,
            role=role,
            line=line,
            provenance=provenance,
        )
    )
    if source_symbol is not None:
        _add_edge(
            edges,
            source=source_symbol,
            target=symbol_name,
            kind="configures",
            filepath=filepath,
            line=line,
            provenance=source_edge_provenance,
        )
    resource = _resource_for_key(key)
    if resource is None:
        return
    category, resource_name = resource
    target = _add_resource(
        name=resource_name,
        category=category,
        source_family=source_family,
        line=line,
        provenance=f"{provenance}-resource",
        filepath=filepath,
        symbols=symbols,
        resources=resources,
        confidence="INFERRED",
    )
    _add_edge(
        edges,
        source=symbol_name,
        target=target,
        kind="configures",
        filepath=filepath,
        line=line,
        confidence="INFERRED",
        provenance=f"{provenance}-resource",
    )


def extract_compose_resources(
    path: Path,
    text: str,
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    # Keep this line-oriented: it preserves useful evidence line numbers while
    # avoiding a general-purpose Compose interpreter that could normalize secrets.
    symbols: dict[str, Symbol] = {}
    edges: list[Edge] = []
    configs: list[ConfigMetadata] = []
    resources: dict[str, ResourceMetadata] = {}
    for service, service_line, service_indent, block in _compose_service_blocks(text.splitlines()):
        service_symbol = _add_resource(
            name=service,
            category="service",
            source_family="compose",
            line=service_line,
            provenance="compose-service",
            filepath=path,
            symbols=symbols,
            resources=resources,
        )
        _extract_compose_service_block(
            path=path,
            service_symbol=service_symbol,
            service_indent=service_indent,
            block=block,
            symbols=symbols,
            edges=edges,
            configs=configs,
            resources=resources,
        )
    return list(symbols.values()), edges, configs, list(resources.values())


def _compose_service_blocks(
    lines: list[str],
) -> list[tuple[str, int, int, list[tuple[int, int, str]]]]:
    services_indent: int | None = None
    for line_no, raw in enumerate(lines, start=1):
        split = _split_yaml_key_value(raw.strip())
        if split and split[0] == "services" and split[1] == "":
            services_indent = _indent(raw)
            start_line = line_no + 1
            break
    else:
        return []

    blocks: list[tuple[str, int, int, list[tuple[int, int, str]]]] = []
    current_name: str | None = None
    current_line = 0
    current_indent = 0
    current_block: list[tuple[int, int, str]] = []
    service_indent: int | None = None
    for line_no, raw in enumerate(lines[start_line - 1 :], start=start_line):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent(raw)
        if indent <= services_indent:
            break
        split = _split_yaml_key_value(stripped)
        if split and split[1] == "" and not stripped.startswith("-"):
            if service_indent is None:
                service_indent = indent
            if indent == service_indent:
                if current_name is not None:
                    blocks.append((current_name, current_line, current_indent, current_block))
                current_name = split[0]
                current_line = line_no
                current_indent = indent
                current_block = []
                continue
        if current_name is not None:
            current_block.append((line_no, indent, stripped))
    if current_name is not None:
        blocks.append((current_name, current_line, current_indent, current_block))
    return blocks


def _extract_compose_service_block(
    *,
    path: Path,
    service_symbol: str,
    service_indent: int,
    block: list[tuple[int, int, str]],
    symbols: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
) -> None:
    index = 0
    while index < len(block):
        line_no, indent, stripped = block[index]
        split = _split_yaml_key_value(stripped)
        if split is None or indent != service_indent + 2:
            index += 1
            continue
        key, value = split
        child_block: list[tuple[int, int, str]] = []
        lookahead = index + 1
        while lookahead < len(block) and block[lookahead][1] > indent:
            child_block.append(block[lookahead])
            lookahead += 1
        _extract_compose_property(
            path=path,
            service_symbol=service_symbol,
            key=key,
            value=value,
            line_no=line_no,
            child_block=child_block,
            symbols=symbols,
            edges=edges,
            configs=configs,
            resources=resources,
        )
        index = lookahead


def _extract_compose_property(
    *,
    path: Path,
    service_symbol: str,
    key: str,
    value: str,
    line_no: int,
    child_block: list[tuple[int, int, str]],
    symbols: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
) -> None:
    if key == "image" and value and not _is_dynamic_infra_value(value):
        target = _add_resource(
            name=value,
            category="image",
            source_family="compose",
            line=line_no,
            provenance="compose-image",
            filepath=path,
            symbols=symbols,
            resources=resources,
        )
        _add_edge(
            edges,
            source=service_symbol,
            target=target,
            kind="uses",
            filepath=path,
            line=line_no,
            provenance="compose-service-image",
        )
    elif key == "build":
        _extract_compose_build(
            path=path,
            value=value,
            line_no=line_no,
            service_symbol=service_symbol,
            child_block=child_block,
            symbols=symbols,
            edges=edges,
            resources=resources,
        )
    elif key == "ports":
        _extract_compose_ports(path, service_symbol, child_block, symbols, edges, resources)
    elif key == "environment":
        _extract_compose_environment(
            path, service_symbol, child_block, symbols, edges, configs, resources
        )
    elif key == "depends_on":
        _extract_compose_references(
            path,
            service_symbol,
            child_block,
            "service",
            "compose-service-dependency",
            "compose-service-depends-on",
            symbols,
            edges,
            resources,
        )
    elif key == "env_file":
        values = [(line_no, value)] if value else _compose_reference_values(child_block)
        _extract_compose_references_from_values(
            path,
            service_symbol,
            values,
            "env_file",
            "compose-env-file",
            "compose-service-env-file",
            symbols,
            edges,
            resources,
        )
    elif key in {"volumes", "networks"}:
        category = "volume" if key == "volumes" else "network"
        values = []
        for resource_line, raw_resource in _compose_reference_values(child_block):
            resource_name = (
                _compose_volume_name(raw_resource)
                if category == "volume"
                else _compose_named_reference(raw_resource)
            )
            if resource_name is not None:
                values.append((resource_line, resource_name))
        _extract_compose_references_from_values(
            path,
            service_symbol,
            values,
            category,
            f"compose-{category}",
            f"compose-service-{category}",
            symbols,
            edges,
            resources,
        )


def _extract_compose_build(
    *,
    path: Path,
    value: str,
    line_no: int,
    service_symbol: str,
    child_block: list[tuple[int, int, str]],
    symbols: dict[str, Symbol],
    edges: list[Edge],
    resources: dict[str, ResourceMetadata],
) -> None:
    context_value = value if value else None
    dockerfile_value: str | None = None
    dockerfile_line = line_no
    for child_line, _child_indent, child in child_block:
        split = _split_yaml_key_value(child)
        if split is None:
            continue
        key, child_value = split
        if key == "context" and child_value:
            context_value = child_value
        elif key == "dockerfile" and child_value:
            dockerfile_value = child_value
            dockerfile_line = child_line
    if context_value and not _is_dynamic_infra_value(context_value):
        _add_resource_edge(
            path,
            service_symbol,
            context_value,
            "build_context",
            line_no,
            "compose-build-context",
            "compose-service-build-context",
            symbols,
            edges,
            resources,
        )
    if dockerfile_value and not _is_dynamic_infra_value(dockerfile_value):
        _add_resource_edge(
            path,
            service_symbol,
            dockerfile_value,
            "dockerfile",
            dockerfile_line,
            "compose-build-dockerfile",
            "compose-service-dockerfile",
            symbols,
            edges,
            resources,
        )


def _add_resource_edge(
    path: Path,
    source: str,
    name: str,
    category: str,
    line: int,
    provenance: str,
    edge_provenance: str,
    symbols: dict[str, Symbol],
    edges: list[Edge],
    resources: dict[str, ResourceMetadata],
) -> None:
    target = _add_resource(
        name=name,
        category=category,
        source_family="compose" if provenance.startswith("compose") else "dockerfile",
        line=line,
        provenance=provenance,
        filepath=path,
        symbols=symbols,
        resources=resources,
    )
    _add_edge(
        edges,
        source=source,
        target=target,
        kind="uses",
        filepath=path,
        line=line,
        provenance=edge_provenance,
    )


def _extract_compose_ports(
    path: Path,
    service_symbol: str,
    child_block: list[tuple[int, int, str]],
    symbols: dict[str, Symbol],
    edges: list[Edge],
    resources: dict[str, ResourceMetadata],
) -> None:
    for port_line, _port_indent, port_value in _yaml_list_values(child_block):
        port_name = _compose_port_name(port_value)
        if port_name is None:
            continue
        _add_resource_edge(
            path,
            service_symbol,
            port_name,
            "port",
            port_line,
            "compose-port",
            "compose-service-port",
            symbols,
            edges,
            resources,
        )


def _extract_compose_environment(
    path: Path,
    service_symbol: str,
    child_block: list[tuple[int, int, str]],
    symbols: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
) -> None:
    for env_line, env_key in _compose_environment_keys(child_block):
        _add_config(
            key=env_key,
            source_family="compose",
            role="declaration",
            line=env_line,
            provenance="compose-env-key",
            filepath=path,
            symbols=symbols,
            edges=edges,
            configs=configs,
            resources=resources,
            source_symbol=service_symbol,
            source_edge_provenance="compose-service-env",
        )


def _extract_compose_references(
    path: Path,
    service_symbol: str,
    child_block: list[tuple[int, int, str]],
    category: str,
    provenance: str,
    edge_provenance: str,
    symbols: dict[str, Symbol],
    edges: list[Edge],
    resources: dict[str, ResourceMetadata],
) -> None:
    _extract_compose_references_from_values(
        path,
        service_symbol,
        _compose_reference_values(child_block),
        category,
        provenance,
        edge_provenance,
        symbols,
        edges,
        resources,
    )


def _extract_compose_references_from_values(
    path: Path,
    service_symbol: str,
    values: list[tuple[int, str]],
    category: str,
    provenance: str,
    edge_provenance: str,
    symbols: dict[str, Symbol],
    edges: list[Edge],
    resources: dict[str, ResourceMetadata],
) -> None:
    for resource_line, resource_name in values:
        if _is_dynamic_infra_value(resource_name):
            continue
        _add_resource_edge(
            path,
            service_symbol,
            resource_name,
            category,
            resource_line,
            provenance,
            edge_provenance,
            symbols,
            edges,
            resources,
        )


def _yaml_list_values(block: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    values: list[tuple[int, int, str]] = []
    for line_no, indent, stripped in block:
        if not stripped.startswith("-"):
            continue
        value = _strip_quotes(stripped[1:].strip())
        if value:
            values.append((line_no, indent, value))
    return values


def _compose_environment_keys(block: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    keys: list[tuple[int, str]] = []
    for line_no, _indent_value, stripped in block:
        if stripped.startswith("-"):
            value = _strip_quotes(stripped[1:].strip())
            key = value.split("=", 1)[0].strip()
        else:
            split = _split_yaml_key_value(stripped)
            if split is None:
                continue
            key = split[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
            keys.append((line_no, key))
    return keys


def _compose_reference_values(block: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    top_indent = min((indent for _line_no, indent, _stripped in block), default=None)
    for line_no, _indent_value, stripped in block:
        if top_indent is not None and _indent_value != top_indent:
            continue
        if stripped.startswith("-"):
            value = _strip_quotes(stripped[1:].strip())
        else:
            split = _split_yaml_key_value(stripped)
            if split is None:
                continue
            value = split[0]
        value = _strip_quotes(value)
        if value:
            values.append((line_no, value))
    return values


def _compose_named_reference(value: str) -> str | None:
    value = _strip_quotes(value)
    if not value or _is_dynamic_infra_value(value):
        return None
    return value


def _compose_volume_name(value: str) -> str | None:
    value = _strip_quotes(value)
    if not value or _is_dynamic_infra_value(value):
        return None
    source = value.split(":", 1)[0].strip()
    # Bind mounts often expose local machine paths rather than stable infra names.
    if not source or source.startswith((".", "/", "~")):
        return None
    return source


def _compose_port_name(value: str) -> str | None:
    value = _strip_quotes(value)
    if _is_dynamic_infra_value(value):
        return None
    protocol = "tcp"
    if "/" in value:
        value, protocol = value.rsplit("/", 1)
    parts = [part for part in value.split(":") if part]
    if not parts or any(not part.isdigit() for part in parts[-2:]):
        return None
    if len(parts) >= 2:
        return f"{parts[-2]}-{parts[-1]}/{protocol.lower()}"
    return f"{parts[-1]}/{protocol.lower()}"


def extract_dockerfile_resources(
    path: Path,
    text: str,
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    symbols: dict[str, Symbol] = {}
    edges: list[Edge] = []
    configs: list[ConfigMetadata] = []
    resources: dict[str, ResourceMetadata] = {}
    dockerfile_symbol = _add_resource(
        name=path.name,
        category="dockerfile",
        source_family="dockerfile",
        line=1,
        provenance="dockerfile-file",
        filepath=path,
        symbols=symbols,
        resources=resources,
    )
    current_stage: str | None = None
    stages: dict[str, str] = {}
    for line_no, raw in enumerate(_join_dockerfile_lines(text), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if match := _DOCKER_FROM_RE.match(stripped):
            current_stage = _extract_dockerfile_from(
                path, dockerfile_symbol, match, line_no, stages, symbols, edges, resources
            )
            continue
        source = current_stage or dockerfile_symbol
        if match := _DOCKER_EXPOSE_RE.match(stripped):
            for raw_port in match.group("ports").split():
                port_name = _compose_port_name(raw_port)
                if port_name is not None:
                    _add_resource_edge(
                        path,
                        source,
                        port_name,
                        "port",
                        line_no,
                        "dockerfile-expose",
                        "dockerfile-stage-port",
                        symbols,
                        edges,
                        resources,
                    )
            continue
        if match := _DOCKER_ARG_RE.match(stripped):
            _extract_dockerfile_key(
                path,
                source,
                match.group("body").split("=", 1)[0].strip(),
                line_no,
                "dockerfile-arg-key",
                "dockerfile-stage-arg",
                symbols,
                edges,
                configs,
                resources,
            )
            continue
        if match := _DOCKER_ENV_RE.match(stripped):
            key = match.group("body").split("=", 1)[0].split(maxsplit=1)[0].strip()
            _extract_dockerfile_key(
                path,
                source,
                key,
                line_no,
                "dockerfile-env-key",
                "dockerfile-stage-env",
                symbols,
                edges,
                configs,
                resources,
            )
            continue
        if match := _DOCKER_COPY_FROM_RE.match(stripped):
            target = stages.get(match.group("stage"))
            if target is not None:
                _add_edge(
                    edges,
                    source=source,
                    target=target,
                    kind="uses",
                    filepath=path,
                    line=line_no,
                    provenance="dockerfile-copy-from-stage",
                )
    return list(symbols.values()), edges, configs, list(resources.values())


def _extract_dockerfile_from(
    path: Path,
    dockerfile_symbol: str,
    match: re.Match[str],
    line_no: int,
    stages: dict[str, str],
    symbols: dict[str, Symbol],
    edges: list[Edge],
    resources: dict[str, ResourceMetadata],
) -> str | None:
    image, stage_name = _parse_docker_from_body(match.group("body"), len(stages))
    if image is None:
        return None
    if _is_dynamic_infra_value(image):
        return None
    base_symbol = stages.get(image)
    base_provenance = "dockerfile-stage-base-stage"
    if base_symbol is None:
        base_symbol = _add_resource(
            name=image,
            category="image",
            source_family="dockerfile",
            line=line_no,
            provenance="dockerfile-base-image",
            filepath=path,
            symbols=symbols,
            resources=resources,
        )
        base_provenance = "dockerfile-stage-base-image"
    stage_symbol = _add_resource(
        name=f"{path.name}:{stage_name}",
        category="stage",
        source_family="dockerfile",
        line=line_no,
        provenance="dockerfile-stage",
        filepath=path,
        symbols=symbols,
        resources=resources,
    )
    stages[stage_name] = stage_symbol
    _add_edge(
        edges,
        source=dockerfile_symbol,
        target=stage_symbol,
        kind="uses",
        filepath=path,
        line=line_no,
        provenance="dockerfile-defines-stage",
    )
    _add_edge(
        edges,
        source=stage_symbol,
        target=base_symbol,
        kind="uses",
        filepath=path,
        line=line_no,
        provenance=base_provenance,
    )
    return stage_symbol


def _parse_docker_from_body(body: str, stage_count: int) -> tuple[str | None, str]:
    tokens = body.split()
    index = 0
    while index < len(tokens) and tokens[index].startswith("--"):
        if "=" not in tokens[index] and index + 1 < len(tokens):
            index += 2
            continue
        index += 1
    if index >= len(tokens):
        return None, f"stage{stage_count}"
    image = tokens[index]
    stage = f"stage{stage_count}"
    for token_index in range(index + 1, len(tokens) - 1):
        if tokens[token_index].upper() == "AS":
            stage = tokens[token_index + 1]
            break
    return image, stage


def _extract_dockerfile_key(
    path: Path,
    source: str,
    key: str,
    line_no: int,
    provenance: str,
    edge_provenance: str,
    symbols: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
) -> None:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
        _add_config(
            key=key,
            source_family="dockerfile",
            role="declaration",
            line=line_no,
            provenance=provenance,
            filepath=path,
            symbols=symbols,
            edges=edges,
            configs=configs,
            resources=resources,
            source_symbol=source,
            source_edge_provenance=edge_provenance,
        )


def _join_dockerfile_lines(text: str) -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + stripped)
        pending = ""
    if pending:
        lines.append(pending.rstrip())
    return lines
