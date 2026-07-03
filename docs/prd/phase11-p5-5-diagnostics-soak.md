# P5.5 — Opt-in Diagnostics + Soak Testing

Phase 11 roadmap item #15 (roadmap §P5 "Add opt-in diagnostics and soak testing", lines 487–509).

## Problem Statement

As Seam's query volume and watcher activity grow across real sessions, an operator has **no way to see how the running server actually behaves** — whether memory is creeping up over a long-lived `seam start`, whether file descriptors are leaking, whether the SQLite DB is bloating, how many queries have run, or which tool calls are slow. The only signals today are log lines (`SEAM_LOG_LEVEL`) and the one-shot `seam status` counts.

There is also **no repeatable way to exercise the read path under sustained mixed load** to surface leaks, watcher regressions, or slow-query paths before they reach a user. The local-first / no-telemetry contract means Seam cannot phone home with this data — so the operator needs a purely local, opt-in mechanism they can turn on, reproduce a workload against, and inspect offline.

## Solution

Add an **opt-in local diagnostics facility** gated by `SEAM_DIAGNOSTICS=1`. When enabled, Seam records lightweight operational metrics to a local append-only NDJSON file inside `.seam/` (already gitignored):

- resident memory (RSS), best-effort per platform,
- open file-descriptor count, best-effort per platform,
- SQLite DB size on disk,
- cumulative query count for the process,
- per-tool slow-query summaries (tool name + duration + result size only — **never the query arguments or any source text**),
- watcher activity counters (files re-indexed, re-index errors).

Disabled by default, the facility is a true no-op: no file is created, no sampling occurs, and the read path is byte-identical to pre-P5.5 with zero measurable overhead.

Add a **soak script** (`benchmarks/soak.py`, run via `make soak`) that drives a configurable number of mixed `seam_search` / `seam_context` / `seam_impact` / `seam_trace` requests against an already-indexed repo. Run with `SEAM_DIAGNOSTICS=1`, it produces an NDJSON trace plus a human-readable summary (total queries, slow-query count, peak RSS, FD delta, DB size), so an operator can catch leaks and slow paths locally or in optional CI — **without adding any telemetry, network call, or runtime dependency.**

## User Stories

1. As a Seam operator running a long-lived `seam start`, I want to enable diagnostics with a single env var, so that I can watch memory and FD usage over a session without attaching a profiler.
2. As an operator, I want diagnostics **off by default**, so that a normal install has zero overhead and writes no extra files.
3. As an operator, I want the diagnostics data written to a **local file inside `.seam/`**, so that it stays out of git and never leaves my machine.
4. As a privacy-conscious operator, I want the diagnostics file to **never contain source code, query argument text, or secret-like values**, so that enabling it can never leak my codebase or credentials.
5. As an operator debugging a suspected memory leak, I want each diagnostics snapshot to include **RSS**, so that I can see memory trend over time.
6. As an operator debugging a suspected FD leak, I want each snapshot to include an **open file-descriptor count**, so that I can detect descriptors that are never closed.
7. As an operator, I want each snapshot to include the **SQLite DB size on disk**, so that I can spot unexpected index growth.
8. As an operator, I want a **cumulative query count** per process, so that I can correlate resource growth with query volume.
9. As an operator diagnosing latency, I want **slow-query summaries** (tool name, duration in ms, result size in chars), so that I can identify which tool calls are expensive — without recording what was searched for.
10. As an operator, I want a configurable **slow-query threshold** (`SEAM_DIAGNOSTICS_SLOW_MS`), so that I can tune what counts as "slow" for my repo size.
11. As an operator, I want **watcher activity counters** (files re-indexed, re-index errors), so that I can detect a watcher that is thrashing or silently failing.
12. As an operator, I want the diagnostics file path to be **configurable** (`SEAM_DIAGNOSTICS_PATH`), so that I can redirect it to a scratch location if needed.
13. As an operator on macOS (no `/proc`), I want diagnostics to **degrade gracefully** — recording `null` for a metric it cannot obtain rather than crashing, so that the facility is useful cross-platform.
14. As an operator, I want diagnostics to **never crash the server or a query** even if sampling fails, so that turning it on is always safe.
15. As an operator, I want a **per-process snapshot flushed at process exit**, so that even short-lived CLI invocations leave a record.
16. As an operator using the **MCP server**, I want every tool call timed and counted, so that I get a complete picture of server-side query behavior.
17. As an operator using the **CLI read commands** (`seam query/search/context/impact/trace`), I want those invocations recorded too, so that CLI-driven workloads are visible in the same NDJSON file.
18. As an operator running many CLI commands, I want the NDJSON file to be **append-only and safe across concurrent processes**, so that parallel invocations don't corrupt each other's records.
19. As a maintainer, I want a **soak script** that replays mixed read requests against an indexed repo, so that I can reproduce sustained load on demand.
20. As a maintainer, I want the soak script to accept a **configurable iteration count / duration**, so that I can run a quick smoke or a long soak.
21. As a maintainer, I want the soak run to print a **summary** (queries run, slow-query count, peak RSS, FD delta, DB size delta), so that I get a pass/fail signal at a glance.
22. As a maintainer, I want the soak script to be runnable **locally and in optional CI** but **not part of `make gate`**, so that the gate stays fast and offline (mirroring `bench-semantic` / `no-egress`).
23. As a maintainer, I want the diagnostics recorder to be a **deep, isolated module** with a small interface, so that I can unit-test its redaction and formatting guarantees without spawning a server.
24. As a maintainer, I want the redaction guarantee **enforced by tests**, so that a future change that accidentally logs argument text fails the gate.
25. As an operator, I want enabling diagnostics to require **no new install / no extra dependency**, so that it works on any base `seam-code` install.

