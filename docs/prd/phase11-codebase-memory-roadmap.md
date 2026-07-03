# PRD — Phase 11: Codebase-Memory-Inspired Roadmap

> Status: proposed — 2026-07-01.
> Source audit: `DeusData/codebase-memory-mcp` cloned at `/tmp/codebase-memory-mcp`
> commit `4a42285`. The production binary built locally, and a fast-mode smoke index
> of this repository produced 5,488 nodes and 28,649 edges in 369 ms.

## Problem Statement

The codebase-memory-mcp project overlaps strongly with Seam's thesis: index source once,
then let agents query a local knowledge graph instead of reconstructing structure with
grep. It also exposes several product surfaces Seam does not yet have: a dramatic 3D graph
overview, schema introspection, structural search with degree filters, snippet retrieval,
architecture summaries, route/config/test edges, a broader installer/distribution story,
and hardened release/security checks.

Seam should use that repository as competitive research, not as a codebase to port
wholesale. The useful path is to copy the product ideas that fit Seam's existing design
principles: local-first, SQLite-backed, read-only MCP tools, small Python modules, explicit
schema migrations, transport-agnostic handlers, and output discipline for agents.

## Audit Summary

### What codebase-memory-mcp does well

- Ships a single static C binary with vendored grammars and no runtime dependencies.
- Builds a richer graph model with node labels such as `Route`, `Variable`, `Resource`,
  `Branch`, and edge types such as `HTTP_CALLS`, `TESTS`, `USAGE`, `WRITES`,
  `CONFIGURES`, and `RAISES`.
- Exposes practical agent tools missing from Seam: `get_graph_schema`, `search_graph`
  with label/degree filters, `get_code_snippet`, `get_architecture`, and Cypher-like
  `query_graph`.
- Provides a full-bleed 3D graph UI with bloom, orbit controls, hover tooltips, filters,
  project selection, and satellite galaxies for linked projects.
- Has useful operations ideas: checksum-verified npm installer, install write-scope
  audits, network-egress checks, release provenance, optional compressed graph artifacts,
  diagnostics, and soak testing.

### What Seam should not copy directly

- Do not replace Seam's 2D React Flow analysis canvas with a 3D renderer. The 3D view is
  excellent as an overview/constellation mode, but 2D cards are better for precise impact,
  trace, and symbol-neighborhood debugging.
- Do not expose write/delete/index/ADR mutation tools through MCP by default. Seam's
  read-only MCP surface is safer.
- Do not add startup update checks. They conflict with Seam's zero-network read-path
  identity.
- Do not copy broad auto-detect-and-write installer behavior. Keep installer targets
  explicit and reversible.
- Do not port the C codebase or the custom Cypher parser wholesale. Several external files
  are thousands of lines long; Seam should preserve small modules and testable leaves.
- Treat `ingest_traces` as not implemented: the tool currently accepts traces but returns
  that runtime edge creation is not yet implemented.
- Treat `semantic_query` carefully: in smoke testing it logged zero vector candidates but
  still returned generic `search_graph` rows. Seam must avoid that ambiguous contract.

### Adoption Decision

This roadmap intentionally includes every candidate in the following groups:

- **Definitely add first:** `seam_schema`, `seam_snippet`, structural graph search,
  `seam_architecture`, 2D Explorer UX improvements, installer write-scope audit, and
  no-egress proof.
- **Worth adding, staged carefully:** 3D constellation UI, route edges, test edges,
  config/resource edges, release hardening, and npm shim.
- **Experimental only:** semantic/similarity edges, resolved edge IDs, compressed graph
  artifacts, broader agent/editor install targets, full Cypher if typed graph tools prove
  insufficient, and broad auto-detect installer behavior if explicit install targets prove
  too cumbersome.

The only candidates excluded from the implementation roadmap are the items in
`Deferred / Do Not Add Now`. Those are still evaluated in detail so the decision can be
revisited later without losing context.

## Product Direction

Phase 11 turns Seam from a focused code-intelligence engine into a fuller local code
workbench for agents and humans:

