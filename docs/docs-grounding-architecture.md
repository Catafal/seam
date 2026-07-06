---
agentic_doc:
  name: DocsAndSpecGroundingArchitecture
  type: architecture-reference
  status: production
  version: "1.0"
  issue: "#371"
  audience:
    - human-maintainers
    - coding-agents
    - roadmap-agents
purpose:
  primary: "Explain why docs/spec grounding is evidence, not dependency semantics."
  agent_question:
    - "Which local docs explain this symbol, route, config key, resource, or file?"
    - "Can this document reference be trusted as explicit grounding evidence?"
    - "Which commands prove docs/spec grounding is populated in this checkout?"
capabilities:
  schema_flags:
    - has_doc_anchors
    - has_doc_grounding
  tools:
    - seam_schema
    - seam_grounding
    - seam_plan
    - seam_context_pack
execution:
  local_only: true
  network_required: false
  estimated_validation_seconds: 30
  primary_commands:
    - "uv run seam schema --json"
    - "uv run seam grounding --query 'docs/spec grounding' --json"
    - "uv run pytest tests/unit/test_grounding.py -q"
security:
  data_classification: local-source-and-doc-metadata
  pii_handling: false
  stores_secret_values: false
  network_egress: false
observability:
  health_signal: "schema capabilities and grounding query warnings"
  stale_index_signal: "index_status or freshness.stale"
related:
  architecture: docs/ARCHITECTURE.md
  mcp_contract: docs/api-contracts/mcp-tools.yaml
  tests: tests/unit/test_grounding.py
  implementation:
    - seam/indexer/docs.py
    - seam/query/grounding.py
    - seam/server/grounding_handler.py
---

# Docs and Spec Grounding Architecture

This document explains why Seam indexes local Markdown documentation as a first-class
evidence surface without turning docs into code dependencies. For the schema tables and
tool list, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For the MCP contract, see
[`api-contracts/mcp-tools.yaml`](api-contracts/mcp-tools.yaml).

## Agent Quick Start

Use docs/spec grounding when code structure is not enough and the question is about
intent, provenance, or local specification context.

```bash
# Confirm the index advertises populated docs/spec grounding.
uv run seam schema --json

# Find documentation anchors related to a symbol, file, route, config key, resource, or concept.
uv run seam grounding --query "docs/spec grounding" --json

# Fetch bounded snippets for returned anchors when the first response identifies useful docs.
uv run seam grounding --query "docs/spec grounding" --include-snippets --json
```

Interpretation rules for agents:

- Treat grounding candidates as local documentation evidence, not dependency edges.
- Prefer `EXACT` and `HIGH` confidence references before reading low-confidence textual
  leads.
- Check `doc_kind`, `status`, `relation_type`, `confidence`, `provenance`, and caveats
  before using a document to guide an edit.
- Run `uv run seam sync` if schema or grounding output reports stale index state.

## What Problem This Solves

Agents often need to answer questions that code structure cannot answer alone:

- Which PRD, roadmap, ADR, task, or README explains this symbol?
- Is there a local spec that mentions this route, config key, resource, or file?
- Which document anchor should I read before changing this behavior?

Before grounding, the best answer was broad text search. That forced an agent to blend
documentation hits, source hits, and speculative semantic matches by hand. Docs grounding
turns explicit local documentation evidence into a bounded query surface so agents can
find the relevant document, line range, confidence, and caveats before they inspect code.

The feature deliberately does not answer whether code implements the spec correctly.
It answers the narrower question: "what local documentation explicitly points at this
thing, and how strong is that reference?"

## Key Design Decisions

### Reconstructed: Grounding Is Not a Dependency Edge

What: document references live in `document_files`, `document_anchors`, and
`document_references`, not in `edges`.

Why: a Markdown mention of `OrderService.checkout` is provenance evidence, not proof that
the document depends on the function or that changing the function breaks the document.
Keeping grounding outside `edges` prevents `seam impact`, `seam trace`, and graph-search
from treating docs as runtime or structural relationships.

