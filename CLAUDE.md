# Project: Seam

## What This Is
Local code intelligence MCP server — indexes codebases with tree-sitter, stores in SQLite, exposes `seam_query`, `seam_context`, `seam_search` via MCP so AI agents query instead of grep.

## Tech Stack
- Python 3.14+ | uv 0.9.14
- tree-sitter 0.25.2 + tree-sitter-python 0.25.0 + tree-sitter-typescript 0.23.2 + tree-sitter-go 0.25.0 + tree-sitter-rust 0.24.2 + tree-sitter-java 0.23.5 + tree-sitter-c-sharp 0.23.5 + tree-sitter-ruby 0.23.1 + tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4 + tree-sitter-php 0.24.1
- mcp 1.27.2 (stdio transport — OPTIONAL `server` extra, not a core dep) | watchdog 6.0.0 | typer 0.26.4 | tomlkit 0.15.0 (Codex install config)
- SQLite + FTS5 (built-in, no ORM) | pytest 9.0.3 | ruff 0.15.15 | mypy 2.1.0

## Commands
- `make gate` — Full verification (lint + typecheck + tests) — **run before every commit**
- `make install-dev` — Install all deps including dev
- `make fmt` — Format + fix lint (not part of gate)
- `make bench-semantic` — Run semantic recall benchmark (requires `[semantic]` extra + one-time model download; NOT part of gate)
- `uv run seam init` — Index current directory
- `uv run seam init --semantic` — Index + build local embeddings for hybrid semantic search (requires `pip install 'seam-code[semantic]'`; downloads model ~67 MB on first run)
- `uv run seam sync` — Incrementally reconcile the index (changed/added/removed files) + gated cluster recompute
- `uv run seam sync --semantic` — Incrementally embed new symbols after sync (orphan sweep + missing-set embed; faster than init --semantic)
- `uv run seam start` — Start MCP server + watcher
- `uv run seam status` — Show index stats (includes `embeddings` row: count + model, or mismatch warning)
- `uv run seam search <text>` / `seam query <concept>` / `seam context <symbol>` — CLI-only read
  commands (no MCP server needed); `--json`/`--quiet`, `--lean` on context; `--to-file` on context
  writes the full result to `.seam/out/` and prints a one-line summary + path (WS5)
- `uv run seam search <text> --no-semantic` — Force keyword-only FTS5, bypassing hybrid path
- `uv run seam query <concept> --no-semantic` — Force keyword-only FTS5, bypassing hybrid path
- `uv run seam impact <symbol>` / `seam trace <from> <to>` — blast-radius analysis and dependency
  path tracing; `--json`/`--quiet`, `--lean`, `--limit`, `--max-bytes` on impact; `--to-file` on
  both writes the full (untrimmed, verbose) result to `.seam/out/` and prints a summary + path (WS5)
- `uv run seam flows [entry]` — execution flows: list entry points (call-graph roots ranked by
  downstream reach), or expand one entry's forward call-chain tree; `--json`/`--quiet`; `--to-file`
  writes the full result to `.seam/out/` and prints a one-line summary + path (WS5)
- `uv run seam structure [path]` — whole-repo directory/file/container structure tree; `--json`/`--quiet`
- `uv run seam install` — CLI-FIRST default: write token-lean CLI guidance into an agent
  (Claude Code skill / Cursor `.mdc` rule / Codex/Zed `AGENTS.md` block / VS Code
  `.github/copilot-instructions.md` / Gemini `GEMINI.md`); `--with-mcp` ALSO writes
  the MCP config (`--target claude|cursor|codex|vscode|gemini|zed|all`, `--location project|user`,
  `--print-config`); `uv run seam uninstall` reverses both (guidance + MCP)
- `uv run seam serve` — Start the local Seam Explorer web server (FastAPI, 127.0.0.1:7420);
  requires `[web]` extra (`pip install 'seam-code[web]'`); `--host`, `--port`, `--no-open`
- `uv run seam rebase [path] [--from <old_root>]` — Re-home a fetched index by rewriting
  `files.path` prefixes from the CI root to the local root; auto-detects old root when `--from`
  is omitted (WS4); called automatically by `seam fetch`
- `uv run seam pack-index [path] [--dest <dir>]` — Pack the `.seam/` index into a canonical
  `seam-index.tar.gz` + `seam-index.sha256` sidecar for CI artifact sharing (WS4);
  **distinct from `seam pack <symbol>` which is the unrelated context-pack command**
- `uv run seam fetch [path] [--semantic]` — Download, verify, unpack, rebase, and sync a
  CI-prebuilt index for the current git SHA (nearest-ancestor fallback up to
  `SEAM_FETCH_ANCESTOR_DEPTH`); requires `SEAM_INDEX_ARTIFACT_URL` to be set (WS4)
- `uv sync` installs the CLI only; `uv sync --extra server` adds the optional MCP server (`mcp` package); `uv sync --extra semantic` adds fastembed for semantic search

## File References
- `DISCOVERY.md` — real goal (what we're building and why)
- `PRD.md` — requirements and acceptance criteria
- `APP_FLOW.md` — agent interaction flows
- `TECH_STACK.md` — exact package versions
- `BACKEND_STRUCTURE.md` — module map and import rules
- `IMPLEMENTATION_PLAN.md` — current task breakdown (build script)
- `progress.txt` — session state (READ THIS FIRST)
- `lessons.md` — gotchas and AI mistake log
- `docs/ARCHITECTURE.md` — system diagram and data flows
- `docs/database/schema.sql` — SQLite schema (authoritative)
- `docs/api-contracts/mcp-tools.yaml` — MCP tool specs
- `docs/adr/` — architecture decision records

## Package Layout
```
seam/config.py               ← all settings (env vars with defaults)
                                SEAM_LANGUAGE_MAP: .py .ts .tsx .js .mjs .cjs .go .rs .java .cs .rb
                                  .c .h .cpp .cc .cxx .c++ .hpp .hh .hxx .php .swift (12 languages)
                                                   .java .cs .rb .c .h .cpp .cc .cxx .c++ .hpp .hh .hxx .php
                                SEAM_CLUSTER_NAMING: "deterministic" | "llm" (default: deterministic)
                                SEAM_LLM_API_KEY: optional, required for llm naming
                                SEAM_LLM_MODEL: default "gpt-4o-mini"
                                SEAM_CLUSTER_MIN_SIZE: min community size (default: 2)
                                SEAM_AFFECTED_DEPTH: max upstream hops for affected traversal (default: 5)
                                SEAM_MAX_AFFECTED_FILES: max changed files per seam_affected call (default: 200)
                                SEAM_MAX_AFFECTED_SYMBOLS: max symbols analyzed per file in affected() (default: 50)
                                SEAM_FUZZY_MAX_DIST: max Damerau-Levenshtein distance for fuzzy fallback (default: 1)
                                SEAM_FUZZY_MAX_CANDIDATES: max symbol names evaluated in fuzzy scan (default: 500)
                                SEAM_MAX_SIGNATURE_LEN: max signature length in chars before truncation (default: 300)
                                SEAM_BUILTIN_FILTERING: "on" | "off" — tag count==0 names as builtin (default: on)
                                SEAM_IMPORT_RESOLUTION: "on" | "off" — import-promotion step A (default: on)
                                SEAM_MAX_IMPORT_CANDIDATES: cap on declaring files per import lookup (default: 25)
                                SEAM_PROXIMITY_MAX_CANDIDATES: cap on collision candidates for proximity ranking (default: 25)
                                SEAM_PACK_NEIGHBOR_LIMIT: max enriched callers and max enriched callees in context_pack (default: 10)
                                SEAM_PACK_PER_FILE_CAP: max neighbor entries from any single file — diversity cap (default: 3)
                                SEAM_PACK_MAX_COMMENTS: max WHY/HACK/NOTE comments in context_pack bundle (default: 10)
                                SEAM_PACK_RELEVANCE_RANK: "on" | "off" — rank context_pack neighbors by
                                  personalized-PageRank (RWR) relevance to the seed BEFORE the per-file +
                                  global caps, so the kept N are the most relevant neighbors not the
                                  lowest-symbol-id ones (E3; default: on). "off" = byte-identical revert
                                  (min_id order). Read-path/MCP-only; no re-index.
                                SEAM_RWR_MAX_NODES: max nodes in the bounded RWR local subgraph (default: 500)
                                SEAM_RWR_MAX_DEPTH: max BFS hops from the seed when collecting the RWR subgraph (default: 3)
                                SEAM_IMPACT_MAX_RESULTS: per-tier entry cap for seam_impact (default: 25; 0 = unlimited) [Phase 8]
                                SEAM_IMPACT_RELEVANCE_SORT: "on" | "off" — rank EXTERNAL dependents ahead of the
                                  target's own container-members (self-references) BEFORE the per-tier cap, so the
                                  cap drops self-refs first and external dependents survive truncation (E2/E3;
                                  default: on). "off" = byte-identical revert to prior production-before-test
                                  ordering. Handler-layer only — seam_changes/seam_affected are unaffected.
                                SEAM_IMPACT_SELF_REF: "rank" | "hide" | "show" — how seam_impact treats the target's
                                  own members (E2/E3; default: rank). "rank" = keep but sort last (lossless;
                                  risk_summary still counts them). "hide" = drop from entry lists + surface a
                                  hidden_self_refs count (mirrors hidden_tests). "show" = legacy, no special treatment.
                                SEAM_IMPACT_OMIT_NULL_CANDIDATE: "on" | "off" — drop `best_candidate` from
                                  seam_impact entries when it is null (E1; default: on). best_candidate is only
                                  meaningful for AMBIGUOUS entries (the proximity pick); for EXTRACTED/INFERRED it
                                  is always null, so omitting it is LOSSLESS (null ≡ absent) and reclaims ~25 B/
                                  entry — leaner default output so more high-signal dependents survive the per-tier
                                  cap. "off" = byte-identical revert (keeps `best_candidate: null`). resolved_by is
                                  always kept. Handler-layer, read-path only — no re-index; seam_changes/affected
                                  unaffected.
                                SEAM_IMPACT_MAX_BYTES: int — opt-in hard byte ceiling for seam_impact output
                                  (E1-FULL; default: 0 = unlimited = byte-identical to pre-feature). When > 0,
                                  entries are trimmed from the LEAST-VALUABLE end (downstream before upstream;
                                  MAY_NEED_TESTING before WILL_BREAK; intra-tier tail before front) until the
                                  serialized response fits. Unit = characters of compact JSON (the same
                                  serializer seam/cli/output.py uses) — a deterministic ~4-chars/token proxy
                                  with no external tokenizer dep. Runs AFTER the per-tier count cap and E2/E3
                                  relevance ordering. Dropped counts are merged INTO `truncated` additively;
                                  `byte_capped` = {"limit": int, "omitted": int} is added ONLY when the ceiling
                                  actually trimmed ≥1 entry. seam_changes/seam_affected call the analysis layer
                                  directly (below the handler) and are NEVER affected by this knob regardless
                                  of its value. Handler-layer, read-path only — no schema change, no re-index.
                                SEAM_EDGE_PROVENANCE: "on" | "off" — surface edge-provenance fields on
                                  seam_impact entries and seam_trace hops (E4; default: "on").
                                  When "on", each seam_impact tier entry carries:
                                    kind          — edge kind of the final hop (call | import | extends |
                                                    implements | instantiates | holds | reads | writes | uses)
                                    synthesized_by — synthesis channel name when heuristic (e.g.
                                                    'interface-override', 'closure-collection', 'event-emitter'),
                                                    or null when the hop is statically extracted.
                                  Each seam_trace hop gains synthesized_by (it already carried kind).
                                  kind is kept in lean mode (verbose=False); synthesized_by is stripped in
                                  lean mode (like resolved_by — provenance detail). null is RETAINED for
                                  synthesized_by in verbose mode (null = static edge, the common informative
                                  value), unlike best_candidate which is E1-omitted when null.
                                  "off" = byte-identical pre-E4 output (neither field emitted).
                                  Handler-layer, read-path only — no schema change, no re-index.
                                  seam_changes/seam_affected are always byte-stable (bypass the handler).
                                SEAM_IMPACT_STEER: "on" | "off" — emit a top-level `next_actions` list of
                                  ready-to-act truncation hints on seam_impact output (E4; default: "on").
                                  When "on", `next_actions: list[str]` is attached to the seam_impact response
                                  when ≥1 entry was trimmed (by the per-tier count cap or the E1-FULL byte
                                  ceiling). Each hint is a complete, actionable sentence naming the exact
                                  remedy (e.g. "Raise limit to 17 to see 12 more WILL_BREAK upstream
                                  dependents." / "Pass max_bytes=0 for the full untrimmed blast radius.").
                                  ABSENT when nothing was trimmed — presence is the "there is more" signal.
                                  When the byte ceiling dropped ALL entries, always includes an anti-false-safe
                                  warning ("blast radius was trimmed to nothing — this is NOT 'no dependents'").
                                  The steer stays WITHIN max_bytes (reserved inside the byte ceiling via
                                  _attach_steer's re-trim pass). Generated by the pure leaf seam/analysis/steer.py.
                                  "off" = byte-identical pre-E4 output (no next_actions key ever).
                                  Handler-layer, read-path only — no schema change, no re-index.
                                  seam_changes/seam_affected are always byte-stable (bypass the handler).
                                SEAM_STALENESS_CHECK: "on" | "off" — gate for the P2 index staleness banner
                                  on the 5 graph-traversal tools (default: "on"). When "on", after each of
                                  seam_impact / seam_changes / seam_affected / seam_context / seam_trace
                                  produces its result, _maybe_attach_staleness() appends a top-level
                                  `index_status` = {stale: true, reason, hint} banner when the index is
                                  detected stale. ABSENT when fresh — presence is the unambiguous stale signal.
                                  "off" = no stat IO, no banner, byte-identical to pre-P2. Handler-layer,
                                  read-path only — no schema change, no re-index. `seam status` freshness
                                  field is INDEPENDENT of this knob (CLI always checks, respect_knob=False).
                                SEAM_STALENESS_SCAN_CAP: int — max files stat'd per staleness verdict
                                  (P2; default: 200). The bounded scan checks only the newest SCAN_CAP files
                                  ordered by indexed_at DESC. Stale files OUTSIDE that window are not
                                  detected — documented limitation to keep per-call overhead sub-millisecond
                                  on repos with 10k+ files. Raise to 0 for an unlimited scan (not recommended
                                  on large repos). Read-path/MCP-only; no re-index.
                                SEAM_STALENESS_TTL_SECONDS: int — per-process verdict cache TTL in seconds
                                  (P2; default: 5). Only STALE verdicts are cached (the safe asymmetry — a
                                  fresh verdict is never cached so a file edited within the TTL window is
                                  detected on the next call). A known-stale verdict persists for up to TTL
                                  seconds after the index is fixed. Set to 0 to disable the cache entirely
                                  (every call re-scans). Read-path/MCP-only; no re-index.
                                SEAM_FLOW_ENTRY_LIMIT: max entry points listed by seam_flows (default: 20)
                                SEAM_FLOW_MAX_DEPTH: max depth when expanding a flow tree (default: 6)
                                SEAM_FLOW_MAX_BREADTH: max callees per node in a flow tree (default: 8)
                                SEAM_FLOW_REACH_DEPTH: BFS depth used to score entry-point reach (default: 5)
                                SEAM_SEMANTIC: "off" | "on" — master switch for hybrid semantic search (default: off)
                                SEAM_EMBED_MODEL: fastembed model name (default: "BAAI/bge-small-en-v1.5")
                                SEAM_SEMANTIC_LIMIT: top-k semantic candidates fetched before RRF merge (default: 20)
                                SEAM_SEMANTIC_SCAN_CAP: max embedding rows loaded per scan (default: 0 =
                                  unlimited; WS2a). 0 = all stored vectors scanned in both the SQL fallback
                                  path (no LIMIT clause) and the mmap path (all artifact rows considered) —
                                  no cap-induced recall loss. A positive N is an optional memory-safety ceiling
                                  for operators who need a hard bound; rows beyond N are invisible to semantic
                                  search in both paths. "off"/pre-WS2a default was 20000.
                                SEAM_RRF_K: RRF smoothing constant k, Cormack et al. SIGIR 2009 (default: 60)
                                SEAM_VECTOR_STORE: "on" | "off" — master switch for the persisted mmap vector
                                  store (WS2a; default: "on"). When "on":
                                    WRITE: after a successful `seam init --semantic` / `seam sync --semantic`
                                      embed pass, three sibling files are written atomically beside the SQLite DB
                                      in .seam/: vectors.f32 (float32 matrix), vectors.ids.i64 (int64 id sidecar),
                                      vectors.meta.json (model, dim, count, index_version). Write failure is
                                      logged and NEVER fails the embed run (SQL path remains authoritative).
                                    READ: semantic_candidates prefers the mmap store (zero-copy matmul) and
                                      falls through to the SQL brute-force path on any issue (absent artifact,
                                      model mismatch, size mismatch, stale index_version token).
                                  "off" = no artifact written, no artifact read — byte-identical to pre-WS2a
                                    SQL-only path. Honors Seam's E-series opt-out discipline.
                                  No schema change, no migration. Artifact lives in .seam/ (gitignored).
                                SEAM_VEC_ANN: "off" | "on" — master switch for the optional vec0 KNN tier
                                  backed by the sqlite-vec virtual table (WS2b; default: "off").
                                  "off" = byte-identical to pre-WS2b (no probe, no vec0 table, no overhead).
                                  "on" = after `seam init --semantic` / `seam sync --semantic`, index_vec()
                                    builds a cosine-distance vec0 KNN table from the embeddings table.
                                    The S3 read path issues KNN MATCH queries instead of brute-force cosine
                                    scan. Three-tier cascade: vec0 KNN → mmap → SQL.
                                    Requires: [semantic-ann] extra AND SEAM_SEMANTIC=on AND row count ≥ MIN_ROWS.
                                  IMPORTANT — scaffold status: sqlite-vec v0.1.9 performs EXACT brute-force
                                    KNN (no HNSW/IVF ANN index). Measured ~5× SLOWER than the numpy mmap
                                    path at 10k–250k rows with perfect recall@10 = 1.000. Leave "off" until
                                    sqlite-vec ships a true approximate index. The scaffold is there so that
                                    enabling ANN will transparently upgrade without a re-index.
                                  No schema migration — vec_embeddings + vec_meta are created on demand.
                                  Toggling requires re-running `seam init --semantic` / `seam sync --semantic`.
                                SEAM_VEC_ANN_MIN_ROWS: int — minimum embedding rows before index_vec() builds
                                  the vec0 KNN table (WS2b; default: 50000). Forward-compatibility gate only —
                                  NOT a performance crossover threshold. sqlite-vec v0.1.9 is currently slower
                                  than brute-force at ALL scales, so there is no row-count above which the
                                  vec0 tier wins. The default 50000 was set as a DDL-overhead guard and as
                                  the point where a true ANN index would start paying off. Set lower only for
                                  testing/scaffold validation; do NOT lower to chase performance until
                                  sqlite-vec ships approximate indexing. `make bench-semantic-ann` measures
                                  the current exact-KNN vs numpy crossover. Read-path/index-time only.
                                SEAM_EMBED_BODY: "off" | "on" — gate for including a leading body slice + DB
                                  comments in each symbol's embedding input (WS1-A + WS1-B; default: off).
                                  When "on": index_embeddings reads each source file at most once and appends
                                  body text + WHY/HACK/NOTE comment text (from the DB) after the header,
                                  bounded by SEAM_EMBED_INPUT_MAX_CHARS. Vectors change — requires a full
                                  `seam init --semantic` re-index to repopulate. When "off": no disk reads,
                                  no body, no comment join — vectors byte-identical to pre-WS1-A.
                                SEAM_EMBED_INPUT_MAX_CHARS: character budget for embedding input (header +
                                  body + comments) when SEAM_EMBED_BODY=on (WS1-A; default: 2000 ≈ 500
                                  tokens). The header is NEVER truncated; body fills any remaining budget,
                                  then comments. 0 = unlimited (no cap on body/comment content beyond the
                                  header) — mirrors the SEAM_IMPACT_MAX_BYTES 0=unlimited convention.
                                SEAM_NAME_EXPANSION_CAP: max member bare names included when a class/interface/struct
                                  is used as a context/impact/query seed (Tier A name-resolution; default: 50)
                                SEAM_BARE_RESOLVE_CAP: max rows returned by the suffix scan inside
                                  resolve_query_to_defs() for bare-name → qualified-def lookup (Tier A; default: 25)
                                SEAM_TYPE_INFERENCE: "on" | "off" — master switch for extraction-time receiver-type
                                  inference in Python and TypeScript/JS extractors (Tier B; default: on).
                                  When "on", the extractor resolves receiver expressions (class fields, function
                                  params, local variables with type annotations) to qualified 'Type.method' call
                                  targets — e.g. `client: Client` turns obj.send() → `Client.send` as the edge
                                  target. Conservatism contract: only plain user types bind; optionals/generics/
                                  unknown identifiers return None → bare target kept (never emit a wrong edge).
                                  When "off", inference is skipped entirely — byte-identical to pre-Tier-B.
                                  See also SEAM_SWIFT_TYPE_INFERENCE (Swift-specific knob, independent).
                                SEAM_SWIFT_TYPE_INFERENCE: "on" | "off" — Swift-specific receiver-type inference
                                  (Phase 10 / Tier B Swift extension; default: on). Independent of SEAM_TYPE_INFERENCE.
                                SEAM_COMPOSITION_EDGES: "on" | "off" — emit 'holds' edges for typed stored
                                  fields/properties and typed constructor/init parameters (default: on).
                                  Extraction-time only; toggling requires seam init re-index.
                                SEAM_PARAM_EDGES: "on" | "off" — emit 'uses' edges from a function/method to
                                  every plain user type it references as a PARAMETER (e.g. f(x: T) → f uses T;
                                  default: on). Complements 'holds' (stored composition) with signature-level
                                  coupling so a param-injected dependency is a DIRECT (d=1) upstream dependent
                                  of the type. Same conservatism as holds (plain user types only). All 12
                                  languages (Ruby is untyped → naturally none). Higher-volume than holds →
                                  impact/changes/affected verdicts widen. Extraction-time only; "off" =
                                  byte-identical to pre-feature; requires seam init re-index to populate.
                                SEAM_EDGE_SYNTHESIS: "on" | "off" — master switch for the whole-graph
                                  edge-synthesis post-pass (A2 override fan-out + A1 dynamic-dispatch
                                  channels; default: on). "off" = byte-identical pre-synthesis (no
                                  synthesized edges written). Post-pass-time only; toggling requires
                                  seam init / sync to take effect.
                                SEAM_SYNTHESIS_FANOUT_CAP: max synthesized edges per source in a channel
                                  (default: 40). Per-channel semantics differ — see Known Gotchas:
                                  A2 + closure-collection TRUNCATE to N; event-emitter SKIPS the whole
                                  event when handler count > N (likely a generic false-positive event).
                                SEAM_SYNTHESIS_MAX_SOURCE_BYTES: total source-load budget for the
                                  synthesis pass in bytes (default: 50MB; 0 = unlimited).
                                SEAM_FIELD_ACCESS_EDGES: "on" | "off" — emit 'reads' and 'writes'
                                  edges for field/property access (default: on). Extraction-time
                                  only; toggling requires seam init re-index. "off" = byte-identical
                                  to pre-A3 (no field-access edges, no 'field' kind symbols).
                                SEAM_DIAGNOSTICS: "0" | "1" — master switch for opt-in local
                                  diagnostics recording (P5.5; default: "0" = off). "1" enables the
                                  DiagnosticsRecorder, which appends lightweight operational metrics
                                  (RSS, open-FD count, DB size, query count, slow-query summaries,
                                  watcher counters) to a local append-only NDJSON file inside .seam/.
                                  Records ONLY tool names + numeric metrics — never source text,
                                  query arguments, or secret-like values (structural redaction).
                                  "0" = no file, no sampling, no atexit handler, byte-identical
                                  read path. Local-file-only — no network, no telemetry. Read-path +
                                  watcher instrumentation; no schema change, no new dependency.
                                SEAM_DIAGNOSTICS_PATH: NDJSON diagnostics file path (P5.5; default:
                                  ".seam/diagnostics.ndjson" — inside .seam/, already gitignored).
                                  Configurable to redirect to a scratch location.
                                SEAM_DIAGNOSTICS_SLOW_MS: int — slow-query threshold in ms (P5.5;
                                  default: 100). A query with duration_ms >= this appends a slow_query
                                  line; below it the query counter is still incremented (surfaced in
                                  the atexit snapshot) but no line is written (zero IO).
                                SEAM_INDEX_ARTIFACT_URL: str — HTTPS (or file://) URL template for
                                  downloading a pre-built index artifact (WS4 S2/S3; default: "" =
                                  feature inert). Must contain a `{sha}` placeholder which `seam fetch`
                                  replaces with the resolved git commit SHA. When empty, `seam fetch`
                                  immediately exits with INVALID_INPUT — no network access, no change
                                  to the local index. Example:
                                    "https://ci.example.com/artifacts/seam/{sha}/seam-index.tar.gz"
                                  WHY a template: the SHA changes per commit but the URL structure is
                                  stable; CI publishes once and consumers reconstruct the exact URL from
                                  their checked-out commit. `file://` is also accepted (zero network —
                                  used by offline tests and local hand-off workflows).
                                SEAM_FETCH_ANCESTOR_DEPTH: int — maximum number of first-parent
                                  ancestors `seam fetch` walks when the HEAD artifact is absent (WS4 S3;
                                  default: 50). `seam fetch` tries HEAD first, then walks first-parent
                                  history (newest-first) up to this bound, stopping at the first SHA
                                  that has a published artifact. WHY first-parent: avoids walking into
                                  merged feature branches (where CI may or may not have published).
                                  Set to 1 to disable fallback (HEAD only). 0 = same as 1.
seam/analysis/diagnostics.py ← LEAF: opt-in local diagnostics recorder (P5.5)
                                DiagnosticsRecorder — null recorder (all no-op) when SEAM_DIAGNOSTICS
                                  != "1"; else appends slow_query + resource-snapshot NDJSON lines.
                                  Structural redaction: record_query(tool, duration_ms, result_chars)
                                  takes only a name + numbers — arg text/result bodies can't be logged.
                                  Never raises (leaf discipline). Stdlib best-effort sampling (no dep):
                                  RSS via resource.getrusage (guarded import; null on Windows), FDs via
                                  /proc/self/fd (Linux only), DB size via os.path.getsize.
                                get_recorder() — one-per-process singleton (MCP + CLI + watcher share it)
                                run_query(tool, thunk) — time + record a read-query, result unchanged
                                result_chars(result) — serialized-length SIZE PROXY (measured, discarded)
                                set_db_path / close / reset_recorder — resolved-db-path + test hygiene
seam/indexer/field_access.py ← LEAF: Python field-access extractor + facade re-exports (A3)
                                extract_field_access_edges(node, language, path, symbols) →
                                  list[Edge] for Python; dispatches to family modules for other langs.
                                Distinguishes reads vs. writes via LHS-of-assignment /
                                augmented-assign / del detection. Conservatism contract:
                                self/this/cls → enclosing class; typed receiver via
                                resolve_receiver_type; unresolvable → bare field name; never raises.
seam/indexer/field_access_ts.py ← LEAF: TypeScript/JS field-access extractor (A3)
seam/indexer/field_access_go_rust.py ← LEAF: Go + Rust field-access extractor (A3)
seam/indexer/field_access_ext.py ← LEAF: Java + C# field-access extractor (A3)
seam/indexer/field_access_c_cpp.py ← LEAF: C + C++ field-access extractor (A3)
seam/indexer/field_access_ext2.py ← LEAF: Ruby + PHP field-access extractor (A3)
seam/indexer/field_access_php_swift.py ← LEAF: PHP emission helpers + Swift field-access extractor (A3)
seam/query/context.py        ← A3 read-path addition: field_readers and field_writers lists
                                added to the context() result — symbols with 'reads'/'writes'
                                edges to/from this symbol. Separate from callers/callees (which
                                include all edge kinds via the kind-agnostic BFS).
seam/analysis/embeddings.py  ← LEAF: fastembed wrapper for semantic search (Semantic phase)
                                is_available() → bool (lazy, cached; never raises)
                                symbol_text(name, signature, docstring) → str (canonical embed input)
                                embed_texts(texts, model) → list[bytes] (float32 blobs; [] on failure)
                                embed_query(text, model) → bytes (b'' on failure); both degrade gracefully
                                fastembed + numpy are LAZY imports (only inside function bodies, never at module scope)
seam/indexer/embedding_index.py ← index orchestration bridge for embeddings (Semantic phase, mirrors cluster_index)
                                index_embeddings(conn, *, model, batch, only_symbol_ids=None) → int:
                                  -1=error, 0=skipped/empty-scope, ≥1=count. single-transaction batch upsert
                                  (INSERT OR REPLACE) for clean-retry on failure. When only_symbol_ids is None
                                  (default), embeds ALL symbols (byte-identical to pre-WS3 init --semantic).
                                  When a non-empty set, embeds only those IDs via TEMP TABLE JOIN (avoids
                                  SQLite variable-number limit). Scoped path suppresses artifact write.
                                  Called by `seam init --semantic` after clustering (full embed, None).
                                  WS2a: full-embed path calls _write_artifact() on success; gated by SEAM_VECTOR_STORE.
                                  WS3: scoped path (only_symbol_ids set) suppresses _write_artifact — orchestrator owns rebuild.
                                symbols_needing_embeddings(conn, model) → set[int]: LEFT JOIN to find un-embedded ids.
                                delete_orphan_embeddings(conn) → int: delete embedding rows whose symbol is gone.
                                sync_embeddings(conn, *, model, batch) → int: incremental embed orchestrator
                                  for `seam sync --semantic`. Orchestrates: orphan sweep → missing-set →
                                  scoped embed → artifact rebuild (staleness-token–aware). Never raises;
                                  returns 0 = nothing new or fastembed absent, -1 = failed, ≥1 = n_added.
