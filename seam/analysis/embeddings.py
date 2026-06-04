"""Local embedding wrapper for semantic search (T3).

Leaf module — imports ONLY stdlib + fastembed (lazy).
numpy is NEVER imported here; we only call .tobytes() on numpy arrays returned by fastembed.
NO server/cli/query imports.

Design decisions:
- fastembed is an OPTIONAL extra ([semantic]). This module degrades gracefully
  when it is absent: is_available() → False, embed_texts() → [], embed_query() → b''.
- WHY no numpy import here: numpy is a fastembed transitive dep. Importing it at module
  scope would make this module fail to import in environments where only the base package is
  installed (no [semantic] extra). We call .tobytes() on the array objects fastembed returns
  — that is safe because if fastembed is importable, numpy is also present.
- TextEmbedding is lazily loaded and cached per model name via _MODEL_CACHE.
  WHY cached: FastEmbed model loading takes ~200ms; reloading on every call would make the
  read path unacceptably slow. FastEmbed models are read-safe; the dict is not locked
  (worst case: two concurrent callers each create an instance; one is discarded — harmless).
- Never raises: any error in the real path is logged as a warning and the function
  returns the safe default ([] or b'').

Public API:
    is_available() -> bool
    symbol_text(name, signature, docstring) -> str
    embed_texts(texts, model) -> list[bytes]
    embed_query(text, model) -> bytes
    _get_model(model)   <- internal, exposed for monkeypatching in tests
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Availability cache ────────────────────────────────────────────────────────
# None = not yet checked; True/False = cached result.
# Reset to None in tests that need to simulate absence.
_fastembed_available: bool | None = None


def is_available() -> bool:
    """Return True if the fastembed package is importable.

    The result is cached after the first call — subsequent calls are O(1).
    Never raises: any import error returns False gracefully.

    WHY cached: this function is called once per query in the read path;
    caching avoids repeated sys.modules lookups in hot code.
    """
    global _fastembed_available  # noqa: PLW0603
    if _fastembed_available is None:
        try:
            import fastembed  # noqa: F401

            _fastembed_available = True
        except Exception:  # noqa: BLE001
            _fastembed_available = False
    return bool(_fastembed_available)


# ── Model cache ───────────────────────────────────────────────────────────────
# Maps model_name → TextEmbedding instance. Single-process; not thread-locked
# (fastembed models are read-safe; worst case we create two and discard one).
_MODEL_CACHE: dict[str, Any] = {}


def _get_model(model: str) -> Any:
    """Lazy-load and cache a fastembed TextEmbedding instance.

    WHY: FastEmbed model loading (~200ms) must not happen at import time.
    The cache avoids reloading across multiple calls within one process.

    Raises: any exception from fastembed (caller is responsible for catching).
    """
    if model not in _MODEL_CACHE:
        from fastembed import TextEmbedding  # type: ignore[import-untyped]

        _MODEL_CACHE[model] = TextEmbedding(model_name=model)
    return _MODEL_CACHE[model]


# ── Public API ────────────────────────────────────────────────────────────────


def symbol_text(
    name: str,
    signature: str | None,
    docstring: str | None,
) -> str:
    """Build the canonical text string to embed for a symbol.

    Combines name + signature + docstring into a single string. Fields that are
    None or empty are omitted (no trailing whitespace, no 'None' literals).

    WHY this order: the symbol name is the highest-signal token; signature provides
    parameter types and return type (shape of the function); docstring provides
    intent and usage context. Together they cover both syntactic and semantic search.

    Args:
        name:       Symbol name (always included).
        signature:  Function/class signature, or None.
        docstring:  Docstring / documentation text, or None.

    Returns:
        A single str suitable for embedding. Never raises.
    """
    parts: list[str] = [name]
    if signature:
        parts.append(signature)
    if docstring:
        parts.append(docstring)
    return "\n".join(parts)


def embed_texts(texts: list[str], model: str) -> list[bytes]:
    """Embed a list of texts using the local fastembed model.

    Returns one float32 blob (numpy.array.tobytes()) per input text.
    Returns [] when:
      - fastembed is not available (graceful degradation)
      - the texts list is empty (no-op fast path)
      - any exception occurs (logs a warning, returns [])

    WHY: The caller (embedding_index.py) batches its own inputs; this function
    processes them all in one fastembed call. Returning [] on error mirrors the
    index_clusters pattern: a single failure logs a warning and the index is left
    unpopulated rather than crashing.

    Args:
        texts: List of strings to embed. May be empty.
        model: FastEmbed model name (e.g. "BAAI/bge-small-en-v1.5").

    Returns:
        list[bytes] of length len(texts), each a float32 numpy array serialised
        with .tobytes(). Returns [] on any failure.
    """
    if not is_available():
        return []
    if not texts:
        return []
    try:
        emb_model = _get_model(model)
        # fastembed TextEmbedding.embed() returns a Generator of numpy float32 arrays.
        vectors = list(emb_model.embed(texts))
        return [v.tobytes() for v in vectors]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "embeddings.embed_texts: failed to embed %d texts with model %r (%s: %s); "
            "returning [] — run 'seam init --semantic' again or check fastembed install.",
            len(texts),
            model,
            type(exc).__name__,
            exc,
        )
        return []


def embed_query(text: str, model: str) -> bytes:
    """Embed a single query string using the local fastembed model.

    Returns float32 bytes (numpy.array.tobytes()) for the query vector.
    Returns b'' when:
      - fastembed is not available (graceful degradation)
      - any exception occurs (logs a warning, returns b'')

    WHY query_embed vs embed: FastEmbed offers a separate query_embed() path that
    prepends the BGE query prefix ("Represent this sentence for searching relevant
    passages:"). Using it ensures the query vector lives in the same space as the
    passage embeddings built with embed().

    Args:
        text:  The search query string.
        model: FastEmbed model name.

    Returns:
        bytes (float32 numpy array serialised with .tobytes()), or b'' on failure.
    """
    if not is_available():
        return b""
    try:
        emb_model = _get_model(model)
        # query_embed returns a Generator; we consume the first (and only) element.
        vectors = list(emb_model.query_embed([text]))
        if not vectors:
            return b""
        return vectors[0].tobytes()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "embeddings.embed_query: failed to embed query %r with model %r (%s: %s); "
            "returning b'' — semantic search will return no results.",
            text,
            model,
            type(exc).__name__,
            exc,
        )
        return b""
