"""Analysis layer — read-only graph reasoning over the Seam SQLite index.

Import hierarchy: analysis imports from query/indexer/db and config.
Server and CLI import from analysis. Analysis MUST NOT import from server or cli.

Modules:
    traversal  — recursive edge-walk (BFS, cycle-safe, path-confidence aggregation)
    impact     — blast-radius analysis: tier-bucketed impact from traversal
"""
