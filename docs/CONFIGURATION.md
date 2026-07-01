# Configuration Reference

Every Seam setting is an environment variable with a sensible default. The authoritative
source is [`seam/config.py`](../seam/config.py) — this document mirrors it, grouped by area.

**No code outside `seam/config.py` reads `os.getenv` directly.** To change behavior, set the
environment variable before running `seam` (or export it in your shell / agent config).

### How to read the "Re-index?" column

- **read-time** — takes effect immediately on the existing index; no re-index needed.
- **`seam init`** — an extraction-time knob; the value is baked into the stored graph, so
  changing it requires a full re-index to take effect.
- **`init --semantic`** — affects only the embeddings, populated by `seam init --semantic`.

Most boolean knobs default to `on` and have an `off` that restores **byte-identical**
pre-feature behavior — a deliberate discipline so upgrades never silently change output.

---

## Core / indexing

| Variable | Default | Effect | Re-index? |
|----------|---------|--------|-----------|
| `SEAM_DB_PATH` | `.seam/seam.db` | Index location, relative to the project root. | n/a |
| `SEAM_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. | n/a |
| `SEAM_DEBOUNCE_MS` | `500` | Watcher debounce delay (ms) — coalesces rapid saves. | n/a |
| `SEAM_MAX_FILE_BYTES` | `1048576` (1 MB) | Files larger than this are silently skipped. | `seam init` |
| `SEAM_MAX_SIGNATURE_LEN` | `300` | Hard cap on stored signature length (truncated with `…`). | `seam init` |

`SEAM_LANGUAGE_MAP` (the extension → language table) is defined in `config.py` and is not an
environment variable — see [README, Supported languages](../README.md#supported-languages).

---

## Edge extraction (graph richness)

All extraction-time. `off` = byte-identical to before that edge kind existed; toggling needs
`seam init`.

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_INHERITANCE_EDGES` | `on` | Emit `extends` / `implements` edges. |
| `SEAM_COMPOSITION_EDGES` | `on` | Emit `holds` edges (typed stored fields + constructor params). |
| `SEAM_PARAM_EDGES` | `on` | Emit `uses` edges (function → user types referenced as parameters). Higher-volume than `holds` → widens blast radius. |
| `SEAM_FIELD_ACCESS_EDGES` | `on` | Emit `reads` / `writes` edges + index fields as `kind='field'` symbols. |
| `SEAM_TYPE_INFERENCE` | `on` | Receiver-type inference (Python + TS/JS): resolve `obj.method()` to `Type.method`. |
| `SEAM_SWIFT_TYPE_INFERENCE` | `on` | Swift-specific receiver-type inference (independent of the above). |
| `SEAM_TOKENIZE_IDENTIFIERS` | `on` | Write camelCase/snake_case-split tokens into `search_text` so `"push to talk"` matches `PushToTalkMonitor`. |

P3.2 config/resource extraction has no separate knob. It indexes safe declaration files
(`.env.example`, selected JSON/TOML/YAML config files, manifests, compose files) and literal
Python/TS/JS config reads, but deliberately skips value-bearing `.env` files by default and
never persists raw config values.

---

## Edge synthesis (post-pass)

