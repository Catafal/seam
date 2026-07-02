# PRD: Auto-init on `seam serve`

> Status: ready-for-agent.
> Motivation: reduce human web-UI onboarding friction (raised while discussing how projects
> like DeusData/codebase-memory-mcp achieve one-command startup).
> Schema target: no migration. CLI/indexer refactor + serve behavior change only.

## Problem Statement

A human who just wants to see the Seam Explorer (especially the new 3D Constellation) has to run
several commands and know a hidden detail: `pip install 'seam-code[web]'`, then `seam init`, then
`seam serve`. If they skip `seam init`, `seam serve` errors with "No index found. Run `seam init`
first." — a dead end that forces them back to the docs.

Competing tools (e.g. codebase-memory-mcp) feel like "one command" because they ship a single static
binary that bundles everything and bootstraps on first run. Seam is deliberately a Python package
(not a static binary), but it should still make the happy path feel like one command. Today the
web-UI happy path is 2–3 steps with a failure mode that punishes the most common mistake (forgetting
to index first).

## Solution

When a user runs `seam serve` on a project whose index is missing, Seam automatically indexes the
project first — with clear, honest messaging that this one-time step is happening — and then starts
the server. The human web-UI flow collapses to a single command: `seam serve`.

A `--no-init` flag preserves the previous behavior (error out if no index) for scripting/CI callers
who want an explicit failure rather than an implicit index build. Because indexing writes files and
can be slow on large repos, the auto-init only triggers when the index is entirely MISSING — never
to silently rebuild a stale index (staleness is already surfaced by the freshness banner and handled
by `seam sync`). The `[web]` extra is checked before the (potentially slow) auto-init so the user is
never made to wait for a full index only to hit a missing-dependency error at the end.

## User Stories

1. As a developer trying Seam for the first time, I want `seam serve` to just work on an un-indexed
   repo, so that I can see the Explorer without reading the docs.
2. As a developer, I want `seam serve` to index my repo automatically when no index exists, so that I
   don't have to remember a separate `seam init` step.
3. As a developer, I want a clear message telling me indexing is happening, so that I don't think the
   command hung.
4. As a developer, I want the auto-init to show the same progress feedback as `seam init`, so that I
   know how far along it is on a large repo.
5. As a developer, I want the server to start automatically once the auto-init finishes, so that the
   whole flow is one command.
6. As a developer on a large repo, I want to know the auto-init is a one-time cost, so that I
   understand subsequent `seam serve` runs are fast.
7. As a scripting/CI user, I want a `--no-init` flag, so that `seam serve` fails fast instead of
   implicitly building an index.
8. As a developer who forgot the `[web]` extra, I want that reported before any indexing happens, so
   that I don't wait for a full index and then hit a dependency error.
9. As a developer, I want the missing-`[web]` message to be copy-pasteable, so that I can fix it in
   one step.
10. As a developer with an existing index, I want `seam serve` to behave exactly as before (no
    re-index), so that startup stays instant.
11. As a developer, I want auto-init NOT to rebuild a stale index automatically, so that `seam serve`
    never silently triggers a slow re-index I didn't ask for.
12. As a developer, I want to be told (via the existing freshness signal) when my index is stale, so
    that I can choose to run `seam sync`/`seam init` myself.
13. As a developer, I want auto-init to work in a non-git directory, so that filesystem-only projects
    are supported (matching `seam init`).
14. As a developer, I want auto-init on an empty directory to produce a valid empty index and still
    serve, so that I get a working (if sparse) Explorer instead of a crash.
15. As a developer, I want a clear error if auto-init itself fails, so that I understand the problem
    rather than seeing a half-started server.
16. As a maintainer, I want the `init` command and serve's auto-init to share ONE indexing code path,
    so that they can never drift.
17. As a maintainer, I want the shared indexing logic extracted into a deep, testable module, so that
    it can be unit-tested without the CLI.
18. As a maintainer, I want `seam init` output to be byte-stable after the refactor, so that existing
    behavior and tests are preserved.
19. As a maintainer, I want auto-init to respect the same config (language map, cluster/synthesis
    settings) as `seam init`, so that the two produce identical indexes.
20. As a developer, I want `seam serve --no-init` on a missing index to give the same clear
    "run seam init" guidance as before, so that the explicit path is still friendly.
21. As a developer, I want the browser to open only after the server actually starts (unchanged), so
    that auto-init time doesn't leave me staring at a connection-refused page.
