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

# ── E2/E3: seam_impact output relevance shaping ─────────────────────────────
# Master switch for relevance ordering in seam_impact (handler-layer, read-path
# only). "on" (default) ranks EXTERNAL dependents ahead of the target's own
# container-members (self-references) BEFORE the per-tier cap, so the cap drops
# self-refs first and external dependents survive truncation. "off" reverts to
# the prior production-before-test ordering for byte-identical output.
# Handler-only: seam_changes / seam_affected call the analysis layer directly and
# are unaffected regardless of this setting.
SEAM_IMPACT_RELEVANCE_SORT: str = os.getenv("SEAM_IMPACT_RELEVANCE_SORT", "on")

# How seam_impact treats the target's own container-members (self-references):
#   "rank" (default) — keep them, but sort last so the cap drops them first
#                      (lossless: risk_summary still counts the full blast radius).
#   "hide"           — drop self-refs from entry lists, surface a hidden_self_refs
#                      count (mirrors hidden_tests); frees the most external budget.
#   "show"           — legacy: no self-ref special treatment (ordering falls back to
#                      production-before-test, same as RELEVANCE_SORT="off").
SEAM_IMPACT_SELF_REF: str = os.getenv("SEAM_IMPACT_SELF_REF", "rank")

# E1 — drop `best_candidate` from seam_impact entries when it is null. best_candidate
# is meaningful only for AMBIGUOUS entries (it is the proximity pick); for every
# EXTRACTED/INFERRED entry it is null and carries no signal. Omitting null is lossless
# (null ≡ absent per the established null-contract) and reclaims ~25 B/entry so more
# high-signal dependents survive an agent's byte/context budget under the per-tier cap.
# "off" = byte-identical revert (keeps `best_candidate: null`). Handler-layer, read-path
# only — no schema change, no re-index. seam_changes/seam_affected are unaffected (they
# call the analysis layer directly). resolved_by is always kept (genuine provenance).
SEAM_IMPACT_OMIT_NULL_CANDIDATE: str = os.getenv("SEAM_IMPACT_OMIT_NULL_CANDIDATE", "on")

# E1-FULL — opt-in byte ceiling for seam_impact output.
#
# WHY a byte ceiling (not just the per-tier count cap):
#   SEAM_IMPACT_MAX_RESULTS caps entries-per-tier, but a tier of 25 entries with long
#   signatures can be many kilobytes while a tier of 25 short entries is tiny. Agents
#   budget their context window in tokens (≈ bytes/chars), not entry counts, so the
#   count cap cannot guarantee the output fits a context budget. This knob adds a
#   hard byte ceiling so agents can say "give me the highest-signal dependents that
#   fit in N characters."
#
# HOW it works (handler-layer only, no re-index, no schema change):
#   After the existing per-tier count cap and E2/E3 relevance ordering, handle_seam_impact
#   trims entries from the least-valuable end of a global priority order — downstream
#   before upstream, MAY_NEED_TESTING before WILL_BREAK, tail of each tier before front —
#   until the serialized output fits the budget. Because E2/E3 already ordered entries
#   (externals first, production before test), the survivors are the highest-signal
#   dependents that fit.
#
# UNIT = characters (compact JSON byte count). A real tokenizer is an external,
#   model-specific dependency that violates Seam's zero-external-services rule.
#   Characters are deterministic and a stable ~4-chars/token proxy.
#
# DEFAULT = 0 (unlimited). 0 or any negative value means the byte ceiling is INACTIVE
#   and the output is byte-identical to the pre-feature behavior. Set to a positive
#   integer (e.g. 8000) to activate the ceiling.
#
# SCOPE: handler-layer and read-path only. seam_changes and seam_affected call the
#   analysis-layer impact() directly (below the handler) and are unaffected. No schema
#   migration or re-index is required — the ceiling is applied at response-assembly time.
SEAM_IMPACT_MAX_BYTES: int = int(os.getenv("SEAM_IMPACT_MAX_BYTES", "0"))


# ── E4: Edge provenance + truncation steer ───────────────────────────────────