seam/query/vector_store.py   ← LEAF: persisted mmap vector store (WS2a)
                                get_artifact_dir(conn) → Path | None (derives .seam/ dir from PRAGMA database_list)
                                write_store(store_dir, symbol_ids, matrix_or_blobs, model, dim, index_version) → None
                                  atomic write: temp file + os.replace per file; meta written LAST so load_store
                                  never sees a meta without a valid matrix; try/finally cleans up temp files
                                load_store(store_dir, model) → VectorStore | None
                                  validates model, dtype/byteorder, file sizes; mmap-loads matrix zero-copy
                                top_k(store, query_vec_bytes, k, *, scan_cap=0) → list[tuple[int, float]]
                                  same cosine formula as SQL fallback for byte-identical results; scan_cap
                                  mirrors SEAM_SEMANTIC_SCAN_CAP (0 = all rows)
                                compute_index_version(conn, model) → str "count:max_symbol_id" staleness token
                                numpy imported at module scope (gated by try/except ImportError → degrade to None/[])
seam/query/vec_extension.py  ← LEAF: sqlite-vec extension capability probe + loader (WS2b S1)
                                probe_vec_extension(conn) → bool: full CREATE/DROP round-trip probe on a
                                  disposable :memory: connection (side-effect-free on caller's DB). Covers:
                                  enable_load_extension disabled (macOS system SQLite), package absent,
                                  load failure, vec0 DDL failure. Never raises; logs exactly ONE WARNING on
                                  failure naming the reason. Returns True only when all steps succeed.
                                load_vec_extension(conn) → bool: loads the extension onto `conn` for reuse
                                  (enables → loads → disables in one guarded sequence; loaded extension
                                  stays active for the connection lifetime). Returns False + WARNING on error.
                                Both import sqlite_vec LAZILY so this module is importable without [semantic-ann].
seam/indexer/vec_index.py    ← ANN index-builder bridge (WS2b S2; mirrors cluster_index/synthesis_index)
                                index_vec(conn, *, model) → int: Triple-gated entry point (returns 0=skipped
                                  when any gate fails, -1=error, ≥1=rows indexed). Gates: (1) SEAM_VEC_ANN=on,
                                  (2) probe_vec_extension passes, (3) embedding row count ≥ SEAM_VEC_ANN_MIN_ROWS.
                                  Delegates to _build_vec_index on success; catches all exceptions → -1.
                                _build_vec_index(conn, *, model) → int: inner implementation (may raise).
                                  Steps: infer dim from embeddings table → load extension onto conn (OUTSIDE
                                  any transaction — sqlite-vec restriction) → fetch all (symbol_id, vector)
                                  rows → in ONE transaction: CREATE vec_meta table, DROP+CREATE vec_embeddings
                                  (vec0, cosine distance), bulk-insert rows, upsert staleness token into vec_meta.
                                VEC_TABLE = "vec_embeddings" (vec0 virtual table; rowid = symbol_id)
                                VEC_META_TABLE = "vec_meta" (ordinary table; stores staleness token + dim)
                                Called by `seam init --semantic` (after index_embeddings) + `seam sync --semantic`.
seam/query/semantic.py       ← LEAF: semantic search read path (Semantic phase)
                                rrf_merge(fts_ranked, semantic_ranked, k=60) → list[int] (pure RRF, no model)
                                cosine_sim(a_bytes, b_bytes) → float (pure-Python struct.unpack; no numpy dep)
                                semantic_candidates(conn, query, *, model, limit) → list[tuple[int, float]]
                                  model-mismatch guard → [] (never silently mixes embedding spaces)
                                  WS2b S3: three-tier cascade: ANN (_try_vec_path) → mmap (_try_mmap_path) → SQL.
                                    ANN tier: per-process probe cache + vec_meta staleness check + KNN MATCH query.
                                    None = tier unavailable/stale → fall through; [] = searched, nothing matched.
                                  WS2a: prefers mmap path (_try_mmap_path) when SEAM_VECTOR_STORE=on;
                                    falls through to SQL on None (absent/stale/corrupt artifact)
                                  SQL brute-force fallback: SEAM_SEMANTIC_SCAN_CAP=0 (default) = no LIMIT
                                  numpy fast path inside _semantic_candidates_impl (matmul, ~1–5ms/10k)
                                  pure-Python cosine_sim fallback when numpy absent (defensive)
seam/analysis/processes.py   ← LEAF: execution flows (Flows) — list_entry_points (call-graph roots
                                ranked by downstream reach, tests excluded) + build_flow (forward
                                call-chain tree, depth/breadth-capped, cycle-safe). Reuses confidence
                                + testpaths; name-count confidence (no import promotion). Never raises.
seam/installer/              ← `seam install`/`uninstall` engine (CLI-only; NO MCP tool)
                                CLI-FIRST: bare install writes token-lean CLI guidance (project-scoped);
                                  `--with-mcp` additionally writes the MCP config (respects --location)
                                __init__.py: TARGETS registry {claude,cursor,codex,vscode,gemini,zed} + resolve_seam_command()
                                core.py: AgentTarget ABC (+install_guidance/uninstall_guidance/guidance_previews)
                                  + InstallResult + shared idempotent JSON merge
                                jsonfile.py (LEAF, stdlib json) — Claude/Cursor; tomlfile.py (LEAF, tomlkit) — Codex
                                guide.py (LEAF) — ONE guide template + 4 renderers (skill / .mdc / Codex AGENTS
                                  block / thin CLAUDE.md hook); single source so the formats never drift
                                markdownfile.py (LEAF, stdlib) — owned-file write + marker-delimited block
                                  upsert/remove (`<!-- seam:start/end -->`) for AGENTS.md/CLAUDE.md; atomic
                                claude.py: skill + CLAUDE.md hook (+ .mcp.json type:stdio when --with-mcp)
                                cursor.py: .cursor/rules/seam.mdc agent-requested rule (+ .cursor/mcp.json w/ --with-mcp)
                                codex.py: AGENTS.md guidance block (+ ~/.codex/config.toml when --with-mcp)
                                vscode.py: .github/copilot-instructions.md guidance block (+ .vscode/mcp.json
                                  w/ --with-mcp; project-only — user-profile path is OS-specific)
                                gemini.py: GEMINI.md guidance block (+ .gemini/settings.json w/ --with-mcp;
                                  supports project + user scope)
                                zed.py: AGENTS.md guidance block shared with codex (+ .zed/settings.json
                                  or ~/.config/zed/settings.json w/ --with-mcp; supports project + user scope)
seam/cli/install.py          ← `seam install`/`uninstall` Typer commands (registered onto app in main.py)
seam/cli/read.py             ← `seam query`/`search`/`context` — CLI-only read commands over the
                                transport-agnostic tools.py handlers (query SQLite directly; NO MCP)
seam/cli/serve.py            ← `seam serve` — lazy-import FastAPI/uvicorn ([web] extra) + run the
                                Seam Explorer web server on 127.0.0.1:7420; NO_INDEX guard; opens browser
seam/cli/main.py             ← Typer CLI (init, sync, start, status, impact, trace, changes, why, clusters,
                                affected, pack, pack-index, rebase, fetch, install, uninstall, query, search, context, serve)
                                NOTE: `from seam.server.mcp import create_server` is LAZY (inside start())
                                — `mcp` is an optional extra; only `seam start` needs it
seam/indexer/db.py (schema)  ← schema loaded packaged-first: seam/_data/schema.sql (force-included in wheel)
                                with fallback to docs/database/schema.sql (dev). Fixes installed `seam init`.
                                --json / --quiet on read commands; --stdin on affected + changes
                                sync: --json / --quiet / --force-clusters (Phase 7)
                                --lean on impact/trace/pack + --limit on impact (Phase 8); all 3 modes route through handlers
                                --max-bytes N on impact (E1-FULL): character budget for the response body;
                                  0 = unlimited (default SEAM_IMPACT_MAX_BYTES); symmetric with MCP max_bytes.
seam/indexer/sync.py         ← LEAF: Phase 7 reconcile engine — sync(conn, root, *, recompute_clusters,
                                force_clusters, naming_mode, llm_api_key, llm_model, min_size) → SyncResult
                                mtime pre-filter → SHA-1 confirm; existsSync-guarded delete; FULL cluster
                                recompute gated on graph_changed (added+modified+removed>0) or force_clusters
                                reuses walk_project + index_one_file + sha1 + delete_file + index_clusters
                                SyncResult: added, modified, removed, unchanged, skipped, graph_changed,
                                clusters_recomputed, cluster_count (None=skipped, -1=recompute failed, ≥0=ok)
seam/cli/output.py           ← LEAF: agent-output contract — success/error JSON envelope, quiet renderer
                                {"ok":true,"data":...} / {"ok":false,"error":{"code","message"}}
                                error codes: NO_INDEX INVALID_INPUT INVALID_QUERY NOT_A_GIT_REPO DB_ERROR
seam/query/fts.py            ← LEAF: FTS5 query construction + multi-signal rescoring (Phase 3)
                                build_match_query(text) → OR-joined prefix MATCH expression
                                rescore(rows, terms) → reranked rows (name/path/test/cluster signals)
                                extract_terms(text) → plain token list (single source of tokenisation)
seam/analysis/affected.py    ← affected(conn, changed_files, *, depth, repo_root) → AffectedResult
                                changed files → owning symbols → upstream impact → impacted test files
                                reuses analysis.impact + analysis.testpaths.is_test_file
seam/analysis/relevance.py   ← LEAF: seam_impact output ranking + self-ref classification (E2/E3)
                                owning_container(name) → container | None (everything before last dot)
                                classify_self_ref(name, container, self_names) → bool (entry in target's class)
                                relevance_key/order_by_relevance → externals first, self-refs last (stable)
                                partition_self_refs → (external, self_refs) for "hide" mode
                                Pure (no DB), never raises; consumed by handle_seam_impact in server/tools.py.
                                Conservatism: uncertain → treat as EXTERNAL (never hide a real dependent).
seam/analysis/steer.py       ← LEAF: E4 truncation-steer generator (stdlib-only: logging, typing)
                                generate_steer(truncated, byte_capped, risk_summary, limit, max_bytes,
                                  tier_order, direction_order) → list[str]. Pure, DB-free, never raises
                                  (degrades to [] on any error). Mirrors byte_budget.py / relevance.py /
                                  rwr.py leaf discipline: trim metadata in → ready-to-act prose hints out.
                                  Called by _attach_steer in server/tools.py after the byte ceiling runs.
                                  Injects canonical TIER_* names from impact.py via tier_order param
                                  (single source of truth — the leaf stays standalone-testable without
                                  importing impact.py). All-trimmed case emits the anti-false-safe warning.
seam/analysis/byte_budget.py ← LEAF: E1-FULL byte ceiling for seam_impact output (stdlib-only: json, typing)
                                fit_to_byte_budget(response, *, budget, ...) → (trimmed, byte_dropped, omitted)
                                  trims entries in keep-priority order (upstream-before-downstream,
                                  WILL_BREAK-before-MAY_NEED_TESTING) via O(n) running-total prefix walk.
                                  Conservatism: running total OVER-estimates (comma over-charge) so the
                                  result is a HARD upper bound — running <= budget guarantees fit.
                                serialized_size(obj) → int: single source of truth for the CLI emit
                                  measurement (json.dumps with ensure_ascii=False; handler + tests share this
                                  so budget arithmetic cannot drift between layers).
                                Pure (no DB, no config, no IO); never raises; never mutates input.
seam/indexer/parser.py       ← tree-sitter parsing (Python, TypeScript, JavaScript, Go, Rust,
                                Java, C#, Ruby, C, C++, PHP)
seam/indexer/graph_common.py ← LEAF: shared TypedDicts (Symbol/Edge/Comment), helpers
                                Symbol now carries: signature, decorators, is_exported, visibility, qualified_name
                                  symbols.kind gains 'field' (A3) — qualified_name='Type.field'; additive TEXT value
                                Edge now carries: receiver (raw receiver text; None for bare/import/pre-v10 edges)
                                Edge kind vocabulary: 'call' | 'import' | 'extends' | 'implements' | 'instantiates' | 'holds' | 'reads' | 'writes' | 'uses'
                                  — 'uses' added by the method-param feature (function/method → plain user type referenced as a parameter)
                                  — 'instantiates' added by Tier B B6 (new/struct-literal/composite-literal nodes)
                                  — 'holds' added by composition feature (typed stored field/property + constructor param)
                                  — 'reads' | 'writes' added by A3 (field/property access; mode from LHS/augmented-assign/del detection)
seam/indexer/graph_go_rust.py← Go + Rust extractors (imports graph_common only)
seam/indexer/graph_java_csharp.py ← Java + C# symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; split from graph.py to stay under 1000 lines
seam/indexer/graph_c_cpp.py  ← C + C++ symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; _dedup_cpp_symbols handles in-class/out-of-line duplicates
seam/indexer/graph_ruby.py   ← Ruby symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; handles def self.x singleton methods
seam/indexer/graph_php.py    ← PHP symbol/edge/comment extractors (Phase 9)
                                imports graph_common only; handles grouped-use and enum methods
seam/indexer/graph_swift.py  ← Swift symbol/edge/comment extractors (Phase 10)
                                imports graph_common only; class/struct/actor/extension→class,
                                enum→type, protocol→interface; /// and /** */ docstrings
seam/indexer/graph_swift_infer.py ← LEAF: Swift receiver-type inference (Phase 10 / Tier B extension)
                                Two-layer scope model: class-level property pre-scan + per-function
                                param/local bindings. Controlled by SEAM_SWIFT_TYPE_INFERENCE config knob.
                                Conservatism contract: only plain user types bind; None on optionals/generics/
                                chained/unknown. _resolve_navigation_target is the core lookup function.
seam/indexer/graph_scope_infer.py ← LEAF: Python + TypeScript/JS receiver-type inference (Tier B B4)
                                Mirrors graph_swift_infer two-layer scope model. Used by graph.py extractors.
                                resolve_receiver_type(receiver_text, class_name, var_types, self_names) → str|None
                                self/cls/this normalize to enclosing class; optionals/containers/generics → None.
                                Controlled by SEAM_TYPE_INFERENCE config knob.
seam/indexer/graph_scope_infer_ext.py ← LEAF: Java + C# + Ruby receiver-type inference (Tier B B5)
                                Extends the two-layer scope model to Java/C#/Ruby families.
seam/indexer/graph_scope_infer_ext2.py ← LEAF: Go + Rust + C/C++ + PHP receiver-type inference (Tier B B5)
                                Extends the two-layer scope model to Go/Rust/C/C++/PHP families.
seam/indexer/graph_typescript.py ← TypeScript/JS extractors (split from graph.py for Tier B B3)
                                Tier B B3: member_expression call_expression nodes now emit call edges
                                (previously only bare identifier calls were indexed — this fixes the
                                major TS/JS recall hole where obj.method() calls were silently dropped).
                                Tier B B6: new_expression nodes emit 'instantiates' edges.
seam/indexer/graph.py        ← Python/TS dispatchers; re-exports types from graph_common;
                                imports Go/Rust/Java/C#/C/C++/Ruby/PHP/Swift extractors at top level
seam/indexer/signatures.py   ← LEAF: Phase 4 enrichment — extract_node_fields(node, language, ...) → NodeFields
                                per-language: signature, decorators, is_exported, visibility, qualified_name
                                for Python / TypeScript / JavaScript / Go / Rust; never raises
seam/indexer/signatures_ext.py ← LEAF: Phase 9 enrichment for Java/C#/Ruby/C/C++/PHP (Phase 9)
                                NodeFields re-declared (not imported) to avoid circular import; drift-tested
seam/analysis/imports.py     ← LEAF: extract_import_mappings + resolve_import_source + compute_path_proximity
                                per-language import extraction for Python/TS/JS/Go/Rust; never raises
                                maps import source strings to candidate declaring-file paths (5-lang extension order)
seam/analysis/imports_ext.py ← LEAF: Phase 9 import-mapping extraction for Java/C#/Ruby/C/C++/PHP (Phase 9)
                                _ImportMapping re-declared (not imported) to avoid circular import; drift-tested
                                resolution returns [] for Java/C#/PHP package paths (classpath out of scope)
seam/analysis/builtins.py    ← LEAF: is_builtin(name, language) → bool over static per-language frozensets
                                covers Python/TS/JS/Go/Rust/Java/C#/Ruby/C/C++/PHP; conservative vocabulary
seam/analysis/confidence.py  ← whole-index confidence resolver (Phase 5 extended)
                                resolve_edge() → Resolution{confidence, resolved_by, best_candidate}
                                load_import_mappings(conn, file_path) → list[ImportMapping]
                                resolve() kept as backward-compat thin shim
seam/indexer/pipeline.py     ← shared parse→extract→upsert path (CLI + watcher)
seam/indexer/cluster_index.py← clustering orchestration bridge (Phase 2)
                                index_clusters(conn, ...) → int; called by seam init only
seam/indexer/db.py           ← SQLite write (init_db, upsert_file, delete_file)
seam/analysis/clustering.py  ← LEAF: pure-Python Louvain community detection (Phase 2)
                                detect_communities(nodes, edges) → {name: cluster_id}
seam/analysis/rwr.py         ← LEAF: pure personalized-PageRank / RWR (E3 neighbor ranking)
                                personalized_pagerank(adjacency, seeds, *, restart, iters, tol) → {name: score}
                                graph + seed SET in → relevance scores out; no DB/IO/config; never raises.
                                seeds is a SET (symbol's edge_match_names: qualified + bare) so neighbours
                                reachable via either storage form score against the same logical seed.
                                Degenerate → {} (empty/no-seed-present) or seed-only mass (isolated seed).
seam/analysis/cluster_naming.py ← LEAF: deterministic + opt-in LLM cluster labeling (Phase 2)
seam/analysis/synthesis.py   ← LEAF: edge-synthesis engine — A2 interface→implementation override
                                fan-out (link every base method to every same-name impl as a
                                synthesized 'call' edge; deliberate OVER-APPROXIMATION, not MRO).
                                Pure; never raises; bounded by SEAM_SYNTHESIS_FANOUT_CAP.
seam/analysis/synthesis_channels.py ← LEAF: A1 dynamic-dispatch channels —
                                A1a closure-collection (collection iterated AND element invoked,
                                  paired by field name to append sites) +
                                A1b event-emitter (registrar verbs ↔ dispatcher verbs keyed by
                                  event-string literal). Pairs field-names/event-keys GLOBALLY
                                  (cross-file); INFERRED, bounded by fanout cap.
seam/indexer/synthesis_index.py ← edge-synthesis orchestration bridge (mirrors cluster_index.py)
                                index_synthesis(conn, ...) → int: -1=error, ≥0=count of synthesized
                                edges. Reads symbols+edges, runs engine, writes synthesized edges in
                                ONE transaction under a synthetic ':synthesis:' files row. Idempotent
                                (delete-then-insert). Called by seam init/sync only; NOT the watcher.
seam/query/engine.py         ← query(), context(), search() — read path
                                context() enriched with cluster_id/label/peers (Phase 2)
                                all three return signature/decorators/is_exported/visibility/qualified_name (Phase 4)
seam/query/names.py          ← LEAF: Tier A name-resolution helpers — bare_name, is_container_symbol,
                                get_member_names, edge_match_names, resolve_query_to_defs,
                                expand_impact_seeds. Imports only stdlib + seam/config (leaf, like clusters.py).
                                Bridges the qualified-symbol / bare-edge asymmetry: symbols stored as
                                "Class.method", edges stored as bare "method". Pure read-time; no schema change.
seam/query/clusters.py       ← cluster read queries (Phase 2): list_clusters, cluster_members,
                                cluster_peers; guards pre-v4 indexes
seam/query/pack.py           ← LEAF: context_pack(conn, symbol_name) → ContextPack | None
                                orchestrates context()+why() into one enriched bundle; applies caps from config
                                ContextPack: target, callers, callees (NeighborRef), why, cluster_peers, truncated
                                E3: ranks neighbors by RWR relevance to the seed BEFORE caps (most-relevant N
                                  survive, not lowest-id). _neighbor_scores (one bounded subgraph + one PPR per
                                  pack, reused for callers+callees) → _enrich_neighbors sorts by
                                  (-ppr_score, is_test, min_id). Gated by SEAM_PACK_RELEVANCE_RANK.
seam/server/tools.py         ← thin FACADE: re-exports every handler unchanged so seam/server/mcp.py
                                and all imports stay byte-identical. After P2 split (Slice 2, #103):
                                  impact_handler.py handles all seam_impact logic (615 lines);
                                  trace_handler.py handles seam_trace (184 lines);
                                  handler_common.py holds shared helpers (323 lines).
                                All files < 1000 lines; tool count stays 12.
seam/server/handler_common.py ← shared helpers for all MCP handlers (P2 Slice 2 / #103)
                                _HEAVY_FIELDS frozenset + _apply_verbosity (lean-output contract)
                                limit constants (_QUERY_*, _SEARCH_*, _IMPACT_*, _TRACE_*)
                                _serialize_hop / _serialize_edge_hop (trace handler + tests share these)
                                _trace_not_found / _qualified_trace_candidates
                                _relativize / _clamp
                                compute_uid / _resolve_uid (P6c stable handle)
                                _invalid_input / _invalid_query
                                _maybe_attach_staleness (P2 staleness banner, last step in 5 handlers)
seam/server/impact_handler.py ← all seam_impact shaping logic extracted from tools.py (P2 Slice 2)
                                handle_seam_impact + all E-series helpers (_shape_tier_group,
                                _apply_byte_ceiling, _attach_steer, _serialize_tier_entry, etc.)
                                Re-exported from tools.py facade; no behavior change from the split.
seam/server/trace_handler.py  ← handle_seam_trace extracted from tools.py (P2 Slice 2)
                                Re-exported from tools.py facade; no behavior change from the split.
seam/analysis/staleness.py   ← LEAF: P2 index staleness detector — single source of truth for
                                "is this index stale?" for both the MCP banner and `seam status`.
                                check_staleness(conn, *, root, watcher_alive, scan_cap, respect_knob)
                                  → StalenessVerdict {stale, reason, hint}. Never raises; on any
                                  IO/DB error returns stale=False (conservative: do NOT cry wolf).
                                Algorithm: bounded-scan over newest SEAM_STALENESS_SCAN_CAP files
                                  by indexed_at DESC → per-file mtime vs stored mtime comparison.
                                Watcher-aware: watcher_alive=True skips file-drift check (watcher
                                  self-heals) BUT still reports stale when synthesized edges exist
                                  (watcher never recomputes synth edges or clusters).
                                Per-process TTL cache: caches ONLY stale verdicts (safe asymmetry —
                                  fresh is never cached so edits within the TTL window are detected).
                                respect_knob=False used by `seam status` CLI so the CLI freshness
                                  field is independent of SEAM_STALENESS_CHECK.
seam/server/graph_api.py     ← LEAF: build_neighborhood(conn, name, direction) → dict (Phase B1)
                                depth-1 neighbors from edges table; homonym-collapse (name-keyed nodes);
                                node enrichment: kind, signature, visibility, is_exported, cluster, definition_count
                                build_constellation(conn) → {clusters, links} (Explorer Phase 2): cluster
                                  list + weighted inter-cluster links; homonym-safe name→cluster map; never raises
seam/server/web.py           ← FastAPI app factory: create_web_app(db_path, root) → FastAPI (Phase B2)
                                v1: /api/status · /api/search · /api/graph/neighborhood · /api/symbol/{name} · /api/clusters
                                Explorer Phase 2 (all reuse handle_seam_* verbatim — zero query dup):
                                  /api/impact (handle_seam_impact, verbose=False) · /api/trace (handle_seam_trace,
                                  paths only) · /api/changes (handle_seam_changes; NOT_A_GIT_REPO→400) ·
                                  /api/constellation (graph_api.build_constellation)
                                Pydantic models = TS codegen source; static SPA at seam/_web/ (build hint if absent)
                                127.0.0.1-only enforced by CLI; requires [web] extra (lazy import pattern)
seam/watcher/daemon.py       ← watchdog daemon (debounced re-index)
tests/fixtures/              ← sample.py, sample.ts, sample.go, sample.rs
tests/eval/                  ← P1 recall harness (edge-synthesis phase): fixture repo +
                                SHA-stamped golden.json + recall@K/MRR metric (recall_harness.py,
                                eval_report.py, gen_golden.py). test_recall_regression.py is
                                gate-wired. `make eval` runs it; `make eval-generate` regenerates
                                golden.json.
```

## Coding Conventions
- Max 200 lines per function | Max 1000 lines per file
- All imports at top of file
- Config from `seam/config.py` only — never `os.getenv()` in other modules
- Tests in `tests/` mirroring package structure
- snake_case files + functions | PascalCase classes | UPPER_SNAKE constants
- Type hints required; use `X | None` not `Optional[X]`

## Non-Negotiables
- **Gate must pass before every commit** — no exceptions, no `--no-verify`
- **Zero external services at runtime** — no API keys, no network calls
- **SQLite only** — no Neo4j, no graph DB, no ORM
- **Config from seam/config.py** — never hardcode paths or env var names
- **Parsers never raise** — return None on error; let the indexer skip gracefully
- **Edges use string names** (not symbol IDs) — required for independent re-indexing

## Current Phase
WS2b — sqlite-vec KNN scaffold for semantic search (issues #267/#268/#269/#270). Read-path + indexing-time; no schema change, no new MCP tool (count stays 16). Default behavior with `SEAM_VEC_ANN=off` (the default) is byte-identical to pre-WS2b for all queries and for `seam init --semantic`.
- **What this is (honest framing after benchmark):** WS2b wires in a `sqlite-vec` vec0 KNN table as a forward-compatible scaffold in the three-tier cascade (vec0 KNN → mmap → SQL). sqlite-vec v0.1.9 performs **exact brute-force KNN** — it has no approximate-nearest-neighbour index (no HNSW, no IVF). A real-scale benchmark (synthetic 384-dim, 10k–250k rows) showed it is **~5× slower than the numpy mmap path** at every scale tested, with perfect recall@10 = 1.000. The feature is shipped off-by-default (`SEAM_VEC_ANN=off`) and is **not a performance win today** — it is a forward-compatible scaffold that will transparently upgrade to true ANN (without re-index) when sqlite-vec ships approximate indexing. Do NOT recommend `SEAM_VEC_ANN=on` for latency until then.
- **Why the scaffold was still worth shipping:** The vec0 tier takes 0 ms when off (byte-identical path). The staleness token, three-tier cascade, and probe/load machinery are already correct and tested. When sqlite-vec adds HNSW/IVF, enabling it will require only `SEAM_VEC_ANN=on seam init --semantic` — no code change, no schema migration, no re-index of the embeddings table.
- **New leaf `seam/query/vec_extension.py`** (stdlib-only: logging, sqlite3): `probe_vec_extension(conn) → bool` — full CREATE/DROP round-trip to confirm the extension is present and functional (no side effects on the real DB); `load_vec_extension(conn) → bool` — loads `sqlite_vec` onto a given connection for reuse. Never raises; returns False on any failure so callers degrade transparently. `_VEC_PROBE_SQL` constant centralises the probe DDL. Both functions are idempotent (double-call safe). Gate: `SEAM_VEC_ANN=on` required; checked by callers, not here.
- **New bridge `seam/indexer/vec_index.py`** (mirrors cluster_index/synthesis_index pattern): `index_vec(conn, *, model) → int` — triple-gated public entry point (0=skipped when any gate fails, -1=error, ≥1=rows indexed). Gates: (1) `SEAM_VEC_ANN=on`, (2) `probe_vec_extension(conn)` passes, (3) embedding row count ≥ `SEAM_VEC_ANN_MIN_ROWS`. Delegates to `_build_vec_index(conn, *, model)` on success. `_build_vec_index`: CREATE `vec_embeddings` vec0 table (idempotent `CREATE IF NOT EXISTS`), batch-INSERT all rows from `embeddings WHERE model=?` into the vec0 table, write staleness token `"count:max_id"` into the `vec_meta` ordinary table. Idempotent: drops + recreates on every call so incremental sync is handled by full rebuild. Called by `seam init --semantic` / `seam sync --semantic` after `index_embeddings`. Never raises from `index_vec`; `-1` on error (CLI surfaces warning, exit still 0).
- **Three-tier cascade in `seam/query/semantic.py` (WS2b S3):** ANN tier `_try_vec_path(conn, query_vec_bytes, model, k)` → mmap tier `_try_mmap_path` → SQL brute-force. `None` = tier unavailable/stale (fall through); `[]` = searched and nothing matched (valid result, propagate). ANN staleness: `_try_vec_path` reads the stored token from `vec_meta` and compares to `compute_index_version(conn, model)` — mismatch → `None` (fall through to mmap). The three-tier function `semantic_candidates` is the single read-path entry point; it is transparent to all callers including MCP tools (which invoke it via `engine.py`).
- **S4 — benchmark harness `benchmarks/semantic_ann_scale.py` + gate-wired smoke test `tests/unit/test_semantic_ann_bench_smoke.py`:** `run_scale(n_rows, *, dim, queries, k, seed) → ScaleResult` generates normalised synthetic float32 embeddings, builds a temp-file SQLite DB, runs brute-force (numpy matmul) and ANN (vec0 KNN MATCH) paths, computes mean latency + recall@K, returns a dataclass. Self-skips when `sqlite_vec` is not importable. `ScaleResult` fields: `n_rows, dim, queries, k, brute_ms, ann_ms, speedup, recall_at_k, ann_available`. `make bench-semantic-ann` is NOT part of `gate`. The smoke test IS part of `gate` (tiny synthetic scale, guarded with `pytest.importorskip("sqlite_vec")`).
- **2 new config knobs:** `SEAM_VEC_ANN` (`"off"`/`"on"`, default `"off"`; `"off"` = byte-identical pre-WS2b — no probe, no vec0 table, no overhead) and `SEAM_VEC_ANN_MIN_ROWS` (int, default 50000; ANN only activated above this threshold — brute-force wins below it). Both are read-path + indexing-time; no schema migration. MCP tool count stays 16.
- **`[semantic-ann]` extra:** `pip install 'seam-code[semantic-ann]'` adds `sqlite-vec` (C extension). `[semantic]` (fastembed) is a SEPARATE extra — ANN acceleration and embedding generation are independent. The `[semantic-ann]` extra does NOT pull fastembed; conversely, `[semantic]` does not pull sqlite-vec. Operators install both for the full hybrid-ANN path; installing only `[semantic]` keeps the mmap/SQL fallback.
- **no-egress proof unchanged:** sqlite-vec is an IN-PROCESS SQLite extension (shared library loaded via `enable_load_extension`); it makes zero network calls at any point. ANN read path = load extension → SQL KNN MATCH → rows from local `.seam/seam.db`. The no-egress CI strace proof requires NO new exclusion for WS2b. See `.github/workflows/no-egress.yml` header comment for the full rationale.
- **Tests:** `tests/unit/test_vec_extension.py`, `tests/unit/test_vec_index.py`, `tests/integration/test_vec_search.py` (S1–S3 gate tests). Gate: ruff + mypy clean, full suite passes, N skipped (pre-existing fastembed real-model tests + sqlite_vec-gated tests when `[semantic-ann]` absent).
See `progress.txt`.

### Prior phase
WS4 — Shared prebuilt index via CI artifact (`seam fetch`). Three new CLI commands (`seam rebase`, `seam pack-index`, `seam fetch`); two new config knobs; two new pure leaves + one orchestration module. No schema change, no migration, no new MCP tool (count stays 16). Default behavior with `SEAM_INDEX_ARTIFACT_URL` unset and none of the new commands run is byte-identical to pre-WS4.
- **Why (the team-onboarding gap this closes):** every developer joining a project — or every CI job on a fresh clone — had to run `seam init` (possibly `seam init --semantic`) to build the index from scratch. On a large codebase this takes minutes and requires the full tree-sitter parse pass plus, optionally, a model-download + embedding run. WS4 lets the CI pipeline publish a prebuilt index archive once per commit and share it via a URL; teammates and CI consumers run `seam fetch` to download, verify, unpack, rebase, and sync in seconds instead of minutes.
- **New leaf `seam/indexer/rebase.py`** (stdlib-only: logging, os, sqlite3): `rebase_index(conn, *, new_root, old_root=None) → int`. Investigation confirmed `files.path` is the ONLY column in the schema that stores absolute filesystem paths — symbols, edges, comments, clusters, embeddings, and all other tables reference files via integer FK or store bare string names. Re-homing an index is therefore a pure single-column prefix rewrite. `old_root` auto-detected as `os.path.commonpath` of all non-synthetic file rows when not supplied. Synthetic rows (path starts with `:`) are never touched. Never raises; idempotent (calling twice returns 0). KNOWN LIMITATION: cross-OS fetch (Linux→Windows or macOS→Windows) fails silently because stored paths use `/` but Windows `os.sep` is `\`; same-OS fetches always work.
- **New leaf `seam/indexer/artifact.py`** (stdlib-only: tarfile, hashlib, logging, os, io, pathlib, dataclasses): `pack_index(seam_dir, *, dest_dir) → PackResult | None` and `unpack_index(archive_path, *, dest_dir, checksum_path) → bool`. Single source of truth for the archive format (flat `.tar.gz`, members: `seam.db` always + optional `vectors.f32` / `vectors.ids.i64` / `vectors.meta.json` when present). Security contract: path-traversal guard (`_is_safe_member`), symlink/hardlink rejection, ALL-OR-NOTHING extraction (validate all members before writing any). `filter='data'` explicit for Python 3.12+ compatibility as second line of defence. Checksum sidecar in `sha256sum`-compatible format. Never raises — all public functions catch every exception and return None/False.
- **New orchestration `seam/cli/fetch.py`** (stdlib urllib only — zero third-party deps): `fetch_index(project_root, *, db_root, semantic) → dict`. Steps: (1) validate `SEAM_INDEX_ARTIFACT_URL`; (2) resolve git SHA + archive URL (HEAD first, then `SEAM_FETCH_ANCESTOR_DEPTH` first-parent ancestors); (3) download archive + optional checksum sidecar (checksum leniency: 404 → proceed with WARNING, mismatch → abort with FetchError); (4) verify + unpack into temp staging dir; (5) atomic swap-in with backup/restore (existing `.seam/` renamed to `.seam.fetch.bak/`, staged index moved into `.seam/`, backup deleted — on ANY exception the backup is restored before re-raise, so the original index is never corrupted); (6) rebase via `rebase_index`; (7) sync local delta via the existing `sync_project` + optional incremental `sync_embeddings`.
- **2 new config knobs:** `SEAM_INDEX_ARTIFACT_URL` (str, default `""` = feature inert) and `SEAM_FETCH_ANCESTOR_DEPTH` (int, default 50). CLI-only; no schema change, no re-index, no new MCP tool.
- **Security posture:** sha256 checksum verification before extraction; path-traversal + symlink/hardlink guard on every archive member (ALL-OR-NOTHING); atomic swap-in with backup/restore; `seam fetch` is the ONLY network path in Seam (the read path stays 100% local after fetch completes). Excluded from the no-egress proof (intentional setup-time download, documented alongside `seam init --semantic`).
- **CI template `.github/workflows/seam-index.yml`:** example workflow that runs `seam init`, optionally `seam init --semantic`, then `seam pack-index` and publishes the archive as a GitHub Actions artifact or release asset keyed by git SHA. Teams adapt this file to their CI system.
- **Tests:** `tests/unit/test_rebase.py` (434 lines: auto-detect, prefix-with-sep, identity, synthetic-row exclusion, single-file, empty DB, never-raises), `tests/unit/test_artifact.py` (497 lines: pack/unpack/verify, path-traversal rejected, symlink rejected, ALL-OR-NOTHING, checksum mismatch, optional vector files), `tests/integration/test_fetch.py` (588 lines: end-to-end fetch with `file://` URLs, ancestor fallback, checksum leniency, atomic swap, backup restore on failure, semantic flag, `SEAM_INDEX_ARTIFACT_URL` unset). Gate: ruff + mypy clean, full suite passes, 6 skipped (pre-existing fastembed real-model tests).
See `progress.txt`.

### Prior phase
Phase D — Explorer redesign: Coherent flow + tab model (issues #272–#275, closes the linear A→D Explorer redesign arc). Frontend + one additive Web API field only. No schema change, no migration, no re-index, MCP tool count stays 16. Zero new dependencies. One additive backend change: `GET /api/status` gains `stale: bool` + `stale_reason: str | None`.
- **Why (the three UX gaps this closes):** (1) The tab system was contextual — the "Constellation" button relabelled itself with the OTHER mode's name, so users could never see where they were. (2) The header carried operational index stats (symbol/edge/cluster counts + last-indexed time), consuming space that should belong to navigation. (3) There was no breadcrumb trail — navigating from a landing area card into a symbol's neighborhood and then clicking an unrelated node left the user with no path back without reloading.
- **D1 (#272) — Additive watcher-aware `stale` / `stale_reason` on `GET /api/status`:** `StatusResponse` moved from `web.py` to `web_schema.py` (file was at 991 lines; the established pattern). Two new fields added: `stale: bool` and `stale_reason: str | None` (None when fresh). Both derived from `check_staleness(conn, root=root, watcher_alive=..., respect_knob=False)` — the same source of truth used by the MCP graph-traversal handlers and `seam status` CLI — so `/api/status` can never disagree with them. `respect_knob=False` is critical: it ensures the endpoint returns the real verdict even when `SEAM_STALENESS_CHECK=off` (which would otherwise short-circuit to `stale=False`, causing `/api/status` to contradict `/api/schema` and `/api/architecture`).
- **D2 (#273) — Explicit Overview/Symbol/Topology tab bar (anti-pattern killed):** New `web/src/lib/tabs.ts` (pure, Node-safe) owns `ViewMode`, `TabDef[]`, and the label↔mode mapping in one place: `"Symbol"` → `"neighborhood"`, `"Overview"` → `"overview"`, `"Topology"` → `"topology"`. `HeaderToggle` (the component that relabelled itself with the OTHER mode's name) was removed entirely. `TabBar.tsx` renders the three tabs; the Changes drawer became a plain `aria-pressed` button so it doesn't compete visually with the sky-accent active tab (one accent element = one signal). `iconName` is a string union resolved to Lucide components inside `TabBar.tsx` so `tabs.ts` stays importable in Node test environments without React.
- **D3 (#274) — Bottom server-admin status strip:** New `StatusStrip.tsx` component demoted from the header — index counts, last-indexed relative time, and the stale warning (`stale=true` from D1) now live in a fixed-height monospace row at the bottom of the shell. Design principles: amber fires ONLY for a real stale verdict (one accent); the stale slot is always reserved (blank `<span>` not `display:none`) so the strip never shifts layout when stale toggles; `role="alert" aria-live="assertive"` announces the stale signal to screen readers; `data-testid="stale-indicator"` is the stable test hook (Tailwind classes are unstable across refactors).
- **D4 (#275) — End-to-end breadcrumbs:** Pure `deriveCrumbs(state, handlers) → Crumb[]` in `web/src/lib/breadcrumbs.ts` (injected handlers keep it unit-testable without React). Trail grammar: `repo` (always) → `area` (when `preselectedArea` set AND mode is overview/neighborhood) → `symbol` (when `centerSymbol` set AND neighborhood) → `selected` (when `selectedSymbol` distinct from center AND neighborhood). Topology clamps to `[repo]` — no drill path. `Breadcrumb.tsx` is a thin keyboard-navigable renderer (every crumb is a `<button>` with `aria-current="page"` on the last). Two-level system: App-level crumbs (this feature) cover the outer drill (landing → area → symbol); TreemapCanvas internal crumbs cover the inner Overview drill (folder → file → class) — together they form one coherent trail. `<main>` restructured to `flex-col` with Breadcrumb as `shrink-0` row 1 and a new wrapper `<div>` as row 2 so no existing component was modified.
- **Gate:** ruff + mypy clean, all Python tests pass / 6 skipped, tsc clean, vitest tests pass. `web.py` stays under 1000 lines. MCP tool count = 16.
See `progress.txt`.

### Prior phase
P5.5 — Opt-in local diagnostics (`SEAM_DIAGNOSTICS`) + soak testing (issues #237/#238/#239/#240/#241/#242). Read-path + indexing-time instrumentation only; no schema change, no new MCP tool (count stays 16), no new runtime dependency.
- **Why (the operational-visibility gap this closes):** as query volume and watcher activity grow across real sessions, an operator had no way to see how a running `seam start` behaves — memory creep, FD leaks, DB bloat, query volume, or which tool calls are slow — and no repeatable way to exercise the read path under sustained load. The local-first / no-telemetry contract forbids phoning home, so P5.5 adds a purely local, opt-in facility the operator turns on, reproduces a workload against, and inspects offline.
- **New leaf `seam/analysis/diagnostics.py` (the heart):** a `DiagnosticsRecorder` that is a **null recorder** when `SEAM_DIAGNOSTICS != "1"` (every method a no-op, no file opened, no atexit handler, byte-identical read path). When `"1"` it appends lightweight metrics to a local append-only NDJSON file inside `.seam/`: `record_query(tool, duration_ms, result_chars)` (writes a `slow_query` line only when `duration_ms >= SEAM_DIAGNOSTICS_SLOW_MS`; always increments the query counter), `record_watcher_event(kind)` (watcher counters), `sample_resources(db_path)` (RSS, open FDs, DB size — `null` for any metric unavailable on the platform), `snapshot(db_path)` (a `snapshot` line), plus a process-exit `atexit` flush. **Security invariant — structural redaction:** `record_query` accepts only a tool name + numeric metrics; argument text and result bodies are NOT parameters, so they can never be written (enforced by a hardcoded key set + a defense-in-depth subset assertion + a gate test). Leaf discipline: **never raises** (all IO/sampling wrapped, degrades to no-op). Stdlib best-effort resource sampling — **no new dependency**: peak RSS via `resource.getrusage` (Linux KiB / macOS bytes normalized; `null` on Windows), open FDs via `/proc/self/fd` (Linux only, `null` elsewhere), DB size via `os.path.getsize`. `get_recorder()` is the one-per-process singleton (shared by MCP + CLI + watcher); `run_query(tool, thunk)` times + records a call without altering its result; `close()`/`reset_recorder()` unregister the atexit handler for clean test teardown; `set_db_path()` records the resolved DB path so the atexit snapshot measures the right file under `--db-dir` / a non-root CWD.
- **Instrumentation (zero-overhead when off):** MCP server (`create_server` in `seam/server/mcp.py`) wraps all 16 tools with a per-tool `_instrument` decorator whose wrap decision is made ONCE at decoration time — off → the tool is returned unchanged (byte-identical FastMCP schema, no per-call cost). CLI read commands (`seam search/query/context` in `cli/read.py`; `seam impact/trace` in `cli/main.py`) route their handler call through `run_query`. The watcher (`watcher/daemon.py`) increments `reindexed` on a successful per-file re-index and `reindex_errors` in the caught-failure branch. `result_chars` is a serialized-length SIZE PROXY (measured then discarded — never stored).
- **Soak harness `benchmarks/soak.py` + `make soak`:** drives a configurable number of mixed `seam_search`/`context`/`impact`/`trace` requests (args discovered from the live index) against an already-indexed repo, through `run_query`, then prints a summary (queries, slow count, avg/peak latency, peak RSS, open-FD delta, DB size). `SEAM_DIAGNOSTICS=1 make soak` also captures the NDJSON trace and populates the resource fields (n/a when off). Requires an existing index; **NOT part of `make gate`** (mirrors `bench-semantic` / `no-egress`).
- **3 new config knobs:** `SEAM_DIAGNOSTICS` (`"0"`/`"1"`, default `"0"` = byte-identical no-op), `SEAM_DIAGNOSTICS_PATH` (default `.seam/diagnostics.ndjson`), `SEAM_DIAGNOSTICS_SLOW_MS` (int, default 100). No schema change, no migration, no new MCP tool (count stays 16), no new runtime dependency.
- **Tests:** `tests/unit/test_diagnostics.py` (29 unit tests: redaction invariant, no-op-when-off, NDJSON shape, graceful `null` degradation, never-raises, slow-query threshold, multi-process append, atexit-uses-resolved-db-path) + 4 integration modules — `test_diagnostics_mcp.py` (server tool calls → query records, no source leak, tool count 16, error still counted), `test_diagnostics_cli.py` (REAL subprocess → slow_query + atexit snapshot, no leak, disabled writes nothing), `test_diagnostics_watcher.py` (`_do_index` → reindexed/reindex_errors counters), `test_soak_smoke.py` (harness runs / errors cleanly / writes NDJSON). Gate: ruff + mypy clean, full suite passes, 6 skipped (pre-existing fastembed real-model tests).
See `progress.txt`.

### Prior phase
Phase 11 #259 — 3D Constellation polish: spherical globe layout, additive node glows, calmer edge web, click-isolates-never-navigates (issues #260/#261/#262/#263). Frontend + `seam/query/layout.py` only. No schema change, no migration, no re-index, MCP tool count stays 16. Zero new dependencies.
- **Why (the visual and UX gaps this closes):** the 3D constellation had three compounding problems: (1) the flat XY-ring + z-smear seed produced a disc-cloud with an "orange spike" — a few very-high-degree hub nodes repelled far from the cluster and dominated the view; (2) all nodes were solid spheres rendered with the stellar/degree color scale, occluding each other and making the depth buffer a black hole wherever spheres overlapped; (3) clicking a 3D node triggered 2D neighborhood navigation, making the 3D view feel like an accidental redirect rather than an exploration surface. The four slices close each gap independently.
- **S1 (#260) — Spherical globe layout + outlier clamp + log1p node sizing:** `seam/query/layout.py` replaces the flat ring seed with a deterministic Fibonacci (golden-spiral) sphere. Nodes are sorted by `(fnv1a(cluster_key), fnv1a(name))` before being assigned Fibonacci indices so same-cluster nodes get contiguous polar-angle bands (spatial locality in the warm-start). After FA2, `_recenter_and_clamp` recenters to the centroid and pulls radial outliers beyond mean + 2.5·sigma inward — eliminating the orange spike without flattening the normal spread. `node_size` switches from `base + min(deg*0.3, 10)` to `base + min(log1p(deg) * 1.5, 6)` (hub/leaf ratio ≈ 2.5×, smooth, no discontinuity at the cap). Two new pure helpers are exported for TDD: `_sphere_seed_positions` and `_recenter_and_clamp`. Pure, deterministic, never raises; LayoutResult/LayoutNode/LayoutEdge shapes unchanged.
- **S2 (#261) — Additive node glows, color by kind:** `NodeCloud.tsx` switches `meshBasicMaterial` to `AdditiveBlending + depthWrite=false + transparent`. Node colors now come from `KIND_COLORS[node.label]` (same source as the filter legend) instead of the stellar/degree scale. `node.color` is no longer the color authority; `kindColor(label)` is the single resolution point shared with the filter legend so the two cannot drift. Highlight boost (above 1.0, triggers Bloom) applied to kind color; dimmed nodes at 0.15×. No black holes in the depth buffer — spheres glow additively on the dark canvas background.
- **S3 (#262) — Calmer edge web + controlled Bloom:** `EdgeLines.tsx` lowers ambient edge intensities (same-cluster 0.25 → 0.10; cross-cluster 0.06 → 0.02) and adds a `LOUD_KIND_DIM = 0.5×` multiplier for warm/saturated kinds (`instantiates`, `uses`, `writes`) in the no-highlight path only — their large R-channel would otherwise dominate the ambient field. Bloom threshold raised 0.3 → 0.6 (only genuine node cores bloom; highlighted edges at 0.488 max-channel stay below threshold), intensity 1.2 → 0.8, radius 0.6 → 0.65.
- **S4 (#263) — Click isolates, never navigates:** `onFocusSymbol` removed from `ConstellationTabProps` entirely (structural enforcement — TypeScript enforces the isolation contract). A 3D node click now only isolates its neighborhood (dim + fly camera); it never changes `centerSymbol` or navigates the 2D graph. Inbound 2D→3D sync (`focusSymbol` prop) is kept. Added Esc key deselect (document-level listener), empty-canvas deselect (`Canvas.onPointerMissed`), and a `prefers-reduced-motion` guard (auto-rotate off + camera snap in one frame). Discoverability hint shown when no node is selected.
- **Gate:** ruff + mypy clean, all pytest passed, tsc clean, vitest passed. No schema change, no new deps.
See `progress.txt`.

### Prior phase
WS5 — File-as-context escape hatch for heavy CLI read commands (issues #226/#227). CLI-only, read-path only; no schema change, no new config knob, no new MCP tool (count stays 16).
- **Why (the context-window token gap this closes):** `seam impact` on a hub symbol or `seam context` on a deeply connected class can produce thousands of tokens — burning the agent's entire in-context budget for a single read. Before WS5, the only options were `--lean` (drops enrichment) or `--limit`/`--max-bytes` (caps entries), both of which lose information. WS5 adds `--to-file` to `seam impact`, `seam trace`, `seam flows`, and `seam context`: the full untrimmed result lands on disk at `.seam/out/<command>-<label>.json`, and stdout receives only a one-line summary + the path. The agent reads only the slice it cares about.
- **New pure leaf `seam/cli/file_sink.py`** (stdlib-only: json, logging, os, re, tempfile, pathlib): `summarize(data, command) → str` — pure, never raises; shape-aware one-liner per command (impact: total dependents + per-tier counts from `risk_summary`; context: caller/callee counts ± `[ambiguous]`; trace: hop count or "no path"; flows: entry-point count or step count + depth). Appends "(index stale)" when `index_status.stale` is present. `write_output_file(data, *, command, label, out_dir, path_override) → ToFileResult` — sanitizes the label (replaces unsafe chars including dots, caps at 120 chars to stay under the 255-char FS limit), ensures the output directory exists, writes compact JSON atomically (temp file + os.replace in the SAME directory to avoid cross-device rename errors), returns `{path, bytes, summary}`. Genuine filesystem failures are re-raised to the caller; never silently swallowed. Serializer matches `seam/cli/output.py` exactly: `json.dumps(data, ensure_ascii=False)` + trailing newline for shell compatibility.
- **CLI wiring** (thin calls into file_sink — all logic in the leaf): `seam impact`, `seam trace`, `seam flows` wired in `seam/cli/main.py` via shared `_get_seam_out_dir` + `_emit_to_file_error` + `_handle_to_file_output` helpers. `seam context` wired in `seam/cli/read.py` via a local `_write_to_file_and_emit` helper (read.py is imported by main.py; putting the helper in main.py would create a circular import). For `seam impact`, `--to-file` overrides display flags by calling `handle_seam_impact` with `verbose=True`, `limit=0`, `max_bytes=0` — the on-disk file is always the full untrimmed blast radius regardless of `--lean`/`--limit`/`--max-bytes`.
- **No schema change, no migration, no new config knob, no new MCP tool.** MCP tool count stays 16. Default behavior with no `--to-file` flag is byte-identical to pre-WS5 for all four commands.
- **Tests:** `tests/unit/test_file_sink.py` (52 tests: `summarize` for all 4 commands, `write_output_file`, path resolution, label sanitization, stale banner, error propagation, edge cases) + `tests/integration/test_to_file.py` (25 integration tests, TF1–TF18: all four commands, all output modes `--json`/`--quiet`/rich, bare `--to-file` vs explicit `--to-file-path`, `--to-file-path`-alone file mode, directory hint, concurrent-safe atomic write). `--to-file` is a boolean flag (bare, no value); `--to-file-path` carries an explicit destination. Gate: ruff + mypy clean, full suite passes (0 failed), 6 skipped (pre-existing fastembed real-model tests).
See `progress.txt`.

### Prior phase
P5.1 — npm shim `@catafal/seam` that wraps `uvx` (issue #229). Distribution layer only; no Python product source changed, no schema change, no new MCP tool.
- **Why (the distribution gap this closes):** JS/TS projects that don't have a Python toolchain can't run `pip install seam-code` without ceremony. `npx @catafal/seam <cmd>` lets any Node.js developer use Seam without installing Python or managing a venv — they need only `uv` (a single static binary, no Python required) and Node.js. The shim is deliberately NOT a bundled binary or an npm postinstall downloader: `uvx` owns download, checksum verification, and caching against PyPI. This means the npm package carries no Python code, no wheels, and no install-time scripts — the smallest possible attack surface.
- **`pkg/npm/lib/invocation.js` (pure, dependency-free deep module):** two exported functions — `resolveRunner(env, opts)` → string | null (honors `SEAM_NPM_UVX` env override; defaults to a `which`/`where` probe that uses `execFileSync`, never a shell string — no shell-injection risk; returns null rather than throwing so bin.js owns the UX decision) and `buildUvxArgs(userArgv, opts)` → string[] (returns `['--from', 'seam-code==<version>', 'seam', ...userArgv]`; honors `SEAM_NPM_FROM` spec override for pre-release testing; arg order is load-bearing). Pure functions, no I/O — fully testable without spawning.
- **`pkg/npm/bin.js` (thin harness):** reads its own version from `package.json` (single source of truth — no hardcoded version), calls `resolveRunner` + `buildUvxArgs`, spawns via `spawnSync` with `stdio:'inherit'` (child output flows directly to terminal, no buffering), propagates `child.status ?? 1` (null on signal termination → 1, not 0). No `shell:true` anywhere. When uvx is absent, prints an install-guidance URL to stderr and exits 1.
- **`pkg/npm/package.json`:** `@catafal/seam@0.4.0`, `engines.node>=18`, no `postinstall` script (this file only runs on explicit invocation), `files` allowlist ships only `bin.js`, `lib/`, and `README.md`. `devDependencies` contains only `vitest` — the published package has zero runtime npm deps.
- **`pkg/npm/lib/invocation.test.js`:** 20 vitest unit tests covering the canonical arg shape, version pin passthrough, fromSpecOverride, empty/undefined argv, flag-order preservation, whitespace-only override ignored, graceful fallback on no-opts, and the resolveRunner env-override + probe injection contract. All tests inject fakes; no network, no spawn.
- **`tests/integration/test_npm_shim.py`:** Python smoke test (7 tests) using a stub uvx shell script to assert exact argv forwarding to uvx, exit-code propagation (0 and 42), `SEAM_NPM_FROM` spec override, `SEAM_NPM_UVX` custom runner, missing-uvx guidance message, and runtime argv matching pyproject.toml version. Self-skips when node is absent (mirrors fastembed `importorskip` discipline).
- **`tests/unit/test_smoke.py` (new gate test):** `test_npm_package_version_matches_pyproject` asserts `pkg/npm/package.json` version equals `pyproject.toml` version using only stdlib `json` + `tomllib` — no Node required. Runs in `make gate`, fails immediately on drift before any publish step.
- **`Makefile`: `make test-npm`** — node-gated (`command -v node || skip`); `cd pkg/npm && npm test` (vitest). NOT part of `make gate` (requires Node ≥18). The version-parity gate test IS part of `make gate`.
- **`README.md`:** npx install path added to Quickstart (with uv prerequisite note + reproducible-version note), `make test-npm` added to Development commands, and a "Publishing the npm shim" section documenting the trailing-PyPI publish ritual.
- **No schema change, no migration, no new config knobs, no new MCP tools.** MCP tool count stays 16. Gate: ruff + mypy clean, 3427 tests passed, 6 skipped (fastembed), 0 failed. `make test-npm`: vitest 20 passed (1 file).
See `progress.txt`.

### Prior phase
P5.2 — Supply-chain hardening of the PyPI release workflow. No product source changed.
- **Why:** A `uses: owner/repo@v1` tag ref can be silently replaced between runs — a supply-chain risk for a package published to PyPI. Pinning every action to a full 40-hex commit SHA makes each CI run bit-for-bit reproducible and immune to tag-squatting or branch-force-push attacks.
- **`tests/support/actions_pin_audit.py` (S1):** stdlib-only `uses:` ref classifier. `classify_uses_ref(ref) → 'pinned' | 'mutable'`: local `./…` or 40-hex SHA = PINNED; everything else = MUTABLE. Fail-closed: an unrecognisable ref is MUTABLE. `python -m tests.support.actions_pin_audit <files>` exits 1 on any violation. Never raises.
- **`tests/unit/test_actions_pin_audit.py`:** 25 tests covering the full public contract + one repo-invariant gate test (`test_all_workflow_files_are_sha_pinned`) that scans `.github/workflows/*.yml` in `make gate` — fails immediately and deterministically if anyone adds a mutable ref to any workflow.
- **SHA-pinning:** all `uses:` refs in `ci.yml`, `release.yml`, and `no-egress.yml` are pinned to 40-hex SHAs with inline `# vN` human-readable comments.
- **Hardened `release.yml` job graph:** `gate → build → smoke(3.12, 3.13) → {publish, github-release}`. Both `publish` and `github-release` declare `needs: [gate, smoke]` — a red build or failing smoke matrix cannot reach PyPI or the GitHub Release.
- **Pre-publish wheel smoke:** installs the built `.whl` FILE (not the source tree) into a clean `/tmp/smoke-env` venv and asserts `seam --help` exits 0. (The CLI has no `--version` flag; `--help` is the correct smoke assertion.)
- **Checksums + GitHub Release:** `checksums.txt` (sha256 of sdist + wheel) written OUTSIDE `dist/` (a `.txt` inside `dist/` would cause a PyPI publish error) and attached to the GitHub Release via the pre-installed `gh` CLI — no third-party action required, so the pin-audit gate is not widened.
- **PEP 740 attestations** (`attestations: true`) and Trusted Publishing (OIDC) preserved. The publish path is CI-only-proven — it requires a real `v*` tag push.
See `progress.txt`.

### Prior phase
Phase C — Explorer redesign: Constellation, done BOTH ways (issues #251/#252/#253). Frontend + one additive Web API field only. No schema change, no migration, no re-index, MCP tool count stays 16. Zero new dependencies.
- **Why (the constellation dead-end this closes):** the 3D view was visually "wow" but answered no developer question. A 2,000-node perspective cloud cannot tell you whether the architecture is a hub-and-spoke, a mesh, or a chain — 3D perspective and node overlap make the macro shape unreadable. Meanwhile `/api/constellation` already computed the answer (20–50 clusters + weighted inter-cluster links) and the frontend threw it away. Phase C renders that data as a legible 2D cluster-graph, demotes 3D behind a toggle, and wires cluster clicks into the existing drill path.
- **Additive `representative` on `/api/constellation` (C1):** `build_constellation` in `graph_api.py` now calls a new public helper `fetch_cluster_representatives(conn) → dict[int, str]` that runs the MIN(id) rep-query `/api/clusters` already used. Single source of truth: both endpoints call the same function, so the hand-off target is consistent and cannot diverge. `ConstellationCluster` Pydantic model + `web/src/api/types.ts` gain `representative: str | None`.
- **2D cluster-graph fallback (C2):** `useConstellation()` thin hook; pure `clusterGraphLayout(clusters, links, opts) → {nodes, edges}` (deterministic radial layout — sorted by size DESC, placed on a circle; node size ∝ √(size); edge width ∝ log1p(weight); `clusterColor` for identity; no physics, no jitter, trivially unit-testable); `ClusterGraph2D` component in `@xyflow/react` (sibling of `GraphCanvas`, not a modification). Empty state: "No clusters yet — run `seam init` to build the index."
- **2D/3D toggle + hand-off (C3):** `resolveClusterHandoff(cluster) → string | null` pure resolver (representative → label → null; graceful no-op); `App.tsx` renames `ViewMode` "constellation" → "topology" and adds `TopologySubMode` "2d"|"3d" state (default "2d"); inline sub-toggle in header; `handleOpenCluster` calls the resolver then `setCenterSymbol + setMode("neighborhood")` — clicks always exit to the 2D neighborhood, no navigation within 3D.
- **Review fixer:** cluster node type changed from "clusterNode" → "default" (ClusterGraph2D registers no `nodeTypes` map; the custom type triggered React Flow error 002 per node; appearance is via `style` prop). Hand-off tests strengthened with symbol-text assertions.
- **Gate:** ruff + mypy clean (122 files), 3555 pytest passed / 6 skipped, 466 vitest passed (39 test files), tsc clean, vite build green. `web.py` = 991 lines (under 1000).
See `progress.txt`.

### Prior phase
Phase B — Explorer redesign: Landing · Areas · Snippet (issues #231/#232/#233/#234). Frontend + one additive Web API field only. No schema change, no migration, no re-index, MCP tool count stays 16.
- **One areas concept via `useAreas` (B1):** the landing's "Largest areas" section dropped `useClusters()` and now derives areas from the same folder-based `deriveAreas` the Overview uses. A shared `useAreas({ includeTests })` hook in `hooks.ts` composes `useStructure` + `useHubs` + `deriveAreas` — both the landing and `StructureOverview` consume it, so the "area" concept has exactly one derivation site and landing/Overview cannot drift apart again. Clicking a landing area enters the scoped treemap directly (`preselectedArea` state + `StructureOverview` `initialArea` prop). One `showTests` toggle controls both hub chips and area cards.
- **Additive `/api/structure` `degree` field (B2):** `list_structure` in `graph_api.py` gains a per-symbol fan-in degree (incoming edge count) via a LEFT JOIN against a pre-aggregated edges subquery. Bridges the qualified/bare name asymmetry for methods (bare-target call edges are attributed via the bare trailing identifier). `StructureSymbol`/`StructureResponse` Pydantic models moved to new `web_schema.py` to keep `web.py` under 1000 lines. Frontend: `TreeNode` gains `degree: number`; new `rollupDegree()` mirrors `rollupCounts()` summing degrees bottom-up. This is the only backend change.
- **Treemap sized and colored by fan-in degree (B3):** new pure leaf `degreeColor(degree, maxDegree) → "#rrggbb"` (sequential ramp: cool zinc floor → hot amber ceiling; linear RGB interpolation; `maxDegree=0` guard). `hashColor` retired from `TreemapCanvas` for leaves. Squarify `value` = `max(degree, 1)` (floor so zero-degree cells stay visible). `maxDegree` is the local visible-level max so a drilled subdir uses the full ramp. New `DegreeLegend` sub-component. Size and color now encode the same single quantity (fan-in) so they reinforce rather than compete.
- **Source snippet panel in `DetailPanel` (B4):** new pure leaf `buildSnippetSelector(symbol, firstDef) → SnippetSelector | undefined` prefers a file+line selector so the Source section shows the exact indexed definition the panel is already displaying (homonym-safe). `DetailPanel` gains a collapsible `SourceSection` (collapsed by default so callers/callees are not pushed off screen on hub symbols). `useSnippet` is gated on the selector being defined — naturally lazy. Actionable copy for all empty/error states.
- **Gate:** ruff + mypy clean (120 files), 3429 Python tests passed / 6 skipped, 423 frontend vitest tests passed (34 test files), tsc clean, vite build green. `web.py` = 984 lines.
See `progress.txt`.

### Prior phase
WS3 — Incremental re-embedding for `seam sync --semantic` (issues #208/#210/#211). Indexing-time only; no schema change, no new config knob, no new MCP tool (count stays 16).
- **Why (the freshness/efficiency gap this closes):** Before WS3, `seam sync --semantic` called `index_embeddings` with no scope — it re-embedded ALL symbols on every sync, even unchanged ones. On a large codebase this was slow (proportional to total symbol count, not changed symbol count) and wasteful (stable symbols produce identical vectors). After WS3, `seam sync --semantic` is incremental: it orphan-sweeps deleted embeddings, computes the missing-set (symbols with no embedding row for the current model), embeds only those, and rebuilds the mmap artifact only when the DB state diverged.
- **Scoped embed — `only_symbol_ids` param on `index_embeddings` (Slice 1, #210):** `only_symbol_ids: set[int] | None = None`. `None` (default) = full embed — byte-identical to pre-WS3 `init --semantic`. A non-empty set = embed ONLY those symbol IDs via a TEMP TABLE JOIN (avoids SQLite's variable-number limit of 999). An empty set returns 0 immediately without calling the embedder. The scoped path deliberately suppresses `_write_artifact` — writing the artifact from a partial scope would corrupt the full artifact; the Slice 2 orchestrator owns rebuild. Three new public helpers added to `seam/indexer/embedding_index.py`: `symbols_needing_embeddings(conn, model) → set[int]` (LEFT JOIN to find un-embedded IDs — avoids NOT IN NULL pitfall); `delete_orphan_embeddings(conn) → int` (defensive sweep for embeddings with no matching symbol); `sync_embeddings(conn, *, model, batch) → int` (the Slice 2 orchestrator).
- **Incremental orchestrator — `sync_embeddings` (Slice 2, #211):** Orchestrates the full incremental cycle: (1) orphan sweep via `delete_orphan_embeddings`; (2) missing-set via `symbols_needing_embeddings`; (3) scoped embed via `index_embeddings(only_symbol_ids=missing_ids)`; (4) conditional artifact rebuild. The artifact rebuild uses a staleness-token comparison (`compute_index_version` result vs stored `store.index_version`) rather than relying solely on the `n_removed` counter — because SQLite FK CASCADE deletes embedding rows BEFORE `sync_embeddings` runs (when symbols are removed during `sync_project → delete_file`), so the orphan sweep sees nothing to delete even though the artifact is stale. The staleness-token check is cause-agnostic and catches all divergence sources. Returns n_added (≥0), 0 when nothing new or fastembed absent, -1 on any error. Never raises — mirrors `index_embeddings` / `index_clusters` sentinel discipline.
- **CLI wiring:** `seam sync --semantic` now calls `sync_embeddings` (imported from `embedding_index`) instead of `index_embeddings`. The `--semantic` help text updated to document the incremental behavior. The `init --semantic` path is UNTOUCHED — it goes through `run_init → init_index.py → index_embeddings(only_symbol_ids=None)`, which is the full embed; no change there.
- **No schema change, no migration, no new config knob, no new MCP tool.** MCP tool count stays 16. `seam init --semantic` behavior is byte-identical to pre-WS3.
- **Tests:** `tests/unit/test_scoped_embedding.py` (27 tests, SC1–SC8: full embed, scoped embed, empty scope, body enrichment, large scope >999 IDs, symbols_needing_embeddings, delete_orphan_embeddings, artifact suppression on scoped path), `tests/integration/test_sync_embeddings.py` (11 integration tests, SE1–SE8: full incremental cycle, orphan sweep, pure-removal artifact rebuild, artifact staleness token, CLI wiring, fastembed-absent skip). Gate: ruff + mypy clean, 3354 tests passed, 6 skipped (pre-existing fastembed real-model tests), 0 failed.
See `progress.txt`.

### Prior phase
P5.4 — Linux-CI no-egress proof: syscall-level verification that Seam makes zero outbound network connections on its read path (`.github/workflows/no-egress.yml`).
- **Why:** The local-first contract was asserted in prose (SECURITY.md, Known Gotchas) but never verified at the syscall level. P5.4 closes that gap with a `strace`-based proof in CI.
- **New `tests/support/egress_audit.py` (S1):** Pure stdlib strace `connect()` parser — classifies each line as `'local'`, `'external'`, or `None`. Fail-closed: AF_INET/AF_INET6 with an unparseable address is reported as `external`. `python -m tests.support.egress_audit` reads trace files from argv, exits 1 on any external connect.
- **New `tests/support/mcp_smoke.py` (S2):** asyncio MCP stdio handshake driver — spawns `seam start <root>`, performs `initialize` + `list_tools`, exits 0. Used by the CI workflow to prove the server path under strace.
- **`.github/workflows/no-egress.yml`:** `ubuntu-latest` job: runs `seam init` / `seam search` / `seam context` / `seam impact` + the MCP smoke driver, each under `strace -f -e trace=connect`, feeds each tracefile to `egress_audit`; fails on any external connect. `SEAM_CLUSTER_NAMING=llm`, `seam init --semantic` (model download), and `seam fetch` (WS4 shared-index provisioning download) are excluded — the known opt-in setup-time network paths. NOT part of `make gate` (strace + Linux required).
- **No product source changed.** No schema change, no new config knobs, no new MCP tools. `tests/unit/test_egress_audit.py`: 28 unit tests covering the parser contract (including the `<unfinished>/<... resumed>` split-line limitation).
See `progress.txt`.

### Prior phase
WS2a — Persisted mmap vector store for semantic search (issues #197/#198). Read-path + indexing-time; no schema change, no new MCP tool.
- **Why (the semantic performance and recall gap this closes):** The original semantic read path rebuilt an `(N, dim)` float32 matrix from SQLite blobs on EVERY query — slow (per-query blob decode) and capped by `SEAM_SEMANTIC_SCAN_CAP` (old default 20,000 rows), which silently dropped symbols beyond the cap from all semantic results on large codebases. A persisted mmap artifact fixes both: the OS page cache backs the matrix (zero-copy, no per-query decode) and the artifact is written from ALL rows at embed time (no cap-induced recall loss).
- **New leaf `seam/query/vector_store.py`:** `get_artifact_dir(conn)` derives the `.seam/` directory from PRAGMA database_list. `write_store(...)` writes three sibling files atomically (temp+os.replace per file; meta last so `load_store` never sees a meta without a valid matrix; try/finally cleans up temp files on failure to prevent disk leaks). `load_store(dir, model)` validates model, dtype/byteorder, and file sizes before mmap-loading the matrix zero-copy. `top_k(store, query_vec, k, *, scan_cap=0)` uses the same `(mat @ q) / (norms_mat * norm_q)` cosine formula as the SQL fallback for byte-identical results. `compute_index_version(conn, model)` returns a `"{count}:{max_symbol_id}"` staleness token (cheap, deterministic, sufficient to detect row additions/removals). Never raises — all public functions catch exceptions and log warnings, returning None/[].
- **Write hook in `seam/indexer/embedding_index.py`:** `_write_artifact(conn, model, dim)` re-reads the persisted rows from DB in symbol-id order (authoritative source, not in-memory batch state) and calls `write_store`. Failure is swallowed (logged at WARNING) — the `index_embeddings` return value is unaffected; the SQL path remains the fallback. Gated by `SEAM_VECTOR_STORE=on`.
- **Read path in `seam/query/semantic.py`:** `_try_mmap_path(conn, query_vec, model, limit)` loads the artifact, recomputes the index-version token from the DB, and compares against the stored token. A mismatch (stale artifact) or any other issue returns `None` — the caller falls through to the SQL brute-force path transparently. `None` (no artifact/stale) is distinct from `[]` (valid semantic result, nothing matched).
- **`SEAM_SEMANTIC_SCAN_CAP` default changed 20000 → 0 (unlimited, Slice 2 / #198):** Both paths honor 0=unlimited: the SQL fallback omits the LIMIT clause and the mmap `top_k` considers all artifact rows. A positive cap is still honoured (SQL LIMIT, mmap `matrix[:scan_cap]` slice) for memory-constrained operators.
- **2 new config knobs:** `SEAM_VECTOR_STORE` (`"on"`/`"off"`, default `"on"`; `"off"` = byte-identical pre-WS2a SQL-only path — no artifact IO). No schema change, no migration. MCP tool count stays 16.
- **Tests:** `tests/unit/test_vector_store.py` (write/load/top_k/staleness/degrade), `tests/integration/test_vector_store_fallback.py` (end-to-end mmap path + fallback via fake DB), `tests/integration/test_scan_cap_unlimited.py` (cap=0 includes beyond-old-cap symbol; positive cap excludes it, for both paths), `tests/unit/test_semantic_review_fixes.py` (cap default == 0). Gate: ruff + mypy clean, 3268 tests passed, 6 skipped (existing fastembed skips), 0 failed.
See `progress.txt`.

### Prior phase
Phase 11 P5.3 — installer write-scope audit (issues #199/#200/#201/#202). Test-only slice; no installer source change.
- **Why (the safety gap this closes):** `seam install` / `seam uninstall` write to the agent's config files and the project root. There was no systematic proof they ONLY touch expected paths and never leak outside the declared scope. P5.3 adds an audit harness and 28 parametrized test cells that snapshot the filesystem before and after every install/uninstall call and diff the result.
- **New test-support helper `tests/support/fs_audit.py`** (stdlib-only): `snapshot(roots) → dict[str, sha256_hex]` + `diff(before, after) → FsChanges`. sha256 digest avoids mtime-resolution false-negatives; unreadable files surface as `modified` via a sentinel digest rather than silently disappearing. Never raises.
- **S1 (`tests/unit/test_fs_audit.py`, #200):** 16 unit tests locking down the `fs_audit` public contract (snapshot + diff + sentinel behavior).
- **S2 (`tests/integration/test_installer_write_scope.py` install section, #201):** 10 cells verifying every install invocation writes exactly the expected set (guidance-only × 3 targets, --with-mcp × 5 target/location combos, --target all × 2, idempotent second run × 3, corrupt-config backup, --print-config × 2, codex/project invalid).
- **S3 (uninstall section, #202):** 8 round-trip cells + 3 foreign-content preservation tests + 1 negative meta-test. Empirically documented benign uninstall residue: empty container files/structures left after the installer removes its block (see Known Gotchas). Cursor guidance is the only zero-residue target. Foreign content always survives.
- **No installer source changes, no schema change, no new config knobs, no new MCP tools.** Gate: ruff + mypy clean, 3277 tests pass (6 skipped).
See `progress.txt`.

### Prior phase
Phase 11 P2.2 — 2D Explorer upgrades: resizable panels, grouped caller/callee with edge metadata, HUD, expanded FilterBar (node-kind axis + All/None + live counts), file-tree sidebar, fly-to-fit viewport, and backend `kind` enrichment on callers/callees + impact entries. Frontend + thin web-layer additions only. No schema change, no new config knobs, no new MCP tools.
- **Why (the Explorer gaps this closes):** The 2D graph tab had a fixed-width detail panel, caller/callee counts only (no clickable rows), no filter counts, no node-kind filtering, no file-tree navigation, no viewport fit-to-overlay, and edge-kind data that the API omitted from the `/api/symbol` response (it was in the DB but not plumbed through). P2.2 closes all of these in 9 slices (issues #186–#194 + review fix #185).
- **S1 — resizable + persisted detail panel (#186):** `ResizeHandle` now exposes `readPanelWidth(key, fallback)` shared by both 2D (`App.tsx`) and 3D (`ConstellationTab.tsx`). Panel width is persisted to `localStorage` (`seam-detail-panel-w`).
- **S2 — caller/callee kind+confidence enrichment (#187):** `/api/symbol/{name}` now returns `callers`/`callees` as `CallerRef` objects `{name, kind, confidence}` instead of bare strings. A new `fetch_edge_refs(conn, symbol_name, *, direction)` helper in `seam/server/graph_api.py` queries the `edges` table directly (MIN(id) dedup, returns `[]` on error). `ImpactEntry` in `web.py` gains `kind: str | None` (already produced by the handler's `SEAM_EDGE_PROVENANCE=on` path — Pydantic was discarding it).
- **S3 — grouped clickable caller/callee in DetailPanel (#189):** Rows are grouped by edge kind with per-group `GROUP_CAP=5` + "show N more" expander. Each row is clickable (updates `selectedSymbol`; does NOT change `centerSymbol`). Confidence badge (EXTRACTED/INFERRED/AMBIGUOUS). Long docstrings clamped at `DOCSTRING_CHAR_LIMIT=200` with a show-more toggle.
- **S4 — `useGraphOverlays` hook extraction (#188):** Overlay-decoration logic (`decorateNodes`, `buildOffCanvasNodes`, `decorateEdges`, `visibleClusters`, `applyNodeKindFilter`) extracted from `GraphCanvas.tsx` into `web/src/hooks/useGraphOverlays.ts`. All pure functions are exported for vitest isolation. Keeps `GraphCanvas.tsx` under the 1000-line limit as later slices add code.
- **S5 — GraphHUD (#190):** `GraphHUD` component (React Flow Panel at bottom-left) shows visible-node + visible-edge counts, filtered-out badge, off-canvas impacted count, selected count, and a freshness dot. Shared `freshnessColor(lastIndexed)` extracted from `ConstellationHUD` into `web/src/lib/freshnessColor.ts` so both HUDs apply the same green/amber convention (10-minute threshold).
- **S6a — FilterBar All/None + live counts + phantom-kind pruning (#191):** `FilterBar` gains `All / None` per group, per-option visible counts (from post-overlay `displayEdges`), colored dots, and removes phantom edge kinds — the 9 real kinds only. Count helpers live in `web/src/lib/filterBarCounts.ts` (pure, vitest-tested).
- **S6b — node-kind filter axis + session-global persistence (#192):** `GraphFilterState` (`web/src/lib/graphFilterState.ts`) extends `EdgeFilterState` with `nodeKinds: Set<string>`. Persists explicitly-DISABLED kind sets to `localStorage` (`seam-graph-filter`) so new kinds default to enabled without migration. `applyNodeKindFilter` in `useGraphOverlays` sets the React Flow `hidden` flag after overlay decoration (independent axes). Filter state is initialized from localStorage on mount and NOT reset on center change — session-global by design.
- **S7 — fly-to-fit viewport (#193):** `ViewportController` component (inside the React Flow tree, using `useReactFlow()`) fires `fitView` on overlay activation/deactivation. Transition-guard via prev-value refs + `framed` ref prevents spurious re-fits when data arrives asynchronously after the toggle. "Fit all" button always available at bottom-right.
- **S8 — FileSidebar (#194):** Collapsible, resizable VS-Code-style file-tree sidebar. Structure data is lazy-fetched (only on first open). Debounced search forces all dirs expanded when a filter is active. Clicks use qualified name to open the correct homonym. Width + open/closed state persist to `localStorage` (`seam-sidebar-open`, `seam-sidebar-w`).
- **No new config knobs, no schema change, no re-index.** MCP tool count stays 16. Gate: ruff + mypy clean, all Python tests pass; frontend vitest + typecheck + build green. New test files: `DetailPanel.test.tsx`, `FileSidebar.test.tsx`, `filterBarCounts.test.ts`, `graphFilterState.test.ts`, `graphHud.test.ts`, `panelWidth.test.ts`, `useGraphOverlays.test.ts`, `viewportController.test.tsx`.
See `progress.txt`.

### Prior phase
WS1 — Richer embedding input: body slice + DB comments in `seam init --semantic` (issues #182/#183/#184). Extraction/indexing-time only; no schema change, no re-index of the graph, no new MCP tool.
- **Why (the semantic recall gap this closes):** The original embedding input was `name + signature + docstring` — three fields that tell an embedding model the *shape* of a symbol but nothing about its *implementation*. A developer searching for "parse a JWT token" got no signal from the function body that actually calls `jwt.decode()`. Adding the body slice + inline WHY/HACK/NOTE comments closes this gap without tokenizer dependencies or schema changes.
- **WS1-A (`SEAM_EMBED_BODY=on`, issue #182):** `index_embeddings` fetches `start_line`/`end_line`/`file_path` for each symbol, reads each source file at most once (per-file dict cache), and appends the body text to the embedding input up to `SEAM_EMBED_INPUT_MAX_CHARS` (default 2000 ≈ 500 tokens). The header (name + signature + docstring) is NEVER truncated — body fills the remaining budget. A file that cannot be read degrades that file's symbols to header-only (log warning, never raises). Default `"off"` = byte-identical to pre-WS1-A; no disk reads occur.
- **WS1-B (comments, issue #183):** Gated by the same `SEAM_EMBED_BODY` knob. A single SQL pass (`GROUP_CONCAT` per symbol over `comments WHERE line BETWEEN start_line AND end_line`) fetches WHY/HACK/NOTE comment texts. These are appended AFTER the body, when budget remains. Empty/whitespace-only comment strings contribute nothing — no dangling separator. When `SEAM_EMBED_BODY=off`: no comment join, byte-identical to pre-WS1-B.
- **WS1-C (#184):** Semantic recall benchmark (`benchmarks/semantic_recall.py`) extended to measure body-on vs body-off recall over the 15 concept queries. `make bench-semantic` runs both modes and reports the delta. NOT part of the gate.
- **New pure helper `extract_body_slice(source_lines, start_line, end_line) → str`** in `seam/analysis/embeddings.py`: pure, no disk IO, guards all edge cases (empty, out-of-range, start > end) without raising. Cleanly unit-testable in isolation.
- **2 new config knobs:** `SEAM_EMBED_BODY` (`"off"`/`"on"`, default `"off"`; `"off"` = byte-identical pre-WS1-A), `SEAM_EMBED_INPUT_MAX_CHARS` (int, default 2000; 0 = unlimited). Indexing-time only — changing them requires a full `seam init --semantic` re-index to repopulate vectors. No schema change, no graph re-index, MCP tool count stays 16.
- **Tests:** 4 new test modules — `tests/unit/test_embeddings_body.py` (250 lines, `extract_body_slice` + `symbol_text` with body/comments), `tests/unit/test_embeddings_comments.py` (278 lines, `symbol_text` budget/empty/whitespace guard), `tests/integration/test_embedding_index_body.py` (357 lines, end-to-end body path in `_index_embeddings_impl`), `tests/integration/test_embedding_index_comments.py` (376 lines, end-to-end comment join path). Gate: ruff + mypy clean, full suite passes.
See `progress.txt`.

### Prior phase
Phase 11 P2.1 — 3D Constellation Explorer tab in the Seam Explorer web UI (`[web]` extra). Read-path, frontend + one new FastAPI route only.
- **Why (the Explorer gap this closes):** The 2D cluster constellation (`/api/constellation`) showed inter-cluster links but no individual nodes or edges. A developer browsing the Explorer had no way to see the full symbol graph spatially or identify hub symbols visually.
- **Server-side layout (`seam/query/layout.py`):** ForceAtlas2 (40 iterations, O(n²) numpy kernel) + ring-seed (FNV-1a, deterministic) + BFS z-depth from entry points. Returns pre-positioned `LayoutResult` {nodes, edges, clusters, total_nodes}. Cached per `(MAX(indexed_at) × 1_000_000 + file_count, max_nodes)` with TTL=`SEAM_STALENESS_TTL_SECONDS`. Never raises.
- **New FastAPI route (`seam/server/web_layout.py`):** `GET /api/graph/layout?max_nodes=N`. Pydantic models use the `Layout*` namespace to avoid collision with existing 2D `GraphNode`. Mirrors the `register_*_routes` pattern. Fresh connection per request (thread-safety).
- **Frontend (React + R3F):** `ConstellationTab.tsx` (shell + state machine), `ConstellationScene.tsx` (R3F canvas: OrbitControls, Bloom, CameraAnimator, auto-rotate), `NodeCloud.tsx` (InstancedMesh, per-instance color with bloom-trigger boost), `EdgeLines.tsx` (additive LineSegments), `NodeLabels.tsx` (CanvasTexture sprites, top-80 by size), `ClusterHalos.tsx` (translucent spheres), `FilterPanel.tsx` (kind/edge toggles), `NodeDetailPanel.tsx`, `ConstellationHUD.tsx`. Loaded via `React.lazy()` to keep R3F out of the initial bundle.
- **Color system (`seam/lib/constellationColors.ts`):** stellar color scale (red dwarf → blue giant by degree), per-edge-kind hues for all 9 kinds, teal-void canvas background.
- **2 new config knobs in `seam/config.py`:** `SEAM_LAYOUT_MAX_NODES` (int, default 2000) and `SEAM_LAYOUT_MAX_SAFE_NODES` (int, default 3000 — hard ceiling applied inside `compute_layout`, regardless of caller). No schema change, no re-index, no new MCP tool. `[web]` extra now requires `numpy`.
- **Tests:** backend unit tests in `tests/query/test_layout.py` (layout pipeline, stellar_color, node_size, FNV-1a, BFS depth, cluster summaries) + `tests/server/test_web_layout.py` (route 200/503); frontend vitest in `web/src/__tests__/constellation/` (pure helpers: computeHighlightedIds, computeCameraTarget, easeOutCubic, buildEdgeGeometry, bareName, selectLabelNodes, computeInstanceColor, countByField).
See `progress.txt`.

### Prior phase
CLI-first agent guidance — `seam install` defaults to a token-lean CLI playbook; MCP is opt-in via `--with-mcp`. Installer-layer only (issue #106).
- **Why (the token gap this closes):** Seam is agentic-first, but `seam install` wired up MCP, the most expensive path for an agent to *use* Seam — the 12 MCP tool schemas cost ~6,060 tokens of standing context/session, and the MCP tools bottom out at lean-JSON. The CLI's `--quiet` mode is ~14–17× leaner than the leanest MCP call (measured: `seam impact connect --quiet` = 170 tok vs 2,324), but nothing told the agent the CLI existed or when to escalate verbosity. This makes the cheap, on-brand path the default.
- **CLI-first default; MCP opt-in:** bare `seam install` now writes a token-lean CLI **guidance** playbook into the repo (the escalation ladder `--quiet`→`--json --lean`→`--json`, `seam init`/`sync` freshness, when-to-use triggers, MCP-as-alternative note). `--with-mcp` ALSO writes the MCP config (the old behavior). `seam uninstall` reverses both. Seam unpublished → changing the default is safe.
- **Per-agent rendering (token principle: tiny always-loaded + progressive detail):** Claude Code → a project **skill** (`.claude/skills/seam/SKILL.md`, body loads on invocation) + a thin `CLAUDE.md` discovery hook (~95% reliable vs ~70–85% description-only). Cursor → an **"Agent Requested" rule** (`.cursor/rules/seam.mdc`, `alwaysApply: false` + description + empty globs). Codex → an inline `AGENTS.md` block (no progressive mechanism). Guidance is **project-scoped** (lives in the repo), independent of the MCP `--location` — so Codex gets repo guidance even though its MCP is user-only.
- **2 new pure leaves:** `seam/installer/guide.py` (ONE guide template + 4 renderers — single source, no drift) and `seam/installer/markdownfile.py` (owned-file write + marker-delimited block upsert/remove with `<!-- seam:start/end -->`, atomic; preserves foreign AGENTS.md/CLAUDE.md content, never duplicates). `AgentTarget` ABC extended with `install_guidance`/`uninstall_guidance`/`guidance_previews`.
- **No new config knobs, no schema change, no re-index, MCP tool count stays 12.** Gate: ruff + mypy clean (100 files), full suite + 47 installer tests pass.
See `progress.txt`.

### Prior phase
P2 — index staleness banner on the 5 graph-traversal tools + `tools.py` split. Handler-layer, read-path only.
- **Why (the usability gap this closes):** agents had no signal that the index they were querying was stale. A file edited after `seam init` would silently produce incorrect blast-radius and change-risk answers with no indication. `seam status` had a freshness field but it wasn't surfaced inline on every graph query.
- **Staleness banner (`index_status`):** after each of the 5 graph-traversal handlers (seam_impact, seam_changes, seam_affected, seam_context, seam_trace) produces its core result, `_maybe_attach_staleness()` appends a top-level `index_status` = `{stale: true, reason: str, hint: str}` key when the index is stale. ABSENT when fresh (presence = stale signal, like `next_actions`). Risk verdicts and all existing fields are byte-identical to pre-P2 — the banner is purely additive.
- **Watcher-aware:** when a live watcher process is running (`watcher.pid` alive), file-mtime drift is NOT reported stale (the watcher self-heals). However, a watched index WITH synthesized edges IS reported stale because the watcher never recomputes synthesized edges or clusters.
- **New pure leaf `seam/analysis/staleness.py`** (stdlib-only: logging, os, sqlite3, time, pathlib): `check_staleness(conn, *, root, watcher_alive, scan_cap, respect_knob) → StalenessVerdict`. Bounded-scan over newest `SEAM_STALENESS_SCAN_CAP` files by `indexed_at DESC`. Per-process TTL cache caches ONLY stale verdicts. Never raises; conservative stale=False on any IO/DB error. Used by both the MCP banner helper and `seam status` CLI (with `respect_knob=False` so the CLI is independent of the MCP banner knob).
- **`seam status` freshness semantic change:** `seam status` now delegates to `staleness.py` (with `respect_knob=False`). Its freshness semantics changed: watcher-aware (live watcher → file drift NOT reported stale) AND synth-aware (watched index WITH synthesized edges = stale). The old heuristic ("only detects modified/added tracked files") is superseded — see Known Gotchas.
- **`tools.py` split (1671 → 697 lines):** pure mechanical refactor. `handler_common.py` (323 lines, shared helpers + `_maybe_attach_staleness`), `impact_handler.py` (615 lines, all impact shaping), `trace_handler.py` (184 lines, seam_trace). `tools.py` re-exports every handler unchanged — `seam/server/mcp.py` and all test imports are byte-identical. All server files < 1000 lines.
- **3 new config knobs:** `SEAM_STALENESS_CHECK` (`"on"`/`"off"`, default `"on"`; `"off"` = byte-identical pre-P2), `SEAM_STALENESS_SCAN_CAP` (int, default 200), `SEAM_STALENESS_TTL_SECONDS` (int, default 5). Handler-layer, read-path only — no schema change, no re-index. `seam_changes`/`seam_affected` risk verdicts byte-stable. MCP tool count stays 12. Gate: ruff + mypy clean, 3055 tests pass.
See `progress.txt`.

### Prior phase
E4 — edge provenance on `seam_impact` entries + `seam_trace` hops, and actionable truncation steer. Handler-layer, read-path only.
- **Why (two long-standing "Known Gotchas" closed):** (1) `seam_impact` entries were edge-kind-blind — a `WILL_BREAK` dependent could arrive via a hard `call`, a data-coupling `reads`, a composition `holds`, or a heuristic synthesized edge; there was no way to tell. The `edges.synthesized_by` column existed precisely to surface this but was never plumbed to the output. (2) When entries were dropped by the per-tier count cap or the byte ceiling, the response carried honest `truncated`/`byte_capped` counts but no remedy hint — agents had to guess whether to raise `limit` or `max_bytes`.
- **Edge provenance (`kind` + `synthesized_by`):** each `seam_impact` tier entry now carries `kind` (the edge kind of the final hop — full 9-kind vocab: `call | import | extends | implements | instantiates | holds | reads | writes | uses`) and `synthesized_by` (synthesis channel name when heuristic, `null` when statically extracted). `seam_trace` hops gain `synthesized_by` (they already carried `kind`). Lean mode (`verbose=False`) keeps `kind` (core field) and strips `synthesized_by` (provenance, like `resolved_by`). Gated by `SEAM_EDGE_PROVENANCE` (`"on"` default; `"off"` = byte-identical pre-E4).
- **Truncation steer (`next_actions`):** a top-level `next_actions: list[str]` of ready-to-act prose hints is attached to `seam_impact` when ≥1 entry was trimmed. Counts come from the existing `truncated`/`byte_capped` metadata; hints name the exact remedy (e.g. *"Raise limit to 17 to see 12 more WILL_BREAK upstream dependents."*). ABSENT when nothing was trimmed — presence is the unambiguous "there is more" signal. All-trimmed case emits an explicit anti-false-safe warning. The steer stays WITHIN `max_bytes` (reserved inside the byte ceiling). Gated by `SEAM_IMPACT_STEER` (`"on"` default; `"off"` = no `next_actions` key ever).
- **New pure leaf `seam/analysis/steer.py`** (stdlib-only: logging, typing): `generate_steer(…)` — trim metadata in → list of strings out; no DB/IO/config; never raises (degrades to `[]` on any error). Mirrors the leaf discipline of `byte_budget.py`, `relevance.py`, `rwr.py`. `_attach_steer` in the handler re-trims from the pre-ceiling response when needed so the steer stays within the budget in a single pass (no loop).
- **Traversal threading:** `_fetch_outgoing_edges` / `_fetch_incoming_edges` gain `synthesized_by` in their SELECT; `Reached`, `Hop`, `EdgeHop` TypedDicts gain the field; BFS propagates the winning hop's `kind`/`synthesized_by` alongside `resolved_by`. Additive — confidence rules and path selection unchanged.
- **2 new config knobs:** `SEAM_EDGE_PROVENANCE` (`"on"`/`"off"`, default `"on"`; `"off"` = byte-identical pre-E4) and `SEAM_IMPACT_STEER` (`"on"`/`"off"`, default `"on"`; `"off"` = no `next_actions` key ever). Handler-layer, read-path only — no schema change, no re-index. `seam_changes`/`seam_affected` byte-stable (call the analysis layer directly). MCP tool count stays 12. Gate: ruff + mypy clean, 3008 tests pass.
See `progress.txt`.

### Prior phase
E1-FULL — opt-in byte ceiling for `seam_impact` output (`SEAM_IMPACT_MAX_BYTES`). Handler-layer, read-path only.
- **Why (the final usability gap after E1):** the E1 null-candidate omission brought the default 1500-char window to 5/5 for the benchmark cell, but a hub symbol or a large codebase can still produce a response body that overruns an agent's context window. There was no way to set a hard budget — only the per-tier COUNT cap, which is a poor proxy because entry byte size varies widely (long signatures vs. short names). Agents budget in tokens (≈ characters), not entry counts.
- **The fix (opt-in hard ceiling on characters):** `max_bytes` param on `seam_impact` (default `SEAM_IMPACT_MAX_BYTES` = 0 = unlimited). When active, runs AFTER the per-tier count cap and E2/E3 relevance ordering and trims entries from the least-valuable end (downstream before upstream; MAY_NEED_TESTING before WILL_BREAK; intra-tier tail before front) until the serialized response fits. O(n) prefix walk (no full re-serialization per placement). `risk_summary` always reflects the honest full pre-cap total; dropped counts merge INTO `truncated` additively (the invariant holds end-to-end); `byte_capped` = `{"limit", "omitted"}` is added ONLY when the ceiling actually trimmed entries.
- **New pure leaf `seam/analysis/byte_budget.py`** (stdlib-only: json, typing): `fit_to_byte_budget(response, *, budget, …)` + `serialized_size(obj)`. Mirrors the leaf discipline of `relevance.py` and `rwr.py`. Hard-ceiling guarantee: the running total OVER-estimates (intentional +1 comma per entry) so `running <= budget` is a proven upper bound on the real serialized size. Reserve in `_apply_byte_ceiling` accounts for the trailing `byte_capped`/`truncated` metadata so the FINAL response (entries + metadata) stays within budget.
- **Hard ceiling and false-safe protection:** a budget below the irreducible envelope drops ALL entries — the CLI says "trimmed to fit --max-bytes" (NOT "no dependents found"), preventing an agent from concluding a symbol is safe to delete when its dependents were merely trimmed.
- **CLI:** `seam impact --max-bytes N` (default `SEAM_IMPACT_MAX_BYTES`). Separate footers for count-cap and byte-ceiling drops (so byte-trimmed entries are not misattributed to `--limit`). `seam_changes`/`seam_affected` unaffected (call the analysis layer directly, below the handler).
- **1 new config knob:** `SEAM_IMPACT_MAX_BYTES` (int, default 0 = unlimited; opt-in). No new MCP tool; tool count stays 12. No schema change, no migration, no re-index. Gate: ruff + mypy clean, 2890 tests pass.
See `progress.txt`.

### Prior phase
E3 — neighbor relevance ranking for `seam_context_pack` (personalized PageRank / RWR). Read-path only.
- **Why (the most under-delivered CodeGraph-gap backlog item):** `context_pack` capped callers/callees to `SEAM_PACK_NEIGHBOR_LIMIT` (10) in `min_id` order (lowest symbol id = arbitrary insertion order) — so the kept 10 were neither relevant nor alphabetical; a hot symbol could drop its most important caller purely because it was indexed late. (Note: the prior "E2/E3" #94 ranked impact TIERS; this is the separate, still-open neighbor-ranking gap the round-2 CodeGraph backlog flagged — `context()` callers were still `sorted()` alphabetical.)
- **The fix (full RWR, the principled option):** rank neighbors by **personalized PageRank** from the seed over a bounded local subgraph, BEFORE the per-file + global caps. With restart-at-seed, a neighbor woven into the seed's neighborhood (shares callers/callees → same functional cluster) outranks a globally-popular but topically-distant one — relevance-TO-THE-SEED, which raw degree can't express (this is CodeGraph's `computeGraphRelevance` approach). Key = `(-ppr_score, is_test, min_id)` (stable → `off` is a byte-identical min_id revert).
- **New pure leaf `seam/analysis/rwr.py`** (mirrors `clustering.py`): `personalized_pagerank(adjacency, seeds, …)` — graph + seed SET in → scores out; no DB/IO/config; never raises. `seeds` is a SET (the symbol's `edge_match_names`: qualified + bare) so the qualified/bare asymmetry is handled. Subgraph fetch + scoring live in `pack.py` (`_fetch_local_subgraph` bounded BFS, `_neighbor_scores`), computed ONCE per pack and reused for callers + callees.
- **3 knobs:** `SEAM_PACK_RELEVANCE_RANK` (on/off, default on; off = byte-identical), `SEAM_RWR_MAX_NODES` (500), `SEAM_RWR_MAX_DEPTH` (3). Read-path/MCP-only — no schema change, no re-index. `seam_context`'s uncapped lists stay alphabetical (out of scope). MCP tool count stays 12.
- **Scope note (honest):** `context_pack` is MCP-only (not in the neutral-benchmark capture, which uses `seam context`), so this is a PRODUCT-quality win, NOT a benchmark-score change — the benchmark frontier is tapped (per the E1 campaign re-bench; remaining gap is structure synthesis). Gate: ruff + mypy clean (92 files), 2890 tests pass.
See `progress.txt`.

### Prior phase
E1 — leaner default `seam_impact` output (omit null `best_candidate`). Handler-layer, read-path only.
- **Why (the final clicky impact-B lever):** after `uses` edges, all 5 external truth-dependents resolve at d=1, and `--lean` showed 5/5 in the benchmark's 1500-char window — but the DEFAULT (verbose) window held only 4/5. Measured root cause: in every WILL_BREAK entry `best_candidate` is null (it is the AMBIGUOUS proximity pick — null for all EXTRACTED/INFERRED entries) while `resolved_by` is non-null. The `, "best_candidate": null` suffix (~25 B/entry) was the only thing pushing the 5th truth-dependent past the window.
- **The fix (lossless, not a byte-ceiling):** drop `best_candidate` from impact entries WHEN IT IS NULL. null ≡ absent per the established null-contract, so this removes zero information; a non-null best_candidate (AMBIGUOUS entries) is always kept. `resolved_by` is kept always (genuine provenance several agents/tests rely on). A byte-ceiling / token-budget machinery was deliberately NOT built — field omission already hits 5/5.
- **New knob `SEAM_IMPACT_OMIT_NULL_CANDIDATE`** (`"on"`/`"off"`, default on; off = byte-identical revert that keeps `best_candidate: null`). Handler-layer only via `_serialize_tier_entry` (threaded through `_shape_tier_group`). No schema change, no re-index. `seam_changes`/`seam_affected` byte-stable (they call the analysis layer directly). `/api/impact` already uses lean → unaffected. MCP tool count stays 12.
- **Result (clicky impact-B, deterministic):** DEFAULT window 4/5 → **5/5** (6379 B → 5803 B), matching `--lean`. Campaign complete: 1/5 (A3) → 3/5 (E2/E3) → 4/5 (qualified seeds) → 4/5-default/5/5-lean (`uses`) → **5/5 default (E1)**. Gate: ruff + mypy clean, 2877 tests pass.
See `progress.txt`.

### Prior phase
Method-param composition edges (`uses`) — all 12 languages, extraction-time. Edge-kind vocab 8 → 9.
- **Why (the last clicky impact-B miss):** after E2/E3 + qualified seeds, 4/5 external truth-dependents surfaced; the last one (`OverlayWindowManager.showOverlay`) sat at d=2 because it RECEIVES `companionManager` as a method PARAM (not a stored field), so `holds` never captured it. `uses` edges close this: a function/method → every plain user type it references as a parameter, making a param-injected dependency a DIRECT (d=1) upstream dependent of the type.
- **All 12 languages:** per-language param-type collectors reuse the existing receiver-inference machinery (`record_<lang>_param_types` / the plain-type helpers) so `uses` and `holds` apply identical conservatism. Swift/Python/TS/JS use dedicated `collect_param_types_*`; Go/Rust/Java/C#/C++/PHP route through the shared `param_types_via_recorder` over their recorder; C uses a small local typedef/struct extractor; Ruby is untyped → naturally emits none.
- **Conservatism (same as holds):** only plain user types bind — optionals/generics/containers/builtins/pointers-stripped are refused. Never a wrong edge; never raises.
- **New knob `SEAM_PARAM_EDGES`** (`"on"`/`"off"`, default on; off = byte-identical pre-feature). Extraction-time only; requires `seam init` re-index. Kind-agnostic traversal → impact/context/trace pick `uses` up automatically. MCP tool count stays 12.
- **Result (clicky impact-B, deterministic):** `showOverlay` promoted d=2 → d=1; all 5 truth-dependents now d=1. Default-verbose window held 4/5 (closed by E1, above); `--lean` showed 5/5. Gate: ruff + mypy clean, 2873 tests pass.
See `progress.txt`.

### Prior phase
E2/E3 seam_impact output relevance shaping (issue #93) — handler-layer, read-path-only, no re-index.
- **Why (the usability gap this closes):** the 2026-06-07 neutral re-benchmark showed `holds`+synthesis+A3 edges improved RECALL (`CompanionManager` upstream 8→21) but NOT usability — for a class seed, `expand_impact_seeds` fans out to all members, so the upstream walk surfaces the class's OWN sibling methods as direct dependents. Alphabetical ordering floated these self-references above the EXTERNAL truth-dependents, and under the byte cap only 1 of 5 externals survived. The right answer was in the result, just below the cut line. E2/E3 shape the output so the recall gains become usable.
- **E3 — relevance ranking before the cap:** `seam_impact` now ranks EXTERNAL dependents ahead of the target's own container-members (and production ahead of test as the secondary key) BEFORE the per-tier cap, so `entries[:limit]` keeps the closest, highest-signal external dependents. Stable sort preserves the analysis layer's distance/alphabetical order within each group.
- **E2 — self-reference handling:** a dependent that belongs to the target's own class is a self-reference. Default `rank` mode keeps them but sorts them last (lossless — `risk_summary` still counts the full blast radius; the cap simply sheds them first). Opt-in `hide` mode drops them from entry lists and surfaces a `hidden_self_refs` count (mirrors `hidden_tests`).
- **New deep leaf `seam/analysis/relevance.py`** (pure, no DB, never raises): `owning_container`, `classify_self_ref`, `relevance_key`, `order_by_relevance`, `partition_self_refs`. Wired into `handle_seam_impact` (handler-layer) via `_compute_self_context` + `_shape_tier_group` helpers.
- **2 new config knobs:** `SEAM_IMPACT_RELEVANCE_SORT` (`"on"`/`"off"`, default on; off = byte-identical revert) and `SEAM_IMPACT_SELF_REF` (`"rank"`/`"hide"`/`"show"`, default rank).
- **Handler-only → `seam_changes`/`seam_affected` byte-stable** (they call the analysis-layer `impact()` directly). No schema change, no migration, no re-index. MCP tool count stays 12. Gate: ruff + mypy clean, 2859 tests pass.
See `progress.txt`.

### Prior phase
A3 field-access edges + field symbols (all 12 languages, extraction-time, watcher-compatible).
- **Why (the visibility gap this closes):** the call graph previously captured only invocations — a field read or write was invisible to `seam_impact`. If you renamed `Client.url` or changed its type, none of the readers/writers surfaced in the blast radius. A3 adds first-class field-access edges so data-flow through stored fields is as visible as control-flow through calls.
- **New edge kinds `reads` and `writes`:** `reads` — a symbol reads a field (`obj.field`, `self.field`); `writes` — a symbol writes/deletes it (LHS of assignment, augmented-assign `+=/-=`, `del`). Edge kind vocabulary grows from 6 to 8: `call | import | extends | implements | instantiates | holds | reads | writes`. All field-access edges carry `confidence='INFERRED'`.
- **Fields/properties are now first-class symbols:** `symbols.kind` gains `'field'`; `qualified_name='Type.field'`. Additive value in the existing TEXT column — no schema migration.
- **12 languages, extraction-time, watcher-compatible:** extraction runs per-file during parse/upsert. The watcher picks up field-access edges automatically (same boundary as `call`/`holds`). No post-pass required.
- **Conservatism contract:** `self`/`this`/`cls` resolve to the enclosing class; typed receivers use `resolve_receiver_type` (same Tier B inference); unresolvable receivers keep bare field name. NEVER emit a wrong edge — undefined/generic/chained receivers silently omit the edge.
- **New config knob:** `SEAM_FIELD_ACCESS_EDGES` (`"on"`/`"off"`, default `"on"`); extraction-time only; `"off"` = byte-identical to pre-A3.
- **New read-path view:** `seam/query/context.py` adds `field_readers` and `field_writers` lists to the context result; `seam/server/tools.py` `handle_seam_context` surfaces them. Kind-agnostic BFS traversal picks up `reads`/`writes` automatically — `seam_impact`, `seam_trace`, `seam_context` callers/callees include them with no per-tool change.
- **New leaf modules (1000-line split):** `seam/indexer/field_access.py` (Python extractor + facade re-exports), `field_access_ts.py` (TypeScript/JS), `field_access_go_rust.py` (Go/Rust), `field_access_ext.py` (Java/C#), `field_access_c_cpp.py` (C/C++), `field_access_ext2.py` (Ruby/PHP), `field_access_php_swift.py` (PHP emission + Swift).
- **MCP tool count stays 12.** `seam_context` gains `field_readers`/`field_writers` in its output. No new tool.
See `progress.txt`.

### Prior phase
Edge-synthesis whole-graph post-pass + gate-able recall harness (PRD #83, schema v11 → v12).
- **Why (the recall gap this closes):** static extraction never sees runtime polymorphism. A call to a base/interface method, an element invoked out of a collection, or a handler fired by an event-bus has no statically-resolvable call edge — so `seam_impact` on the *implementation* showed empty upstream. Edge synthesis is a deliberate **over-approximation** that runs once over the whole indexed graph and writes the edges that static parsing structurally cannot infer. Cost of a false-positive synthesized edge (slightly wider blast radius) is accepted as far cheaper than a missed dependency.
- **A2 — interface→implementation override fan-out** (`seam/analysis/synthesis.py`): links every base/interface method to **every** same-name implementation as a synthesized `call` edge. Deliberately NOT MRO/type-resolved — it fans out to all candidates (bounded by the fanout cap). When a base method changes, all implementors surface upstream.
- **A1a — closure-collection dispatch** (`seam/analysis/synthesis_channels.py`): when a collection is both iterated AND has its elements invoked, the collected callables (paired to their append/registration sites by field name) are linked to the invocation site.
- **A1b — event-emitter dispatch** (`seam/analysis/synthesis_channels.py`): registrar verbs (`on`/`subscribe`/`addListener`…) are matched to dispatcher verbs (`emit`/`dispatch`/`publish`…) keyed by the event-string literal, linking handler ↔ emit site.
- **Bridge `seam/indexer/synthesis_index.py`** (mirrors `cluster_index.py` / `embedding_index.py`): reads all symbols + edges, runs the synthesis engine, writes synthesized edges in **one transaction** under a synthetic `:synthesis:` row in `files`. Idempotent (delete-then-insert that synthetic file's edges each run). Never raises; returns `-1` on error (CLI surfaces "failed", exit still 0).
- **Schema v11 → v12:** single additive migration (`_run_migration_v11_to_v12`) adds `edges.synthesized_by TEXT NULL`. Auto-runs on `connect()`; idempotent; never raises. `synthesized_by IS NULL` ⟹ statically extracted; a channel-name string ⟹ synthesized. Provenance is derived: `synthesized_by IS NOT NULL` ⟹ heuristic. Pre-v12 rows keep `synthesized_by=NULL`; a full `seam init` re-index is needed to populate synthesized edges.
- **Synthesized edges** carry `kind='call'`, `confidence='INFERRED'`, `synthesized_by=<channel>`. The read-path traversal is **kind-agnostic**, so `seam_impact` / `seam_context` / `seam_trace` traverse them automatically (exactly like `holds` edges) — no per-tool change.
- **Gated like clusters:** runs in `seam init` (always) and `seam sync` (gated on `graph_changed`, or `--force-synthesis`). NOT run by the per-file watcher.
- **P1 recall harness** (`tests/eval/`): fixture repo + SHA-stamped `golden.json` + recall@K / MRR metric, wired into the gate via `test_recall_regression.py`. `make eval` runs it; `make eval-generate` regenerates the golden file.
- **3 new config knobs:** `SEAM_EDGE_SYNTHESIS` (`"on"`/`"off"`, default `on`; off = byte-identical pre-synthesis), `SEAM_SYNTHESIS_FANOUT_CAP` (default 40), `SEAM_SYNTHESIS_MAX_SOURCE_BYTES` (default 50 MB total source-load budget; 0 = unlimited).
- **MCP tool count stays 12.** No new tools. Gate: all tests pass (2498).
See `progress.txt`.

### Prior phase (Tier B receiver capture + receiver-type inference)
Tier B receiver capture + extraction-time receiver-type inference (schema v9 → v10).
- **Root cause (the real fix):** Tier A bridged the qualified/bare asymmetry at read time. Tier B fixes it at the source: the extractor now captures the raw receiver expression in `edges.receiver` (v9→v10 migration) AND infers its type to emit a qualified `Type.method` target on the edge itself. Once a call is stored as `Client.send`, it joins the symbol row `Client.send` exactly — no read-time bridging needed for that edge.
- **Schema v9 → v10:** single additive migration (`_run_migration_v9_to_v10`) adds `edges.receiver TEXT NULL`. Auto-runs on `connect()`; idempotent; never raises. Pre-v10 rows keep `receiver=NULL` (same null-contract as Phase 4/5 fields) — a full `seam init` re-index is needed to backfill receiver + qualified targets.
- **B1 — receiver column + Python receiver capture:** `edges.receiver` added to schema and `Edge` TypedDict. Python call-edge extractor captures raw receiver text (e.g. `self`, `client`) into `Edge.receiver`. Import and bare-identifier edges remain `receiver=None`.
- **B2 — receiver capture across remaining 11 languages:** all language extractors (TS/JS, Go, Rust, Java, C#, Ruby, C, C++, PHP, Swift) capture receiver text into `Edge.receiver` on attribute/member calls.
- **B3 — TS/JS member-expression call edges (recall hole fix):** previously only bare identifier calls were indexed for TypeScript/JavaScript; `obj.method()` patterns were silently dropped. B3 adds `member_expression call_expression` handling to the TS/JS extractor — a major recall improvement. Controlled by `SEAM_TYPE_INFERENCE` (on by default).
- **B4 — scope-inference module + Python/TS/JS receiver-type inference:** new leaf `seam/indexer/graph_scope_infer.py` provides `resolve_receiver_type()` — the core two-layer scope model (class-level field/property pre-scan + per-function param/local bindings) for Python and TS/JS. When a receiver type is confidently inferred, the extractor emits `target_name = "Type.method"` instead of the bare method name. Conservatism contract: NEVER emit a wrong edge — refuse on optionals, containers, generics, chained/unknown receivers.
- **B5 — receiver-type inference for remaining families + Swift static calls:** two more inference leaf modules (`graph_scope_infer_ext.py` for Java/C#/Ruby; `graph_scope_infer_ext2.py` for Go/Rust/C/C++/PHP) plus Swift static class call patterns (extension of `graph_swift_infer.py`).
- **B6 — instantiates edges across all 12 languages:** `new_expression` / struct-literal / composite-literal nodes now emit `kind="instantiates"` edges (e.g. `new Foo()`, `Foo{}`, `Foo { ... }`, PascalCase bare call in Swift). The `instantiates` kind is now part of the closed edge-kind vocabulary alongside `call`, `import`, `extends`, `implements`.
- **1 new config knob:** `SEAM_TYPE_INFERENCE: "on" | "off"` (default `"on"`) — master switch for extraction-time receiver-type inference in Python and TS/JS. Set to `"off"` to revert to bare-identifier-only targets (byte-identical to pre-Tier-B). Swift uses its own independent `SEAM_SWIFT_TYPE_INFERENCE` knob.
- **MCP tool count stays 11.** No new tools. Read path (Tier A names.py) consumes qualified targets automatically — no per-tool changes. Gate: all tests pass.

### Prior phase (Tier A name-resolution)
Tier A name-resolution (read-path-only bridge between qualified symbol names and bare call-edge targets).
- **Root cause fixed (read-path only, no schema change):** Seam stores method symbols as `Class.method` but call-edge `target_name` as the bare identifier `method`. This asymmetry caused every method context/impact to show empty upstream. Tier A patches this entirely at read time.
- **New leaf module `seam/query/names.py`:** five pure functions — `bare_name`, `is_container_symbol`, `get_member_names`, `edge_match_names`, `resolve_query_to_defs`, `expand_impact_seeds`. Imports only stdlib + `seam/config`. Pattern mirrors `seam/query/clusters.py`.
- **Slice 1 — qualified↔bare bridging in `engine.py` context():** edge lookups now search `[name, bare_name]` so a call stored as bare `method` joins against the qualified symbol `Class.method`.
- **Slice 2 — all-definitions aggregation:** `context()` resolves to ALL matching symbol defs (bare-name suffix scan via `resolve_query_to_defs`), not just the first homonym. A bare query `speakText` finds `TTS.speakText`, `AudioPlayer.speakText` etc. and merges their callers/callees. `ambiguous` flag is set when >1 definition is found.
- **Slice 3 — class→member expansion in `context()` + `query()`:** when the seed is a class/interface/struct, `edge_match_names` fans out to all member bare names so callers of any method of the class are included. Bounded by `SEAM_NAME_EXPANSION_CAP` (default 50).
- **Slice 4 — seed-expansion in `seam_impact` + `seam_trace`:** `expand_impact_seeds` provides the same qualified+bare (or class+members) seed list to the BFS `walk()`, so impact analysis now shows upstream callers for qualified method names and containers.
- **2 new config knobs:** `SEAM_NAME_EXPANSION_CAP` (default 50), `SEAM_BARE_RESOLVE_CAP` (default 25). No schema change, no migration, no re-index needed; MCP tool count stays 11. Gate: all tests pass.
See `progress.txt`.

### Prior phase (Semantic search)
- **New `[semantic]` extra** (`pip install 'seam-code[semantic]'`) — pulls `fastembed>=0.4` (ONNX/CPU, no torch). Base install unchanged; gate stays offline.
- **3 new modules:** `seam/analysis/embeddings.py` (fastembed wrapper), `seam/indexer/embedding_index.py` (index orchestration), `seam/query/semantic.py` (read path: RRF + cosine + model-mismatch guard).
- **Schema v6→v7:** new `embeddings(symbol_id PK, model, dim, vector BLOB)` table. Auto-migrated on `connect()`; no backfill — populated only by `seam init --semantic`.
- **5 new config knobs:** `SEAM_SEMANTIC` (off/on, default off), `SEAM_EMBED_MODEL` (default `BAAI/bge-small-en-v1.5`), `SEAM_SEMANTIC_LIMIT` (default 20), `SEAM_SEMANTIC_SCAN_CAP` (default 20000), `SEAM_RRF_K` (default 60).
- **Hybrid path in `engine.py`:** `search()` uses RRF-merged result set (FTS snippets preserved); `query()` injects semantic symbols as seeds (score=0.5) before 1-hop expansion. `_is_hybrid_enabled` check is per-query (one COUNT — negligible); warns once per process if `SEAM_SEMANTIC=on` but no embeddings.
- **CLI surfaces:** `seam init --semantic`, `seam sync --semantic`, `seam status` (embeddings row + model mismatch indicator), `seam search/query --no-semantic` (passes `semantic=False` param — no config mutation).
- **MCP transparent:** `seam_search`/`seam_query` auto-hybrid via engine.py. No new tool, count stays 11. Optional `semantic` param (default `true`) lets callers force keyword-only.
- **Benchmark:** `benchmarks/semantic_recall.py` (15 concept queries, 8 keyword-friendly + 7 vocabulary-gap), `make bench-semantic`. NOT part of gate — requires fastembed + model.
- **Gate:** 1747 tests, 5 skipped (real-model behind `pytest.importorskip("fastembed")`), 0 failed. Fully offline.
See `progress.txt`. Next: v0.1.0 — publish to PyPI as `seam-code`.

### Prior phase (CLI-only completion + optional-MCP install profile)
CLI-only completion + optional-MCP install profile.
- **3 new CLI commands** — `seam query` / `search` / `context` (seam/cli/read.py) over the existing
  transport-agnostic handlers; query SQLite directly → the FULL feature set is usable with NO MCP server.
- **`mcp` is now an OPTIONAL extra** (`[project.optional-dependencies] server`), not a core dep. `mcp` is
  imported lazily inside `start()`; `seam start` without it exits with an install hint. `pip install seam-code`
  = CLI only; `pip install 'seam-code[server]'` adds the server. (`mcp` kept in the dev group for tests.)
- **Distribution bug fixed (found via a real wheel install):** `seam init` read `docs/database/schema.sql`
  (outside the package) → crashed on any `pip install`. Schema now force-included at `seam/_data/schema.sql`,
  loaded packaged-first with a dev fallback. Guard test added.
- 1504 tests passing; gate green. Plan: `.claude/tasks/cli-query-context-search.md`.
See `progress.txt`. Next: v0.1.0 — publish to PyPI as `seam-code`.

### Prior phase
`seam install` (roadmap item 8) — one-command MCP wiring for Claude Code / Cursor / Codex.
- **New `seam/installer/` package** + `seam/cli/install.py`: `seam install` / `seam uninstall`.
  AgentTarget ABC; one target per agent. Claude → `.mcp.json` (project) / `~/.claude.json` `projects.<root>`
  (user), entry has `type:"stdio"`. Cursor → `.cursor/mcp.json` (no `type`). Codex → `~/.codex/config.toml`
  `[mcp_servers.seam]` (TOML via new dep `tomlkit`; user scope only).
- **Idempotent + safe:** deep-equal → `unchanged` (no write); atomic temp+rename; `.backup` on corrupt config;
  preserves other servers. `--target claude|cursor|codex|all`, `--location project|user`, `--print-config`, `--json`.
- Command written = absolute resolved `seam` path (via `sys.argv[0]`) + `["start", <root>]`. CLI-only — **no new
  MCP tool** (server stays read-only); tool count stays 10. No schema change, no migration.
- 1492 tests passing; gate green. Plan: `.claude/tasks/seam-install.md`.
See `progress.txt`. Next: v0.1.0 release prep — actually publish to PyPI as `seam-code`; add more agent targets
(one file each) as needed. Kotlin still parked behind a robust grammar.

### Prior phase
Agentic-readiness hardening (post-Phase-10) — 3 critical audit fixes.
- **Distribution renamed `seam` → `seam-code`** in pyproject (PyPI `seam` is taken by Seam Labs' SDK).
  Import package + console command stay `seam`. Not yet published; README install is from-source.
- **MCP error/not-found contract unified** via `_finalize` (seam/server/mcp.py): app errors now
  `isError=True` (`"CODE: message"`), not-found → `{"found": false}`. See the Known Gotchas entry.
- **`seam init` writes `.seam/.gitignore` (`*`)** so `seam_changes` stops reporting its own DB files.
- Source: an end-to-end agentic-readiness audit (real MCP stdio client on a fresh repo).

### Prior phase
Phase 10 complete — Swift support (11 → 12 languages). **Kotlin evaluated and deferred.**
- **New grammar:** tree-sitter-swift 0.7.3 (parses cleanly against tree-sitter 0.25.2, has_error=False).
  Entry point is `tree_sitter_swift.language()`.
- **Kotlin deferred:** the only available grammar (tree-sitter-kotlin 1.1.0) emits ERROR nodes on common
  constructs (interfaces, objects, classes-with-constructor) and recovered ~1 of 6 symbols on a realistic
  file — would silently drop most code. Revisit when a robust grammar ships. See ADR-009.
- **New extractor module:** graph_swift.py (mirrors graph_go_rust.py). class/struct/actor/extension→class,
  enum→type, protocol→interface, methods→Type.method; bare-identifier calls only; /// and /** */ docstrings.
- Swift wired into signatures_ext.py (visibility from access modifiers, @attributes as decorators) and
  imports_ext.py (import-mapping extraction; resolution returns [] — modules not file-resolvable in-repo).
- No schema change, no migration, MCP tool count stays 10.
- 1454 tests passing; gate green.
See `progress.txt` for session history. Next: roadmap item 8 (`seam install`) / v0.1.0 release prep.

### Prior phase
Phase 9 complete — language expansion (5 → 11 languages): Java, C#, Ruby, C, C++, PHP added.
- New grammars: tree-sitter-{java,c-sharp,ruby,c,cpp,php}; per-family extractor modules
  (graph_java_csharp.py, graph_c_cpp.py, graph_ruby.py, graph_php.py) mirroring graph_go_rust.py.
- New leaf modules signatures_ext.py + imports_ext.py (Phase 4 enrichment + Phase 5 import mappings
  for the new langs; TypedDicts re-declared to avoid circular imports, guarded by drift tests).
- Kind mapping uses the closed vocabulary; import + bare-identifier call edges only. See ADR-008.

### Prior phase
Phase 8 complete — lean output (`verbose`) + `seam_impact` summary tier shipped.
- **Lean output (#1):** `verbose: bool = True` on the enrichment-carrying handlers
  (seam_context, seam_trace, seam_impact, seam_context_pack). `verbose=False` strips the 6 heavy
  fields (decorators, is_exported, visibility, qualified_name, resolved_by, best_candidate) via
  the shared `_apply_verbosity` helper in tools.py — keeps signature + core fields. seam_search
  AND seam_query are enrichment-free → NO verbose flag (would be a no-op). CLI: `--lean` on
  impact/trace/pack (query/context have no CLI command — MCP-only).
- **Impact summary (#2):** seam_impact returns `risk_summary` {direction: {tier: count}} over the
  FULL pre-cap (post-include_tests) set, caps each tier at `SEAM_IMPACT_MAX_RESULTS` (default 25),
  reports `truncated` {direction: {tier: omitted}}, and accepts `limit` (0 = unlimited). The cap
  applies BY DEFAULT — this fixes the hub-symbol 30k-token blast (init_db: 30k → 4.5k tokens).
- All 3 CLI impact modes (--json/--quiet/Rich) route through `handle_seam_impact` so --lean/--limit
  apply uniformly; Rich shows a truncation footer, quiet signals truncation on stderr.
- No schema change, no migration, MCP tool count stays 10. Benchmark: 83.4%/77.6% → 91.8%/88.7%.
- 1107 tests passing; gate green.
See `progress.txt` for session history.

### Prior phase (Phase 7)
Phase 7 complete — one-shot `seam sync` with gated cluster recompute shipped.
- New leaf module `seam/indexer/sync.py`: `sync(conn, root, *, …) → SyncResult`.
- Filesystem reconcile (NOT git): mtime pre-filter → SHA-1 confirm; re-index only changed/added
  files, delete removed ones. Reuses walk_project + index_one_file + sha1 + delete_file.
- Delete is existsSync-guarded (roadmap §6.1): a tracked file is removed ONLY once it genuinely no
  longer exists on disk — a transient walk hiccup / wrong-dir / --db-dir mismatch can't wipe the index.
- FULL cluster recompute (clusters are global Louvain — no correct incremental update), GATED on
  `graph_changed = (added+modified+removed) > 0`; skipped when nothing changed. `--force-clusters`
  recomputes anyway (covers the live-watcher-already-indexed case → kills the stale-clusters gotcha).
- `cluster_count`: None = recompute skipped, -1 = recompute RAN but FAILED (index_clusters sentinel,
  surfaced as "failed" + warning, mirroring `seam init`), ≥0 = success. `clusters_recomputed` is
  True only on success.
- New CLI command `seam sync [path]` with --json / --quiet / --force-clusters. CLI-only —
  NO new MCP tool (MCP server stays read-only; tool count stays 10).
- No schema change, no new deps, no migration, no new config knobs (reuses SEAM_CLUSTER_*).
- 1031 tests passing; gate green.
See `progress.txt` for session history. Next: roadmap item 8 (`seam install`) / v0.1.0 release prep.

## MCP Tools
- `seam_schema` — index capability and schema introspection (Phase 11 P1.1). Returns schema/version metadata, freshness, counts, grouped breakdowns, optional capability flags, tool guidance, recommended next calls, and structured warnings. `verbose=true` adds table/column metadata for diagnostics. Pure-read; no schema change, no migration.
- `seam_snippet` — exact bounded source retrieval (Phase 11 P1.2). Returns live source text for one indexed symbol by UID, symbol, symbol+file, or file+line, with root containment, ambiguity candidates, optional same-file neighbor hints, freshness warnings, and truncation metadata. Use after `seam_search`/`seam_query` when you need the implementation body without broad graph context.
- `seam_graph_search` — typed structural graph search (Phase 11 P1.3). Returns paginated symbol records by kind/name/file/language/edge/degree/preset filters, with UID chaining, root-relative paths, edge-aware degree counts, optional capped one-hop previews, and structured warnings. Use before `seam_snippet`/`seam_context` when you know the graph shape you want, not the exact symbol.
- `seam_architecture` — bounded repository architecture briefing (Phase 11 P1.4). Returns identity/freshness, scoped counts, language/physical/cluster summaries, entry points, fan-in hotspots, fan-out orchestrators, boundaries, edge mix, optional-surface status, warnings, truncation metadata, and concrete next calls. Use after `seam_schema` to orient before precise graph/context/snippet/impact calls.
- `seam_query` — FTS5 + 1-hop graph expansion (Phase 0); OR-join + rescore since Phase 3; **hybrid semantic+FTS5 via RRF when `SEAM_SEMANTIC=on` and embeddings exist** (Semantic phase); optional `semantic: bool = True` param to force keyword-only
- `seam_context` — symbol 360-degree view, enriched with cluster_id/label/peers (Phase 2) + signature/decorators/is_exported/visibility/qualified_name (Phase 4); **Tier A: resolves bare/qualified/class names and aggregates all matching defs** (callers/callees merged across homonyms; `ambiguous=true` when >1 def found; class name fans out to all member callers); **traverses `holds` composition edges** (owning classes that store this type as a field appear as callers); **A3: also returns `field_readers` and `field_writers` lists** — symbols with `reads`/`writes` edges pointing to/from this symbol (the typed split; `callers` remains the inclusive view via kind-agnostic BFS); **P2: attaches `index_status` banner when stale (additive only; gated by `SEAM_STALENESS_CHECK`)**
- `seam_search` — full-text FTS5 search (Phase 0); OR-join + rescore + fuzzy fallback since Phase 3; signature is FTS-searchable (Phase 4); **hybrid semantic+FTS5 via RRF when `SEAM_SEMANTIC=on` and embeddings exist** (Semantic phase); optional `semantic: bool = True` param; FTS snippets preserved for FTS hits, "" for semantic-only hits
- `seam_impact` — blast-radius analysis by risk tier (Phase 1); each entry now carries `resolved_by` (provenance) and `best_candidate` (proximity pick on AMBIGUOUS) since Phase 5; Phase 8 adds `risk_summary` (full per-tier counts), a per-tier `limit` cap (default 25, 0=unlimited), and `truncated`; **Tier A: `expand_impact_seeds` bridges qualified↔bare and fans out class seeds to member names before BFS walk** (a container seed now emits BOTH bare `method` AND qualified `Class.method` member forms, so Tier-B-qualified member-call edges match at d=1 — fixes direct injectors that previously landed at d=2); **Tier B: traverses `instantiates` edges alongside call/import/extends/implements; qualified Type.method targets resolve exactly**; **traverses `holds` composition edges** (a held type's blast radius includes all owning classes at d=1); **A3: traverses `reads`/`writes` field-access edges automatically** (kind-agnostic BFS — field symbols and their readers/writers appear in the blast radius); **E2/E3: ranks EXTERNAL dependents ahead of the target's own container-members (self-references) BEFORE the per-tier cap so external dependents survive truncation** (handler-layer, read-path-only, no re-index; `SEAM_IMPACT_RELEVANCE_SORT=off` reverts byte-identically; `SEAM_IMPACT_SELF_REF=hide` drops self-refs entirely and adds a `hidden_self_refs` count); **E1: omits `best_candidate` from an entry when it is null** (default `SEAM_IMPACT_OMIT_NULL_CANDIDATE=on`; lossless leaner output — null ≡ absent; non-null best_candidate on AMBIGUOUS entries is kept; `resolved_by` always kept; `=off` restores `best_candidate: null`); **E1-FULL: `max_bytes` param (default `SEAM_IMPACT_MAX_BYTES` = 0 = unlimited) — opt-in hard byte ceiling that trims entries from the least-valuable end (downstream before upstream; MAY_NEED_TESTING before WILL_BREAK) until the serialized response fits; dropped counts are merged INTO `truncated` additively; `byte_capped` = `{"limit": int, "omitted": int}` is added ONLY when the ceiling fired (absent when unlimited or nothing trimmed); `risk_summary` always reflects the honest full pre-cap total; a budget smaller than the irreducible envelope drops ALL entries (empty list + non-empty risk_summary = "trimmed to nothing", NOT "no dependents"); seam_changes/seam_affected are NEVER affected**; **E4: each tier entry now carries `kind` (edge kind of the final hop: full 9-kind vocab `call | import | extends | implements | instantiates | holds | reads | writes | uses`; always present, kept in lean mode) and `synthesized_by` (synthesis channel name when heuristic, `null` when statically extracted; stripped in lean mode like `resolved_by`; `null` retained in verbose mode as the meaningful "static edge" signal); a top-level `next_actions: list[str]` of ready-to-act truncation hints is attached when ≥1 entry was trimmed (ABSENT when nothing trimmed); gated by `SEAM_EDGE_PROVENANCE` (default on) and `SEAM_IMPACT_STEER` (default on)**; **P2: attaches a top-level `index_status` = {stale: bool, reason: str, hint: str} banner when the index is stale (ABSENT when fresh — presence = stale signal); risk verdicts inside the response are byte-stable; gated by `SEAM_STALENESS_CHECK` (default on)**
- `seam_trace` — shortest call/dependency path (Phase 1); each hop now carries `resolved_by` and `best_candidate` since Phase 5; **Tier A: source/target seeds use the same qualified↔bare expansion as seam_impact**; **Tier B: hop `kind` may now be `instantiates`**; hop `kind` may also be `holds` (class composition path); **A3: hop `kind` may also be `reads` or `writes`** (field-access path); **E4: each hop now carries `synthesized_by` (synthesis channel name when heuristic, `null` when statically extracted; stripped in lean mode; gated by `SEAM_EDGE_PROVENANCE`)**; full edge-kind docstring corrected to the 9-kind vocabulary; **P2: attaches `index_status` banner when stale (same contract as seam_impact; gated by `SEAM_STALENESS_CHECK`)**
- `seam_changes` — git diff → changed symbols → risk level (Phase 1); --stdin on CLI; **P2: attaches `index_status` banner when stale (additive only — risk verdicts are byte-stable; gated by `SEAM_STALENESS_CHECK`)**
- `seam_why` — semantic comments WHY/HACK/NOTE/TODO/FIXME (Phase 1b)
- `seam_clusters` — list functional areas or drill into one cluster (Phase 2)
- `seam_affected` — changed files → impacted test files via reverse-dependency traversal (Phase 3); **P2: attaches `index_status` banner when stale (additive only — impacted-test list is byte-stable; gated by `SEAM_STALENESS_CHECK`)**
- `seam_context_pack` — enriched context bundle: target + NeighborRef callers/callees + WHY + cluster peers + truncated counts (Phase 6); **E3: callers/callees are ranked by personalized-PageRank (RWR) relevance to the seed BEFORE the per-file + global caps**, so when the lists are capped the kept neighbors are the most relevant (woven into the seed's local neighborhood), not the lowest-symbol-id ones. Internal ranking only — NeighborRef shape and `truncated` semantics are unchanged. Gated by `SEAM_PACK_RELEVANCE_RANK` (default on; off = byte-identical min_id order)
- `seam_flows` — execution flows: list entry points (call-graph roots ranked by downstream reach), or expand one entry's depth/breadth-capped, cycle-safe forward call-chain tree (Flows). No arg → `{entry_points:[{name,kind,file,reach}]}`; with `entry` → a Flow tree (or `{found:false}`). Pure-structural, no LLM.
- `seam_structure` — whole-repo directory/file/container structure tree (Tier D11). Returns a nested dir/file/container/function tree built from the index. Methods roll up into their owning container's `members` count. No args. Each node: `{kind, name, path, symbol_count, area, children, members}`. Pure-read, no schema change.

There are **sixteen MCP tools** (`seam_architecture` is the newest — Phase 11 P1.4). The graph-record tools return the five Phase 4 enrichment fields where available: `signature`, `decorators`, `is_exported`, `visibility`, `qualified_name`. Fields are `null` (not absent) for pre-v5 rows or unsupported scenarios — callers treat `null` as "unknown". (`seam_flows`, `seam_structure`, `seam_schema`, `seam_snippet`, `seam_graph_search`, and `seam_architecture` are exceptions: they do NOT carry the full Phase 4 enrichment field set as graph records.)

**Tier B edge enrichment:** The edge kind vocabulary now includes `instantiates` (added in Tier B B6) and `holds` (composition edges, added in the composition feature) alongside `call`, `import`, `extends`, `implements`. `seam_impact`, `seam_context`, and `seam_trace` traverse all edge kinds including `instantiates` and `holds`. `seam_trace` hop `kind` may be `instantiates` or `holds`. Edges with a confidently inferred receiver type now carry a qualified `Type.method` target directly in the DB — `seam_context` and `seam_impact` resolve these with higher confidence (EXTRACTED when unique, no read-time bridging needed for those hops). The raw receiver text is stored in `edges.receiver` (v10 column, NULL for pre-v10 rows and for bare/import/holds edges).

**Method-param `uses` edges:** The edge kind vocabulary now further includes `uses` — a function/method references a plain user type as a **parameter** in its signature (`f(x: T)` → `f` uses `T`). This brings the total to **9 edge kinds**: `call | import | extends | implements | instantiates | holds | reads | writes | uses`. It complements `holds` (stored composition: fields + constructor params) with signature-level coupling, so a param-injected dependency (e.g. a method that receives a service it does not store) is a DIRECT (d=1) upstream dependent of the type. Same conservatism as `holds` (only plain user types bind; optionals/generics/containers/builtins refused). All 12 languages (Ruby is dynamically typed → naturally emits none; C extracts typedef/struct param types). All `uses` edges carry `confidence='INFERRED'`. Kind-agnostic traversal → `seam_impact`/`seam_context`/`seam_trace` pick them up automatically. Extraction-time only; gated by `SEAM_PARAM_EDGES` (default on); requires `seam init` re-index. Higher-volume than `holds` → blast-radius verdicts widen. No new MCP tool; count stays 12.

**A3 field-access edges:** The edge kind vocabulary now further includes `reads` (field read) and `writes` (field write/delete), bringing the total to **8 edge kinds**: `call | import | extends | implements | instantiates | holds | reads | writes`. `symbols.kind` gains `'field'` (first-class indexed field/property symbols, qualified as `Type.field`). All `reads`/`writes` edges carry `confidence='INFERRED'`. Because the traversal layer is kind-agnostic, `seam_impact`, `seam_context`, and `seam_trace` pick them up automatically — no per-tool change. `seam_context` additionally surfaces `field_readers` (list of symbols with `reads` edges to this symbol) and `field_writers` (list of symbols with `writes` edges to this symbol) as a typed complement to the inclusive `callers` list. Extraction-time only; controlled by `SEAM_FIELD_ACCESS_EDGES` (default on). Requires `seam init` re-index to populate. No new MCP tool; count stays 12.

**Edge synthesis (edge-synthesis phase):** the whole-graph synthesis post-pass writes synthesized edges with `kind='call'` and `confidence='INFERRED'`, tagged in the v12 `edges.synthesized_by TEXT NULL` column (NULL = statically extracted; a channel-name string = synthesized; provenance is heuristic when `synthesized_by IS NOT NULL`). Because the read-path traversal is **kind-agnostic**, `seam_impact`, `seam_context`, and `seam_trace` traverse synthesized edges automatically (exactly like `holds` edges) — so an interface-method change surfaces all implementations, and an event-bus / closure-collection dispatch surfaces its handlers. No new MCP tool; tool count stays 12. **E4 (closed follow-up):** `synthesized_by` is now surfaced in `seam_impact` tier entries and `seam_trace` hops when `SEAM_EDGE_PROVENANCE=on` (default). Agents can distinguish a heuristic synthesized INFERRED edge from a statically-extracted one by checking `synthesized_by` (`null` = static; channel name = synthesized). `kind` is also surfaced on impact entries (it was already on trace hops, but docstrings corrected to the full 9-kind vocabulary).

**Semantic hybrid (Semantic phase):** `seam_search` and `seam_query` auto-merge FTS5 candidates with semantic (cosine) candidates via Reciprocal Rank Fusion (RRF, k=60) when BOTH conditions hold: `SEAM_SEMANTIC=on` AND embeddings exist for the configured model. A keyword-only index behaves byte-identically to pre-Semantic. The `semantic` param (default `true`) can be passed to force keyword-only from a tool call.

**Phase 8 lean output:** `seam_context`, `seam_trace`, `seam_impact`, `seam_context_pack` accept `verbose: bool = True`. With `verbose=False` the heavy fields (decorators, is_exported, visibility, qualified_name, resolved_by, best_candidate, **and E4's synthesized_by**) are **absent** (not null) — `signature` + core fields are always kept. `kind` is a core field — it is NOT stripped in lean mode. `verbose=True` is byte-identical to pre-Phase-8 (EXCEPT `seam_impact`, which always adds `risk_summary`/`truncated`, caps by default, E1 omits null `best_candidate`, and E4 adds `kind`/`synthesized_by` and `next_actions` when applicable). `seam_query` and `seam_search` carry no enrichment → no `verbose` flag.

`seam_impact` and `seam_trace` additionally return `resolved_by` and `best_candidate` on each entry/hop since Phase 5. Both are `null` for pre-v6 rows or when resolution context is unavailable (same null-contract as Phase 4 fields).

`seam_context_pack` returns `truncated: {callers, callees, comments}` counts of entries dropped by caps. When a neighbor name has no indexed declaration it is silently skipped (not an error). Use `seam_impact` for the full blast radius when the pack is truncated.

## Known Gotchas
- **`sqlite-vec` is a native C extension — macOS system Python disables extension loading; use python.org/brew/uv builds (WS2b):** `conn.enable_load_extension(True)` is disabled at compile time on the macOS system Python (the one bundled with the OS at `/usr/bin/python3`). python.org builds, Homebrew Python, and uv-managed Pythons all support it. When the probe fails (`probe_vec_extension` returns False), `index_vec` skips the build silently (returns 0) and `_try_vec_path` skips the ANN tier (returns None), falling through to the mmap/SQL path — the result is correct, just without ANN acceleration. The probe failure is logged at WARNING level so the operator knows why the ANN tier is inactive. Seam itself is always installed via uv, so macOS users running `uv run seam` are unaffected; the gotcha only applies to developers calling Seam programmatically from a system-Python virtualenv.
- **The vec0 KNN table goes STALE after watcher edits — rebuild with `seam init --semantic` / `seam sync --semantic` (WS2b):** like clusters, synthesized edges, and the mmap artifact, the `vec_embeddings` vec0 table is written only by `seam init --semantic` / `seam sync --semantic`, NOT by the per-file watcher. After live edits that add/remove symbols and are picked up by the watcher, the staleness token (`count:max_id` in `vec_meta`) will diverge from the live DB state; `_try_vec_path` detects this mismatch and returns None (falls through to mmap/SQL) — no wrong results. Run `seam sync --semantic` (incremental) or `seam init --semantic` (full rebuild) to refresh the vec0 table. The staleness fallback is transparent and correct. (Note: until sqlite-vec ships approximate indexing, the mmap fallback is actually faster anyway — see the benchmark gotcha above.)
- **`SEAM_VEC_ANN=off` (default) is byte-identical to pre-WS2b — no probe, no vec0 table, no overhead (WS2b):** when `SEAM_VEC_ANN=off`, `index_vec` returns 0 immediately (no probe, no DDL, no rows written), `_try_vec_path` is never called, and `semantic_candidates` goes directly to `_try_mmap_path`. There is zero overhead, no `vec_embeddings` table created, and no `vec_meta` row written. The `[semantic-ann]` extra can be installed without activating the ANN tier — `SEAM_VEC_ANN=on` must be set explicitly to enable it. Use `"off"` to revert to the pre-WS2b mmap/SQL-only path for debugging, CI isolation, or environments where the extension cannot load cleanly.
- **sqlite-vec v0.1.9 has NO approximate-nearest-neighbour index — the vec0 tier is currently SLOWER than brute-force at all scales (WS2b):** A real-scale benchmark (synthetic 384-dim float32, 10k–250k rows, 20 queries, recall@10) showed sqlite-vec v0.1.9 is ~5× **slower** than the numpy matmul mmap path with perfect recall@10 = 1.000 at every scale. This is because sqlite-vec's `vec0` virtual table currently performs **exact** brute-force KNN — there is no HNSW, no IVF, no approximate index. `SEAM_VEC_ANN=on` is therefore NOT recommended for performance. The feature is a **forward-compatible scaffold**: when sqlite-vec ships a true approximate index the vec0 tier will transparently outperform brute-force without any Seam code change or re-index of embeddings. Keep `SEAM_VEC_ANN=off` (the default) until that version ships.
- **`SEAM_VEC_ANN_MIN_ROWS` (default 50 000) is a forward-compat DDL gate, not a performance crossover (WS2b):** the threshold was designed as the row count where a true ANN index would start paying off. Because sqlite-vec v0.1.9 has no approximate index, there is no crossover today — the vec0 tier is slower at every scale including above 50 000 rows. Do not lower `SEAM_VEC_ANN_MIN_ROWS` to try to improve performance; it will not help until sqlite-vec ships approximate indexing. The gate exists to avoid the DDL overhead of building a vec0 table that would not be used. Run `make bench-semantic-ann` to measure exact-KNN vs numpy matmul latency on your hardware.
- **`SEAM_DIAGNOSTICS=0` (default) is a TRUE no-op — byte-identical to pre-P5.5 (P5.5):** when off, no NDJSON file is created, no resource sampling runs, no `atexit` handler is registered, and the MCP `_instrument` decorator returns each tool UNCHANGED (FastMCP sees a byte-identical schema; tool count stays 16). The CLI `run_query` is a one-attribute-check passthrough. There is zero measurable read-path overhead. The facility is local-file-only — it NEVER makes a network call or emits telemetry.
- **Diagnostics records ONLY tool names + numeric metrics — never source text or query arguments (P5.5):** `record_query(tool, duration_ms, result_chars)` takes no argument text and no result body by design (structural redaction — the offending data is not even a parameter). `result_chars` is a serialized-length SIZE PROXY: the result is serialized, its length measured, and the string immediately discarded — never stored. This is enforced by a hardcoded key set, a defense-in-depth subset assertion on every written line, and a gate test that fails if a secret-like string ever reaches the NDJSON. When adding a field to a diagnostics line, update the allowed key set AND confirm it is numeric/None — never a string derived from user code.
- **`ru_maxrss` is PEAK RSS, not current, and its unit differs by platform (P5.5):** the `rss_bytes` metric comes from `resource.getrusage(RUSAGE_SELF).ru_maxrss`, which is the process high-water mark — it never decreases within a process, so it shows the worst-case RSS, not the instantaneous value. The raw value is in KiB on Linux and bytes on macOS; the recorder normalizes both to bytes. On Windows (`resource` module absent) `rss_bytes` is `null`. Current-RSS parity would require a dependency (psutil) — deliberately not added.
- **`open_fds` is Linux-only; `null` on macOS/Windows/BSD (P5.5):** the open file-descriptor count is read from `/proc/self/fd`, which only exists on Linux. On every other platform the metric is `null` (graceful degradation, never an error). The soak summary shows `open-FD delta: n/a` accordingly. This is the documented cost of the no-dependency (no-psutil) decision — FD-leak detection via diagnostics works on Linux CI, not on a macOS dev box.
- **A fast query writes NO per-query line — only the slow ones + the atexit snapshot count it (P5.5):** `record_query` writes a `slow_query` NDJSON line ONLY when `duration_ms >= SEAM_DIAGNOSTICS_SLOW_MS` (default 100 ms). A fast query still increments the in-memory query counter, which is surfaced in the `snapshot` line (written periodically and at process exit), but it produces no line of its own. This is intentional — "slow query summaries", not a line-per-query log. To capture a line for EVERY query (e.g. in a soak run), set `SEAM_DIAGNOSTICS_SLOW_MS=0`.
- **CLI query counts are per-process; the NDJSON file is the durable cross-invocation record (P5.5):** each `seam search/query/context/impact/trace` invocation is a fresh process, so its in-memory query counter resets to 0 and its atexit snapshot reports only that one invocation's activity. Cross-invocation trends (e.g. total queries across a session) are reconstructed by reading the append-only NDJSON file (each process appends whole lines under `O_APPEND`, safe across concurrent processes), NOT from an accumulating counter. The long-lived MCP server (`seam start`) DOES accumulate across all its tool calls in one process.
- **The soak script requires an existing index and is NOT part of `make gate` (P5.5):** `benchmarks/soak.py` / `make soak` reconciles nothing — it drives read requests against an already-built `.seam/seam.db` (run `seam init` first) and errors with a non-zero exit if none exists. It is a local/optional-CI tool (like `make bench-semantic` / the no-egress job), never run by the gate. Run `SEAM_DIAGNOSTICS=1 make soak` to also capture an NDJSON trace and populate the RSS/FD/DB-size summary fields (they show `n/a` when diagnostics is off, since resource sampling is gated on the recorder being enabled).
- **The atexit snapshot's `db_size_bytes` needs the resolved DB path — set via `set_db_path()` (P5.5):** the process-exit snapshot falls back to `config.SEAM_DB_PATH`, which is relative to the process CWD — wrong (null/incorrect `db_size_bytes`) under `--db-dir` or when a command runs from a directory other than the project root. The CLI (`_open_index`, the impact/trace commands), the MCP server (`create_server`, via `PRAGMA database_list`), and the watcher (`self._db_path`) all call `recorder.set_db_path(...)` with the resolved path, so `db_size_bytes` is correct on those paths. Only the RARE case of a custom caller that enables diagnostics without setting the path gets the CWD-relative fallback (and only `db_size_bytes` is affected — RSS/FDs/counters are path-independent).
- **In-process tests that enable diagnostics must reset the recorder in teardown (P5.5):** enabling a recorder registers a process-lifetime `atexit` handler; a test that points it at a `TemporaryDirectory` and does not clean up leaves a dangling handler that fires (and harmlessly logs a caught failure) at interpreter exit once the dir is gone. Use `diagnostics.reset_recorder()` (closes + clears the singleton) in an autouse fixture, and — critically — do NOT `monkeypatch.setattr` the `_process_recorder` singleton (monkeypatch RESTORES it during teardown, BEFORE your reset runs, orphaning the enabled recorder). The existing diagnostics test modules show the correct pattern (autouse `_reset_diag` + `reset_recorder()` in the fixture `finally` while the temp dir still exists).
- **`seam fetch` is the ONLY network path in Seam — runtime stays 100% local after fetch completes (WS4):** all read commands (`seam search`, `seam context`, `seam impact`, the 16 MCP tools, `seam serve`, the watcher) make zero network calls. `seam fetch` downloads ONE artifact from `SEAM_INDEX_ARTIFACT_URL` at setup time and then the runtime path is offline. The no-egress CI proof explicitly excludes `seam fetch` from its strace probes (alongside `seam init --semantic`). When `SEAM_INDEX_ARTIFACT_URL` is not set (the default), `seam fetch` exits immediately with `INVALID_INPUT` — zero network access, zero effect on the local index.
- **`SEAM_INDEX_ARTIFACT_URL` unset = feature completely inert — no behavioral change (WS4):** the default value is `""` (empty string). With an empty URL, `seam fetch` raises `FetchError(INVALID_INPUT)` before any file I/O or network access. The URL is only consumed by `seam fetch`; no other command or code path reads it. Every existing Seam command is byte-identical to pre-WS4 when `SEAM_INDEX_ARTIFACT_URL` is unset.
- **The fetched index's embedding model must match `SEAM_EMBED_MODEL` or semantic search degrades to FTS5-only (WS4):** `seam fetch` does not validate the embedded model in the fetched `seam.db` — it calls `sync_embeddings` with the CURRENT `SEAM_EMBED_MODEL`, which will find all existing embeddings already present (matching the CI model) OR find them as "wrong model" rows (if CI used a different model) and degrade silently to FTS5. To ensure hybrid semantic search works after `seam fetch --semantic`, the CI producer and local consumer must use the same `SEAM_EMBED_MODEL`. Run `seam status` to confirm the model row matches; run `seam init --semantic` to rebuild if it mismatches.
- **`seam rebase` rewrites ONLY `files.path` — all other data is preserved unchanged (WS4):** `rebase_index` executes a single `UPDATE files SET path = ? || substr(path, ?)` WHERE clause. Symbols, edges, clusters, embeddings, comments — everything except `files.path` — is untouched. The rewrite is idempotent (calling again on an already-local index returns 0). Synthetic rows (path starts with `:`) are explicitly excluded to protect edge-synthesis bridge rows. `seam fetch` calls `rebase_index` automatically as step 6 — you only need `seam rebase` if you unpack an archive manually.
- **Cross-OS fetch (Linux/macOS → Windows) is an accepted MVP gap (WS4):** `rebase_index` builds the LIKE prefix using `os.sep`. On Windows, `os.sep` is `\`, but paths stored in a Linux/macOS-built index use `/`. The LIKE pattern will not match those rows, so `files_rebased` will be 0 and queries will refer to CI paths instead of local paths. Same-OS fetches (Linux→Linux, macOS→macOS, Windows→Windows) always work. Cross-OS Windows consumers must run `seam init` locally for a correct index.
- **Rebase auto-detect maps the COMMON PREFIX of indexed paths → new root; realistic repos align with ZERO sync churn (WS4):** `rebase_index` with `old_root=None` (the `seam fetch` path) uses `os.path.commonpath` of all non-synthetic `files.path` rows as the source prefix. This equals the project root whenever ANY indexed file sits at/near the root — and because Seam indexes root-level config resources (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, …) as first-class files, essentially every real repo has such a root anchor. Verified empirically: a src-rooted repo with a root `pyproject.toml` rebases with `sync added=0/removed=0` (full speedup). The pathological case — a repo where EVERY indexed file (source AND config) shares a prefix deeper than the project root (e.g. everything under `src/` with no indexed root file at all) — rebases to that deeper prefix, so `sync` re-indexes the delta (added==removed==N). The result is still CORRECT (sync self-heals), only the WS4 speedup is lost for that fetch. A future enhancement could record the exact source root in the archive to make rebase precise regardless of file distribution; not needed for real-world layouts.
- **Checksum sidecar is OPTIONAL — 404 proceeds with a WARNING; mismatch ABORTS (WS4):** `seam fetch` downloads the `.sha256` sidecar alongside the archive. When the sidecar returns a 404 (or any HTTP/I/O error), verification is skipped and the fetch proceeds — this allows CI setups that publish archives without sidecars to work. When the sidecar IS present and the sha256 does not match, the fetch aborts with `FetchError` before any file is written to disk. If you require mandatory verification, ensure your CI always publishes the sidecar (`.sha256` file) alongside the archive (`.tar.gz`).
- **`seam pack-index` is DISTINCT from `seam pack` — do not confuse them (WS4):** `seam pack <symbol>` is the context-pack command (reads the index, bundles a symbol's caller/callee context). `seam pack-index [path]` produces a portable archive of the whole `.seam/` directory. Both are registered Typer commands under different names; the CLI disambiguates them. Tooling that calls `seam pack` programmatically is unaffected — the context-pack behavior and exit codes are byte-identical to pre-WS4.
- **Atomic swap-in: an interrupted fetch may leave a `.seam.fetch.bak/` directory (WS4):** if `seam fetch` is killed between the `rename(.seam → .seam.fetch.bak)` and the `copytree(staged → .seam)` steps, the backup directory survives. The next `seam fetch` call removes any stale `.seam.fetch.bak` before beginning its own swap, so a subsequent fetch is always safe. To recover manually: `mv .seam.fetch.bak .seam` (when `.seam` does not exist) or `rm -rf .seam.fetch.bak` (when the swap did complete). The original index is never corrupted — only the backup is potentially orphaned.
- **The npm shim requires `uv`/`uvx` at RUN time — it is a wrapper, not a bundle (P5.1):** `npx @catafal/seam` delegates to `uvx` to download and run `seam-code` from PyPI. If `uvx` is not on PATH (and `SEAM_NPM_UVX` is not set), `bin.js` exits 1 with a URL pointing to the uv install docs. There is no auto-install of uv — auto-installing an interpreter-level dependency inside a postinstall script would violate the zero-install-time-code principle and is a known attack vector. Prerequisite: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or platform equivalent).
- **`@catafal/seam@X.Y.Z` only resolves if `seam-code==X.Y.Z` is already on PyPI (P5.1):** the npm package pins an exact `seam-code==<version>` spec that it passes to `uvx --from`. If you `npm publish @catafal/seam` before the matching PyPI release is live, every `npx @catafal/seam` invocation will fail with a uvx "package not found" error. The correct release order is: (1) wait for `seam-code==X.Y.Z` to appear on PyPI; (2) then `npm publish`.
- **npm and pyproject versions are locked in lockstep by a gate test (P5.1):** `test_npm_package_version_matches_pyproject` in `tests/unit/test_smoke.py` runs as part of `make gate` and fails if `pkg/npm/package.json` version diverges from `pyproject.toml` version. When bumping for a release, bump BOTH files and run `make gate` before either publish step.
- **`make test-npm` is NOT part of `make gate` (P5.1):** the vitest npm suite (`pkg/npm/lib/invocation.test.js`) requires Node.js ≥18 and lives outside the Python gate. The Python gate covers the version-parity test (`test_npm_package_version_matches_pyproject`) and the integration smoke (`tests/integration/test_npm_shim.py`, self-skips when node is absent). Run `make test-npm` explicitly in environments with Node.js.
- **All GitHub Actions must be SHA-pinned — `make gate` fails on any mutable ref (P5.2):** every `uses:` ref in `.github/workflows/*.yml` must be pinned to a full 40-hex commit SHA with an inline `# vN` comment (e.g. `actions/checkout@df4cb1c...  # v6`). The repo-invariant test `test_all_workflow_files_are_sha_pinned` in `tests/unit/test_actions_pin_audit.py` runs as part of `make gate` and fails immediately if you add a tag-pinned or branch-pinned step. When adding a new workflow step, copy the SHA from the action's release page or `git ls-remote`; paste it as `@<40-hex-sha>  # vX`. One specific exception: `pypa/gh-action-pypi-publish` is pinned to a **branch-head SHA** (`# release/v1`) — the maintainers do not tag releases, they advance a named branch. This SHA is NOT a tag that Dependabot bumps automatically; it needs a periodic manual refresh by checking the current `release/v1` HEAD. The publish step itself is CI-only-proven: the `publish` and `github-release` jobs only trigger on a `v*` tag push and cannot be exercised locally or in a branch CI run.
- **P5.4 no-egress CI job is Linux-only and not part of `make gate`; strace split-connect lines classify as `None` (P5.4):** `.github/workflows/no-egress.yml` runs on `ubuntu-latest` only — `strace` is unavailable on macOS. `make gate` does NOT invoke it; to validate on a Linux host, run the workflow steps manually. Parser limitation: when `strace -f` interleaves syscalls across threads, a `connect()` may split into `<unfinished ...>` / `<... connect resumed>` lines; `classify_connect_line` returns `None` for both halves because `_CONNECT_RE` requires the full struct body in one line. This is benign for the proof: Seam makes no external connections, so any split `connect()` is necessarily local (SQLite WAL, Unix socket) — there are no split EXTERNAL connects to miss. A full re-proof would only matter if Seam ever intentionally added a network call. **Documented exclusions from the no-egress proof** (intentional setup-time provisioning, NOT read-path egress — every read command after these runs offline): (1) `seam init --semantic` / `SEAM_CLUSTER_NAMING=llm` — model download and optional LLM cluster naming at index time; (2) `seam fetch` (WS4 S3) — downloads a pre-built team index artifact from CI when `SEAM_INDEX_ARTIFACT_URL` is set. All three are absent from the strace probes; only the read path is under the zero-egress assertion.
- **Mmap artifact goes stale after watcher edits — rebuild with `seam init --semantic` or `seam sync --semantic` (WS2a)**: the mmap artifact (`vectors.f32` / `.ids.i64` / `.meta.json`) is written only by `seam init --semantic` / `seam sync --semantic`, NOT by the per-file watcher. After live edits that add/remove symbols, the staleness token (COUNT + MAX symbol_id) will mismatch and `_try_mmap_path` falls back to the SQL path automatically — no wrong results, but the mmap performance and recall-completeness benefits are lost until you run `seam init --semantic` or `seam sync --semantic`. The fallback is transparent and correct; the only consequence is the per-query blob-decode cost.
- **Mmap artifact is a cache; it lives in `.seam/` which is already gitignored (WS2a)**: `seam init` writes `.seam/.gitignore` (`*`) so the artifact files are never committed. On a fresh clone with no artifact, `load_store` returns `None` and the semantic read path degrades to SQL automatically. Re-run `seam init --semantic` to rebuild.
- **`SEAM_SEMANTIC_SCAN_CAP` default changed from 20000 to 0 (unlimited) in WS2a**: existing operators who relied on the 20000-row default as a memory ceiling should explicitly set `SEAM_SEMANTIC_SCAN_CAP=20000` (or any positive value) in their environment. The mmap path bounds memory via the OS page cache (file-backed, not heap-allocated), so the 0=unlimited default is safe for most deployments. A positive cap slices the matrix (`matrix[:N]`) in the mmap path as well as applying a SQL LIMIT in the fallback — set it when you have a hard memory constraint, not as a recall filter.
- **`SEAM_VECTOR_STORE=off` is byte-identical to pre-WS2a**: when `"off"`, no artifact is written (`_write_artifact` is not called) and no artifact is read (`_try_mmap_path` is not called) — the semantic read path uses only the SQL brute-force path. Use `"off"` to revert to the pre-WS2a SQL-only behavior for debugging, CI isolation, or operators who prefer not to create the three artifact files. The SQL path is always correct; `"off"` is purely a performance/recall trade-off choice.
- **Staleness token detects row ADD/REMOVE, not in-place vector update (WS2a)**: the token `"{count}:{max_symbol_id}"` catches `seam sync --semantic` or `seam init --semantic` running again (new rows inserted, old rows replaced → max_id or count changes). It does NOT detect an in-place vector replacement where count and max_id happen to be identical to the prior run. This scenario cannot occur in Seam's write path (`INSERT OR REPLACE` with the same symbol_id replaces the row but does NOT change count or max_id) — a full re-embed produces the same count/max_id. The token is sufficient for the actual write patterns.
- **`seam sync --semantic` is now incremental — only NEW symbols are embedded (WS3)**: `seam sync --semantic` calls `sync_embeddings`, which orphan-sweeps deleted embeddings and then embeds only the missing set. It does NOT re-embed symbols whose embedding row already exists for the current model. If you suspect stale vectors (e.g. after changing `SEAM_EMBED_BODY` or switching models), run `seam init --semantic` for a full clean-slate re-embed. `seam init --semantic` is always a full re-embed — unchanged.
- **Pure-removal `seam sync --semantic` still rebuilds the mmap artifact (WS3)**: when `seam sync` deletes symbols (via `delete_file`), SQLite FK CASCADE removes their embedding rows atomically — BEFORE `sync_embeddings` runs. The orphan sweep in `sync_embeddings` therefore finds nothing to delete (`n_removed==0`). The artifact rebuild uses a staleness-token comparison (`compute_index_version(conn, model)` vs stored `store.index_version`) to detect this: a token mismatch triggers a rebuild even when `n_added==0` and `n_removed==0`. This is why a pure-removal sync (only deletions, no new symbols) still results in a fresh, correct artifact.
- **Mixed-model index still requires `seam init --semantic` after changing `SEAM_EMBED_MODEL` (WS3 unchanged)**: `symbols_needing_embeddings(conn, model)` filters by the CURRENT model — symbols embedded under a different model appear as missing and will be re-embedded. BUT the old model's rows remain in the DB (one row per symbol per model) until a full `seam init --semantic` or a manual `DELETE FROM embeddings WHERE model != ?`. A mixed-model DB is not wrong, but it wastes space and the read path uses the configured model only.
- **Codex and Zed share one Seam block in AGENTS.md — uninstalling either removes the shared guidance (P6.4)**: both `CodexTarget` and `ZedTarget` write CLI guidance into `<root>/AGENTS.md` under the same `<!-- seam:start/end -->` marker. `upsert_block` is idempotent so installing both targets (in any order) yields ONE block, never a duplicate. Trade-off: `seam uninstall --target zed` (or `--target codex`) removes the shared block — the other agent's guidance disappears too. Escape hatch: re-run `seam install --target <other-agent>` to restore the block. Documented and verified by `test_codex_zed_share_agents_md_block_uninstall_removes_shared`.
- **`seam uninstall` leaves benign empty container files — expected, audited, and safe (P5.3 / P6.4)**: the installer removes its own content (guidance block from `CLAUDE.md`/`AGENTS.md`/`GEMINI.md`/`.github/copilot-instructions.md`, its entry from `.mcp.json`/`.claude.json`/`config.toml`/`.vscode/mcp.json`/`.gemini/settings.json`/`.zed/settings.json`/`~/.config/zed/settings.json`) but does NOT prune the now-empty parent file/structure — doing so would risk silently deleting foreign content. Residue per target/location: `CLAUDE.md` → empty file; `.mcp.json` → `{"mcpServers": {}}`; `~/.claude.json` → `{"projects": {"<root>": {"mcpServers": {}}}}`; `AGENTS.md` → empty file; `~/.codex/config.toml` → empty string; `.github/copilot-instructions.md` → empty file (VS Code guidance shared-block file); `.vscode/mcp.json` → `{"servers": {}}`; `GEMINI.md` → empty file; `.gemini/settings.json` → `{"mcpServers": {}}`; Zed guidance shares AGENTS.md (see shared-block gotcha above); `.zed/settings.json` or `~/.config/zed/settings.json` → `{"context_servers": {}}`. Cursor's `seam.mdc` is the only owned-file zero-residue guidance target (the cursor rule file is created and fully controlled by the installer — uninstall deletes the whole file). VS Code's `.github/copilot-instructions.md` is a shared-block file → leaves an empty file residue, like `CLAUDE.md`. Foreign content in all shared files always survives. The P5.3/P6.4 test suite (`tests/integration/test_installer_write_scope.py`) encodes the exact expected residue and will fail loudly if it changes.
- **2D Explorer localStorage keys (P2.2) — no collision with 3D; set and semantics are fixed**: The 2D graph tab writes four `localStorage` keys: `seam-detail-panel-w` (detail panel pixel width), `seam-graph-filter` (persisted disabled kind sets — see below), `seam-sidebar-open` ("true"/"false"), `seam-sidebar-w` (sidebar pixel width). The 3D Constellation tab writes `seam-constellation-panel-w`. None of these overlap. Do NOT reuse a 2D key for a future 3D feature or vice versa — mismatched width semantics (2D panel vs 3D panel) would silently initialize one panel to the other's size.
- **`seam-graph-filter` stores DISABLED kinds, not enabled kinds (P2.2)**: The persistence strategy stores the set of explicitly-disabled kind names, not the enabled set. This means a newly-added edge kind or node kind is ENABLED by default on all existing installations (it won't appear in the disabled set). Stale disabled entries (kinds removed from the vocabulary) are silently ignored during load. No migration is needed when the vocabulary grows. Consequence: if you add a kind to `ALL_EDGE_KINDS` or `ALL_NODE_KINDS` and want it disabled by default for existing users, you cannot achieve this via localStorage — ship it enabled and let the user toggle it off.
- **2D filter state is session-global and is NOT reset on center-symbol change (P2.2 semantic change)**: Before P2.2, `EdgeFilterState` was initialized to `defaultEdgeFilter()` on each render (effectively reset). From P2.2 onward, `GraphFilterState` is initialized from `loadGraphFilter()` at mount and persisted on every change, but it is NOT cleared in the `center`-change effect. This means a user who hides INFERRED edges will keep that preference when clicking to a different symbol. The prior behavior (reset-on-navigate) is gone. If you need reset-on-navigate, reinitialize `filter` state inside the `center` useEffect — but do so deliberately, as it would break the session-global UX contract.
- **`/api/symbol/{name}` callers/callees changed shape (P2.2 — additive but breaking for strict consumers)**: `callers` and `callees` were `string[]`; they are now `CallerRef[]` (`{name: string, kind: string, confidence: string}`). The `web/src/api/schema-types.ts` codegen file was updated; TypeScript consumers that pattern-matched on string lists will fail to compile. Pure-JS / non-typed consumers (if any) will receive objects instead of strings. The `fetch_edge_refs` backend helper returns `[]` (not an error) on any DB failure — so callers/callees gracefully degrade to empty rather than 503.
- **`ImpactEntry.kind` in the web API is `null` when `SEAM_EDGE_PROVENANCE=off` (P2.2)**: The `/api/impact` route passes `verbose=False` to `handle_seam_impact`. With `SEAM_EDGE_PROVENANCE=on` (default), `kind` is a core field that survives lean mode and is now passed through the Pydantic `ImpactEntry` model. With `SEAM_EDGE_PROVENANCE=off`, the handler omits `kind` from its output; Pydantic will see it as absent and default to `null`. Web UI code must treat `null` as "kind unknown" and not attempt to display an edge-kind badge.
- **`useGraphOverlays` pure functions are exported for testing — do NOT import them in application code outside the hook (P2.2)**: `decorateNodes`, `buildOffCanvasNodes`, `decorateEdges`, `visibleClusters`, and `applyNodeKindFilter` are exported purely so vitest can test them without a React renderer. Application code should consume the `useGraphOverlays` hook result (`displayNodes`, `displayEdges`, etc.) and never call these functions directly — the hook manages the memoization and ordering guarantees (node-kind filter applied AFTER impact/trace decoration).
- **`ViewportController` must be a child of `<ReactFlow>` — not a sibling (P2.2)**: `ViewportController` uses `useReactFlow()`, which requires the React Flow context provider. Rendering it outside the `<ReactFlow>` tree (e.g. as a sibling in `GraphCanvas`) will throw a runtime error. The `framed` ref prevents spurious viewport jumps when the same overlay stays active across re-renders — this guard is load-bearing; removing it causes the viewport to re-fit whenever impact data refreshes.
- **`SEAM_EMBED_BODY=on` is indexing-time only — changing it requires `seam init --semantic` re-index (WS1)**: the body slice and comment texts are baked into the embedding vectors at index time. Toggling `SEAM_EMBED_BODY` after indexing has no effect on existing vectors — they remain whatever they were when `seam init --semantic` was last run. To activate body enrichment: set `SEAM_EMBED_BODY=on` and re-run `seam init --semantic`. To revert to header-only: set `SEAM_EMBED_BODY=off` and re-run. A mixed index (some rows embedded with body, some without) is detected by the model-mismatch guard IF the model name differs; otherwise, no guard catches it — avoid partial re-indexes.
- **`SEAM_EMBED_BODY=off` (default) = byte-identical vectors to pre-WS1-A — no disk reads, no SQL join (WS1)**: when `SEAM_EMBED_BODY=off`, `index_embeddings` skips the file-read cache, the per-symbol comment join, and the body/comments kwargs to `symbol_text()`. The embedding query (`SELECT id, name, signature, docstring`) is unchanged. Existing `seam init --semantic` users upgrading to WS1 get byte-identical vectors until they explicitly set `SEAM_EMBED_BODY=on` and re-index. This mirrors the `SEAM_SEMANTIC` opt-in discipline.
- **A source file that cannot be read degrades its symbols to header-only embeddings, not an error (WS1)**: when `SEAM_EMBED_BODY=on` and a source file is unreadable (deleted, permission error, encoding error), `_read_file_lines()` logs a `WARNING` and returns `None`. All symbols from that file fall back to header-only embedding (byte-identical to `SEAM_EMBED_BODY=off` for those symbols). The embedding run continues and returns a positive count — no `-1` sentinel. Re-run `seam init --semantic` after fixing the file access issue.
- **`SEAM_EMBED_INPUT_MAX_CHARS=0` = unlimited body + comments; default 2000 (WS1)**: the 0=unlimited convention mirrors `SEAM_IMPACT_MAX_BYTES`. Internally, `SEAM_EMBED_INPUT_MAX_CHARS=0` is mapped to a 1 M char sentinel so `symbol_text()` receives a finite `max_chars` (the `None` path disables body/comments entirely and is reserved for `SEAM_EMBED_BODY=off`). A very large body (e.g. a 500-line function) is included in full when `SEAM_EMBED_INPUT_MAX_CHARS=0` — this inflates vector-index load time. Use the default 2000 unless you have a specific reason to include full bodies.
- **Comment text joined by WS1-B is `GROUP_CONCAT(..., ' ')` — ordering is SQLite insertion order, not source line order (WS1)**: SQLite's `GROUP_CONCAT` does not guarantee ordering within the group. In practice, comments are inserted in file-walk order (which is line order), but this is not enforced. The comments field is a semantic enrichment hint for embedding — arbitrary ordering is acceptable because the embedding model treats it as a bag of tokens, not a structured sequence. If line-order matters for your use case, post-process the joined string externally.
- **`[web]` extra requires numpy (P2.1)**: `seam/query/layout.py` imports numpy at module level (not lazily) so the O(n²) ForceAtlas2 kernel has compiled-C speed. `pip install 'seam-code[web]'` pulls numpy automatically via the `[web]` extras declaration. A `seam serve` call without the `[web]` extra already fails with an install hint — if numpy is absent the error is `ModuleNotFoundError: numpy`. Do NOT add a `try/except ImportError` fallback; the layout endpoint has no pure-Python fallback that is fast enough to be useful.
- **`/api/graph/layout` layout is degree-capped at `SEAM_LAYOUT_MAX_NODES` (P2.1)**: nodes are selected by degree DESC (most-connected first), then by name ASC as a deterministic tie-break. Symbols below the cap are absent from the layout even if they appear in the 2D graph or MCP results. `total_nodes` in the response is the honest uncapped count; compare it to `len(nodes)` to detect truncation. Raise `SEAM_LAYOUT_MAX_NODES` to include more nodes (but note the O(n²) memory cost — 3 000 nodes → ~216 MB per FA2 iteration). The hard ceiling `SEAM_LAYOUT_MAX_SAFE_NODES` (default 3 000) is enforced inside `compute_layout` regardless of what the caller passes.
- **`/api/graph/layout` layout cache is keyed on `(MAX(indexed_at), file_count, max_nodes)` — stale layouts persist until the cache TTL expires (P2.1)**: after `seam sync` or `seam init`, the indexed_at value changes and the cache key changes — the next request re-runs FA2 from scratch. However, within the TTL window (`SEAM_STALENESS_TTL_SECONDS`, default 5 s) a stale cache entry may still be served. This is intentional: the layout endpoint is called on every tab activation and re-running 40 FA2 iterations on each request would stall the server. The layout does NOT attach an `index_status` staleness banner (unlike the 5 graph-traversal MCP tools) because the layout's cached positions are always self-consistent — the banner would fire on every request in the TTL window and provide no actionable guidance. Run `seam sync` to invalidate the cache on demand.
- **`seam status` freshness semantics changed in P2 — watcher-aware AND synth-aware now**: the old behavior "only detects modified/added tracked files" is superseded. The new `staleness.py`-based check has two new dimensions: (1) when a live watcher is running, file-mtime drift is NOT reported stale (the watcher self-heals it — this was always correct but wasn't modeled before); (2) a watcher-running index WITH synthesized edges IS reported stale because the watcher never recomputes synthesized edges or clusters (seam init or seam sync required). The `seam status` CLI calls `check_staleness(..., respect_knob=False)` so the CLI freshness field is ALWAYS computed, regardless of whether `SEAM_STALENESS_CHECK` is on or off.
- **Staleness banner is handler/read-path only — no re-index; `seam_changes`/`seam_affected` risk verdicts byte-stable**: `SEAM_STALENESS_CHECK` gates the `index_status` banner on the 5 graph-traversal handlers only. `seam_changes` and `seam_affected` risk verdicts come from the analysis layer below the handler (same as the E2/E3 and E1 knobs) and are therefore byte-stable regardless of this knob. The banner is purely additive — its presence/absence never changes any other field in the response. No schema change, no migration.
- **Staleness banner can persist up to `SEAM_STALENESS_TTL_SECONDS` after the index is fixed**: only stale verdicts are cached in the per-process TTL cache (safe asymmetry — fresh is never cached). Once the index is refreshed (seam sync / seam init), the stale verdict expires at most `SEAM_STALENESS_TTL_SECONDS` (default 5 s) after the fix. A burst of MCP reads on a known-stale repo re-uses the cached verdict rather than re-scanning. Set `SEAM_STALENESS_TTL_SECONDS=0` to disable the cache entirely.
- **Bounded scan misses stale files outside the newest-SCAN_CAP window**: `check_staleness` stat-checks only the newest `SEAM_STALENESS_SCAN_CAP` (default 200) files ordered by `indexed_at DESC`. A file that was indexed early and then edited may not appear in this window — its staleness is NOT detected. This is an accepted limitation to keep per-call overhead sub-millisecond on large repos (10k+ files × stat = ~50–100 ms). Raise `SEAM_STALENESS_SCAN_CAP` if false-fresh verdicts are a concern. The log emits an `INFO` line when the scan fills the cap so operators can detect the partial-verdict case.
- **A pure mtime-only change (touch / os.utime without content change) triggers the stale banner but `seam sync` does NOT clear it**: `seam sync` SHA-confirms file content before re-indexing; a file whose SHA matches the stored value is skipped, so the stored mtime is never refreshed. The stale banner therefore persists after a touch-only edit even after `seam sync` runs — the index IS correct (content unchanged), but the mtime check is confused. This is a pre-existing condition: the old `seam status` mtime heuristic had the same blind spot. Escape hatch: `seam init` refreshes stored mtimes for all files. Cross-reference: see also the `seam sync` "content change that preserves mtime EXACTLY" gotcha below.
- **`uses` edges are HIGHER-VOLUME than `holds` — blast-radius verdicts widen after a re-index, and they require `seam init`**: a `uses` edge is emitted per plain-user-typed parameter, so most typed functions contribute ≥1. They enrich a TYPE's upstream (every function whose signature references it — desirable) and a FUNCTION's downstream (its param types); they do NOT add upstream noise to a function (the function is the SOURCE). `seam_impact`/`seam_changes`/`seam_affected` verdicts grow — gate with `SEAM_PARAM_EDGES=off` for byte-stable upgrades. Extraction-time: pre-feature indexes have no `uses` edges until re-indexed. Ruby emits none (untyped); C extracts typedef/named-struct param types only (function-pointer/anonymous-struct params skipped, same spirit as the existing C typedef gotchas).
- **`uses` vs `holds` — signature coupling vs stored composition**: `holds` = a class STORES a type (field or constructor param that becomes a field); `uses` = a function REFERENCES a type as a parameter without necessarily storing it. A constructor param typed with a user class produces BOTH a `holds` edge (class→T) and a `uses` edge (Class.ctor→T) — different sources, different kinds; intentional, not a duplicate. `uses` does not cover return types or local-variable types (out of scope).
- **`expand_impact_seeds` emits qualified member forms — a container's own within-cap members become walk SEEDS (excluded from their own blast radius), and impact/trace/affected verdicts WIDEN**: post-Tier-B, member-call edge targets are qualified (`Class.method`), so a container seed now emits both `method` and `Class.method` to match them at d=1. Two consequences: (1) a class's own members that are within `SEAM_NAME_EXPANSION_CAP` (default 50) are now seeds → they no longer appear as self-references in their own `seam_impact` (correct — they ARE the thing being changed); members BEYOND the cap still surface as self-refs (ranked last by E2/E3). (2) impact/trace/affected match more edges at d=1, so blast-radius verdicts widen — the same read-path widening Tier A introduced. This is a correctness fix (it repairs a Tier-B regression where direct injectors landed at d=2), not gated by a knob; a full `seam init` is not required (read-path).
- **E2/E3 relevance ranking is HANDLER-ONLY and read-time — no re-index, no effect on `seam_changes`/`seam_affected`**: `SEAM_IMPACT_RELEVANCE_SORT` / `SEAM_IMPACT_SELF_REF` shape only `seam_impact`'s handler output. `seam_changes` and `seam_affected` call the analysis-layer `impact()` directly (below the handler), so their risk verdicts stay byte-stable regardless of these knobs. There is no DB change — the knobs take effect immediately on the existing index. `SEAM_IMPACT_RELEVANCE_SORT=off` reverts `seam_impact` to the prior production-before-test ordering byte-identically.
- **`SEAM_IMPACT_SELF_REF=hide` can drop a bare-name homonym that collides with a member name**: self-reference classification is name-keyed (like the rest of Seam). A target class `Foo` with member `Foo.run` contributes the bare name `run` to its self-name set; an UNRELATED external symbol also named bare `run` would be classified self-ref and, in `hide` mode, dropped from the output (in the default `rank` mode it is only deprioritized, never dropped). This is the same homonym-collapse limitation the edge graph already has — Seam cannot distinguish two bare `run`s. Use the default `rank` mode (lossless) unless byte budget is critical, and prefer qualified targets to disambiguate.
- **`hidden_self_refs` appears ONLY under `SEAM_IMPACT_SELF_REF=hide`**: like `hidden_tests`, its presence signals self-refs were filtered (even when the count is 0, so agents can rely on it to reconcile `risk_summary` against the shown entries). In the default `rank` mode it is absent — self-refs are present in the output (sorted last), so `risk_summary` already accounts for them.
- **`seam_context_pack` neighbor ORDER is RWR-relevance, not alphabetical or insertion order (E3)**: under `SEAM_PACK_RELEVANCE_RANK=on` (default), callers/callees are sorted by `(-personalized_pagerank_score, is_test, min_id)` before the per-file + global caps. So the kept neighbors when a list is capped are the ones most relevant to the seed (in its local call-graph neighborhood), and a neighbor whose name sorts late / was indexed late can now appear FIRST. The full uncapped lists from `seam_context` (engine `context()`) are UNAFFECTED — they remain alphabetical (`sorted()`); E3 only reorders the CAPPED pack. `truncated` counts and the `NeighborRef` shape are unchanged (ranking is internal — no `ppr_score` field is exposed). Set `SEAM_PACK_RELEVANCE_RANK=off` for the prior min_id order. The RWR walk is bounded (`SEAM_RWR_MAX_NODES`/`SEAM_RWR_MAX_DEPTH`); on a bound hit or any failure it degrades to min_id order, never raises.
- **`best_candidate` is ABSENT (not null) on non-AMBIGUOUS `seam_impact` entries by default (E1)**: under `SEAM_IMPACT_OMIT_NULL_CANDIDATE=on` (default) a null `best_candidate` key is omitted from impact entries. `best_candidate` is the AMBIGUOUS proximity pick — null for every EXTRACTED/INFERRED entry — so its omission is lossless (null ≡ absent, the same null-contract as everywhere else). An AMBIGUOUS entry's NON-null `best_candidate` is always kept. Tooling must treat a missing `best_candidate` key as "no proximity pick" (i.e. not AMBIGUOUS), identical to the prior `null`. `resolved_by` is NOT affected — it is always present (may be null). Set `SEAM_IMPACT_OMIT_NULL_CANDIDATE=off` for the byte-identical prior shape (`best_candidate: null` retained). Handler-layer only — `seam_changes`/`seam_affected` never carried `best_candidate` and are unaffected; `seam_trace` hops still carry `best_candidate` (E1 scoped to impact entries only).
- **`reads`/`writes` feed the kind-agnostic BFS — impact/changes/affected verdicts WIDEN after a field-access re-index**: field-access edges add one edge per access site (higher volume than `holds`, which is one edge per stored field). Every `seam_impact`, `seam_changes`, and `seam_affected` result will include field readers/writers in the blast radius. If you need verdicts to stay byte-stable across an A3 upgrade, gate the index with `SEAM_FIELD_ACCESS_EDGES=off`.
- **field-access edges and `field` symbols require a `seam init` re-index**: pre-A3 indexes have no `reads`/`writes` edges and no `kind='field'` symbols. `seam_impact` / `seam_context` / `seam_trace` on a field name will show empty results until the index is rebuilt. There is NO schema migration — `reads`/`writes`/`field` are additive TEXT values in the existing `edges.kind` and `symbols.kind` columns.
- **`kind='field'` is a new additive symbol kind — tooling assuming the closed vocabulary must handle it**: code that treats `symbols.kind` as a closed enum `{function, class, method, interface, type}` will encounter unexpected `'field'` values after a re-index. Field symbols count against `SEAM_MAX_IMPACT_SYMBOLS` in `seam_changes` (same cap as other symbol kinds). Treat `kind='field'` as a first-class symbol: it has a `qualified_name` (`Type.field`), appears in `seam_search` results, and participates in the impact graph via `reads`/`writes` edges.
- **On a FIELD seed, `seam_context` `callers` is a superset of `field_readers` ∪ `field_writers`**: `callers` is populated by the kind-agnostic BFS and therefore includes ALL edge kinds pointing to this symbol — call edges, import edges, reads edges, writes edges. `field_readers` and `field_writers` are the precise typed split (only `reads`/`writes` edges). Use `field_readers`/`field_writers` when you need to distinguish data-flow from control-flow; use `callers` when you need the full inclusive blast radius.
- **read/write provenance is now surfaced in `seam_impact`/`seam_trace` output (closed in E4)**: each `seam_impact` tier entry carries `kind` (the edge kind of the final hop — full 9-kind vocab including `reads` and `writes`) when `SEAM_EDGE_PROVENANCE=on` (default). `seam_trace` hops always carried `kind`; their docstring was corrected to the full vocabulary. `seam_context`'s typed `field_readers`/`field_writers` split remains for the dedicated field-access view. Agents can now distinguish a data-coupling `reads`/`writes` dependent from a control-coupling `call` dependent directly in impact results.
- **Clustering EXCLUDES synthesized edges**: cluster detection filters out edges with `synthesized_by IS NOT NULL` to avoid feedback pollution. The synthesis post-pass runs **after** clustering and its edges persist in the `edges` table across runs — feeding them back into the next Louvain pass would let synthesized over-approximations re-partition communities. Clusters therefore reflect only statically-extracted coupling.
- **Synthesized edges go STALE after watcher edits**: like clusters, synthesized edges are NOT recomputed per-file by the watcher — they are written only by `seam init` / `seam sync`. After live edits, run `seam init` or `seam sync` (or `seam sync --force-synthesis`) to refresh. This is the **same accepted trade-off as stale cluster labels, but slightly higher-stakes**: a stale synthesized `call` edge feeds `seam_impact` / `seam_changes`, so a stale edge can over- or under-report blast radius, not merely mislabel a cluster.
- **`seam_changes` / `seam_affected` risk verdicts WIDEN after a synthesis-enabled re-index**: synthesis adds edges, so blast-radius and change-risk verdicts grow — the same effect inheritance (`extends`/`implements`) and `holds` edges have. If you need verdicts to stay byte-stable across an upgrade, gate the index with `SEAM_EDGE_SYNTHESIS=off`.
- **`SEAM_SYNTHESIS_FANOUT_CAP` semantics differ per channel**: for A2 (interface→impl) and closure-collection channels the cap **TRUNCATES** to N synthesized edges (you get the first N). For the event-emitter channel the cap **SKIPS the entire event** when the handler count exceeds N — a generic high-fanout event (e.g. a global `change` bus with hundreds of listeners) is treated as a likely false-positive and dropped rather than truncated. Divergence is deliberate: truncating a fan-out keeps signal; truncating a suspect mega-event keeps noise.
- **`synthesized_by` is now surfaced in `seam_impact` / `seam_trace` output (closed in E4)**: each `seam_impact` tier entry and each `seam_trace` hop carries `synthesized_by` when `SEAM_EDGE_PROVENANCE=on` (default). `null` = statically extracted; a channel-name string (e.g. `"interface-override"`, `"closure-collection"`, `"event-emitter"`) = synthesized by the post-pass. See the E4 gotchas below for the three-way null-ambiguity and lean-mode stripping rules. `SEAM_EDGE_PROVENANCE=off` reverts to byte-identical pre-E4 output (neither field emitted).
- **E4 `synthesized_by` null has three distinct meanings depending on context**: (1) `SEAM_EDGE_PROVENANCE=off` → the field is ABSENT entirely (byte-identical pre-E4, no key at all); (2) `SEAM_EDGE_PROVENANCE=on` AND the index predates v12 OR `SEAM_EDGE_SYNTHESIS=off` → `synthesized_by=null` for ALL entries (every edge is statically extracted — correct, since there are no synthesized edges); (3) `SEAM_EDGE_PROVENANCE=on` AND a v12 synthesis-enabled index → `synthesized_by=null` means the specific edge IS statically extracted; a non-null channel name means it is synthesized. A pre-v12 or SEAM_EDGE_SYNTHESIS=off index is correctly described by case (2): null universally means static, which is accurate. No distinguishing flag is added — the DB state already determines semantics. Agent rule: absent = provenance off; null = static; string = synthesized.
- **E4 `synthesized_by` is stripped in lean mode (`verbose=False`)**: it joins the "heavy fields" list (`_HEAVY_FIELDS`) alongside `resolved_by`, `best_candidate`, `decorators`, etc. `kind` is NOT stripped in lean — it is a core field (like `confidence` and `distance`). This follows the Phase 8 lean contract: lean = minimal shape for token-budget callers; core identity + provenance-lite. Use `verbose=True` (default) to get `synthesized_by`.
- **E4 `next_actions` stays WITHIN `max_bytes`**: the steer is generated AFTER the byte ceiling runs, then `_attach_steer` checks whether appending it would breach the budget. If it would, the handler re-trims from the PRE-ceiling response (clean — not the already-trimmed one, which would double-count `truncated`) with a reserve for the steer, then regenerates the steer for the smaller entry set. A single re-trim converges because the steer's count-cap hints depend on count-cap `truncated` (invariant under further byte trimming); only the byte-ceiling hint's digit changes, absorbed by the `_STEER_RESERVE_MARGIN`. All-trimmed case (entries empty, risk_summary non-empty) is the documented exception: the anti-false-safe warning is always attached even if it would exceed a sub-envelope budget — preventing the dangerous false-safe is the point.
- **E4 `SEAM_EDGE_PROVENANCE` and `SEAM_IMPACT_STEER` are handler/read-path only — no re-index, `seam_changes`/`seam_affected` byte-stable**: both knobs shape `seam_impact` / `seam_trace` handler output only. `seam_changes` and `seam_affected` call the analysis-layer `impact()` directly (below the handler), so their output is byte-stable regardless of these knobs. No schema change, no DB query added — `kind` and `synthesized_by` are read from the existing v12 `edges` columns already fetched by the neighbor queries; the only change is adding them to the SELECT list and threading them through the BFS result structs.
- **A1 channels pair field-names / event-keys GLOBALLY (cross-file)**: closure-collection pairs collection field names to append sites, and event-emitter pairs registrar→dispatcher by event-string literal, **across the whole repo** — so generic names like `handlers` or a `change` event can produce false-positive links. The risk is bounded by `SEAM_SYNTHESIS_FANOUT_CAP` and by requiring BOTH an invocation/dispatch site AND an append/registration site; all such edges are tagged `INFERRED`.
- **`edges.synthesized_by` is NULL until a synthesis-enabled `seam init` / `sync` re-index**: the v11→v12 migration (auto-run on `connect()`) adds the `edges.synthesized_by` column but does NOT populate it — synthesized edges are written only by the post-pass. On a pre-v12 (or `SEAM_EDGE_SYNTHESIS=off`) index, no rows carry a synthesized tag and impact/context/trace behave byte-identically to pre-synthesis.
- **`edges.receiver` is NULL until `seam init` re-index after upgrading to v10**: the v9→v10 migration (auto-run on `connect()`) adds the `edges.receiver` column with `NULL` as the default. Existing edge rows keep `receiver=NULL` — same null-contract as the Phase 4/5 enrichment fields. Only a full `seam init` re-index populates `receiver` and upgrades bare call targets to qualified `Type.method` targets. Until then, qualified-target edges are absent and Tier A read-time bridging remains the only disambiguation.
- **Tier B inference is extraction-time only — changing `SEAM_TYPE_INFERENCE` requires re-index**: `SEAM_TYPE_INFERENCE=off` skips inference during extraction; switching it later has no retroactive effect. Run `seam init` to rebuild the index with the new setting. Toggling the knob at read time has no effect (the edges are already stored).
- **Conservatism contract — Tier B NEVER emits a wrong edge**: `resolve_receiver_type()` returns `None` (→ bare target kept) for optionals (`Foo | None`, `Foo?`, `Optional[Foo]`), containers (`list[T]`, `dict[K,V]`, `[Foo]`), generics (`Array<T>`, `Set<T>`), chained receivers (`a.b.c()`), and any identifier not found in the current scope. Only a plain user-type name that appears exactly in the class-field/param/local scope gets a qualified edge. The cost of a false negative (missed edge) is always lower than a false positive (wrong target).
- **TS/JS member-expression call edges (Tier B B3) require `seam init` re-index**: pre-B3 indexes have no `obj.method()` call edges for TypeScript/JavaScript (they were silently dropped). After upgrading to Tier B, run `seam init` to capture these edges. Until then, `seam_impact` / `seam_context` on TS/JS methods will under-report upstream callers.
- **`instantiates` edges require `seam init` re-index**: pre-B6 indexes have no `instantiates` edges. `new Foo()` / `Foo{}` / composite-literal calls appear as absent in the graph until re-indexed. The `instantiates` kind is traversed by `seam_impact` / `seam_trace` alongside `call` / `import` / `extends` / `implements`.
- **`holds` edges require `seam init` re-index**: pre-composition indexes have no `holds` edges. Typed stored fields/properties and typed constructor/init parameters are absent from the graph until re-indexed. After re-indexing, `seam_impact` / `seam_context` / `seam_trace` traverse `holds` edges automatically (the traversal layer is kind-agnostic). No schema migration is required — `holds` is a new value in the existing `edges.kind` column.
- **`SEAM_COMPOSITION_EDGES` is extraction-time only**: setting `SEAM_COMPOSITION_EDGES=off` suppresses `holds` emission only during indexing. Toggling the knob after indexing has no retroactive effect — run `seam init` to rebuild the index without composition edges. Conversely, switching from `off` → `on` requires a re-index to populate `holds` edges.
- **`holds` captures stored composition only — not method params, locals, or return types**: a `holds` edge is emitted only for a typed stored field/property on a class body, OR for a typed constructor/init parameter (which typically becomes a stored field). Method parameter types, local variable annotations, and return types do NOT produce `holds` edges. Builtins (`int`, `string`, `bool`, etc.) are filtered via `is_builtin()` — no noise for primitive-typed fields.
- **Tier A name-resolution is read-time-only**: the qualified↔bare bridging in `seam_context`, `seam_impact`, `seam_trace`, and `seam_query` is a pure read-path shim — it does NOT change how symbols or edges are stored. The extractor still writes method symbol names as `Class.method` and call-edge `target_name` as bare `method`. The bridge reconciles this at query time via `seam/query/names.py`. Once Tier B edges are indexed, these edges are already qualified — Tier A handles the remainder.
- **`ambiguous` flag semantics in `seam_context` (Tier A)**: before Tier A, `ambiguous=True` meant the name appeared in more than one file (cross-file collision). After Tier A, `ambiguous=True` also means a bare query resolved to multiple qualified definitions (e.g. querying `parse` found `Parser.parse` + `Lexer.parse`). In BOTH cases callers/callees are merged across ALL matching definitions. `ambiguous` signals "merged view — consider disambiguating with a qualified name or uid".
- **`SEAM_NAME_EXPANSION_CAP` (default 50) caps class→member fan-out**: when `seam_context`, `seam_impact`, or `seam_query` receives a class/interface/struct name, up to 50 member bare names are added to the edge lookup. Classes with >50 methods will silently have some members excluded from the fan-out; raise the cap via env var if precision matters more than query cost.
- **`SEAM_BARE_RESOLVE_CAP` (default 25) caps the bare-name suffix scan**: `resolve_query_to_defs` uses `LIKE '%.name'` which cannot use the B-tree index (full-table scan). The cap bounds the scan before the Python exact-suffix filter. Common identifiers like `run`, `get`, `parse` can match thousands of qualified symbols — without the cap this would be O(N) unbounded. Set to 0 for unlimited (not recommended on large codebases).
- **Clusters recomputed only on full `seam init` OR `seam sync` (Phase 7)**: the file *watcher*
  still does NOT recompute clusters after per-file edits — new symbols indexed by the live watcher
  get `cluster_id=NULL` until a recompute runs. `seam sync` now closes this: it recomputes clusters
  (gated on graph change) after reconciling. If the watcher already indexed your edits (so `seam sync`
  sees no on-disk drift → graph unchanged → recompute skipped), run `seam sync --force-clusters`
  (cheap — recomputes clusters without re-indexing files) or `seam init`.
- **`seam sync` is filesystem-reconcile, not git**: it detects changes by mtime + SHA-1 against the
  `files` table, so it works in non-git repos and catches pulled/merged/checked-out changes. Blind
  spot (same as CodeGraph): a content change that preserves mtime EXACTLY is missed — `seam init`
  (full re-index) is the escape hatch. A tracked file is deleted from the index only once it
  genuinely no longer exists on disk (existsSync guard) — a file the walk skipped but that still
  exists is kept, not removed. Also note: a touch-only mtime bump (no content change) is skipped by
  `seam sync` (SHA-confirms content first) so the stored mtime is not refreshed — see the P2
  staleness gotcha above for the consequence on the stale banner.
- **`seam sync` requires an existing index**: it reconciles, it does not bootstrap. On a directory
  with no `.seam/seam.db` it errors `NO_INDEX` (run `seam init` first). It is CLI-only — there is no
  `seam_sync` MCP tool (the MCP server is read-only). A failed cluster recompute during sync surfaces
  as `cluster_count=-1` / `clusters_recomputed=false` / a "clusters: failed" warning (exit still 0 —
  the file reconcile succeeded); run `seam init` to rebuild clusters.
- **Homonym collapse**: the community detection graph is keyed on symbol NAME (not file+name),
  matching the `edges` table. Two files both defining a symbol named `helper` share one graph
  node — both get the same `cluster_id`. Visible in `clusters.size` (counts DB rows, not names).
- **SEAM_CLUSTER_MIN_SIZE default is 2**: pure singletons (symbols with no edges) are NOT
  persisted as clusters by default. Set to 1 to retain every symbol in its own cluster.
- **LLM naming is index-time only**: the MCP server read path is always 100% local.
  `SEAM_CLUSTER_NAMING=llm` only affects the `seam init` post-pass.
- **Search uses OR-join since Phase 3**: multi-term queries like `"parse issues board"` are
  built as `"parse"* OR "issues"* OR "board"*` so one non-matching word cannot zero the result.
  Results are re-ranked with name/path/test/cluster signals. If FTS returns zero rows a LIKE
  fallback runs, then a Damerau-Levenshtein fuzzy scan (up to SEAM_FUZZY_MAX_DIST=1 edit
  distance, capped at SEAM_FUZZY_MAX_CANDIDATES=500 symbols). A genuinely empty result from
  all three tiers still surfaces as an empty list — distinct from INVALID_QUERY.
- **`seam affected` uses the same edge graph as `seam impact`**: symbols not yet in the index
  (e.g. brand-new files before the next `seam init`) contribute zero dependents silently.
  Run `seam init` to refresh the index before running `seam affected` on new files.
- **`seam affected` depth cap**: traversal stops at SEAM_AFFECTED_DEPTH (default 5) hops.
  Raise via env var for deeper graphs. When a file has more symbols than SEAM_MAX_AFFECTED_SYMBOLS
  (default 50) the result carries `partial=true` — the affected set may be incomplete.
- **`--json` errors go to stdout, not stderr**: unlike CodeGraph (which emits ANSI errors on
  stderr even in JSON mode), Seam's `--json` mode always writes a structured envelope to stdout
  and exits non-zero. Shell pipelines and CI steps can branch on the `ok` key reliably.
- **MCP error contract ≠ CLI envelope — same code+message, different transport signal**:
  the CLI returns `{"ok":false,"error":{"code","message"}}`. The MCP tools (via `_finalize`
  in `seam/server/mcp.py`) instead **raise** on the handler's `{"error","message"}` sentinel so
  FastMCP sets `isError=True` with content `"<CODE>: <message>"` — because FastMCP only flips
  `isError` on a raise (returning a dict leaves `isError=False`, which an agent reads as success).
  A handler `None` ("nothing found") is normalized to `{"found": false}` (NOT empty content, NOT
  an error). Handlers/CLI/output.py are unchanged — only the MCP boundary normalizes.
- **`seam init` writes `.seam/.gitignore` (`*`)**: keeps the index (db/-shm/-wal) out of git so
  `seam_changes` never reports Seam's own artifacts as changed files. Written INSIDE `.seam/` —
  Seam still touches nothing outside `.seam/`. Idempotent (only written if absent).
- **Phase 4 enrichment fields are NULL until the next full `seam init` after upgrade**: the
  v4→v5 migration (run automatically on `connect()`) adds the five columns to the schema but
  does NOT backfill existing rows. Only a full re-index (`seam init`) populates signature,
  decorators, is_exported, visibility, and qualified_name for existing symbols.
- **`connect()` auto-migrates schema on open**: reads never break after a schema upgrade — the
  migration runs inline on the first `connect()` call. However, field values stay `null` for
  all rows that predate the re-index (see gotcha above).
- **Signature is FTS-searchable**: since Phase 4 the `symbols_fts` virtual table indexes
  `(name, docstring, signature)`. Type-shaped queries like `"conn sqlite3 Connection"` now
  match on parameter types and return annotations, not just symbol names.
- **`import_mappings` NOT backfilled by v5→v6 migration**: the v5→v6 migration (auto-run on
  `connect()`) creates the `import_mappings` table but does NOT populate it. `resolved_by`
  and import-promotion stay name-count-only until the next full `seam init`. Run `seam init`
  to enable Phase 5 resolution on an existing index.
- **Import promotion is read-time and requires `repo_root`**: `seam_changes` and `seam_affected`
  DELIBERATELY do not use import promotion — `changes.py` keeps name-count risk verdicts
  byte-stable across schema upgrades; `affected.py` does not read confidence at all.
  Import promotion applies only to `seam_impact`, `seam_trace`, and `seam_context`.
- **Go module-qualified imports are out of scope**: paths like `github.com/org/repo/pkg` are
  not resolved to indexed files. Go cross-package calls that use module-qualified import paths
  remain AMBIGUOUS if the target name has multiple declarations. Same-repo-relative Go paths
  resolve normally.
- **`.h` files always map to C, not C++** (Phase 9): SEAM_LANGUAGE_MAP routes `.h` → `"c"`.
  A C++-only project that puts declarations in `.h` headers parses those files with the C grammar,
  which handles most patterns (structs, typedefs, function prototypes) but misses C++-only constructs
  (templates, namespaces, in-class members). Use `.hpp`, `.hh`, or `.hxx` for C++ headers.
- **Nested classes have flat qualified names** (Phase 9): an inner class `Inner` inside `Outer`
  is indexed as `Inner` (not `Outer.Inner`), matching the existing Go/Rust precedent and the
  homonym-collapse gotcha. The edge graph is keyed on symbol name, so `Outer.Inner` would not match
  any edge target.
- **C++ pure-virtual method declarations are not extracted** (Phase 9): `virtual void f() = 0;`
  parses as `field_declaration` in the tree-sitter C++ grammar, not as `function_definition`. Only
  `function_definition` nodes are extracted. Concrete overriding implementations are indexed normally.
- **C function-pointer typedefs are not extracted** (Phase 9): `typedef int (*Cb)(int);` is silently
  skipped because the declarator is `abstract_function_declarator`, not `type_identifier`. Named-struct
  and enum typedefs (`typedef struct Foo Foo;`) are extracted correctly.
- **Java/C#/PHP import resolution returns `[]`** (Phase 9): import edges are extracted (e.g. `List`
  from `import java.util.List`) and stored in `import_mappings`, but `resolve_import_source()` returns
  `[]` for qualified package/namespace paths — classpath/NuGet/Composer layout is unavailable at index
  time. Cross-package Java/C#/PHP calls fall back to the name-count rule. Same-repo symbols whose name
  is unique in the index still resolve to EXTRACTED normally.
- **C/C++ system `#include <...>` resolution returns `[]`** (Phase 9): system headers like `<stdio.h>`
  produce an import edge with target `stdio`, but `resolve_import_source()` returns `[]` (no file found
  in the repo). These edges degrade to INFERRED/name-count at read time.
- **C++ visibility is null** (Phase 9): in-class access specifiers (`public:`, `private:`) are
  not yet threaded through to individual method symbols. All C++ symbols report `visibility=null`.
  Java, C#, and PHP visibility is extracted from access modifiers and is correct.
- **Ruby visibility is null** (Phase 9): Ruby's `private`/`protected` are method-call DSL constructs
  at runtime, not static AST nodes attached to `def`. Visibility cannot be determined statically without
  tracking which names appear after a `private` call — out of scope for this MVP.
- **On a multi-hop path, `resolved_by` reflects the FINAL hop**: path-level confidence uses
  the weakest-hop rule (AMBIGUOUS < INFERRED < EXTRACTED). `resolved_by` on the path entry
  reflects the provenance of the edge that produced the weakest-hop confidence, not of every
  hop individually.
- **Embeddings table is empty until `seam init --semantic`**: the v6→v7 migration (auto-run on
  `connect()`) creates the `embeddings` table but does NOT backfill it. Rows are populated
  only by `seam init --semantic` (or `seam sync --semantic`). Until then, `_is_hybrid_enabled`
  returns False and `seam_search`/`seam_query` behave byte-identically to pre-Semantic.
- **One-time model download on first `seam init --semantic`, then 100% local**: fastembed
  downloads the model (~67 MB for `BAAI/bge-small-en-v1.5`) on the FIRST `seam init --semantic`
  run; subsequent runs use the local fastembed cache at `~/.cache/huggingface/` (or the
  platform equivalent). The MCP read path (query embedding) never touches the network.
- **Changing `SEAM_EMBED_MODEL` requires a full `seam init --semantic` re-index**: vectors from
  different embedding models live in different metric spaces — mixing them silently corrupts
  cosine scores. When the stored model ≠ configured model, `semantic_candidates` detects the
  mismatch (COUNT WHERE model=? == 0), logs a WARNING, and returns `[]`. The engine falls
  through to pure-FTS5. Re-run `seam init --semantic` with the new model to rebuild.
- **`[semantic]` extra required**: `seam-code` base install does NOT include fastembed.
  Install with: `pip install 'seam-code[semantic]'` (or `uv sync --extra semantic`). When
  fastembed is absent, `is_available()` returns False, `index_embeddings` returns 0 (skipped),
  and the hybrid path degrades silently to FTS-only. An install hint is printed if `--semantic`
  is requested but fastembed is absent.
- **Gate skips real-model tests via `pytest.importorskip("fastembed")`**: all 5 skipped tests
  in the gate require the `[semantic]` extra (and would trigger a model download). They are
  skipped automatically when fastembed is not installed — the gate stays offline and fast.
  Synthetic vectors (`struct.pack` float32 blobs) are used for all other semantic tests.
- **`SEAM_IMPACT_MAX_BYTES` default is 0 (unlimited) — byte ceiling is opt-in**: when `SEAM_IMPACT_MAX_BYTES=0` (or negative), the byte ceiling is INACTIVE and the output is byte-identical to the pre-E1-FULL output. Set to a positive integer (e.g. `8000`) to activate. The knob is deliberately opt-in, following the same `=off`-revert discipline as the other E-series knobs — existing consumers that depend on a stable shape are unaffected until they opt in. Handler-layer, read-path only; no schema change, no re-index; `seam_changes`/`seam_affected` are never affected.
- **`SEAM_IMPACT_MAX_BYTES` is a CHARACTER count, not a token count**: the unit is `len(json.dumps(obj, ensure_ascii=False))` — the same serializer `seam/cli/output.py emit_json` uses, so the budget bounds the actually-rendered bytes. A real tokenizer is model-specific, changes between model generations, and requires an external dep that violates Seam's zero-external-services rule. Characters are a deterministic, model-independent ~4-chars/token proxy. A 8000-char budget ≈ 2000 tokens on most LLMs; tune to your context window with margin.
- **`SEAM_IMPACT_MAX_BYTES` hard ceiling holds for the response BODY, not the full CLI envelope**: the budget bounds the seam_impact response dict (entries + `byte_capped` + grown `truncated`), NOT the CLI `{"ok":true,"data":...}` wrapper (~15 extra chars) or MCP transport framing. A budget smaller than the irreducible envelope (all entry lists empty, mandatory metadata only) cannot be honored — every entry is dropped, the bare envelope is returned, and `byte_capped.omitted` reports the total dropped count. The Rich/CLI output explicitly says "trimmed to fit --max-bytes" (NOT "no dependents found") to prevent the dangerous false-safe where an agent concludes a symbol is safe to delete.
- **`truncated` conflates count-cap drops and byte-ceiling drops — use `byte_capped` to split them**: `truncated` is the merged total (count cap + byte ceiling drops, additive) so the invariant `risk_summary[dir][tier] - shown == truncated[dir][tier]` holds end-to-end. The CLI `--limit` footer reports only the count-cap portion (`total_omitted - byte_omitted`) so byte-ceiling drops are not misattributed to `--limit`; the separate byte-ceiling footer reports `byte_capped.omitted` explicitly. MCP callers that want to split the two causes should read `byte_capped.omitted` (byte drops) and subtract from the per-direction `truncated` totals (merged drops).
- **`seam_changes`/`seam_affected` are byte-stable regardless of `SEAM_IMPACT_MAX_BYTES`**: both call the analysis-layer `impact()` directly, below the handler where the byte ceiling runs. The byte ceiling is handler-layer-only; changing `SEAM_IMPACT_MAX_BYTES` has zero effect on their output. This is intentional — change-risk verdicts are safety-critical and must not silently shrink because of a response-size knob.
- **`--to-file` overrides `--lean`/`--limit`/`--max-bytes` for the on-disk file (WS5)**: when `--to-file` is active, `seam impact` calls `handle_seam_impact` with `verbose=True`, `limit=0`, `max_bytes=0` — the file always contains the full untrimmed, verbose blast radius regardless of any display flags. The one-line summary printed to stdout reflects this full result (it reads from `risk_summary`, which is always the honest pre-cap total). The other three commands (`seam trace`, `seam flows`, `seam context`) have no trimming flags, so `--to-file` makes no behavioral difference there — they always write what the handler returns. Files land in `.seam/out/` by default (`.seam/.gitignore: *` written by `seam init` ensures they are gitignored). `--to-file` is a BOOLEAN flag (bare `--to-file`, no value) because Typer 0.26 cannot express an optional-value option; pass `--to-file-path <dest>` for an explicit destination (a file path, or a trailing-`/` directory hint — the directory is created if absent). `--to-file-path` alone implies `--to-file` (file mode is on when either flag is set).

## Seam: Code Intelligence
This repository should use Seam itself for code intelligence.

**Decision rules:**
- Session start or unfamiliar area → `uv run seam schema --json`
- Find relevant code → `uv run seam query "<concept>" --json` or `uv run seam graph-search --json`
- Understand a function/class → `uv run seam context <symbol> --json`
- Before changing an existing symbol → `uv run seam impact <symbol> --json`
- Before committing → `uv run seam changes --json`
- Stale index → `uv run seam sync` or `uv run seam init`

**Index location:** `.seam/` (gitignored)
