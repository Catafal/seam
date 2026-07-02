# PRD — Phase 11 P5.1: Verified npm shim

> Status: proposed — 2026-07-02.
> Roadmap: `docs/prd/phase11-codebase-memory-roadmap.md` → P5 item #1 (Proposed Phase Order #14).
> Trust tier follow-on to the merged P5.3 (installer write-scope), P5.4 (no-egress),
> P5.2 (release hardening). Those proved the RUNTIME and the RELEASE are trustworthy;
> P5.1 widens DISTRIBUTION reach without weakening either.

## Problem Statement

Seam ships as a Python package (`seam-code` on PyPI, console command `seam`). A JS/TS
developer — or the agent working inside a JS/TS repo — has to know to reach for `pip`,
`pipx`, or `uv` to get Seam, which is friction in a Node-first environment where `npx` is
the muscle-memory way to run one-off tools. The reference project (`codebase-memory-mcp`)
has a materially smoother "one command, works in agent environments" story via an npm
package. Seam has no npm entry point at all, so Node users bounce off before they ever
index a repo.

## Solution

Publish a small, verified **npm shim** — `@catafal/seam` — that wraps `uvx`. Running
`npx @catafal/seam <args>` transparently invokes `uvx --from 'seam-code==<pinned>' seam
<args>`, so a Node user gets the full Seam CLI (init / query / context / impact / start /
serve / install / everything) with one familiar command and no pip in sight. uv already
handles the download, checksum verification, and caching of the wheel against PyPI, so the
shim itself downloads nothing and needs no bespoke artifact-verification code. If `uv`/`uvx`
is not installed, the shim fails **explicitly and safely** with a one-line install pointer,
never silently or by attempting to bootstrap uv itself. The npm version is pinned in lockstep
with the PyPI version, so `npx @catafal/seam@0.4.0` always runs exactly Seam 0.4.0 —
reproducible by construction.

## User Stories

1. As a JS/TS developer, I want to run `npx @catafal/seam init`, so that I can index my repo
   without learning pip/pipx/uv workflows first.
2. As a JS/TS developer, I want `npx @catafal/seam --version`, so that I can confirm which
   Seam version I'm about to run.
3. As an AI coding agent operating in a Node project, I want a single `npx` command that
   starts Seam's MCP server, so that I can wire up code intelligence without a Python setup
   step in my environment.
4. As a developer, I want `npx @catafal/seam start .` to launch the MCP server exactly as
   `seam start .` would, so that the shim is a transparent pass-through, not a reduced surface.
5. As a developer who already has uv, I want the shim to reuse my uv cache, so that repeat
   runs are fast and don't re-download the wheel.
6. As a developer WITHOUT uv installed, I want a clear, actionable error telling me how to
   install uv, so that I'm never left with a cryptic failure or a silent no-op.
7. As a security-conscious developer, I want the shim to never execute a downloaded binary or
   bypass a checksum, so that adopting the npm entry point doesn't widen my supply-chain risk.
8. As a security-conscious developer, I want the shim to never build its child command via a
   shell string, so that repository names, paths, or args can't inject shell commands.
9. As a developer, I want `npx @catafal/seam@0.4.0` to run precisely Seam 0.4.0 (not "latest"),
   so that my scripts and CI are reproducible.
10. As a developer, I want the shim to forward my exit code faithfully, so that CI steps and
    shell pipelines can branch on success/failure of the underlying Seam command.
11. As a developer, I want the shim to forward stdin/stdout/stderr transparently, so that
    interactive commands, JSON output, and the MCP stdio transport all work unchanged.
12. As a developer on macOS, Linux, or Windows, I want the shim to work on my platform, so
    that team members on different OSes share one command.
13. As a maintainer, I want the pure argv-building logic isolated in a dependency-free module,
    so that I can unit-test every branch (uvx found/missing, version pin, arg passthrough)
    without spawning processes.
14. As a maintainer, I want a smoke test that runs the real `bin.js` against a stubbed `uvx`,
    so that arg-forwarding and exit-code propagation are proven end-to-end, not just in theory.
15. As a maintainer, I want the npm and PyPI versions kept in lockstep at release time, so
    that a published `@catafal/seam@X` can always resolve `seam-code==X`.
16. As a maintainer, I want the npm package `files` allowlist to ship only the shim sources,
    so that no stray repo content leaks into the published tarball.
17. As a maintainer, I want NO `postinstall` script in the package, so that installing the
    shim runs zero code and touches nothing until the user actually invokes it.
18. As a Node user behind a corporate proxy, I want all network behavior to be uv's (against
    PyPI), so that my existing pip/uv proxy configuration is respected with no new network
    path introduced by the shim.