# Master switch for edge-provenance fields on seam_impact entries and seam_trace hops.
# When "on" (default), each seam_impact tier entry and each seam_trace hop carries:
#   kind          — the edge kind that reached the dependent (call, reads, holds, …)
#   synthesized_by — synthesis channel name when heuristic, null for static edges
# This surfaces information already stored in v12 edges.kind + edges.synthesized_by
# so agents can distinguish a hard call edge from a heuristic synthesized edge.
# "off" = byte-identical pre-E4 output (neither field is emitted). Handler-layer and
# read-path only — no schema change, no re-index. seam_changes/seam_affected unaffected.
SEAM_EDGE_PROVENANCE: str = os.getenv("SEAM_EDGE_PROVENANCE", "on")

# Master switch for the next_actions truncation steer on seam_impact output.
# When "on" (default), a top-level `next_actions: list[str]` of ready-to-act prose
# hints is attached to the seam_impact response when ≥1 entry was trimmed by the
# per-tier count cap or the E1-FULL byte ceiling. The steer names the exact remedy
# (e.g. "Raise limit to 17 to see 12 more WILL_BREAK dependents"). ABSENT when
# nothing was trimmed — so its presence is an unambiguous "there is more" signal.
# "off" = byte-identical pre-E4 output (no next_actions key ever). Handler-layer and
# read-path only — no schema change, no re-index. seam_changes/seam_affected unaffected.
SEAM_IMPACT_STEER: str = os.getenv("SEAM_IMPACT_STEER", "on")


# ── P2: Index staleness banner ────────────────────────────────────────────────

# Master switch for the index-staleness check on graph-traversal MCP read tools.
# When "on" (default), the 5 graph-traversal handlers (seam_impact, seam_changes,
# seam_affected, seam_context, seam_trace) attach a structured index_status banner to
# their output when the index is stale — "this index is stale; results may be wrong;
# run seam sync/init". When fresh → no banner → output byte-identical to pre-feature.
# "off" = no banner ever, no stat IO, byte-identical to pre-feature. Handler-layer and
# read-path only — no schema change, no re-index. Single source of truth via
# seam/analysis/staleness.py (seam status also delegates to the same module).
SEAM_STALENESS_CHECK: str = os.getenv("SEAM_STALENESS_CHECK", "on")

# Maximum number of files stat'd per staleness verdict. Only the N most-recently-indexed
# real files are checked (newest indexed_at first, LIMIT N). A stale file that falls
# outside this window is not detected — documented limitation. Default 200 bounds the
# stat IO to ~5-20ms even on a network filesystem and prevents O(files) checks on the
# hot MCP read path.
SEAM_STALENESS_SCAN_CAP: int = int(os.getenv("SEAM_STALENESS_SCAN_CAP", "200"))

# Per-process verdict cache TTL in seconds. Within this window, repeated MCP read-tool
# calls in one server session reuse the cached verdict instead of re-stat'ing files.
# Default 5s: fresh enough for interactive use; prevents re-stat on every tool call in a
# rapid burst (e.g. an agent running seam_impact + seam_context back-to-back). Set to 0
# to disable caching (always re-stat; useful for testing).
SEAM_STALENESS_TTL_SECONDS: int = int(os.getenv("SEAM_STALENESS_TTL_SECONDS", "5"))


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

# E3 — rank context_pack neighbors by personalized-PageRank (RWR) relevance to the seed symbol
# BEFORE the per-file + global caps, so the kept N are the most relevant neighbors rather than the
# lowest-symbol-id ones. With restart-at-seed, a neighbor woven into the seed's local neighborhood
# (shares callers/callees → same functional cluster) outranks a globally-popular but topically-
# distant neighbor — relevance-to-the-seed, which raw degree cannot express. Pure-offline,
# deterministic; the stable sort preserves the prior min_id order within ties. "off" = byte-
# identical revert (min_id order). Read-path / MCP-tool only; no schema change, no re-index.
SEAM_PACK_RELEVANCE_RANK: str = os.getenv("SEAM_PACK_RELEVANCE_RANK", "on")

# Max nodes in the bounded local subgraph the RWR walk runs over (cost ceiling). The subgraph is a
# depth-capped BFS from the seed; once this many nodes are collected, expansion stops. Keeps RWR
# O(subgraph) — ~hundreds of nodes × ~30 power-iterations — never a whole-graph walk.
SEAM_RWR_MAX_NODES: int = int(os.getenv("SEAM_RWR_MAX_NODES", "500"))

