# PRD: Phase 3 — Agent-First Interface

> Source roadmap: `.claude/research/codegraph-vs-seam.md` §8 (MVP tier).
> Imports modeled on CodeGraph v0.9.8. Schema target: no migration required (v4 stays).

## Problem Statement

Seam is harder for AI coding agents to use than it should be, in three concrete ways an agent hits within the first few queries:

1. **Search silently fails on natural-language queries.** `seam_search` and `seam_query` pass the raw query string straight into the FTS5 `MATCH` clause, where space-separated terms are implicitly AND-ed. A realistic query like `"parse issues board"` returns **zero hits** if any single word isn't a token in some symbol — while `"parse issues"` returns ten. To an agent, zero hits reads as "this code doesn't exist," so it falls back to grep and burns the tokens Seam exists to save.

2. **The CLI is unreadable to a machine.** Every CLI command emits Rich/ANSI markup for a human terminal (`[bold red]WILL BREAK[/bold red]`, decorated tables). There is no `--json`, no `--quiet`, and no stdin support on any command. An agent must either scrape ANSI-decorated output (fragile) or stand up the full MCP server (heavy). There is no clean, composable, scriptable path.

3. **There is no "which tests do I run?" primitive.** Seam can tell an agent the blast radius of a symbol (`seam_impact`) and the risk of a diff (`seam_changes`), but it cannot turn a set of changed files into the concrete set of **impacted test files** an agent should run before committing. That last-mile, action-oriented answer is the single most useful thing an agent wants after editing.

## Solution

Three vertical slices that together make Seam behave the way an agent expects, modeled on the parts of CodeGraph that work well (and improving on the parts that don't):

1. **Search that doesn't silently fail.** Build the FTS5 `MATCH` expression as an OR of prefix terms (so one non-matching word can't zero the query), recover precision with a multi-signal rescore (name-exact/prefix bonus, path relevance, test-file dampening, and Seam's own cluster signal), and add a LIKE→fuzzy fallback so a near-miss still returns something rankable. The agent gets relevant results for natural-language queries instead of an empty list.

2. **A machine-readable CLI.** Add `--json` and `--quiet` to every read command, and `--stdin` to the file-list consumers. JSON output uses a **structured envelope** — `{"ok": true, "data": …}` on success and `{"ok": false, "error": {"code", "message"}}` on failure, with errors emitted as JSON (and a non-zero exit code) when `--json` is set. This is the one place Seam leapfrogs CodeGraph, whose CLI leaves errors as ANSI text on stderr even in JSON mode. The human Rich view stays the default; agents opt into `--json`/`--quiet`.

3. **A `seam affected` command + MCP tool.** Given changed files (as arguments or piped via `--stdin`), Seam returns the impacted test files via reverse-dependency traversal over the existing edge graph. `git diff --name-only | seam affected --stdin --quiet` emits a bare list of test files ready to hand to `pytest`. Exposed both on the CLI and as the `seam_affected` MCP tool.

## User Stories

1. As an AI coding agent, I want `seam_search` to return relevant symbols for a multi-word natural-language query, so that one off-vocabulary word doesn't make me believe the code doesn't exist.
2. As an AI coding agent, I want `seam_query` to use the same forgiving multi-term matching as search, so that concept queries don't silently collapse to empty results.
3. As an AI coding agent, I want search results ranked so that an exact-name match outranks an incidental docstring mention, so that the top result is usually the symbol I meant.
4. As an AI coding agent, I want test-file matches dampened in ranking on non-test queries, so that production code surfaces above its tests.
5. As an AI coding agent, I want a near-miss query (a typo or a stem like "caching" vs "cache") to still return rankable candidates via a fallback, so that I am not blocked by exact-token mismatch.
6. As a Seam maintainer, I want the FTS query-building and rescoring logic isolated in a pure module, so that the matching rules are unit-testable without a database.
7. As an AI coding agent, I want `seam impact --json` to return the blast radius as a structured object, so that I can parse risk tiers without scraping ANSI markup.
8. As an AI coding agent, I want every read command to accept `--json`, so that I have one uniform, parseable interface across the whole CLI.
9. As an AI coding agent, I want a `--quiet` mode that prints only the load-bearing values (e.g. bare file paths), so that I spend minimum tokens and can pipe output directly.
10. As an AI coding agent, I want CLI errors emitted as a JSON object with a stable `code` and `message` when I pass `--json`, so that I can branch on failure programmatically instead of guessing from stderr text.
11. As an AI coding agent, I want a non-zero exit code on failure even in `--json` mode, so that shell pipelines and CI steps fail correctly.
12. As an AI coding agent, I want the JSON success envelope to be consistent (`{"ok": true, "data": …}`) across all commands, so that I can write one response parser.
13. As a developer using Seam in a script, I want `seam status --json` to give me machine-readable index stats, so that I can gate CI on index freshness.
14. As an AI coding agent, I want to pipe a list of changed files into `seam affected --stdin`, so that I can chain it after `git diff --name-only` without writing a temp file.
15. As an AI coding agent, I want `seam affected` to return the impacted test files for a set of changed source files, so that I can run exactly those tests instead of the whole suite.
16. As an AI coding agent, I want `seam affected --quiet` to print bare test-file paths one per line, so that I can pipe them straight into `pytest`.
17. As an AI coding agent, I want a changed file that is itself a test file to be included in the affected set, so that edits to a test still trigger that test.
18. As an AI coding agent, I want `seam affected` to traverse dependents up to a bounded depth, so that the result stays focused and predictable.
19. As an AI coding agent, I want `seam_affected` available as an MCP tool with the same semantics as the CLI command, so that I can use it from an MCP host without shelling out.
20. As an AI coding agent, I want to pipe changed files into `seam changes --stdin`, so that I can run a pre-commit risk check on a precomputed file list.
21. As a Seam maintainer, I want the affected-test computation isolated in its own module reusing the existing impact traversal and test-file classifier, so that it is unit-testable and does not duplicate blast-radius logic.
22. As a Seam maintainer, I want the JSON/quiet emission and the error envelope centralized in one CLI output module, so that the agent-output contract has a single source of truth and is testable in isolation.
23. As an AI coding agent, I want the relevance score surfaced (if at all) labeled as "relevance" and never as a percentage, so that I am not misled by an unbounded heuristic printed as "8374%".
24. As a Seam maintainer, I want a regression test asserting that `"parse issues board"`-style multi-term queries return hits, so that the AND-bug can never silently return.
25. As an AI coding agent, I want `seam_search` and `seam_query` to keep rejecting genuinely malformed input distinctly from "no matches," so that I can tell a bad query from an empty result.
26. As a Seam maintainer, I want the human Rich output to remain the default (no `--json`), so that interactive use is unchanged and only agents opt into structured output.

## Implementation Decisions

**Modules built/modified** (confirmed with the user):

- **NEW `seam/query/fts.py`** — pure leaf module, no DB/IO. Two functions:
  - `build_match_query(text) -> str`: tokenize the raw query, strip FTS5 operators/special characters, wrap each surviving term as a quoted prefix (`"term"*`), and join with `OR`. This is the direct fix for the implicit-AND zero-hit bug.
  - `rescore(rows, terms) -> rows`: multi-signal re-rank over candidate rows — exact-name and prefix-name bonuses, path relevance, test-file dampening on non-test queries, and a boost for candidates sharing the strongest seed's cluster (Seam's `cluster_id`, a signal CodeGraph lacks). The blended score is an unbounded relevance heuristic and must never be presented as a percentage.