Whole-graph post-pass run by `seam init` and `seam sync` (gated), never the watcher.

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_EDGE_SYNTHESIS` | `on` | Master switch for the synthesis post-pass (interface→impl fan-out + closure/event channels). `off` = no synthesized edges. |
| `SEAM_SYNTHESIS_FANOUT_CAP` | `40` | Per-channel fan-out cap. A2/closure channels **truncate** to N; the event-emitter channel **skips** an event whose handler count exceeds N (likely a false-positive mega-event). `0` = no cap. |
| `SEAM_SYNTHESIS_MAX_SOURCE_BYTES` | `52428800` (50 MB) | Total source-load budget for the source-text channels. `0` = unlimited. |

---

## Import resolution & confidence

Read-time (resolution recomputed per query) — except mapping extraction, which is index-time.

| Variable | Default | Effect | Re-index? |
|----------|---------|--------|-----------|
| `SEAM_IMPORT_RESOLUTION` | `on` | Import-promotion step A (resolve a same-file import to a unique declarer → `EXTRACTED`). `off` extracts no mappings. | `seam init` (mappings) |
| `SEAM_BUILTIN_FILTERING` | `on` | Tag count==0 known-builtin names as `INFERRED`/`builtin` instead of `unresolved`. | read-time |
| `SEAM_MAX_IMPORT_CANDIDATES` | `25` | Cap on declaring files evaluated per import lookup. | read-time |
| `SEAM_PROXIMITY_MAX_CANDIDATES` | `25` | Cap on collision candidates ranked by path proximity (the AMBIGUOUS `best_candidate`). | read-time |
| `SEAM_BARREL_DEPTH` | `3` | Max hops to chase a named import through re-export barrels (`index.ts`). `0` disables. | read-time |

---

## `seam_impact` output shaping

All handler-layer / read-time. **None of these affect `seam_changes` or `seam_affected`**,
which call the analysis layer directly (their verdicts are byte-stable regardless).

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_IMPACT_MAX_RESULTS` | `25` | Per-tier entry cap. `0` = unlimited. `risk_summary` always reports the honest full count. |
| `SEAM_IMPACT_RELEVANCE_SORT` | `on` | Rank external dependents ahead of the target's own members before the cap (so self-refs are dropped first). `off` = prior ordering. |
| `SEAM_IMPACT_SELF_REF` | `rank` | `rank` (keep self-refs, sort last, lossless) · `hide` (drop + report `hidden_self_refs`) · `show` (legacy, no special treatment). |
| `SEAM_IMPACT_OMIT_NULL_CANDIDATE` | `on` | Drop `best_candidate` from an entry when it is null (lossless — null ≡ absent). `off` keeps `best_candidate: null`. |
| `SEAM_IMPACT_MAX_BYTES` | `0` (off) | Opt-in hard character ceiling on the response body; trims from the least-valuable end until it fits. `0` = unlimited. |
| `SEAM_EDGE_PROVENANCE` | `on` | Surface `kind` + `synthesized_by` on impact entries and trace hops. `off` = neither field emitted. |
| `SEAM_IMPACT_STEER` | `on` | Emit `next_actions` truncation hints when entries were trimmed. `off` = no `next_actions` key. |

---

## Index staleness banner (P2)

Handler-layer / read-time, on the 5 graph-traversal tools. `seam status` always checks
freshness independently of `SEAM_STALENESS_CHECK`.

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_STALENESS_CHECK` | `on` | Attach the `index_status` banner when the index is stale. `off` = no banner, no stat IO. |
| `SEAM_STALENESS_SCAN_CAP` | `200` | Max files stat'd per verdict (newest by `indexed_at`). `0` = unlimited (not recommended on large repos). |
| `SEAM_STALENESS_TTL_SECONDS` | `5` | Per-process cache TTL. Only *stale* verdicts are cached. `0` disables caching. |

---

## Clustering (Louvain)

Cluster recompute runs in `seam init` / `seam sync` (gated), never the watcher.

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_CLUSTER_NAMING` | `deterministic` | `llm` opts into LLM cluster labels (index-time only; read path stays local). |
| `SEAM_LLM_API_KEY` | _(unset)_ | Required for `llm` naming; absent → silently falls back to deterministic. |
| `SEAM_LLM_MODEL` | `gpt-4o-mini` | Model used for LLM naming. |
| `SEAM_CLUSTER_MIN_SIZE` | `2` | Min community size persisted as a cluster. `1` retains singletons. |
| `SEAM_CLUSTER_CONFIDENCE_FILTER` | `1000` | On graphs larger than this, only high-trust edges feed Louvain. `off` disables the filter; `0` always filters. |