## Implementation Decisions

### Modules

- **`seam/analysis/diagnostics.py` (new LEAF deep module — the heart of P5.5).**
  A `DiagnosticsRecorder` object created once per process. When `SEAM_DIAGNOSTICS != "1"` it is a **null recorder**: every method is a no-op, no file handle is opened, no sampling runs. When enabled it:
  - `record_query(tool, duration_ms, result_chars)` — increments the process query counter and, when `duration_ms >= SEAM_DIAGNOSTICS_SLOW_MS`, appends a slow-query NDJSON line. **The only fields ever written for a query are: event type, tool name, duration_ms, result_chars, monotonic-ish sequence, timestamp.** Argument values and result *bodies* are never passed in and never stored.
  - `record_watcher_event(kind)` — increments watcher counters (`reindexed`, `reindex_errors`).
  - `sample_resources(db_path)` → a metrics dict `{rss_bytes, open_fds, db_size_bytes, query_count, watcher_reindexed, watcher_errors}` with `null` for any metric unavailable on the platform.
  - `snapshot(db_path)` — samples resources and appends one `event="snapshot"` NDJSON line.
  - Follows the leaf discipline of `staleness.py` / `byte_budget.py` / `steer.py`: **pure-ish, never raises** (any IO/sampling error is caught, logged at WARNING, and the recorder continues as a degraded no-op for that call).
  - **Resource collection is stdlib best-effort (no new dependency):** RSS via `resource.getrusage(RUSAGE_SELF).ru_maxrss` (documented as *peak* RSS, and the byte/kB unit differs Linux vs macOS — normalized and documented); open FDs via counting `/proc/self/fd` on Linux, `null` on platforms without it; DB size via `os.path.getsize`. Metrics that cannot be obtained are `null` — never an error.
  - **NDJSON writer** is internal to this module: append mode with `O_APPEND` semantics (one `json.dumps` object per line, `ensure_ascii=False`), so concurrent CLI processes appending to the same file interleave whole lines without corruption. Default path `.seam/diagnostics.ndjson` (inside `.seam/`, already gitignored via `seam init`'s `.seam/.gitignore`).
  - **Process-exit flush:** when enabled, registers an `atexit` handler that writes a final `snapshot` line, so short-lived CLI invocations still leave a record.

- **`seam/server/mcp.py` (modified — thin instrumentation hook).**
  A single wrapper applied around each registered tool closure (or an extension of `_finalize` that also receives the tool name + start time) times the handler call with a monotonic clock and calls `recorder.record_query(tool, duration_ms, result_chars)`, where `result_chars` is the length of the serialized result (a size proxy, not the content). When diagnostics is disabled the wrapper resolves to the null recorder and adds no measurable overhead. `_finalize`'s existing error/`found:false` contract is unchanged.

- **`seam/cli/read.py` (modified — CLI read-command instrumentation).**
  `seam query / search / context / impact / trace` obtain the process recorder and record each invocation the same way, then rely on the `atexit` snapshot flush. Because each CLI call is a **fresh process**, the query counter reflects a single invocation; cross-invocation trends are reconstructed from the append-only NDJSON file, not from an in-memory counter.

- **`seam/watcher/daemon.py` (modified — watcher counters).**
  The debounced re-index path increments `record_watcher_event("reindexed")` on success and `record_watcher_event("reindex_errors")` on a caught re-index failure. No behavioral change when diagnostics is disabled.

- **`benchmarks/soak.py` (new script) + `make soak` target.**
  Mirrors the existing `benchmarks/run_benchmark.py` prior art (calls handlers directly against an indexed repo). Drives a configurable number of mixed `seam_search` / `seam_context` / `seam_impact` / `seam_trace` requests, optionally under `SEAM_DIAGNOSTICS=1`, then prints a summary (queries run, slow-query count, peak RSS, open-FD delta, DB size). **Not part of `make gate`** (mirrors `bench-semantic` / `no-egress` — local/optional-CI only). Accepts an iteration count (and/or duration) argument.

### Config knobs (all in `seam/config.py`, read-path only)

- `SEAM_DIAGNOSTICS` — `"0"` | `"1"` (default `"0"` = off = byte-identical to pre-P5.5).
- `SEAM_DIAGNOSTICS_PATH` — NDJSON file path (default `.seam/diagnostics.ndjson`).
- `SEAM_DIAGNOSTICS_SLOW_MS` — slow-query threshold in ms (default `100`).

### Invariants

- **No schema change, no migration, no new MCP tool.** MCP tool count stays 16.
- **No new runtime dependency, no new optional extra.** Works on any base `seam-code` install.
- **No network call, no telemetry.** All data is local; the facility only ever writes to the local NDJSON file.
- **Off by default is a true no-op.** No file created, no sampling, no atexit handler registered, no measurable read-path overhead.
- **Redaction is structural:** the `record_query` interface accepts only a tool name and numeric metrics — argument text and result bodies are not parameters, so they *cannot* be written even by mistake.

## Testing Decisions

A good test here asserts **external behavior and the safety contract**, not internal buffering:

- **`seam/analysis/diagnostics.py` — unit-tested thoroughly (the module that MUST be covered):**
  - **Redaction invariant (security-critical):** given a recorded slow query whose (hypothetical) arguments contain a secret-like string and source text, assert that string **never appears** anywhere in the produced NDJSON. This is the test that must fail if a future change starts logging argument text.
  - **No-op when disabled:** with `SEAM_DIAGNOSTICS=0`, assert no file is created and every method returns without side effects.
  - **NDJSON line shape:** each written line is valid JSON with the expected keys (`event`, `tool`/`kind`, numeric metrics, timestamp) and nothing else.
  - **Graceful degradation:** a metric that cannot be sampled on the current platform is `null`, and sampling never raises (simulate an IO failure → recorder degrades, does not crash).
  - **Never raises:** all public methods swallow errors (mirrors `staleness.py` / `byte_budget.py` test discipline).
  - **Slow-query threshold:** a query below `SEAM_DIAGNOSTICS_SLOW_MS` produces no slow-query line; at/above it does.
  - **Append-only / multi-process safety:** two recorders appending to the same path produce interleaved whole lines, all parseable.

- **Integration test (mirrors `tests/integration/` prior art):** run a handful of handler calls with `SEAM_DIAGNOSTICS=1` through the MCP wrapper, assert an NDJSON file appears with the expected number of query records + a final snapshot, and assert **no source text** from the indexed fixture appears in the file.

- **Soak script:** treated as a non-gated script (like `run_benchmark.py`). An optional lightweight smoke test may assert it runs a small iteration count against the eval fixture without error; it is **not** part of `make gate`.

Prior art to follow: `tests/unit/test_actions_pin_audit.py` / `tests/support/egress_audit.py` (pure-leaf + invariant gate test), the "never raises / degrades to safe default" tests around `seam/analysis/staleness.py` and `seam/analysis/byte_budget.py`, and `benchmarks/run_benchmark.py` for the soak harness shape.

## Out of Scope

- **Any telemetry / network export.** Diagnostics is local-file-only; no phone-home, ever.
- **A new optional dependency (psutil, etc.).** Decided: stdlib best-effort with graceful `null`; no `[diagnostics]` extra.
- **Historical aggregation / dashboards / a `seam diagnostics` viewer command.** The output is a raw NDJSON file for the operator to inspect with their own tools (`jq`, etc.). A viewer can be a later item.
- **Per-metric alerting or thresholds beyond the slow-query threshold.**
- **Instrumenting `seam init` / `seam sync` indexing internals.** P5.5 covers the read path (MCP tools + CLI read commands) and watcher counters; deep indexing-pipeline profiling is P4 (pipeline/performance).
- **A schema change, migration, or new MCP tool.**
- **Cross-platform current-RSS parity.** `ru_maxrss` is *peak* RSS and its unit differs by platform; this is documented, not normalized away with a dependency.

## Further Notes

- The facility deliberately mirrors Seam's established opt-in discipline: `SEAM_DIAGNOSTICS=0` (default) is byte-identical to pre-P5.5, exactly like `SEAM_VECTOR_STORE=off` / `SEAM_EDGE_SYNTHESIS=off` revert cleanly.
- The NDJSON file lives inside `.seam/`, which `seam init` already covers with a `.seam/.gitignore` (`*`) — so diagnostics output is never committed and Seam still touches nothing outside `.seam/`.
- Because CLI read commands are separate processes, the in-memory query counter is per-invocation; the append-only NDJSON file is the durable cross-process record. This is a documented consequence of the chosen scope (MCP server + CLI), not a defect.
- The soak script is the natural place to later wire an **optional** CI job (like `no-egress.yml`): run the soak under diagnostics and fail if peak RSS or FD count exceeds a budget. That CI job is out of scope for this PRD but the script is designed to make it a one-file follow-up.
