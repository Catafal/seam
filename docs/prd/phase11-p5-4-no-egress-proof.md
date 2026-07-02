# Phase 11 · P5.4 — No-Egress Proof

> Roadmap item **#7** of the "Proposed Phase Order" — the second *definitely add first*
> trust-tier item. Pairs with the merged **P5.3** installer write-scope audit: P5.3 proved
> "the installer only writes expected files"; P5.4 proves "the read path makes zero network
> calls."
>
> **Linux-only CI deliverable.** `strace` does not exist on macOS, so the workflow + real
> syscall trace can only be validated in GitHub Actions. Only the pure trace parser is
> locally testable.

## Problem Statement

Seam's headline promise is **zero external services at runtime — no API keys, no network
calls**. Today that promise lives only in the README and `CLAUDE.md` as prose. A user who
must justify running Seam inside a locked-down or air-gapped environment has no repeatable
evidence that the read path (`seam init` / `search` / `context` / `impact`, and the
`seam start` MCP server) never opens an outbound connection. A regression — a new
dependency that phones home, an accidental telemetry call, a stray hostname lookup — would
ship silently, because nothing in CI watches for it. "Local-first" is currently an
unverified claim.

## Solution

Add an optional Linux CI job that runs each read-path command under `strace -f -e
trace=connect` and **fails the job on any outbound `connect()` to a non-local address**.
Loopback, UNIX-domain, and netlink sockets are allowed (they are local IPC/OS plumbing, not
egress); anything else — any real IP connect — fails the build and names the offending
syscall line.

The trace classification is a small, pure parser that is unit-tested locally with synthetic
`strace` fixtures, so the *logic* is verifiable on any platform (including the maintainer's
macOS box); the CI job wires that parser to real `strace` runs on `ubuntu-latest`. After
this lands, every push and PR carries machine-checked proof that Seam's read path is silent
— the local-first promise becomes a gate, not a sentence.

## User Stories

1. As a security-conscious adopter, I want machine-checked proof that Seam makes no outbound network calls on its read path, so that I can run it in a locked-down environment with confidence.
2. As a Seam maintainer, I want CI to fail if any command opens an external connection, so that a dependency or code change that phones home is caught before merge.
3. As a maintainer, I want the offending `connect()` syscall line printed when the job fails, so that I can immediately see which address was contacted and trace it to its cause.
4. As a maintainer, I want `seam init` (default, no `--semantic`) proven silent, so that indexing a repository is known to touch nothing but the local filesystem.
5. As a maintainer, I want `seam search` proven silent, so that full-text/hybrid search is known to be local-only.
6. As a maintainer, I want `seam context` proven silent, so that symbol lookups never leak the code being queried.
7. As a maintainer, I want `seam impact` proven silent, so that blast-radius analysis never leaves the machine.
8. As a maintainer, I want the `seam start` MCP server proven silent across a real JSON-RPC handshake (initialize + list tools), so that the agent-facing serving path — not just the CLI — is covered.
9. As a maintainer, I want loopback, UNIX-domain, and netlink sockets treated as allowed, so that local IPC and OS plumbing (e.g. systemd-resolved on loopback, netlink socket setup) do not produce false failures.
10. As a maintainer, I want ANY non-loopback IP connect treated as a failure — including private-LAN ranges — so that the proof is strict and cannot be weakened by a "but it's only internal" exception.
11. As a maintainer, I want each traced command to be required to actually SUCCEED (exit cleanly / produce output), so that a command which crashes early cannot trivially "pass" the egress check by never reaching the network code.
12. As a maintainer, I want the trace parser to be a pure, unit-tested module, so that its classification logic is trustworthy and I can validate it locally without `strace`.
13. As a maintainer, I want synthetic `strace` fixtures covering AF_INET/AF_INET6/AF_UNIX/AF_NETLINK, loopback vs external addresses, failed connects (EINPROGRESS/ECONNREFUSED), and `-f` child-process `[pid N]` prefixes, so that the parser is proven against realistic trace shapes.
14. As a maintainer, I want a failed *external* connect (return `-1`) still counted as a violation, so that an attempted-but-refused outbound call is not excused — intent to egress is the signal.
15. As a maintainer, I want the semantic model download (`seam init --semantic`) explicitly EXCLUDED from this job, so that the one legitimate, documented download does not have to be mocked and the job stays deterministic and offline.
16. As a maintainer, I want the job to run on pull requests and pushes to main, so that the proof is continuous and visible on every change, like the existing `gate` CI.
17. As a maintainer, I want the job to pin its GitHub Actions to the same tag convention as the existing CI, so that the security job does not itself introduce a supply-chain soft spot.
18. As a maintainer, I want the job to declare least-privilege permissions (`contents: read`), so that the security proof job follows the same hardening as `ci.yml`.
19. As a contributor, I want a clear, self-contained failure message ("external connection detected: <line>") rather than a raw strace dump, so that I can act on a red build without decoding syscalls.
20. As a maintainer, I want the parser invocable as a standalone module (`python -m tests.support.egress_audit <tracefile>`) returning a nonzero exit on any violation, so that the CI harness is a thin shell wrapper with no embedded parsing logic.