---

## Semantic search (opt-in)

Requires the `semantic` extra and `seam init --semantic`.

| Variable | Default | Effect | Re-index? |
|----------|---------|--------|-----------|
| `SEAM_SEMANTIC` | `off` | Master switch for hybrid FTS5 + embedding search. | read-time |
| `SEAM_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (384-dim, quantized ONNX, ~67 MB, MIT). Changing it requires re-embedding. | `init --semantic` |
| `SEAM_SEMANTIC_LIMIT` | `20` | Top-k semantic candidates fetched before RRF merge. | read-time |
| `SEAM_SEMANTIC_SCAN_CAP` | `20000` | Max embedding rows loaded per scan (memory bound). | read-time |
| `SEAM_RRF_K` | `60` | Reciprocal Rank Fusion smoothing constant (Cormack et al., SIGIR 2009). | read-time |

---

## Change risk & affected tests

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_MAX_IMPACT_SYMBOLS` | `50` | Max changed symbols analyzed per `seam_changes` call (sets `partial` when exceeded). |
| `SEAM_AFFECTED_DEPTH` | `5` | Max upstream hops for `seam_affected` traversal. |
| `SEAM_MAX_AFFECTED_FILES` | `200` | Max changed files accepted per `seam_affected` call. |
| `SEAM_MAX_AFFECTED_SYMBOLS` | `50` | Max symbols analyzed per file in `affected()` (sets `partial`). |

---

## Search fallback (fuzzy)

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_FUZZY_MAX_DIST` | `1` | Max Damerau-Levenshtein edit distance for the fuzzy fallback (when FTS + LIKE return nothing). |
| `SEAM_FUZZY_MAX_CANDIDATES` | `500` | Max symbol names scanned in the fuzzy fallback. |

---

## Name resolution (Tier A)

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_NAME_EXPANSION_CAP` | `50` | Max member bare names fanned out when a class/interface is used as a seed. |
| `SEAM_BARE_RESOLVE_CAP` | `25` | Max rows from the bare-name suffix scan (`LIKE '%.name'`). `0` = unlimited (not recommended). |

---

## `seam_context_pack` (neighbor bundle)

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_PACK_NEIGHBOR_LIMIT` | `10` | Max enriched callers and max enriched callees per bundle. |
| `SEAM_PACK_PER_FILE_CAP` | `3` | Max neighbor entries from any single file (homonym diversity). |
| `SEAM_PACK_MAX_COMMENTS` | `10` | Max WHY/HACK/NOTE comments in the bundle. |
| `SEAM_PACK_RELEVANCE_RANK` | `on` | Rank neighbors by personalized-PageRank relevance to the seed before the caps. `off` = min_id order. |
| `SEAM_RWR_MAX_NODES` | `500` | Max nodes in the bounded RWR subgraph. |
| `SEAM_RWR_MAX_DEPTH` | `3` | Max BFS hops from the seed when collecting the RWR subgraph. |

---

## `seam_flows` (execution flows)

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_FLOW_ENTRY_LIMIT` | `20` | Max entry points listed. |
| `SEAM_FLOW_MAX_DEPTH` | `6` | Max depth when expanding one flow tree. |
| `SEAM_FLOW_MAX_BREADTH` | `8` | Max callees per node in a flow tree. |
| `SEAM_FLOW_REACH_DEPTH` | `5` | BFS depth used to score entry-point reach. |
| `SEAM_ENTRY_SCORE` | `on` | Rank entry points by `entry_score × reach` (framework-aware) instead of raw reach. `off` = raw reach. |

---

## `seam_structure` (repo map)

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_STRUCTURE_MAX_DEPTH` | `8` | Max tree depth — counts dir + file + container levels, not just directories. |
| `SEAM_STRUCTURE_MAX_NODES` | `2000` | Max non-root nodes; `<= 0` = unlimited. |