19. As a maintainer, I want the shim documented in the README's install section, so that Node
    users discover it alongside the pip/uv instructions.
20. As a developer, I want `npx @catafal/seam` with no args to behave exactly like `seam`
    with no args (its help/usage), so that discovery works the same way.
21. As a maintainer, I want the shim's uv-detection to also accept a uv-provided `uvx` on a
    non-standard PATH via an env override, so that unusual installs still work.
22. As a release engineer, I want publishing the npm package to be a documented, minimal
    manual (or CI) step, so that cutting an npm release is low-ceremony and mirrors the PyPI
    release.

## Implementation Decisions

**Delivery strategy — wrap `uvx` (confirmed).**
The shim delegates to `uvx --from 'seam-code==<version>' seam <userArgs>`. `uvx` (from
Astral's uv) resolves, downloads, checksum-verifies (against PyPI), caches, and runs the
`seam` console script from the `seam-code` distribution in an ephemeral environment. The shim
fetches and verifies nothing itself. The reference project's download/verify/extract layer
(GitHub Releases, SHA256 `checksums.txt`, tar-slip defense, redirect capping) is **explicitly
out of scope** — it exists only to ship a static binary, which Seam does not have. That layer
is deferred to a future "bundled artifact" phase (roadmap open question #5) if ever needed.

**Package identity — `@catafal/seam` (confirmed).**
Scoped to the `Catafal` org, matching the roadmap's `npx @catafal/seam` example. The
underlying console command remains `seam`. `engines.node >= 18`. `os`/`cpu` are left
unconstrained — the shim is pure JS with no native/prebuilt binary, so it runs anywhere Node
18+ runs (platform support is entirely uv's concern).

**Version pinning — exact, mirrored (confirmed).**
The npm `package.json` `version` mirrors the PyPI `seam-code` version. The shim reads its own
`version` and pins the PyPI resolution to it: `uvx --from 'seam-code==<version>' seam ...`.
Consequence: npm and PyPI versions MUST be released in lockstep. A released
`@catafal/seam@X` therefore always runs Seam `X`, reproducibly. (An escape hatch env var —
see below — allows overriding the pinned spec for local testing against an unpublished
version, but the default is the pinned exact spec.)

**Deep module — a pure argv-builder (the testable seam).**
A dependency-free module exposes two pure functions:
- `resolveRunner(env)` → the absolute/looked-up `uvx` command to spawn, honoring an explicit
  override env var (e.g. `SEAM_NPM_UVX`) when set; returns a sentinel/`null` (NOT throwing)
  when uvx cannot be located, so the caller renders the guidance message. Pure w.r.t. the
  passed `env`; does not itself shell out to discover uv beyond an optional `which`-style
  probe that is injected/mockable.
- `buildUvxArgs(userArgv, { version, fromSpecOverride })` → the exact args array to pass to
  `uvx`, i.e. `['--from', <spec>, 'seam', ...userArgv]`, where `<spec>` is
  `seam-code==<version>` by default or `fromSpecOverride` (from an env var like
  `SEAM_NPM_FROM`) when provided. No process spawning, no I/O — returns data only.

**Thin harness — `bin.js`.**
`#!/usr/bin/env node`. Reads its own version from `package.json`, calls `resolveRunner` +
`buildUvxArgs`, and if a runner was found, `spawnSync(uvx, args, { stdio: 'inherit' })` —
**array args, never a shell string** (no `shell: true`) — then `process.exit(child.status ??
0)`. If no runner, print the uv-install guidance to stderr and exit non-zero. On spawn error
(e.g. ENOENT despite the probe), print a clear message and exit non-zero. Keep `bin.js` as
small as possible; all decision logic lives in the pure module.

**Safety contract (what "verified" means for the uvx path):**
- No `postinstall` / lifecycle scripts — installing the shim executes no code.
- No downloads, no checksum logic, no archive extraction in the shim (uv owns all of that
  against PyPI).
- No shell interpolation anywhere — child process is always spawned with an argv array.
- uv absence is an explicit, non-zero, guidance-bearing failure — never silent, never an
  auto-install of uv (respecting the permission boundary, same principle as the installer).

**Release/versioning coupling.**
Document (and, where practical, wire) that cutting a release bumps BOTH `pyproject.toml` /
`seam/__init__.py` AND `pkg/npm/package.json` to the same version. The existing smoke test
that asserts `seam.__version__ == pyproject version` gains a sibling assertion for the npm
`package.json` version, so a drift fails the gate. npm publish itself is a separate,
documented step (manual `npm publish` or an optional CI job) — it is NOT auto-triggered by
the PyPI release in this slice.

**No product source change.**
No change to `seam/`, the schema, config knobs, or the MCP tool set (stays 16). This is a
new distribution artifact plus one test-parity assertion.

## Testing Decisions

**What makes a good test here:** exercise the shim's *external* behavior — the exact command
it would run, how it reacts to a missing runner, how it forwards args and exit codes —
without asserting internal structure. The pure argv-builder makes the high-value branches
testable as data (input argv/env → output args) with zero process spawning.

**Modules tested:**
- `pkg/npm/lib/invocation.js` (pure) — **vitest** unit suite covering:
  `buildUvxArgs` produces `['--from', 'seam-code==<v>', 'seam', ...args]`; version pin uses
  the package version; `SEAM_NPM_FROM` override replaces the spec; empty argv yields just the
  `seam` invocation; flags like `--version` / `start .` / `--json` pass through verbatim and
  in order; `resolveRunner` returns the override when `SEAM_NPM_UVX` is set, a located `uvx`
  when the probe finds it, and the missing sentinel when it does not. Run via a **`make
  test-npm`** target — node-gated and OUTSIDE the Python `make gate` (same discipline as
  `no-egress` and `bench-semantic`, which are not part of the gate).
- `bin.js` (thin) — a **pytest** smoke (`tests/integration/…`) that puts a **stub `uvx`** on
  `PATH` (a tiny script that echoes its argv and exits with a controlled code), runs `node
  bin.js <args>`, and asserts (a) the stub received exactly `--from seam-code==<v> seam
  <args>`, and (b) the stub's exit code is propagated by `bin.js`. Skipped via a node-absence
  guard (mirrors the `pytest.importorskip("fastembed")` real-model skips) so the Python gate
  stays green on machines without node.

**Version-parity test:** extend the existing `tests/unit/test_smoke.py::test_package_version`
(or add a sibling) to assert the npm `package.json` version equals the `pyproject.toml`
version — so npm/PyPI drift fails `make gate` locally.

**Prior art in the codebase:**
- `tests/support/fs_audit.py` + `egress_audit.py` + `actions_pin_audit.py` — the established
  "pure, dependency-free, unit-tested core + thin consumer" pattern this slice mirrors in JS.
- `web/` already uses vitest — the `make test-npm` target follows that toolchain, no new test
  framework introduced.
- The fastembed `importorskip` skips — prior art for a gate test that self-skips when an
  optional external tool (here, node) is absent.
- `tests/integration/test_cli_version.py` — prior art for asserting `seam --version` behavior
  via subprocess.

## Out of Scope

- Downloading/verifying any binary or release artifact; SHA256 `checksums.txt` handling;
  tar-slip / path-traversal defense; redirect-capping. (Deferred "bundled artifact" phase —
  only relevant if Seam ever ships a standalone binary. The uvx path needs none of it.)
- Auto-installing `uv`/`uvx` on the user's behalf. The shim detects and guides only.
- Publishing to npm as an automated step of the PyPI release. This slice ships the package +
  tests + docs + a documented publish procedure; wiring an auto-publish CI job is a later,
  optional follow-on.
- Broadening to other package registries (Homebrew, Nix, etc.) — the reference has these, but
  they are separate distribution slices.
- Any change to `seam/` product source, the DB schema, config knobs, or the MCP tool surface.
- Windows-specific binary handling — moot, since the shim ships no binary and delegates OS
  concerns entirely to uv.

## Further Notes

- **Dependency on the pending 0.4.0 PyPI publish:** the shim resolves `seam-code==<version>`.
  The npm package can be authored and fully tested now (stub uvx in tests; no real network),
  but a *published* `@catafal/seam@0.4.0` only works end-to-end once `seam-code==0.4.0` is on
  PyPI (currently pending the Trusted Publisher config — 0.3.0 is live). An npm release should
  therefore trail the corresponding PyPI release. The tests never hit the network, so this
  does not block implementation or the gate.
- **Roadmap open question #5 ("wrap uvx first, or wait for a bundled artifact story?")** is
  resolved here in favor of wrapping uvx first, consistent with the P5 section's stated
  preference.
- **uv presence is the one runtime prerequisite** the shim can't remove. This is an
  intentional, documented trade-off: wrapping uvx keeps the shim tiny and download-free at the
  cost of requiring uv. The guidance message turns that requirement into a one-line fix rather
  than a dead end.
- The reference `pkg/npm/{package.json,bin.js,install.js}` layout is a useful structural
  template, but Seam's `bin.js` is far thinner (no `install.js` at all) because there is
  nothing to download.