1. Agent tools become more self-describing and precise: schema, snippet, structural
   search, architecture summary.
2. The web explorer gains a separate 3D constellation mode inspired by the screenshot,
   while keeping the current 2D graph as the precision workflow.
3. The graph model grows carefully with high-value edges: route, config, test, raises,
   and eventually semantic/similarity edges.
4. Distribution and trust become first-class: npm shim, checksums, pinned CI actions,
   installer write-scope tests, no-egress proof, and diagnostics.

## Roadmap

### P0 — Roadmap Groundwork

1. **Keep this roadmap as the tracking document.**
   - Owner files: `docs/prd/phase11-codebase-memory-roadmap.md`.
   - Outcome: all candidate work is visible in one phase document, with defer/avoid
     decisions recorded.

2. **Create implementation issues from this roadmap before coding.**
   - Split into independently grabbable slices: schema/snippet/search/architecture,
     3D UI, graph edge expansion, distribution/security.
   - Each implementation issue must name acceptance tests and blast radius.

### P1 — Agent Tooling And Query Surfaces

#### 1. Add `seam_schema`

Add a read-only CLI/MCP/Web handler that returns:

- schema version and freshness summary,
- symbol kind counts,
- edge kind counts,
- known enrichment columns and nullability,
- available tools and recommended query path,
- whether embeddings, clusters, synthesis, and import mappings are populated.

Justification: codebase-memory-mcp's `get_graph_schema` gives agents a safe first call
before ad hoc graph exploration. Seam currently has strong contracts in docs but no
machine-readable schema tool.

Acceptance:

- `seam schema --json` returns `{ok:true,data:{...}}`.
- `seam_schema` is read-only and attaches staleness like other graph tools.
- Tests cover empty DB, current DB, pre-migration DB, and missing embeddings.

#### 2. Add `seam_snippet`

Add exact source retrieval by `uid`, symbol, or file+line:

- returns source text for the exact symbol range,
- includes signature, file, start/end lines, kind, docstring, and optional neighbors,
- supports ambiguity suggestions instead of silently picking the wrong symbol,
- enforces root containment before reading files,
- offers lean/default modes so source retrieval does not explode context by accident.

Justification: `seam_context_pack` is broad. Agents often need only "show me the body of
this exact thing." codebase-memory-mcp's `get_code_snippet` proved this is a high-value
primitive.

Acceptance:

- `seam snippet --uid ... --json` round-trips from `seam_search` and `seam_query`.
- Ambiguous symbol names return candidates with `uid`.
- Reading outside the project root is impossible.
- Tests cover Unicode/encoding failures, missing file, stale file, and line-range drift.

#### 3. Add structural graph search

Add a `seam graph-search` CLI and `seam_graph_search` MCP tool with:

- `kind` filter,
- `name_pattern` regex or glob,
- `file_pattern`,
- `edge_kind` filter,
- `min_degree` / `max_degree`,
- direction-aware degree filters,
- pagination,
- optional connected-node preview.

Justification: this covers practical questions without full Cypher: dead code,
high fan-in utilities, high fan-out orchestrators, "all route handlers," "all symbols
that write fields," and "all symbols with no callers."

Acceptance:

- Dead-code query works without Cypher.
- High-degree/hotspot query is stable and paginated.
- Regex errors return structured `INVALID_QUERY`.
- Output includes honest `total`, `limit`, `offset`, and `has_more`.

#### 4. Add `seam_architecture`

Aggregate existing Seam primitives into one overview:

- language/file counts,
- top packages/directories,
- entry points,
- routes when route edges exist,
- hotspots by fan-in/fan-out,
- cross-area boundaries,
- cluster summaries,
- physical structure summary,
- optional scope path.

Justification: Seam already has `seam_structure`, `seam_clusters`, `seam_flows`, and
impact primitives. codebase-memory-mcp's `get_architecture` shows the value of one
compact "what is this repo?" answer.

Acceptance:

