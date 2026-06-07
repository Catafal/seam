"""MCP server setup — FastMCP stdio transport, twelve tools registered.

Creates and configures the MCP server instance.
Tool handlers in tools.py are thin adapters; this module wires them to FastMCP.

Usage (from cli/main.py):
    server = create_server(conn, root)
    server.run(transport="stdio")

Tools registered (Phase 0 + Phase 1 + Phase 1b + Phase 2 + Phase 3 + Phase 6 + Tier D11):
    seam_query        — FTS5 + 1-hop graph expansion search
    seam_context      — 360-degree symbol view (callers, callees, location, cluster)
    seam_search       — full-text search (FTS5 BM25)
    seam_impact       — blast-radius analysis by risk tier (Phase 1)
    seam_trace        — shortest call/dependency path between two symbols (Phase 1)
    seam_changes      — git diff → changed symbols → risk level (Phase 1)
    seam_why          — semantic comments (WHY/HACK/NOTE/TODO/FIXME) near a location (Phase 1b)
    seam_clusters     — list clusters or members of a cluster (Phase 2)
    seam_affected     — changed files → impacted test files via reverse-dependency BFS (Phase 3)
    seam_context_pack — enriched context bundle: target + neighbors + WHY + peers (Phase 6)
    seam_flows        — execution flows: entry points + forward call-chain expansion
    seam_structure    — whole-repo directory/file/container structure tree (Tier D11)

Design:
- One FastMCP instance per process; connection is injected at creation time.
- Tools are closures capturing conn + root so FastMCP's decorator pattern
  (which does not pass state through the call signature) stays clean.
- Return types are Any to avoid FastMCP structured-output mode, which wraps
  results in a Pydantic model we don't need.
"""

import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

import seam.config as config
from seam.analysis.changes import DEFAULT_BASE_REF
from seam.server.tools import (
    handle_seam_affected,
    handle_seam_changes,
    handle_seam_clusters,
    handle_seam_context,
    handle_seam_context_pack,
    handle_seam_flows,
    handle_seam_impact,
    handle_seam_query,
    handle_seam_search,
    handle_seam_structure,
    handle_seam_trace,
    handle_seam_why,
)

# Limit defaults/bounds (mirrors tools.py constants — kept local to avoid circular import)
_QUERY_LIMIT_DEFAULT = 10
_SEARCH_LIMIT_DEFAULT = 20
_IMPACT_DEPTH_DEFAULT = 3
_IMPACT_DIRECTION_DEFAULT = "upstream"
_TRACE_DEPTH_DEFAULT = 10
_CHANGES_SCOPE_DEFAULT = "working"
# Import DEFAULT_BASE_REF from analysis.changes instead of redefining it
# to avoid drift when the canonical default changes.
_CHANGES_BASE_REF_DEFAULT = DEFAULT_BASE_REF

_AFFECTED_DEPTH_DEFAULT = config.SEAM_AFFECTED_DEPTH
_IMPACT_LIMIT_DEFAULT = config.SEAM_IMPACT_MAX_RESULTS


def _finalize(result: Any) -> Any:
    """Normalize a handler result to the MCP transport's native failure contract.

    WHY: FastMCP only sets isError=True when a tool *raises* — returning an error
    dict leaves isError=False, so a protocol-compliant agent (which checks isError)
    reads a rejection as success. The handlers return {"error": CODE, "message": ...}
    sentinels (kept for the CLI's {ok:false,error:{code,message}} envelope), so here —
    at the MCP boundary only — we raise on that sentinel and let FastMCP flip isError.
    A None ("handler found nothing") becomes a structured {"found": false} so agents
    never receive empty content for a valid no-result answer. Every other value
    (success dict, list) passes through byte-identical.

    The CLI and MCP thus expose the SAME code+message via each transport's native
    error signal — not byte-identical JSON (clean JSON cannot survive FastMCP's
    "Error executing tool <name>: " content prefix on a raise).
    """
    if result is None:
        return {"found": False}
    if isinstance(result, dict) and "error" in result and "message" in result:
        raise ToolError(f"{result['error']}: {result['message']}")
    return result


