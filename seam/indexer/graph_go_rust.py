"""Thin re-exporter: Go + Rust symbol/edge/comment extractors.

LAYER: imports from graph_go (leaf) and graph_rust (leaf) — never from graph.py.

WHY this file exists: graph.py imports all family extractors through stable public names.
The Go and Rust extractors were split into graph_go.py and graph_rust.py when
graph_go_rust.py exceeded 1000 lines (Tier B additions). This re-exporter keeps
graph.py's import stable.

graph.py continues to import from this module:
    from seam.indexer.graph_go_rust import (
        _extract_comments_go, _extract_comments_rust,
        _extract_edges_go, _extract_edges_rust,
        _extract_symbols_go, _extract_symbols_rust,
    )
"""

# Re-export Go extractors — the actual code lives in graph_go.py.
from seam.indexer.graph_go import (
    _extract_comments_go,
    _extract_edges_go,
    _extract_symbols_go,
)

# Re-export Rust extractors — the actual code lives in graph_rust.py.
from seam.indexer.graph_rust import (
    _extract_comments_rust,
    _extract_edges_rust,
    _extract_symbols_rust,
)

__all__ = [
    "_extract_comments_go",
    "_extract_edges_go",
    "_extract_symbols_go",
    "_extract_comments_rust",
    "_extract_edges_rust",
    "_extract_symbols_rust",
]