- Works on empty and normal indexes.
- Uses existing handlers/analysis modules where possible.
- Has a hard byte budget and `sections` selector so agents can keep output lean.

#### 5. Potentially add full Cypher if needed

Do not implement a Cypher parser in the first pass. Keep it as a conditional candidate
after structural search and architecture summary prove insufficient.

Justification: codebase-memory-mcp's Cypher implementation is a large custom parser and
executor. Seam can get most agent value with narrower, safer tools first.

Detailed evaluation:

- **User value:** medium. Power users can express arbitrary graph questions, but most
  agent questions are better served by purpose-built tools with stable schemas.
- **Engineering cost:** high. A correct parser, executor, safety layer, pagination model,
  error model, and compatibility story would become a product inside the product.
- **Agent reliability:** risky. Free-form graph queries encourage agents to synthesize
  invalid query strings and then recover through retries instead of using deterministic
  tools.
- **Security/trust:** risky if write-like behavior, filesystem references, or expensive
  traversals are not tightly sandboxed.
- **First-principles verdict:** Seam does not need arbitrary graph programmability yet.
  It needs reliable answers to recurring codebase questions. Structural search should
  absorb the real use cases first.
- **Safer substitute now:** ship `seam_graph_search`, `seam_architecture`, `seam_context`,
  `seam_impact`, and `seam_trace` with typed parameters, pagination, and stable response
  contracts. Add narrowly scoped filters when real user questions exceed the current
  surface.
- **Risk if added too early:** Seam becomes harder to test, agents learn to generate
  brittle query strings, and every graph schema change becomes a query-language
  compatibility problem.

Reconsider when:

- users repeatedly ask questions that cannot be expressed with structural search,
  architecture summary, context, impact, trace, or affected-tests tools;
- there is a read-only query language design with a hard cost budget and no custom
  mutation semantics;
- the maintenance burden is justified by actual usage.

Acceptance if promoted:

- Query execution is read-only by construction.
- Every query has a hard node/edge/row/time budget.
- Output is paginated and always reports truncated state.
- Schema version is included in query responses.
- Invalid queries return structured errors without stack traces.
- The public docs position Cypher as an expert escape hatch, not the default agent path.

### P2 — Explorer UI And 3D Constellation

#### 1. Add a 3D constellation overview mode

Build a new Explorer tab inspired by the screenshot and the external `graph-ui`:

- full-bleed Three.js scene, not inside a card,
- glowing node cloud with bloom,
- relationship lines with edge-kind coloring,
- orbit controls and idle auto-rotation,
- hover tooltip and click-to-select,
- filter sidebar for node kinds and edge kinds,
- quick graph HUD: visible nodes, visible edges, filtered count, selected count,
- "open in 2D analysis canvas" action for a selected symbol or cluster.

This should be a whole-repo overview and presentation layer, not the primary debugging
canvas. The current React Flow view remains the precise workflow for neighborhood,
impact, trace, and changes.

Justification: the 3D UI is visually strong and is valuable for human orientation,
demos, and cluster exploration. It should complement, not replace, Seam's existing
2D graph analysis model.

Acceptance:

- Desktop and mobile Playwright screenshots show nonblank canvas.
- Canvas pixel checks confirm nodes and edges render.
- 3D mode handles large graphs by sampling, clustering, or level-of-detail caps.
- Selecting a node can navigate to the existing detail panel / 2D graph.
- No network calls and no external telemetry.

#### 2. Upgrade the existing 2D Explorer

Copy/adapt these interaction ideas from the external UI:

- resizable side panels persisted in `localStorage`,
- grouped caller/callee lists in `DetailPanel`,
- graph HUD in `GraphCanvas`,
- all/none filter controls with per-kind/per-confidence counts,
- searchable file/path sidebar as an optional companion to `StructureOverview`,
- viewport "fly to fit" for selected impact/trace paths using React Flow APIs.

Justification: these are low-risk UX improvements that fit Seam's current React Flow
architecture.

Acceptance:

