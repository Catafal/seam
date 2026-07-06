# Seam

<p align="center">
  <img src="docs/assets/seam-hero.png" alt="A codebase as a graph: hexagonal symbol nodes connected by typed edges around a central indexed core; a queried symbol (chartreuse) traces through the core to an impacted dependent (magenta)" width="100%">
</p>

**Local code intelligence for AI agents.** Index a codebase once; agents query its structure instead of re-discovering it with `grep` every session.

`v0.3.0` · 12 languages · 19 MCP tools · SQLite-backed · **zero network calls at query time** · gate-green (~3,055 tests)

[![CI](https://github.com/Catafal/seam/actions/workflows/ci.yml/badge.svg)](https://github.com/Catafal/seam/actions/workflows/ci.yml)

---

## The problem

An AI coding agent starts every session blind. To answer "what breaks if I change `init_db`?" it greps for the name, opens each hit, reads the surrounding code, follows the imports, and reconstructs the call graph by hand — spending thousands of tokens rebuilding structural knowledge that was true last session and the session before.

That structure is *computable*. A parser already knows that `index_one_file` calls `init_db`, that `Server` holds a `Client`, that `UserView` implements `Renderable`. Seam computes it once, stores it in a local SQLite graph, and exposes it over a handful of MCP tools so the agent **asks instead of greps**.

```text
Without Seam:  "what calls init_db?"  → grep → 14 files → read each → trace imports → ~30k tokens, often wrong
With Seam:     seam_impact init_db    → blast radius by risk tier → ~4.5k tokens, graph-accurate
```

<p align="center">
  <img src="docs/assets/seam-demo.gif" alt="Terminal demo: seam init indexes the repo, seam impact init_db returns the blast radius by risk tier, seam search ranks concept matches" width="100%">
</p>

The win compounds: every structural question — callers, blast radius, call paths, functional areas, which tests to run — is one tool call against a graph that stays fresh automatically.

## The mental model

Think of Seam as **a compiler's symbol table and call graph for your whole repository**, kept fresh in the background and exposed over MCP and a CLI.

```text
  source files                tree-sitter            .seam/seam.db
  (12 languages)   ─────────▶  structural    ─────▶  SQLite + FTS5        ◀── file watcher
                               parsing               (symbols + edges          (debounced
                                                       + clusters + FTS)         re-index)
                                                            │
                                            ┌───────────────┴───────────────┐
                                            ▼                               ▼
                                     MCP server (stdio)              CLI read commands
                                     19 read-only tools              schema / query / impact …
                                            │                               │
                                            └──────────────┬────────────────┘
                                                           ▼
                                            AI agent (Claude Code · Cursor · Codex)
```

Three properties define it:

- **Indexed once, kept honest.** `seam init` builds the graph; an optional `watchdog` daemon re-indexes edited files in the background. Graph tools surface freshness guidance when an index is stale, so agents can run `seam sync` instead of trusting old evidence.
- **100% local.** The index is a per-project SQLite file (`.seam/seam.db`). The read path makes **no network calls** — no API keys, no cloud, no telemetry.
- **A graph, not a search box.** Symbols are nodes; calls, imports, inheritance, composition, and field access are typed edges. Every answer is graph traversal, not string matching.

---

## Quickstart

Published on PyPI as **`seam-code`** — the PyPI name `seam` belongs to an unrelated package, so the *distribution* is `seam-code` while the import package and the `seam` command keep the short name.

```bash
pip install seam-code                    # CLI only
pip install 'seam-code[server]'          # + the MCP server (seam start)
pip install 'seam-code[semantic]'        # + semantic search (fastembed, ONNX/CPU, ~67 MB model on first run)
pip install 'seam-code[semantic-ann]'    # + ANN acceleration for semantic search (sqlite-vec KNN)
pip install 'seam-code[web]'             # + the Seam Explorer web UI (FastAPI)
```

Or with **npx** (JS/TS projects, no Python toolchain required):

> **Prerequisite:** install [uv](https://docs.astral.sh/uv/getting-started/installation/) first — the shim delegates to `uvx` (bundled with uv) to download and run `seam-code` from PyPI. No Python install needed; uv manages it.

```bash
npx @catafal/seam init                    # index the project
npx @catafal/seam search "auth token"    # full-text search
npx @catafal/seam impact init_db --json  # blast radius
```

The npm package version mirrors the PyPI version exactly — `@catafal/seam@0.4.0` always installs `seam-code==0.4.0`. No drift, no silent upgrades.

Or from source with uv:

```bash
git clone <repo-url> && cd seam
uv sync                     # CLI only — no MCP server, no semantic search
uv sync --extra server      # + the MCP server (`seam start`) — adds the `mcp` package
uv sync --extra semantic    # + semantic search (fastembed, ONNX/CPU, no torch, ~67 MB model on first run)
uv sync --extra web         # + the Seam Explorer web UI (FastAPI)
# everything: uv sync --extra server --extra semantic --extra web
```

### Use it from the CLI (no server needed)

Every read command queries the SQLite index directly — the full feature set works with no MCP server running:

```bash
cd /path/to/your/project
uv run seam init                       # index the project (writes .seam/seam.db)
uv run seam search "auth token"        # full-text (hybrid semantic when enabled)
uv run seam query "verify user login"  # concept search + 1-hop graph expansion
uv run seam graph-search --recipe production-hotspots --json  # structural graph search
uv run seam graph-search --list-recipes  # named recipes for common agent questions
uv run seam architecture --json        # bounded repo architecture briefing
uv run seam context authenticate_user  # 360° view: callers, callees, cluster, signature
uv run seam plan authenticate_user     # inspect/test plan before editing a symbol
uv run seam impact  authenticate_user  # blast radius by risk tier
uv run seam structure                  # whole-repo directory/container map
# also: trace · changes · why · clusters · affected · flows · pack · status · sync
```

### Wire it to an AI agent

`seam install` defaults to writing a **token-lean CLI playbook** into the repo so the agent queries via the `seam` CLI (cheaper than MCP — the CLI's `--quiet` mode is ~14× leaner than the leanest MCP call, and there's no ~6k-token standing tool-schema cost). It renders into each agent's cheapest native mechanism: a Claude Code skill, a Cursor agent-requested rule, an `AGENTS.md` block for Codex/Zed, VS Code Copilot instructions, or `GEMINI.md`.

```bash
uv run seam install                       # CLI guidance for Claude Code (skill + CLAUDE.md hook)
uv run seam install --target all          # guidance for every registered target
uv run seam install --auto --print-config # detect supported/relevant targets; write nothing
uv run seam install --with-mcp            # ALSO wire the MCP server (needs the `server` extra)
uv run seam install --print-config        # preview everything, write nothing
uv run seam uninstall                     # reverse it (removes guidance + MCP config)
```

Use `--auto --print-config` when you want a compact setup plan first: it reports
detected/supported targets, exact guidance paths, optional MCP preview paths when
combined with `--with-mcp`, and explicit follow-up install commands. Auto mode is
preview-only; choose `--target <name>` or `--target all` for writes.

The guidance teaches the agent the escalation ladder (`--quiet` → `--json --lean` → full `--json`), how to keep the index fresh (`seam init` / `seam sync`), and when to reach for each command. It's idempotent (a marker-delimited block in `AGENTS.md`/`CLAUDE.md`, never duplicated, foreign content preserved) and reversible.

Prefer native tool-calling? Add `--with-mcp` (install the `server` extra first). To wire MCP by hand, add to `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "seam": { "type": "stdio", "command": "seam", "args": ["start", "/path/to/your/project"] }
  }
}
```

On a clean checkout, the first `seam start` performs one local graph-only `seam init`
before the MCP server starts. Use `seam start --no-init /path/to/project` when
scripts or CI should fail fast instead. Existing but stale indexes are never rebuilt
silently; agents still see freshness guidance and should run `seam sync` explicitly.

### Shared team index — CI publishes, developers fetch or import

For teams, running `seam init` on every developer machine (and keeping each one fresh) is unnecessary overhead. The **shared-index flow** lets CI build the index once on each merge to `main`, publish it as an artifact, and let developers download a pre-built index in seconds instead of waiting for full indexing.

**Step 1 — wire the CI workflow (one-time)**

Copy `.github/workflows/seam-index.yml` (included in this repo as a template) into your project and adapt the upload step to your artifact store. The template uses `gh release create` with no extra pinned actions; teams can swap the upload step for S3, GCS, Artifactory, or any store that serves the archive over HTTPS.

CI can produce the same artifact directly:

```bash
seam export-index --json
# seam pack-index --json remains as a compatibility alias
```

The archive contains `seam.db`, optional vector sidecars, and an embedded `manifest.json` with the producer version, schema version, repository fingerprint, git metadata, and content flags. The archive is paired with `seam-index.sha256`.

**Step 2 — set `SEAM_INDEX_ARTIFACT_URL` (per developer)**

Add to your shell profile (or `.env`):

```bash
# The {sha} placeholder is expanded by `seam fetch` to the current HEAD SHA,
# then walked back through first-parent history until a published artifact is found.
export SEAM_INDEX_ARTIFACT_URL="https://github.com/<owner>/<repo>/releases/download/seam-index-{sha}/seam-index.tar.gz"
```

**Step 3 — run `seam fetch`**

```bash
seam fetch --strict  # require checksum + manifest identity before landing the index
seam fetch           # compatibility mode: download, verify checksum when present, sync delta
seam fetch --semantic  # also embed any symbols added locally after the CI cut-point
```

`seam fetch` is the **one intentional setup-time network path** in Seam. Every other command is fully offline — query time makes zero network calls (verified at the syscall level by `.github/workflows/no-egress.yml`). `seam fetch` is excluded from that proof because it is a deliberate one-time provisioning download, exactly like `seam init --semantic`'s model bootstrap.

Use `--strict` for automation and team setup scripts. Strict fetch refuses to replace the local index unless the checksum sidecar exists, the archive has a manifest, the schema version is supported, and the artifact identity matches the resolved git revision/repository. Compatibility fetch still supports older artifact stores, but those paths are surfaced as permissive/unverified when checksum or manifest evidence is absent.

When a checksum sidecar is available, `seam fetch --json` also reports `artifact.verified`, the checksum, and the embedded manifest when present. Pre-manifest artifacts with a valid checksum still fetch as verified legacy artifacts in compatibility mode and report `artifact.manifest=null`. Older artifact stores that do not publish `seam-index.sha256` still work in compatibility mode, but they are reported as unverified so automation can reject them or move to `--strict`.

After `seam init`, `seam fetch`, or `seam import-index`, `seam schema --json` includes a `bootstrap` block. That block tells agents whether the current index is a local build, a verified artifact, an unverified artifact, stale, or provenance-unknown. The persisted record lives in `.seam/bootstrap.json` and stores only safe metadata: source, verification state, checksum, manifest/schema version, git SHA, a hash of the remote, sync summary, and semantic-sync status. It never stores artifact URLs, bearer tokens, source text, or absolute CI paths.

**Local archive workflow**

Use the local lifecycle commands when an artifact has already been downloaded or moved through a trusted internal channel:

```bash
seam inspect-index /path/to/seam-index.tar.gz --json
seam import-index /path/to/seam-index.tar.gz --path /repo --json
```

`inspect-index` is read-only. `import-index` requires the checksum sidecar, rejects unsupported manifest/schema versions, refuses repository identity mismatches unless `--allow-repo-mismatch` is explicit, stages extraction in a temporary directory, opens the staged SQLite DB, rebases paths to the local checkout by default, then atomically swaps `.seam/`. Git remote and HEAD are used when available; the path fingerprint is a fallback for non-git artifacts. Validation, extraction, rebase, and swap failures preserve the existing index. Pass `--sync` only when you want local edits incorporated immediately after the artifact has landed.

**After `seam fetch` or `seam import-index` runs**, the index is local. All subsequent reads (`seam query`, `seam impact`, `seam context`, MCP tools) are offline. Run `seam fetch` again when you want to pull a newer CI build.

**Caveats**

- **Model match**: the embedding model used in CI (set via `SEAM_EMBED_MODEL` in the workflow, defaulting to `BAAI/bge-small-en-v1.5`) must match the model on developer machines. A mismatch is safe — `seam fetch` detects it and the fetched index degrades gracefully to FTS5-only search. Semantic search re-enables automatically after `seam fetch --semantic`.
- **Staleness banner**: after `seam fetch`, the index reflects the CI commit. If local files have diverged (new code, edited files), `seam_impact` and other graph tools will attach an `index_status: {stale: true, ...}` banner. This is the normal signal to re-run `seam fetch` (for a newer CI build) or `seam sync` (to incorporate local edits into the fetched base).

### Cross-repo workspaces (CLI, opt-in)

`seam workspace` lets you query several already-indexed repos as one explicit local trust
set. It does not scan sibling directories, does not merge databases, and does not change
the default single-repo behavior of `seam query`, `seam graph-search`, `seam impact`, MCP
tools, or the Explorer.

```bash
seam workspace init /path/to/workspace --json
seam workspace add api /path/to/api /path/to/workspace --json
seam workspace add web /path/to/web /path/to/workspace --json
seam workspace status /path/to/workspace --json
seam workspace graph-search /path/to/workspace --recipe route-entrypoints --json
seam workspace route-callers /path/to/workspace --method GET --path /api/users --json
seam workspace matches /path/to/workspace --config-key DATABASE_URL --json
seam workspace impact /path/to/workspace authenticate_user --json
```

The workspace registry lives at `.seam/workspace.json` under the selected workspace root.
Adding a repo writes only that registry; registered child repos are opened read-only during
status and query commands. Results carry a repo alias plus both `local_uid` and a
workspace UID formatted as `repo_alias:local_uid`, so follow-up snippets can target the
right checkout without depending on absolute paths. Config/resource matching keeps the
existing no-secret contract: key names and redacted value shape are allowed, raw values are
not returned.

---

## The 19 MCP tools

Grouped by the question an agent is asking. Every tool is **read-only**; the server never writes the index.

### Find code

| Tool | Answers | Key args |
|------|---------|----------|
| `seam_schema` | "What can this index answer?" — schema version, counts, optional capabilities, freshness, tool guidance, and warnings. | `verbose` |
| `seam_architecture` | "What kind of repo is this?" — bounded architecture briefing with physical areas, clusters, entry points, routes, configs, resources, infra, hotspots, boundaries, edge mix, warnings, and next calls. | `scope`, `sections` (MCP) / `section` (CLI/Web), `limit`, `max_bytes` |
| `seam_search` | "Where is text X mentioned?" — FTS5 over names + docstrings + signatures, with fuzzy fallback; hybrid keyword+semantic when enabled. | `text`, `limit`, `semantic` |
| `seam_query` | "Find all code related to concept X." — FTS5 match + 1-hop graph expansion, rescored by name/path/cluster signals. | `concept`, `limit`, `semantic` |
| `seam_graph_search` | "Which symbols match this graph shape?" — typed structural discovery by kind, edge kind, degree, path, preset, route/config/resource nodes, and optional one-hop previews. | `kind`, `edge_kind`, `direction`, `preset`, `limit` |
| `seam_snippet` | "Show me the exact code for this result." — bounded source text by UID, symbol, symbol+file, or file+line, with freshness/truncation warnings and optional same-file neighbor hints. | `uid`, `symbol`, `file`, `line`, `include_neighbors` |

### Understand a symbol

| Tool | Answers | Key args |
|------|---------|----------|
| `seam_context` | "Show me everything about symbol X." — callers, callees, signature, cluster, `field_readers`/`field_writers`, and static `test_callers`/`tested_symbols`. Resolves bare/qualified/class names and merges homonyms. | `symbol` or `uid` |
| `seam_context_pack` | "Give me a paste-ready bundle for X." — `seam_context` + WHY comments + enriched neighbors + direct relationship evidence + caveats + next calls in one call; neighbors ranked by relevance to the seed. | `symbol` |
| `seam_why` | "Why is this code like this?" — the `WHY`/`HACK`/`NOTE`/`TODO`/`FIXME` comments. | `symbol`, `path` |

### Assess change risk

| Tool | Answers | Key args |
|------|---------|----------|
| `seam_impact` | "What breaks if I change X?" — blast radius bucketed into risk tiers, with provenance, summary counts, and a hard byte/entry budget. | `target`, `direction`, `max_depth`, `limit`, `max_bytes` |
| `seam_plan` | "What should I inspect and test?" — target-mode composes context, upstream impact, and indexed test evidence; diff-mode composes changed symbols and affected tests. | `symbol`, `mode`, `scope`, `base_ref`, `max_depth` |
| `seam_suspects` | "What should I review before cleanup?" — conservative symbol/file suspect candidates with blockers, risk, caveats, and follow-up calls; never deletion proof. | `mode`, `target`, `file_pattern`, `kind`, `visibility`, `test_scope`, `limit` |
| `seam_grounding` | "Which docs/specs explain this?" — local ADR/PRD/roadmap/task anchors that explicitly mention a symbol, file, route, config key, resource, or spec question. Doc links are grounding evidence, not dependency edges. | `symbol`, `file`, `route`, `config_key`, `resource`, `query`, `doc_kind`, `status`, `include_snippets` |
| `seam_changes` | "Is my current diff risky?" — git diff → changed symbols → overall risk level. | `scope`, `base_ref` |
| `seam_affected` | "Which tests should I run?" — changed files → impacted test files via reverse-dependency traversal. | `changed_files`, `depth` |

### Navigate the graph

| Tool | Answers | Key args |
|------|---------|----------|
| `seam_trace` | "How does X reach Y?" — shortest call/dependency path, hop by hop, with edge kind + confidence. | `source`, `target`, `max_depth` |
| `seam_flows` | "Where does execution start, and where does it go?" — entry points ranked by reach, or one entry's forward call-chain tree. | `entry` (optional) |

### Map the repository

| Tool | Answers | Key args |
|------|---------|----------|
| `seam_clusters` | "What are the functional areas?" — Louvain communities (semantic coupling), or the members of one. | `cluster_id` (optional) |
| `seam_structure` | "How is the repo laid out?" — the filesystem → directory → file → container tree with symbol counts and area labels. | `path`, `depth`, `nodes` |

> **Lean mode.** The enrichment-carrying tools (`seam_context`, `seam_trace`, `seam_impact`, `seam_context_pack`) accept `verbose=false` (CLI `--lean`) to drop heavy enrichment fields and shrink the response for tight token budgets. `seam_context_pack` keeps compact relationship evidence in lean mode so agents still see why caller/callee claims are believed. `seam_impact` additionally supports a hard `max_bytes` ceiling and emits `next_actions` hints when results are trimmed.

---

## Supported languages

Twelve languages, parsed with [tree-sitter](https://tree-sitter.github.io/). All share the same tools, symbol kinds, edge kinds, and enrichment fields.

| Language | Extensions | | Language | Extensions |
|----------|-----------|-|----------|-----------|
| Python | `.py` | | C# | `.cs` |
| TypeScript | `.ts` `.tsx` | | Ruby | `.rb` |
| JavaScript | `.js` `.mjs` `.cjs` | | C | `.c` `.h` |
| Go | `.go` | | C++ | `.cpp` `.cc` `.cxx` `.c++` `.hpp` `.hh` `.hxx` |
| Rust | `.rs` | | PHP | `.php` |
| Java | `.java` | | Swift | `.swift` |

> **Kotlin is not yet supported** — the available tree-sitter-kotlin grammar mis-parses common constructs (interfaces, objects, classes with constructors). Tracked for a future release. See [`docs/adr/009-swift-support.md`](docs/adr/009-swift-support.md).
>
> Per-language extraction caveats (e.g. `.h` maps to C; C++ method visibility; Ruby dynamic visibility) are documented in [`docs/CONCEPTS.md`](docs/CONCEPTS.md#per-language-notes).

---

## Benchmarks

Three reproducible, deliberately-honest benchmarks back the pitch.

**Token reduction vs. `grep` + read** — [`docs/benchmark.md`](docs/benchmark.md)

> **91.8% fewer tokens** vs. realistic whole-file grep (88.7% vs. a conservative
> windowed grep), across 6 structural questions — 314k → 26k estimated tokens. Every
> query is a win; the largest are the things grep is worst at (`seam_clusters` 97.9%,
> `seam_search` 97%).

Reproduce: `seam init . && python benchmarks/run_benchmark.py`.

**Head-to-head vs. CodeGraph / graphify** — [`docs/competitive-benchmark.md`](docs/competitive-benchmark.md)

Run on an external repo Seam had never seen:

- **Fastest index — 0.26s** (others 0.60–4.04s) and **smallest footprint — 1.1 MB** (others 3.4–45 MB).
- **The only tool that writes nothing outside its own `.seam/` folder** — others mutate your `AGENTS.md`/`CLAUDE.md` or config on install.
- Leanest answer on concept search.

Both are static *retrieval-context* proxies (chars ÷ 4), not live agent-session A/Bs —
the docs state their own limitations. The competitive run predates the Phase 8 impact-tool
fix, so current output is leaner than it shows.

**Agent answerability** — [`docs/agent-answerability-benchmark.md`](docs/agent-answerability-benchmark.md)

Runs 26 natural-language coding-agent questions against the deterministic eval fixture and
scores answer facts, evidence, caveats, output cost, round trips, latency, freshness, and
false confidence. Reproduce with `make eval-answerability`.

---

## Core concepts

A short tour — the full treatment is in [`docs/CONCEPTS.md`](docs/CONCEPTS.md).

**The graph.** Nodes are symbols (`function`, `class`, `method`, `interface`, `type`, `field`, `route`, `config`, `resource`). Edges are **typed** and capture fifteen relationships:

| Edge kind | Captures |
|-----------|----------|
| `call` | one symbol invokes another |
| `import` | a module/symbol import |
| `extends` · `implements` | class inheritance / interface conformance |
| `instantiates` | `new Foo()` / struct-literal construction |
| `holds` | a class **stores** a typed field/property (composition / DI) |
| `uses` | a function **references** a user type as a parameter (signature coupling) |
| `reads` · `writes` | a field/property is read or written (data-flow) |
| `http_calls` | a symbol calls a literal HTTP route |
| `reads_config` | code reads a literal config or env key |
| `configures` | a config key describes a runtime resource |
| `raises` | a symbol explicitly raises or throws a visible exception type |
| `catches` | a symbol explicitly handles a typed exception |
| `tests` | a test symbol statically exercises or names a production symbol |

Edges are keyed by **symbol name**, not row id — this is what lets the watcher re-index one file independently without rewriting the whole graph. Route, config, and resource nodes live as normal `symbols.kind` values; route metadata lives in `routes`, config metadata lives in `config_keys`, and resource metadata lives in `resources`. Config metadata stores key names and redacted value shape only, never raw values. Docker Compose and Dockerfile evidence is indexed as resource nodes for services, images, Dockerfiles, build contexts, ports, stages, env files, volumes, and networks; dynamic/interpolated values are skipped. Literal HTTP evidence covers direct `fetch`, literal Axios calls, local wrappers named `apiFetch`, and literal Python module-import calls through `requests`/`httpx`/`aiohttp`; dynamic URLs and third-party absolute URLs are skipped. Traversal is kind-agnostic, and exception/test edges are exposed through graph search, trace, context, and architecture. Default blast-radius impact intentionally excludes `raises`/`catches` and `tests` so failure-path and test-evidence relationships do not inflate production change-risk tiers.

For infra evidence, start with `seam architecture --section infra --json`, then use `seam graph-search --kind resource --json` for concrete resource nodes and previews.

**Confidence tiers.** Each edge resolves to `EXTRACTED` (target is unambiguous), `AMBIGUOUS` (name collides — verify), or `INFERRED` (heuristic / cross-module). A multi-hop path is only as strong as its weakest hop. Each result carries `resolved_by` provenance explaining *how* the tier was decided.

**Risk tiers.** `seam_impact`, `seam_changes`, and `seam_plan` bucket dependents by distance: `WILL_BREAK` (d=1, must update), `LIKELY_AFFECTED` (d=2, should test), `MAY_NEED_TESTING` (d≥3, test if critical). `seam_changes` rolls these up into `low` → `medium` → `high` → `critical`; `seam_plan` turns the same evidence into ranked inspect items and a bounded pytest command.

**Cleanup suspects.** `seam_suspects` is the review surface for possible dead code and orphan files. It combines absence signals with blockers such as public/exported API shape, static test evidence, route/config/resource conventions, field access, inheritance, imports, and indexed contained-symbol usage. Its output is deliberately framed as suspect strength and removal risk, not as "unused" proof, because static absence can miss dynamic runtime use.

**Docs/spec grounding.** `seam_grounding` indexes lightweight local Markdown anchors from ADRs, PRDs, roadmaps, tasks, guides, and implementation notes, then resolves explicit references to indexed code evidence when possible. It preserves provenance, confidence, status, and caveats, and it never feeds doc links into impact traversal.

**Clusters.** A pure-Python Louvain pass groups symbols into functional areas by coupling. Labels are deterministic by default (`dir/file — top symbol`) or, opt-in, LLM-generated at index time only.

**Edge synthesis.** Static parsing can't see runtime polymorphism. A post-pass over the whole graph synthesizes the edges parsing structurally misses — interface→implementation fan-out, closure-collection dispatch, event-emitter handlers — tagged with their provenance channel.

**Semantic search.** Opt-in local embeddings (fastembed, ONNX on CPU) merge with FTS5 via Reciprocal Rank Fusion, so `"retry logic"` can surface `_backoff_with_jitter` even with no shared token. The model downloads once, then runs 100% locally.

**KNN scaffold via sqlite-vec (WS2b).** The optional `[semantic-ann]` extra wires in `sqlite-vec` as a third tier in the read path (**vec0 KNN → mmap → SQL brute-force**), each falling through to the next on any structural issue. This tier is **off by default** (`SEAM_VEC_ANN=off`) and ships as a **forward-compatible scaffold**, not a performance improvement today.

> **Benchmark reality (sqlite-vec v0.1.9):** sqlite-vec's `vec0` virtual table performs **exact** brute-force KNN — it has no approximate-nearest-neighbour index (no HNSW, no IVF). Measured on synthetic 384-dim float32 embeddings it is **~5× slower** than the built-in numpy matmul path at every scale tested (10k–250k rows), with perfect recall@10 = 1.000. Enable `SEAM_VEC_ANN=on` only if you are testing the scaffold or waiting for sqlite-vec to ship a true approximate index.
>
> | N rows | numpy BF ms | sqlite-vec ms | speedup | recall@10 |
> |-------:|------------:|--------------:|--------:|----------:|
> | 10,000 | 0.588 | 3.919 | 0.2× | 1.000 |
> | 50,000 | 5.903 | 20.496 | 0.3× | 1.000 |
> | 100,000 | 7.053 | 41.609 | 0.2× | 1.000 |
> | 250,000 | 19.740 | 109.373 | 0.2× | 1.000 |

When sqlite-vec ships a true approximate index (HNSW/IVF), enabling `SEAM_VEC_ANN=on` will transparently upgrade from exact to approximate without any re-index of the embeddings table — the scaffold is there, waiting. To opt in:

```bash
pip install 'seam-code[semantic-ann]'
SEAM_VEC_ANN=on seam init --semantic   # builds embeddings + vec0 KNN table
```

Two knobs govern the vec0 tier:

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_VEC_ANN` | `off` | Master switch. `on` enables the vec0 KNN tier (off = byte-identical to pre-WS2b). **Leave `off` until sqlite-vec ships approximate indexing.** |
| `SEAM_VEC_ANN_MIN_ROWS` | `50000` | Minimum embedding count before the vec0 table is built. Forward-compat gate; does not indicate a performance crossover today (vec0 is currently slower at all scales). |

**Packaging caveat.** `sqlite-vec` is a native C extension. Some Python builds — notably the **macOS system Python** — disable `conn.enable_load_extension()` at compile time. On those builds Seam detects the failure, logs a single clear notice, and falls back to the mmap/brute-force path automatically. Nothing crashes; no results are lost. To use the vec0 tier on macOS, install Python via [python.org](https://www.python.org/downloads/) or `brew install python` (both enable extension loading), or run via `uv` (which uses its own Python build that supports extension loading).

**Staleness banner.** Graph-traversal tools attach an `index_status` banner when the index has drifted from disk — so an agent is never silently handed wrong blast-radius answers.

---

## Configuration

Everything is environment-variable driven with sensible defaults — see [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the full reference (~50 knobs). The few you might actually set:

| Variable | Default | Effect |
|----------|---------|--------|
| `SEAM_SEMANTIC` | `off` | Enable hybrid keyword + semantic search (needs the `semantic` extra + `seam init --semantic`). |
| `SEAM_CLUSTER_NAMING` | `deterministic` | `llm` opts into LLM cluster labels at index time (needs `SEAM_LLM_API_KEY`). Read path stays 100% local regardless. |
| `SEAM_IMPACT_MAX_BYTES` | `0` (off) | Hard character ceiling on `seam_impact` output for tight context budgets. |

Most knobs are gated `on` by default and have an `off` that restores byte-identical pre-feature behavior — a deliberate discipline so upgrades never silently change tool output.

---

## Seam Explorer — local visual graph UI

`seam serve` (the `[web]` extra) starts a read-only, `127.0.0.1`-only browser explorer for the index.

```bash
uv sync --extra web
seam init          # index first
seam serve         # opens http://127.0.0.1:7420
seam serve --no-open --port 8000
```

A React + TypeScript SPA (React Flow) served by FastAPI. Nothing leaves the machine. Features: command-palette search, a depth-1 caller/callee card-canvas with confidence-styled edges, lazy expand, a **resizable detail panel** with **grouped clickable caller/callee rows** (edge kind + confidence badges), a **GraphHUD** (live node/edge/filtered counts + freshness dot), a **FilterBar** (node kinds + edge kinds + confidence tiers with All/None controls and live counts), a **file-tree sidebar** with debounced search, an impact overlay that paints blast radius by risk tier, a **fly-to-fit viewport** on overlay activation, a trace-path highlighter, a git-changes drawer, a schema/architecture read API, a whole-repo cluster constellation, and a **3D Constellation Explorer** tab. Explorer routes reuse the **same handlers** that power the CLI/MCP tools — a third transport, no query logic duplicated. Filter preferences (node kinds, edge kinds, confidence) persist across page reloads via `localStorage` and survive symbol navigation within a session.

### 3D Constellation Explorer tab

The Constellation tab renders the entire indexed graph as an interactive star field:

- **Nodes** are spheres sized and colored by degree and symbol kind (red dwarf → blue giant stellar scale). Hub classes appear as large blue/white spheres; isolated helpers are small red dots.
- **Edges** are additive-blended line segments color-coded by kind (teal = call, blue = import, purple = extends, cyan = holds, green = reads, red = writes, amber = uses).
- **Cluster halos** are faint translucent spheres marking the spatial extent of each Louvain functional area. Click any node to fly the camera there and open a detail panel with neighbors.
- **Filters** (left panel): show/hide node kinds and edge kinds. **HUD** (top-left): visible counts, max-nodes slider.

The layout is computed server-side (ForceAtlas2 + ring seeds in numpy) and cached per index version, so the browser tab always loads pre-positioned positions with no client-side simulation cost. Positions are deterministic — the same index always produces the same layout.

```bash
pip install 'seam-code[web]'
seam init          # index first
seam serve         # opens http://127.0.0.1:7420 -> click the "Topology" tab
```

Browser visual QA for this surface lives under `web/tests/browser/`. It starts a
temporary indexed fixture, serves the built Explorer on loopback, and checks
Chromium desktop/mobile canvas pixels for blank-scene and white-out regressions.

```bash
make test-web-visual
# or, from web/: npx playwright install chromium && npm run test:visual
```

Pull requests touching Explorer, server layout/API code, or this workflow also
run Explorer Visual QA in GitHub Actions and upload the Playwright report plus
topology test results as artifacts.

---

## Architecture

The import hierarchy is strictly layered — read flows down, never up:

```text
cli / server / web  →  analysis  →  query  →  indexer / db
```

`analysis` is built from **pure leaf modules** (no DB, no IO, never raise) — clustering, RWR neighbor ranking, byte budgeting, the truncation steer, staleness detection. This makes each piece testable in isolation and keeps the failure surface tiny.

- **Read it as a narrative:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — current system overview, then the full phase-by-phase history.
- **Read it as a diagram:** [`docs/architecture.html`](docs/architecture.html) — a standalone illustrated page (system pipeline, layered hierarchy, data-flow, edge-kind graph, schema).
- **Decision rationale:** [`docs/adr/`](docs/adr/) — architecture decision records.
- **Concepts in depth:** [`docs/CONCEPTS.md`](docs/CONCEPTS.md) — how and why each subsystem works.

---

## Design principles

These are the non-negotiables — and they are guarantees to the user, not just internal rules:

1. **Zero external services at runtime.** The MCP read path makes no network calls. The only optional outbound call (LLM cluster naming) runs at index time, is off by default, and falls back to deterministic labels on any error.
2. **SQLite only.** No graph DB, no vector DB to babysit, no ORM. One file per project, gitignored.
3. **Parsers never raise.** A malformed file is skipped, not fatal. The indexer degrades gracefully; analysis leaves return empty rather than throw.
4. **Edges are keyed by name.** This is what makes independent per-file re-indexing correct — the watcher can rewrite one file's symbols and edges without touching the rest of the graph.
5. **Additive by default.** New features ship behind a defaulted-on switch with an `off` that is byte-identical to before. Schema changes are additive migrations that auto-run on open.

---

## Development

```bash
uv sync --dev              # install dev dependencies
make gate                  # lint (ruff) + typecheck (mypy) + tests — must be green before every commit
make fmt                   # format + autofix (not part of the gate)
make build-web             # build the Explorer SPA into seam/_web/ (requires Node.js — build-time only)
make test-web-visual       # optional Playwright QA for the Topology/3D canvas
make eval                  # run the recall-regression harness
make eval-answerability    # run the agent answerability benchmark
make test-npm              # run the npm shim vitest suite (requires Node.js ≥18)
make bench-semantic        # semantic recall benchmark — needs [semantic] extra + seam init --semantic
make bench-semantic-ann    # ANN scale benchmark (brute-force vs KNN latency + recall) — needs [semantic-ann] extra
make soak                  # sustained mixed read load (P5.5 diagnostics harness)
```

### Publishing the npm shim

The npm shim (`pkg/npm/`) is published manually from that directory, trailing the matching PyPI release:

```bash
# Step 1: ensure seam-code==<version> is live on PyPI
# Step 2: bump pkg/npm/package.json version to match pyproject.toml version
make gate          # the version-parity test fails if they differ
# Step 3: publish
cd pkg/npm
npm publish --access public
```

The gate test `test_npm_package_version_matches_pyproject` (in `tests/unit/test_smoke.py`) fails if `pkg/npm/package.json` `version` diverges from `pyproject.toml` `version`, preventing accidental drift.

**Conventions:** max 200 lines/function, 1000 lines/file · all imports at top · config only from `seam/config.py` · type hints required (`X | None`, not `Optional[X]`) · tests in `tests/` mirroring the package.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the gate workflow, and project conventions.
Bug reports and feature requests: use the [issue templates](https://github.com/Catafal/seam/issues/new/choose).
Security issues: see [SECURITY.md](SECURITY.md).
Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

See [LICENSE](LICENSE).
