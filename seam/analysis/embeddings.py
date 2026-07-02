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

WS1-A additions:
- extract_body_slice(source_lines, start_line, end_line) → str: pure helper that
  extracts a 1-based inclusive line range from already-read source lines. Guards all
  edge cases (empty, out-of-range, start > end) without raising. No disk IO.
- symbol_text() accepts optional keyword-only args `body` and `max_chars`. With both
  unset, output is byte-identical to the original 3-arg call. The header (name +
  signature + docstring) is NEVER truncated; body fills any remaining max_chars budget.

Public API:
    is_available() -> bool
    extract_body_slice(source_lines, start_line, end_line) -> str
    symbol_text(name, signature, docstring, *, body=None, max_chars=None) -> str
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


def extract_body_slice(source_lines: list[str], start_line: int, end_line: int) -> str:
    """Extract a contiguous body slice from already-read source lines.

    Takes a list of file lines (0-indexed in the list, but accessed via 1-based
    line numbers) and returns the inclusive range [start_line, end_line] joined
    with newlines. Guards all edge cases without raising.

    WHY no disk IO here: callers (index_embeddings) handle file reads with a per-file
    cache. This function stays pure and is cleanly unit-testable in isolation.

    Guards:
    - Empty source_lines → ""
    - start_line < 1 (0 or negative) → ""
    - start_line > end_line → ""
    - start_line > len(source_lines) → ""
    - end_line > len(source_lines) → clamped to len(source_lines) (graceful)

    Args:
        source_lines: Lines of the source file as a list (from file.splitlines()).
        start_line:   1-based start line (inclusive).
        end_line:     1-based end line (inclusive).

    Returns:
        Joined text of the requested lines, or "" on any out-of-range condition.
        Never raises.
    """
    # Guard: trivial / degenerate cases
    if not source_lines:
        return ""
    if start_line < 1:
        return ""
    if start_line > end_line:
        return ""
    if start_line > len(source_lines):
        return ""

    # Clamp end_line to the last valid line (1-based → 0-based: end_line-1)
    clamped_end = min(end_line, len(source_lines))

    # Convert to 0-based slice: [start_line-1, clamped_end) (exclusive upper bound)
    selected = source_lines[start_line - 1 : clamped_end]
    return "\n".join(selected)


def symbol_text(
    name: str,
    signature: str | None,
    docstring: str | None,
    *,
    body: str | None = None,
    max_chars: int | None = None,
) -> str:
    """Build the canonical text string to embed for a symbol.

    Combines name + signature + docstring into a single string. Fields that are
    None or empty are omitted (no trailing whitespace, no 'None' literals).

    WHY this order: the symbol name is the highest-signal token; signature provides
    parameter types and return type (shape of the function); docstring provides
    intent and usage context. Together they cover both syntactic and semantic search.

    WS1-A: optional keyword-only args `body` and `max_chars` extend the output with
    a leading slice of the symbol's implementation body. The header is NEVER truncated.
    When body and max_chars are both unset (the default), output is byte-identical to
    the original 3-arg call — no behaviour change.

    Args:
        name:       Symbol name (always included).
        signature:  Function/class signature, or None.
        docstring:  Docstring / documentation text, or None.
        body:       Implementation body text (pre-sliced). Appended after the header
                    when max_chars allows it. None or '' → no body appended.
        max_chars:  Character budget for the combined output (header + body). The
                    header is assembled first and is NEVER truncated. If budget
                    remains after the header, body is appended up to the remaining
                    chars. None → body is ignored (byte-identical to 3-arg output).

    Returns:
        A single str suitable for embedding. Never raises.
    """
    # ── Header (identical to pre-WS1-A, always assembled in full) ────────────
    parts: list[str] = [name]
    if signature:
        parts.append(signature)
    if docstring:
        parts.append(docstring)
    header = "\n".join(parts)

    # ── No body path: byte-identical to pre-WS1-A default ────────────────────
    # Body is only appended when BOTH body text AND max_chars are provided.
    if not body or max_chars is None:
        return header

    # ── Body path: fill remaining budget after header ─────────────────────────
    # header_len + 1 separator newline; if the header already fills the budget,
    # there is nothing left for the body — return header as-is (never truncate).
    separator = "\n"
    used = len(header) + len(separator)
    remaining = max_chars - used
    if remaining <= 0:
        return header

    # Truncate body to remaining budget (leading slice)
    body_slice = body[:remaining]
    return header + separator + body_slice


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