- `DetailPanel` no longer truncates common signatures/docstrings awkwardly.
- Caller/callee rows are clickable and navigate without losing graph state.
- Filter counts update after impact/trace overlays.
- File sidebar can highlight or open contained symbols without forcing a whole-project
  layout blob.

#### 3. Defer project-management controls

Do not copy codebase-memory-mcp's project-control UI as-is. Seam is per-repo and local
project scoped; adding multi-project management should be a separate product decision.
The detailed evaluation lives in `Deferred / Do Not Add Now`.

### P3 — Graph Model Expansion

#### 1. Add route nodes and `HTTP_CALLS`

Extract web routes from common Python/TypeScript frameworks first:

- FastAPI decorators,
- Flask route decorators,
- Express/router calls,
- common frontend fetch wrappers where URL literals are visible.

Justification: codebase-memory-mcp surfaces routes as first-class graph nodes. This is
high-value for agents navigating API boundaries and frontend-backend relationships.

Acceptance:

- Route nodes appear in schema, search, architecture, and Explorer.
- Route extraction is conservative and confidence-tagged.
- No dynamic route inference unless provenance marks it heuristic.

#### 2. Add config/resource links

Extract config keys and simple code references:

- `.env.example`, TOML/YAML/JSON project config,
- Python/TS env var reads,
- dependency/config file references where deterministic.

Justification: `CONFIGURES` edges are useful for deployment/debug questions. Seam should
start with high-confidence config-to-code references.

Acceptance:

- `seam_graph_search edge_kind=configures` works.
- Config extraction never indexes secrets or values by default; keys only unless the
  file is explicitly safe.

#### 3. Add test edges

Materialize `TESTS` relationships from test files to source symbols using import/call
evidence and file-name proximity.

Justification: Seam already has `seam_affected`. Explicit test edges would improve
architecture summaries, Explorer overlays, and test-impact confidence.

Acceptance:

- Existing `seam_affected` behavior stays byte-compatible unless explicitly enhanced.
- Test edges are tagged with provenance and can be excluded from production impact.

#### 4. Add raises/exception edges

Extract explicit `raise` / `throw` relationships where the exception type is statically
visible.

Justification: exception flow is a frequent debugging path and is cheap to extract
conservatively.

Acceptance:

- `RAISES` edges show up in schema and structural search.
- Builtin/common exceptions are not over-modeled as repo symbols.

### P4 — Indexing Pipeline And Performance

#### 1. Formalize the pass pipeline

Refactor toward an explicit pass model:

1. discover files,
2. parse/extract definitions,
3. write symbols,
4. extract/import mappings,
5. resolve calls/usages,
6. extract comments,
7. post-pass clusters/synthesis/semantic/test/config/route edges,
8. embeddings when requested.

Justification: Seam's current per-file extraction is simple and good. But as route/config/test
and semantic edges grow, explicit passes will make dependencies and performance easier to
reason about.

Acceptance:

- No behavior change in the first refactor.
- Per-pass timing is emitted in verbose init/status.
- Tests prove partial parser failures still degrade per file, not per run.

#### 2. Benchmark SQLite bulk-write settings

Create a benchmark spike for:

- WAL behavior during full init,
- temporary indexing transaction settings,
- cache size,
- prepared statement reuse,
- checkpoint/optimize after init.

Justification: codebase-memory-mcp is fast partly because it treats SQLite write lifecycle
as a performance surface. Seam should benchmark equivalent safe improvements.

Acceptance:

- Benchmark compares at least Seam, Bach, and one larger repo.
- Crash/recovery tests pass.
- No speed improvement is accepted if it risks DB corruption or stale reads.

### P5 — Distribution, Installer, And Trust

#### 1. Add verified npm shim

Publish a small npm package that makes Seam easier for JS/TS-heavy users:

- preferably wraps `uvx seam-code` first,
- later may download signed release artifacts,
- verifies checksums before executing downloaded artifacts,
- rejects path traversal in archives,
- avoids shell interpolation.

Justification: codebase-memory-mcp's distribution story is materially better for
"one command, works in agent environments." Seam can improve without abandoning PyPI.