Trade-off: graph traversals do not automatically include docs. Callers must use
`seam_grounding` when they want spec evidence.

Breaks when: callers start expecting grounding to prove implementation conformance. That
requires a separate verification layer, not a stronger document index.

### Explicit: Resolution Is Exact Before It Is Useful

What: the extractor records local Markdown links, code spans, issue references, and
literal route/config/resource-looking tokens, then resolves them against existing index
tables using exact matches.

Why: false positives are more damaging than missed leads in an agent planning tool. A
wrongly grounded PRD can send the agent toward the wrong behavior and make subsequent
code edits look justified by the wrong source.

Trade-off: prose-only references can be missed unless the document includes a concrete
symbol, file path, route path, config key, or resource literal.

Breaks when: teams expect natural-language semantic search to behave like grounding. Add
a separate lower-confidence search mode before weakening high-confidence resolution.

### Reconstructed: Markdown Is Indexed After Code

What: `walk_project` includes Markdown files but sorts them after code and config files.

Why: document references need the current symbol, file, route, config, and resource index
to exist before resolution. Reusing the normal file table also lets staleness checks treat
docs like any other indexed local file.

Trade-off: a first index pass can spend extra time on docs after code extraction. That is
acceptable because grounding is local-only, bounded, and does not require embeddings or
network calls.

Breaks when: document extraction starts depending on whole-document semantic analysis or
large generated docs. Keep extraction lightweight or add explicit caps before widening the
included document set.

### Explicit: Snippets Are Read on Demand

What: anchor metadata and capped search text are stored in SQLite; full Markdown bodies
are not.

Why: the index should remain a code-intelligence database, not a copy of every document.
On-demand snippets let the read path show the relevant anchor text while respecting the
current working tree and existing staleness warnings.

Trade-off: snippet reads require filesystem access at query time and can reflect local
edits that have not been re-indexed. The MCP/CLI handler therefore attaches the same
staleness caveat used by other read tools.

Breaks when: callers need archived, immutable doc evidence from a historical index. That
belongs in graph artifacts or a snapshot feature, not the default local grounding path.

### Reconstructed: The Tool Is Separate So Existing Surfaces Stay Small

What: grounding is exposed through `seam grounding` and `seam_grounding`, while
`seam_schema` advertises the capability and related table counts.

Why: existing surfaces such as `seam_context`, `seam_plan`, and `seam_architecture`
already carry bounded graph evidence. Adding document anchors to every response would
increase payload size and make dependency answers look like spec answers.

Trade-off: an agent needs one extra follow-up call after discovering a target symbol or
file. The response includes recommended next calls to make that handoff explicit.

Breaks when: most agent workflows need grounding every time. In that case, add compact
"grounding available" hints to selected tools instead of inlining full candidates.

## Component Map

| Component | Owns | Depends on | Does NOT own |
|---|---|---|---|
| `seam.indexer.docs` | Markdown classification, anchor extraction, explicit reference detection, exact resolution | Existing `files`, `symbols`, `routes`, `config_keys`, and `resources` tables | Semantic search, conformance checking, dependency edges |
| `seam.indexer.pipeline` | Routing Markdown files through document extraction during init/sync/watch re-index | `is_document_file`, `extract_and_resolve_document`, `upsert_document_file` | Markdown parsing beyond lightweight line-based extraction |
| `seam.indexer.db` | Atomic persistence of document file, anchor, and reference rows | Schema v16 tables and existing file lifecycle cleanup | Ranking, query shaping, CLI/MCP presentation |
| `seam.query.grounding` | Read-only candidate filtering, ranking, snippets, caveats, and next-call suggestions | Grounding tables and local filesystem snippets | Mutating the index, running sync, proving spec compliance |
| `seam.server.grounding_handler` | MCP-compatible handler and stale-index caveat integration | `query_grounding`, handler common staleness logic | CLI formatting and transport registration |
| `seam.cli.grounding` | Human CLI flags, JSON output, quiet rows, and Rich table rendering | `handle_seam_grounding` and readonly DB opening | Grounding semantics |
| `seam.query.schema` | Capability discovery and counts for grounding tables and tool availability | SQLite schema introspection | Deep grounding results |
| `docs/api-contracts/mcp-tools.yaml` | Agent-facing MCP contract for `seam_grounding` | Tool implementation contract | Runtime validation logic |

