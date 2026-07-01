"""MCP server setup — FastMCP stdio transport, fifteen tools registered.

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
    seam_schema       — read-only index capability and freshness map (Phase 11)
    seam_snippet      — exact bounded source retrieval for one indexed symbol (Phase 11)
    seam_graph_search — typed structural graph discovery over symbols/edges (Phase 11)

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
    handle_seam_graph_search,
    handle_seam_impact,
    handle_seam_query,
    handle_seam_schema,
    handle_seam_search,
    handle_seam_snippet,
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
_IMPACT_MAX_BYTES_DEFAULT = config.SEAM_IMPACT_MAX_BYTES


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
    """Configure and return a FastMCP server with all fifteen Seam tools registered.

    Phase 0:  seam_query, seam_context, seam_search
    Phase 1:  seam_impact, seam_trace, seam_changes
    Phase 1b: seam_why
    Phase 2:  seam_clusters
    Phase 3:  seam_affected
    Phase 6:  seam_context_pack
    Flows:    seam_flows
    Tier D11: seam_structure
    Phase 11: seam_schema, seam_snippet, seam_graph_search

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
    def seam_schema(verbose: bool = False) -> Any:
        """Describe the current Seam index capabilities before choosing deeper tools.

        Use this as the first call in an unfamiliar repo. It reports index freshness,
        schema/version identity, counts, feature population, warnings, tool guidance,
        and optional verbose table/column metadata. It is read-only and never repairs
        or mutates the index.
        """
        return _finalize(handle_seam_schema(conn, root, verbose=verbose))

    @mcp.tool()
    def seam_snippet(
        uid: str | None = None,
        symbol: str | None = None,
        file: str | None = None,
        line: int | None = None,
        context_lines: int = 0,
        max_lines: int = 200,
        max_bytes: int = 20_000,
        include_neighbors: bool = False,
    ) -> Any:
        """Retrieve bounded live source for one exact indexed symbol.

        Use this after seam_search or seam_query when you need the implementation
        body for a returned uid without asking for broad graph context. Source is
        read only after root containment is checked, and the response reports
        freshness and truncation warnings when the live file may not match the
        indexed range.
        """
        return _finalize(
            handle_seam_snippet(
                conn,
                root,
                uid=uid,
                symbol=symbol,
                file=file,
                line=line,
                context_lines=context_lines,
                max_lines=max_lines,
                max_bytes=max_bytes,
                include_neighbors=include_neighbors,
            )
        )

    @mcp.tool()
    def seam_graph_search(
        kind: str | None = None,
        name_pattern: str | None = None,
        qualified_name_pattern: str | None = None,
        file_pattern: str | None = None,
        language: str | None = None,
        edge_kind: str | None = None,
        direction: str = "both",
        min_degree: int | None = None,
        max_degree: int | None = None,
        min_in_degree: int | None = None,
        max_in_degree: int | None = None,
        min_out_degree: int | None = None,
        max_out_degree: int | None = None,
        confidence: str | None = None,
        synthesized: str = "any",
        cluster_id: int | None = None,
        visibility: str | None = None,
        is_exported: bool | None = None,
        test_scope: str = "any",
        preset: str | None = None,
        sort: str = "default",
        limit: int = 20,
        offset: int = 0,
        include_preview: bool = False,
        preview_limit: int = 3,
        regex: bool = False,
    ) -> Any:
        """Find symbols by graph shape before you know the exact symbol name.

        Use this for dead-code suspects, fan-in/fan-out hotspots, field readers
        and writers, inheritance relationships, or other typed structural filters.
        Results are metadata-only and include UIDs for follow-up seam_snippet,
        seam_context, seam_impact, or seam_trace calls.
        """
        return _finalize(
            handle_seam_graph_search(
                conn,
                root,
                kind=kind,
                name_pattern=name_pattern,
                qualified_name_pattern=qualified_name_pattern,
                file_pattern=file_pattern,
                language=language,
                edge_kind=edge_kind,
                direction=direction,
                min_degree=min_degree,
                max_degree=max_degree,
                min_in_degree=min_in_degree,
                max_in_degree=max_in_degree,
                min_out_degree=min_out_degree,
                max_out_degree=max_out_degree,
                confidence=confidence,
                synthesized=synthesized,
                cluster_id=cluster_id,
                visibility=visibility,
                is_exported=is_exported,
                test_scope=test_scope,
                preset=preset,
                sort=sort,
                limit=limit,
                offset=offset,
                include_preview=include_preview,
                preview_limit=preview_limit,
                regex=regex,
            )
        )

    @mcp.tool()
    def seam_impact(
        target: str = "",
        direction: str = _IMPACT_DIRECTION_DEFAULT,
        max_depth: int = _IMPACT_DEPTH_DEFAULT,
        include_tests: bool = False,
        verbose: bool = True,
        limit: int = _IMPACT_LIMIT_DEFAULT,
        max_bytes: int = _IMPACT_MAX_BYTES_DEFAULT,
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

        E4 — Edge provenance (SEAM_EDGE_PROVENANCE=on, default):
          kind           — the edge kind via which this dependent was reached. Full
                           vocabulary: call | import | extends | implements | instantiates
                           | holds | reads | writes | uses. Lets you distinguish a hard
                           call-edge dependent from a data-coupling (reads/holds) or
                           signature-coupling (uses) dependent. Always present in both
                           verbose and lean modes (core field, never stripped).
          synthesized_by — synthesis channel name when the edge is heuristic (e.g.
                           "interface-override", "closure-collection", "event-emitter"),
                           null when statically extracted. Lets you weight entries that
                           rest on over-approximated synthesized edges differently.
                           Null is RETAINED (null = "static edge", the informative common
                           case — unlike best_candidate which is E1-omitted when null).
                           Stripped in lean mode (verbose=false), like resolved_by.
                           AMBIGUITY (important): null does NOT, on its own, prove this
                           edge is static — an index that was never synthesis-rebuilt
                           (pre-v12, or SEAM_EDGE_SYNTHESIS=off at index time) carries
                           null for EVERY edge. So all-null across a result can mean
                           "no synthesized edges traversed" OR "synthesis never ran" — it
                           does not distinguish them; run `seam init` to populate. A
                           MISSING key (not null) means SEAM_EDGE_PROVENANCE=off or lean
                           mode stripped it — three distinct "no value" states.
          Set SEAM_EDGE_PROVENANCE=off for byte-identical pre-E4 output.

        E4 — Truncation steer (SEAM_IMPACT_STEER=on, default):
          next_actions   — top-level list[str] of ready-to-act prose hints, PRESENT only
                           when ≥1 entry was trimmed (by count cap or byte ceiling).
                           ABSENT when nothing was trimmed (so its presence is an
                           unambiguous "there is more" signal). Hints name the specific
                           direction+tier+count trimmed and the exact remedy (raise limit,
                           zero max_bytes, etc.) — so you know what to do, not just that
                           something was dropped. An all-trimmed response (empty entries
                           but non-empty risk_summary) includes a warning that the blast
                           radius was trimmed to nothing, NOT that no dependents exist.
                           Set SEAM_IMPACT_STEER=off to suppress next_actions entirely.

        By default (include_tests=false) the result is the PRODUCTION blast radius:
        test-file dependents are filtered out and their count is reported as hidden_tests.
        This keeps "what breaks?" focused on production code (test callers otherwise
        dominate the tiers and trip the per-tier cap). Set include_tests=true to include
        test dependents too; or use seam_affected to get the impacted test files directly.

        Set verbose=false to omit heavy enrichment fields (resolved_by, best_candidate,
        synthesized_by) from every tier entry and get a compact response. kind is always
        kept even in lean mode. verbose=true (default) returns all enrichment fields.
        NOTE: risk_summary, truncated, and the per-tier cap apply regardless of verbose.
        Use limit=0 for the full, uncapped transitive set.

        limit controls the per-tier entry cap (default: SEAM_IMPACT_MAX_RESULTS=25).
        Set limit=0 to disable the cap and receive all transitive entries.
        The response always includes risk_summary: {direction: {tier: count}} computed
        from the full pre-cap result, so the blast radius size is always visible.
        When entries were truncated, truncated: {direction: {tier: omitted}} is included.

        max_bytes controls the per-call character budget for the serialized output
        (characters of compact JSON). Default: SEAM_IMPACT_MAX_BYTES (0 = unlimited).
        When > 0, runs after the per-tier count cap and E2/E3 relevance ordering, trimming
        entries from the least-valuable end (downstream before upstream, MAY_NEED_TESTING
        before WILL_BREAK, tail before front) until the output fits. Byte-dropped counts
        are merged into truncated additively. When the ceiling fires, byte_capped is added:
        {"limit": <budget>, "omitted": <total entries dropped by the byte pass>}.
        byte_capped is absent when max_bytes=0 or when everything fit (no trimming).
        risk_summary remains the honest full pre-cap total regardless of byte trimming.

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
                max_bytes=max_bytes,
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
        where each hop carries the edge kind and per-edge confidence
        (EXTRACTED | INFERRED | AMBIGUOUS).

        Full edge kind vocabulary (E4 corrected from stale 'call | import'):
          call | import | extends | implements | instantiates | holds | reads | writes | uses
        The hop kind reflects the actual relationship traversed — e.g. a 'holds' hop
        means one class stores the other as a typed field, while a 'reads' hop means
        a field-access read edge was traversed.

        E4 — synthesized_by on each hop (SEAM_EDGE_PROVENANCE=on, default):
          synthesized_by — synthesis channel name when the hop is a heuristic synthesized
                           edge (e.g. "interface-override"), null when statically extracted.
                           Lets you see which hops in a path rest on over-approximations.
                           In lean mode (verbose=false), synthesized_by is stripped
                           (like resolved_by) — kind is always kept.
                           AMBIGUITY (important): null on a hop does NOT prove the hop is
                           static — an index never synthesis-rebuilt (pre-v12, or
                           SEAM_EDGE_SYNTHESIS=off at index time) carries null for EVERY
                           hop. all-null can mean "no synthesized hops" OR "synthesis never
                           ran"; run `seam init` to populate. A MISSING key means lean mode
                           or SEAM_EDGE_PROVENANCE=off.

        Also returns one-hop callers and callees for both symbols so you can see
        the immediate neighborhood alongside the path.

        Use this when you need to understand how control flows from one symbol to
        another, or to answer "how does X reach Y?" without manual grep.

        Returns found=false (paths=[]) when no path exists — this is a real,
        distinguishable "not connected" answer, not an error.

        Per-hop confidence lets you flag any hop that rests on an AMBIGUOUS edge
        (name collision at extraction time) so you know which conclusions are certain
        and which need manual verification.

        Set verbose=false to omit heavy fields (resolved_by, best_candidate,
        synthesized_by) from every hop and edge hop. kind is always kept.
        verbose=true (default) is byte-identical to pre-Phase-8 (plus E4 fields).

        Pass uid / target_uid (stable handles from seam_search/seam_query results)
        instead of source / target to pin exact symbols and skip re-disambiguation.
        """
        return _finalize(
            handle_seam_trace(
                conn,
                source,
                target,
                root,
                max_depth=max_depth,
                verbose=verbose,
                uid=uid,
                target_uid=target_uid,
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
    def seam_structure(
        path: str | None = None,
        depth: int | None = None,
        nodes: int | None = None,
        symbols: bool = False,
    ) -> Any:
        """Get the whole-repository directory/file/container structure tree.

        By default this is a MODULE/AREA OVERVIEW — a nested tree of:
          dir nodes       — directories in the repository hierarchy
          file nodes      — indexed source files under each directory
          container nodes — class/interface/type symbols within each file

        Standalone module-level functions and method/member symbols are rolled up into
        counts rather than listed as nodes — this keeps the tree a compact "what are the
        main modules?" skeleton instead of an exhaustive symbol dump. Pass symbols=True to
        also list standalone functions under each file (for a detailed structural view).

        Each node carries:
          kind:         'dir' | 'file' | 'container' | 'function'
          name:         display name (dir basename, file name, symbol name)
          path:         repo-root-relative path; null for container and function nodes
          symbol_count: total symbol rows in this subtree
          area:         functional-area label from cluster data (null if no clustering)
          children:     child nodes
          members:      count of method/member rows rolled into this container (0 for non-containers)
          truncated:    count of nodes omitted by depth/node caps (0 = nothing trimmed)

        Optional scoping and bounds (Slice 3):
          path:  Scope the tree to a subdirectory. A relative path resolves against the
                 repo root (NOT the server cwd); an absolute path is honoured as-is.
                 An unknown or out-of-tree path degrades to {found: false} — never an error.
          depth: Maximum nesting depth (root=0). Nodes beyond this depth are dropped
                 and counted in `truncated`. Defaults to SEAM_STRUCTURE_MAX_DEPTH (8).
          nodes: Maximum total non-root nodes. Excess nodes are dropped BFS-order (closest
                 to root survive) and counted in `truncated`. 0 = unlimited.
                 Defaults to SEAM_STRUCTURE_MAX_NODES (2000).
          symbols: When true, also list standalone module-level functions as nodes under
                 each file. Default false = compact module/area overview (dirs, files,
                 classes only). Turn on for a detailed per-symbol structural view.

        Use this to get a structural overview before diving into a specific file or
        symbol, or to understand how files and containers are organized across the repo.

        Returns {found: false} when the (scoped) tree has no symbols — an empty/not-yet
        indexed repo, or a scope path that matches no indexed files.
        """
        # Pass the scope path string straight through: handle_seam_structure /
        # build_structure resolve a relative path against `root`, not the server cwd.
        scope_path = Path(path) if path else None
        result = handle_seam_structure(
            conn,
            root,
            path=scope_path,
            max_depth=depth,
            max_nodes=nodes,
            include_functions=symbols,
        )
        # Normalize a genuinely-empty (scoped) tree to the not-found sentinel so
        # _finalize emits {found: false} — matching every sibling read tool and the
        # docstring contract. The CLI keeps rendering the (possibly empty) tree itself.
        tree = result["tree"]
        if not tree["children"] and tree["symbol_count"] == 0:
            return _finalize(None)
        return _finalize(result)

    return mcp