Acceptance:

- `npx @catafal/seam --version` or equivalent works.
- Failure modes are explicit and safe.
- No non-fatal checksum bypass in release mode.

#### 2. Harden releases

Add:

- wheel/sdist checksums,
- release smoke install,
- GitHub release artifact attachment,
- provenance where practical,
- CI check for pinned GitHub Actions.

Justification: Seam's local-code promise needs a trust story. The external repo's security
posture is a useful bar, even if Seam starts with a smaller version.

Acceptance:

- Release job verifies `pip install seam-code && seam --version`.
- `checksums.txt` is generated and published.
- Mutable action refs are blocked or explicitly waived.

#### 3. Add installer write-scope audit

Run installer tests under fake `HOME` and fake repo roots:

- `seam install --target all`,
- `seam install --with-mcp`,
- uninstall reversal,
- preview mode writes nothing.

Justification: Seam's installer is already careful; a write-scope audit makes that
guarantee measurable.

Acceptance:

- Tests assert only expected files are created/modified.
- Sensitive paths are never touched.
- Uninstall removes only owned blocks/files.

#### 4. Add no-egress proof

Create an optional Linux security job using `strace -e connect` or equivalent:

- `seam init`,
- `seam search`,
- `seam context`,
- `seam impact`,
- `seam start` smoke JSON-RPC.

Semantic model download is excluded from this job unless the test explicitly verifies
the one expected download during `seam init --semantic`.

Justification: "zero network calls at query time" is a core promise. A proof is stronger
than a README claim.

Acceptance:

- Job passes on normal read path.
- Any unexpected outbound connection fails the job.

#### 5. Add opt-in diagnostics and soak testing

Add `SEAM_DIAGNOSTICS=1`:

- local NDJSON file,
- RSS,
- open file count,
- DB size,
- query count,
- slow query summaries without source text,
- watcher activity counters.

Add a short soak script that runs mixed search/context/impact/trace requests against an
indexed repo.

Justification: this helps catch leaks, watcher regressions, and slow-query paths without
adding telemetry.

Acceptance:

- Disabled by default.
- Never includes source code or secret-like values.
- Soak test can run locally and in optional CI.

### P6 — Experimental Graph And Team Bootstrap

This phase contains useful ideas that should ship behind flags, RFCs, or explicit
commands. They are worth adding, but only after the core query surfaces and trust checks
are stable.

#### 1. Prototype semantic/similarity edges

Prototype optional `similar` / `semantically_related` edges from cheap local signals:

- identifier tokens,
- signature tokens,
- docstring terms,
- shared callees,
- same cluster/file proximity,
- optional embeddings when already present.

Do not feed semantic edges into core impact by default. Start with search/context-pack
use cases and keep them excluded from risk-tier traversal unless explicitly requested.

Justification: codebase-memory-mcp materializes related-code edges. This could improve
"find related code" and cluster quality, but it risks polluting blast-radius answers if
treated like hard dependencies.

Acceptance:

- Feature is opt-in at index time.
- Semantic edges are excluded from `seam_impact` by default.
- Evaluation shows recall improvement without major precision loss.

#### 2. Investigate resolved edge IDs

Do not immediately migrate away from name-keyed edges. Instead, evaluate an additive
resolution table or nullable resolved endpoint IDs:

- `edge_resolutions(edge_id, source_symbol_id, target_symbol_id, confidence, resolved_by)`,
  or
- nullable `source_symbol_id` / `target_symbol_id` columns populated by full `seam init`.

Justification: codebase-memory-mcp's ID-backed edges avoid homonym collapse, but Seam's
name-keyed design enables independent per-file re-indexing. Any change here is high
blast radius and must be additive first.

Acceptance:

- RFC before implementation.
- Benchmarks compare impact/context precision before and after.
- Watcher correctness is proven for changed endpoint files.

#### 3. Optional compressed graph artifact

Add explicit commands:

- `seam artifact export`,
- `seam artifact import`,
- artifact metadata with schema version, Seam version, root hash/fingerprint, created_at,
  and language/edge counts.