# Max BFS depth (hops from the seed) when collecting the local subgraph for the RWR walk.
SEAM_RWR_MAX_DEPTH: int = int(os.getenv("SEAM_RWR_MAX_DEPTH", "3"))

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

# Maximum number of stored embedding rows considered per semantic scan.
# 0 = unlimited (default): all stored vectors are scanned — the mmap path reads the
#   full prebuilt artifact and the SQL fallback uses no LIMIT, so no symbol is silently
#   excluded from semantic search due to rowid ordering. With the WS2a mmap store
#   bounding memory via the OS page cache, unlimited scanning is the correct default.
# Positive N = optional safety ceiling for memory-constrained operators: at most N rows
#   are loaded in the SQL fallback path (LIMIT N), and at most N rows are considered
#   in the mmap path (matrix[:N] slice). Rows beyond the cap are invisible to semantic
#   search — intentional when the operator needs a hard memory bound, not the default.
SEAM_SEMANTIC_SCAN_CAP: int = int(os.getenv("SEAM_SEMANTIC_SCAN_CAP", "0"))

# RRF smoothing constant k (used in Reciprocal Rank Fusion).
# k=60 is the standard value from Cormack, Clarke & Buettcher (SIGIR 2009).
# Higher k flattens rank differences; lower k amplifies them.
SEAM_RRF_K: int = int(os.getenv("SEAM_RRF_K", "60"))

# ── WS2a: Persisted mmap vector store ────────────────────────────────────────

# Master switch for the persisted mmap vector store.
# When "on" (default), two things happen automatically:
#   1. WRITE: after a successful `seam init --semantic` / `seam sync --semantic` embed
#      pass, the embedding indexer writes a compact 3-file artifact beside the SQLite DB
#      in the .seam/ directory:
#        vectors.f32       — raw C-order float32 matrix (count × dim)
#        vectors.ids.i64   — int64 symbol-id sidecar, row-aligned with the matrix
#        vectors.meta.json — model, dim, count, index_version, dtype, byteorder
#      The write is ATOMIC (temp file + os.replace). A write failure is logged and never
#      fails the embed run (the SQLite path remains the source of truth).
#   2. READ: the semantic read path (seam_search / seam_query) prefers the mmap store:
#      it mmap-loads the artifact zero-copy, validates the metadata (model, dtype, size,
#      index-version token), and computes cosine via one numpy matmul. If the artifact
#      is absent, corrupt/truncated, model-mismatched, or stale (index-version mismatch),
#      it falls through to the existing SQLite brute-force path byte-identically.
#
# When "off": no artifact is written and no artifact is read. The behavior is byte-identical
#   to the current SQLite-only path (pre-WS2a), honoring Seam's E-series opt-out discipline.
#
# WHY mmap (vs SQL brute-force):
#   The SQL path rebuilds an (N, dim) float32 matrix from per-row blob decodes on EVERY
#   query. A long-lived MCP server reusing the mmap reuses the OS page cache across calls —
#   no per-query decode. A CLI one-shot reads the prebuilt file instead of re-decoding.
#   Critically, the SQL path is bounded by SEAM_SEMANTIC_SCAN_CAP (default 20,000 rows),
#   which silently drops symbols beyond the cap from all semantic results on large codebases.
#   The mmap path reads the full artifact written at embed time — no cap-induced recall loss.
#
# Staleness detection: the metadata stores an index-version token (COUNT + MAX(symbol_id)
#   for the model). At read time, the token is recomputed from the DB; a mismatch means
#   the artifact is stale → SQL fallback. This is the same cheap-derived-key pattern used
#   by the layout cache (SEAM_STALENESS_TTL_SECONDS).
#
# No schema change, no migration. The artifact lives in .seam/ which is already gitignored.
SEAM_VECTOR_STORE: str = os.getenv("SEAM_VECTOR_STORE", "on")

# ── WS1-A: Richer embedding input — body-slice enrichment ────────────────────

# Gate for including a leading slice of each symbol's implementation body in its
# embedding input. Default "off" — opt-in, mirrors SEAM_SEMANTIC.
# When "on": index_embeddings reads each source file at most once and appends a
# body slice (up to SEAM_EMBED_INPUT_MAX_CHARS chars) after the header. Vectors
# change — requires a full `seam init --semantic` re-index to repopulate.
# When "off": no disk reads, no body text, vectors byte-identical to pre-WS1-A.
SEAM_EMBED_BODY: str = os.getenv("SEAM_EMBED_BODY", "off")

