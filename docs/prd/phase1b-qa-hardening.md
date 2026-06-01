# PRD — Phase 1b: QA hardening (issues #10 + #11)

> Slice of Phase 1b. Parents: issue #10 (test-caller distinction) + issue #11 (configurable cap + partial verdict). Status: ready-for-agent.
> Two small, related QA-driven enhancements bundled into one PR.

## Problem Statement

As an AI agent using `seam impact` to decide what production code breaks if I change a
symbol, the blast-radius results are dominated by test functions and pytest fixtures
(`test_*`, `conftest`, fixtures). Asking "what breaks if I change `connect`" returns
mostly test callers, burying the real production dependents. It is *correct* but low
signal-to-noise for the primary question. (#10)

As the same agent running `seam changes` on a large branch diff, the overall `risk_level`
is silently computed from only the first 50 changed symbols (a hardcoded cap). The cap
logs a warning, but the verdict in the report/CLI does not say it is partial, so I can
over-trust a risk level that only saw a subset. The cap is also not configurable. (#11)

## Solution

- **#10:** Every `seam impact` dependent is tagged `is_test` (true/false) via a documented
  path heuristic. Default output includes everything, with test entries marked. An opt-in
  `--production-only` flag (CLI) / `include_tests=false` (tool) filters test dependents out,
  so an agent can ask "what *production* code breaks?" and get a clean answer.
- **#11:** The cap moves to `seam/config.py` (`SEAM_MAX_IMPACT_SYMBOLS`, env-overridable,
  default 50). `ChangeReport` gains a `partial` boolean, true when the cap was hit; the CLI
  and handler note the risk verdict is partial (e.g. "⚠ PARTIAL — cap 50 hit; 50/233 analyzed").

## User Stories

1. As an agent, I want each `seam impact` dependent tagged `is_test`, so that I can tell production dependents from test dependents at a glance.
2. As an agent, I want a `--production-only` flag on `seam impact`, so that I can filter out test callers and see only production blast radius.
3. As an agent, I want the test/production distinction available on the `seam_impact` MCP tool (`include_tests` parameter), so that programmatic callers get the same filtering.
4. As an agent, I want the default `seam impact` behavior unchanged (all dependents shown, tests marked), so that nothing I rely on breaks.
5. As a maintainer, I want the "is this a test file" heuristic documented and tested in one place, so that the rule has a single source of truth.
6. As an agent, I want the heuristic to flag files under a `tests/` or `test/` directory segment, files named `test_*`/`*_test.py`/`*.spec.*`/`*.test.*`, and `conftest.py`, so that the common Python/TS test conventions are covered.
7. As an agent, I want `is_test=false` for dependents with no indexed file (external/unresolved names), so that uncertainty never masquerades as "production".
8. As an agent running `seam impact --production-only`, I want test entries removed from every tier (WILL_BREAK / LIKELY_AFFECTED / MAY_NEED_TESTING), so that the filter is consistent across the whole result.
9. As a maintainer, I want `_MAX_IMPACT_SYMBOLS` to read from `seam/config.py` (`SEAM_MAX_IMPACT_SYMBOLS`), so that the cap can be raised via env var without a code change.
10. As an agent running `seam changes` on a big diff, I want the report to carry `partial=true` when the cap was hit, so that I know the risk verdict saw only a subset.
11. As an agent, I want the CLI `seam changes` output to print a clear PARTIAL marker (with the cap and the analyzed/total counts) when the verdict is partial, so that I do not over-trust it.
12. As an agent, I want `partial=false` and no marker on normal-sized diffs, so that the signal only appears when it matters.
13. As a maintainer, I want the cap behavior to still log its existing warning, so that observability is unchanged.

## Implementation Decisions

- **Test heuristic — single documented helper.** A pure function `is_test_file(path: str) -> bool` lives in the analysis layer (importable by impact and, if needed later, changes). Rule (documented): returns true when the path has a `tests` or `test` directory segment, OR the basename matches `test_*`, `*_test.py`, `*.spec.{js,jsx,ts,tsx}`, `*.test.{js,jsx,ts,tsx}`, or is `conftest.py`. Case-insensitive on the basename patterns. `None`/empty path → false.

- **#10 tagging (decision: tag + opt-in filter).** `impact()` adds `is_test` to every TieredEntry (computed from `file_map` — the absolute path; entries with no file → `is_test=false`). A new `include_tests: bool = True` parameter on `impact()` filters test entries out of all tiers when false (default keeps current behavior). The `seam_impact` handler and MCP tool expose `include_tests` (default true); the CLI exposes `--production-only` (sets `include_tests=false`). The CLI renders a `[test]` marker on test entries when tests are shown. No API shape break — `is_test` is additive.

- **#11 configurable cap (decision: config value + partial flag, no CLI flag).** Add `SEAM_MAX_IMPACT_SYMBOLS` to `seam/config.py` (int, env `SEAM_MAX_IMPACT_SYMBOLS`, default 50). `changes.py` reads it from config instead of the module constant. `_collect_impact` reports whether it truncated; `detect_changes` sets a new `ChangeReport["partial"]` boolean accordingly. The CLI `seam changes` and the `seam_changes` handler surface `partial` (CLI prints a PARTIAL marker with cap + counts). Risk computation itself is unchanged — `partial` is purely informational. The existing truncation `warning` log stays.

- **Config access rule respected.** The cap is read via `seam/config.py` only — no `os.getenv` in `changes.py`.

- **Import hierarchy preserved.** The test heuristic helper stays in the analysis layer; no new server/cli imports into analysis.

## Testing Decisions

- **What makes a good test:** assert external behavior — given indexed symbols in test vs production files, `impact()` tags `is_test` correctly and `--production-only`/`include_tests=false` removes exactly the test entries from every tier; given a diff with more changed symbols than the cap, `detect_changes` returns `partial=true`; below the cap, `partial=false`.
- **Modules tested:**
  - `is_test_file` heuristic — unit tests for each pattern (tests/ dir, test/ dir, `test_x.py`, `x_test.py`, `*.spec.ts`, `*.test.tsx`, `conftest.py`) and the negatives (`production.py`, `latest.py` must NOT match, empty/None → false).
  - `impact()` — `is_test` present on entries; `include_tests=false` filters across all tiers; entries with `file=None` are `is_test=false`.
  - `detect_changes` — `partial=true` when changed real-symbol count exceeds the configured cap (use a small monkeypatched/`config`-set cap to avoid building a 51-symbol fixture); `partial=false` otherwise.
- **Prior art:** `tests/unit/test_impact.py`, `tests/integration/test_changes.py`, `tests/integration/test_impact_handler.py`. Follow their temp-DB fixture and assertion style.
- **TDD:** write the failing `is_test_file` and `partial` tests first.

## Out of Scope

- Tagging/filtering test callers on `seam_trace` / `callers` / `callees` (the `is_test` data needs a file lookup those paths don't currently do) — #10's hard ACs are about `seam impact`; the rest is a possible later follow-up.
- A CLI flag to override the cap per-invocation (`--max-impact-symbols`) — AC marks it optional; config env var covers it.
- Changing the risk-rollup math when `partial` — `partial` is informational only.
- A dedicated `is_test` column on the symbols table — the path heuristic at read time avoids a schema migration and stays correct under re-index.
- Single multi-seed traversal to raise the practical cap (mentioned in #11 as a "consider") — out of scope; configurability + the partial marker address the AC.

## Further Notes

- Heuristic honesty: `is_test=false` for unresolved/external names keeps the tool from ever labelling unknown code as "production".
- The `partial` marker closes the exact gap `/qa` flagged: a risk verdict computed on a subset now says so, instead of reading as a full verdict.
- Acceptance criteria (from #10): impact distinguishes production vs test dependents; default documented; opt-in flag; heuristic documented + tested; gate green. (from #11): cap configurable via config.py; partial verdict marked when cap hit; gate green.