Justification: codebase-memory-mcp's artifact export lets teammates skip re-indexing.
This is useful for large repos, but conflicts with Seam's repo-cleanliness differentiator
unless strictly opt-in.

Acceptance:

- Artifact files are never generated by default.
- Import refuses newer schema versions.
- Artifact path is documented and gitignored.

#### 4. Broaden installer targets one at a time

Add explicit targets only:

- VS Code,
- Zed,
- Gemini CLI,
- OpenCode,
- Aider.

Justification: codebase-memory-mcp supports many agents. Seam should broaden reach while
keeping explicit user control.

Acceptance:

- Every target has preview/install/uninstall tests.
- No broad auto-detect write-everywhere behavior by default.

#### 5. Potentially add broad auto-detect installer behavior

Keep broad auto-detect-and-write behavior as a conditional late-stage candidate, not a
default installer path. The safe version is a guided installer that discovers available
agent/editor configs, prints an exact write plan, asks for explicit confirmation per
target, and can reverse every owned edit.

Justification: broad detection can reduce setup friction once Seam supports many targets,
but installation is a permission boundary. Convenience should come after exact preview,
ownership markers, uninstall tests, and fake-home coverage are proven for each target.

Acceptance if promoted:

- Default mode is preview-only.
- The user must explicitly confirm each target before writes happen.
- Every write includes an owned block or owned file marker.
- Uninstall reverses only owned edits.
- Duplicate MCP registrations are detected and reported before writes.
- Tests run under fake `HOME` fixtures for every supported target.
- A non-interactive mode requires explicit `--target` or `--all-confirmed` flags.

### Deferred / Do Not Add Now

These candidates remain out of the implementation roadmap until stronger evidence appears.
`Full Cypher` and broad auto-detect installer behavior are now conditional roadmap
candidates above, so they are no longer listed here as hard rejects.

#### 1. Do not copy project-management UI

codebase-memory-mcp has project-selection and project-control surfaces. Seam should not
copy those into Phase 11.

Detailed evaluation:

- **User value:** unclear. Seam's current job is per-repo code intelligence, not managing
  a portfolio of indexed workspaces.
- **Engineering cost:** medium. Multi-project state introduces persistence, switching,
  permissions, stale-index semantics, and UI complexity.
- **Agent reliability:** mixed. More project controls can help humans but can confuse
  agent flows if tools become scoped to the wrong project.
- **Security/trust:** higher risk. Multi-project UIs make it easier to accidentally expose
  or query a repo the user did not intend.
- **First-principles verdict:** a local code-intelligence tool needs precise answers for
  the current repo before it needs workspace management. Keep project management separate.
- **Safer substitute now:** keep Seam scoped to the active repository, then support
  explicit artifact export/import and explicit installer targets. If cross-repo work is
  needed later, design it as a separate cross-repo impact product, not as incidental UI.
- **Risk if added too early:** the UI starts solving navigation and workspace management
  before Seam has fully nailed code intelligence. That creates product sprawl and a larger
  permission surface without improving core answers.

Reconsider when:

- users repeatedly run Seam across multiple repos in one session;
- cross-repo impact analysis becomes a core feature;
- there is a clear permission and project-selection model for MCP clients.

#### 2. Do not add runtime trace ingest yet

The external repo exposes `ingest_traces`, but the audited implementation reports that
runtime edge creation from traces is not yet implemented. Seam should not copy the API.

Detailed evaluation:

- **User value:** potentially high if real runtime traces can connect dynamic calls,
  routes, background jobs, and generated code paths.
- **Engineering cost:** high. Trace formats differ by language/runtime, and merging
  runtime evidence with static edges needs provenance, retention, invalidation, and
  privacy rules.
- **Agent reliability:** risky unless trace edges are clearly separated from static edges.
  Agents may treat one observed runtime path as a universal dependency.
- **Security/trust:** high risk. Runtime traces can contain file paths, URLs, identifiers,
  request data, and secret-like values.
