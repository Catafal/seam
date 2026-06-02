# Phase 6 — Context-Pack Primitive (`seam_context_pack` / `seam pack`)

> Depth tier, roadmap `.claude/research/codegraph-vs-seam.md` §8 item 6 / §4.5a.
> Builds on the existing read path: `engine.context()`, `comments.why()`, the name-keyed
> `edges` graph, and `clusters`. **No schema change.**

## Problem Statement

As an AI agent about to modify a symbol, I currently have to make several separate Seam
calls to understand it: `seam_context` for the 360° view, `seam_why` for the rationale
comments, and repeated `seam_context` calls on each caller/callee to learn what those
neighbors actually look like (their signature, file, kind). That is N round-trips, manual
stitching, and the neighbor lists I get back from `seam_context` are bare *names* — to know
whether `parse` is the right `parse` I have to look each one up myself.

I want a single call that returns a **ready-to-paste context bundle** for a symbol: the
symbol itself fully enriched, its direct callers and callees *already enriched* (signature,
file, line, kind — not just names), the WHY/HACK/NOTE comments that explain it, and its
functional-area peers — all in one structured payload, with homonym noise controlled so one
file's same-named symbols cannot flood the bundle.

## Solution

A new orchestration primitive, `context_pack(conn, symbol_name, *, repo_root=None)`, living in
a new leaf module `seam/query/pack.py`. It composes the *existing* read primitives — it adds
no new extraction, no new table, no new edge semantics. It is surfaced two ways:

- **MCP tool** `seam_context_pack` — for agents.
- **CLI command** `seam pack <symbol>` — same bundle, with `--json` / `--quiet`.

The bundle (the `ContextPack`) contains:

1. **`target`** — the symbol's full 360° context: file, line range, kind, docstring,
   signature, decorators, is_exported, visibility, qualified_name, cluster_id, cluster_label,
   and the `ambiguous` flag (true when the name collides across files).
2. **`callers`** and **`callees`** — the direct 1-hop neighbors, each **enriched** to
   `{name, file, line, kind, signature}` rather than a bare name string. Resolved by looking
   up each neighbor name in the `symbols` table (first match per name).
3. **`why`** — the semantic comments (WHY/HACK/NOTE/TODO/FIXME) attached to the target symbol,
   via `comments.why(symbol=...)`.
4. **`cluster_peers`** — the functional-area peers already produced by `context()`.

**Homonym mitigation** (the §4.5a "per-file diversity cap"): when enriching neighbors, cap how
many enriched entries come from any single file (`SEAM_PACK_PER_FILE_CAP`, default 3). This
stops a file that defines twenty same-named helpers from drowning the bundle, and keeps the
pack diverse across the codebase. Neighbor lists are also globally capped
(`SEAM_PACK_NEIGHBOR_LIMIT`, default 10 each) and comments capped
(`SEAM_PACK_MAX_COMMENTS`, default 10).

It is **1-hop only** — no transitive call-path DFS in this phase (explicitly deferred; see Out
of Scope). The read path stays 100% local and never raises: a missing symbol returns `None`
(same contract as `context()`), and every sub-lookup degrades to empty rather than failing the
whole pack.

## User Stories

1. As an AI agent, I want one call that returns a symbol plus its enriched neighbors and
   rationale, so that I can understand a symbol without making five separate Seam calls.
2. As an AI agent, I want each caller/callee in the bundle to carry its signature, file, line,
   and kind, so that I can tell *which* `parse` is meant without a follow-up lookup.
3. As an AI agent, I want the WHY/HACK/NOTE comments for the symbol included in the bundle, so
   that I learn the non-obvious rationale before I edit.
4. As an AI agent, I want the target's cluster peers in the bundle, so that I see the
   functional area the symbol belongs to.
5. As an AI agent, I want the `ambiguous` flag surfaced on the target, so that I know when the
   name collides across files and the bundle may mix declarations.
6. As an AI agent, I want neighbors from a single file capped, so that one file's homonyms do
   not flood the bundle and bury the diverse callers I actually need.