22. As a maintainer, I want the security warning for non-loopback `--host` to still fire, so that the
    auto-init change doesn't weaken the existing safety guard.

## Implementation Decisions

- **Extract a deep indexing module.** The indexing orchestration currently inlined in the `init` CLI
  command (init DB → walk project → per-file extraction loop → cluster post-pass → synthesis
  post-pass → test-edge post-pass → optional embeddings) is extracted into a single reusable function
  with a simple interface: it accepts the project root, an optional DB-directory override, an optional
  semantic flag, and an optional progress callback; it returns a structured result carrying the counts
  (files indexed/skipped, symbols, edges, clusters, synthesis, test edges, embeddings) plus the DB
  path. It performs no terminal rendering itself — presentation stays in the CLI.
- **`init` command delegates to the shared function.** The `init` command keeps its options, Rich
  progress spinner, and summary table, but drives them through the shared function via the progress
  callback so its user-visible output is unchanged.
- **`seam serve` auto-inits on a missing index.** The current "No index found → exit 1" guard is
  replaced by: (a) verify the `[web]` extra is importable first (cheap, avoids indexing-then-failing);
  (b) if the index is missing and `--no-init` was not passed, print a one-time "indexing…" message,
  call the shared init function (surfacing progress), then continue to serve; (c) if `--no-init` was
  passed and the index is missing, keep the previous clear error and exit non-zero.
- **Scope guard: missing-only.** Auto-init triggers strictly when the index database does not exist.
  A present-but-stale index is served as-is (staleness is already surfaced by the freshness banner and
  remediated by `seam sync`). Auto-init never rebuilds an existing index.
- **Ordering.** `[web]` availability is checked before auto-init; auto-init runs before building the
  FastAPI app and before the browser opens; the non-loopback host warning is unchanged.
- **Failure handling.** If the shared init function fails, `seam serve` reports a clear error and
  exits non-zero without starting a partial server.
- **No new dependencies, no schema change, no new MCP tool.** This is a CLI/indexer refactor plus a
  serve behavior change. `seam init`'s indexes remain identical.

## Testing Decisions

- Good tests assert external behavior: does `seam serve` create an index when none exists, does it
  skip auto-init when one exists, does `--no-init` preserve the old error, is the `[web]` check
  reported before indexing, and does `seam init` still produce the same result. They must not assert
  internal call sequences or private helpers.
- The extracted indexing module is unit-tested directly: run it on a small fixture project and assert
  a valid index is produced with sensible non-negative counts, that it is idempotent (a second run
  reconciles rather than corrupts), and that it works on an empty directory and a non-git directory.
- The serve command is tested with the CLI runner (mocking the blocking server run): (a) missing
  index + default → index created then server-run invoked; (b) existing index → no re-index, server
  run invoked; (c) `--no-init` + missing index → clear error, exit non-zero, no server run; (d)
  missing `[web]` → dependency error reported before any indexing occurs; (e) auto-init failure →
  clear error, non-zero exit, no server run.
- The `init` command retains its existing tests; add a regression assertion that its output/summary is
  unchanged after delegating to the shared function.
- Prior art: `tests/integration/test_serve_cli.py` (serve CLI behavior), the existing `init` command
  tests, and the indexer/pipeline test families.

## Out of Scope

- Auto-rebuilding a stale (present-but-outdated) index — handled by `seam sync` and the freshness
  banner.
- Bundling the `[web]` extra into the base install — the extra stays optional; only the onboarding
  messaging and flow improve.
- Publishing to PyPI / `uvx` / npm shim / install script — these are the separate distribution levers
  (roadmap P5) that make "one command install" real; this PRD only reduces the post-install command
  count.
- Any change to the MCP or 2D/3D Explorer surfaces.
- Auto-init for other read commands (`seam search`/`query`/`context`/`impact`); this PRD covers
  `seam serve` only. (A future change could extend the shared function to them.)

## Further Notes

This is a small, high-DX change: it turns the web-UI happy path from "install extra → init → serve"
into "serve" for anyone who has Seam installed, without abandoning Seam's optional-`[web]`, no-static-
binary design. The larger "one command to install" story (uvx/npm/brew) remains gated on publishing
Seam to PyPI and is tracked separately.

The extraction of a shared init function is the load-bearing decision: it is a genuine deep module
(root in → indexed DB + counts out) that removes duplication risk between `seam init` and serve
auto-init, and it opens the door to reusing the same bootstrap for other commands later.