- **First-principles verdict:** runtime evidence is valuable only when it is precise,
  scrubbed, and provenance-tagged. A stub API creates false confidence.
- **Safer substitute now:** add static route/test/config/raises edges first, then use
  `seam_affected` and future test edges for practical runtime-adjacent questions. If real
  execution evidence is needed, start with a narrow source such as coverage data rather
  than a generic trace-ingest API.
- **Risk if added too early:** agents will treat incomplete runtime observations as
  authoritative system behavior. Worse, trace ingestion can accidentally persist sensitive
  runtime data unless the privacy model is designed first.

Reconsider when:

- Seam has a concrete trace source to support first, such as Python coverage, pytest,
  OpenTelemetry spans, or Playwright traces;
- trace ingestion stores only minimal structural metadata by default;
- trace-derived edges are opt-in and visually distinct from static dependencies.

#### 3. Do not add startup update checks

Do not check for updates on MCP start, CLI query, or web UI load.

Detailed evaluation:

- **User value:** low. Update prompts are convenient but not central to code
  understanding.
- **Engineering cost:** low to medium, but the hidden cost is product trust.
- **Agent reliability:** negative. Network behavior during tool startup can add latency
  and failure modes unrelated to the user's code question.
- **Security/trust:** bad fit. Seam's promise is local-first and no unexpected network
  calls. Startup update checks weaken that story.
- **First-principles verdict:** a local code intelligence tool should not phone home on
  read paths. Release discovery belongs in explicit user commands.
- **Safer substitute now:** publish release notes, checksums, and an explicit
  `seam update-check` or documentation path. Keep all automatic query/startup paths
  network-silent.
- **Risk if added too early:** the product undermines its strongest trust claim for a
  minor convenience feature. In enterprise/offline contexts, even harmless update checks
  can become blockers.

Reconsider when:

- there is an explicit `seam update-check` command;
- update checks are never automatic;
- enterprise/offline users can disable the path entirely.

#### 4. Do not port the C codebase or vendored parser strategy

Do not rewrite Seam as a C static binary or vendor a large parser bundle as the default
architecture.

Detailed evaluation:

- **User value:** medium. A static binary can be fast and easy to distribute.
- **Engineering cost:** very high. It would abandon Seam's current Python ecosystem,
  tests, packaging, and local development velocity.
- **Agent reliability:** neutral to negative. Binary speed helps indexing, but smaller
  typed Python modules are easier for agents and maintainers to inspect and patch.
- **Security/trust:** mixed. Static binaries are convenient, but vendored parser bundles
  increase supply-chain and update surface.
- **First-principles verdict:** Seam's bottleneck is not currently "cannot run because it
  is not a C binary." The real bottleneck is query quality, graph semantics, and adoption.
- **Safer substitute now:** keep Python as the main implementation, benchmark SQLite and
  parser hotspots, and consider narrow native acceleration only when profiling proves a
  specific bottleneck.
- **Risk if added too early:** the project spends its complexity budget on runtime and
  packaging instead of answer quality. It also makes ordinary contribution, debugging, and
  agent-driven maintenance harder.

Reconsider when:

- profiling proves Python runtime overhead is the dominant adoption blocker;
- a narrow native extension can solve a specific hotspot without rewriting the product;
- release signing/provenance is already mature.

#### 5. Do not copy ambiguous `semantic_query` behavior

Do not expose a semantic query tool that silently falls back to generic graph search when
semantic candidates are missing.

Detailed evaluation:

- **User value:** high when semantic search is honest and returns relevant matches.
- **Engineering cost:** medium, assuming embeddings or token-based similarity already
  exist.
- **Agent reliability:** high risk if failure is hidden. Agents need to know whether a
  result came from embedding similarity, lexical fallback, graph traversal, or some blend.
- **Security/trust:** manageable if local-only, but source text and embeddings need clear
  storage and opt-in rules.
- **First-principles verdict:** semantic search is useful only if provenance is explicit.
  A tool that says "semantic" while returning generic fallback results degrades trust.
