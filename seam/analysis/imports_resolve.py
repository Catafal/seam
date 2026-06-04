"""P3 resolution helpers — tsconfig/jsconfig aliases + go.mod module prefix.

Leaf module — imports ONLY stdlib + the shared `_probe_extensions` helper from
seam.analysis.imports. Split out of imports.py purely to keep that file under
the 1000-line cap; the logic is part of the import-resolution path.

Provides (all cached per repo_root, all index-time, all never-raise):
    _load_tsconfig_aliases(repo_root) -> dict[str, list[str]]
        Read tsconfig.json / jsconfig.json `compilerOptions.paths` + `baseUrl`
        into a longest-prefix-first alias map. {} on missing/malformed config.
    _resolve_ts_alias(source_module, repo_root, extensions, probe) -> list[str]
        Expand an aliased TS/JS specifier ('@/foo') to existing file paths.
    _load_go_module(repo_root) -> str | None
        Read the `module <path>` line from go.mod. None when absent.

Caches are single-repo (cleared when a different repo_root is seen) — the index
pipeline processes one repo at a time, so a 1-entry cache keeps memory bounded.
"""

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# ── P3: per-repo config caches (read ONCE per repo_root, zero read-time cost) ──
# _TSCONFIG_ALIAS_CACHE: {repo_root_str: {alias_pattern: [target_pattern, ...]}}
#   alias/target patterns keep their trailing '/*' (or are exact, no '*').
#   Insertion order is longest-prefix-first so resolution tries the most
#   specific alias before the broader catch-all.
# _GO_MODULE_CACHE: {repo_root_str: module_path_or_None}
_TSCONFIG_ALIAS_CACHE: dict[str, dict[str, list[str]]] = {}
_GO_MODULE_CACHE: dict[str, str | None] = {}

# tsconfig allows // line and /* block */ comments — strip them before json.loads.
# Order matters: block comments first (may span lines), then line comments.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n\r]*")


def _load_tsconfig_aliases(repo_root: Path) -> dict[str, list[str]]:
    """Read tsconfig.json / jsconfig.json paths+baseUrl into an alias map.

    Returns a dict {alias_pattern: [resolved_target_pattern, ...]} where every
    target is made relative to repo_root (baseUrl-joined). Insertion order is
    longest-alias-first so the caller can try the most specific match first.

    Cached per repo_root (the cache holds a single repo at a time). Never raises:
    a missing or malformed config yields {} (degrades to current behavior).
    """
    key = str(repo_root)
    if key in _TSCONFIG_ALIAS_CACHE:
        return _TSCONFIG_ALIAS_CACHE[key]
    # A different repo_root is being indexed — drop the prior single-repo entry.
    _TSCONFIG_ALIAS_CACHE.clear()

    aliases: dict[str, list[str]] = {}
    try:
        config_path = None
        for name in ("tsconfig.json", "jsconfig.json"):
            candidate = repo_root / name
            if candidate.exists():
                config_path = candidate
                break
        if config_path is None:
            _TSCONFIG_ALIAS_CACHE[key] = aliases
            return aliases

        raw = config_path.read_text(encoding="utf-8", errors="replace")
        # Strip comments (tsconfig is JSONC) before parsing as strict JSON.
        stripped = _BLOCK_COMMENT_RE.sub("", raw)
        stripped = _LINE_COMMENT_RE.sub("", stripped)
        data = json.loads(stripped)

        compiler_opts = data.get("compilerOptions", {}) if isinstance(data, dict) else {}
        base_url = compiler_opts.get("baseUrl", ".") or "."
        paths = compiler_opts.get("paths", {})
        if not isinstance(paths, dict):
            _TSCONFIG_ALIAS_CACHE[key] = aliases
            return aliases

        for alias, targets in paths.items():
            if not isinstance(alias, str) or not isinstance(targets, list):
                continue
            resolved_targets: list[str] = []
            for target in targets:
                if isinstance(target, str):
                    # baseUrl-join: 'src/*' under baseUrl '.' stays 'src/*'.
                    resolved_targets.append(f"{base_url}/{target}".replace("\\", "/"))
            if resolved_targets:
                aliases[alias] = resolved_targets

        # Longest-prefix-first: '@/components/*' must beat '@/*'. Sort by the
        # alias length WITHOUT the trailing '*' so specificity wins ties.
        aliases = dict(
            sorted(aliases.items(), key=lambda kv: len(kv[0].rstrip("*")), reverse=True)
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_load_tsconfig_aliases: failed for %s: %r", repo_root, exc)
        aliases = {}

    _TSCONFIG_ALIAS_CACHE[key] = aliases
    return aliases


def _resolve_ts_alias(
    source_module: str,
    repo_root: Path,
    extensions: list[str],
    probe: Callable[[Path, list[str]], list[str]],
) -> list[str]:
    """Expand a tsconfig/jsconfig path alias to existing file paths.

    Matches `source_module` against the longest-prefix-first alias map from
    `_load_tsconfig_aliases`. A `<prefix>/*` alias captures the suffix after the
    prefix and substitutes it into each `<target>/*` pattern; an exact alias (no
    '*') maps the whole specifier. Each candidate base is probed (via the passed
    `probe` helper) against the per-language extension order. Returns [] when no
    alias matches. Never raises.
    """
    aliases = _load_tsconfig_aliases(repo_root)
    if not aliases:
        return []
    results: list[str] = []
    for alias, targets in aliases.items():
        if alias.endswith("/*"):
            prefix = alias[:-1]  # keep trailing '/', drop the '*'
            if not source_module.startswith(prefix):
                continue
            suffix = source_module[len(prefix) :]
            for target in targets:
                # target like 'src/*' or './src/*' → substitute the captured suffix.
                sub = target[:-1] + suffix if target.endswith("*") else target
                base = Path(os.path.normpath(repo_root / sub))
                results.extend(probe(base, extensions))
        elif alias == source_module:
            for target in targets:
                base = Path(os.path.normpath(repo_root / target))
                results.extend(probe(base, extensions))
        if results:
            # Longest-prefix-first ordering means the first matching alias is the
            # most specific — stop once it yields hits.
            return results
    return results


def _load_go_module(repo_root: Path) -> str | None:
    """Read the `module <path>` line from go.mod at repo_root.

    Returns the module path (e.g. 'github.com/org/repo') or None when go.mod is
    absent / has no module line. Cached per repo_root. Never raises.
    """
    key = str(repo_root)
    if key in _GO_MODULE_CACHE:
        return _GO_MODULE_CACHE[key]
    # Different repo_root — drop the prior single-repo entry.
    _GO_MODULE_CACHE.clear()

    module_path: str | None = None
    try:
        go_mod = repo_root / "go.mod"
        if go_mod.exists():
            for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("module "):
                    module_path = stripped[len("module ") :].strip()
                    break
    except Exception as exc:  # noqa: BLE001
        logger.debug("_load_go_module: failed for %s: %r", repo_root, exc)
        module_path = None

    _GO_MODULE_CACHE[key] = module_path
    return module_path