## Data Flow

1. `seam init`, `seam sync`, or the watcher calls `walk_project`, which now includes
   Markdown documents after source/config files.
2. `index_one_file` detects Markdown via `is_document_file` and reads the document as
   local UTF-8 text with replacement for invalid bytes.
3. `extract_document` classifies the document, builds heading anchors, records bounded
   anchor search text, and extracts explicit raw references.
4. `resolve_document_references` checks each raw reference against the already-indexed
   file, symbol, route, config, and resource tables.
5. `upsert_document_file` writes the Markdown file row and replaces any stale document
   child rows in one transaction, while clearing incompatible code/config/import rows for
   that file id.
6. `seam_grounding` or `seam grounding` calls `query_grounding` with a target-driven
   lookup (`--symbol`, `--file`, `--route`, `--config`, `--resource`) or a docs-first
   lookup (`--query`, `--doc-kind`, `--status`, `--relation`).
7. Results are ranked by confidence, resolution, document path, and line. Optional
   snippets are read from the live working tree and capped by configuration.
8. The handler attaches stale-index status when local files changed after indexing, so
   callers know whether to run `seam sync` before relying on the evidence.

## Hidden Invariants

- Grounding tables are additive schema v16 state. Old indexes must degrade with an
  `UNSUPPORTED` warning instead of crashing.
- Document references are evidence rows, never graph edges. If this invariant changes,
  impact and trace semantics will become misleading.
- Markdown indexing must run after code/config/resource indexing when resolving exact
  references during a full init.
- Secret-looking assignment lines must not be stored in anchor search text. The index can
  store key names and redacted shapes, but not sensitive values.
- `doc_path` filters the document being searched. It does not mean "documents that
  reference this doc."
- Snippets are bounded and optional because MCP responses must stay predictable.
- Workspace federation and artifact compatibility constants must move with schema
  versions, even when the migration is additive.

## Extension Points

- Add new document kinds in `classify_doc` when the repo adopts another durable local
  convention, such as runbooks or release plans.
- Add new reference detectors in `seam.indexer.docs` only when the syntax is explicit
  enough to explain its provenance and confidence.
- Add lower-confidence semantic doc search as a separate mode or separate confidence tier;
  do not dilute exact grounding results.
- Add compact grounding hints to `seam_plan` or `seam_context` only after measuring payload
  growth and agent usefulness.
- Add graph artifact export for document evidence when callers need a portable snapshot of
  what the index knew at a specific time.

## Executable Commands

### Test Functionality

```bash
# Focused docs/spec grounding unit and transport coverage.
uv run pytest tests/unit/test_grounding.py -q

# Schema registry coverage for seam_grounding tool visibility.
uv run pytest tests/unit/test_schema_tool.py::test_schema_mcp_registration -q
```

### Validate Output

```bash
# Verify populated grounding capability and counts in the current checkout.
uv run seam schema --json | jq '.data | {
  has_doc_anchors: .capabilities.has_doc_anchors,
  has_doc_grounding: .capabilities.has_doc_grounding,
  document_files: .counts.document_files,
  document_anchors: .counts.document_anchors,
  document_references: .counts.document_references
}'

# Verify that a docs-first grounding query returns bounded candidates and caveats.
uv run seam grounding --query "docs/spec grounding" --json
```

### Health Check And Debugging

