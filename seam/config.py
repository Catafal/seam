"""Seam configuration — all settings read from environment with sensible defaults."""

import os
from pathlib import Path

# Path to the SQLite database, relative to the project root that runs `seam`
SEAM_DB_PATH: str = os.getenv("SEAM_DB_PATH", ".seam/seam.db")

# Logging level: DEBUG | INFO | WARNING | ERROR
SEAM_LOG_LEVEL: str = os.getenv("SEAM_LOG_LEVEL", "INFO")

# Debounce delay for file watcher (milliseconds)
SEAM_DEBOUNCE_MS: int = int(os.getenv("SEAM_DEBOUNCE_MS", "500"))

# Maximum file size to index (bytes). Files above this are silently skipped.
SEAM_MAX_FILE_BYTES: int = int(os.getenv("SEAM_MAX_FILE_BYTES", str(1024 * 1024)))  # 1MB

# Maximum number of changed symbol names to run impact() on in one detect_changes call.
# If the diff touches more real symbols than this cap, only the first N are analyzed
# and ChangeReport.partial is set to True. Raise via env var on large codebases.
SEAM_MAX_IMPACT_SYMBOLS: int = int(os.getenv("SEAM_MAX_IMPACT_SYMBOLS", "50"))

# File extensions to index, mapped to language identifier.
# Phase 9: added Java, C#, Ruby, C/H, C++, and PHP extensions.
# WHY .h → C (not C++): mixed C/C++ projects use .h for both C and C++ headers.
# Routing .h to the C++ grammar would break C-only projects; routing to C is the
# safer default and handles the common case (struct/typedef/function prototypes).
# C++-only header patterns (.hpp/.hh/.hxx) are explicitly mapped to C++.
# See ADR-008, limitation (a).
SEAM_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    # Phase 9 — Java
    ".java": "java",
    # Phase 9 — C# (csharp to avoid keyword collision)
    ".cs": "csharp",
    # Phase 9 — Ruby
    ".rb": "ruby",
    # Phase 9 — C (header .h → C; MVP decision)
    ".c": "c",
    ".h": "c",
    # Phase 9 — C++ (all common variants)
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    # Phase 9 — PHP
    ".php": "php",
    # Phase 10 — Swift
    ".swift": "swift",
}


# ── Phase 2: Clustering configuration ────────────────────────────────────────

# Cluster naming mode: "deterministic" (default) or "llm" (opt-in).
# When set to "llm", SEAM_LLM_API_KEY must also be set or naming falls back
# to "deterministic". LLM naming runs only during `seam init`, never in MCP.
SEAM_CLUSTER_NAMING: str = os.getenv("SEAM_CLUSTER_NAMING", "deterministic")

# Optional LLM API key for cluster naming (only used when SEAM_CLUSTER_NAMING=llm).
# When absent/empty, LLM naming is silently skipped and deterministic is used.
SEAM_LLM_API_KEY: str | None = os.getenv("SEAM_LLM_API_KEY") or None

# LLM model for cluster naming. Uses a small/fast model by default.
SEAM_LLM_MODEL: str = os.getenv("SEAM_LLM_MODEL", "gpt-4o-mini")

# Minimum cluster size. Communities with fewer distinct graph nodes than this
# are NOT persisted as clusters — their symbols get cluster_id=NULL (unclustered).
# Default 2: kills pure singletons (symbols with no edges) so `seam clusters`
# shows functional areas, not 200 one-symbol rows.
# Set to 1 to retain all singletons as their own clusters.
SEAM_CLUSTER_MIN_SIZE: int = int(os.getenv("SEAM_CLUSTER_MIN_SIZE", "2"))

# P2 — confidence-filtered Louvain. On LARGE graphs (symbol_count > this threshold),
# only high-trust edges (EXTRACTED + import-kind INFERRED) are passed to community
# detection, so noisy AMBIGUOUS/inferred-call edges can't merge unrelated modules.
# Small repos are unaffected (the full edge set keeps recall on sparse graphs).
# Special values: "off" disables the filter entirely (always pass all edges);
# "0" forces the filter on for any non-empty graph (used by tests). Default 1000.
SEAM_CLUSTER_CONFIDENCE_FILTER: str = os.getenv("SEAM_CLUSTER_CONFIDENCE_FILTER", "1000")


# ── Phase 3: Affected-tests configuration ────────────────────────────────────