7. As an AI agent, I want the caller and callee lists each bounded to a sane maximum, so that a
   hot utility called from 300 sites returns a usable bundle instead of a wall of entries.
8. As an AI agent, I want a count of how many neighbors were omitted by the caps, so that I
   know the bundle was truncated and can fall back to `seam_impact` for the full blast radius.
9. As a developer, I want a `seam pack <symbol>` CLI command, so that I can inspect the bundle
   a human-readable way from the terminal.
10. As a developer, I want `seam pack --json`, so that I get the same structured envelope my
    scripts already parse from the other read commands.
11. As a developer, I want `seam pack --quiet`, so that I get a terse human rendering without
    the JSON envelope.
12. As an AI agent, I want a missing symbol to return a clean "not found" result, so that the
    pack has the same predictable contract as `seam_context`.
13. As an AI agent, I want the pack to work on a pre-v5/pre-v6 index by returning `null` for
    fields that index cannot supply, so that the contract degrades gracefully rather than
    erroring.
14. As an AI agent, I want the bundle's enrichment fields (signature, decorators, etc.) to use
    the same `null`-means-unknown contract as the other tools, so that callers handle one
    contract everywhere.
15. As an AI agent, I want the pack to never make a network call or require an API key, so that
    it honors Seam's zero-external-services guarantee.
16. As a developer, I want the new config knobs documented in CLAUDE.md, so that I can tune the
    caps without reading source.
17. As an AI agent, I want the MCP tool and the CLI command to produce the identical bundle, so
    that behavior does not diverge between the two entry points.
18. As an AI agent, I want the pack to reuse `context()`'s import-resolution `repo_root`
    threading where it improves neighbor accuracy, so that the bundle benefits from Phase 5
    resolution when `repo_root` is available — without it being mandatory.

## Implementation Decisions

- **New leaf module `seam/query/pack.py`.** Imports only stdlib + the existing read primitives
  (`engine.context`, `comments.why`, the `symbols`/`edges` tables). It owns no SQL schema and
  performs only reads. Single public function `context_pack(conn, symbol_name, *, repo_root=None)
  -> ContextPack | None`.
- **`ContextPack` is a `TypedDict`** (consistent with `ContextResult`, `CommentHit`, etc.).
  Shape:
  ```
  ContextPack:
    target:        ContextResult-equivalent (the full 360° dict)
    callers:       list[NeighborRef]   # enriched, capped
    callees:       list[NeighborRef]   # enriched, capped
    why:           list[CommentHit]    # capped
    cluster_peers: list[str]           # from target
    truncated:     { callers: int, callees: int, comments: int }  # counts omitted by caps
  NeighborRef:
    name, file, line, kind, signature   # signature may be null on pre-v5 rows
  ```