## Implementation Decisions

- **Two-part slice mirroring P5.3's shape (deep testable module + thin CI harness).** The trust-critical logic is a pure parser (locally unit-testable); the platform-bound wiring is a workflow YAML (CI-validated only). This keeps the untestable-on-macOS surface as thin as possible.

- **New deep module — the egress trace parser (`tests/support/egress_audit.py`).** Lives alongside `fs_audit.py` in the `tests/support/` package created by P5.3. Stdlib-only, pure, never raises on malformed input:
  - A classifier that, given a single `strace` output line, decides whether it represents a `connect()` and, if so, whether the target is **local** (AF_UNIX, AF_NETLINK, or an AF_INET/AF_INET6 address in loopback `127.0.0.0/8` / `::1`) or **external** (any other address family carrying a routable address, i.e. any non-loopback IP). Lines that are not `connect()` calls are ignored.
  - A scanner that, given full `strace` text, returns the list of **external** offender lines (empty list == clean).
  - A thin `main(argv)` CLI entry: read one or more trace files, print each external offender with a clear `external connection detected:` prefix, and return exit code `0` (clean) or `1` (≥1 violation). Invocable as `python -m tests.support.egress_audit <tracefile>...`.
  - Robustness contract (informed by real `strace -f -e trace=connect` output): must handle `[pid N]` child-process prefixes (from `-f`), the `sa_family=…` field, `sin_addr=inet_addr("…")` / `inet_pton` IPv6 forms, `sun_path=…` UNIX sockets, netlink sockets, and connect lines ending in success (`= 0`) OR failure (`= -1 EINPROGRESS` / `ECONNREFUSED`). A failed connect to an **external** address is still a violation (intent to egress). Unparseable/partial lines are treated conservatively — if a line clearly is a `connect()` to a non-local family but cannot be classified, it is reported as a violation rather than silently passed (fail-closed for a security proof).

- **Strict allow-list (confirmed).** Only loopback, UNIX-domain, and netlink are allowed. Private-LAN ranges (10/8, 172.16/12, 192.168/16) are treated as **external** and fail the job — there is no "internal is fine" exception, because the read path should contact nothing at all.

- **New CI harness (`.github/workflows/no-egress.yml`).** A dedicated Linux job (`ubuntu-latest`), separate from `ci.yml`:
  - Triggers on `pull_request` and `push` to `main` (continuous + visible, like `gate`); `permissions: contents: read`; GitHub Actions pinned to the same tag convention as `ci.yml` (`actions/checkout`, `astral-sh/setup-uv`).
  - Installs `strace` via apt, installs Seam via `uv sync` (server extra for the MCP smoke; NOT the semantic extra), indexes a small fixture/target directory, then runs EACH command under `strace -f -e trace=connect -o <tracefile>` and pipes each trace through `python -m tests.support.egress_audit`.
  - Covered commands: `seam init` (default — NO `--semantic`), `seam search`, `seam context`, `seam impact`, and a bounded **`seam start` MCP stdio smoke** that drives one real JSON-RPC handshake (initialize + list tools) and confirms a response, then terminates via `timeout`.
  - **No-false-pass guard:** each traced command must ALSO exit successfully (or, for `seam start`, the smoke must confirm a JSON-RPC response) — a command that crashes before reaching network code fails the job rather than passing vacuously.

- **`seam start` smoke via the MCP SDK stdio client (confirmed direction).** Rather than hand-rolling newline-delimited JSON-RPC framing, the smoke uses the `mcp` SDK's own stdio client (already available via the optional `server` extra) to spawn `seam start`, perform `initialize` + `tools/list`, print the tool count, and exit — the whole thing wrapped in `strace -f` (so the spawned server is followed) and a `timeout`. The exact driver (a few lines, inline in the workflow or a tiny helper) is an implementation detail; the decision is "use the SDK client, run the server as the traced child, bound with a timeout."

