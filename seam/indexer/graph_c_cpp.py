"""Thin re-exporter: C + C++ symbol/edge/comment extractors.

LAYER: imports from graph_c (leaf) and graph_cpp (leaf) — never from graph.py.

WHY this file exists: graph.py imports all family extractors through stable public names.
The C and C++ extractors were split into graph_c.py and graph_cpp.py when
graph_c_cpp.py exceeded 1000 lines (Tier B additions). This re-exporter keeps
graph.py's import stable.

graph.py continues to import from this module:
    from seam.indexer.graph_c_cpp import (
        _extract_comments_c, _extract_comments_cpp,
        _extract_edges_c, _extract_edges_cpp,
        _extract_symbols_c, _extract_symbols_cpp,
    )
"""

# Re-export C extractors — the actual code lives in graph_c.py.
from seam.indexer.graph_c import (
    _extract_comments_c,
    _extract_edges_c,
    _extract_symbols_c,
)

# Re-export C++ extractors — the actual code lives in graph_cpp.py.
from seam.indexer.graph_cpp import (
    _extract_comments_cpp,
    _extract_edges_cpp,
    _extract_symbols_cpp,
)

__all__ = [
    "_extract_comments_c",
    "_extract_edges_c",
    "_extract_symbols_c",
    "_extract_comments_cpp",
    "_extract_edges_cpp",
    "_extract_symbols_cpp",
]
