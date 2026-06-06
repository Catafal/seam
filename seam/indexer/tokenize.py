"""Identifier compound-split tokenization (Tier D #12) — search-recall leaf.

PROBLEM: ``symbols_fts`` uses FTS5's default unicode61 tokenizer, which does NOT split
camelCase or snake_case. So ``GlobalPushToTalkShortcutMonitor`` is one opaque token and a
natural-language query ("push to talk shortcut monitor") can never reach its sub-words.
The split must happen at INDEX time (a stored concatenated token can't be un-joined at
query time), so the indexer writes ``split_identifier(name)`` into ``symbols.search_text``
(a dedicated 4th FTS column) and the query layer splits query terms with the SAME function.

LAYER: pure leaf — imports only stdlib. No DB, no config, no other seam modules. Used by
both ``seam/indexer/db.py`` (index side) and ``seam/query/fts.py`` (query side) so the two
sides tokenize identically. Every function is pure, deterministic, and never raises.

WHY identifier-only (no docstring): the docstring is already its own ``symbols_fts`` column,
so folding it into search_text would double-index prose and dilute ranking. search_text is
kept to identifier vocabulary (name + qualified_name segments) only.
"""

import re

# camelCase / acronym boundary insertion. Two passes, applied in order:
#   1. "<any><Upper><lower>+"  → split before an Upper that starts a Word (handles the
#      acronym→Word boundary: "HTTPServer" → "HTTP Server", "parseJSONData" → "...JSON Data").
#   2. "<lower|digit><Upper>"  → split a lowercase/digit run before the next Upper
#      ("fooBar" → "foo Bar", "v2Loader" → "v2 Loader").
_BOUNDARY_ACRONYM_WORD = re.compile(r"(.)([A-Z][a-z]+)")
_BOUNDARY_LOWER_UPPER = re.compile(r"([a-z0-9])([A-Z])")

# Separators that delimit identifier parts across languages: dot (qualified names),
# underscore (snake_case), hyphen (kebab), slash/colon (paths/namespaces).
_SEPARATORS = re.compile(r"[._\-/:]+")


def split_identifier(text: str) -> list[str]:
    """Split a code identifier into lowercased sub-word tokens (deduped, order-preserving).

    Examples:
        "GlobalPushToTalkShortcutMonitor" -> ["global","push","to","talk","shortcut","monitor"]
        "parseJSONData"                   -> ["parse","json","data"]
        "find_cycle"                      -> ["find","cycle"]
        "Class.method"                    -> ["class","method"]
        "v2Loader"                        -> ["v2","loader"]
        ""                                -> []

    Pure and total: any string in, a (possibly empty) list of lowercase tokens out. Never raises.
    """
    if not text:
        return []
    # Normalize cross-language separators to spaces first, then insert camelCase boundaries.
    s = _SEPARATORS.sub(" ", text)
    s = _BOUNDARY_ACRONYM_WORD.sub(r"\1 \2", s)
    s = _BOUNDARY_LOWER_UPPER.sub(r"\1 \2", s)

    seen: set[str] = set()
    out: list[str] = []
    for tok in s.split():
        low = tok.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(low)
    return out


def build_search_text(name: str, qualified_name: str | None = None) -> str:
    """Return the space-joined search_text for a symbol: split(name) ∪ split(qualified_name).

    qualified_name is folded in (deduped against the name tokens) to recover the enclosing
    type/namespace word WHERE qualified_name carries it (e.g. 'Class.method' → adds 'class';
    a bare top-level function whose qualified_name is just its own name adds nothing). docstring
    is deliberately NOT folded — it is already its own FTS column. Never raises.
    """
    toks = split_identifier(name)
    if qualified_name:
        seen = set(toks)
        for t in split_identifier(qualified_name):
            if t not in seen:
                seen.add(t)
                toks.append(t)
    return " ".join(toks)