# Maximum hop depth for the affected-tests traversal.
# Controls how far upstream (through the call/import graph) the `seam affected`
# command walks from each changed symbol to find dependent test files.
# Higher values find more distant test files but increase runtime on large graphs.
SEAM_AFFECTED_DEPTH: int = int(os.getenv("SEAM_AFFECTED_DEPTH", "5"))

# Maximum number of changed files accepted by handle_seam_affected.
# Inputs larger than this are rejected with INVALID_INPUT (agent mistake guard).
# Mirrors the _clamp discipline used by other bounded handlers.
SEAM_MAX_AFFECTED_FILES: int = int(os.getenv("SEAM_MAX_AFFECTED_FILES", "200"))

# Maximum symbols analyzed per file in affected().
# When a file defines more symbols than this, only the first N are traversed
# and AffectedResult.partial is set to True.
# Reuses the SEAM_MAX_IMPACT_SYMBOLS env var pattern for consistency.
SEAM_MAX_AFFECTED_SYMBOLS: int = int(os.getenv("SEAM_MAX_AFFECTED_SYMBOLS", "50"))

# ── Phase 3: Search / fuzzy fallback configuration ───────────────────────────

# Maximum Damerau-Levenshtein edit distance for the fuzzy fallback.
# Applied when both FTS and LIKE fallbacks return zero rows.
# 1 = catch single-char typos (conservative); 2 = broader (may add noise).
SEAM_FUZZY_MAX_DIST: int = int(os.getenv("SEAM_FUZZY_MAX_DIST", "1"))

# Maximum candidate symbol names to evaluate in the fuzzy fallback.
# Caps the O(n) edit-distance scan over distinct symbol names.
# On very large codebases, raise this via env var if precision matters more.
SEAM_FUZZY_MAX_CANDIDATES: int = int(os.getenv("SEAM_FUZZY_MAX_CANDIDATES", "500"))


# ── Phase 4: Node-field enrichment configuration ─────────────────────────────

# Hard cap on stored signature length. Without a cap, a function with many type-annotated
# parameters can produce a 500+ character signature that dominates the FTS index and makes
# MCP responses painful to read. 300 chars captures the full header of all but pathological
# cases; truncation appends '...' so consumers can detect incomplete signatures.
SEAM_MAX_SIGNATURE_LEN: int = int(os.getenv("SEAM_MAX_SIGNATURE_LEN", "300"))


# ── Phase 5: Import Resolution configuration ─────────────────────────────────

# Master switch for builtin filtering. When "off", count==0 names always resolve
# to INFERRED 'unresolved' regardless of whether they are known builtins.
SEAM_BUILTIN_FILTERING: str = os.getenv("SEAM_BUILTIN_FILTERING", "on")

# Master switch for import-resolution promotion (step A). When "off", import
# mappings are not extracted at index time and not used for promotion at read time.
# The name-count rule (existing behavior) is used exclusively.
SEAM_IMPORT_RESOLUTION: str = os.getenv("SEAM_IMPORT_RESOLUTION", "on")

# Cap on candidate declaring files evaluated per import-resolution lookup (step A).
# Prevents runaway DB queries on pathologically ambiguous indexes.
SEAM_MAX_IMPORT_CANDIDATES: int = int(os.getenv("SEAM_MAX_IMPORT_CANDIDATES", "25"))

# P6a — master switch for inheritance edge extraction. When "on" (default), class
# base-class / interface clauses are emitted as kind='extends' / kind='implements'
# edges (string-name-keyed: source=subclass, target=base), so an interface/base
# change surfaces its subclasses/implementers in seam_impact via upstream traversal.
# When "off", no inheritance edges are emitted — byte-identical to pre-P6a indexes.
SEAM_INHERITANCE_EDGES: str = os.getenv("SEAM_INHERITANCE_EDGES", "on")

# Cap on collision candidates ranked by file-path proximity (step D).
# Prevents O(n) proximity computation on large symbol tables.
SEAM_PROXIMITY_MAX_CANDIDATES: int = int(os.getenv("SEAM_PROXIMITY_MAX_CANDIDATES", "25"))

# P4 — barrel re-export following. Max hops to chase a named import through
# barrel index.ts/re-export files before giving up. When import promotion finds
# a candidate file that does NOT itself declare the exported name (i.e. it is a
# barrel that re-exports from siblings), the resolver follows that file's OWN
# import_mappings up to this many hops to find the real declarer. Bounded and
# cached per (file, name) within a single resolution → no unbounded read cost.
# Default 3 matches CodeGraph's barrel-chasing depth. Set to 0 to DISABLE barrel
# following entirely (byte-identical to pre-P4 behavior).
SEAM_BARREL_DEPTH: int = int(os.getenv("SEAM_BARREL_DEPTH", "3"))