- **Semantic download excluded (confirmed).** The job never runs `seam init --semantic`, so the one legitimate fastembed model download never occurs and nothing has to be mocked. The default cluster-naming mode is deterministic (local), so the `SEAM_CLUSTER_NAMING=llm` `urllib` path is never exercised either. Both non-default network touchpoints are simply not invoked.

- **No product code changes, no schema change, no new config knobs, no new MCP tool.** This is a CI + test-support slice. MCP tool count stays 16. If the job surfaces a genuine egress in the read path, that is a real bug to be captured and fixed separately — not silently allow-listed.

## Testing Decisions

- **A good test here asserts the parser's external behavior — offender-in → verdict-out — never its internals.** Tests feed synthetic `strace` text (crafted fixtures, not live traces) and assert the returned offender list / `main` exit code. No mocking of `strace` itself; no live network.

- **Module under test:** `tests/support/egress_audit.py` — unit-tested in `tests/unit/test_egress_audit.py`. Coverage includes: a clean trace (loopback + UNIX + netlink only) → no offenders / exit 0; an external AF_INET connect → offender / exit 1; an external AF_INET6 connect → offender; a loopback `127.0.0.53` DNS connect → allowed; a failed external connect (`= -1 EINPROGRESS`) → still an offender; `[pid N]` child-prefixed lines parsed correctly; non-connect lines ignored; a malformed connect-to-external line → fail-closed (reported); `main` exit codes for clean vs dirty files.

- **Prior art in the codebase:**
  - `tests/support/fs_audit.py` + `tests/unit/test_fs_audit.py` (P5.3) — the exact "pure deep module in `tests/support/`, unit-tested with synthetic inputs, driven by a thin harness" pattern this slice repeats.
  - `.github/workflows/ci.yml` — the existing `ubuntu-latest` + `uv` job whose action-pinning, `permissions`, and trigger conventions the new workflow mirrors.

- **The CI workflow itself is validated in GitHub Actions, not by `make gate`.** The parser is gate-covered (ruff + mypy + pytest); the YAML + real `strace` behavior is proven by the job running green on a real PR. This split is called out explicitly so no one expects local `make gate` to exercise the strace path.

- **Gate:** the new parser + its tests must keep `make gate` green (ruff + mypy + full suite). Type-hinted (`X | None`), imports at top, snake_case, ≤200 lines/function, ≤1000 lines/file.

## Out of Scope

- **Verifying the ONE expected fastembed download on `seam init --semantic`** — the roadmap lists this as an optional clause. MVP is scoped to the read-path no-egress proof; an allow-listed single-download verification is a possible later follow-on, not part of this slice.
- **macOS / Windows egress proofs** — `strace` is Linux-only; a `dtruss`/DTrace equivalent for macOS (which requires disabling SIP) is out of scope. The proof is Linux-CI-only by design.
- **Blocking network at the kernel/sandbox level** (network namespaces, seccomp, firewall egress deny) — this slice *observes and asserts* rather than *enforces*. A namespace-based hard block is a heavier, separate hardening option.
- **Changing product behavior** — no code is added to *prevent* egress; the slice proves the current absence of it. A real egress finding is a separate bug fix.
- **The `SEAM_CLUSTER_NAMING=llm` and `--semantic` paths** — deliberately not exercised; they are the two known, documented, opt-in network paths and are excluded rather than audited here.

## Further Notes

- This is a **trust-tier** slice: its value is a repeatable, visible proof, not a feature. Together with P5.3 it closes the "definitely add first" trust pair — installer write-scope + read-path no-egress.
- Fail-closed is the guiding principle: when the parser is unsure whether a `connect()` is local, it reports rather than excuses. A security proof that errs toward false positives is safe (a human investigates); one that errs toward false negatives is worthless.
- After approval, split with `/to-issues` (S1 = parser + unit tests, locally verifiable; S2 = workflow wiring, CI-verifiable), then implement on a `codex/`-prefixed worktree. The S2 workflow can only be proven green by opening the PR and watching the job run — factor that into the definition of done.
- The maintainer cannot fully validate S2 on macOS; plan to rely on the Actions run as the acceptance signal for the workflow half.