- **Safer substitute now:** prototype semantic/similarity edges as opt-in discovery data,
  and require every search response to state retrieval mode, fallback mode, candidate
  counts, and whether embeddings were actually used.
- **Risk if added too early:** agents and users will over-trust results because the tool
  name implies semantic retrieval even when the returned rows came from another mechanism.

Reconsider when:

- the response schema includes retrieval mode and candidate counts;
- empty semantic candidates return an honest empty result or explicit fallback reason;
- semantic and lexical result sets are separable in output.

## Proposed Phase Order

1. `P1.1 seam_schema` — definitely add first; it gives agents a truthful map of the
   graph before they query it.
2. `P1.2 seam_snippet` — definitely add first; exact source retrieval is a core agent
   primitive.
3. `P1.3 structural graph search` — definitely add first; it covers dead-code, hotspot,
   and relationship questions without full Cypher.
4. `P1.4 seam_architecture` — definitely add first after schema/search; it composes
   existing primitives into one repo-level answer.
5. `P2.2 Explorer UX upgrades` — definitely add first; small UI improvements compound
   across every graph workflow.
6. `P5.3 installer write-scope audit` — definitely add first; installation is a trust
   boundary and should be test-proven.
7. `P5.4 no-egress proof` — definitely add first; it turns the local-first claim into a
   repeatable check.
8. `P3.1 route nodes / HTTP_CALLS` — staged carefully; routes are high-signal boundaries.
9. `P3.3 test edges` — staged carefully; improves affected-test and impact confidence.
10. `P3.2 config/resource links` — staged carefully; useful, but must avoid secrets.
11. `P3.4 raises/exception edges` — staged carefully; cheap when conservative.
12. `P2.1 3D constellation overview` — staged carefully; important for human orientation
    and product feel, but isolated from the precision 2D workflow.
13. `P5.2 release hardening` — staged carefully; raises trust once the product surface is
    stronger.
14. `P5.1 verified npm shim` — staged carefully; improves JS/TS adoption without
    replacing PyPI.
15. `P5.5 diagnostics and soak testing` — staged carefully; useful once query volume and
    watcher behavior grow.
16. `P4 pipeline/performance` — staged carefully; do after new edge families expose real
    indexing pressure.
17. `P6.1 semantic/similarity edges` — experimental only; discovery surface, never default
    impact traversal.
18. `P6.2 resolved edge IDs` — experimental only; RFC and additive design before any graph
    migration.
19. `P6.3 compressed graph artifact` — experimental only; explicit export/import, never
    default.
20. `P6.4 broader agent/editor targets` — experimental only; one explicit target at a
    time with preview/install/uninstall tests.
21. `P1.5 full Cypher` — conditional only; add if typed graph tools cannot express real
    repeated user questions.
22. `P6.5 broad auto-detect installer` — conditional only; add after explicit targets are
    proven and the default remains preview/confirm.

Everything in `Deferred / Do Not Add Now` remains outside the implementation sequence.

## Open Questions

1. Should the 3D constellation be in the default first viewport of `seam serve`, or a tab
   behind the current landing/Explorer?
2. Should `seam_snippet` return source by default, or require `--include-source` with a
   default line-window preview?
3. Should semantic/similarity edges ever participate in impact traversal, or only in
   discovery/context surfaces?
4. Is a resolved-edge table worth the complexity, or should Seam preserve name-keyed edges
   permanently and keep improving disambiguation?
5. Should npm distribution wrap `uvx seam-code` first, or wait until there is a bundled
   artifact story?

## Success Criteria

- Agents can discover schema, retrieve exact source, find dead code/hotspots, and get a
  repo architecture summary without falling back to grep.
- Humans can open `seam serve` and get a visually compelling 3D constellation plus the
  existing precise 2D workflows.
- New edge kinds improve route/config/test/debugging questions without polluting impact
  risk tiers.
- Distribution is easier for non-Python users while preserving local-first guarantees.
- The release/security story proves the claims Seam already makes: local, reversible,
  bounded output, no unexpected network calls, and no hidden repo mutations.