```bash
# Refresh document anchors/references after editing docs.
uv run seam sync

# Inspect stale-index state before trusting grounding output.
uv run seam schema --json | jq '.data.freshness'

# Inspect a specific symbol or file after grounding identifies the code target.
uv run seam context <symbol> --json
```

Expected healthy state for this repository:

- `has_doc_anchors` is `true`.
- `has_doc_grounding` is `true`.
- `document_files`, `document_anchors`, and `document_references` are non-zero.
- `seam_grounding` appears in `seam_schema.tools`.

## Security And Compliance

Docs/spec grounding preserves Seam's local-first trust model:

- It reads local repository documentation only.
- It does not fetch GitHub issues, pull requests, external links, package registries, or
  web pages.
- It keeps document references outside the code dependency graph, so spec evidence does
  not become impact evidence.
- It stores bounded anchor search text and reference metadata, not full Markdown bodies.
- It filters secret-looking assignment lines from searchable anchor text.

Data handling:

| Data | Stored | Rationale |
|---|---:|---|
| Document path, title, kind, status | yes | Lets agents rank and filter local docs. |
| Heading path and anchor line range | yes | Lets agents inspect bounded source evidence. |
| Explicit reference target and provenance | yes | Explains why a doc is connected to code. |
| Full document body | no | Prevents the index from becoming a document mirror. |
| Secret values or environment values | no | Preserves the no-secret indexing contract. |

## Observability And Error Signals

Grounding health is exposed through normal Seam read surfaces instead of telemetry:

| Signal | Where to Check | Meaning |
|---|---|---|
| `has_doc_anchors` | `seam schema --json` | Document anchor tables are populated. |
| `has_doc_grounding` | `seam schema --json` | Resolved document references are populated. |
| `document_*` counts | `seam schema --json` | Current index volume for docs/spec evidence. |
| `index_status` / `freshness.stale` | `seam schema`, `seam grounding` | Run `seam sync` before trusting changed docs. |
| `UNSUPPORTED` warning | `seam grounding --json` | The opened index is too old or lacks grounding tables. |

Common recovery actions:

| Condition | Recovery |
|---|---|
| Missing `.seam/` index | Run `uv run seam init`. |
| Stale docs after local edits | Run `uv run seam sync`. |
| Grounding tables unsupported | Rebuild with current Seam using `uv run seam init`. |
| Low-confidence reference only | Verify with `seam snippet`, `seam context`, or direct file read before editing. |

## Related Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) lists the schema tables and tool inventory.
- [`api-contracts/mcp-tools.yaml`](api-contracts/mcp-tools.yaml) defines the MCP contract
  for `seam_grounding`.
- [`CONFIGURATION.md`](CONFIGURATION.md) documents `SEAM_GROUNDING_DEFAULT_LIMIT`.
- [`agent-answerability-benchmark.md`](agent-answerability-benchmark.md) explains how
  docs/spec grounding is measured as answerability coverage.
- [`prd/agent-answerability-graph-quality-coherence.md`](prd/agent-answerability-graph-quality-coherence.md)
  explains why stale roadmap and tracker signals need deterministic coherence checks.

## What to Read First

1. [`seam/indexer/docs.py`](../seam/indexer/docs.py) explains the conservative extraction
   contract and the exact-resolution rules.
2. [`docs/database/schema.sql`](database/schema.sql) shows the v16 grounding tables and
   indexes.
3. [`seam/query/grounding.py`](../seam/query/grounding.py) shows how candidates are
   filtered, ranked, caveated, and converted into next calls.
4. [`seam/server/grounding_handler.py`](../seam/server/grounding_handler.py) shows how
   grounding inherits the standard stale-index warning.
5. [`seam/cli/grounding.py`](../seam/cli/grounding.py) shows the user-facing command
   surface and output modes.

---

**Last Updated**: 2026-07-06
**Agent Status**: Production docs/spec grounding reference
**Issue Status**: #371 ready to close as shipped
**AGENTS.md Compliant**: Yes
**Machine-Readable**: YAML frontmatter plus executable validation commands
