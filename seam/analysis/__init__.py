"""Analysis layer — read-only graph reasoning over the Seam SQLite index.

Import hierarchy (top to bottom, no cycles):
    cli / server → analysis → query → indexer / db
    Analysis MUST NOT import from server or cli.

Modules:
    traversal  — BFS edge-walk, cycle-safe, path-confidence aggregation
                 (weakest-hop + strongest-at-min-distance rules)
    impact     — blast-radius bucketing: tier-grouped Reached results from traversal
    flows      — path tracing (source→target shortest path) and one-hop
                 callers/callees queries
    changes    — git diff → changed symbols → impact rollup → ChangeReport + risk level
"""
