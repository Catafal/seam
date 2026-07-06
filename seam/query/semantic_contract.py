"""Agent-facing semantic discovery contract.

Semantic vectors are retrieval aids, not graph facts. This leaf centralizes the
status and caveat vocabulary so CLI, MCP, Web, and schema surfaces do not drift.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Literal, NotRequired, TypedDict

import seam.config as config
from seam.analysis.embeddings import is_available

RetrievalMode = Literal[
    "lexical",
    "semantic-only",
    "hybrid",
    "keyword-only",
    "keyword-fallback",
    "graph-expanded",
    "graph-expanded-from-semantic",
]

SEMANTIC_DISCOVERY_CAVEAT = (
    "Semantic similarity is a discovery lead, not dependency evidence; verify with "
    "snippet/context/graph tools before editing."
)
SEMANTIC_NEXT_CALLS = ["seam_snippet", "seam_context", "seam_plan"]


class SemanticReadiness(TypedDict):
    requested: bool
    enabled: bool
    usable: bool
    status: str
    reason: str | None
    model: str
    embedding_count: int
    matching_embedding_count: int
    embedding_model_matches: bool
    hint: str | None


class RetrievalContract(TypedDict):
    retrieval_mode: RetrievalMode
    retrieval: dict[str, Any]
    caveats: list[str]
    recommended_next_calls: list[str]


class RetrievalFields(TypedDict, total=False):
    retrieval_mode: RetrievalMode
    retrieval: dict[str, Any]
    caveats: list[str]
    recommended_next_calls: list[str]
    semantic_score: NotRequired[float | None]


def _embedding_counts(conn: sqlite3.Connection, model: str) -> tuple[int, int]:
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
        matching = int(
            conn.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)).fetchone()[0]
        )
    except sqlite3.Error:
        return 0, 0
    return total, matching


def semantic_readiness(
    conn: sqlite3.Connection,
    *,
    requested: bool,
    model: str | None = None,
    semantic_enabled: bool | None = None,
    availability_check: Callable[[], bool] = is_available,
) -> SemanticReadiness:
    """Classify whether semantic discovery can run for the current read.

    The classifier deliberately runs before query embedding so callers can expose
    fallback reasons without triggering model load, download, or remote work.
    """
    active_model = model or config.SEAM_EMBED_MODEL
    enabled = config.SEAM_SEMANTIC == "on" if semantic_enabled is None else semantic_enabled
    total, matching = _embedding_counts(conn, active_model)
    model_matches = total == 0 or matching > 0

    def build(
        *,
        status: str,
        reason: str | None,
        usable: bool,
        hint: str | None,
    ) -> SemanticReadiness:
        return {
            "requested": requested,
            "enabled": enabled,
            "usable": usable,
            "status": status,
            "reason": reason,
            "model": active_model,
            "embedding_count": total,
            "matching_embedding_count": matching,
            "embedding_model_matches": model_matches,
            "hint": hint,
        }

    if not requested:
        return build(
            status="keyword_only",
            reason="keyword_only_override",
            usable=False,
            hint="Semantic discovery was bypassed for this call.",
        )
    if not enabled:
        return build(
            status="disabled",
            reason="config_off",
            usable=False,
            hint="Set SEAM_SEMANTIC=on and index with 'seam init --semantic' to enable semantic discovery.",
        )
    if total == 0:
        return build(
            status="unavailable",
            reason="no_embeddings",
            usable=False,
            hint="Run 'seam init --semantic' to build embeddings for semantic discovery.",
        )
    if matching == 0:
        return build(
            status="unavailable",
            reason="model_mismatch",
            usable=False,
            hint="Run 'seam init --semantic' to rebuild embeddings for the configured model.",
        )
    if not availability_check():
        return build(
            status="unavailable",
            reason="optional_extra_unavailable",
            usable=False,
            hint="Install the semantic extra before running semantic discovery.",
        )
    return build(status="usable", reason=None, usable=True, hint=None)


def retrieval_contract(
    mode: RetrievalMode,
    *,
    semantic_score: float | None = None,
    lexical_score: float | None = None,
    rrf_score: float | None = None,
) -> RetrievalContract:
    """Return compact retrieval evidence fields for a result or query seed."""
    sources: list[str] = []
    if mode in {"lexical", "hybrid", "keyword-only", "keyword-fallback"}:
        sources.append("lexical")
    if mode in {"semantic-only", "hybrid", "graph-expanded-from-semantic"}:
        sources.append("semantic")
    if mode in {"graph-expanded", "graph-expanded-from-semantic"}:
        sources.append("graph")

    retrieval: dict[str, Any] = {"mode": mode, "sources": sources}
    if semantic_score is not None:
        retrieval["semantic_score"] = semantic_score
    if lexical_score is not None:
        retrieval["lexical_score"] = lexical_score
    if rrf_score is not None:
        retrieval["rrf_score"] = rrf_score

    caveats: list[str] = []
    next_calls: list[str] = []
    if mode in {"semantic-only", "graph-expanded-from-semantic"}:
        caveats.append(SEMANTIC_DISCOVERY_CAVEAT)
        next_calls.extend(SEMANTIC_NEXT_CALLS)

    return {
        "retrieval_mode": mode,
        "retrieval": retrieval,
        "caveats": caveats,
        "recommended_next_calls": next_calls,
    }
