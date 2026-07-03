"""Conservative config/resource extraction for operational graph evidence.

Owns: turning safe config declarations and literal source config reads into
config/resource symbols, no-secret metadata, and graph edges.
Does not own: executing project code, probing infrastructure, solving dynamic
config keys, or indexing raw config values.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from tree_sitter import Node

from seam.indexer.graph_common import ConfigMetadata, Edge, ResourceMetadata, Symbol, _text
from seam.indexer.infra_resources import (
    extract_compose_resources as _extract_infra_compose_resources,
)
from seam.indexer.infra_resources import (
    extract_dockerfile_resources as _extract_dockerfile_resources,
)
from seam.indexer.infra_resources import is_dockerfile_name as _is_dockerfile_name

SAFE_ENV_FILENAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
    ".env.local.example",
}
UNSAFE_ENV_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
}
SAFE_CONFIG_FILENAMES = {
    "package.json",
    "pyproject.toml",
    "config.json",
    "config.toml",
    "config.yaml",
    "config.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}
SAFE_CONFIG_SUFFIXES = (
    ".config.json",
    ".config.toml",
    ".config.yaml",
    ".config.yml",
)

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*(?:=.*)?$")
_PY_GETENV_RE = re.compile(r"\bos\.getenv\(\s*([\"'])(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\1")
_PY_ENVIRON_GET_RE = re.compile(
    r"\bos\.environ(?:b)?\.get\(\s*([\"'])(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\1"
)
_PY_ENVIRON_SUB_RE = re.compile(
    r"\bos\.environ(?:b)?\[\s*([\"'])(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\1\s*\]"
)
_TS_PROCESS_DOT_RE = re.compile(r"\bprocess\.env\.([A-Za-z_][A-Za-z0-9_]*)")
_TS_PROCESS_BRACKET_RE = re.compile(
    r"\bprocess\.env\[\s*([\"'])(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\1\s*\]"
)
_TS_IMPORT_META_RE = re.compile(r"\bimport\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)")
_IGNORED_SOURCE_NODE_TYPES = {
    "comment",
    "string",
    "string_content",
    "string_fragment",
    "template_string",
    "template_substitution",
}


def is_config_resource_file(path: Path) -> bool:
    """Return true only for files safe to index without leaking config values."""
    name = path.name
    lower_name = name.lower()
    if lower_name in UNSAFE_ENV_FILENAMES:
        return False
    if lower_name in SAFE_ENV_FILENAMES or lower_name.endswith((".env.example", ".env.template")):
        return True
    if lower_name in SAFE_CONFIG_FILENAMES:
        return True
    if any(lower_name.endswith(suffix) for suffix in SAFE_CONFIG_SUFFIXES):
        return True
    if _is_dockerfile_name(name):
        return True
    return "config" in {part.lower() for part in path.parts} and lower_name.endswith(
        (".json", ".toml", ".yaml", ".yml")
    )


def config_symbol_name(key: str) -> str:
    return f"CONFIG {normalize_config_key(key)}"


def resource_symbol_name(category: str, name: str) -> str:
    return f"RESOURCE {category} {normalize_resource_name(name)}"


def normalize_config_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.]", "_", key.strip()).upper()


def normalize_resource_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@/-]", "_", name.strip()).upper()


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


def _config_metadata(
    *,
    key: str,
    source_family: str,
    role: str,
    value_state: str,
    value_category: str | None,
    line: int,
    provenance: str,
    confidence: str = "EXTRACTED",
) -> ConfigMetadata:
    normalized = normalize_config_key(key)
    return ConfigMetadata(
        symbol_name=config_symbol_name(key),
        key=key,
        normalized_key=normalized,
        source_family=source_family,
        role=role,
        value_state=value_state,
        value_category=value_category,
        line=line,
        confidence=confidence,  # type: ignore[typeddict-item]
        provenance=provenance,
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


def _value_category(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    text = str(value).strip()
    if text == "":
        return "empty"
    if "://" in text:
        return "url-like"
    if text.lower() in {"true", "false"}:
        return "boolean"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return "number"
    if any(token in text.lower() for token in ("example", "placeholder", "change-me", "changeme")):
        return "placeholder"
    return "present"


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


def _line_for_key(lines: list[str], key: str, default: int = 1) -> int:
    needle = key.split(".")[-1].strip("\"'")
    for index, line in enumerate(lines, start=1):
        if needle and needle in line:
            return index
    return default


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


def _flatten_mapping(value: object, prefix: str = "") -> list[tuple[str, object]]:
    if not isinstance(value, dict):
        return []
    items: list[tuple[str, object]] = []
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(raw_value, dict):
            items.extend(_flatten_mapping(raw_value, path))
        elif isinstance(raw_value, list):
            items.append((path, "list"))
        else:
            items.append((path, raw_value))
    return items


def _parse_simple_yaml(text: str) -> dict[str, object]:
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if ":" not in stripped or stripped.startswith("-"):
            continue
        key, value = stripped.split(":", 1)
        key = key.strip().strip("\"'")
        value = value.strip().strip("\"'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = value
    return root


def _add_config(
    *,
    key: str,
    value: object,
    source_family: str,
    role: str,
    line: int,
    provenance: str,
    filepath: Path,
    symbols: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
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
            value_state="redacted" if value is not None else "key-only",
            value_category=_value_category(value),
            line=line,
            provenance=provenance,
        )
    )
    resource = _resource_for_key(key)
    if resource is None:
        return
    category, resource_name = resource
    metadata = _resource_metadata(
        name=resource_name,
        category=category,
        source_family=source_family,
        line=line,
        provenance=f"{provenance}-resource",
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
    _add_edge(
        edges,
        source=symbol_name,
        target=metadata["symbol_name"],
        kind="configures",
        filepath=filepath,
        line=line,
        confidence="INFERRED",
        provenance=f"{provenance}-resource",
    )


def _extract_env_template(
    path: Path,
    text: str,
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    symbols: dict[str, Symbol] = {}
    edges: list[Edge] = []
    configs: list[ConfigMetadata] = []
    resources: dict[str, ResourceMetadata] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(stripped)
        if not match:
            continue
        value: object | None = None
        if "=" in stripped:
            value = stripped.split("=", 1)[1].strip()
        _add_config(
            key=match.group("key"),
            value=value,
            source_family="env-template",
            role="declaration",
            line=line_no,
            provenance="env-template-key",
            filepath=path,
            symbols=symbols,
            edges=edges,
            configs=configs,
            resources=resources,
        )
    return list(symbols.values()), edges, configs, list(resources.values())


def _extract_mapping_config(
    path: Path,
    text: str,
    mapping: dict[str, object],
    *,
    source_family: str,
    provenance: str,
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    symbols: dict[str, Symbol] = {}
    edges: list[Edge] = []
    configs: list[ConfigMetadata] = []
    resources: dict[str, ResourceMetadata] = {}
    lines = text.splitlines()
    for key, value in _flatten_mapping(mapping):
        _add_config(
            key=key,
            value=value,
            source_family=source_family,
            role="declaration",
            line=_line_for_key(lines, key),
            provenance=provenance,
            filepath=path,
            symbols=symbols,
            edges=edges,
            configs=configs,
            resources=resources,
        )
    return list(symbols.values()), edges, configs, list(resources.values())


def _extract_package_resources(
    path: Path,
    text: str,
    mapping: dict[str, object],
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    symbols: dict[str, Symbol] = {}
    resources: dict[str, ResourceMetadata] = {}
    lines = text.splitlines()
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = mapping.get(section)
        if not isinstance(deps, dict):
            continue
        for dep in deps:
            metadata = _resource_metadata(
                name=str(dep),
                category="dependency",
                source_family="manifest",
                line=_line_for_key(lines, str(dep)),
                provenance="manifest-dependency",
                confidence="EXTRACTED",
            )
            resources[metadata["symbol_name"]] = metadata
            symbols[metadata["symbol_name"]] = _symbol(
                metadata["symbol_name"],
                "resource",
                str(path),
                metadata["line"],
                f"dependency {dep}",
            )
    return list(symbols.values()), [], [], list(resources.values())


def extract_config_file(
    path: Path,
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    """Extract no-secret config/resource evidence from a safe config file."""
    lower_name = path.name.lower()
    if not is_config_resource_file(path):
        return [], [], [], []
    text = path.read_text(encoding="utf-8", errors="replace")
    if _is_dockerfile_name(path.name):
        return _extract_dockerfile_resources(path, text)
    if lower_name in SAFE_ENV_FILENAMES or lower_name.endswith((".env.example", ".env.template")):
        return _extract_env_template(path, text)
    try:
        if lower_name.endswith(".json"):
            data = json.loads(text)
            if not isinstance(data, dict):
                return [], [], [], []
            if lower_name == "package.json":
                return _extract_package_resources(path, text, data)
            return _extract_mapping_config(
                path,
                text,
                data,
                source_family="json-config",
                provenance="json-config-key",
            )
        if lower_name.endswith(".toml"):
            data = tomllib.loads(text)
            if lower_name == "pyproject.toml":
                package_symbols, package_edges, package_configs, package_resources = (
                    _extract_mapping_config(
                        path,
                        text,
                        data,
                        source_family="toml-config",
                        provenance="toml-config-key",
                    )
                )
                return package_symbols, package_edges, package_configs, package_resources
            return _extract_mapping_config(
                path,
                text,
                data,
                source_family="toml-config",
                provenance="toml-config-key",
            )
        if lower_name.endswith((".yaml", ".yml")):
            data = _parse_simple_yaml(text)
            if lower_name in {
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            }:
                return _extract_infra_compose_resources(path, text)
            return _extract_mapping_config(
                path,
                text,
                data,
                source_family="yaml-config",
                provenance="yaml-config-key",
            )
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return [], [], [], []
    return [], [], [], []


def _enclosing_symbol(symbols: list[Symbol], line: int) -> str | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol["kind"] in {"function", "method", "class"}
        and symbol["start_line"] <= line <= symbol["end_line"]
    ]
    if not candidates:
        return None
    return min(
        candidates, key=lambda symbol: (symbol["end_line"] - symbol["start_line"], symbol["name"])
    )["name"]


def _ignored_source_ranges(root: Node) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _IGNORED_SOURCE_NODE_TYPES:
            ranges.append((node.start_byte, node.end_byte))
            continue
        stack.extend(node.children)
    return ranges


def _line_start_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    current = 0
    for line in lines:
        offsets.append(current)
        current += len(line.encode("utf-8")) + 1
    return offsets


def _is_ignored_match(
    *,
    line: str,
    line_start_byte: int,
    match_start: int,
    ignored_ranges: list[tuple[int, int]],
) -> bool:
    match_byte = line_start_byte + len(line[:match_start].encode("utf-8"))
    return any(start <= match_byte < end for start, end in ignored_ranges)


def _source_read(
    *,
    key: str,
    line: int,
    filepath: Path,
    source_name: str | None,
    language: str,
    provenance: str,
    symbols_by_name: dict[str, Symbol],
    edges: list[Edge],
    configs: list[ConfigMetadata],
    resources: dict[str, ResourceMetadata],
) -> None:
    symbol_name = config_symbol_name(key)
    symbols_by_name.setdefault(
        symbol_name,
        _symbol(symbol_name, "config", str(filepath), line, f"config {normalize_config_key(key)}"),
    )
    configs.append(
        _config_metadata(
            key=key,
            source_family=language,
            role="read",
            value_state="not-read",
            value_category=None,
            line=line,
            provenance=provenance,
        )
    )
    if source_name:
        edges.append(
            Edge(
                source=source_name,
                target=symbol_name,
                kind="reads_config",
                file=str(filepath),
                line=line,
                confidence="EXTRACTED",
            )
        )
    resource = _resource_for_key(key)
    if resource:
        category, resource_name = resource
        metadata = _resource_metadata(
            name=resource_name,
            category=category,
            source_family=language,
            line=line,
            provenance=f"{provenance}-resource",
        )
        resources.setdefault(metadata["symbol_name"], metadata)
        symbols_by_name.setdefault(
            metadata["symbol_name"],
            _symbol(
                metadata["symbol_name"],
                "resource",
                str(filepath),
                line,
                f"{category} resource {metadata['normalized_name']}",
            ),
        )
        edges.append(
            Edge(
                source=symbol_name,
                target=metadata["symbol_name"],
                kind="configures",
                file=str(filepath),
                line=line,
                confidence="INFERRED",
            )
        )


def extract_source_config_reads(
    root: Node,
    language: str,
    filepath: Path,
    source_symbols: list[Symbol],
) -> tuple[list[Symbol], list[Edge], list[ConfigMetadata], list[ResourceMetadata]]:
    """Extract literal config reads from parsed source without solving dynamic keys."""
    if language not in {"python", "typescript", "javascript"}:
        return [], [], [], []
    symbols_by_name: dict[str, Symbol] = {}
    edges: list[Edge] = []
    configs: list[ConfigMetadata] = []
    resources: dict[str, ResourceMetadata] = {}
    text = _text(root)
    lines = text.splitlines()
    line_start_offsets = _line_start_offsets(lines)
    ignored_ranges = _ignored_source_ranges(root)
    patterns: list[tuple[re.Pattern[str], str]]
    if language == "python":
        patterns = [
            (_PY_GETENV_RE, "python-os-getenv"),
            (_PY_ENVIRON_GET_RE, "python-os-environ"),
            (_PY_ENVIRON_SUB_RE, "python-os-environ"),
        ]
    else:
        patterns = [
            (
                _TS_PROCESS_DOT_RE,
                "typescript-process-env" if language == "typescript" else "javascript-process-env",
            ),
            (
                _TS_PROCESS_BRACKET_RE,
                "typescript-process-env" if language == "typescript" else "javascript-process-env",
            ),
            (
                _TS_IMPORT_META_RE,
                "typescript-import-meta-env"
                if language == "typescript"
                else "javascript-import-meta-env",
            ),
        ]
    for line_no, line in enumerate(lines, start=1):
        source_name = _enclosing_symbol(source_symbols, line_no)
        line_start_byte = line_start_offsets[line_no - 1]
        for pattern, provenance in patterns:
            for match in pattern.finditer(line):
                if _is_ignored_match(
                    line=line,
                    line_start_byte=line_start_byte,
                    match_start=match.start(),
                    ignored_ranges=ignored_ranges,
                ):
                    continue
                key = match.group("key") if "key" in match.groupdict() else match.group(1)
                _source_read(
                    key=key,
                    line=line_no,
                    filepath=filepath,
                    source_name=source_name,
                    language=language,
                    provenance=provenance,
                    symbols_by_name=symbols_by_name,
                    edges=edges,
                    configs=configs,
                    resources=resources,
                )
    return list(symbols_by_name.values()), edges, configs, list(resources.values())