- **NEW `seam/analysis/affected.py`** — `affected(conn, changed_files, *, depth, …) -> AffectedResult`. Resolves each changed file's owning symbols, runs the existing upstream `impact` traversal to collect dependents, classifies each dependent's file with the existing `is_test_file()` helper, and returns the unique impacted test files. A changed file that is itself a test file is included directly. Result shape (TypedDict): `{changed_files: list[str], affected_tests: list[str], total_dependents_traversed: int}`. Reuses `seam/analysis/impact.py` and `seam/analysis/testpaths.py` — no duplicate blast-radius logic.
- **NEW `seam/cli/output.py`** — the agent-output contract in one place:
  - success envelope `{"ok": true, "data": <payload>}`,
  - error envelope `{"ok": false, "error": {"code": <str>, "message": <str>}}`,
  - a `--quiet` renderer for bare line-oriented output,
  - the rule that `--json` errors go to stdout as JSON **and** set a non-zero exit code.
- **MODIFY `seam/query/engine.py`** — `search()` and `query()` build their `MATCH` string via `fts.build_match_query()` instead of using the raw text, then pass FTS rows through `fts.rescore()`. Add the LIKE→fuzzy fallback when the FTS path returns zero rows. The existing contract (malformed input still surfaces distinctly from "no matches") is preserved.
- **MODIFY `seam/cli/main.py`** — add `--json` and `--quiet` options to the read commands (`impact`, `trace`, `changes`, `why`, `clusters`, `status`), add the new `affected` command, add `--stdin` to `affected` and `changes`. Each command computes its result payload, then routes through `seam/cli/output.py` for `--json`/`--quiet`, or renders the existing Rich view by default.
- **MODIFY `seam/server/tools.py`** — add `handle_seam_affected(conn, changed_files, root, …)` following the existing thin-adapter pattern (validate → call analysis → relativize paths → return dict).
- **MODIFY `seam/server/mcp.py`** — register `seam_affected` alongside the existing eight tools (becomes the ninth).

**Architectural decisions:**