- **Neighbor enrichment** is a single batched lookup over the distinct neighbor names against
  the `symbols` table (first match per name, lowest id — mirrors `context()`'s tie-break), then
  per-file capping applied in a stable order. Avoid N+1 queries: gather all distinct neighbor
  names, resolve them in one `WHERE name IN (...)` pass.
- **Caps are config-driven**, never hardcoded — values come from `seam/config.py` only:
  - `SEAM_PACK_NEIGHBOR_LIMIT` (default 10) — max enriched callers and max enriched callees.
  - `SEAM_PACK_PER_FILE_CAP` (default 3) — max neighbor entries from any single file.
  - `SEAM_PACK_MAX_COMMENTS` (default 10) — max WHY comments in the bundle.
- **`truncated` counts** report how many neighbors/comments were dropped by the caps so the
  agent knows the bundle is partial (story 8).
- **Reuse, do not reimplement.** `target` is produced by calling `engine.context()` verbatim;
  `why` by calling `comments.why(symbol=...)`. The only genuinely new logic is neighbor
  enrichment + per-file capping + truncation accounting.
- **MCP tool `seam_context_pack`** — a thin adapter in `seam/server/tools.py` mirroring the
  existing handler pattern (`handle_seam_context`). Serializes the `ContextPack`, relativizes
  file paths against `repo_root` the way the other handlers do.
- **CLI command `seam pack <symbol>`** in `seam/cli/main.py`, wired into the existing
  `--json` / `--quiet` envelope path (`seam/cli/output.py`). Rich rendering shows the target
  header, an enriched caller/callee table, and the WHY comments. JSON mode emits the
  `{ok, data}` envelope.
- **No schema change, no migration.** The pack reads only existing tables. It works on any
  index ≥ v4 and degrades on older ones via the same guards `context()`/`why()` already use.
- **MCP tool count goes 9 → 10.** `seam_context_pack` is the new tool. No existing tool changes.

## Testing Decisions

A good test here asserts **external behavior of the bundle**, not the internal query plan:
given an indexed fixture, calling `context_pack` returns a bundle whose neighbors are enriched,
whose caps hold, and whose contract matches `context()` for the missing/ambiguous cases. Do not
assert SQL strings or call counts.

Modules under test (all three emphasized per the design gate):

1. **`pack.py` orchestration** (`tests/unit/test_pack.py` + `tests/integration/test_pack_*.py`):
   - target enrichment present; callers/callees enriched with `{name,file,line,kind,signature}`.
   - missing symbol → `None` (matches `context()`).
   - WHY comments included and capped at `SEAM_PACK_MAX_COMMENTS`.
   - `truncated` counts correct when neighbor/comment lists exceed caps.
   - graceful degradation: a neighbor name with no `symbols` row is skipped, not fatal.
   - Prior art: `tests/integration/test_phase5_read_layer.py`,
     `tests/unit/test_confidence_phase5.py`, existing `context()` tests.
2. **MCP tool + CLI parity** (`tests/integration/test_pack_parity.py`):
   - `handle_seam_context_pack` and `seam pack` produce the same bundle for the same symbol.
   - `--json` returns a valid `{ok:true,data:...}` envelope; missing symbol returns the proper
     error/empty envelope; `--quiet` renders without the envelope.
   - Prior art: existing CLI `--json`/`--quiet` parity tests for `seam context` / `seam affected`.
3. **Homonym diversity** (`tests/integration/test_pack_homonym.py`):
   - a fixture where one file defines many same-named neighbors → per-file cap holds; the
     bundle stays diverse across files.
   - an ambiguous target → `target.ambiguous == True` surfaced in the pack.
   - Prior art: the homonym-collapse fixtures from Phase 5
     (`tests/integration/test_phase5_e2e_homonym.py`).

Run `make gate` (ruff + mypy + pytest) before every commit — must stay green.

## Out of Scope

- **Transitive call-path DFS.** This phase is 1-hop only. A depth-bounded walk
  (`SEAM_PACK_PATH_DEPTH`) showing how the symbol is reached transitively is explicitly
  deferred to a future phase (the rejected design-gate option B).
- **New schema / new table / new edge kinds.** None. The pack is read-only orchestration.
- **A second-hop neighbor enrichment** (neighbors-of-neighbors). Out.
- **LLM summarization of the bundle.** The pack is mechanical assembly; no model calls. Honors
  zero-external-services.
- **Ranking/scoring of neighbors** beyond the deterministic per-file cap + stable ordering.
  No BM25, no proximity scoring inside the pack (that lives in `search`/`confidence`).
- **Changing `seam_context`.** The existing tool is untouched; the pack is additive.
- **Persisting anything** (`resolved_by`, pack results) — all read-time, nothing stored.

## Further Notes

- Provenance/import-resolution: where `repo_root` is supplied, neighbor accuracy benefits from
  the Phase 5 resolver indirectly (via `context()`), but the pack does not itself re-run edge
  resolution — it surfaces what the existing primitives return. `repo_root` stays optional.
- The per-file diversity cap is the same homonym-mitigation idea CodeGraph applies at
  `src/context/index.ts:978`; Seam's name-keyed edges make the 1-hop gather trivial.
- This is the first item of the Depth tier; items 7 (`seam sync`) and 8 (`seam install`) remain
  for subsequent phases and are not touched here.