# Character budget for the combined embedding input (header + body + comments) when
# SEAM_EMBED_BODY=on. The header (name + signature + docstring) is NEVER truncated;
# body fills any remaining budget, then comments fill any remaining after that.
# Default 2000: ~500 tokens — a ~4-char/token proxy (no tokenizer dependency),
# matches the SEAM_IMPACT_MAX_BYTES discipline.
# 0 = unlimited (no cap on body/comment content beyond the header) — mirrors the
# SEAM_IMPACT_MAX_BYTES convention where 0 = unlimited.  In practice, index_embeddings
# maps 0 → a large internal sentinel so that body + comments are included in full.
SEAM_EMBED_INPUT_MAX_CHARS: int = int(os.getenv("SEAM_EMBED_INPUT_MAX_CHARS", "2000"))


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


# ── Slice #77: Composition (holds) edges ────────────────────────────────────

# Master switch for composition edge extraction. When "on" (default), the extractor
# emits an Edge(kind="holds", confidence="INFERRED") from a class to every plain user
# type it stores as a typed field/property OR receives as a typed constructor/init
# parameter. This captures DI and composition relationships in the call graph so that
# seam_impact traversal surfaces which classes depend on a given type structurally (not
# only via explicit calls). Conservatism contract: only plain user type names bind — the
# same refusal rules as SEAM_TYPE_INFERENCE (optionals, generics, containers, primitives,
# dotted expressions are all rejected). When "off", the collector pass is skipped entirely
# and the produced edge set is byte-identical to pre-Slice-#77 behavior.
SEAM_COMPOSITION_EDGES: str = os.getenv("SEAM_COMPOSITION_EDGES", "on")

# ── Method-param composition: 'uses' edges ──────────────────────────────────
# When "on" (default), the extractor emits an Edge(kind="uses", confidence="INFERRED")
# from a function/method to every plain user type it references as a PARAMETER in its
# signature — e.g. `func showOverlay(companionManager: CompanionManager)` emits
# showOverlay -> CompanionManager. This makes a param-injected dependency a DIRECT (d=1)
# upstream dependent of the type, complementing `holds` (which captures only STORED
# composition — fields and constructor params). Conservatism contract is identical to
# SEAM_COMPOSITION_EDGES: only plain user type names bind (optionals/generics/containers/
# primitives/dotted expressions rejected via the same per-language plain-type helpers).
# Higher-volume than `holds` (most typed functions have ≥1 user-typed param) → impact/
# changes/affected verdicts WIDEN. Extraction-time only; "off" = byte-identical to
# pre-feature; requires `seam init` re-index to populate.
SEAM_PARAM_EDGES: str = os.getenv("SEAM_PARAM_EDGES", "on")


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


# ── Tier D #12: identifier compound-split tokenization (search recall) ───────

# When "on" (default), the indexer writes a camelCase/snake_case-split version of each
# symbol name (+ qualified_name segments) into symbols.search_text — a dedicated FTS5
# column — so a natural-language query like "push to talk monitor" matches the camelCase
# symbol GlobalPushToTalkShortcutMonitor. The query layer (fts.py) splits query terms with
# the SAME splitter so both sides tokenize identically.
# Index-time: toggling requires `seam init` re-index (search_text is computed at write time).
# When "off", search_text is stored NULL and query-term expansion is skipped — byte-identical
# to pre-Tier-D #12 keyword search.
SEAM_TOKENIZE_IDENTIFIERS: str = os.getenv("SEAM_TOKENIZE_IDENTIFIERS", "on")


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


# ── Tier D11: Structure view configuration (seam_structure) ──────────────────

# Maximum nesting depth of the structure tree.
# The root dir node is depth 0; its immediate children are depth 1, and so on.
# Nodes at depth > max_depth are cut from the tree (not returned); the cut
# count is added to StructureResult.truncated. Default 8 handles virtually
# all real codebases (3–5 levels is typical); raise via env var for monorepos.
SEAM_STRUCTURE_MAX_DEPTH: int = int(os.getenv("SEAM_STRUCTURE_MAX_DEPTH", "8"))

