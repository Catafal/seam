"""Cluster labeling module — deterministic labels with optional LLM naming.

Public API:
    deterministic_label(members) -> str
    label_cluster(members, naming_mode, api_key, model) -> tuple[str, str]

Design:
    - deterministic_label: always available, zero dependencies, no I/O.
      Derives a label from dominant directory/file prefix + highest-degree symbol.
      Format: "dir/subdir — symbol_name" (em-dash separator).
    - label_cluster: dispatches to LLM or deterministic based on naming_mode.
      LLM path is fully isolated in _call_llm_for_label (stub-able in tests).
      Any LLM error → silent fallback to deterministic + log warning.
    - _call_llm_for_label: uses stdlib urllib only. NEVER called by default.

Naming source values:
    "deterministic" — label from heuristic (always the fallback)
    "llm"          — label from LLM (only when naming_mode='llm' AND key present)

Import rules:
    This module imports ONLY stdlib (logging, urllib, json, pathlib, collections).
    It must NOT import seam.indexer.db, seam.query, seam.server, seam.cli.
"""

import json
import logging
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Timeout for LLM HTTP request (seconds). Fail fast — labeling is best-effort.
_LLM_TIMEOUT_SECONDS = 10

# Default LLM model when not specified
_DEFAULT_LLM_MODEL = "gpt-4o-mini"

# Anthropic-compatible API endpoint default (can be overridden in tests)
_DEFAULT_LLM_ENDPOINT = "https://api.openai.com/v1/chat/completions"


# ── Public types ──────────────────────────────────────────────────────────────

# Member dict shape (consumed by label functions):
# {"name": str, "file": str, "degree": int}
# "degree" = number of edges this symbol has (used to find the "most connected")
MemberInfo = dict[str, Any]


# ── deterministic_label ───────────────────────────────────────────────────────


def deterministic_label(members: list[MemberInfo]) -> str:
    """Derive a human-readable cluster label from its members.

    Label format: "<dominant_path> — <highest_degree_symbol>"
    Example: "seam/analysis — traversal.walk"

    Algorithm:
        1. Find the dominant directory: the most common immediate-parent dir
           among all member file paths (ties broken alphabetically).
        2. Find the highest-degree symbol (ties broken by name alphabetically).
        3. Combine as "dir — symbol".

    Args:
        members: List of MemberInfo dicts with 'name', 'file', 'degree' keys.

    Returns:
        Non-empty label string. Returns "<unnamed>" on empty input (never raises).
    """
    if not members:
        return "<unnamed>"

    if len(members) == 1:
        # Single-member cluster: just use the symbol name and its file's stem
        m = members[0]
        file_stem = Path(m["file"]).stem if m.get("file") else ""
        name = m.get("name", "<unknown>")
        if file_stem and file_stem != name:
            return f"{file_stem} — {name}"
        return name

    # ── Step 1: dominant directory ────────────────────────────────────────────
    # Use the last two path components (parent dir + file stem) for readability.
    # WHY: Full absolute paths would be noisy; just 'dir/stem' is informative.
    path_components: list[str] = []
    for m in members:
        file_path = m.get("file", "")
        if file_path:
            p = Path(file_path)
            parts = p.parts
            # Take up to 2 components: parent dir + filename (no extension)
            if len(parts) >= 2:
                path_components.append(f"{parts[-2]}/{p.stem}")
            else:
                path_components.append(p.stem)

    # Count occurrences and pick the most common (alphabetical tie-break)
    if path_components:
        counts = Counter(path_components)
        # Sort by (-count, component) for deterministic tie-breaking
        dominant_path = min(
            counts.keys(),
            key=lambda k: (-counts[k], k),
        )
    else:
        dominant_path = "<unknown>"

    # ── Step 2: highest-degree symbol ────────────────────────────────────────
    # Sort by (-degree, name) for deterministic tie-breaking
    top_symbol = min(
        members,
        key=lambda m: (-m.get("degree", 0), m.get("name", "")),
    )
    top_name = top_symbol.get("name", "<unknown>")

    return f"{dominant_path} — {top_name}"


# ── label_cluster ─────────────────────────────────────────────────────────────


def label_cluster(
    members: list[MemberInfo],
    naming_mode: str = "deterministic",
    api_key: str | None = None,
    model: str | None = None,
    endpoint: str | None = None,
) -> tuple[str, str]:
    """Produce a label for a cluster, returning (label, naming_source).

    Args:
        members:      List of MemberInfo dicts (name, file, degree).
        naming_mode:  "deterministic" (default) or "llm".
        api_key:      API key for LLM; required when naming_mode="llm".
        model:        LLM model name; uses _DEFAULT_LLM_MODEL when not set.
        endpoint:     LLM API endpoint; uses _DEFAULT_LLM_ENDPOINT when not set.

    Returns:
        (label, naming_source) where naming_source ∈ {"deterministic", "llm"}.
        The LLM path is only tried when naming_mode="llm" AND api_key is present.
        Any LLM error falls back to deterministic silently.

    WHY: The naming_source is stored in the clusters table so operators can see
    which names were AI-generated vs. heuristic-derived.
    """
    if naming_mode != "llm" or not api_key:
        # Deterministic mode or no key provided — never touch network code
        return deterministic_label(members), "deterministic"

    # LLM path: try to call, fall back on ANY error
    try:
        llm_label = _call_llm_for_label(
            members,
            api_key=api_key,
            model=model or _DEFAULT_LLM_MODEL,
            endpoint=endpoint or _DEFAULT_LLM_ENDPOINT,
        )
        if llm_label and llm_label.strip():
            return llm_label.strip(), "llm"
        # Empty/blank response → fall through to deterministic
        logger.warning("cluster_naming: LLM returned empty label, using deterministic fallback")
        return deterministic_label(members), "deterministic"

    except Exception as exc:
        # Any error (network, timeout, bad JSON, missing key) is fail-safe
        logger.warning(
            "cluster_naming: LLM naming failed (%s: %s) — using deterministic fallback",
            type(exc).__name__,
            exc,
        )
        return deterministic_label(members), "deterministic"


# ── _call_llm_for_label ────────────────────────────────────────────────────────


def _call_llm_for_label(
    members: list[MemberInfo],
    api_key: str,
    model: str = _DEFAULT_LLM_MODEL,
    endpoint: str = _DEFAULT_LLM_ENDPOINT,
) -> str:
    """Call an OpenAI-compatible LLM API to generate a cluster label.

    Uses stdlib urllib only — no external SDK. This is intentionally isolated
    so tests can monkeypatch this function without touching urllib.

    WHY: Isolated here (not inlined in label_cluster) so tests can stub it
    via `patch('seam.analysis.cluster_naming._call_llm_for_label')`.

    Args:
        members:  List of MemberInfo dicts.
        api_key:  Bearer token for the API.
        model:    Model name string.
        endpoint: Full URL to the chat completions endpoint.

    Returns:
        The label string from the LLM response (may be empty).

    Raises:
        Any exception from urllib or JSON parsing — caller handles these.
    """
    # Build a concise prompt from member names and files
    symbol_list = ", ".join(m.get("name", "") for m in members[:20])  # cap at 20
    prompt = (
        f"Name this code cluster in 3-5 words. Members: {symbol_list}. "
        "Respond with ONLY the cluster name, no explanation."
    )

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 20,
        "temperature": 0,  # deterministic response
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT_SECONDS) as response:
        body = json.loads(response.read().decode("utf-8"))

    # Extract content from OpenAI-compatible response shape
    return body["choices"][0]["message"]["content"]
