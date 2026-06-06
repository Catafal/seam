"""Thin re-exporter: Java + C# symbol/edge/comment extractors.

LAYER: imports from graph_java (leaf) and graph_csharp (leaf) — never from graph.py.

WHY this file exists: graph.py imports all family extractors through stable public names.
The Java and C# extractors were split into graph_java.py and graph_csharp.py when
graph_java_csharp.py exceeded 1000 lines (Tier B additions). This re-exporter keeps
graph.py's import stable — a single name change here is all that was needed.

graph.py continues to import from this module:
    from seam.indexer.graph_java_csharp import (
        _extract_comments_csharp, _extract_comments_java,
        _extract_edges_csharp, _extract_edges_java,
        _extract_symbols_csharp, _extract_symbols_java,
    )
"""

# Re-export Java extractors — the actual code lives in graph_java.py.
# Re-export C# extractors — the actual code lives in graph_csharp.py.
from seam.indexer.graph_csharp import (
    _extract_comments_csharp,
    _extract_edges_csharp,
    _extract_symbols_csharp,
)
from seam.indexer.graph_java import (
    _extract_comments_java,
    _extract_edges_java,
    _extract_symbols_java,
)

__all__ = [
    "_extract_comments_java",
    "_extract_edges_java",
    "_extract_symbols_java",
    "_extract_comments_csharp",
    "_extract_edges_csharp",
    "_extract_symbols_csharp",
]