# Maximum total number of nodes in the structure tree (excluding the root itself).
# When the tree would exceed this count, excess nodes are dropped BFS-style (closest
# to root survive) and StructureResult.truncated reports how many were omitted.
# Default 2000: sufficient for most repos; prevents MCP token-budget explosions on
# giant codebases with thousands of files and containers.
# 0 (or any value <= 0) = UNLIMITED — no node cap (matches the seam_impact limit=0
# convention; avoids the footgun where a negative value silently empties the tree).
SEAM_STRUCTURE_MAX_NODES: int = int(os.getenv("SEAM_STRUCTURE_MAX_NODES", "2000"))


# ── Edge-synthesis post-pass (PRD #83, Slice #1) ─────────────────────────────

# Master switch for the edge-synthesis post-pass. When "on" (default), the post-pass
# runs after `seam init` and `seam sync` (gated on graph_changed or --force-synthesis)
# and synthesizes dynamic-dispatch edges the parser cannot see: e.g. interface→
# implementation method fan-out (A2 channel). Synthesized edges are stored as ordinary
# call edges tagged with the channel that produced them and lower confidence (INFERRED),
# so existing kind-agnostic traversal in seam_impact/seam_context/seam_trace picks them
# up automatically — no read-path changes needed.
#
# When "off", the synthesis pass is completely skipped; the graph is byte-identical to
# pre-synthesis behavior. This is an extraction-time knob: toggling it requires a full
# `seam init` re-index to take effect (the edges are already stored; changing the knob
# at read time has no retroactive effect).
SEAM_EDGE_SYNTHESIS: str = os.getenv("SEAM_EDGE_SYNTHESIS", "on")

# Per-channel fan-out cap for edge synthesis. For each base method that participates
# in the interface-override channel (A2), at most this many synthesized call edges are
# emitted. This bounds the graph explosion on a widely-implemented interface with many
# concrete types — e.g. a logger interface with 100 implementations would produce at
# most SEAM_SYNTHESIS_FANOUT_CAP edges per method, not 100. Conservative default of 40
# matches the typical class hierarchy depth in a real codebase.
# Set to 0 to disable the cap (emit all synthesized edges, potentially unbounded).
SEAM_SYNTHESIS_FANOUT_CAP: int = int(os.getenv("SEAM_SYNTHESIS_FANOUT_CAP", "40"))

# Total budget (bytes) of source text the synthesis pass loads into memory for the
# source-text channels (closure-collection, event-emitter). The pass reads every
# indexed file's text into one dict; on a very large monorepo that could exhaust
# memory at the final init step (after clustering already succeeded). Once the
# cumulative loaded size crosses this budget, no further files are read and a WARNING
# is logged — synthesis under-produces rather than OOM-killing the indexer. Mirrors
# the bounded-scan philosophy of SEAM_SEMANTIC_SCAN_CAP. Default 50 MB; 0 = unlimited.
SEAM_SYNTHESIS_MAX_SOURCE_BYTES: int = int(
    os.getenv("SEAM_SYNTHESIS_MAX_SOURCE_BYTES", str(50 * 1024 * 1024))
)


# ── A3: Field-access edges (reads/writes) + fields as first-class symbols ────

# Master switch for field-access edge extraction. When "on" (default), the extractor
# emits an Edge(kind="reads"|"writes") for each attribute access that is NOT in call
# position. Attribute accesses in call position (obj.method()) remain 'call' edges and
# are unchanged. Field/property declarations and first self.x = ... assignments become
# Symbol(kind="field", qualified_name="Type.field") when this is "on".
# When "off", the extractor produces a graph byte-identical to pre-A3 behavior:
# no 'field' symbols, no 'reads'/'writes' edges. Extraction-time only — toggling
# requires a full `seam init` re-index to take effect (same contract as
# SEAM_COMPOSITION_EDGES / SEAM_TYPE_INFERENCE).
SEAM_FIELD_ACCESS_EDGES: str = os.getenv("SEAM_FIELD_ACCESS_EDGES", "on")


# ── Phase 11 P2.1: 3D Constellation Explorer layout endpoint ─────────────────