- **No schema change.** Everything reuses the v4 schema (`symbols`, `edges`, `symbols_fts`, `clusters`). The search fix is query-construction + ranking only; `affected` is traversal over existing edges; the CLI work is presentation only.
- **Import hierarchy preserved.** `fts.py` is a pure leaf (no imports from query/server/cli). `affected.py` lives in `analysis` and may import `impact`/`testpaths` only (mirrors `changes.py`). `cli/output.py` is imported by `cli/main.py` only. No new cycles.
- **Confidence tiers untouched.** EXTRACTED/AMBIGUOUS/INFERRED semantics are unchanged; `affected` carries through whatever confidence the impact traversal assigns.
- **`affected` reuses `impact(direction="upstream")`** — upstream dependents are exactly the reverse-dependency set "who breaks if this changes," and impact entries already carry `file` and `is_test`. No new traversal engine.
- **Relevance score is a heuristic, not a probability.** If surfaced, label it "relevance"; never render `%`. (Lesson imported directly from CodeGraph's "8374%" artifact.)
- **Rich stays the human default.** Absent `--json`/`--quiet`, every command behaves exactly as today. Agents opt in.

**API contracts:**

- JSON success: `{"ok": true, "data": <command-specific payload>}`.
- JSON error: `{"ok": false, "error": {"code": "<STABLE_CODE>", "message": "<human text>"}}` + non-zero exit.
- Error codes reuse the existing vocabulary where applicable (`INVALID_INPUT`, `INVALID_QUERY`, `NOT_A_GIT_REPO`, plus `NO_INDEX` for the "run seam init first" case).
- `seam_affected` MCP result: `{"changed_files": [...], "affected_tests": [...], "total_dependents_traversed": int}` with file paths relativized to the project root.

## Testing Decisions

**What makes a good test here:** assert observable behavior, not internals. For `fts.py`, assert the *shape* of the produced MATCH string and the *ordering* of rescored results — not intermediate variables. For `affected.py`, build a small fixture graph and assert the *set of impacted test files*, not the traversal order. For the CLI output layer, assert the *emitted bytes* (envelope keys, exit code, quiet line format), not the function call sequence. For integration, assert the agent-visible contract end-to-end.

**Modules to be tested** (all four, per user selection):

1. **`seam/query/fts.py`** — pure unit tests: OR-join of multiple terms, operator/special-char stripping, single-term and empty-after-strip edge cases; rescore ordering (exact-name beats docstring mention, test files dampened on non-test queries, cluster boost applied). Prior art: existing query-engine tests under `tests/`.
2. **`seam/analysis/affected.py`** — unit tests on fixture graphs: changed source file → its dependent test files; changed file that is itself a test included directly; depth bound respected; a changed file with no dependents yields an empty affected set. Prior art: `tests/` coverage of `impact.py` and `changes.py`.
3. **`seam/cli/output.py`** — contract tests: success envelope keys, error envelope shape + non-zero exit pairing, quiet line format. Prior art: existing CLI tests (typer `CliRunner`).
4. **MCP + CLI integration** — `seam_affected` handler returns the documented shape; `--json` on each read command round-trips to a valid envelope; **the regression test** asserting a multi-term query (`"parse issues board"`-style) returns hits and can never silently AND to zero again. Prior art: existing MCP handler tests and CLI smoke tests.

## Out of Scope

- **No schema migration** and no new node fields (signature/docstring/visibility/etc.) — that is the next roadmap tier (§8 item 4), a separate PRD.
- **No import-statement resolution / confidence promotion / builtin filtering** (§8 item 5) — separate PRD.
- **No `context`-pack primitive** (§8 item 6) — separate PRD.
- **No `seam sync` and no cluster-recompute changes** (§8 item 7).
- **No `install` command** (§8 item 8).
- **No daemon / warm-query architecture** — explicitly deferred.
- **No WASM migration, no framework resolvers, no route nodes** — explicitly rejected in the research doc.
- **No change to the existing Rich human output** beyond routing through the new output module; the default interactive experience is unchanged.
- **`--stdin` is limited to `affected` and `changes`** — not added to symbol-argument commands (`impact`, `trace`, `why`) in this phase.

## Further Notes

- This phase is the bug-fix + interface MVP from the competitive analysis. Items 1 and 2 (search + JSON envelope) are where Seam moves from "MCP-only, agent-hostile CLI" to satisfying the agent-CLI principles (Article #37: machine-parseable, composable, minimum-viable output, fail-fast structured errors); they also let Seam leapfrog CodeGraph, which has neither an OR-join nor JSON errors.
- The competitor reference (`@colbymchenry/codegraph` v0.9.8) is cloned at `/tmp/codegraph-src` at authoring time; the per-subsystem evidence with `file:line` citations lives in `.claude/research/codegraph-vs-seam.md`. The directly relevant CodeGraph sources: FTS OR-join (`src/db/queries.ts:961-969`), multi-signal rescore (`src/db/queries.ts:820-826`, `src/search/query-utils.ts`), `affected` (`src/bin/codegraph.ts:1477-1603`).
- `make gate` must pass before every commit; zero external services at runtime; SQLite only; config from `seam/config.py`. The `affected` depth bound and any test-glob defaults should be config constants, not hardcoded.
- Suggested build order within the phase: (1) `fts.py` + engine wiring + regression test, (2) `cli/output.py` + `--json`/`--quiet` retrofit, (3) `affected.py` + CLI command + `seam_affected` MCP tool + `--stdin` on affected/changes.