# ── Phase 8: Lean output + impact cap ────────────────────────────────────────

# Per-tier entry cap for seam_impact. When a tier contains more entries than this
# cap, the list is sliced to this many entries (the closest/highest-risk first,
# since tiers are distance-ordered by construction). A summary count per tier
# (risk_summary) is always computed BEFORE capping so the histogram is honest.
# Set to 0 to disable the cap and return all entries.
# Default 25 prevents the "hub symbol dumps 200+ entries" problem (issue #33).
SEAM_IMPACT_MAX_RESULTS: int = int(os.getenv("SEAM_IMPACT_MAX_RESULTS", "25"))


# ── Phase 6: Context-Pack configuration ─────────────────────────────────────

# Maximum enriched callers AND maximum enriched callees in one context_pack bundle.
# When the raw neighbor list exceeds this, the list is truncated and the count of
# dropped entries is reported in ContextPack.truncated.callers/callees.
SEAM_PACK_NEIGHBOR_LIMIT: int = int(os.getenv("SEAM_PACK_NEIGHBOR_LIMIT", "10"))

# Maximum neighbor entries from any single file (homonym diversity cap).
# When a hot utility file defines many same-named symbols, capping per file
# keeps the bundle diverse across the codebase.
# Applied BEFORE the global neighbor limit (PRD §4.5a).
SEAM_PACK_PER_FILE_CAP: int = int(os.getenv("SEAM_PACK_PER_FILE_CAP", "3"))

# Maximum WHY/HACK/NOTE/TODO/FIXME comments in the bundle.
SEAM_PACK_MAX_COMMENTS: int = int(os.getenv("SEAM_PACK_MAX_COMMENTS", "10"))

# ── Execution flows configuration (seam_flows) ───────────────────────────────

# Max entry points returned by seam_flows in list mode.
# Entry points are call-graph roots ranked by downstream reach; the top N are
# the program's main execution starting points (CLI commands, web routes, etc.).
SEAM_FLOW_ENTRY_LIMIT: int = int(os.getenv("SEAM_FLOW_ENTRY_LIMIT", "20"))

# Max depth (levels of callees) when expanding a single flow tree.
# Bounds the tree on deep call chains; nodes beyond this are cut and the flow
# is marked truncated=True.
SEAM_FLOW_MAX_DEPTH: int = int(os.getenv("SEAM_FLOW_MAX_DEPTH", "6"))

# Max children (callees) shown per node when expanding a flow tree.
# Caps fan-out at hub symbols (a function that calls 50 helpers); excess callees
# are dropped and the flow is marked truncated=True.
SEAM_FLOW_MAX_BREADTH: int = int(os.getenv("SEAM_FLOW_MAX_BREADTH", "8"))

# BFS depth used only to SCORE entry-point reach (how many symbols a root reaches).
# Separate from MAX_DEPTH: scoring wants a stable ranking signal, not a full walk.
SEAM_FLOW_REACH_DEPTH: int = int(os.getenv("SEAM_FLOW_REACH_DEPTH", "5"))

# P6b — framework entry-point scoring. When "on" (default), a per-symbol
# entry_score float is computed at INDEX time from the file's path pattern
# (e.g. views.py, routes/, controllers/) and the symbol's decorator text
# (e.g. @app.route, @router.get). list_entry_points() then ranks by
# entry_score * reach instead of raw reach, so a framework route (low reach)
# can outrank a deep utility. When "off", entry_score is still stored as the
# neutral baseline (1.0) and ranking is byte-identical to raw reach (pre-P6b).
SEAM_ENTRY_SCORE: str = os.getenv("SEAM_ENTRY_SCORE", "on")


# ── Semantic search configuration (opt-in, Phase Semantic) ───────────────────

# Master switch for semantic (embedding-based) search. "off" by default — opt-in.
# Set to "on" to enable hybrid FTS5 + cosine recall in seam_search / seam_query.
# Requires: (a) `[semantic]` extra installed, (b) `seam init --semantic` run to
# populate the `embeddings` table. When "off" (or extra absent, or no embeddings),
# falls back to the existing pure-FTS5 path — behaviour is byte-identical to today.
SEAM_SEMANTIC: str = os.getenv("SEAM_SEMANTIC", "off")