# Default node cap for GET /api/graph/layout. The layout kernel is O(n^2) in numpy;
# at 2000 nodes the (n,n,3) float64 repulsion matrix is ~96 MB — well within
# a laptop's budget. Raise via env var for larger codebases if memory is not a concern.
SEAM_LAYOUT_MAX_NODES: int = int(os.getenv("SEAM_LAYOUT_MAX_NODES", "2000"))

# Hard OOM ceiling. The endpoint clamps max_nodes to this value before any computation
# so an untrusted caller cannot trigger a malloc of (n,n,3) * 8 bytes beyond this cap.
# Default 3000: (3000,3000,3) * 8 ≈ 216 MB — acceptable for a local dev server.
# This value also sets the Query(le=...) upper bound on the max_nodes param.
SEAM_LAYOUT_MAX_SAFE_NODES: int = int(os.getenv("SEAM_LAYOUT_MAX_SAFE_NODES", "3000"))

# Cache TTL (seconds) for the layout result. Reuses the existing staleness TTL so
# operators have one knob for "how stale is my read cache?" across all paths.
# The layout cache key is (MAX(files.indexed_at), max_nodes) — a change to the index
# produces a new key and forces a recompute on the next request.
# See: SEAM_STALENESS_TTL_SECONDS (defined above, default 5 s).


# ── P5.5: Opt-in diagnostics facility ────────────────────────────────────────

# Master switch for local diagnostics recording. "0" by default — true no-op.
# Set to "1" to enable the DiagnosticsRecorder, which appends lightweight operational
# metrics (RSS, FD count, DB size, query count, slow-query summaries, watcher counters)
# to a local NDJSON file inside .seam/. When "0": no file is created, no sampling runs,
# no atexit handler is registered, and the read path is byte-identical to pre-P5.5.
# WHY opt-in: diagnostics output is local-file-only (never network/telemetry) but still
# writes a file and consumes CPU for sampling; operators who don't need it pay zero cost.
SEAM_DIAGNOSTICS: str = os.getenv("SEAM_DIAGNOSTICS", "0")

# Path for the NDJSON diagnostics file. Default is inside .seam/ (already gitignored
# via `seam init`'s .seam/.gitignore) so diagnostics output is never committed.
# Configurable so operators can redirect to a scratch location (e.g. /tmp) if needed.
SEAM_DIAGNOSTICS_PATH: str = os.getenv("SEAM_DIAGNOSTICS_PATH", ".seam/diagnostics.ndjson")

# Slow-query threshold in milliseconds. A record_query() call with duration_ms >= this
# value appends a slow_query NDJSON line. Below this threshold: the query counter is
# still incremented but no line is written (zero IO). Default 100 ms — covers most
# real-world fast queries on small/mid codebases while surfacing O(100ms) outliers.
SEAM_DIAGNOSTICS_SLOW_MS: int = int(os.getenv("SEAM_DIAGNOSTICS_SLOW_MS", "100"))


# ── WS4 S2: Index artifact distribution ─────────────────────────────────────

# HTTPS URL template for downloading a pre-built index artifact.
# Must contain a `{sha}` placeholder which will be replaced with the commit SHA
# or build identifier at download time. Default "" (empty string) = feature inert —
# seam fetch (WS4 S3) checks for this value and skips network access when empty.
#
# Example:
#   SEAM_INDEX_ARTIFACT_URL="https://example.com/artifacts/seam/{sha}/seam-index.tar.gz"
#
# WHY a URL template: the SHA changes per commit / build tag but the URL structure
# is stable. A template lets CI publish the archive once and consumers reconstruct
# the exact URL from the commit they checked out.
SEAM_INDEX_ARTIFACT_URL: str = os.getenv("SEAM_INDEX_ARTIFACT_URL", "")

# Maximum number of first-parent ancestors to walk when the HEAD artifact is absent.
# `seam fetch` tries HEAD first, then walks up first-parent history (newest-first)
# up to this bound, fetching the nearest published artifact.
# Default 50: covers typical CI pipelines where artifacts are published every ~few commits.
# Set to 1 to disable fallback (HEAD only). 0 = effectively "HEAD only" (same as 1).
SEAM_FETCH_ANCESTOR_DEPTH: int = int(os.getenv("SEAM_FETCH_ANCESTOR_DEPTH", "50"))


def get_db_path(project_root: Path) -> Path:
    """Resolve the database path relative to the project root."""
    return project_root / SEAM_DB_PATH