def create_server(conn: sqlite3.Connection, root: Path) -> FastMCP:
    """Configure and return a FastMCP server with all twelve Seam tools registered.

    Phase 0:  seam_query, seam_context, seam_search
    Phase 1:  seam_impact, seam_trace, seam_changes
    Phase 1b: seam_why
    Phase 2:  seam_clusters
    Phase 3:  seam_affected
    Phase 6:  seam_context_pack
    Flows:    seam_flows
    Tier D11: seam_structure

    Args:
        conn: Open SQLite connection to the Seam index DB.
        root: Project root Path — used to relativize file paths in results
              and as the git repo root for seam_changes.

    Returns:
        A FastMCP instance ready for server.run(transport="stdio").
    """
    mcp: FastMCP = FastMCP(name="seam")

    @mcp.tool()
    def seam_query(concept: str, limit: int = _QUERY_LIMIT_DEFAULT) -> Any:
        """Find all code related to a concept using hybrid search (FTS5 + 1-hop graph expansion).

        Use this when you need to find where a concept lives across the codebase.

        No `verbose` flag: query results carry no Phase 4/5 enrichment fields, so lean
        mode would be a no-op — query is enrichment-free, like seam_search.
        """
        return _finalize(handle_seam_query(conn, concept, root, limit=limit))

    @mcp.tool()
    def seam_context(symbol: str = "", verbose: bool = True, uid: str | None = None) -> Any:
        """Get a 360-degree view of a symbol: its callers, callees, file location, and docstring.

        Use before touching any existing function or class.

        Pass uid (a stable handle from a seam_search/seam_query result) instead of
        symbol to pin the EXACT (file, line) symbol — bypassing homonym ambiguity and
        saving a disambiguation round-trip. When uid is given, symbol is ignored.

        Set verbose=false to omit heavy enrichment fields (decorators, is_exported,
        visibility, qualified_name) and receive a compact response. signature and all
        core identity fields are always kept.
        verbose=true (default) is byte-identical to the pre-Phase-8 output.

        Returns {found: false} when the symbol/uid is not in the index.
        """
        return _finalize(handle_seam_context(conn, symbol, root, verbose=verbose, uid=uid))

    @mcp.tool()
    def seam_search(text: str, limit: int = _SEARCH_LIMIT_DEFAULT) -> Any:
        """Full-text search across all indexed symbol names and docstrings (FTS5 BM25).

        Use when you know a keyword but not the exact symbol name.
        Supports FTS5 operators: AND, OR, NOT, phrase search in quotes.
        """
        return _finalize(handle_seam_search(conn, text, root, limit=limit))

    @mcp.tool()
    def seam_impact(
        target: str = "",
        direction: str = _IMPACT_DIRECTION_DEFAULT,
        max_depth: int = _IMPACT_DEPTH_DEFAULT,
        include_tests: bool = False,
        verbose: bool = True,
        limit: int = _IMPACT_LIMIT_DEFAULT,
        uid: str | None = None,
    ) -> Any:
        """Blast-radius analysis — what breaks if I change this symbol?

        Returns all symbols that depend on the target (upstream), that the target
        depends on (downstream), or both — grouped into risk tiers by distance:
          WILL_BREAK       (distance 1) — direct dependents, definitely affected.
          LIKELY_AFFECTED  (distance 2) — indirect dependents, probably affected.
          MAY_NEED_TESTING (distance 3+) — transitive dependents, test to be sure.

        Each entry carries the aggregated path confidence (EXTRACTED | INFERRED | AMBIGUOUS)
        so you know which conclusions to lean on and which to verify by reading.
        Each entry also carries is_test (bool) so you can distinguish production dependents
        from test-only callers.

        By default (include_tests=false) the result is the PRODUCTION blast radius:
        test-file dependents are filtered out and their count is reported as hidden_tests.
        This keeps "what breaks?" focused on production code (test callers otherwise
        dominate the tiers and trip the per-tier cap). Set include_tests=true to include
        test dependents too; or use seam_affected to get the impacted test files directly.

        Set verbose=false to omit heavy enrichment fields (resolved_by, best_candidate)
        from every tier entry and get a compact response. verbose=true (default) returns
        all Phase 4/5 enrichment fields. NOTE: risk_summary, truncated, and the per-tier
        cap are NEW in Phase 8 and apply regardless of verbose — so the impact response is
        NOT byte-identical to pre-Phase-8 (unlike the other tools). Use limit=0 for the
        full, uncapped transitive set.

        limit controls the per-tier entry cap (default: SEAM_IMPACT_MAX_RESULTS=25).
        Set limit=0 to disable the cap and receive all transitive entries.
        The response always includes risk_summary: {direction: {tier: count}} computed
        from the full pre-cap result, so the blast radius size is always visible.
        When entries were truncated, truncated: {direction: {tier: omitted}} is included.

        Pass uid (a stable handle from a seam_search/seam_query result) instead of
        target to pin the exact symbol and skip homonym re-disambiguation.

        Use before editing any symbol to understand the blast radius.
        """
        return _finalize(
            handle_seam_impact(
                conn,
                target,
                root,
                direction=direction,
                max_depth=max_depth,
                include_tests=include_tests,
                verbose=verbose,
                limit=limit,
                uid=uid,
            )
        )

    @mcp.tool()
    def seam_trace(
        source: str = "",
        target: str = "",
        max_depth: int = _TRACE_DEPTH_DEFAULT,
        verbose: bool = True,
        uid: str | None = None,
        target_uid: str | None = None,
    ) -> Any:
        """Trace the call/dependency path between two symbols.

        Returns the shortest path from source to target as an ordered list of hops,
        where each hop carries the edge kind (call | import) and per-edge confidence
        (EXTRACTED | INFERRED | AMBIGUOUS).

        Also returns one-hop callers and callees for both symbols so you can see
        the immediate neighborhood alongside the path.

        Use this when you need to understand how control flows from one symbol to
        another, or to answer "how does X reach Y?" without manual grep.

        Returns found=false (paths=[]) when no path exists — this is a real,
        distinguishable "not connected" answer, not an error.

        Per-hop confidence lets you flag any hop that rests on an AMBIGUOUS edge
        (name collision at extraction time) so you know which conclusions are certain
        and which need manual verification.

        Set verbose=false to omit heavy fields (resolved_by, best_candidate) from
        every hop and edge hop. verbose=true (default) is byte-identical to pre-Phase-8.

        Pass uid / target_uid (stable handles from seam_search/seam_query results)
        instead of source / target to pin exact symbols and skip re-disambiguation.
        """
        return _finalize(
            handle_seam_trace(
                conn, source, target, root, max_depth=max_depth, verbose=verbose,
                uid=uid, target_uid=target_uid,
            )
        )

    @mcp.tool()
    def seam_changes(
        scope: str = _CHANGES_SCOPE_DEFAULT,
        base_ref: str = _CHANGES_BASE_REF_DEFAULT,
    ) -> Any:
        """Pre-commit risk check — map git diff to affected symbols and risk level.

        Diffs the working tree / staged set / branch against a git ref, maps each
        changed line range to the symbols it touched, runs impact analysis, and
        returns an overall risk level:
          low      — no downstream dependents found
          medium   — transitive dependents (MAY_NEED_TESTING)
          high     — indirect dependents (LIKELY_AFFECTED)
          critical — direct dependents (WILL_BREAK)

        scope values:
          working — git diff (unstaged working tree vs index)
          staged  — git diff --cached (staged changes only)
          branch  — git diff <base_ref>...HEAD (entire branch vs base ref)

        Use before committing to understand what your changes break.
        Fails (isError) with NOT_A_GIT_REPO when run outside a git repository.
        """
        return _finalize(handle_seam_changes(conn, root, base_ref=base_ref, scope=scope))

    @mcp.tool()
    def seam_why(
        file: str | None = None,
        line: int | None = None,
        symbol: str | None = None,
    ) -> Any:
        """Return semantic comments (WHY/HACK/NOTE/TODO/FIXME) near a location or symbol.

        Lookup modes (at least one of file or symbol is required):
          file only        — all semantic comments in that file.
          file + line      — comments within ±15 lines of that line number.
          symbol           — comments inside the symbol's body and just above its definition.

        Result fields per comment:
          file    — path relative to project root.
          line    — 1-based line number.
          marker  — WHY | HACK | NOTE | TODO | FIXME.
          text    — comment body after the marker, stripped.

        Returns an empty list (not an error) when a file/symbol has no semantic comments.
        Use before editing a function to read the documented intent and known caveats.
        """
        return _finalize(handle_seam_why(conn, root, file=file, line=line, symbol=symbol))

    @mcp.tool()
    def seam_clusters(cluster_id: int | None = None) -> Any:
        """List all code clusters (functional areas) or the members of one cluster.

        With no argument: returns [{id, label, size}] — an overview of all detected
        functional areas in the codebase, sorted by id.

        With cluster_id: returns [{name, file, line, kind}] — all symbols that belong
        to that cluster, with file paths relative to the project root.

        Clusters are computed during `seam init` using Louvain community detection over
        the call/import graph. Each cluster represents a cohesive group of symbols that
        frequently call or import each other.

        Returns an empty list (not an error) when the index has no cluster data —
        either the repo has no symbols, or `seam init` hasn't been run yet.

        Use this to understand the codebase's functional areas before diving into a
        specific subsystem. Then use seam_context to see a symbol's cluster peers.
        """
        return _finalize(handle_seam_clusters(conn, root, cluster_id=cluster_id))

    @mcp.tool()
    def seam_affected(
        changed_files: list[str],
        depth: int = _AFFECTED_DEPTH_DEFAULT,
    ) -> Any:
        """Find which test files are impacted by a set of changed source files.

        Given a list of changed file paths, traverses the reverse-dependency graph
        (upstream impact) to find all test files that depend on symbols in those files.

        Result shape:
            changed_files          — the input files (relativized to project root)
            affected_tests         — sorted unique test files that must be re-run
            total_dependents_traversed — count of all dependency entries examined

        Usage pattern (agent workflow):
            1. Get changed files from git: `git diff --name-only`
            2. Pass them to seam_affected to get the impacted test files
            3. Run only those tests: `pytest <affected_tests>`

        A changed file that is itself a test file is always included in affected_tests.
        Files not in the index are silently skipped (they have no graph dependents).
        Fails (isError) with INVALID_INPUT when changed_files is empty.

        Use this to determine the minimal set of tests to run before committing.
        """
        return _finalize(handle_seam_affected(conn, changed_files, root, depth=depth))

    @mcp.tool()
    def seam_context_pack(symbol: str, verbose: bool = True) -> Any:
        """Get a ready-to-paste context bundle for a symbol.

        Returns a single payload containing:
          target        — the symbol's full 360-degree view (file, kind, docstring,
                          signature, decorators, is_exported, visibility, ambiguous flag,
                          cluster info, raw callers/callees name lists)
          callers       — 1-hop callers, each enriched with {name, file, line, kind,
                          signature} — not just names. Capped at SEAM_PACK_NEIGHBOR_LIMIT.
          callees       — 1-hop callees, similarly enriched and capped.
          why           — WHY/HACK/NOTE/TODO/FIXME comments attached to the symbol.
                          Capped at SEAM_PACK_MAX_COMMENTS.
          cluster_peers — the symbol's functional-area peers (from seam_clusters).
          truncated     — {callers, callees, comments} counts of entries dropped by caps.

        When a neighbor name has no indexed declaration (external/unindexed symbol),
        it is silently skipped in callers/callees — not an error.

        Use this before modifying a symbol to get everything you need in one call:
        location, enriched neighbors, rationale comments, and functional area — without
        the five separate seam_context / seam_why / seam_context-on-each-neighbor calls.

        Set verbose=false to omit heavy enrichment fields (decorators, is_exported,
        visibility, qualified_name) from target and every neighbor. signature and core
        fields are always kept. verbose=true (default) is byte-identical to pre-Phase-8.

        Returns {found: false} when the symbol is not in the index (same contract as
        seam_context). Fails (isError) with INVALID_INPUT when symbol is blank/whitespace.
        """
        return _finalize(handle_seam_context_pack(conn, symbol, root, verbose=verbose))

    @mcp.tool()
    def seam_flows(entry: str | None = None) -> Any:
        """Discover execution flows — how the program actually runs, end to end.

        With no argument: returns {"entry_points": [{name, kind, file, reach}]} —
        the codebase's top execution starting points (call-graph roots ranked by
        how many symbols they reach downstream). On a real repo these are the CLI
        commands, web routes, MCP handlers, and main() — derived structurally, no AI.

        With an entry name: returns that entry point's flow tree — a depth/breadth-
        capped, cycle-safe expansion of what it calls, transitively:
            entry, kind, file
            steps        — nested [{name, kind, file, line, confidence, children,
                           truncated}] following the call chain forward
            total_steps  — number of symbols in the flow
            truncated    — True if depth/breadth caps cut part of the tree

        Use this to answer "how does feature X work?" in ONE call instead of reading
        the entry file and chasing every callee by hand. Start with no argument to
        see the entry points, then drill into the one you care about.

        Each symbol appears once (first reach wins — cycle-safe). Step confidence
        uses the fast name-count resolver; use seam_impact/seam_trace for import-
        promoted confidence. Returns {found: false} when the entry name is unknown.
        """
        return _finalize(handle_seam_flows(conn, root, entry=entry))

    @mcp.tool()
    def seam_structure() -> Any:
        """Get the whole-repository directory/file/container structure tree.

        Returns a nested tree of:
          dir nodes       — directories in the repository hierarchy
          file nodes      — indexed source files under each directory
          container nodes — class/interface/type symbols within each file
          function nodes  — top-level functions within each file

        Method/member symbols are rolled up into their owning container's
        `members` count and do NOT appear as separate nodes — this keeps the tree
        compact and focused on the structural skeleton rather than every detail.

        Each node carries:
          kind:         'dir' | 'file' | 'container' | 'function'
          name:         display name (dir basename, file name, symbol name)
          path:         repo-root-relative path; null for container and function nodes
          symbol_count: total symbol rows in this subtree
          area:         functional-area label (null — populated in a future slice)
          children:     child nodes
          members:      count of method/member rows rolled into this container (0 for non-containers)

        Use this to get a structural overview before diving into a specific file or
        symbol, or to understand how files and containers are organized across the repo.

        Returns {found: false} when the index has no symbols (empty repo or not yet indexed).
        """
        return _finalize(handle_seam_structure(conn, root))

    return mcp
