# PRD: WS6.1 — Agent-trace-derived eval goldens (trace-capture loop)

> Roadmap source: `.claude/tasks/cursor-informed-roadmap.md` §8.1 (WS6, P3 research track).
> Now schedulable because the answerability harness (`tests/eval/`, PR #323) exists to receive derived goldens.

## Problem Statement

Seam's eval benchmark (`make eval`, `make eval-answerability`) is only as good as its
**hand-authored** goldens. Every recall golden (`golden.json`: `query → expected_symbols`)
and every answerability scenario (`answerability_scenarios.json`: `question → tool_plan →
expected_facts`) is written by a human guessing what *should* surface. This has two costs:

1. **It rewards what we imagined, not what real tasks need.** A human author encodes their
   mental model of "a good result"; they cannot encode the retrieval failures they never
   thought to test. The benchmark can be green while real agent sessions on real repos keep
   missing the symbol that actually mattered.
2. **It does not learn.** Cursor trains its embedding model on agent-session traces — "what
   retrieval would have helped, in hindsight." Seam cannot train a model cheaply, but today it
   captures *nothing* from real sessions, so it has no hindsight signal to steer WS1 embedding
   tuning or to grow the golden set toward task-usefulness.

There is currently **no mechanism to capture what an agent actually queried in a real session,
correlate it with what the agent actually ended up needing, and turn the gap into a golden.**
(`SEAM_DIAGNOSTICS` deliberately cannot do this — its structural-redaction invariant records
only tool names + numeric metrics, never query args or result content.)

## Solution

A **local, opt-in, offline trace-capture loop** that closes the hindsight gap in three steps:

1. **Capture** — with `SEAM_TRACE_CAPTURE=1`, Seam records each read-path tool call from a real
   session (the query args + the *symbol names* it returned + counts — symbols-only, not full
   bodies) to a local, gitignored NDJSON trace under `.seam/`.
2. **Derive** — offline, a pure deriver correlates each captured retrieval query against the
   **hindsight outcome signal** — the set of symbols the agent actually *edited* during the
   session (from the session's git diff, mapped to symbols via the existing index). For each
   query it emits a **golden candidate** (`query → expected_symbols = the symbols that mattered`)
   and flags **gap candidates**: queries where a symbol the agent later edited did *not* surface
   in the captured result. Gap candidates are the highest-value goldens — provable retrieval
   failures on real work.
3. **Promote** — derived candidates are written to a **review file**, never auto-merged. A human
   approves candidates into a **separate, repo-keyed live golden set** (distinct from the
   fixture-keyed `golden.json` so the deterministic gate is never polluted), and may hand-lift a
   generalizable insight into the fixture benchmark as a new scenario.

The result: the benchmark grows from real sessions and rewards task-usefulness, and WS1 gets an
objective tuning target — without any model training, any network call, or any change to the
deterministic gate.

## User Stories

1. As a Seam maintainer, I want to opt into local session-trace capture with a single env var, so
   that I can start collecting real usage signal without changing how I run Seam.
2. As a Seam maintainer, I want trace capture to default OFF and be byte-identical to today when
   off, so that no user pays any cost or privacy surface unless they explicitly choose to.
3. As a privacy-conscious user, I want captured traces to stay 100% local (gitignored `.seam/`,
   never networked), so that turning capture on never exfiltrates my code or queries.
4. As a privacy-conscious user, I want capture to store query text + result *symbol names* only
   (not full result bodies or source), so that the on-disk trace is the minimum needed to derive
   goldens.
5. As a Seam maintainer, I want each trace record to identify its session and the tool call
   (tool, args, returned symbols, count, timestamp), so that the deriver can reconstruct what was
   asked and what came back.
6. As a Seam maintainer, I want the recorder to never raise and to flush on process exit, so that
   a capture failure can never break a real tool call or lose a session's tail.
7. As a Seam maintainer, I want capture wired into both the MCP server and the CLI read path with
   zero overhead when off, so that both transports contribute traces on the same terms as
   `SEAM_DIAGNOSTICS`.
8. As a Seam maintainer, I want the hindsight outcome signal to come from the session's git diff
   (the symbols I actually edited), so that "what should have surfaced" is grounded in what the
   task actually needed, not in a guess.
9. As a Seam maintainer, I want the diff→symbol mapping to reuse the existing index (file + line
   ranges), so that a changed hunk resolves to the qualified symbols it touched with no new
   parsing.
10. As a Seam maintainer, I want the deriver to be a pure function (trace + outcome → candidates)
    with no IO, so that it is deterministic and fully unit-testable on synthetic traces.
11. As a Seam maintainer, I want each derived candidate to carry provenance (session id, source
    query, derived-at), so that I can trust and audit where a golden came from.
12. As a Seam maintainer, I want the deriver to flag "gap" candidates (edited symbol absent from
    the query's captured result), so that I can prioritize the retrieval failures that cost real
    work over confirmations of things that already worked.
13. As a Seam maintainer, I want derived candidates deduplicated by (tool, query), so that a
    repeated query across sessions produces one candidate, not noise.
14. As a Seam maintainer, I want derived candidates written to a review file and never
    auto-merged into any golden set, so that the benchmark's quality bar stays under human
    control.
15. As a Seam maintainer, I want approved candidates to land in a **separate, repo-keyed** live
    golden set (not the fixture-keyed `golden.json`), so that the deterministic fixture gate is
    never affected by live-repo goldens.
16. As a Seam maintainer, I want a single command to run capture→derive→review end-to-end on an
    already-captured trace, so that closing the loop is one obvious step, not a pile of scripts.
17. As a Seam maintainer, I want the live golden set to record the repo commit SHA it was derived
    against, so that a stale golden (symbols since renamed/removed) is detectable, not silently
    wrong.
18. As a Seam maintainer, I want the whole loop to be offline and add no runtime dependency, so
    that it honors Seam's zero-external-services and local-first contracts.
19. As a Seam maintainer, I want capture/derive to be excluded from `make gate` (like
    `bench-semantic` and the soak harness), so that a research tool never gates commits.
20. As a WS1 owner, I want the accumulated gap candidates surfaced as a ranked list, so that I
    have an objective, real-session target for embedding-input tuning.
21. As a reviewer, I want the capture policy (symbols-only, opt-in, local, gitignored) documented
    alongside `SEAM_DIAGNOSTICS`, so that the deliberate reversal of the diagnostics redaction
    invariant is explicit and justified, not accidental.
22. As a Seam maintainer, I want to disable capture and delete `.seam/traces/` at any time with no
    residue elsewhere, so that opting out is complete and trivial.
23. As a new contributor, I want the trace loop's modules to be small and isolated, so that I can
    understand and extend the deriver without touching the runtime read path.
24. As a Seam maintainer, I want a captured trace with no correlating git diff (read-only session)
    to derive zero candidates rather than error, so that browsing sessions degrade cleanly.
25. As a Seam maintainer, I want capture to work whether the session used the MCP tools or the CLI
    commands, so that both agent styles feed the same loop.

## Implementation Decisions

- **Three concerns, three modules, deliberately split by lifetime:**
  - **Recorder (shipped, runtime):** a `TraceRecorder` in the installed package, mirroring the
    `DiagnosticsRecorder` discipline — a null recorder when `SEAM_TRACE_CAPTURE != "1"` (every
    method a no-op, no file opened, no `atexit` handler, byte-identical read path), a
    one-per-process singleton shared by MCP + CLI, `atexit` flush, and **never raises**. It is
    wired via the same instrumentation seam as diagnostics (a decorate-once wrap on the MCP tools
    that returns the tool unchanged when off; a `run_query`-style passthrough on the CLI read
    commands), so overhead when off is a single attribute check.
  - **Deriver (offline, dev tooling):** a **pure** `derive_goldens(trace_records, outcome) →
    list[GoldenCandidate]` — no IO, no config, no DB — plus a pure `derive_outcome_from_diff(diff,
    index) → set[symbol]`. These live with the eval harness, not on the runtime path.
  - **Curation glue (offline, dev tooling):** reads captured traces, runs the deriver, writes a
    review file, and (on human approval) merges approved candidates into the live golden set.
- **Capture policy — the one deliberate security reversal, tightly bounded (from the approved
  design questions):** trace records store **query args + returned symbol NAMES + counts only** —
  never full result bodies or source text. Default **OFF**; local-only NDJSON under
  `.seam/traces/` (already gitignored via `.seam/.gitignore: *`); never networked. This reverses
  the diagnostics structural-redaction invariant *on purpose* (deriving a golden requires knowing
  the query and what came back) and must be documented next to `SEAM_DIAGNOSTICS`, with the
  symbols-only bound as the mitigation.
- **Hindsight outcome signal = the session's git diff** (approved): the symbols the agent actually
  edited are the ground-truth relevant set. The diff→symbol mapping reuses the index's existing
  symbol file+line ranges (a changed hunk resolves to the qualified symbols whose range it
  intersects). Read-only sessions (empty diff) derive zero candidates — not an error.
- **Golden target = `golden.json` recall shape** (approved): a candidate is `{tool, query, k,
  expected_symbols, provenance}`, matching the recall harness so approved candidates run through
  the existing `recall_harness` / recall@K + MRR metric unchanged. The richer
  `answerability_scenarios.json` is out of scope for auto-derivation (human-curated only).
- **Fixture vs live separation (critical for a deterministic gate):** the existing `golden.json`
  and `answerability_scenarios.json` are keyed to the eval **fixture** (`fixture_hash`) so the
  gate is reproducible. Real-session goldens are against the **live repo** and MUST NOT be merged
  into the fixture set. Approved candidates land in a **separate, repo-keyed live golden set** that
  records the repo commit SHA; it is run by a separate, non-gate eval invocation. A human may
  hand-lift a generalizable insight into the fixture benchmark as a new scenario — that transplant
  is manual, not automatic.
- **Gap candidates are first-class:** the deriver marks a candidate as a `gap` when an
  outcome symbol is absent from that query's captured result symbols. Gap candidates are the
  ranked WS1-tuning signal (story 20) and the priority for promotion.
- **Config knobs (2, both indexing/runtime-tooling, no schema change):** `SEAM_TRACE_CAPTURE`
  (`"0"`/`"1"`, default `"0"` — off = byte-identical) and `SEAM_TRACE_CAPTURE_PATH` (default
  `.seam/traces/`). Consumed only through `seam/config.py`. No new MCP tool (count stays 16). No
  schema change, no migration, no new runtime dependency.
- **Not part of `make gate`:** capture/derive/promote are research tooling (like `make
  bench-semantic` and `make soak`); a new `make` target runs the loop on demand. The deterministic
  fixture recall regression remains the only eval in the gate.

## Testing Decisions

Good tests here verify **observable behavior through the public interface** — feed a module a
constructed input, assert the output — not internal structure. All four modules get isolated
tests (approved test scope), offline, no network, no model download:

- **`trace_derive` (the pure heart) — highest value:** construct synthetic trace records + a
  synthetic outcome set; assert the derived candidates, the `expected_symbols` content, the
  dedup-by-(tool, query) behavior, the gap flag (outcome symbol absent from the query result),
  provenance fields, and the empty-outcome → zero-candidates case. Pure input→output, no fixtures.
- **`outcome_signal` (git-diff → symbols):** feed a synthetic unified diff + a small in-memory
  index (symbol file+line ranges); assert the exact set of qualified symbols whose ranges the
  hunks intersect, including a hunk touching no indexed symbol (→ empty) and a multi-symbol hunk.
- **`trace_capture` (recorder) — mirror the `tests/unit/test_diagnostics.py` suite:** null-when-off
  (no file, no `atexit`, byte-identical), NDJSON line shape, symbols-only content invariant (a
  defense-in-depth assertion that no full-body/source string is ever written — the analog of the
  diagnostics redaction gate test), never-raises on IO failure, opt-in gating, and
  `reset_recorder` teardown hygiene (same autouse-fixture pattern the diagnostics tests use to
  avoid a dangling `atexit` handler).
- **Curation/promote glue — integration-level:** a captured-trace file → derive → write review
  file → approve → merge into a live golden set round-trip; assert approved candidates land in the
  live set (with commit SHA), unapproved ones do not, and the fixture `golden.json` is untouched.

Prior art to follow: `tests/unit/test_diagnostics.py` (recorder discipline + redaction gate test),
`tests/eval/test_metrics.py` + `tests/eval/test_recall_regression.py` (golden/recall shape and
metric), and the `tests/integration/test_diagnostics_*.py` modules (MCP/CLI instrumentation
wiring, no-leak assertions).

## Out of Scope

- **Custom / fine-tuned embedding model (roadmap §8.2).** Explicitly "do not schedule" until a
  trace corpus accrues. This PRD builds the corpus mechanism; training is a separate future PRD.
- **Auto-derivation of `answerability_scenarios.json` scenarios** (question + tool_plan +
  expected_facts). Rich scenarios stay human-curated; only recall goldens auto-derive.
- **Auto-merging derived goldens into the fixture set or into the gate.** Promotion is
  human-in-the-loop into a separate live set; the deterministic fixture gate is untouched.
- **Capturing full result bodies or source text.** Symbols-only is a hard bound this slice.
- **Cross-machine / networked trace aggregation.** Traces are strictly local.
- **A new MCP tool.** The loop is CLI + offline tooling only; MCP tool count stays 16.
- **Non-git repos as the outcome signal.** MVP uses git diff; a non-git outcome source is future.

## Further Notes

- This is the P3 research track made concrete. Its payoff compounds: every real session on any
  repo (Seam's own, or a user's) that opts in becomes a source of objective retrieval-quality
  goldens, and the ranked gap list becomes WS1's tuning target.
- The capture-policy reversal of the diagnostics redaction invariant is the single item most
  worth a careful review pass. Keeping it opt-in, local, gitignored, and symbols-only is what
  makes the reversal defensible — the review should confirm all four hold and that a full-body
  string can never reach the trace file (the defense-in-depth gate test).
- Suggested slice order (tracer bullets): **S1 (HITL) Capture** → **S2 (AFK) Derive** → **S3
  (AFK) Promote + close the loop**. S1 is HITL because it lands the capture-policy decision; S2/S3
  are pure/offline and AFK-safe.