# Local embedding model identifier (fastembed / HuggingFace model name).
# Default: bge-small-en-v1.5 — 384-dim, quantized ONNX on CPU, ~67MB, MIT.
# The model is downloaded ONCE on first `seam init --semantic`; subsequent runs use
# the local fastembed cache. Changing this value requires a full `seam init --semantic`
# to repopulate the embeddings table — mixing model vectors silently degrades quality.
SEAM_EMBED_MODEL: str = os.getenv("SEAM_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# Top-k semantic candidates fetched before merging with FTS results via RRF.
# Higher values improve recall but cost more cosine comparisons. 20 is a good
# default at the scale of a typical codebase (1k–20k symbols).
SEAM_SEMANTIC_LIMIT: int = int(os.getenv("SEAM_SEMANTIC_LIMIT", "20"))

# Maximum number of stored embedding rows loaded per semantic scan.
# Bounds the brute-force cosine scan: rows beyond this cap are never loaded.
# Protects against unbounded memory use on very large indexes.
# Default 20000: covers ~20k symbols; adjust up if your codebase is larger.
SEAM_SEMANTIC_SCAN_CAP: int = int(os.getenv("SEAM_SEMANTIC_SCAN_CAP", "20000"))

# RRF smoothing constant k (used in Reciprocal Rank Fusion).
# k=60 is the standard value from Cormack, Clarke & Buettcher (SIGIR 2009).
# Higher k flattens rank differences; lower k amplifies them.
SEAM_RRF_K: int = int(os.getenv("SEAM_RRF_K", "60"))


# ── Tier A Slice 3: class/container member fan-out ───────────────────────────

# Maximum number of member bare names included in edge_match_names() when the
# queried symbol is a class/interface/struct container. Caps the fan-out on
# "god-class" containers that have hundreds of methods, which would otherwise
# produce huge IN clauses and bloat the token budget.
# Default 50: covers virtually all real-world classes; raise via env var on
# very large containers if precision matters more than query cost.
SEAM_NAME_EXPANSION_CAP: int = int(os.getenv("SEAM_NAME_EXPANSION_CAP", "50"))

# ── Tier A Slice 2: bare-name suffix scan cap ────────────────────────────────

# Maximum rows returned by the suffix scan (LIKE '%.name') inside
# resolve_query_to_defs(). Without a cap, a bare name like "get", "parse", or
# "run" can match thousands of qualified symbols (full-table scan, no index),
# and each row then triggers additional DB calls in context() — O(N*4) queries.
# Default 25 matches SEAM_MAX_IMPORT_CANDIDATES and is consistent with other caps.
# Set to 0 for unlimited (not recommended on large codebases).
SEAM_BARE_RESOLVE_CAP: int = int(os.getenv("SEAM_BARE_RESOLVE_CAP", "25"))


# ── Tier B B4: Receiver-type inference (Python + TypeScript/JS) ──────────────

# Master switch for receiver-type inference in Python and TypeScript/JS extractors.
# When "on" (default), the extractor resolves receiver expressions (class fields,
# function parameters, and local variables with type annotations) to qualified
# 'Type.method' call targets at INDEX time — e.g. `client: Client` → `Client.send`.
# This fixes the cross-class call collapse (AMBIGUOUS) by emitting the right target.
# Conservatism contract: only plain user types bind; optionals/generics/unknowns
# → None → bare target kept (never emit a wrong edge).
# When "off", inference is skipped entirely and targets remain bare — byte-identical
# to pre-Tier-B behavior. Gating via env var so tests can toggle without monkey-patching.
SEAM_TYPE_INFERENCE: str = os.getenv("SEAM_TYPE_INFERENCE", "on")


# ── P5: Swift inter-class call resolution ────────────────────────────────────

# Lightweight receiver-type inference for Swift call edges. When "on" (default),
# the Swift extractor resolves two HIGH-VALUE member-call patterns to qualified
# 'Type.method' edges at INDEX time:
#   (1) self.method()                 → '<EnclosingType>.method'
#   (2) ClassName().method() OR a var assigned from a class instantiation in the
#       SAME function scope (let x = Foo(); x.bar()) → 'Foo.bar'
# Tracking is function-scope-local (a var→class dict during the AST walk) — no
# cross-file inference. Set to "off" to revert to bare-identifier-only call edges
# (byte-identical to pre-P5 behavior). See ADR-009.
SEAM_SWIFT_TYPE_INFERENCE: str = os.getenv("SEAM_SWIFT_TYPE_INFERENCE", "on")


def get_db_path(project_root: Path) -> Path:
    """Resolve the database path relative to the project root."""
    return project_root / SEAM_DB_PATH
